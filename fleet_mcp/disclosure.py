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

What is deliberately absent
---------------------------
No `scrub`, no `sanitize`, no `best_effort`, no `if_you_really_need_it`. A
caller that wants a field across adds it here, by name, in a diff somebody
reviews, and a caller that wants a remote body across cannot have it. The
friction is the feature.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

OMITTED_MARK = "[omitted:fleet-mcp]"
"""Stands where a value was withheld. A field that vanished silently would make
a truncated record look like a complete one, which is the failure this module
exists to prevent."""

# A control-plane fact is short. A credential is not, and a serialized
# configuration document is not either. The bound is not a redactor — nothing is
# inspected for what it *looks like* — it is a size ceiling on what a *fact* is
# allowed to be.
MAX_STRING_CHARS = 200

# Same reasoning for collections: a list of every session on a device is a fact,
# a list of a hundred thousand of them is a payload.
MAX_LIST_ITEMS = 16

SAFE_AUDIT_FIELDS: frozenset[str] = frozenset(
    {
        # when and what
        "ts",
        "event",
        "withheld",
        # who
        "principal",
        "subject",
        "client_id",
        "client_name",
        "scope",
        "grant_type",
        # what was addressed
        "device_ref",
        "devices",
        "session_ref",
        "project",
        "run_id",
        "idempotency_key",
        "mode",
        "limit",
        "redactions",
        # what came back
        "bytes",
        "detail",
        "sessions",
        "unreachable",
        "reason",
        "revoked",
        "redirect_uri",
        "redirect_uris",
    }
)
"""The audit vocabulary, closed. Adding a name here is the whole procedure for
letting a new field be recorded, and that is deliberate: the alternative — a
permissive `record()` with a reviewer asked to notice a bad call — is a control
that works until the reviewer is tired."""


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
    """
    head = [label]
    if status is not None:
        head.append(f"returned HTTP {status}")
    elif rpc_code is not None:
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
    tail = f" ({'; '.join(measured)})" if measured else ""

    return (
        f"{' '.join(head)}{tail}. "
        "The text itself is withheld: remote text is where an upstream credential "
        "gets quoted back, and this surface did not write it. Read it on the "
        "device that produced it, where it is already visible to whoever runs "
        "that device."
    )


def project_value(value: Any) -> Any:
    """Reduce one audit value to a fact: a short scalar, or a list of them.

    A nested object is refused outright rather than walked. A dict in an audit
    field is a document — a config section, a parsed credential file, a provider
    response — and walking it would mean this module had to know which of its
    keys were safe, which is the guessing game it exists to end.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING_CHARS else OMITTED_MARK
    if isinstance(value, list | tuple):
        return [project_value(item) for item in list(value)[:MAX_LIST_ITEMS]]
    return OMITTED_MARK


def project_record(
    record: Mapping[str, Any], *, allowed: Iterable[str] = SAFE_AUDIT_FIELDS
) -> tuple[dict[str, Any], int]:
    """Project one record onto the allowlist. Returns `(projected, withheld)`.

    `withheld` counts both kinds of omission — a field name that is not on the
    list, and a value that was too large or too structured to be a fact — so an
    operator reading the log can tell "nothing unusual happened" from "something
    was dropped here".

    The count is carried in the record itself as `withheld`. Re-projecting an
    already-projected record is idempotent: the carried count is preserved
    rather than counted again, so reading the file back does not increment it.
    """
    allow = set(allowed)
    prior = record.get("withheld")
    if not isinstance(prior, int) or isinstance(prior, bool) or prior < 0:
        prior = 0

    projected: dict[str, Any] = {}
    withheld = prior
    for key, value in record.items():
        if key == "withheld":
            continue
        if key not in allow:
            withheld += 1
            continue
        reduced = project_value(value)
        if reduced == OMITTED_MARK:
            withheld += 1
        projected[key] = reduced
    if withheld:
        projected["withheld"] = withheld
    return projected, withheld
