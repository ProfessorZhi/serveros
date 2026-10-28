#!/usr/bin/env python3
"""#417 — the durable Batch registration a ChatGPT dispatch leaves behind.

The owner's requirement, verbatim: *没有持久登记就不宣称 chatGPT 退出后可追踪*
(without a persistent registration, do not claim the dispatch is still
traceable after ChatGPT exits). This suite holds the registration layer to
that: one file, one Batch ID, six identity columns that are never conflated,
and a consumption interface that takes #456's commander plan and ACKs.

What is NOT here: any delivery truth. The registration is what the dispatcher
asserted at ACK time. Whether a message reached the owner is #423's ledger's
answer, and `batch.py`'s aggregator never reads this file for that.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BATCH_DIR = ROOT / "integrations" / "fleet-v1-milestone-batch"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


batch = _load("serveros_fleet_v1_milestone_batch", BATCH_DIR / "batch.py")
registration = _load("serveros_fleet_v1_milestone_batch_registration",
                     BATCH_DIR / "registration.py")

# -- the dispatch this suite is about --------------------------------------- #

BATCH_ID = "dispatch-2026-10-28-alpha"
RECIPIENT = "chatgpt:conv-2026-10-28"
REGISTERED_AT = "2026-10-27T10:00:00+00:00"
DEADLINE = "2026-10-27T11:00:00Z"

DEV_A, BOOT_A = "device:tokyo-shared-dev-01", "boot-tokyo-1"
DEV_B, BOOT_B = "device:dell-5820", "boot-dell-1"
DEV_C, BOOT_C = "device:win-fjd", "boot-win-1"
RUN_A, RUN_B, RUN_C = "run_demo_1", "run_dell_1", "run_fjd_1"
SESSION_A, SESSION_B, SESSION_C = ("session:tokyo-alpha", "session:dell-beta",
                                  "session:fjd-gamma")
DELIVERY_A, DELIVERY_B, DELIVERY_C = "om_dispatch_1", "om_dispatch_2", "om_dispatch_3"


class Commander:
    """#456's documented contract shape, standing in until it lands on this base.

    `validate_batch(plan, snapshot) -> {"verdict", "batch_id", "task_count",
    "blocked"}` is the whole interface this module consumes. Deliberately
    duck-typed: a stand-in here proves the seam our side needs, and the live
    test at the bottom of this file starts enforcing the real module the moment
    #456 is on a base that carries it.
    """

    def __init__(self, verdict: str = "PASS", blocked=()):
        self.verdict = verdict
        self.blocked = list(blocked)
        self.calls = []

    def validate_batch(self, plan, snapshot):
        self.calls.append((plan, snapshot))
        return {"verdict": self.verdict, "batch_id": plan.get("batch_id"),
                "task_count": len(plan.get("tasks") or []),
                "blocked": list(self.blocked)}


def plan(batch_id: str = BATCH_ID, tasks=3) -> dict:
    """#456's dispatcher plan: what the tasks ARE, before anything was issued."""
    rows = [
        {"task_id": "t-tokyo", "device_ref": DEV_A, "runtime": "claude",
         "project_id": "serveros", "worktree": "/w/tokyo",
         "scope": "integrations/fleet-v1-milestone-batch", "acceptance": "a",
         "tests": "b", "report": "c", "idempotency_key": "idem-tokyo",
         "session_ref": SESSION_A},
        {"task_id": "t-dell", "device_ref": DEV_B, "runtime": "codex",
         "project_id": "serveros", "worktree": "/w/dell",
         "scope": "integrations/x", "acceptance": "a", "tests": "b",
         "report": "c", "idempotency_key": "idem-dell",
         "session_ref": SESSION_B},
        {"task_id": "t-fjd", "device_ref": DEV_C, "runtime": "claude",
         "project_id": "serveros", "worktree": "/w/fjd",
         "scope": "integrations/y", "acceptance": "a", "tests": "b",
         "report": "c", "idempotency_key": "idem-fjd",
         "session_ref": SESSION_C},
    ]
    return {"batch_id": batch_id, "tasks": rows[:tasks]}


def acks(*, runs=(RUN_A, RUN_B, RUN_C), deliveries=(DELIVERY_A, DELIVERY_B,
                                                   DELIVERY_C),
         boots=(BOOT_A, BOOT_B, BOOT_C), sessions=(SESSION_A, SESSION_B,
                                                   SESSION_C),
         tasks=3, **changes) -> list:
    """What the commander learned at ACK time, one row per task."""
    devices = (DEV_A, DEV_B, DEV_C)
    out = []
    for index in range(tasks):
        row = {"task_id": ("t-tokyo", "t-dell", "t-fjd")[index],
               "device_ref": devices[index],
               "session_ref": sessions[index],
               "run_id": runs[index] if index < len(runs) else None,
               "delivery_id": deliveries[index],
               "boot_id": boots[index]}
        row.update(changes)
        out.append(row)
    return out


#: "no contract was passed" — distinct from "the caller passed None on purpose".
_DEFAULT = object()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-reg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.directory = self.tmp / "batches"

    # -- registering -------------------------------------------------------

    def register(self, *, plan_value=None, ack_rows=None, contract=_DEFAULT,
                 path=None, snapshot=None, **changes):
        options = {"contract": Commander() if contract is _DEFAULT else contract,
                   "snapshot": {"devices": {}} if snapshot is None else snapshot,
                   "source": "cloudcli",
                   "registered_at": REGISTERED_AT,
                   "registered_by": "chatgpt",
                   "conversation_ref": RECIPIENT,
                   "expires_at": DEADLINE}
        options.update(changes)
        return registration.from_commander(plan_value or plan(),
                                           ack_rows if ack_rows is not None
                                           else acks(),
                                           path=path, **options)

    def path(self) -> Path:
        return registration.path_for(self.directory, BATCH_ID)

    def document(self, path=None) -> dict:
        return json.loads((path or self.path()).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# one file, one Batch ID
# --------------------------------------------------------------------------- #
class ARegistrationIsOneDurableDocument(Base):
    def test_a_registration_round_trips_through_one_file(self):
        _, outcome = self.register(path=self.path())
        self.assertEqual(outcome, registration.REGISTERED)
        document = self.document()
        self.assertEqual(document["schema"], registration.SCHEMA)
        self.assertEqual(document["batch_id"], BATCH_ID)
        self.assertEqual(document["registered_by"], "chatgpt")
        self.assertEqual(document["conversation_ref"], RECIPIENT)
        self.assertEqual(len(document["members"]), 3)

        loaded = registration.load(self.path())
        self.assertEqual(loaded.batch_id, BATCH_ID)
        self.assertEqual(
            [member.run_id for member in loaded.members], [RUN_A, RUN_B, RUN_C])
        self.assertEqual(loaded.batch.summary_key, f"batch:{BATCH_ID}:summary",
                         "the durable Batch ID is what the receipt key is built "
                         "from; a restart may not rename it")
        # The declaration the aggregator gets back is the one that was filed.
        self.assertEqual(
            [member.key for member in loaded.batch.members],
            [f"cloudcli:{RUN_A}", f"cloudcli:{RUN_B}", f"cloudcli:{RUN_C}"])

    def test_the_write_is_one_step_and_leaves_no_temporary_behind(self):
        self.register(path=self.path())
        leftovers = [name for name in os.listdir(self.directory)
                     if name != self.path().name]
        self.assertEqual(leftovers, [], f"a temporary survived: {leftovers}")

    def test_saving_the_same_batch_twice_writes_once(self):
        filed, outcome = self.register(path=self.path())
        self.assertEqual(outcome, registration.REGISTERED)
        first = self.path().read_bytes()
        self.assertEqual(registration.save(self.path(), filed),
                         registration.UNCHANGED)
        _, outcome = self.register(path=self.path())
        self.assertEqual(outcome, registration.UNCHANGED)
        self.assertEqual(self.path().read_bytes(), first)

    def test_a_second_registration_of_one_batch_may_not_change_a_member(self):
        self.register(path=self.path())
        first = self.path().read_bytes()
        moved = acks(boots=(BOOT_A, "boot-dell-2", BOOT_C))
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=moved, path=self.path())
        self.assertEqual(caught.exception.code, "CONFLICTING_REGISTRATION")
        # The refused registration did not overwrite the accepted one.
        self.assertEqual(self.path().read_bytes(), first)

    def test_a_batch_id_cannot_escape_or_leave_its_directory(self):
        for ident in ("../evil", "a/b", "a\\b", "..", "."):
            with self.subTest(ident=ident), self.assertRaises(
                    batch.BatchRefused) as caught:
                batch.Batch(batch_id=ident, members=(
                    batch.BatchMember(source="cloudcli", ref=RUN_A,
                                      device_ref=DEV_A, boot_id=BOOT_A),))
            self.assertEqual(caught.exception.code, "BAD_BATCH_ID")

    def test_a_hand_built_batch_carries_no_origin_and_a_registered_one_does(self):
        hand = batch.Batch(batch_id=BATCH_ID, members=(
            batch.BatchMember(source="cloudcli", ref=RUN_A, device_ref=DEV_A,
                              boot_id=BOOT_A),))
        self.assertIsNone(hand.origin,
                          "a batch nobody registered may not claim durability")
        self.assertEqual(self.register(path=self.path())[0].batch.origin,
                         batch.ORIGIN_REGISTRATION)


# --------------------------------------------------------------------------- #
# six columns, six facts
# --------------------------------------------------------------------------- #
class TheIdentityColumnsAreNeverConflated(Base):
    def test_every_identity_field_keeps_its_own_column(self):
        self.register(path=self.path())
        member = self.document()["members"][0]
        self.assertEqual(set(member), set(registration.MEMBER_FIELDS))
        # device / provider / session / run / delivery / boot, each in its own
        # column, each holding its own value.
        self.assertEqual(member["device_ref"], DEV_A)
        self.assertEqual(member["provider"], "claude")
        self.assertEqual(member["session_ref"], SESSION_A)
        self.assertEqual(member["run_id"], RUN_A)
        self.assertEqual(member["delivery_id"], DELIVERY_A)
        self.assertEqual(member["boot_id"], BOOT_A)
        identities = [member["device_ref"], member["session_ref"],
                      member["run_id"], member["delivery_id"], member["boot_id"]]
        self.assertEqual(len(set(identities)), len(identities))

    def test_the_run_id_is_the_watch_ref_and_the_delivery_id_is_not(self):
        loaded, _ = self.register(path=self.path())
        member = loaded.members[0]
        self.assertEqual(member.run_id, RUN_A)
        self.assertEqual(member.member.ref, RUN_A)
        self.assertEqual(loaded.batch.watches()[0].ref, RUN_A)
        self.assertEqual(loaded.batch.watches()[0].boot_id, BOOT_A)

    def test_a_run_id_that_is_also_a_delivery_id_is_refused(self):
        # #416: a deliveryId can answer RUN_NOT_FOUND while the runId still
        # reads. One value in both columns would hide exactly that difference.
        self.assertDup(acks(runs=(RUN_A, DELIVERY_B, RUN_C)), "delivery_id",
                       "run_id")

    def test_a_session_ref_may_not_stand_in_for_an_id_either(self):
        # The rule is one rule over three columns, so it is pinned where the
        # three are assembled rather than only through the ACK adapter.
        member = batch.BatchMember(source="cloudcli", ref=RUN_B, device_ref=DEV_B,
                                   boot_id=BOOT_B, task_id="t-dell")
        for session, delivery, first, second in (
                (RUN_B, DELIVERY_B, "run_id", "session_ref"),
                (SESSION_B, SESSION_B, "session_ref", "delivery_id")):
            with self.subTest(second=second), self.assertRaises(
                    registration.RegistrationRefused) as caught:
                registration.RegisteredMember(
                    member=member, provider="codex", session_ref=session,
                    delivery_id=delivery)
            self.assertEqual(caught.exception.code, "ID_CONFLATION")
            self.assertIn(first, caught.exception.why)
            self.assertIn(second, caught.exception.why)

    def assertDup(self, ack_rows, first, second):
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=ack_rows, path=self.path())
        self.assertEqual(caught.exception.code, "ID_CONFLATION")
        self.assertIn(first, caught.exception.why)
        self.assertIn(second, caught.exception.why)
        self.assertFalse(self.path().exists(), "a refused batch left a file")

    def test_the_provider_is_the_agent_runtime_and_the_source_is_the_reader(self):
        loaded, _ = self.register(path=self.path())
        self.assertEqual([member.provider for member in loaded.members],
                         ["claude", "codex", "claude"])
        self.assertEqual([watch.source for watch in loaded.batch.watches()],
                         ["cloudcli", "cloudcli", "cloudcli"])


# --------------------------------------------------------------------------- #
# an ACK is delivery, not execution
# --------------------------------------------------------------------------- #
class AQueuedAckIsNotAStart(Base):
    def test_an_ack_with_no_run_is_registered_but_not_watchable(self):
        loaded, _ = self.register(
            ack_rows=acks(runs=(RUN_A, None, RUN_C)), path=self.path())
        self.assertEqual(len(loaded.batch.members), 3,
                         "a queued task is still part of the dispatch")
        self.assertEqual(len(loaded.batch.watches()), 2,
                         "a task with no run has nothing to watch")
        queued = [member for member in loaded.members if member.run_id is None]
        self.assertEqual([member.task_id for member in queued], ["t-dell"])
        self.assertFalse(loaded.batch.members[1].watchable)

    def test_ack_only_is_delivery_not_execution(self):
        # #456's own words: a receipt that is delivery-only is not a run.
        loaded, _ = self.register(
            ack_rows=acks(runs=(RUN_A, RUN_B, RUN_C),
                          ack_only=True), path=self.path())
        self.assertEqual(len(loaded.batch.watches()), 0,
                         "an ACK-only receipt may not become a watch")
        self.assertEqual([member.run_id for member in loaded.members],
                         [None, None, None])

    def test_a_queued_member_without_a_deadline_is_refused(self):
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=acks(runs=(RUN_A, None, RUN_C)),
                          expires_at=None, path=self.path())
        self.assertEqual(caught.exception.code, "NO_DEADLINE")
        self.assertIn("t-dell", caught.exception.why)
        self.assertFalse(self.path().exists())

    def test_a_queued_member_learns_its_run_without_a_second_registration(self):
        self.register(ack_rows=acks(runs=(RUN_A, None, RUN_C)), path=self.path())
        amended, outcome = registration.amend(
            self.path(), task_id="t-dell", run_id=RUN_B, boot_id=BOOT_B,
            delivery_id="om_dispatch_2b", at="2026-10-27T10:05:00+00:00")
        self.assertEqual(outcome, registration.REGISTERED)
        self.assertEqual(amended.batch_id, BATCH_ID)
        self.assertEqual(len(amended.batch.watches()), 3)
        self.assertEqual(amended.members[1].run_id, RUN_B)
        self.assertEqual(amended.members[1].member.boot_id, BOOT_B)
        # One file still, and re-filing it is a no-op.
        self.assertEqual(registration.save(self.path(), amended),
                         registration.UNCHANGED)
        reloaded = registration.load(self.path())
        self.assertEqual(reloaded.members[1].run_id, RUN_B)
        self.assertEqual(reloaded.registered_at, REGISTERED_AT,
                         "an amendment is not a re-registration")

    def test_an_issued_run_can_never_be_amended_to_a_different_one(self):
        self.register(path=self.path())
        with self.assertRaises(registration.RegistrationRefused) as caught:
            registration.amend(self.path(), task_id="t-tokyo",
                               run_id="run_somebody_else", boot_id=BOOT_A,
                               delivery_id=DELIVERY_A, at=REGISTERED_AT)
        self.assertEqual(caught.exception.code, "ALREADY_ISSUED")
        self.assertEqual(registration.load(self.path()).members[0].run_id, RUN_A)

    def test_amending_an_unknown_task_or_the_same_values_is_not_a_write(self):
        self.register(ack_rows=acks(runs=(RUN_A, None, RUN_C)), path=self.path())
        with self.assertRaises(registration.RegistrationRefused) as caught:
            registration.amend(self.path(), task_id="t-nowhere", run_id=RUN_B,
                               boot_id=BOOT_B, delivery_id="om_x", at=REGISTERED_AT)
        self.assertEqual(caught.exception.code, "UNKNOWN_TASK")
        _, outcome = registration.amend(
            self.path(), task_id="t-dell", run_id=None, boot_id=None,
            delivery_id=DELIVERY_B, at=REGISTERED_AT)
        self.assertEqual(outcome, registration.UNCHANGED)


# --------------------------------------------------------------------------- #
# the consumption interface from #456
# --------------------------------------------------------------------------- #
class TheCommanderInterfaceIsTheOnlyWayIn(Base):
    def test_the_commander_validates_the_plan_before_anything_is_written(self):
        contract = Commander(verdict="BLOCKED", blocked=[
            {"task_id": "t-dell", "code": "SESSION_BUSY",
             "detail": "session:dell-beta is RUNNING"}])
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(contract=contract, path=self.path())
        self.assertEqual(caught.exception.code, "COMMANDER_REFUSED")
        self.assertIn("SESSION_BUSY", caught.exception.why)
        self.assertFalse(self.path().exists())
        # The plan really was validated by #456's own judgement, with the live
        # snapshot, and not re-implemented here.
        self.assertEqual(len(contract.calls), 1)
        self.assertEqual(contract.calls[0][0]["batch_id"], BATCH_ID)
        self.assertEqual(contract.calls[0][1], {"devices": {}})

    def test_a_missing_commander_contract_is_refused_not_skipped(self):
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(contract=None, path=self.path())
        self.assertEqual(caught.exception.code, "COMMANDER_CONTRACT_MISSING")

    def test_the_ack_verdict_cannot_make_a_member_verified(self):
        # Every ACK says PASS and completed. None of that is a milestone fact:
        # #428's read decides, and until it speaks the members are PENDING.
        loaded, _ = self.register(
            ack_rows=acks(verdict="PASS", state="completed"), path=self.path())
        self.assertEqual(len(loaded.batch.watches()), 3)
        # Structurally: the envelope has no column a verdict could hide in, so
        # an ACK cannot settle anything even by accident.
        self.assertEqual(
            [field for field in registration.MEMBER_FIELDS
             if "verdict" in field or field in ("state", "completed",
                                                "native_result")], [])
        aggregator = batch.BatchAggregator(loaded.batch, notifier=_Seam(),
                                           clock=lambda: 0)
        tick = aggregator.absorb_rows([])
        self.assertEqual(tick.verdicts, {f"cloudcli:{run_id}": batch.PENDING
                                         for run_id in (RUN_A, RUN_B, RUN_C)})
        self.assertFalse(tick.settled)

    def test_a_missing_ack_or_an_unknown_ack_is_refused(self):
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=acks(tasks=2), path=self.path())
        self.assertEqual(caught.exception.code, "ACK_MISSING")
        self.assertIn("t-fjd", caught.exception.why)

        extra = acks() + [{"task_id": "t-ghost", "device_ref": DEV_A,
                           "delivery_id": "om_ghost"}]
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=extra, path=self.path())
        self.assertEqual(caught.exception.code, "ACK_UNKNOWN_TASK")
        self.assertIn("t-ghost", caught.exception.why)

    def test_an_ack_that_names_another_session_is_not_this_task_s_ack(self):
        _, outcome = self.register(path=self.path())
        self.assertEqual(outcome, registration.REGISTERED)
        rows = acks()
        rows[1]["session_ref"] = SESSION_A
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=rows, plan_value=plan(),
                          path=self.directory / "other.json")
        self.assertEqual(caught.exception.code, "ACK_INCOMPLETE")
        self.assertIn("t-dell", caught.exception.why)
        self.assertFalse((self.directory / "other.json").exists())

    def test_an_ack_without_a_delivery_id_is_incomplete(self):
        rows = acks()
        rows[1].pop("delivery_id")
        with self.assertRaises(registration.RegistrationRefused) as caught:
            self.register(ack_rows=rows, path=self.path())
        self.assertEqual(caught.exception.code, "ACK_INCOMPLETE")
        self.assertIn("t-dell", caught.exception.why)

    def test_the_registration_records_which_plan_it_validated(self):
        loaded, _ = self.register(path=self.path())
        canonical = json.dumps(plan(), sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"))
        expected = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(loaded.plan_digest, expected)
        self.assertEqual(self.document()["plan_digest"], expected)
        # A different plan is a different digest — the file cannot silently
        # stand for a dispatch that was never validated.
        other = plan(tasks=2)
        self.assertNotEqual(registration.digest(other), expected)


class _Seam:
    """A notifier that can answer `send_text` and nothing else matters here."""

    def send_text(self, **_kwargs):
        raise AssertionError("this suite does not send")

    def reconcile(self, _key):
        raise AssertionError("this suite does not send")


# --------------------------------------------------------------------------- #
# when #456 is on a base that carries it, the stand-in stops being a stand-in
# --------------------------------------------------------------------------- #
CONTRACT_PATH = ROOT / "integrations" / "chatgpt-fleet-next" / "batch_contract.py"


class TheLiveCommanderContractIsConsumedAsIs(Base):
    @unittest.skipUnless(CONTRACT_PATH.exists(),
                         "#456's batch_contract.py is not on this base; the "
                         "stand-in above is the contract, and this test is "
                         "NOT_RUN rather than claimed")
    def test_a_plan_the_live_contract_accepts_becomes_a_registration(self):
        live = _load("serveros_chatgpt_batch_contract", CONTRACT_PATH)
        self.assertTrue(hasattr(live, "validate_batch"))
        snapshot = {"devices": {DEV_A: {"roles": ["dev"], "runtimes": ["claude"]},
                                DEV_B: {"roles": ["dev"], "runtimes": ["codex"]},
                                DEV_C: {"roles": ["dev"], "runtimes": ["claude"]}},
                    "create_allowed": {DEV_A: True, DEV_B: True, DEV_C: True},
                    "sessions": []}
        loaded, outcome = self.register(contract=live, plan_value=plan(tasks=3),
                                        ack_rows=acks(), path=self.path(),
                                        snapshot=snapshot)
        self.assertEqual(outcome, registration.REGISTERED)
        self.assertEqual(loaded.batch_id, BATCH_ID)


if __name__ == "__main__":
    unittest.main()
