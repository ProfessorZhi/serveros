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
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
