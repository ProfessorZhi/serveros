#!/usr/bin/env python3
"""Offline contract tests for the ChatGPT batch-commander Skill.

No Fleet call, model call, device, network or credential. The Skill and the
pure decision helpers are the objects under test.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "chatgpt-fleet-next"
SKILL = HERE / "skills" / "fleet-batch-commander" / "SKILL.md"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location(
    "fleet_batch_contract", HERE / "batch_contract.py")
contract = importlib.util.module_from_spec(spec)
sys.modules["fleet_batch_contract"] = contract
spec.loader.exec_module(contract)


def skill_text() -> str:
    return SKILL.read_text(encoding="utf-8")


def snapshot() -> dict:
    return {
        "devices": {
            "device:tokyo": {"roles": ["development-primary"],
                             "runtimes": ["claude", "codex"]},
            "device:dell": {"roles": ["gpu-compute-worker"],
                            "runtimes": ["claude"]},
            "device:win": {"roles": ["corporate-workstation"],
                           "runtimes": ["claude", "codex"]},
        },
        "abilities": {
            "device:tokyo": {"build": {"state": "READY", "effective": True,
                                       "granted": True, "stale": False}},
            "device:dell": {"gpu-test": {"state": "READY", "effective": True,
                                         "granted": True, "stale": False}},
            "device:win": {"compat": {"state": "READY", "effective": True,
                                      "granted": True, "stale": False}},
        },
        "models": {
            "device:tokyo": {"claude": ["sonnet", "opus"],
                             "codex": ["gpt-6.1-sol"]},
            "device:dell": {"claude": ["sonnet"]},
            "device:win": {"claude": ["sonnet"], "codex": ["gpt-6.1-sol"]},
        },
        "grants": {
            "device:tokyo": {"claude": ["send", "create"],
                             "codex": ["send", "create"]},
            "device:dell": {"claude": ["send"]},
            "device:win": {"claude": ["send"], "codex": ["send"]},
        },
        "create_allowed": {"device:tokyo": True, "device:dell": False,
                           "device:win": False},
        "sessions": [
            {"session_ref": "device:tokyo|claude|s1", "device_ref": "device:tokyo",
             "runtime": "claude", "project_id": "p1", "state": "MANAGED_IDLE"},
            {"session_ref": "device:dell|claude|s2", "device_ref": "device:dell",
             "runtime": "claude", "project_id": "p2", "state": "RESIDENT_HOST"},
            {"session_ref": "device:win|codex|s3", "device_ref": "device:win",
             "runtime": "codex", "project_id": "p3", "state": "MANAGED_IDLE"},
        ],
    }


def task(**overrides) -> dict:
    base = {
        "task_id": "T1", "device_ref": "device:tokyo", "runtime": "claude",
        "project_id": "p1", "session_ref": "device:tokyo|claude|s1",
        "worktree": "wt/t1", "scope": "implement one slice",
        "acceptance": "tests pass and artifact is read",
        "tests": "pytest tests/test_slice.py", "report": "artifacts/t1.json",
        "idempotency_key": "idem-t1", "required_roles": ["development-primary"],
        "required_ability": "build", "required_model": "sonnet",
        "required_grant": "send",
    }
    base.update(overrides)
    return base


class TheSkillIsAChatGPTPlaybook(unittest.TestCase):
    def test_frontmatter_and_trigger_are_legal(self) -> None:
        text = skill_text()
        self.assertTrue(text.startswith("---\n"))
        end = text.find("\n---", 4)
        self.assertGreater(end, 0)
        front = text[4:end]
        self.assertIn("name: fleet-batch-commander", front)
        self.assertRegex(front, r"(?m)^description: Use when ")

    def test_it_names_the_batch_contract_and_the_shared_skills(self) -> None:
        text = skill_text()
        for token in ("Batch ID", "persist", "checkpoint", "run_id",
                      "queued_message_uuid", "task_id", "worktree", "acceptance",
                      "tests", "report", "idempotency_key", "ACK", "UNKNOWN",
                      "observer", "OBSERVER_NOT_AVAILABLE", "MODEL_NOT_AVAILABLE",
                      "MISSELECTED_DEVICE", "STALE_CAPABILITY",
                      "FALSE_COMPLETION", "Session", "project"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for shared in ("fleet-device-and-session-discovery",
                       "fleet-safety-boundaries", "fleet-create-with-grants",
                       "fleet-reuse-idle-session",
                       "fleet-commander-orchestration",
                       "fleet-async-dispatch", "fleet-evidence-review"):
            with self.subTest(shared=shared):
                self.assertIn(shared, text)

    def test_it_does_not_turn_ack_into_success_or_wait(self) -> None:
        low = skill_text().lower()
        self.assertIn("ack is delivery", low)
        self.assertIn("do not poll", low)
        self.assertIn("not success", low)
        self.assertNotIn("wait until the run settles", low)
        self.assertNotIn("poll until", low.replace("do not poll until", ""))


class TheBatchPlanIsChecked(unittest.TestCase):
    def test_three_independent_tasks_pass(self) -> None:
        batch = {"batch_id": "BATCH-1", "tasks": [
            task(),
            task(task_id="T2", device_ref="device:dell", runtime="claude",
                 project_id="p2", session_ref="device:dell|claude|s2",
                 worktree="wt/t2", required_roles=["gpu-compute-worker"],
                 required_ability="gpu-test", required_model="sonnet",
                 required_grant="send", idempotency_key="idem-t2"),
            task(task_id="T3", device_ref="device:win", runtime="codex",
                 project_id="p3", session_ref="device:win|codex|s3",
                 worktree="wt/t3", required_roles=["corporate-workstation"],
                 required_ability="compat", required_model="gpt-6.1-sol",
                 required_grant="send", idempotency_key="idem-t3"),
        ]}
        result = contract.validate_batch(batch, snapshot())
        self.assertEqual(result["verdict"], "PASS", result["blocked"])

    def test_misselected_device_is_refused(self) -> None:
        batch = {"batch_id": "B", "tasks": [
            task(required_roles=["gpu-compute-worker"])]}
        result = contract.validate_batch(batch, snapshot())
        self.assertEqual(result["verdict"], "BLOCKED")
        self.assertIn("MISSELECTED_DEVICE",
                      {row["code"] for row in result["blocked"]})

    def test_stale_capability_is_refused(self) -> None:
        snap = snapshot()
        snap["abilities"]["device:tokyo"]["build"]["stale"] = True
        result = contract.validate_batch({"batch_id": "B", "tasks": [task()]}, snap)
        self.assertIn("STALE_CAPABILITY",
                      {row["code"] for row in result["blocked"]})

    def test_model_limit_is_refused_without_fallback(self) -> None:
        result = contract.validate_batch(
            {"batch_id": "B", "tasks": [task(required_model="haiku")]}, snapshot())
        self.assertIn("MODEL_NOT_AVAILABLE",
                      {row["code"] for row in result["blocked"]})

    def test_missing_grant_is_refused(self) -> None:
        result = contract.validate_batch(
            {"batch_id": "B", "tasks": [task(required_grant="control")]}, snapshot())
        self.assertIn("GRANT_DENIED",
                      {row["code"] for row in result["blocked"]})

    def test_busy_session_is_not_stolen(self) -> None:
        snap = snapshot()
        snap["sessions"][0]["state"] = "RUNNING_AGENT"
        result = contract.validate_batch({"batch_id": "B", "tasks": [task()]}, snap)
        self.assertIn("SESSION_BUSY",
                      {row["code"] for row in result["blocked"]})

    def test_two_tasks_cannot_share_a_worktree(self) -> None:
        result = contract.validate_batch(
            {"batch_id": "B", "tasks": [task(), task(task_id="T2",
                                                       worktree="wt/t1")]},
            snapshot())
        self.assertIn("WORKTREE_CONFLICT",
                      {row["code"] for row in result["blocked"]})

    def test_batch_id_is_required(self) -> None:
        result = contract.validate_batch({"tasks": [task()]}, snapshot())
        self.assertIn("BATCH_ID_MISSING",
                      {row["code"] for row in result["blocked"]})


class CompletionIsNotACK(unittest.TestCase):
    def test_ack_only_is_unknown(self) -> None:
        self.assertEqual(
            contract.completion_verdict({"run_id": None, "ack_only": True})["verdict"],
            "UNKNOWN")

    def test_completed_without_native_result_is_unknown(self) -> None:
        self.assertEqual(
            contract.completion_verdict({"run_id": "r1", "state": "completed"})["verdict"],
            "UNKNOWN")

    def test_completed_without_evidence_is_unknown(self) -> None:
        self.assertEqual(
            contract.completion_verdict(
                {"run_id": "r1", "state": "completed", "native_result": {"x": 1}}
            )["verdict"], "UNKNOWN")

    def test_run_not_found_is_unknown_not_success(self) -> None:
        self.assertEqual(
            contract.completion_verdict(
                {"run_id": "r1", "state": "RUN_NOT_FOUND"})["verdict"],
            "UNKNOWN")

    def test_running_is_not_failed_or_done(self) -> None:
        self.assertEqual(
            contract.completion_verdict(
                {"run_id": "r1", "state": "RUNNING"})["verdict"], "RUNNING")

    def test_failure_is_failed(self) -> None:
        self.assertEqual(
            contract.completion_verdict(
                {"run_id": "r1", "state": "FAILED"})["verdict"], "FAILED")

    def test_evidence_read_is_pass(self) -> None:
        self.assertEqual(
            contract.completion_verdict(
                {"run_id": "r1", "state": "completed", "native_result": {"x": 1},
                 "evidence_ref": "artifact://t1", "acceptance": True})["verdict"],
            "PASS")

    def test_one_unknown_keeps_batch_unknown(self) -> None:
        out = contract.batch_verdict([
            {"verdict": "PASS"}, {"verdict": "UNKNOWN"}])
        self.assertEqual(out["verdict"], "UNKNOWN")

    def test_one_failure_makes_batch_failed(self) -> None:
        out = contract.batch_verdict([
            {"verdict": "PASS"}, {"verdict": "FAILED"}])
        self.assertEqual(out["verdict"], "FAILED")

    def test_all_pass_is_green(self) -> None:
        out = contract.batch_verdict([{"verdict": "PASS"}, {"verdict": "PASS"}])
        self.assertEqual(out["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
