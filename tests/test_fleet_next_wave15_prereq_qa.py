#!/usr/bin/env python3
"""Wave 15 prerequisite QA — the notifier seam #495 called missing, measured
against the notifier the Wave 14 RC actually carries.

THE CLAIM
---------
#495 pinned #423's original head ``d9b2948`` and reported
``NOTIFIER_SEAM_MISSING`` from #481's ``BatchAggregator``. That head predates
#460's public ``send_text`` seam ``4fc9fcb``. The RC ``00503f9`` carries
``4fc9fcb`` through #481's merge ``ba76a80``. If that is true, the seam is not
missing from the composition; the QA pin addressed a different commit.

This file proves it with two runs of the **real** #481 aggregator:

    old  d9b2948 -> send_text False -> NOTIFIER_SEAM_MISSING   (RED)
    new  4fc9fcb -> send_text True  -> constructed              (GREEN)

No notifier source is copied into this repository. ``seam_probe.py`` extracts
each frozen commit with ``git archive`` into a throw-away directory and runs
the aggregator there. The test also pins the 5/5 limit: the composed head is
4/5, the two new failures are #490's naming collision, and no Wave 15 claim of
a green full gate is made here.

WHAT THIS DOES NOT DO
---------------------
No network, provider, credential, service, Feishu/IM, model or GPU. It reads
git objects and runs one offline import per mode. LAYERS: CODE / UNIT.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROBE = (REPO / "integrations" / "fleet-next-wave15-prereq-qa"
         / "seam_probe.py")

#: The gate is independent of the RC branch: it lives on main and reads the RC
#: by SHA. Carrying the RC's own ledger commit would make the Wave 14 ledger's
#: "the delta is exactly its recorded files" assertion false for a legitimate
#: Wave 15 addition.
GATE_BASE = "e69fc0337ae5d33000478e3d5162077f525bc8cc"

CURRENT = "00503f91b019ce342af52f38b19bb3aa21dfff55"
OLD_423 = "d9b2948a62609e16007f2fe0ae288f9652d64cd1"
SEAM_460 = "4fc9fcbc677f278d10caeb34d55184545810a13a"
NEWLINE_460 = "1dbeda5f87bce23b2134f869f6379667bc9847b9"
BATCH_481 = "9b7710a26180e1e6d00ce597827d447d0d2348b7"
ACTIVATION_488 = "009662f36a2a5f8e11c51ae517834379a91a7770"
MERGE_481_SEAM = "ba76a80411ca4fa3d94f05105877bc1e8fb377ff"
WAVE13 = "a00fabc87fff65aeb50a851536b3314be8399090"
LARK_FIX_415 = "dd5919a9256246570e611a7fa86aea28b46ef14d"

NOTIFIER_PATH = "integrations/fleet-v1-milestone-notify/milestone_notify.py"
COMPOSED_BLOB = "7df27cb36b169134ff279e43c0b9c329cf919fef"
OLD_BLOB = "72fcec8d553f1568a114449d352e1803250d26cc"
NEWLINE_BLOB = "8ff93b0e34f335d4b6903c8ef75bf8636117b3a6"

V2_HEADS = {
    "#407": "9538238d6a169507ef91366107d8bd3a4d75403d",
    "#473": "a097b27d17c184d3222d982991a90cc04283fea3",
    "#474": "0ad3c078a7d4d865ff7d8c54d01a08235237f8ec",
}

#: PR head refs, used only when a pinned commit is absent from the checkout.
#: The hosted suite checks out with fetch-depth: 0; this fallback keeps the
#: gate runnable on a narrower checkout without silently measuring a new tree.
PINNED_PRS = {
    494: CURRENT,
    493: "4f5320de7e187daadd79adc92e7487facd7b1820",
    488: ACTIVATION_488,
    490: "06a8be67422f1320e7bdfee38410f8b24d27b501",
    483: "644ee4a8e35b770426841929d5989bcfad128bef",
    460: NEWLINE_460,
    423: OLD_423,
    415: LARK_FIX_415,
    492: WAVE13,
    481: BATCH_481,
}

LEGACY_PREFIXES = (
    "fleet_mcp/", "runtime/", "services/", "plugin/", "deploy/",
    "serverosd/", "bin/",
)

MANIFEST = (REPO / "integrations" / "fleet-next-wave15-prereq-qa"
            / "composition.json")
RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-WAVE15-PREREQ-20261028.md"

ROOT: Path | None = None


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("wave15_seam_probe", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _has_commit(sha: str) -> bool:
    return subprocess.run(
        ("git", "cat-file", "-e", f"{sha}^{{commit}}"),
        cwd=REPO, capture_output=True).returncode == 0


def _ensure_pins() -> None:
    """Fetch a missing PR head into the object database, then assert its SHA.

    A gate that silently measured a newer tip would answer a different
    question. The fetch adds objects only; it creates no branch.
    """
    for pr, sha in PINNED_PRS.items():
        if _has_commit(sha):
            continue
        done = subprocess.run(
            ("git", "fetch", "--no-tags", "origin", f"refs/pull/{pr}/head"),
            cwd=REPO, capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise AssertionError(
                f"PR #{pr} {sha} is absent and cannot be fetched: "
                f"{done.stderr.strip()[-400:]}")
        got = subprocess.run(
            ("git", "rev-parse", "FETCH_HEAD"), cwd=REPO,
            capture_output=True, text=True, check=True).stdout.strip()
        if got != sha:
            raise AssertionError(
                f"PR #{pr} moved: refs/pull/{pr}/head is {got}, the gate "
                f"pinned {sha}")


def setUpModule() -> None:  # noqa: N802 - unittest protocol
    global ROOT
    _ensure_pins()
    ROOT = Path(tempfile.mkdtemp(prefix="wave15-prereq-qa-"))
    _load_probe_module().prepare(REPO, ROOT)


def tearDownModule() -> None:  # noqa: N802 - unittest protocol
    if ROOT is not None:
        shutil.rmtree(ROOT, ignore_errors=True)


def _git(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args), cwd=REPO, check=True, capture_output=True).stdout


def _blob_digest(rev_path: str) -> str:
    body = _git("cat-file", "-p", rev_path).replace(b"\r\n", b"\n")
    return hashlib.sha256(body).hexdigest()


def _blob_id(rev_path: str) -> str:
    """The git blob object id at `<rev>:<path>` (not a content sha256)."""
    return _git("rev-parse", rev_path).decode().strip()


def _is_ancestor(commit: str, of: str = "HEAD") -> bool:
    return subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, of),
        cwd=REPO).returncode == 0


def _merge_base(a: str, b: str) -> str:
    return _git("merge-base", a, b).decode().strip()


def _merge_parents(commit: str) -> tuple[str, ...]:
    return tuple(
        _git("rev-list", "--parents", "-n", "1", commit).decode().split()[1:])


def _merge_tree(a: str, b: str) -> tuple[bool, str]:
    done = subprocess.run(
        ("git", "merge-tree", "--write-tree", a, b),
        cwd=REPO, capture_output=True)
    return done.returncode == 0, done.stdout.decode().strip()


def _run_probe(mode: str) -> dict:
    done = subprocess.run(
        (sys.executable, str(PROBE), "--root", str(ROOT), "--mode", mode),
        cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    if done.returncode != 0:
        raise AssertionError(
            f"seam probe {mode} failed:\n{done.stdout}\n{done.stderr}")
    return json.loads(done.stdout)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TheSeamRedAndGreenAreReproducible(unittest.TestCase):
    """The same real aggregator, two frozen notifier revisions."""

    def test_the_423_original_head_refuses_the_aggregator(self):
        """**RED.** This is #495's reading reproduced on the real Batch CLI:
        the pinned head has no public `send_text`."""
        result = _run_probe("old")
        self.assertFalse(result["send_text"])
        self.assertFalse(result["constructed"])
        self.assertEqual(result["code"], "NOTIFIER_SEAM_MISSING")
        self.assertIn("send_text", result["why"])

    def test_the_composed_notifier_constructs_the_aggregator(self):
        """**GREEN.** The RC's notifier is the one with the public seam; the
        aggregator constructs without a patch."""
        result = _run_probe("new")
        self.assertTrue(result["send_text"])
        self.assertTrue(result["constructed"])
        self.assertIsNone(result["code"])

    def test_the_green_notifier_is_the_rc_blob(self):
        self.assertEqual(
            _blob_id(f"{CURRENT}:{NOTIFIER_PATH}"), COMPOSED_BLOB)
        self.assertEqual(
            _blob_id(f"{SEAM_460}:{NOTIFIER_PATH}"), COMPOSED_BLOB)
        self.assertEqual(
            _blob_id(f"{OLD_423}:{NOTIFIER_PATH}"), OLD_BLOB)
        self.assertNotEqual(COMPOSED_BLOB, OLD_BLOB)

    def test_the_seam_is_an_ancestor_of_481_and_the_rc(self):
        for commit in (SEAM_460, BATCH_481, ACTIVATION_488, CURRENT):
            with self.subTest(commit=commit[:9]):
                self.assertTrue(_is_ancestor(SEAM_460, commit))
        self.assertIn(SEAM_460, _merge_parents(MERGE_481_SEAM),
                      "#481's merge does not name 4fc9fcb as a parent")

    def test_the_later_460_head_is_an_optional_clean_bump(self):
        """#460's final head is not in the RC, but its only delta over the seam
        is the newline fix, and it merges cleanly. It is not required for
        `send_text`, so this gate records it instead of composing it."""
        self.assertFalse(_is_ancestor(NEWLINE_460, CURRENT))
        self.assertEqual(_merge_base(CURRENT, NEWLINE_460), SEAM_460)
        clean, tree = _merge_tree(CURRENT, NEWLINE_460)
        self.assertTrue(clean, f"#460 final head conflicts with the RC at {tree}")
        self.assertEqual(
            _blob_id(f"{NEWLINE_460}:{NOTIFIER_PATH}"), NEWLINE_BLOB)
        self.assertNotEqual(NEWLINE_BLOB, COMPOSED_BLOB)
        body = _git("cat-file", "-p", f"{CURRENT}:{NOTIFIER_PATH}").decode()
        self.assertIn("def send_text", body)

    def test_there_is_exactly_one_notifier_source(self):
        tree = _git(
            "ls-tree", "-r", "--name-only", CURRENT).decode().splitlines()
        copies = [path for path in tree
                  if Path(path).name == "milestone_notify.py"]
        self.assertEqual(copies, [NOTIFIER_PATH])


class TheQAPinIsTheOnlyCauseOfTheFalseBlock(unittest.TestCase):
    """#495's blocked interface is a composition pin, not a missing source."""

    def test_the_qa_pin_is_the_old_423_head(self):
        qa = _manifest()["qa_input"]
        self.assertEqual(qa["pr"], 495)
        self.assertEqual(
            qa["head"], "75e25724424f430c9058638f059cc8787f4ab963")
        self.assertEqual(qa["pinned_notifier"]["head"], OLD_423)
        self.assertEqual(qa["pinned_notifier"]["blob"], OLD_BLOB)
        self.assertFalse(qa["pinned_notifier"]["send_text"])
        self.assertIn("QA_PIN_ARTIFACT", qa["verdict"])

    def test_the_manifest_records_the_composed_notifier(self):
        notifier = _manifest()["notifier"]
        self.assertTrue(notifier["single_source"])
        self.assertEqual(notifier["composed"]["commit"], SEAM_460)
        self.assertEqual(notifier["composed"]["blob"], COMPOSED_BLOB)
        self.assertTrue(notifier["composed"]["send_text"])
        self.assertEqual(
            notifier["composed"]["merged_into_481_by"], MERGE_481_SEAM)
        self.assertTrue(notifier["qa_pin_artifact"])
        self.assertEqual(
            notifier["optional_final"]["head"], NEWLINE_460)
        self.assertFalse(notifier["optional_final"]["in_rc"])
        self.assertEqual(
            notifier["optional_final"]["merge_into_rc"], "clean")

    def test_the_record_names_the_correction(self):
        source = RECORD.read_text(encoding="utf-8")
        for token in (OLD_423[:9], SEAM_460[:9], OLD_BLOB[:9],
                      COMPOSED_BLOB[:9], "NOTIFIER_SEAM_MISSING",
                      "AGGREGATOR_OK", "QA_PIN_ARTIFACT"):
            with self.subTest(token=token):
                self.assertIn(token, source)


class ThePinsAndBuildOrderAreExact(unittest.TestCase):
    """A future composition can be rebuilt from these exact commits."""

    def test_the_gate_is_independent_of_the_rc_branch(self):
        """The gate lives on main and reads the RC by SHA. If the RC were an
        ancestor of this branch, the Wave 14 ledger's own delta assertion
        would treat these legitimate Wave 15 files as an unrecorded edit.

        The positive "the base commit is an ancestor" reading is deliberately
        not asserted here: the hosted checkout is depth-1, so the base commit
        may not be in the object database even though the fact is true. The
        independence claim is the negative one, and it is checkable without it.
        """
        self.assertFalse(_is_ancestor(CURRENT))
        self.assertEqual(_manifest()["gate_base"]["commit"], GATE_BASE)

    def test_every_pinned_commit_is_present(self):
        pins = _manifest()["pins"]
        for key, commit in pins.items():
            with self.subTest(pin=key):
                self.assertEqual(
                    subprocess.run(
                        ("git", "cat-file", "-e", f"{commit}^{{commit}}"),
                        cwd=REPO).returncode, 0,
                    f"{key} {commit} is not in the object database")
        self.assertEqual(pins["494"], CURRENT)
        self.assertEqual(pins["423"], OLD_423)
        self.assertEqual(pins["460_seam"], SEAM_460)
        self.assertEqual(pins["481"], BATCH_481)
        self.assertEqual(pins["488"], ACTIVATION_488)

    def test_the_dependency_order_is_topological(self):
        order = [item["commit"] for item in _manifest()["dependency_order"]]
        self.assertEqual(order, [OLD_423, SEAM_460, BATCH_481, ACTIVATION_488])
        for earlier, later in zip(order, order[1:]):
            with self.subTest(earlier=earlier[:9], later=later[:9]):
                self.assertTrue(_is_ancestor(earlier, later))
        for item in _manifest()["dependency_order"]:
            self.assertTrue(item["in_rc"])

    def test_the_rc_composition_order_is_the_actual_chain(self):
        order = _manifest()["rc_composition_order"]
        self.assertEqual(order[0]["commit"], WAVE13)
        self.assertEqual(order[-1]["commit"], CURRENT)
        for item in order[1:-1]:
            with self.subTest(pr=item["pr"]):
                self.assertTrue(_is_ancestor(item["commit"], CURRENT))
        self.assertEqual(
            [item["pr"] for item in order],
            ["#492", "#493", "#488", "#490", "#483", "#494"])

    def test_pending_fixes_are_not_claimed_as_landed(self):
        ids = {item["id"] for item in _manifest()["pending_fixes"]}
        for required in (
            "#490-request-naming", "#493-unconfirmed-settled",
            "#493-ci-device-ref", "#490-493-signature",
            "#488-activation-false-success", "#495-notifier-pin",
        ):
            with self.subTest(required=required):
                self.assertIn(required, ids)
        for item in _manifest()["pending_fixes"]:
            if item["status"] != "PENDING_NO_SHA":
                continue
            self.assertNotIn("commit", item,
                             f"{item['id']} claims a commit it does not have")


class TheDualTrackBoundaryAndReleaseMatrix(unittest.TestCase):
    """#478 keeps Legacy 0.1.0; #425's gates are recorded, not claimed."""

    def test_no_legacy_path_moved(self):
        changed = _git(
            "diff", "--name-only", f"{WAVE13}..{CURRENT}").decode().splitlines()
        offenders = sorted(
            path for path in changed if path.startswith(LEGACY_PREFIXES))
        self.assertEqual(offenders, [])
        self.assertEqual(
            _git("rev-parse", f"{WAVE13}:fleet_mcp").strip(),
            _git("rev-parse", f"{CURRENT}:fleet_mcp").strip())

    def test_483_is_additive(self):
        status = _git(
            "diff", "--name-status",
            "e69fc0337ae5d33000478e3d5162077f525bc8cc.."
            "644ee4a8e35b770426841929d5989bcfad128bef").decode().splitlines()
        self.assertEqual([line for line in status if not line.startswith("A\t")], [])

    def test_v2_roots_are_not_ancestors(self):
        for pr, commit in V2_HEADS.items():
            with self.subTest(pr=pr):
                self.assertFalse(
                    _is_ancestor(commit, CURRENT),
                    f"{pr} {commit} entered the RC; the V2 roots stay out")

    def test_415_time_fix_is_already_in_the_rc(self):
        self.assertTrue(_is_ancestor(LARK_FIX_415, CURRENT))
        self.assertEqual(
            _blob_digest(f"{LARK_FIX_415}:tests/test_direct_lark_notify.py"),
            _blob_digest(f"{CURRENT}:tests/test_direct_lark_notify.py"))
        blocker = next(
            item for item in _manifest()["release_matrix"]["blockers"]
            if item["id"] == "#415-lark-window")
        self.assertEqual(blocker["status"], "RESOLVED_IN_RC")
        self.assertEqual(blocker["main_status"], "OPEN_ON_MAIN")

    def test_the_gate_ci_does_not_claim_5of5(self):
        gate_ci = _manifest()["gate_ci"]
        self.assertFalse(gate_ci["claims_5of5"])
        self.assertEqual(gate_ci["owner"], "#415")
        self.assertIn("direct_lark_notify",
                      gate_ci["known_external_new_failure"])

    def test_the_release_matrix_covers_425_and_478(self):
        matrix = _manifest()["release_matrix"]
        gates = matrix["issue_425"]["gates"]
        for gate in ("A_legacy_control_preserved", "B_next_side_by_side_artifact",
                     "C_agent_monitor_e2e", "D_real_coinstall_im_switch",
                     "E_final_rc_legacy_preserved"):
            with self.subTest(gate=gate):
                self.assertIn(gate, gates)
        self.assertEqual(
            matrix["issue_425"]["hosted_ci_5of5"]["status"], "NOT_MET")
        self.assertEqual(
            matrix["issue_478"]["v2_candidates_not_merged"], [407, 473, 474])
        self.assertTrue(matrix["issue_478"]["legacy_tree_unchanged"])
        self.assertEqual(matrix["issue_478"]["legacy_version"], "0.1.0")
        for blocker in matrix["blockers"]:
            self.assertIn("owner", blocker)
            self.assertIn("status", blocker)
            self.assertIn("evidence", blocker)

    def test_not_run_is_explicit(self):
        not_run = _manifest()["not_run"]
        for token in ("real Legacy/Next co-install", "Timer install",
                      "App1 IM", "main merge"):
            with self.subTest(token=token):
                self.assertIn(token, not_run)


class TheRecordIsADeliverable(unittest.TestCase):
    """The document says what was measured and what was not."""

    def test_the_record_names_the_exact_heads(self):
        source = RECORD.read_text(encoding="utf-8")
        for commit in (CURRENT, OLD_423, SEAM_460, NEWLINE_460,
                       BATCH_481, ACTIVATION_488):
            with self.subTest(commit=commit[:9]):
                self.assertIn(commit[:9], source)

    def test_the_record_states_the_limit_and_the_not_run_list(self):
        source = RECORD.read_text(encoding="utf-8")
        for token in ("4/5", "not 5/5", "NOT_RUN",
                      "real Legacy/Next co-install", "Timer", "App1 IM",
                      "main merge", "not a release", "fleet_mcp",
                      "0.1.0", "#425", "#478", "#495",
                      "#407", "#473", "#474", "owner"):
            with self.subTest(token=token):
                self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
