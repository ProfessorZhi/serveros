#!/usr/bin/env python3
"""Wave 18 composition ledger — #500 plus #503 and #504, measured on the tree
the composition actually produced.

WHY THIS FILE EXISTS
--------------------
Wave 16 named three runtime-tick failures as a #497 × #498 block. Wave 18 adds
the two author fixes by ancestry:

  * #503 — the runtime-tick test measures the tick's clock (one file);
  * #504 — a typed CI subject is not an authorization, and an undecided watch
    is not exit 0 (#501 P0-A/P0-B and the deliveryId-only P1).

The same discipline applies: each author head is a merge parent, each own
increment is compared to the author's blob, and the Wave 12/13/14/16 historical
claims are re-checked on their own trees. Wave 18 adds P0 fixes to files Wave
16 recorded, so the Wave 16 ledger is re-scoped to `80220a4`; nothing it
recorded is deleted.

WHAT THIS FILE DOES NOT DO
--------------------------
No device, broker, Feishu, model, credential, service or network. It reads the
repository it lives in. It does not claim a release artifact, a real device or
production; those are separate Gate Matrix rows.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BASE = "80220a4ad1b009946f11bda0ce96dd8ff8e05468"
BASE_TREE = "42a2f984bb1ab54623c30b8a01a14bc93b729599"
COMPOSED_SOURCE = "1543d1782aba9b01a4e6ec6952fd085eae0c95ac"
COMPOSED_SOURCE_TREE = "5d6d478419294742f8b071cc4f2a119fbf3a4428"

AUTHOR_ANCESTRY = (
    ("#503", "b7f1f6134a1e3dcee4b916068c693ff059c097bd",
     "0096ba695f6c7bcd35dbb5a82957a1f5631d9058",
     "f94b9c71060db684369a5809feffcd6560661070"),
    ("#504", "6286bfbc885771a45415efcb33405bb21a841fde",
     "3db8ebf107f83981965cc888269b3b90d89ecb72",
     "1543d1782aba9b01a4e6ec6952fd085eae0c95ac"),
)

AUTHOR_FILES = {
    "b7f1f6134a1e3dcee4b916068c693ff059c097bd": (
        "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py",
    ),
    "6286bfbc885771a45415efcb33405bb21a841fde": (
        "integrations/fleet-next-agent-monitor/README.md",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/contract.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/sources.py",
        "tests/test_fleet_next_agent_monitor_core.py",
    ),
}

OWN_FILES = (
    "docs/integration/FLEET-NEXT-RC-WAVE18-20261029.md",
    "integrations/fleet-next-rc-wave18/README.md",
    "integrations/fleet-next-rc-wave18/composition.json",
    "tests/test_fleet_next_rc_wave18_ledger.py",
)

MODIFIED_LEDGER = "tests/test_fleet_next_rc_wave16_ledger.py"
WAVE12_LEDGER = "tests/test_fleet_next_rc_wave12_ledger.py"
WAVE13_LEDGER = "tests/test_fleet_next_rc_wave13_ledger.py"
WAVE14_LEDGER = "tests/test_fleet_next_rc_wave14_ledger.py"
WAVE16_LEDGER = MODIFIED_LEDGER
WAVE12_BLOB = "1265351b07280ae2243f7f1897e58b6b22cdbab4"
WAVE13_BLOB = "6d1be1e88698a1ee64930e80a7af2793b55ec6da"
WAVE14_BLOB = "ee4e64a20638982e3bfd90e58ad2d25d60a182e8"
WAVE16_ORIGINAL_BLOB = "c00d4c0bbd1e27b529b5e8b8607c95b965aafb50"
WAVE16_MODIFIED_BLOB = "245679b47bec5a74ccf8e1edbff241a644913236"

NOTIFIER_PATH = "integrations/fleet-v1-milestone-notify/milestone_notify.py"
NOTIFIER_BLOB = "7df27cb36b169134ff279e43c0b9c329cf919fef"
SEAM_460 = "4fc9fcbc677f278d10caeb34d55184545810a13a"
LARK_FIX_415 = "dd5919a9256246570e611a7fa86aea28b46ef14d"

V2_HEADS = {
    "#407": "9538238d6a169507ef91366107d8bd3a4d75403d",
    "#473": "a097b27d17c184d3222d982991a90cc04283fea3",
    "#474": "0ad3c078a7d4d865ff7d8c54d01a08235237f8ec",
}

P0_504_TESTS = (
    "test_a_ci_watch_must_not_deliver_under_an_ungranted_device",
    "test_a_typed_ci_subject_is_not_authorized_by_being_typed",
    "test_an_expired_run_never_leaves_the_tick_at_exit_zero",
    "test_an_unobservable_run_never_leaves_the_tick_at_exit_zero",
    "test_a_delivery_id_only_watch_is_delivered_under_the_native_run_id",
    "test_one_blind_watch_makes_the_whole_tick_unconfirmed",
    "test_the_real_wrapper_never_reports_an_unobserved_watch_as_exit_zero",
)

P0_503_TESTS = (
    "test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it",
    "test_a_configured_tick_carries_its_vitals_through_the_wrapper",
    "test_a_second_process_does_not_inherit_the_first_ones_verdict",
)

LEGACY_PREFIXES = (
    "fleet_mcp/", "runtime/", "services/", "plugin/", "deploy/",
    "serverosd/", "bin/",
)

MANIFEST = (REPO / "integrations" / "fleet-next-rc-wave18" / "composition.json")
RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-RC-WAVE18-20261029.md"


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


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TheTwoFixesAreBuiltByAncestry(unittest.TestCase):
    def test_each_author_head_is_a_merge_parent_and_an_ancestor(self):
        for pr, head, base, merge in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr):
                self.assertTrue(_is_ancestor(head))
                self.assertIn(head, _merge_parents(merge))
                self.assertEqual(
                    _git("merge-base", BASE, head).decode().strip(), base)

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
                        _blob_digest(f"HEAD:{relative}"),
                        _blob_digest(f"{commit}:{relative}"))
                    self.assertEqual(
                        entries[relative],
                        _blob_digest(f"{commit}:{relative}"))

    def test_the_wave18_delta_is_exactly_the_recorded_files(self):
        changed = set(
            _git("diff", "--name-only", f"{BASE}..HEAD")
            .decode().splitlines())
        changed |= set(
            _git("diff", "--name-only", "HEAD").decode().splitlines())
        changed |= set(
            _git("ls-files", "--others", "--exclude-standard")
            .decode().splitlines())
        expected = {path for paths in AUTHOR_FILES.values() for path in paths}
        expected |= set(OWN_FILES)
        expected.add(MODIFIED_LEDGER)
        self.assertEqual({p for p in changed if p}, expected)

    def test_the_increments_do_not_overlap(self):
        seen: dict[str, str] = {}
        for commit, paths in AUTHOR_FILES.items():
            for relative in paths:
                self.assertNotIn(relative, seen)
                seen[relative] = commit


class TheHistoricalLedgersArePreserved(unittest.TestCase):
    def test_wave12_wave13_wave14_are_byte_identical(self):
        self.assertEqual(_blob_id(f"HEAD:{WAVE12_LEDGER}"), WAVE12_BLOB)
        self.assertEqual(_blob_id(f"HEAD:{WAVE13_LEDGER}"), WAVE13_BLOB)
        self.assertEqual(_blob_id(f"HEAD:{WAVE14_LEDGER}"), WAVE14_BLOB)
        self.assertEqual(_blob_id(f"{BASE}:{WAVE12_LEDGER}"), WAVE12_BLOB)
        self.assertEqual(_blob_id(f"{BASE}:{WAVE13_LEDGER}"), WAVE13_BLOB)
        self.assertEqual(_blob_id(f"{BASE}:{WAVE14_LEDGER}"), WAVE14_BLOB)

    def test_wave16_is_tree_scoped_to_its_own_head(self):
        source = (REPO / WAVE16_LEDGER).read_text(encoding="utf-8")
        self.assertEqual(_blob_id(f"{BASE}:{WAVE16_LEDGER}"),
                         WAVE16_ORIGINAL_BLOB)
        self.assertIn('WAVE16_HEAD = "80220a4ad1b009946f11bda0ce96dd8ff8e05468"',
                      source)
        self.assertIn("test_every_recorded_file_holds_the_authors_blob", source)
        manifest = _manifest()
        self.assertEqual(
            manifest["history"]["wave16"]["original_blob"],
            WAVE16_ORIGINAL_BLOB)
        self.assertEqual(
            manifest["history"]["wave16"]["modified_blob"],
            _blob_id(f"HEAD:{WAVE16_LEDGER}"))
        self.assertEqual(
            manifest["history"]["wave16"]["historical_head"], BASE)
        self.assertEqual(
            manifest["history"]["wave16"]["modified_blob"],
            WAVE16_MODIFIED_BLOB)

    def test_the_wave16_author_heads_still_exist(self):
        for commit in ("3db8ebf107f83981965cc888269b3b90d89ecb72",
                       "0096ba695f6c7bcd35dbb5a82957a1f5631d9058",
                       "02027c53f59100fbf43e0ac1f6d84f58561ee91c"):
            with self.subTest(commit=commit[:9]):
                self.assertTrue(_is_ancestor(commit, BASE))


class TheNotifierAndLarkFixesAreNotReintroduced(unittest.TestCase):
    def test_the_notifier_is_still_the_composed_seam(self):
        self.assertEqual(_blob_id(f"HEAD:{NOTIFIER_PATH}"), NOTIFIER_BLOB)
        self.assertTrue(_is_ancestor(SEAM_460))
        tree = _git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
        self.assertEqual([p for p in tree
                          if Path(p).name == "milestone_notify.py"],
                         [NOTIFIER_PATH])

    def test_the_lark_fix_is_still_an_ancestor(self):
        self.assertTrue(_is_ancestor(LARK_FIX_415))
        self.assertEqual(
            _blob_digest(f"{LARK_FIX_415}:tests/test_direct_lark_notify.py"),
            _blob_digest("HEAD:tests/test_direct_lark_notify.py"))


class TheLegacyBoundaryIsUntouched(unittest.TestCase):
    def test_no_legacy_path_moved(self):
        changed = _git(
            "diff", "--name-only", f"{BASE}..HEAD").decode().splitlines()
        self.assertEqual(
            sorted(p for p in changed if p.startswith(LEGACY_PREFIXES)), [])
        self.assertEqual(_git("rev-parse", f"{BASE}:fleet_mcp").strip(),
                         _git("rev-parse", "HEAD:fleet_mcp").strip())

    def test_483_is_still_additive(self):
        status = _git(
            "diff", "--name-status",
            "e69fc0337ae5d33000478e3d5162077f525bc8cc.."
            "644ee4a8e35b770426841929d5989bcfad128bef").decode().splitlines()
        self.assertEqual([l for l in status if not l.startswith("A\t")], [])


class TheP0ReadingIsRecorded(unittest.TestCase):
    def test_the_manifest_has_exact_readings(self):
        p0 = _manifest()["p0"]
        self.assertEqual(p0["503"]["reading"], "3 passed")
        self.assertEqual(p0["504"]["reading"],
                         "52 passed, 4 subtests passed")
        self.assertEqual(p0["499"]["reading"],
                         "80 passed, 21 subtests passed")
        self.assertEqual(p0["480_483"]["reading"],
                         "86 passed, 11 subtests passed")
        self.assertEqual(p0["historical_ledgers"],
                         "70 passed, 196 subtests passed")
        self.assertEqual(p0["targeted"], "287 passed, 269 subtests passed")

    def test_the_503_and_504_scenarios_are_present(self):
        runtime = (REPO / "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py").read_text(encoding="utf-8")
        for name in P0_503_TESTS:
            with self.subTest(test=name):
                self.assertIn(name, runtime)
        core = (REPO / "tests/test_fleet_next_agent_monitor_core.py").read_text(encoding="utf-8")
        for name in P0_504_TESTS:
            with self.subTest(test=name):
                self.assertIn(name, core)

    def test_the_declared_reader_failures_stay_gone(self):
        governance = _manifest()["governance"]
        self.assertIn("3 failed", governance["declared_implementations"])
        self.assertTrue(governance["two_490_failures_gone"])
        self.assertNotIn("490", governance["declared_implementations"])


class TheGateMatrixAndNotRunAreExplicit(unittest.TestCase):
    def test_the_six_gate_rows_are_separate(self):
        matrix = _manifest()["gate_matrix"]
        for row in ("CODE", "UNIT", "CI", "RELEASE_ARTIFACT",
                    "REAL_DEVICE", "PRODUCTION"):
            with self.subTest(row=row):
                self.assertIn(row, matrix)
                self.assertIn("status", matrix[row])
        self.assertEqual(matrix["CODE"]["status"], "PASS")
        self.assertEqual(matrix["UNIT"]["status"], "PASS_TARGETED")
        self.assertEqual(matrix["RELEASE_ARTIFACT"]["status"], "NOT_RUN")
        self.assertEqual(matrix["REAL_DEVICE"]["status"], "NOT_RUN")
        self.assertEqual(matrix["PRODUCTION"]["status"], "NOT_RUN")
        self.assertFalse(matrix["CI"]["claims_5of5"])

    def test_the_release_matrix_covers_425_478_434(self):
        matrix = _manifest()["release_matrix"]
        for issue in ("issue_425", "issue_478", "issue_434"):
            with self.subTest(issue=issue):
                self.assertIn(issue, matrix)
        self.assertTrue(matrix["issue_478"]["legacy_tree_unchanged"])
        self.assertEqual(matrix["issue_478"]["legacy_version"], "0.1.0")
        self.assertEqual(matrix["issue_425"]["hosted_ci_5of5"]["status"],
                         "TARGET")

    def test_not_run_is_explicit(self):
        not_run = _manifest()["not_run"]
        for token in ("real Legacy/Next co-install", "Timer install",
                      "App1 IM", "main merge"):
            with self.subTest(token=token):
                self.assertIn(token, not_run)


class TheRecordIsADeliverable(unittest.TestCase):
    def test_the_record_names_the_exact_heads_and_readings(self):
        source = RECORD.read_text(encoding="utf-8")
        for commit in (BASE, COMPOSED_SOURCE, "b7f1f6134", "6286bfbc8"):
            with self.subTest(commit=commit[:9]):
                self.assertIn(commit[:9], source)
        for token in ("52 passed", "3 passed", "80 passed", "86 passed",
                      "70 passed", "287 passed", "269 subtests",
                      "not 5/5", "BLOCKED", "NOT_RUN",
                      "real Legacy/Next co-install", "Timer", "App1 IM",
                      "main merge", "tree-scoped", "RELEASE_ARTIFACT",
                      "REAL_DEVICE", "PRODUCTION"):
            with self.subTest(token=token):
                self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
