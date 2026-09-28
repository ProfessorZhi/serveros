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
    if age >= int(window["window_seconds"]):
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
