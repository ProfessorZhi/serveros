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

Five properties, in the order a reader needs them:

  1. THE AUTHORITY THE RECORD NAMES IS RESOLVED HERE, not taken on the caller's
     word. `admit` reads the grant off its own `grants` collection inside the
     same transaction and refuses when it is not recorded, is revoked, is not
     current at the admission instant, does not name the selected items, or
     does not declare the reserved bounds. Every one of those is a fact the
     layers above check against THEIR OWN input: `work admit` composes a plan
     from an envelope it read outside the lock, so a revocation in another
     process lands in the gap and only the holder of the lock can see it.
  2. THE INTENT IS WHAT IS IDEMPOTENT, NOT THE INSTANT. A retried admission is
     the same admission and returns the stored record; a retried admission
     whose objective, bounds or key changed is a DIFFERENT admission and is
     refused, with both digests printed. That refusal is also what makes two
     concurrent schedulers safe — the loser is told it lost.
  3. THE ACTION KEY IS EXCLUSIVE AMONG OPEN ADMISSIONS. The reason comes from
     `runtime/bounded_invocation` and not from taste: that helper keys one
     receipt per action key and REPLAYS it for equal input, so a second open
     admission under the same key would receive the first cycle's result and
     the receipt would say `replayed: True` about work it never did.
  4. THE PHASES ADVANCE ONE AT A TIME, PER STORE. Each of the two is another
     store's answer, recorded as that store gave it. Skipping, reversing,
     re-reporting a phase with different evidence, and reporting another
     resource's lease or another key's receipt are each refused by name.
  5. RETIREMENT RELEASES AND KEEPS THE FIRST. It is not a deletion: the record
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
import shutil
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
GRANT = "grant-1"
CONTEXT = "ctx-delegated-1"
# A window with T0, T1 and T2 inside it, so the three instants this file uses
# are all instants a grant covered.
WINDOW = {"valid_from": "2025-12-31T00:00:00+00:00",
          "expires_at": "2026-01-02T00:00:00+00:00"}


def a_grant(**over) -> dict:
    """The delegation `a_record()` is admitted under, as the store records it.

    Its items and its budget are the record's own and its window contains the
    record's instant. All three matter: `admit` resolves the record against
    this grant, so a fixture whose grant merely EXISTED would pass the check
    without being the delegation the record claims to run under.
    """
    grant = {"grant_ref": GRANT, "delegated_by": CONTEXT, "items": ["SRV-1"],
             "budget": dict(BUDGET), **WINDOW}
    grant.update(over)
    return grant


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
    record = {"cycle_ref": "cyc-1", "grant_ref": GRANT,
              "selection": a_selection(), "reservation": dict(BUDGET),
              "lease": {"resource": "browser:chromium", "scope": "session",
                        "holder": "cyc-1"},
              "action_key": "act-1", "admitted_at": T0}
    record.update(over)
    return record


def a_store(tmp_path: Path) -> AgentControlStore:
    """A store holding the one delegation `a_record()` names, and nothing else.

    Seeded, because `admit` resolves the grant it is handed against this
    store's own `grants` collection rather than taking the caller's word for
    it. Every record in this file was admitted under `grant-1` and the store
    used to hold no grant at all: the whole file passed anyway, which is the
    measurement behind `TestAnAdmissionResolvesTheAuthorityItNames`. Seeding
    here is what makes the rest of the file a test of the transaction rather
    than of a lucky coincidence.
    """
    store = AgentControlStore(tmp_path / "store.json")
    store.put_context({"context_ref": CONTEXT})
    store.put_grant(a_grant())
    return store


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

    The delegation is seeded the same way `a_store` seeds it, so the two
    fixtures admit the same record under the same authority.
    """
    store = AgentControlStore(store_path(env))
    store.put_context({"context_ref": CONTEXT})
    store.put_grant(a_grant())
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
        kwargs = {"cycle_ref": "cyc-1", "observed_at": T0, "admitted_at": T0,
                  "action_key": "act-1",
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
        assert plan["grant_ref"] == GRANT
        assert plan["reservation"] == BUDGET
        assert plan["selection"]["selected"] == ["SRV-1"]
        # The plan's output is exactly what `admit` takes: this test would fail
        # on a key the store requires and the plan forgot. `admitted_at` is in
        # the set for that reason and not for symmetry — the store checks it
        # against the grant's window, so a plan that omitted it would return a
        # record the store refuses.
        assert set(plan) == {"cycle_ref", "grant_ref", "reservation", "lease",
                             "action_key", "admitted_at", "selection"}

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

    def test_no_admission_instant_is_refused(self):
        """The instant is in the record because the window is read against it.

        Refused here as well as in the store, for `NO_CYCLE_REF`'s reason: the
        record this function returns is the one `admit` takes, and a record
        with no instant is not one it takes.
        """
        plan = self.plan(admitted_at="   ")

        assert plan["refused"] == "NO_ADMISSION_INSTANT"
        assert "window" in plan["because"]

    def test_the_instant_is_in_the_record_and_not_in_its_identity(self, tmp_path):
        """Present in the record, absent from the identity, and both on purpose.

        Present: `admit` reads it against the grant's window. Absent from the
        identity: a retried admission happens at a later instant and is still
        the same admission.
        """
        store = a_store(tmp_path)
        first = store.admit(a_record(admitted_at=T0))
        again = store.admit(a_record(admitted_at=T1))

        assert first["admitted_at"] == T0
        assert again["replayed"] is True
        assert again["admitted_at"] == T0

    def test_the_plan_writes_exactly_what_the_store_wants(self, tmp_path):
        """The seam that matters: the plan's record goes in unchanged.

        Unchanged INCLUDING the instant, which is why the plan carries one —
        `admit` checks it against the grant's window, so a plan that left it to
        the caller would be returning a record the store refuses.
        """
        store = a_store(tmp_path)
        stored = store.admit(self.plan())

        assert stored["cycle_ref"] == "cyc-1"
        assert stored["phase"] == store_mod.ADMISSION_PHASES[0]
        assert stored["reservation"] == BUDGET
        assert stored["admitted_at"] == T0


# ---------------------------------------------------------------------------
# The authority the record names is resolved here, not taken on the caller's
# word. This file admitted 44 records under a grant its store never held, and
# every one of them passed — which is the measurement, not the argument.
# ---------------------------------------------------------------------------


class TestAnAdmissionResolvesTheAuthorityItNames:
    def test_the_recorded_grant_that_covers_the_record_is_admitted(self, tmp_path):
        """The positive control: without it the refusals below prove nothing."""
        store = a_store(tmp_path)
        stored = store.admit(a_record())

        assert stored["phase"] == store_mod.ADMISSION_PHASES[0]
        assert stored["grant_ref"] == GRANT

    def test_a_grant_this_store_never_recorded_is_refused(self, tmp_path):
        """The word is the plan's and the OPERAND is not, so the sentence says which.

        `admission_plan` refuses a missing grant too: it checks the envelope the
        caller handed it, which is the caller's own map. This is the store's
        record, and a refusal that read the same would send a reader to the
        wrong one.
        """
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(grant_ref="grant-never-recorded"))

        message = str(refusal.value)
        assert "no grant by that name has been recorded in this store" in message
        assert "checks the envelope it is HANDED" in message

    def test_a_grant_revoked_before_the_admission_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        store.revoke_grant(GRANT, at=T0, reason="the delegation ended")
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(admitted_at=T1))

        message = str(refusal.value)
        assert "was revoked at" in message and T0 in message
        assert "the store says is gone" in message

    def test_a_grant_whose_revocation_field_is_absent_is_refused(self, tmp_path):
        """`is not False` rather than `is not True`, and the two differ HERE.

        A record this store wrote always carries `revoked`, so this case comes
        from a store written by something else. It is refused because the
        permissive reading of a missing field is how a revocation becomes
        invisible — and the reason this test writes the file instead of
        recording the grant through `put_grant` is that `put_grant` cannot
        produce the input the two readings disagree on.
        """
        path = tmp_path / "no-revocation-field.json"
        path.write_text(json.dumps({
            "schema": store_mod.SCHEMA,
            "contexts": {CONTEXT: {"context_ref": CONTEXT}},
            "approvals": {}, "bindings": {}, "selections": {}, "admissions": {},
            "grants": {GRANT: {k: v for k, v in a_grant().items()
                               if k != "revoked"}}}), encoding="utf-8")
        store = AgentControlStore(path)

        with pytest.raises(StoreError) as refusal:
            store.admit(a_record())

        assert "was revoked at an unrecorded instant" in str(refusal.value)

    def test_a_revocation_landing_after_the_plan_is_refused(self, tmp_path):
        """The race this check exists for, and the reason it is in the store.

        `work admit` reads the envelope, composes a plan and writes; the read
        is outside the lock. A revocation in another process lands in that gap.
        The plan here was composed from a grant that WAS live, and the write is
        refused because the store is the only party holding the lock.
        """
        store = a_store(tmp_path)
        plan = self.a_plan_from_a_live_grant(store)
        store.revoke_grant(GRANT, at=T1, reason="withdrawn mid-cycle")

        with pytest.raises(StoreError) as refusal:
            store.admit(plan)

        assert "was revoked at" in str(refusal.value)

    def test_an_instant_outside_the_window_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(admitted_at="2026-03-01T00:00:00+00:00"))

        message = str(refusal.value)
        assert "is outside" in message
        assert WINDOW["expires_at"] in message

    def test_a_grant_with_no_readable_window_is_refused(self, tmp_path):
        """Absent and naive both, because neither leaves anything to be inside.

        Written through `put_grant` rather than by editing the store, so the
        case is one the store really ACCEPTS recording — which is the point:
        the window is not required at record time, so this boundary is where it
        has to be required.
        """
        for index, window in enumerate((
                {"valid_from": None, "expires_at": None},
                {"valid_from": "2025-12-31T00:00:00", "expires_at": T2})):
            case = tmp_path / f"window-{index}"
            case.mkdir()
            store = AgentControlStore(case / "store.json")
            store.put_context({"context_ref": CONTEXT})
            store.put_grant(a_grant(**window))
            with pytest.raises(StoreError) as refusal:
                store.admit(a_record())

            assert "no readable window" in str(refusal.value)
            assert "INVALID_GRANT_TIME" in str(refusal.value)

    def test_an_admission_with_no_instant_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(admitted_at=""))

        message = str(refusal.value)
        assert "does not say when it was admitted" in message
        assert "no window can be checked" in message

    def test_a_selection_outside_the_grants_items_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(selection=a_selection(selected=["SRV-2"])))

        message = str(refusal.value)
        assert "SRV-2" in message and "does not name" in message
        assert "ITEM_NOT_AUTHORIZED" in message

    def test_a_reservation_that_is_not_the_grants_budget_is_refused(self, tmp_path):
        """The check that keeps the reservation a copy rather than a second bound.

        `intake/daily_cycle.admission_plan` reads the three bounds off the grant;
        this is the line that says so at the moment they are written. Without it
        a caller could record a cycle under bounds its delegation never declared.
        """
        store = a_store(tmp_path)
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record(reservation={**BUDGET, "max_attempts": 9}))

        message = str(refusal.value)
        assert '"max_attempts": 9' in message and '"max_attempts": 2' in message
        assert "checked against its source" in message

    def test_a_grant_with_no_budget_cannot_cover_a_reservation(self, tmp_path):
        """`put_grant` records it and this refuses it, which is the split.

        The store that records delegations is not the store that decides what a
        bound is; a recorded grant with no budget is a grant no cycle can be
        admitted under, and the sentence prints the two sides rather than the
        word "mismatch".
        """
        store = AgentControlStore(tmp_path / "unbudgeted.json")
        store.put_context({"context_ref": CONTEXT})
        store.put_grant(a_grant(budget=None))
        with pytest.raises(StoreError) as refusal:
            store.admit(a_record())

        message = str(refusal.value)
        assert '"wall_seconds": 900' in message and "declares {}" in message

    def test_a_replay_is_not_re_judged_after_a_revocation(self, tmp_path):
        """Order is the message's: a replay answers a different question.

        "Has this already been admitted" is not "is this authorized now", and
        the retry comes from a resume, not from a new cycle. Answering the
        second question would refuse a resume that has nothing to change.
        """
        store = a_store(tmp_path)
        first = store.admit(a_record())
        store.revoke_grant(GRANT, at=T1, reason="the delegation ended")
        again = store.admit(a_record())

        assert first["replayed"] is False
        assert again["replayed"] is True
        assert again["grant_ref"] == GRANT

    def test_the_repair_that_works_is_a_new_grant(self, tmp_path):
        """A refusal a caller cannot act on is not a refusal, it is a wall.

        The store's own rule is that a grant is write-once and ends by
        revocation, so the repair is the one the model already declares: admit
        under the cycle's own new delegation.
        """
        store = a_store(tmp_path)
        store.revoke_grant(GRANT, at=T1, reason="the delegation ended")
        store.put_context({"context_ref": CONTEXT})
        store.put_grant(a_grant(grant_ref="grant-2"))
        stored = store.admit(a_record(cycle_ref="cyc-2", grant_ref="grant-2",
                                      action_key="act-2"))

        assert stored["grant_ref"] == "grant-2"
        assert set(store.admissions()) == {"cyc-2"}

    @staticmethod
    def a_plan_from_a_live_grant(store) -> dict:
        """The plan a caller composes from an envelope it read OUTSIDE the lock.

        Built through the real plan rather than hand-written, so the record
        under test is one `work admit` would really have produced.
        """
        return cycle.admission_plan(
            a_decision(), [a_row()], an_envelope(),
            cycle_ref="cyc-1", observed_at=T0, admitted_at=T0, action_key="act-1",
            lease={"resource": "browser:chromium", "scope": "session",
                   "holder": "cyc-1"})


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


class TestTheCycleIsWhatAPersonAcceptedNotWhatTheFabricProposes:
    """`work focus` on a real tree, where the two answers are visibly two.

    The tree is `intake/samples/workchain`, which exists to be run against with
    nothing stubbed (its own README says so). It is COPIED into the temporary
    directory first, because two of these tests write a `today.yaml` and a test
    that edits the fixture it asserts on is testing its own editing.

    What is being pinned is P6's fourth change, and the failure it replaces is
    not a crash: `select_focus` fills every spare slot it can, so a verb that
    printed it as the cycle could never report fewer objectives than the
    profile carries. On a day when a person selected one Item and it finished,
    that verb said two — and the two were proposals.
    """

    def tree(self, tmp_path: Path) -> dict:
        sample = REPO / "intake" / "samples" / "workchain"
        for part in ("boards", "records"):
            shutil.copytree(sample / part, tmp_path / part)
        return {"boards": tmp_path / "boards", "records": tmp_path / "records"}

    def focus(self, tree: dict, *extra: str) -> subprocess.CompletedProcess:
        return cli("work", "focus", "--boards-root", str(tree["boards"]),
                   "--records-root", str(tree["records"]),
                   "--workspace-root", str(tree["records"].parent),
                   # The fixture's projections are dated 2026-01-01, which is
                   # older than any TTL a caller would pass. The board's age is
                   # not what these tests are about — `test_daily_cycle.py` and
                   # `test_focus_source.py` hold it — so the window is opened
                   # wide rather than the fixture's dates rewritten, which
                   # would be editing the evidence to fit the assertion.
                   "--ttl", "999999999", *extra)

    def cycle_block(self, stdout: str) -> str:
        return stdout.split("— 本次周期")[1].split("— 建议")[0]

    def proposal_block(self, stdout: str) -> str:
        return stdout.split("— 建议")[1]

    def test_the_objective_a_person_chose_is_the_cycle(self, tmp_path):
        """The fixture's `sample-alpha/board/today.yaml` names SAMP-0002. It is
        in the cycle and the five in-flight Items beside it are NOT — they are
        the proposal's, in a block of their own."""
        done = self.focus(self.tree(tmp_path))
        assert done.returncode == 0, done.stderr
        cycle = self.cycle_block(done.stdout)
        assert "objectives  1" in cycle
        assert "SAMP-0002" in cycle
        for other in ("SAMP-0007", "SAMP-0101", "SAMP-0102", "SAMP-0103"):
            assert other not in cycle, f"{other} is in flight, not chosen"
        assert "focus       PLANNED" in cycle

    def test_the_proposal_is_printed_and_is_not_the_cycle(self, tmp_path):
        """The same run, the other block. `OVER_CAPACITY` and five in flight is
        the recommender's answer about this board; reading it as the cycle is
        the defect, and the two blocks are how a reader tells them apart."""
        done = self.focus(self.tree(tmp_path))
        proposal = self.proposal_block(done.stdout)
        assert "proposal    OVER_CAPACITY" in proposal
        assert "in flight   5" in proposal
        assert "SAMP-0007" in proposal

    def test_a_chosen_objective_that_is_finished_leaves_focus_at_zero(self, tmp_path):
        """Acceptance A, on the command line.

        The selection is rewritten to name SAMP-0004 — the fixture's one CLOSED
        Item, outcome set and every required document present. Nothing new was
        chosen. Focus is 0, the reason is on the Item, and the recommender is
        still proposing — which is exactly the pair of numbers that used to be
        one number.
        """
        tree = self.tree(tmp_path)
        (tree["records"] / "projects" / "sample-alpha" / "board"
         / "today.yaml").write_text(
            "schema: serveros/today.v1\nproject: sample-alpha\n"
            "date: '2026-09-25'\nitems:\n- SAMP-0004\n"
            "note: a selection that has since finished\nauthored_by: test\n",
            encoding="utf-8")
        done = self.focus(tree)
        assert done.returncode == 0, done.stderr
        cycle = self.cycle_block(done.stdout)
        assert "objectives  0" in cycle
        assert "SAMP-0004" in cycle and "TERMINAL_STATE" in cycle
        # And the other answer is not zero: the fabric still sees five
        # objectives in flight on this board. Two numbers, one run, and only
        # one of them is today's focus.
        assert "in flight   5" in self.proposal_block(done.stdout)

    def test_a_chosen_objective_that_is_delivered_but_owes_evidence_stays(
            self, tmp_path):
        """The other half of the pair, and the case the fixture already carried.

        SAMP-0003 is `DELIVERED` — a word that sounds finished — and its
        projection row has `outcome: null`. It has its report; what it does not
        have is the outcome the contract names at that state. So the boundary
        says OPEN, and the objective is still being carried, with the gap named.

        The failure this pins is a reader trusting the state NAME. Read from
        the documents alone, this Item looks finished: `work item SAMP-0003`
        prints DELIVERED with all five documents PRESENT. That reading is what
        made this test look unnecessary until the boundary was actually
        computed — the fixture's own row is the counterexample.
        """
        tree = self.tree(tmp_path)
        (tree["records"] / "projects" / "sample-alpha" / "board"
         / "today.yaml").write_text(
            "schema: serveros/today.v1\nproject: sample-alpha\n"
            "date: '2026-09-25'\nitems:\n- SAMP-0003\n"
            "note: a selection that was delivered and never accepted\n"
            "authored_by: test\n",
            encoding="utf-8")
        done = self.focus(tree)
        assert done.returncode == 0, done.stderr
        cycle = self.cycle_block(done.stdout)
        assert "objectives  1" in cycle
        assert "SAMP-0003" in cycle
        assert "AWAITING_ACCEPTANCE_EVIDENCE:outcome" in cycle
        # Not the state's own reason: SAMP-0003 being carried is not reported
        # as `TERMINAL_STATE`, which is the word that says the opposite.
        assert "TERMINAL_STATE" not in cycle

    def test_the_item_verb_prints_what_the_item_still_owes(self, tmp_path):
        """The other face of the same answer, on the command a person runs
        about ONE Item — because `<ID> DELIVERED` on its first line is a word,
        and this is where the word is checked. Both directions: SAMP-0003 owes
        an outcome, and SAMP-0004 owes nothing."""
        tree = self.tree(tmp_path)
        owed = cli("work", "item", "SAMP-0003", "--project", "sample-alpha",
                   "--boards-root", str(tree["boards"]),
                   "--records-root", str(tree["records"]),
                   "--workspace-root", str(tree["records"].parent))
        assert owed.returncode == 0, owed.stderr
        assert "完成边界   未结清" in owed.stdout
        assert "↳ 缺字段   outcome" in owed.stdout
        # The report IS there, and saying so is the difference between this
        # Item and one that never produced its deliverable.
        assert "缺文档" not in owed.stdout

        discharged = cli("work", "item", "SAMP-0004", "--project", "sample-alpha",
                         "--boards-root", str(tree["boards"]),
                         "--records-root", str(tree["records"]),
                         "--workspace-root", str(tree["records"].parent))
        assert "完成边界   已完成" in discharged.stdout

    def test_nobody_having_chosen_is_reported_as_such_and_not_as_an_empty_choice(
            self, tmp_path):
        """Both files deleted: no person has ever chosen. Focus is 0, and the
        word is `NO_SELECTION_AUTHORED` — not `SELECTION_IS_EMPTY`, which would
        say somebody chose nothing."""
        tree = self.tree(tmp_path)
        for project in ("sample-alpha", "sample-beta"):
            path = tree["records"] / "projects" / project / "board" / "today.yaml"
            if path.exists():
                path.unlink()
        done = self.focus(tree)
        cycle = self.cycle_block(done.stdout)
        assert "accepted    UNPRODUCED" in cycle
        assert "NO_SELECTION_AUTHORED" in cycle
        assert "objectives  0" in cycle

    def test_work_admit_admits_the_cycle_and_not_the_proposal(self, tmp_path):
        """The verb that writes, pointed at the same board.

        It refuses — no grant is recorded on this node and no row can name one
        — and the refusal names SAMP-0002, the Item a person chose. That is the
        assertion that matters: `work admit` read the cycle block. If it had
        read the proposal, the sentence would name SAMP-0007.
        """
        tree = self.tree(tmp_path)
        env = a_data_root(tmp_path)
        done = cli("work", "admit", "--boards-root", str(tree["boards"]),
                   "--records-root", str(tree["records"]),
                   "--workspace-root", str(tree["records"].parent),
                   "--ttl", "999999999", "--cycle", "cyc-1",
                   "--action-key", "act-1", "--resource", "browser:chromium",
                   "--scope", "session", env_extra=env)
        assert done.returncode == 0, done.stderr
        assert "refused     SELECTION_CARRIES_NO_GRANT" in done.stdout
        assert "SAMP-0002" in done.stdout
        assert "SAMP-0007" not in done.stdout


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
