#!/usr/bin/env python3
"""Model evidence, project grant, and batch-safety contract tests.

Offline only: no Fleet call, provider call, model turn, device, network or
credential. The evidence fixture is an *observation-shaped test record*; it is
not a claim that CloudCLI 1.41 enumerates any model.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "chatgpt-fleet-next"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("fleet_model_contract", HERE / "batch_contract.py")
contract = importlib.util.module_from_spec(spec)
sys.modules["fleet_model_contract"] = contract
spec.loader.exec_module(contract)

NOW = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone.utc)
SHA = "a" * 64


def evidence(device="device:a", runtime="claude", *,
             declared=(), schema=True, release="1.41.0",
             captured_at="2026-10-28T11:00:00Z", sha=SHA):
    return {
        "device_ref": device,
        "runtime": runtime,
        "release": release,
        "captured_at": captured_at,
        "schema_types": {"model": "string"} if schema else {},
        "declared": {"model": list(declared)},
        "capture_sha256": sha,
    }


def task(model=None, *, project="p1", device="device:a", runtime="claude",
         session="s:a", require_explicit=False):
    value = {
        "task_id": "T1", "device_ref": device, "runtime": runtime,
        "project_id": project, "worktree": "wt/t1",
        "scope": "slice", "acceptance": "tests pass",
        "tests": "pytest", "report": "r.json", "idempotency_key": "i1",
        "required_grant": "send",
    }
    if session is not None:
        value["session_ref"] = session
    if model is not None:
        value["required_model"] = model
    if require_explicit:
        value["require_explicit_model"] = True
    return value


def snapshot(*, evidence_rows=None, create_models=(), sessions=None):
    return {
        "devices": {"device:a": {"roles": ["dev"], "runtimes": ["claude", "codex"]},
                    "device:b": {"roles": ["dev"], "runtimes": ["claude"]}},
        "grants": {"device:a": {"claude": ["send"], "codex": ["send"]},
                   "device:b": {"claude": ["send"]}},
        "create_allowed": {"device:a": True, "device:b": False},
        "create_allow": {"p1": {"models": list(create_models)}},
        "sessions": sessions if sessions is not None else [
            {"session_ref": "s:a", "device_ref": "device:a", "runtime": "claude",
             "project_id": "p1", "state": "MANAGED_IDLE"},
        ],
        "model_evidence": evidence_rows or [],
    }


def verdict(batch, snap):
    return contract.validate_batch(batch, snap, now=NOW)


class ModelEvidenceIsNotACatalogue(unittest.TestCase):
    def test_no_model_uses_provider_default_and_unknown_identity(self) -> None:
        out = verdict({"batch_id": "B", "tasks": [task()]}, snapshot())
        self.assertEqual(out["verdict"], "PASS", out["blocked"])
        decision = out["model_decisions"]["T1"]
        self.assertEqual(decision["decision"], "PROVIDER_DEFAULT")
        self.assertIsNone(decision["model"])
        self.assertEqual(decision["model_identity"], "UNKNOWN")
        self.assertEqual(out["provider_calls"], 0)

    def test_missing_catalog_is_evidence_missing_not_unavailable(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"], evidence_rows=[]))
        self.assertIn("MODEL_EVIDENCE_MISSING",
                      {row["code"] for row in out["blocked"]})
        self.assertEqual(out["model_decisions"]["T1"]["model_identity"], "UNKNOWN")

    def test_schema_only_is_unverified_not_unavailable(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=(), schema=True)]))
        self.assertIn("MODEL_UNVERIFIED",
                      {row["code"] for row in out["blocked"]})

    def test_empty_evidence_without_schema_is_unverified(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=(), schema=False)]))
        self.assertIn("MODEL_UNVERIFIED",
                      {row["code"] for row in out["blocked"]})

    def test_declared_evidence_and_grant_allow_explicit_model(self) -> None:
        # Synthetic unit fixture for the explicit branch. CloudCLI 1.41 does
        # not enumerate models; this row is not a claim that it does.
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=("sonnet",))]))
        self.assertEqual(out["verdict"], "PASS", out["blocked"])
        decision = out["model_decisions"]["T1"]
        self.assertEqual(decision["decision"], "EXPLICIT")
        self.assertEqual(decision["model"], "sonnet")
        self.assertEqual(decision["model_identity"], "EXPLICIT")
        self.assertEqual(decision["support"], "PROVIDER_ADVERTISED")

    def test_project_grant_denies_before_evidence(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["opus"],
                     evidence_rows=[evidence(declared=("sonnet",))]))
        self.assertIn("MODEL_POLICY_DENIED",
                      {row["code"] for row in out["blocked"]})

    def test_declared_list_without_the_id_is_not_available(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=("opus",))]))
        self.assertIn("MODEL_NOT_AVAILABLE",
                      {row["code"] for row in out["blocked"]})

    def test_cross_device_model_is_a_targeting_error(self) -> None:
        rows = [evidence(device="device:b", declared=("sonnet",))]
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"], evidence_rows=rows))
        self.assertIn("MODEL_DEVICE_MISMATCH",
                      {row["code"] for row in out["blocked"]})

    def test_cross_runtime_model_is_a_targeting_error(self) -> None:
        rows = [evidence(runtime="codex", declared=("sonnet",))]
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"], evidence_rows=rows))
        self.assertIn("MODEL_RUNTIME_MISMATCH",
                      {row["code"] for row in out["blocked"]})

    def test_stale_release_pin_is_not_evidence(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=("sonnet",),
                                             release="1.40.0")]))
        self.assertIn("MODEL_EVIDENCE_STALE",
                      {row["code"] for row in out["blocked"]})

    def test_old_capture_is_not_evidence(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task("sonnet")]},
            snapshot(create_models=["sonnet"],
                     evidence_rows=[evidence(declared=("sonnet",),
                                             captured_at="2026-10-20T00:00:00Z")]))
        self.assertIn("MODEL_EVIDENCE_STALE",
                      {row["code"] for row in out["blocked"]})

    def test_malformed_evidence_is_refused(self) -> None:
        cases = [
            evidence(sha="not-a-digest"),
            evidence(captured_at="2026-10-28T11:00:00"),
            {**evidence(declared=("sonnet",)), "declared": {"model": "sonnet"}},
            {**evidence(declared=("sonnet",)), "schema_types": []},
        ]
        for row in cases:
            with self.subTest(row=row):
                out = verdict(
                    {"batch_id": "B", "tasks": [task("sonnet")]},
                    snapshot(create_models=["sonnet"], evidence_rows=[row]))
                self.assertIn("MODEL_EVIDENCE_MALFORMED",
                              {row["code"] for row in out["blocked"]})

    def test_require_explicit_without_model_fails_closed(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task(require_explicit=True)]},
            snapshot())
        self.assertIn("MODEL_NOT_NAMED",
                      {row["code"] for row in out["blocked"]})

    def test_malformed_project_grant_is_not_a_grant(self) -> None:
        snap = snapshot(create_models=["sonnet"])
        snap["create_allow"]["p1"]["models"] = "sonnet"
        out = verdict({"batch_id": "B", "tasks": [task("sonnet")]}, snap)
        self.assertIn("MODEL_POLICY_MALFORMED",
                      {row["code"] for row in out["blocked"]})


class BatchAndSessionSafety(unittest.TestCase):
    def test_no_session_and_no_create_is_refused(self) -> None:
        snap = snapshot(sessions=[])
        snap["create_allowed"]["device:a"] = False
        out = verdict({"batch_id": "B", "tasks": [task(session=None)]}, snap)
        self.assertIn("SESSION_REQUIRED",
                      {row["code"] for row in out["blocked"]})

    def test_busy_session_is_not_stolen(self) -> None:
        rows = [{"session_ref": "s:a", "device_ref": "device:a",
                 "runtime": "claude", "project_id": "p1",
                 "state": "RUNNING_AGENT"}]
        out = verdict({"batch_id": "B", "tasks": [task()]},
                      snapshot(sessions=rows))
        self.assertIn("SESSION_BUSY",
                      {row["code"] for row in out["blocked"]})

    def test_cross_project_session_is_refused(self) -> None:
        rows = [{"session_ref": "s:a", "device_ref": "device:a",
                 "runtime": "claude", "project_id": "other",
                 "state": "MANAGED_IDLE"}]
        out = verdict({"batch_id": "B", "tasks": [task()]},
                      snapshot(sessions=rows))
        self.assertIn("SESSION_PROJECT_MISMATCH",
                      {row["code"] for row in out["blocked"]})

    def test_device_verb_grant_is_still_checked(self) -> None:
        out = verdict(
            {"batch_id": "B", "tasks": [task() | {"required_grant": "control"}]},
            snapshot())
        self.assertIn("GRANT_DENIED",
                      {row["code"] for row in out["blocked"]})

    def test_batch_id_is_required(self) -> None:
        out = contract.validate_batch({"tasks": [task()]}, snapshot(), now=NOW)
        self.assertIn("BATCH_ID_MISSING",
                      {row["code"] for row in out["blocked"]})

    def test_ack_only_and_false_completion_stay_unknown(self) -> None:
        self.assertEqual(contract.completion_verdict(
            {"run_id": None, "ack_only": True})["verdict"], "UNKNOWN")
        self.assertEqual(contract.completion_verdict(
            {"run_id": "r1", "state": "completed"})["verdict"], "UNKNOWN")
        self.assertEqual(contract.completion_verdict(
            {"run_id": "r1", "state": "RUN_NOT_FOUND"})["verdict"], "UNKNOWN")


class The472SessionFixtureIsConsumed(unittest.TestCase):
    """The real session rows from #472, not a permissive stand-in.

    Values copied from tests/test_fleet_v1_milestone_batch_registration.py at
    #472 head 0c588896a7b4b6b122a593c83683342429020cd6.
    """

    DEV_A = "device:tokyo-shared-dev-01"
    DEV_B = "device:dell-5820"
    DEV_C = "device:win-fjd"
    SESSION_A = "session:tokyo-alpha"
    SESSION_B = "session:dell-beta"
    SESSION_C = "session:fjd-gamma"
    PROJECT = "serveros"

    def live_snapshot(self):
        return {
            "devices": {self.DEV_A: {"roles": ["dev"], "runtimes": ["claude"]},
                        self.DEV_B: {"roles": ["dev"], "runtimes": ["codex"]},
                        self.DEV_C: {"roles": ["dev"], "runtimes": ["claude"]}},
            "create_allowed": {self.DEV_A: True, self.DEV_B: True,
                               self.DEV_C: True},
            "grants": {self.DEV_A: {"claude": ["send"]},
                       self.DEV_B: {"codex": ["send"]},
                       self.DEV_C: {"claude": ["send"]}},
            "sessions": [
                {"session_ref": self.SESSION_A, "device_ref": self.DEV_A,
                 "runtime": "claude", "project_id": self.PROJECT,
                 "state": "MANAGED_IDLE"},
                {"session_ref": self.SESSION_B, "device_ref": self.DEV_B,
                 "runtime": "codex", "project_id": self.PROJECT,
                 "state": "MANAGED_IDLE"},
                {"session_ref": self.SESSION_C, "device_ref": self.DEV_C,
                 "runtime": "claude", "project_id": self.PROJECT,
                 "state": "MANAGED_IDLE"},
            ],
            "model_evidence": [],
        }

    def tasks(self):
        rows = []
        for index, (device, runtime, session) in enumerate((
                (self.DEV_A, "claude", self.SESSION_A),
                (self.DEV_B, "codex", self.SESSION_B),
                (self.DEV_C, "claude", self.SESSION_C)), start=1):
            rows.append({
                "task_id": f"T{index}", "device_ref": device,
                "runtime": runtime, "project_id": self.PROJECT,
                "session_ref": session, "worktree": f"wt/{index}",
                "scope": "slice", "acceptance": "a",
                "tests": "pytest", "report": "r", "idempotency_key": f"i{index}",
                "required_grant": "send",
            })
        return {"batch_id": "batch-472", "tasks": rows}

    def test_the_live_session_shape_is_accepted_without_a_model_catalogue(self) -> None:
        out = contract.validate_batch(self.tasks(), self.live_snapshot(), now=NOW)
        self.assertEqual(out["verdict"], "PASS", out["blocked"])
        self.assertEqual(out["provider_calls"], 0)
        for task_id in ("T1", "T2", "T3"):
            self.assertEqual(out["model_decisions"][task_id]["decision"],
                             "PROVIDER_DEFAULT")

    def test_a_busy_session_in_the_same_fixture_is_still_refused(self) -> None:
        snap = self.live_snapshot()
        snap["sessions"][1]["state"] = "RUNNING_AGENT"
        out = contract.validate_batch(self.tasks(), snap, now=NOW)
        self.assertIn("SESSION_BUSY",
                      {row["code"] for row in out["blocked"]})


if __name__ == "__main__":
    unittest.main()
