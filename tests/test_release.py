"""Tests for the release/promotion path.

Hermetic: the artifact is synthetic, the runtime root is a temp directory, and
the unit and shim destinations are temp directories too, so running these never
touches the machine's real `~/.config/systemd/user` or `~/.local/bin`.

The defect these tests exist for is specific. A node whose supervisor and whose
CLI resolve different revisions is running two products at once, and nothing in
either program can see it — each one is internally consistent and points
somewhere that works. So the assertion that matters is not "promote returned 0";
it is that afterwards the unit and the shim name the same revision, and that a
rollback returns both.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"

_spec = importlib.util.spec_from_file_location("serveros_release", RELEASE)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

UNIT_TEMPLATE = """\
[Unit]
Description=ServerOS node runtime (semantic controller)

[Service]
Type=simple
Environment=SERVEROS_STATE_DIR=@SERVEROS_STATE_DIR@
ExecStart=@SERVEROS_RUNTIME@/bin/serverosd
Restart=on-failure

[Install]
WantedBy=default.target
"""

CLI_SHIM = """\
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import cli
if __name__ == "__main__":
    sys.exit(cli.main())
"""

CLI_MODULE = """\
import argparse
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="store_true")
    ap.parse_args()
    return 0
"""


def make_artifact(base: Path, marker: str, *, with_daemon: bool = True) -> Path:
    """A minimal but structurally real artifact.

    The shim is the real three-line shim and the module it imports is real, so
    `bin/serverosctl --help` exercises the actual `bin/` <-> `runtime/`
    relationship that a revision can get wrong while both paths still exist.
    """
    art = base / f"artifact-{marker}"
    (art / "bin").mkdir(parents=True)
    (art / "runtime").mkdir(parents=True)
    (art / "README.md").write_text(f"marker {marker}\n")
    (art / "runtime" / "serverosd.service").write_text(UNIT_TEMPLATE)
    (art / "runtime" / "cli.py").write_text(CLI_MODULE)
    (art / "runtime" / "daemon.py").write_text("# daemon\n")
    (art / "bin" / "serverosctl").write_text(CLI_SHIM)
    if with_daemon:
        (art / "bin" / "serverosd").write_text("# entry point\n")
    return art


def run_release(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RELEASE), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        self.composition = "serveros-linux-work"
        self.comp_root = self.runtime_root / self.composition

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra: str) -> list[str]:
        return [
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(self.state_dir),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
            *extra,
        ]

    def promote(self, artifact: Path, revision: str, *extra: str):
        return run_release(["promote", "--artifact", str(artifact),
                            "--revision", revision, *self.args(*extra)])

    def status(self):
        return run_release(["status", *self.args()])

    def test_promote_points_the_unit_and_the_cli_at_one_revision(self):
        """The assertion the whole tool exists for.

        Each entry point can be individually correct and still leave the node
        running two revisions. Only comparing them catches it.
        """
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)

        unit = (self.unit_dir / "serverosd.service").read_text()
        self.assertIn(str(self.comp_root / "current") + "/bin/serverosd", unit)
        self.assertIn(str(self.state_dir), unit)

        shim = (self.bin_dir / "serverosctl").resolve()
        self.assertEqual(shim, (self.comp_root / "current" / "bin" / "serverosctl").resolve())
        self.assertEqual(self.status().returncode, 0,
                         f"status reported divergence after a promote:\n{self.status().stdout}")

    def test_promote_renders_every_placeholder(self):
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        unit = (self.unit_dir / "serverosd.service").read_text()
        self.assertNotIn("@SERVEROS_RUNTIME@", unit)
        self.assertNotIn("@SERVEROS_STATE_DIR@", unit)
        self.assertIn(str(self.comp_root / "current"), unit)

    def test_rollback_returns_both_entry_points_to_the_previous_revision(self):
        a, b = make_artifact(self.base, "a"), make_artifact(self.base, "b")
        self.assertEqual(self.promote(a, "rev-a").returncode, 0)
        self.assertEqual(self.promote(b, "rev-b").returncode, 0)
        self.assertEqual(self.status().returncode, 0)

        self.assertEqual(
            run_release(["rollback", *self.args()]).returncode, 0)
        self.assertEqual(self.status().returncode, 0,
                         f"rollback left the node divergent:\n{self.status().stdout}")
        # Resolution, not text. The unit names `current` and keeps naming it
        # across every promotion and rollback; that stability is what lets a
        # rollback be one symlink swap instead of a re-render that could fail
        # halfway. So what a rollback must be judged by is where `current`
        # ends up, not which revision string the unit happens to contain.
        self.assertEqual(release.current_revision(self.comp_root), "rev-a")
        self.assertIn(str(self.comp_root / "current"),
                      (self.unit_dir / "serverosd.service").read_text())

    def test_rollback_keeps_the_revision_it_replaced(self):
        """A rollback is an exchange, not a discard.

        If rolling back deleted the revision it moved away from, a bad rollback
        would be unrecoverable — which is the one moment the tool is being used
        precisely because something is already wrong.
        """
        a, b = make_artifact(self.base, "a"), make_artifact(self.base, "b")
        self.promote(a, "rev-a")
        self.promote(b, "rev-b")
        self.assertTrue((self.comp_root / "revisions" / "rev-a").is_dir())
        run_release(["rollback", *self.args()])
        self.assertTrue((self.comp_root / "revisions" / "rev-b").is_dir(),
                        "rollback removed the revision it rolled back from")
        self.assertTrue((self.comp_root / "revisions" / "rev-a").is_dir())

    def test_promote_refuses_an_artifact_with_no_daemon(self):
        art = make_artifact(self.base, "a", with_daemon=False)
        result = self.promote(art, "rev-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bin/serverosd", result.stderr)
        self.assertFalse((self.comp_root / "current").is_symlink(),
                         "a refused artifact left the node pointed at something")

    def test_promote_refuses_to_restage_a_revision_without_replace(self):
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        again = self.promote(art, "rev-a")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already staged", again.stderr)

    def test_a_failed_verify_leaves_current_untouched(self):
        """Verification happens while `current` still points at what is running."""
        good = make_artifact(self.base, "good")
        self.assertEqual(self.promote(good, "rev-good").returncode, 0)

        broken = make_artifact(self.base, "broken")
        # A shim whose runtime moved out from under it: both paths exist, and
        # the entry point still cannot run. This is the failure an existence
        # check cannot see.
        (broken / "runtime" / "cli.py").unlink()

        result = self.promote(broken, "rev-broken")
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED", result.stderr)
        self.assertEqual(release.current_revision(self.comp_root), "rev-good")

    def test_staged_revision_is_immutable_once_promoted(self):
        """Swapping `current` must not move or rewrite the revision itself."""
        art = make_artifact(self.base, "a")
        self.promote(art, "rev-a")
        rev = self.comp_root / "revisions" / "rev-a"
        before = release.fingerprint(rev)
        self.promote(make_artifact(self.base, "b"), "rev-b")
        self.assertEqual(release.fingerprint(rev), before)
        manifest = json.loads((rev / release.TREE_MANIFEST).read_text())
        self.assertEqual(manifest["revision"], "rev-a")

    def test_fingerprint_reproduces_the_recorded_tree_hash_recipe(self):
        """The hash must be the one the release records quote.

        Two hashes of the same directory that cannot be compared are worse than
        no hash, because a reader takes the agreement of two numbers as
        verification. So the shell recipe the records use is run here, and the
        Python must match it exactly — including `find`'s `./` prefix and
        `sha256sum`'s two-space separator.
        """
        art = make_artifact(self.base, "a")
        shell = subprocess.run(
            "find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum",
            shell=True, cwd=art, stdout=subprocess.PIPE, text=True,
            env={**os.environ, "LC_ALL": "C"})
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(release.fingerprint(art), shell.stdout.split()[0])

    def test_fingerprint_changes_when_the_tree_changes(self):
        art = make_artifact(self.base, "a")
        before = release.fingerprint(art)
        (art / "runtime" / "extra.py").write_text("# new\n")
        self.assertNotEqual(release.fingerprint(art), before)


if __name__ == "__main__":
    unittest.main()
