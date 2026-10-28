#!/usr/bin/env python3
"""Wave 13 composition ledger — the same fidelity claim as Wave 12, scoped to
the tree it was actually measured on.

WHY THIS FILE EXISTS
--------------------
Wave 12's ledger (`test_fleet_next_rc_wave12_ledger.py`) pinned one digest per
composed file, including the Batch README, from the Wave 12 tree `f64d7af1`.
That claim was true on `f64d7af1` and became false the moment a later,
*legitimate* author change to the same file entered a composition: #481
`9b7710a` adds the `cli.py` ownership row and the scheduler entrypoint section
to that README.

The failure that produced was correctly diagnosed as `BLOCKED_OWNER` rather
than repaired by editing the digest. The repair is not a new number in the old
test; it is to make the claim **tree-scoped**:

* Wave 12 keeps its digest, and the digest is re-checked against the Wave 12
  tree object itself. History is not rewritten and it is not quietly forgotten.
* Wave 13 records its own tree, its own per-file digests, and — for every file
  a later author changed — the *author commit* whose blob the tree must hold.

A digest constant alone says "someone wrote this number down". The second half
says "and this is the commit whose bytes it is", which is the part a reviewer
cannot see in a diff.

WHY THE GIT OBJECTS ARE READABLE IN CI
--------------------------------------
The ancestry and author-blob assertions read real git objects. The `suite` job
checks out `serveros` with `fetch-depth: 0`, so the author commits that are
parents of this composition are present on the hosted runner too. Without that,
these tests would pass locally and fail in CI, which is worse than not having
them; the workflow change is part of this ledger, not an operator step.

WHAT THIS FILE DOES NOT DO
--------------------------
No device, broker, Feishu, model, credential, service or network. It reads the
repository it lives in. LAYERS: CODE (bytes and ancestry) / UNIT (unittest).
"""
from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The composition, in the order it was built. `#487` is the base; the other
#: three are the authors' exact heads, merged by ancestry.
AUTHOR_ANCESTRY = (
    ("#487", "43efd5ce0bfbe61db335910fabb85c92a24ea8e1"),
    ("#477", "be2c1cccd6a506485c9465967b2531facffe459e"),
    ("#481", "9b7710a26180e1e6d00ce597827d447d0d2348b7"),
    ("#480", "3777d134be02d896c9e36bc2ffd3a25baf9aaa04"),
)

BASE = AUTHOR_ANCESTRY[0][1]

#: Every product/test file the three later authors contribute, and the commit
#: whose blob the composed tree must still be holding for it. A local edit to
#: any of them makes the claim false while leaving a normal test run green.
AUTHOR_FILES = {
    "be2c1cccd6a506485c9465967b2531facffe459e": (
        "integrations/chatgpt-fleet-next/batch_contract.py",
        "tests/test_fleet_chatgpt_batch_commander.py",
        "tests/test_fleet_v1_l4_model_evidence.py",
    ),
    "9b7710a26180e1e6d00ce597827d447d0d2348b7": (
        "integrations/fleet-v1-milestone-batch/README.md",
        "integrations/fleet-v1-milestone-batch/cli.py",
        "tests/test_fleet_v1_milestone_batch_cli.py",
        "tests/test_fleet_v1_milestone_batch_cli_fakes.py",
    ),
    "3777d134be02d896c9e36bc2ffd3a25baf9aaa04": (
        "docs/reviews/fleet-next-parallel-plugin-20261028.md",
        "integrations/fleet-next-parallel-plugin/README.md",
        "integrations/fleet-next-parallel-plugin/parallel_plugin.py",
        "tests/test_fleet_next_parallel_plugin.py",
    ),
}

#: The Wave 12 tree and the README it actually contained. Kept here as the
#: cross-check for the Wave 12 ledger's preserved claim, not as a substitute
#: for it.
WAVE12_TREE = "f64d7af1"
WAVE12_README_DIGEST = (
    "d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf"
)
WAVE13_README_DIGEST = (
    "3f479eec5a7643ab51e0a3e67a87cb841e537c1b37edaeb69d5961cb61104558"
)

#: A composed path may not start with any of these. #478 keeps the old Fleet
#: running in parallel; this RC composes the Next lane's source only.
LEGACY_PREFIXES = (
    "fleet_mcp/",
    "runtime/",
    "services/",
    "plugin/",
    "deploy/",
    "serverosd/",
    "bin/",
)

RECORD = (
    REPO / "docs" / "integration" / "FLEET-NEXT-RC-WAVE13-20261028.md"
)


def _git(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args), cwd=REPO, check=True, capture_output=True
    ).stdout


def _blob(rev_path: str) -> bytes:
    """The bytes git holds at `<rev>:<path>`, line endings normalised the same
    way the Wave 12 ledger normalises a working-tree file."""
    return _git("cat-file", "-p", rev_path).replace(b"\r\n", b"\n")


def _digest(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def _blob_digest(rev_path: str) -> str:
    return hashlib.sha256(_blob(rev_path)).hexdigest()


def _is_ancestor(commit: str) -> bool:
    return subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, "HEAD"),
        cwd=REPO,
    ).returncode == 0


class TheCompositionIsBuiltByAncestry(unittest.TestCase):
    """The authors' commits are parents of the result, not re-applied hunks."""

    def test_every_author_head_is_an_ancestor_of_the_rc(self):
        """**Turns red when** the composition is rebuilt by cherry-pick: the
        bytes can be identical and the ancestry is gone, which is exactly the
        property a diff cannot show."""
        for pr, commit in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr, commit=commit[:9]):
                self.assertTrue(
                    _is_ancestor(commit),
                    f"{pr} {commit} is not an ancestor of HEAD — the tree may "
                    "hold the same bytes, but it is not this composition")

    def test_the_rc_is_not_the_qa_branch(self):
        """#489 is an independent QA lane. Using its tree as the RC would make
        the RC's evidence the QA's evidence, which is not independent."""
        for relative in (
            "integrations/fleet-next-rc-wave13-qa",
            "tests/test_fleet_next_rc_wave13_compose.py",
        ):
            with self.subTest(path=relative):
                self.assertFalse(
                    (REPO / relative).exists(),
                    f"{relative} belongs to the #489 QA lane and must not be "
                    "part of the RC")


class TheWave13FilesAreTheAuthorsBytes(unittest.TestCase):
    """Per file, the composed tree holds the author's own blob."""

    def test_each_file_holds_its_authors_blob(self):
        """**Turns red when** any composed file is hand-edited on this branch,
        including a well-meaning edit that "fixes" a failing assertion."""
        for author, paths in AUTHOR_FILES.items():
            for relative in paths:
                with self.subTest(author=author[:9], file=relative):
                    self.assertEqual(
                        _digest(REPO / relative),
                        _blob_digest(f"{author}:{relative}"),
                        f"{relative} no longer holds {author}'s blob — "
                        "re-derive the composition instead of editing the file")

    def test_the_readme_moved_between_the_two_tree_scopes(self):
        """The one file whose digest legitimately differs between the scopes.

        Wave 12's tree still holds the old README; this tree holds #481's.
        Asserting both makes "the digest changed" a *recorded* fact rather
        than the disappearance of a guard.
        """
        self.assertEqual(
            _blob_digest(f"{WAVE12_TREE}:integrations/fleet-v1-milestone-batch/README.md"),
            WAVE12_README_DIGEST,
            "the Wave 12 tree no longer holds the README Wave 12 recorded")
        self.assertEqual(
            _digest(REPO / "integrations" / "fleet-v1-milestone-batch" / "README.md"),
            WAVE13_README_DIGEST,
            "the composed README is not #481's blob")
        self.assertNotEqual(
            WAVE13_README_DIGEST, WAVE12_README_DIGEST,
            "if the two scopes name the same bytes there is no tree scope to "
            "record; re-read why this ledger exists")


class TheWave12ClaimSurvivesOnItsOwnTree(unittest.TestCase):
    """The old ledger is not rewritten; it is re-aimed at its own tree."""

    def test_the_wave12_tree_still_holds_its_own_readme(self):
        self.assertEqual(
            _git("cat-file", "-t", WAVE12_TREE).strip(), b"tree")
        self.assertEqual(
            _blob_digest(f"{WAVE12_TREE}:integrations/fleet-v1-milestone-batch/README.md"),
            WAVE12_README_DIGEST)


class TheLegacySurfaceIsUntouched(unittest.TestCase):
    """#478 keeps the old Fleet in parallel. This RC may not move it."""

    def test_no_legacy_path_is_in_the_composition_delta(self):
        changed = _git(
            "diff", "--name-only", f"{BASE}..HEAD").decode().splitlines()
        offenders = sorted(
            path for path in changed
            if path.startswith(LEGACY_PREFIXES))
        self.assertEqual(
            offenders, [],
            "the composition moved a Legacy/Next runtime path: "
            + ", ".join(offenders))

    def test_the_fleet_mcp_tree_is_byte_identical(self):
        before = _git("rev-parse", f"{BASE}:fleet_mcp").strip()
        after = _git("rev-parse", "HEAD:fleet_mcp").strip()
        self.assertEqual(before, after,
                         "the Legacy `fleet_mcp/` tree changed")


class TheRecordIsADeliverable(unittest.TestCase):
    """The document is part of the RC, and it says what was and was not run."""

    def test_the_record_names_the_composed_shas(self):
        source = RECORD.read_text(encoding="utf-8")
        for pr, commit in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr):
                self.assertIn(commit[:7], source,
                              f"the record does not name {pr}'s head")

    def test_the_record_does_not_claim_a_release(self):
        source = RECORD.read_text(encoding="utf-8")
        self.assertIn("REAL_DEVICE", source)
        self.assertIn("PRODUCTION", source)
        self.assertIn("not measured", source)


if __name__ == "__main__":
    unittest.main()
