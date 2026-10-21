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
from unittest import mock
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

    def source(self, key: str, version: str, skills, mutate=None) -> Path:
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
        if mutate is not None:
            mutate(root)
        return root

    def build(self, key: str, version: str, skills,
              mutate=None) -> tuple[dict, Path]:
        root = self.source(key, version, skills, mutate=mutate)
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



def _add_mcp(root: Path, *, url: str = "https://example.invalid/mcp",
             extra: bool = False, command: str | None = None) -> None:
    servers = {"fleet": {"type": "streamable-http", "url": url}}
    if extra:
        servers["extra"] = {"type": "streamable-http", "url": url + "/extra"}
    if command is not None:
        servers["fleet"] = {"type": "stdio", "command": command}
    (root / "mcp.json").write_text(
        json.dumps({"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                    "mcpServers": servers}, indent=2),
        encoding="utf-8")


def _add_legacy_mcp(root: Path, *,
                    url: str = "https://example.invalid/mcp") -> None:
    (root / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"fleet": {"type": "http", "url": url}}},
                   indent=2),
        encoding="utf-8")


def _edit_manifest(root: Path, rel: str, mutate) -> None:
    path = root / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _interface(data: dict) -> dict:
    block = data["extensions"]["com.openai"]
    return block.get("interface") if isinstance(block.get("interface"), dict) else block


class TheProtectedAppConfigurationCannotDrift(unittest.TestCase):
    """An overlay may update version/Skills; App-owned config stays fixed."""

    def setUp(self) -> None:
        self.f = Fixture(self)

    def _pair(self, one, two):
        return (self.f.build("cfg-v1", "0.1.1", OLD_SKILLS, mutate=one),
                self.f.build("cfg-v2", "0.1.2", OLD_SKILLS + NEW_SKILLS,
                             mutate=two))

    def _refused(self, one, two) -> str:
        prev = self.f.build("cfg-v1", "0.1.1", OLD_SKILLS, mutate=one)
        nxt = self.f.build("cfg-v2", "0.1.2", OLD_SKILLS + NEW_SKILLS,
                           mutate=two)
        with self.assertRaises(pkg.Refused) as caught:
            pkg.plan_update(previous_record=prev[0], previous_archive=prev[1],
                            next_record=nxt[0], next_archive=nxt[1])
        return str(caught.exception)

    def test_identical_mcp_config_with_version_and_skills_passes(self) -> None:
        prev, nxt = self._pair(lambda root: _add_mcp(root),
                               lambda root: _add_mcp(root))
        plan = pkg.plan_update(previous_record=prev[0], previous_archive=prev[1],
                               next_record=nxt[0], next_archive=nxt[1])
        self.assertEqual(plan["verdict"], "PASS")
        self.assertIn("mcp", plan["protected"])

    def test_mcp_url_drift_is_refused(self) -> None:
        message = self._refused(
            lambda root: _add_mcp(root, url="https://one.invalid/mcp"),
            lambda root: _add_mcp(root, url="https://two.invalid/mcp"))
        self.assertIn("mcp", message.lower())

    def test_added_mcp_server_is_refused(self) -> None:
        message = self._refused(
            lambda root: _add_mcp(root),
            lambda root: _add_mcp(root, extra=True))
        self.assertIn("mcp", message.lower())

    def test_removing_mcp_config_is_refused(self) -> None:
        message = self._refused(lambda root: _add_mcp(root), lambda root: None)
        self.assertIn("mcp", message.lower())

    def test_legacy_dot_mcp_drift_is_refused(self) -> None:
        message = self._refused(
            lambda root: _add_legacy_mcp(root, url="https://one.invalid/mcp"),
            lambda root: _add_legacy_mcp(root, url="https://two.invalid/mcp"))
        self.assertIn("mcp", message.lower())

    def test_referenced_mcp_file_drift_is_refused(self) -> None:
        def with_script(root):
            _add_mcp(root, command="./mcp-server.sh")
            (root / "mcp-server.sh").write_text("#!/bin/sh\necho one\n",
                                                 encoding="utf-8")

        def changed_script(root):
            _add_mcp(root, command="./mcp-server.sh")
            (root / "mcp-server.sh").write_text("#!/bin/sh\necho two\n",
                                                 encoding="utf-8")
        message = self._refused(with_script, changed_script)
        self.assertIn("mcp", message.lower())

    def test_short_description_drift_is_refused(self) -> None:
        def set_short(value):
            def mutate(root):
                _edit_manifest(root, "plugin.json",
                               lambda data: _interface(data).update(
                                   shortDescription=value))
            return mutate
        message = self._refused(set_short("One subtitle"),
                                set_short("A different subtitle"))
        self.assertIn("manifest", message.lower())

    def test_default_prompt_type_or_order_drift_is_refused(self) -> None:
        def set_prompt(value):
            def mutate(root):
                _edit_manifest(root, "plugin.json",
                               lambda data: _interface(data).update(
                                   defaultPrompt=value))
            return mutate
        message = self._refused(set_prompt(["First", "Second"]),
                                set_prompt(["Second", "First"]))
        self.assertIn("manifest", message.lower())

    def test_top_level_description_drift_is_refused(self) -> None:
        def set_description(value):
            return lambda root: _edit_manifest(
                root, "plugin.json", lambda data: data.update(description=value))
        message = self._refused(set_description("one"), set_description("two"))
        self.assertIn("manifest", message.lower())

    def test_compat_manifest_repeated_field_drift_is_refused(self) -> None:
        def set_compat_short(value):
            def mutate(root):
                _edit_manifest(root, ".codex-plugin/plugin.json",
                               lambda data: data["interface"].update(
                                   shortDescription=value))
            return mutate
        message = self._refused(set_compat_short("One"), set_compat_short("Two"))
        self.assertIn("compat", message.lower())

    def test_compat_manifest_version_must_match_root(self) -> None:
        def mutate(root):
            _edit_manifest(root, ".codex-plugin/plugin.json",
                           lambda data: data.update(version="0.1.1"))
        message = self._refused(lambda root: None, mutate)
        self.assertIn("compat", message.lower())

    def test_keywords_change_is_allowed(self) -> None:
        def set_keywords(value):
            return lambda root: _edit_manifest(
                root, "plugin.json", lambda data: data.update(keywords=value))
        prev, nxt = self._pair(set_keywords(["one"]), set_keywords(["two"]))
        plan = pkg.plan_update(previous_record=prev[0], previous_archive=prev[1],
                               next_record=nxt[0], next_archive=nxt[1])
        self.assertEqual(plan["verdict"], "PASS")

    def test_new_non_skill_member_is_refused(self) -> None:
        def extra(root):
            (root / "extra.txt").write_text("not a skill\n", encoding="utf-8")
        message = self._refused(lambda root: None, extra)
        self.assertIn("member", message.lower())

    def test_changed_supporting_file_outside_skills_is_refused(self) -> None:
        def changed(root):
            (root / "assets" / "README.md").write_text(
                "changed supporting file\n", encoding="utf-8")
        message = self._refused(lambda root: None, changed)
        self.assertIn("member", message.lower())


class TheRehearsalRefusesUnsafeTreesBeforeWriting(unittest.TestCase):
    """Symlinks and shared-output boundaries are checked before any copy/write."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fleet-update-unsafe-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.f = Fixture(self)
        self.src = self.f.source("safe-src", "0.1.1", OLD_SKILLS)
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        self.canary = self.outside / "canary.txt"
        self.canary.write_text("CANARY-CONTENT\n", encoding="utf-8")

    def _rehearse(self, out: Path) -> None:
        pkg.rehearse(source_root=self.src,
                     baseline_path=OVERLAY / "baseline.json", out_dir=out)

    def _assert_refused_and_sentinel(self, out: Path) -> str:
        with self.assertRaises(pkg.Refused) as caught:
            self._rehearse(out)
        message = str(caught.exception)
        self.assertNotIn("CANARY-CONTENT", message)
        self.assertTrue(self.canary.is_file())
        self.assertEqual(self.canary.read_text(encoding="utf-8"),
                         "CANARY-CONTENT\n")
        return message

    def test_source_symlink_file_is_refused_before_out_is_created(self) -> None:
        (self.src / "notes.md").symlink_to(self.canary)
        out = self.tmp / "out"
        message = self._assert_refused_and_sentinel(out)
        self.assertIn("symlink", message.lower())
        self.assertFalse(out.exists())
        self.assertTrue((self.src / "notes.md").is_symlink())

    def test_source_symlink_directory_is_refused_before_copy(self) -> None:
        (self.src / "linked-dir").symlink_to(self.outside, target_is_directory=True)
        out = self.tmp / "out"
        message = self._assert_refused_and_sentinel(out)
        self.assertIn("symlink", message.lower())
        self.assertFalse(out.exists())

    def test_shared_skills_symlink_is_refused(self) -> None:
        bad_shared = self.tmp / "bad-skills"
        bad_shared.mkdir()
        (bad_shared / "fleet-create-with-grants").symlink_to(
            self.outside, target_is_directory=True)
        out = self.tmp / "out"
        with mock.patch.object(pkg, "SHARED_SKILLS", bad_shared):
            message = self._assert_refused_and_sentinel(out)
        self.assertIn("symlink", message.lower())
        self.assertFalse(out.exists())

    def test_output_parent_symlink_is_refused_before_any_write(self) -> None:
        link = self.tmp / "out-link"
        link.symlink_to(self.outside, target_is_directory=True)
        out = link / "nested"
        message = self._assert_refused_and_sentinel(out)
        self.assertIn("symlink", message.lower())
        self.assertEqual(sorted(p.name for p in self.outside.iterdir()),
                         ["canary.txt"])

    def test_non_empty_output_is_refused_without_rmtree(self) -> None:
        out = self.tmp / "nonempty"
        out.mkdir()
        sentinel = out / "sentinel.txt"
        sentinel.write_text("KEEP\n", encoding="utf-8")
        with mock.patch.object(pkg.shutil, "rmtree",
                               side_effect=AssertionError("rmtree called")):
            message = self._assert_refused_and_sentinel(out)
        self.assertIn("empty", message.lower())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "KEEP\n")

    def test_source_output_overlap_is_refused(self) -> None:
        out = self.src / "out"
        message = self._assert_refused_and_sentinel(out)
        self.assertIn("overlap", message.lower())
        self.assertFalse(out.exists())


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
