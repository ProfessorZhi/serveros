#!/usr/bin/env python3
"""The single-card update planner: edit in place, rotate on expiry, or say
nothing — computed as a pure function, sent through a mock that sends nothing.

What it decides, and nothing else
---------------------------------
One logical Fleet monitor card is edited in place on a 600-second cadence; a
severe finding skips the wait; and when the message carrying it has aged past
the service's own update window, the next send is a ROTATION (a new physical
message for the same logical card). Those are the only three moves, and this
module computes which one applies. It does not render, it does not measure, it
does not look at a clock — `at` is an argument — and it holds nothing durable.

REUSE WITHOUT EXPANDING PUBLIC CODE
-----------------------------------
`runtime/card_delivery.py` is the state authority for «what was sent, and may
this message still be changed», and this module borrows exactly two things from
it: `canonical()` (so one payload has one byte string and therefore one version)
and `binding_verdict()` (so «may I patch» is answered in ONE place). What it
does NOT do is add a `fleet-monitor` kind to `card_delivery.KINDS`: that is a
closed set another change may own, and widening it from here would be expanding
public code this planner is only a caller of. So `freeze()` builds a version in
the same `<key>@<hash12>` shape with the same canonicalisation, and files
nothing in the ledger. If the ledger is ever asked to carry this card, the kind
is added THERE, by that module's owner, and this planner starts calling
`freeze_card` instead of `freeze`.

WHY THE DEFAULT TRANSPORT SENDS NOTHING
---------------------------------------
`apply_plan` is the only place a send could happen, and its transport defaults
to `MockTransport`, which records the call and answers with a synthetic id and
opens no socket. A caller that wants the real transport passes one explicitly;
nothing in this module, and no default anywhere in it, reaches Feishu.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


card_delivery = _load("serveros_runtime_card_delivery", _HERE / "card_delivery.py")

CARD_KEY = "fleet-monitor"
NORMAL_REFRESH_S = 600

#: The three moves, plus the one that is not a move. NOOP is a word of this
#: module's own; the other two are `card_delivery`'s, reused rather than respelt
#: so a `PATCH_MESSAGE` here means the same thing it means in the ledger.
NOOP = "NOOP"
NEW_MESSAGE = card_delivery.NEW_MESSAGE
PATCH_MESSAGE = card_delivery.PATCH_MESSAGE

#: Why nothing was sent. NOT_DUE is «the tick is early»; UNCHANGED is «the card
#: is byte-identical to the one already in the chat, so there is nothing to
#: change». They are different findings: an early tick will send later, an
#: unchanged one will not until the card changes.
NOT_DUE = "NOT_DUE"
UNCHANGED = "UNCHANGED"


class PlanError(RuntimeError):
    pass


def _moment(value):
    """A timezone-aware instant, or `None`. A naive value is refused, not
    assumed — the update window is days wide and a guessed zone can move the
    answer across a day."""
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def freeze(card, *, key: str = CARD_KEY) -> dict:
    """The version of this card, in `card_delivery`'s `<key>@<hash12>` shape."""
    if not isinstance(card, dict) or not card:
        raise PlanError("a version is a hash of a card, and this is not one")
    blob = card_delivery.canonical(card)
    digest = hashlib.sha256(blob).hexdigest()
    return {"key": key, "version": f"{key}@{digest[:12]}",
            "payload_hash": digest, "payload_bytes": len(blob)}


def plan_update(*, last, card, at, cadence_seconds=NORMAL_REFRESH_S,
                urgent=False, window_days=None) -> dict:
    """Which move the next send makes about ONE logical card, and why.

    PURE. `last` is what the ledger recorded — `{"version", "at", "binding"}`
    with `binding` = `{"remote_id", "at"}` or None — or `None` for «nothing was
    ever sent». `card` is the already-rendered card; its version is computed
    here and compared with what is in the chat.

    The order of the questions is the design:

      1. Nothing ever sent            -> NEW_MESSAGE (first), always.
      2. Not due and not urgent       -> NOOP (NOT_DUE). An urgent finding
                                          bypasses the cadence; nothing else does.
      3. Due, message unchangeable    -> NEW_MESSAGE (rotate). The binding is
                                          gone or aged out, so the logical card
                                          needs a new physical message to stay
                                          updatable.
      4. Due, message in hand, same   -> NOOP (UNCHANGED).
      5. Due, message in hand, moved  -> PATCH_MESSAGE (edit).
    """
    frozen = freeze(card)
    if last is None:
        return {"action": NEW_MESSAGE, "change": "first", "send": True,
                "reason": card_delivery.NEVER_SENT, "version": frozen["version"],
                "payload_bytes": frozen["payload_bytes"], "remote_id": None,
                "elapsed_s": None, "urgent": bool(urgent),
                "cadence_seconds": cadence_seconds}

    sent, now = _moment(last.get("at")), _moment(at)
    elapsed = None if (sent is None or now is None) else (now - sent).total_seconds()
    due = urgent or elapsed is None or elapsed >= cadence_seconds
    if not due:
        return {"action": NOOP, "change": "none", "send": False,
                "reason": NOT_DUE, "version": frozen["version"],
                "payload_bytes": frozen["payload_bytes"], "remote_id": None,
                "elapsed_s": elapsed, "urgent": False,
                "cadence_seconds": cadence_seconds}

    if window_days is None:
        window_days = card_delivery.update_window_days()
    binding = last.get("binding") or {}
    verdict = card_delivery.binding_verdict(binding, at=at, window_days=window_days)
    changed = frozen["version"] != last.get("version")

    if verdict["action"] != PATCH_MESSAGE:
        # IDENTITY_MISSING or IDENTITY_EXPIRED: the recorded message cannot be
        # changed, so the next send is a NEW physical message for the same
        # logical card. `rotate` when something was sent before; this branch is
        # reached with `last` set, so it is always a rotation here.
        return {"action": NEW_MESSAGE, "change": "rotate", "send": True,
                "reason": verdict["reason"], "version": frozen["version"],
                "payload_bytes": frozen["payload_bytes"], "remote_id": None,
                "elapsed_s": elapsed, "urgent": bool(urgent),
                "cadence_seconds": cadence_seconds}

    if not changed:
        return {"action": NOOP, "change": "none", "send": False,
                "reason": UNCHANGED, "version": frozen["version"],
                "payload_bytes": frozen["payload_bytes"],
                "remote_id": verdict["remote_id"], "elapsed_s": elapsed,
                "urgent": bool(urgent), "cadence_seconds": cadence_seconds}

    return {"action": PATCH_MESSAGE, "change": "edit", "send": True,
            "reason": verdict["reason"], "version": frozen["version"],
            "payload_bytes": frozen["payload_bytes"],
            "remote_id": verdict["remote_id"], "elapsed_s": elapsed,
            "urgent": bool(urgent), "cadence_seconds": cadence_seconds}


class MockTransport:
    """Stands in for the Feishu transport. IT OPENS NO SOCKET AND SENDS NOTHING.

    Every call is appended to `.calls` and answered with a synthetic `om_` id,
    so a test asserts what the planner WOULD have sent without a transport ever
    being touched. This is the default `apply_plan` uses, which is what makes
    «default must not call real Feishu» a property of the code and not a promise.
    """

    def __init__(self, prefix: str = "om_mock") -> None:
        self.prefix = prefix
        self.calls: list[dict] = []

    def send(self, *, card, action, version) -> dict:
        self.calls.append({
            "action": action, "version": version,
            "bytes": len(json.dumps(card, ensure_ascii=False, sort_keys=True)),
        })
        return {"state": "DELIVERED",
                "remote_id": f"{self.prefix}{len(self.calls):04d}"}


def apply_plan(plan, card, *, at, transport=None) -> dict:
    """Carry out a plan against a transport, defaulting to the mock.

    The ONLY place a send could happen. With the default transport it cannot:
    the mock records the call and answers with a synthetic id. The receipt it
    returns is the shape `card_delivery.record` would take — version and the
    message id the send produced — but filing it is the ledger owner's job and
    not this module's.
    """
    transport = transport if transport is not None else MockTransport()
    if not plan["send"]:
        return {"state": "NOOP", "action": plan["action"], "reason": plan["reason"],
                "version": plan["version"], "remote_id": None, "at": at}
    result = transport.send(card=card, action=plan["action"],
                            version=plan["version"])
    return {"state": result.get("state", "UNKNOWN"), "action": plan["action"],
            "reason": result.get("reason") or plan["reason"],
            "version": plan["version"], "remote_id": result.get("remote_id"),
            "at": at}


def main(argv=None):  # pragma: no cover - hand check
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--card", required=True, help="path to a rendered card JSON")
    parser.add_argument("--at", required=True)
    parser.add_argument("--last-version", default=None)
    args = parser.parse_args(argv)
    card = json.loads(Path(args.card).read_text(encoding="utf-8"))
    last = ({"version": args.last_version, "at": args.at,
             "binding": {"remote_id": "om_mock0001", "at": args.at}}
            if args.last_version else None)
    json.dump({"version": freeze(card)["version"],
               "plan": plan_update(last=last, card=card, at=args.at,
                                   window_days=14)},
              sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
