#!/usr/bin/env python3
"""Trusted actor context: who is behind a request, and what established it.

    python3 actor.py --provider botmux-human-control --actor hwz \
        --transport feishu --transport-identity verified --conversation oc_x \
        --message om_y --observed-at 2026-09-14T00:00:00Z

Implements `actor-context-contract.yaml`. Read that file first; the reasoning
for every field is there and is not repeated here.

The one property this module exists to hold
-------------------------------------------
**Identity is a lookup, not an argument.**

An Agent runtime that can say "the human is hwz" can approve its own
mutations, and every admission gate behind it becomes decoration. So there is
no function below that accepts an actor from a caller and returns a context.
`build()` is called by ServerOS with what a PROVIDER reported, and the caller's
half of the protocol is `resolve()` — present a ref, get a record or a miss.

The three refusals are the security boundary, and each is a separate test:

    forged_payload()   the request carried `human` / `actor` / `approved`
    parse_context_ref  the ref is not a context ref at all
    (a miss)           the ref is well-formed and ServerOS never minted it

The third is not an error case. It is the normal consequence of a caller
inventing a handle, and it is reported as MISS rather than raised, so that no
caller can catch a "not found" and substitute a default.

Like the other read-only kernels, this module runs no probe and opens no
socket. It does read its own contract, because the freshness window is a
number in a file and a second copy of it here would be a second authority.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read the actor context contract\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "actor-context-contract.yaml"
DELEGATION_TRUST = Path.home() / ".config/serveros/human-control-trust.json"
DELEGATION_EVIDENCE_SCHEMA = "serveros/human-delegation.v1"
ISSUANCE_SCHEMA = "serveros/delegation-issuance.v1"

CONTEXT_PREFIX = "human-context:"

# `ACTOR_PREFIX = "human:"` used to live here, and its removal is not tidying.
# Its two readers were the two functions below that now build a ref from
# `ACTOR_NAMESPACES`, and a constant spelling the old shape invites exactly the
# mistake this change exists to fix: a ref assembled by prefixing looks correct
# while it silently cannot say where its own segment boundaries are. A named
# constant is more inviting than an f-string, not less.

# The namespaces an actor ref may carry. The namespace is not decoration: it
# names WHERE the id came from, so two ids issued by different systems cannot
# be read as the same person by a reader that only saw the string.
#
#   human    the original form. The id is opaque and the namespace says only
#            that a human was identified — not by what, and not which of that
#            system's id spaces the value belongs to.
#   feishu   a Lark id, with the kind naming the id space. `feishu:union:<id>`
#            is a `union_id`: developer-scoped, the same value in every app of
#            the tenant, and readable without the field-level permission that
#            gates an email.
#
# `human` is NOT migrated to the new spelling. Contexts already minted carry
# it, `runtime/agent_control_store.py#for_actor_action` matches approvals on the
# ref verbatim, and a grammar change that made live records unreadable would be
# a rename wearing a security change's clothes.
ACTOR_NAMESPACES = ("human", "feishu")

# The id spaces a namespace may name, closed. `open` is spelled here so that the
# kind can be judged rather than guessed at, but it is the kind a reader must
# be most careful with: an `ou_` open id is app-scoped, so a context carrying
# one identifies a different person to every app that reads it. A provider that
# reports one is reporting evidence about ITS view, and the ref says so.
ACTOR_ID_KINDS = ("union", "open", "email")

# The fields a REQUEST is forbidden to carry. Not "ignored if present": refused
# by name, because a silently dropped field teaches its author that the field
# was read and accepted, and the next version of the client keeps sending it.
FORBIDDEN_CALLER_KEYS = ("human", "actor", "actor_ref", "approved", "user",
                         "user_id", "open_id", "email")

# What a provider must report for ServerOS to mint a context. Everything here
# comes from the provider; nothing here can be supplied by the agent that will
# later present the resulting ref.
PROVIDER_REPORT_FIELDS = (
    "human_control_provider", "transport", "transport_identity",
    "conversation_ref", "message_ref", "observed_at", "provider_status",
    "provenance", "actor_ref",
)


class Rejected(ValueError):
    """The record or request cannot be used — as opposed to "no such context",
    which is a lookup result and is returned rather than raised."""


class ForgedContext(Rejected):
    """A request carried identity it is not allowed to carry."""


def contract() -> dict:
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise Rejected(f"{CONTRACT} must contain a mapping")
    return data


def delegation_trust(path: Path | None = None) -> dict:
    """Operator-owned public keys; a request cannot choose its trust root.

    `path` exists so that a fixture can run this reader against a file it owns.
    `verify_delegation` deliberately does NOT take one and always calls this
    with the operator's own location: a trust root that an admission path could
    point elsewhere would be a trust root a caller selects, which is the thing
    this function's docstring rules out. Reading the shape of a chosen file is
    a different act from deciding whether a signature under it is trusted, and
    only the first one is parameterizable.
    """
    target = Path(path) if path is not None else DELEGATION_TRUST
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Rejected("DELEGATION_PROVIDER_UNBOUND: human-control trust binding unavailable") from exc
    if not isinstance(data, dict) or data.get("schema") != "serveros/human-control-trust.v1":
        raise Rejected("DELEGATION_PROVIDER_UNBOUND: invalid human-control trust binding")
    return data


def signed_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def verify_delegation(evidence: dict, *, now: str) -> tuple[dict, dict]:
    """Verify a provider's approval of this exact scope, fresh at issuance."""
    if not isinstance(evidence, dict) or set(evidence) != {"payload", "signature"}:
        raise Rejected("DELEGATION_EVIDENCE_INVALID: signed provider evidence required")
    payload = evidence["payload"]
    fields = {"schema", "provider_id", "key_id", "audience", "issued_at", "actor_report", "grant"}
    if not isinstance(payload, dict) or set(payload) != fields or payload.get("schema") != DELEGATION_EVIDENCE_SCHEMA:
        raise Rejected("DELEGATION_EVIDENCE_INVALID: unknown signed payload")
    if any(not isinstance(payload[k], str) or not payload[k].strip()
           for k in ("provider_id", "key_id", "audience", "issued_at")):
        raise Rejected("DELEGATION_EVIDENCE_INVALID: provider, key, audience and clock must be named")
    trust = delegation_trust()
    providers = trust.get("providers")
    provider = providers.get(payload.get("provider_id"), {}) if isinstance(providers, dict) else {}
    keys = provider.get("keys") if isinstance(provider, dict) else None
    key = keys.get(payload.get("key_id"), {}) if isinstance(keys, dict) else {}
    if (not isinstance(key, dict) or key.get("enabled") is not True
            or not trust.get("audience") or payload.get("audience") != trust["audience"]):
        raise Rejected("DELEGATION_PROVIDER_UNBOUND: provider/key/audience not trusted")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise Rejected("DELEGATION_VERIFIER_UNAVAILABLE: Ed25519 support is required") from exc
    try:
        public = base64.b64decode(key["public_key"], validate=True)
        signature = base64.b64decode(evidence["signature"], validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, signed_bytes(payload))
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise Rejected("DELEGATION_SIGNATURE_INVALID: provider evidence did not verify") from exc
    report, grant = payload["actor_report"], payload["grant"]
    if (not isinstance(report, dict) or set(report) != set(PROVIDER_REPORT_FIELDS)
            or report.get("human_control_provider") != payload["provider_id"]):
        raise Rejected("DELEGATION_EVIDENCE_INVALID: actor report does not identify this provider")
    issued = _parse(payload["issued_at"])
    if not _parse(report["observed_at"]) <= issued <= _parse(now):
        raise Rejected("DELEGATION_EVIDENCE_INVALID: issuance clock precedes evidence or is in the future")
    context = build(report, now=payload["issued_at"])
    verdict = authorizes(context, now)
    if not verdict["authorizes"]:
        raise Rejected("DELEGATION_ISSUANCE_NOT_FRESH: " + verdict["because"])
    allowed = {"grant_ref", "valid_from", "expires_at", "projects", "items", "actions",
               "scope_digests", "covers", "budget", "engineering"}
    if not isinstance(grant, dict) or set(grant) - allowed:
        raise Rejected("DELEGATION_SCOPE_INVALID: unknown or owner-only grant fields")
    if not isinstance(grant.get("grant_ref"), str) or not grant["grant_ref"].strip():
        raise Rejected("DELEGATION_SCOPE_INVALID: grant_ref required")
    for field in ("projects", "items", "actions", "covers"):
        value = grant.get(field, [])
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            raise Rejected(f"DELEGATION_SCOPE_INVALID: {field} must be a string list")
    if not grant.get("actions") or not (grant.get("items") or grant.get("covers")):
        raise Rejected("DELEGATION_SCOPE_INVALID: an explicit work or subject scope and actions are required")
    engineering = "engineering.execute" in grant["actions"]
    if engineering != ("engineering" in grant) or (engineering and not isinstance(grant["engineering"], dict)):
        raise Rejected("DELEGATION_SCOPE_INVALID: engineering.execute requires its signed engineering binding")
    if grant.get("items"):
        digests = grant.get("scope_digests")
        if not grant.get("projects") or not isinstance(digests, dict) or any(
                not isinstance(digests.get(item), str) or not digests[item].strip() for item in grant["items"]):
            raise Rejected("DELEGATION_SCOPE_INVALID: work scope needs projects and each item's scope_digest")
    budget = grant.get("budget")
    if (not isinstance(budget, dict) or budget.get("exhausted") is not False
            or any(type(budget.get(k)) is not int or budget[k] <= 0
                   for k in ("wall_seconds", "max_attempts", "max_output_bytes"))):
        raise Rejected("DELEGATION_SCOPE_INVALID: positive bounded budget required")
    if not _parse(grant.get("valid_from")) < _parse(grant.get("expires_at")) or _parse(grant["expires_at"]) <= _parse(now):
        raise Rejected("DELEGATION_SCOPE_INVALID: delegation window is invalid or already expired")
    return {**grant, "delegated_by": context["context_ref"]}, context


def issued_delegation(grant: dict, *, now: str) -> dict:
    """Validate durable issuance without extending the signed scope or actor TTL."""
    issuance = grant.get("issuance")
    if (not isinstance(issuance, dict) or set(issuance) != {"schema", "accepted_at", "evidence"}
            or issuance.get("schema") != ISSUANCE_SCHEMA):
        raise Rejected("DELEGATION_ISSUANCE_INVALID: owner issuance receipt required")
    at = issuance["accepted_at"]
    if _parse(at) > _parse(now):
        raise Rejected("DELEGATION_ISSUANCE_INVALID: receipt is ahead of execution clock")
    approved, context = verify_delegation(issuance["evidence"], now=at)
    actual = {k: v for k, v in grant.items() if k not in ("issuance", "ref", "revoked", "revoked_at", "revoked_reason")}
    if actual != approved:
        raise Rejected("DELEGATION_SCOPE_CHANGED: stored grant differs from provider-approved scope")
    return {**authorizes(context, at), "issuance_ref": context["context_ref"],
            "accepted_at": at, "basis": "provider-signed-delegation"}


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------

def _segment(value: str) -> str:
    return quote(str(value), safe="")


def context_ref(provider_id: str, digest: str) -> str:
    return CONTEXT_PREFIX + "/".join(_segment(v) for v in (provider_id, digest))


def parse_context_ref(ref: str) -> tuple[str, str]:
    """(provider_id, digest). Raises when this is not a context ref.

    A caller that passes a session ref, a device ref or a bare string gets a
    named refusal rather than a lookup that misses and reads like "expired".
    """
    if not str(ref).startswith(CONTEXT_PREFIX):
        raise Rejected(f"{ref!r} is not a trusted actor context ref")
    parts = str(ref)[len(CONTEXT_PREFIX):].split("/")
    if len(parts) != 2 or not all(parts):
        raise Rejected(f"malformed trusted actor context ref: {ref!r}")
    return unquote(parts[0]), unquote(parts[1])


def _typed_prefixes() -> tuple[str, ...]:
    return tuple(f"{name}:" for name in ACTOR_NAMESPACES)


def actor_ref(actor_id: str, *, namespace: str = "human",
              kind: str | None = None) -> str:
    """A typed actor ref: `<namespace>:<id>`, or `<namespace>:<kind>:<id>`.

    **Every segment is encoded on its own**, and that is the whole reason this
    is a function instead of an f-string. A ref built by interpolation cannot
    distinguish an id of `union:a` from a kind `union` with an id `a`, so a
    value the provider read out of a document would be free to move the
    boundaries of the very string a reader parses to find out what kind of id
    it is holding. Here the separator is written by this function and nowhere
    else: a `:` inside a value is quoted into its own segment and can never
    become a boundary.

    `kind` is None rather than defaulted. A namespace that names a system
    without naming which of its id spaces the value came from is the original
    `human:` shape, and inventing a kind for it would put a claim into the ref
    that no provider made.
    """
    if not str(actor_id).strip():
        raise Rejected("an actor_ref needs a non-empty actor id")
    if namespace not in ACTOR_NAMESPACES:
        raise Rejected(f"{namespace!r} is not an actor ref namespace; known: "
                       + ", ".join(ACTOR_NAMESPACES))
    if kind is not None and kind not in ACTOR_ID_KINDS:
        raise Rejected(f"{kind!r} is not an actor id kind; known: "
                       + ", ".join(ACTOR_ID_KINDS))
    if str(actor_id).startswith(_typed_prefixes()):
        raise Rejected(f"{actor_id!r} is already a typed actor ref")
    segments = [namespace] + ([kind] if kind is not None else []) + [str(actor_id)]
    return ":".join(_segment(segment) for segment in segments)


def parse_actor_ref(ref: str) -> tuple[str, str | None, str]:
    """(namespace, kind, actor_id) from a typed actor ref. Raises when the
    value is not one.

    Two shapes, and no third: `<namespace>:<id>` and
    `<namespace>:<kind>:<id>`. A segment count outside those two is refused
    rather than read left-to-right, because a ref with a hand-written `:` in it
    has two readings and a parser that picks one of them is choosing an actor
    for a caller that wrote something ambiguous.

    A namespace with a kind must name a known kind. `human:opaque:x` is
    therefore refused rather than read as an id of `opaque:x`: the word after
    `human:` would otherwise be an unvalidated claim about which id space the
    value came from.
    """
    text = str(ref)
    namespace = next((name for name in ACTOR_NAMESPACES
                      if text.startswith(f"{name}:")), None)
    if namespace is None:
        raise Rejected(f"{ref!r} is not a typed actor ref")
    parts = text[len(namespace) + 1:].split(":")
    if len(parts) == 1:
        kind, id_segment = None, parts[0]
    elif len(parts) == 2:
        kind, id_segment = parts[0], parts[1]
        if kind not in ACTOR_ID_KINDS:
            raise Rejected(f"{ref!r} names actor id kind {kind!r}; known: "
                           + ", ".join(ACTOR_ID_KINDS))
    else:
        raise Rejected(f"malformed actor ref: {ref!r}")
    actor_id = unquote(id_segment)
    if not actor_id:
        raise Rejected(f"malformed actor ref: {ref!r}")
    return namespace, kind, actor_id


def normalize_actor_ref(value: str) -> str:
    """A well-formed typed actor ref, from either an id or a ref already typed.

    For a CLI that takes `--actor` from a person, where both "the id" and "the
    whole ref" are reasonable things to type and only one of them should reach
    a record.

    A value carrying a known namespace MUST parse. Wrapping an unparseable one
    as `human:<value>` would turn `feishu:` into an id called "feishu:" and
    record a different actor than the caller named — which is why the branch
    below is on "does this look typed", not on "did parsing happen to fail".
    """
    text = str(value)
    if text.startswith(_typed_prefixes()):
        namespace, kind, actor_id = parse_actor_ref(text)
        return actor_ref(actor_id, namespace=namespace, kind=kind)
    return actor_ref(text)


# ---------------------------------------------------------------------------
# Digests and forgery
# ---------------------------------------------------------------------------

def digest_of(report: dict) -> str:
    """The identity half of a provider report, hashed.

    Deliberately over the provider's OWN fields only. If a caller-supplied
    value entered this hash, a caller could steer the ref, and two reports of
    one event from one provider would stop being the same context — which is
    what makes replay idempotent rather than a second context each time.
    """
    material = json.dumps(
        {k: report.get(k) for k in PROVIDER_REPORT_FIELDS},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def forged_payload(payload: dict) -> list[str]:
    """Which forbidden keys the payload carried, sorted. Empty means none.

    Returned rather than raised so the caller can name every offending key at
    once: a client that sends three of them should learn about three, not
    about whichever the loop reached first.
    """
    return sorted(k for k in (payload or {}) if k.lower() in FORBIDDEN_CALLER_KEYS)


def refuse_if_forged(payload: dict) -> None:
    offending = forged_payload(payload)
    if offending:
        raise ForgedContext(
            "this call may not carry identity: " + ", ".join(offending) + ". "
            "Identity is resolved from the context_ref against evidence the "
            "HumanControlProvider reported; a caller that states its own "
            "actor is stating something ServerOS does not read."
        )


# ---------------------------------------------------------------------------
# Building (ServerOS side, from a provider report)
# ---------------------------------------------------------------------------

def build(report: dict, *, now: str, nonce: str | None = None) -> dict:
    """One trusted actor context, from what a provider reported.

    `now` is supplied rather than read, so that the expiry is a function of the
    caller's clock — the same clock the validation will later be checked
    against. A module that read its own clock would produce a record whose
    window nobody could reproduce.
    """
    # Checked before the field sweep, so that the reason a blocked provider
    # produces nothing reads as what it is — "nobody was identified" — rather
    # than as a list of fields that happen to be empty together. A context is
    # always ABOUT somebody; with no identified human there is no context to
    # mint, and this is where that rule lives.
    if not report.get("actor_ref"):
        raise Rejected(
            "this provider report identifies no human (provider_status "
            f"{report.get('provider_status')!r}), so there is no actor for a "
            "trusted context to be about. A provider that declined to verify "
            "produces no context — not a context with an empty actor.")

    missing = [f for f in PROVIDER_REPORT_FIELDS if not report.get(f)]
    if missing:
        raise Rejected(
            "a provider report missing " + ", ".join(missing) +
            " cannot become a trusted context: the fields it did not carry are "
            "exactly the ones that make it evidence")

    status = report["provider_status"]
    allowed = contract()["fields"]["provider_status"]["values"]
    if status not in allowed:
        raise Rejected(f"provider_status {status!r} is not one of {allowed}")

    provenance = report["provenance"]
    allowed_prov = contract()["fields"]["provenance"]["values"]
    if provenance not in allowed_prov:
        raise Rejected(f"provenance {provenance!r} is not one of {allowed_prov}")

    # A provider that did not verify cannot produce a FACT, and a provider that
    # verified must not report UNKNOWN. Both directions are refusals: the first
    # is overclaiming by ServerOS, the second is a provider that answered and
    # then declined to be read.
    if status == "VERIFIED" and provenance != "FACT":
        raise Rejected(
            f"a VERIFIED report must carry provenance FACT, not {provenance!r}")
    if status != "VERIFIED" and provenance == "FACT":
        raise Rejected(
            f"provider_status {status!r} claims no verification, so it cannot "
            "carry provenance FACT")

    # Parsed into its three parts and rebuilt from them, rather than copied
    # through: the rebuild is what re-encodes each segment, so a report carrying
    # a hand-written ref cannot put a separator into the record.
    namespace, kind, actor_id = parse_actor_ref(report["actor_ref"])
    digest = digest_of(report)
    observed_at = report["observed_at"]
    window = int(contract()["freshness"]["window_seconds"])

    # A report dated ahead of the clock that is reading it is not an early one.
    # `expires_at` below is derived from `observed_at`, so accepting a future
    # stamp mints a window that has not opened yet and will still be open long
    # after the observation it claims is old: `freshness` would then call it
    # FRESH (`age` is negative, and a negative age is below every threshold),
    # and it would stay FRESH for `window` seconds past a moment that has not
    # arrived. REFUSED rather than clamped, because moving the stamp to `now`
    # would produce a context about an event nobody observed, which is the
    # confusion this whole plane exists to prevent.
    #
    # This is the only place that mints a context, so the refusal here covers
    # every path that writes one; `freshness` below is hardened separately for
    # the records that are already on disk.
    if _parse(observed_at) > _parse(now):
        raise Rejected(
            f"this report says the actor was observed at {observed_at!r}, which "
            f"is ahead of the clock this ServerOS read ({now!r}). A trusted "
            f"context cannot be minted about an observation that has not "
            f"happened yet: its window is computed from that timestamp, so a "
            f"future one would open later than the event and stay open after it")

    record = {
        "schema": contract()["schema"],
        "context_ref": context_ref(report["human_control_provider"], digest),
        "actor_ref": actor_ref(actor_id, namespace=namespace, kind=kind),
        "human_control_provider": report["human_control_provider"],
        "transport": report["transport"],
        "transport_identity": report["transport_identity"],
        "conversation_ref": report["conversation_ref"],
        "message_ref": report["message_ref"],
        "observed_at": observed_at,
        "attached_at": now,
        "expires_at": _plus_seconds(observed_at, window),
        "provenance": provenance,
        "provider_status": status,
        "provider_evidence": {k: report.get(k) for k in PROVIDER_REPORT_FIELDS},
    }
    if nonce:
        # Not part of the digest — a nonce that changed the ref would make the
        # same event mint a new context every time it was re-reported, which is
        # the opposite of what idempotency needs. It is carried for audit only.
        record["nonce"] = nonce
    return validate(record)


def _plus_seconds(stamp: str, seconds: int) -> str:
    return (_parse(stamp) + timedelta(seconds=seconds)).isoformat()


def _parse(stamp: str) -> datetime:
    """Parsed, or a named refusal.

    `datetime.fromisoformat` raises a bare ValueError, which is this module's
    `Rejected`'s PARENT — so a caller catching `Rejected` would not catch it,
    and the daemon would report `ValueError` where every other refusal on this
    path names the rule that was broken. Wrapped here rather than at the call
    sites, because there are three of them and the fourth would forget.
    """
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise Rejected(
            f"{stamp!r} is not a timestamp, so this record cannot be aged — and "
            "a record that cannot be aged can never be found stale") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Validation and freshness (read side)
# ---------------------------------------------------------------------------

def validate(record: dict) -> dict:
    """Refuse a record that is not the declared shape. Returns it unchanged.

    Structural only: freshness is NOT checked here. A stored record does not
    become invalid — it becomes old, and the reader is the one with the clock.
    """
    if not isinstance(record, dict):
        raise Rejected("a trusted actor context must be a mapping")
    for name, spec in contract()["fields"].items():
        if spec.get("required") and not record.get(name):
            raise Rejected(f"trusted actor context has no {name!r}")
    parse_context_ref(record["context_ref"])
    parse_actor_ref(record["actor_ref"])
    if record["provenance"] not in contract()["fields"]["provenance"]["values"]:
        raise Rejected(f"bad provenance {record['provenance']!r}")
    # Parsed, not merely present: an unparseable stamp is a record that cannot
    # be aged, and a record that cannot be aged can never be found stale.
    observed = _parse(record["observed_at"])
    expires = _parse(record["expires_at"])
    if expires <= observed:
        raise Rejected(
            f"expires_at {record['expires_at']!r} is not after observed_at "
            f"{record['observed_at']!r}; a context with no window is not a window")
    return record


def freshness(record: dict, now: str) -> dict:
    """FRESH / STALE / EXPIRED, computed now, from the record's own clock.

    Never read from the record: a stored verdict is a claim that was true when
    it was written, and this plane exists precisely because such claims get
    read later.

    STALE is not a kind of FRESH. It may be displayed — "a person said this,
    some minutes ago" is useful to a reader — and it authorizes nothing. The
    two thresholds are separate because "ask again" and "this is dead" are
    different instructions to whoever receives them.
    """
    observed = _parse(record["observed_at"])
    current = _parse(now)
    age = (current - observed).total_seconds()
    window = contract()["freshness"]
    if age < 0:
        # The reading clock is BEHIND the observation. That is not "very
        # fresh" — it means the two clocks disagree about which instant this
        # record belongs to, and nothing here can tell which one is right. The
        # fail-safe bucket is the one that authorizes nothing, so it is
        # EXPIRED; `because` names the real reason rather than letting the
        # record sit in a neighbour's label. `build` refuses to mint such a
        # record at all, so reaching this branch means one was written by
        # something else.
        state = "EXPIRED"
    elif age >= int(window["window_seconds"]):
        state = "EXPIRED"
    elif age >= int(window["stale_after_seconds"]):
        state = "STALE"
    else:
        state = "FRESH"
    return {
        "state": state,
        "age_seconds": age,
        "stale_after_seconds": int(window["stale_after_seconds"]),
        "window_seconds": int(window["window_seconds"]),
        "because": _because(state, age, int(window["window_seconds"])),
    }


def _because(state: str, age: float, window: int) -> str:
    if age < 0:
        # Named first, and independently of `state`: whatever bucket the record
        # landed in, the fact a reader needs is that the arithmetic ran
        # backwards. Saying "past the 900s window" about a record dated in the
        # future would be exactly the kind of confident-but-wrong sentence this
        # module refuses to emit elsewhere.
        return (f"observed {-int(age)}s in the FUTURE of the clock reading it, "
                f"so this record cannot be aged at all — it is not new, and "
                f"nothing that cannot be aged may authorize")
    if state == "FRESH":
        return "observed inside the fresh window"
    if state == "STALE":
        return ("observed, but not recently enough to authorize a mutation "
                "without asking again")
    return f"observed {int(age)}s ago, past the {window}s window"


def authorizes(record: dict, now: str) -> dict:
    """May this context authorize a mutation right now? A verdict, with why.

    Kept separate from `freshness` so that the one caller that needs the strong
    answer asks the strong question. A reader showing a human what happened
    wants `freshness`; an admission gate wants this.
    """
    fresh = freshness(record, now)
    ok = (fresh["state"] == "FRESH" and record["provenance"] == "FACT"
          and record["provider_status"] == "VERIFIED")
    return {**fresh, "authorizes": ok,
            "provenance": record["provenance"],
            "provider_status": record["provider_status"],
            "because": fresh["because"] if ok else _why_not(record, fresh)}


def _why_not(record: dict, fresh: dict) -> str:
    if record["provider_status"] != "VERIFIED":
        return (f"the provider did not verify this actor "
                f"({record['provider_status']}) — {record.get('transport_identity')}")
    if record["provenance"] != "FACT":
        return f"the context carries provenance {record['provenance']}, not FACT"
    return fresh["because"]


# ---------------------------------------------------------------------------
# The caller's half
# ---------------------------------------------------------------------------

def resolve(ref: str, store: dict) -> dict:
    """Look a presented ref up in what ServerOS actually minted.

    Returns `{"found": False, "because": ...}` on a miss. It does NOT raise and
    it has no default: a caller that invented a ref must not be able to catch
    an exception and continue with a substitute context, and a caller that
    passed an empty string must not receive the most recent one.
    """
    try:
        _provider, _digest = parse_context_ref(ref)
    except Rejected as exc:
        return {"found": False, "context_ref": str(ref), "because": str(exc)}
    record = (store or {}).get(str(ref))
    if record is None:
        return {"found": False, "context_ref": str(ref),
                "because": ("no context with this ref was reported by any "
                            "HumanControlProvider")}
    return {"found": True, "context": record}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider")
    parser.add_argument("--actor")
    parser.add_argument("--transport")
    parser.add_argument("--transport-identity")
    parser.add_argument("--conversation")
    parser.add_argument("--message")
    parser.add_argument("--observed-at")
    parser.add_argument("--status", default="VERIFIED")
    parser.add_argument("--now")
    args = parser.parse_args(argv)

    now = args.now or datetime.now(timezone.utc).isoformat()
    if not (args.provider and args.actor and args.conversation):
        parser.error("--provider, --actor and --conversation are required")
    record = build({
        "human_control_provider": args.provider,
        "actor_ref": normalize_actor_ref(args.actor),
        "transport": args.transport or "feishu",
        "transport_identity": args.transport_identity or "unstated",
        "conversation_ref": args.conversation,
        "message_ref": args.message or "unstated",
        "observed_at": args.observed_at or now,
        "provider_status": args.status,
        "provenance": "FACT" if args.status == "VERIFIED" else "UNKNOWN",
    }, now=now)
    record["freshness"] = freshness(record, now)
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
