"""The four paths a daily cycle must survive, run against real processes.

WHY THIS FILE EXISTS
--------------------
`runtime/bounded_invocation.invoke_once` is the fabric's only bounded executor:
it writes a durable `STARTED` receipt before it spawns, kills the whole process
group on timeout, and refuses to reuse an action key with different input. Every
one of those properties is a sentence in a docstring, and the module had **no
consumer anywhere in the fabric** — its only importer was
`tests/test_daily_cycle.py`, which imported it and never called it.

`docs/daily-work-cycle.md` §12 is the requirement this file answers:

    必须验证完整一路：真实增量 → 认识提交/游标 → 真实 board/current grants → 原子选择/
    资源准入 → 设计/计划审阅 → 自有工作树实施 → 真实验收 → 对账和发布回执。
    失效/重启/双触发与取消反例也要覆盖。

The full chain cannot be verified on this node today — there is no grant
authority, no model transport and no unattended executor, and the board a
person reads is twelve days old (`tests/test_focus_source.py` reports that with
the capture). What CAN be verified is the layer the whole chain stands on, and
it is verified here by running it rather than by reading it: four real
subprocesses, four real receipts on disk, and the two refusal paths.

The four paths, named as the round names them:

  1. one success path — a real exit 0, and a receipt that refuses to call it a
     verified effect;
  2. one product-test-failure path — a real non-zero exit, and the review gate
     refusing a receipt that carries it;
  3. one timeout / unknown-effect path — a real kill, and `effect: UNKNOWN`
     with no automatic retry;
  4. one duplicate-trigger / idempotency path — the same key run twice with the
     side effect counted, and the same key with different input refused.

A note on what is NOT asserted, because asserting it would be the thing this
round keeps finding: `effect: UNVERIFIED` on a clean exit is not a claim that
nothing happened. It is the statement that this layer does not know, and the
assertion below is that the receipt says so.

THE FIFTH THING THIS FILE RUNS, ADDED 2026-09-26
------------------------------------------------
The receipt lock. It is held from before `Popen` until after the group is
reaped, so it is held across the entire life of the command it guards, and
until today it was taken with a BLOCKING `flock` — which meant that a second
caller under the same action key waited for that command to finish (up to its
full wall budget) to be told what the receipt's own digest check would have
answered immediately. J07's fifth criterion names this directly — 一项等待不冻
其他合法 scope — and `work dispatch` is what made the path reachable in
production. The tests at the end of this file assert the property that
replaced it: contention is refused, promptly and by name, and a scope with its
own key is not held up by one that is working.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from runtime.bounded_invocation import invoke_once


class BoundedInvocationPaths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.cwd = self.root / "cwd"
        self.receipts = self.root / "receipts"
        self.cwd.mkdir()
        # 0700, because `invoke_once` refuses anything else: a receipt another
        # local user can read is a receipt another local user can forge.
        self.receipts.mkdir(mode=0o700)

    def tearDown(self):
        self._tmp.cleanup()

    def receipt(self, key: str) -> dict:
        return json.loads((self.receipts / f"{key}.json").read_text())

    # -- path 1: success ----------------------------------------------------
    def test_a_real_success_is_recorded_as_unverified_effect(self):
        result = invoke_once(key="success", argv=["/bin/sh", "-c", "echo hello"],
                             cwd=self.cwd, receipts=self.receipts, timeout=30)
        self.assertEqual(result["status"], "EXITED")
        self.assertEqual(result["process_exit_code"], 0)
        self.assertEqual(result["effect"], "UNVERIFIED",
                         "the direct process exiting 0 is not a business result")
        self.assertTrue(result["direct_process_exited"])
        self.assertFalse(result["descendants_verified"],
                         "no descendant was verified, and the receipt says so")
        self.assertFalse(result["replayed"])
        self.assertEqual((self.receipts / "success.stdout").read_text().strip(), "hello")
        on_disk = self.receipt("success")
        self.assertEqual(on_disk["input_digest"], result["input_digest"])
        self.assertEqual(on_disk["action_key"], "success")

    def test_the_started_receipt_is_written_before_the_command_can_be_seen(self):
        """The property a crash window depends on: the receipt exists and says
        `STARTED` while the process is still running. Read here from inside the
        command's own execution, which is the only vantage point that can tell
        "written first" from "written afterwards"."""
        marker = self.cwd / "started.json"
        invoke_once(key="crash-window",
                    argv=["/bin/sh", "-c", f"cat {self.receipts}/crash-window.json > "
                                           f"{marker}"],
                    cwd=self.cwd, receipts=self.receipts, timeout=30)
        seen = json.loads(marker.read_text())
        self.assertEqual(seen["status"], "STARTED")
        self.assertIsNone(seen["process_exit_code"])
        self.assertEqual(seen["effect"], "UNKNOWN")
        self.assertEqual(self.receipt("crash-window")["status"], "EXITED")

    # -- path 2: a product test that fails ---------------------------------
    def test_a_failing_test_is_a_result_the_gate_then_refuses(self):
        """`invoke_once` records the exit code; it does not decide what the
        failure means. `review_gate` is where a non-zero exit stops a review
        from being counted, and the two are joined here rather than described."""
        from intake.daily_cycle import review_gate
        result = invoke_once(key="failing-test",
                             argv=["/bin/sh", "-c", "echo 'assert failed' >&2; exit 3"],
                             cwd=self.cwd, receipts=self.receipts, timeout=30)
        self.assertEqual(result["status"], "EXITED")
        self.assertEqual(result["process_exit_code"], 3)
        self.assertEqual((self.receipts / "failing-test.stderr").read_text().strip(),
                         "assert failed")

        # A review adapter that hands the gate exactly what this run produced.
        packet = {"id": "T-1", "project": "test", "action": "research",
                  "scope_digest": "s", "source_digest": "s", "design_digest": "d",
                  "plan_digest": "p", "baseline_digest": "b",
                  "author_session": "session-author", "review_ttl_seconds": 900,
                  "allowed_review_models": ["gpt-6-astra"], "binding_ready": True}
        grant = {"ref": "g", "revoked": False, "valid_from": "2026-09-21T00:00:00Z",
                 "expires_at": "2026-09-23T00:00:00Z", "projects": ["test"],
                 "items": ["T-1"], "actions": ["research"],
                 "scope_digests": {"T-1": "s"},
                 "budget": {"wall_seconds": 60, "max_attempts": 1,
                            "max_output_bytes": 1000, "exhausted": False}}
        review = {"adapter_verified": True,
                  "process_exit_code": result["process_exit_code"],
                  "verdict": "APPROVE", "reviewer_session": "session-reviewer",
                  "model": "gpt-6-astra", "reasoning_effort": "high",
                  "observed_at": "2026-09-21T19:40:00Z",
                  "id": "T-1", "scope_digest": "s", "source_digest": "s",
                  "design_digest": "d", "plan_digest": "p", "baseline_digest": "b"}
        from datetime import datetime, timezone
        decision = review_gate(packet, review, grant,
                               now=datetime(2026, 9, 21, 19, 40, tzinfo=timezone.utc))
        self.assertEqual(decision["decision"], "BLOCKED")
        self.assertEqual(decision["reason"], "REVIEW_RECEIPT_UNVERIFIED")

        review["process_exit_code"] = 0
        passed = review_gate(packet, review, grant,
                             now=datetime(2026, 9, 21, 19, 40, tzinfo=timezone.utc))
        self.assertEqual(passed["decision"], "ELIGIBLE_FOR_ADMISSION")
        self.assertFalse(passed["execution_authorized"],
                         "a passing review is not an execution authorization")

    # -- path 3: timeout, effect unknown -----------------------------------
    def test_a_timeout_kills_the_group_and_leaves_the_effect_unknown(self):
        late = self.cwd / "finished-after-the-timeout"
        started = time.monotonic()
        result = invoke_once(key="timeout",
                             argv=["/bin/sh", "-c", f"sleep 3; touch {late}"],
                             cwd=self.cwd, receipts=self.receipts, timeout=1)
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "TIMED_OUT")
        self.assertEqual(result["effect"], "UNKNOWN")
        self.assertEqual(result["reason"],
                         "requires effect/worker reconciliation; no automatic retry")
        self.assertLess(elapsed, 3.0, "the call returned only after the command's own "
                                      "sleep, so the timeout did not fire")
        time.sleep(3)
        self.assertFalse(late.exists(),
                         "the killed process group went on to touch the file, so the "
                         "kill reached the direct child only")

    def test_a_timed_out_key_is_not_retried_by_being_called_again(self):
        """The rule a retry would break: `STARTED` was written, so a second call
        must reconcile rather than re-run. The command counts its own starts."""
        counter = self.cwd / "starts"
        argv = ["/bin/sh", "-c", f"echo x >> {counter}; sleep 3"]
        first = invoke_once(key="no-blind-retry", argv=argv, cwd=self.cwd,
                            receipts=self.receipts, timeout=1)
        self.assertEqual(first["status"], "TIMED_OUT")
        second = invoke_once(key="no-blind-retry", argv=argv, cwd=self.cwd,
                             receipts=self.receipts, timeout=1)
        self.assertTrue(second["replayed"])
        self.assertFalse(second["executed_now"])
        self.assertEqual(second["status"], "TIMED_OUT",
                         "the replay reports the first attempt, not a new one")
        time.sleep(0.2)
        self.assertEqual(counter.read_text().count("x"), 1,
                         "the command ran a second time")

    # -- path 4: duplicate trigger, idempotency ----------------------------
    def test_the_same_key_and_input_runs_the_command_exactly_once(self):
        counter = self.cwd / "runs"
        argv = ["/bin/sh", "-c", f"echo x >> {counter}"]
        call = dict(key="twice", argv=argv, cwd=self.cwd, receipts=self.receipts,
                    timeout=30)
        first = invoke_once(**call)
        second = invoke_once(**call)
        third = invoke_once(**call)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"] and third["replayed"])
        self.assertEqual(counter.read_text().count("x"), 1)
        self.assertEqual(second["input_digest"], first["input_digest"])

    def test_the_same_key_with_different_input_is_refused(self):
        invoke_once(key="changed", argv=["/bin/sh", "-c", "true"], cwd=self.cwd,
                    receipts=self.receipts, timeout=30)
        with self.assertRaises(ValueError) as caught:
            invoke_once(key="changed", argv=["/bin/sh", "-c", "false"], cwd=self.cwd,
                        receipts=self.receipts, timeout=30)
        self.assertIn("same action key with different input", str(caught.exception))

    def test_changed_input_includes_a_changed_prompt(self):
        """The same argv with a different prompt is different input, which is the
        case a review adapter actually produces."""
        argv = ["/bin/sh", "-c", "cat"]
        invoke_once(key="prompt", argv=argv, cwd=self.cwd, receipts=self.receipts,
                    input_text="first prompt", timeout=30)
        with self.assertRaises(ValueError):
            invoke_once(key="prompt", argv=argv, cwd=self.cwd, receipts=self.receipts,
                        input_text="second prompt", timeout=30)

    # -- the receipt lock: who waits, and who is told -----------------------
    def await_receipt(self, key: str, timeout: float = 30.0) -> None:
        """Wait until a dispatch is inside its lock.

        `STARTED` is written INSIDE the lock and immediately before `Popen`, so
        this file existing is proof that the lock is held. That is what keeps
        these tests deterministic: nothing here asserts that a sleep was long
        enough, and the gate below cannot be released by any path except the
        test's own `finally`.
        """
        path = self.receipts / f"{key}.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return
            time.sleep(0.01)
        self.fail(f"{path.name} never appeared, so the holder never got its lock")

    @staticmethod
    def gated(marker: Path, then: str = "") -> list[str]:
        """A command that cannot finish until the marker file exists."""
        script = f"while [ ! -f {marker} ]; do sleep 0.05; done"
        return ["/bin/sh", "-c", f"{script}; {then}" if then else script]

    def test_a_held_receipt_lock_is_refused_rather_than_waited_for(self):
        """J07's fifth criterion, at the layer that had the only mechanism for it.

        The wait this refuses is not a wait for a file: that lock is held for
        the whole life of the command it guards, so a caller that blocked on it
        would be waiting for another dispatch — here, until a marker this test
        creates only AFTER it has seen the refusal. `holder.is_alive()` is the
        assertion a wait could not satisfy, and it is a fact rather than a
        threshold: the holder is gated on a file that does not yet exist.
        """
        marker = self.cwd / "release-one"
        call = dict(key="held", argv=self.gated(marker), cwd=self.cwd,
                    receipts=self.receipts, timeout=30)
        holder = threading.Thread(target=invoke_once, kwargs=call)
        holder.start()
        try:
            self.await_receipt("held")
            with self.assertRaises(ValueError) as caught:
                invoke_once(**call)
            # The lock is named, so this cannot be a different refusal wearing
            # the same exception type — and the input is IDENTICAL, so a lock
            # that was free would have replayed rather than raised.
            self.assertIn("held.lock", str(caught.exception))
            self.assertIn("Nothing was run", str(caught.exception))
            self.assertTrue(holder.is_alive(),
                            "the holder had already finished, so the refusal "
                            "above proved nothing about waiting")
        finally:
            marker.write_text("go\n")
            holder.join(timeout=30)
            self.assertFalse(holder.is_alive())

    def test_a_second_action_key_completes_while_the_first_is_still_running(self):
        """The lock is per action key, which is what bounds the refusal.

        Without this, replacing the blocking lock with a refused one would be
        an improvement nobody could tell from a lock taken on the whole receipt
        directory — the same refusal, hiding a worse defect.
        """
        marker = self.cwd / "release-two"
        holder = threading.Thread(target=invoke_once, kwargs=dict(
            key="scope-a", argv=self.gated(marker), cwd=self.cwd,
            receipts=self.receipts, timeout=30))
        holder.start()
        try:
            self.await_receipt("scope-a")
            other = invoke_once(key="scope-b", argv=["/bin/sh", "-c", "echo b"],
                                cwd=self.cwd, receipts=self.receipts, timeout=30)
            self.assertEqual(other["status"], "EXITED")
            self.assertEqual((self.receipts / "scope-b.stdout").read_text(), "b\n")
            self.assertTrue(holder.is_alive(),
                            "the other scope finished first, so nothing here "
                            "was shown to run beside it")
        finally:
            marker.write_text("go\n")
            holder.join(timeout=30)
            self.assertFalse(holder.is_alive())

    def test_the_retry_the_refusal_asks_for_replays_instead_of_running_again(self):
        """The refusal's instruction has to be true, or it is worse than a wait.

        An operator is told to retry once that dispatch finishes. What makes
        that safe is `input_digest`, and a command that counts its own runs is
        the only way to assert it: a retry that executed again would be the
        double dispatch the whole receipt exists to prevent.
        """
        marker = self.cwd / "release-three"
        counter = self.cwd / "runs-after-contention"
        call = dict(key="contended",
                    argv=self.gated(marker, then=f"echo x >> {counter}"),
                    cwd=self.cwd, receipts=self.receipts, timeout=30)
        holder = threading.Thread(target=invoke_once, kwargs=call)
        holder.start()
        try:
            self.await_receipt("contended")
            with self.assertRaises(ValueError) as caught:
                invoke_once(**call)
            self.assertIn("contended.lock", str(caught.exception))
        finally:
            marker.write_text("go\n")
            holder.join(timeout=30)
            self.assertFalse(holder.is_alive())

        retried = invoke_once(**call)
        self.assertTrue(retried["replayed"])
        self.assertFalse(retried["executed_now"])
        self.assertEqual(counter.read_text().count("x"), 1,
                         "the retry executed the command instead of replaying")

    def test_a_lock_that_cannot_be_opened_is_not_reported_as_contention(self):
        """A fault on the lock file is not somebody holding it.

        The refusal above tells an operator that another dispatch is running and
        that retrying once it finishes is the move. A fault on the lock file
        itself must not be dressed as that, or the retries never end. Asserted
        with a lock path that is a directory, so `os.open` fails with `EISDIR`,
        and against the exception's own TYPE rather than a message: what is
        being pinned is that the fault arrived unchanged.

        What this does NOT reach, measured rather than assumed: a non-contention
        error from `flock` itself. `_exclusive_nowait` re-raises those too, and
        no test can produce one — the errno it guards against is what the
        kernel reports when its lock table is exhausted. So that line stays
        (folding every errno into "somebody holds it" would be a lie) and is
        recorded as unpinned instead of being implied to be covered.
        """
        (self.receipts / "held.lock").mkdir()
        with self.assertRaises(OSError) as caught:
            invoke_once(key="held", argv=["/bin/sh", "-c", "true"], cwd=self.cwd,
                        receipts=self.receipts, timeout=30)
        self.assertIsInstance(caught.exception, IsADirectoryError,
                              "a lock that could not be opened was reported as "
                              "one another writer holds")

    # -- the guards around the four paths ----------------------------------
    def test_a_receipt_directory_another_user_can_read_is_refused(self):
        open_dir = self.root / "open"
        open_dir.mkdir(mode=0o755)
        os.chmod(open_dir, 0o755)
        with self.assertRaises(ValueError) as caught:
            invoke_once(key="open-dir", argv=["/bin/sh", "-c", "true"], cwd=self.cwd,
                        receipts=open_dir, timeout=30)
        self.assertIn("0700", str(caught.exception))

    def test_an_unsafe_action_key_is_refused(self):
        for key in ("", "../escape", "with space", "a" * 101, "semi;colon"):
            with self.assertRaises(ValueError):
                invoke_once(key=key, argv=["/bin/sh", "-c", "true"], cwd=self.cwd,
                            receipts=self.receipts, timeout=30)

    def test_relative_roots_are_refused(self):
        with self.assertRaises(ValueError):
            invoke_once(key="relative", argv=["/bin/sh", "-c", "true"],
                        cwd=Path("runtime"), receipts=self.receipts, timeout=30)


if __name__ == "__main__":
    unittest.main()
