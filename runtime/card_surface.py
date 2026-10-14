#!/usr/bin/env python3
"""Two surfaces: ONE long-lived LIVE card, and per-period REPORT cards.

WHY THIS IS NOT `card_period`
-----------------------------
`card_period` models «one card per period»: every period CREATEs its own message,
FINALs it at the period boundary, freezes it, and the next period CREATEs another.
The 2026-10-14 product ruling replaces that cadence with TWO SURFACES:

    LIVE    ONE card for the recipient, kept alive and PATCHed in place: a
            heartbeat on every half-hour SLOT, and IMMEDIATELY when an alert
            event (NEW / ESCALATED / RECOVERED / handling change) lands. The live
            alert area is a REGION of this card, never a third card.
    REPORT  a separate «ServerOS Core REPORT» card per period (morning /
            afternoon / night / holiday), with its OWN message id, frozen when
            its own delivery is acknowledged. It NEVER shares a message id with
            the LIVE card, and a REPORT's success or failure never freezes and
            never blocks the LIVE card.

THE CURSOR IS A SLOT, NOT «THE LAST TIME WE PATCHED»
----------------------------------------------------
A heartbeat is due when the half-hour SLOT has moved past the last slot whose
patch was ACKNOWLEDGED — `last_delivered_regular_slot`. It is deliberately NOT
`now - last_patch_at >= 1800`: an alert patched at 17:29 would push that clock
forward and make the 17:30 regular heartbeat look «not due», so a busy minute
could starve the regular cadence. Events move the EVENT cursor and never the
regular one; the two are separate fields because they answer separate questions.

CURSORS MOVE ONLY ON A DELIVERED RECEIPT
----------------------------------------
Staging a patch records the INTENT (which slot, which event keys). The cursors
are advanced by `confirm_live` alone, and only for a `DELIVERED` receipt naming
the message the patch was aimed at. A FAILED or UNKNOWN patch leaves the slot
and the event keys UNCONSUMED, so the next tick retries the same message instead
of treating a send that may never have arrived as done.

ALERT IS NOT A THIRD CARD. `card_delivery`'s per-episode `alert` / `recovery`
kinds keep their production path — the existing incident card is NOT deleted
here. This module carries the product surface's alerts inside the LIVE card.

WHAT IT REUSES (no second transport, no second ledger)
------------------------------------------------------
    card_delivery.CardLedger / binding_verdict   receipts, versions, 14-day window
    message_transport.LarkMessageSender          send_card / update / reconcile
    card_period                                  phases + the acknowledged renderer

No clock (every instant is a parameter), no I/O in the policy, and no change to
the running production `monitoring-policy.yaml` (`hourly` / `one-per-workday`).
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-surface.v1"

LIVE = "LIVE"
REPORT = "REPORT"
SURFACES = (LIVE, REPORT)

#: The heartbeat slot. The card's REFRESH cadence — NOT the sampling cadence.
SLOT_SECONDS = 1800

HEARTBEAT = "HEARTBEAT"
EVENT = "EVENT"
ROTATION = "ROTATION"
NOT_DUE = "NOT_DUE"

NEW = "NEW"
ESCALATED = "ESCALATED"
RECOVERED = "RECOVERED"
HANDLING = "HANDLING"
EVENT_KINDS = (NEW, ESCALATED, RECOVERED, HANDLING)

CREATE = "CREATE"
PATCH = "PATCH"
SUCCESSOR = "SUCCESSOR"
FINAL = "FINAL"
SKIP = "SKIP"
ACTIONS = (CREATE, PATCH, SUCCESSOR, FINAL, SKIP)

LIVE_PHASE = "LIVE"
FINAL_PENDING = "FINAL_PENDING"
FROZEN = "FROZEN"
#: A CREATE whose message id is not yet known. It is a DURABLE state, not an
#: absence: a restart that read it as «no binding» would send a SECOND live
#: card, which is the one thing a single-LIVE rule forbids.
CREATE_PENDING = "CREATE_PENDING"
#: A pending CREATE (or report) whose idempotency key is past the send service's
#: window. Re-sending under it is no longer deduplicated, so this state means
#: «a human or a real readback must decide» — never «send again».
NEEDS_RECONCILE = "NEEDS_RECONCILE"
PHASES = (LIVE_PHASE, FINAL_PENDING, FROZEN, CREATE_PENDING, NEEDS_RECONCILE)

#: The send service's idempotency window, matching `message_transport`. Past it
#: the same key is no longer deduplicated, so a retry is a NEW message.
IDEMPOTENCY_WINDOW_SECONDS = 3600

CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
ALREADY_FROZEN = "ALREADY_FROZEN"
NOT_A_PERIOD = "NOT_A_PERIOD"
NEVER_SENT = "NEVER_SENT"
PERIOD_ENDED = "PERIOD_ENDED"
SAME_PERIOD = "SAME_PERIOD"
UNVERIFIED = "UNVERIFIED"
RESUME_CREATE_PENDING = "RESUME_CREATE_PENDING"
PENDING_UNRESOLVED = "PENDING_UNRESOLVED"

#: Whether a receipt came from this process's own fake or from a real remote.
#: Recorded on every commit so a trace can never read a synthetic `DELIVERED`
#: as evidence that Feishu accepted anything.
EVIDENCE_SYNTHETIC = "synthetic"
EVIDENCE_REMOTE = "remote"

#: How many consumed EVENT keys the hot document keeps. Bounded so a long-lived
#: LIVE does not grow forever; the oldest keys fall off first, which at worst
#: lets a very old replay patch once more — never a wrong freeze.
CONSUMED_CAP = 200
#: How many REPORT rows stay hot. A FROZEN report past the cap is moved to a
#: compact `retired` index rather than deleted, so history is not lost while the
#: hot document stays bounded.
REPORT_HOT_CAP = 64


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
# instants, slots and event identities — pure
# ---------------------------------------------------------------------------

def _moment(value):
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _age_seconds(then, now):
    """Seconds from `then` to `now`, or None when either cannot be read."""
    before, after = _moment(then), _moment(now)
    if before is None or after is None:
        return None
    return (after - before).total_seconds()


def receipt_remote_id(receipt) -> str:
    """The message a receipt names — from the receipt's TOP level or its provider.

    `message_transport` answers with the id under `provider.remote_id`, and a
    caller may hand in a flattened row where it sits at the top level. Reading
    only one of the two shapes is how a DELIVERED receipt comes to look like «it
    named no message», so both are read here and the question is answered once.
    """
    if not isinstance(receipt, dict):
        return ""
    provider = receipt.get("provider") if isinstance(receipt.get("provider"), dict) else {}
    return str(receipt.get("remote_id") or provider.get("remote_id") or "").strip()


def receipt_evidence(receipt) -> str:
    """WHOSE word a receipt is: this process's own fake, or a real remote.

    An UNLABELLED receipt is the WEAKER claim, never the stronger: a synthetic
    `DELIVERED` must not be able to read as evidence that Feishu accepted
    anything, and the safe direction is to under-claim.
    """
    if not isinstance(receipt, dict):
        return EVIDENCE_SYNTHETIC
    stated = str(receipt.get("evidence") or "").strip()
    if stated in (EVIDENCE_SYNTHETIC, EVIDENCE_REMOTE):
        return stated
    provider = receipt.get("provider") if isinstance(receipt.get("provider"), dict) else {}
    return (EVIDENCE_REMOTE if str(provider.get("id") or "") == "lark-cli"
            else EVIDENCE_SYNTHETIC)


def regular_slot_id(at, *, slot_seconds: int = SLOT_SECONDS) -> int | None:
    """The stable half-hour slot an instant falls in: `utc_epoch // slot_seconds`.

    A SLOT ID and not a timestamp, because the question the cadence asks is
    «which half-hour is this», not «how long since the last patch». Keying on
    time-since-last-patch is exactly the bug the 2026-10-14 review names: an
    alert at 17:29 would postpone the 17:30 regular heartbeat.

    An instant with no zone is refused (None) rather than assumed local: a slot
    that depends on the reader's own clock would move between hosts. The
    decimal display a person sees is the local `:00/:30`, which the renderer
    handles; this is the cadence key.
    """
    if not isinstance(slot_seconds, int) or isinstance(slot_seconds, bool) \
            or slot_seconds <= 0:
        raise CardSurfaceError(f"slot_seconds must be a positive integer, not {slot_seconds!r}")
    moment = _moment(at)
    if moment is None or moment.tzinfo is None:
        return None
    return int(moment.timestamp()) // slot_seconds


def event_key(event) -> str | None:
    """The stable identity of one alert event: `episode/event_id@version`.

    `None` when the event carries no identity at all — such an event is
    UNVERIFIED and never triggers a patch, because an event nobody can name
    would re-patch the card on every poll. A missing version is carried as `?`,
    so a versioned replay is distinguished from an unversioned one instead of
    the two colliding.
    """
    if not isinstance(event, dict):
        return None
    ident = event.get("episode") or event.get("event_id")
    if not str(ident or "").strip():
        return None
    version = str(event.get("version") or "").strip() or "?"
    return f"{str(ident).strip()}@{version}"


def event_kinds(events) -> list[str]:
    """The deduped, ordered alert kinds in one batch (a display summary)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in events or ():
        raw = item.get("kind") if isinstance(item, dict) else item
        kind = str(raw or "").upper()
        if kind in EVENT_KINDS and kind not in seen:
            seen.add(kind)
            out.append(kind)
    return out


def _fresh_events(events, consumed) -> tuple[list[str], int]:
    """The event keys NOT already consumed, and the count of unverifiable ones.

    Two different episodes with the SAME kind are two events (`ep@1` and
    `ep2@1`); the same episode replayed is one (`ep@1` twice). That is the whole
    reason the identity is the pair and not the kind.
    """
    consumed = consumed if isinstance(consumed, dict) else {}
    fresh: list[str] = []
    unverified = 0
    seen: set[str] = set()
    for item in events or ():
        key = event_key(item)
        if key is None:
            unverified += 1
            continue
        if key in consumed or key in seen:
            continue
        seen.add(key)
        fresh.append(key)
    return fresh, unverified


def heartbeat_due(*, slot: int | None, last_delivered_slot) -> bool:
    """Whether the regular heartbeat slot has moved past the delivered one.

    An unreadable stored slot answers True (refresh rather than stall); an
    unreadable `now` slot answers False (act on nothing).
    """
    if slot is None:
        return False
    if last_delivered_slot is None:
        return True
    try:
        return int(slot) > int(last_delivered_slot)
    except (TypeError, ValueError):
        return True


def live_decision(*, now, binding, events=(), live_state=None,
                  slot_seconds: int = SLOT_SECONDS, window_days: int | None = None) -> dict:
    """What the LIVE surface does at this instant, and why. PURE.

    At most ONE patch, because a heartbeat and an event landing in the same
    instant are COALESCED (both reasons reported). The patchability question is
    `card_delivery.binding_verdict`'s, asked rather than re-derived.
    """
    window = delivery.update_window_days() if window_days is None else window_days
    state = live_state if isinstance(live_state, dict) else {}
    consumed = state.get("consumed_events") or {}
    fresh, unverified = _fresh_events(events, consumed)
    slot = regular_slot_id(now, slot_seconds=slot_seconds)
    heartbeat = heartbeat_due(slot=slot,
                              last_delivered_slot=state.get("last_delivered_regular_slot"))
    judged = delivery.binding_verdict(binding, at=now, window_days=window)
    reasons: list[str] = []
    if fresh:
        reasons.append(EVENT)
    if heartbeat:
        reasons.append(HEARTBEAT)

    base = {"surface": LIVE, "events": fresh, "unverified_events": unverified,
            "slot": slot, "reasons": reasons, "window_days": window,
            "at": now, "coalesced": EVENT in reasons and HEARTBEAT in reasons}

    if judged["reason"] == delivery.IDENTITY_EXPIRED:
        return dict(base, action=SUCCESSOR, message_action=delivery.SUCCESSOR_MESSAGE,
                    reason=ROTATION, reasons=reasons + [ROTATION],
                    remote_id=judged["remote_id"],
                    says=(f"承载 LIVE 的消息已发出约 {int(judged['age_days'])} 天，超过 "
                          f"{window} 天可改窗口：按台账有据地**继任**——旧消息停止更新，"
                          f"新消息继续承载同一份实时状态"))
    if judged["reason"] == delivery.IDENTITY_MISSING:
        return dict(base, action=CREATE, message_action=delivery.NEW_MESSAGE,
                    reason=NEVER_SENT, remote_id=None,
                    says="还没有承载 LIVE 的消息：本次新建一条，此后一直是这一条")
    if judged["reason"] == delivery.IDENTITY_UNDATED:
        return dict(base, action=SKIP, message_action=None,
                    reason=delivery.IDENTITY_UNDATED, remote_id=judged["remote_id"],
                    says="承载 LIVE 的发出时间读不出来，可改窗口无法判断：不据此 PATCH")
    if not reasons:
        return dict(base, action=SKIP, message_action=None, reason=NOT_DUE,
                    remote_id=judged["remote_id"], reasons=[],
                    says="没到心跳 slot，也没有新事件：这一拍不动 LIVE")
    return dict(base, action=PATCH, message_action=delivery.PATCH_MESSAGE,
                reason=reasons[0], remote_id=judged["remote_id"],
                says="LIVE 到点更新（" + "、".join(reasons) + "）")


def successor_binding(*, previous, remote_id: str, at: str) -> dict:
    """The binding a ROTATED LIVE carries forward. The old id is kept, not lost."""
    if not str(remote_id or "").strip():
        raise CardSurfaceError("a successor needs the message it succeeds to")
    previous = previous if isinstance(previous, dict) else {}
    return {"remote_id": str(remote_id).strip(), "bound_at": str(at),
            "previous_remote_id": previous.get("remote_id"),
            "rotations": int(previous.get("rotations") or 0) + 1,
            "last_delivered_regular_slot": previous.get("last_delivered_regular_slot"),
            "consumed_events": dict(previous.get("consumed_events") or {}),
            "last_patch_at": previous.get("last_patch_at")}


# ---------------------------------------------------------------------------
# the REPORT policy — pure
# ---------------------------------------------------------------------------

def report_decision(*, period, reports, closing=None, at=None) -> dict:
    """What the REPORT surface does at this instant. PURE.

    The order is: resume an UNBOUND report's own CREATE, then close a PENDING
    FINAL, then close a report whose PERIOD IS NO LONGER CURRENT — including when
    `now` is outside every period (`in_period: false`, e.g. after midnight),
    which is exactly when a cross-shift report would otherwise be stranded. Only
    then does the current period's own report open or update. LIVE is never
    touched by any branch.
    """
    rows = [r for r in (reports or []) if isinstance(r, dict) and r.get("period_ref")]
    current = (period or {}).get("period_ref") if (period or {}).get("in_period") else None

    for report in rows:
        phase = report.get("phase") or LIVE_PHASE
        if phase in (CREATE_PENDING, NEEDS_RECONCILE):
            pending = report.get("pending") if isinstance(report.get("pending"), dict) else {}
            age = _age_seconds(pending.get("at"), at) if at else None
            if phase == CREATE_PENDING and age is not None \
                    and age <= IDEMPOTENCY_WINDOW_SECONDS:
                return {"surface": REPORT, "action": CREATE, "resume": True,
                        "reason": RESUME_CREATE_PENDING, "period_ref": report["period_ref"],
                        "idempotency_key": pending.get("idempotency_key"),
                        "says": ("上一份报告的 CREATE 意图仍在幂等窗口内："
                                 "用**同一个** key 重发同一条，不新建")}
            return {"surface": REPORT, "action": SKIP, "reason": NEEDS_RECONCILE,
                    "period_ref": report["period_ref"],
                    "says": ("上一份报告的 CREATE 意图已超出幂等窗口（或时间读不出来）："
                             "**不重发**，需要真实 readback 或人工裁决")}
    for report in rows:
        if (report.get("phase") or LIVE_PHASE) == FINAL_PENDING:
            return {"surface": REPORT, "action": FINAL, "reason": FINAL_PENDING,
                    "period_ref": report["period_ref"], "retry": True,
                    "says": ("上一份报告尚未被远端确认：本次重试**同一条** message_id，"
                             "不新建，也不影响仍在更新的 LIVE")}
    for report in rows:
        if (report.get("phase") or LIVE_PHASE) == LIVE_PHASE \
                and report["period_ref"] != current:
            return {"surface": REPORT, "action": FINAL, "reason": PERIOD_ENDED,
                    "period_ref": report["period_ref"],
                    "says": (f"{report['period_ref']} 的时段已过去：本次对**那一条**"
                             f"报告做最后一次 patch 并封存，与当前时段无关")}
    if closing and current and closing == current:
        return {"surface": REPORT, "action": FINAL, "reason": PERIOD_ENDED,
                "period_ref": current, "says": "时段结束：封存本时段的报告"}

    if not current:
        return {"surface": REPORT, "action": SKIP, "reason": CALENDAR_UNKNOWN,
                "period_ref": None, "says": "当前没有时段身份；已有的未封存报告已在上方处理"}
    mine = next((r for r in rows if r["period_ref"] == current), None)
    if mine is None:
        return {"surface": REPORT, "action": CREATE, "reason": NOT_A_PERIOD,
                "period_ref": current,
                "says": (f"{current} 还没有报告：新建一条**独立**消息承载它，"
                         f"与 LIVE 不共用 message_id")}
    if (mine.get("phase") or LIVE_PHASE) == FROZEN:
        return {"surface": REPORT, "action": SKIP, "reason": ALREADY_FROZEN,
                "period_ref": current, "says": f"{current} 的报告已冻结：不再变化"}
    return {"surface": REPORT, "action": PATCH, "reason": SAME_PERIOD,
            "period_ref": current, "says": f"{current} 的报告已在：patch 这一条"}


# ---------------------------------------------------------------------------
# the document on disk — one store, two surfaces
# ---------------------------------------------------------------------------

def empty() -> dict:
    return {"schema": SCHEMA, "live": None, "reports": [], "retired": []}


def surface_path(state_path) -> Path:
    return Path(state_path).parent.parent / "cards" / "surfaces.json"


def default_path() -> Path:
    return surface_path(_registry().state_root() / "runtime" / "alerts" / "state.json")


def _trim_reports(document: dict) -> dict:
    """Bound the HOT report rows without losing history.

    A FROZEN report past `REPORT_HOT_CAP` moves to a compact `retired` index
    (period_ref, remote_id, frozen_at) rather than disappearing: the hot document
    stays bounded and the chain is still followable through the receipts the
    ledger already holds. An OPEN or PENDING report is never evicted — evicting
    one would strand a report that still owes a final patch.
    """
    reports = [dict(r) for r in document.get("reports") or []]
    if len(reports) <= REPORT_HOT_CAP:
        return document
    retired = [dict(r) for r in document.get("retired") or []]
    keep, evictable = [], []
    for report in reports:
        if (report.get("phase") or LIVE_PHASE) == FROZEN:
            evictable.append(report)
        else:
            keep.append(report)
    room = max(0, REPORT_HOT_CAP - len(keep))
    if len(evictable) <= room:
        return document
    evicted = evictable[:len(evictable) - room]
    retired.extend({"period_ref": r["period_ref"], "remote_id": r.get("remote_id"),
                    "frozen_at": (r.get("frozen") or {}).get("at")} for r in evicted)
    document["reports"] = keep + evictable[len(evicted):]
    document["retired"] = retired
    return document


class SurfaceStore:
    """The LIVE binding and the REPORT bindings. One document, one atomic write.

    An unreadable store is REFUSED rather than replaced: a fresh document would
    lose the LIVE binding and the next tick would CREATE a second live card.
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
            if not isinstance(live, dict):
                raise CardSurfaceError(f"{self.path} has a non-object `live`")
            phase = live.get("phase") or (LIVE_PHASE if live.get("remote_id")
                                          else CREATE_PENDING)
            if phase in (CREATE_PENDING, NEEDS_RECONCILE):
                # A CREATE whose id is unknown is READABLE, not broken. It must
                # carry the durable intent, because that is the only thing a
                # restart can resume from — reading it as «no binding» would
                # send a second live card.
                pending = live.get("pending")
                if not isinstance(pending, dict) or not str(pending.get("idempotency_key") or ""):
                    raise CardSurfaceError(
                        f"the LIVE card in {self.path} is {phase!r} with no durable "
                        f"idempotency key: a restart could only guess, and guessing "
                        f"is a SECOND live card")
                if not str(pending.get("recipient") or ""):
                    raise CardSurfaceError(
                        f"the LIVE card in {self.path} is {phase!r} and names no "
                        f"recipient; a resumed send must go to the SAME person")
            else:
                if not str(live.get("remote_id") or ""):
                    raise CardSurfaceError(
                        f"the LIVE binding in {self.path} names no message: a binding "
                        f"to nothing would let the next patch guess a message")
                if not str(live.get("bound_at") or ""):
                    raise CardSurfaceError(
                        f"the LIVE binding in {self.path} has no `bound_at`")
        for report in data["reports"]:
            if not isinstance(report, dict) or not report.get("period_ref"):
                raise CardSurfaceError(
                    f"a report in {self.path} has no `period_ref`")
            if (report.get("phase") or LIVE_PHASE) not in PHASES:
                raise CardSurfaceError(
                    f"a report in {self.path} is in phase {report.get('phase')!r}, "
                    f"which is not one of {', '.join(PHASES)}")
        return data

    def read(self) -> dict:
        return self._data

    def _write(self, document: dict) -> None:
        document = _trim_reports(document)
        _store_io()._atomic_write(self.path, document)
        self._data = document

    # -- LIVE ---------------------------------------------------------------

    def decide_live(self, *, at, events=(), slot_seconds=SLOT_SECONDS,
                    window_days=None) -> dict:
        live = self._data.get("live") or {}
        phase = live.get("phase") or (LIVE_PHASE if live.get("remote_id") else None)
        if phase in (CREATE_PENDING, NEEDS_RECONCILE):
            return self._resume_or_reconcile(live=live, at=at, phase=phase)
        binding = None
        if live.get("remote_id"):
            binding = {"remote_id": live["remote_id"], "at": live.get("bound_at")}
        return live_decision(now=at, binding=binding, events=events,
                             live_state=live, slot_seconds=slot_seconds,
                             window_days=window_days)

    def _resume_or_reconcile(self, *, live: dict, at: str, phase: str) -> dict:
        """What an UNBOUND live does: resume its own intent, or stop.

        Past the send service's idempotency window the same key is no longer
        deduplicated, so re-sending is a NEW message. That is not a retry — it is
        the second live card — so the answer is NEEDS_RECONCILE and nothing is
        sent. The phase is persisted here so a restart reads the same verdict.
        """
        pending = live.get("pending") or {}
        age = _age_seconds(pending.get("at"), at)
        base = {"surface": LIVE, "action": SKIP, "message_action": None,
                "reasons": [], "events": [], "slot": pending.get("slot"),
                "remote_id": None, "at": at, "coalesced": False,
                "idempotency_key": pending.get("idempotency_key"),
                "reasons_blocked": True}
        if age is None or age > IDEMPOTENCY_WINDOW_SECONDS:
            if phase != NEEDS_RECONCILE:
                next_doc = dict(self._data)
                stuck = dict(live)
                stuck["phase"] = NEEDS_RECONCILE
                next_doc["live"] = stuck
                self._write(next_doc)
            return dict(base, reason=NEEDS_RECONCILE,
                        says=("上一轮 CREATE 的意图已超出幂等窗口（或时间读不出来）："
                              "同 key 重发不再被去重，因此**不重发**。"
                              "需要真实 readback 或人工裁决"))
        return {"surface": LIVE, "action": CREATE, "message_action": delivery.NEW_MESSAGE,
                "reason": RESUME_CREATE_PENDING, "resume": True,
                "idempotency_key": pending.get("idempotency_key"),
                "reasons": [RESUME_CREATE_PENDING], "events": [],
                "slot": pending.get("slot"), "remote_id": None, "at": at,
                "coalesced": False, "reasons_blocked": False,
                "says": "上一轮 CREATE 的意图仍在幂等窗口内：用**同一个** key 重发同一条，"
                        "而不是新建第二条"}

    def mark_needs_reconcile(self, *, at: str, why: str = "") -> dict:
        """Park the live surface for a human/readback decision. Sends nothing."""
        next_doc = dict(self._data)
        live = dict(next_doc.get("live") or {})
        if not live:
            raise CardSurfaceError("there is no live surface to park")
        live["phase"] = NEEDS_RECONCILE
        live["reconcile_reason"] = str(why or "unspecified")
        live["reconcile_at"] = str(at)
        next_doc["live"] = live
        self._write(next_doc)
        return {"phase": NEEDS_RECONCILE, "at": at}

    def stage_live(self, *, decision: dict, at: str, idempotency_key=None,
                   recipient=None, payload_digest=None) -> dict:
        """Record the INTENT to perform this LIVE step. Cursors do not move.

        Staging is separate from committing because a patch that was ATTEMPTED is
        not a patch that ARRIVED: only `confirm_live`, on a DELIVERED receipt,
        may consume the slot or the event keys.

        AN UNRESOLVED PENDING IS NEVER OVERWRITTEN. Re-staging under the SAME
        idempotency key is a legitimate retry of the same intent; re-staging
        under a DIFFERENT one would silently drop a send that may already be in
        flight, so it is refused.
        """
        action = (decision or {}).get("action")
        if action not in (CREATE, SUCCESSOR, PATCH, SKIP):
            raise CardSurfaceError(f"{action!r} is not a LIVE step")
        next_doc = dict(self._data)
        live = dict(next_doc.get("live") or {})
        if action == SKIP:
            return {"staged": False, "decision": decision}
        key = str(idempotency_key or (decision or {}).get("idempotency_key") or "").strip()
        if not key:
            raise CardSurfaceError(
                f"a {action} must carry a durable idempotency key: it is what a "
                f"restart resumes from and what the service deduplicates on")
        held = live.get("pending") if isinstance(live.get("pending"), dict) else None
        if held and held.get("idempotency_key") and held.get("idempotency_key") != key:
            raise CardSurfaceError(
                f"an unresolved {held.get('action')} intent "
                f"({held.get('idempotency_key')!r}) is still pending; staging "
                f"{action!r} under {key!r} would drop a send that may be in flight")
        entry = {"action": action, "at": str(at), "slot": decision.get("slot"),
                 "event_keys": list(decision.get("events") or []),
                 "remote_id": decision.get("remote_id"),
                 "heartbeat": HEARTBEAT in (decision.get("reasons") or []),
                 "idempotency_key": key,
                 "recipient": str(recipient or (held or {}).get("recipient") or ""),
                 "payload_digest": payload_digest}
        if action == CREATE and not live.get("remote_id"):
            # the durable CREATE-pending state: readable, resumable, and NOT an
            # empty live that a restart would read as «create one».
            live = {"phase": CREATE_PENDING, "pending": entry,
                    "previous_remote_id": live.get("previous_remote_id"),
                    "rotations": int(live.get("rotations") or 0),
                    "consumed_events": dict(live.get("consumed_events") or {}),
                    "last_delivered_regular_slot": live.get("last_delivered_regular_slot")}
        else:
            live["pending"] = entry
        next_doc["live"] = live
        self._write(next_doc)
        return {"staged": True, "pending": entry, "decision": decision}

    def confirm_live(self, *, at: str, receipt: dict,
                     evidence: str = EVIDENCE_SYNTHETIC) -> dict:
        """Commit a staged LIVE step — ONLY on a DELIVERED receipt.

        A FAILED / UNSUPPORTED / UNKNOWN receipt, or one naming a DIFFERENT
        message, is refused and the intent stays pending: the slot and the event
        keys are NOT consumed, so the next tick retries the SAME message rather
        than treating a send that may never have arrived as done.

        `evidence` records WHOSE word the receipt is. A synthetic `DELIVERED`
        from this process's own fake is not evidence that the far end accepted
        anything, and the state says so.
        """
        live = self._data.get("live") or {}
        pending = live.get("pending")
        if not isinstance(pending, dict):
            raise CardSurfaceError("no LIVE step is pending to confirm")
        state = str((receipt or {}).get("state") or "")
        if state != delivery.DELIVERED:
            raise CardSurfaceError(
                f"回执的 state 是 {state!r}，不是 {delivery.DELIVERED!r}：不推进 slot/"
                f"事件游标，也不新建消息——留在 pending，下一拍重试同一条")
        action = pending.get("action")
        next_doc = dict(self._data)
        current = dict(next_doc.get("live") or {})
        if action in (CREATE, SUCCESSOR):
            remote_id = str(receipt_remote_id(receipt)
                            or pending.get("remote_id") or "").strip()
            if not remote_id:
                raise CardSurfaceError(
                    f"a {action} receipt names no message id; there is nothing to bind")
            if action == SUCCESSOR:
                current = successor_binding(previous=current, remote_id=remote_id, at=at)
            else:
                current = {"remote_id": remote_id, "bound_at": at,
                           "previous_remote_id": None, "rotations": 0,
                           "last_delivered_regular_slot": pending.get("slot"),
                           "consumed_events": {}, "last_patch_at": at}
            if pending.get("slot") is not None and action == CREATE:
                current["last_delivered_regular_slot"] = pending["slot"]
            current["phase"] = LIVE_PHASE
            current["evidence"] = evidence
        elif action == PATCH:
            target = str(pending.get("remote_id") or "")
            echoed = str(receipt_remote_id(receipt) or target)
            if target and echoed and echoed != target:
                raise CardSurfaceError(
                    f"回执命名的消息 {echoed!r} 不是这次 patch 的目标 {target!r}："
                    f"不拿另一条消息的回执推进这一条的游标")
            if pending.get("heartbeat") and pending.get("slot") is not None:
                current["last_delivered_regular_slot"] = pending["slot"]
            consumed = dict(current.get("consumed_events") or {})
            for key in pending.get("event_keys") or []:
                consumed[key] = {"at": str(at), "slot": pending.get("slot")}
            # bound the cursor so a long-lived LIVE cannot grow forever
            if len(consumed) > CONSUMED_CAP:
                for key in list(consumed)[:len(consumed) - CONSUMED_CAP]:
                    consumed.pop(key, None)
            current["consumed_events"] = consumed
            current["last_patch_at"] = str(at)
            current["evidence"] = evidence
        else:
            raise CardSurfaceError(f"{action!r} is not a confirmable LIVE step")
        current.pop("pending", None)
        next_doc["live"] = current
        self._write(next_doc)
        return {"confirmed": True, "action": action, "live": current, "at": at}

    def fail_live(self, *, reason: str) -> dict:
        """Drop a staged intent after a terminal failure. Cursors do not move."""
        next_doc = dict(self._data)
        live = dict(next_doc.get("live") or {})
        pending = live.pop("pending", None)
        next_doc["live"] = live
        self._write(next_doc)
        return {"dropped": pending is not None, "reason": reason}

    # -- REPORT -------------------------------------------------------------

    def decide_report(self, *, period, closing=None, at=None) -> dict:
        return report_decision(period=period, reports=self._data.get("reports") or [],
                               closing=closing, at=at)

    def stage_report(self, *, period_ref: str, at: str, idempotency_key: str,
                     payload_digest=None, label=None, day_ref=None) -> dict:
        """Persist the INTENT to create a period's report, BEFORE it is sent.

        The row it writes has no message id yet — that is the point: a process
        that dies between the send and the id being recorded must find the intent
        on disk and resume the SAME message, not create a second report.
        """
        key = str(idempotency_key or "").strip()
        if not period_ref or not key:
            raise CardSurfaceError("a report intent needs a period_ref and an idempotency key")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        current = next((r for r in reports if r.get("period_ref") == period_ref), None)
        if current is not None:
            held = (current.get("pending") or {}).get("idempotency_key")
            if (current.get("phase") or LIVE_PHASE) == CREATE_PENDING and held == key:
                return {"staged": True, "idempotent": True, "report": current}
            raise CardSurfaceError(
                f"{period_ref} already has a report row ({current.get('phase') or LIVE_PHASE}); "
                f"staging {key!r} over it would drop a send that may be in flight")
        row = {"period_ref": period_ref, "phase": CREATE_PENDING, "remote_id": None,
               "label": label, "day_ref": day_ref, "bound_at": None,
               "last_patch_at": None, "archive": None, "frozen": None,
               "prepared_version": None,
               "pending": {"action": CREATE, "at": str(at),
                           "idempotency_key": key, "payload_digest": payload_digest}}
        reports.append(row)
        next_doc["reports"] = reports
        self._write(next_doc)
        return {"staged": True, "report": row}

    def bind_report(self, *, period_ref: str, at: str, remote_id: str,
                    evidence: str = EVIDENCE_SYNTHETIC) -> dict:
        """Promote a REPORT CREATE_PENDING to BOUND — only with a real message id."""
        message_id = str(remote_id or "").strip()
        if not message_id:
            raise CardSurfaceError(
                "a report cannot be bound without a message id; a report bound to "
                "nothing would be patched by guesswork")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        row = next((r for r in reports if r.get("period_ref") == period_ref), None)
        if row is None:
            raise CardSurfaceError(f"{period_ref} has no report row to bind")
        if (row.get("phase") or LIVE_PHASE) not in (CREATE_PENDING, NEEDS_RECONCILE):
            raise CardSurfaceError(
                f"{period_ref} is {(row.get('phase') or LIVE_PHASE)!r}, "
                f"not an unbound report")
        row.update({"phase": LIVE_PHASE, "remote_id": message_id, "bound_at": str(at),
                    "last_patch_at": str(at), "pending": None, "evidence": evidence})
        next_doc["reports"] = reports
        self._write(next_doc)
        return {"bound": True, "report": row}

    def apply_report(self, *, period, decision: dict, at: str, remote_id=None,
                     archive=None, prepared_version=None) -> dict:
        """Record the REPORT's state. LIVE is never touched here.

        THIS IS THE INDEPENDENCE: a report's phase moves in the report's own row
        and there is no code path from here to `live`.
        """
        action = (decision or {}).get("action")
        ref = (decision or {}).get("period_ref") or (period or {}).get("period_ref")
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
                       "last_patch_at": at, "archive": archive, "frozen": None,
                       "prepared_version": None}
            reports.append(current)
        else:
            if current is None:
                raise CardSurfaceError(f"{ref} has no report to {action}")
            if action == PATCH:
                # NOT confirmed here. A patch that was ATTEMPTED is not a patch
                # that ARRIVED, and a local `last_patch_at` that moved on a
                # FAILED send would make the next render believe the far end is
                # showing this version. The observation is kept as PENDING and
                # only `ack_report_patch` promotes it.
                current["pending_patch"] = {"at": str(at), "archive": archive}
            elif action == FINAL:
                current["phase"] = FINAL_PENDING
                current["archive"] = archive
                current["pending_at"] = at
                # The version this final report WILL go out as. The acknowledgement
                # is checked against it, so a receipt for another card cannot
                # freeze this one.
                current["prepared_version"] = (str(prepared_version)
                                               if prepared_version else None)
        next_doc["reports"] = reports
        self._write(next_doc)
        return {"applied": True, "report": current, "at": at}

    def ack_report_patch(self, *, period_ref: str, at: str, receipt: dict) -> dict:
        """Promote a PENDING report patch — only on an acknowledgement OF IT.

        Mirrors `confirm_live`: a FAILED / UNKNOWN receipt, or one naming another
        message, leaves the pending observation in place and the confirmed cursor
        (`last_patch_at` / `archive`) exactly where it was.
        """
        report = next((r for r in self._data.get("reports") or []
                       if r.get("period_ref") == period_ref), None)
        if report is None:
            raise CardSurfaceError(f"{period_ref} has no report to acknowledge")
        pending = report.get("pending_patch")
        if not isinstance(pending, dict):
            raise CardSurfaceError(f"{period_ref} has no pending patch to acknowledge")
        state = str((receipt or {}).get("state") or "")
        if state != delivery.DELIVERED:
            raise CardSurfaceError(
                f"回执的 state 是 {state!r}，不是 {delivery.DELIVERED!r}：报告的 patch "
                f"不算已确认，保留 pending，不推进确认游标")
        bound = str(report.get("remote_id") or "").strip()
        got_id = receipt_remote_id(receipt)
        if not got_id or not bound or got_id != bound:
            raise CardSurfaceError(
                f"回执命名的消息 {got_id!r} 不是这份报告绑定的 {bound!r}：不推进确认游标")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        for item in reports:
            if item.get("period_ref") == period_ref:
                item["last_patch_at"] = pending["at"]
                if pending.get("archive") is not None:
                    item["archive"] = pending["archive"]
                item["pending_patch"] = None
                item["evidence"] = receipt_evidence(receipt)
        next_doc["reports"] = reports
        self._write(next_doc)
        return {"acked": True, "period_ref": period_ref, "at": at}

    def confirm_report(self, *, period_ref: str, at: str, receipt: dict,
                       evidence: str = EVIDENCE_SYNTHETIC) -> dict:
        """Freeze a REPORT — only on a verified acknowledgement OF THAT REPORT.

        Three gates, all required: `state == DELIVERED`; the receipt's `version`
        matches the report's prepared version (when one was prepared); and the
        receipt names THIS report's message. A DELIVERED receipt for another card
        is not evidence about this one. The LIVE binding is neither read nor
        written — a report that cannot be confirmed blocks only itself.
        """
        report = next((r for r in self._data.get("reports") or []
                       if r.get("period_ref") == period_ref), None)
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
                f"（失败/超时/未知留在 FINAL_PENDING，重试同一条消息）")
        prepared = str(report.get("prepared_version") or "").strip()
        if not prepared:
            # A freeze needs the version its final PATCH went out AS; a report
            # that names none can be checked against no receipt at all.
            raise CardSurfaceError(
                f"{period_ref} 没有 prepared_version：冻结必须对照这份报告最终 PATCH "
                f"所依据的版本，而它没有，因此不能冻结")
        got_version = str((receipt or {}).get("version") or "").strip()
        if got_version != prepared:
            raise CardSurfaceError(
                f"回执的版本 {got_version!r} 与这份报告准备好的 {prepared!r} 不一致："
                f"这是一份关于**另一张卡**的证明，不能用来冻结这一张")
        bound = str(report.get("remote_id") or "").strip()
        got_id = receipt_remote_id(receipt)
        if not got_id:
            # NO fallback to the bound id: a receipt with no identity is not
            # evidence about THIS message, it is evidence about nothing.
            raise CardSurfaceError(
                "回执没有指名消息（顶层与 provider 都没有 remote_id）：没有身份的回执"
                "不能冻结任何报告")
        if not bound or got_id != bound:
            raise CardSurfaceError(
                f"回执命名的消息 {got_id!r} 不是这份报告绑定的 {bound!r}："
                f"不能拿另一条消息的回执冻结这份报告")
        next_doc = dict(self._data)
        reports = [dict(r) for r in next_doc.get("reports") or []]
        for item in reports:
            if item.get("period_ref") == period_ref:
                item["phase"] = FROZEN
                item["frozen"] = {"at": str(at), "state": state,
                                  "remote_id": got_id, "version": got_version,
                                  "evidence": evidence}
                item["pending_at"] = None
                item["evidence"] = evidence
        next_doc["reports"] = reports
        self._write(next_doc)
        return {"confirmed": True, "period_ref": period_ref, "at": at}

    def summary(self) -> dict:
        live = self._data.get("live") or {}
        reports = self._data.get("reports") or []
        return {"schema": SCHEMA,
                "live": ({"remote_id": live.get("remote_id"),
                          "last_delivered_regular_slot": live.get("last_delivered_regular_slot"),
                          "consumed_events": len(live.get("consumed_events") or {}),
                          "pending": live.get("pending"),
                          "rotations": int(live.get("rotations") or 0)} if live else None),
                "reports": [{"period_ref": r.get("period_ref"),
                             "phase": r.get("phase") or LIVE_PHASE} for r in reports],
                "retired": len(self._data.get("retired") or [])}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Print this instant's LIVE decision, for a hand check."""
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
