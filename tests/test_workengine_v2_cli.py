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

    def test_context_attach_retry_and_change_updates_one_repo_context(self):
        with tempfile.TemporaryDirectory() as td:
            artifacts = str(Path(td) / "artifacts")
            created = self.run_cli(
                td, artifacts, "item", "create", "--project", "serveros", "--prefix", "SRV",
                "--title", "context update", "--kind", "ops",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            added = self.run_cli(td, artifacts, "activity", "add", "SRV-0001", "verify", "--kind", "test")
            self.assertEqual(added.returncode, 0, added.stderr)
            first_worktree = str(Path(td) / "first")
            final_worktree = str(Path(td) / "final")

            for args in (
                ("--mode", "develop", "--revision", "old", "--worktree", first_worktree),
                ("--mode", "develop", "--revision", "old", "--worktree", first_worktree),
                ("--mode", "test", "--revision", "new", "--worktree", final_worktree, "--dirty"),
            ):
                attached = self.run_cli(
                    td, artifacts, "context", "attach", "SRV-0001", "verify",
                    "--repo", "work-engine", *args,
                )
                self.assertEqual(attached.returncode, 0, attached.stderr)

            second_repo = self.run_cli(
                td, artifacts, "context", "attach", "SRV-0001", "verify",
                "--repo", "ops-repo", "--mode", "inspect", "--revision", "ops",
            )
            self.assertEqual(second_repo.returncode, 0, second_repo.stderr)
            begun = self.run_cli(
                td, artifacts, "run", "begin", "SRV-0001", "verify",
                "--worker", "codex", "--idempotency-key", "context-update",
            )
            self.assertEqual(begun.returncode, 0, begun.stderr)
            contexts = json.loads(begun.stdout)["contexts"]
            self.assertEqual(len(contexts), 2)
            by_repo = {context["repo"]: context for context in contexts}
            self.assertEqual(
                by_repo["work-engine"],
                {
                    "item_id": "SRV-0001", "activity_id": "verify", "repo": "work-engine",
                    "mode": "test", "revision": "new", "worktree": final_worktree, "dirty": True,
                },
            )
            self.assertEqual(by_repo["ops-repo"]["revision"], "ops")

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

    def test_board_render_refuses_to_replace_a_populated_board_with_an_empty_one(self):
        """A project whose items are absent from the state dir must not erase a populated board.

        The board is a projection, but for a project whose items live somewhere the state
        directory cannot see, the file on disk is the only record of them, and a second
        render cannot bring them back. Fail closed; make the operator say it on purpose.
        """
        with tempfile.TemporaryDirectory() as td:
            artifacts = str(Path(td) / "artifacts")
            created = self.run_cli(
                td, artifacts, "item", "create", "--project", "litellm", "--prefix", "LIT",
                "--title", "billing", "--kind", "ops", "--card-locator", "/records/LIT-0001/card.yaml",
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            board_dir = Path(td) / "board"
            kept = self.run_cli(td, artifacts, "board", "render", "--project", "litellm", "--output-dir", str(board_dir))
            self.assertEqual(kept.returncode, 0, kept.stderr)
            self.assertEqual(json.loads(kept.stdout)["items"], 1)
            before = (board_dir / "current.json").read_text(encoding="utf-8")
            self.assertIn("LIT-0001", before)

            # Same output directory, a project the state dir has no items for.
            empty = self.run_cli(td, artifacts, "board", "render", "--project", "multi-user-claw", "--output-dir", str(board_dir))
            self.assertEqual(empty.returncode, 2, empty.stdout)
            self.assertIn("refusing to render the board", empty.stderr)
            self.assertIn("multi-user-claw", empty.stderr)
            self.assertEqual((board_dir / "current.json").read_text(encoding="utf-8"), before)

    def test_board_render_allows_an_empty_board_on_purpose_or_on_a_first_render(self):
        """The guard must not become the reason a board cannot be written at all."""
        with tempfile.TemporaryDirectory() as td:
            artifacts = str(Path(td) / "artifacts")
            # First render of a project with no items: no board exists yet, nothing to erase.
            fresh_dir = Path(td) / "fresh"
            first = self.run_cli(td, artifacts, "board", "render", "--project", "litellm", "--output-dir", str(fresh_dir))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["items"], 0)

            # Overwriting it then needs the explicit flag.
            kept_dir = Path(td) / "kept"
            self.run_cli(
                td, artifacts, "item", "create", "--project", "litellm", "--prefix", "LIT",
                "--title", "billing", "--kind", "ops", "--card-locator", "/records/LIT-0001/card.yaml",
            )
            self.run_cli(td, artifacts, "board", "render", "--project", "litellm", "--output-dir", str(kept_dir))
            forced = self.run_cli(
                td, artifacts, "board", "render", "--project", "other", "--output-dir", str(kept_dir), "--allow-empty",
            )
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(json.loads((kept_dir / "current.json").read_text(encoding="utf-8"))["items"], [])


if __name__ == "__main__":
    unittest.main()
