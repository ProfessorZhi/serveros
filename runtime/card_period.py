#!/usr/bin/env python3
"""§11½ — the period card: one message per period, patched quietly, closed once.

WHY THIS IS NOT `card_day`
--------------------------
`card_day` answers «one card per WORKDAY, patched at the hour». Its clock is
§10's 08:00–20:00 window and its unit is the hour, and both are properties of the
ROUTINE BROADCAST cadence — the cadence §10 constrains precisely because a
routine broadcast is a message a person is meant to notice.

This module answers a different question, and the difference is the one the
2026-10-14 ruling writes down: §10's 08:00–20:00 constrains the routine ACTIVE
broadcast. It does not constrain resource SAMPLING, and it does not constrain the
silent in-place PATCH of a card that already exists. So the monitoring surface
stays up around the clock — 全天候保留完整 V13 LIVE 监控卡 — and what changes
across a day is only which MESSAGE carries it.

A PERIOD is a named stretch of that surface. The default table, configurable by
declaration:

    上午 09:00–12:00     下午 12:00–18:00     夜间 18:00–次日 09:00
    NON_WORKING_DAY      全天 00:00–24:00

The table is a DECLARATION and not a derivation — the rule
`runtime/recipe-contract.yaml` §7 states for a recipe's period
(«a loop that picks its own window writes a record about a period nobody asked
for»). One message is created per period; every later tick patches THAT message
rather than sending another; at the period's end one FINAL patch writes the
archive and the card is FROZEN. That is «换班»: the shift changes, the card does
not multiply.

WHAT A PERIOD TICK DOES, AND WHAT IT REFUSES TO CLAIM
-----------------------------------------------------
Within a period a tick every `tick_seconds` (default 600 — ten minutes) patches
the same message, and a patch is SILENT: the in-place update raises no
notification. That is the measured fact `runtime/monitoring_policy.py` was
written around — «a patched message does not raise a new notification» — so
«夜间不频繁推送» is not a special case here; it is what a patch already is. The
one message a period may visibly add is the CREATE, and this module does not
claim otherwise: a first tick that creates a card MAY be a visible message, and
the ruling says so out loud («新时段卡初建可能有一次可见消息，不可声称静默创建»).

Major incidents do NOT ride this ladder. They keep §12's separate short
NEW / ESCALATED / RECOVERED notification (`card_delivery`'s per-episode kinds),
and this LIVE card is preserved beside them rather than replaced by them.

WHAT IT REUSES, SO THAT NOTHING IS WRITTEN TWICE
------------------------------------------------
    business_calendar.day   which day this instant is, and on whose authority
    card_delivery           the version, the receipt, the update window, the
                            freeze, and `binding_verdict`
    message_transport       the in-place PATCH (`im messages patch`)
    card_day                the compact row vocabulary and `timeline`
    fleet_monitor_card      the V13 three-device card, unchanged

No second delivery ledger is written and no renderer is duplicated: the FINAL
card is the fleet card with an archive written above it.

WHAT IT WILL NOT DO
-------------------
It reads no clock (every instant is a parameter), measures nothing (it is handed
a model), and sends nothing (it answers; the caller transports). It opens no
chart: the VChart element and the narrow-screen `column_set` are both still
unverified against a real client, so `charts_verified` stays False and the
default card is the text one — «schema 有文档» is not «真机渲染过».
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-period.v1"

CREATE = "CREATE"
PATCH = "PATCH"
FINAL = "FINAL"
SKIP = "SKIP"
ACTIONS = (CREATE, PATCH, FINAL, SKIP)

#: Why. One per answer, because «SKIP» alone cannot be argued with.
NEW_PERIOD = "NEW_PERIOD"
SAME_PERIOD = "SAME_PERIOD"
PERIOD_ENDED = "PERIOD_ENDED"
ALREADY_FROZEN = "ALREADY_FROZEN"
FINAL_UNCONFIRMED = "FINAL_UNCONFIRMED"
CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
OUT_OF_ORDER = "OUT_OF_ORDER"
REASONS = (NEW_PERIOD, SAME_PERIOD, PERIOD_ENDED, ALREADY_FROZEN,
           FINAL_UNCONFIRMED, CALENDAR_UNKNOWN, OUT_OF_ORDER)

#: The three phases an open period can be in, and WHY THE FIRST TWO ARE NOT THE
#: THIRD. `FINAL` prepares an archive and asks the transport to PATCH it; it does
#: NOT make the card frozen, because this module cannot know the far end accepted
#: it. Writing «frozen» on the strength of having *attempted* the patch is the
#: failure the 2026-10-14 review names: a 429/timeout leaves the card unfrozen at
#: the far end, the next tick reads ALREADY_FROZEN and refuses to retry, and the
#: boundary opens a successor over an archive that never landed.
#:
#:     LIVE           the period is running; ticks PATCH it silently
#:     FINAL_PENDING  the final archive is prepared and its PATCH was attempted,
#:                    but no verified acknowledgement has arrived; a later tick
#:                    RETRIES the same message_id and never opens a successor
#:     FROZEN         a verified acknowledgement (a DELIVERED `card_delivery`
#:                    receipt for this version) was recorded; only now may the
#:                    next period CREATE its own message
LIVE = "LIVE"
FINAL_PENDING = "FINAL_PENDING"
FROZEN = "FROZEN"
PHASES = (LIVE, FINAL_PENDING, FROZEN)

#: The day kinds `business_calendar` answers with, and which period table each
#: one reads. UNKNOWN is deliberately absent: a day nobody can name gets no
#: period, because a period_ref built from it would be the dedupe key of every
#: unnamed day at once.
WORKDAY = "WORKDAY"
NON_WORKING_DAY = "NON_WORKING_DAY"

#: The table key each day kind reads. The KIND is `business_calendar`'s token
#: (uppercase, because that is the value `day()` returns and `day_ref`'s kind
#: travels with); the KEY is the declaration's own spelling, lowercase the way
#: `business_calendar`'s declaration block writes `workdays`. They are kept
#: apart so a declaration never has to know a token it does not render.
DAY_TABLE_KEYS = {WORKDAY: "workday", NON_WORKING_DAY: "non_working_day"}

#: The default period table. A DECLARATION the caller may replace wholesale —
#: never a derivation this module performs. Times are the calendar's own zone
#: (`business_calendar.day` already resolved the instant there).
DEFAULT_TABLE = {
    "tick_seconds": 600,
    "workday": (
        {"name": "morning", "label": "上午", "start": "09:00", "end": "12:00"},
        {"name": "afternoon", "label": "下午", "start": "12:00", "end": "18:00"},
        # The night period WRAPS: it ends at 09:00 on the NEXT calendar day.
        {"name": "night", "label": "夜间", "start": "18:00", "end": "09:00"},
    ),
    "non_working_day": (
        {"name": "all_day", "label": "全天", "start": "00:00", "end": "00:00"},
    ),
}

#: The row's whole vocabulary, borrowed from `card_day` where it is the same
#: question. `tick` is this period's ordinal and not the workday's, so the two
#: documents cannot be confused by their rows either.
ROW_FIELDS = ("tick", "hhmm", "local", "verdict", "observation", "sections",
              "gaps")


class CardPeriodError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# the modules this one composes
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


calendar = _load("serveros_runtime_business_calendar",
                 _HERE / "reports" / "business_calendar.py")
delivery = _load("serveros_runtime_card_delivery", _HERE / "card_delivery.py")
day = _load("serveros_runtime_card_day", _HERE / "card_day.py")
monitor = _load("serveros_runtime_fleet_monitor_card",
                _HERE / "fleet_monitor_card.py")


def _registry():
    return _load("serveros_runtime_registry", _HERE / "registry.py")


def _store_io():
    # The same atomic write every durable document in this tree uses.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


# ---------------------------------------------------------------------------
# the period table
# ---------------------------------------------------------------------------

def _minutes(hhmm) -> int | None:
    """`"HH:MM"` as minutes past local midnight, or None if it is not one.

    A table entry that cannot be read is refused rather than defaulted: a
    silent 00:00 would invent a period boundary nobody declared, and every
    period_ref after it would be filed under the wrong window.
    """
    text = str(hhmm or "").strip()
    if len(text) != 5 or text[2] != ":":
        return None
    try:
        hour, minute = int(text[:2]), int(text[3:])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def table(declaration=None) -> dict:
    """The period table, with the declaration's entries replacing the defaults.

    `declaration` is an optional `{"tick_seconds": int, "workday": [...],
    "non_working_day": [...]}` block. Each LIST replaces its default whole
    rather than merging entry by entry: a table half from the file and half from
    here is a table neither source states, and a reader comparing the two would
    find a period neither declared.
    """
    out = {"tick_seconds": int(DEFAULT_TABLE["tick_seconds"])}
    for key in DAY_TABLE_KEYS.values():
        out[key] = [dict(p) for p in DEFAULT_TABLE[key]]
    if not isinstance(declaration, dict):
        return out
    if declaration.get("tick_seconds") is not None:
        seconds = declaration["tick_seconds"]
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
            raise CardPeriodError(
                f"tick_seconds 必须是正整数秒，而不是 {seconds!r}："
                f"它决定同一时段内多久 patch 一次同一张卡")
        out["tick_seconds"] = seconds
    for key in DAY_TABLE_KEYS.values():
        if key in declaration and declaration[key] is not None:
            entries = declaration[key]
            if not isinstance(entries, list) or not entries:
                raise CardPeriodError(
                    f"period table 的 {key} 必须是非空列表，而不是 "
                    f"{type(entries).__name__}")
            for entry in entries:
                if not isinstance(entry, dict) or not entry.get("name"):
                    raise CardPeriodError(
                        f"{key} 的每个时段都要有 `name`，这一个没有："
                        f"name 是 period_ref 的一部分，也就是去重键")
                if _minutes(entry.get("start")) is None \
                        or _minutes(entry.get("end")) is None:
                    raise CardPeriodError(
                        f"{key} 的时段 {entry.get('name')!r} 的 start/end 读不出来"
                        f"（{entry.get('start')!r} → {entry.get('end')!r}）："
                        f"需要 \"HH:MM\"")
            out[key] = [dict(e) for e in entries]
    return out


def _period_ref(day_ref: str, name: str) -> str:
    """The identity of one period, and the dedupe key of its one message."""
    return f"{day_ref}#{name}"


def _contains(entry: dict, minute: int, *, tail: bool = False) -> tuple[bool, int] | None:
    """Whether `minute` falls in `entry`, and how far into it (minutes).

    `start == end` is a FULL day, not an empty one — that is how the all-day
    period is spelled, and reading it as empty would leave a holiday with no
    period at all and a card nobody opens. A start AFTER the end is a wrap
    across midnight, which the night period uses.

    `tail` says which side of a wrapping period to look at. A wrapping period
    belongs to the day it OPENED on, so its early-morning half is the PREVIOUS
    day's (tail=True) and its late half is its own (tail=False). Without this a
    02:00 instant would match today's own 18:00→09:00 period and be filed under
    the wrong day_ref — a second card at every midnight.
    """
    start, end = _minutes(entry.get("start")), _minutes(entry.get("end"))
    if start is None or end is None:
        return None
    if start == end:
        return True, minute
    if start < end:
        if start <= minute < end:
            return True, minute - start
        return False, 0
    # wraps midnight
    if tail:
        if minute < end:
            return True, (1440 - start) + minute
        return False, 0
    if minute >= start:
        return True, minute - start
    return False, 0


def period_at(instant, *, declaration=None, table_: dict | None = None) -> dict:
    """Which period `instant` is in, and under which day's identity.

    The answer is derived from `business_calendar.day`, so the day this period
    belongs to is the calendar's answer and not a second derivation here. A day
    whose kind is UNKNOWN gets NO period: naming one would need a day_ref that
    does not exist, and a period_ref built from `None` would dedupe every
    unnamed day into one card.

    A wrapping period (the default 夜间, 18:00→09:00) means an instant before the
    day's first start belongs to the PREVIOUS day's last period — so the
    previous day is resolved too, and `day_ref` on the answer is that previous
    day, not today's. That is the whole reason a period_ref names its OWN day.
    """
    tbl = table_ or table(declaration)
    facts = calendar.day(instant, declaration=declaration)
    base = {
        "schema": SCHEMA,
        "at": instant if isinstance(instant, str) else None,
        "day_ref": facts.get("day_ref"),
        "day_kind": facts.get("kind"),
        "timezone": facts.get("timezone"),
        "local": facts.get("local"),
        "period_ref": None,
        "name": None,
        "label": None,
        "start": None,
        "end": None,
        "tick": None,
        "tick_seconds": tbl["tick_seconds"],
        "in_period": False,
        "reason": None,
        "says": "",
    }
    local = facts.get("local")
    if facts.get("kind") == "UNKNOWN" or not local:
        base["reason"] = CALENDAR_UNKNOWN
        base["says"] = (
            f"calendar=UNKNOWN（{facts.get('says')}）：不命名时段，也不据此新建"
            f"或 patch 时段卡；采样与记录照常")
        return base

    moment = datetime.datetime.fromisoformat(local)
    minute = moment.hour * 60 + moment.minute

    answer = _locate(facts["kind"], facts["day_ref"], minute, tbl, local=local)
    if answer is not None:
        base.update(answer)
        return base

    # Not inside today's own periods: it is the tail of the previous day's last
    # period. Resolve the previous day and look for a period that reaches us.
    prev_instant = moment - datetime.timedelta(days=1)
    prev = calendar.day(prev_instant.isoformat(), declaration=declaration)
    if prev.get("kind") in (WORKDAY, NON_WORKING_DAY) and prev.get("day_ref"):
        for entry in tbl.get(DAY_TABLE_KEYS.get(prev["kind"], ""), []):
            hit = _contains(entry, minute, tail=True)
            if hit and hit[0]:
                base.update(_describe(prev["kind"], prev["day_ref"], entry,
                                      hit[1], tbl, local=local))
                return base
        if prev["kind"] == WORKDAY:
            base["reason"] = CALENDAR_UNKNOWN
            base["says"] = (
                f"{prev['day_ref']} 是工作日，但它最后一个时段没有覆盖 "
                f"{moment.strftime('%H:%M')}：时段表有缺口，不据此建卡")
            return base
    base["reason"] = CALENDAR_UNKNOWN
    base["says"] = (
        f"{moment.strftime('%H:%M')} 既不落在当天的任何时段里，前一天的最后一个"
        f"时段也接不到它：时段表有缺口，不据此建卡")
    return base


def _locate(kind: str, day_ref: str, minute: int, tbl: dict, *,
            local: str) -> dict | None:
    for entry in tbl.get(DAY_TABLE_KEYS.get(kind, ""), []):
        hit = _contains(entry, minute)
        if hit and hit[0]:
            return _describe(kind, day_ref, entry, hit[1], tbl, local=local)
    return None


def _describe(kind: str, day_ref: str, entry: dict, into: int, tbl: dict, *,
              local: str) -> dict:
    # `into` is minutes past the period's start; `tick_seconds` is seconds, so
    # the ordinal multiplies back to seconds before dividing. Dividing minutes
    # by seconds would make every tick inside the first ten minutes «1» and the
    # cadence a lie.
    tick = (into * 60) // int(tbl["tick_seconds"]) + 1
    name = entry["name"]
    return {
        "period_ref": _period_ref(day_ref, name),
        "day_ref": day_ref,
        "day_kind": kind,
        "name": name,
        "label": entry.get("label") or name,
        "start": entry.get("start"),
        "end": entry.get("end"),
        "tick": tick,
        "tick_seconds": tbl["tick_seconds"],
        "local": local,
        "in_period": True,
        "reason": None,
        "says": (f"{day_ref} · {entry.get('label') or name} 时段"
                 f"（{entry.get('start')}–{entry.get('end')}）· "
                 f"第 {tick} 拍"),
    }


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------

def empty() -> dict:
    return {"schema": SCHEMA, "open": None, "previous": None}


def _phase(open_) -> str:
    """Which phase an open period is in.

    A document written before `phase` existed carries `frozen` and no `phase`;
    it is read as FROZEN — the strongest reading, and the one its own `frozen`
    block already claims. Reading a frozen card as LIVE would let the next tick
    PATCH a card somebody has already archived.
    """
    if not isinstance(open_, dict):
        return LIVE
    phase = open_.get("phase")
    if phase in PHASES:
        return phase
    return FROZEN if open_.get("frozen") else LIVE


def _attempts(open_) -> int:
    pending = open_.get("pending") if isinstance(open_, dict) else None
    return int(pending.get("attempts") or 0) if isinstance(pending, dict) else 0


def _is_later(at, then) -> bool:
    """Whether `at` is strictly later than `then`, both ISO instants.

    Unreadable on either side is `False`, and that is the safe direction: the
    alternative treats an instant nobody could place on a timeline as a boundary
    and archives a shift on it.
    """
    try:
        now = datetime.datetime.fromisoformat(str(at))
        was = datetime.datetime.fromisoformat(str(then))
    except (TypeError, ValueError):
        return False
    if now.tzinfo is None or was.tzinfo is None:
        return False
    return now > was


def decide(document: dict, *, period: dict, at: str,
           closing: str | None = None) -> dict:
    """What happens to the period card at this instant, and why.

    The answer is a LIST of STEPS because a shift change is two operations and
    pretending it is one is how a card multiplies: the period that just ended
    must have its archive patched in and the period that just began must be
    CREATEd. But the two happen at DIFFERENT INSTANTS now, because the freeze is
    no longer this function's to make — `FINAL` only PREPARES, and a successor is
    opened only after a verified acknowledgement (`PeriodStore.confirm`).

    A PENDING FINAL IS THE ONLY THING THIS INSTANT MAY DO. While an archive is
    prepared but unconfirmed, EVERY tick retries the SAME message: the same
    period's later tick, and the first tick of the NEXT period, both answer
    FINAL. It never PATCHes live again and it never opens a successor — a
    successor opened over an archive that has not landed is the second card this
    module exists to forbid.

    `closing` is how a caller ends a shift EXPLICITLY — «收班 / 结算» — instead
    of waiting for the next period's first tick to detect the boundary.

    DIRECTION IS CHECKED, NOT ASSUMED. An instant in a period DIFFERENT from the
    open one is a boundary only when it is LATER than the open period began
    (`_is_later`). An instant that is EARLIER is a clock that stepped back or a
    replay, and closing the open period on it would archive a shift that has not
    ended — so it is refused.

    Nothing here reads a clock or writes anything: `at`, `period` and `closing`
    are arguments, and the document is returned unchanged.
    """
    document = document or empty()
    open_ = document.get("open")
    open_ref = open_.get("period_ref") if isinstance(open_, dict) else None
    phase = _phase(open_)
    cur = period if (isinstance(period, dict) and period.get("in_period")) else None
    cur_ref = cur.get("period_ref") if cur else None

    # 0. An unconfirmed final blocks everything else. It is checked FIRST and
    #    returns on its own, so no branch below can open a successor beside it.
    if open_ref and phase == FINAL_PENDING:
        return _answer(open_, FINAL, FINAL_UNCONFIRMED, at,
                       (f"{open_ref} 的最终归档已准备好，但还没有被远端确认"
                        f"（已尝试 {_attempts(open_)} 次）：本次只重试同一条 "
                        f"message_id，不新建消息，也不开新时段"),
                       period_ref=open_ref,
                       steps=[_final_step(open_, open_ref, retry=True)])

    steps: list[dict] = []

    # 1. Prepare the final when the period is ending. Only a LIVE period has
    #    anything to end; a FROZEN one is already archived.
    if open_ref and phase == LIVE:
        if closing and closing == open_ref:
            steps.append(_final_step(open_, open_ref))
        elif cur is not None and cur_ref != open_ref:
            if _is_later(at, open_.get("opened_at")):
                steps.append(_final_step(open_, open_ref))
            else:
                return _answer(open_, SKIP, OUT_OF_ORDER, at,
                               (f"这一拍属于 {cur_ref}，早于当前打开的 {open_ref}"
                                f"（开始于 {open_.get('opened_at')}）：不据此动"
                                f"卡片，避免把时钟倒退读成换班"))

    # A FINAL prepared this instant is the WHOLE answer. The successor waits for
    # the acknowledgement, so no branch below may add a CREATE beside it — and
    # DISCARDING the step here is what would make a boundary look like a no-op
    # while the successor guard below silently swallowed it.
    if steps:
        return _answer(open_, FINAL, PERIOD_ENDED, at, _final_says(open_ref),
                       period_ref=open_ref, steps=steps)

    if not cur:
        return _answer(open_, SKIP,
                       (period or {}).get("reason") or CALENDAR_UNKNOWN, at,
                       (period or {}).get("says")
                       or "没有可用的时段身份，不动时段卡")

    ref = cur_ref

    if ref != open_ref or not open_ref:
        # A successor may open ONLY on a FROZEN predecessor. A FINAL_PENDING one
        # already returned at step 0; this guard covers a document whose phase
        # reached here by another route.
        if open_ref and phase != FROZEN:
            return _answer(open_, SKIP, FINAL_UNCONFIRMED, at,
                           (f"{open_ref} 的归档尚未确认冻结：不在此新建下一时段，"
                            f"避免在一份还没落地的归档之上再开一张卡"),
                           period_ref=open_ref)
        says = (f"{cur['day_ref']} · {cur['label']} 时段开始"
                + ("（上一时段已归档并冻结）" if steps else "（今天还没有时段卡）")
                + "：新建一条消息承载它，之后每一拍都 patch 这一条；"
                  "这一条初建时可能是一次可见消息")
        steps.append({"action": CREATE, "period_ref": ref, "period": cur,
                      "reason": NEW_PERIOD, "says": says})
        return _answer(open_, CREATE, NEW_PERIOD, at, says, period_ref=ref,
                       steps=steps)

    if phase == FROZEN:
        frozen_at = (open_.get("frozen") or {}).get("at")
        return _answer(open_, SKIP, ALREADY_FROZEN, at,
                       (f"{ref} 已冻结（{frozen_at}）：不再因普通采样变化，"
                        f"本次不动它"), period_ref=ref)

    latest = open_.get("latest")
    if isinstance(latest, dict) and isinstance(latest.get("tick"), int) \
            and cur["tick"] < latest["tick"]:
        return _answer(open_, SKIP, OUT_OF_ORDER, at,
                       (f"这一拍是 {ref} 的第 {cur['tick']} 拍，而卡片已经在第 "
                        f"{latest['tick']} 拍上：往回的一拍不写，避免时段内出现"
                        f"倒序"), period_ref=ref)

    # A same-tick replay is a PATCH and not a SKIP: the patch lands on the SAME
    # message, so it cannot become a second card, and refusing it would make a
    # timer that fires twice in one slot leave the card a slot behind.
    says = (f"{ref} 已在（最后更新 {open_.get('updated_at')}，本拍是第 "
            f"{cur['tick']} 拍）：静默 patch 这一条，不新增聊天记录")
    steps.append({"action": PATCH, "period_ref": ref, "period": cur,
                  "reason": SAME_PERIOD, "says": says})
    return _answer(open_, PATCH, SAME_PERIOD, at, says, period_ref=ref,
                   steps=steps)


def _final_step(open_: dict, open_ref: str, *, retry: bool = False) -> dict:
    return {
        "action": FINAL, "period_ref": open_ref,
        "period": {"period_ref": open_ref, "label": open_.get("label"),
                   "day_ref": open_.get("day_ref")},
        "reason": FINAL_UNCONFIRMED if retry else PERIOD_ENDED,
        "retry": bool(retry),
        "says": _final_says(open_ref),
    }


def _final_says(open_ref) -> str:
    return (f"{open_ref} 时段已结束：本次对这同一条消息做最后一次 patch"
            f"（总览概括 + 完整时段趋势 + 三设备摘要 + 事故/自愈/未解决交接），"
            f"随后冻结，不再因普通采样变化")


def _answer(open_, action, reason, at, says, *, period_ref=None, steps=None):
    return {"schema": SCHEMA, "at": at, "steps": list(steps or []),
            "action": action, "reason": reason, "period_ref": period_ref,
            "message": _message(open_, action, at), "says": says}


def _message(open_period, action: str, at: str) -> dict:
    return message_verdict(action, open_period, at=at)


def message_verdict(action: str, open_period, *, at: str) -> dict:
    """What happens to the MESSAGE carrying the period card, and why.

    The companion of the card decision, never a substitute for it. `PATCH` is
    answered «patch it» only when a message identity is genuinely in hand AND
    still inside the service's window — and WHICH of those holds is not decided
    here: `card_delivery.binding_verdict` answers it once for the day's card,
    the incident card and this one, and this function asks and then says it in
    the period's own words. A second reading of the update window would be a
    second chance to read it wrongly, and the wrong one is the one promising a
    patch the transport refuses.
    """
    window = delivery.update_window_days()
    verdict = {"action": None, "reason": None, "remote_id": None,
               "window_days": window, "says": ""}

    if action == SKIP:
        verdict.update({
            "action": day.NO_MESSAGE, "reason": day.NOT_ROUTINE,
            "says": "这一拍不动时段卡，也就没有要发的消息"})
        return verdict

    if action == CREATE:
        verdict.update({
            "action": delivery.NEW_MESSAGE, "reason": delivery.NEVER_SENT,
            "says": ("本时段还没有承载它的消息：本次新发一条（可能是一次可见"
                     "消息），从下一拍起都是静默 patch 这一条")})
        return verdict

    if action == FINAL:
        # The final patch is still a patch of the SAME message; the archive is
        # a new BODY, not a second message.
        binding = open_period.get("message") if isinstance(open_period, dict) else None
        judged = delivery.binding_verdict(binding, at=at, window_days=window)
        if judged["reason"] == delivery.IN_HAND:
            verdict.update({
                "action": delivery.PATCH_MESSAGE, "reason": delivery.IN_HAND,
                "remote_id": judged["remote_id"],
                "says": (f"归档写进同一条消息（在手，{window} 天可改窗口内）："
                         f"最后一次 patch 这一条，然后冻结")})
            return verdict
        verdict.update({
            "action": delivery.NEW_MESSAGE, "reason": judged["reason"],
            "remote_id": judged["remote_id"],
            "says": (f"归档想写回承载时段卡的那条消息，但它读不出来或已超出 "
                     f"{window} 天可改窗口（{judged['reason']}）：这一份文档"
                     f"如实说不改发第二条——那会变成第二张卡")})
        return verdict

    judged = delivery.binding_verdict(
        open_period.get("message") if isinstance(open_period, dict) else None,
        at=at, window_days=window)
    remote_id = judged["remote_id"]
    if judged["reason"] == delivery.IDENTITY_MISSING:
        verdict.update({
            "action": delivery.NEW_MESSAGE,
            "reason": delivery.IDENTITY_MISSING, "remote_id": remote_id,
            "says": ("时段卡已在，但没有记下承载它的那条消息，因此无从 patch。"
                     "这件事只有投递台账知道（`card deliveries`）；"
                     "发出后用 `card period-announce --message-id` 记下即可")})
        return verdict
    if judged["reason"] == delivery.IDENTITY_UNDATED:
        verdict.update({
            "action": delivery.NEW_MESSAGE,
            "reason": delivery.IDENTITY_UNDATED, "remote_id": remote_id,
            "says": (f"记下了承载时段卡的消息，但它的发出时间（或这一刻）读不出来，"
                     f"{window} 天可改窗口因此无法判断；不声称可以 patch")})
        return verdict
    if judged["reason"] == delivery.IDENTITY_EXPIRED:
        verdict.update({
            "action": delivery.NEW_MESSAGE,
            "reason": delivery.IDENTITY_EXPIRED, "remote_id": remote_id,
            "says": (f"承载时段卡的消息已发出约 {int(judged['age_days'])} 天，"
                     f"超过 {window} 天可改窗口，改不动了；只能新发一条，"
                     f"而那意味着同一时段的第二张卡")})
        return verdict
    if judged["reason"] == delivery.IN_HAND:
        age_days = judged["age_days"]
        since = (f"发出约 {age_days:.1f} 天" if age_days > 0
                 else "记下的发出时间不早于这一拍")
        verdict.update({
            "action": delivery.PATCH_MESSAGE, "reason": delivery.IN_HAND,
            "remote_id": remote_id,
            "says": (f"承载时段卡的消息在手（{since}，在 {window} 天可改窗口内）："
                     f"静默 patch 这一条，不新发")})
        return verdict
    raise CardPeriodError(
        f"`binding_verdict` answered {judged['reason']!r}, which is not one of "
        f"the reasons it declares; this verdict has no sentence for it and will "
        f"not fall through to a patch")


# ---------------------------------------------------------------------------
# the row
# ---------------------------------------------------------------------------

def row(model: dict, *, period: dict) -> dict:
    """One tick of the period, as the card will show it.

    Extracted by name, never copied: whatever the model grows cannot arrive
    here, because there is no field for it. §11's «不复制 PID/code/raw probe» is
    a property of this function rather than a rule its callers remember.
    """
    if not isinstance(period, dict) or not period.get("period_ref") \
            or not isinstance(period.get("tick"), int):
        raise CardPeriodError(
            f"a row needs the period it belongs to: period_ref="
            f"{period.get('period_ref') if isinstance(period, dict) else None!r}, "
            f"tick={period.get('tick') if isinstance(period, dict) else None!r}")
    health = model.get("env_health") or {}
    sections = model.get("sections") or {}
    candidate = {
        "tick": period["tick"],
        "hhmm": _hhmm(period),
        "local": period.get("local") or None,
        "verdict": health.get("verdict"),
        "observation": health.get("observation"),
        "sections": {name: (sections.get(name) or {}).get("state")
                     for name in day.ROW_SECTIONS},
        "gaps": len(health.get("gaps") or []),
    }
    return check_row(candidate)


def _hhmm(period: dict) -> str | None:
    local = period.get("local")
    if not local:
        return None
    moment = datetime.datetime.fromisoformat(local)
    return moment.strftime("%H:%M")


def check_row(candidate: dict) -> dict:
    """The row's schema, enforced on documents as well as on callers."""
    if not isinstance(candidate, dict):
        raise CardPeriodError(f"a row is a JSON object, not {type(candidate).__name__}")
    extra = [k for k in candidate if k not in ROW_FIELDS]
    if extra:
        raise CardPeriodError(
            f"a period row carries {', '.join(sorted(extra))} — a row keeps "
            f"{', '.join(ROW_FIELDS)} and nothing else. Machine detail belongs "
            f"in the observation, which the card links to and does not copy")
    missing = [k for k in ROW_FIELDS if k not in candidate]
    if missing:
        raise CardPeriodError(
            f"a period row is missing {', '.join(missing)}; an absent value and "
            f"a value nobody recorded would read the same without it")
    sections = candidate.get("sections")
    if not isinstance(sections, dict):
        raise CardPeriodError("a row's `sections` is an object of state words")
    strays = [k for k in sections if k not in day.ROW_SECTIONS]
    if strays:
        raise CardPeriodError(
            f"a row's `sections` names {', '.join(sorted(strays))}, which is not "
            f"one of {', '.join(day.ROW_SECTIONS)}")
    gaps = candidate.get("gaps")
    if not isinstance(gaps, int) or isinstance(gaps, bool) or gaps < 0:
        raise CardPeriodError(
            f"a row's `gaps` is the count of collection gaps, not {gaps!r}")
    if not isinstance(candidate.get("tick"), int) or isinstance(candidate["tick"], bool):
        raise CardPeriodError(f"a row's `tick` is an ordinal, not {candidate.get('tick')!r}")
    verdict = candidate.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        raise CardPeriodError(
            f"a row leads with the card's verdict and this one has {verdict!r}")
    return dict(candidate)


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------

def apply(document: dict, *, step: dict, row_: dict | None = None,
          at: str | None = None, frozen: dict | None = None,
          archive: dict | None = None) -> dict:
    """The document this step produces. Pure: the caller writes it."""
    act = (step or {}).get("action")
    stamp = at or (step or {}).get("at")
    if act not in ACTIONS:
        raise CardPeriodError(
            f"{act!r} is not one of {', '.join(ACTIONS)} — an action nobody can "
            f"name is an action somebody will read as «no change»")
    next_doc = dict(document or empty())
    open_ = next_doc.get("open")

    if act == SKIP:
        if row_ is not None:
            raise CardPeriodError(
                "a SKIP carries no row. This instant was decided NOT to update "
                "the card, and recording what it would have said is how a "
                "period's rows grow rows it never reported")
        return next_doc

    if not stamp:
        raise CardPeriodError("an update needs the instant it happened")

    if act == CREATE:
        if row_ is None:
            raise CardPeriodError("a creation needs the row it opens with")
        if not step.get("period_ref"):
            raise CardPeriodError("a creation needs the period it belongs to")
        if isinstance(open_, dict) and open_.get("period_ref") == step["period_ref"]:
            raise CardPeriodError(
                f"{step['period_ref']} already has a card. Two creations in one "
                f"period is the second chat message this module exists to forbid")
        if isinstance(open_, dict) and _phase(open_) != FROZEN:
            # The load-bearing guard the 2026-10-14 review asked for: a successor
            # must not open over a predecessor whose archive has not been
            # confirmed at the far end. Opening one there is a second card.
            raise CardPeriodError(
                f"the open period {open_.get('period_ref')} is "
                f"{_phase(open_)!r}, not {FROZEN!r}: a successor must not open "
                f"before the predecessor's archive is confirmed at the far end. "
                f"Record the acknowledgement (`confirm`) — or leave the final "
                f"pending and retry the same message — instead of opening one")
        if isinstance(open_, dict):
            # The period just closed becomes `previous`, and it carries its
            # freeze with it — a reader asking «what did the shift hand over»
            # needs the closed card, not only the open one.
            next_doc["previous"] = {
                "period_ref": open_.get("period_ref"),
                "label": open_.get("label"),
                "day_ref": open_.get("day_ref"),
                "opened_at": open_.get("opened_at"),
                "closed_at": open_.get("frozen", {}).get("at") if
                isinstance(open_.get("frozen"), dict) else stamp,
                "ticks": len(open_.get("rows") or []) + (1 if open_.get("latest") else 0),
                "frozen": open_.get("frozen"),
                "archive": open_.get("archive"),
            }
        period = step.get("period") or {}
        next_doc["open"] = {
            "period_ref": step["period_ref"],
            "label": period.get("label"),
            "day_ref": period.get("day_ref"),
            "day_kind": period.get("day_kind"),
            "start": period.get("start"),
            "end": period.get("end"),
            "tick_seconds": period.get("tick_seconds"),
            "opened_at": stamp,
            "updated_at": stamp,
            "latest": check_row(row_),
            "rows": [],
            "message": None,
            "phase": LIVE,
            "pending": None,
            "frozen": None,
            "confirmed": None,
            "archive": None,
        }
        return next_doc

    if not isinstance(open_, dict) or not open_.get("period_ref"):
        raise CardPeriodError(
            f"there is no open card to {act}. A {act} without a card would have "
            f"to invent one, and every field of it would be invented with it")
    if open_.get("period_ref") != step.get("period_ref"):
        raise CardPeriodError(
            f"the open card belongs to {open_.get('period_ref')} and this {act} "
            f"is for {step.get('period_ref')}: acting on one period's card with "
            f"another period's tick is how two shifts become one card")

    if act == PATCH:
        if row_ is None:
            raise CardPeriodError("a patch needs the row it is about")
        latest = open_.get("latest")
        rows = [check_row(r) for r in (open_.get("rows") or [])]
        if isinstance(latest, dict) and latest.get("tick") == row_["tick"]:
            # Same tick, second look: the slot's row is REPLACED, so a period
            # keeps at most one row per slot.
            next_open = dict(open_)
            next_open.update({"latest": check_row(row_), "updated_at": stamp,
                              "rows": rows})
            next_doc["open"] = next_open
            return next_doc
        if isinstance(latest, dict):
            rows = (rows + [check_row(latest)])[-_row_ceiling(open_):]
        next_open = dict(open_)
        next_open.update({"latest": check_row(row_), "updated_at": stamp,
                          "rows": rows})
        next_doc["open"] = next_open
        return next_doc

    # FINAL — PREPARE. It does NOT freeze: freezing is `confirm`'s, and
    # `confirm` runs only on a verified remote acknowledgement. Writing `frozen`
    # here would be the failure the review names — a 429/timeout leaves the card
    # unfrozen at the far end while this document says it was archived.
    if _phase(open_) == FROZEN:
        raise CardPeriodError(
            f"{open_.get('period_ref')} is already frozen at "
            f"{(open_.get('frozen') or {}).get('at')}: a second FINAL is a second "
            f"archive of a shift that has already been handed over")
    previous_pending = (open_.get("pending")
                        if isinstance(open_.get("pending"), dict) else {})
    prepared = frozen or previous_pending.get("frozen") or {
        "kind": delivery.PERIOD,
        "at": stamp,
        "version": None,
        "payload_hash": None,
    }
    held_id = previous_pending.get("remote_id")
    if not held_id and isinstance(open_.get("message"), dict):
        held_id = open_["message"].get("remote_id")
    chosen_archive = archive if archive is not None else step.get("archive")
    next_open = dict(open_)
    next_open["phase"] = FINAL_PENDING
    next_open["updated_at"] = stamp
    next_open["archive"] = (chosen_archive if chosen_archive is not None
                            else open_.get("archive"))
    # The confirmed block stays None until `confirm`: having PREPARED an archive
    # is not having delivered one.
    next_open["frozen"] = None
    next_open["pending"] = {
        "prepared_at": previous_pending.get("prepared_at") or stamp,
        "frozen": prepared,
        "remote_id": held_id,
        "attempts": int(previous_pending.get("attempts") or 0) + 1,
        "last_attempt_at": stamp,
    }
    next_doc["open"] = next_open
    return next_doc


def frozen_block(*, card: dict, at: str, period_ref: str) -> dict:
    """The `frozen` block a period card's FINAL receipt names.

    The freeze IS `card_delivery.freeze_card`'s, under the `period` kind: the
    version and the payload hash are that module's, so a receipt filed for a
    period card has the same shape as one for the day's card and is checked by
    the same `card_delivery.verify`. A second freeze written here would be a
    second place «which version went out» is decided, which is the thing the
    ledger exists to answer once.
    """
    return delivery.freeze_card(delivery.PERIOD, card, at=at,
                                idempotency_key=f"card-period-{period_ref}-{at}")


def _row_ceiling(open_: dict) -> int:
    """How many rows a period keeps.

    Bounded rather than unbounded, for the reason the ledger is: this document
    is read on a path that runs every ten minutes forever, and a list that only
    grows is a document that eventually cannot be written atomically. The
    ceiling is derived from the window it has to hold — a full day of ten-minute
    ticks — so a shorter tick does not silently evict the morning.
    """
    seconds = open_.get("tick_seconds") or DEFAULT_TABLE["tick_seconds"]
    return max(1, (24 * 3600) // int(seconds))


def summary(document: dict | None) -> dict:
    """What a card renders, and what a command prints."""
    document = document or empty()
    open_ = document.get("open")
    if not isinstance(open_, dict):
        return {"schema": SCHEMA, "period_ref": None, "ticks": 0, "latest": None,
                "rows": [], "timeline": [], "message": None, "phase": None,
                "pending": None, "confirmed": None, "frozen": None,
                "previous": document.get("previous"), "archive": None,
                "says": "还没有任何时段卡"}
    rows = [check_row(r) for r in (open_.get("rows") or [])]
    latest = open_.get("latest")
    all_rows = rows + ([latest] if latest else [])
    entries = day.timeline(all_rows)
    frozen = open_.get("frozen")
    phase = _phase(open_)
    pending = open_.get("pending") if isinstance(open_.get("pending"), dict) else None
    if phase == FROZEN:
        state_word = "（已冻结·远端已确认）"
    elif phase == FINAL_PENDING:
        state_word = f"（最终归档待确认·已尝试 {_attempts(open_)} 次）"
    else:
        state_word = "（进行中）"
    return {
        "schema": SCHEMA,
        "period_ref": open_.get("period_ref"),
        "label": open_.get("label"),
        "day_ref": open_.get("day_ref"),
        "kind": open_.get("day_kind"),
        "tick_seconds": open_.get("tick_seconds"),
        "ticks": len(all_rows),
        "opened_at": open_.get("opened_at"),
        "updated_at": open_.get("updated_at"),
        "latest": latest,
        "rows": rows,
        "timeline": entries,
        "transitions": sum(1 for e in entries if e["kind"] == "transition"),
        "unobserved": sum(1 for e in entries if e["kind"] == "gap"),
        "message": open_.get("message"),
        # The phase is the load-bearing field: FROZEN means a verified remote
        # acknowledgement was recorded, and nothing else does. `frozen` is set
        # only in that phase, so a reader that only looks at `frozen` cannot be
        # misled into reading a prepared-but-unconfirmed archive as delivered.
        "phase": phase,
        "pending": pending,
        "confirmed": open_.get("confirmed"),
        "frozen": frozen,
        "frozen_at": frozen.get("at") if isinstance(frozen, dict) else None,
        "archive": open_.get("archive"),
        "previous": document.get("previous"),
        "says": (f"{open_.get('period_ref')} 已记录 {len(all_rows)} 拍" + state_word),
    }


# ---------------------------------------------------------------------------
# the document on disk
# ---------------------------------------------------------------------------

def period_path(state_path) -> Path:
    """Where the period card's memory sits, given a state directory."""
    return Path(state_path).parent.parent / "cards" / "period.json"


def default_path() -> Path:
    """The SAME computation as `period_path`, over the ledger's own anchor."""
    return period_path(_registry().state_root() / "runtime" / "alerts" / "state.json")


class PeriodStore:
    """The period card, and the ticks it folded. One document, one atomic write.

    An unreadable store is REFUSED rather than replaced: a fresh document would
    make the same period get a second NEW message, which is the one thing a
    «one message per period» rule forbids by name.
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
            raise CardPeriodError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start from an empty period: the same "
                f"period would get a second NEW message, which is exactly what "
                f"«一时段一条消息» forbids — losing the memory is not the harm, "
                f"re-creating the card is.") from exc
        if not isinstance(data, dict):
            raise CardPeriodError(f"{self.path} is not a JSON object")
        open_ = data.get("open")
        if open_ is not None and not isinstance(open_, dict):
            raise CardPeriodError(
                f"{self.path} has an `open` that is {type(open_).__name__}; "
                f"this document holds at most one open period")
        if isinstance(open_, dict):
            if not open_.get("period_ref"):
                raise CardPeriodError(
                    f"the open period in {self.path} has no `period_ref`: the "
                    f"identity is the dedupe key")
            phase = open_.get("phase")
            if phase is not None and phase not in PHASES:
                raise CardPeriodError(
                    f"the open period in {self.path} is in phase {phase!r}, which "
                    f"is not one of {', '.join(PHASES)}. A phase nobody can name "
                    f"is a phase this store would have to guess about, and the "
                    f"guess decides whether a frozen card is patched again")
            for item in open_.get("rows") or []:
                check_row(item)
            if open_.get("latest") is not None:
                check_row(open_["latest"])
            message = open_.get("message")
            if message is not None and (not isinstance(message, dict)
                                        or not str(message.get("remote_id") or "")):
                raise CardPeriodError(
                    f"the open period in {self.path} has a `message` that names "
                    f"no message. A binding to nothing would let the next update "
                    f"patch whichever message it guessed")
            pending = open_.get("pending")
            if pending is not None:
                if _phase(open_) != FINAL_PENDING:
                    raise CardPeriodError(
                        f"the open period in {self.path} carries a `pending` while "
                        f"its phase is {_phase(open_)!r}: a pending final is the "
                        f"state of {FINAL_PENDING!r} and nothing else")
                if not isinstance(pending, dict) or not str(
                        (pending.get("frozen") or {}).get("version") or ""):
                    raise CardPeriodError(
                        f"the open period in {self.path} has a pending final with "
                        f"no prepared version. Without it a restart could not ask "
                        f"the delivery ledger whether the archive landed, and the "
                        f"only way forward would be a second message")
        return data

    def read(self) -> dict:
        return self._data

    def advance(self, *, period: dict, at: str, row_for=None,
                finalize=None, closing: str | None = None) -> dict:
        """Decide this instant, apply every step, and write once.

        Returns the decision with `applied: true` and the resulting summary. This
        verb records the LOCAL state — the card, its rows, and a PREPARED final —
        and it is deliberately NOT the thing that declares a shift archived:
        a FINAL step here moves the period to `FINAL_PENDING` and nothing more.
        The far end's acceptance is recorded separately, by `confirm`, on a
        verified `card_delivery` receipt. A caller that skips `confirm` leaves the
        period pending, which is the point: an unconfirmed archive must not let a
        successor open.

        `row_for` is handed each CREATE/PATCH step and returns the row it should
        record — this store measures nothing, so the readings come from the
        caller's model. `finalize` is handed each FINAL step and returns
        `{"archive": ..., "frozen": ...}`, the PREPARED archive and the version
        its PATCH will carry. Both are callbacks and not arguments because the row
        comes from a reading this store does not take and the archive is rendered
        from the model and the period's rows, which it does not hold. A caller
        that omits `finalize` still gets a FINAL that records the preparation, but
        the archive and the prepared version are then honestly `None` rather than
        invented here.
        """
        decision = decide(self._data, period=period, at=at, closing=closing)
        document = self._data
        for step in decision["steps"]:
            archive = step.get("archive")
            frozen = step.get("frozen")
            row_ = step.get("row_")
            if row_for is not None and step["action"] in (CREATE, PATCH):
                row_ = row_for(step)
            if step["action"] == FINAL and finalize is not None:
                filled = finalize(step) or {}
                archive = filled.get("archive", archive)
                frozen = filled.get("frozen", frozen)
            document = apply(document, step=step, row_=row_, at=at,
                             frozen=frozen, archive=archive)
        if decision["steps"]:
            _store_io()._atomic_write(self.path, document)
            self._data = document
        return {"schema": SCHEMA, "at": at, "applied": bool(decision["steps"]),
                "decision": decision, "period": summary(self._data)}

    def announce(self, *, remote_id: str, at: str) -> dict:
        """Record which message carries the open period's card."""
        message_id = str(remote_id or "").strip()
        if not message_id:
            raise CardPeriodError(
                "an announcement needs the message it is about: recording «a "
                "message» without an identity is the state this verb ends")
        if any(c.isspace() for c in message_id):
            raise CardPeriodError(
                "a message id is one token, and this one contains whitespace")
        if message_id.startswith("oc_"):
            raise CardPeriodError(
                "that is a chat id, not a message id: `oc_` names a conversation "
                "and `om_` names one message in it")
        if not str(at or ""):
            raise CardPeriodError("an announcement needs the instant it was made")
        open_ = self._data.get("open")
        if not isinstance(open_, dict) or not open_.get("period_ref"):
            raise CardPeriodError(
                "there is no open period for this message to carry")
        previous = open_.get("message")
        held = str(previous.get("remote_id") or "") if isinstance(previous, dict) else ""
        if not held:
            change = day.BOUND
            message = {"remote_id": message_id, "at": str(at)}
        elif held == message_id:
            change = day.UNCHANGED
            message = dict(previous)
        else:
            change = day.REBOUND
            message = {"remote_id": message_id, "at": str(at)}
        next_doc = dict(self._data)
        next_doc["open"] = dict(open_)
        next_doc["open"]["message"] = message
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"announced": True, "change": change, "message": message,
                "period_ref": open_.get("period_ref"), "period": summary(self._data)}

    def confirm(self, *, at: str, receipt: dict) -> dict:
        """Commit the freeze — but ONLY on a verified remote acknowledgement.

        This is the second half of the split the 2026-10-14 review asked for:
        `FINAL` prepares an archive and asks the transport to PATCH it, and THIS
        records that the far end accepted it. Until it runs the period stays
        `FINAL_PENDING`, every tick retries the same message, and no successor
        opens.

        `receipt` is a `card_delivery` receipt row for the `period` kind. It is
        accepted only when its `state` is DELIVERED and its `version` matches the
        version the pending final prepared. A receipt for a DIFFERENT version is
        a proof about a different card, and freezing on it would freeze an
        archive nobody sent.
        """
        open_ = self._data.get("open")
        if not isinstance(open_, dict):
            raise CardPeriodError("there is no open period to confirm")
        phase = _phase(open_)
        if phase == FROZEN:
            raise CardPeriodError(
                f"{open_.get('period_ref')} is already frozen at "
                f"{(open_.get('frozen') or {}).get('at')}")
        if phase != FINAL_PENDING:
            raise CardPeriodError(
                f"{open_.get('period_ref')} is {phase!r}: there is no pending final "
                f"to confirm. A confirmation is about an archive that was PREPARED "
                f"and sent, not about a live period")
        state = str((receipt or {}).get("state") or "")
        if state != delivery.DELIVERED:
            raise CardPeriodError(
                f"远端回执的 state 是 {state!r}，不是 {delivery.DELIVERED!r}："
                f"不能据此冻结。失败 / 超时 / 未知都必须留在 {FINAL_PENDING}，"
                f"由下一拍重试同一条消息")
        pending = open_.get("pending") or {}
        prepared = pending.get("frozen") or {}
        version = str(prepared.get("version") or "")
        if not version:
            raise CardPeriodError(
                "the pending final names no prepared version, so there is nothing a "
                "receipt could be checked against")
        receipt_version = str((receipt or {}).get("version") or "")
        if receipt_version and receipt_version != version:
            raise CardPeriodError(
                f"回执命名的版本 {receipt_version!r} 与准备好的 {version!r} 不同："
                f"这是一份关于另一张卡的证明，不能用来冻结这一张")
        remote_id = str((receipt or {}).get("remote_id")
                        or pending.get("remote_id") or "")
        if not remote_id:
            raise CardPeriodError(
                "the receipt names no message id and the pending final has none: a "
                "freeze that cannot say which message carries the archive is a "
                "freeze nobody can follow")
        next_doc = dict(self._data)
        next_open = dict(open_)
        frozen = dict(prepared)
        frozen["at"] = str(at)
        next_open.update({
            "phase": FROZEN,
            "frozen": frozen,
            "confirmed": {"at": str(at), "state": state, "version": version,
                          "remote_id": remote_id},
            "pending": None,
            "updated_at": str(at),
        })
        next_doc["open"] = next_open
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"confirmed": True, "period_ref": open_.get("period_ref"),
                "frozen": frozen, "period": summary(self._data)}

    def reconcile_pending(self, *, ledger, at: str) -> dict:
        """After a restart, ask the delivery ledger whether the archive landed.

        The RESTART path, and the reason no second delivery ledger exists: the
        INTENT is here (`open.pending`) and the RECEIPT is in `card_delivery` —
        this asks that ledger rather than keeping one of its own. A DELIVERED
        receipt for the pending version CONFIRMS the freeze; anything else
        (FAILED, UNKNOWN, a different version, no receipt at all) leaves the final
        pending and the caller retries the same message.
        """
        open_ = self._data.get("open")
        if not isinstance(open_, dict) or _phase(open_) != FINAL_PENDING:
            return {"reconciled": True, "outcome": "NOT_PENDING",
                    "period_ref": (open_.get("period_ref")
                                   if isinstance(open_, dict) else None)}
        pending = open_.get("pending") or {}
        version = str((pending.get("frozen") or {}).get("version") or "")
        rows = [r for r in ledger.entries(delivery.PERIOD)
                if isinstance(r, dict) and str(r.get("version") or "") == version]
        row = rows[-1] if rows else None
        if row is not None and row.get("state") == delivery.DELIVERED:
            result = self.confirm(at=at, receipt=row)
            return {"reconciled": True, "outcome": "CONFIRMED", **result}
        return {"reconciled": True, "outcome": "RETRY",
                "period_ref": open_.get("period_ref"),
                "receipt_state": (row or {}).get("state"),
                "says": ("投递台账里没有这一版的 DELIVERED 回执：最终归档仍未确认，"
                         "下一拍重试同一条 message_id，不新建消息")}

    def summary(self) -> dict:
        return summary(self._data)


def deploy_readiness() -> dict:
    """Whether this module may be pointed at a live Feishu surface. It may not.

    A RETURN VALUE rather than a docstring sentence, because a caller can branch
    on it. What is missing is not code here but EVIDENCE: no real `im messages
    patch` round-trip has been run, so «the far end accepted the archive» rests
    on the transport's own report with nothing independent to check it against.
    That is exactly the class of claim the 2026-10-14 review forbids marking
    deployable, and `PeriodStore` is a PLANNER plus a local state machine — it is
    not, on its own, proof that a shift was archived at the far end.
    """
    return {
        "ready": False,
        "reason": ("远端 ACK 未经真机验证：本模块只接受一个 DELIVERED 回执作为冻结"
                   "依据，而该回执尚未在真实飞书上往返验证；也没有独立的 readback "
                   "可用。因此 `PeriodStore` 是计划器 + 本地状态机，"
                   "不能单独声称「时段已在远端归档」"),
        "unverified": [
            "真实 Feishu `im messages patch` 的最终归档往返（CREATE/PATCH/FINAL）",
            "失败 / 429 / 超时下同一 message_id 的真实重试落地",
            "重启后 `reconcile_pending` 对真实 `card_delivery` 台账的读取",
            "CardKit 2.0 窄屏渲染（chart gate 仍关）",
        ],
    }


# ---------------------------------------------------------------------------
# the FINAL archive — the view model the last patch renders
# ---------------------------------------------------------------------------

def archive(model: dict, *, period: dict, rows: list[dict],
            incidents=(), recoveries=(), unresolved=(), history=None) -> dict:
    """The four blocks a period's final patch carries.

    总览概括 / 时段趋势 / 三设备摘要 / 事故·自愈·未解决交接. Built here and not
    in the renderer, for the reason `card_day.summary` gives: a renderer that
    knew how to read a row would be a second reader of the row schema.

    `incidents`, `recoveries` and `unresolved` are the period's event ledger —
    the NEW/ESCALATED/RECOVERED facts and the findings still open — handed in by
    the caller. This function does not read a ledger: the events come from the
    same source the incident cards do, so «what happened this shift» is answered
    once.
    """
    entries = day.timeline([check_row(r) for r in rows])
    devices = [_device_summary(device) for device in (model.get("devices") or [])]
    return {
        "schema": SCHEMA,
        "period_ref": period.get("period_ref"),
        "label": period.get("label"),
        "day_ref": period.get("day_ref"),
        "overview": _overview(model, period, entries),
        "trend": {
            "ticks": len(rows),
            "timeline": entries,
            "transitions": sum(1 for e in entries if e["kind"] == "transition"),
            "unobserved": sum(1 for e in entries if e["kind"] == "gap"),
        },
        "devices": devices,
        # The period's SAMPLED history, when the caller has one. A summary, never
        # a curve: `charts_verified` is still False, and a window with fewer than
        # two samples says «趋势不足» rather than drawing one.
        "history": history,
        "handover": {
            "incidents": [dict(i) for i in incidents],
            "recoveries": [dict(r) for r in recoveries],
            "unresolved": [dict(u) for u in unresolved],
        },
    }


def _overview(model: dict, period: dict, entries: list[dict]) -> dict:
    devices = model.get("devices") or []
    return {
        "devices": len(devices),
        "attention": len(_alert_lines(model)),
        "verdict": monitor._fleet_verdict(model) if devices else "UNKNOWN",
        "transitions": sum(1 for e in entries if e["kind"] == "transition"),
        "unobserved": sum(1 for e in entries if e["kind"] == "gap"),
        "says": (f"{period.get('day_ref')} · {period.get('label')} 时段结束："
                 f"{len(devices)} 台设备，{len(_alert_lines(model))} 项需要关注，"
                 f"时段内 {sum(1 for e in entries if e['kind'] == 'transition')} "
                 f"处状态变化、{sum(1 for e in entries if e['kind'] == 'gap')} "
                 f"段未观测"),
    }


def _device_summary(device: dict) -> dict:
    """One device's line in the final 摘要, as TYPED words.

    `reach` is three letters and `reach_word` is the phrase a person reads. The
    reachability MAPPING never travels: the card this module used to emit
    carried `{'state': 'KNOWN', 'reason': None, 'observed_at': ...}` straight
    into the body, because this dict was handed to a formatter that stringified
    it. A shape a formatter can leak is a shape this layer does not return.
    """
    return {
        "device_ref": device.get("device_ref"),
        "name": device.get("display_name") or device.get("device_ref"),
        "reach": _reach_state(device),
        "reach_word": _reach_note(device),
        "readings": [monitor.reading_line(r)
                     for r in (device.get("readings") or [])][:6],
    }


# ---------------------------------------------------------------------------
# the presentation layer — TYPED, and it can only speak in words
# ---------------------------------------------------------------------------
#
# EVERY value that reaches a card body comes through one of these functions.
# The rule they enforce is the one the 2026-10-14 review found broken: no raw
# `dict`, no `None`, no internal key and no Python quote may reach the markdown.
# The failure they prevent is concrete — the previous card body contained
# `{'state': 'KNOWN', 'reason': None, 'observed_at': '...'}` — and the way they
# prevent it is that a caller wanting a word receives a word, so the mapping
# object has no path to the body at all.

_REACH_WORDS = {"KNOWN": "有新鲜读数", "STALE": "可达性过期", "UNKNOWN": "未获取采样"}
#: Labels the tokyo KPI row prefers, in this order, from the readings that
#: actually arrived. A label with no reading is simply absent — never a zero.
_KPI_ORDER = ("CPU", "内存", "交换", "磁盘")
#: The swap reading above this percent gets its own tight line. It does NOT
#: colour the header: a swap near its soft line is worth a look, not an incident.
_SWAP_NEAR_PERCENT = 89.0


def _reach_state(device) -> str:
    """A device's reachability as one of THREE words. Never the mapping."""
    reach = device.get("reachability") if isinstance(device, dict) else None
    state = str(reach.get("state") or "").upper() if isinstance(reach, dict) else ""
    return state if state in _REACH_WORDS else "UNKNOWN"


def _reach_note(device) -> str:
    """The reachability phrase a person reads. Never `None`, never a key."""
    state = _reach_state(device)
    if state == "KNOWN":
        return "有新鲜读数"
    if state == "STALE":
        return "可达性过期"
    # An unreached device means the CONNECTOR did not reach it; it does NOT mean
    # the device is offline. Fleet reachability is not resource-monitoring
    # coverage, and «失联» would say the second thing while meaning the first.
    return "未获取采样 · 状态未知"


def _device_name(device) -> str:
    return str(device.get("display_name") or device.get("device_ref") or "设备")


def _coverage_line(model: dict) -> str:
    return " · ".join(f"{_device_name(d)} {_reach_note(d)}"
                      for d in (model.get("devices") or []))


def _service_line(model: dict) -> str:
    """Service COUNTS, but only when they were actually collected.

    `共 0` from a pass that never read a service list is not «there are zero
    services» — it is «nobody looked». The two are reported differently here
    for the same reason `reading_line` refuses to render an unread reading as
    a zero.
    """
    services = (model.get("summary") or {}).get("services") or {}
    if int(services.get("total") or 0) <= 0:
        return "服务：未采集"
    return (f"服务 共 {services['total']} · 正常 {services['ok']} · 需关注 "
            f"{services['attention']} · 需处理 {services['critical']} · 无法确认 "
            f"{services['unknown']}")


def _kpi_lines(device) -> list[str]:
    """Two to four of the device's REAL readings, formatted with their units."""
    known = [r for r in (device.get("readings") or [])
             if r.get("state") == monitor.STATE_KNOWN and r.get("value") is not None]
    picked, used = [], set()
    for want in _KPI_ORDER:
        for r in known:
            if id(r) in used:
                continue
            if want in str(r.get("label") or ""):
                picked.append(r)
                used.add(id(r))
                break
    if not picked:
        picked = known[:4]
    return [f"**{r['label']}** {monitor._value(r)}" for r in picked[:4]]


def _swap_warning(device) -> str | None:
    """A tight line when swap sits near its soft threshold. No colour, no alarm."""
    for r in device.get("readings") or []:
        if "交换" not in str(r.get("label") or "") \
                or r.get("state") != monitor.STATE_KNOWN:
            continue
        value = str(r.get("value") or "")
        if not value.endswith("%"):
            continue
        try:
            percent = float(value[:-1])
        except ValueError:
            continue
        if percent >= _SWAP_NEAR_PERCENT:
            note = r.get("note") or ""
            return f"{r['label']} {value}" + (f"，接近阈值（{note}）" if note else "，接近阈值")
    return None


def _alert_lines(model: dict) -> list[str]:
    """REAL findings only — a reading at ATTENTION/CRITICAL that was READ.

    A device nobody sampled is NOT an alert: it is a data gap, and it is listed
    as one below. Conflating the two is what made «Windows 未获取采样» paint the
    whole card red, which is the false severity the review named.
    """
    out = []
    for device in model.get("devices") or []:
        for r in device.get("readings") or []:
            severity = str(r.get("severity") or "OK").upper()
            if severity in ("ATTENTION", "CRITICAL") \
                    and r.get("state") == monitor.STATE_KNOWN:
                out.append(f"{_device_name(device)} · {r['label']}："
                           f"{monitor._value(r)}"
                           + (f"（{r['note']}）" if r.get("note") else ""))
    return out


def _real_critical(model: dict) -> bool:
    """Whether a genuinely CONFIRMED critical exists. The only red trigger."""
    for device in model.get("devices") or []:
        for r in device.get("readings") or []:
            if str(r.get("severity") or "").upper() == "CRITICAL" \
                    and r.get("state") == monitor.STATE_KNOWN:
                return True
    services = (model.get("summary") or {}).get("services") or {}
    return int(services.get("critical") or 0) > 0


def _gap_lines(model: dict) -> list[str]:
    gaps = []
    for device in model.get("devices") or []:
        if _reach_state(device) != "KNOWN":
            gaps.append(f"{_device_name(device)}：未获取采样（状态未知）")
            continue
        unknown = [r for r in device.get("readings") or []
                   if r.get("state") != monitor.STATE_KNOWN]
        if unknown:
            gaps.append(f"{_device_name(device)}：{len(unknown)} 项未读到")
    return gaps


def _fold_title(device) -> str:
    """A fold header that leads with the STATE, in words, never «失联».

    No emoji: the style guide rules decorative icons out of a serious card's
    titles, and the severity is carried by the words beside the name instead.
    """
    name = _device_name(device)
    readings = device.get("readings") or []
    bad = [str(r.get("severity") or "").upper() for r in readings
           if str(r.get("severity") or "").upper() in ("CRITICAL", "ATTENTION")
           and r.get("state") == monitor.STATE_KNOWN]
    mark = {"CRITICAL": "需处理 · ", "ATTENTION": "需关注 · "}.get(
        min(bad, key=lambda s: monitor.SEVERITY_RANK.get(s, 9), default=""), "")
    unknowns = sum(1 for r in readings if r.get("state") != monitor.STATE_KNOWN)
    tail = f" · 无法确认 {unknowns}" if unknowns else ""
    return f"{name} · {mark}{_reach_note(device)}{tail}"


def _fold_body(device, *, index: int) -> list[dict]:
    """The full V13 content of one device, inside its fold — as native elements.

    The machine's own readings become a small `column_set` of tiles rather than
    one run-on markdown line, so a fold opened on a phone shows the numbers the
    same way the first screen does. A device nobody sampled says so once.
    """
    ref = device.get("device_ref") or str(index)
    elements = []
    readings = device.get("readings") or []
    if _reach_state(device) != "KNOWN":
        elements.append(_md("<font color='grey'>未获取采样 · 状态未知</font>"))
    else:
        known = [r for r in readings
                 if r.get("state") == monitor.STATE_KNOWN and r.get("value") is not None]
        for start in range(0, min(len(known), 4), 2):
            elements.append({
                "tag": "column_set", "flex_mode": "none",
                "horizontal_spacing": "8px", "margin": "0px 0px 8px 0px",
                "columns": [
                    {"tag": "column", "width": "weighted", "weight": 1,
                     "background_style": "grey-50", "padding": "8px",
                     "vertical_spacing": "2px",
                     "elements": [
                         _md(monitor._value(r)),
                         {"tag": "markdown", "text_size": "notation",
                          "content": f"<font color='grey'>{_reading_caption(r)}</font>"},
                     ]}
                    for r in known[start:start + 2]
                ],
            })
        lines = []
        for r in readings:
            lines.append(monitor.reading_line(r))
            trend = monitor._trend_line(r)
            if trend:
                lines.append(trend)
        if lines:
            elements.append(_md("\n".join(lines)))
    accounts = device.get("accounts") or []
    if accounts:
        elements.append(monitor._accounts_panel(ref, accounts))
    return elements


def _local_hhmm(instant) -> str | None:
    """`17:42` in the reader's own zone. The card never shows a long ISO."""
    try:
        moment = datetime.datetime.fromisoformat(str(instant))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone().strftime("%H:%M")


def _archive_lines(view: dict) -> list[str]:
    """The period summary as short markdown lines, for the closure block.

    A TREND NEEDS TWO POINTS. With one sample the line says so out loud rather
    than printing «完整时段趋势一览» over a single reading, and no chart element
    is emitted for it — a `chart` over one value is a picture of nothing.
    """
    overview = view.get("overview") or {}
    trend = view.get("trend") or {}
    handover = view.get("handover") or {}
    ticks = int(trend.get("ticks") or 0)
    lines = [f"**时段小结** · {view.get('label') or '时段'}", overview.get("says") or ""]
    if ticks < 2:
        lines.append(f"**时段趋势**（仅 {ticks} 次采样，不足以评价趋势）")
    else:
        lines.append(f"**完整时段趋势一览**（{ticks} 拍 · "
                     f"{trend.get('transitions')} 处变化）")
        for entry in (trend.get("timeline") or [])[:8]:
            lines.append(f"- {entry.get('says')}")
    lines.append(f"**三设备摘要**（{len(view.get('devices') or [])}）")
    for device in (view.get("devices") or []):
        lines.append(f"- {device.get('name') or device.get('device_ref')} · "
                     f"{device.get('reach_word') or '状态未知'}")
    history = view.get("history")
    if isinstance(history, dict):
        if not history.get("available"):
            lines.append("**时段采样**（历史未接通或窗口不可读：UNKNOWN）")
        else:
            axes = history.get("axes") or {}
            parts = []
            for name, axis in axes.items():
                if not axis or axis.get("n") is None or axis.get("mean") is None:
                    continue
                parts.append(f"{name} 均 {axis['mean']} · 峰 {axis['peak']}"
                             + (f"（{axis['peak_at']}）" if axis.get("peak_at") else ""))
            lines.append(f"**时段采样** {history.get('says') or ''}".rstrip())
            if parts:
                lines.append("- " + "；".join(parts))
            if (history.get("trend") or "UNKNOWN") != "OK":
                lines.append("- 采样点不足 2 个：**趋势不足**，不出曲线")
    lines.append("**交接 · 事故 / 自愈 / 未解决**")
    incidents = handover.get("incidents") or []
    recoveries = handover.get("recoveries") or []
    unresolved = handover.get("unresolved") or []
    for item in incidents:
        lines.append(f"- 事故 · {item.get('says') or item.get('episode') or '（未命名）'}")
    for item in recoveries:
        lines.append(f"- 自愈 · {item.get('says') or item.get('episode') or '（未命名）'}")
    for item in unresolved:
        lines.append(f"- 未解决 · {item.get('says') or item.get('key') or '（未命名）'}")
    if not (incidents or recoveries or unresolved):
        lines.append("- 本时段未记录事故")
    return lines


def render_archive_text(view: dict) -> str:
    """The archive as plain markdown (the CLI and the tests read this)."""
    return "\n".join(_archive_lines(view))


    return elements


# ---------------------------------------------------------------------------
# the native CardKit 2.0 card — a dashboard, not a markdown blob
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT ONE MARKDOWN BLOB
# ---------------------------------
# The previous card was assembled entirely from `markdown` elements. It carried
# the right facts and still read badly: «some text, then more text» is not a
# layout — the eye has no entry point, nothing is grouped, and a phone shows a
# wall of lines. This renderer uses the components the CardKit 2.0 reference
# documents for the job the card actually has — a data dashboard — and nothing
# else:
#
#     column_set + column   the KPI grid (native 2x2, real tile backgrounds)
#     div.fields            the per-device coverage pairs (native label/value)
#     collapsible_panel     the three device bodies, folded
#     markdown              short captions, the closure block and the footer
#     header                title / subtitle / text_tag_list / icon
#
# WHAT IT REFUSES, ON PURPOSE
# ---------------------------
#   * `column.corner_radius` — NOT a field of `column` in the component
#     reference, and the one that makes Card 2.0 answer `200621 unknown
#     property` (larksuite/cli issue #2371, whose own style-guide example
#     carries it). Only the confirmed `background_style` / `padding` /
#     `vertical_spacing` / `width` / `weight` are used, so a rounded KPI tile is
#     simply not attempted rather than faked.
#   * `chart` — gated off. One sample is not a trend and no VChart has been read
#     on a real client.
#   * `interactive_container` — its `behaviors` is mandatory when the component
#     is declared and this card has no interaction, so it is not used as
#     decoration.
#   * `table` — root-only and unverified at both widths; not forced in.

#: The component vocabulary this renderer may emit. A tag outside it is refused
#: before the card is handed to a transport, so an invented tag — which renders
#: as a blank — cannot ship.
_NATIVE_TAGS = frozenset({
    "markdown", "hr", "div", "column_set", "column", "collapsible_panel",
    "table", "chart", "plain_text", "lark_md", "standard_icon", "text_tag",
})
#: `header.template` and `text_tag.color` enumerations, from `components/header.md`.
_NATIVE_TEMPLATES = ("blue", "wathet", "turquoise", "green", "yellow", "orange",
                     "red", "carmine", "violet", "purple", "indigo", "grey",
                     "default")
_NATIVE_TAG_COLORS = ("neutral", "blue", "turquoise", "lime", "orange", "violet",
                      "indigo", "wathet", "green", "yellow", "red", "purple",
                      "carmine")
#: A token from the CLI's own verified icon list. Chosen rather than composed:
#: a made-up token renders as nothing.
_HEADER_ICON = "setting_outlined"
_NATIVE_MAX_ELEMENTS = 200
#: The KPI axes, TYPED. `label` is what the tile prints — chosen by this table
#: rather than copied from the reading — and `unit` says what the number IS.
#: The type check is the whole point: a reading whose value contradicts its axis
#: is DROPPED from the grid rather than shown under a caption that lies about
#: it, which is exactly the confusion the desktop screenshot caught —
#: «CPU 128» was 128 logical CORES, not a utilization, because the caption came
#: from the reading's label instead of from what the number was.
#:
#: `unit: "any"` with `labels` picks the caption FROM the value's own type, so a
#: source that later reports a real CPU utilization gets the caption it has
#: earned and a source reporting a core count keeps the honest one.
_KPI_AXES = (
    {"match": "CPU", "unit": "any",
     "labels": {"percent": "CPU 使用率", "other": "CPU逻辑核"}},
    {"match": "内存", "unit": "percent", "labels": {"percent": "内存", "other": "内存"}},
    {"match": "交换", "unit": "percent", "labels": {"percent": "交换区", "other": "交换区"}},
    {"match": "磁盘", "unit": "percent", "labels": {"percent": "磁盘", "other": "磁盘"}},
)


def _value_unit(value) -> str:
    """What a formatted value IS: `percent` or `other`. One place decides."""
    return "percent" if str(value).strip().endswith("%") else "other"


def _reading_caption(reading) -> str:
    """A READING's tile caption, typed when its axis is known.

    The fold tiles take their caption from here for the same reason the KPI
    grid does: a bare «CPU» under a core count is the confusion this whole layer
    exists to remove, and the fold is not a place it is allowed to reappear. An
    unknown label keeps its own name rather than being forced into an axis.
    """
    label = str(reading.get("label") or "")
    value, _ = _split_value(reading.get("value"))
    unit = _value_unit(value)
    for axis in _KPI_AXES:
        if axis["match"] in label:
            return (axis.get("labels") or {}).get(unit, label)
    return label


def _split_value(raw) -> tuple[str, str]:
    """`33.7%（83.3 GiB / 247.0 GiB）` -> («33.7%», «83.3 GiB / 247.0 GiB»).

    The big number and its caption are separated here rather than at the tile,
    because «which part is the number» is a property of the reading's own
    formatting. A value with no leading number (`128 逻辑核`) keeps its whole
    text as the number and has no caption — and a core COUNT is never rendered
    as a percentage, because it is not one.
    """
    text = str(raw or "").strip()
    if not text:
        return "—", ""
    found = re.match(r"^([0-9][0-9.,]*%?)\s*(.*)$", text)
    if found and found.group(1):
        return found.group(1), found.group(2).strip("（）() ").strip()
    return text, ""


def _kpi_cell(axis: dict, value: str, note: str) -> dict:
    """One KPI tile: its value, caption, and the colours it has EARNED."""
    label = (axis.get("labels") or {}).get(_value_unit(value), axis.get("match"))
    color, background = "blue", "grey-50"
    if label.startswith("交换") and value.endswith("%"):
        try:
            if float(value[:-1]) >= _SWAP_NEAR_PERCENT:
                color, background = "orange", "yellow-50"
        except ValueError:
            pass
    return {"label": label, "value": value, "note": note,
            "bg": background, "color": color}


def _kpi_cells(model: dict) -> list[dict]:
    """The measured KPIs, in axis order, type-checked against their axis.

    An axis with no reading is ABSENT — never a zero, never a green tile. An
    axis whose reading contradicts its declared unit is absent too: «a
    percentage is not a core count» and a tile that showed one under the other
    would be the caption lying, which is what this table exists to prevent.
    """
    devices = model.get("devices") or []
    main = next((d for d in devices if _reach_state(d) == "KNOWN"),
                devices[0] if devices else None)
    known = []
    if main is not None:
        known = [r for r in (main.get("readings") or [])
                 if r.get("state") == monitor.STATE_KNOWN and r.get("value") is not None]
    cells = []
    for axis in _KPI_AXES:
        match = next((r for r in known
                      if axis["match"] in str(r.get("label") or "")), None)
        if match is None:
            continue
        value, note = _split_value(match.get("value"))
        if axis["unit"] != "any" and _value_unit(value) != axis["unit"]:
            # A contradiction between the number and what it claims to be. The
            # raw reading still appears in the device fold; the grid simply does
            # not repeat it under a caption it does not match.
            continue
        cells.append(_kpi_cell(axis, value, note))
    return cells


def _kpi_tile(cell: dict) -> dict:
    """One native `column`: a big value over a small grey caption.

    Value and caption only. The reading's own note (a byte split, a threshold)
    lives in the device fold, so the tile stays two lines and the 2x2 keeps its
    height on a phone.
    """
    return {
        "tag": "column", "width": "weighted", "weight": 1,
        "background_style": cell["bg"], "padding": "12px",
        "vertical_spacing": "2px",
        "elements": [
            {"tag": "markdown", "text_align": "center",
             "content": f"## <font color='{cell['color']}'>{cell['value']}</font>"},
            {"tag": "markdown", "text_align": "center", "text_size": "notation",
             "content": f"<font color='grey'>{cell['label']}</font>"},
        ],
    }


def _kpi_grid(cells: list[dict]) -> list[dict]:
    """Two tiles per `column_set` — a native 2x2.

    `flex_mode: "none"` is the reference's own recommendation for metric tiles:
    the columns compress by weight rather than stacking or stretching, so a
    phone keeps the grid and a long value wraps inside its own tile instead of
    being clipped.
    """
    elements = []
    for start in range(0, len(cells), 2):
        elements.append({
            "tag": "column_set", "flex_mode": "none",
            "horizontal_spacing": "8px", "margin": "0px 0px 8px 0px",
            "columns": [_kpi_tile(cell) for cell in cells[start:start + 2]],
        })
    return elements


def _source_coverage(model: dict) -> dict:
    """Per-device coverage as native `div.fields` label/value pairs.

    Native alignment, not a `column_set` used to fake a field grid — and one
    line per device, so the coverage is read as a table rather than retold in a
    paragraph.
    """
    fields = []
    for device in model.get("devices") or []:
        fields.append({
            "is_short": True,
            "text": {"tag": "lark_md",
                     "content": f"**{_device_name(device)}**\n"
                                f"<font color='grey'>{_reach_note(device)}</font>"},
        })
    return {"tag": "div", "fields": fields, "margin": "0px 0px 8px 0px"}


def _native_panel(title: str, elements: list[dict], eid: str) -> dict:
    """A fold, in the shape the node's live cards already send."""
    return {
        "tag": "collapsible_panel",
        "element_id": eid,
        "expanded": False,
        "header": {
            "title": {"tag": "markdown", "content": f"**{title}**"},
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "size": "16px 16px"},
            "icon_position": "follow_text",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "padding": "8px 8px 8px 8px",
        "vertical_spacing": "8px",
        "margin": "0px 0px 8px 0px",
        "elements": elements,
    }


def _device_panel(model: dict, device: dict, index: int, *, main) -> dict:
    """One device's fold: its detail, plus the collected device's service line."""
    elements = _fold_body(device, index=index)
    if device is main:
        # Service COUNTS live inside the collected device's own fold, never as a
        # standalone first-screen line — «服务：未采集» as its own row was one of
        # the things that made an earlier card read badly.
        elements.append({"tag": "markdown", "text_size": "notation",
                         "content": f"<font color='grey'>{_service_line(model)}</font>"})
    return _native_panel(_fold_title(device), elements,
                         monitor.element_id("pan", device.get("device_ref") or index))


def _archive_panel(archive: dict) -> dict:
    """The FINAL closure, a light-background block after the first screen."""
    return {
        "tag": "column_set", "flex_mode": "none", "margin": "0px 0px 8px 0px",
        "columns": [{
            "tag": "column", "width": "weighted", "weight": 1,
            "background_style": "grey-50", "padding": "12px",
            "vertical_spacing": "4px",
            "elements": [_md("\n".join(_archive_lines(archive)))],
        }],
    }


def _card_footer(test_mark) -> dict:
    """The one-line read-only footer, shared by LIVE and ARCHIVED."""
    return {"tag": "markdown", "text_size": "notation",
            "content": "<font color='grey'>只读 · 本卡不会停止、重启或清理任何东西"
            + (" · 灰度测试，非正式播报" if test_mark else "") + "</font>"}


def _native_header(model: dict, *, archive, test_mark, now_hhmm) -> dict:
    """Title / subtitle / status tags / icon, within the header contract.

    The title names the PRODUCT. The period identity, the test mark and the
    sample time are small subtitle text, and the collection state is a
    `text_tag_list` — no developer identifier and no raw JSON reach the header.
    """
    devices = model.get("devices") or []
    collected = sum(1 for device in devices if _reach_state(device) == "KNOWN")
    tags = [{"tag": "text_tag",
             "text": {"tag": "plain_text",
                      "content": f"{collected}/{len(devices)} 已采集"},
             "color": "blue" if collected else "neutral"}]
    pending = len(devices) - collected
    if pending:
        tags.append({"tag": "text_tag",
                     "text": {"tag": "plain_text", "content": f"{pending} 待接采集"},
                     "color": "neutral"})
    # TWO IDENTITIES, NOT ONE TITLE WITH A DIFFERENT SUBTITLE. A live card and a
    # period report are different products, and a reader who opens a report
    # should not have to read the subtitle to find out which one they are in.
    if archive is not None:
        label = str((archive.get("label") or "").strip())
        window = str((archive.get("period_ref") or "").strip())
        title = "ServerOS · 核报告"
        bits = [b for b in (label or None,
                            window.split("/")[-1] if window else None,
                            "期末快照",
                            str(test_mark) if test_mark else None) if b]
    else:
        title = "ServerOS · 节点监控"
        bits = [b for b in (f"{now_hhmm} 更新" if now_hhmm else None,
                            str(test_mark) if test_mark else None,
                            "时段进行中") if b]
    return {
        "template": "red" if _real_critical(model) else "blue",
        "icon": {"tag": "standard_icon", "token": _HEADER_ICON, "color": "blue"},
        "title": {"tag": "plain_text", "content": title},
        "subtitle": {"tag": "plain_text", "content": " · ".join(bits)},
        "text_tag_list": tags[:3],
    }


def _element_count(node) -> int:
    if isinstance(node, dict):
        return (1 if "tag" in node else 0) + sum(_element_count(v) for v in node.values())
    if isinstance(node, list):
        return sum(_element_count(v) for v in node)
    return 0


def _validate_native(card: dict) -> dict:
    """Refuse a native card that breaks a structural rule. Raises, never warns.

    Checks the schema, the component vocabulary, element-id uniqueness and
    shape, the 200-element ceiling and the serialised size — the same rules
    `fleet_monitor_card.validate` applies, against THIS renderer's vocabulary
    (which adds `div` / `text_tag` and drops nothing).
    """
    if card.get("schema") != "2.0":
        raise CardPeriodError(f"schema {card.get('schema')!r} is not '2.0'")
    unknown = sorted(set(monitor._walk_tags(card, [])) - _NATIVE_TAGS)
    if unknown:
        raise CardPeriodError(
            f"native card emits undeclared tag(s) {unknown}; an invented tag "
            f"renders as a blank")
    header = card.get("header") or {}
    template = header.get("template")
    if template is not None and template not in _NATIVE_TEMPLATES:
        raise CardPeriodError(f"header.template {template!r} is not an enumerated colour")
    for tag in header.get("text_tag_list") or []:
        if tag.get("color") not in _NATIVE_TAG_COLORS:
            raise CardPeriodError(f"text_tag colour {tag.get('color')!r} is not enumerated")
    ids = monitor._walk_element_ids(card, [])
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise CardPeriodError(f"duplicate element_id(s) {duplicates}")
    for eid in ids:
        if not isinstance(eid, str) or not monitor._ELEMENT_ID_RE.match(eid) \
                or len(eid) > monitor.ELEMENT_ID_MAX:
            raise CardPeriodError(f"element_id {eid!r} is not «字母开头，≤20 字符»")
    count = _element_count(card.get("body")) + 1  # header counts as one
    if count > _NATIVE_MAX_ELEMENTS:
        raise CardPeriodError(f"card has {count} elements, over the {_NATIVE_MAX_ELEMENTS} limit")
    size = len(json.dumps(card, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    if size > monitor.MAX_CARD_BYTES:
        raise CardPeriodError(f"card is {size} bytes, over the {monitor.MAX_CARD_BYTES} budget")
    return card


def render_period_card(model: dict, *, archive: dict | None = None,
                       test_mark: str | None = None, rich: bool = False,
                       charts_verified: bool = False) -> dict:
    """The period card as a native CardKit 2.0 dashboard. PURE.

    The header is NEUTRAL by default; red is kept for a genuinely CONFIRMED
    critical (`_real_critical`) and nothing else, so an unmeasured data source
    never paints the card. The first screen is a native KPI grid, the coverage
    pairs, at most one warning and one gap line — then three folds. A `table`
    and a `chart` are not emitted: neither has been read on a real client, and
    `charts_verified` is refused rather than trusted.
    """
    if charts_verified:
        raise CardPeriodError(
            "charts_verified is not a parameter a caller toggles yet: neither the "
            "VChart element nor the 390px narrow layout has been accepted on a "
            "real client, so this renderer refuses to claim a chart was verified")
    devices = model.get("devices") or []
    main = next((d for d in devices if _reach_state(d) == "KNOWN"),
                devices[0] if devices else None)
    elements: list[dict] = []
    # The component tree, in order, each piece a named helper so LIVE and
    # ARCHIVED cannot drift. A REPORT LEADS WITH ITS ARCHIVE — its first screen
    # is the period's own summary, and the live readings below it are a closing
    # snapshot, not the point of the card. LIVE is the mirror image.
    if archive is not None:
        elements.append(_archive_panel(archive))
    elements.extend(_kpi_grid(_kpi_cells(model)))
    warning = _swap_warning(main) if main else None
    if warning:
        elements.append({"tag": "markdown", "text_size": "notation",
                         "margin": "0px 0px 8px 0px", "content": f"⚠️ {warning}"})
    if devices:
        elements.append(_source_coverage(model))
    # NOTE: there is deliberately NO second, full-width line naming the
    # unmeasured devices. `_source_coverage` above already carries each device's
    # state once, and repeating the names in a run-on sentence is what made a
    # card that said the same thing three times and overflowed the right edge.
    if archive is not None:
        # one short, quiet line marking what the readings below are
        elements.append({"tag": "markdown", "text_size": "notation",
                         "margin": "0px 0px 8px 0px",
                         "content": "<font color='grey'>以下为期末资源快照</font>"})
    for index, device in enumerate(devices):
        elements.append(_device_panel(model, device, index, main=main))
    elements.append(_card_footer(test_mark))
    card = {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": _native_header(model, archive=archive, test_mark=test_mark,
                                 now_hhmm=_local_hhmm(model.get("generated_at"))),
        "body": {"direction": "vertical", "padding": "12px 12px 20px 12px",
                 "elements": elements},
    }
    return _validate_native(card)


def render_archive(view: dict, *, model: dict, rich: bool = False,
                   charts_verified: bool = False, test_mark: str | None = None) -> dict:
    """The period's FINAL card — the same renderer, with the closure block added.

    Kept as its own name because callers (and the CLI) ask for «the archive
    card» specifically; it IS `render_period_card`, so a fix to the first screen
    fixes both and the LIVE card can never look different from the FINAL one by
    accident.
    """
    return render_period_card(model, archive=view, test_mark=test_mark,
                              rich=rich, charts_verified=charts_verified)


def _md(content: str) -> dict:
    return monitor._md(content)


def _wrap(text: str, *, limit: int = None) -> str:
    if limit is None:
        limit = getattr(monitor, "MOBILE_LINE_CHARS", 40)
    return monitor._wrap_lines(text, limit=limit)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Answer one instant on stdout, for a hand check."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--at", default="")
    parser.add_argument("--declare", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--store", default="")
    args = parser.parse_args(argv)
    instant = args.at or datetime.datetime.now(
        datetime.timezone.utc).astimezone().isoformat()
    declaration = json.loads(args.declare) if args.declare else None
    period = period_at(instant, declaration=declaration)
    store = PeriodStore(args.store or default_path())
    result = store.advance(period=period, at=instant)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print(period["says"])
        print(result["decision"]["says"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
