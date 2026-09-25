"""The daily profile's rows, read against the board that exists rather than a factory.

WHY THIS FILE EXISTS
--------------------
`tests/test_daily_cycle.py` builds its rows from `item()`, a factory that sets
`worker_stopped: True`, `resources_released: True`, `grant_ref`, `scope_digest`
and `next_action` on every row. With one exception those fields are invented by
the test: no producer on this node emits any of them. So the 47 tests in that file
were all passing while the policy was, on the only projection it could ever be
pointed at, wrong in a way none of them could observe — it reported all 17 rows
of the live board as unresolved work, 7 CLOSED and 4 DELIVERED among them,
because it read the ABSENCE of a field as the field being False.

The exception is `worker_stopped`, which gained a producer on 2026-09-25 and
whose factory value is now a value a real dispatch can produce: the Item's
admission names an action key, and the receipt `invoke_once` filed under that key
is what says whether a worker is still held. `WorkerLivenessIsReadFromTheReceipt
ThatOwnsIt` below drives that chain with receipts the real writer produced, and
the same chain decides an Item whose Run is terminal. The remaining four fields
are still invented by the factory, and `NOT_PRODUCED` still says so for each.

That is the same failure the round keeps meeting: a surface was measured
correctly and the measurement was read as a fact about the world. The fix for
it is not another synthetic case. It is a fixture that is a CAPTURE — the real
producer's own bytes, at a recorded time, with digests — so that the row shape
under test is the row shape that exists, and a change in the producer shows up
here as a failure rather than passing silently.

TWO CAPTURES, BOTH REAL
-----------------------
`tests/data/personal-board-live-2026-09-22.json`
    17 rows, `serverosctl work board --json`, captured 2026-09-22T03:37:37+08:00.
    The projection of record for the node's records-derived Items. Its three
    `current.json` files carry `generated_at` of 2026-09-10 — twelve days old, and
    that is a fact about the node, not about the fixture: the declared producer
    of these projections (`board render`, `fleet/state-root-contract.yaml`) has
    not re-rendered them since.

`tests/data/personal-board-fresh-2026-09-22.json`
    1 row, `serverosctl work board --json --boards-root <fresh>`, where `<fresh>`
    was rendered the same minute by that declared producer against the live
    work-engine store (`python -m workengine board render --state-dir
    /data/home/vince/state/serveros --project P --output-dir …`). It is the other
    half of the same measurement: freshly rendered, the fabric holds ONE Item
    (SRV-0011, INBOX) where the twelve-day-old projection holds seventeen, and
    they do not overlap. `tests/test_focus_source.py` reports that; resolving it
    is a change to which authority answers "what state is this Item in" and is
    not taken here.

Both files are TRIMMED — each row keeps `id`, `project`, `state`, `kind`,
`title` and `priority`, which is what the adapter reads, and drops the rest of
a 3.4 KB-per-row document. Trimming is mechanical and the full captures'
digests are recorded below, so the trim can be checked against the original.
Nothing in either file was written by hand; a hand-edited fixture would make
every assertion here a test of the editing.

The `because` fields of the two priority cells are the board's own prose, and
they are asserted on: a reader of a failing test should learn that the priority
column is empty because the field is PROPOSED_NOT_APPROVED, not that a fixture
went missing.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intake.daily_cycle import continue_focus, liveness, select_focus
from intake.focus_source import (
    NOT_PRODUCED,
    PRODUCED,
    RECEIPT_LIVENESS,
    Refused,
    compose,
    oldest,
    priority_level,
    rows_from_board,
    worker_stopped_by_item,
)
from runtime.bounded_invocation import invoke_once

DATA = Path(__file__).resolve().parent / "data"
LIVE = DATA / "personal-board-live-2026-09-22.json"
FRESH = DATA / "personal-board-fresh-2026-09-22.json"

# sha256 of the trimmed files as committed. If a row is edited by hand, this
# fails before any assertion about behaviour does, which is the order a reader
# wants: "the evidence changed" is a different message from "the policy is
# wrong".
LIVE_SHA256 = "98227b3e0cb7f833cf1d4d1527f1f8b302442669fdc1970a040f6ff80a4ecef1"
LIVE_FULL_SHA256 = "fda145ac0c562dfa4b2e377a1b539a362c265f64b4dce5e6c19295f0644530e9"
FRESH_SHA256 = "c1df36b613021458b5aedc9d5a9117decd847025f240569c1b160221e934697e"
FRESH_FULL_SHA256 = "769a8f5ea5f29c5d6bc9d1b63b6a19aaa5db2cb18587a61b66d4180e5940a98a"

# The real projection ages, read from the three `current.json` files the live
# board names. Passed in rather than read here: a test that reads the live state
# directory passes or fails depending on what else has run today.
LIVE_TIMES = {
    "litellm": "2026-09-10T07:03:23.887775+00:00",
    "multi-user-claw": "2026-09-10T07:03:24.051538+00:00",
    "serveros": "2026-09-10T07:28:49.215528+00:00",
}
# And the fresh render's, from the same three filenames, one minute apart.
FRESH_TIMES = {
    "litellm": "2026-09-21T19:38:56.013923+00:00",
    "multi-user-claw": "2026-09-21T19:38:56.071319+00:00",
    "serveros": "2026-09-21T19:38:56.129825+00:00",
}

# The machine's clock when both captures were taken: 2026-09-22 03:38 +08:00.
NOW = datetime(2026, 9, 21, 19, 40, tzinfo=timezone.utc)
# The live capture's own `at`, which is the instant its rows describe. Not the
# projections' age — that is `LIVE_TIMES`, and the two answer different questions.
CAPTURED_AT = "2026-09-22T03:37:37+08:00"

# A person's authored selection naming one Item, in the shape `workchain.py`
# writes and `continue_focus` reads. `state` is derived from the Items the way
# `load_today` derives it, so this fixture cannot carry the pair (`SELECTED`,
# `[]`) the producer cannot produce.
SELECTED_ONE = {"state": "SELECTED", "items": ["SRV-0001"], "why": "a person chose this",
                "date": "2026-09-22"}



def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def board_rows(board: dict) -> list[dict]:
    return [row for section in board["sections"] for row in section["rows"]]


class TheCaptureIsTheProducerItClaimsToBe(unittest.TestCase):
    def test_the_live_capture_is_unmodified(self):
        text = LIVE.read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), LIVE_SHA256)

    def test_the_fresh_capture_is_unmodified(self):
        text = FRESH.read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), FRESH_SHA256)

    def test_the_full_captures_are_named_so_the_trim_can_be_checked(self):
        """The digests of the untrimmed captures, recorded so that a reader who
        re-runs the producing command can show the trim dropped no row and
        invented none. A fixture whose provenance cannot be re-derived is an
        assertion about a file rather than about the fabric."""
        for digest in (LIVE_FULL_SHA256, FRESH_FULL_SHA256):
            self.assertEqual(len(digest), 64)

    def test_the_captures_are_the_two_producer_outputs_they_name(self):
        live, fresh = load(LIVE), load(FRESH)
        for board in (live, fresh):
            self.assertEqual(board["schema"], "serveros/personal-board.v1")
            self.assertEqual(sorted(board["projects"][0]),
                             ["path", "project", "state", "why"])
            self.assertEqual(len(board["projects"]), 3)
        self.assertEqual(len(board_rows(live)), 17)
        self.assertEqual([r["id"] for r in board_rows(fresh)], ["SRV-0011"])

    def test_the_two_captures_disagree_and_the_disagreement_is_the_finding(self):
        """Not a fixture defect: the fabric's own two answers, kept together so
        the disagreement is visible in one place. The live board's 17 Items are
        records-derived and its projections have not been re-rendered since
        2026-09-10; the producer run today renders from the work-engine store,
        which holds SRV-0011 and six marketops/tokyo Items and none of the 17.
        `docs/daily-work-cycle.md` §12 requires a real board before the cycle is
        claimed to work; this is what that requirement meets."""
        live_ids = {r["id"] for r in board_rows(load(LIVE))}
        fresh_ids = {r["id"] for r in board_rows(load(FRESH))}
        self.assertEqual(live_ids & fresh_ids, set())


class TheAdapterInventsNothing(unittest.TestCase):
    def test_no_row_carries_a_field_without_a_declared_producer(self):
        rows = rows_from_board(load(LIVE))
        self.assertEqual(len(rows), 17)
        for row in rows:
            self.assertEqual(set(row) - set(PRODUCED), set(),
                             f"{row['id']} carries a field PRODUCED does not "
                             f"name a producer for")

    def test_no_row_claims_a_liveness_the_board_never_stated(self):
        """The specific invention this adapter refuses. The board row carries
        `runs` with their states, so `worker_stopped` could be synthesized; a
        Run recorded as finished is a database row and whether its process
        stopped is a different fact."""
        for row in rows_from_board(load(LIVE)):
            for field in ("worker_stopped", "resources_released"):
                self.assertNotIn(field, row)
            self.assertEqual(liveness(row), (None, []))

    def test_the_absent_fields_are_named_with_a_reason(self):
        for field in ("resources_released", "grant_ref",
                      "scope_digest", "next_action", "blocked_by"):
            self.assertTrue(NOT_PRODUCED.get(field),
                            f"{field} is absent from every row and the module "
                            f"does not say why")

    def test_the_field_that_gained_a_producer_left_the_table_that_says_it_has_none(self):
        """`worker_stopped` is the one field this round moved between the two
        tables, and a field left in both is a field whose state depends on which
        table the reader opened. The general rule is asserted next door in
        `CompletionIsCarriedOutAndNeverInvented`; this names the move, so that a
        reader of the daily-work policy finds the reason it moved beside the
        absence it replaced."""
        self.assertIn("worker_stopped", PRODUCED)
        self.assertNotIn("worker_stopped", NOT_PRODUCED)
        self.assertIn("admission", PRODUCED["worker_stopped"])
        self.assertIn("receipt", PRODUCED["worker_stopped"])

    def test_a_row_without_a_canonical_id_is_refused(self):
        broken = {"sections": [{"id": "p", "title": "t", "rows": [
            {"project": "p", "state": "READY"}]}]}
        with self.assertRaises(Refused):
            rows_from_board(broken)

    def test_a_board_that_is_not_a_board_is_refused(self):
        with self.assertRaises(Refused):
            rows_from_board({"items": []})


class WorkerLivenessIsReadFromTheReceiptThatOwnsIt(unittest.TestCase):
    """The producer `worker_stopped` gained, exercised against the real writer.

    Every receipt here is written by `runtime.bounded_invocation.invoke_once`
    itself, not by a `json.dump` in this file. That is the point of the class:
    the question is what the ONE writer of receipts records, and a fixture that
    spelled the fields out by hand would be a test of this file's idea of the
    writer — which is the failure mode the module being tested exists to name.

    Two of the cases below are the ones that decide whether an Item whose Run
    reached a terminal state releases, so they are asserted at both ends: the
    value the writer recorded (read back from the receipt) and the value the row
    carries (read back from `compose`).

    The board rows are synthetic here, and deliberately so — a liveness read is
    about an Item and the dispatch admitted for it, not about the 17 rows of the
    live capture. `ThePolicyOnTheRealBoard` is where those meet the policy.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.receipts = self.root / "receipts"
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()

    def dispatch(self, key, argv, **kw):
        """A real dispatch, and the receipt it wrote."""
        return invoke_once(key=key, argv=argv, cwd=self.cwd,
                           receipts=self.receipts, **kw)

    def receipt(self, key):
        return json.loads((self.receipts / f"{key}.json").read_text(encoding="utf-8"))

    def admit(self, item_id, key):
        """One entry of `agent_control_store.store.json#admissions`, trimmed to
        the two fields the chain walks: the selection that names the Item and
        the action key the receipt is filed under."""
        return {"selection": {"selected": [item_id]}, "action_key": key}

    def board(self, *item_ids):
        return {"sections": [{"id": "today", "rows": [
            {"id": i, "project": "test", "state": "INBOX"} for i in item_ids]}]}

    def composed(self, item_ids, admissions, **kw):
        return compose(self.board(*item_ids), admissions=admissions, **kw)

    def test_a_dispatch_that_exited_leaves_the_field_off_the_row(self):
        """The case that must NOT come back as held.

        `invoke_once` records `worker_stopped: False` before it spawns and
        rewrites it to `True` only when no process was started, so an `EXITED`
        receipt carries `False` as an initializer and `descendants_verified:
        False` because nothing in this fabric ever verifies a descendant. Reading
        that `False` as "a worker is still held" is what puts every finished
        dispatch permanently in flight — the OVER_CAPACITY false alarm this node
        has already paid for once. Undecidable is the honest reading, and the
        field is left OFF the row so `daily_cycle` decides by the Item's state
        and says that it did.
        """
        self.dispatch("dispatch-exited", ["/bin/true"])
        recorded = self.receipt("dispatch-exited")
        self.assertEqual(recorded["status"], "EXITED")
        self.assertIs(recorded["worker_stopped"], False)
        self.assertIs(recorded["descendants_verified"], False)

        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-exited")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        entry = composed["liveness"]["items"]["SRV-0001"]
        self.assertEqual(composed["liveness"]["status"], "PRODUCED")
        self.assertNotIn("worker_stopped", row)
        self.assertNotIn("worker_stopped", entry)
        self.assertIn("RECEIPT:EXITED:DESCENDANTS_UNVERIFIED", entry["because"])
        self.assertEqual(liveness(row), (None, []))
        self.assertEqual(entry["action_keys"], ["dispatch-exited"])

    def test_a_dispatch_that_never_started_says_the_worker_stopped(self):
        """The writer's one positive claim, and the only path that makes it."""
        self.dispatch("dispatch-nostart", [str(self.root / "no-such-binary")])
        recorded = self.receipt("dispatch-nostart")
        self.assertEqual(recorded["status"], "START_FAILED")
        self.assertIs(recorded["worker_stopped"], True)
        self.assertEqual(recorded["effect"], "NO_PROCESS_STARTED")

        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-nostart")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertIs(row["worker_stopped"], True)
        self.assertEqual(liveness(row), (False, []))
        self.assertIn("NO_PROCESS_STARTED",
                      composed["liveness"]["items"]["SRV-0001"]["because"])

    def test_a_dispatch_that_was_cut_off_keeps_the_item_held(self):
        """Acceptance D's raw material, at the adapter: the process was
        signalled and the writer itself says the effect needs reconciliation, so
        a Run that later reaches a terminal state has released nothing."""
        self.dispatch("dispatch-timeout", ["/bin/sh", "-c", "sleep 30"], timeout=1)
        recorded = self.receipt("dispatch-timeout")
        self.assertEqual(recorded["status"], "TIMED_OUT")
        self.assertIn("reconciliation", recorded["reason"])

        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-timeout")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertIs(row["worker_stopped"], False)
        self.assertEqual(liveness(row), (True, ["worker_stopped"]))

    def test_a_refused_dispatch_names_what_it_is_rather_than_holding_or_releasing(self):
        """The status the reader got wrong first, and the reason the table is
        read before the record's initializers.

        `invoke_once` writes a refusal when the outputs of a call with this key
        already exist: no process was started, and the record it leaves carries
        `worker_stopped: False` and `direct_process_exited: False` because those
        are the values it was created with. A reader that consulted
        `direct_process_exited` before the status answered "a process was started
        and nothing recorded it gone" — a claim about a process this call never
        started — and held the Item forever on a dispatch that never ran. The
        honest answer is neither released nor held: this receipt is not evidence
        about a worker, and the Item's own state decides, which is what the field
        being absent says.
        """
        self.receipts.mkdir(parents=True, mode=0o700)
        (self.receipts / "dispatch-refused.stdout").write_text("")
        recorded = self.dispatch("dispatch-refused", ["/bin/true"])
        self.assertEqual(recorded["status"], "REFUSED")
        self.assertEqual(recorded["reason"], "OUTPUT_ALREADY_EXISTS")
        self.assertIs(recorded["worker_stopped"], False)
        self.assertIs(recorded["direct_process_exited"], False)

        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-refused")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertNotIn("worker_stopped", row)
        self.assertEqual(liveness(row), (None, []))
        self.assertIn("RECEIPT:REFUSED:NOTHING_STARTED_BY_THIS_CALL",
                      composed["liveness"]["items"]["SRV-0001"]["because"])

    def test_an_admission_with_no_receipt_keeps_the_item_held(self):
        """An admission whose dispatch left no receipt is the one case where the
        fabric knows a dispatch was admitted and cannot see its end, so it holds
        rather than releasing. The reason names the key, because "which key" is
        the question an operator has to answer next."""
        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "never-run")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertIs(row["worker_stopped"], False)
        entry = composed["liveness"]["items"]["SRV-0001"]
        self.assertIn("NO_RECEIPT_FOR_ACTION_KEY", entry["because"])
        self.assertEqual(entry["action_keys"], ["never-run"])

    def test_a_receipt_filed_under_another_action_key_is_not_this_dispatch(self):
        """A pair the writer cannot produce, made by copying its own bytes: the
        file at `<key>.json` carries the key it was written under, and a receipt
        that names a different dispatch is not evidence about this one. Held, as
        every other unexplainable receipt here is."""
        self.dispatch("dispatch-a", ["/bin/true"])
        (self.receipts / "dispatch-b.json").write_bytes(
            (self.receipts / "dispatch-a.json").read_bytes())
        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-b")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertIs(row["worker_stopped"], False)
        self.assertIn("RECEIPT_NAMES_ANOTHER_ACTION_KEY",
                      composed["liveness"]["items"]["SRV-0001"]["because"])

    def test_a_symlinked_receipt_is_undecidable_and_not_followed(self):
        """Nothing here follows a link out of the receipts root, and a link is
        why the field is absent rather than `False`: nobody has offered evidence
        about a process, which is different from evidence that one is running."""
        self.dispatch("dispatch-real", ["/bin/true"])
        os.symlink(self.receipts / "dispatch-real.json", self.receipts / "dispatch-link.json")
        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-link")},
                                 receipts_root=self.receipts)
        row = composed["rows"][0]
        self.assertNotIn("worker_stopped", row)
        self.assertEqual(liveness(row), (None, []))
        self.assertIn("RECEIPT_IS_A_SYMLINK",
                      composed["liveness"]["items"]["SRV-0001"]["because"])

    def test_one_held_dispatch_holds_the_item_whatever_the_order(self):
        """More than one dispatch can name one Item, and a `True` from one call
        says nothing about the process another call started. Held beats
        undecidable beats released, and the answer does not depend on the order
        the store happened to hand the admissions over in."""
        self.dispatch("dispatch-done", ["/bin/true"])                       # undecidable
        self.dispatch("dispatch-cut", ["/bin/sh", "-c", "sleep 30"], timeout=1)  # held
        self.dispatch("dispatch-nostart", [str(self.root / "no-such-binary")])   # released
        keys = ["dispatch-cut", "dispatch-done", "dispatch-nostart"]
        for order in (keys, list(reversed(keys))):
            with self.subTest(order=order):
                admissions = {f"c{i}": self.admit("SRV-0001", k) for i, k in enumerate(order)}
                composed = self.composed(["SRV-0001"], admissions, receipts_root=self.receipts)
                self.assertIs(composed["rows"][0]["worker_stopped"], False)
                self.assertIn("REQUIRES_RECONCILIATION",
                              composed["liveness"]["items"]["SRV-0001"]["because"])

    def test_an_item_no_admission_names_is_not_an_item_with_a_held_worker(self):
        """Two Items and one admission. The named Item's read is about a worker;
        the other has no entry at all, because "nobody dispatched anything for
        this Item" is not "a dispatch cannot say whether its worker stopped", and
        only the second one is about a worker. Collapsing them into a per-row
        `False` is how a quiet Item starts reading as a stuck one."""
        composed = self.composed(["SRV-0001", "SRV-0002"],
                                 {"c1": self.admit("SRV-0001", "never-run")},
                                 receipts_root=self.receipts)
        self.assertEqual(composed["liveness"]["status"], "PRODUCED")
        self.assertEqual(sorted(composed["liveness"]["items"]), ["SRV-0001"])
        self.assertIs(composed["liveness"]["items"]["SRV-0001"]["worker_stopped"], False)
        rows = {row["id"]: row for row in composed["rows"]}
        self.assertNotIn("worker_stopped", rows["SRV-0002"])
        self.assertEqual(liveness(rows["SRV-0002"]), (None, []))
        self.assertIs(rows["SRV-0001"]["worker_stopped"], False)

    def test_a_store_that_names_no_item_on_this_board_says_that(self):
        """The node's normal state, and the one the live control store is in: no
        admission at all, so there is no action key to look up and no row is
        about a worker. Not a fault, and not a `False` per row — the status says
        what was missing rather than every row carrying the suspicion."""
        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-9999", "never-run")},
                                 receipts_root=self.receipts)
        self.assertEqual(composed["liveness"]["status"], "NO_ADMISSION_NAMES_AN_ITEM")
        self.assertEqual(composed["liveness"]["items"], {})
        self.assertNotIn("worker_stopped", composed["rows"][0])
        self.assertIn("nothing has", composed["liveness"]["because"])

    def test_no_receipts_root_never_becomes_a_released_worker(self):
        """There is no default receipts directory, and the absence of one is a
        reason per the read rather than a `False` per row. This is the assertion
        that fails if a later change defaults the root to the node's own path or
        answers the missing root with a verdict."""
        self.dispatch("dispatch-exited", ["/bin/true"])
        composed = self.composed(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-exited")})
        self.assertEqual(composed["liveness"]["status"], "RECEIPTS_ROOT_NOT_NAMED")
        self.assertIsNone(composed["liveness"]["receipts_root"])
        self.assertIn("no default", composed["liveness"]["because"])
        for row in composed["rows"]:
            self.assertNotIn("worker_stopped", row)
            self.assertEqual(liveness(row), (None, []))

    def test_a_relative_receipts_root_is_refused(self):
        """A relative root resolves against whatever directory the caller stood
        in, and a receipt read from the wrong directory is an answer about a
        different dispatch — refused, not defaulted."""
        with self.assertRaises(Refused):
            worker_stopped_by_item({"c1": self.admit("SRV-0001", "k")},
                                   receipts_root="receipts", item_ids={"SRV-0001"})

    def terminal_row(self, item_ids, admissions, **kw):
        """The board as the two acceptance-D tests need it: one Item whose Run
        has reached a terminal state, carrying the boundary's own verdict that it
        owes nothing. Both tests use this, so the ONLY difference between them is
        what the receipt records — a pair that differs in two places proves
        neither."""
        composed = self.composed(item_ids, admissions, **kw)
        row = composed["rows"][0]
        row["state"] = "CLOSED"
        row["completion"] = {"state": "DISCHARGED", "assessed": True,
                             "missing_fields": [], "missing_documents": [],
                             "unreported_documents": [], "why": "the boundary says so"}
        return composed

    def test_a_terminal_run_whose_worker_is_still_held_releases_nothing(self):
        """Acceptance D, end to end: from the receipt the writer filed to the
        focus decision, with no fixture field in between.

        The Item's Run has reached a terminal state and the contract's boundary
        says it owes nothing — so on the state alone it is finished and frees its
        slot. The dispatch admitted for it was cut off after its process was
        signalled, and the writer itself says the effect needs reconciliation, so
        nothing was released: the objective a person accepted still holds its slot
        and continues. Before 2026-09-25 this could not be reached on this node at
        all — `daily_cycle.classify` carried the branch, and no board row could
        carry the field the branch tests.
        """
        self.dispatch("dispatch-cut", ["/bin/sh", "-c", "sleep 30"], timeout=1)
        composed = self.terminal_row(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-cut")},
                                     receipts_root=self.receipts)
        self.assertIs(composed["rows"][0]["worker_stopped"], False)

        decision = continue_focus(composed["rows"], {}, SELECTED_ONE,
                                  now=NOW, observed_at=CAPTURED_AT)
        self.assertEqual(decision["selected"], ["SRV-0001"])
        self.assertEqual(decision["because"]["SRV-0001"],
                         "WORKER_OR_RESERVATION_HELD:worker_stopped")
        self.assertEqual(decision["excluded"], [])

    def test_the_same_terminal_run_releases_once_the_receipt_says_nothing_started(self):
        """The other side of acceptance D, so the test above cannot pass by the
        decision being unable to release anything at all. Same board, same
        terminal state, same discharged boundary; the only difference is what the
        receipt records."""
        self.dispatch("dispatch-nostart", [str(self.root / "no-such-binary")])
        composed = self.terminal_row(["SRV-0001"], {"c1": self.admit("SRV-0001", "dispatch-nostart")},
                                     receipts_root=self.receipts)
        self.assertIs(composed["rows"][0]["worker_stopped"], True)

        decision = continue_focus(composed["rows"], {}, SELECTED_ONE,
                                  now=NOW, observed_at=CAPTURED_AT)
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "SRV-0001", "reason": "TERMINAL_STATE"}])

    def test_the_table_names_only_statuses_the_writer_writes(self):
        """Half of the transcription, checked against the writer's own source:
        every status `RECEIPT_LIVENESS` answers is a literal in
        `runtime/bounded_invocation.py`.

        The other half — a status the writer gains and this table does not have —
        cannot be checked mechanically here and is not pretended to be: it is
        answered `None` with `RECEIPT:STATUS_NOT_KNOWN_HERE:<status>`, which is a
        weaker read and still an honest one, and the list below is where a reader
        finds out that the table was written against the writer as of a date.
        """
        # Read off `runtime/bounded_invocation.invoke_once` on 2026-09-25: the
        # record is written with `status: STARTED` before the spawn and rewritten
        # to one of the other six, so all seven are statuses a stored receipt can
        # carry.
        writer_statuses = {"STARTED", "EXITED", "TIMED_OUT", "OUTPUT_LIMIT",
                           "IO_FAILED", "START_FAILED", "REFUSED"}
        self.assertEqual(set(RECEIPT_LIVENESS), writer_statuses)
        source = (Path(__file__).resolve().parents[1]
                  / "runtime" / "bounded_invocation.py").read_text(encoding="utf-8")
        for status in writer_statuses:
            self.assertIn(f'"{status}"', source,
                          f"{status} is in the liveness table and is not a status "
                          f"the writer writes")


class PriorityComesFromTheCardOrNotAtAll(unittest.TestCase):
    def test_the_live_rows_have_no_priority_and_the_board_says_why(self):
        for raw in board_rows(load(LIVE)):
            level, why = priority_level(raw)
            self.assertIsNone(level, raw["id"])
            self.assertEqual(why["state"], "UNPRODUCED")
            self.assertIn("card_extension", why["why"])
            self.assertIn("not approved", why["why"])

    def test_a_declared_level_is_taken(self):
        for cell, expected in (("personal", "P2"), ("source", "P0")):
            row = {"priority": {"personal": {"state": "UNPRODUCED", "why": "w"},
                                "source": {"state": "UNPRODUCED", "why": "w"}}}
            row["priority"][cell] = {"state": expected}
            level, why = priority_level(row)
            self.assertEqual(level, expected)
            self.assertEqual(why["cell"], cell)

    def test_a_priority_that_is_not_the_boards_structure_is_not_a_level(self):
        """A scalar level in a row that never went through the board is not a
        level this fabric produced, and an object is not a crash."""
        for value in (None, 3, {"level": "P0"}, {"personal": "P0"}, ["P0"]):
            level, why = priority_level({"priority": value})
            self.assertIsNone(level, repr(value))
            self.assertEqual(why["state"], "NOT_A_PRIORITY_CELL")


class CompletionIsCarriedOutAndNeverInvented(unittest.TestCase):
    """The boundary the board computed, passed through unchanged — or absent.

    `completion` is the one field here whose producer is another module in this
    repository rather than the board's own renderer: `workchain.completion_of`
    decides it from the contract and the row's documents, and this adapter's job
    is to carry the answer, not to reach the same conclusion a second way. Two
    derivations of "does this Item still owe something" would be two answers,
    and the one a person reads would depend on which surface they asked.
    """

    def test_a_row_that_carries_a_verdict_carries_it_out_unchanged(self):
        verdict = {"state": "OPEN", "assessed": True, "missing_fields": ["outcome"],
                   "missing_documents": [], "unreported_documents": [],
                   "why": "in DELIVERED the contract requires ['outcome', 'report']"}
        board = {"sections": [{"id": "today", "rows": [
            {"id": "X-1", "project": "p", "state": "DELIVERED",
             "completion": dict(verdict)}]}]}
        row = rows_from_board(board)[0]
        self.assertEqual(row["completion"], verdict)

    def test_a_row_that_carries_no_verdict_gains_none(self):
        """Absence is a third answer and not a favourable one.

        Both frozen captures predate the boundary, so every one of their rows
        is this case — and an adapter that supplied `COMPLETE` for a silent row
        would make the whole 2026-09-22 board read as finished work.
        """
        for raw in board_rows(load(LIVE)):
            self.assertNotIn("completion", raw)
        for row in rows_from_board(load(LIVE)):
            self.assertNotIn("completion", row, row["id"])

    def test_a_verdict_that_is_not_a_mapping_is_not_carried_as_one(self):
        """A null or a string in that position is the board failing to answer,
        and `classify` reads an absent verdict as NOT_ASSESSED. Coercing it
        into a dict here would turn the failure into a value."""
        for value in (None, "", "NOT_ASSESSED", []):
            board = {"sections": [{"id": "today", "rows": [
                {"id": "X-1", "project": "p", "state": "DELIVERED",
                 "completion": value}]}]}
            self.assertNotIn("completion", rows_from_board(board)[0], repr(value))

    def test_the_two_field_tables_do_not_both_claim_it(self):
        """`PRODUCED` says who produces a field; `NOT_PRODUCED` says why nobody
        does. A field in both is a contradiction that would read to a caller as
        whichever one they happened to open."""
        self.assertIn("completion", PRODUCED)
        self.assertNotIn("completion", NOT_PRODUCED)
        self.assertEqual(set(PRODUCED) & set(NOT_PRODUCED), set())


class TheAgeIsTheProjectionsNotTheRenders(unittest.TestCase):
    def test_the_live_board_is_stale_by_its_own_projections(self):
        composed = compose(load(LIVE), projection_times=LIVE_TIMES)
        self.assertEqual(composed["observed_at"], LIVE_TIMES["litellm"])
        decision = select_focus(composed["rows"], {}, now=NOW,
                                observed_at=composed["observed_at"])
        self.assertEqual(decision["status"], "BLOCKED")
        self.assertEqual(decision["reason"], "STALE_BOARD")

    def test_the_board_that_was_just_composed_is_not_evidence_of_a_fresh_board(self):
        """The trap this file exists to keep visible: `work board` composes in
        the current second, so the document's own `at` always looks fresh while
        the facts in it are twelve days old."""
        board = load(LIVE)
        self.assertNotEqual(board["at"], LIVE_TIMES["litellm"])
        self.assertEqual(board["at"][:10], "2026-09-22")

    def test_a_freshly_rendered_projection_is_fresh(self):
        composed = compose(load(FRESH), projection_times=FRESH_TIMES)
        self.assertEqual(composed["observed_at"], FRESH_TIMES["litellm"])
        decision = select_focus(composed["rows"], {}, now=NOW,
                                observed_at=composed["observed_at"])
        self.assertNotEqual(decision.get("reason"), "STALE_BOARD")

    def test_an_undated_projection_makes_the_age_unknown(self):
        times = dict(FRESH_TIMES); times["serveros"] = None
        self.assertIsNone(compose(load(FRESH), projection_times=times)["observed_at"])

    def test_a_naive_timestamp_is_refused_rather_than_compared(self):
        with self.assertRaises(Refused):
            oldest({"p": "2026-09-10T07:03:23.887775"})

    def test_the_oldest_is_chosen_as_an_instant_not_as_a_string(self):
        """`2026-09-10T07:28+00:00` sorts before `2026-09-10T15:00+08:00` and
        names an EARLIER instant, so a lexicographic minimum would be inverted
        by the pair below while looking correct."""
        earlier_instant_later_string = {
            "a": "2026-09-10T15:00:00+08:00",   # == 07:00Z
            "b": "2026-09-10T07:28:49+00:00",
        }
        self.assertEqual(oldest(earlier_instant_later_string),
                         "2026-09-10T15:00:00+08:00")


class ThePolicyOnTheRealBoard(unittest.TestCase):
    """The measurement, run against the capture rather than described."""

    def setUp(self):
        self.composed = compose(load(LIVE), projection_times=LIVE_TIMES)
        self.decision = select_focus(self.composed["rows"], {}, now=NOW,
                                     observed_at=CAPTURED_AT)

    def choose(self, observed_at=CAPTURED_AT, rows=None, grants=None):
        """The row logic, over the real rows, at the moment they were captured.

        `observed_at` is a parameter so that `TheAgeIsTheProjectionsNotTheRenders`
        can show what the same rows produce when their age is the projections'
        real one; here it is the capture's, because the subject is which row
        leaves the decision by which branch, and a stale-board refusal would
        answer a different question.
        """
        return select_focus(rows or self.composed["rows"], grants or {},
                            now=NOW, observed_at=observed_at)

    def test_finished_work_is_not_reported_as_in_flight(self):
        """The defect, stated as the property that replaces it: of 17 real rows,
        the 7 CLOSED and 4 DELIVERED ones leave the decision by name."""
        terminal = [e for e in self.choose()["excluded"]
                    if e["reason"] == "TERMINAL_STATE"]
        self.assertEqual(sorted(e["id"] for e in terminal),
                         sorted(["LIT-0201", "LIT-0202", "LIT-0204", "MUC-0101",
                                 "MUC-0102", "MUC-0103", "SRV-0001",
                                 "LIT-0102", "MUC-0105", "SRV-0002", "SRV-0005"]))

    def test_only_unresolved_work_holds_a_slot(self):
        decision = self.choose()
        self.assertEqual(sorted(decision["carryover"]),
                         ["LIT-0101", "LIT-0103", "LIT-0104", "LIT-0203",
                          "MUC-0104", "SRV-0003"])
        self.assertEqual(sorted(decision["carryover_because"].values()),
                         ["STATE_HOLDS_SLOT"] * 6)

    def test_the_node_is_over_capacity_and_that_is_the_true_answer(self):
        """Six unresolved Items against a profile of two: the live node has four
        Items in flight and two waiting, so 'no new focus' is a fact about the
        node and not a defect of the reading."""
        decision = self.choose()
        self.assertEqual(decision["status"], "OVER_CAPACITY")
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["unused_slots"], 0)

    def test_the_reading_this_replaced_called_all_seventeen_in_flight(self):
        """The positive control, and the only evidence that the fix is a fix.
        This is the expression the function used to hold — one line — applied to
        the same real rows: 17 carryover, including everything that has
        finished."""
        rows = self.composed["rows"]
        naive = [r["id"] for r in rows
                 if r.get("worker_stopped") is not True
                 or r.get("resources_released") is not True]
        self.assertEqual(len(naive), 17)
        self.assertEqual(len(self.choose()["carryover"]), 6)

    def test_every_row_leaves_the_decision_with_a_reason(self):
        decision = self.choose()
        decided = (len(decision["carryover"]) + len(decision["excluded"])
                   + len(decision["selected"]))
        self.assertEqual(decided, 17)

    def test_the_fresh_projection_plans_and_says_why_nothing_was_selected(self):
        composed = compose(load(FRESH), projection_times=FRESH_TIMES)
        decision = select_focus(composed["rows"], {}, now=NOW,
                                observed_at=composed["observed_at"])
        self.assertEqual(decision["status"], "PLANNED")
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "SRV-0011", "reason": "STATE_NOT_ELIGIBLE"}])

    def test_nothing_is_selected_without_a_grant_and_the_reason_says_so(self):
        """A row that clears every other test still has no authorization,
        because no grant authority exists on this node. Selection returning
        empty is the honest answer; the reason is what stops it reading as
        'nothing to do'."""
        rows = [{"id": "T-1", "project": "test", "state": "READY",
                 "priority": "P1", "next_action": "research"}]
        decision = select_focus(rows, {}, now=NOW, observed_at=CAPTURED_AT)
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "MISSING_OR_REVOKED_GRANT"}])


if __name__ == "__main__":
    unittest.main()
