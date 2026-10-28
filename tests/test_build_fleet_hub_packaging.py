#!/usr/bin/env python3
"""D2 stage 2 — `fleet_hub` is packaged, and the packaging lists fail closed.

WHY THIS FILE EXISTS
--------------------
#447 physically moved the L2 audience rule to `fleet_hub/fleet_audience.py`,
registered `fleet_hub: {path: fleet_hub}` in `plugin/bundle.yaml`, and left the
second registration list — `scripts/build.py#CORE_MODULES` (this lane) — to the
build owner. Until it lands, the build copies no `fleet_hub` directory while
`runtime/fleet_monitor_plan.py` still loads it, so a source checkout runs and an
installed node fails later with `FileNotFoundError`.

The old loop hid that in one direction: a `CORE_MODULES` member with no bundle
path was skipped with `continue`, and a bundle path with no `CORE_MODULES`
member was never visited at all. Both are now refusals.

This file is the build-side counterpart to `tests/test_packaging_closure.py`
and `tests/test_fleet_hub_boundary.py` (both pytest-only). It pins, in unittest:

1. `fleet_hub` is in `CORE_MODULES` and in the bundle, with the same path;
2. every packaged source reaches only `CORE_MODULES` directories, and
   `runtime/fleet_monitor_plan.py` really reaches `fleet_hub`;
3. the node-runtime artifact carries `fleet_hub/fleet_audience.py` byte-for-byte;
   the plugin face does not;
4. removing EITHER registration makes a node build refuse by name — never a
   silent skip;
5. a product directory block (`reports`) added to a composition without a
   `CORE_MODULES` entry is refused, not packaged: the packaging list is not a
   layer authority and cannot make something "core";
6. building does not modify `docs/architecture/layers.yaml` (#414 owns it).

Hermetic: synthetic ability-management repository, temp bundle copies, temp
artifacts. No install, service, credential or network.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
BUNDLE = REPO / "plugin" / "bundle.yaml"
PACK_CONTRACT = REPO / "abilities" / "pack-contract.yaml"
LAYERS = REPO / "docs" / "architecture" / "layers.yaml"

sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(REPO / "scripts"))
import test_build  # noqa: E402  (the synthetic authority source)
import loader_graph  # noqa: E402


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = _load("serveros_build_d2_stage2", REPO / "scripts" / "build.py")


def bundle_document() -> dict:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def temp_bundle(base: Path, mutate) -> Path:
    document = bundle_document()
    mutate(document)
    path = base / "composition.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def reached_tops() -> dict[str, list[str]]:
    """The packaging-closure rule, re-derived with the same helper it uses."""
    reached: dict[str, list[str]] = {}
    for module in build.CORE_MODULES:
        for source, _rel in build.local_skill_files(REPO / module):
            if source.suffix != ".py":
                continue
            rel = source.resolve().relative_to(REPO)
            for ref in loader_graph.module_refs(rel):
                parts = ref.parts
                top = parts[0] if len(parts) > 1 else f"<repository root>/{parts[0]}"
                reached.setdefault(top, []).append(rel.as_posix())
    return reached


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev = test_build.make_ability_repo(cls.base / "ability")
        cls.market = cls.base / ".agents" / "plugins" / "marketplace.json"
        cls.node = cls._build("node", "node-runtime", BUNDLE)
        cls.plugin = cls._build("plugin", "plugin", BUNDLE)
        cls.combined = cls._build("combined", None, BUNDLE)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _build(cls, tag: str, face, bundle: Path) -> Path:
        out = cls.base / tag / "payload"
        assert build.build(bundle, cls.repo, cls.rev, out, cls.market, True,
                           PACK_CONTRACT, face=face) == 0
        return out

    def plan(self, bundle: Path, face=None):
        out = self.base / f"plan-{self._testMethodName}" / "payload"
        return build.plan(bundle, self.repo, self.rev, out, self.market, face=face)


class TheTwoListsAgree(unittest.TestCase):
    def test_fleet_hub_is_a_core_module_with_its_bundle_path(self):
        self.assertIn("fleet_hub", build.CORE_MODULES)
        self.assertEqual(bundle_document()["fleet_hub"]["path"], "fleet_hub")

    def test_every_core_module_declares_its_path(self):
        bundle = bundle_document()
        undeclared = [m for m in build.CORE_MODULES
                      if not (bundle.get(m) or {}).get("path")]
        self.assertEqual(undeclared, [])

    def test_every_bundle_path_is_a_core_module(self):
        bundle = bundle_document()
        with_path = {k for k, v in bundle.items()
                     if isinstance(v, dict) and "path" in v}
        self.assertEqual(sorted(with_path - set(build.CORE_MODULES)), [])

    def test_every_packaged_source_reaches_only_packaged_directories(self):
        reached = reached_tops()
        self.assertIn("fleet_hub", reached,
                      "runtime/fleet_monitor_* must reach the hub package")
        self.assertEqual(sorted(top for top in reached
                                if top not in build.CORE_MODULES), [])

    def test_the_monitor_stack_really_loads_the_hub(self):
        refs = set(loader_graph.module_refs(
            Path("runtime/fleet_monitor_plan.py")))
        self.assertIn(Path("fleet_hub/fleet_audience.py"), refs)


class TheArtifactCarriesIt(Fixture):
    def test_the_node_face_ships_fleet_hub_byte_for_byte(self):
        member = "fleet_hub/fleet_audience.py"
        artifact = json.loads((self.node / "ARTIFACT.json").read_text())
        self.assertIn(member, artifact["members"])
        self.assertEqual((self.node / member).read_bytes(),
                         (REPO / member).read_bytes())

    def test_the_plugin_face_is_still_skill_only(self):
        members = json.loads((self.plugin / "ARTIFACT.json").read_text())["members"]
        self.assertFalse([m for m in members if m.startswith("fleet_hub/")])
        self.assertFalse((self.plugin / "fleet_hub").exists())

    def test_the_combined_artifact_still_ships_both_faces(self):
        members = json.loads((self.combined / "ARTIFACT.json").read_text())["members"]
        self.assertIn("fleet_hub/fleet_audience.py", members)
        self.assertIn("skills/serveros/SKILL.md", members)


class AMissingRegistrationFailsClosed(Fixture):
    def test_a_core_module_with_no_bundle_path_is_refused(self):
        def drop(document):
            document.pop("fleet_hub")
        with self.assertRaises(ValueError) as caught:
            self.plan(temp_bundle(self.base, drop), face="node-runtime")
        self.assertIn("fleet_hub", str(caught.exception))
        self.assertIn("no `path`", str(caught.exception))

    def test_a_bundle_path_with_no_core_module_is_refused(self):
        original = build.CORE_MODULES
        build.CORE_MODULES = tuple(m for m in original if m != "fleet_hub")
        try:
            with self.assertRaises(ValueError) as caught:
                self.plan(BUNDLE, face="node-runtime")
        finally:
            build.CORE_MODULES = original
        self.assertIn("fleet_hub", str(caught.exception))
        self.assertIn("not in scripts/build.py#CORE_MODULES", str(caught.exception))

    def test_a_plugin_composition_cannot_ship_half_the_modules(self):
        """A `[plugin]` composition with non-node module blocks is still a node
        artifact that forgot its members; the old gate skipped it because
        `node-runtime` was not declared and no required member was carried."""
        def half(document):
            document["artifacts"] = ["plugin"]
            keep = {"documents"}
            for key in list(document):
                value = document[key]
                if isinstance(value, dict) and "path" in value and key not in keep:
                    document.pop(key)
        with self.assertRaises(ValueError) as caught:
            self.plan(temp_bundle(self.base, half))
        self.assertIn("no `path` in the composition", str(caught.exception))

    def test_a_product_directory_cannot_be_packaged_as_core(self):
        """`reports` is a product layer; a path block must not make it core."""
        layers = yaml.safe_load(LAYERS.read_text(encoding="utf-8"))
        reports = (layers.get("directories") or {}).get("reports")
        if isinstance(reports, str):
            self.assertNotEqual(reports, "L1")
        elif isinstance(reports, dict):
            self.assertNotIn("L1", reports.get("parts") or {})
        self.assertNotIn("reports", build.CORE_MODULES)
        self.assertNotIn("reports", bundle_document())

        def add_product(document):
            document["reports"] = {"path": "reports"}
        before = hashlib.sha256(LAYERS.read_bytes()).hexdigest()
        with self.assertRaises(ValueError) as caught:
            self.plan(temp_bundle(self.base, add_product), face="node-runtime")
        self.assertIn("reports", str(caught.exception))
        self.assertIn("not in scripts/build.py#CORE_MODULES", str(caught.exception))
        self.assertNotIn("reports", build.CORE_MODULES)
        self.assertEqual(hashlib.sha256(LAYERS.read_bytes()).hexdigest(), before,
                         "a packaging attempt must not touch the layer table")

    def test_a_plugin_only_root_composition_is_not_a_node_artifact(self):
        """No module blocks ⇒ nothing to agree about; it still builds.

        A minimal root composition (the shape `plugin/profiles/testing.yaml`
        has) with one local Skill and no module block. The synthetic authority
        fixture does not carry the testing profile's own Skills, so the fixture
        is the composition rather than the profile.
        """
        document = {
            "schema": "serveros/bundle.v1", "artifacts": ["plugin"],
            "composition": "plugin-only-fixture", "name": "plugin-only-fixture",
            "display_name": "plugin-only-fixture", "description": "fixture",
            "short_description": "fixture", "long_description": "fixture",
            "author": "test", "category": "Productivity", "license": "MIT",
            "brand_color": "#000000", "version": "0.0.1",
            "cachebuster": "fixture.pluginonly",
            "default_prompt": ["fixture"],
            "skills": [{"name": "serveros", "source": "local"}],
        }
        path = self.base / f"plugin-only-{self._testMethodName}.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        files, manifests, _readme, _market, _version = self.plan(path)
        rels = {rel.as_posix() for _c, rel, _e in files}
        self.assertFalse([rel for rel in rels if rel.startswith("fleet_hub/")])
        self.assertIn("skills/serveros/SKILL.md", rels)


class PackagingDoesNotChangeLayerAuthority(Fixture):
    def test_building_does_not_modify_the_layer_table(self):
        before = hashlib.sha256(LAYERS.read_bytes()).hexdigest()
        self.plan(BUNDLE, face="node-runtime")
        self.assertEqual(hashlib.sha256(LAYERS.read_bytes()).hexdigest(), before)

    def test_fleet_hub_is_registered_as_an_l2_directory(self):
        """#414 owns the table; this lane only refuses a layer claim.

        Red until #414 registers `fleet_hub`. When it lands, the entry must be
        an L2-hub directory and never L1 or product — the shape is not guessed
        here, so a split entry with other L2 part names is accepted.
        """
        layers = yaml.safe_load(LAYERS.read_text(encoding="utf-8"))
        entry = (layers.get("directories") or {}).get("fleet_hub")
        self.assertIsNotNone(
            entry,
            "`fleet_hub` is packaged but unregistered in layers.yaml; #414 owns "
            "that file and must register it as an L2-hub directory")
        names = {entry} if isinstance(entry, str) else set(
            (entry.get("parts") or {}))
        self.assertNotIn("L1", names, "fleet_hub is not an L1 directory")
        self.assertNotIn("product", names, "fleet_hub is not a product directory")
        self.assertTrue(any(name.startswith("L2") for name in names),
                        f"fleet_hub must live at L2; got {sorted(names)}")

    def test_packaging_list_contains_no_product_display_directory(self):
        for product in ("reports", "cards", "notifications"):
            self.assertNotIn(product, build.CORE_MODULES)


if __name__ == "__main__":
    unittest.main()
