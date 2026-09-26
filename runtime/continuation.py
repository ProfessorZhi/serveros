"""What a logical Parent leaves behind when it has to wait, and who wakes it.

WHY THIS MODULE EXISTS, and why it is not a daemon. A Parent that has to wait for
a child, a dependency or a window must be able to stop — a process that blocks
holds a session, a lease and a slot for the length of someone else's work — and
the fabric must be able to say, afterwards, where that Parent had got to. Left as
prose that is a promise; left as a chat transcript it is not recoverable state.

The product names both halves and both are quoted here rather than paraphrased:

  `docs/context-capsule.md` — a Parent checkpoint contains «the accepted
  Requirement revision, required/remaining results, accepted evidence locators,
  actual child attempts and unresolved effects, write scope and the next safe
  action», and it is «recoverable work state, **not a transcript summary or a new
  database**».

  `docs/daily-work-cycle.md` §17.4 — «没有 receiver 时准确显示"checkpoint已保存，
  待续"，不能伪造后台运行».

The first quotation is why `CHECKPOINT_FIELDS` below is closed and why the only
free-text field is one bounded sentence: a closed field set is how a transcript is
made unable to arrive, the same way a closed parameter set is how an argv is made
unable to arrive in the control contract.

The second is why `continuation_state` takes the receiver list as an ARGUMENT
rather than reading it off the record. A record that could name its own receiver
would be a record that can claim a background run — and the thing this module is
most likely to get wrong is exactly that claim, because it costs nothing to write
and the person reading it has no way to tell. So no field of a checkpoint says who
will wake it; the answer comes from the fabric, and `receivers()` below is the one
place in this release that knows.

WHAT RECEIVERS() ANSWERS, AND WHO FILLS IT. `runtime/wake.py` is the wake half,
and `wake.fill_registry` is the only writer of the name below. It fills it by
MEASURING this node — is `serveros-continuation-wake.timer` in the wanted set,
and does its service name a Parent to start — because a registry written from a
declaration would be a registry that could claim a background run that nobody
installed, and that claim is the one thing §17.4 forbids from the other side.
Both halves are required: a timer with no Parent fires and wakes nothing, and a
Parent with no timer is never reached.

So the empty tuple is still a measurement, and it is the one that holds on a node
where the routine is not installed — which is every node until an operator runs
`scripts/release.py continuation-wake` and arms it. The honest consequence there
is the product's own sentence: a checkpoint is saved and awaiting continuation,
and a reader that said anything else would be reporting a background run nobody
started.

AND IT IS STILL NOT A TICK. `runtime/daemon.py`'s tick reads neither admissions
nor checkpoints, and that has not changed — deliberately. The daemon's loop
evaluates detectors, and a loop that also started session-shaped work would make
every tick a possible writer of authority. The wake is a bounded oneshot on its
own timer, and it carries a delegation rather than minting one.

AND AN EVALUATOR IS NOT A RECEIVER, which is why `wait_state` does not fill it.
`wait_state` below answers a different question from «will something wake this»:
it answers «has the thing this checkpoint waits for happened yet», from the
admissions collection and the clock. That answer is what a NEW Parent reads to
decide whether resuming now is possible — and resuming is, and has always been,
what a new Parent does (`cmd_work_continuations`: «Resuming is what `work
continuations` and a new Parent do»). An evaluator that can say «the child has a
recorded outcome» must not be allowed to make `continuation_state` start saying a
receiver is installed, and the reason did not change when the registry got a
writer: whether a unit is installed is a fact about a unit directory, and an
evaluator reading a store cannot see one. The two are kept apart on purpose and
`tests/test_continuation.py` pins the separation.

AND A READING OF ONE RECORD IS NOT A READING OF THE ITEM. `attempts_state` below
answers «what became of the attempts this record names», which is a question
about a record. `carried_attempts` answers «what does this Item still owe from
the epochs it came through», which is a question about the Item — and the store's
own sentence is that the ITEM is the scope and the epoch is only how it changes
hands. So a Parent that replaces another writes a record that legitimately names
no attempt, and the reading a person needs is not a field of it: the child the
replaced Parent dispatched is the Item's obligation under the new epoch. The two
readings are kept apart because neither is the other's summary — an attempt is
reported by exactly one of them, and a reader adding the two counts together
counts nothing twice.

WHAT THIS MODULE DOES NOT DO. It does not decide that a Parent should wait. The
caller states what it is waiting on and this module refuses the shapes that are
not a wait; whether the work could simply have gone on is a question about the
task, and no function here can answer it. `WAIT_KINDS` is closed for the same
reason `CHECKPOINT_FIELDS` is: an open kind list is a kind that means nothing.
"""
from __future__ import annotations

import datetime

#: The fields a checkpoint may carry — the capsule's list, and nothing else.
#:
#: Closed on purpose. A field a caller can add is a field a caller will use to put
#: a transcript in, and the product's word for this object is the opposite of a
#: transcript. `next_safe_action` is the one free-text field and `build_checkpoint`
#: bounds it.
#:
#: These are the fields a CALLER supplies. `build_checkpoint` adds two it derives —
#: `checkpoint_ref` and `written_at` — and they are not on this list because a
#: caller that could name its own key could file a checkpoint over another
#: Parent's, which is the one write this record exists to make impossible.
CHECKPOINT_FIELDS = (
    "work_item",
    "epoch",
    "parent_owner",
    "requirement_revision",
    "remaining_results",
    "evidence_locators",
    "child_attempts",
    "unresolved_effects",
    "write_scope",
    "next_safe_action",
    "waiting_on",
)

#: The ways a Parent can be genuinely waiting, and there are two.
#:
#: `child` — a delegation this store holds an admission for. The reference is
#:   checked against the admissions collection, so «waiting on a child» is a
#:   statement this fabric can confirm rather than one a caller can assert. A
#:   child this store does not hold is refused: `docs/SCENARIO-ACCEPTANCE.md`
#:   names «要求用户复制结果» as the failure, and a wait on something ServerOS
#:   cannot see is how that failure starts.
#:
#: `window` — an instant. «唤醒条件/到期策略» in
#:   `docs/COLLABORATION-CONTRACT.md`'s `continuation` row is this and the child
#:   case, and a window is the one that names an expiry rather than an event.
#:
#: A THIRD KIND IS DELIBERATELY ABSENT. A dependency on another Work Item is a
#: real way for work to wait, and it is not here because this module would have to
#: take a caller's word for it — there is no store on this node that says which
#: Items an Item depends on. Adding a kind nothing can check would make the list
#: longer and the record weaker, so the absence is stated here instead.
WAIT_KINDS = ("child", "window")

#: One sentence, so one line. Long enough for a real instruction and too short
#: for a transcript.
MAX_NEXT_ACTION = 400

#: The three answers to «has the thing this checkpoint waits for happened yet».
#:
#: Three and not a boolean, because the middle one is the point: a store that
#: cannot answer must not be made to answer «over». Collapsing an unreadable
#: child into «not yet» would park a Parent forever behind a store problem, and
#: collapsing it into «over» would resume one on no evidence — the second is the
#: failure this vocabulary exists to make impossible to express.
WAIT_ANSWERS = ("WAIT_OVER", "WAIT_OPEN", "WAIT_UNANSWERABLE")

#: The continuation triggers installed on this node. See the module docstring.
#:
#: Written by `runtime/wake.py#fill_registry` and by nothing else, and filled
#: only by MEASURING this node's unit directory. `cmd_work_continuations` and
#: `cmd_work_wake` both call it before they read `receivers()`, so the two
#: surfaces answer from one measurement rather than from this import-time value.
#: The empty tuple is what an uninstalled node reports, and that is the state
#: every node starts in.
RECEIVERS: tuple = ()

#: The five answers to «what became of the attempts this checkpoint records».
#:
#: Four of them are about one attempt, and the fifth is about a record that names
#: none — which is a fact `build_checkpoint` allows and which must not be printed
#: as any of the others. `ATTEMPT_UNANSWERABLE` is not «not finished»: it is «this
#: store cannot say», kept apart for the reason `WAIT_ANSWERS` gives one screen
#: down. `ATTEMPT_ACCEPTED` and `ATTEMPT_RETIRED` are the two answers that would
#: otherwise be one: both mean the attempt finished, and they differ in whether
#: anything says its result was taken into the Item.
#:
#: The TUPLE's order is «how finished, then the two odd ones», which is not the
#: order the record-level answer is decided in. `attempts_state` decides most
#: cautiously first (a store it cannot read outranks an open attempt, and an open
#: attempt outranks a finished one), so a caller that wants the precedence reads
#: that function and not this tuple.
ATTEMPT_ANSWERS = ("ATTEMPT_ACCEPTED", "ATTEMPT_RETIRED", "ATTEMPT_OPEN",
                   "ATTEMPT_UNANSWERABLE", "ATTEMPT_NONE")

#: The three answers to «what does this Item's continuation still owe from the
#: epochs it came through».
#:
#: A THIRD VOCABULARY BECAUSE IT IS A THIRD QUESTION. `ATTEMPT_ANSWERS` is about
#: one record's own field; this is about the Item, whose scope the store says
#: outlives the epoch that happened to hold it. A reading that answered the
#: second question with the first vocabulary would have to say `ATTEMPT_NONE`
#: about an Item with a child still running — which is a true statement about the
#: record and a false one about the work.
#:
#: `ATTEMPTS_CARRIED_UNANSWERABLE` IS FIRST AND IS THE SAME RULE AS
#: `ATTEMPTS_UNANSWERABLE` one tuple up: a store this reading cannot open outranks
#: an attempt it can see is still running, because the answer has to be the one a
#: reader can act on and «an epoch I cannot read may have left something behind»
#: is not «nothing is outstanding».
CARRIED_ANSWERS = ("ATTEMPTS_CARRIED_UNANSWERABLE", "ATTEMPTS_CARRIED",
                   "NOTHING_CARRIED")


def receivers() -> tuple:
    """The continuation receivers that could wake a saved checkpoint.

    The single reader of `RECEIVERS`, so that the wake half has one place to fill
    and `continuation_state` has one place to ask. Returning the tuple rather than
    the name keeps a caller from appending to a registry it does not own.
    """
    return RECEIVERS


def checkpoint_key(work_item: str, epoch: int) -> str:
    """The key one Parent's checkpoint is filed under.

    `work_item` and the epoch, and NOT the owner. The scope a continuation is
    unique within is the Item at one epoch of itself; the owner is a field, so
    that two Parents claiming the same scope is a readable conflict rather than
    two records that silently coexist.
    """
    return f"{work_item}#{int(epoch)}"


def _instant(value) -> datetime.datetime | None:
    """One ISO instant, or None if the value is not one.

    The same tolerance `runtime/cli.py`'s receipt comparison uses, and for the
    same reason: every writer in this tree writes UTC with an offset, so a naive
    instant is read as UTC rather than refused, and a value that is not an instant
    at all answers None because the caller's question is a comparison.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment


def _is_epoch(value) -> bool:
    """One epoch: a positive integer, and NOT a bool.

    `isinstance(True, int)` is True, so `epoch=True` would otherwise be epoch 1 —
    the one value a caller arrives at by accident rather than by intent. This is
    `build_checkpoint`'s refusal written once and read in two places, because the
    reading below orders records by epoch and an ordering that accepted a bool
    would sort a record that named no epoch at all.
    """
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 1)


def _as_list(value, field: str, why: str) -> list[str]:
    """One field's value as a list of non-empty strings.

    A scalar is accepted and wrapped: «one locator» and «a list holding one
    locator» are the same statement, and refusing the scalar would make a caller
    learn a spelling rather than a rule.
    """
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out = []
    for entry in items:
        text = str(entry).strip()
        if not text:
            raise ValueError(f"{field} names an empty value and {why}")
        out.append(text)
    return out


def build_checkpoint(*, work_item: str, epoch, parent_owner: str, next_safe_action: str,
                     waiting_on: dict, admissions: dict | None = None,
                     now: str, requirement_revision: str | None = None,
                     remaining_results=None, evidence_locators=None,
                     child_attempts=None, unresolved_effects=None,
                     write_scope=None) -> dict:
    """One Parent checkpoint, validated, ready for `put_checkpoint` to transact.

    Raises `ValueError` for every refusal, and each message says what the caller
    would otherwise have recorded. The store is what refuses a SECOND checkpoint
    for the same scope; this function cannot see the scope's history and does not
    pretend to.

    `waiting_on` IS REQUIRED AND IS THE POINT. A Parent that has a next safe action
    it could simply take has nothing to wait for, and a checkpoint around one is
    the failure `docs/SCENARIO-ACCEPTANCE.md` names: «第一版实现就返回等用户继续».
    There is no default and no «none» kind, so a Parent with work to do cannot file
    one of these by leaving a field out — it has to state what it is waiting on,
    and state something this fabric can confirm.
    """
    item = str(work_item or "").strip()
    if not item:
        raise ValueError(
            "a checkpoint without a work_item is a record of nothing; the Item is "
            "what a later Parent matches on and what makes an old-epoch record "
            "unable to be mistaken for a current one")
    if not _is_epoch(epoch):
        raise ValueError(
            f"epoch {epoch!r} is not a positive integer. The epoch is the half of "
            f"the key that lets the same Item be carried by a second Parent "
            f"without the first one's record being overwritten — an epoch nobody "
            f"can order is not one")
    owner = str(parent_owner or "").strip()
    if not owner:
        raise ValueError(
            "a checkpoint without a parent_owner does not say who was carrying the "
            "Item, so nothing can tell a resumed Parent from a second one")
    action = str(next_safe_action or "").strip()
    if not action:
        raise ValueError(
            "next_safe_action is empty. A checkpoint exists so that resuming is "
            "possible without replaying the session that wrote it, and this field "
            "is the whole of what resuming reads: without it the record is a "
            "parking notice")
    if len(action) > MAX_NEXT_ACTION:
        raise ValueError(
            f"next_safe_action is {len(action)} characters and the bound is "
            f"{MAX_NEXT_ACTION}. A checkpoint is recoverable work state and not a "
            f"transcript summary: a long one is the session being copied, and a "
            f"session copied is a session replayed")
    if not isinstance(waiting_on, dict):
        raise ValueError(
            "waiting_on must be a mapping naming what this Parent is waiting for. "
            "There is no «nothing» value: a Parent with work it could do has "
            "nothing to checkpoint")
    kind = str(waiting_on.get("kind") or "").strip()
    if kind not in WAIT_KINDS:
        raise ValueError(
            f"waiting_on.kind {kind or 'is absent'} is not one of {list(WAIT_KINDS)}. "
            f"The list is closed because each kind is checked against something: a "
            f"child against the admissions this store holds, a window against the "
            f"clock it is read at. A kind nothing can check would let a caller "
            f"record a wait that no part of this fabric is able to confirm")
    wait: dict = {"kind": kind}
    if kind == "child":
        refs = _as_list(waiting_on.get("refs"), "waiting_on.refs",
                        "a child wait names no child")
        if not refs:
            raise ValueError(
                "a child wait names no child, so there is nothing for a later "
                "Parent to look up and nothing to accept the result of")
        known = admissions if isinstance(admissions, dict) else {}
        unknown = [ref for ref in refs if ref not in known]
        if unknown:
            raise ValueError(
                f"waiting on {', '.join(unknown)}, which this store holds no "
                f"admission for. A child is a delegated cycle, and a wait on one "
                f"the fabric cannot see is a wait that will never be answered — the "
                f"result would have to be carried by hand, which is the failure "
                f"`docs/SCENARIO-ACCEPTANCE.md` names as «要求用户复制结果»")
        wait["refs"] = refs
    else:
        until = waiting_on.get("until")
        moment = _instant(until)
        if moment is None:
            raise ValueError(
                f"a window wait needs an `until` instant and {until!r} is not one. "
                f"The instant is the expiry policy, and a window without one is a "
                f"wait with no end — which reads as «this will resume eventually» "
                f"and is the claim the product forbids")
        now_moment = _instant(now)
        if now_moment is None:
            raise ValueError(f"the write clock {now!r} is not an instant")
        if moment <= now_moment:
            raise ValueError(
                f"the window closes at {until}, which is not after the moment this "
                f"record is written ({now}). A window that has already passed is "
                f"not a wait, and saving one would make the record read as awaiting "
                f"a continuation that is owed now")
        wait["until"] = str(until)

    return {
        # Derived, never supplied. See `CHECKPOINT_FIELDS`.
        "checkpoint_ref": checkpoint_key(item, epoch),
        "work_item": item,
        "epoch": int(epoch),
        "parent_owner": owner,
        "requirement_revision": (str(requirement_revision).strip()
                                 if requirement_revision else None),
        "remaining_results": _as_list(remaining_results, "remaining_results",
                                      "a remaining result with no name cannot be "
                                      "completed"),
        "evidence_locators": _as_list(evidence_locators, "evidence_locators",
                                      "an empty locator is not a locator"),
        "child_attempts": _as_list(child_attempts, "child_attempts",
                                   "an unnamed attempt cannot be looked up again"),
        "unresolved_effects": _as_list(unresolved_effects, "unresolved_effects",
                                       "an unnamed effect cannot be reconciled"),
        "write_scope": _as_list(write_scope, "write_scope",
                                "an empty write scope is not a scope"),
        "next_safe_action": action,
        "waiting_on": wait,
        "written_at": str(now),
    }


def continuation_state(record: dict, *, receivers_seen=None, now: str | None = None) -> dict:
    """Whether anything will wake this checkpoint, and the word for the answer.

    The receiver list is an ARGUMENT and not a field of `record`, which is the
    whole safety property of this function: see the module docstring. A caller
    that passes `receivers_seen` is reporting what the fabric has installed, and
    the only caller in this release passes `receivers()`.

    Three answers, and the third is not a failure:

      `AWAITING_CONTINUATION` — no receiver is installed. The checkpoint is saved,
        the state is durable, and nothing will wake it. The product's sentence is
        in `says`, and it is the literal the acceptance criterion names.
      `RECEIVER_INSTALLED` — something registered could wake it, and `receivers`
        names what. This release cannot reach it; the branch exists so that the
        day the registry is filled the reader does not have to be rewritten to
        stop saying «awaiting».
      `SUPERSEDED` — a later epoch of the same Item took the scope over. Reported
        ahead of the receiver question because «who is carrying this» is answered
        before «what would wake it», and a superseded record's answer to the
        second is about a Parent that is no longer the owner.

    `says` is present on every answer, including the two where it is not the
    product's literal. A reader that had to compose the sentence for itself would
    compose a different one per caller, and the first one to differ would be the
    one that reports a background run.
    """
    if record.get("superseded_by"):
        return {"state": "SUPERSEDED", "receivers": [],
                "superseded_by": record.get("superseded_by"),
                "superseded_at": record.get("superseded_at"),
                "says": (f"checkpoint superseded at {record.get('superseded_at')} by "
                         f"{record.get('superseded_by')} — a later epoch of "
                         f"{record.get('work_item')} carries it now")}
    installed = list(receivers() if receivers_seen is None else receivers_seen)
    if not installed:
        return {"state": "AWAITING_CONTINUATION", "receivers": [],
                "says": (f"checkpoint 已保存，待续 — {record.get('work_item')} "
                         f"epoch {record.get('epoch')} is held by "
                         f"{record.get('parent_owner')} and no continuation "
                         f"receiver is installed on this node, so nothing will "
                         f"wake it. Resuming is what `work continuations` and a "
                         f"new Parent do, and neither of them claims a background "
                         f"run")}
    return {"state": "RECEIVER_INSTALLED", "receivers": installed,
            "says": (f"checkpoint saved and a continuation receiver is installed "
                     f"({', '.join(str(r) for r in installed)}); whether it wakes "
                     f"this one depends on the wait it was filed with")}


def wait_state(record: dict, *, admissions: dict | None = None,
               now: str | None = None) -> dict:
    """Whether the wait this checkpoint was filed with is over yet.

    THE QUESTION `waiting_on` WAS WRITTEN TO ASK, AND UNTIL NOW NOTHING ANSWERED.
    Every other part of a wait already existed: `build_checkpoint` validates it
    against the admissions collection, the store files it under the Item and the
    epoch, and `cmd_work_continuations` prints it. What did not exist anywhere in
    this tree was code that evaluated it — so a checkpoint read exactly the same
    at the moment it was written and a week after the child had finished, and the
    one question it exists to make answerable, «can this Parent carry on yet»,
    had no answer at all.

    IT EVALUATES. IT DOES NOT RESUME, AND IT DOES NOT WAKE. Nothing here starts a
    process, and a WAIT_OVER is not a claim that something will: it is what a new
    Parent reads to decide whether resuming now is possible, and resuming in this
    architecture is a new Parent (see the module docstring — there is no daemon,
    and a receiver that ran by itself would be the fabrication §17.4 forbids).

    TWO KINDS, TWO AUTHORITIES, NEITHER INVENTED HERE:

      `window`  the clock. `until` against `now`, both read with the same
                tolerance `build_checkpoint` used to accept them.
      `child`   the admissions collection, which is where `build_checkpoint`
                already checks a child wait against. A child's result has arrived
                when its admission was RETIRED: `retire_admission` is the only
                writer of `retired_at`, and retiring is this store's word for
                «this cycle's outcome is recorded». A phase is deliberately NOT
                that — `DISPATCHED` says a receipt was written, i.e. the child
                was invoked, which is the state the whole wait is meant to
                outlive. Reading a phase as a result would resume a Parent while
                its child was still running.

    PARTIAL IS WAIT_OPEN AND NEVER WAIT_OVER. A checkpoint may name several
    children, and the wait ends when the last of them has a recorded outcome.
    `settled` and `outstanding` name which is which, so nobody has to infer «some
    of them» from a boolean.

    A CHILD THE STORE DOES NOT HOLD IS UNANSWERABLE AND NOT «NOT YET» — and that
    is a change of reading from `build_checkpoint`'s, for a different question.
    There, an unknown child is refused at write time because a wait the fabric
    cannot confirm would have to be carried by hand. Here the record already
    exists and may name a child this store no longer holds (`retire_admission`
    keeps records, but a store can be moved, swept, or read from another root).
    The honest answer is that this reading cannot say, which is why it is not
    folded into either of the two answers that can.

    `admissions` ABSENT IS ITS OWN CASE, apart from «this store holds no such
    child»: one repair is to bring the store, the other is that the child is not
    in it. Both are WAIT_UNANSWERABLE and `detail` says which one this is.
    """
    record = record if isinstance(record, dict) else {}
    wait = record.get("waiting_on")
    wait = wait if isinstance(wait, dict) else None
    base = {"checkpoint_ref": record.get("checkpoint_ref"),
            "work_item": record.get("work_item"), "epoch": record.get("epoch"),
            "waiting_on": wait}
    if wait is None or not str(wait.get("kind") or "").strip():
        return {**base, "state": "WAIT_UNANSWERABLE", "evidence": [],
                "detail": ("this record carries no readable `waiting_on`, so there "
                           "is no wait to evaluate. It is not a wait that is over: "
                           "a record with nothing to wait for is a record whose "
                           "wait cannot be read"),
                "says": ("this checkpoint's wait is unreadable, so whether it can "
                         "be resumed cannot be said from it")}

    kind = str(wait["kind"]).strip()
    if kind == "window":
        until = _instant(wait.get("until"))
        moment = _instant(now) if now else None
        if until is None or moment is None:
            missing = ("waiting_on.until" if until is None else "the reading clock")
            return {**base, "state": "WAIT_UNANSWERABLE", "until": wait.get("until"),
                    "evidence": [],
                    "detail": (f"{missing} is not an instant, so the window cannot "
                               f"be compared with anything. A window is a "
                               f"comparison and a comparison with a value nobody "
                               f"can read has no answer"),
                    "says": ("this checkpoint waits on a window whose end cannot be "
                             "read, so whether it has passed cannot be said")}
        if moment >= until:
            return {**base, "state": "WAIT_OVER", "until": wait["until"],
                    "over_by_seconds": round((moment - until).total_seconds(), 3),
                    "evidence": [{"until": wait["until"], "read_at": str(now)}],
                    "detail": ("the window has closed. This is an answer about the "
                               "clock and about nothing else: it says the wait is "
                               "over, not that anything has resumed the Parent"),
                    "says": (f"the window ended at {wait['until']}, so the wait is "
                             f"over and a new Parent can resume this Item")}
        return {**base, "state": "WAIT_OPEN", "until": wait["until"],
                "seconds_remaining": round((until - moment).total_seconds(), 3),
                "evidence": [{"until": wait["until"], "read_at": str(now)}],
                "detail": "the window has not closed yet",
                "says": (f"the window ends at {wait['until']}; the wait is not over "
                         f"yet")}

    if kind != "child":
        return {**base, "state": "WAIT_UNANSWERABLE", "evidence": [],
                "detail": (f"waiting_on.kind is {kind!r}, which this evaluator has "
                           f"no authority for. `WAIT_KINDS` is closed and this is "
                           f"not one of {list(WAIT_KINDS)}, so a record carrying it "
                           f"was not written by `build_checkpoint`"),
                "says": ("this checkpoint's kind of wait is one nothing here can "
                         "evaluate, so whether it is over cannot be said")}

    refs = list(wait.get("refs") or [])
    if not refs:
        return {**base, "state": "WAIT_UNANSWERABLE", "evidence": [],
                "detail": ("a child wait with no refs names nothing to look up, so "
                           "there is nothing whose result could have arrived"),
                "says": ("this checkpoint waits on no named child, so whether the "
                         "wait is over cannot be said")}
    if not isinstance(admissions, dict):
        return {**base, "state": "WAIT_UNANSWERABLE", "evidence": [],
                "refs": refs,
                "detail": ("no admissions were supplied, so the children this "
                           "checkpoint waits on were not looked up at all. This is "
                           "«nobody brought the store» and not «the store holds no "
                           "such child»"),
                "says": ("this reading was given no admissions, so it cannot say "
                         "whether the children it waits on have finished")}

    settled, outstanding, unheld, evidence = [], [], [], []
    for ref in refs:
        held = admissions.get(ref)
        if not isinstance(held, dict):
            unheld.append(ref)
            evidence.append({"ref": ref, "held": False})
            continue
        retired_at = held.get("retired_at")
        if retired_at:
            settled.append(ref)
            evidence.append({"ref": ref, "held": True, "retired_at": retired_at,
                             "outcome": held.get("outcome")})
        else:
            outstanding.append(ref)
            evidence.append({"ref": ref, "held": True, "retired_at": None,
                             "phase": held.get("phase"),
                             "because": ("this admission is still open. A phase is "
                                         "not a result: DISPATCHED says the child "
                                         "was invoked, not that it finished")})
    answer = {**base, "refs": refs, "settled": settled,
              "outstanding": outstanding, "unheld": unheld, "evidence": evidence}
    if unheld:
        return {**answer, "state": "WAIT_UNANSWERABLE",
                "detail": (f"this store holds no admission for "
                           f"{', '.join(unheld)}, so whether their results have "
                           f"arrived cannot be read from here. This is not «not "
                           f"yet»: the child may have finished and the record of "
                           f"it may simply not be in this store"),
                "says": (f"{', '.join(unheld)} is not in this store, so whether the "
                         f"wait is over cannot be said")}
    if outstanding:
        return {**answer, "state": "WAIT_OPEN",
                "detail": (f"{len(outstanding)} of {len(refs)} children have no "
                           f"recorded outcome yet, and the wait ends when the last "
                           f"one does"),
                "says": (f"waiting on {', '.join(outstanding)}, whose admission has "
                         f"no recorded outcome yet, so the wait is not over")}
    return {**answer, "state": "WAIT_OVER",
            "detail": ("every child this checkpoint waits on has a recorded "
                       "outcome. This is an answer about the store and about "
                       "nothing else: it says the wait is over, not that anything "
                       "has resumed the Parent"),
            "says": (f"every child it waits on has a recorded outcome, so the wait "
                     f"is over and a new Parent can resume this Item")}


def attempts_state(record: dict, *, admissions: dict | None = None) -> dict:
    """What became of the child attempts this checkpoint says it already made.

    THE FIELD THAT WAS WRITTEN, VALIDATED, FILED, AND NEVER READ BACK. A
    checkpoint carries `child_attempts`; `work checkpoint --attempt` writes it,
    `build_checkpoint` validates it, and its own refusal states why the field
    exists at all — «an unnamed attempt cannot be looked up again». Nothing
    looked it up again. `work continuations`, the one surface a resuming Parent
    reads, renders seven of the eleven declared fields and this is one of the
    three it left out — so the read answered «what is left to do» and said
    nothing about «what has already been tried», which is the fact that decides
    whether resuming means continuing or means dispatching a second child.
    `docs/SCENARIO-ACCEPTANCE.md` names that failure («新建第二Task») in the same
    row as this checkpoint's own reason for existing, and
    `docs/daily-work-cycle.md` §17.4 names the reading: «恢复前对账 baseline、
    writer、effect 和旧 attempt».

    IT ANSWERS ABOUT THE RECORD AND THE STORE, AND ABOUT NOTHING ELSE. An attempt
    is a delegated cycle, so the admissions collection is the only thing on this
    node that can say what became of one. There is no clock here and no board: an
    attempt whose admission is still open has produced no result, whatever the
    Item's Runs or its board row happen to say.

    THREE ANSWERS FOR ONE ATTEMPT, AND THE THIRD IS NOT «NOT YET»:

      `ATTEMPT_RETIRED`    the store holds it and it was retired, which is this
                           store's word for «this cycle's outcome is recorded» —
                           the same word `wait_state` reads, and `outcome` is
                           carried back when the retirement recorded one.
      `ATTEMPT_OPEN`       the store holds it and it still holds its reservation.
                           A phase is deliberately not an answer: `DISPATCHED`
                           says the child was invoked, not that it finished.
      `ATTEMPT_UNANSWERABLE`  the store holds no such admission. Not «not yet»,
                           for the reason `wait_state` gives: the checkpoint
                           already exists and may name an attempt from a store
                           that was moved, swept, or is being read from another
                           root. `build_checkpoint` refuses an attempt nothing can
                           look up when it appears in `waiting_on`; it can still
                           appear HERE, because this field records what was
                           already done rather than claiming what the fabric can
                           still see.

    ACCEPTED IS ONE OF ITS STATES NOW, AND IT WAS NOT WHEN THIS FUNCTION WAS
    WRITTEN. The paragraph that used to stand here said nothing on this node
    records an acceptance as a fact separate from the record's own
    `evidence_locators`, and that was true of the node it was written on — a test
    held the shape of that answer so that a later slice adding an acceptance store
    would have to move it on purpose. Slice L added one:
    `runtime/agent_control_store.accept_admission` writes who took a cycle's
    result up and which locator they took up, once, on the edge. So the answer is
    read from the store like the other four and is NOT inferred from the record.

    WHAT THAT DOES NOT CLAIM. The store records the take-up; it does not verify
    that the record carries the locator. `evidence_locators` is the Item's own
    restatement, written by that Parent when it next checkpoints, so between the
    two the record is legitimately one write behind the edge, and an answer that
    called that a disagreement would be inventing a rule the design does not
    state. What this function answers is «the store says a take-up was recorded»,
    which is the fact a resuming Parent needs before it decides whether it is
    about to accept the same result a second time.
    """
    record = record if isinstance(record, dict) else {}
    refs = [str(ref) for ref in (record.get("child_attempts") or []) if str(ref).strip()]
    base = {"checkpoint_ref": record.get("checkpoint_ref"),
            "work_item": record.get("work_item"), "epoch": record.get("epoch"),
            "attempts": refs}
    if not refs:
        return {**base, "state": "ATTEMPT_NONE", "evidence": [], "retired": [],
                "accepted": [], "open": [], "unheld": [],
                "detail": ("this record names no child attempt, so there is "
                           "nothing to look up. It is not a record whose attempts "
                           "all finished: a Parent that made none says so by "
                           "carrying none, which is a state `build_checkpoint` "
                           "allows and not a gap in this reading"),
                "says": "this checkpoint records no child attempt already made"}
    if not isinstance(admissions, dict):
        # Shaped like `wait_state`'s answer to the same situation, and for the same
        # reason: there is no `unheld` list here, because «this store holds no such
        # attempt» is a statement about a store that was read, and none was brought.
        return {**base, "state": "ATTEMPT_UNANSWERABLE", "evidence": [],
                "retired": [], "accepted": [], "open": [],
                "detail": ("no admissions were supplied, so the attempts this "
                           "record names were not looked up at all. This is «nobody "
                           "brought the store» and not «the store holds no such "
                           "attempt»"),
                "says": ("this reading was given no admissions, so what became of "
                         "the attempts it records cannot be said")}

    retired, accepted, open_now, unheld, evidence = [], [], [], [], []
    for ref in refs:
        held = admissions.get(ref)
        if not isinstance(held, dict):
            unheld.append(ref)
            evidence.append({"ref": ref, "held": False,
                             "state": "ATTEMPT_UNANSWERABLE",
                             "accepted_at": None, "accepted_by": None,
                             "accepted_locator": None,
                             "because": ("this store holds no admission under this "
                                         "ref, so what became of the attempt cannot "
                                         "be read from here")})
            continue
        retired_at = held.get("retired_at")
        accepted_at = held.get("accepted_at")
        if retired_at:
            retired.append(ref)
            if accepted_at:
                # `accepted` is a SUBSET of `retired` and not a third list beside
                # it: a take-up is only possible after the cycle's outcome was
                # recorded, so every accepted ref is also a retired one, and two
                # lists that overlapped by construction would make a consumer
                # count the same attempt twice.
                accepted.append(ref)
                evidence.append({"ref": ref, "held": True,
                                 "state": "ATTEMPT_ACCEPTED",
                                 "phase": held.get("phase"),
                                 "retired_at": retired_at,
                                 "outcome": held.get("outcome"),
                                 "accepted_at": accepted_at,
                                 "accepted_by": held.get("accepted_by"),
                                 "accepted_locator": held.get("accepted_locator"),
                                 "because": ("this attempt was retired and a "
                                             "take-up of its result is recorded "
                                             "against it, so the Item is not short "
                                             "of this one — and the same result is "
                                             "not to be accepted twice")})
            else:
                evidence.append({"ref": ref, "held": True,
                                 "state": "ATTEMPT_RETIRED",
                                 "phase": held.get("phase"),
                                 "retired_at": retired_at,
                                 "outcome": held.get("outcome"),
                                 "accepted_at": None, "accepted_by": None,
                                 "accepted_locator": None,
                                 "because": ("this attempt was retired, so its "
                                             "outcome is recorded — and nothing "
                                             "says whether the result was taken "
                                             "up, which the record's own accepted "
                                             "evidence is what answers")})
        else:
            open_now.append(ref)
            evidence.append({"ref": ref, "held": True, "state": "ATTEMPT_OPEN",
                             "phase": held.get("phase"), "retired_at": None,
                             "outcome": None, "accepted_at": None,
                             "accepted_by": None, "accepted_locator": None,
                             "because": ("this admission is still open. A phase is "
                                         "not a result: DISPATCHED says the attempt "
                                         "was invoked, not that it finished")})
    answer = {**base, "evidence": evidence, "retired": retired,
              "accepted": accepted, "open": open_now, "unheld": unheld}
    taken_up = (f" ({len(accepted)} of them taken up)" if accepted
                else " (none of them taken up)")
    counts = (f"{len(retired)} finished{taken_up}, {len(open_now)} still open, "
              f"{len(unheld)} this store cannot look up")
    if unheld:
        return {**answer, "state": "ATTEMPT_UNANSWERABLE",
                "detail": (f"this store holds no admission for "
                           f"{', '.join(unheld)}, so what became of "
                           f"{'it' if len(unheld) == 1 else 'them'} cannot be read "
                           f"from here — which is not the same as an attempt that "
                           f"has not finished"),
                "says": (f"of the attempts this checkpoint records, {counts} — so a "
                         f"Parent resuming on this record cannot yet say whether "
                         f"one of them is still owed")}
    if open_now:
        return {**answer, "state": "ATTEMPT_OPEN",
                "detail": (f"{len(open_now)} of {len(refs)} recorded attempts have "
                           f"no recorded outcome yet"),
                "says": (f"of the attempts this checkpoint records, {counts} — an "
                         f"attempt without a recorded outcome is one this Parent "
                         f"may already be paying for")}
    if len(accepted) != len(retired):
        short = [ref for ref in retired if ref not in accepted]
        return {**answer, "state": "ATTEMPT_RETIRED",
                "detail": ("every recorded attempt has a recorded outcome, and no "
                           "take-up of a result is recorded for "
                           + ", ".join(short)
                           + ". Both are answers about the store: it says nothing "
                             "about whether the result was refused — a required "
                             "revision or a registered gap is the Parent's next "
                             "checkpoint, and it is the record that carries it"),
                "says": (f"of the attempts this checkpoint records, {counts}, so "
                         f"resuming does not mean dispatching a second child — and "
                         f"for the ones with no take-up it means reading the "
                         f"record's accepted evidence before accepting one")}
    return {**answer, "state": "ATTEMPT_ACCEPTED",
            "detail": ("every recorded attempt has a recorded outcome and a "
                       "take-up of its result is recorded against it. This is an "
                       "answer about the store and about nothing else: it does not "
                       "check that the record's own accepted evidence names the "
                       "same locators, which is a restatement that Parent writes "
                       "when it next checkpoints"),
            "says": (f"of the attempts this checkpoint records, {counts}, so "
                     f"resuming means continuing from results the Item already has "
                     f"rather than dispatching a second child")}


def predecessors(record: dict, checkpoints: dict | None = None) -> list[dict]:
    """The records this Item came through, oldest epoch first.

    «What came before this one» is the question `superseded_by` is a POINTER to
    and not an answer to. The pointer says which record took the scope over, and
    the store writes it on takeover — but a reading that walked the pointers
    would inherit every way a pointer can fail to compose, and a store on disk is
    a file rather than a proof of the rules that wrote it.

    THAT IS NOT HYPOTHETICAL, AND IT WAS MEASURED RATHER THAN IMAGINED. Both
    writers of that field now enforce the same three conditions (see
    `runtime/agent_control_store.supersede_checkpoint`, which refuses a successor
    this store does not hold, one of another Item, and one at an earlier epoch) —
    but it did not when this function was written, so records exist that a
    pointer-walking chain would follow out of the Item it started in, and the
    store's idempotent branch still REPLAYS such a record rather than refusing
    its own history. A reading that had to walk pointers would have to decide what
    to do about that; this one does not have to decide anything, because the
    order it uses is the Item's epochs.

    SO THE ORDER COMES FROM THE STORE'S OWN MODEL AND NOT FROM THE POINTERS:
    «The scope is the Item; the epoch is only how it changes hands.» This
    function reads that sentence as a definition — the epochs of THIS Item, below
    the one in hand, that the store no longer counts as live. The pointer stays
    on the records as the reason, and whether it is internally consistent is a
    question for the store rather than something this reading has to trust.

    A LOWERE-EPOCH RECORD THE STORE STILL COUNTS AS LIVE IS NOT A PREDECESSOR. It
    would mean an Item with two owners, which `put_checkpoint` refuses and which
    a higher-epoch write resolves in the same transaction, so the shape is
    unreachable through the store — and if one is on disk anyway, calling it a
    record the Item came through would invent a handover nobody made.

    An unreadable argument answers the empty list and not an error: this is one
    half of a reading, and the half that answers «nothing came before» is
    legitimately empty for the first epoch of every Item.
    """
    record = record if isinstance(record, dict) else {}
    item = str(record.get("work_item") or "")
    epoch = record.get("epoch")
    if not item or not _is_epoch(epoch) or not isinstance(checkpoints, dict):
        return []
    mine = str(record.get("checkpoint_ref") or "")
    came_through = []
    for ref, other in checkpoints.items():
        if not isinstance(other, dict) or str(ref) == mine:
            continue
        if str(other.get("work_item") or "") != item:
            continue
        if not other.get("superseded_by"):
            continue
        if not _is_epoch(other.get("epoch")):
            continue
        if other["epoch"] < epoch:
            came_through.append(other)
    return sorted(came_through,
                  key=lambda held: (held["epoch"],
                                    str(held.get("checkpoint_ref") or "")))


def carried_attempts(record: dict, *, checkpoints: dict | None = None,
                     admissions: dict | None = None) -> dict:
    """What this Item still owes from the epochs an earlier Parent held it.

    THE ACCEPTANCE CRITERION «Parent替换后原child自动接回», WHICH WAS PROSE UNTIL
    THIS FUNCTION. A Parent is replaced by a new Parent at a higher epoch; the
    store supersedes the old record in the same transaction and the new record is
    whatever its caller wrote. Nothing carries the old epoch's attempts into it,
    so the child the replaced Parent dispatched survives only as a historical
    block that a reader has to know to walk — and the record the Item is now
    being read through answers `ATTEMPT_NONE` about an Item with a child still
    running. That is not a gap in the store: the child is still there, keyed by
    its cycle. It is a gap in the READING, and it is the reason «原child自动接回»
    could only be asserted as prose.

    THE UNIT IS THE ITEM AND NOT THE RECORD, because the store's own sentence
    says so: «The scope is the Item; the epoch is only how it changes hands.»
    A child dispatched under one epoch is the Item's obligation under the next
    one, and the reading has to be able to say that without the new owner having
    been handed anything.

    WHAT IS CARRIED, AND WHAT IS NOT — and each of the three is a different one:

      `ATTEMPT_OPEN`     carried. The store holds it and no outcome is recorded,
                         so somebody is still paying for it.
      `ATTEMPT_RETIRED`  carried. An outcome exists and no take-up of the result
                         is recorded, so reading the result is what the new owner
                         owes. One take-up closes it: `ATTEMPT_ACCEPTED` is NOT
                         carried, because `work accept` writes once and the Item
                         already has that result.
      `ATTEMPT_UNANSWERABLE`  NOT carried and NOT dropped: the record-level answer
                         becomes `ATTEMPTS_CARRIED_UNANSWERABLE`, because «an
                         epoch I cannot read may have left something behind» is
                         not «nothing is outstanding». Same rule as
                         `attempts_state`'s, one vocabulary up.

    AN ATTEMPT THE RECORD ITSELF NAMES IS NOT CARRIED, AND THAT IS WHAT MAKES THE
    TWO READINGS COMPOSE. A successor that writes `child_attempts` has carried
    them forward by hand and `attempts_state` answers for them; a successor that
    does not gets this answer instead. Every attempt is reported by exactly one
    of the two and never by both, so a reader that adds the counts together is
    not counting anything twice.

    WHAT IT DOES NOT DO, in the shape the rest of this module states it. It does
    not resume and it does not wake: nothing here starts a process, and the new
    owner is still a new Parent (`receivers()` is the one place that could ever
    say otherwise). It does not decide that an inherited attempt SHOULD be
    continued — abandoning it, superseding it and continuing it are three
    different acts and all three are the Parent's. And it does not check that the
    result of an inherited, retired attempt can still be taken up, which is
    `runtime/agent_control_store.accept_admission`'s question and not this one's.
    """
    record = record if isinstance(record, dict) else {}
    came = predecessors(record, checkpoints)
    base = {"checkpoint_ref": record.get("checkpoint_ref"),
            "work_item": record.get("work_item"),
            "epoch": record.get("epoch"),
            "predecessors": [str(held.get("checkpoint_ref") or "")
                             for held in came]}
    if not came:
        return {**base, "state": "NOTHING_CARRIED", "carried": [], "closed": [],
                "unreadable": [], "evidence": [],
                "detail": ("this record is the first epoch of this Item the store "
                           "holds, so there is no earlier Parent whose attempts it "
                           "could be carrying. That is a different fact from «the "
                           "Item came through three epochs and they are all closed», "
                           "and `predecessors` tells the two apart"),
                "says": ("this Item has no earlier epoch in this store, so nothing "
                         "was carried into this one")}
    if not isinstance(admissions, dict):
        return {**base, "state": "ATTEMPTS_CARRIED_UNANSWERABLE", "carried": [],
                "closed": [], "unreadable": [], "evidence": [],
                "detail": ("no admissions were supplied, so what this Item's "
                           "earlier epochs dispatched was not looked up at all. "
                           "This is «nobody brought the store» and not «the earlier "
                           "epochs left nothing behind», the same distinction "
                           "`attempts_state` draws and for the same reason"),
                "says": ("this reading was given no admissions, so whether an "
                         "earlier epoch of this Item left an attempt behind cannot "
                         "be said")}
    own = {str(ref) for ref in (record.get("child_attempts") or [])
           if str(ref).strip()}
    carried, closed, unreadable, evidence = [], [], [], []
    for older in came:
        by = {"named_by": older.get("checkpoint_ref"),
              "named_by_epoch": older.get("epoch"),
              "named_by_parent": older.get("parent_owner")}
        for entry in attempts_state(older, admissions=admissions)["evidence"]:
            ref = str(entry.get("ref"))
            if ref in own:
                # Named by the record in hand, so it is that record's own attempt
                # and `attempts_state` is the reading that answers for it. Counted
                # once, never twice.
                continue
            where = (f"epoch {older.get('epoch')} of this Item, carried by "
                     f"{older.get('parent_owner')}")
            if entry["state"] == "ATTEMPT_UNANSWERABLE":
                unreadable.append(ref)
                because = (f"{where}, names this attempt and this store holds no "
                           f"admission under the ref — so whether that epoch left "
                           f"something behind cannot be read from here")
            elif entry["state"] == "ATTEMPT_ACCEPTED":
                closed.append(ref)
                because = (f"{where}, dispatched it and a take-up of its result is "
                           f"recorded against it, so nothing about it is left for "
                           f"the Parent that carries the Item now")
            elif entry["state"] == "ATTEMPT_RETIRED":
                carried.append(ref)
                because = (f"{where}, dispatched it; its outcome is recorded and no "
                           f"take-up of the result is, so what the Parent that "
                           f"carries the Item now owes is to read that result — this "
                           f"record does not name it, which is why it is here rather "
                           f"than in the record's own list")
            else:
                carried.append(ref)
                because = (f"{where}, dispatched it and no outcome is recorded, so "
                           f"somebody is still paying for it — this record does not "
                           f"name it, which is why it is here rather than in the "
                           f"record's own list")
            evidence.append({**entry, **by, "because": because})
    answer = {**base, "evidence": evidence, "carried": carried, "closed": closed,
              "unreadable": unreadable}
    epochs = len(came)
    verb = "epoch" if epochs == 1 else "epochs"
    if unreadable:
        # The one answer that says both halves. An item that owes one thing and
        # cannot be read about another is not «nothing is outstanding», and it is
        # also not just «unreadable»: what could be read is still owed.
        owed = (f" and {len(carried)} of those it still owes"
                if carried else "")
        return {**answer, "state": "ATTEMPTS_CARRIED_UNANSWERABLE",
                "detail": (f"this Item came through {epochs} earlier {verb}, and "
                           f"this store holds no admission for "
                           f"{', '.join(unreadable)} — so whether that epoch left "
                           f"an attempt behind cannot be read from here. That is "
                           f"not the same as an Item with nothing outstanding"),
                "says": (f"this Item came through {epochs} earlier {verb}, and "
                         f"{len(unreadable)} attempt"
                         + ("" if len(unreadable) == 1 else "s")
                         + f" named there cannot be looked up in this store"
                         + owed
                         + f", so what the new owner inherits cannot be said in "
                           f"full from here")}
    if carried:
        return {**answer, "state": "ATTEMPTS_CARRIED",
                "detail": (f"this Item came through {epochs} earlier {verb} and "
                           f"{len(carried)} attempt"
                           + ("" if len(carried) == 1 else "s")
                           + f" nothing has closed "
                           + ("is" if len(carried) == 1 else "are")
                           + f" still its own: {', '.join(carried)}. "
                           f"They are not fields of this record, and the Parent "
                           f"carrying the Item now owes them whether or not it was "
                           f"told"),
                "says": (f"this Item came through {epochs} earlier {verb} and still "
                         f"owes {len(carried)} attempt"
                         + ("" if len(carried) == 1 else "s")
                         + f" no take-up closes ({', '.join(carried)}), so resuming "
                           f"here means reading "
                         + ("it" if len(carried) == 1 else "them")
                         + f" and not dispatching a second child for the same "
                           f"work")}
    return {**answer, "state": "NOTHING_CARRIED",
            "detail": (f"this Item came through {epochs} earlier {verb} and every "
                       f"attempt those Parents dispatched has a take-up recorded "
                       f"against it, so nothing was left behind for the Parent that "
                       f"carries the Item now"),
            "says": (f"this Item came through {epochs} earlier {verb} and "
                     + ("it left" if epochs == 1 else "they left")
                     + f" nothing open, so there is nothing carried into this one")}
