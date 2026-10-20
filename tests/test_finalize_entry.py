"""The existing finalize route must be T10-safe, not only a parallel helper.

WHY THIS FILE EXISTS
--------------------
#14 added `finalize_run_verified`, but nothing calls it. The production-facing
route is still `workengine run finalize` -> `ServerOSStore.finalize_run`, and
that method concludes SUCCEEDED from the caller's `verdict`/`exit_code`. A Run
that V1/T10 requires can therefore be marked successful without a runner
receipt, which is the gap serveros #263 records as
`work_engine_consumption=NOT_WIRED`.

This file tests the entry route itself:

* legacy non-T10 finalization stays compatible but is labelled `UNSIGNED` and
  `t10_acceptable: False`;
* `t10_required=True` is an explicit opt-in that refuses
  `BLOCKED_TRUST_SOURCE` when no trusted parent is bound — there is no unsafe
  fallback to the legacy path;
* when a test authority IS supplied, the route still requires an exact
  `T10Binding` (Item / Run / commit / actor) and a verified receipt, and refuses
  child text, forged exit codes, forged hashes, cross-Run receipts, stale SHAs,
  and post-revocation attempts.

The authority used here is a TEST DOUBLE. A passing test does not mean a
production parent exists; production remains `BLOCKED_TRUST_SOURCE`.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from workengine import WorkItem
from workengine.authority import ReceiptError, T10Binding, VerifiedRunReceipt
from workengine.cli import main
from workengine.model import Activity, ContextMode, ExecutionContext, RunState
from workengine.store import ServerOSStore

import v1_import_isolation

# Captured at collection time, while the in-tree `workengine` is the one in
# `sys.modules`. The T10 suites replace it with a pinned archive during their
# session fixtures; restoring these exact objects is what keeps this module's
# `ReceiptError` and `ServerOSStore` the ones its route calls. See the helper.
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
OTHER_ACTOR = "human:other"
DIGEST = "a" * 64
SHA = "b" * 64
COMMIT = "c" * 40
STALE_COMMIT = "d" * 40
RECEIPT_REF = "trusted:receipt:1"
_UNSET = object()


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
        actor_ref=ACTOR,
    )
    values.update(overrides)
    return VerifiedRunReceipt(**values)


class StubReceiptAuthority:
    """A test authority. It is not a production trust source."""

    def __init__(self, result=None, *, accept=(RECEIPT_REF,), error=None):
        self.result = result
        self.accept = set(accept)
        self.error = error

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        if self.error is not None:
            raise self.error
        if receipt_ref not in self.accept:
            raise ValueError("not a receipt this authority issued")
        return self.result


class FinalizeEntryTest(unittest.TestCase):
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

    def _binding(self, **overrides) -> T10Binding:
        values = dict(item_ref=ITEM, run_ref=self.run.id,
                      commit_sha=COMMIT, actor_ref=ACTOR)
        values.update(overrides)
        return T10Binding(**values)

    def _t10(self, *, authority=None, binding=_UNSET, receipt_ref=RECEIPT_REF,
             verdict="PASS", exit_code=0):
        if binding is _UNSET:
            binding = self._binding()
        return self.store.finalize_run(
            self.item.id, self.run.id,
            verdict=verdict, exit_code=exit_code, command="pytest",
            cases=["one"], revision="r", dirty=False,
            t10_required=True, t10_binding=binding,
            receipt_ref=receipt_ref, receipt_authority=authority)

    def _refused(self, code, **kwargs):
        with self.assertRaises(ReceiptError) as ctx:
            self._t10(**kwargs)
        self.assertEqual(ctx.exception.reason_code, code, str(ctx.exception))
        self.assertEqual(
            self.store.get_run(self.item.id, self.run.id).state,
            RunState.FINALIZING, "a refused T10 finalize must not conclude the Run")
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0,
                "a refused T10 finalize must not write evidence")
        return ctx.exception

    # -- the actual CLI entry route ----------------------------------------

    def test_cli_legacy_finalize_is_labelled_unsigned(self):
        """Ordinary compatibility survives, but says what it is."""
        emitted = []
        with patch("workengine.cli.emit",
                   side_effect=lambda value, stream=None: emitted.append(value)), \
                redirect_stderr(StringIO()):
            code = main([
                "run", "finalize", self.item.id, self.run.id,
                "--verdict", "PASS", "--exit-code", "0",
                "--command", "pytest", "--revision", "r", "--case", "one",
                "--state-dir", str(Path(self._td.name) / "state"),
            ])
        self.assertEqual(code, 0)
        answer = emitted[0]
        self.assertEqual(answer["state"], "SUCCEEDED")
        self.assertEqual(answer["authorization"], "UNSIGNED")
        self.assertIs(answer["t10_acceptable"], False)
        run = self.store.get_run(self.item.id, self.run.id)
        payload = json.loads(
            (Path(run.artifact_dir) / "finalize.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["authorization"], "UNSIGNED",
                         "the durable evidence must say UNSIGNED too")

    def test_cli_t10_required_fails_closed_without_trusted_parent(self):
        """The production-facing route refuses; it does not fall back."""
        err = StringIO()
        with redirect_stdout(StringIO()), redirect_stderr(err):
            code = main([
                "run", "finalize", self.item.id, self.run.id,
                "--verdict", "PASS", "--exit-code", "0",
                "--command", "pytest", "--revision", "r", "--case", "one",
                "--t10-required", "--receipt", RECEIPT_REF,
                "--state-dir", str(Path(self._td.name) / "state"),
            ])
        self.assertEqual(code, 2)
        answer = json.loads(err.getvalue())
        self.assertEqual(answer["error"]["code"], "BLOCKED_TRUST_SOURCE")
        self.assertEqual(
            self.store.get_run(self.item.id, self.run.id).state,
            RunState.FINALIZING)
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

    # -- the T10-required store route, no trusted parent -------------------

    def test_store_t10_required_without_authority_is_blocked(self):
        self._refused("BLOCKED_TRUST_SOURCE", authority=None)

    def test_store_t10_required_without_binding_is_refused(self):
        self._refused("T10_BINDING_REQUIRED",
                      authority=StubReceiptAuthority(receipt(run_ref=self.run.id)),
                      binding=None)

    def test_store_t10_required_without_receipt_is_refused(self):
        self._refused("RUNNER_RECEIPT_REQUIRED",
                      authority=StubReceiptAuthority(receipt(run_ref=self.run.id)),
                      receipt_ref="")

    def test_store_t10_required_rejects_a_caller_json_hash(self):
        self._refused("RUNNER_RECEIPT_REQUIRED",
                      authority=StubReceiptAuthority(receipt(run_ref=self.run.id)),
                      receipt_ref={"receipt_sha256": SHA})

    def test_post_revocation_is_denied(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id),
            error=ReceiptError("AUTHORITY_REVOKED", "the grant was revoked"))
        self._refused("AUTHORITY_REVOKED", authority=authority)

    # -- child/caller self-claims ------------------------------------------

    def test_child_pass_with_process_exit_1_cannot_succeed(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, process_exit_code=1, verdict="PASS"))
        self._refused("RECEIPT_INCONSISTENT", authority=authority, exit_code=1)

    def test_caller_exit_zero_disagreeing_with_the_receipt_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, process_exit_code=1, verdict="FAIL"))
        self._refused("EXIT_CODE_DISAGREEMENT", authority=authority, exit_code=0)

    def test_forged_input_digest_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, input_digest="not-a-digest"))
        self._refused("INVALID_RECEIPT", authority=authority)

    # -- exact WorkRun / Item / commit / actor -----------------------------

    def test_cross_run_receipt_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(run_ref="RUN-9999"))
        self._refused("RECEIPT_RUN_MISMATCH", authority=authority)

    def test_cross_item_binding_is_refused(self):
        self._refused("T10_ITEM_MISMATCH",
                      authority=StubReceiptAuthority(receipt(run_ref=self.run.id)),
                      binding=self._binding(item_ref="DEM-9999"))

    def test_cross_run_binding_is_refused(self):
        self._refused("T10_RUN_MISMATCH",
                      authority=StubReceiptAuthority(receipt(run_ref=self.run.id)),
                      binding=self._binding(run_ref="RUN-9999"))

    def test_stale_commit_sha_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, commit_sha=STALE_COMMIT))
        self._refused("T10_COMMIT_MISMATCH", authority=authority,
                      binding=self._binding(commit_sha=COMMIT))

    def test_actor_mismatch_is_refused(self):
        authority = StubReceiptAuthority(
            receipt(run_ref=self.run.id, actor_ref=OTHER_ACTOR))
        self._refused("T10_ACTOR_MISMATCH", authority=authority,
                      binding=self._binding(actor_ref=ACTOR))

    # -- the one positive route, with a test authority ---------------------

    def test_exact_verified_exit_zero_can_succeed_only_with_trusted_authority(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        result = self._t10(authority=authority)
        self.assertEqual(result["state"], "SUCCEEDED")
        self.assertEqual(result["authorization"], "T10_VERIFIED")
        self.assertIs(result["t10_acceptable"], True)
        self.assertEqual(set(result), {"state", "verdict", "authorization",
                                       "t10_acceptable"},
                         "the route must not self-sign a QA verdict")

        run = self.store.get_run(self.item.id, self.run.id)
        payload = json.loads(
            (Path(run.artifact_dir) / "finalize.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["authorization"], "T10_VERIFIED")
        self.assertEqual(payload["runner_receipt"]["actor_ref"], ACTOR)
        self.assertEqual(payload["runner_receipt"]["commit_sha"], COMMIT)
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT sha256 FROM evidence WHERE item_id=? AND run_id=?",
                (self.item.id, self.run.id)).fetchone()
        self.assertEqual(
            row["sha256"],
            __import__("hashlib").sha256(
                (Path(run.artifact_dir) / "finalize.json").read_bytes()).hexdigest())

    def test_t10_required_on_an_already_unsigned_run_is_refused(self):
        """A legacy SUCCEEDED must not be relabelled as T10-verified."""
        legacy = self.store.finalize_run(
            self.item.id, self.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision="r", dirty=False)
        self.assertEqual(legacy["authorization"], "UNSIGNED")
        self.assertIs(legacy["t10_acceptable"], False)
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        with self.assertRaises(ReceiptError) as ctx:
            self._t10(authority=authority)
        self.assertEqual(ctx.exception.reason_code, "T10_UNSIGNED_RUN")

    def test_an_invalid_binding_commit_is_refused(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        self._refused("INVALID_T10_BINDING", authority=authority,
                      binding=self._binding(commit_sha="deadbeef"))

    def test_a_binding_without_an_actor_is_refused(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        self._refused("INVALID_T10_BINDING", authority=authority,
                      binding=self._binding(actor_ref=""))

    def test_a_second_t10_finalize_with_a_different_anchor_is_refused(self):
        """A terminal T10 Run cannot be re-anchored to another receipt."""
        first = self._t10(
            authority=StubReceiptAuthority(receipt(run_ref=self.run.id)))
        self.assertIs(first["t10_acceptable"], True)
        other = StubReceiptAuthority(
            receipt(run_ref=self.run.id, receipt_sha256="e" * 64))
        with self.assertRaises(ReceiptError) as ctx:
            self._t10(authority=other)
        self.assertEqual(ctx.exception.reason_code, "T10_BINDING_MISMATCH")
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)

    def test_a_second_t10_finalize_with_the_same_receipt_is_idempotent(self):
        authority = StubReceiptAuthority(receipt(run_ref=self.run.id))
        first = self._t10(authority=authority)
        second = self._t10(authority=authority)
        self.assertEqual(first, second)
        with self.store.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
