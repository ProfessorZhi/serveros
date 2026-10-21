"""Same-ID overlay update, offline readback and rollback checks.

The Creator is not reachable from this test, and the checker does not pretend
it is. What is checked is the local contract an operator can hand to a person
before a Creator action: the plugin id did not move, the version advanced, the
old Skills survived, the archive hash is the recorded one, a readback receipt
agrees with the plan, and the previous archive is a valid rollback target.

Every receipt says `production_verified: False`. A green local rehearsal is not
a live update, an App-owned avatar change, or a release verdict.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OVERLAY = ROOT / "integrations" / "fleet-app-overlay"
FIXTURE = ROOT / "tests" / "data" / "fleet-overlay-fixture"
SHARED = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"

OLD_SKILLS = (
    "fleet-create-with-grants",
    "fleet-device-and-session-discovery",
    "fleet-evidence-review",
    "fleet-reuse-idle-session",
    "fleet-safety-boundaries",
)
NEW_SKILLS = (
    "fleet-async-dispatch",
    "fleet-commander-orchestration",
    "fleet-plugin-update",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bo = _load("fleet_overlay_update_builder", OVERLAY / "build_overlay.py")
pkg = _load("fleet_overlay_package_update", OVERLAY / "package_update_check.py")


class Fixture:
    """Two buildable overlay sources over the synthetic fixture bytes."""

    def __init__(self, test: unittest.TestCase):
        self._tmp = Path(tempfile.mkdtemp(prefix="fleet-overlay-update-"))
        test.addCleanup(shutil.rmtree, self._tmp, True)
        self.baseline = json.loads((OVERLAY / "baseline.json").read_text(encoding="utf-8"))

    def source(self, key: str, version: str, skills) -> Path:
        root = self._tmp / key / "src"
        shutil.copytree(FIXTURE, root)
        shutil.rmtree(root / "skills")
        (root / "skills").mkdir()
        for name in skills:
            shutil.copytree(SHARED / name, root / "skills" / name)
        for rel in ("plugin.json", ".codex-plugin/plugin.json"):
            path = root / rel
            data = json.loads(path.read_text(encoding="utf-8"))
            data["version"] = version
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return root

    def build(self, key: str, version: str, skills) -> tuple[dict, Path]:
        root = self.source(key, version, skills)
        out = self._tmp / key / "dist"
        record = bo.build(root, out, copy.deepcopy(self.baseline))
        return record, out / record["archive"]

    def pair(self) -> tuple[dict, Path, dict, Path]:
        one = self.build("v1", "0.1.1", OLD_SKILLS)
        two = self.build("v2", "0.1.2", OLD_SKILLS + NEW_SKILLS)
        return one[0], one[1], two[0], two[1]


def receipt(plan: dict, record: dict, archive: Path, *,
            plugin_id: str | None = None,
            previous: str = "PluginRelease_SYNTHETIC_V1",
            release: str = "PluginRelease_SYNTHETIC_V2",
            version: str | None = None,
            skills: list[str] | None = None,
            archive_sha256: str | None = None,
            icons=None, source: str = pkg.SOURCE_SYNTHETIC) -> dict:
    view = pkg.archive_view(record, archive)
    return {
        "schema": pkg.READBACK_SCHEMA,
        "source": source,
        "plugin_id": plugin_id or record["plugin_id"],
        "previous_release_id": previous,
        "release_id": release,
        "version": version or record["version"],
        "skills": sorted(skills if skills is not None else view["skills"]),
        "archive_sha256": archive_sha256 or record["archive_sha256"],
        "icons": view["icon_sha256"] if icons is None else icons,
    }


class TheSameIdentityAndVersionContract(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture(self)
        self.previous, self.previous_archive, self.next, self.next_archive = self.f.pair()
        self.plan = pkg.plan_update(
            previous_record=self.previous, previous_archive=self.previous_archive,
            next_record=self.next, next_archive=self.next_archive)

    def test_the_plan_preserves_the_plugin_id_and_advances_the_version(self) -> None:
        self.assertEqual(self.plan["plugin_id"], self.previous["plugin_id"])
        self.assertEqual(self.plan["from_version"], "0.1.1")
        self.assertEqual(self.plan["to_version"], "0.1.2")
        self.assertEqual(self.plan["verdict"], "PASS")
        self.assertEqual(self.plan["writes"], [])

    def test_the_old_skills_survive_and_the_new_skills_appear(self) -> None:
        self.assertEqual(self.plan["skills_removed"], [])
        self.assertEqual(set(self.plan["skills_added"]), set(NEW_SKILLS))
        for name in OLD_SKILLS:
            self.assertIn(name, self.plan["skills"])

    def test_a_changed_plugin_id_is_refused(self) -> None:
        other = copy.deepcopy(self.next)
        other["plugin_id"] = "plugin_asdk_other"
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=self.previous,
                            previous_archive=self.previous_archive,
                            next_record=other, next_archive=self.next_archive)
        self.assertIn("plugin_id", str(caught.exception))

    def test_a_version_that_does_not_advance_is_refused(self) -> None:
        record, archive = self.f.build("same-version", "0.1.1",
                                       OLD_SKILLS + NEW_SKILLS)
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=self.previous,
                            previous_archive=self.previous_archive,
                            next_record=record, next_archive=archive)
        self.assertIn("advance", str(caught.exception))

    def test_removing_a_previous_skill_is_refused(self) -> None:
        record, archive = self.f.build("removed", "0.1.3", NEW_SKILLS)
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=self.previous,
                            previous_archive=self.previous_archive,
                            next_record=record, next_archive=archive)
        self.assertIn("skill", str(caught.exception).lower())

    def test_changing_the_app_binding_bytes_is_refused(self) -> None:
        root = self.f.source("app-change", "0.1.3", OLD_SKILLS + NEW_SKILLS)
        app = json.loads((root / ".app.json").read_text(encoding="utf-8"))
        app["apps"]["serveros-fleet"]["id"] = "plugin_asdk_app_OTHER"
        (root / ".app.json").write_text(json.dumps(app), encoding="utf-8")
        out = self.f._tmp / "app-change" / "dist"
        record = bo.build(root, out, copy.deepcopy(self.f.baseline))
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=self.previous,
                            previous_archive=self.previous_archive,
                            next_record=record,
                            next_archive=out / record["archive"])
        self.assertIn("App binding", str(caught.exception))

    def test_a_record_hash_that_does_not_match_the_archive_is_refused(self) -> None:
        other = copy.deepcopy(self.next)
        other["archive_sha256"] = "0" * 64
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=self.previous,
                            previous_archive=self.previous_archive,
                            next_record=other, next_archive=self.next_archive)
        self.assertIn("archive_sha256", str(caught.exception))


class TheReadbackIsAClaimWithAProvenance(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture(self)
        self.previous, self.previous_archive, self.next, self.next_archive = self.f.pair()
        self.plan = pkg.plan_update(
            previous_record=self.previous, previous_archive=self.previous_archive,
            next_record=self.next, next_archive=self.next_archive)

    def test_a_matching_receipt_passes_but_is_never_called_production(self) -> None:
        result = pkg.verify_readback(
            self.plan, receipt(self.plan, self.next, self.next_archive))
        self.assertEqual(result["verdict"], "PASS")
        self.assertFalse(result["production_verified"])
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["source"], pkg.SOURCE_SYNTHETIC)

    def test_a_reused_release_id_is_refused(self) -> None:
        bad = receipt(self.plan, self.next, self.next_archive,
                      release="PluginRelease_SYNTHETIC_V1")
        with self.assertRaises(pkg.Refused) as caught:
            pkg.verify_readback(self.plan, bad)
        self.assertIn("release_id", str(caught.exception))

    def test_a_missing_skill_is_refused(self) -> None:
        bad = receipt(self.plan, self.next, self.next_archive,
                      skills=list(OLD_SKILLS))
        with self.assertRaises(pkg.Refused) as caught:
            pkg.verify_readback(self.plan, bad)
        self.assertIn("skills", str(caught.exception))

    def test_a_wrong_archive_hash_is_refused(self) -> None:
        bad = receipt(self.plan, self.next, self.next_archive,
                      archive_sha256="f" * 64)
        with self.assertRaises(pkg.Refused) as caught:
            pkg.verify_readback(self.plan, bad)
        self.assertIn("archive_sha256", str(caught.exception))

    def test_a_live_label_is_refused_because_this_tool_did_not_observe_it(self) -> None:
        bad = receipt(self.plan, self.next, self.next_archive, source="LIVE")
        with self.assertRaises(pkg.Refused) as caught:
            pkg.verify_readback(self.plan, bad)
        self.assertIn("LIVE", str(caught.exception))

    def test_missing_icons_are_partial_not_a_pass(self) -> None:
        bad = receipt(self.plan, self.next, self.next_archive, icons={})
        result = pkg.verify_readback(self.plan, bad)
        self.assertEqual(result["verdict"], "PARTIAL")
        self.assertEqual(result["icons_status"], "NOT_READ")


class TheRollbackTargetIsCheckedNotExecuted(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture(self)
        self.previous, self.previous_archive, self.next, self.next_archive = self.f.pair()

    def test_the_previous_archive_is_a_valid_target(self) -> None:
        result = pkg.check_rollback(
            previous_record=self.previous, previous_archive=self.previous_archive,
            current_record=self.next)
        self.assertTrue(result["rollback_ready"])
        self.assertEqual(result["restores_version"], "0.1.1")
        self.assertEqual(result["writes"], [])

    def test_a_different_plugin_id_cannot_be_the_rollback_target(self) -> None:
        other = copy.deepcopy(self.next)
        other["plugin_id"] = "plugin_asdk_other"
        with self.assertRaises(pkg.Refused) as caught:
            pkg.check_rollback(
                previous_record=self.previous, previous_archive=self.previous_archive,
                current_record=other)
        self.assertIn("plugin_id", str(caught.exception))

    def test_a_mismatched_previous_archive_is_refused(self) -> None:
        with self.assertRaises(pkg.Refused) as caught:
            pkg.check_rollback(
                previous_record=self.previous, previous_archive=self.next_archive,
                current_record=self.next)
        self.assertIn("archive_sha256", str(caught.exception))


class TheLocalRehearsalBuildsBothVersionsOffline(unittest.TestCase):
    def test_the_cli_rehearsal_treats_out_as_a_directory(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="fleet-update-cli-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "rehearsal"
        rc = pkg.main(["rehearse", "--source", str(FIXTURE),
                       "--baseline", str(OVERLAY / "baseline.json"),
                       "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertTrue((out / "rehearsal.json").is_file())

    def test_the_rehearsal_is_deterministic_and_writes_no_external_effect(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="fleet-update-rehearsal-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "out"
        result = pkg.rehearse(source_root=FIXTURE,
                              baseline_path=OVERLAY / "baseline.json",
                              out_dir=out)
        self.assertEqual(result["plan"]["verdict"], "PASS")
        self.assertEqual(result["readback"]["verdict"], "PASS")
        self.assertEqual(result["observed_receipt"]["source"], pkg.SOURCE_SYNTHETIC)
        self.assertTrue(result["rollback"]["rollback_ready"])
        self.assertFalse(result["production_verified"])
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["uploads"], [])
        self.assertEqual(result["installs"], [])
        archives = sorted(p.name for p in out.rglob("*.zip"))
        self.assertEqual(len(archives), 2, archives)
        self.assertTrue((out / "rehearsal.json").is_file())
        # The archive is a local file; opening it is the only act the test takes.
        with zipfile.ZipFile(next(p for p in out.rglob("*.zip")
                                  if "0.1.2" in p.name)) as zf:
            self.assertIn("plugin.json", zf.namelist())


if __name__ == "__main__":
    unittest.main()
