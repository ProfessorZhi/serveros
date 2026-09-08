import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class ServerOSCliTest(unittest.TestCase):
    def run_cli(self, state, artifacts, *args):
        env = os.environ.copy()
        env["SERVEROS_STATE_DIR"] = state
        env["SERVEROS_ARTIFACTS_ROOT"] = artifacts
        return subprocess.run(
            [sys.executable, "-m", "workengine", "--json", *args],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_item_activity_and_run_json_contract(self):
        with tempfile.TemporaryDirectory() as td:
            artifacts = str(Path(td) / "artifacts")
            created = self.run_cli(
                td,
                artifacts,
                "item",
                "create",
                "--project",
                "serveros",
                "--prefix",
                "SRV",
                "--title",
                "converge",
                "--kind",
                "ops",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            item = json.loads(created.stdout)
            self.assertEqual(item["id"], "SRV-0001")
            self.assertEqual(item["state"], "INBOX")

            added = self.run_cli(td, artifacts, "activity", "add", item["id"], "verify", "--kind", "test")
            self.assertEqual(added.returncode, 0, added.stderr)
            begun = self.run_cli(
                td,
                artifacts,
                "run",
                "begin",
                item["id"],
                "verify",
                "--worker",
                "codex",
                "--idempotency-key",
                "cli-test",
            )
            self.assertEqual(begun.returncode, 0, begun.stderr)
            run = json.loads(begun.stdout)
            self.assertEqual(run["run"], "RUN-0001")
            self.assertEqual(run["project"], "serveros")
            self.assertEqual(run["state"], "ALLOCATED")
            self.assertTrue(Path(run["artifact_dir"]).is_absolute())

    def test_relative_state_override_is_rejected(self):
        env = os.environ.copy()
        env["SERVEROS_STATE_DIR"] = "relative/state"
        result = subprocess.run(
            [sys.executable, "-m", "workengine", "--json", "item", "list"],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "INVALID_INPUT")

    def test_outcome_deliverable_and_board_projection(self):
        with tempfile.TemporaryDirectory() as td:
            artifacts = str(Path(td) / "artifacts")
            created = self.run_cli(
                td, artifacts, "item", "create", "--project", "serveros", "--prefix", "SRV",
                "--title", "converge", "--kind", "ops", "--card-locator", "/records/SRV-0001/card.yaml",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            outcome = self.run_cli(td, artifacts, "item", "outcome", "SRV-0001", "--set", "PARTIAL")
            self.assertEqual(json.loads(outcome.stdout)["outcome"], "PARTIAL")
            added = self.run_cli(
                td, artifacts, "deliverable", "add", "SRV-0001", "--kind", "report",
                "--locator", "/artifacts/SRV-0001/report.md",
            )
            self.assertEqual(json.loads(added.stdout)["kind"], "report")
            board_dir = Path(td) / "board"
            rendered = self.run_cli(
                td, artifacts, "board", "render", "--project", "serveros", "--output-dir", str(board_dir),
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            self.assertEqual(json.loads(rendered.stdout)["items"], 1)
            board = json.loads((board_dir / "current.json").read_text(encoding="utf-8"))
            self.assertEqual(board["items"][0]["deliverables"][0]["kind"], "report")
            self.assertIn("SRV-0001", (board_dir / "current.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
