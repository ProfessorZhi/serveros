#!/usr/bin/env python3
"""Wave 14 composition ledger — #492's RC plus the four Monitor/Legacy heads,
measured on the tree the composition actually produced.

WHY THIS FILE EXISTS
--------------------
Wave 13's ledger made the claim *tree-scoped*: a per-file digest belongs to the
tree it was measured on, and a later author change is a new fact, not a reason
to rewrite the old number. Wave 14 adds four author heads (#493, #488, #490,
#483) to that tree. The same discipline applies, with two additions:

* #423's notifier is already an ancestor of the Wave 13 base. It must stay
  present **once**; this composition may not import a second copy of its
  source, and the Monitor's `notifier.py` must remain an adapter (no sqlite, no
  second ledger).
* every author head must be a **parent** of the merge that carries it, and
  every file in each PR's own increment is compared to the author commit's own
  blob. A cherry-pick that reproduces the bytes fails the ancestry claim; a
  hand edit that "fixes" a failing assertion fails the blob claim.

WHAT THIS FILE DOES NOT DO
--------------------------
No device, broker, Feishu, model, credential, service or network. It reads the
repository it lives in. The one subprocess it starts imports the four new
source packages with `socket` disabled, which is the offline-packaging claim
stated in the record. LAYERS: CODE (bytes and ancestry) / UNIT (unittest).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: The Wave 13 RC the four heads are composed onto. Hosted CI for this exact
#: base was run 40662311688, 5/5 jobs.
BASE = "a00fabc87fff65aeb50a851536b3314be8399090"
BASE_TREE = "d84aef2566572353001ec50a60d33ac95c656281"

#: The composed source commit, before this ledger was added. It is a merge
#: whose second parent is #483's head; the other author heads are parents of
#: earlier merges in its first-parent chain.
COMPOSED_SOURCE = "7ff573db7300867fd37b673a59b0101e7f87dab6"
COMPOSED_SOURCE_TREE = "4f28f4ef78e47790f874fe046a457c2d3defc7d0"

#: (PR, author head, merge base used to derive the PR's own increment, merge
#: commit on this branch). The merge bases are measured, not assumed: #488 is
#: based on #481's `9b7710a`, the other three on `main`'s `e69fc03`, where
#: neither `9b7710a` nor `e69fc03` adds files this composition does.
AUTHOR_ANCESTRY = (
    ("#493", "4f5320de7e187daadd79adc92e7487facd7b1820",
     "e69fc0337ae5d33000478e3d5162077f525bc8cc",
     "b23cda6bbecff7ecbb0636beb56b491de3061cd3"),
    ("#488", "009662f36a2a5f8e11c51ae517834379a91a7770",
     "9b7710a26180e1e6d00ce597827d447d0d2348b7",
     "efde75022a308680b3920277401517cd312e3c4b"),
    ("#490", "06a8be67422f1320e7bdfee38410f8b24d27b501",
     "e69fc0337ae5d33000478e3d5162077f525bc8cc",
     "a5e55b93a97e17e97853e2be2c3715526f9ed226"),
    ("#483", "644ee4a8e35b770426841929d5989bcfad128bef",
     "e69fc0337ae5d33000478e3d5162077f525bc8cc",
     "7ff573db7300867fd37b673a59b0101e7f87dab6"),
)

#: Each PR's own increment, pinned here so a manifest edit alone cannot weaken
#: the claim. They are re-derived from the author commits below.
AUTHOR_FILES = {
    "4f5320de7e187daadd79adc92e7487facd7b1820": (
        "integrations/fleet-next-agent-monitor/README.md",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/__init__.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/__main__.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/contract.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/offline.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/runtime.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/sources.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/state.py",
        "tests/test_fleet_next_agent_monitor_core.py",
    ),
    "009662f36a2a5f8e11c51ae517834379a91a7770": (
        "docs/integration/FLEET-NEXT-AGENT-MONITOR-ACTIVATION.md",
        "integrations/fleet-next-agent-monitor-activation/activation.py",
        "integrations/fleet-next-agent-monitor-activation/batch_adapter.py",
        "integrations/fleet-next-agent-monitor-activation/serveros-next-agent-monitor.service.template",
        "tests/test_fleet_next_agent_monitor_activation.py",
        "tests/test_fleet_next_agent_monitor_activation_batch.py",
    ),
    "06a8be67422f1320e7bdfee38410f8b24d27b501": (
        "docs/integration/FLEET-NEXT-SOURCE-ADAPTER.md",
        "integrations/fleet-next-monitor-source-adapter/README.md",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/__init__.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/adapter.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/cli.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/contract.py",
        "integrations/fleet-next-monitor-source-adapter/fleet_next_monitor_source_adapter/serializer.py",
        "tests/test_fleet_next_monitor_source_adapter.py",
    ),
    "644ee4a8e35b770426841929d5989bcfad128bef": (
        "docs/integration/FLEET-NEXT-LEGACY-SAFETY.md",
        "integrations/fleet-next-legacy-safety/README.md",
        "integrations/fleet-next-legacy-safety/legacy_safety.py",
        "tests/test_fleet_next_legacy_survival.py",
    ),
}

#: #423's single notifier. It is an ancestor of BASE, so this composition must
#: carry it unchanged and must not import it a second time.
NOTIFIER_PR = "#423"
NOTIFIER_HEAD = "d9b2948a62609e16007f2fe0ae288f9652d64cd1"
NOTIFIER_PATH = "integrations/fleet-v1-milestone-notify/milestone_notify.py"

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

#: The files this ledger itself adds. The composition delta must be exactly the
#: author files above plus these, nothing else.
OWN_FILES = (
    "docs/integration/FLEET-NEXT-RC-WAVE14-20261028.md",
    "integrations/fleet-next-rc-wave14/README.md",
    "integrations/fleet-next-rc-wave14/composition.json",
    "tests/test_fleet_next_rc_wave14_ledger.py",
)

MANIFEST = REPO / "integrations" / "fleet-next-rc-wave14" / "composition.json"
RECORD = REPO / "docs" / "integration" / "FLEET-NEXT-RC-WAVE14-20261028.md"
WAVE13_LEDGER = "tests/test_fleet_next_rc_wave13_ledger.py"
WAVE12_LEDGER = "tests/test_fleet_next_rc_wave12_ledger.py"

#: Imports that would make the offline-packaging claim false. `urllib.parse`
#: is deliberately not on this list: it is a parser, not a transport.
NETWORK_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:socket|ssl|requests|httpx|aiohttp|"
    r"http\.client|urllib\.request)\b",
    re.MULTILINE,
)


def _git(*args: str) -> bytes:
    return subprocess.run(
        ("git", *args), cwd=REPO, check=True, capture_output=True
    ).stdout


def _blob(rev_path: str) -> bytes:
    return _git("cat-file", "-p", rev_path).replace(b"\r\n", b"\n")


def _blob_digest(rev_path: str) -> str:
    return hashlib.sha256(_blob(rev_path)).hexdigest()


def _digest(path: Path) -> str:
    return hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()


def _is_ancestor(commit: str, of: str = "HEAD") -> bool:
    return subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, of),
        cwd=REPO,
    ).returncode == 0


def _merge_parents(commit: str) -> tuple[str, ...]:
    line = _git("rev-list", "--parents", "-n", "1", commit).decode().split()
    return tuple(line[1:])


def _committed_delta() -> set[str]:
    return set(
        _git("diff", "--name-only", f"{BASE}..HEAD").decode().splitlines()
    )


def _working_delta() -> set[str]:
    """Committed composition delta, plus the ledger files before they are
    committed, so RED/GREEN can be read on the same working tree."""
    changed = _committed_delta()
    changed |= set(_git("diff", "--name-only", "HEAD").decode().splitlines())
    changed |= set(
        _git("ls-files", "--others", "--exclude-standard").decode().splitlines()
    )
    return {path for path in changed if path}


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


class TheCompositionIsBuiltByAncestry(unittest.TestCase):
    """The authors' commits are parents of the result, not re-applied hunks."""

    def test_every_author_head_is_an_ancestor_of_the_rc(self):
        """**Turns red when** the composition is rebuilt by cherry-pick: the
        bytes can be identical and the ancestry is gone."""
        for pr, commit, _, _ in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr, commit=commit[:9]):
                self.assertTrue(
                    _is_ancestor(commit),
                    f"{pr} {commit} is not an ancestor of HEAD — the tree may "
                    "hold the same bytes, but it is not this composition")

    def test_every_recorded_merge_carries_its_author_as_a_parent(self):
        """A merge commit on this branch names the author head as a parent.
        That is the mechanical difference between a merge and a copy."""
        for pr, commit, _, merge in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr, merge=merge[:9]):
                parents = _merge_parents(merge)
                self.assertIn(
                    commit, parents,
                    f"{merge} does not carry {pr}'s head {commit} as a parent")
                self.assertTrue(
                    _is_ancestor(merge),
                    f"{merge} is not an ancestor of HEAD — the recorded merge "
                    "belongs to a different history")
                self.assertTrue(
                    _is_ancestor(merge, COMPOSED_SOURCE),
                    f"{merge} is not on the path to {COMPOSED_SOURCE[:9]}")

    def test_the_composed_source_is_the_tree_the_record_names(self):
        self.assertTrue(_is_ancestor(BASE, COMPOSED_SOURCE))
        self.assertEqual(
            _git("rev-parse", f"{BASE}^{{tree}}").strip().decode(), BASE_TREE)
        self.assertEqual(
            _git("rev-parse", f"{COMPOSED_SOURCE}^{{tree}}").strip().decode(),
            COMPOSED_SOURCE_TREE)
        self.assertTrue(_is_ancestor(COMPOSED_SOURCE))

    def test_the_rc_is_not_a_qa_lane(self):
        """The independent QA lane gets its own tree. Using it as the RC would
        make the RC's evidence the QA's evidence."""
        for relative in (
            "integrations/fleet-next-rc-wave14-qa",
            "tests/test_fleet_next_rc_wave14_compose.py",
        ):
            with self.subTest(path=relative):
                self.assertFalse(
                    (REPO / relative).exists(),
                    f"{relative} belongs to the independent QA lane and must "
                    "not be part of the RC")


class TheManifestNamesEachAuthorsOwnIncrement(unittest.TestCase):
    """Per PR, per file: the manifest and the author commit agree."""

    def test_the_manifest_has_the_expected_shape(self):
        manifest = _manifest()
        self.assertEqual(
            manifest["schema"],
            "serveros/fleet-next-rc-wave14-composition.v1")
        self.assertEqual(manifest["base"]["commit"], BASE)
        self.assertEqual(manifest["base"]["tree"], BASE_TREE)
        self.assertEqual(manifest["composed_source"]["commit"], COMPOSED_SOURCE)
        self.assertEqual(
            manifest["composed_source"]["tree"], COMPOSED_SOURCE_TREE)
        self.assertEqual(
            [item["pr"] for item in manifest["authors"]],
            [pr for pr, _, _, _ in AUTHOR_ANCESTRY])

    def test_each_authors_file_list_is_re_derived_from_git(self):
        """**Turns red when** a manifest line is edited without the tree, or a
        file is smuggled into a PR's increment."""
        manifest = _manifest()
        for item in manifest["authors"]:
            pr, commit, merge_base, _ = next(
                row for row in AUTHOR_ANCESTRY if row[0] == item["pr"])
            with self.subTest(pr=pr):
                self.assertEqual(item["commit"], commit)
                self.assertEqual(item["merge_base"], merge_base)
                recorded = sorted(entry["path"] for entry in item["files"])
                measured = sorted(
                    _git("diff", "--name-only", f"{merge_base}..{commit}")
                    .decode().splitlines())
                self.assertEqual(recorded, measured,
                                 f"{pr}'s recorded increment is not its delta")
                self.assertEqual(
                    recorded, sorted(AUTHOR_FILES[commit]),
                    f"{pr}'s increment changed; re-derive the composition")

    def test_every_recorded_file_holds_the_authors_own_blob(self):
        """The working tree, the manifest digest and the author commit must be
        three readings of one file."""
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
                        _digest(REPO / relative),
                        _blob_digest(f"{commit}:{relative}"),
                        f"{relative} no longer holds {commit}'s blob — "
                        "re-derive the composition instead of editing it")
                    self.assertEqual(
                        entries[relative],
                        _blob_digest(f"{commit}:{relative}"),
                        f"the manifest digest for {relative} is not the "
                        "author's blob")

    def test_the_composition_adds_nothing_it_did_not_record(self):
        """The delta is exactly the four increments plus this ledger. No
        unrelated file moved."""
        expected = {path for paths in AUTHOR_FILES.values() for path in paths}
        expected |= set(OWN_FILES)
        self.assertEqual(_working_delta(), expected)

    def test_the_four_increments_do_not_overlap(self):
        seen: dict[str, str] = {}
        for commit, paths in AUTHOR_FILES.items():
            for relative in paths:
                self.assertNotIn(
                    relative, seen,
                    f"{relative} is claimed by both {seen.get(relative)} and "
                    f"{commit}")
                seen[relative] = commit


class TheLegacySurfaceIsUntouched(unittest.TestCase):
    """#478 keeps the old Fleet in parallel. This RC may not move it."""

    def test_no_legacy_path_is_in_the_composition_delta(self):
        offenders = sorted(
            path for path in _working_delta()
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

    def test_the_legacy_isolation_pr_is_additive(self):
        """#483 may add guards and tests; it may not edit a Legacy file."""
        status = _git(
            "diff", "--name-status",
            "e69fc0337ae5d33000478e3d5162077f525bc8cc.."
            "644ee4a8e35b770426841929d5989bcfad128bef").decode().splitlines()
        offenders = [line for line in status if not line.startswith("A\t")]
        self.assertEqual(offenders, [],
                         "#483 is not an additive change: " + "; ".join(offenders))


class TheSingleNotifierIsPresentAndNotDuplicated(unittest.TestCase):
    """#423's notifier is one object, already in the base, injected by path."""

    def test_the_notifier_is_already_in_the_wave13_base(self):
        self.assertTrue(
            _is_ancestor(NOTIFIER_HEAD, BASE),
            f"{NOTIFIER_PR} {NOTIFIER_HEAD} is not an ancestor of the base")
        self.assertEqual(
            _blob_digest(f"{BASE}:{NOTIFIER_PATH}"),
            _blob_digest(f"HEAD:{NOTIFIER_PATH}"),
            "this composition rewrote #423's notifier instead of using it")

    def test_there_is_exactly_one_notifier_source(self):
        tree = _git("ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()
        copies = [path for path in tree
                  if Path(path).name == "milestone_notify.py"]
        self.assertEqual(
            copies, [NOTIFIER_PATH],
            "a second notifier source was imported: " + ", ".join(copies))

    def test_the_monitor_notifier_is_an_adapter_not_a_second_ledger(self):
        """**Turns red when** the Monitor grows its own sqlite ledger or sender
        instead of resolving #423's object by module path."""
        source = (
            REPO / "integrations" / "fleet-next-agent-monitor"
            / "fleet_next_agent_monitor" / "notifier.py"
        ).read_text(encoding="utf-8")
        self.assertIn("importlib.import_module", source)
        self.assertIn("BLOCKED_LEDGER", source)
        for second_ledger in ("sqlite3", "CREATE TABLE", "class MilestoneLedger"):
            with self.subTest(token=second_ledger):
                self.assertNotIn(
                    second_ledger, source,
                    f"the Monitor's notifier contains {second_ledger!r}; that "
                    "is a second ledger, not an adapter")

    def test_the_four_merges_do_not_re_import_the_notifier(self):
        author_heads = {commit for _, commit, _, _ in AUTHOR_ANCESTRY}
        self.assertNotIn(NOTIFIER_HEAD, author_heads)
        manifest = _manifest()
        self.assertEqual(manifest["notifier"]["pr"], NOTIFIER_PR)
        self.assertEqual(manifest["notifier"]["head"], NOTIFIER_HEAD)
        self.assertEqual(manifest["notifier"]["copy_count"], 1)
        self.assertIn("ancestor", manifest["notifier"]["relation"])


class TheWave13LedgerIsPreservedAndNotWeakened(unittest.TestCase):
    """Wave 13's record and its refusal conditions survive byte for byte."""

    def test_the_two_earlier_ledgers_are_untouched(self):
        for relative in (WAVE13_LEDGER, WAVE12_LEDGER):
            with self.subTest(path=relative):
                self.assertEqual(
                    _git("rev-parse", f"HEAD:{relative}").strip(),
                    _git("rev-parse", f"{BASE}:{relative}").strip(),
                    f"{relative} was edited by this composition")

    def test_the_wave13_refusal_conditions_are_still_present(self):
        source = (REPO / WAVE13_LEDGER).read_text(encoding="utf-8")
        for token in (
            "43efd5ce0bfbe61db335910fabb85c92a24ea8e1",
            "be2c1cccd6a506485c9465967b2531facffe459e",
            "9b7710a26180e1e6d00ce597827d447d0d2348b7",
            "3777d134be02d896c9e36bc2ffd3a25baf9aaa04",
            "LEGACY_PREFIXES",
            "fleet_mcp/",
            "REAL_DEVICE",
            "PRODUCTION",
            "not measured",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source,
                              f"Wave 13's ledger lost {token!r}")

    def test_the_manifest_records_the_preserved_blobs(self):
        manifest = _manifest()
        for relative in (WAVE13_LEDGER, WAVE12_LEDGER):
            with self.subTest(path=relative):
                self.assertEqual(
                    manifest["preserved"][relative]["blob"],
                    _git("rev-parse", f"HEAD:{relative}").strip().decode())


class TheCompositionIsOfflineImportable(unittest.TestCase):
    """The four new packages import with no network and no node runtime."""

    def test_the_four_packages_import_with_sockets_disabled(self):
        paths = [
            str(REPO / "integrations" / "fleet-next-agent-monitor"),
            str(REPO / "integrations" / "fleet-next-monitor-source-adapter"),
            str(REPO / "integrations" / "fleet-next-legacy-safety"),
            str(REPO / "integrations" / "fleet-next-agent-monitor-activation"),
        ]
        script = (
            "import sys, socket\n"
            "def blocked(*a, **k):\n"
            "    raise RuntimeError('network disabled for offline probe')\n"
            "socket.socket = blocked\n"
            "socket.create_connection = blocked\n"
            "for p in sys.argv[1:]: sys.path.insert(0, p)\n"
            "import fleet_next_agent_monitor\n"
            "import fleet_next_monitor_source_adapter\n"
            "import legacy_safety\n"
            "import activation\n"
            "import batch_adapter\n"
            "print('OFFLINE_IMPORT_OK')\n"
        )
        completed = subprocess.run(
            (sys.executable, "-I", "-c", script, *paths),
            cwd=REPO, capture_output=True, text=True, timeout=120)
        self.assertEqual(
            completed.returncode, 0,
            "offline import failed:\n" + completed.stdout + completed.stderr)
        self.assertIn("OFFLINE_IMPORT_OK", completed.stdout)

    def test_no_new_integration_module_imports_a_transport(self):
        offenders = []
        roots = (
            "fleet-next-agent-monitor",
            "fleet-next-agent-monitor-activation",
            "fleet-next-monitor-source-adapter",
            "fleet-next-legacy-safety",
        )
        for root in roots:
            for path in (REPO / "integrations" / root).rglob("*.py"):
                for match in NETWORK_IMPORT.finditer(
                        path.read_text(encoding="utf-8")):
                    offenders.append(
                        f"{path.relative_to(REPO)}: {match.group(0).strip()}")
        self.assertEqual(offenders, [],
                         "a composed module imports a transport: "
                         + "; ".join(offenders))

    def test_the_manifest_states_offline_packaging(self):
        offline = _manifest()["offline"]
        self.assertTrue(offline["importable"])
        self.assertEqual(offline["network_imports"], [])
        self.assertIn("offline", offline["evidence"].lower())


class TheRecordIsADeliverable(unittest.TestCase):
    """The document is part of the RC, and it says what was and was not run."""

    def test_the_record_names_the_composed_shas(self):
        source = RECORD.read_text(encoding="utf-8")
        for pr, commit, _, merge in AUTHOR_ANCESTRY:
            with self.subTest(pr=pr):
                self.assertIn(commit[:9], source,
                              f"the record does not name {pr}'s head")
                self.assertIn(merge[:9], source,
                              f"the record does not name {pr}'s merge")
        for value in (BASE, COMPOSED_SOURCE):
            self.assertIn(value[:9], source)
        self.assertIn(NOTIFIER_HEAD[:9], source)

    def test_the_record_states_the_gate_reading_and_its_limit(self):
        source = RECORD.read_text(encoding="utf-8")
        for token in (
            "179 passed",
            "5/5",
            "baseline",
            "not zero",
            "REAL_DEVICE",
            "PRODUCTION",
            "not measured",
            "not a release",
            "BLOCKED_OWNER",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source,
                              f"the record does not state {token!r}")

    def test_the_record_names_what_was_not_merged(self):
        source = RECORD.read_text(encoding="utf-8")
        for pr in ("#473", "#474", "#407"):
            with self.subTest(pr=pr):
                self.assertIn(pr, source)
        self.assertIn("not merged", source.lower())


if __name__ == "__main__":
    unittest.main()
