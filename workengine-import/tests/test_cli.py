"""CLI smoke tests exercising argparse wiring end-to-end.

These call ``workengine.cli.main`` directly (in-process) against a temp state
directory, so they are hermetic and also lock in the ``--state-dir``
placement semantics (before or after the subcommand).
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from workengine.cli import main


def _run(argv, state_dir):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _id_from_create(out):
    for line in out.splitlines():
        if line.startswith("created "):
            return line.split()[1]
    raise AssertionError(f"no 'created ' line in:\n{out}")


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"

    def test_create_advance_list(self):
        code, out, err = _run(
            ["--state-dir", str(self.state_dir), "create", "--title", "t", "--requirement", "r"],
            self.state_dir,
        )
        self.assertEqual(code, 0, err)
        item_id = _id_from_create(out)

        code, _, err = _run(["advance", item_id, "--state-dir", str(self.state_dir), "--to", "TRIAGED"], self.state_dir)
        self.assertEqual(code, 0, err)

        code, out, err = _run(["list", "--state-dir", str(self.state_dir)], self.state_dir)
        self.assertEqual(code, 0, err)
        self.assertIn(item_id, out)
        self.assertIn("TRIAGED", out)

    def test_resume_shows_worktree_and_next(self):
        code, out, _ = _run(
            [
                "--state-dir",
                str(self.state_dir),
                "create",
                "--title",
                "t",
                "--requirement",
                "r",
                "--worktree",
                "/tmp/resume-me",
                "--plan",
                "step1",
            ],
            self.state_dir,
        )
        item_id = _id_from_create(out)
        for state in ("TRIAGED", "READY", "IMPLEMENTING"):
            code, _, err = _run(
                ["advance", item_id, "--state-dir", str(self.state_dir), "--to", state], self.state_dir
            )
            self.assertEqual(code, 0, err)

        # Flag may appear after the subcommand.
        code, out, err = _run(["resume", item_id, "--state-dir", str(self.state_dir)], self.state_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("/tmp/resume-me", out)          # worktree recovered
        self.assertIn("next transitions", out)
        self.assertIn("VERIFYING", out)               # legal next step
        self.assertNotIn("hint", out)

    def test_state_dir_isolation(self):
        # Different state dirs must not see each other's items.
        other = Path(self._tmp.name) / "other-state"
        code, out, _ = _run(
            ["--state-dir", str(self.state_dir), "create", "--title", "t", "--requirement", "r"], self.state_dir
        )
        item_id = _id_from_create(out)
        code, out2, _ = _run(["list", "--state-dir", str(other)], other)
        self.assertEqual(code, 0)
        self.assertNotIn(item_id, out2)

    def test_illegal_advance_returns_nonzero(self):
        code, out, _ = _run(
            ["--state-dir", str(self.state_dir), "create", "--title", "t", "--requirement", "r"], self.state_dir
        )
        item_id = _id_from_create(out)
        # NEW -> READY (skipping TRIAGED) is illegal.
        code, _, err = _run(["advance", item_id, "--state-dir", str(self.state_dir), "--to", "READY"], self.state_dir)
        self.assertEqual(code, 2)
        self.assertIn("Illegal transition", err)

    def test_unknown_id_resume_returns_nonzero(self):
        code, _, err = _run(["resume", "WI-NOPE", "--state-dir", str(self.state_dir)], self.state_dir)
        self.assertEqual(code, 1)
        self.assertIn("unknown work item id", err)


if __name__ == "__main__":
    unittest.main()
