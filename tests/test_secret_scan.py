"""Tests for the §28 credential-shape gate.

A scanner is only a gate if it can fail, and a scanner that fails on ordinary
text is one somebody turns off. So most of what follows are the two halves of
that claim: every rule fires on a sample of its own shape, and ordinary release
text — digests, device refs, a sentence that merely names the prefixes — is not
reported. The gate itself, against the real artifact, is in `test_build.py`.

The scanner is invoked as a subprocess rather than imported, because the gate is
the CLI: its exit code is what a release checks, and a test that called an
internal function would pass while the command was broken.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "scan_secrets.py"

# One sample per rule. Each is assembled at runtime so that the file itself is
# not a credential-shaped literal in a scanner that reports those.
POSITIVE_SAMPLES = {
    "private-key": "-----BEGIN OPENSSH PRIVATE KEY-----",
    "github-token": "ghp_" + "A" * 36,
    "aws-access-key-id": "AKIA" + "B" * 16,
    "slack-token": "xoxb-" + "1" * 20,
    "anthropic-or-openai-key": "sk-" + "c" * 40,
}

# Text a real release tree is full of. None of it is a credential, and a rule
# that fired on any of it would be wrong in the direction that gets a gate
# disabled. The third entry is the important one: naming a prefix is what the
# documentation and this scanner's own docstring do constantly.
NEGATIVE_SAMPLES = (
    "sha256=7e6610ee9b044eecad2cd0ccd2c48ce9a2a56d985b84fc2a0439eb06ac9a0d2a",
    "device_ref: device:server-beijing",
    "the ghp_ prefix and the sk- prefix are named in this sentence",
    "commit 0a98bb6db36ec50b448b6409c9c310c21ca21150",
    "-----BEGIN PGP SIGNATURE-----",
    "sk-learn is a python library",
    "path: /opt/applications/data/home-hwz/runtime-assets/hwz-server-os/current",
)


def run_scanner(*paths: Path) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(SCANNER), *map(str, paths), "--json"],
                          capture_output=True, text=True, timeout=180)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = {"stdout": proc.stdout, "stderr": proc.stderr}
    return proc.returncode, report


class RuleTests(unittest.TestCase):
    """Each rule must fire on its own shape, and on nothing in the negative set."""

    def test_every_rule_fires_on_its_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            for rule, sample in POSITIVE_SAMPLES.items():
                (tree / f"{rule}.txt").write_text(f"note = {sample}\n")
            rc, report = run_scanner(tree)
            self.assertEqual(rc, 1, "a tree holding credentials must not exit 0")
            self.assertEqual(report["verdict"], "CREDENTIAL_SHAPE_FOUND")
            found = {f["path"]: f["rule"] for f in report["findings"]}
            for rule in POSITIVE_SAMPLES:
                with self.subTest(rule=rule):
                    self.assertEqual(found.get(f"{rule}.txt"), rule,
                                     f"rule {rule!r} did not fire on its own sample")

    def test_ordinary_release_text_is_not_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "notes.md").write_text("\n".join(NEGATIVE_SAMPLES) + "\n")
            rc, report = run_scanner(tree)
            self.assertEqual(rc, 0, f"false positive(s): {report.get('findings')}")
            self.assertEqual(report["verdict"], "CLEAN")
            self.assertEqual(report["findings"], [])

    def test_a_single_file_argument_is_scanned_not_silently_skipped(self):
        """`os.walk` on a file yields nothing.

        A file argument that fell through to the tree walk would read zero files
        and print CLEAN — a checker passing because it examined nothing, which
        is the failure this scanner exists to prevent one level up. A release
        body is exactly the kind of file someone points this at.
        """
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "body.md"
            body.write_text("token = " + POSITIVE_SAMPLES["github-token"] + "\n")
            rc, report = run_scanner(body)
            self.assertEqual(rc, 1, "a file holding a credential must not exit 0")
            self.assertEqual(report["scanned_files"], 1)
            self.assertEqual([f["path"] for f in report["findings"]], ["body.md"])

    def test_a_clean_tree_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            (tree / "README.md").write_text("# nothing to see\n")
            rc, report = run_scanner(tree)
            self.assertEqual(rc, 0)
            self.assertEqual(report["scanned_files"], 1)

    def test_a_missing_path_is_an_error_not_a_pass(self):
        rc, _ = run_scanner(Path("/nonexistent/never/here"))
        self.assertEqual(rc, 2, "scanning nothing must not read as scanning clean")


class ExclusionTests(unittest.TestCase):
    """The fixture list is named, and the scan does not quietly skip a whole directory."""

    def test_the_fixture_list_is_individual_files(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import scan_secrets
        finally:
            sys.path.pop(0)
        for entry in scan_secrets.FIXTURE_PATHS:
            with self.subTest(entry=entry):
                self.assertFalse(entry.endswith("/"),
                                 "a directory-wide exclusion is not a named fixture")
                self.assertTrue((ROOT / entry).is_file(),
                                f"excluded fixture does not exist: {entry}")

    def test_a_credential_under_tests_is_reported(self):
        """`tests/` is not exempt wholesale — only the named fixtures are."""
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            tests_dir = tree / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_something_else.py").write_text(
                "TOKEN = " + repr(POSITIVE_SAMPLES["github-token"]) + "\n")
            rc, report = run_scanner(tree)
            self.assertEqual(rc, 1)
            self.assertEqual([f["path"] for f in report["findings"]],
                             ["tests/test_something_else.py"])


if __name__ == "__main__":
    unittest.main()
