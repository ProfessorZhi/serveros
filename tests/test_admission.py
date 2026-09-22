#!/usr/bin/env python3
"""Atomic admission: one write for the selection, the bounds and the intent.

WHY THIS FILE EXISTS
--------------------
P6's last sentence asks for the selection, the budget reservation and the
lease/dispatch intent to be in one authoritative transaction, with a
RECOVERABLE IDEMPOTENT RELATION to the two stores that perform the other halves
rather than a pretended cross-store commit. `runtime/agent_control_store.admit`
is that transaction and `intake/daily_cycle.admission_plan` is the policy that
decides what goes in it. Neither had a test before this file.

Four properties, in the order a reader needs them:

  1. THE INTENT IS WHAT IS IDEMPOTENT, NOT THE INSTANT. A retried admission is
     the same admission and returns the stored record; a retried admission
     whose objective, bounds or key changed is a DIFFERENT admission and is
     refused, with both digests printed. That refusal is also what makes two
     concurrent schedulers safe — the loser is told it lost.
  2. THE ACTION KEY IS EXCLUSIVE AMONG OPEN ADMISSIONS. The reason comes from
     `runtime/bounded_invocation` and not from taste: that helper keys one
     receipt per action key and REPLAYS it for equal input, so a second open
     admission under the same key would receive the first cycle's result and
     the receipt would say `replayed: True` about work it never did.
  3. THE PHASES ADVANCE ONE AT A TIME, PER STORE. Each of the two is another
     store's answer, recorded as that store gave it. Skipping, reversing,
     re-reporting a phase with different evidence, and reporting another
     resource's lease or another key's receipt are each refused by name.
  4. RETIREMENT RELEASES AND KEEPS THE FIRST. It is not a deletion: the record
     stays, saying what the cycle was admitted for, because the after-the-fact
     question is computed from exactly that.

`RECEIPT_STATUSES` is not asserted against a hand-written list. It is derived
from `runtime/bounded_invocation.py` by parsing it — every string that can reach
a receipt's `status` — and compared to the store's copy, because a vocabulary
invented in the reader is how a store comes to accept a status the writer never
writes. That derivation is `TestTheReceiptVocabularyIsTheWriters` below.

Hermetic: a temporary directory per test, no daemon, no socket, no clock read by
the module. The two instants that appear are arguments.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
INTAKE = REPO / "intake"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
cycle = _load("serveros_intake_daily_cycle", INTAKE / "daily_cycle.py")

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError

T0 = "2026-01-01T00:00:00+00:00"
T1 = "2026-01-01T00:05:00+00:00"
T2 = "2026-01-01T00:10:00+00:00"
DIGEST = "a" * 64
BUDGET = {"wall_seconds": 900, "max_attempts": 2, "max_output_bytes": 200000}


def a_selection(selected=None, digest=DIGEST, observed_at=T0, **over) -> dict:
    # No coercion: the two cases this file needs to tell apart are a LIST of
    # ids and a value that is not a list (`admit` refuses the second), and
    # `list("SRV-1")` would turn the refusal case into a five-element selection
    # that passes.
    selection = {"selection_digest": digest, "observed_at": observed_at,
                 "selected": ["SRV-1"] if selected is None else selected,
                 "carryover": []}
    selection.update(over)
    return selection


def a_record(**over) -> dict:
    record = {"cycle_ref": "cyc-1", "grant_ref": "grant-1",
              "selection": a_selection(), "reservation": dict(BUDGET),
              "lease": {"resource": "browser:chromium", "scope": "session",
                        "holder": "cyc-1"},
              "action_key": "act-1", "admitted_at": T0}
    record.update(over)
    return record


def a_store(tmp_path: Path) -> AgentControlStore:
    return AgentControlStore(tmp_path / "store.json")


def a_decision(**over) -> dict:
    decision = {"status": "PLANNED", "selected": ["SRV-1"], "carryover": [],
                "selection_digest": DIGEST}
    decision.update(over)
    return decision


def a_row(item_id: str = "SRV-1", **over) -> dict:
    row = {"id": item_id, "grant_ref": "grant-1"}
    row.update(over)
    return row


def an_envelope(**over) -> dict:
    grant = {"ref": "grant-1", "budget": dict(BUDGET)}
    grant.update(over)
    return {"grant-1": grant}


CLI = REPO / "bin" / "serverosctl"


def a_data_root(tmp_path: Path) -> dict:
    """A fixture DATA_ROOT, so `paths` resolves both stores inside the test.

    Both, and not just the control store: the two other stores this round
    reads — the resource leases and the receipts directory — are addressed by
    `runtime/paths.py` as well, and a fixture that isolated only one of them
    would have `work confirm` reading this node's live leases.
    """
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
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


def store_path(env: dict) -> Path:
    return Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"


def seeded(env: dict) -> dict:
    """One cycle admitted, under a grant and a lease this fixture names.

    Written straight to the store rather than through `work admit`, because
    `work admit` can only be driven from a board that selects something and
    this file's board fixture is empty on purpose — the CLI class is about the
    two verbs AFTER the admission, and the plan's own refusals are covered
    directly above.
    """
    store = AgentControlStore(store_path(env))
    store.admit(a_record(lease={"resource": "browser:chromium", "scope": "session",
                                "holder": "cyc-1"}))
    return store.summary()


# ---------------------------------------------------------------------------
# 1. The intent is what is idempotent, and not the instant.
# ---------------------------------------------------------------------------


class TestTheIntentIsTheIdentity:
    def test_a_retried_admission_is_the_same_admission(self, tmp_path):
        store = a_store(tmp_path)
        first = store.admit(a_record())
        again = store.admit(a_record())

        assert first["replayed"] is False
        assert again["replayed"] is True
        assert again["phase"] == store_mod.ADMISSION_PHASES[0]
        assert again["intent_digest"] == first["intent_digest"]
        assert len(store.admissions()) == 1

    def test_the_instant_is_not_part_of_the_intent(self, tmp_path):
        """A resume happens later and is still the same admission.

        This is the assertion that fails if `admitted_at` is ever added to
        `_intent_digest`: the second call would be a different intent and would
        be refused instead of replayed, so every retry would be a lost cycle.
        """
        store = a_store(tmp_path)
        store.admit(a_record(admitted_at=T0))
        again = store.admit(a_record(admitted_at=T1))

        assert again["replayed"] is True
        assert again["intent_digest"] == store.get_admission("cyc-1")["intent_digest"]

    def test_a_store_that_was_never_told_keeps_the_first_instant(self, tmp_path):
        """The stored record is the first one's, not the retry's.

        `replayed` returns the STORED record with its own `admitted_at`, so a
        caller cannot move the instant by calling again.
        """
        store = a_store(tmp_path)
        store.admit(a_record(admitted_at=T0))
        again = store.admit(a_record(admitted_at=T1))

        assert again["admitted_at"] == T0

    @pytest.mark.parametrize("changed", [
        {"selection": a_selection(digest="b" * 64)},
        {"grant_ref": "grant-2"},
        {"reservation": {"wall_seconds": 60, "max_attempts": 2,
                         "max_output_bytes": 200000}},
        {"lease": {"resource": "browser:firefox", "scope": "session",
                   "holder": "cyc-1"}},
        {"action_key": "act-2"},
    ])
    def test_one_thing_changed_is_a_different_admission(self, tmp_path, changed):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(**changed))

        message = str(refusal.value)
        assert "already admitted under a different intent" in message
        # Both digests are printed: a reader has to be able to see WHAT changed
        # without this store guessing which field it was.
        assert store.get_admission("cyc-1")["intent_digest"] in message

    def test_the_loser_of_two_schedulers_is_told_it_lost(self, tmp_path):
        """Two cycles' worth of intent for one ref: one wins, one is refused.

        Not a race in this test — the point is the OUTCOME of the race, which is
        that the second is told rather than both running.
        """
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(selection=a_selection(selected=["SRV-2"],
                                                       digest="b" * 64)))

        assert "already admitted under a different intent" in str(refusal.value)

    def test_the_store_trusts_the_selection_digest_and_does_not_rederive_it(self, tmp_path):
        """The boundary, stated rather than left for a reader to find.

        A record whose `selected` changed but whose `selection_digest` did not
        REPLAYS: the store compares the digest it was handed and cannot check
        it. It cannot, and not by oversight — `select_focus` computes that
        digest over the chosen ROW DICTS and the observation, and a row dict is
        not in this record, so nothing here could recompute it. What keeps this
        honest is that the digest has exactly one producer and `admission_plan`
        reads it from the decision rather than accepting one, which
        `TestThePlanDecidesAndRefuses` covers from the other side.
        """
        store = a_store(tmp_path)
        store.admit(a_record())
        again = store.admit(a_record(selection=a_selection(selected=["SRV-2"])))

        assert again["replayed"] is True
        assert again["selection"]["selected"] == ["SRV-1"], (
            "the stored selection is the first one's: a replay returns the "
            "record, it does not merge the caller's copy into it")


# ---------------------------------------------------------------------------
# 2. The action key is exclusive among open admissions.
# ---------------------------------------------------------------------------


class TestTheActionKeyIsHeld:
    def test_two_open_cycles_cannot_share_one_action_key(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(cycle_ref="cyc-2"))

        message = str(refusal.value)
        assert "already held by the open admission" in message
        assert "cyc-1" in message
        assert "replayed" in message, "the refusal must say why, not just that"

    def test_retiring_releases_the_key(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.retire_admission("cyc-1", at=T1)
        second = store.admit(a_record(cycle_ref="cyc-2"))

        assert second["phase"] == store_mod.ADMISSION_PHASES[0]
        assert set(store.admissions()) == {"cyc-1", "cyc-2"}


# ---------------------------------------------------------------------------
# 3. The phases advance one at a time, per store.
# ---------------------------------------------------------------------------


class TestThePhasesAdvanceOneAtATime:
    def test_the_intent_phase_is_not_confirmable(self, tmp_path):
        """`admit` wrote it. Reporting this store's own act back is not evidence."""
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase=store_mod.ADMISSION_PHASES[0],
                                    evidence={"act": "DECLARE"}, at=T1)

        assert "not a phase a caller confirms" in str(refusal.value)

    def test_a_phase_may_not_be_skipped(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="DISPATCHED",
                                    evidence={"action_key": "act-1",
                                              "status": "EXITED"}, at=T1)

        assert "one phase past the next" in str(refusal.value)

    def test_a_phase_may_not_move_backwards(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=T1)
        store.confirm_admission("cyc-1", phase="DISPATCHED",
                                evidence={"action_key": "act-1",
                                          "status": "EXITED"}, at=T2)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                    evidence={"act": "REFRESH",
                                              "resource": "browser:chromium"},
                                    at=T2)

        assert "do not move backwards" in str(refusal.value)

    def test_the_same_phase_with_the_same_evidence_replays(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        evidence = {"act": "DECLARE", "resource": "browser:chromium"}
        first = store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                        evidence=evidence, at=T1)
        again = store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                        evidence=dict(evidence), at=T2)

        assert first["replayed"] is False
        assert again["replayed"] is True
        assert again["phase"] == "LEASE_HELD"

    def test_the_same_phase_with_different_evidence_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=T1)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                    evidence={"act": "REFRESH",
                                              "resource": "browser:chromium"},
                                    at=T2)

        assert "different evidence" in str(refusal.value)
        assert "a question for a person" in str(refusal.value)

    def test_lease_evidence_about_another_resource_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                    evidence={"act": "DECLARE",
                                              "resource": "gpu:0"}, at=T1)

        assert "browser:chromium" in str(refusal.value)

    @pytest.mark.parametrize("act", ["HELD", "declare", "", None])
    def test_an_act_the_lease_store_does_not_produce_is_refused(self, tmp_path, act):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                    evidence={"act": act,
                                              "resource": "browser:chromium"},
                                    at=T1)

        assert "DECLARE or REFRESH" in str(refusal.value)

    def test_dispatch_evidence_under_another_key_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=T1)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="DISPATCHED",
                                    evidence={"action_key": "act-9",
                                              "status": "EXITED"}, at=T2)

        assert "evidence about a different dispatch" in str(refusal.value)

    def test_a_status_invoke_once_does_not_write_is_refused(self, tmp_path):
        """The status vocabulary is the writer's, checked here rather than trusted.

        `SUCCEEDED` is the word an invented vocabulary reaches for and the one
        `runtime/bounded_invocation` never writes: a failed command exits with a
        code and the receipt says `EXITED` with an effect of `UNVERIFIED`.
        """
        store = a_store(tmp_path)
        store.admit(a_record())
        store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=T1)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="DISPATCHED",
                                    evidence={"action_key": "act-1",
                                              "status": "SUCCEEDED"}, at=T2)

        assert "SUCCEEDED" in str(refusal.value)

    def test_empty_evidence_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD", evidence={},
                                    at=T1)

        assert "needs the evidence" in str(refusal.value)

    def test_a_confirmation_for_a_cycle_never_admitted_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-none", phase="LEASE_HELD",
                                    evidence={"act": "DECLARE",
                                              "resource": "browser:chromium"},
                                    at=T1)

        assert "has no admission" in str(refusal.value)

    def test_a_confirmation_after_retirement_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.retire_admission("cyc-1", at=T1)
        with pytest.raises(StoreError) as refusal:
            store.confirm_admission("cyc-1", phase="LEASE_HELD",
                                    evidence={"act": "DECLARE",
                                              "resource": "browser:chromium"},
                                    at=T2)

        assert "was retired at" in str(refusal.value)


# ---------------------------------------------------------------------------
# 4. Retirement releases, and keeps the first.
# ---------------------------------------------------------------------------


class TestRetirementIsIdempotentAndKeepsTheFirst:
    def test_retiring_twice_keeps_the_first_instant(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        first = store.retire_admission("cyc-1", at=T1, outcome="DELIVERED")
        again = store.retire_admission("cyc-1", at=T2, outcome="ABANDONED")

        assert first["retired_at"] == T1
        assert again["retired_at"] == T1
        assert again["outcome"] == "DELIVERED", (
            "the second call must not rewrite how the cycle ended")

    def test_a_retired_admission_is_still_in_the_store(self, tmp_path):
        """Retiring is not un-admitting: the selection is what a review reads."""
        store = a_store(tmp_path)
        store.admit(a_record())
        store.retire_admission("cyc-1", at=T1)

        stored = store.get_admission("cyc-1")
        assert stored is not None
        assert stored["selection"]["selected"] == ["SRV-1"]
        assert store.summary()["admissions"] == 1
        assert store.summary()["admissions_open"] == 0

    def test_retiring_a_cycle_never_admitted_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.retire_admission("cyc-none", at=T1)

        assert "no admission to retire" in str(refusal.value)

    def test_an_empty_ref_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.retire_admission("   ", at=T1)

        assert "needs the cycle's ref" in str(refusal.value)


# ---------------------------------------------------------------------------
# The six things `admit` refuses, each naming what to change.
# ---------------------------------------------------------------------------


class TestWhatAdmitRefuses:
    @pytest.mark.parametrize("field,value,expected", [
        ("cycle_ref", "", "cannot be stored"),
        ("cycle_ref", None, "cannot be stored"),
        ("selection", None, "carries no selection"),
        ("selection", {"selected": ["SRV-1"]}, "names no selection_digest"),
        ("selection", a_selection(selected=[]), "selected nothing"),
        ("selection", a_selection(selected="SRV-1"), "selected nothing"),
        ("grant_ref", "", "names no grant_ref"),
        ("reservation", None, "carries no reservation"),
        ("lease", None, "carries no lease intent"),
        ("action_key", "act 1", "is not an action key"),
        ("action_key", "", "is not an action key"),
    ])
    def test_each_refusal_names_what_to_change(self, tmp_path, field, value, expected):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(**{field: value}))

        assert expected in str(refusal.value)

    @pytest.mark.parametrize("key", sorted(BUDGET))
    def test_a_reservation_missing_a_bound_is_refused(self, tmp_path, key):
        store = a_store(tmp_path)
        short = {k: v for k, v in BUDGET.items() if k != key}
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(reservation=short))

        assert key in str(refusal.value)

    @pytest.mark.parametrize("key", sorted(BUDGET))
    def test_a_reservation_with_a_nonpositive_bound_is_refused(self, tmp_path, key):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(reservation={**BUDGET, key: 0}))

        assert key in str(refusal.value)

    @pytest.mark.parametrize("field", sorted(store_mod.LEASE_INTENT_FIELDS))
    def test_a_lease_intent_missing_a_field_is_refused(self, tmp_path, field):
        store = a_store(tmp_path)
        intent = {"resource": "browser:chromium", "scope": "session",
                  "holder": "cyc-1"}
        del intent[field]
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(lease=intent))

        assert field in str(refusal.value)

    def test_a_lease_intent_is_stored_as_the_three_named_fields(self, tmp_path):
        """Whatever else the caller sent, what is stored is the intent this store reads.

        A caller that smuggled a fourth key would be storing a field no reader
        of `lease_evidence_conflict` consults, and it would compare unequal on
        the next retry — so the record would replay as a different intent.
        """
        store = a_store(tmp_path)
        intent = {"resource": "browser:chromium", "scope": "session",
                  "holder": "cyc-1", "note": "smuggled"}
        stored = store.admit(a_record(lease=intent))

        assert set(stored["lease"]) == set(store_mod.LEASE_INTENT_FIELDS)
        assert store.admit(a_record(lease=intent))["replayed"] is True

    def test_a_reservation_is_stored_as_the_three_named_bounds(self, tmp_path):
        store = a_store(tmp_path)
        stored = store.admit(a_record(
            reservation={**BUDGET, "exhausted": False}))

        assert set(stored["reservation"]) == set(store_mod.BUDGET_KEYS)


# ---------------------------------------------------------------------------
# `admission_plan` — the half that decides what the record IS.
# ---------------------------------------------------------------------------


class TestThePlanDecidesAndRefuses:
    def plan(self, **over):
        kwargs = {"cycle_ref": "cyc-1", "observed_at": T0, "action_key": "act-1",
                  "lease": {"resource": "browser:chromium", "scope": "session",
                            "holder": "cyc-1"}}
        kwargs.update(over)
        decision = kwargs.pop("decision", a_decision())
        rows = kwargs.pop("rows", [a_row()])
        grants = kwargs.pop("grants", an_envelope())
        return cycle.admission_plan(decision, rows, grants, **kwargs)

    def test_a_selection_under_one_recorded_grant_becomes_a_record(self):
        plan = self.plan()

        assert "refused" not in plan
        assert plan["grant_ref"] == "grant-1"
        assert plan["reservation"] == BUDGET
        assert plan["selection"]["selected"] == ["SRV-1"]
        # The plan's output is exactly what `admit` takes: this test would fail
        # on a key the store requires and the plan forgot.
        assert set(plan) == {"cycle_ref", "grant_ref", "reservation", "lease",
                             "action_key", "selection"}

    def test_nothing_selected_is_refused_with_the_decisions_own_reason(self):
        plan = self.plan(decision={"status": "BLOCKED", "reason": "STALE_BOARD",
                                   "selected": [], "carryover": []})

        assert plan["refused"] == "NOTHING_SELECTED"
        assert "STALE_BOARD" in plan["because"]

    def test_a_selected_row_the_board_does_not_carry_is_refused(self):
        plan = self.plan(rows=[a_row("SRV-2")])

        assert plan["refused"] == "SELECTION_NOT_ON_THE_BOARD"
        assert "SRV-1" in plan["because"]

    def test_a_selected_row_with_no_grant_is_refused_as_a_disagreement(self):
        """`select_focus` cannot select such a row, so this is a disagreement.

        The word says so: it is not "nothing was selected" (something was) and
        not "the grant is missing" (no grant was ever named).
        """
        plan = self.plan(rows=[a_row(**{"grant_ref": None})])

        assert plan["refused"] == "SELECTION_CARRIES_NO_GRANT"
        assert "select_focus" in plan["because"]

    def test_two_grants_in_one_cycle_is_refused_with_both_named(self):
        """One cycle has one action key, one lease and one dispatch.

        Two grants would be two answers to what bounds that dispatch, and no
        combination rule is declared — so the two-grant cycle is refused with
        both grants named rather than merged on a rule nobody wrote.
        """
        rows = [a_row("SRV-1"), a_row("SRV-2", grant_ref="grant-2")]
        grants = {"grant-1": {"ref": "grant-1", "budget": dict(BUDGET)},
                  "grant-2": {"ref": "grant-2", "budget": dict(BUDGET)}}
        plan = self.plan(decision=a_decision(selected=["SRV-1", "SRV-2"]),
                         rows=rows, grants=grants)

        assert plan["refused"] == "MULTIPLE_GRANTS_IN_ONE_CYCLE"
        assert "grant-1" in plan["because"] and "grant-2" in plan["because"]
        assert "admit them as two cycles" in plan["because"]

    def test_a_grant_the_envelope_does_not_hold_is_refused(self):
        plan = self.plan(grants={})

        assert plan["refused"] == "GRANT_NOT_RECORDED"

    def test_a_grant_with_no_budget_is_refused(self):
        plan = self.plan(grants={"grant-1": {"ref": "grant-1"}})

        assert plan["refused"] == "MISSING_BOUNDED_BUDGET"

    @pytest.mark.parametrize("key", sorted(BUDGET))
    def test_a_grant_whose_budget_is_unbounded_is_refused(self, key):
        plan = self.plan(grants={"grant-1": {"ref": "grant-1",
                                             "budget": {**BUDGET, key: None}}})

        assert plan["refused"] == "MISSING_BOUNDED_BUDGET"
        assert key in plan["because"]

    def test_no_cycle_ref_is_refused(self):
        plan = self.plan(cycle_ref="  ")

        assert plan["refused"] == "NO_CYCLE_REF"

    def test_the_plan_writes_exactly_what_the_store_wants(self, tmp_path):
        """The seam that matters: the plan's record goes in unchanged."""
        store = a_store(tmp_path)
        stored = store.admit(self.plan())

        assert stored["cycle_ref"] == "cyc-1"
        assert stored["phase"] == store_mod.ADMISSION_PHASES[0]
        assert stored["reservation"] == BUDGET


# ---------------------------------------------------------------------------
# The receipt vocabulary is the writer's, derived and not retyped.
# ---------------------------------------------------------------------------


def _statuses_invoke_once_can_write() -> set[str]:
    """Every string that can reach a receipt's `status`, read from the source.

    Three forms, all present in `runtime/bounded_invocation.py`:

      * the initial `record = {... "status": "STARTED" ...}` dict literal;
      * `record.update(status="...")` — including the conditional one, whose
        two branches are both statuses;
      * `record.update(status=reason, ...)`, where `reason` is a local assigned
        a string literal earlier. The name is resolved by collecting what it is
        assigned anywhere in the file, which is the only way to see `TIMED_OUT`
        and `OUTPUT_LIMIT` — they reach `status` through a variable and are
        otherwise invisible to a literal scan.
    """
    tree = ast.parse((RUNTIME / "bounded_invocation.py").read_text(encoding="utf-8"))
    statuses: set[str] = set()
    variables: set[str] = set()

    def literals(node) -> set[str]:
        """Strings this expression can be, following a conditional's two arms."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        if isinstance(node, ast.IfExp):
            return literals(node.body) | literals(node.orelse)
        return set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "status":
                    statuses |= literals(value)
        elif isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg != "status":
                    continue
                if isinstance(keyword.value, ast.Name):
                    variables.add(keyword.value.id)
                else:
                    statuses |= literals(keyword.value)
    assert variables, "no variable-channel status found; the derivation went stale"

    # Only the names that actually reach `status=`, and only their assignments.
    # Collecting every uppercase literal assigned anywhere in the file would be
    # a wider net than the question, and it would add `NO_PROCESS_STARTED` and
    # `OUTPUT_ALREADY_EXISTS` — an effect and a reason, neither of them statuses.
    for name in variables:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                statuses |= literals(node.value)
    return statuses


class TestTheReceiptVocabularyIsTheWriters:
    def test_the_store_accepts_exactly_what_invoke_once_writes(self):
        """Not "contains": exactly.

        A store that accepted MORE would record a receipt status the writer
        never produces, which is a claim about an execution nobody performed.
        """
        assert set(store_mod.RECEIPT_STATUSES) == _statuses_invoke_once_can_write()

    def test_the_two_conditionals_are_both_derived(self):
        """The two statuses a literal scan misses, named.

        `TIMED_OUT` and `OUTPUT_LIMIT` reach `record.update(status=reason, ...)`
        through a variable, and `START_FAILED`/`IO_FAILED` through a
        conditional expression. If the derivation stopped resolving either
        channel this test fails while the one above might still pass.
        """
        derived = _statuses_invoke_once_can_write()
        assert {"TIMED_OUT", "OUTPUT_LIMIT"} <= derived
        assert {"START_FAILED", "IO_FAILED"} <= derived
        assert len(derived) == len(store_mod.RECEIPT_STATUSES), (
            "the derivation and the store's copy differ in size, so at least "
            "one of the two vocabularies is out of step")


# ---------------------------------------------------------------------------
# The store's own summary, and what sweep does with admissions.
# ---------------------------------------------------------------------------


class TestTheSummaryCountsWhatIsHeld:
    def test_the_summary_separates_open_from_recorded(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.admit(a_record(cycle_ref="cyc-2", action_key="act-2"))
        store.retire_admission("cyc-2", at=T1)

        summary = store.summary()
        assert summary["admissions"] == 2
        assert summary["admissions_open"] == 1

    def test_the_phase_histogram_counts_only_open_ones(self, tmp_path):
        store = a_store(tmp_path)
        store.admit(a_record())
        store.admit(a_record(cycle_ref="cyc-2", action_key="act-2"))
        store.confirm_admission("cyc-2", phase="LEASE_HELD",
                                evidence={"act": "DECLARE",
                                          "resource": "browser:chromium"}, at=T1)
        store.retire_admission("cyc-2", at=T2)

        by_phase = store.summary()["admissions_by_phase"]
        assert set(by_phase) == set(store_mod.ADMISSION_PHASES)
        assert by_phase["INTENT_RECORDED"] == 1
        assert by_phase["LEASE_HELD"] == 0

    def test_sweep_never_removes_an_admission(self, tmp_path):
        """The reason is the action key, not nostalgia.

        Clearing an open admission would hand its action key to the next cycle,
        and `runtime/bounded_invocation.invoke_once` has already given an answer
        under that key — the second cycle would receive the first's receipt.
        A retired one is the record of what the cycle did.
        """
        store = a_store(tmp_path)
        store.admit(a_record())
        store.admit(a_record(cycle_ref="cyc-2", action_key="act-2"))
        store.retire_admission("cyc-2", at=T1)

        store.sweep(before=T2)

        assert set(store.admissions()) == {"cyc-1", "cyc-2"}


class TestTheCli:
    """The four verbs, run as processes against a fixture root.

    The store's own tests drive `admit` directly. These drive `serverosctl`,
    because the part that is only in the CLI is the half that reads the OTHER
    stores — `_lease_answer` and `_dispatch_answer` decide what counts as the
    lease store's answer and the receipts directory's — and a mutant there
    would survive a suite that never ran the verb.
    """

    def test_admissions_reports_an_empty_store_as_empty(self, tmp_path):
        done = cli("work", "admissions", env_extra=a_data_root(tmp_path))

        assert done.returncode == 0, done.stderr
        assert "0 open of 0 recorded" in done.stdout
        assert "no cycle has been admitted on this node" in done.stdout

    def test_admit_that_cannot_decide_writes_nothing(self, tmp_path):
        """The property that matters most about a verb that writes.

        An empty boards root composes a board with no observation, so the
        decision is `PROJECTION_AGE_UNKNOWN` and the plan refuses. The store
        must be byte-identical afterwards: a refusal that had already recorded
        a reservation would leave a lease and an action key held by a cycle
        that never started.
        """
        env = a_data_root(tmp_path)
        path = Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"
        before = path.read_bytes() if path.exists() else None

        done = cli("work", "admit", "--cycle", "cyc-1", "--action-key", "act-1",
                   "--resource", "browser:chromium", "--scope", "session",
                   env_extra=env)

        assert done.returncode == 0, done.stderr
        assert "refused     NOTHING_SELECTED" in done.stdout
        assert "PROJECTION_AGE_UNKNOWN" in done.stdout
        assert (path.read_bytes() if path.exists() else None) == before

    def test_work_admit_takes_no_grant(self):
        """The removed authority path, pinned where a later commit would look.

        §9.11 deleted `work focus --grants`; a `work admit --grant` would put
        the same hole back one verb over, and it would look like a convenience.
        The grant is read off the selected row or the cycle is not admitted.
        """
        help_text = cli("work", "admit", "--help").stdout

        # The OPTION STRINGS, not the whole text: this verb's own `--cycle`
        # help says "there is deliberately no --grant", and a substring test
        # would fail on the sentence that explains the absence.
        options = set(re.findall(r"(?m)^\s{2,}(--[a-z-]+)", help_text))
        assert "--grant" not in options
        assert "--action-key" in options

    def test_confirm_refuses_a_cycle_that_was_never_admitted(self, tmp_path):
        done = cli("work", "confirm", "cyc-none", "--phase", "LEASE_HELD",
                   env_extra=a_data_root(tmp_path))

        assert done.returncode != 0
        assert "has no admission" in done.stderr

    def test_confirm_refuses_the_phase_admit_wrote(self, tmp_path):
        done = cli("work", "confirm", "cyc-1", "--phase", "INTENT_RECORDED",
                   env_extra=a_data_root(tmp_path))

        assert done.returncode != 0
        assert "not a phase a caller confirms" in done.stderr

    def test_confirm_dispatch_needs_the_receipts_directory(self, tmp_path):
        env = a_data_root(tmp_path)
        seeded(env)

        done = cli("work", "confirm", "cyc-1", "--phase", "DISPATCHED",
                   env_extra=env)

        assert done.returncode != 0
        assert "needs `--receipts`" in done.stderr

    def test_confirm_lease_refuses_a_reference_the_store_does_not_hold(self, tmp_path):
        """The verb reads `runtime/lease_store.py`; it does not take a word for it."""
        env = a_data_root(tmp_path)
        seeded(env)

        done = cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD",
                   env_extra=env)

        assert done.returncode != 0
        assert "browser:chromium" in done.stderr
        assert "so nothing declared it" in done.stderr

    def test_confirm_lease_records_the_lease_stores_own_answer(self, tmp_path):
        env = a_data_root(tmp_path)
        seeded(env)
        declared = cli("lease", "declare", "browser:chromium", "--scope", "session",
                       "--holder", "cyc-1", "--sharing", "shared", env_extra=env)
        assert declared.returncode == 0, declared.stderr

        done = cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD",
                   "--at", T1, env_extra=env)

        assert done.returncode == 0, done.stderr
        assert "phase       LEASE_HELD" in done.stdout
        assert "act         DECLARE" in done.stdout
        assert "resource-leases" in done.stdout
        # And the store's own record now says the same, which is what a later
        # reader sees rather than this printout.
        store = AgentControlStore(
            Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json")
        assert store.get_admission("cyc-1")["confirmations"]["LEASE_HELD"]["act"] == "DECLARE"

    def test_confirm_dispatch_records_a_receipt_invoke_once_wrote(self, tmp_path):
        env = a_data_root(tmp_path)
        seeded(env)
        cli("lease", "declare", "browser:chromium", "--scope", "session",
            "--holder", "cyc-1", "--sharing", "shared", env_extra=env)
        cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD", "--at", T1,
            env_extra=env)
        receipts = tmp_path / "receipts"
        bounded = _load("serveros_runtime_bounded_invocation",
                        RUNTIME / "bounded_invocation.py")
        bounded.invoke_once(key="act-1", argv=["/bin/true"], cwd=tmp_path,
                            receipts=receipts, timeout=10)

        done = cli("work", "confirm", "cyc-1", "--phase", "DISPATCHED",
                   "--receipts", str(receipts), "--at", T2, env_extra=env)

        assert done.returncode == 0, done.stderr
        assert "phase       DISPATCHED" in done.stdout
        assert "status      EXITED" in done.stdout

    def test_confirm_lease_refuses_a_reference_that_was_released(self, tmp_path):
        """Held and released are different answers, and the store's is the one read.

        The sentence is the one under test and not only the refusal:
        `runtime/lease_store.release` DROPS a retired reference from
        `references` before writing, so a released lease arrives at the reader
        as an ABSENT one. "This holder never declared it" would be the wrong
        thing to say, and it is what a reader that only looked at `references`
        would say — the `history` entry is the evidence that separates them.
        """
        env = a_data_root(tmp_path)
        seeded(env)
        cli("lease", "declare", "browser:chromium", "--scope", "session",
            "--holder", "cyc-1", "--sharing", "shared", env_extra=env)
        released = cli("lease", "release", "browser:chromium", "--scope", "session",
                       "--holder", "cyc-1", env_extra=env)
        assert released.returncode == 0, released.stderr

        done = cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD", env_extra=env)

        assert done.returncode != 0
        assert "released its reference" in done.stderr
        assert "so the lease is not held" in done.stderr

    def test_confirm_lease_refuses_a_holder_that_never_declared(self, tmp_path):
        """The other half of the same branch: a different holder's reference.

        Same absence, different cause, different next action — so the two
        sentences must not be one sentence.
        """
        env = a_data_root(tmp_path)
        seeded(env)
        cli("lease", "declare", "browser:chromium", "--scope", "session",
            "--holder", "someone-else", "--sharing", "shared", env_extra=env)

        done = cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD", env_extra=env)

        assert done.returncode != 0
        assert "not by 'cyc-1' in scope 'session'" in done.stderr
        assert "released" not in done.stderr

    def test_confirm_dispatch_passes_the_receipts_own_key_and_not_the_admissions(self, tmp_path):
        """A receipt naming another key is refused, so the file's value is what travels.

        The file is at `<action-key>.json` for the admission's key, and it says
        it is about a different one. A verb that substituted the admission's key
        — the "helpful" thing to do, since that is the key it was asked about —
        would record this receipt as evidence for a dispatch it does not
        describe.
        """
        env = a_data_root(tmp_path)
        seeded(env)
        cli("lease", "declare", "browser:chromium", "--scope", "session",
            "--holder", "cyc-1", "--sharing", "shared", env_extra=env)
        cli("work", "confirm", "cyc-1", "--phase", "LEASE_HELD", "--at", T1,
            env_extra=env)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        (receipts / "act-1.json").write_text(json.dumps(
            {"action_key": "act-9", "status": "EXITED", "input_digest": "0" * 64}),
            encoding="utf-8")

        done = cli("work", "confirm", "cyc-1", "--phase", "DISPATCHED",
                   "--receipts", str(receipts), env_extra=env)

        assert done.returncode != 0
        assert "evidence about a different dispatch" in done.stderr

    def test_retire_releases_and_the_listing_still_shows_it(self, tmp_path):
        env = a_data_root(tmp_path)
        seeded(env)

        done = cli("work", "retire", "cyc-1", "--outcome", "DELIVERED",
                   "--at", T2, env_extra=env)

        assert done.returncode == 0, done.stderr
        assert f"retired at  {T2}" in done.stdout

        listing = cli("work", "admissions", env_extra=env)
        assert "0 open of 1 recorded" in listing.stdout
        assert f"(retired {T2})" in listing.stdout
        assert "SRV-1" in listing.stdout, "the selection is what a review reads"


class TestTheStoreIsStillReadableByOlderConsumers:
    def test_a_store_written_before_admissions_existed_reads_with_the_collection(self, tmp_path):
        """The six collections are added on load, not demanded of the file.

        A store written by the version before this one has no `admissions` key,
        and a reader that raised on it would make every earlier document
        unreadable — an upgrade that loses the records it was meant to keep.
        """
        path = tmp_path / "store.json"
        path.write_text(json.dumps({
            "schema": store_mod.SCHEMA, "contexts": {}, "approvals": {},
            "bindings": {}, "selections": {}, "grants": {}}), encoding="utf-8")

        store = AgentControlStore(path)
        assert store.admissions() == {}
        assert store.summary()["admissions"] == 0
