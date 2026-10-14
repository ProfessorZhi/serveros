#!/usr/bin/env python3
"""Two surfaces: ONE long-lived LIVE card, and per-period REPORT cards.

WHY THIS IS NOT `card_period`
-----------------------------
`card_period` models «one card per period»: every period CREATEs its own message,
FINALs it at the period boundary, freezes it, and the next period CREATEs another.
The 2026-10-14 product ruling replaces that cadence with TWO SURFACES:

    LIVE    ONE card for the recipient, kept alive and PATCHed in place: a
            heartbeat every 30 minutes, and IMMEDIATELY when an alert event
            (NEW / ESCALATED / RECOVERED / handling change) lands. The live alert
            area is a REGION of this card, never a third card.
    REPORT  a separate «ServerOS Core REPORT» card per period (morning /
            afternoon / night / holiday), with its OWN message id, frozen when
            its own delivery is acknowledged. It NEVER shares a message id with
            the LIVE card, and a REPORT's success or failure never freezes and
            never blocks the LIVE card.

ALERT IS NOT A THIRD CARD. `card_delivery`'s per-episode `alert` / `recovery`
kinds keep their production path — the existing incident card is NOT deleted
here. What changes is that the NEW product surface carries its alerts inside the
LIVE card and does not mint a separate ALERT CardKit message.

WHAT IT REUSES (no second transport, no second ledger)
------------------------------------------------------
    card_delivery.CardLedger          receipts / versions / the 14-day window
    card_delivery.binding_verdict     «is this message still patchable, and why»
    message_transport.LarkMessageSender   send_card / update / reconcile
    card_period.render_period_card    the approved native CardKit renderer

    No second delivery ledger is written and no second transport is built.

WHAT IT DOES NOT DO
-------------------
No clock (every instant is a parameter), no I/O in the policy functions, no
change to the running production `monitoring-policy.yaml` (`hourly` /
`one-per-workday` still governs the live timer until a separate, explicit
switch), and no chart: the VChart element is still unverified on a real client.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-surface.v1"

#: The two product surfaces. There is no third.
LIVE = "LIVE"
REPORT = "REPORT"
SURFACES = (LIVE, REPORT)

#: The heartbeat slot. The card's REFRESH cadence — NOT the sampling cadence.
#: Sampling may be denser (for trend / alert detection) and lives in the managed
#: record store; this is only how often the card is PATCHed when nothing happens.
SLOT_SECONDS = 1800

#: Why a LIVE patch is due. A heartbeat and an event in the same instant are
#: COALESCED into ONE patch and both reasons are reported.
HEARTBEAT = "HEARTBEAT"
EVENT = "EVENT"
ROTATION = "ROTATION"
NOT_DUE = "NOT_DUE"

#: The alert events that patch the LIVE card IMMEDIATELY, without waiting for the
#: next heartbeat slot. They are a region of the LIVE card, not a card of their
#: own — see the module docstring.
NEW = "NEW"
ESCALATED = "ESCALATED"
RECOVERED = "RECOVERED"
HANDLING = "HANDLING"
EVENT_KINDS = (NEW, ESCALATED, RECOVERED, HANDLING)

#: What the surface may do at an instant.
CREATE = "CREATE"
PATCH = "PATCH"
#: A LIVE whose bound message can no longer be patched. It is NOT a new logical
#: card: the old message stops being updated and the successor carries the SAME
#: live state forward. Only a transport-layer fact (the 14-day window, a dead
#: identity, a lost message) may cause it.
SUCCESSOR = "SUCCESSOR"
FINAL = "FINAL"
SKIP = "SKIP"
ACTIONS = (CREATE, PATCH, SUCCESSOR, FINAL, SKIP)

#: The three phases an open REPORT is in — the same words `card_period` uses, so
#: the ACK discipline is one vocabulary: PREPARE, then a verified acknowledgement
#: FROZEN, and never the other way round.
LIVE_PHASE = "LIVE"
FINAL_PENDING = "FINAL_PENDING"
FROZEN = "FROZEN"
PHASES = (LIVE_PHASE, FINAL_PENDING, FROZEN)

#: Cancel reasons.
CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
ALREADY_FROZEN = "ALREADY_FROZEN"
NOT_A_PERIOD = "NOT_A_PERIOD"


class CardSurfaceError(RuntimeError):
    pass


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


delivery = _load("serveros_runtime_card_delivery", _HERE / "card_delivery.py")


def _store_io():
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


def _registry():
    return _load("serveros_runtime_registry", _HERE / "registry.py")


# ---------------------------------------------------------------------------
# the LIVE policy — pure
# ---------------------------------------------------------------------------

def _moment(value):
    """An ISO instant as a datetime, or None when it cannot be read."""
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def heartbeat_due(*, now, last_patch_at, slot_seconds: int = SLOT_SECONDS) -> bool:
    """Whether a heartbeat slot has elapsed since the last LIVE patch.

    An unreadable instant on EITHER side answers True: a card whose last patch
    time nobody can read is a card that should be refreshed, and refusing there
    would stall the surface on a corrupt field. A non-positive slot is refused
    rather than read as «always due», which would turn the heartbeat into a storm.
    """
    if not isinstance(slot_seconds, int) or isinstance(slot_seconds, bool) \
            or slot_seconds <= 0:
        raise CardSurfaceError(f"slot_seconds must be a positive integer, not {slot_seconds!r}")
    now_dt, last_dt = _moment(now), _moment(last_patch_at)
    if now_dt is None or last_dt is None:
        return True
    return (now_dt - last_dt).total_seconds() >= slot_seconds


def event_kinds(events) -> list[str]:
    """The DEDUPED, ordered alert kinds in one batch.

    Duplicates collapse — the same event delivered twice by a retrying timer is
    one reason to patch, not two — and an unrecognised word is dropped rather
    than treated as an event, because a kind nobody can name must not be able to
    force a card out of its cadence.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in events or ():
        raw = item.get("kind") if isinstance(item, dict) else item
        kind = str(raw or "").upper()
        if kind in EVENT_KINDS and kind not in seen:
            seen.add(kind)
            out.append(kind)
    return out


def live_decision(*, now, binding, events=(), last_patch_at=None,
                  slot_seconds: int = SLOT_SECONDS, window_days: int | None = None) -> dict:
    """What the LIVE surface does at this instant, and why. PURE.

    At most ONE patch per call. A heartbeat and an event landing in the same
    instant are COALESCED into a single patch — «同一轮事件/心跳合并一次写» —
    and both reasons are reported, so the caller can tell «nothing happened, the
    half hour came round» from «something happened».

    The patchability question is NOT decided here: «is the bound message still
    changeable at `now`» is `card_delivery.binding_verdict`'s, asked once for
    every card family. This function asks it and then chooses.
    """
    window = delivery.update_window_days() if window_days is None else window_days
    kinds = event_kinds(events)
    heartbeat = heartbeat_due(now=now, last_patch_at=last_patch_at,
                              slot_seconds=slot_seconds)
    judged = delivery.binding_verdict(binding, at=now, window_days=window)
    reasons: list[str] = []
    if kinds:
        reasons.append(EVENT)
    if heartbeat:
        reasons.append(HEARTBEAT)

    state = judged["reason"]
    if state == delivery.IDENTITY_EXPIRED:
        reasons.append(ROTATION)
        action, message_action = SUCCESSOR, delivery.SUCCESSOR_MESSAGE
        says = (f"承载 LIVE 的消息已发出约 {int(judged['age_days'])} 天，超过 "
                f"{window} 天可改窗口：改不动了。按台账有据地做**继任**——"
                f"旧消息停止更新，新消息继续承载同一份实时状态，不是第二条监控卡")
    elif state == delivery.IDENTITY_MISSING:
        action, message_action = CREATE, delivery.NEW_MESSAGE
        says = "还没有承载 LIVE 的消息：本次新建一条，此后一直是这一条"
    elif not reasons:
        return {"surface": LIVE, "action": SKIP, "message_action": None,
                "reason": NOT_DUE, "reasons": [], "events": [], "remote_id": None,
                "coalesced": False, "window_days": window, "at": now,
                "says": "没到心跳时点，也没有新事件：这一拍不动 LIVE"}
    else:
        action, message_action = PATCH, delivery.PATCH_MESSAGE
        says = ("LIVE 到点更新（" + "、".join(reasons) + "）")

    if action == PATCH and state in (delivery.IDENTITY_UNDATED,):
        # The message is in hand but its send time cannot be read, so the window
        # is unanswerable. A patch that might land outside the window is refused
        # rather than attempted; it is not a rotation cause, because we do not
        # KNOW it expired.
        return {"surface": LIVE, "action": SKIP, "message_action": None,
                "reason": delivery.IDENTITY_UNDATED, "reasons": reasons,
                "events": kinds, "remote_id": judged["remote_id"],
                "coalesced": False, "window_days": window, "at": now,
                "says": "承载 LIVE 的发出时间读不出来，可改窗口无法判断：不据此 PATCH"}
    return {"surface": LIVE, "action": action, "message_action": message_action,
            "reason": reasons[0] if reasons else NOT_DUE, "reasons": reasons,
            "events": kinds, "remote_id": judged["remote_id"],
            "coalesced": EVENT in reasons and HEARTBEAT in reasons,
            "window_days": window, "at": now, "says": says}


def successor_binding(*, previous, remote_id: str, at: str) -> dict:
    """The binding a ROTATED LIVE carries forward. The old id is kept, not lost.

    A rotation is a transport-layer event, and the evidence for it is the old
    message's receipt in the ledger — this binding records what it replaced so a
    reader can follow the chain rather than finding a card that silently changed
    identity.
    """
    if not str(remote_id or "").strip():
        raise CardSurfaceError("a successor needs the message it succeeds to")
    previous = previous if isinstance(previous, dict) else {}
    return {"remote_id": str(remote_id).strip(), "bound_at": str(at),
            "previous_remote_id": previous.get("remote_id"),
            "rotations": int(previous.get("rotations") or 0) + 1}


# ---------------------------------------------------------------------------
# the REPORT policy — pure
# ---------------------------------------------------------------------------

def report_decision(*, period, report, closing=None) -> dict:
    """What the REPORT surface does at this instant. PURE.

    A REPORT is bound to ONE `period_ref` and NEVER to the LIVE binding. Its
    phase machine is the same three words the period card uses — PREPARE, then
    FROZEN on a verified acknowledgement — so a REPORT that cannot be confirmed
    stays `FINAL_PENDING` and is retried on the SAME message, while the LIVE card
    keeps updating beside it.
    """
    period_ref = (period or {}).get("period_ref")
    if not period_ref or not (period or {}).get("in_period"):
        return {"surface": REPORT, "action": SKIP, "reason": CALENDAR_UNKNOWN,
                "period_ref": None, "says": "没有可用的时段身份，不建报告"}
    if not isinstance(report, dict) or report.get("period_ref") != period_ref:
        return {"surface": REPORT, "action": CREATE, "reason": NOT_A_PERIOD,
                "period_ref": period_ref,
                "says": (f"{period_ref} 还没有报告：新建一条**独立**消息承载它，"
                         f"与 LIVE 不共用 message_id")}
    phase = report.get("phase") or LIVE_PHASE
    if phase == FROZEN:
        return {"surface": REPORT, "action": SKIP, "reason": ALREADY_FROZEN,
                "period_ref": period_ref,
                "says": f"{period_ref} 的报告已冻结：不再变化"}
    if phase == FINAL_PENDING:
        return {"surface": REPORT, "action": FINAL, "reason": FINAL_PENDING,
                "period_ref": period_ref, "retry": True,
                "says": ("最终报告尚未被远端确认：本次重试**同一条** message_id，"
                         "不新建，也不影响仍在更新的 LIVE")}
    if closing and closing == period_ref:
        return {"surface": REPORT, "action": FINAL, "reason": "PERIOD_ENDED",
                "period_ref": period_ref,
                "says": "时段结束：本次对同一条报告消息做最后一次 patch，随后冻结"}
    return {"surface": REPORT, "action": PATCH, "reason": "SAME_PERIOD",
            "period_ref": period_ref,
            "says": f"{period_ref} 的报告已在：patch 这一条"}


# ---------------------------------------------------------------------------
# the document on disk — one store, two surfaces
# ---------------------------------------------------------------------------

def empty() -> dict:
    return {"schema": SCHEMA, "live": None, "reports": []}


def surface_path(state_path) -> Path:
    """Where the two surfaces' memory sits, given a state directory."""
    return Path(state_path).parent.parent / "cards" / "surfaces.json"


def default_path() -> Path:
    """The SAME computation as `surface_path`, over the ledger's own anchor."""
    return surface_path(_registry().state_root() / "runtime" / "alerts" / "state.json")


class SurfaceStore:
    """The LIVE binding and the REPORTs' bindings. One document, one atomic write.

    An unreadable store is REFUSED rather than replaced: a fresh document would
    lose the LIVE binding and the next tick would CREATE a second live card,
    which is the one thing a single-LIVE rule forbids by name.
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CardSurfaceError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start empty: the LIVE binding would be lost "
                f"and the next tick would create a SECOND live card.") from exc
        if not isinstance(data, dict) or not isinstance(data.get("reports"), list):
            raise CardSurfaceError(f"{self.path} is not a card-surface document")
        live = data.get("live")
        if live is not None:
            if not isinstance(live, dict) or not str(live.get("remote_id") or ""):
                raise CardSurfaceError(
                    f"the LIVE binding in {self.path} names no message: a binding "
                    f"to nothing would let the next patch guess a message")
            if not str(live.get("bound_at") or ""):
                raise CardSurfaceError(
                    f"the LIVE binding in {self.path} has no `bound_at`: the "
                    f"service's update window is measured from when the message was "
                    f"sent, and an instant nobody wrote down cannot be measured")
        for report in data["reports"]:
            if not isinstance(report, dict) or not report.get("period_ref"):
                raise CardSurfaceError(
                    f"a report in {self.path} has no `period_ref`: the identity is "
                    f"the dedupe key of its own message")
            if (report.get("phase") or LIVE_PHASE) not in PHASES:
                raise CardSurfaceError(
                    f"a report in {self.path} is in phase {report.get('phase')!r}, "
                    f"which is not one of {', '.join(PHASES)}")
        return data

    def read(self) -> dict:
        return self._data

    # -- LIVE ---------------------------------------------------------------

    def decide_live(self, *, at, events=(), slot_seconds=SLOT_SECONDS,
                    window_days=None) -> dict:
        """The LIVE decision for this instant, against the stored binding."""
        live = self._data.get("live")
        binding = None
        if isinstance(live, dict) and live.get("remote_id"):
            binding = {"remote_id": live["remote_id"], "at": live.get("bound_at")}
        return live_decision(now=at, binding=binding, events=events,
                             last_patch_at=(live or {}).get("last_patch_at"),
                             slot_seconds=slot_seconds, window_days=window_days)

    def apply_live(self, *, decision: dict, remote_id=None, at: str) -> dict:
        """Record what the LIVE surface did. Idempotent on the SAME instant.

        A patch that found the binding already current does NOT move the clock
        backwards and does NOT create a second binding: `last_patch_at` only
        moves forward, so a replayed tick cannot make the heartbeat fire twice.
        """
        action = (decision or {}).get("action")
        if action not in ACTIONS:
            raise CardSurfaceError(f"{action!r} is not a surface action")
        next_doc = dict(self._data)
        live = dict(next_doc.get("live") or {})
        if action in (CREATE, SUCCESSOR):
            if not str(remote_id or "").strip():
                raise CardSurfaceError(
                    f"a {action} must record the message it produced; without it "
                    f"the next patch has nothing to aim at")
            if action == SUCCESSOR:
                live = successor_binding(previous=live, remote_id=remote_id, at=at)
                live["last_patch_at"] = at
            else:
                live = {"remote_id": str(remote_id).strip(), "bound_at": at,
                        "previous_remote_id": None, "rotations": 0,
                        "last_patch_at": at}
            next_doc["live"] = live
        elif action == PATCH:
            if not live.get("remote_id"):
                raise CardSurfaceError("a PATCH needs a bound LIVE message")
            previous = live.get("last_patch_at")
            live["last_patch_at"] = at if _moment(previous) is None or \
                (_moment(at) is not None and _moment(at) > _moment(previous)) else previous
            next_doc["live"] = live
        elif action == SKIP:
            pass
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"applied": action in (CREATE, SUCCESSOR, PATCH), "live": live,
                "decision": decision, "at": at}

    # -- REPORT -------------------------------------------------------------

    def _report(self, period_ref: str) -> dict | None:
        for report in self._data.get("reports") or []:
            if report.get("period_ref") == period_ref:
                return report
        return None

    def decide_report(self, *, period, closing=None) -> dict:
        """The REPORT decision for this period, against the stored binding."""
        ref = (period or {}).get("period_ref")
        return report_decision(period=period, report=self._report(ref) if ref else None,
                               closing=closing)

    def apply_report(self, *, period, decision: dict, at: str, remote_id=None,
                     archive=None) -> dict:
        """Record the REPORT's state. LIVE is never touched here.

        THIS IS THE INDEPENDENCE. A report's phase moves in the report's own
        entry; there is no code path from here to `live`, so a report that
        succeeds, fails or freezes cannot freeze, stall or rotate the LIVE card.
        """
        action = (decision or {}).get("action")
        ref = (period or {}).get("period_ref")
        if action not in ACTIONS or not ref:
            raise CardSurfaceError(f"{action!r}/{ref!r} is not a report step")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        current = next((r for r in reports if r.get("period_ref") == ref), None)
        if action == SKIP:
            return {"applied": False, "report": current, "at": at}
        if action == CREATE:
            if not str(remote_id or "").strip():
                raise CardSurfaceError("a report creation must record its message id")
            if current is not None:
                raise CardSurfaceError(f"{ref} already has a report message")
            current = {"period_ref": ref, "phase": LIVE_PHASE,
                       "remote_id": str(remote_id).strip(), "bound_at": at,
                       "last_patch_at": at, "archive": archive, "frozen": None}
            reports.append(current)
        else:
            if current is None:
                raise CardSurfaceError(f"{ref} has no report to {action}")
            if action == PATCH:
                current["last_patch_at"] = at
            elif action == FINAL:
                # PREPARE only. The freeze is `confirm_report`'s, on a verified
                # acknowledgement — the same fail-closed order as card_period.
                current["phase"] = FINAL_PENDING
                current["archive"] = archive
                current["pending_at"] = at
        next_doc["reports"] = reports
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"applied": True, "report": current, "at": at}

    def confirm_report(self, *, period_ref: str, at: str, receipt: dict) -> dict:
        """Freeze a REPORT — only on a verified remote acknowledgement.

        A FAILED / UNSUPPORTED / UNKNOWN receipt is refused and the report stays
        `FINAL_PENDING` for a retry on the same message. The LIVE binding is not
        read and not written: a report that cannot be confirmed blocks only
        itself.
        """
        report = self._report(period_ref)
        if report is None:
            raise CardSurfaceError(f"{period_ref} has no report to confirm")
        if (report.get("phase") or LIVE_PHASE) != FINAL_PENDING:
            raise CardSurfaceError(
                f"{period_ref} is {(report.get('phase') or LIVE_PHASE)!r}, not a "
                f"pending final: there is nothing to confirm")
        state = str((receipt or {}).get("state") or "")
        if state != delivery.DELIVERED:
            raise CardSurfaceError(
                f"回执的 state 是 {state!r}，不是 {delivery.DELIVERED!r}：不能据此冻结"
                f"报告（失败/超时/未知留在 FINAL_PENDING，重试同一条消息）")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        for item in reports:
            if item.get("period_ref") == period_ref:
                item["phase"] = FROZEN
                item["frozen"] = {"at": str(at), "state": state,
                                  "remote_id": (receipt or {}).get("remote_id")
                                  or report.get("remote_id"),
                                  "version": (receipt or {}).get("version")}
                item["pending_at"] = None
        next_doc["reports"] = reports
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"confirmed": True, "period_ref": period_ref, "at": at}

    def summary(self) -> dict:
        live = self._data.get("live") or {}
        reports = self._data.get("reports") or []
        return {"schema": SCHEMA,
                "live": ({"remote_id": live.get("remote_id"),
                          "last_patch_at": live.get("last_patch_at"),
                          "rotations": int(live.get("rotations") or 0)} if live else None),
                "reports": [{"period_ref": r.get("period_ref"),
                             "phase": r.get("phase") or LIVE_PHASE} for r in reports]}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Print this instant's two decisions, for a hand check."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", default="")
    parser.add_argument("--store", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    at = args.at or datetime.datetime.now(
        datetime.timezone.utc).astimezone().isoformat()
    store = SurfaceStore(args.store or default_path())
    live = store.decide_live(at=at)
    if args.json:
        print(json.dumps(live, ensure_ascii=False, indent=2, default=str))
    else:
        print("LIVE  :", live["action"], "·", live["says"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
