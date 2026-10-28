#!/usr/bin/env python3
"""Wave 12 composition ledger — the RC carries the authors' own deltas, and the
two ids it was built to fix are fixed *by mechanism*, not by a relaxed test.

WHY THIS FILE EXISTS
--------------------
Wave 12 composes two already-reviewed author commits onto #479's exact head
`cd1485f` by ancestry (`git merge-tree --write-tree`, never by copying files):

* #471 `7b4b9d66` — `fix(fleet-next): pin the ChatGPT payload member set`
* #472 `0c588896` — `test(fleet-v1): give the live #456 contract a real session
  list, and keep its refusals`

Both are single commits whose parent is already an ancestor of the RC
(`7b4b9d6^ == 14306f36`, `0c58889^ == 56fcb927`), so the merge-base is exactly
each author's parent and the merge carries exactly each author's own files —
4 files, +215/−18, nothing else. That is a property of the *history*, and it is
the property a reviewer cannot see in a diff: a cherry-pick that re-applies the
same hunks produces the same file bytes and a different, unattributable tree.

So this file pins two things a green test run does not:

1. **Fidelity** — the four files still hold the authors' exact content (digest
   per file), and the product file the failing test blamed
   (`integrations/fleet-v1-milestone-batch/registration.py`) is untouched. The
   second half is the anti-hack guard: if someone makes
   `test_a_plan_the_live_contract_accepts_becomes_a_registration` pass by
   teaching the registerer to tolerate an empty session list, that digest moves
   and this goes red.
2. **Mechanism** — the two fixes are present as *rules*, not as numbers:
   `PAYLOAD_MEMBERS` is a declared set that `collect()` refuses to deviate from,
   and the registration fixture answers with the sessions the plan names while
   keeping all five refusal cases. A future edit that re-broadens either one
   turns a mechanism assertion red here before it turns a behaviour assertion
   red under a different name.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not run the product against a device, a broker, Feishu, a model or a
credential, and it makes no claim about any of them. LAYERS: CODE (bytes) /
UNIT (unittest; no provider, no network, no service, no clock).

Line endings: the working tree is CRLF on Windows (`core.autocrlf`) and LF on
the Linux runner, so every digest is computed over `\\r\\n`-normalised bytes.
A change that only rewrites line endings is therefore *not* caught here — that
is the one blind spot, and it is deliberate: it keeps one file green on both
hosts instead of making the honest reading platform-dependent.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BUILD_ZIP = REPO / "integrations" / "chatgpt-fleet-next" / "build_zip.py"
SKILLS = REPO / "integrations" / "chatgpt-fleet-next" / "skills"
PLUGIN_PACKAGE_TEST = REPO / "tests" / "test_fleet_next_plugin_package.py"
BATCH_README = REPO / "integrations" / "fleet-v1-milestone-batch" / "README.md"
REGISTRATION_TEST = REPO / "tests" / "test_fleet_v1_milestone_batch_registration.py"
REGISTRATION = REPO / "integrations" / "fleet-v1-milestone-batch" / "registration.py"

#: The tree these digests were measured on. TREE-SCOPED on purpose: the values
#: below are the authors' bytes *on `f64d7af1`*, and that is the only tree they
#: claim. A later composition may legitimately carry a newer author blob for a
#: file — #481 `9b7710a` edits the Batch README — and rewriting this table to
#: the new number would erase the Wave 12 fact instead of recording a second
#: one. The claimed tree is read back by object id below, so the historical
#: claim stays checkable even after the working tree has moved on.
WAVE12_TREE = "f64d7af1"

#: The one composed file a later author owns in a later composition. Its Wave
#: 12 digest is still asserted against `WAVE12_TREE`; the current working
#: tree's copy is asserted by `test_fleet_next_rc_wave13_ledger.py`, which
#: compares it to #481's own blob. Neither scope is left unguarded.
README = "integrations/fleet-v1-milestone-batch/README.md"

#: LF-normalised sha256 of every file the Wave 12 composition touches, plus the
#: one product file it must not, recorded from the composed tree `f64d7af1`.
DIGESTS = {
    "integrations/chatgpt-fleet-next/build_zip.py":
        "f1fd2ab580f2c5a15f69c9bf57c105fde5e11ef99025c4d35da9e848fae40fc9",
    "tests/test_fleet_next_plugin_package.py":
        "672a0591bd4b6e72ec557ae5deb0f8e6bda7a3da0e1839376da7665c4818fd70",
    "integrations/fleet-v1-milestone-batch/README.md":
        "d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf",
    "tests/test_fleet_v1_milestone_batch_registration.py":
        "a685ea678071b561107731ea6b327baf6b6668e80365a9565d15b52f93c1edb9",
    "integrations/fleet-v1-milestone-batch/registration.py":
        "f226860cc58893b5b248e41100e275eace3402beeb383753e1d2e95836f67fe1",
}

#: What #471 declares. Kept as a literal here on purpose: if the author's set
#: changes, this list is what tells the next reader that the payload changed.
DECLARED_MEMBERS = {
    "plugin.json",
    "icon.svg",
    "README.md",
    "skills/fleet-ability-routing/SKILL.md",
    "skills/fleet-agent-collaboration/SKILL.md",
    "skills/fleet-batch-commander/SKILL.md",
    "skills/fleet-multiagent-review/SKILL.md",
}

#: The refusal codes #472 keeps alongside the accepting case. Losing one of
#: these is how "make it green" turns into "stop checking".
REFUSAL_CODES = (
    "SESSION_NOT_FOUND",
    "SESSION_PROJECT_MISMATCH",
    "SESSION_BUSY",
    "GRANT_DENIED",
)


def _load(name: str, path: Path):
    """Load a module that is not on the import path.

    `sys.modules[name]` is set *before* `exec_module`: a `@dataclass` in the
    module resolves its annotations through `sys.modules[cls.__module__]` and
    reads `None` otherwise.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _tree_digest(relative: str) -> str:
    """The digest of `relative` inside `WAVE12_TREE`, read from the object
    database. This is the historical claim; it does not look at the working
    tree, so a later author's edit cannot make it silently disappear."""
    blob = subprocess.run(
        ("git", "cat-file", "-p", f"{WAVE12_TREE}:{relative}"),
        cwd=REPO, check=True, capture_output=True).stdout
    return hashlib.sha256(blob.replace(b"\r\n", b"\n")).hexdigest()


class TheCompositionIsTheAuthorsBytes(unittest.TestCase):
    """Four files from two commits, plus one file nobody touched."""

    def test_each_composed_file_still_holds_the_authors_content(self):
        """**Turns red when** any of the four is hand-edited on this branch.

        That is the point: the RC's claim is "exactly these two deltas", and a
        local tweak to any of them makes the claim false while leaving the suite
        green.

        The README is the one entry a *later* author legitimately changed, so
        its working-tree copy is not asserted here: this class checks the Wave
        12 bytes against the Wave 12 tree object, and
        `test_fleet_next_rc_wave13_ledger.py` checks the current copy against
        #481's blob. Both scopes have an exact claim; neither is relaxed to a
        "different from" or a "starts with".
        """
        for relative in list(DIGESTS)[:4]:
            if relative == README:
                continue
            with self.subTest(file=relative):
                self.assertEqual(
                    _digest(REPO / relative), DIGESTS[relative],
                    f"{relative} no longer matches the composed tree f64d7af1 — "
                    "re-derive the composition instead of editing the file")

    def test_the_wave12_tree_object_still_holds_every_recorded_digest(self):
        """The Wave 12 claim, checked on the tree it was measured on.

        **Turns red when** the recorded digests stop describing `f64d7af1` —
        including the README, which the working tree has since moved past.
        This is the assertion that makes the tree scope a fact rather than a
        comment: if someone re-points `DIGESTS` at a newer tree, the object
        read disagrees.
        """
        self.assertEqual(
            subprocess.run(
                ("git", "cat-file", "-t", WAVE12_TREE),
                cwd=REPO, check=True, capture_output=True).stdout.strip(),
            b"tree")
        for relative, expected in DIGESTS.items():
            with self.subTest(file=relative):
                self.assertEqual(
                    _tree_digest(relative), expected,
                    f"{WAVE12_TREE} no longer holds the recorded bytes for "
                    f"{relative} — the Wave 12 claim was rewritten, not "
                    "re-scoped")

    def test_the_product_file_the_red_test_blamed_is_untouched(self):
        """`registration.py:498` raised `COMMANDER_REFUSED: SESSION_NOT_FOUND`.

        #472 fixes that from the *fixture* side. If instead the registerer is
        taught to accept a session list that does not name the plan's sessions,
        the red goes away and the guarantee does not — so this digest is the
        guard that the fix stayed on the test side of the line.
        """
        relative = "integrations/fleet-v1-milestone-batch/registration.py"
        self.assertEqual(
            _digest(REPO / relative), DIGESTS[relative],
            "the registerer moved; if this was meant, re-run the refusal cases "
            "in test_fleet_v1_milestone_batch_registration.py before trusting "
            "the green")


class ThePayloadMemberSetIsDeclaredNotCounted(unittest.TestCase):
    """#471 replaced `len(members) == 6` with a declared set. The count was the
    symptom; the rule is the fix."""

    def test_the_declaration_is_the_source_tree(self):
        """The declaration and the tree must agree, in both directions.

        **Turns red when** a Skill (or a reference, or a script) is added to
        `integrations/chatgpt-fleet-next/skills/` without being declared — which
        is exactly the silent payload change #471 exists to prevent.
        """
        module = _load("wave12_build_zip", BUILD_ZIP)
        declared = set(module.PAYLOAD_MEMBERS)
        self.assertEqual(declared, DECLARED_MEMBERS,
                         "the declared member set changed; re-read §3 of the "
                         "Wave 12 record before accepting the new payload")

        on_disk = {f"skills/{p.parent.name}/{p.name}"
                   for p in SKILLS.glob("*/SKILL.md")}
        self.assertEqual(declared & on_disk, on_disk,
                         "a skill exists on disk that the payload does not "
                         "declare; it will be refused at build time")
        self.assertEqual(len(on_disk), 4, "the skill count moved")

    def test_the_builder_refuses_a_deviation_rather_than_repacking(self):
        """`collect()` compares the selection to the declaration and raises
        `Refused` naming the member, in both directions."""
        source = _text(BUILD_ZIP)
        self.assertIn("unexpected payload member(s):", source)
        self.assertIn("missing payload member(s):", source)
        self.assertTrue(
            issubclass(_load("wave12_build_zip_refused", BUILD_ZIP).Refused,
                       Exception),
            "the refusal must be an exception a caller can catch")

    def test_the_test_no_longer_hardcodes_six(self):
        """The original red: `self.assertEqual(len(members), 6)` against a
        package of seven. **Turns red when** the count comes back."""
        source = _text(PLUGIN_PACKAGE_TEST)
        self.assertNotIn("assertEqual(len(members), 6)", source,
                         "the hardcoded count is back; it will disagree with "
                         "the payload the moment a Skill is added")
        self.assertIn("set(bz.PAYLOAD_MEMBERS)", source)
        for node in ("test_an_unlisted_skill_member_is_refused_not_silently_packed",
                     "test_a_rogue_skill_directory_is_refused",
                     "test_a_missing_declared_skill_is_refused"):
            with self.subTest(node=node):
                self.assertIn(node, source,
                              "the refusal cases #471 added are gone")


class TheLiveFixtureIsHonest(unittest.TestCase):
    """#472 replaced `"sessions": []` with the sessions the plan names, and
    kept the refusals that prove the fixture is not why it passes."""

    def test_the_accepted_plan_is_answered_with_its_own_sessions(self):
        """**Turns red when** the accepting test goes back to an empty
        snapshot — the exact shape that produced `SESSION_NOT_FOUND`."""
        source = _text(REGISTRATION_TEST)
        self.assertIn("def live_sessions(", source)
        self.assertIn("def live_snapshot(", source)

        accepting = source.split(
            "def test_a_plan_the_live_contract_accepts_becomes_a_registration",
            1)[1]
        accepting = accepting.split("\n    def ", 1)[0]
        self.assertIn("live_snapshot()", accepting,
                      "the accepting case must be handed the live fixture")
        self.assertNotIn('"sessions": []', accepting,
                         "an empty session list is back in the accepting case")

    def test_an_empty_session_list_is_still_refused(self):
        """The other direction, in the same file: `"sessions": []` must remain
        as a *refusal* input. That pairing is what makes the green mean
        something."""
        source = _text(REGISTRATION_TEST)
        self.assertIn("live_snapshot(sessions=[])", source)
        self.assertIn("code=\"SESSION_NOT_FOUND\"", source,
                      "the empty-list refusal lost its code")

    def test_the_refusals_were_kept(self):
        """Five refusal cases, each asserting #456's own code, each asserting
        that nothing was filed."""
        source = _text(REGISTRATION_TEST)
        self.assertGreaterEqual(source.count("self.assertRefused("), 5,
                                "a refusal case was removed to keep this green")
        for code in REFUSAL_CODES:
            with self.subTest(code=code):
                self.assertIn(f'"{code}"', source,
                              f"{code} is no longer asserted")
        self.assertIn('"COMMANDER_REFUSED"', source)
        self.assertIn("assertFalse(self.path().exists()", source,
                      "a blocked plan must leave no file; that assertion is "
                      "the one that makes 'refused' mean 'refused'")

    def test_the_registration_is_filed_only_after_the_live_verdict(self):
        source = _text(REGISTRATION_TEST)
        self.assertIn("def test_the_registration_is_filed_only_after_the_live_verdict",
                      source)


class TheWave12RecordStaysTrue(unittest.TestCase):
    """The document is part of the deliverable; these keep its numbers tied to
    the tree they were measured on."""

    RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-RC-WAVE12-20261028.md"

    def test_the_record_names_the_composed_and_parent_shas(self):
        source = _text(self.RECORD)
        for sha in ("26ad7245", "f64d7af1", "cd1485f", "7b4b9d66", "0c588896"):
            with self.subTest(sha=sha):
                self.assertIn(sha, source,
                              "the record lost a SHA a reviewer needs to replay "
                              "the composition")

    def test_the_record_does_not_claim_a_release(self):
        """This lane is CODE/UNIT/CI only. A record that starts claiming a
        device or a production reading is wrong even if every test passes."""
        source = _text(self.RECORD)
        self.assertIn("REAL_DEVICE", source)
        self.assertIn("not measured", source)


if __name__ == "__main__":
    unittest.main()
