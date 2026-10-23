"""The egress allowlist: what may leave this surface, named in advance.

Why an allowlist rather than a better mask
------------------------------------------
`privacy.py` masks recognised credential shapes in text that is already on its
way out. That is the right instrument for exactly one input — a session
transcript. Session history is free-form by nature, a read that dropped fields
it did not recognise would be a read that lied about what the session said, and
where nothing stronger is possible a mask that catches the common case is worth
having.

It is the wrong instrument for everything else, and the reason is structural
rather than a question of pattern coverage. A mask is *fail-open*: it begins by
sending everything and removes what it recognises, so a credential shape nobody
wrote a pattern for is sent, and its failure is silent and indistinguishable
from success. An allowlist is *fail-closed*: it begins by sending nothing and
admits what has been named, so an unrecognised field is withheld by default and
the withholding is visible — in the emitted record, on stderr, and in the count.

This module governs the two surfaces where a value this process did not author
can end up somewhere it is stored or read back.

Remote text
-----------
An HTTP error body, an upstream JSON-RPC error message, a non-JSON response:
none of it was written here. What sits on the far side of `cloudcli_endpoint`
is a gateway in front of a model provider, and a provider SDK's error object is
exactly where an upstream API key gets quoted back into a message. There is no
schema to allowlist such text against — it is whatever the far end felt like
sending this time — so the rule is the strongest one available: **remote text
is summarised locally and never relayed.** What crosses is written here: the
label, the method, the HTTP status, the size, and a coarse kind. An operator who
needs the body reads it where it was produced, on the device, in a shell they
already trust; a body relayed through an Agent transcript is a body relayed to
everyone who can read that transcript.

Fields this module did not name
-------------------------------
`AuditLog.record` accepts `**fields` from its callers. That is convenient and it
is also an open door: the audit is written from every part of the surface and
read back by `fleet_status`, so one careless caller puts a credential into a
file and then into whichever transcript asked for status. The field *names* are
allowlisted here, the rule is applied on the way in, and it is applied again on
the way out — a record already on disk from an older build, or edited by hand,
is still projected when it is read.

A name is not enough either
---------------------------
Allowing a field by name and letting its *value* through on a length bound
closes the door and leaves the window open, because the bound was measuring the
wrong thing. A fact is short and so is a credential: `ccp_` plus sixteen bytes
of hex is thirty-seven characters, a `Bearer` header is shorter than a log
line, and both fit under any ceiling large enough to admit a session reference.
Worse, the names that were most exposed were the ones most obviously useful —
`detail` and `reason` are *free text a caller chose*, and a caller who pastes a
token into an error message has parked that token in the audit by asking for it
to be recorded.

So the table below maps each allowed name to a **kind**, and the kind is what
decides whether a value survives:

*   A name bound to a *shape* — a count, a flag, a timestamp, a lowercase code,
    an identifier, an OAuth scope, a URI *origin* — keeps values that are
    recognisably that shape and withholds the rest. The URI kind is where the
    reviewer's second finding lives: `redirect_uri` is recorded as
    `scheme://host[:port]` and nothing else, because the query string is where
    an authorization code or a token rides and the userinfo component is where a
    credential is *meant* to be.
*   A name bound to *digest* keeps a one-way fingerprint. `idempotency_key` is
    in this group on purpose: the value has a caller on the other end of it, it
    is opaque, and no pattern can tell a session id from a token — but the audit
    only ever needed to prove that two records carried the *same* key, which a
    digest does and the value does not.
*   A name bound to *withheld* never keeps the value at all, whatever it says
    and however short it is. `detail`, `reason` and `client_name` are here
    because they are free text with a caller at the other end, and the only rule
    that does not depend on recognising the credential is to not keep the text.

The residual, stated rather than implied: a field whose kind is `ref` can still
hold a bare token, because `[A-Za-z0-9._:|-]+` is exactly what a session
reference and a token both look like. Those fields are the ones this surface
already returns in its own responses — the transcript has the `session_ref` and
the `run_id` before the audit does — so the audit is not the last line of
defence for them, and narrowing them further would cost the log its ability to
say which run happened without buying a guarantee it does not uniquely own.

What is deliberately absent
---------------------------
No `scrub`, no `sanitize`, no `best_effort`, no `if_you_really_need_it`. A
caller that wants a field across adds it here, by name, in a diff somebody
reviews, and a caller that wants a remote body across cannot have it. The
friction is the feature. An unknown kind — a name added to the table with a
kind this module does not implement — withholds, so the failure mode of a
half-finished edit is a missing field on stderr, not a value in the log.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

OMITTED_MARK = "[omitted:fleet-mcp]"
"""Stands where a value was withheld. A field that vanished silently would make
a truncated record look like a complete one, which is the failure this module
exists to prevent."""

# A control-plane fact is short. A credential is not, and a serialized
# configuration document is not either. The bound is not a redactor — nothing is
# inspected for what it *looks like* — it is a size ceiling on what a *fact* is
# allowed to be. Every string kind is checked against it as well as against its
# own pattern, so a pattern with an open-ended repeat cannot become a payload.
MAX_STRING_CHARS = 200

# Same reasoning for collections: a list of every session on a device is a fact,
# a list of a hundred thousand of them is a payload.
MAX_LIST_ITEMS = 16

# The value kinds. Each one is a *rule about a value*, not a redaction: it
# either recognises the value as the shape that name is for, or it does not.
WITHHELD = "withheld"
COUNT = "count"
FLAG = "flag"
STAMP = "stamp"
CODE = "code"
REF = "ref"
SCOPE = "scope"
URI = "uri"
DIGEST = "digest"
ERROR_CODE = "error_code"

# A control-plane code: `fleet.send.replay`, `oauth.token`, `latest`. Lowercase,
# dotted or dashed, no whitespace and no punctuation a credential would need.
_CODE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")

# A refusal code from `errors.py`: `APPROVAL_REQUIRED`, `SCOPE_DENIED`,
# `WRITE_NOT_GRANTED`. A second kind rather than a wider `_CODE`, because these
# are a different word list with a different owner, not a different case of the
# same one. `error` already means the RFC 6749 name on the OAuth path, and
# folding the two together would either let an uppercase word through a
# lowercase shape (which is what the shape exists to stop) or force this
# surface to lowercase its own codes in the log, so that the record and the
# refusal the caller received no longer say the same word.
#
# Still a closed set: `errors.py` is where these are defined, and a caller
# never supplies one. The shape accepts exactly what that file can produce.
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")

# A record's own timestamp, which this module writes and nothing else does.
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# A reference this surface composes or this deployment minted — a device ref, a
# session ref (`device|provider|native`), a run id, a principal (`oauth:<cid>`).
# Deliberately no whitespace, no `=`, no quote, no backslash: those are what a
# credential embedded in text brings with it. See the module docstring for what
# this shape does *not* exclude, and why that is accepted rather than hidden.
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:|-]*$")

# An OAuth scope list: space-separated scope tokens, which is what RFC 6749
# says and what `claims["scope"].split()` produces. Each token additionally has
# to carry the `namespace:action` shape this deployment mints.
#
# That extra requirement is not decoration. A scope-token grammar loose enough
# to accept any RFC-6749 token accepts `Bearer <hex>` as *two* scope tokens —
# `Bearer` and the credential — so a kind that was meant to be a closed shape
# would relay a pasted header by pure accident of where the space fell. It is
# also the difference between a rule and a coin flip: whether that test passes
# must not depend on whether the random fixture begins with a letter.
_SCOPE_TOKEN = r"[A-Za-z][A-Za-z0-9._-]*(?::[A-Za-z0-9._-]+)+"
_SCOPE = re.compile(rf"^{_SCOPE_TOKEN}(?: {_SCOPE_TOKEN})*$")

# Our own digest marker, recognised so that projecting an already-projected
# record does not hash the hash.
_DIGEST_MARK = re.compile(r"^sha256:[0-9a-f]{12}$")

DIGEST_PREFIX = "sha256:"

KIND_BY_FIELD: dict[str, str] = {
    # when and what
    "ts": STAMP,
    "event": CODE,
    "withheld": COUNT,
    # who
    "principal": REF,
    "subject": REF,
    "client_id": REF,
    "client_name": WITHHELD,  # a client's own free text
    "scope": SCOPE,
    "grant_type": CODE,
    # The RFC error code of a refused `/oauth/register` — `invalid_redirect_uri`,
    # `invalid_client_metadata`. A `code` and not `withheld`, for the same reason
    # `grant_reason` below is: the value is written by this surface from a closed
    # set (the RFC 6749 / 7591 error names), so it can be recorded, and it is the
    # field the refusal body already returns — recording it tells an operator why
    # a client was turned away without telling anyone something the client does
    # not already hold. What must never be recorded is the free text beside it:
    # `error_description` quotes the caller's own input back (for a bad redirect
    # it holds the redirect_uri), and a caller that passes a description under
    # this name loses it to the `code` shape rather than parking it in the log.
    "error": CODE,
    # The refusal a call received, named from `errors.py`'s closed set. A
    # separate field from `error` above because it is a separate vocabulary:
    # that one holds RFC 6749 names in lowercase, this one holds this surface's
    # own constants. Recording the code and not the detail is the line: the
    # detail is prose built around what the caller sent, the code is what an
    # operator greps for when asking whether a door was tested.
    "error_code": ERROR_CODE,
    # what was addressed
    "device_ref": REF,
    "devices": COUNT,
    # How many (device, runtime) pairs a listing walked. A device serving two
    # runtimes is two of these, and the count is the smallest fact that says
    # whether a listing was one machine or one machine twice — which is the
    # question a reader of this log has about a fleet where a machine can serve
    # more than one runtime. A count, like `devices` beside it, and not a list:
    # the pairs are already named in `unreachable` where they are the answer.
    "runtimes": COUNT,
    # The mirrored CloudCLI verb a `mcp.native_call` ran. A NAME from the closed
    # set — never a value the caller typed — so the audit says WHICH verb ran
    # without carrying anything a session held.
    "tool": CODE,
    "session_ref": REF,
    "project": REF,
    "project_id": REF,
    "runtime": CODE,
    "run_id": REF,
    "idempotency_key": DIGEST,  # opaque, caller-chosen, correlatable
    "mode": CODE,
    "limit": COUNT,
    "redactions": COUNT,
    # the grant decision, when a device-level grant is what admitted or refused
    # a call. `verb` and `decision` are this surface's own words, and
    # `grant_reason` is a code this surface writes — never a caller's text —
    # which is why it is a `code` and not `withheld` like the `reason` above it.
    # The two names are separate on purpose: `reason` is a field a caller can
    # fill and therefore is never kept, and folding the grant's closed-set code
    # into it would either lose the code or re-open `reason` to free text.
    "verb": CODE,
    "decision": CODE,
    "grant_reason": CODE,
    # Who signed in at `/oauth/authorize`, and how it went.
    #
    # `username` is a `ref` and not a `code` because a real account name is
    # neither lowercase-dotted nor a closed word. The narrowing that matters is
    # not the kind but the call site: what is recorded here is an entry read
    # from the operator's own password anchor, and the caller's submitted text
    # is recorded ONLY when it matched one. An unknown name — which is exactly
    # what a probe sends, and may be a pasted token — is passed as None and
    # never reaches the log. That is the opposite of the usual direction and it
    # is deliberate: `ref`'s residual (see the module docstring) accepts a
    # token that happens to have the shape, and an unauthenticated form is the
    # one place a stranger chooses the value.
    "username": REF,
    # The closed word this module decided: `ok`, `bad_password`, `bad_totp`,
    # `unknown_user`, `no_totp_secret`, `missing_username`, `throttled`, `csrf`.
    # A code and not `withheld`, for the same reason `grant_reason` is: the
    # value is written here from a closed set, never taken from the request.
    "login_result": CODE,
    # what came back
    "bytes": COUNT,
    "detail": WITHHELD,  # free text a caller filled
    "sessions": COUNT,
    "unreachable": REF,
    "reason": WITHHELD,  # free text a caller filled
    "revoked": FLAG,
    "aborted": FLAG,  # whether an interrupt stopped a run
    "redirect_uri": URI,
    "redirect_uris": URI,
}
"""The audit vocabulary, closed twice over: the names, and for each name the
kind of value it may carry. Adding a name here is the whole procedure for
letting a new field be recorded, and that is deliberate: the alternative — a
permissive `record()` with a reviewer asked to notice a bad call — is a control
that works until the reviewer is tired.

A name with no kind is not in the audit at all, and a kind this module does not
implement withholds, so both halves of the table are closed in the same
direction."""

SAFE_AUDIT_FIELDS: frozenset[str] = frozenset(KIND_BY_FIELD)
"""Derived from the table rather than kept beside it: two lists drift, one list
cannot, and a field that is allowed by name but missing a kind would otherwise
silently lose its value rule."""


def classify_body(raw: str, content_type: str | None = None) -> str:
    """Return a coarse kind for a response body, never the body or its type.

    A caller reading this wants to know whether the far end sent JSON, an SSE
    frame, an HTML error page from something in front of it, or plain text —
    because that distinction says *which* process answered. It does not want the
    media type string, which is remote input, and it certainly does not want the
    body.
    """
    if not raw:
        return "empty"
    ctype = (content_type or "").lower()
    if "event-stream" in ctype:
        return "event-stream"
    if "json" in ctype:
        return "json"
    if "html" in ctype:
        return "html"
    if ctype.startswith("text/"):
        return "plain-text"
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if stripped.startswith("event:") or "\ndata:" in raw:
        return "event-stream"
    if stripped.startswith("<"):
        return "html"
    if raw.isprintable():
        return "plain-text"
    return "binary"


def remote_summary(
    label: str,
    *,
    method: str | None = None,
    status: int | None = None,
    body: str | None = None,
    content_type: str | None = None,
    rpc_code: object = None,
    error_message: object = None,
    error_code: object = None,
    refused: bool = False,
) -> str:
    """Describe a remote failure without relaying a character of remote text.

    `body` and `error_message` are accepted so they can be *measured* — their
    length, and for a body its coarse kind. They are never included. Passing
    them here rather than truncating them at the call site is the point: the
    truncation that used to happen there (`raw[:200]`) was a bound on how much
    of an unknown string got relayed, not a decision not to relay it, and 200
    characters is enough for a header or a key or the first line of a config
    dump.

    `method` is this surface's own outbound method name, so it is relayed: the
    message has to say which call failed.

    `refused` says the far end answered with a refusal rather than with
    something unreadable. A tool result flagged `isError` carries a code and a
    message the device chose; calling that "answered unusably" describes the
    shape of its answer and hides the fact that it answered.

    `error_code` is the exception to "no remote text", and it is a narrow one:
    the far end's `structuredContent.code` is an identifier from a fixed
    upper-case vocabulary (`NO_CLIENT`, `PROJECT_WORKSPACE_ROOT_REQUIRED`), the
    same shape `_ERROR_CODE` already admits into the audit. Relaying it is what
    turns "answered unusably" into a fact a caller can act on without reading
    the device's journal. It is still checked against that shape and the size
    ceiling: a code that is not an enum of that shape is reported as
    `not-an-enum` and never echoed, so a gateway that puts a sentence — or a key
    — in that field does not thereby get it across.
    """
    head = [label]
    if status is not None:
        head.append(f"returned HTTP {status}")
    elif rpc_code is not None or refused:
        head.append("refused the call")
    else:
        head.append("answered unusably")
    if method:
        head.append(f"for {method}")

    measured: list[str] = []
    if body is not None:
        measured.append(f"body {len(body)} bytes, {classify_body(body, content_type)}")
    if error_message is not None:
        measured.append(f"error message {len(str(error_message))} chars")
    if rpc_code is not None:
        code = rpc_code if isinstance(rpc_code, int) and not isinstance(rpc_code, bool) else "not-an-integer"
        measured.append(f"code {code}")
    if error_code is not None:
        named = error_code if _shaped(error_code, _ERROR_CODE) else "not-an-enum"
        measured.append(f"error code {named}")
    tail = f" ({'; '.join(measured)})" if measured else ""

    return (
        f"{' '.join(head)}{tail}. "
        "The text itself is withheld: remote text is where an upstream credential "
        "gets quoted back, and this surface did not write it. Read it on the "
        "device that produced it, where it is already visible to whoever runs "
        "that device."
    )


def digest_of(value: Any) -> str:
    """A stable, one-way fingerprint of one value.

    Used where the audit needs to say "the same one as before" but has no
    business holding the value. Twelve hex characters of SHA-256 is ample to
    distinguish the keys a deployment sees and gives nothing back: it is not a
    salt-and-KDF, so a value a guesser already has a shortlist for is a value
    they can confirm — which is the same exposure as the rest of this file
    already has, and far less than the value itself.
    """
    return DIGEST_PREFIX + hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _uri_origin(value: str) -> str | None:
    """`scheme://host[:port]`, or `None` if this is not that.

    The path, the query, the fragment and the userinfo component are dropped,
    not truncated. The query is where an authorization code or an access token
    is carried in the flows this surface speaks, and userinfo is where a
    credential is by definition; the path distinguishes one endpoint from
    another on the same host, which is not a fact the audit needs and is a
    string the registrant chose.
    """
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    return f"{parts.scheme}://{host}:{port}" if port is not None else f"{parts.scheme}://{host}"


def _shaped(value: str, pattern: re.Pattern[str]) -> bool:
    return len(value) <= MAX_STRING_CHARS and pattern.match(value) is not None


def project_value(value: Any, *, kind: str = WITHHELD) -> Any:
    """Reduce one audit value to a fact *of the kind its field name declares*.

    The kind is not optional in practice: `project_record` supplies the one from
    `KIND_BY_FIELD`, and the default — `withheld` — is the one that keeps
    nothing, so a caller that reaches here without a kind loses the value rather
    than passing it. That default used to be the opposite, and the difference is
    the whole finding: a value was kept because it was short, and a credential
    is short.

    A list is projected item by item under the same kind, because a list of one
    kind of fact is a fact. A nested object is refused outright rather than
    walked: a dict in an audit field is a document — a config section, a parsed
    credential file, a provider response — and walking it would mean this module
    had to know which of its keys were safe, which is the guessing game it
    exists to end.
    """
    if value is None:
        return None
    if isinstance(value, list | tuple):
        return [project_value(item, kind=kind) for item in list(value)[:MAX_LIST_ITEMS]]

    if kind == COUNT:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 \
            else OMITTED_MARK
    if kind == FLAG:
        return value if isinstance(value, bool) else OMITTED_MARK
    if kind == STAMP:
        return value if isinstance(value, str) and _shaped(value, _STAMP) else OMITTED_MARK
    if kind == CODE:
        return value if isinstance(value, str) and _shaped(value, _CODE) else OMITTED_MARK
    if kind == ERROR_CODE:
        return value if isinstance(value, str) and _shaped(value, _ERROR_CODE) else OMITTED_MARK
    if kind == REF:
        return value if isinstance(value, str) and _shaped(value, _REF) else OMITTED_MARK
    if kind == SCOPE:
        return value if isinstance(value, str) and _shaped(value, _SCOPE) else OMITTED_MARK
    if kind == URI:
        if not isinstance(value, str) or len(value) > MAX_STRING_CHARS:
            return OMITTED_MARK
        origin = _uri_origin(value)
        return origin if origin is not None else OMITTED_MARK
    if kind == DIGEST:
        if isinstance(value, str) and _DIGEST_MARK.match(value):
            return value  # already ours; hashing it again would break the match
        if isinstance(value, str | int) and not isinstance(value, bool):
            return digest_of(value)
        return OMITTED_MARK
    # WITHHELD, and any kind this build does not implement: keep nothing.
    return OMITTED_MARK


def project_record(
    record: Mapping[str, Any], *, allowed: Iterable[str] | None = None
) -> tuple[dict[str, Any], int]:
    """Project one record onto the allowlist. Returns `(projected, withheld)`.

    `withheld` counts both kinds of omission — a field name that is not on the
    list, and a value that was not the shape its kind is for — so an operator
    reading the log can tell "nothing unusual happened" from "something was
    dropped here".

    The count is carried in the record itself as `withheld`. Re-projecting an
    already-projected record is idempotent: the carried count is preserved
    rather than counted again, and a value that is already the omitted mark is
    not counted a second time, so reading the file back does not increment it.

    `allowed` is for tests that want to exercise one field in isolation; it
    accepts names, and the kinds still come from `KIND_BY_FIELD`, so it cannot
    be used to smuggle a *kind* past this function.
    """
    allow = SAFE_AUDIT_FIELDS if allowed is None else frozenset(allowed)
    prior = record.get("withheld")
    if not isinstance(prior, int) or isinstance(prior, bool) or prior < 0:
        prior = 0

    projected: dict[str, Any] = {}
    withheld = prior
    for key, value in record.items():
        if key == "withheld":
            continue
        kind = KIND_BY_FIELD.get(key)
        if key not in allow or kind is None:
            withheld += 1
            continue
        reduced = project_value(value, kind=kind)
        already_sealed = isinstance(value, str) and value == OMITTED_MARK
        if reduced == OMITTED_MARK and not already_sealed:
            withheld += 1
        projected[key] = reduced
    if withheld:
        projected["withheld"] = withheld
    return projected, withheld
