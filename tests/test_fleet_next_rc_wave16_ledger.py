#!/usr/bin/env python3
"""Wave 16 composition ledger — #494 plus the three P0 fixes, measured on the
tree the composition actually produced.

WHY THIS FILE EXISTS
--------------------
Wave 14 recorded a tree and made every byte claim tree-scoped. Wave 16 adds
three author P0 heads on top of that tree:

  * #497 — a receipt settles a fact, and a CI event gets a typed subject;
  * #498 — the activation wrapper carries the tick's verdict (three commits on
    #488's `009662f`, not one);
  * #499 — the source adapter becomes a source #493 can call.

The same discipline applies: every author head is a merge parent, every file in
each PR's own increment is compared to the author commit's own blob, and the
Wave 12/13/14 historical claims are re-checked **on their own trees**. Wave 16
adds P0 fixes to files Wave 14 recorded, so a HEAD-relative reading of the Wave
14 claim would be false; the Wave 14 ledger is corrected to read its own head
`00503f9` and nothing it recorded is weakened.

WHAT THIS FILE DOES NOT DO
--------------------------
No device, broker, Feishu, model, credential, service or network. It reads the
repository it lives in. It also records one real integration block rather than
disguising it: #498's three runtime-tick tests skip on the author head (no
#493) and fail on the composed tree with #497 (vitals contract and clock
identity). That block is named, with owner handoff, and no test is moved into
a baseline file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BASE = "00503f91b019ce342af52f38b19bb3aa21dfff55"
BASE_TREE = "1c58a45912ef7ebb0b76b7dee7f087262ae62463"

#: The Wave 16 ledger head. Wave 18 legitimately adds P0 fixes to files this
#: wave recorded; every byte claim here belongs to this tree, not to whatever a
#: later wave composes on top of it. Same tree-scoping rule as Wave 14.
WAVE16_HEAD = "80220a4ad1b009946f11bda0ce96dd8ff8e05468"
COMPOSED_SOURCE = "f02fa1688827111698062813eb94d1e86040cfd5"
COMPOSED_SOURCE_TREE = "d45cf478bcf829ae3d851974a1e7da018de58989"

AUTHOR_ANCESTRY = (
    ("#497", "3db8ebf107f83981965cc888269b3b90d89ecb72",
     "4f5320de7e187daadd79adc92e7487facd7b1820",
     "9c85f684f9d14d0612caa2b622518aa529fff82d"),
    ("#498", "0096ba695f6c7bcd35dbb5a82957a1f5631d9058",
     "009662f36a2a5f8e11c51ae517834379a91a7770",
     "af6bd42fbb4387140577751df28416412f31d21f"),
    ("#499", "02027c53f59100fbf43e0ac1f6d84f58561ee91c",
     "06a8be67422f1320e7bdfee38410f8b24d27b501",
     "f02fa1688827111698062813eb94d1e86040cfd5"),
)

#: #498 is a three-commit branch on #488, not a single commit whose parent is
#: `009662f`. Its immediate parent is `f8e1b6f`; the branch base is #488.
AUTHOR_498_COMMITS = (
    "6aefafd075fa1b49dd9f5ce0ccdaeb2ea50696f0",
    "f8e1b6f51613073b36e121de5b86fec87a1b5306",
    "0096ba695f6c7bcd35dbb5a82957a1f5631d9058",
)
AUTHOR_498_PARENT = "f8e1b6f51613073b36e121de5b86fec87a1b5306"

AUTHOR_FILES = {
    "3db8ebf107f83981965cc888269b3b90d89ecb72": (
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/state.py",
        "tests/test_fleet_next_agent_monitor_core.py",
    ),
    "0096ba695f6c7bcd35dbb5a82957a1f5631d9058": (
        "integrations/fleet-next-agent-monitor-activation/activation.py",
        "tests/test_fleet_next_agent_monitor_activation.py",
        "tests/test_fleet_next_agent_monitor_activation_batch.py",
        "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py",
        "tests/test_fleet_next_agent_monitor_activation_truth.py",
    ),
    "02027c53f59100fbf43e0ac1f6d84f58561ee91c": (
        "docs/integration/FLEET-NEXT-SOURCE-ADAPTER.md",
        "integrations/fleet-next-monitor-source-adapter/README.md",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/__init__.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/adapter.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/cli.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/compat.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/contract.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/serializer.py",
        "tests/test_fleet_next_monitor_source_adapter.py",
    ),
}

OWN_FILES = (
    "docs/integration/FLEET-NEXT-RC-WAVE16-20261028.md",
    "integrations/fleet-next-rc-wave16/README.md",
    "integrations/fleet-next-rc-wave16/composition.json",
    "tests/test_fleet_next_rc_wave16_ledger.py",
)

#: The only existing ledger path this wave edits. Its byte claim moves from
#: "the working tree" to "the Wave 14 head"; nothing it recorded is deleted.
MODIFIED_LEDGER = "tests/test_fleet_next_rc_wave14_ledger.py"
WAVE12_LEDGER = "tests/test_fleet_next_rc_wave12_ledger.py"
WAVE13_LEDGER = "tests/test_fleet_next_rc_wave13_ledger.py"
WAVE14_LEDGER = MODIFIED_LEDGER
WAVE12_BLOB = "1265351b07280ae2243f7f1897e58b6b22cdbab4"
WAVE13_BLOB = "6d1be1e88698a1ee64930e80a7af2793b55ec6da"
WAVE14_ORIGINAL_BLOB = "dc54f477fd0ff33304909587e15981731ffdb4f2"

NOTIFIER_PATH = "integrations/fleet-v1-milestone-notify/milestone_notify.py"
NOTIFIER_COMPOSED_BLOB = "7df27cb36b169134ff279e43c0b9c329cf919fef"
SEAM_460 = "4fc9fcbc677f278d10caeb34d55184545810a13a"
LARK_FIX_415 = "dd5919a9256246570e611a7fa86aea28b46ef14d"

V2_HEADS = {
    "#407": "9538238d6a169507ef91366107d8bd3a4d75403d",
    "#473": "a097b27d17c184d3222d982991a90cc04283fea3",
    "#474": "0ad3c078a7d4d865ff7d8c54d01a08235237f8ec",
}

P0_498_FAILURES = (
    "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealRefusalReachesTheSupervisor::test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it",
    "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealVitalsSurviveTheWrapper::test_a_configured_tick_carries_its_vitals_through_the_wrapper",
    "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealVitalsSurviveTheWrapper::test_a_second_process_does_not_inherit_the_first_ones_verdict",
)

LEGACY_PREFIXES = (
    "fleet_mcp/", "runtime/", "services/", "plugin/", "deploy/",
    "serverosd/", "bin/",
)

MANIFEST = (REPO / "integrations" / "fleet-next-rc-wave16" / "composition.json")
RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-RC-WAVE16-20261028.md"


def _git(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args), cwd=REPO, check=True, capture_output=True).stdout


def _blob_digest(rev_path: str) -> str:
    body = _git("cat-file", "-p", rev_path).replace(b"\r\n", b"\n")
    return hashlib.sha256(body).hexdigest()


def _blob_id(rev_path: str) -> str:
    return _git("rev-parse", rev_path).decode().strip()


def _is_ancestor(commit: str, of: str = "HEAD") -> bool:
    return subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, of),
        cwd=REPO).returncode == 0


def _merge_parents(commit: str) -> tuple[str, ...]:
    return tuple(
        _git("rev-list", "--parents", "-n", "1", commit).decode().split()[1:])


def _count_commits(base: str, head: str) -> int:
    return int(_git("rev-list", "--count", f"{base}..{head}").decode().strip())


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TheP0HeadsAreBuiltByAncestry(unittest.TestCase):
    def test_every_author_head_is_a_merge_parent_and_an_ancestor(self):
        for pr, head, base, merge in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr):
                self.assertTrue(_is_ancestor(head))
                self.assertIn(head, _merge_parents(merge))
                self.assertEqual(_git("merge-base", BASE, head).decode().strip(), base)

    def test_498_is_the_three_commit_branch_the_record_names(self):
        """The warning was "parent != #488". It is not a conflict: #498 is a
        three-commit stack whose immediate parent is f8e1b6f and whose branch
        base is #488. The own delta must include all three."""
        self.assertFalse(_is_ancestor(AUTHOR_498_PARENT, "009662f"))
        self.assertEqual(
            _merge_parents(AUTHOR_ANCESTRY[1][1])[0], AUTHOR_498_PARENT)
        self.assertEqual(_count_commits(AUTHOR_ANCESTRY[1][2],
                                        AUTHOR_ANCESTRY[1][1]), 3)
        for commit in AUTHOR_498_COMMITS:
            with self.subTest(commit=commit[:9]):
                self.assertTrue(_is_ancestor(commit))

    def test_the_composed_source_is_the_tree_the_record_names(self):
        self.assertTrue(_is_ancestor(BASE, COMPOSED_SOURCE))
        self.assertEqual(
            _git("rev-parse", f"{BASE}^{{tree}}").decode().strip(), BASE_TREE)
        self.assertEqual(
            _git("rev-parse", f"{COMPOSED_SOURCE}^{{tree}}").decode().strip(),
            COMPOSED_SOURCE_TREE)

    def test_no_v2_root_entered(self):
        for pr, commit in V2_HEADS.items():
            with self.subTest(pr=pr):
                self.assertFalse(_is_ancestor(commit))


class TheManifestNamesEachOwnIncrement(unittest.TestCase):
    def test_author_file_lists_are_re_derived(self):
        manifest = _manifest()
        self.assertEqual(
            [item["pr"] for item in manifest["authors"]],
            [pr for pr, _, _, _ in AUTHOR_ANCESTRY])
        for item in manifest["authors"]:
            pr, head, base, merge = next(
                row for row in AUTHOR_ANCESTRY if row[0] == item["pr"])
            with self.subTest(pr=pr):
                self.assertEqual(item["head"], head)
                self.assertEqual(item["merge_base"], base)
                self.assertEqual(item["merge"], merge)
                recorded = sorted(entry["path"] for entry in item["files"])
                measured = sorted(
                    _git("diff", "--name-only", f"{base}..{head}")
                    .decode().splitlines())
                self.assertEqual(recorded, measured)
                self.assertEqual(recorded, sorted(AUTHOR_FILES[head]))

    def test_every_recorded_file_holds_the_authors_blob(self):
        manifest = _manifest()
        entries = {
            entry["path"]: entry["sha256"]
            for item in manifest["authors"] for entry in item["files"]
        }
        expected = {path for paths in AUTHOR_FILES.values() for path in paths}
        self.assertEqual(set(entries), expected)
        for commit, paths in AUTHOR_FILES.items():
            for relative in paths:
                with self.subTest(author=commit[:9], file=relative):
                    self.assertEqual(
                        _blob_digest(f"{WAVE16_HEAD}:{relative}"),
                        _blob_digest(f"{commit}:{relative}"),
                        f"{relative} did not hold {commit}'s blob on the Wave "
                        "16 head")
                    self.assertEqual(
                        entries[relative],
                        _blob_digest(f"{commit}:{relative}"))

    def test_the_wave16_delta_is_exactly_the_recorded_files(self):
        changed = set(
            _git("diff", "--name-only", f"{BASE}..{WAVE16_HEAD}")
            .decode().splitlines())
        expected = {path for paths in AUTHOR_FILES.values() for path in paths}
        expected |= set(OWN_FILES)
        expected.add(MODIFIED_LEDGER)
        self.assertEqual(changed, expected)

    def test_the_increments_do_not_overlap(self):
        seen: dict[str, str] = {}
        for commit, paths in AUTHOR_FILES.items():
            for relative in paths:
                self.assertNotIn(relative, seen)
                seen[relative] = commit


class TheHistoricalLedgersArePreserved(unittest.TestCase):
    def test_wave12_and_wave13_are_byte_identical(self):
        self.assertEqual(_blob_id(f"HEAD:{WAVE12_LEDGER}"), WAVE12_BLOB)
        self.assertEqual(_blob_id(f"HEAD:{WAVE13_LEDGER}"), WAVE13_BLOB)
        self.assertEqual(_blob_id(f"{BASE}:{WAVE12_LEDGER}"), WAVE12_BLOB)
        self.assertEqual(_blob_id(f"{BASE}:{WAVE13_LEDGER}"), WAVE13_BLOB)

    def test_wave14_is_tree_scoped_to_its_own_head(self):
        source = (REPO / WAVE14_LEDGER).read_text(encoding="utf-8")
        self.assertEqual(_blob_id(f"{BASE}:{WAVE14_LEDGER}"),
                         WAVE14_ORIGINAL_BLOB)
        self.assertIn('WAVE14_HEAD = "00503f91b019ce342af52f38b19bb3aa21dfff55"',
                      source)
        self.assertIn("_wave14_delta", source)
        for token in ("43efd5ce0", "be2c1cccd", "9b7710a26", "3777d134b",
                      "LEGACY_PREFIXES", "fleet_mcp/"):
            with self.subTest(token=token):
                self.assertIn(token, source)
        manifest = _manifest()
        self.assertEqual(
            manifest["history"]["wave14"]["original_blob"],
            WAVE14_ORIGINAL_BLOB)
        self.assertEqual(
            manifest["history"]["wave14"]["modified_blob"],
            _blob_id(f"HEAD:{WAVE14_LEDGER}"))
        self.assertEqual(
            manifest["history"]["wave14"]["historical_head"], BASE)

    def test_the_historical_author_blobs_are_still_on_the_wave14_tree(self):
        historical = (
            "4f5320de7e187daadd79adc92e7487facd7b1820",
            "009662f36a2a5f8e11c51ae517834379a91a7770",
            "06a8be67422f1320e7bdfee38410f8b24d27b501",
            "644ee4a8e35b770426841929d5989bcfad128bef",
        )
        source = (REPO / WAVE14_LEDGER).read_text(encoding="utf-8")
        for commit in historical:
            with self.subTest(commit=commit[:9]):
                self.assertIn(commit, source)
        self.assertIn("test_every_recorded_file_holds_the_authors_own_blob",
                      source)
        self.assertIn("WAVE14_HEAD", source)


class TheNotifierAndLarkFixesAreNotReintroduced(unittest.TestCase):
    def test_the_notifier_is_still_the_composed_seam(self):
        self.assertEqual(_blob_id(f"HEAD:{NOTIFIER_PATH}"),
                         NOTIFIER_COMPOSED_BLOB)
        self.assertTrue(_is_ancestor(SEAM_460))
        tree = _git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
        copies = [p for p in tree if Path(p).name == "milestone_notify.py"]
        self.assertEqual(copies, [NOTIFIER_PATH])

    def test_the_lark_fix_is_still_an_ancestor(self):
        self.assertTrue(_is_ancestor(LARK_FIX_415))
        self.assertEqual(
            _blob_digest(f"{LARK_FIX_415}:tests/test_direct_lark_notify.py"),
            _blob_digest(f"HEAD:tests/test_direct_lark_notify.py"))


class TheLegacyBoundaryIsUntouched(unittest.TestCase):
    def test_no_legacy_path_moved(self):
        changed = _git(
            "diff", "--name-only", f"{BASE}..{WAVE16_HEAD}").decode().splitlines()
        offenders = sorted(
            p for p in changed if p.startswith(LEGACY_PREFIXES))
        self.assertEqual(offenders, [])
        self.assertEqual(_git("rev-parse", f"{BASE}:fleet_mcp").strip(),
                         _git("rev-parse", f"{WAVE16_HEAD}:fleet_mcp").strip())

    def test_483_is_still_additive(self):
        status = _git(
            "diff", "--name-status",
            "e69fc0337ae5d33000478e3d5162077f525bc8cc.."
            "644ee4a8e35b770426841929d5989bcfad128bef").decode().splitlines()
        self.assertEqual([l for l in status if not l.startswith("A\t")], [])

    def test_480_and_483_refusal_tests_are_present(self):
        for path in ("tests/test_fleet_next_parallel_plugin.py",
                     "tests/test_fleet_next_legacy_survival.py"):
            with self.subTest(path=path):
                self.assertTrue((REPO / path).is_file())
        legacy = (REPO / "tests/test_fleet_next_legacy_survival.py").read_text(
            encoding="utf-8")
        for token in ("test_a_shared_plugin_backend_id_is_a_collision",
                      "test_a_collision_is_always_blocked_and_never_renamed_aside",
                      "shadow"):
            with self.subTest(token=token):
                self.assertIn(token, legacy)


class TheP0ReadingAndTheBlockAreRecorded(unittest.TestCase):
    def test_the_manifest_has_exact_p0_readings(self):
        p0 = _manifest()["p0"]
        self.assertEqual(p0["497"]["reading"], "37 passed")
        self.assertEqual(p0["499"]["reading"], "80 passed, 21 subtests passed")
        self.assertEqual(p0["480_483"]["reading"], "86 passed, 11 subtests passed")
        self.assertEqual(p0["498"]["reading"], "3 failed")
        self.assertEqual(tuple(p0["498"]["failures"]), P0_498_FAILURES)
        self.assertEqual(p0["498"]["owner"], "#498 author, with #497 author")
        self.assertEqual(p0["498"]["status"], "BLOCKED")

    def test_the_498_block_has_a_named_cause(self):
        p0 = _manifest()["p0"]["498"]
        self.assertIn("backlog", p0["root_cause"])
        self.assertIn("clock", p0["root_cause"])
        self.assertIn("skip", p0["root_cause"].lower())

    def test_the_compat_view_and_the_request_rename_are_present(self):
        compat = (REPO / "integrations/fleet-next-monitor-source-adapter"
                  / "fleet_next_monitor_source_adapter" / "compat.py")
        self.assertTrue(compat.is_file())
        body = compat.read_text(encoding="utf-8")
        for token in ("CompatMonitorSource", "DENIED", "SHA_MISMATCH"):
            with self.subTest(token=token):
                self.assertIn(token, body)
        serializer = (REPO / "integrations/fleet-next-monitor-source-adapter"
                      / "fleet_next_monitor_source_adapter" / "serializer.py")
        text = serializer.read_text(encoding="utf-8")
        self.assertIn("read_request", text)
        self.assertIn("RETIRED_REQUEST_KEY", text)

    def test_the_governance_reading_excludes_the_two_490_failures(self):
        governance = _manifest()["governance"]
        self.assertIn("3 failed", governance["declared_implementations"])
        self.assertIn("baseline", governance["declared_implementations"])
        self.assertNotIn("490", governance["declared_implementations"])
        self.assertTrue(governance["two_490_failures_gone"])
        self.assertIn("2 failed", governance["loader_identity"])


class TheReleaseMatrixAndNotRunAreExplicit(unittest.TestCase):
    def test_the_matrix_covers_425_478_434(self):
        matrix = _manifest()["release_matrix"]
        for issue in ("issue_425", "issue_478", "issue_434"):
            with self.subTest(issue=issue):
                self.assertIn(issue, matrix)
        self.assertEqual(matrix["issue_425"]["hosted_ci_5of5"]["status"],
                         "NOT_MET")
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
    def test_the_record_names_the_exact_heads_and_readings(self):
        source = RECORD.read_text(encoding="utf-8")
        for commit in (BASE, COMPOSED_SOURCE, "3db8ebf10", "0096ba695",
                       "02027c53f"):
            with self.subTest(commit=commit[:9]):
                self.assertIn(commit[:9], source)
        for token in ("263 passed", "189 subtests", "80 passed",
                      "86 passed", "25 passed", "37 passed", "3 failed",
                      "not 5/5", "BLOCKED", "NOT_RUN",
                      "real Legacy/Next co-install", "Timer", "App1 IM",
                      "main merge", "tree-scoped", "#495", "#496"):
            with self.subTest(token=token):
                self.assertIn(token, source)

    def test_the_record_names_every_498_failure(self):
        source = RECORD.read_text(encoding="utf-8")
        for test_id in P0_498_FAILURES:
            with self.subTest(test=test_id.rsplit("::", 1)[-1]):
                self.assertIn(test_id.rsplit("::", 1)[-1], source)


if __name__ == "__main__":
    unittest.main()
