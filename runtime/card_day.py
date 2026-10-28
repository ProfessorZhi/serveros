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
answered here without reading a clock and without sending anything: the instants
are parameters, and the one number this module needs from the transport — the
service's own update window — is read through `card_delivery`, which is where
the transport is loaded.

THE OTHER HALF OF 「只 patch 一张」, WHICH IS WHERE THE MESSAGE LIVES
--------------------------------------------------------------------
«Only patch ONE Status Card» has a chat-message half that no document in this
tree held: the decision above says which CARD this instant is about, and nothing
said WHICH MESSAGE carries it. A card created at 08:00 is a message; the patches
at 09:00 and 10:00 have to land on that same message, and a system that does not
remember its id can only send a second one. So `open.message` is where the day's
card remembers the message it went out in, `decide` answers the message half
alongside the card half, and `announce` is the verb that records it.

The verdict is deliberately not "patch if you can, otherwise send". It answers
`PATCH_MESSAGE` only when a message identity is genuinely in hand and still
inside the service's window, and otherwise says `NEW_MESSAGE` WITH the reason it
could not be a patch — a missing identity and an expired one are different
findings and the reader has to be able to tell them apart.

Whether the identity is still usable is the same question an Incident Card asks
about the message carrying an episode, so it is answered ONCE, in
`card_delivery.binding_verdict`, and this module asks it rather than deciding it
again. Two readings of the fourteen-day window would be two chances to read it
wrongly, and the wrong one would be the one promising a patch the transport
refuses. What stays here is what is about a WORKDAY: whether a routine patch is
due at all, and what the reader is told — the same finding is not the same
sentence for a day's card and for an incident.

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
No clock, no render, no send, and no transport of its own: the one number it
needs from the transport is read through `card_delivery`, and this module loads
that for the number and for the shared verdict, and for nothing else. It does
not decide that a card should be sent: it decides what happens to the DAY's
card, and a caller that ignores the answer has ignored a judgement rather than a
suggestion.

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

#: What an announcement did to the binding. Three words rather than a boolean:
#: «this card had no message yet» and «this card's message was replaced» are
#: different events, and a single `changed: true` reports the first one as the
#: second — which reads as «somebody re-sent the card» when nobody did.
BOUND = "BOUND"
UNCHANGED = "UNCHANGED"
REBOUND = "REBOUND"
ANNOUNCE_CHANGES = (BOUND, UNCHANGED, REBOUND)

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
    return _load("serveros_runtime_business_calendar",
                 _HERE / "reports" / "business_calendar.py")


def _delivery():
    """`runtime/card_delivery.py`, for the number and the verdict it owns.

    Loaded under the SAME module name `runtime/cli.py` and `runtime/daemon.py`
    load it as, so this is one copy and not two — and the daemon is the only
    writer of the ledger, so a second copy here would be a second reading of a
    document somebody else is writing. Two things are read from it: the
    service's update window (through `update_window_days`) and the judgment
    about whether a recorded message may still be patched. Both are declarations
    and verdicts over given instants; this module still sends nothing.
    """
    return _load("serveros_runtime_card_delivery", _HERE / "card_delivery.py")


def _registry():
    """`runtime/registry.py`, for the ONE producer of the state root.

    Loaded under the same module name `runtime/cli.py` and `runtime/daemon.py`
    load it as, like every other sibling here. One thing is read from it —
    `state_root()`, which honours `SERVEROS_STATE_DIR` and falls back to the
    path contract — and it is asked rather than recomposed, because a module
    that built the pair itself is how the daemon and the verbs came to disagree
    about where the control store is (§9.48.5).
    """
    return _load("serveros_runtime_registry", _HERE / "registry.py")


def update_window_days() -> int:
    """How long the service lets a sent card be changed. Read, not copied.

    A literal here would be the second place the number lives, and the two would
    disagree the first time the service moved its window — at which point this
    module would promise a patch the transport refuses, or refuse one it would
    have accepted.
    """
    return _delivery().update_window_days()


# ---------------------------------------------------------------------------
# the message half's vocabulary
# ---------------------------------------------------------------------------
# Down here rather than with the other closed sets because two of these words
# are not this file's: a card's receipt and a card's verdict are written in the
# same words in `card_delivery`, and a reader that branched on a spelling this
# file invented would not recognise the one the ledger writes.

#: The message half of the same decision: what happens to the chat message that
#: SHOWS the card. Closed set for the same reason `ACTIONS` is — a caller
#: branching on a third spelling of "send it" is a caller that will send twice.
NEW_MESSAGE = _delivery().NEW_MESSAGE
PATCH_MESSAGE = _delivery().PATCH_MESSAGE
#: This one is this file's own: «this instant has no message to send» is a
#: thing only a routine broadcast can say — an incident card has no such hour.
NO_MESSAGE = "NO_MESSAGE"
MESSAGE_ACTIONS = (NEW_MESSAGE, PATCH_MESSAGE, NO_MESSAGE)

#: Why the message half answered what it did. «SEND, and the reason is that we
#: never recorded an id» and «SEND, and the reason is that the card is brand
#: new» are different findings; a single SEND would hide the first one, and the
#: first one is the missing link this whole section exists to close.
#:
#: NEVER_SENT and NOT_ROUTINE are this file's; the three IDENTITY_* words are
#: `card_delivery`'s, because the verdict that produces them is answered there.
NEVER_SENT = _delivery().NEVER_SENT
IN_HAND = _delivery().IN_HAND
IDENTITY_MISSING = _delivery().IDENTITY_MISSING
IDENTITY_EXPIRED = _delivery().IDENTITY_EXPIRED
IDENTITY_UNDATED = _delivery().IDENTITY_UNDATED
NOT_ROUTINE = "NOT_ROUTINE"
MESSAGE_REASONS = (NEVER_SENT, IN_HAND, IDENTITY_MISSING, IDENTITY_EXPIRED,
                   IDENTITY_UNDATED, NOT_ROUTINE)


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


def default_path() -> Path:
    """Where the day's memory sits when the caller names no store.

    The SAME computation as `day_path`, over the file the ledger's own directory
    is already anchored on: `runtime/daemon.py#card_ledger` builds
    `<state_dir>/alerts/state.json` and hands it to `card_delivery.ledger_path`,
    and this hands the same file to the sibling function. So on a node the day's
    card and the receipt ledger are one directory apart by construction, which is
    what the module's own first sentence claims and what a reader checking
    "which message carries today's card" needs to be true.

    It used to be `~/.serveros/cards/day.json`, and that was wrong for a reason
    written down where it can be checked rather than argued:
    `fleet/node-root-contract.yaml` declares `~/.serveros/` to hold three named
    files and states that "large state does not live here" — an entry of that
    root can be deleted and re-derived. A day's card cannot: losing the record of
    which message carries it is what makes the next tick create the second card
    V28 forbids (`card day-advance` refuses to replace an unreadable store at
    all, for exactly this reason). A document whose loss is the harm does not
    belong in a root whose entries are all disposable. The ledger never was
    there; only this document was, and that is how one feature's two documents
    came to have two answers to "where does a card live".

    Asked at CALL time rather than frozen into a constant, because the answer is
    a function of the environment `registry.state_root` reads — the same reason
    `cli._agent_control_store_path` asks it per call.
    """
    return day_path(_registry().state_root() / "runtime" / "alerts" / "state.json")


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
        # The message half, attached at the single point every answer leaves
        # through. It is computed from the answer and not passed in, so no
        # return path can forget it and leave a caller to assume the card's
        # message is somebody else's question.
        base["message"] = message_verdict(action, document.get("open"), at=at)
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


def message_verdict(action: str, open_day, *, at: str) -> dict:
    """What happens to the message carrying today's card, and why.

    The companion of the card decision, never a substitute for it: `action` is
    what `decide` answered about the CARD, and this answers about the chat
    message that shows it. It exists because «只 patch 一张» is unenforceable
    from the card alone — the patches have to land on the same message, and only
    a recorded identity makes that possible.

    PATCH_MESSAGE is answered only when a message identity is genuinely in hand
    AND still inside the service's window at this instant. Every other answer is
    NEW_MESSAGE, and each carries the reason it is not a patch, because «we
    never recorded an id» and «the id is too old to use» need different fixes.
    The age is `at` minus the recorded send time — this instant against that
    one, both given — so nothing here reads a clock.

    WHICH of those answers holds is not decided here. «Is the message this card
    remembers still changeable at `at`» is the same question an Incident Card
    asks about the message carrying an episode, and `card_delivery` answers it
    once for both; this function asks and then says it in the day's own words.
    What is left here is the part that is about a WORKDAY: whether a routine
    patch is due at all, and the sentences — the same finding is not the same
    sentence for a day's card and for an incident.
    """
    window = update_window_days()
    verdict = {"action": None, "reason": None, "remote_id": None,
               "window_days": window, "says": ""}

    if action == SKIP:
        verdict.update({
            "action": NO_MESSAGE, "reason": NOT_ROUTINE,
            "says": "这一拍不动当天卡，也就没有要发的消息"})
        return verdict

    if action == CREATE:
        # By construction: the document this instant produces has no message
        # recorded yet, because the message does not exist until somebody sends
        # this card.
        verdict.update({
            "action": NEW_MESSAGE, "reason": NEVER_SENT,
            "says": ("这是一张新建的当天卡，还没有消息承载它：本次新发一条，"
                     "从下一拍起都是 patch 这一条")})
        return verdict

    judged = _delivery().binding_verdict(
        open_day.get("message") if isinstance(open_day, dict) else None,
        at=at, window_days=window)
    remote_id = judged["remote_id"]

    if judged["reason"] == IDENTITY_MISSING:
        # What this document does NOT know, and must not pretend to: whether the
        # card ever went out. If it did, sending now makes a second card; if it
        # did not, sending now is exactly right. Only the delivery ledger can
        # tell those apart, so the sentence names it instead of picking one.
        verdict.update({
            "action": NEW_MESSAGE, "reason": IDENTITY_MISSING,
            "remote_id": remote_id,
            "says": ("当天卡已经在，但没有记下承载它的那条消息，因此无从 patch。"
                     "如果它已经发出过，这时新发一条就是第二张卡（V28 只允许"
                     "一张）；如果还没有发出过，那就还没有消息。"
                     "这件事只有投递台账知道（`card deliveries`），"
                     "这一份文档只能如实说它没有记下是哪一条；"
                     "发出后用 `card day-announce --message-id` 记下即可")})
        return verdict

    if judged["reason"] == IDENTITY_UNDATED:
        verdict.update({
            "action": NEW_MESSAGE, "reason": IDENTITY_UNDATED,
            "remote_id": remote_id,
            "says": (f"记下了承载卡的那条消息，但它的发出时间（或这一刻）"
                     f"读不出来，{window} 天可改窗口因此无法判断。"
                     f"无法判断就不声称可以 patch，也不在这里改发一条新的")})
        return verdict

    if judged["reason"] == IDENTITY_EXPIRED:
        verdict.update({
            "action": NEW_MESSAGE, "reason": IDENTITY_EXPIRED,
            "remote_id": remote_id,
            "says": (f"承载当天卡的那条消息已经发出约 "
                     f"{int(judged['age_days'])} 天，超过 {window} 天可改窗口，"
                     f"改不动了；只能新发一条，而那意味着第二张卡")})
        return verdict

    if judged["reason"] == IN_HAND:
        # Two sentences rather than one because «-0.0 天前发出» is what a
        # backdated instant produces, and a reading nobody can place on a
        # timeline is worse than no reading: what the caller needs from this
        # line is whether the window still holds, and that is answerable in
        # both cases.
        age_days = judged["age_days"]
        since = (f"发出约 {age_days:.1f} 天" if age_days > 0
                 else "记下的发出时间不早于这一拍")
        verdict.update({
            "action": PATCH_MESSAGE, "reason": IN_HAND, "remote_id": remote_id,
            "says": (f"承载当天卡的消息在手（{since}，在 {window} 天可改窗口内）："
                     f"本次 patch 这一条，不新发")})
        return verdict

    # Unreachable through the four reasons above, and refused rather than
    # answered: falling through would mean answering «patch it» about a reason
    # this file cannot read, which is the one wrong answer this verdict exists
    # to avoid.
    raise CardDayError(
        f"`binding_verdict` answered {judged['reason']!r}, which is not one of "
        f"the four reasons it declares; this verdict has no sentence for it and "
        f"will not fall through to a patch")


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
    return _states_says(row_, head=str(row_.get("hhmm")))


def _states_says(row_: dict, *, head: str) -> str:
    """The states of one row, under a caller-chosen leading label.

    Split out of `row_says` so a steady RUN can print the same body under an
    hour RANGE instead of a single hour — one composition of these words, so an
    interval and an hour cannot describe the same state in two vocabularies.

    The observation word is not printed. It said `（新鲜）` after `无采集缺口`
    and `（部分新鲜）` after `采集缺口 2` — the same fact twice in one line, on
    every line of a day's history. §1's screenshot names that kind of repeat
    (重复说明) and §M4's 「去scope英文key» is the same instruction one level
    down: the line carries the reading and not the name of the reading.
    """
    sections = row_.get("sections") or {}
    states = " · ".join(f"{BLOCK_NAMES.get(name, name)}"
                        f"={_state_word(sections.get(name))}"
                        for name in ROW_SECTIONS)
    gaps = row_.get("gaps") or 0
    tail = f" · 采集缺口 {gaps}" if gaps else " · 无采集缺口"
    return f"{head} {_state_word(row_.get('verdict'))} · {states}{tail}"


# ---------------------------------------------------------------------------
# the timeline: the day as intervals, with what did not get observed kept in
# ---------------------------------------------------------------------------

#: The axes two hours are compared on. Named here so that a row that grows a
#: field becomes a decision about this list rather than a change in what the
#: timeline quietly compares.
COMPARED = ("verdict", "observation", "gaps")

#: The words this module prints, spelled out rather than imported. It is loaded
#: by path before the renderer exists (`_load`), and a copy taken at import time
#: would be a copy either way — so `tests/test_card_day.py` asserts these three
#: maps agree with `cards`' rather than a comment promising they do.
#:
#: The four summary states are `cards.FACE_WORDS`; `UNKNOWN` is the fifth
#: because a block that was not read has no verdict and this module still has to
#: name it — as 无法确认 and never as a green word, for the reason
#: `cards.NEUTRAL_WORD` gives. The three faulted block states are
#: `presentation.env_health`'s own list rather than a guess at one.
STATE_WORDS = {
    "OK": "正常",
    "ATTENTION": "建议查看",
    "CRITICAL": "需要处理",
    "UNCONFIRMED": "无法确认",
    "UNKNOWN": "无法确认",
    "DEGRADED": "降级",
    "NOT_READY": "未就绪",
    "DOWN": "不可用",
}

#: The gateway's two words for the case where it has no reading to give at all.
#: `presentation.GATEWAY_UNBOUND` is an established absence and
#: `cards._EMPTY_LEAD_UNBOUND` names it in full; this map is the same name.
GATEWAY_WORDS = {"UNBOUND": "未配置监控绑定", "BOUND": "已绑定"}

#: The blocks and the axes, under the names a person reads. `mine` / `host` /
#: `gateway` are the model's keys and `verdict` / `observation` / `gaps` are its
#: column names; a line printing either is the card showing a reader its schema.
BLOCK_NAMES = {"mine": "我的资源", "host": "整机", "gateway": "AI Gateway"}
AXIS_NAMES = {"verdict": "整体状态", "observation": "观测", "gaps": "采集缺口"}


def _state_word(value) -> str:
    """One state, in the vocabulary a person reads.

    A state this map has not been taught is printed as itself rather than
    guessed at or dropped: an unrecognised reading is not a good reading, and it
    is not a reason to print nothing either.
    """
    if value is None:
        return STATE_WORDS["UNKNOWN"]
    text = str(value)
    return STATE_WORDS.get(text) or GATEWAY_WORDS.get(text) or text


def changes_between(previous: dict | None, row_: dict) -> list[str]:
    """What moved between two hours, in the vocabulary a person reads.

    Returns the axes that differ, each as `<block or axis> <before>→<after>`,
    and an empty list when nothing moved. The three section words are compared
    one by one rather than as a block, so a card can say WHICH block changed
    instead of «something did» — with three blocks that is the difference
    between a line a reader acts on and a line that sends them hunting.

    THE ROLL-UP IS NOT A SECOND CHANGE. `verdict` is a function of the block
    states and the gap count (`presentation.env_health` builds it from exactly
    those and nothing else), and `observation` is a function of the same two.
    Printing them beside the block that moved reports one fact twice —
    «整体状态 正常→建议查看；整机 正常→建议查看» — and a reader counting two
    changes would be counting one. So the specific axes are collected first and
    the derived ones are reached only when nothing specific moved, which is why
    the two loops below are an if/else and not one pass in `COMPARED` order.

    THE WORDS ARE THE CARD'S, NOT THE MODEL'S. This returned
    `verdict OK→ATTENTION` / `mine OK→ATTENTION` — the model's column names and
    the enumerations behind them — and a card that prints either is showing a
    reader its own schema, which §2 names among the things that must not reach
    the first screen and CARDS-STYLE §M4 names again as 「去scope英文key».
    `row_says` had the same defect one line down and is fixed with it, so the
    face and the fold cannot come out as two vocabularies for one block.

    An axis absent from `previous` is not a change. That is the first hour of a
    day, and reporting `None → OK` would print a transition into the picture the
    day opened with.
    """
    named = named_changes(previous, row_)
    return named if named else derived_changes(previous, row_)


def named_changes(previous: dict | None, row_: dict) -> list[str]:
    """The changes that name a block, or the gap count, and nothing else.

    WHY THIS IS SEPARATE FROM `derived_changes`
    -------------------------------------------
    The status card's first screen prints its delta as one clause, and §2 caps
    that screen at ten to twelve lines. A block that moved is a change a reader
    acts on; `整体状态 正常→建议查看` is the same fact restated one level up, and
    it is what `changes_between` already refuses to print beside the block that
    moved. When nothing specific moved, the derived spelling is the ONLY one
    available and it is still worth a line — that is `derived_changes`, and it
    is what the card falls back to. The two are separate functions so the card
    can decline the fallback on its face without a second implementation of
    «which axes moved» existing anywhere.
    """
    if previous is None:
        return []
    specific: list[str] = []
    before_sections = previous.get("sections") or {}
    after_sections = row_.get("sections") or {}
    for name in ROW_SECTIONS:
        before, after = before_sections.get(name), after_sections.get(name)
        if before != after:
            specific.append(f"{BLOCK_NAMES.get(name, name)} "
                            f"{_state_word(before)}→{_state_word(after)}")
    if previous.get("gaps") != row_.get("gaps"):
        specific.append(f"{AXIS_NAMES['gaps']} "
                        f"{previous.get('gaps') or 0}→{row_.get('gaps') or 0} 项")
    return specific


def derived_changes(previous: dict | None, row_: dict) -> list[str]:
    """The roll-up axes that moved, reached only when no block did.

    Every one of these is a function of the block states and the gap count, so
    printing one beside the block it is derived from reports a single fact
    twice. They exist for the case where nothing more specific moved — the
    verdict can move because the freshness did — and a caller that shows them
    is saying «something moved and it was not a block».
    """
    if previous is None:
        return []
    moved: list[str] = []
    for axis in COMPARED:
        before, after = previous.get(axis), row_.get(axis)
        if before == after:
            continue
        name = AXIS_NAMES.get(axis, axis)
        if axis == "gaps":
            moved.append(f"{name} {before or 0}→{after or 0} 项")
        else:
            moved.append(f"{name} {_state_word(before)}→{_state_word(after)}")
    return moved


def timeline(rows: list[dict]) -> list[dict]:
    """The day as a series of intervals, oldest first.

    WHY THE GAPS ARE ENTRIES AND NOT A COUNT
    ----------------------------------------
    A fold that printed one line per recorded hour and a running count of
    missing ones would compress the two things a reader must tell apart. «The
    state held steady from 16:00 to 17:00» and «16:00 to 17:00 was never looked
    at» produce the same single row in a list of observations, and they are
    opposite claims: one says the machine was fine and the other says nobody
    knows. So a stretch with no reading is its own entry, and it is an INTERVAL
    — it runs between the hour before it and the hour after it, which is why it
    sits between them in the list rather than in a summary line at the bottom.

    A transition is a boundary for the same reason read the other way: two
    adjacent hours that disagree end one interval and begin the next. So the
    list is a sequence of runs — each entry says from which hour to which hour
    one picture held, and says what ended it.

    `rows` is oldest-first, and the current hour is its last element or absent.
    An empty list is an empty day and returns an empty list; this function
    invents no opening entry for a day that has not been opened.

    STEADY HOURS ARE ONE ENTRY AND NOT N
    ------------------------------------
    This is what makes the list a list of RUNS rather than a list of hours, and
    it was the gap between this docstring and its code. Eleven hours that did not
    move were printed as eleven lines reading «与上一时点相同», which is eleven
    statements of one fact and the shape the previous card's history had. §6's
    acceptance is that the day is read at a glance; a reader cannot see the shape
    of a day through a column of identical rows, and the fold's own title — «12
   个时点 · 0 处变化» — was already answering the question the eleven lines were
    asking eleven more times.
    """
    entries: list[dict] = []
    previous: dict | None = None
    #: The hours since the last thing that cut a segment. Emitted as one entry
    #: when something cuts it — a transition, a gap, or the end of the day.
    run: list[dict] = []

    def cut() -> None:
        """Close the open steady run, if there is one."""
        if not run:
            return
        first, last = run[0], run[-1]
        entries.append({
            "kind": "steady",
            "ticks": len(run),
            "from_hhmm": first.get("hhmm"),
            "to_hhmm": last.get("hhmm"),
            "hhmm": last.get("hhmm"),
            "verdict": last.get("verdict"),
            "observation": last.get("observation"),
            "sections": last.get("sections"),
            "gaps": last.get("gaps"),
            "changes": [],
            "says": _steady_says(first, last, len(run)),
        })
        run.clear()

    for index, row_ in enumerate(rows):
        # The stretch nobody observed, emitted BEFORE the hour that follows it,
        # so the list reads in the order it happened. It is a boundary like any
        # other: the hours before it are one run and the hours after it are
        # another, and merging across it would be the compression this function
        # exists to refuse.
        if previous is not None:
            missing = row_["tick"] - previous["tick"] - 1
            if missing > 0:
                cut()
                entries.append({
                    "kind": "gap",
                    "ticks": missing,
                    "from_hhmm": previous.get("hhmm"),
                    "to_hhmm": row_.get("hhmm"),
                    "says": (f"{previous.get('hhmm')}–{row_.get('hhmm')} "
                             f"未有观测：{missing} 个时点没有读数"
                             f"（不是「无变化」，是没人看过）"),
                })
        moved = changes_between(previous, row_)
        if index == 0 or moved:
            # The day's first hour is not a transition — `changes_between`
            # returns nothing against `None` for exactly that reason — so it is
            # its own kind and it opens the list.
            cut()
            entries.append({
                "kind": "open" if index == 0 else "transition",
                "tick": row_["tick"],
                "hhmm": row_.get("hhmm"),
                "verdict": row_.get("verdict"),
                "observation": row_.get("observation"),
                "sections": row_.get("sections"),
                "gaps": row_.get("gaps"),
                "changes": moved,
                "says": _timeline_says(row_, moved, open_=index == 0),
            })
        else:
            run.append(row_)
        previous = row_
    cut()
    return entries


def _steady_says(first: dict, last: dict, count: int) -> str:
    """One interval of hours that did not move, as the line a person reads.

    `count == 1` keeps the single-hour wording, because there the interval IS
    one hour and a range reading «14:00–14:00» would be a worse way to say
    «14:00». Above that the hour becomes a range and the tail says how many
    readings held it — the two facts a reader cannot get from a single body.
    """
    if count == 1:
        return f"{row_says(last)} · 与上一时点相同"
    head = f"{first.get('hhmm')}–{last.get('hhmm')}"
    return f"{_states_says(last, head=head)} · {count} 个时点无变化"


def _timeline_says(row_: dict, moved: list[str], *, open_: bool) -> str:
    """One timeline entry, as the line a person reads.

    `row_says` and this share the hour-first shape on purpose — the fold used to
    be a list of hours and is now a list of runs, and a reader who knew the old
    one should not have to learn a second reading order for the same day. What
    is added is the `→` clause, and it is present exactly when something moved.
    """
    base = row_says(row_)
    if open_:
        return f"{base} · 建卡"
    if not moved:
        # «无变化» is a claim about two hours that were both read. It never
        # stands where a gap does, and the gap entry above says so in its own
        # words — that separation is the point of the kind field.
        return f"{base} · 与上一时点相同"
    return f"{base} · 变化 " + "；".join(moved)


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
                "history": [], "timeline": [], "transitions": 0, "unobserved": 0,
                "missed_ticks": 0, "message": None,
                "previous": document.get("previous"),
                "says": "今天还没有当天的卡"}
    history = [check_row(r) for r in (open_day.get("history") or [])]
    latest = open_day.get("latest")
    updates = len(history) + (1 if latest else 0)
    # The whole day, current hour included, and the current hour is IN the list
    # rather than only on the card's face. The gap that matters most is the one
    # immediately before now — a fold that stopped at the last folded hour would
    # draw the timeline up to the moment the reading stopped and leave a reader
    # believing the day continues unbroken into it.
    rows = history + ([latest] if latest else [])
    entries = timeline(rows)
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
        # Which message carries this card, or `None` when nobody has recorded
        # one. A reader needs the second case as much as the first: it is the
        # difference between «the next update patches the message that is
        # already there» and «the next update can only be a second card».
        "message": open_day.get("message"),
        # The day as intervals — transitions, steady stretches and the ones
        # nobody observed, in the order they happened. Built here rather than by
        # the renderer, because a renderer that knew how to read a row would be a
        # second reader of the row schema, and a renderer that knew how to
        # compare two rows would be a second author of «what counts as a change».
        "timeline": entries,
        # Transitions only. The panel's title counts them, and counting them
        # here keeps the number and the list from drifting apart — a count taken
        # by the renderer would be a second read of the same list.
        "transitions": sum(1 for e in entries if e["kind"] == "transition"),
        "unobserved": sum(1 for e in entries if e["kind"] == "gap"),
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
            message = open_day.get("message")
            if message is not None:
                if not isinstance(message, dict) \
                        or not str(message.get("remote_id") or ""):
                    raise CardDayError(
                        f"the open day in {self.path} has a `message` that names "
                        f"no message. A binding to nothing would let the next "
                        f"update believe it can patch the card, when the only "
                        f"message it could reach is whichever one it guessed")
                if not str(message.get("at") or ""):
                    raise CardDayError(
                        f"the message in {self.path} carries no `at`. The "
                        f"service's update window is measured from when a message "
                        f"was sent, and one that cannot be placed on a timeline "
                        f"makes that window unanswerable — which is a reason to "
                        f"refuse, not a reason to assume it is fresh")
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

    def announce(self, *, remote_id: str, at: str) -> dict:
        """Record which message carries today's card. The second writer.

        Split from `advance` rather than folded into it because the two facts
        arrive at different times: the decision about the card is computable
        from the calendar, and the message id only exists AFTER somebody sent
        it. A verb that took both would have to be called twice anyway, once
        with a blank.

        Rebinding is allowed — a re-sent card lives in a new message, and the id
        that is no longer current must not stay in place or every later update
        would patch a message the receiver is not looking at. What is NOT lost
        by rebinding is the old one: the delivery ledger holds it against the
        version that went out (that is what a receipt's `remote_id` is for), so
        this document keeps only the binding that is current.
        """
        message_id = str(remote_id or "").strip()
        if not message_id:
            raise CardDayError(
                "an announcement needs the message it is about. Recording «a "
                "message» without an identity is the state this verb exists to "
                "end: the next update would have to guess, and a guess that "
                "reaches the wrong message edits somebody else's card")
        if any(character.isspace() for character in message_id):
            raise CardDayError(
                "a message id is one token, and this one contains whitespace, "
                "so it names no message. The value is not repeated here")
        if message_id.startswith("oc_"):
            raise CardDayError(
                "that is a chat id, not a message id: `oc_` names a "
                "conversation and `om_` names one message in it. Binding the "
                "conversation would aim every later patch at a chat")
        if not str(at or ""):
            raise CardDayError(
                "an announcement needs the instant it was made: the service's "
                "update window is measured from the message's own send time, and "
                "an instant nobody wrote down cannot be measured later")
        open_day = self._data.get("open")
        if not isinstance(open_day, dict) or not open_day.get("day_ref"):
            raise CardDayError(
                "there is no card for this message to carry. Announcing one "
                "would file a message under a day that has no card, and the "
                "card created afterwards would open beside a binding that "
                "already claims to carry it")
        previous = open_day.get("message")
        held = str(previous.get("remote_id") or "") if isinstance(previous, dict) else ""
        if not held:
            change = BOUND
            message = {"remote_id": message_id, "at": str(at)}
        elif held == message_id:
            change = UNCHANGED
            # The first binding wins the timestamp. Re-stamping an unchanged
            # message with a later instant would push the service's window
            # forward every time somebody re-ran the announcement, and that
            # window is a property of when the message was SENT.
            message = dict(previous)
        else:
            change = REBOUND
            message = {"remote_id": message_id, "at": str(at)}
        next_doc = dict(self._data)
        next_doc["open"] = dict(open_day)
        next_doc["open"]["message"] = message
        _store_io()._atomic_write(self.path, next_doc)
        self._data = next_doc
        return {"announced": True, "change": change, "message": message,
                "day_ref": open_day.get("day_ref"), "day": summary(self._data)}

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
    store = DayStore(args.store or default_path())
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
