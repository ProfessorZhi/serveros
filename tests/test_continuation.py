#!/usr/bin/env python3
"""The Parent checkpoint: what a waiting Parent leaves, and who wakes it.

WHY THIS FILE EXISTS
--------------------
P7 asks for a continuation chain in which a logical Parent can stop while a child,
a dependency or a window is outstanding, and be resumed from recoverable state
rather than from a replay of its session. Before this slice only the SECOND half
of that was sayable: `fleet/agent_control_router.handoff` moved a *session*
between runtimes, and nothing anywhere recorded where a Parent had got to when it
chose to wait. `docs/context-capsule.md` and `docs/daily-work-cycle.md` §17.4 both
name the object, and neither had a producer or a consumer.

Five properties, in the order a reader needs them:

  1. THE RECORD IS A CAPSULE AND NOT A TRANSCRIPT. `CHECKPOINT_FIELDS` is closed
     and `build_checkpoint` returns exactly that set plus the two fields it
     derives. The one free-text field is bounded. That is the mechanism behind
     the product's «recoverable work state, **not a transcript summary**» — a
     field a caller can add is a field a caller will use to put a session in.
  2. THE SCOPE HAS ONE OWNER, AND THE EPOCH IS HOW IT CHANGES HANDS. The unique
     thing is the Item at one epoch of itself. A second live record for that
     scope is refused; a write at a HIGHER epoch takes the scope over in the same
     transaction, so there is never a moment with two owners and never a scope
     with none. «同一个scope中只有一个父继续owner» and «旧结果晚到覆盖新epoch»
     are the two sentences, and `tests/test_continuation.py` is where they are
     mechanical rather than prose.
  3. A REPLAY IS NOT A SECOND WRITE, AND THAT SURVIVES SUPERSESSION. An equal
     record minus the fields the store adds answers with the stored one. The
     subtraction is load-bearing and there is a test below that fails without it:
     a superseded record carries two fields its writer never wrote, so comparing
     them would turn a retry of an ACCEPTED write into «your record is not here».
  4. «NO RECEIVER» IS TRUE OF THE FABRIC, NOT A MISSING FIELD. The receiver list
     is an argument to `continuation_state` and never a field of the record: a
     record that could name its own receiver could claim a background run, which
     is the one thing `docs/daily-work-cycle.md` §17.4 forbids in as many words
     — «没有 receiver 时准确显示"checkpoint已保存，待续"，不能伪造后台运行».
     `TestTheReceiverIsAskedOfTheFabric` monkeypatches the registry non-empty, so
     the empty tuple this release ships is a measurement and not a constant the
     reader has been hardcoded to repeat.
  5. THE WAIT IS EVALUATED, AND EVALUATING IT IS NOT WAKING ANYTHING. This one
     arrived late and was the largest hole in the chain: `waiting_on` was
     written, validated against the admissions, filed and printed — and no code
     anywhere decided whether the thing it names had happened. A checkpoint read
     identically the moment it was written and a week after its child had
     finished. `wait_state` answers «has it happened yet» from the admission
     store and the clock, in three words rather than two, so that a store which
     cannot answer is not made to answer «over» (which resumes a Parent on no
     evidence) or «not yet» (which parks one forever behind a store problem).
     It does not fill `RECEIVERS`: an answer about a child's recorded outcome
     must never be printed as «something will wake this», because nothing on
     this node runs by itself. `TestAnEvaluatorIsNotAReceiver` is where the two
     are kept apart, and it is the test that would fail first if a later slice
     wired the evaluator into the wake half by accident.

  6. WHAT WAS TRIED IS READ BACK. `child_attempts` was declared in
     `CHECKPOINT_FIELDS`, written by `work checkpoint --attempt`, validated by
     `build_checkpoint` — whose refusal states why the field exists at all, «an
     unnamed attempt cannot be looked up again» — and read by nothing at all.
     `wait_state` answers about the attempt a Parent is WAITING on; `attempts_state`
     answers about the attempts it has ALREADY MADE, which is the fact that decides
     whether resuming means continuing the work or means dispatching a second child.
     The design authority names both the failure and the reading:
     `docs/SCENARIO-ACCEPTANCE.md` puts «新建第二Task» in the same row as this
     checkpoint's reason for existing, and `docs/daily-work-cycle.md` §17.4 says
     «恢复前对账 baseline、writer、effect 和旧 attempt».
     `TestEveryDeclaredFieldIsRenderedOrExempt` is what keeps this from happening
     a second time: a field added to the record and never read back is a change to
     that test, not a silent addition.

A SEVENTH THING THAT IS ALSO ASSERTED, because it is a property of this tree
rather than of this feature: `runtime/continuation.py` imports no other layer of
the repository. `runtime/agent_control_store.py` imports nothing from the repository
either, and the two have to be readable together — the store holds the record and
this module is the only thing that can say what it means. `intake/focus_source.py`
keeps the same boundary. An import added here would be the first thread of a
dependency between the contract and the fabric that reads it.

WHAT THIS FILE DOES NOT COVER: `fleet/agent_control_router.handoff`'s own
`checkpoint` parameter, which is a different object. It was a live defect until
slice J — it was accepted and ignored — and it is now refused by name with the
program §9 describes, so what is uncovered is no longer «what does it mean» but
the program itself. See the ledger's §9.38.

`attempts_state` DOES NOT ANSWER «ACCEPTED», and the absence is deliberate rather
than unfinished. Nothing on this node records an acceptance as a fact separate
from the record's own `evidence_locators` — `work checkpoint --evidence` calls one
«an accepted evidence locator» — so «which of these results was accepted into this
Item» is read from the record and reconciled against the attempts by whoever
resumes. A test asserts the shape of that answer so that a later slice adding an
acceptance store has to move it on purpose.

Hermetic: a temporary directory per test, no daemon, no socket. Every instant is
an argument.
"""
from __future__ import annotations

import ast
import datetime
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
CLI = REPO / "bin" / "serverosctl"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The same module names `tests/test_admission.py` uses, and deliberately: pytest
# runs both files in one process, and two copies of `AgentControlStore` would make
# an `isinstance` check in one file mean nothing about an object raised in the
# other.
store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation",
                     RUNTIME / "continuation.py")

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError

NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
TOMORROW = "2026-09-27T10:00:00+00:00"
# An instant strictly past `TOMORROW`, for the window tests. `LATER` is not one:
# it is an hour after `NOW` and a day BEFORE the window it reads against, which
# is the arithmetic a window test is most likely to get wrong.
DAY_AFTER = "2026-09-28T10:00:00+00:00"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
BUDGET = {"wall_seconds": 900, "max_attempts": 2, "max_output_bytes": 200000}
DIGEST = "a" * 64


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def a_store(tmp_path: Path) -> AgentControlStore:
    """A store holding one admitted cycle, `cyc-1`, for item `SRV-0001`.

    The admission is seeded rather than driven through `work admit` for the same
    reason `tests/test_admission.py` seeds its own: `work admit` can only be
    reached from a board that selects something, and what this file is about
    starts after the delegation exists.
    """
    store = AgentControlStore(tmp_path / "store.json")
    store.put_context({"context_ref": CONTEXT})
    store.put_grant({"grant_ref": GRANT, "delegated_by": CONTEXT,
                     "items": ["SRV-0001"], "budget": dict(BUDGET),
                     "valid_from": "2026-09-01T00:00:00+00:00",
                     "expires_at": "2026-12-31T00:00:00+00:00"})
    store.admit({
        "cycle_ref": "cyc-1", "grant_ref": GRANT,
        "selection": {"selection_digest": DIGEST, "observed_at": NOW,
                      "selected": ["SRV-0001"]},
        "reservation": dict(BUDGET),
        "lease": {"resource": "browser:chromium", "scope": "session",
                  "holder": "cyc-1"},
        "action_key": "act-1", "admitted_at": NOW})
    return store


def a_checkpoint(store: AgentControlStore, **over) -> dict:
    """One valid checkpoint for `SRV-0001`, epoch 1, waiting on `cyc-1`."""
    fields = {"work_item": "SRV-0001", "epoch": 1, "parent_owner": "parent-a",
              "next_safe_action": "accept the child result and continue",
              "waiting_on": {"kind": "child", "refs": ["cyc-1"]},
              "admissions": store.admissions(), "now": NOW}
    fields.update(over)
    return continuation.build_checkpoint(**fields)


def a_data_root(tmp_path: Path) -> dict:
    """A fixture DATA_ROOT, so `paths` resolves both stores inside the test."""
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items",
                 "state/serveros/runtime/leases",
                 "state/serveros/runtime/receipts"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


def cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO), env=env)


# ---------------------------------------------------------------------------
# 1. The record is a capsule and not a transcript.
# ---------------------------------------------------------------------------


class TestTheRecordIsACapsuleAndNotATranscript:
    def test_the_fields_are_exactly_the_capsules_plus_the_two_it_derives(
            self, tmp_path):
        """A closed field set, asserted as an equality so a new field is a change.

        `superseded_by` and `superseded_at` are absent on purpose: they are the
        STORE's statement about what happened to the record afterwards, and a
        caller that could supply them could mark its own record superseded — or
        mark somebody else's.
        """
        record = a_checkpoint(a_store(tmp_path))
        assert set(record) == set(continuation.CHECKPOINT_FIELDS) | {
            "checkpoint_ref", "written_at"}

    def test_the_ref_is_derived_from_the_item_and_the_epoch_and_not_the_owner(
            self, tmp_path):
        """Two owners, one scope, one key — so the collision is readable."""
        store = a_store(tmp_path)
        record = a_checkpoint(store, parent_owner="someone-else")
        assert record["checkpoint_ref"] == "SRV-0001#1"
        assert record["checkpoint_ref"] == continuation.checkpoint_key("SRV-0001", 1)

    def test_a_long_next_action_is_refused_at_the_bound_and_not_before(self):
        """Both sides of the fencepost: 400 is accepted and 401 is not.

        The bound is what makes «recoverable work state» and «transcript
        summary» different objects rather than different intentions, so the
        boundary is worth two assertions rather than one.
        """
        at_the_bound = continuation.build_checkpoint(
            work_item="SRV-0001", epoch=1, parent_owner="parent-a",
            next_safe_action="x" * continuation.MAX_NEXT_ACTION, now=NOW,
            waiting_on={"kind": "window", "until": TOMORROW})
        assert len(at_the_bound["next_safe_action"]) == continuation.MAX_NEXT_ACTION
        with pytest.raises(ValueError, match="401 characters"):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                next_safe_action="x" * (continuation.MAX_NEXT_ACTION + 1), now=NOW,
                waiting_on={"kind": "window", "until": TOMORROW})

    def test_a_scalar_is_one_of_that_list_rather_than_a_second_spelling(self):
        """«one locator» and «a list holding one locator» are the same statement."""
        record = continuation.build_checkpoint(
            work_item="SRV-0001", epoch=1, parent_owner="parent-a",
            next_safe_action="continue", now=NOW,
            waiting_on={"kind": "window", "until": TOMORROW},
            evidence_locators="art://run/1")
        assert record["evidence_locators"] == ["art://run/1"]

    @pytest.mark.parametrize("field", ["remaining_results", "evidence_locators",
                                       "child_attempts", "unresolved_effects",
                                       "write_scope"])
    def test_an_empty_entry_in_any_list_field_is_refused(self, field):
        """A named list with a blank name in it is a list of one fewer than it says."""
        with pytest.raises(ValueError, match=field):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                next_safe_action="continue", now=NOW,
                waiting_on={"kind": "window", "until": TOMORROW},
                **{field: ["something", "  "]})

    def test_an_absent_list_field_is_an_empty_list_and_not_none(self):
        """A reader should not have to tell «none named» from «no key»."""
        record = continuation.build_checkpoint(
            work_item="SRV-0001", epoch=1, parent_owner="parent-a",
            next_safe_action="continue", now=NOW,
            waiting_on={"kind": "window", "until": TOMORROW})
        assert record["remaining_results"] == []
        assert record["write_scope"] == []
        assert record["requirement_revision"] is None


class TestTheShapesThatAreNotACheckpointAreRefused:
    @pytest.mark.parametrize("over,expected", [
        ({"work_item": ""}, "without a work_item"),
        ({"parent_owner": "  "}, "without a parent_owner"),
        ({"next_safe_action": ""}, "next_safe_action is empty"),
        ({"epoch": 0}, "not a positive integer"),
        ({"epoch": -1}, "not a positive integer"),
        ({"epoch": "1"}, "not a positive integer"),
        ({"epoch": True}, "not a positive integer"),
    ])
    def test_the_required_fields(self, over, expected):
        store_kwargs = dict(work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                            next_safe_action="continue", now=NOW,
                            waiting_on={"kind": "window", "until": TOMORROW})
        store_kwargs.update(over)
        with pytest.raises(ValueError, match=expected):
            continuation.build_checkpoint(**store_kwargs)

    def test_a_bool_is_not_an_epoch_even_though_python_says_it_is_an_int(self):
        """`isinstance(True, int)` is True, so the check is spelled out.

        An epoch of `True` would file a checkpoint at `#1` and read back as
        epoch 1 — a second owner for the first epoch's scope, arriving through
        a type coercion rather than through a decision.
        """
        with pytest.raises(ValueError):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=True, parent_owner="parent-a",
                next_safe_action="continue", now=NOW,
                waiting_on={"kind": "window", "until": TOMORROW})

    def test_waiting_on_must_be_a_mapping(self):
        with pytest.raises(ValueError, match="must be a mapping"):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                next_safe_action="continue", now=NOW, waiting_on=None)

    def test_a_kind_nothing_can_check_is_refused_by_name(self):
        """`item` is the third kind the module documents as deliberately absent."""
        with pytest.raises(ValueError, match=r"\['child', 'window'\]"):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                next_safe_action="continue", now=NOW,
                waiting_on={"kind": "item", "refs": ["SRV-0002"]})

    def test_a_wait_with_no_kind_at_all_says_so(self):
        with pytest.raises(ValueError, match="kind is absent"):
            continuation.build_checkpoint(
                work_item="SRV-0001", epoch=1, parent_owner="parent-a",
                next_safe_action="continue", now=NOW, waiting_on={})


class TestAChildWaitIsCheckedAgainstTheAdmissions:
    def test_a_child_this_store_does_not_hold_is_refused(self, tmp_path):
        """«要求用户复制结果» starts exactly here: a wait on an invisible child.

        A child nobody can look up is a result that has to be carried by hand,
        which is the failure `docs/SCENARIO-ACCEPTANCE.md` names — so the
        refusal is the feature and the message says which ref and why.
        """
        store = a_store(tmp_path)
        with pytest.raises(ValueError, match="holds no admission for"):
            a_checkpoint(store, waiting_on={"kind": "child",
                                            "refs": ["cyc-not-admitted"]})

    def test_a_child_wait_naming_no_child_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(ValueError, match="names no child"):
            a_checkpoint(store, waiting_on={"kind": "child", "refs": []})

    def test_a_child_the_store_holds_is_accepted_and_kept_as_given(self, tmp_path):
        store = a_store(tmp_path)
        record = a_checkpoint(store, waiting_on={"kind": "child",
                                                 "refs": ["cyc-1"]})
        assert record["waiting_on"] == {"kind": "child", "refs": ["cyc-1"]}

    def test_a_retired_admission_still_answers_for_the_child_it_records(
            self, tmp_path):
        """Retirement releases the key and KEEPS the record, so the wait stands.

        `retire_admission` is not a cancellation: the cycle is still the thing
        this Parent is waiting on, and a store that answered «no such child»
        after a retire would make a Parent's own recorded wait unreadable at
        exactly the moment the child finished.
        """
        store = a_store(tmp_path)
        store.retire_admission("cyc-1", at=NOW, outcome="dispatched elsewhere")
        record = a_checkpoint(store, waiting_on={"kind": "child", "refs": ["cyc-1"]})
        assert record["waiting_on"]["refs"] == ["cyc-1"]


class TestAWindowWaitNeedsAnInstantThatHasNotPassed:
    def test_an_instant_in_the_past_is_refused(self, tmp_path):
        """A window that already closed is not a wait.

        Saving one would make the record read as awaiting a continuation that
        is owed NOW, which is the shape a reader has no way to tell from a
        Parent that is genuinely waiting.
        """
        store = a_store(tmp_path)
        with pytest.raises(ValueError, match="not after the moment"):
            a_checkpoint(store, waiting_on={"kind": "window", "until": NOW})

    def test_a_value_that_is_not_an_instant_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(ValueError, match="is not one"):
            a_checkpoint(store, waiting_on={"kind": "window", "until": "soon"})

    def test_a_naive_instant_is_read_as_utc_rather_than_refused(self):
        """Every writer in this tree writes an offset; a naive one is UTC.

        The same tolerance `runtime/cli.py`'s receipt comparison uses, and the
        reason is the same: a comparison that refused a naive instant would
        make the answer depend on a spelling instead of on the ordering.
        """
        record = continuation.build_checkpoint(
            work_item="SRV-0001", epoch=1, parent_owner="parent-a",
            next_safe_action="continue", now="2026-09-26T10:00:00",
            waiting_on={"kind": "window", "until": TOMORROW})
        assert record["waiting_on"]["until"] == TOMORROW


# ---------------------------------------------------------------------------
# 2. The scope has one owner, and the epoch is how it changes hands.
# ---------------------------------------------------------------------------


class TestTheScopeHasOneOwner:
    def test_a_write_lands_and_says_it_is_not_a_replay(self, tmp_path):
        store = a_store(tmp_path)
        out = store.put_checkpoint(a_checkpoint(store))
        assert out["replayed"] is False
        assert store.get_checkpoint("SRV-0001#1")["parent_owner"] == "parent-a"

    def test_a_record_without_a_ref_is_refused(self, tmp_path):
        """The key is derived by `build_checkpoint`, so a record that has none
        did not come through it — and could be filed over another Parent's."""
        store = a_store(tmp_path)
        with pytest.raises(StoreError, match="no checkpoint_ref"):
            store.put_checkpoint({"work_item": "SRV-0001", "epoch": 1})

    def test_a_second_live_owner_for_the_scope_is_refused_by_name(self, tmp_path):
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        with pytest.raises(StoreError, match="already holds a live checkpoint"):
            store.put_checkpoint(a_checkpoint(store, parent_owner="parent-b",
                                              next_safe_action="a different plan"))
        assert store.get_checkpoint("SRV-0001#1")["parent_owner"] == "parent-a"

    def test_the_refusal_names_who_holds_it_and_when_they_wrote(self, tmp_path):
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        with pytest.raises(StoreError) as raised:
            store.put_checkpoint(a_checkpoint(store, parent_owner="parent-b",
                                              next_safe_action="another plan"))
        assert "parent-a" in str(raised.value)
        assert NOW in str(raised.value)

    def test_a_higher_epoch_takes_the_scope_over_in_the_same_transaction(
            self, tmp_path):
        """One live owner, and it is the later one.

        Not «refuse and let the caller supersede first»: between the two writes
        there would be a moment with two owners for one Item, and
        `docs/COLLABORATION-CONTRACT.md` says there is one. This is also why
        the assertion is on the LIVE set rather than on the new record alone —
        a store that merely added the second record would pass the second
        assertion and fail this one.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        assert sorted(store.live_checkpoints()) == ["SRV-0001#2"]
        assert sorted(store.checkpoints()) == ["SRV-0001#1", "SRV-0001#2"]

    def test_the_taken_over_record_says_who_took_it_over_and_when(
            self, tmp_path):
        """Recorded on the OLD record, so what was replaced stays readable."""
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          now=LATER,
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        old = store.get_checkpoint("SRV-0001#1")
        assert old["superseded_by"] == "SRV-0001#2"
        assert old["superseded_at"] == LATER

    def test_a_new_record_below_the_live_epoch_is_refused(self, tmp_path):
        """A Parent from an earlier epoch cannot write into the current one.

        This is «旧结果晚到覆盖新epoch» stated as a store rule. `build_checkpoint`
        derives the key, so a same-Item lower epoch normally collides with the
        record already there and takes the refusal above; the hand-built record
        here is the other door into the same rule.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        with pytest.raises(StoreError, match="cannot write into a later one"):
            store.put_checkpoint({"checkpoint_ref": "SRV-0001#9", "work_item":
                                  "SRV-0001", "epoch": 1, "parent_owner": "parent-c",
                                  "waiting_on": {"kind": "window", "until": LATER},
                                  "next_safe_action": "x", "written_at": NOW})

    def test_two_items_do_not_share_a_scope(self, tmp_path):
        """Uniqueness is per Item, so a second Item's checkpoint is a new scope."""
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, work_item="SRV-0002",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        assert sorted(store.live_checkpoints()) == ["SRV-0001#1", "SRV-0002#1"]

    def test_the_record_is_on_disk_and_not_only_in_memory(self, tmp_path):
        """A store read back by a second process is the whole point of the record."""
        path = tmp_path / "store.json"
        a = a_store(tmp_path)
        a.put_checkpoint(a_checkpoint(a))
        again = AgentControlStore(path)
        assert again.get_checkpoint("SRV-0001#1")["next_safe_action"] == \
            "accept the child result and continue"

    def test_a_store_file_written_before_checkpoints_existed_still_loads(
            self, tmp_path):
        """A node's `store.json` predates this slice, so the key may be absent.

        Backward compatibility here is not politeness: this file lives on a
        running node, and a store that refused to load because a collection it
        had never heard of was missing would take the whole control plane down
        on the upgrade.
        """
        path = tmp_path / "store.json"
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        blob = json.loads(path.read_text())
        del blob["checkpoints"]
        path.write_text(json.dumps(blob))
        older = AgentControlStore(path)
        assert older.get_checkpoint("SRV-0001#1") is None
        assert older.checkpoints() == {}
        assert list(older.admissions()) == ["cyc-1"]


class TestAReplayIsNotASecondWrite:
    def test_an_equal_record_written_at_another_instant_is_a_replay(self, tmp_path):
        """The retry that matters is the one that arrives later.

        Built with a DIFFERENT `now` on purpose: with the same one the records
        would be equal without the store having to subtract anything, and the
        subtraction under test would never be exercised. This is why
        `CHECKPOINT_STORE_FIELDS` exists.
        """
        store = a_store(tmp_path)
        first = store.put_checkpoint(a_checkpoint(store))
        again = store.put_checkpoint(a_checkpoint(store, now=LATER))
        assert first["replayed"] is False
        assert again["replayed"] is True
        assert again["written_at"] == NOW, "the FIRST write's instant is the record"

    def test_the_stored_record_does_not_carry_the_replay_flag(self, tmp_path):
        """`replayed` is a fact about the call, not about the record.

        `admit` keeps the same arrangement, and for the same reason: a stored
        field would make the second replay differ from the first by that field
        alone, so the subtraction that recognises a replay would stop working
        after the first one.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, now=LATER))
        assert "replayed" not in store.get_checkpoint("SRV-0001#1")

    def test_a_changed_record_for_the_same_scope_is_not_a_replay(self, tmp_path):
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        with pytest.raises(StoreError, match="already holds a live checkpoint"):
            store.put_checkpoint(a_checkpoint(store, next_safe_action="something else",
                                              now=LATER))

    def test_a_retry_of_an_accepted_write_survives_the_scope_changing_hands(
            self, tmp_path):
        """THE ONE THAT FAILS WITHOUT `CHECKPOINT_STORE_FIELDS`.

        A superseded record carries `superseded_by` and `superseded_at`, which
        its writer never wrote. Compare the whole record and a retry of a write
        the store ALREADY ACCEPTED is answered «your record is not here» — the
        one thing a replay must never say, and it would be said to the Parent
        that timed out on the original call and is retrying it.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        retried = store.put_checkpoint(a_checkpoint(store, now=LATER))
        assert retried["replayed"] is True
        assert retried["superseded_by"] == "SRV-0001#2", \
            "the honest answer is the stored record, which has been taken over"

    def test_a_changed_write_into_a_taken_over_scope_is_refused(self, tmp_path):
        """The replaced Parent cannot write new state into its old epoch."""
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        with pytest.raises(StoreError, match="was taken over"):
            store.put_checkpoint(a_checkpoint(store, next_safe_action="a new plan",
                                              now=LATER))


class TestSupercedingDirectly:
    def test_it_names_a_successor_or_it_is_refused(self, tmp_path):
        """«nobody owns this» is a different fact from «someone else does»."""
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        with pytest.raises(StoreError, match="needs the ref of what took"):
            store.supersede_checkpoint("SRV-0001#1", at=LATER, by="")

    def test_a_ref_this_store_does_not_hold_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError, match="no checkpoint to supersede"):
            store.supersede_checkpoint("SRV-0001#1", at=LATER, by="SRV-0001#2")

    def test_it_is_idempotent_on_the_first_instant(self, tmp_path):
        """The question it answers is WHEN the scope changed hands.

        A second call that moved that instant would move it to whenever somebody
        last mentioned it — the same rule `retire_admission` keeps.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        first = store.supersede_checkpoint("SRV-0001#1", at=LATER, by="SRV-0001#2")
        second = store.supersede_checkpoint("SRV-0001#1", at=TOMORROW,
                                            by="SRV-0001#2")
        assert first["superseded_at"] == LATER
        assert second["superseded_at"] == LATER
        assert second["superseded_by"] == "SRV-0001#2"

    def test_a_second_different_successor_is_refused(self, tmp_path):
        """THE OTHER HALF OF THE SAME RULE, AND THE ONE THAT WAS SILENT.

        This used to hand the same scope to `SRV-0001#3` in a second call and
        assert the record went on saying `SRV-0001#2` — the same shape
        `retire_admission` had for `outcome`, and the same defect: a caller told
        nothing while the store answered from a choice nobody made. Two
        successors for one scope is two owners of one Item, which
        `docs/COLLABORATION-CONTRACT.md` does not allow, so the refusal is the
        only answer that keeps the record readable.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.supersede_checkpoint("SRV-0001#1", at=LATER, by="SRV-0001#2")
        with pytest.raises(StoreError) as refusal:
            store.supersede_checkpoint("SRV-0001#1", at=TOMORROW, by="SRV-0001#3")

        assert "SRV-0001#2" in str(refusal.value)
        assert "SRV-0001#3" in str(refusal.value)
        assert store.get_checkpoint("SRV-0001#1")["superseded_by"] == "SRV-0001#2"

    def test_an_absent_ref_answers_none_and_not_an_empty_record(self, tmp_path):
        """«nobody checkpointed this» and «a Parent checkpointed nothing» are
        opposite readings, and a caller that collapsed them would resume from
        an absence."""
        assert a_store(tmp_path).get_checkpoint("SRV-0001#1") is None


# ---------------------------------------------------------------------------
# 3. «No receiver» is true of the fabric, not a missing field.
# ---------------------------------------------------------------------------


class TestTheReceiverIsAskedOfTheFabric:
    def test_with_no_receiver_installed_the_answer_is_the_products_sentence(
            self, tmp_path):
        """`docs/daily-work-cycle.md` §17.4, and the literal is asserted.

        «没有 receiver 时准确显示"checkpoint已保存，待续"» — a paraphrase would be
        a second wording that can drift from the one an acceptance check looks
        for, so what is asserted is the product's own characters.
        """
        store = a_store(tmp_path)
        state = continuation.continuation_state(store.put_checkpoint(a_checkpoint(store)),
                                                receivers_seen=[])
        assert state["state"] == "AWAITING_CONTINUATION"
        assert state["receivers"] == []
        assert "checkpoint 已保存，待续" in state["says"]
        assert "nothing will wake it" in state["says"]

    def test_the_record_cannot_name_its_own_receiver(self, tmp_path):
        """The safety property, stated as a shape.

        If a checkpoint could carry a receiver, a Parent could write «something
        will wake this» and no reader could tell it from a real trigger. So the
        fact lives in the fabric and the record has nowhere to put it.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        assert not [k for k in record if "receiv" in k.lower()]

    def test_a_filled_registry_changes_the_answer_without_touching_the_record(
            self, tmp_path, monkeypatch):
        """The empty tuple is a measurement, not a constant.

        Monkeypatching the registry is what keeps the reader from being
        hardcoded to say «awaiting»: the day the wake half is installed, the
        same record must read as woken-from, and this test is the proof that it
        would — rather than an assertion that today's empty tuple is right.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        monkeypatch.setattr(continuation, "RECEIVERS", ("runtime.tick",))
        assert continuation.receivers() == ("runtime.tick",)
        state = continuation.continuation_state(record)
        assert state["state"] == "RECEIVER_INSTALLED"
        assert state["receivers"] == ["runtime.tick"]

    def test_this_release_has_no_receiver_installed(self):
        """The measurement the module docstring makes, asserted where it ages.

        Not a wish: `runtime/daemon.py`'s tick reads neither admissions nor
        checkpoints, so there is nothing to register. A future slice that
        installs one will have to change this line, which is the point.
        """
        assert continuation.receivers() == ()
        assert continuation.RECEIVERS == ()

    def test_a_taken_over_record_says_so_before_it_answers_about_waking(
            self, tmp_path):
        """«who carries this» is answered before «what would wake it».

        A superseded record's answer to the receiver question is about a Parent
        that no longer owns the Item, so reporting the receiver first would let
        a replaced Parent read as the current one.
        """
        store = a_store(tmp_path)
        store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        state = continuation.continuation_state(store.get_checkpoint("SRV-0001#1"),
                                                receivers_seen=["runtime.tick"])
        assert state["state"] == "SUPERSEDED"
        assert state["superseded_by"] == "SRV-0001#2"

    def test_every_answer_carries_a_sentence(self, tmp_path):
        """A reader that composed its own would compose a different one each
        time, and the first to differ is the one that reports a background run."""
        store = a_store(tmp_path)
        live = store.put_checkpoint(a_checkpoint(store))
        store.put_checkpoint(a_checkpoint(store, epoch=2, parent_owner="parent-b",
                                          waiting_on={"kind": "window",
                                                      "until": TOMORROW}))
        old = store.get_checkpoint("SRV-0001#1")
        for record, seen in ((live, []), (live, ["runtime.tick"]), (old, [])):
            assert continuation.continuation_state(record, receivers_seen=seen)["says"]


# ---------------------------------------------------------------------------
# 4. The wait is evaluated, and evaluating is not waking.
# ---------------------------------------------------------------------------


class TestTheWaitIsEvaluatedAndNotOnlyRecorded:
    """`waiting_on` was written, checked against the store, printed — and never
    evaluated. These are the answers that did not exist.

    The evaluation is against two authorities and no others: the admission
    collection for a child, the clock for a window. Nothing here starts a
    process, and a `WAIT_OVER` is what a NEW Parent reads — it is not a claim
    that something resumed the old one.
    """

    def test_a_child_with_no_recorded_outcome_leaves_the_wait_open(self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=NOW)
        assert wait["state"] == "WAIT_OPEN"
        assert wait["outstanding"] == ["cyc-1"]
        assert wait["settled"] == []

    def test_the_wait_is_over_once_the_child_has_a_recorded_outcome(self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        assert wait["state"] == "WAIT_OVER"
        assert wait["settled"] == ["cyc-1"]
        assert wait["outstanding"] == []

    def test_the_same_record_answers_differently_without_being_rewritten(
            self, tmp_path):
        """The store moved; the capsule did not. That is the whole point.

        A record that had to be rewritten to notice its child had finished would
        be a record whose reader is the Parent that is gone — which is the
        situation the checkpoint exists to survive.
        """
        store = a_store(tmp_path)
        stored = store.put_checkpoint(a_checkpoint(store))
        before = json.loads(json.dumps(stored))
        first = continuation.wait_state(stored, admissions=store.admissions(),
                                        now=NOW)
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        second = continuation.wait_state(stored, admissions=store.admissions(),
                                         now=LATER)
        assert first["state"] == "WAIT_OPEN"
        assert second["state"] == "WAIT_OVER"
        assert stored == before
        # The stored record is the same one, minus the write-result flag the
        # store never keeps (`test_the_stored_record_does_not_carry_the_replay_flag`).
        assert store.get_checkpoint("SRV-0001#1") == {
            k: v for k, v in before.items() if k != "replayed"}

    def test_being_invoked_is_not_having_finished(self, tmp_path):
        """`DISPATCHED` is a receipt, and a receipt is not a result.

        `runtime/bounded_invocation.invoke_once` writes that phase when the child
        was CALLED, and the receipt it writes can even say `EXITED` — the child
        ran and returned. None of that is this store's word for «this cycle's
        outcome is recorded»: `retire_admission` is the only writer of
        `retired_at`, and it is what the evaluator asks for. Reading a phase as a
        result would resume a Parent while its child was still running, or while
        nobody had yet decided what the child's exit meant.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=NOW)
        store.confirm_admission("cyc-1", phase="DISPATCHED",
                                evidence={"action_key": "act-1",
                                          "status": "EXITED"}, at=NOW)
        assert store.admissions()["cyc-1"]["phase"] == "DISPATCHED"
        assert store.admissions()["cyc-1"]["retired_at"] is None
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        assert wait["state"] == "WAIT_OPEN"
        assert wait["outstanding"] == ["cyc-1"]
        assert "not a result" in wait["evidence"][0]["because"]

    def test_a_child_this_store_does_not_hold_is_unanswerable_and_not_not_yet(
            self, tmp_path):
        """The third answer, and the reason there are three.

        «Not yet» would park a Parent forever behind a store that simply is not
        this one; «over» would resume it on no evidence at all. The record is
        honest about which question it could not answer, and names the ref.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        wait = continuation.wait_state(record, admissions={}, now=NOW)
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert wait["unheld"] == ["cyc-1"]
        assert "cyc-1" in wait["says"]

    def test_bringing_no_store_at_all_is_a_different_answer_from_an_empty_one(
            self, tmp_path):
        """Two readings, two repairs: fetch the store, or look for the child.

        Collapsing them would tell a reader that the child is missing from a
        store nobody opened.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        absent = continuation.wait_state(record, now=NOW)
        empty = continuation.wait_state(record, admissions={}, now=NOW)
        assert absent["state"] == empty["state"] == "WAIT_UNANSWERABLE"
        assert absent["detail"] != empty["detail"]
        assert "not looked up at all" in absent["detail"]
        assert "no admission" in empty["detail"]

    def test_several_children_are_over_only_when_the_last_one_is(self, tmp_path):
        store = a_store(tmp_path)
        store.admit({"cycle_ref": "cyc-2", "grant_ref": GRANT,
                     "selection": {"selection_digest": DIGEST, "observed_at": NOW,
                                   "selected": ["SRV-0001"]},
                     "reservation": dict(BUDGET),
                     "lease": {"resource": "browser:chromium", "scope": "session",
                               "holder": "cyc-2"},
                     "action_key": "act-2", "admitted_at": NOW})
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "child",
                                            "refs": ["cyc-1", "cyc-2"]}))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        partial = continuation.wait_state(record, admissions=store.admissions(),
                                          now=LATER)
        assert partial["state"] == "WAIT_OPEN"
        assert partial["settled"] == ["cyc-1"]
        assert partial["outstanding"] == ["cyc-2"]
        store.retire_admission("cyc-2", at=LATER, outcome="accepted")
        whole = continuation.wait_state(record, admissions=store.admissions(),
                                        now=LATER)
        assert whole["state"] == "WAIT_OVER"
        assert sorted(whole["settled"]) == ["cyc-1", "cyc-2"]

    def test_one_unreadable_child_among_settled_ones_is_not_reported_as_over(
            self, tmp_path):
        """«Most of it finished» must not read as «it finished».

        The record is written while the store still held both children — that is
        the only way past `build_checkpoint` — and read after one of them is
        gone. A store can lose an admission between those two moments, and the
        reading has to survive it.
        """
        store = a_store(tmp_path)
        both = {**store.admissions(), "cyc-gone": {"cycle_ref": "cyc-gone"}}
        record = store.put_checkpoint(
            a_checkpoint(store, admissions=both,
                         waiting_on={"kind": "child",
                                     "refs": ["cyc-1", "cyc-gone"]}))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert wait["settled"] == ["cyc-1"]
        assert wait["unheld"] == ["cyc-gone"]

    def test_a_window_that_has_not_closed_is_open_and_says_how_long(
            self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW}))
        wait = continuation.wait_state(record, admissions={}, now=NOW)
        assert wait["state"] == "WAIT_OPEN"
        assert wait["seconds_remaining"] == 86400.0

    def test_a_window_that_has_closed_is_over_and_says_by_how_much(
            self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW}))
        wait = continuation.wait_state(record, admissions={}, now=DAY_AFTER)
        assert wait["state"] == "WAIT_OVER"
        assert wait["over_by_seconds"] == 86400.0

    def test_the_window_is_read_against_the_clock_it_was_given_and_no_other(
            self, tmp_path):
        """The clock is an argument, so this file has no wall clock in it.

        A reader that reached for the time itself would make the same record
        answer differently between two runs of the same test, and the answer
        would be about the machine rather than about the work. The boundary is
        included: exactly at `until` the window is over, which is the same
        comparison `build_checkpoint` refuses a NEW record on.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW}))
        for clock, expected in ((NOW, "WAIT_OPEN"), (LATER, "WAIT_OPEN"),
                                (TOMORROW, "WAIT_OVER"), (DAY_AFTER, "WAIT_OVER")):
            assert continuation.wait_state(record, admissions={}, now=clock)[
                "state"] == expected, clock

    def test_a_window_whose_end_cannot_be_read_is_unanswerable(self, tmp_path):
        """A record this store holds and this evaluator cannot compare."""
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW}))
        broken = dict(record, waiting_on={"kind": "window", "until": "soon"})
        wait = continuation.wait_state(broken, admissions={}, now=NOW)
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert "waiting_on.until" in wait["detail"]

    def test_a_reading_with_no_clock_is_unanswerable_and_not_open(self, tmp_path):
        """`now` has no default either, for the same reason `receivers()` has none.
        A default would be this module answering with a time it made up."""
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW}))
        wait = continuation.wait_state(record, admissions={})
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert "reading clock" in wait["detail"]

    def test_a_kind_nothing_can_evaluate_is_unanswerable_and_named(self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        broken = dict(record, waiting_on={"kind": "another-item",
                                          "refs": ["SRV-9999"]})
        wait = continuation.wait_state(broken, admissions=store.admissions(),
                                       now=NOW)
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert "another-item" in wait["detail"]

    def test_a_record_with_no_wait_at_all_is_unanswerable_and_not_over(
            self, tmp_path):
        """There is no boolean here, so «no wait» cannot fall through to «over».

        A value that defaulted to «over» would resume a Parent on a record whose
        wait never parsed — the shape `build_checkpoint` refuses to write and
        which a hand-edited or older store can still hold.
        """
        wait = continuation.wait_state({"work_item": "SRV-0001", "epoch": 1},
                                       admissions={}, now=NOW)
        assert wait["state"] == "WAIT_UNANSWERABLE"
        assert wait["waiting_on"] is None
        assert "no readable `waiting_on`" in wait["detail"]

    def test_every_answer_names_the_checkpoint_it_is_about(self, tmp_path):
        """A caller holds a mapping of these; an answer without the ref would
        have to be carried beside it, and carried facts get separated."""
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        for admissions, now in ((store.admissions(), NOW), ({}, NOW),
                                (None, NOW), ({}, None)):
            wait = continuation.wait_state(record, admissions=admissions, now=now)
            assert wait["checkpoint_ref"] == "SRV-0001#1", wait["state"]
            assert wait["work_item"] == "SRV-0001"
            assert wait["epoch"] == 1
            assert wait["says"]

    def test_the_three_answers_are_the_closed_vocabulary(self, tmp_path):
        """Asserted as an equality, so a fourth word is a change and not a typo."""
        assert continuation.WAIT_ANSWERS == ("WAIT_OVER", "WAIT_OPEN",
                                             "WAIT_UNANSWERABLE")


class TestAnEvaluatorIsNotAReceiver:
    """The separation that keeps «the child finished» from reading as «something
    is running».

    A WAIT_OVER is the most tempting thing in this module to wire into the wake
    half: the child is done, so surely something should continue the Parent. It
    is exactly the temptation §17.4 names. Nothing continues a Parent here —
    resuming is what a new Parent does — so the evaluator's answer must not move
    the receiver answer by one character.
    """

    def test_a_wait_that_is_over_does_not_make_the_fabric_say_woken_from(
            self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        state = continuation.continuation_state(record, receivers_seen=[])
        assert wait["state"] == "WAIT_OVER"
        assert state["state"] == "AWAITING_CONTINUATION"
        assert "checkpoint 已保存，待续" in state["says"]
        assert "nothing will wake it" in state["says"]

    def test_the_evaluator_says_out_loud_that_it_resumes_nothing(self, tmp_path):
        """The sentence, because a reader who only sees WAIT_OVER will assume."""
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        assert "nothing else" in wait["detail"]
        assert "a new Parent can resume this Item" in wait["says"]

    def test_this_release_still_installs_no_receiver(self):
        """The measurement, re-asserted where a wired evaluator would break it."""
        assert continuation.RECEIVERS == ()
        assert continuation.receivers() == ()


# ---------------------------------------------------------------------------
# 5. What was tried is read back — the third question, and the second field
#    this module reads that used to be written and never looked up.
# ---------------------------------------------------------------------------


class TestWhatWasTriedIsReadBack:
    """`child_attempts` had a writer, a validator and a refusal that explained it,
    and no reader. These are the answers that did not exist.

    It is deliberately NOT the same question as `wait_state`. The wait is about the
    attempt the Parent is currently waiting on — one ref, in `waiting_on`. This is
    about the attempts it has already made, and the difference is the whole reason
    the field exists: a Parent that resumes and reads only its wait can dispatch a
    second child for work a first child has already done.
    """

    def test_an_attempt_the_store_still_holds_open_reads_as_open(self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert made["state"] == "ATTEMPT_OPEN"
        assert made["open"] == ["cyc-1"]
        assert made["retired"] == []
        assert "not a result" in made["evidence"][0]["because"]

    def test_a_retired_attempt_reads_as_retired_and_carries_its_outcome(
            self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert made["state"] == "ATTEMPT_RETIRED"
        assert made["retired"] == ["cyc-1"]
        assert made["evidence"][0]["outcome"] == "accepted"
        assert made["evidence"][0]["retired_at"] == LATER

    def test_the_same_record_answers_differently_without_being_rewritten(
            self, tmp_path):
        """A record whose reader is the Parent that is gone, again.

        The store moved and the capsule did not. This is the second reader that
        has to survive that, and it is the one that decides whether the Parent
        about to resume dispatches a second child.
        """
        store = a_store(tmp_path)
        stored = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        before = json.loads(json.dumps(stored))
        first = continuation.attempts_state(stored, admissions=store.admissions())
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        second = continuation.attempts_state(stored, admissions=store.admissions())
        assert first["state"] == "ATTEMPT_OPEN"
        assert second["state"] == "ATTEMPT_RETIRED"
        assert stored == before

    def test_a_record_that_names_no_attempt_is_its_own_answer(self, tmp_path):
        """THE ONE THAT MUST NOT BE PRINTED AS EITHER OF THE OTHER TWO.

        A Parent that has made no child attempt is not a Parent whose attempts all
        finished, and it is not one whose store cannot say. `build_checkpoint`
        allows the field to be empty, so this is a state and not a gap — and a
        reader that collapsed it into `ATTEMPT_RETIRED` would tell a resuming
        Parent that everything it never started had finished.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(a_checkpoint(store))
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert record["child_attempts"] == []
        assert made["state"] == "ATTEMPT_NONE"
        assert made["attempts"] == []
        assert made["retired"] == made["open"] == made["unheld"] == []
        assert "names no child attempt" in made["detail"]

    def test_an_attempt_this_store_does_not_hold_is_unanswerable_not_unfinished(
            self, tmp_path):
        """The third answer, for the same reason `wait_state` has one.

        `build_checkpoint` refuses to WRITE an attempt nothing can look up when it
        appears in `waiting_on`. It can still appear here, because this field
        records what was already done rather than claiming what the fabric can
        still see — a store that was moved or swept holds exactly this shape — and
        «not yet» would dispatch a second child for work that may well have
        finished.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        made = continuation.attempts_state(record, admissions={})
        assert made["state"] == "ATTEMPT_UNANSWERABLE"
        assert made["unheld"] == ["cyc-1"]
        assert "no admission" in made["detail"]

    def test_bringing_no_store_at_all_is_a_different_answer_from_an_empty_one(
            self, tmp_path):
        """Fetch the store, or go looking for the attempt. Two repairs.

        The absent answer carries no `unheld` list, which is the shape
        `wait_state` gives the same situation: «this store holds no such attempt»
        is a statement about a store that was read, and none was brought. A list
        that appeared in both would put the same word on two different facts.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        absent = continuation.attempts_state(record)
        empty = continuation.attempts_state(record, admissions={})
        assert absent["state"] == empty["state"] == "ATTEMPT_UNANSWERABLE"
        assert absent["detail"] != empty["detail"]
        assert "not looked up at all" in absent["detail"]
        assert "no admission" in empty["detail"]
        assert "unheld" not in absent and empty["unheld"] == ["cyc-1"]

    def test_several_attempts_are_retired_only_when_the_last_one_is(self, tmp_path):
        store = a_store(tmp_path)
        store.admit({"cycle_ref": "cyc-2", "grant_ref": GRANT,
                     "selection": {"selection_digest": DIGEST, "observed_at": NOW,
                                   "selected": ["SRV-0001"]},
                     "reservation": dict(BUDGET),
                     "lease": {"resource": "browser:chromium", "scope": "session",
                               "holder": "cyc-2"},
                     "action_key": "act-2", "admitted_at": NOW})
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1", "cyc-2"]))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        partial = continuation.attempts_state(record, admissions=store.admissions())
        assert partial["state"] == "ATTEMPT_OPEN"
        assert partial["retired"] == ["cyc-1"]
        assert partial["open"] == ["cyc-2"]
        store.retire_admission("cyc-2", at=LATER, outcome="accepted")
        whole = continuation.attempts_state(record, admissions=store.admissions())
        assert whole["state"] == "ATTEMPT_RETIRED"
        assert sorted(whole["retired"]) == ["cyc-1", "cyc-2"]

    def test_one_unreadable_attempt_among_retired_ones_is_not_reported_as_finished(
            self, tmp_path):
        """Precedence, stated: «most of it finished» must not read as «it did»."""
        store = a_store(tmp_path)
        gone = {**store.admissions(), "cyc-gone": {"cycle_ref": "cyc-gone"}}
        record = store.put_checkpoint(
            a_checkpoint(store, admissions=gone,
                         child_attempts=["cyc-1", "cyc-gone"]))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert made["state"] == "ATTEMPT_UNANSWERABLE"
        assert made["retired"] == ["cyc-1"]
        assert made["unheld"] == ["cyc-gone"]

    def test_every_answer_names_the_checkpoint_it_is_about(self, tmp_path):
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"]))
        for admissions in (store.admissions(), {}, None):
            made = continuation.attempts_state(record, admissions=admissions)
            assert made["checkpoint_ref"] == "SRV-0001#1", made["state"]
            assert made["work_item"] == "SRV-0001"
            assert made["epoch"] == 1
            assert made["attempts"] == ["cyc-1"]
            assert made["says"]

    def test_the_four_answers_are_the_closed_vocabulary(self):
        """Asserted as an equality, so a fifth word is a change and not a typo."""
        assert continuation.ATTEMPT_ANSWERS == ("ATTEMPT_RETIRED", "ATTEMPT_OPEN",
                                                "ATTEMPT_UNANSWERABLE",
                                                "ATTEMPT_NONE")

    def test_it_takes_no_clock(self):
        """There is no instant in «what became of this attempt».

        `wait_state` needs one because a window is a comparison. An attempt is not:
        it is a record in a store, and the store either holds a retirement or it
        does not. A `now` parameter here would be an invitation to expire an
        attempt by elapsed time, which is exactly the reading `retire_admission`
        being the only writer of `retired_at` rules out.
        """
        parameters = list(inspect.signature(
            continuation.attempts_state).parameters)
        assert parameters == ["record", "admissions"], (
            "a clock or a board reaching this function would be a second authority "
            "for a question the store already answers")

    def test_what_was_tried_is_not_what_is_being_waited_for(self, tmp_path):
        """The separation, on one record, with the two answers differing.

        A checkpoint waiting on `cyc-2` and recording `cyc-1` as already attempted
        is an ordinary record: the child it waits on is finished and the attempt it
        already made is not. A reader that used one answer for the other would say
        either «resume, everything is done» or «wait, an attempt is open», and the
        second is the failure the field exists to prevent.
        """
        store = a_store(tmp_path)
        store.admit({"cycle_ref": "cyc-2", "grant_ref": GRANT,
                     "selection": {"selection_digest": DIGEST, "observed_at": NOW,
                                   "selected": ["SRV-0001"]},
                     "reservation": dict(BUDGET),
                     "lease": {"resource": "browser:chromium", "scope": "session",
                               "holder": "cyc-2"},
                     "action_key": "act-2", "admitted_at": NOW})
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"],
                         waiting_on={"kind": "child", "refs": ["cyc-2"]}))
        store.retire_admission("cyc-2", at=LATER, outcome="accepted")
        wait = continuation.wait_state(record, admissions=store.admissions(),
                                       now=LATER)
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert wait["state"] == "WAIT_OVER"
        assert made["state"] == "ATTEMPT_OPEN"

    def test_an_acceptance_is_not_a_state_it_can_answer(self, tmp_path):
        """What is accepted lives on the record, and this answer says so.

        `work checkpoint --evidence` calls a locator «an accepted evidence
        locator», so the record is where acceptance is written. `attempts_state`
        must not invent a per-attempt acceptance: the two facts are reconciled by
        whoever resumes, and a store answer that guessed which attempt a locator
        came from would be answering a question nothing recorded.
        """
        store = a_store(tmp_path)
        record = store.put_checkpoint(
            a_checkpoint(store, child_attempts=["cyc-1"],
                         evidence_locators=["art://run/1"]))
        store.retire_admission("cyc-1", at=LATER, outcome="accepted")
        made = continuation.attempts_state(record, admissions=store.admissions())
        assert "ACCEPTED" not in continuation.ATTEMPT_ANSWERS
        assert set(made["evidence"][0]) == {"ref", "held", "state", "phase",
                                           "retired_at", "outcome", "because"}, (
            "an acceptance is not one of the facts this answer can carry; the "
            "outcome the cycle recorded is a string and is carried verbatim")
        assert made["evidence"][0]["outcome"] == "accepted"


class TestEveryDeclaredFieldIsRenderedOrExempt:
    """The mechanism, not the instance.

    `child_attempts` is the field this slice found written and never read. The
    reason it could stay that way is that nothing connected the record's field list
    to the reading that prints it, so the fix is that connection: every field in
    `CHECKPOINT_FIELDS` is either read by `runtime/cli.py`'s
    `cmd_work_continuations` — the one surface a resuming Parent reads — or it is
    in the named exemption table below with the reason it does not need to be.

    Read from the AST of the function and not from its text, so that a field
    mentioned in a comment or in a docstring does not count as read.
    """

    #: Fields that are deliberately not printed, each with its reason. One entry,
    #: and the bar for a second is that the field is genuinely carried elsewhere
    #: on the same screen rather than merely that nobody asked for it.
    EXEMPT = {
        "epoch": "carried by the ref: `checkpoint_key` is `{work_item}#{epoch}` and "
                 "the ref is the heading of every record this verb prints, so a "
                 "separate epoch line would be the same fact twice",
    }

    def read_by_the_verb(self) -> set:
        source = (REPO / "runtime" / "cli.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_work_continuations":
                break
        else:
            raise AssertionError("runtime/cli.py has no cmd_work_continuations")
        read = set()
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "get"
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id == "record"
                    and inner.args
                    and isinstance(inner.args[0], ast.Constant)
                    and isinstance(inner.args[0].value, str)):
                read.add(inner.args[0].value)
            elif (isinstance(inner, ast.Subscript)
                    and isinstance(inner.value, ast.Name)
                    and inner.value.id == "record"
                    and isinstance(inner.slice, ast.Constant)
                    and isinstance(inner.slice.value, str)):
                read.add(inner.slice.value)
        return read

    def test_every_declared_field_is_rendered_or_named_as_exempt(self):
        read = self.read_by_the_verb()
        unread = [field for field in continuation.CHECKPOINT_FIELDS
                  if field not in read and field not in self.EXEMPT]
        assert unread == [], (
            f"`work continuations` does not read {unread} off the checkpoint it is "
            f"printing, and they are not in EXEMPT. A field a resuming Parent "
            f"cannot see is a field the record carries for nobody — which is what "
            f"`child_attempts` was before this slice. Either print it or add it to "
            f"EXEMPT with the reason it is already carried on the same screen")

    def test_the_exemption_table_does_not_outlive_its_reason(self):
        """An entry for a field that is read anyway is a stale exemption, and a
        stale exemption is how this test stops meaning anything."""
        read = self.read_by_the_verb()
        stale = sorted(field for field in self.EXEMPT if field in read)
        assert stale == [], (
            f"{stale} are exempted and read as well; the exemption is dead and "
            f"should be removed so the reason cannot drift from the code")

    def test_every_exempt_field_is_a_real_field(self):
        unknown = sorted(set(self.EXEMPT) - set(continuation.CHECKPOINT_FIELDS))
        assert unknown == [], f"EXEMPT names fields the record does not carry: {unknown}"

    def test_every_exempt_field_states_a_reason(self):
        thin = sorted(field for field, why in self.EXEMPT.items()
                      if len(str(why).split()) < 8)
        assert thin == [], (
            f"{thin} are exempted without a reason. «Nobody asked for it» is not a "
            f"reason; «it is already on the screen, here» is")


# ---------------------------------------------------------------------------
# 6. The layer boundary this module keeps.
# ---------------------------------------------------------------------------


class TestThisModuleImportsNoOtherLayer:
    """`runtime/agent_control_store.py` imports nothing from this repository, and
    `intake/focus_source.py` keeps the same boundary. The contract module has to
    be readable alongside the store — it is the only thing that can say what a
    stored checkpoint means — so it keeps the boundary too. Asserted rather than
    intended, because an import is exactly the kind of change that arrives with
    a good reason attached."""

    REPO_PACKAGES = {"abilities", "context", "documents", "fleet", "intake",
                     "mcp", "memory", "relations", "runtime", "scripts", "space"}

    def test_no_repository_module_is_imported(self):
        tree = ast.parse((RUNTIME / "continuation.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert imported & self.REPO_PACKAGES == set(), (
            f"runtime/continuation.py imports {sorted(imported & self.REPO_PACKAGES)}; "
            f"the store layer has to be able to read it without the fabric")


# ---------------------------------------------------------------------------
# 7. The two verbs, driven through the CLI.
# ---------------------------------------------------------------------------


class TestTheVerbsDriveTheStore:
    """Driven as a subprocess, because the fixture that isolates the store is a
    `DATA_ROOT` and nothing in-process can move it. The module smoke test above
    covers the logic; what these cover is the wiring nothing else reaches —
    argparse, `emit`, and the `--json` shape a consumer reads."""

    @pytest.fixture()
    def env(self, tmp_path: Path) -> dict:
        env = a_data_root(tmp_path)
        a_store(Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control")
        return env

    def checkpoint(self, env, *extra):
        return cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a",
                   "--next-action", "accept the child result and continue",
                   "--waiting-on-child", "cyc-1", "--at", NOW,
                   *extra, env_extra=env)

    def test_a_checkpoint_is_written_and_reported_with_its_continuation_state(
            self, env):
        done = self.checkpoint(env)
        assert done.returncode == 0, done.stderr
        assert "SRV-0001#1" in done.stdout
        assert "AWAITING_CONTINUATION" in done.stdout
        assert "checkpoint 已保存，待续" in done.stdout

    def test_a_retry_reports_itself_as_a_replay(self, env):
        assert self.checkpoint(env).returncode == 0
        again = self.checkpoint(env)
        assert again.returncode == 0, again.stderr
        assert "replayed" in again.stdout

    def test_a_wait_the_store_cannot_confirm_writes_nothing(self, env):
        """The refusal has to be a refusal and not a quiet write of a weaker record."""
        done = cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a", "--next-action", "continue",
                   "--waiting-on-child", "cyc-not-admitted", "--at", NOW,
                   env_extra=env)
        assert done.returncode != 0
        assert "Nothing was written" in done.stderr
        listed = cli("work", "continuations", env_extra=env)
        assert "no Parent has checkpointed on this node" in listed.stdout

    def test_a_checkpoint_with_nothing_to_wait_for_is_refused(self, env):
        """`docs/SCENARIO-ACCEPTANCE.md` names «第一版实现就返回等用户继续» as the
        failure, and this is that failure arriving as a command line."""
        done = cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a", "--next-action", "continue",
                   "--at", NOW, env_extra=env)
        assert done.returncode != 0
        assert "第一版实现就返回等用户继续" in done.stderr
        assert "Nothing was written" in done.stderr

    def test_both_ways_of_waiting_at_once_is_refused(self, env):
        done = self.checkpoint(env, "--waiting-on-window", TOMORROW)
        assert done.returncode != 0
        assert "both" in done.stderr
        assert "Nothing was written" in done.stderr

    def test_the_reader_shows_a_taken_over_record_and_its_successor(self, env):
        assert self.checkpoint(env).returncode == 0
        assert cli("work", "checkpoint", "SRV-0001", "--epoch", "2",
                   "--parent", "parent-b", "--next-action", "carry on",
                   "--waiting-on-window", TOMORROW, "--at", LATER,
                   env_extra=env).returncode == 0
        listed = cli("work", "continuations", env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "1 live of 2 recorded" in listed.stdout
        assert "SUPERSEDED" in listed.stdout
        assert "AWAITING_CONTINUATION" in listed.stdout

    def test_the_item_filter_narrows_the_reading(self, env):
        assert self.checkpoint(env).returncode == 0
        assert cli("work", "checkpoint", "SRV-0002", "--epoch", "1",
                   "--parent", "parent-b", "--next-action", "carry on",
                   "--waiting-on-window", TOMORROW, "--at", LATER,
                   env_extra=env).returncode == 0
        listed = cli("work", "continuations", "--item", "SRV-0002", env_extra=env)
        assert "SRV-0002#1" in listed.stdout
        assert "SRV-0001#1" not in listed.stdout

    def test_the_receiver_line_says_none_is_installed(self, env):
        """The sentence a person reads, and the one that must not become
        «running». §17.4 again, at the surface where it is actually read."""
        listed = cli("work", "continuations", env_extra=env)
        assert listed.returncode == 0
        assert "receivers" in listed.stdout
        assert "none installed" in listed.stdout

    def test_the_writing_verb_reports_the_wait_it_has_just_recorded(self, env):
        """Free at write time, and the only moment nobody is looking.

        A Parent that checkpoints on a child whose result has already arrived
        has waited for nothing, and the verb that made the record is the last
        reader in a position to say so without being asked.
        """
        done = self.checkpoint(env)
        assert done.returncode == 0, done.stderr
        assert "wait          WAIT_OPEN" in done.stdout

        cli("work", "retire", "cyc-1", "--outcome", "accepted", "--at", LATER,
            env_extra=env)
        late = cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a",
                   "--next-action", "accept the child result and continue",
                   "--waiting-on-child", "cyc-1", "--at", LATER, env_extra=env)
        assert late.returncode == 0, late.stderr
        assert "wait          WAIT_OVER" in late.stdout
        assert "replayed" in late.stdout

    def test_a_window_wait_is_read_against_the_clock_the_reader_is_given(
            self, env):
        """The read half, and the flag that makes it deterministic.

        Without `--at` this verb would compare a stored instant with whatever
        time it happened to be, and the same store would read differently on
        two runs — an answer about the machine rather than about the work.
        """
        assert cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a", "--next-action", "carry on",
                   "--waiting-on-window", TOMORROW, "--at", NOW,
                   env_extra=env).returncode == 0
        open_read = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert open_read.returncode == 0, open_read.stderr
        assert "WAIT_OPEN" in open_read.stdout
        assert "0 over, 1 still open, 0 unreadable" in open_read.stdout

        due = cli("work", "continuations", "--now", TOMORROW, env_extra=env)
        assert due.returncode == 0, due.stderr
        assert "WAIT_OVER" in due.stdout
        assert "resumable     SRV-0001#1" in due.stdout

    def test_the_reading_names_the_resumable_checkpoint_and_keeps_saying_awaiting(
            self, env):
        """The two questions on one line, which is where they are hardest to
        confuse: the child has finished AND nothing on this node will wake the
        Parent that wrote this down."""
        assert self.checkpoint(env).returncode == 0
        assert cli("work", "retire", "cyc-1", "--outcome", "accepted",
                   "--at", LATER, env_extra=env).returncode == 0
        listed = cli("work", "continuations", "--now", LATER, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "SRV-0001#1  AWAITING_CONTINUATION  wait WAIT_OVER" in listed.stdout
        assert "checkpoint 已保存，待续" in listed.stdout
        assert "a new Parent can resume this Item" in listed.stdout

    def test_a_child_this_store_does_not_hold_reads_as_unreadable_not_as_waiting(
            self, env):
        """The store is the authority, and a record can outlive its admission.

        `build_checkpoint` refuses to WRITE this shape — that is the test above
        it — and it can still be read: a store that was moved, swept or replaced
        holds checkpoints naming children it no longer has. The hand-edit below
        is the only way to produce that state through the CLI, and it is what
        such a store looks like on disk. The reading must say «cannot say», in
        both directions: «not yet» would park a Parent forever behind a store
        problem, and «over» would resume one on no evidence.
        """
        assert self.checkpoint(env).returncode == 0
        path = (Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control"
                / "store.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "cyc-1" in data["admissions"]
        del data["admissions"]["cyc-1"]
        path.write_text(json.dumps(data), encoding="utf-8")

        listed = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "WAIT_UNANSWERABLE" in listed.stdout
        assert "1 unreadable" in listed.stdout
        assert "not in this store" in listed.stdout

    def test_the_json_round_trip_carries_the_record_and_its_state(self, env):
        written = cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                      "--parent", "parent-a", "--next-action", "continue",
                      "--waiting-on-child", "cyc-1", "--remaining", "R2",
                      "--at", NOW, "--json", env_extra=env)
        assert written.returncode == 0, written.stderr
        blob = json.loads(written.stdout)
        assert blob["checkpoint"]["checkpoint_ref"] == "SRV-0001#1"
        assert blob["checkpoint"]["remaining_results"] == ["R2"]
        assert blob["continuation"]["state"] == "AWAITING_CONTINUATION"

        listed = cli("work", "continuations", "--json", env_extra=env)
        assert listed.returncode == 0, listed.stderr
        blob = json.loads(listed.stdout)
        assert blob["receivers"] == []
        assert sorted(blob["checkpoints"]) == ["SRV-0001#1"]
        assert blob["states"]["SRV-0001#1"]["state"] == "AWAITING_CONTINUATION"

    def test_the_verb_acts_on_nothing(self, env):
        """It records and exits. Nothing is woken, released or restarted.

        Asserted because the temptation after a checkpoint exists is to have the
        writing verb also do something about it, and «the verb that saves also
        resumes» is how a saved checkpoint becomes a claimed background run.
        """
        before = set(os.listdir(Path(env["DATA_ROOT"]) / "state/serveros/runtime"))
        assert self.checkpoint(env).returncode == 0
        after = set(os.listdir(Path(env["DATA_ROOT"]) / "state/serveros/runtime"))
        assert before == after

    def test_the_reader_prints_what_was_tried_and_what_became_of_it(self, env):
        """The line that did not exist, on the one surface a resuming Parent reads.

        `--attempt` had a help string, a validation and a refusal explaining why
        the field exists; `work continuations` printed none of it. This is the
        whole slice at the surface where it matters: the same record, read twice,
        with the store having moved in between.
        """
        assert cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a",
                   "--next-action", "accept the child result and continue",
                   "--waiting-on-child", "cyc-1", "--attempt", "cyc-1",
                   "--at", NOW, env_extra=env).returncode == 0
        open_read = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert open_read.returncode == 0, open_read.stderr
        assert "ATTEMPT_OPEN" in open_read.stdout
        assert "  attempts      cyc-1" in open_read.stdout
        assert "1 with one still open" in open_read.stdout

        assert cli("work", "retire", "cyc-1", "--outcome", "accepted",
                   "--at", LATER, env_extra=env).returncode == 0
        after = cli("work", "continuations", "--now", LATER, env_extra=env)
        assert after.returncode == 0, after.stderr
        assert "ATTEMPT_RETIRED" in after.stdout
        assert "accepted" in after.stdout
        assert "1 all finished" in after.stdout
        assert "so resuming does not mean dispatching a second child" in after.stdout

    def test_a_checkpoint_that_tried_nothing_says_so_and_not_finished(self, env):
        """The fourth answer, at the line a person reads.

        Most checkpoints name no attempt, and the reading has to say that rather
        than print a blank or, worse, `ATTEMPT_RETIRED` — a Parent told its
        attempts all finished when it made none is a Parent told to skip work.
        """
        assert self.checkpoint(env).returncode == 0
        listed = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "ATTEMPT_NONE" in listed.stdout
        assert "— this record names no child attempt" in listed.stdout
        assert "1 naming none" in listed.stdout

    def test_an_absent_requirement_and_an_absent_evidence_are_printed_in_words(
            self, env):
        """Absent and empty are different readings, so neither prints as a blank."""
        assert self.checkpoint(env).returncode == 0
        listed = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "does not name a revision" in listed.stdout
        assert "carries no accepted evidence" in listed.stdout

    def test_a_named_requirement_and_accepted_evidence_are_printed(self, env):
        """The two facts the attempts are reconciled against, and both were absent
        from this reading while being written by the verb next to it."""
        assert cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a",
                   "--next-action", "accept the child result and continue",
                   "--waiting-on-child", "cyc-1", "--requirement", "rev-7",
                   "--evidence", "art://run/1", "--at", NOW,
                   env_extra=env).returncode == 0
        listed = cli("work", "continuations", "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        assert "  requirement   rev-7" in listed.stdout
        assert "  accepted      art://run/1" in listed.stdout

    def test_the_json_round_trip_carries_the_attempts_answer(self, env):
        assert cli("work", "checkpoint", "SRV-0001", "--epoch", "1",
                   "--parent", "parent-a", "--next-action", "continue",
                   "--waiting-on-child", "cyc-1", "--attempt", "cyc-1",
                   "--at", NOW, env_extra=env).returncode == 0
        listed = cli("work", "continuations", "--json", "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        blob = json.loads(listed.stdout)
        assert blob["attempts"]["SRV-0001#1"]["state"] == "ATTEMPT_OPEN"
        assert blob["attempts"]["SRV-0001#1"]["attempts"] == ["cyc-1"]
