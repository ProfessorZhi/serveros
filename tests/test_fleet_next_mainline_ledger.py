#!/usr/bin/env python3
"""W19 mainline ledger — the PR DAG, the one landed narrow batch, and the
boundaries that stay blocked.

WHAT THIS PROVES
----------------
* main moved from `3bc7adf` to `99c06fa` by merging **#520** at exact head
  `90b6671`; the merge commit's parents are the old main and that head.
* The landed delta is exactly **47 pure additions**, byte-identical to
  #507 tree `05da04f`, all inside the #517 QA allowlist, with zero forbidden
  paths and an unchanged `fleet_mcp` tree.
* #517's independent `landing_check.py` returned **SIGNED_PASS** for this
  exact head; hosted PR CI `40805524991` and main push CI `40806145632` are
  both 5/5; no baseline entry was added.
* #507/#508 were **not** merged wholesale; #407/#473/#474 stay
  `NO DIRECT MERGE`; #509/#506/#514 stay blocked.

The gate is a per-shard baseline gate, not a whole-repository zero-failure
claim. It reads git objects and the JSON ledger; no network is used unless a
pinned PR head is absent from a narrow checkout.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DAG = REPO / "integrations" / "fleet-next-mainline" / "dag.json"
RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-MAINLINE-20261029.md"

MAIN_BEFORE = "3bc7adfe40274741cc528c7dd96167d2b67b00ec"
MAIN_MID = "99c06fab07bd301e7df1ff7fa253f670b4d51d4d"
MAIN_AFTER = "b27c93e346d5503a75f95ec068c386b416841a62"
MAIN_TREE_MID = "c7ca9cc37ded86351df660ff11103ce0dec938a1"
MAIN_TREE_AFTER = "206a4f4583162204cc5172e928f7ab243da73eaf"
LAND_HEAD = "90b6671c9bfecd31d72163e2f5a86d68f991cff9"
LAND2_HEAD = "cd8b875b0722718b31750da9beee1c4df072b415"
RC_TREE = "05da04f8c78f302b029c6304ed96f4e8ded5fc9b"
QA_HEAD = "823a38198322c90c30381758befd4e208ba85c2a"

ALLOWED = (
    "integrations/fleet-next-agent-monitor",
    "integrations/fleet-next-agent-monitor-activation",
    "integrations/fleet-next-monitor-source-adapter",
    "integrations/fleet-v1-milestone-notify",
    "integrations/fleet-v1-milestone-batch",
    "integrations/fleet-v1-milestone-observer",
    "integrations/fleet-v1-milestone-runner",
    "integrations/fleet-v1-milestone-sources",
)
RUNTIME_TEST = "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py"
FORBIDDEN = (
    "fleet_mcp/", "runtime/", "plugin/", "plugins/", "serverosd/", "native/",
    "fleet/", "scripts/", ".github/", ".claude-plugin/", "fleet_hub/",
    "context/", "abilities/", "mcp/", "skills/", "deploy/", "services/",
    "bin/", "workengine/", "tests/hermetic-known-failures.txt",
)

PINNED_PRS = {
    520: LAND_HEAD,
    519: LAND2_HEAD,
    524: "3e6a4ee1b20b853c9df71c3889bdd7eb9140966d",
    507: RC_TREE,
    508: "fb8c435637e486fc0ecf78a2ea83b9b323c936bb",
    517: QA_HEAD,
    407: "9538238d6a169507ef91366107d8bd3a4d75403d",
    473: "a097b27d17c184d3222d982991a90cc04283fea3",
    474: "0ad3c078a7d4d865ff7d8c54d01a08235237f8ec",
    431: "517ef915074e3b14537b746a07b4dcee63eccfb6",
    505: "3512c551574a94ad360f8bde6435bdf07c729167",
    509: "3520d0c886a3abe0ba712eeb855ca2bcdd65579d",
    506: "30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7",
    513: "6602961df350598c95f2c55abceddf8e3509e5ac",
    510: "ea779d9a47bec64dddcd78d3a2da5862998d795d",
    511: "6b98412d43a7e3c36e4dc8f0aa486aff429c81ac",
    512: "d3c7c391dc63caa53eb35cf34f318dee23ed2ac1",
    514: "30c4581ac8989b26cb5a065b49bc5a868e48d57d",
    516: "15d82ab32fbf210db3acd39db07308744ebf90a0",
    519: "cd8b875b0722718b31750da9beee1c4df072b415",
    518: "d7045619458e50b502c66f7dc42ef75ebcd94436",
}


def _git(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args), cwd=REPO, check=True, capture_output=True).stdout


def _text(*args: str) -> str:
    return _git(*args).decode().strip()


def _blob_digest(rev_path: str) -> str:
    body = _git("cat-file", "-p", rev_path).replace(b"\r\n", b"\n")
    return hashlib.sha256(body).hexdigest()


def _is_ancestor(commit: str, of: str = "HEAD") -> bool:
    return subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, of),
        cwd=REPO, capture_output=True).returncode == 0


def _merge_parents(commit: str) -> tuple[str, ...]:
    return tuple(_git("rev-list", "--parents", "-n", "1", commit)
                 .decode().split()[1:])


def _has_commit(sha: str) -> bool:
    return subprocess.run(
        ("git", "cat-file", "-e", f"{sha}^{{commit}}"),
        cwd=REPO, capture_output=True).returncode == 0


def _ensure_main_history() -> None:
    """A depth-1 PR checkout has the merge tree, not main's parents.

    Deepen the main ref first; the landed merge, its old-main parent and its
    landing head are then readable. A missing commit is a named failure, never
    a skip.
    """
    required = (MAIN_BEFORE, MAIN_MID, MAIN_AFTER, LAND_HEAD, LAND2_HEAD)
    if all(_has_commit(sha) for sha in required):
        return
    done = subprocess.run(
        ("git", "fetch", "--no-tags", "--deepen=100", "origin", "main"),
        cwd=REPO, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise AssertionError(
            f"cannot deepen main history: {done.stderr.strip()[-300:]}")
    for sha in required:
        if not _has_commit(sha):
            done = subprocess.run(
                ("git", "fetch", "--no-tags", "origin", sha),
                cwd=REPO, capture_output=True, text=True, timeout=300)
            if done.returncode != 0 or not _has_commit(sha):
                raise AssertionError(
                    f"main history lacks {sha} after deepen; "
                    f"{done.stderr.strip()[-300:]}")


def _ensure_pins() -> None:
    for pr, sha in PINNED_PRS.items():
        if _has_commit(sha):
            continue
        done = subprocess.run(
            ("git", "fetch", "--no-tags", "origin", f"refs/pull/{pr}/head"),
            cwd=REPO, capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            raise AssertionError(
                f"PR #{pr} {sha} is absent and cannot be fetched: "
                f"{done.stderr.strip()[-300:]}")
        got = _text("rev-parse", "FETCH_HEAD")
        if got != sha:
            raise AssertionError(
                f"PR #{pr} moved: {got} != pinned {sha}")


def setUpModule() -> None:  # noqa: N802 - unittest protocol
    _ensure_main_history()
    _ensure_pins()


def _dag() -> dict:
    return json.loads(DAG.read_text(encoding="utf-8"))


def _allowed(path: str) -> bool:
    if path == RUNTIME_TEST:
        return True
    return any(path == d or path.startswith(d + "/") for d in ALLOWED)


def _forbidden(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in FORBIDDEN)


def _landed2_allowed(path: str) -> bool:
    return (path.startswith("tests/") or path.startswith("docs/integration/")
            or path == "integrations/chatgpt-fleet-next/batch_contract.py")


class TheMergeIsTheExpectedShape(unittest.TestCase):
    def test_main_moved_by_two_landings(self):
        self.assertTrue(_is_ancestor(MAIN_BEFORE, MAIN_MID))
        self.assertTrue(_is_ancestor(MAIN_MID, MAIN_AFTER))
        self.assertEqual(set(_merge_parents(MAIN_MID)),
                         {MAIN_BEFORE, LAND_HEAD})
        self.assertEqual(set(_merge_parents(MAIN_AFTER)),
                         {MAIN_MID, LAND2_HEAD})
        self.assertEqual(_text("rev-parse", f"{MAIN_MID}^{{tree}}"),
                         MAIN_TREE_MID)
        self.assertEqual(_text("rev-parse", f"{MAIN_AFTER}^{{tree}}"),
                         MAIN_TREE_AFTER)

    def test_both_landed_deltas_are_pure_additions(self):
        first = _text("diff", "--name-status",
                      f"{MAIN_BEFORE}..{MAIN_MID}").splitlines()
        second = _text("diff", "--name-status",
                       f"{MAIN_MID}..{MAIN_AFTER}").splitlines()
        self.assertEqual(len(first), 47)
        self.assertEqual(len(second), 20)
        self.assertTrue(all(line.startswith("A\t") for line in first + second))
        paths1 = [line.split("\t", 1)[1] for line in first]
        paths2 = [line.split("\t", 1)[1] for line in second]
        self.assertEqual(sorted(paths1), sorted(_dag()["landed_files"]))
        self.assertEqual(sorted(paths2), sorted(_dag()["landed2_files"]))
        self.assertEqual([p for p in paths1 + paths2 if _forbidden(p)], [])
        self.assertTrue(all(_allowed(p) for p in paths1))
        self.assertTrue(all(_landed2_allowed(p) for p in paths2))

    def test_every_landed_blob_matches_its_source_tree(self):
        for path in _dag()["landed_files"]:
            with self.subTest(path=path):
                self.assertEqual(_blob_digest(f"{MAIN_MID}:{path}"),
                                 _blob_digest(f"{RC_TREE}:{path}"))
        for path in _dag()["landed2_files"]:
            with self.subTest(path=path):
                self.assertEqual(_blob_digest(f"{MAIN_AFTER}:{path}"),
                                 _blob_digest(f"{LAND2_HEAD}:{path}"))

    def test_the_legacy_tree_and_baseline_are_untouched(self):
        self.assertEqual(_text("rev-parse", f"{MAIN_BEFORE}:fleet_mcp"),
                         _text("rev-parse", f"{MAIN_AFTER}:fleet_mcp"))
        self.assertEqual(
            _text("diff", "--name-only", f"{MAIN_BEFORE}..{MAIN_AFTER}",
                  "--", "tests/hermetic-known-failures.txt"), "")


class TheLandedRecordCarriesItsGates(unittest.TestCase):
    def test_the_two_landed_records_are_exact(self):
        first, second = _dag()["landed"][0], _dag()["landed"][1]
        self.assertEqual(first["pr"], 520)
        self.assertEqual(first["head"], LAND_HEAD)
        self.assertEqual(first["merge"], MAIN_MID)
        self.assertEqual(first["files"], 47)
        self.assertEqual(first["pr_ci"]["run"], "40805524991")
        self.assertEqual(first["qa"]["review"]["comment_id"], 6486841401)
        self.assertEqual(first["qa"]["review"]["verdict"], "SIGNED_PASS")
        self.assertEqual(first["main_push_ci"]["run"], "40806145632")
        self.assertEqual(second["pr"], 519)
        self.assertEqual(second["head"], LAND2_HEAD)
        self.assertEqual(second["merge"], MAIN_AFTER)
        self.assertEqual(second["files"], 67)
        self.assertEqual(second["merged_additions"], 20)
        self.assertEqual(second["pr_ci"]["run"], "40810813359")
        qa = second["qa"]
        self.assertEqual(qa["pr"], 524)
        self.assertEqual(qa["head"], PINNED_PRS[524])
        self.assertEqual(qa["review_url"],
                         "https://github.com/ProfessorZhi/serveros/pull/524")
        self.assertEqual(
            qa["evidence_path"],
            "integrations/fleet-next-w19-release-acceptance/evidence/"
            "w19-acceptance-20261029.json")
        self.assertEqual(
            qa["evidence_blob"],
            _text("rev-parse", f"{PINNED_PRS[524]}:{qa['evidence_path']}"))
        self.assertEqual(
            qa["evidence_sha256"],
            _blob_digest(f"{PINNED_PRS[524]}:{qa['evidence_path']}"))
        self.assertNotIn("review_comment_id", qa)
        self.assertEqual(qa["verdict"], "LANDING_READY")
        self.assertEqual(second["main_push_ci"]["run"], "40811251506")
        self.assertEqual(first["legacy_delta"], [])
        self.assertFalse(first["baseline_added"])
        self.assertFalse(second["baseline_added"])

    def test_the_qa_gate_head_is_present(self):
        self.assertTrue(_has_commit(QA_HEAD))
        self.assertFalse(_is_ancestor(QA_HEAD, MAIN_AFTER))


class TheDagClassifiesEveryCandidate(unittest.TestCase):
    def test_key_classifications(self):
        prs = {item["pr"]: item for item in _dag()["prs"]}
        expected = {
            415: "merged_canonical", 483: "merged_canonical",
            520: "merged_landing", 519: "merged_landing",
            516: "merged_via_successor",
            507: "canonical_source_blocked_wholesale",
            508: "qa_composition_extract_only",
            517: "independent_qa_gate",
            521: "ready_pending", 523: "ready_pending",
            423: "closed_superseded", 449: "closed_superseded",
            500: "closed_superseded",
            509: "release_artifact_blocked", 506: "sidecar_blocked",
            514: "release_acceptance_blocked",
            407: "no_direct_merge", 473: "no_direct_merge",
            474: "no_direct_merge",
        }
        for pr, classification in expected.items():
            with self.subTest(pr=pr):
                self.assertEqual(prs[pr]["classification"], classification)

    def test_wholesale_branches_are_not_in_main(self):
        for sha in (RC_TREE, PINNED_PRS[508], PINNED_PRS[407],
                    PINNED_PRS[473], PINNED_PRS[474]):
            with self.subTest(sha=sha[:9]):
                self.assertFalse(_is_ancestor(sha, MAIN_AFTER))

    def test_close_state_is_recorded(self):
        close = {item["pr"]: item for item in _dag()["close_candidates"]}
        self.assertEqual(close[516]["status"], "merged via #519")
        self.assertEqual(close[519]["status"], "merged landing")
        self.assertEqual(close[415]["status"], "already merged")
        self.assertEqual(close[483]["status"], "already merged")
        self.assertEqual(close[520]["status"], "already merged")
        self.assertEqual(close[521]["status"], "keep open")
        self.assertEqual(close[523]["status"], "keep open")
        for pr in (509, 506, 514, 407, 473, 474):
            self.assertEqual(close[pr]["status"], "keep open")
        for pr in (423, 449, 500):
            self.assertEqual(close[pr]["status"], "closed superseded")
        self.assertEqual(len(_dag()["closeout_w19c"]["closed"]), 24)
        self.assertEqual(_dag()["closeout_w19c"]["open_before"], 90)
        self.assertEqual(_dag()["closeout_w19c"]["open_after"], 79)

    def test_path_dag_marks_landed_and_forbidden_paths(self):
        paths = _dag()["paths"]
        landed = [p for p in paths
                  if p["pr"] == 520 and p["path"] in _dag()["landed_files"]]
        self.assertEqual(len(landed), 47)
        self.assertTrue(all(p["classification"] == "landed_next_only"
                            for p in landed))
        forbidden = [p for p in paths
                     if p["classification"] == "legacy_v2_forbidden"]
        self.assertGreater(len(forbidden), 0)


class TheGateMatrixAndDocsPlanAreExplicit(unittest.TestCase):
    def test_six_rows_are_separate(self):
        matrix = _dag()["gate_matrix"]
        for row in ("CODE", "UNIT", "CI", "RELEASE_ARTIFACT",
                    "REAL_DEVICE", "PRODUCTION"):
            with self.subTest(row=row):
                self.assertIn(row, matrix)
        self.assertEqual(matrix["CODE"]["status"], "PASS")
        self.assertEqual(matrix["UNIT"]["status"], "PASS_TARGETED")
        self.assertEqual(matrix["CI"]["status"], "PASS")
        self.assertEqual(matrix["RELEASE_ARTIFACT"]["status"], "BLOCKED")
        self.assertEqual(matrix["REAL_DEVICE"]["status"], "NOT_RUN")
        self.assertEqual(matrix["PRODUCTION"]["status"], "NOT_RUN")

    def test_docs_index_is_non_destructive(self):
        index = _dag()["docs_index"]
        for key in ("architecture", "operations", "verification", "history"):
            with self.subTest(key=key):
                self.assertEqual(index[key]["path"], f"docs/fleet-next/{key}")
        self.assertFalse(index["physical_moves"])
        self.assertFalse(index["import_changes"])
        self.assertIn("retained", index["history"]["mode"])

    def test_not_run_is_explicit(self):
        not_run = _dag()["not_run"]
        for token in ("real Legacy/Next co-install", "Timer install",
                      "App1 IM", "release artifact", "real install"):
            with self.subTest(token=token):
                self.assertIn(token, not_run)


class TheRecordIsADeliverable(unittest.TestCase):
    def test_the_record_names_the_merge_and_the_boundaries(self):
        source = RECORD.read_text(encoding="utf-8")
        for token in (MAIN_BEFORE[:9], MAIN_MID[:9], MAIN_AFTER[:9],
                      LAND_HEAD[:9], LAND2_HEAD[:9], "6486841401",
                      "SIGNED_PASS", "47", "20", "0 forbidden",
                      "40805524991", "40810813359", "40811251506",
                      "BLOCKED", "NOT_RUN", "RELEASE_ARTIFACT",
                      "REAL_DEVICE", "PRODUCTION", "NO DIRECT MERGE",
                      "docs/fleet-next", "24", "79"):
            with self.subTest(token=token):
                self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
