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

WHAT RECEIVERS() ANSWERS TODAY, and it is not a placeholder. This release has no
continuation trigger installed: `runtime/daemon.py`'s tick reads neither
admissions nor checkpoints, so nothing on this node wakes a saved checkpoint. The
honest consequence is the product's own sentence — a checkpoint is saved and
awaiting continuation — and a reader that said anything else would be reporting a
background run nobody started. `RECEIVERS` is empty BY CONSTRUCTION and not by
oversight: it is the registry the wake half will fill, and until then the empty
tuple is the measurement.

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

#: The continuation triggers installed on this node. See the module docstring.
RECEIVERS: tuple = ()


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
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
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
