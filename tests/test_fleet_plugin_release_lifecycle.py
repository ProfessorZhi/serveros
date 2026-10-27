"""Update, rollback and refusal for the L4 commander package.

WHY THIS FILE EXISTS
--------------------
`tests/test_fleet_plugin_independence.py` proves the artifact is data and that a
Skill edit moves only Skill bytes. That is one half of the owner's requirement
("independently buildable/installable/discoverable/**updatable/rollback-able**").
The other half has no home yet: nothing checks that an *update* is a version
advance rather than a rebuild, that a *rollback* target is reproducible rather
than remembered, or that an install **refuses** instead of shipping a package
that does not match its source.

Those are the three ways this package can hurt someone:

* an update that ships new Skills under an unchanged version, so no installed
  client ever receives it;
* a rollback that is a claim rather than a byte-for-byte re-creation;
* an install that proceeds from a tree the builder would have refused.

Everything here drives `scripts/fleet_plugin.py` itself. No network, no client
binary, no credential, no service.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIRST_INSTALL = ROOT / "integrations" / "fleet-v1-first-install"
PLUGIN_DIR = ROOT / "plugins" / "serveros-fleet-ingress"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
URL = "https://fleet-next.example.ts.net/mcp"

#: Strings that would mean the package needs the device runtime restarted to take
#: effect. The package is data; a restart requirement is the failure this checks.
RUNTIME_COUPLING = ("serverosd", "systemctl", "systemd", "service unit",
                    "daemon-reload")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fleet_plugin = _load("l4_lifecycle_tool", ROOT / "scripts" / "fleet_plugin.py")
adapter = _load("l4_lifecycle_adapter",
                ROOT / "integrations" / "fleet-cc-dev-plugin" / "build_release_adapter.py")


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _source_copy(tmp: Path, version: str | None = None) -> Path:
    """A copy of the source package, optionally with its version changed."""
    source = tmp / "source"
    shutil.copytree(FIRST_INSTALL / "plugin", source / "plugin")
    shutil.copyfile(FIRST_INSTALL / "brand-assets.lock.json",
                    source / "brand-assets.lock.json")
    if version is not None:
        manifest = source / "plugin" / "plugin.json"
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["version"] = version
        manifest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    return source


def _generate_from(source_root: Path, out: Path) -> dict:
    saved = adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK
    adapter.RELEASE_PLUGIN = source_root / "plugin"
    adapter.SHARED_LOCK = source_root / "brand-assets.lock.json"
    try:
        return adapter.generate(out=out, next_mcp_url=URL)
    finally:
        adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK = saved


class AnUpdateIsAVersionAdvance(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l4-lifecycle-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = _source_copy(self.tmp, version="9.9.1")
        self.before = self.tmp / "before"
        _generate_from(self.source, self.before)

    def _bump(self, version: str) -> Path:
        manifest = self.source / "plugin" / "plugin.json"
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["version"] = version
        manifest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        out = self.tmp / f"after-{version}"
        _generate_from(self.source, out)
        return out

    def test_a_version_advance_moves_only_the_manifest(self) -> None:
        """An update is a version change, not a rebuild of the package."""
        after = self._bump("9.9.2")
        old, new = _tree(self.before), _tree(after)
        changed = sorted(rel for rel in set(old) | set(new)
                         if old.get(rel) != new.get(rel))
        self.assertEqual(changed, [".claude-plugin/plugin.json"],
                         "an update must not disturb Skills, assets or the binding")

    def test_the_skills_and_binding_are_byte_identical_across_an_update(self) -> None:
        after = self._bump("9.9.3")
        old, new = _tree(self.before), _tree(after)
        for rel in sorted(rel for rel in old if rel.startswith(("skills/", "assets/"))):
            with self.subTest(member=rel):
                self.assertEqual(old[rel], new.get(rel))
        self.assertEqual(old[".mcp.json"], new[".mcp.json"],
                         "an update must not re-consent the user")

    def test_the_version_is_what_a_client_sees(self) -> None:
        after = self._bump("9.9.4")
        manifest = json.loads((after / ".claude-plugin" / "plugin.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "9.9.4")


class ARollbackIsAByteForByteRecreation(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l4-rollback-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_the_previous_release_is_reproducible_from_its_own_source(self) -> None:
        """Rollback is "rebuild the pinned revision", so it must be deterministic.

        Two generations from one source must agree byte for byte; otherwise
        "roll back to the previous version" produces a tree nobody released.
        """
        source = _source_copy(self.tmp, version="9.8.0")
        first, second = self.tmp / "a", self.tmp / "b"
        _generate_from(source, first)
        _generate_from(source, second)
        self.assertEqual(_tree(first), _tree(second),
                         "two runs from one source disagree, so a rollback could "
                         "not reproduce the release it rolls back to")

    def test_rolling_back_after_an_update_returns_the_original_bytes(self) -> None:
        source = _source_copy(self.tmp, version="9.8.0")
        before = self.tmp / "before"
        _generate_from(source, before)

        # Forward: a newer release.
        manifest = source / "plugin" / "plugin.json"
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        doc["version"] = "9.8.1"
        manifest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        _generate_from(source, self.tmp / "updated")

        # Back: the pinned revision again.
        doc["version"] = "9.8.0"
        manifest.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        rolled_back = self.tmp / "rolled-back"
        _generate_from(source, rolled_back)

        self.assertEqual(_tree(before), _tree(rolled_back),
                         "rolling back did not reproduce the previous release")


class ThePackageNeedsNoRuntimeRestart(unittest.TestCase):
    """Structural, not prose-matching.

    An earlier version of this check grepped every text file for `serverosd` and
    flagged the README sentence that says a Skills update does **not** restart it
    — punishing the disclaimer and proving nothing. What actually makes a restart
    impossible is that the package carries no executable and no unit: nothing in
    it can ask a supervisor to do anything.
    """

    def setUp(self) -> None:
        if not PLUGIN_DIR.is_dir():
            self.skipTest("the committed plugin is not in this tree")

    def test_the_package_carries_nothing_a_service_manager_could_run(self) -> None:
        runnable = sorted(rel for rel in _tree(PLUGIN_DIR)
                          if Path(rel).suffix.lower() in
                          (".sh", ".service", ".timer", ".socket", ".py", ".js"))
        self.assertEqual(runnable, [],
                         "a package that ships something runnable can make an "
                         "install into a deployment")

    def test_no_manifest_can_start_a_process(self) -> None:
        offenders = {}
        for rel, data in _tree(PLUGIN_DIR).items():
            if not rel.endswith(".json"):
                continue
            text = data.decode("utf-8", "replace")
            hit = [word for word in ("\"command\"", "\"args\"", "\"exec\"",
                                     "\"service\"", "\"unit\"")
                   if word in text]
            if hit:
                offenders[rel] = hit
        self.assertEqual(offenders, {},
                         "the binding is an HTTP endpoint, not a process to start")

    def test_the_only_outbound_endpoint_is_the_mcp_url(self) -> None:
        binding = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))
        (server,) = binding["mcpServers"].values()
        self.assertEqual(set(server), {"type", "url", "oauth"})
        self.assertEqual(server["url"], fleet_plugin.DEFAULT_NEXT_MCP_URL,
                         "a second endpoint in the binding is a second thing to "
                         "re-consent on")

    def test_no_skill_hardcodes_a_tool_index_or_count(self) -> None:
        """Tool numbers move; a Skill that pins one goes stale silently."""
        import re
        offenders = {}
        for rel, data in _tree(PLUGIN_DIR).items():
            if not rel.startswith("skills/"):
                continue
            text = data.decode("utf-8", "replace")
            hits = sorted(set(re.findall(r"(?i)\b\d+\s*(?:tools?|verbs?)\b|tool\s*#?\d+",
                                         text)))
            if hits:
                offenders[rel] = hits
        self.assertEqual(offenders, {},
                         "a Skill must teach discovery, not a position in a list")


class InstallRefusesBeforeItTouchesTheClient(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l4-refusal-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.calls: list[list[str]] = []

    def _fake_run(self, argv, **kwargs):
        self.calls.append(list(argv))

        class _Done:
            returncode = 0
            stdout = "[]"
        return _Done()

    def test_a_stale_tree_is_refused_and_the_client_is_never_invoked(self) -> None:
        skill = PLUGIN_DIR / "skills" / "fleet-evidence-review" / "SKILL.md"
        original = skill.read_bytes()
        try:
            skill.write_bytes(original + b"\nstale\n")
            with mock.patch.object(fleet_plugin.shutil, "which",
                                   return_value="/usr/bin/claude"), \
                 mock.patch.object(fleet_plugin.subprocess, "run", self._fake_run):
                code = fleet_plugin.install(URL, keep_legacy=False)
        finally:
            skill.write_bytes(original)
        self.assertEqual(code, 1, "a stale package must not be installed")
        self.assertEqual(self.calls, [],
                         "the client was invoked before the tree was verified")

    def test_no_client_on_path_is_a_refusal_not_a_crash(self) -> None:
        with mock.patch.object(fleet_plugin.shutil, "which", return_value=None), \
             mock.patch.object(fleet_plugin.subprocess, "run", self._fake_run):
            code = fleet_plugin.install(URL, keep_legacy=False)
        self.assertEqual(code, 2)
        self.assertEqual(self.calls, [])

    def test_the_builder_refuses_a_url_that_is_not_the_mcp_path(self) -> None:
        """A local or non-/mcp URL is refused, so a typo cannot become a binding."""
        source = _source_copy(self.tmp / "second")
        saved = adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK
        adapter.RELEASE_PLUGIN = source / "plugin"
        adapter.SHARED_LOCK = source / "brand-assets.lock.json"
        try:
            with self.assertRaises(adapter.Refused):
                adapter.generate(out=self.tmp / "out",
                                 next_mcp_url="https://x.example/not-mcp")
        finally:
            adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK = saved


if __name__ == "__main__":
    unittest.main()
