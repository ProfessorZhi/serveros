#!/usr/bin/env python3
"""Human approval: the binding, the decision, and the consumption.

Implements `approval-contract.yaml`. Read that file first; the reasoning lives
there and is not repeated here.

This module is pure. It holds no approvals — a `Store` is passed in — for the
same reason the other kernels take their inputs as records: a decision that
reads a global is a decision that cannot be reproduced in a test, and this is
the decision a security boundary rests on.

The one sentence the whole file implements:

    an approval names an ACTION on a TARGET with PARAMETERS, decided by an
    ACTOR from a CONTEXT, until a TIME — and any change to any of those is a
    different approval that nobody has granted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read the approval contract\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "approval-contract.yaml"

APPROVAL_PREFIX = "approval:"

# The verbs this repository treats as human-gated. Read from the Agent Session
# contract rather than restated, so that a verb added there becomes gated here
# without anyone remembering to come back.
SESSION_CONTRACT = _HERE / "agent-session-contract.yaml"

DEFAULT_WINDOW_SECONDS = 900


class Rejected(ValueError):
    """The request or record cannot be used at all."""


def contract() -> dict:
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise Rejected(f"{CONTRACT} must contain a mapping")
    return data


def human_gated_actions() -> tuple[str, ...]:
    """The verbs that require a human decision, from the session contract.

    Imported from the one file that declares them. A second list here would
    agree with it until the day a verb was added to one and not the other, and
    the disagreement would be an action that reads gated in the contract and
    ungated in the code that enforces it.
    """
    data = yaml.safe_load(SESSION_CONTRACT.read_text(encoding="utf-8")) or {}
    return tuple((data.get("admission") or {}).get("human_gated") or ())


# ---------------------------------------------------------------------------
# The fingerprint
# ---------------------------------------------------------------------------

def _canonical(value):
    """Sort at every depth, and fold missing onto null.

    Missing and null are the same value here on purpose: an explicit null that
    evaded the binding would be a one-character edit that changes what the
    provider does while leaving the approval matching.
    """
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def normalize(*, action: str, target: dict, params: dict) -> dict:
    """The binding-relevant request, canonicalized.

    `target` keeps its keys even when every value is null. Dropping empty keys
    would make "an action with no session" hash the same as "an action whose
    session was not stated", and the contract binds those to different
    approvals.
    """
    return _canonical({
        "action": str(action),
        "target": {k: target.get(k) for k in
                   ("device_ref", "session_ref", "agent_runtime", "provider")},
        "params": dict(params or {}),
    })


def fingerprint(*, action: str, target: dict, params: dict) -> str:
    material = json.dumps(normalize(action=action, target=target, params=params),
                          sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def approval_ref(fingerprint_value: str, context_ref: str, actor_ref: str) -> str:
    """The ref is the binding, hashed — including WHO and FROM WHERE.

    One request can be approved by two different people from two different
    contexts, and those are two decisions. Folding actor and context into the
    hash keeps "who approved this" in the ref, so an audit does not have to
    trust a field beside it.
    """
    material = "\x1f".join([fingerprint_value, str(context_ref), str(actor_ref)])
    return APPROVAL_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------

def issue(*, action: str, target: dict, params: dict, context: dict,
          now: str, window_seconds: int = DEFAULT_WINDOW_SECONDS,
          store: dict | None = None) -> dict:
    """Record that a person approved this exact request, from this context.

    Idempotent by construction: the ref is derived from the binding, so asking
    twice returns the same record rather than creating a second one. There is
    no counter and no de-duplication pass — the identity of an approval is the
    thing it authorizes.

    Refuses an action that is not human-gated. Issuing an approval for
    something that does not need one would create authority the contract never
    granted, and a later reader would find an approval beside a delegation
    action and conclude the action needed approving.
    """
    if action not in human_gated_actions():
        raise Rejected(
            f"{action!r} is not a human-gated action, so there is nothing for a "
            f"person to approve. Gated: {list(human_gated_actions())}")

    for field in ("context_ref", "actor_ref"):
        if not context.get(field):
            raise Rejected(
                f"an approval is decided from a trusted context; {field} is missing. "
                "A provider that could not establish the human cannot produce one.")

    fp = fingerprint(action=action, target=target, params=params)
    ref = approval_ref(fp, context["context_ref"], context["actor_ref"])
    if store is not None and ref in store:
        return store[ref]

    binding = contract()["binding"]
    record = {
        "schema": contract()["schema"],
        "approval_ref": ref,
        "actor_ref": context["actor_ref"],
        "context_ref": context["context_ref"],
        "human_control_provider": context.get("human_control_provider"),
        "transport": context.get("transport"),
        "action": action,
        "target": normalize(action=action, target=target, params={})["target"],
        "scope": binding["required"]["scope"],
        "request_fingerprint": fp,
        "issued_at": context.get("observed_at") or now,
        "recorded_at": now,
        "expires_at": _plus(context.get("observed_at") or now, window_seconds),
        "state": "ISSUED",
        "consumed_by": None,
        "consumed_at": None,
    }
    return record


def _plus(stamp: str, seconds: int) -> str:
    return (_parse(stamp) + timedelta(seconds=seconds)).isoformat()


def _parse(stamp: str) -> datetime:
    """Parsed, or a named refusal — see the same function in `context/actor.py`.

    `fromisoformat` raises a bare ValueError, which is `Rejected`'s parent, so
    a caller catching `Rejected` would miss it. On this path the missing catch
    would be worse than usual: the record being parsed is an approval, and an
    approval whose window cannot be read is one whose expiry nobody checked.
    """
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise Rejected(
            f"{stamp!r} is not a timestamp, so the window this approval is "
            "valid for cannot be computed") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def decide(*, approvals: list[dict], request: dict, context_ref: str | None,
           now: str) -> dict:
    """Is this request approved? One verdict, and the reason it was reached.

    `approvals` is every approval currently held for this actor and action. The
    function looks for one that matches the request exactly; everything that
    does not match is reported, because "an approval exists and it is for
    something else" is the fact the requester needs and the fact a bare
    `DENY` destroys.
    """
    action = request["action"]
    if action not in human_gated_actions():
        raise Rejected(f"{action!r} is not a human-gated action")
    if not context_ref:
        return _verdict("APPROVAL_REQUIRED", action,
                        "no trusted context was presented, so no approval can "
                        "be matched to anyone")
    wanted = fingerprint(action=action, target=request.get("target") or {},
                         params=request.get("params") or {})

    relevant = [a for a in approvals
                if a.get("action") == action
                and a.get("actor_ref") == request.get("actor_ref")]

    if not relevant:
        return _verdict("APPROVAL_REQUIRED", action,
                        "no approval names this action for this actor")

    current = [a for a in relevant if a.get("context_ref") == context_ref]
    if not current:
        # The actor approved something from a different conversation or turn.
        # Reporting this as "not approved" would send the requester to ask for
        # an approval that already exists.
        return _verdict(
            "APPROVAL_CONTEXT_MISMATCH", action,
            "an approval exists for this actor and action, decided from a "
            "different context than the one presented",
            approval_ref=relevant[0].get("approval_ref"),
            approved_from=relevant[0].get("context_ref"))

    expired = [a for a in current if _parse(a["expires_at"]) <= _parse(now)]
    if expired:
        return _verdict("APPROVAL_EXPIRED", action,
                        f"the approval for this request closed at {expired[0]['expires_at']}",
                        approval_ref=expired[0].get("approval_ref"))

    exact = [a for a in current if a.get("request_fingerprint") == wanted]
    if not exact:
        other = current[0]
        return _verdict(
            "APPROVAL_BINDING_MISMATCH", action,
            "an approval exists for this action and context, and not for these "
            "parameters — the request changed after it was approved",
            approval_ref=other.get("approval_ref"),
            approved_fingerprint=other.get("request_fingerprint"),
            presented_fingerprint=wanted)

    spent = [a for a in exact if a.get("state") != "ISSUED"]
    if spent:
        return _verdict("APPROVAL_ALREADY_CONSUMED", action,
                        f"this approval was spent at {spent[0].get('consumed_at')}",
                        approval_ref=spent[0].get("approval_ref"))

    approval = exact[0]
    return {"decision": "ALLOW", "action": action, "class": "human_approved",
            "approval_ref": approval["approval_ref"],
            "request_fingerprint": wanted,
            "reason": "an identified human approved exactly this request",
            "context_ref": context_ref}


def _verdict(decision: str, action: str, reason: str, **extra) -> dict:
    return {"decision": decision, "action": action, "class": "human_gated",
            "reason": reason, **extra}


def consume(approval: dict, *, consumed_by: str, now: str) -> dict:
    """Mark an approval spent. Returns the updated record; does not mutate it.

    Kept a separate step from `decide` so that a caller which decides and then
    fails to perform the action can leave the approval unspent. Deciding is a
    read; spending is a write, and the two belong on opposite sides of the
    action they authorize.
    """
    if approval.get("state") != "ISSUED":
        raise Rejected(f"approval {approval.get('approval_ref')!r} is "
                       f"{approval.get('state')!r}, not ISSUED")
    return {**approval, "state": "CONSUMED", "consumed_by": consumed_by,
            "consumed_at": now}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--action")
    parser.add_argument("--target", help="JSON object")
    parser.add_argument("--params", help="JSON object")
    parser.add_argument("--actor")
    parser.add_argument("--context-ref")
    parser.add_argument("--now")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--gated", action="store_true",
                        help="print the human-gated actions and exit")
    args = parser.parse_args(argv)

    if args.gated:
        for action in human_gated_actions():
            print(action)
        return 0

    now = args.now or datetime.now(timezone.utc).isoformat()
    if not (args.action and args.actor and args.context_ref):
        parser.error("--action, --actor and --context-ref are required")
    target = json.loads(args.target) if args.target else {}
    params = json.loads(args.params) if args.params else {}
    record = issue(
        action=args.action, target=target, params=params,
        context={"context_ref": args.context_ref, "actor_ref": args.actor,
                 "observed_at": now},
        now=now, window_seconds=args.window)
    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
