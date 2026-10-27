#!/usr/bin/env python3
"""D1 — the device Ability face and the node runtime are separate builds.

WHY THIS FILE EXISTS
--------------------
`plugin/bundle.yaml` and `plugin/profiles/linux-work.yaml` declare
`artifacts: [node-runtime, plugin]`. Until this change the only artifact either
composition could produce was ONE payload carrying both faces: the Skills a
Claude Code loads and the `serverosd` daemon they talk to were built together,
versioned together and promoted together, under one cachebuster. A Skill
correction could not reach a device without rebuilding a daemon, and a daemon
rollback could not avoid rewinding the Abilities installed after it.

`scripts/build.py --face` now builds either face as its own artifact, with its
own cache identity, through the same composition and the same pinned
ability-management revision. This file pins what a build can decide:

1. The two faces are DISJOINT payloads. The plugin face has no daemon; the node
   face has no Skill and no plugin manifest. Neither can be mistaken for the
   other by a reader holding only the artifact.
2. A kind is a claim about the bytes, not a label. A composition that declares
   `plugin` and ships `runtime/` is refused, and so is one that declares
   `node-runtime` and ships no daemon. This is the "static declaration
   masquerading as state" defect stated in the form a build can decide.
3. The artifact's own record (`kinds`, `kind_evidence`, `skills`, `mcp_servers`)
   is derived from what was written. Forging the record does not make the face
   exist: `release.py` re-checks the members before anything is promoted.
4. The faces can be released and rolled back INDEPENDENTLY, under separate
   release roots, because each is its own artifact with its own version.
5. `installed` is not `ready`. The artifact states the catalogue and the bytes
   behind its kind; it asserts no device state, and an unobserved device reads
   `UNKNOWN` rather than inheriting capability from a declaration.

Hermetic: the reusable-skill source is a synthetic ability-management repository
built by `test_build.make_ability_repo`, exactly as the build suite does. No
live node, no service, no GPU.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
BUILDER = ROOT / "scripts" / "build.py"
RELEASE = ROOT / "scripts" / "release.py"
PACK_CONTRACT = ROOT / "abilities" / "pack-contract.yaml"
BUNDLE = ROOT / "plugin" / "bundle.yaml"
# Derived, not restated: a composition bump must not make this file fail for a
# reason about a literal rather than about the split being tested.
BUNDLE_DOC = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))
BASE_VERSION = f"{BUNDLE_DOC['version']}+{BUNDLE_DOC['cachebuster']}"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("serveros_build_d1", BUILDER)
release = _load("serveros_release_d1", RELEASE)

sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "scripts"))
import hermetic_tests  # noqa: E402
import test_build  # noqa: E402  (the synthetic ability-management source)


TEMPLATE = """schema: serveros/bundle.v1
artifacts:
  - {kind}
composition: {name}
name: {name}
display_name: {name}
description: synthetic composition
short_description: synthetic
long_description: synthetic
author: test
category: Productivity
license: MIT
brand_color: "#000000"
version: "0.0.1"
cachebuster: {name}.20261027a
default_prompt: ["synthetic"]
skills:
  - name: serveros
    source: local
{modules}"""

MODULES = """runtime:
  path: runtime
bin:
  path: bin
workengine:
  path: workengine
"""


def write_composition(path: Path, *, kind: str, modules: str = "") -> Path:
    path.write_text(TEMPLATE.format(kind=kind, name=path.stem, modules=modules))
    return path


def run_release(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RELEASE), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=env)


def seed_registry(state_dir: Path) -> None:
    target = state_dir / "runtime/registry/resources.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("schema: serveros/managed-runtime-registry.v1\nresources: []\n")


def artifact(out: Path) -> dict:
    return json.loads((out / "ARTIFACT.json").read_text(encoding="utf-8"))


class TheFacesAreSeparatePayloads(unittest.TestCase):
    """Build both faces of the real core composition once."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev = test_build.make_ability_repo(cls.base / "ability")
        cls.plugin = cls._build("plugin", "plugin")
        cls.node = cls._build("node", "node-runtime")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _build(cls, tag: str, face: str) -> Path:
        out = cls.base / tag / "codex" / "payload"
        market = cls.base / tag / "codex" / ".agents" / "plugins" / "marketplace.json"
        rc = build.build(BUNDLE, cls.repo, cls.rev, out, market, True,
                         PACK_CONTRACT, face=face)
        assert rc == 0, f"face {face} failed to build"
        return out

    # -- the plugin face ----------------------------------------------------

    def test_the_plugin_face_ships_the_ability_family_and_no_daemon(self):
        out = self.plugin
        doc = artifact(out)
        self.assertEqual(doc["kinds"], ["plugin"])
        self.assertEqual(doc["face"], "plugin")
        # The composition still declares both faces; this artifact is one.
        self.assertEqual(doc["declared_kinds"], ["node-runtime", "plugin"])
        self.assertEqual(doc["skills"],
                         ["ability-management", "runtime-management", "serveros"])
        for name in doc["skills"]:
            self.assertTrue((out / "skills" / name / "SKILL.md").is_file(), name)
        # The decoupling, measured on the payload rather than the declaration.
        for rel in ("bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
                    "workengine/schema.sql"):
            self.assertFalse((out / rel).exists(), f"plugin face carries {rel}")
        # No node MCP surface: its entry points live in the node artifact.
        self.assertEqual(doc["mcp_servers"], [])
        self.assertFalse((out / ".mcp.json").exists())
        self.assertFalse((out / ".codex-mcp.json").exists())
        # And the artifact's own prose does not advertise one either.
        self.assertNotIn("Node surface", (out / "README.md").read_text(encoding="utf-8"))
        # Nor does the plugin manifest point at a `.mcp.json` this face omits.
        plugin_json = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
        self.assertNotIn("mcpServers", plugin_json)

    def test_the_face_record_states_no_device_state(self):
        """A build can say what it carries; it cannot say what a device can do.

        `installed`, `ready`, `supported`, `granted` and `effective` are
        observations of a node. The artifact records the catalogue and the
        evidence for its kind, and nothing here asserts any of those words — so
        a reader must ask the device rather than infer capability from the
        Skills a payload was built with.
        """
        doc = artifact(self.plugin)
        for word in ("installed", "ready", "supported", "granted", "effective"):
            self.assertNotIn(word, doc)

    def test_the_plugin_face_passes_the_release_plugin_verifier(self):
        verdict = release.verify_plugin(self.plugin)
        self.assertTrue(verdict["ok"], verdict["problems"])

    def test_the_plugin_face_reads_reusable_skills_from_the_pinned_revision(self):
        """ability-management stays the one canonical source of the semantics."""
        for name in ("runtime-management", "ability-management"):
            shipped = (self.plugin / "skills" / name / "SKILL.md").read_bytes()
            pinned = subprocess.run(
                ["git", "-C", str(self.repo), "show",
                 f"{self.rev}:skills/{name}/payload/SKILL.md"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
            self.assertEqual(shipped, pinned, f"{name} is not the pinned payload")
            self.assertFalse((ROOT / "skills" / name).exists(),
                             f"{name} has a second, local source")

    # -- the node face ------------------------------------------------------

    def test_the_node_face_ships_the_daemon_and_no_skill(self):
        out = self.node
        doc = artifact(out)
        self.assertEqual(doc["kinds"], ["node-runtime"])
        self.assertEqual(doc["face"], "node-runtime")
        self.assertEqual(doc["skills"], [])
        self.assertFalse((out / "skills").exists())
        # A daemon a Claude Code could try to load would be the combined
        # payload again; neither plugin manifest may be present.
        self.assertFalse((out / ".claude-plugin").exists())
        self.assertFalse((out / ".codex-plugin").exists())
        for rel in release.REQUIRED_MEMBERS[release.NODE_RUNTIME]:
            self.assertTrue((out / rel).is_file(), rel)
        # The node surface ships with this face, because its entry points do.
        self.assertEqual(doc["mcp_servers"], ["serveros-agent", "serveros-node"])
        self.assertTrue((out / ".mcp.json").is_file())
        # Provenance in prose matches provenance in the record: no Skill, no pin.
        readme = (out / "README.md").read_text(encoding="utf-8")
        self.assertIn("Provided skills: none", readme)
        self.assertIn("Node surface: serveros-node, serveros-agent", readme)
        self.assertNotIn("Reusable skills read from", readme)

    def test_the_node_face_does_not_claim_the_ability_pin(self):
        """Provenance is what was READ. The daemon reads no Skill."""
        source = artifact(self.node)["source"]["ability_management"]
        self.assertIsNone(source["revision"])
        self.assertIsNone(source["repo"])
        self.assertIn("composes no Skill", source["basis"])

    def test_the_node_face_declares_the_kind_its_members_back(self):
        evidence = artifact(self.node)["kind_evidence"]
        self.assertEqual(list(evidence), ["node-runtime"])
        self.assertIn("bin/serverosd", evidence["node-runtime"])
        self.assertIn("runtime/serverosd.service", evidence["node-runtime"])

    # -- independence -------------------------------------------------------

    def test_each_face_has_its_own_version_and_cache_identity(self):
        plugin, node = artifact(self.plugin), artifact(self.node)
        self.assertNotEqual(plugin["version"], node["version"])
        self.assertTrue(plugin["version"].startswith(BASE_VERSION))
        self.assertTrue(node["version"].startswith(BASE_VERSION))
        self.assertEqual(plugin["version"], plugin["version"].split("+")[0]
                         + "+" + plugin["cachebuster"])
        self.assertEqual(node["version"], node["version"].split("+")[0]
                         + "+" + node["cachebuster"])
        # `claude plugin update` compares version strings; a shared one would
        # let one face be read as current while the other's bytes are installed.
        self.assertNotEqual(plugin["cachebuster"], node["cachebuster"])

    def test_the_faces_share_only_the_generated_record(self):
        plugin = set(artifact(self.plugin)["members"])
        node = set(artifact(self.node)["members"])
        self.assertEqual(plugin & node, {"README.md", "ARTIFACT.json"})


class AKindIsAClaimAboutTheBytes(unittest.TestCase):
    """The two directions a static declaration could lie in."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.out = self.base / "codex" / "payload"
        self.market = self.base / "codex" / ".agents" / "plugins" / "marketplace.json"
        self.out.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _plan(self, bundle: Path, *, face=None):
        return build.plan(bundle, Path("/nonexistent-ability"), "0" * 40,
                          self.out, self.market, face=face)

    def test_a_plugin_claim_that_ships_the_daemon_is_refused(self):
        bundle = write_composition(self.base / "claims-plugin.yaml",
                                   kind="plugin", modules=MODULES)
        with self.assertRaises(ValueError) as caught:
            self._plan(bundle)
        self.assertIn("does not declare `node-runtime`", str(caught.exception))
        self.assertIn("bin/serverosd", str(caught.exception))

    def test_a_node_runtime_claim_without_a_daemon_is_refused(self):
        bundle = write_composition(self.base / "claims-node.yaml",
                                   kind="node-runtime", modules="")
        with self.assertRaises(ValueError) as caught:
            self._plan(bundle)
        self.assertIn("declares `node-runtime`", str(caught.exception))
        self.assertIn("does not carry", str(caught.exception))

    def test_a_face_the_composition_does_not_declare_is_refused(self):
        bundle = write_composition(self.base / "node-only.yaml",
                                   kind="node-runtime", modules=MODULES)
        with self.assertRaises(ValueError) as caught:
            self._plan(bundle, face="plugin")
        self.assertIn("not one of this composition's declared artifacts",
                      str(caught.exception))

    def test_an_unknown_face_is_refused(self):
        bundle = write_composition(self.base / "unknown.yaml",
                                   kind="plugin", modules="")
        with self.assertRaises(ValueError) as caught:
            self._plan(bundle, face="daemon")
        self.assertIn("is not one of this composition's declared artifacts",
                      str(caught.exception))


class AForgedRecordCannotCreateAFace(unittest.TestCase):
    """A manifest is a declaration; the verifier checks the bytes behind it."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev = test_build.make_ability_repo(cls.base / "ability")
        cls.plugin = cls._build("plugin", "plugin")
        cls.node = cls._build("node", "node-runtime")

    @classmethod
    def _build(cls, tag, face):
        out = cls.base / tag / "codex" / "payload"
        market = cls.base / tag / "codex" / ".agents" / "plugins" / "marketplace.json"
        assert build.build(BUNDLE, cls.repo, cls.rev, out, market, True,
                           PACK_CONTRACT, face=face) == 0
        return out

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _forge(self, name: str, source: Path, kinds: list[str]) -> Path:
        dest = self.base / name
        shutil.copytree(source, dest)
        doc = artifact(dest)
        doc["kinds"] = kinds
        doc["face"] = kinds[0]
        (dest / "ARTIFACT.json").write_text(json.dumps(doc, indent=2) + "\n")
        return dest

    def test_a_plugin_payload_cannot_be_declared_a_node_runtime(self):
        forged = self._forge("forged-node", self.plugin, ["node-runtime"])
        kind, basis = release.resolve_kind(forged)
        # The declaration is believed about WHICH verifier to run...
        self.assertEqual((kind, basis), ("node-runtime", "declared"))
        # ...and the verifier then refuses it, because the daemon is absent.
        verdict = release.verify_revision(forged, self.base / "state", kind=kind)
        self.assertFalse(verdict["ok"])
        self.assertTrue(any("bin/serverosd" in p for p in verdict["problems"]),
                        verdict["problems"])

    def test_a_node_payload_cannot_be_declared_a_plugin(self):
        forged = self._forge("forged-plugin", self.node, ["plugin"])
        kind, basis = release.resolve_kind(forged)
        self.assertEqual((kind, basis), ("plugin", "declared"))
        verdict = release.verify_revision(forged, self.base / "state", kind=kind)
        self.assertFalse(verdict["ok"])
        self.assertTrue(any("plugin.json" in p for p in verdict["problems"]),
                        verdict["problems"])


class TheFacesReleaseAndRollBackIndependently(unittest.TestCase):
    """Two artifacts, two release roots, two histories."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.rev = test_build.make_ability_repo(self.base / "ability")
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        seed_registry(self.state_dir)
        # `release.py` resolves its default roots while building the parser, and
        # an unbound node refuses that — correctly, rather than inventing a
        # template path. Bind throwaway roots with the suite's own helper for
        # THIS test's subprocesses. The environment is not mutated in-process:
        # a later suite in the same run would inherit roots bound for this one.
        work = self.base / "hermetic"
        work.mkdir()
        self.env = {**os.environ, **hermetic_tests.bind_roots(work)}
        self.node_comp = "serveros-linux-work"
        self.device_comp = "serveros-linux-work-device"

    def tearDown(self):
        self._tmp.cleanup()

    def _build(self, tag: str, face: str, bundle: Path, cachebuster: str | None = None) -> Path:
        source = bundle
        if cachebuster is not None:
            doc = yaml.safe_load(bundle.read_text(encoding="utf-8"))
            doc["cachebuster"] = cachebuster
            source = self.base / f"{tag}.yaml"
            source.write_text(yaml.safe_dump(doc, sort_keys=False))
        out = self.base / tag / "codex" / "payload"
        market = self.base / tag / "codex" / ".agents" / "plugins" / "marketplace.json"
        assert build.build(source, self.repo, self.rev, out, market, True,
                           PACK_CONTRACT, face=face) == 0
        return out

    def _args(self, composition: str) -> list[str]:
        return ["--runtime-root", str(self.runtime_root),
                "--composition", composition,
                "--state-dir", str(self.state_dir),
                "--unit-dir", str(self.unit_dir),
                "--bin-dir", str(self.bin_dir),
                "--no-reload"]

    def _promote(self, artifact_dir: Path, revision: str, composition: str):
        return run_release(["promote", "--artifact", str(artifact_dir),
                            "--revision", revision, *self._args(composition)],
                           env=self.env)

    def _current(self, composition: str):
        return release.current_revision(self.runtime_root / composition)

    def test_a_device_rollback_does_not_move_the_node(self):
        node = self._build("node", "node-runtime", BUNDLE)
        device_one = self._build("device1", "plugin", BUNDLE,
                                 cachebuster="srv0003.20260923c.plugin.v1")
        device_two = self._build("device2", "plugin", BUNDLE,
                                 cachebuster="srv0003.20260923c.plugin.v2")

        self.assertEqual(self._promote(node, "node-a", self.node_comp).returncode, 0)
        self.assertEqual(self._promote(device_one, "device-a", self.device_comp).returncode, 0)
        self.assertEqual(self._promote(device_two, "device-b", self.device_comp).returncode, 0)
        self.assertEqual(self._current(self.device_comp), "device-b")
        self.assertEqual(self._current(self.node_comp), "node-a")

        rollback = run_release(["rollback", *self._args(self.device_comp)], env=self.env)
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        self.assertEqual(self._current(self.device_comp), "device-a")
        # The node's history is a different root and did not move.
        self.assertEqual(self._current(self.node_comp), "node-a")

        node_receipt = json.loads(
            (self.runtime_root / self.node_comp / "revisions" / "node-a"
             / release.TREE_MANIFEST).read_text())
        device_receipt = json.loads(
            (self.runtime_root / self.device_comp / "revisions" / "device-a"
             / release.TREE_MANIFEST).read_text())
        self.assertEqual(node_receipt["kind"], "node-runtime")
        self.assertEqual(device_receipt["kind"], "plugin")
        self.assertTrue((self.runtime_root / self.node_comp / "current/bin/serverosd").is_file())
        self.assertFalse((self.runtime_root / self.device_comp / "current/bin/serverosd").exists())


class TheNodeFaceNeedsNoAbilityCheckout(unittest.TestCase):
    """The runtime build must not open ability-management at all."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *extra: str):
        out = self.base / "payload"
        market = self.base / ".agents" / "plugins" / "marketplace.json"
        return subprocess.run(
            [sys.executable, str(BUILDER), "--write",
             "--bundle", str(BUNDLE), "--output", str(out),
             "--marketplace-path", str(market), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_the_node_face_builds_with_no_ability_repo_and_no_pin(self):
        result = self._run("--face", "node-runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads((self.base / "payload" / "ARTIFACT.json").read_text())
        self.assertEqual(doc["kinds"], ["node-runtime"])
        self.assertIsNone(doc["source"]["ability_management"]["revision"])

    def test_the_plugin_face_still_requires_the_pinned_source(self):
        result = self._run("--face", "plugin")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--ability-repo is required", result.stderr)


class ADeclarationDoesNotMakeAnAbilityReady(unittest.TestCase):
    """`installed` is not `ready`, and neither is a field a build may write.

    The device projection is the surface that separates them: `installed` comes
    from the authority's manifest, while `supported`, `ready` and `grant` come
    from what the device reported. A device that reported its installation and
    not its health must read `UNKNOWN` there and `effective: null` here — the
    resolver's AVAILABLE is not a licence for the projection to call it callable.
    Read-only: this test imports the existing module and its fixtures.
    """

    @classmethod
    def setUpClass(cls):
        here = ROOT / "integrations" / "device-ability-projection"
        cls.projection = _load("device_ability_effective_d1", here / "effective.py")
        cls.manifest = json.loads((here / "fixtures" / "manifest.json").read_text())
        cls.devices = json.loads((here / "fixtures" / "devices.json").read_text())
        cls.revision = "494f45293e53bc8e84e2025f7ac567e6cfb607d8"

    def test_a_device_that_reported_its_install_but_not_its_health_is_unknown(self):
        device = next(d for d in self.devices
                      if str(d.get("_scenario", "")).startswith("no health"))
        projection = self.projection.project_device(
            manifest=self.manifest, source_revision=self.revision, device=device)
        self.assertTrue(projection["abilities"])
        for ability in projection["abilities"]:
            with self.subTest(ability=ability["ability"]):
                self.assertTrue(ability["installed"])
                self.assertEqual(ability["ready"], "UNKNOWN")
                self.assertEqual(ability["grant"], "UNKNOWN")
                self.assertIsNone(ability["effective"])


if __name__ == "__main__":
    unittest.main()
