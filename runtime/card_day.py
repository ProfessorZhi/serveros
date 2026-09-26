#!/usr/bin/env python3
"""§11 — one card per workday, patched at the hour, and the hours it folded.

WHY THIS IS NOT PART OF `card_delivery`
---------------------------------------
§3's split says a send is recorded against a VERSION and never against a clock.
This module answers the other half of that sentence: which DAY a card belongs
to, whether today already has one, and what happens to the hours it has already
reported. `card_delivery` records what a transport did with a payload;
`card_day` records what today's card has said so far. Putting them in one
document would make "the card that was sent" and "the card that exists" the
same row, and the first re-render would overwrite the receipt.

WHAT §11 ASKS FOR, IN FOUR SENTENCES
------------------------------------
    每个 WORKDAY 在首个允许的播报时点创建一张 Daily Status Card
    08:00 错过可在后续工作窗口创建，之后整点只 PATCH，不追发已错过的所有小时
    每次更新先把旧 latest 压缩成 compact history row，再写新 latest
    20:00 完成最后一次 routine patch 后当日卡不再因普通采样变化，次一 WORKDAY 新建

The first two are `decide`; the third is `fold`; the fourth is the window
`business_calendar.routine_window` already computed. Every one of them is
answered here without a clock and without a transport.

THE ROW IS A CLOSED SET OF FIELDS, AND THAT IS THE POINT
--------------------------------------------------------
§11 says a history row keeps «关键人读数字和状态» and does not copy
«PID/code/raw probe». A rule that *trims* a model would have to know every
machine-detail field the model will ever grow; a row that can only be built
from an allowlist cannot carry one at all. So `row()` extracts by name and
`check_row()` refuses anything built by hand with a field outside it — the
second is what makes the first a property of the document and not a habit of
the one caller who read the docstring.

WHAT IT IS NOT ALLOWED TO DO
----------------------------
No clock, no render, no send, no transport. It does not decide that a card
should be sent: it decides what happens to the DAY's card, and a caller that
ignores the answer has ignored a judgement rather than a suggestion.

An unreadable store is REFUSED rather than replaced, for a reason that is
specific to this document: starting over would give the same workday a second
NEW card, which is the one thing V28 forbids by name («只 patch 一张 Status
Card»). Losing the memory is not the harm; re-creating the card is.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-day.v1"

#: The card's own memory, beside the delivery ledger and never inside it. Which
#: document this names for a given state directory is `day_path`, and the two
#: are checked against each other in the tests rather than by eye.
DEFAULT_PATH = Path.home() / ".serveros" / "cards" / "day.json"

CREATE = "CREATE"
PATCH = "PATCH"
SKIP = "SKIP"
ACTIONS = (CREATE, PATCH, SKIP)

#: Why. One per answer, because "SKIP" alone cannot be argued with: a person
#: reading the card has to be able to tell «今天不是工作日» from «今天没有日历».
NEW_WORKDAY = "NEW_WORKDAY"
SAME_WORKDAY = "SAME_WORKDAY"
NOT_A_WORKDAY = "NOT_A_WORKDAY"
CALENDAR_UNKNOWN = "CALENDAR_UNKNOWN"
BEFORE_WINDOW = "BEFORE_WINDOW"
AFTER_WINDOW = "AFTER_WINDOW"
SCHEDULE_UNREADABLE = "SCHEDULE_UNREADABLE"
OUT_OF_ORDER = "OUT_OF_ORDER"

REASONS = (NEW_WORKDAY, SAME_WORKDAY, NOT_A_WORKDAY, CALENDAR_UNKNOWN,
           BEFORE_WINDOW, AFTER_WINDOW, SCHEDULE_UNREADABLE, OUT_OF_ORDER)

#: The reasons a schedule may carry that this module passes through unchanged.
PASSED_THROUGH = (BEFORE_WINDOW, AFTER_WINDOW)

#: The row's whole vocabulary. `hhmm` and `local` are the calendar's clock, not
#: the machine's — §9 puts the timezone in the declaration, and a row stamped in
#: another zone would disagree with the window it claims to be inside.
ROW_FIELDS = ("tick", "hhmm", "local", "verdict", "observation", "sections", "gaps")
#: The same three blocks `presentation.SECTIONS` names, and the tests check the
#: two against each other rather than a comment promising they agree.
ROW_SECTIONS = ("mine", "host", "gateway")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _calendar():
    return _load("serveros_runtime_business_calendar", _HERE / "business_calendar.py")


def _store_io():
    # The same atomic write every other durable document in this tree uses —
    # one implementation of "write JSON without a torn read", not two.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


def ticks_per_workday() -> int:
    """§10's thirteen, read from the module that owns the window.

    A literal here would be the second place the number lives, and the two
    would disagree the first time the window moved. This is a function rather
    than a module constant because the ceiling and the window must be the same
    fact, and a copy taken at import time is still a copy.
    """
    return _calendar().ROUTINE_UPDATES_PER_WORKDAY


class CardDayError(RuntimeError):
    pass


def day_path(state_path) -> Path:
    """Where the day's memory sits, given a state directory.

    `card_delivery.ledger_path` for the same reason and the same shape: sibling
    documents under `<state>/cards/`, because a day record that lived inside
    the register would be erased by a pass that has nothing to do with cards.
    """
    return Path(state_path).parent.parent / "cards" / "day.json"


def empty() -> dict:
    return {"schema": SCHEMA, "open": None, "previous": None}


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------

def decide(document: dict, *, schedule: dict, at: str) -> dict:
    """What happens to the day's card at this instant, and why.

    `schedule` is `business_calendar.routine_window`'s answer — this module does
    not re-derive the calendar, the window or the tick, because a second
    derivation is a second answer waiting to disagree with the one on the face.

    The order of the refusals is the order a reader would fix them: no day
    identity, then not a workday, then outside the window, and last a tick
    behind the card's own — that last one can only be asked once there IS a
    card, which is why it is asked there. Nothing here writes.

    WHY 「整点」 IS NOT A REFUSAL ON THE CLOCK'S SECOND
    --------------------------------------------------
    V28 says 「08:00–20:00 **整点**只 patch 一张 Status Card」, and the first
    reading of that sentence — refuse anything not exactly on the hour — was
    wrong, for two reasons that are written down where they come from:

      * §11 sentence 4 says the card stops changing after 20:00 「不再因普通采样
        变化」, which says ordinary sampling DOES change it inside the window.
        A sample at 09:30 is that sampling. Refusing it would be reading a
        sentence about routine updates as a ban on the sampling the same
        paragraph relies on.
      * `routine_window` reports `on_the_hour` and says so itself: the hour is
        推荐 and «a recommendation is not a rule — a tick a minute late is still
        the same tick». A timer that fires at 09:00:20 is that tick.

    What 「整点」 actually forbids is a SECOND row for the hour, and the tick's
    ordinal already forbids it: 09:00 and 09:30 are both tick 2, so the second
    look REPLACES the hour rather than adding one, and the hour is still folded
    exactly once when 10:00 arrives. Enforcing it on the clock instead would
    make an hourly timer that drifts off the second update the card never again
    — a card that reads 08:00 all day while the node is fine, which is the kind
    of stale surface this whole scope exists to remove.
    """
    sched = schedule or {}
    day_ref = sched.get("day_ref")
    tick = sched.get("tick")
    reason = sched.get("reason") or ""
    base = {
        "schema": SCHEMA,
        "at": at,
        "day_ref": day_ref,
        "day_kind": sched.get("kind"),
        "tick": tick,
        "ticks_per_workday": ticks_per_workday(),
        "missed_ticks": 0,
        "rollover": False,
        "folds": False,
        "routine": False,
        "reason": None,
        "action": None,
        "says": "",
    }

    def answer(action: str, why: str, says: str, **extra) -> dict:
        base.update({"action": action, "reason": why, "says": says})
        base.update(extra)
        return base

    if not day_ref or sched.get("kind") == "UNKNOWN" or reason == CALENDAR_UNKNOWN:
        # A card cannot be filed under a day nobody can name. The identity is
        # also the dedupe key, so `None` would collide across every node and
        # every day at once.
        return answer(
            SKIP, CALENDAR_UNKNOWN,
            "没有可用的当天身份（calendar=UNKNOWN 或读取里没有日期），"
            "不新建也不 patch 当天卡；事故卡另按 P1/P2 策略")

    if sched.get("kind") == "NON_WORKING_DAY" or reason == "NON_WORKING_DAY":
        return answer(
            SKIP, NOT_A_WORKDAY,
            f"{day_ref.split('/')[-1]} 是休息日，不创建空白状态卡，也不补发；"
            "事故卡另按 P1/P2 策略")

    if not sched.get("in_window"):
        if reason in PASSED_THROUGH:
            if reason == BEFORE_WINDOW:
                says = ("早于常规播报窗口，当天卡尚未到第一个允许的时点；"
                        f"进入窗口后的第一个整点建卡，错过的不追发")
            else:
                says = ("晚于常规播报窗口，当日卡不再因普通采样变化，"
                        "不新建也不 patch；次一工作日新建")
            return answer(SKIP, reason, says)
        return answer(
            SKIP, SCHEDULE_UNREADABLE,
            f"日程读数说这一拍不在窗口内，但给的理由是 {reason!r} 而不是"
            f"{' 或 '.join(repr(r) for r in PASSED_THROUGH)}，因此不据此动当天卡")

    if not isinstance(tick, int) or not 1 <= tick <= ticks_per_workday():
        return answer(
            SKIP, SCHEDULE_UNREADABLE,
            f"日程读数里没有可用的时点序号（tick={tick!r}，"
            f"本工作日的时点数是 {ticks_per_workday()}），因此不据此动当天卡")

    open_day = document.get("open")
    if not isinstance(open_day, dict) or not open_day.get("day_ref"):
        return answer(
            CREATE, NEW_WORKDAY, _create_says(sched, day_ref, tick, missed=tick - 1,
                                              rolled=False),
            routine=True, missed_ticks=tick - 1)
    if open_day.get("day_ref") != day_ref:
        return answer(
            CREATE, NEW_WORKDAY, _create_says(sched, day_ref, tick, missed=tick - 1,
                                              rolled=True),
            routine=True, missed_ticks=tick - 1, rollover=True)
    latest = open_day.get("latest")
    if isinstance(latest, dict) and isinstance(latest.get("tick"), int) \
            and tick < latest["tick"]:
        # A backward tick is a clock that stepped or a caller replaying an
        # earlier hour, and letting it through is worse than it looks: the
        # latest moves backwards, the NEXT real tick folds the wrong hour into
        # the history, and the panel a person opens lists the day out of order.
        # Refusing keeps «按小时» an invariant of the document rather than a
        # hope about the clock. It is not an error in the instant — the instant
        # was read correctly — so it is a SKIP with its own reason.
        return answer(
            SKIP, OUT_OF_ORDER,
            f"这一拍是当天的第 {tick} 个时点，而当天卡已经在第 "
            f"{latest['tick']} 个时点（{latest.get('hhmm')}）上：当天卡按小时"
            f"向前推进，往回的一拍不写，避免历史出现倒序；"
            f"采样与记录照常")
    return answer(
        PATCH, SAME_WORKDAY,
        f"{day_ref.split('/')[-1]} 已有当天卡（最后更新 {open_day.get('updated_at')}，"
        f"本拍是第 {tick}/{ticks_per_workday()} 个时点），"
        f"本次只 patch 这一张，不新增一条聊天记录",
        routine=True, folds=_will_fold(open_day, tick))


def _create_says(sched: dict, day_ref: str, tick: int, *, missed: int,
                 rolled: bool) -> str:
    since = ("上一张卡属于另一天，今天新建一张" if rolled
             else "今天还没有卡，现在建一张")
    tail = (f"；早先的 {missed} 个时点不追发" if missed else "")
    return (f"{day_ref.split('/')[-1]} 是工作日，{since}"
            f"（第 {tick}/{ticks_per_workday()} 个时点{tail}）")


def _will_fold(open_day: dict, tick: int) -> bool:
    """Whether this patch pushes the previous latest into the history.

    A second update inside the same hour REPLACES the hour's row rather than
    folding it: §11 keeps the history «按小时», so two rows for one hour would
    be the card disagreeing with its own cadence.
    """
    latest = open_day.get("latest")
    if not isinstance(latest, dict):
        return False
    return latest.get("tick") != tick


# ---------------------------------------------------------------------------
# the row
# ---------------------------------------------------------------------------

def row(model: dict, *, schedule: dict) -> dict:
    """One hour of the day, as the card will show it.

    Extracted by name, never copied: whatever the model grows — a detail block,
    a probe payload, a diagnostic list — cannot arrive here, because there is
    no field for it. §11's «不复制 PID/code/raw probe» is a property of this
    function rather than a rule its callers are asked to remember.
    """
    sched = schedule or {}
    tick, hhmm = sched.get("tick"), hhmm_of(sched)
    if not isinstance(tick, int) or not hhmm:
        raise CardDayError(
            f"a row needs the hour it belongs to: tick={tick!r}, "
            f"hour={sched.get('hour')!r}, minute={sched.get('minute')!r}. "
            f"`business_calendar.routine_window` gives all three when the "
            f"instant is an hour inside the window, and a row without them is a "
            f"row no history can be read back in order")
    health = model.get("env_health") or {}
    sections = model.get("sections") or {}
    candidate = {
        "tick": tick,
        "hhmm": hhmm,
        "local": sched.get("local") or None,
        "verdict": health.get("verdict"),
        "observation": health.get("observation"),
        "sections": {name: (sections.get(name) or {}).get("state")
                     for name in ROW_SECTIONS},
        "gaps": len(health.get("gaps") or []),
    }
    return check_row(candidate)


def check_row(candidate: dict) -> dict:
    """The row's schema, enforced on documents as well as on callers.

    Refusing an unknown field rather than dropping it is the difference between
    a document that says what it carries and one that carries whatever it was
    handed. The refusal names the field, because the fix is always to put it
    back in the model where the reader can still see it.
    """
    if not isinstance(candidate, dict):
        raise CardDayError(f"a row is a JSON object, not {type(candidate).__name__}")
    extra = [k for k in candidate if k not in ROW_FIELDS]
    if extra:
        raise CardDayError(
            f"a history row carries {', '.join(sorted(extra))} — a row keeps "
            f"{', '.join(ROW_FIELDS)} and nothing else. Machine detail belongs "
            f"in the observation, which the card links to and does not copy")
    missing = [k for k in ROW_FIELDS if k not in candidate]
    if missing:
        raise CardDayError(
            f"a history row is missing {', '.join(missing)}; an absent value "
            f"and a value nobody recorded would read the same without it")
    sections = candidate.get("sections")
    if not isinstance(sections, dict):
        raise CardDayError("a row's `sections` is an object of state words")
    strays = [k for k in sections if k not in ROW_SECTIONS]
    if strays:
        raise CardDayError(
            f"a row's `sections` names {', '.join(sorted(strays))}, which is "
            f"not one of {', '.join(ROW_SECTIONS)}")
    gaps = candidate.get("gaps")
    if not isinstance(gaps, int) or isinstance(gaps, bool) or gaps < 0:
        raise CardDayError(
            f"a row's `gaps` is the count of collection gaps, not {gaps!r}: the "
            f"gap texts stay in the observation")
    if not isinstance(candidate.get("tick"), int) or isinstance(candidate["tick"], bool):
        raise CardDayError(f"a row's `tick` is an ordinal, not {candidate.get('tick')!r}")
    verdict = candidate.get("verdict")
    if not isinstance(verdict, str) or not verdict:
        raise CardDayError(
            f"a row leads with the card's verdict and this one has {verdict!r}. "
            f"A row that leads with nothing is a row the folded view cannot be "
            f"read down")
    return dict(candidate)


def row_says(row_: dict) -> str:
    """One history line, for the fold a person opens.

    The hour leads because the list is read down its left edge, and the states
    are the three words the card's own blocks use — a second vocabulary for the
    same three facts would make the folded view look like another product.
    """
    sections = row_.get("sections") or {}
    states = " · ".join(f"{name}={sections.get(name) or 'UNKNOWN'}"
                        for name in ROW_SECTIONS)
    gaps = row_.get("gaps") or 0
    tail = f" · 采集缺口 {gaps}" if gaps else " · 无采集缺口"
    gaps_tail = f"（{row_.get('observation')}）" if row_.get("observation") else ""
    return f"{row_.get('hhmm')} {row_.get('verdict') or 'UNKNOWN'} · {states}{tail}{gaps_tail}"


# ---------------------------------------------------------------------------
# the write, which is `fold` and one document
# ---------------------------------------------------------------------------

def fold(open_day: dict, row_: dict, *, at: str) -> dict:
    """The previous latest becomes an hour, the new row becomes the latest.

    §11's order is load-bearing — compress first, then write — because a
    document that wrote the new latest first and folded afterwards would lose
    the hour it was replacing if it were interrupted in between, and the hour
    it was replacing is the only copy of what the card said before.
    """
    row_ = check_row(row_)
    day = dict(open_day)
    history = [check_row(r) for r in (day.get("history") or [])]
    latest = day.get("latest")

    if latest is not None and latest.get("tick") == row_["tick"]:
        # Same hour, second look: this hour's row is REPLACED. §11 keeps the
        # history 「按小时」, so appending would give one hour two rows.
        day["latest"] = row_
        day["updated_at"] = at
        day["history"] = history
        return day

    if latest is not None:
        history = (history + [check_row(latest)])[-ticks_per_workday():]
    day.update({"latest": row_, "updated_at": at, "history": history})
    return day


def apply(document: dict, *, action: dict, row_: dict | None = None,
          at: str | None = None) -> dict:
    """The document this decision produces. Pure: the caller writes it.

    A SKIP that arrived with a row is REFUSED rather than ignored. Ignoring it
    would let a caller believe it had recorded an hour the day deliberately did
    not report, and the belief would survive until somebody read the file.
    """
    act = (action or {}).get("action")
    stamp = at or (action or {}).get("at")
    if act not in ACTIONS:
        raise CardDayError(
            f"{act!r} is not one of {', '.join(ACTIONS)} — an action nobody can "
            f"name is an action somebody will read as «no change»")
    if act == SKIP:
        if row_ is not None:
            raise CardDayError(
                "a SKIP carries no history row. This instant was decided NOT to "
                "update the day's card, and recording what the card would have "
                "said is how a day's history grows rows the day never reported")
        return dict(document)

    if row_ is None:
        raise CardDayError(
            f"{act} needs the row it is about: a creation with no row is a card "
            f"with nothing on it, and a patch with no row changes nothing")
    if not stamp:
        raise CardDayError("an update needs the instant it happened")

    next_doc = dict(document)
    open_day = next_doc.get("open")
    if act == CREATE:
        if isinstance(open_day, dict) and open_day.get("day_ref") == action.get("day_ref"):
            raise CardDayError(
                f"{action.get('day_ref')} already has a card. Two creations in "
                f"one day is the second chat message V28 exists to forbid")
        if not action.get("day_ref"):
            raise CardDayError("a creation needs the day it belongs to")
        if isinstance(open_day, dict):
            next_doc["previous"] = {
                "day_ref": open_day.get("day_ref"),
                "kind": open_day.get("kind"),
                "opened_at": open_day.get("opened_at"),
                "closed_at": stamp,
                "updates": len(open_day.get("history") or []) + (
                    1 if open_day.get("latest") else 0),
            }
        next_doc["open"] = {
            "day_ref": action["day_ref"],
            "kind": action.get("day_kind"),
            "opened_at": stamp,
            "updated_at": stamp,
            "missed_ticks": int(action.get("missed_ticks") or 0),
            "latest": check_row(row_),
            "history": [],
        }
        return next_doc

    if not isinstance(open_day, dict) or not open_day.get("day_ref"):
        raise CardDayError(
            "there is no card for today to patch. A patch without a card would "
            "have to invent one, and every field of it — when it was opened, "
            "how many hours it missed — would be invented with it")
    if open_day.get("day_ref") != action.get("day_ref"):
        raise CardDayError(
            f"the open card belongs to {open_day.get('day_ref')} and this patch "
            f"is for {action.get('day_ref')}: patching one day's card with "
            f"another day's hour is how two days become one card")
    next_doc["open"] = fold(open_day, row_, at=stamp)
    return next_doc


def summary(document: dict | None) -> dict:
    """What a card renders, and what a command prints.

    `updates` is derived from the rows rather than stored beside them: a second
    field saying how many hours there are is a second answer, and §10's ceiling
    of thirteen is only checkable against the rows themselves.
    """
    document = document or empty()
    open_day = document.get("open")
    if not isinstance(open_day, dict):
        return {"schema": SCHEMA, "day_ref": None, "updates": 0, "latest": None,
                "history": [], "lines": [], "missed_ticks": 0,
                "previous": document.get("previous"),
                "says": "今天还没有当天的卡"}
    history = [check_row(r) for r in (open_day.get("history") or [])]
    latest = open_day.get("latest")
    updates = len(history) + (1 if latest else 0)
    return {
        "schema": SCHEMA,
        "day_ref": open_day.get("day_ref"),
        "kind": open_day.get("kind"),
        "updates": updates,
        "ticks_per_workday": ticks_per_workday(),
        "missed_ticks": open_day.get("missed_ticks") or 0,
        "opened_at": open_day.get("opened_at"),
        "updated_at": open_day.get("updated_at"),
        "latest": latest,
        "history": history,
        # The folded panel's lines. They travel in the model rather than being
        # formatted by the renderer, because a renderer that knew how to read a
        # row would be a second reader of the row schema.
        "lines": [row_says(r) for r in history],
        "previous": document.get("previous"),
        # The subject is not repeated here: both consumers label this line
        # 「当天卡» / «**当天卡**», and a sentence that carried it too would read
        # 「当天卡 当天卡 CN-Mainland/…» on the card a person is shown.
        "says": (f"{open_day.get('day_ref')} 已更新 {updates}/"
                 f"{ticks_per_workday()} 个时点"
                 + (f"（首个时点在窗口内第 {open_day.get('missed_ticks')} 个之后，"
                    f"未追发）" if open_day.get("missed_ticks") else "")),
    }


# ---------------------------------------------------------------------------
# the document on disk
# ---------------------------------------------------------------------------

class DayStore:
    """Today's card, and the hours it folded. One document, one atomic write.

    An unreadable store is REFUSED rather than replaced: a fresh document would
    make the same workday get a second NEW card, and V28 names that as the
    thing that must not happen. The refusal says so, because the next reader
    will otherwise reasonably assume that starting over is the safe move.
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
            raise CardDayError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start from an empty day: the same workday "
                f"would get a second NEW card, which is what V28 forbids by "
                f"name — losing the memory is not the harm, re-creating the "
                f"card is.") from exc
        if not isinstance(data, dict):
            raise CardDayError(f"{self.path} is not a JSON object")
        if "open" in data and data["open"] is not None and not isinstance(data["open"], dict):
            raise CardDayError(
                f"{self.path} has an `open` that is "
                f"{type(data['open']).__name__}; this document holds at most one "
                f"open day and a second shape for it would be a second meaning")
        open_day = data.get("open")
        if isinstance(open_day, dict):
            if not open_day.get("day_ref"):
                raise CardDayError(
                    f"the open day in {self.path} has no `day_ref`: the identity "
                    f"is the dedupe key, and a card without one would be matched "
                    f"against every other day")
            history = open_day.get("history") or []
            if not isinstance(history, list):
                raise CardDayError(f"the open day in {self.path} has a non-list `history`")
            if len(history) > ticks_per_workday():
                raise CardDayError(
                    f"the open day in {self.path} carries {len(history)} history "
                    f"rows and a workday has {ticks_per_workday()} hours: this "
                    f"document was written by something that did not fold by the "
                    f"hour")
            for item in history:
                check_row(item)
            if open_day.get("latest") is not None:
                check_row(open_day["latest"])
        data["schema"] = SCHEMA
        data.setdefault("previous", None)
        return data

    def read(self) -> dict:
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    def advance(self, *, schedule: dict, at: str, model: dict | None = None) -> dict:
        """This tick, decided and — when the day moves — written down.

        The row is built from the model the caller just rendered, so the history
        says what the card said rather than what a later reading would have
        said. A SKIP reads the model not at all and writes nothing.
        """
        action = decide(self._data, schedule=schedule, at=at)
        if action["action"] == SKIP:
            return {"decision": action, "wrote": False, "written": None,
                    "day": summary(self._data)}
        if not isinstance(model, dict):
            raise CardDayError(
                f"{action['action']} needs the model whose hour this is; without "
                f"it the history would hold a row nobody can trace to a card")
        row_ = row(model, schedule=schedule)
        next_doc = apply(self._data, action=action, row_=row_, at=at)
        written = next_doc["open"]
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"decision": action, "wrote": True, "written": written,
                "row": row_, "day": summary(self._data)}

    def summary(self) -> dict:
        return summary(self._data)


def hhmm_of(schedule: dict) -> str | None:
    """The hour of the calendar's day, for a caller that must show one.

    `routine_window` keeps `hour`/`minute` and the local instant; both are the
    declaration's zone, so this formats the fact rather than a second lookup.
    """
    hour = (schedule or {}).get("hour")
    minute = (schedule or {}).get("minute")
    if hour is None or minute is None:
        return None
    return f"{int(hour):02d}:{int(minute):02d}"


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Answer for one instant, on stdout. Writes nothing and sends nothing."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", default="")
    parser.add_argument("--calendar", default="")
    parser.add_argument("--at", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    at = args.at or datetime.datetime.now(
        datetime.timezone.utc).astimezone().isoformat()
    reading = _calendar().read_declaration(args.calendar or None)
    schedule = _calendar().routine_window(at, reading=reading)
    store = DayStore(args.store or DEFAULT_PATH)
    action = decide(store.read(), schedule=schedule, at=at)
    payload = {"schedule": schedule, "decision": action, "day": store.summary()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(action["says"])
        print(f"{action['action']}/{action['reason']}  day={action['day_ref']}  "
              f"tick={action['tick']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
