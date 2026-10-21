"""Crash consistency between the evidence row and `finalize.json` (#290/#291).

WHY THIS FILE EXISTS
--------------------
`_write_finalize` writes one conclusion twice: as a row in `evidence` and as the
`finalize.json` file that row's `locator` names. The row's `sha256` anchors the
file. The two writes must agree even if the process dies between them, and a
reader must refuse a record it cannot verify rather than invent one.

The pre-hardening code had three failures this file pins:

  * It renamed `finalize.json.tmp` into place AFTER the database transaction
    committed. A crash in that window left a committed evidence row whose file
    did not exist. The fix promotes the file before the commit, so a failed
    promotion rolls the row back.
  * On a later finalize it RE-CREATED a missing file from the database row as an
    `UNSIGNED` success. That invented a record and silently dropped the receipt
    the row's anchor covered. A missing file is now `EVIDENCE_MISSING`; a
    crash-left `.tmp` is promoted only if its bytes hash to the row's anchor.
  * It read whatever the LATEST evidence row was, not the finalize row, and it
    treated the mere presence of a `runner_receipt` object as `T10_VERIFIED`.
    A tampered file, or one whose label and receipt contradict each other, now
    fails closed.

LEVEL: LOCAL / HERMETIC. A temp SQLite file and a temp artifact directory, no
daemon, no network.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workengine import WorkItem
from workengine.authority import ReceiptError, T10Binding, VerifiedRunReceipt
from workengine.model import Activity, ContextMode, ExecutionContext, RunState
from workengine.store import ServerOSStore

import v1_import_isolation

# Captured at collection time, while the in-tree `workengine` is the one in
# `sys.modules`. The T10 suites replace it with a pinned archive during their
# session fixtures; restoring these exact objects is what keeps this module's
# `ReceiptError` the class its store raises. See the helper's docstring.
_WORKENGINE = v1_import_isolation.snapshot()


def setUpModule():
    """Undo a preceding T10 fixture's process-wide `workengine` swap."""
    v1_import_isolation.restore(_WORKENGINE)


def tearDownModule():
    """Leave the in-tree package in place for whatever module runs next."""
    v1_import_isolation.restore(_WORKENGINE)


ITEM = "DEM-0001"
ACTION = "action:test:1"
ACTOR = "human:hwz"
DIGEST = "a" * 64
SHA = "b" * 64
COMMIT = "c" * 40
RECEIPT_REF = "trusted:receipt:1"


def a_receipt(**overrides) -> VerifiedRunReceipt:
    values = dict(
        item_ref=ITEM,
        run_ref="RUN-0001",
        action_key=ACTION,
        input_digest=DIGEST,
        receipt_sha256=SHA,
        process_exit_code=0,
        verdict="PASS",
        runner_identity="runner:test",
        commit_sha=COMMIT,
        actor_ref=ACTOR,
    )
    values.update(overrides)
    return VerifiedRunReceipt(**values)


class StubReceiptAuthority:
    """A test authority. A passing test does not mean a production parent."""

    def __init__(self, result=None) -> None:
        self.result = result
        self.calls = []

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        self.calls.append((receipt_ref, item_ref, run_ref, now))
        if receipt_ref != RECEIPT_REF:
            raise ValueError("not a receipt this authority issued")
        return self.result


class FinalizeConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.store = ServerOSStore(Path(self._td.name) / "state")
        self.store.register_project("demo", "DEM")
        self.item = self.store.create_item(
            WorkItem(ITEM, "demo", "x", "test", "/card"))
        self.store.add_activity(Activity(self.item.id, "test", "test"))
        self.store.attach_context(ExecutionContext(
            self.item.id, "test", "repo", ContextMode.TEST, "base"))
        self.run = self.store.begin_run(
            self.item.id, "test", "worker", "key",
            Path(self._td.name) / "artifacts")
        for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
            self.run = self.store.advance_run(self.item.id, self.run.id, state)
        self.locator = Path(self.store.get_run(
            self.item.id, self.run.id).artifact_dir) / "finalize.json"

    # -- helpers -----------------------------------------------------------

    def _authority(self) -> StubReceiptAuthority:
        return StubReceiptAuthority(a_receipt(run_ref=self.run.id))

    def _binding(self, **overrides) -> T10Binding:
        values = dict(item_ref=ITEM, run_ref=self.run.id,
                      commit_sha=COMMIT, actor_ref=ACTOR)
        values.update(overrides)
        return T10Binding(**values)

    def _t10(self, authority=None, *, binding=None, receipt_ref=RECEIPT_REF,
             verdict="PASS", exit_code=0) -> dict:
        return self.store.finalize_run(
            self.item.id, self.run.id,
            verdict=verdict, exit_code=exit_code, command="pytest",
            cases=["one"], revision="r", dirty=False,
            t10_required=True,
            t10_binding=self._binding() if binding is None else binding,
            receipt_ref=receipt_ref,
            receipt_authority=authority or self._authority())

    def _evidence(self) -> dict:
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM evidence WHERE item_id=? AND run_id=? "
                "AND kind='finalize' ORDER BY evidence_id DESC LIMIT 1",
                (self.item.id, self.run.id)).fetchone()
        return dict(row)

    def _rewrite_anchored(self, payload: dict) -> None:
        """Write a payload and move the row's anchor with it.

        This simulates a torn/forged record that still MATCHES its anchor, so
        the test exercises the label/consistency rules rather than the hash.
        """
        self.locator.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        digest = hashlib.sha256(self.locator.read_bytes()).hexdigest()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE evidence SET sha256=? WHERE item_id=? AND run_id=? "
                "AND kind='finalize'",
                (digest, self.item.id, self.run.id))

    # -- the file is the other half of the record --------------------------

    def test_the_evidence_anchor_is_the_file_bytes(self) -> None:
        self._t10()
        row = self._evidence()
        self.assertEqual(row["sha256"],
                         hashlib.sha256(self.locator.read_bytes()).hexdigest())
        self.assertTrue(self.locator.exists())

    def test_a_missing_evidence_file_is_refused_and_not_reconstructed(self) -> None:
        self._t10()
        self.locator.unlink()
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_MISSING")
        self.assertFalse(
            self.locator.exists(),
            "a missing evidence file must not be re-created from the row")
        self.assertEqual(self.store.get_run(
            self.item.id, self.run.id).state, RunState.SUCCEEDED)

    def test_a_missing_finalize_row_is_refused(self) -> None:
        self._t10()
        with self.store.transaction() as conn:
            conn.execute(
                "DELETE FROM evidence WHERE item_id=? AND run_id=? "
                "AND kind='finalize'", (self.item.id, self.run.id))
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_MISSING")

    def test_a_crash_left_tmp_is_promoted_only_with_a_matching_anchor(self) -> None:
        self._t10()
        temporary = Path(str(self.locator) + ".tmp")
        self.locator.replace(temporary)
        again = self._t10()
        self.assertEqual(again["authorization"], "T10_VERIFIED")
        self.assertTrue(self.locator.exists())
        self.assertFalse(temporary.exists())
        self.assertEqual(self._evidence()["sha256"],
                         hashlib.sha256(self.locator.read_bytes()).hexdigest())

    def test_a_tampered_tmp_is_refused_and_not_promoted(self) -> None:
        self._t10()
        temporary = Path(str(self.locator) + ".tmp")
        self.locator.replace(temporary)
        temporary.write_text(
            temporary.read_text(encoding="utf-8").replace(
                '"process_exit_code": 0', '"process_exit_code": 1'),
            encoding="utf-8")
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_HASH_MISMATCH")
        self.assertFalse(self.locator.exists(),
                         "a tampered .tmp must not be promoted into position")

    def test_a_tampered_final_file_is_refused(self) -> None:
        self._t10()
        self.locator.write_text(
            self.locator.read_text(encoding="utf-8").replace(
                f'"actor_ref": "{ACTOR}"', '"actor_ref": "human:other"'),
            encoding="utf-8")
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_HASH_MISMATCH")

    # -- the label and the receipt must agree ------------------------------

    def test_an_unsigned_label_with_a_receipt_is_torn(self) -> None:
        self._t10()
        payload = json.loads(self.locator.read_text(encoding="utf-8"))
        payload["authorization"] = "UNSIGNED"
        self._rewrite_anchored(payload)
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_TORN")

    def test_a_t10_record_without_the_label_is_torn(self) -> None:
        self._t10()
        payload = json.loads(self.locator.read_text(encoding="utf-8"))
        payload.pop("authorization")
        self._rewrite_anchored(payload)
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_TORN")

    def test_a_legacy_record_without_a_label_is_unsigned(self) -> None:
        """A record written before the label existed claims no receipt."""
        self.store.finalize_run(
            self.item.id, self.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision="r", dirty=False)
        payload = json.loads(self.locator.read_text(encoding="utf-8"))
        payload.pop("authorization")
        self._rewrite_anchored(payload)
        again = self.store.finalize_run(
            self.item.id, self.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision="r", dirty=False)
        self.assertEqual(again["authorization"], "UNSIGNED")
        self.assertIs(again["t10_acceptable"], False)

    def test_a_t10_label_without_a_receipt_is_torn(self) -> None:
        self._t10()
        payload = json.loads(self.locator.read_text(encoding="utf-8"))
        payload.pop("runner_receipt")
        self._rewrite_anchored(payload)
        with self.assertRaises(ReceiptError) as ctx:
            self._t10()
        self.assertEqual(ctx.exception.reason_code, "EVIDENCE_TORN")

    # -- the crash window is closed in the right order ---------------------

    def test_a_failed_file_promotion_rolls_the_database_back(self) -> None:
        """The file is promoted BEFORE the commit; a failure leaves FINALIZING.

        If the rename happened after the commit, this test would find the Run
        SUCCEEDED with an evidence row whose locator does not exist — the torn
        record a reader cannot repair.
        """
        with mock.patch.object(Path, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self._t10()
        self.assertEqual(self.store.get_run(
            self.item.id, self.run.id).state, RunState.FINALIZING)
        with self.store.transaction() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evidence WHERE item_id=? AND run_id=?",
                (self.item.id, self.run.id)).fetchone()["n"]
        self.assertEqual(count, 0, "the evidence row must roll back with the file")

    def test_a_later_non_finalize_evidence_row_does_not_shadow_the_record(self) -> None:
        self._t10()
        locator = Path(self._td.name) / "later-evidence.json"
        locator.write_text("{}\n", encoding="utf-8")
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,"
                "cases_json,exit_code,revision,dirty,verdict,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.item.id, self.run.id, "test", str(locator),
                 hashlib.sha256(b"{}\n").hexdigest(), "pytest", "[]", 0, "r",
                 0, "PASS", "2026-10-20T00:00:00Z"))
        again = self._t10()
        self.assertEqual(again["authorization"], "T10_VERIFIED")


if __name__ == "__main__":
    unittest.main()
