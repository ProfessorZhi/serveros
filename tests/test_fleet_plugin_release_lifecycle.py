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

import hashlib
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


class ARollbackTargetIsIdentifiableAndReproducible(unittest.TestCase):
    """What a rollback actually needs.

    An earlier version of this test regenerated the old version after generating
    a new one and compared it to the old one — `f(a) == f(a)` with a call in
    between. It proved determinism, which `test_the_previous_release_is_
    reproducible_from_its_own_source` already proves, and claimed more.

    What is checked here is what an operator needs: each released version has a
    stable digest that identifies it, two versions do not share one, and
    regenerating a pinned version reproduces the digest that identifies it. That
    is what makes "roll back to the previous release" verifiable rather than
    remembered.

    Not proven, and not claimed: that a *newer builder* reproduces an older
    release byte for byte. That would need the builder itself pinned per release,
    which this package does not yet do.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l4-rollback-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    @staticmethod
    def _digest(tree: dict[str, bytes]) -> str:
        h = hashlib.sha256()
        for rel in sorted(tree):
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(tree[rel])
        return h.hexdigest()

    def _release(self, version: str, out_name: str) -> str:
        source = _source_copy(self.tmp / out_name, version=version)
        out = self.tmp / out_name / "out"
        _generate_from(source, out)
        return self._digest(_tree(out))

    def test_each_version_has_its_own_digest(self) -> None:
        first = self._release("9.8.0", "a")
        second = self._release("9.8.1", "b")
        self.assertNotEqual(first, second,
                            "two releases share a digest, so a rollback cannot "
                            "be told apart from the thing it rolls back from")

    def test_regenerating_a_pinned_version_reproduces_its_digest(self) -> None:
        recorded = self._release("9.8.0", "a")
        self._release("9.8.1", "b")          # a later release happens
        again = self._release("9.8.0", "c")  # rollback target is rebuilt
        self.assertEqual(recorded, again,
                         "the pinned version did not reproduce the digest that "
                         "identifies the release being rolled back to")

    def test_the_previous_release_is_reproducible_from_its_own_source(self) -> None:
        source = _source_copy(self.tmp / "det", version="9.7.0")
        first, second = self.tmp / "det" / "one", self.tmp / "det" / "two"
        _generate_from(source, first)
        _generate_from(source, second)
        self.assertEqual(_tree(first), _tree(second),
                         "two runs from one source disagree, so a rebuild cannot "
                         "reproduce the release it rebuilds")


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

    def test_the_package_contains_exactly_the_expected_members(self) -> None:
        """An allowlist of names, not a blocklist of suffixes.

        A suffix blocklist only catches the extensions someone thought of: a
        `hooks/postinstall.bash`, a `deploy/fleet.conf` holding a `[Service]`
        stanza, or a `.ps1` all slipped past an earlier version of this check.
        Naming every member the build is allowed to produce closes that, and it
        fails on the *first* unexpected file rather than on a recognised one.
        """
        skills = _source_copy(self.tmp_members()).glob("plugin/skills/*/SKILL.md")
        expected = {".claude-plugin/plugin.json", ".mcp.json", "README.md",
                    "PRIVACY.md", "assets/composerIcon.png", "assets/logo.png"}
        expected |= {f"skills/{p.parent.name}/SKILL.md" for p in skills}
        self.assertEqual(set(_tree(PLUGIN_DIR)), expected,
                         "an unexpected member is something the package can ship "
                         "that no test looked at")

    def tmp_members(self) -> Path:
        scratch = Path(tempfile.mkdtemp(prefix="l4-members-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        return scratch

    def test_no_skill_tells_the_reader_to_restart_a_service(self) -> None:
        """The prose channel is reachable even when the package ships no code.

        Structure proves the package cannot *start* anything; it cannot stop a
        Skill from instructing an operator to restart one. The README is allowed
        to discuss restarts — it is documentation for a human — but a Skill is
        instruction the assistant follows.
        """
        import re
        imperative = re.compile(
            r"(?i)systemctl|daemon-reload|systemd-|\bservice\s+\S+\s+(?:restart|reload)")
        offenders = {}
        for rel, data in _tree(PLUGIN_DIR).items():
            if not rel.startswith("skills/"):
                continue
            hits = sorted(set(imperative.findall(data.decode("utf-8", "replace"))))
            if hits:
                offenders[rel] = hits
        self.assertEqual(offenders, {},
                         "a Skill that says how to restart a service makes a "
                         "documentation update into an operational instruction")

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
        """Uses the committed binding's own URL.

        An earlier version passed the example URL from this file, which made the
        tree stale on `.mcp.json` no matter what — so the test passed with the
        skill mutation removed and proved nothing about skill staleness. The
        control below is what makes this one mean something.
        """
        skill = PLUGIN_DIR / "skills" / "fleet-evidence-review" / "SKILL.md"
        original = skill.read_bytes()
        try:
            skill.write_bytes(original + b"\nstale\n")
            with mock.patch.object(fleet_plugin.shutil, "which",
                                   return_value="/usr/bin/claude"), \
                 mock.patch.object(fleet_plugin.subprocess, "run", self._fake_run):
                code = fleet_plugin.install(fleet_plugin.DEFAULT_NEXT_MCP_URL,
                                            keep_legacy=False)
        finally:
            skill.write_bytes(original)
        self.assertEqual(code, 1, "a stale package must not be installed")
        self.assertEqual(self.calls, [],
                         "the client was invoked before the tree was verified")

    def test_the_same_install_succeeds_when_the_tree_is_current(self) -> None:
        """The control: without the mutation the client *is* reached.

        Without this, a refusal caused by something else entirely would look
        like skill-staleness detection working.
        """
        self.assertEqual(fleet_plugin.check(fleet_plugin.DEFAULT_NEXT_MCP_URL), 0,
                         "the committed tree must be current for this control")
        with mock.patch.object(fleet_plugin.shutil, "which",
                               return_value="/usr/bin/claude"), \
             mock.patch.object(fleet_plugin.subprocess, "run", self._fake_run):
            code = fleet_plugin.install(fleet_plugin.DEFAULT_NEXT_MCP_URL,
                                        keep_legacy=False)
        self.assertEqual(code, 0)
        self.assertNotEqual(self.calls, [],
                            "a current tree must reach the client, or the refusal "
                            "test above proves nothing")

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
