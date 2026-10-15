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

WHY A SEND ALSO NEEDS A GATE
----------------------------
The card carries machines that belong to different people, and which of them a
recipient may be shown is a rule this module does not own — the source's
`authorize_devices` answers it. So `apply_plan` refuses to send without a
verdict: the enforcement lives at the one place a send starts from, not in a
renderer that hid a row. A refusal touches no transport and produces no receipt,
which is what keeps «we declined to send this» distinct from «we sent it and it
failed» — the second is a delivery record and the first must not be one.

AND THE VERDICT IS A SIGNED TYPE, NOT A BOOLEAN
-----------------------------------------------
The first version accepted a plain `{"ok": True}` dictionary, which made this
whole section a suggestion: any caller could mint the word «yes», hand it in
beside a real transport, and put a private machine's row in a company channel.
So `apply_plan` now requires an `AudienceVerdict` — a typed, HMAC-signed
instance from `runtime/fleet_audience.py` — and checks it against the send it is
about to make: the RECIPIENT it was issued for, the DEVICE SET it covers, and
(for a real transport) the CANONICAL HASH OF THIS CARD. A verdict for another
recipient, another device list, or another card is refused, and a `preview`
verdict — the local-render one — is accepted only for the in-memory mock.
Nothing a caller merely writes can stand in for a decision somebody made.
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
# The signed verdict and its preview form. Loaded under the SAME name
# `fleet_monitor_source` uses, so a verdict issued there is this module's type
# and `isinstance` answers True about it — one file under two names is two
# classes, the hazard the tree's other loaders document.
fleet_audience = _load("serveros_fleet_audience", _HERE / "fleet_audience.py")
preview_gate = fleet_audience.preview_gate

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

#: REFUSED is not a transport answer and not a scheduling one: it is this module
#: declining to hand anything to a transport at all. It is a distinct word so
#: that a refusal can never be read as a delivery that failed — a card that was
#: never sent has no receipt to fail.
REFUSED = "REFUSED"
#: No audience verdict was established, so there is no answer to «may this
#: recipient be shown these devices». A send is refused rather than assumed.
GATE_REQUIRED = "GATE_REQUIRED"
#: What was handed in is not a signed `AudienceVerdict`. A boolean a caller
#: writes — `{"ok": True}` and friends — is not evidence that anybody checked.
VERDICT_UNTRUSTED = "VERDICT_UNTRUSTED"
#: The verdict is real but is about something else: a different card, or a
#: device set other than the one the card carries.
VERDICT_MISMATCH = "VERDICT_MISMATCH"
#: A `preview` verdict — the local-render one — was offered to a real transport.
PREVIEW_ONLY = "PREVIEW_ONLY"
#: The verdict names a different recipient than the one being sent to.
AUDIENCE_MISMATCH = "AUDIENCE_MISMATCH"
#: The device set the caller declares is not the one the verdict was issued over.
DEVICE_MISMATCH = "DEVICE_MISMATCH"
#: The audience verdict said no. The device list the card carries is not this
#: recipient's to see.
AUDIENCE_DENIED = "AUDIENCE_DENIED"


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

    def send(self, *, card, action, version, remote_id=None) -> dict:
        """Record the call and answer with the id it acted on.

        A PATCH answers with the id it was GIVEN — the message it changed — and
        only a NEW message mints one. A mock that minted a fresh id for a patch
        would model the exact defect V28 forbids: the receipt would name a
        second card that does not exist, and a caller filing it would lose track
        of the one on screen.
        """
        self.calls.append({
            "action": action, "version": version, "remote_id": remote_id,
            "bytes": len(json.dumps(card, ensure_ascii=False, sort_keys=True)),
        })
        if action == card_delivery.PATCH_MESSAGE and remote_id:
            return {"state": "DELIVERED", "remote_id": remote_id}
        return {"state": "DELIVERED",
                "remote_id": f"{self.prefix}{len(self.calls):04d}"}


def apply_plan(plan, card, *, at, transport=None, authorization=None,
               recipient=None, device_refs=None) -> dict:
    """Carry out a plan against a transport, defaulting to the mock.

    The ONLY place a send could happen. With the default transport it cannot:
    the mock records the call and answers with a synthetic id. The receipt it
    returns is the shape `card_delivery.record` would take — version and the
    message id the send produced — but filing it is the ledger owner's job and
    not this module's.

    THE AUTHORIZATION IS CHECKED HERE, BEFORE THE TRANSPORT, and it must be a
    signed `AudienceVerdict` — never a dictionary and never a bare `ok`. The
    checks, in the order they are made, and each one a thing the earlier version
    did NOT do:

      0. the type: a dict, a string, or anything hand-written is refused. A
         boolean a caller writes is not evidence that anybody checked;
      1. the answer: `ok` must be True, and a denial names how many devices;
      2. the RECIPIENT: the verdict must name the recipient being sent to (read
         from `recipient`, or from the transport's own `recipient` when it has
         one), so a verdict for one destination cannot be spent on another;
      3. the DEVICE SET: when the caller declares the card's device list, its
         canonical signature must be the one the verdict was issued over;
      4. the CARD, for a real transport: the verdict's canonical card hash must
         be this card's, and its issuer must be `policy`. A `preview` verdict —
         for local rendering — is accepted ONLY by the in-memory mock, so the
         old `open_gate()` «ok for everything» can no longer authorize a real
         send; it was removed rather than renamed.

    Every refusal returns NO RECEIPT (`receipt`/`remote_id` are `None`) and
    touches no transport, which keeps «we declined to send this» distinct from
    «we sent it and it failed» — the second is a delivery record, the first must
    not be one.
    """
    transport = transport if transport is not None else MockTransport()
    # Exact type, not `isinstance`: a subclass that overrides `send` is a real
    # transport wearing the mock's name, and must face the real checks.
    real = type(transport) is not MockTransport
    if not plan["send"]:
        return {"state": "NOOP", "action": plan["action"], "reason": plan["reason"],
                "version": plan["version"], "remote_id": None, "at": at,
                "receipt": None, "denied": []}

    if not isinstance(authorization, fleet_audience.AudienceVerdict):
        return _refused(plan, at, VERDICT_UNTRUSTED,
                        "发送前必须给出一份由 authorize_devices 签发的 AudienceVerdict；"
                        "收到的不是这个类型。用自己写的 ok=True 字典来满足门禁，等于把"
                        "门禁变成建议——一台私有设备的读数会因此进到公司频道，这正是它"
                        "必须是一个签名类型而不是一个布尔的原因")
    if not authorization.ok:
        return _refused(plan, at, AUDIENCE_DENIED,
                        f"该收件人未被授权看到 {len(authorization.denied)} 台设备中的读数。"
                        f"发送被拒，没有触碰 transport，也没有产生任何送达收据",
                        denied=authorization.denied)

    target = str(recipient) if recipient is not None else None
    if target is None:
        transport_recipient = getattr(transport, "recipient", None)
        target = str(transport_recipient) if transport_recipient is not None else None
    if target is not None and str(authorization.audience) != target:
        return _refused(plan, at, AUDIENCE_MISMATCH,
                        "这份判决是为另一个收件人签发的：判决与本次发送的收件人不是同一个，"
                        "拒绝发送")

    if device_refs is not None and (
            fleet_audience.device_set_signature(device_refs)
            != authorization.device_signature):
        return _refused(plan, at, DEVICE_MISMATCH,
                        "调用方声明的设备清单与判决签发时的设备清单不一致：拒绝发送")

    card_signature = freeze(card)["payload_hash"]
    if real:
        if authorization.issuer != fleet_audience.ISSUER_POLICY:
            return _refused(plan, at, PREVIEW_ONLY,
                            "preview 判决只能用于本地 mock 预览，不能用于真实 transport："
                            "预览没有对任何收件人做授权，把它当许可就是没有授权")
        if authorization.card_signature != card_signature:
            return _refused(plan, at, VERDICT_MISMATCH,
                            "判决的 canonical card hash 与要发送的这张卡不一致：一份为别的"
                            "卡（或别的设备清单）签发的判决不能用来发这一张——否则把私有"
                            "设备行加回卡上就绕过了判决")
    elif authorization.card_signature is not None \
            and authorization.card_signature != card_signature:
        # Even against the mock, a policy verdict must be about THIS card.
        return _refused(plan, at, VERDICT_MISMATCH,
                        "判决绑定的卡与本次发送的卡不是同一张：拒绝")

    result = transport.send(card=card, action=plan["action"],
                            version=plan["version"],
                            remote_id=plan.get("remote_id"))
    remote_id = result.get("remote_id")
    return {"state": result.get("state", "UNKNOWN"), "action": plan["action"],
            "reason": result.get("reason") or plan["reason"],
            "version": plan["version"], "remote_id": remote_id, "at": at,
            "receipt": {"version": plan["version"], "remote_id": remote_id},
            "denied": []}


def _refused(plan, at, reason, says, *, denied=()) -> dict:
    """A refusal, with no receipt and no remote id — nothing was attempted."""
    return {"state": REFUSED, "action": plan["action"], "reason": reason,
            "version": plan["version"], "remote_id": None, "at": at,
            "receipt": None, "denied": list(denied), "says": says}


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
