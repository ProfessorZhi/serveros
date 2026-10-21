"""A run's SUCCEEDED is not a caller's sentence; it needs a verified receipt.

WHY THIS FILE EXISTS
--------------------
`finalize_run` writes the caller's `verdict` and `exit_code` into evidence and
concludes `SUCCEEDED` when they spell `PASS` and `0`. serveros #263 introduced a
real bounded-runner receipt (`action_key`, `input_digest`, `receipt_sha256`,
`process_exit_code`) and then said, correctly, that Work Engine finalization
does not consume it — `work_engine_consumption=NOT_WIRED`.

This file is the Work Engine side of that seam. `finalize_run_verified` accepts
only a receipt returned by a trusted `RunnerReceiptAuthority`; child text, a
caller-supplied JSON hash, a bare `exit_code=0`, or an unverified path are not
receipts. The authority is the trust boundary and is not wired yet, so the
production status is `BLOCKED_ORIGINAL_OWNER`; the negative matrix is the part
that must hold before anybody wires it.

The legacy `finalize_run` is left untouched for callers that predate the seam.
It is not T10-authoritative, and this module does not pretend otherwise.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workengine.authority import ReceiptError, VerifiedRunReceipt
from workengine.model import Activity, ContextMode, ExecutionContext, RunState
from workengine.store import ServerOSStore
from workengine import WorkItem

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
DIGEST = "a" * 64
SHA = "b" * 64
COMMIT = "c" * 40


def receipt(**overrides) -> VerifiedRunReceipt:
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
        actor_ref="human:hwz",
    )
    values.update(overrides)
    return VerifiedRunReceipt(**values)


class StubReceiptAuthority:
    """A test authority. It is not a production trust source."""

    def __init__(self, result=None, *, accept=("trusted:receipt:1",), error=None):
        self.result = result
        self.accept = set(accept)
        self.error = error
        self.calls = []

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        self.calls.append((receipt_ref, item_ref, run_ref, now))
        if self.error is not None:
            raise self.error
        if receipt_ref not in self.accept:
            raise ValueError("not a receipt this authority issued")
        return self.result


class RunnerReceiptTest(unittest.TestCase):
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

    def _finalize(self, *, authority=None, receipt_ref="trusted:receipt:1",
                  verdict="PASS", exit_code=0, **overrides):
        return self.store.finalize_run_verified(
            self.item.id, self.run.id,
            verdict=verdict, exit_code=exit_code, command="pytest",
            cases=["one"], revision="r", dirty=False,
            receipt_ref=receipt_ref, receipt_authority=authority, **overrides)

    def _refused(self, code, **kwargs):
        with self.assertRaises(ReceiptError) as ctx:
            self._finalize(**kwargs)
        self.assertEqual(ctx.exception.reason_code, code, str(ctx.exception))
        now = self.store.get_run(self.item.id, self.run.id)
        self.assertEqual(now.state, RunState.FINALIZING,
                         "a refused finalize must not conclude the Run")
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0,
                "a refused finalize must not write evidence")
        return ctx.exception

    # -- no trusted authority / no receipt ---------------------------------

    def test_no_trusted_receipt_authority_blocks_succeeded(self):
        self._refused("BLOCKED_TRUST_SOURCE", authority=None)

    def test_a_caller_supplied_exit_code_zero_is_not_a_receipt(self):
        self._refused("RUNNER_RECEIPT_REQUIRED", authority=StubReceiptAuthority(),
                      receipt_ref="")

    def test_a_caller_supplied_json_hash_is_not_a_receipt(self):
        self._refused("RUNNER_RECEIPT_REQUIRED", authority=StubReceiptAuthority(),
                      receipt_ref={"receipt_sha256": SHA, "exit_code": 0})

    def test_child_text_pass_is_not_a_receipt(self):
        child = Path(self._td.name) / "child.txt"
        child.write_text("ALL TESTS PASSED\n", encoding="utf-8")
        authority = StubReceiptAuthority()  # accepts only its own trusted ref
        self._refused("UNTRUSTED_RECEIPT", authority=authority,
                      receipt_ref=str(child))

    def test_an_unverified_file_path_is_not_a_receipt(self):
        candidate = Path(self._td.name) / "receipt.json"
        candidate.write_text(json.dumps({"status": "EXITED",
                                         "process_exit_code": 0}), encoding="utf-8")
        authority = StubReceiptAuthority()
        self._refused("UNTRUSTED_RECEIPT", authority=authority,
                      receipt_ref=str(candidate))

    def test_a_verifier_that_raises_is_refused(self):
        authority = StubReceiptAuthority(error=RuntimeError("resolver unavailable"))
        self._refused("UNTRUSTED_RECEIPT", authority=authority)

    # -- exact binding ------------------------------------------------------

    def test_receipt_for_a_different_item_is_refused(self):
        authority = StubReceiptAuthority(receipt(item_ref="DEM-9999"))
        self._refused("RECEIPT_ITEM_MISMATCH", authority=authority)

    def test_receipt_for_a_different_run_is_refused(self):
        authority = StubReceiptAuthority(receipt(run_ref="RUN-9999"))
        self._refused("RECEIPT_RUN_MISMATCH", authority=authority)

    def test_receipt_exit_code_disagreement_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(process_exit_code=1, verdict="FAIL"))
        self._refused("EXIT_CODE_DISAGREEMENT", authority=authority)

    def test_receipt_verdict_disagreement_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(process_exit_code=0, verdict="FAIL"))
        self._refused("RECEIPT_VERDICT_DISAGREEMENT", authority=authority)

    def test_invalid_input_digest_is_refused(self):
        authority = StubReceiptAuthority(receipt(input_digest="not-a-digest"))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_invalid_receipt_sha_is_refused(self):
        authority = StubReceiptAuthority(receipt(receipt_sha256="short"))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_invalid_commit_sha_is_refused(self):
        authority = StubReceiptAuthority(receipt(commit_sha="deadbeef"))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_a_verified_receipt_without_an_actor_is_refused(self):
        authority = StubReceiptAuthority(receipt(actor_ref=""))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_pass_with_nonzero_exit_is_inconsistent(self):
        authority = StubReceiptAuthority(
            receipt(process_exit_code=1, verdict="PASS"))
        self._refused("RECEIPT_INCONSISTENT", authority=authority, exit_code=1)

    def test_a_receipt_without_an_action_key_is_refused(self):
        authority = StubReceiptAuthority(receipt(action_key=""))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_a_receipt_without_a_runner_identity_is_refused(self):
        authority = StubReceiptAuthority(receipt(runner_identity=""))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_a_receipt_with_a_non_integer_exit_code_is_refused(self):
        authority = StubReceiptAuthority(receipt(process_exit_code="0"))
        self._refused("INVALID_RECEIPT", authority=authority)

    def test_an_authority_that_returns_nothing_is_untrusted_receipt(self):
        class Nothing:
            def verify(self, receipt_ref, *, item_ref, run_ref, now):
                return None

        self._refused("UNTRUSTED_RECEIPT", authority=Nothing())

    # -- the positive path, with a test authority --------------------------

    def test_a_verified_matching_receipt_can_conclude_succeeded(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        result = self._finalize(authority=authority)
        self.assertEqual(result, {"state": "SUCCEEDED", "verdict": "PASS"})
        run = self.store.get_run(self.item.id, self.run.id)
        self.assertEqual(run.state, RunState.SUCCEEDED)
        locator = Path(run.artifact_dir) / "finalize.json"
        payload = json.loads(locator.read_text(encoding="utf-8"))
        self.assertEqual(payload["runner_receipt"]["input_digest"], DIGEST)
        self.assertEqual(payload["runner_receipt"]["receipt_sha256"], SHA)
        self.assertEqual(payload["runner_receipt"]["process_exit_code"], 0)
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT sha256 FROM evidence WHERE item_id=? AND run_id=?",
                (self.item.id, self.run.id)).fetchone()
        self.assertEqual(row["sha256"],
                         hashlib.sha256(locator.read_bytes()).hexdigest())

    def test_a_verified_failing_receipt_concludes_failed(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, process_exit_code=1, verdict="FAIL"))
        result = self._finalize(authority=authority, verdict="FAIL", exit_code=1)
        self.assertEqual(result, {"state": "FAILED", "verdict": "FAIL"})
        self.assertEqual(
            self.store.get_run(self.item.id, self.run.id).state, RunState.FAILED)

    def test_a_verified_receipt_is_idempotent(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        first = self._finalize(authority=authority)
        second = self._finalize(authority=authority)
        self.assertEqual(first, second)
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_no_verified_finalize_verb_is_exposed_on_the_cli(self):
        from workengine.cli import build_parser
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["run", "finalize-verified", ITEM, "RUN-0001"])


if __name__ == "__main__":
    unittest.main()
