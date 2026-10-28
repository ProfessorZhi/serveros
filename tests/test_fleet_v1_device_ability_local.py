#!/usr/bin/env python3
"""#434 L3 — the local Device Ability package, and the states it may not fake.

WHAT THIS FILE PINS
-------------------
The device Ability family is only real if a device can answer, from its own
disk: which version is installed, are those the built bytes, does the
authority's catalog still support it, and is it READY/GRANTED/EFFECTIVE or
UNKNOWN. `scripts/build.py --face plugin` (#413) and `runtime_truth` (#421)
settle the artifact; this file pins the local package and its state contract:

* the package is the plugin face and carries no node member;
* every member is hash-frozen; a missing manifest, a changed byte, an extra
  file or a forged face is TAMPERED and cannot be installed;
* install/rollback move one atomic pointer only after verification; a tampered
  previous revision cannot be rolled back to;
* the catalog is the one `ability-management` revision the build pinned; a
  different authority revision is DRIFT, never silently resolved;
* `installed`, `supported`, `ready`, `granted`, `effective` are reported
  separately, `effective` is null unless every decisive term was reported, and
  no missing grant/health can read as ready;
* the caretaker is presentation only: a closed facts envelope, no Run, no
  grant, no state change;
* changing a Skill produces a new package revision and leaves the node
  artifact byte-identical (no L1/L2 rebuild);
* the package is importable and collectable on Windows (no POSIX-only import
  or mutation verb).

Offline: synthetic ability-management repository, temp stores, fake clock. No
network, no provider, no credential, no service, no global install.
"""
from __future__ import annotations

import ast
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
PACKAGE_DIR = REPO / "integrations" / "fleet-v1-device-ability-local"
MODULE_PATH = PACKAGE_DIR / "device_ability_local.py"
BUNDLE = REPO / "plugin" / "bundle.yaml"
PACK_CONTRACT = REPO / "abilities" / "pack-contract.yaml"
STAMP = "2026-10-28T00:00:00+00:00"

sys.path.insert(0, str(TESTS))
import test_build  # noqa: E402  (the synthetic authority repository)


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


local = _load("device_ability_local", MODULE_PATH)


def clock() -> str:
    return STAMP


def bundle_with(base: Path, tag: str, cachebuster: str) -> Path:
    document = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))
    document["cachebuster"] = cachebuster
    path = base / f"{tag}.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


class Fixture(unittest.TestCase):
    """One synthetic authority, four package revisions, two node artifacts."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev1 = test_build.make_ability_repo(cls.base / "ability")
        skill = cls.repo / "skills" / "runtime-management" / "payload" / "SKILL.md"
        skill.write_text(skill.read_text() + "\nsecond catalog revision\n")
        test_build._git(cls.repo, "add", "-A")
        test_build._git(cls.repo, "commit", "--quiet", "-m", "rev2")
        cls.rev2 = test_build._git(cls.repo, "rev-parse", "HEAD")
        cls.bundle_v1 = BUNDLE
        cls.bundle_v2 = bundle_with(cls.base, "v2", "srv0003.20260923c.local.v2")

        cls.root1 = cls.base / "store1"
        cls.store1 = local.PackageStore(
            cls.root1, catalog_revision=cls.rev1, clock=clock,
            source={"ability_repo": cls.repo, "ability_rev": cls.rev1})
        cls.pkg1 = cls.store1.build(
            bundle=cls.bundle_v1, ability_repo=cls.repo, ability_rev=cls.rev1,
            pack_contract=PACK_CONTRACT,
            marketplace=cls.root1 / ".agents" / "plugins" / "marketplace.json")

        cls.root2 = cls.base / "store2"
        cls.pkg2 = local.PackageStore(
            cls.root2, catalog_revision=cls.rev2, clock=clock,
            source={"ability_repo": cls.repo, "ability_rev": cls.rev2}).build(
            bundle=cls.bundle_v2, ability_repo=cls.repo, ability_rev=cls.rev2,
            pack_contract=PACK_CONTRACT,
            marketplace=cls.root2 / ".agents" / "plugins" / "marketplace.json")

        cls.node_a = cls._node(cls.base / "node-a", cls.rev1)
        cls.node_b = cls._node(cls.base / "node-b", cls.rev2)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _node(cls, base: Path, rev: str) -> Path:
        build = local._build()
        out = base / "codex" / "payload"
        market = base / "codex" / ".agents" / "plugins" / "marketplace.json"
        code = build.build(BUNDLE, cls.repo, rev, out, market, True,
                           PACK_CONTRACT, face="node-runtime")
        assert code == 0
        return out

    def copy_store(self, name: str = "copy") -> Path:
        self._copies = getattr(self, "_copies", 0) + 1
        root = self.base / f"{name}-{self._testMethodName}-{self._copies}"
        shutil.copytree(self.root1, root)
        return root

    def store_on(self, root: Path, catalog: str | None = None,
                 source_rev: str | None = None, with_source: bool = True):
        kwargs = {"source": {"ability_repo": self.repo,
                             "ability_rev": source_rev or self.rev1}} \
            if with_source else {}
        return local.PackageStore(
            root, catalog_revision=catalog or self.rev1, clock=clock, **kwargs)


class ThePackageIsThePluginFace(Fixture):
    def test_it_holds_no_node_member_and_freezes_every_hash(self):
        manifest = self.pkg1
        self.assertEqual(manifest["schema"], local.SCHEMA_PACKAGE)
        self.assertEqual(manifest["face"], "plugin")
        self.assertEqual(manifest["kinds"], ["plugin"])
        self.assertEqual(manifest["catalog_revision"], self.rev1)
        self.assertTrue(manifest["skills"])
        self.assertFalse(manifest["mcp_servers"])
        self.assertGreater(len(manifest["members"]), 0)
        for rel in ("bin/serverosd", "runtime/daemon.py", "workengine/schema.sql"):
            self.assertNotIn(rel, manifest["members"])
            self.assertFalse((self.store1.revision_dir(manifest["version"])
                              / local.PAYLOAD / rel).exists())
        for digest in manifest["members"].values():
            self.assertTrue(digest.startswith("sha256:"))

    def test_readback_before_install_is_not_installed(self):
        read = self.store1.readback()
        self.assertFalse(read["installed"])
        self.assertEqual(read["state"], "NOT_INSTALLED")

    def test_install_verifies_then_moves_one_pointer(self):
        root = self.copy_store()
        store = self.store_on(root)
        read = store.install(self.pkg1["version"])
        self.assertTrue(read["changed"])
        self.assertTrue(read["installed"])
        again = store.install(self.pkg1["version"])
        self.assertFalse(again["changed"], "installing the same version is a no-op")

    def test_an_unknown_version_is_refused(self):
        with self.assertRaises(local.Refused):
            self.store1.install("9.9.9+nope.plugin")

    def test_a_bundle_without_the_plugin_face_is_refused(self):
        node_only = self.base / "node-only.yaml"
        document = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))
        document["artifacts"] = ["node-runtime"]
        node_only.write_text(yaml.safe_dump(document, sort_keys=False))
        root = self.base / f"refuse-{self._testMethodName}"
        store = local.PackageStore(
            root, catalog_revision=self.rev1, clock=clock,
            source={"ability_repo": self.repo, "ability_rev": self.rev1})
        with self.assertRaises(local.Refused):
            store.build(bundle=node_only, ability_repo=self.repo,
                        ability_rev=self.rev1, pack_contract=PACK_CONTRACT,
                        marketplace=root / "m" / "marketplace.json")


class ATamperedPackageIsNeverReady(Fixture):
    def _tamper(self, mutate) -> Path:
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        mutate(store.revision_dir(self.pkg1["version"]))
        return root

    def test_a_missing_manifest_is_tampered(self):
        root = self._tamper(lambda rev: (rev / local.PACKAGE_MANIFEST).unlink())
        read = self.store_on(root).readback()
        self.assertFalse(read["installed"])
        self.assertEqual(read["state"], "TAMPERED")

    def test_a_changed_payload_byte_is_tampered(self):
        def mutate(rev):
            target = rev / local.PAYLOAD / "README.md"
            target.write_text(target.read_text() + "tampered\n")
        read = self.store_on(self._tamper(mutate)).readback()
        self.assertEqual((read["installed"], read["state"]), (False, "TAMPERED"))
        with self.assertRaises(local.Refused):
            self.store_on(self._tamper(mutate)).install(self.pkg1["version"])

    def test_an_extra_payload_file_is_tampered(self):
        def mutate(rev):
            (rev / local.PAYLOAD / "extra.txt").write_text("extra\n")
        read = self.store_on(self._tamper(mutate)).readback()
        self.assertEqual(read["state"], "TAMPERED")
        self.assertIn("extra", read["why"])

    def test_edited_member_hashes_are_tampered(self):
        def mutate(rev):
            path = rev / local.PACKAGE_MANIFEST
            doc = json.loads(path.read_text())
            doc["members"] = {}
            path.write_text(json.dumps(doc))
        read = self.store_on(self._tamper(mutate)).readback()
        self.assertEqual(read["state"], "TAMPERED")

    def test_a_forged_face_is_tampered(self):
        def mutate(rev):
            path = rev / local.PACKAGE_MANIFEST
            doc = json.loads(path.read_text())
            doc["face"] = "node-runtime"
            doc["kinds"] = ["node-runtime"]
            path.write_text(json.dumps(doc))
        read = self.store_on(self._tamper(mutate)).readback()
        self.assertEqual(read["state"], "TAMPERED")

    def test_rewriting_the_payload_and_its_hashes_is_caught_by_recomposition(self):
        def mutate(rev):
            target = rev / local.PAYLOAD / "README.md"
            target.write_text(target.read_text() + "rewritten\n")
            path = rev / local.PACKAGE_MANIFEST
            doc = json.loads(path.read_text())
            doc["members"] = local._members(rev / local.PAYLOAD)
            path.write_text(json.dumps(doc))
        read = self.store_on(self._tamper(mutate)).readback()
        self.assertFalse(read["installed"])
        self.assertEqual(read["state"], "TAMPERED")
        self.assertIn("recomposition", read["why"])

    def test_without_a_source_the_readback_says_it_was_not_verified(self):
        root = self.copy_store()
        store = local.PackageStore(root, catalog_revision=self.rev1, clock=clock)
        store.install(self.pkg1["version"])
        read = store.readback()
        self.assertTrue(read["installed"])
        self.assertFalse(read["source_verified"])
        self.assertEqual(read["recomposition"], "UNAVAILABLE")

    def test_node_members_in_a_forged_revision_are_tampered(self):
        root = self.copy_store()
        store = self.store_on(root)
        source = store.revision_dir(self.pkg1["version"])
        forged = store.revision_dir("9.9.9+forged.plugin")
        shutil.copytree(source, forged)
        (forged / local.PAYLOAD / "bin").mkdir(exist_ok=True)
        (forged / local.PAYLOAD / "bin" / "serverosd").write_text("#!/bin/sh\n")
        doc = json.loads((forged / local.PACKAGE_MANIFEST).read_text())
        doc["version"] = "9.9.9+forged.plugin"
        doc["members"] = {str(p.relative_to(forged / local.PAYLOAD)): "sha256:" + "0" * 64
                          for p in (forged / local.PAYLOAD).rglob("*") if p.is_file()}
        (forged / local.PACKAGE_MANIFEST).write_text(json.dumps(doc))
        read = store.readback("9.9.9+forged.plugin")
        self.assertFalse(read["installed"])
        self.assertEqual(read["state"], "TAMPERED")
        self.assertIn("node members", read["why"])


class CatalogDriftIsNotSuccess(Fixture):
    def test_a_moved_authority_revision_is_drift(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        moved = self.store_on(root, catalog=self.rev2, with_source=False)
        read = moved.readback()
        self.assertTrue(read["catalog_drift"])
        self.assertEqual(read["state"], "DRIFT")

        state = local.states(
            package=read,
            resolved={"status": "AVAILABLE", "provider": "provider:x", "reason": None},
            observed=("device_capabilities", "profile", "health", "policy"))
        self.assertEqual(state["catalog_state"], "DRIFT")
        self.assertEqual(state["supported"], "UNKNOWN")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "DRIFT")

    def test_install_refuses_drift_unless_explicitly_allowed(self):
        root = self.copy_store()
        moved = self.store_on(root, catalog=self.rev2, with_source=False)
        with self.assertRaises(local.Refused) as caught:
            moved.install(self.pkg1["version"])
        self.assertIn("CATALOG_DRIFT", str(caught.exception))
        allowed = moved.install(self.pkg1["version"], allow_drift=True)
        self.assertTrue(allowed["changed"])
        self.assertEqual(moved.readback()["state"], "DRIFT")

    def test_a_package_built_at_the_current_revision_does_not_drift(self):
        read = self.store_on(self.root2, catalog=self.rev2,
                             with_source=False).readback(self.pkg2["version"])
        self.assertTrue(read["installed"])
        self.assertFalse(read["catalog_drift"])


class StatesAreStrict(Fixture):
    ALL = ("device_capabilities", "profile", "health", "policy")

    def read(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        return store.readback()

    def state(self, package, observed=ALL,
              resolved=None):
        return local.states(
            package=package,
            resolved=resolved or {"status": "AVAILABLE", "provider": "provider:x",
                                  "reason": None},
            observed=observed)

    def test_a_fully_observed_ability_is_effective(self):
        state = self.state(self.read())
        self.assertEqual(state["installed"], True)
        self.assertEqual(state["supported"], "SUPPORTED")
        self.assertEqual(state["ready"], "READY")
        self.assertEqual(state["granted"], "ALLOWED")
        self.assertEqual(state["effective"], "provider:x")
        self.assertEqual(state["status"], "EFFECTIVE")

    def test_a_missing_grant_is_not_ready_or_effective(self):
        state = self.state(self.read(),
                           observed=("device_capabilities", "profile", "health"))
        self.assertEqual(state["granted"], "UNKNOWN")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "UNKNOWN")

    def test_missing_health_is_unknown_even_when_the_resolver_says_available(self):
        state = self.state(self.read(),
                           observed=("device_capabilities", "profile", "policy"))
        self.assertEqual(state["ready"], "UNKNOWN")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "UNKNOWN")

    def test_missing_support_axes_are_unknown(self):
        state = self.state(self.read(), observed=("health", "policy"))
        self.assertEqual(state["supported"], "UNKNOWN")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "UNKNOWN")

    def test_a_denied_policy_is_denied(self):
        state = self.state(self.read(), resolved={
            "status": "AVAILABLE", "provider": "provider:x",
            "reason": "POLICY_DENIED"})
        self.assertEqual(state["granted"], "DENIED")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "DENIED")

    def test_an_unhealthy_runtime_is_not_ready(self):
        state = self.state(self.read(), resolved={
            "status": "DEGRADED", "provider": "provider:x",
            "reason": "RUNTIME_UNHEALTHY"})
        self.assertEqual(state["ready"], "NOT_READY")
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "NOT_READY")

    def test_an_unknown_resolver_status_is_unknown(self):
        state = self.state(self.read(), resolved={
            "status": "UNKNOWN", "provider": None, "reason": "NO_OBSERVATION"})
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "UNKNOWN")

    def test_not_installed_dominates_every_term(self):
        state = self.state(self.store1.readback())
        self.assertEqual(state["installed"], False)
        self.assertIsNone(state["effective"])
        self.assertEqual(state["status"], "NOT_INSTALLED")

    def test_a_hand_built_installed_mapping_is_refused(self):
        with self.assertRaises(local.Refused):
            local.states(package={"installed": True},
                         resolved={"status": "AVAILABLE", "provider": "p"},
                         observed=self.ALL)

    def test_a_degraded_resolver_is_degraded_not_effective(self):
        state = self.state(self.read(), resolved={
            "status": "DEGRADED", "provider": "provider:x", "reason": None})
        self.assertEqual(state["status"], "DEGRADED")
        self.assertEqual(state["effective"], "provider:x")

    def test_status_effective_iff_a_provider_is_reported(self):
        cases = [
            (self.ALL, {"status": "AVAILABLE", "provider": "provider:x", "reason": None}),
            (("device_capabilities", "profile", "health"), {"status": "AVAILABLE",
                                                            "provider": "provider:x",
                                                            "reason": None}),
            (self.ALL, {"status": "UNKNOWN", "provider": None, "reason": "X"}),
            (self.ALL, {"status": "AVAILABLE", "provider": "provider:x",
                        "reason": "POLICY_DENIED"}),
            (self.ALL, {"status": "DEGRADED", "provider": "provider:x",
                        "reason": None}),
        ]
        for observed, resolved in cases:
            with self.subTest(observed=observed, resolved=resolved):
                state = self.state(self.read(), observed=observed, resolved=resolved)
                self.assertEqual(state["status"] in ("EFFECTIVE", "DEGRADED"),
                                 state["effective"] is not None)


class RollbackIsAPointerWrite(Fixture):
    def test_two_versions_install_and_roll_back_without_deleting(self):
        root = self.base / f"rollback-{self._testMethodName}"
        store = local.PackageStore(root, catalog_revision=self.rev1, clock=clock)
        market = root / ".agents" / "plugins" / "marketplace.json"
        one = store.build(bundle=bundle_with(self.base, "r1", "srv0003.20260923c.local.r1"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        two = store.build(bundle=bundle_with(self.base, "r2", "srv0003.20260923c.local.r2"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        self.assertNotEqual(one["version"], two["version"])
        store.install(one["version"])
        store.install(two["version"])
        self.assertEqual(store.readback()["version"], two["version"])
        back = store.rollback()
        self.assertEqual(back["rolled_back_to"], one["version"])
        self.assertEqual(store.readback()["version"], one["version"])
        again = store.rollback()
        self.assertEqual(again["rolled_back_to"], two["version"])
        self.assertEqual(store.readback()["version"], two["version"])
        # Both revisions still exist: rollback is not deletion.
        self.assertTrue(store.revision_dir(one["version"]).is_dir())
        self.assertTrue(store.revision_dir(two["version"]).is_dir())

    def test_rollback_without_previous_is_refused(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        with self.assertRaises(local.Refused):
            store.rollback()

    def test_rollback_to_a_tampered_previous_is_refused(self):
        root = self.base / f"rollback-tamper-{self._testMethodName}"
        store = local.PackageStore(root, catalog_revision=self.rev1, clock=clock)
        market = root / ".agents" / "plugins" / "marketplace.json"
        one = store.build(bundle=bundle_with(self.base, "t1", "srv0003.20260923c.local.t1"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        two = store.build(bundle=bundle_with(self.base, "t2", "srv0003.20260923c.local.t2"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        store.install(one["version"])
        store.install(two["version"])
        readme = store.revision_dir(one["version"]) / local.PAYLOAD / "README.md"
        readme.write_text(readme.read_text() + "tampered\n")
        with self.assertRaises(local.Refused):
            store.rollback()
        self.assertEqual(store.readback()["version"], two["version"])


class RollbackHonoursCatalogDrift(Fixture):
    def test_rollback_refuses_a_drifted_previous_unless_allowed(self):
        root = self.base / f"rollback-drift-{self._testMethodName}"
        store = local.PackageStore(root, catalog_revision=self.rev1, clock=clock)
        market = root / ".agents" / "plugins" / "marketplace.json"
        one = store.build(bundle=bundle_with(self.base, "d1", "srv0003.20260923c.local.d1"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        two = store.build(bundle=bundle_with(self.base, "d2", "srv0003.20260923c.local.d2"),
                          ability_repo=self.repo, ability_rev=self.rev1,
                          pack_contract=PACK_CONTRACT, marketplace=market)
        store.install(one["version"])
        store.install(two["version"])
        moved = self.store_on(root, catalog=self.rev2, with_source=False)
        with self.assertRaises(local.Refused) as caught:
            moved.rollback()
        self.assertIn("CATALOG_DRIFT", str(caught.exception))
        allowed = moved.rollback(allow_drift=True)
        self.assertEqual(allowed["rolled_back_to"], one["version"])
        self.assertEqual(moved.readback()["state"], "DRIFT")


class TheCaretakerOnlyExplains(Fixture):
    def facts(self, state):
        return {key: state[key] for key in
                ("status", "installed", "supported", "ready", "granted",
                 "effective", "version", "catalog_state", "why")}

    def test_effective_facts_render_without_authority(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        state = local.states(
            package=store.readback(),
            resolved={"status": "AVAILABLE", "provider": "provider:x", "reason": None},
            observed=("device_capabilities", "profile", "health", "policy"))
        text = local.caretaker_text(self.facts(state))
        self.assertIn("EFFECTIVE", text)
        self.assertIn("effective=provider:x", text)

    def test_unknown_facts_say_nothing_was_claimed(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        state = local.states(
            package=store.readback(),
            resolved={"status": "AVAILABLE", "provider": "provider:x", "reason": None},
            observed=("device_capabilities", "profile"))
        text = local.caretaker_text(self.facts(state))
        self.assertIn("UNKNOWN", text)
        self.assertIn("nothing is claimed", text)
        self.assertNotIn("effective=provider:x", text)

    def test_a_failing_model_only_drops_the_gloss(self):
        def boom(_facts):
            raise RuntimeError("model down")
        state = {"status": "UNKNOWN", "installed": True, "supported": "UNKNOWN",
                 "ready": "UNKNOWN", "granted": "UNKNOWN", "effective": None,
                 "version": "v", "catalog_state": "CURRENT", "why": {}}
        text = local.caretaker_text(state, summarizer=boom)
        self.assertIn("UNKNOWN", text)
        self.assertNotIn("gloss", text)

    def test_a_gloss_cannot_add_authority_words_to_the_facts(self):
        state = {"status": "UNKNOWN", "installed": True, "supported": "UNKNOWN",
                 "ready": "UNKNOWN", "granted": "UNKNOWN", "effective": None,
                 "version": "v", "catalog_state": "CURRENT", "why": {}}
        before = dict(state)
        local.caretaker_text(
            state, summarizer=lambda _f: "READY, GRANTED and EFFECTIVE")
        self.assertEqual(state, before, "the caretaker must not mutate the facts")

    def test_an_unknown_state_word_is_refused(self):
        with self.assertRaises(local.Refused):
            local.caretaker_text({"status": "GRANTED", "installed": True})

    def test_contradictory_effective_facts_are_refused(self):
        with self.assertRaises(local.Refused):
            local.caretaker_text({"status": "EFFECTIVE", "installed": True,
                                  "supported": "SUPPORTED", "ready": "READY",
                                  "granted": "UNKNOWN", "effective": "provider:x",
                                  "catalog_state": "CURRENT"})

    def test_an_authority_key_is_refused(self):
        for key in ("grant", "run_state", "principal", "authorization"):
            with self.subTest(key=key):
                with self.assertRaises(local.Refused):
                    local.caretaker_text({"status": "EFFECTIVE", key: "ALLOWED"})

    def test_the_module_cannot_run_or_grant(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        forbidden = {"fleet", "fleet_mcp", "runtime", "subprocess", "requests",
                     "socket", "urllib"}
        self.assertEqual(imports & forbidden, set())
        calls = {node.func.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertEqual(calls & {"notify", "grant", "send", "run"}, set())
        for key in ("grant", "run_state", "principal", "authorization"):
            self.assertNotIn(key, local.CARETAKER_FACTS)
        self.assertIn('face="plugin"', source)


class ASkillUpdateDoesNotRebuildTheNode(Fixture):
    def test_the_node_face_is_byte_identical_across_skill_revisions(self):
        one, two = tree(self.node_a), tree(self.node_b)
        self.assertGreater(len(one), 0, "both node builds are empty")
        self.assertEqual(one, two)
        artifact = json.loads((self.node_a / "ARTIFACT.json").read_text())
        self.assertIsNone(artifact["source"]["ability_management"]["revision"],
                          "the node face must not depend on the ability pin")

    def test_the_ability_package_changes_with_the_skill(self):
        self.assertNotEqual(self.pkg1["version"], self.pkg2["version"])
        one = self.pkg1["members"]["skills/runtime-management/SKILL.md"]
        two = self.pkg2["members"]["skills/runtime-management/SKILL.md"]
        self.assertNotEqual(one, two)

    def test_the_package_carries_no_node_module(self):
        for rel in self.pkg1["members"]:
            self.assertFalse(rel.startswith(("bin/", "runtime/", "workengine/")),
                             rel)

    def test_the_catalog_revision_is_the_build_pin_not_a_second_catalog(self):
        manifest = self.pkg1
        self.assertEqual(manifest["catalog_revision"], self.rev1)
        revision_dir = self.store1.revision_dir(manifest["version"])
        names = {p.name for p in revision_dir.rglob("*") if p.is_file()}
        self.assertNotIn("catalog.yaml", names)
        self.assertNotIn("taxonomy.v2.yaml", names)
        self.assertEqual(set(manifest), {
            "schema", "name", "version", "face", "kinds", "catalog_revision",
            "source", "skills", "mcp_servers", "members", "built_at"})

    def test_states_reuses_the_existing_projection_rule(self):
        root = self.copy_store()
        store = self.store_on(root)
        store.install(self.pkg1["version"])
        local.states(package=store.readback(), resolved={"status": "UNKNOWN"},
                     observed=())
        self.assertIn("device_ability_effective", sys.modules)


class ThePackageIsPortable(Fixture):
    def test_no_posix_only_import_or_mutation(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertEqual(imports & {"fcntl", "pwd", "grp", "resource",
                                    "termios", "msvcrt"}, set())
        for verb in ("os.symlink", "os.chmod", "shutil.move", ".rename("):
            self.assertNotIn(verb, source, f"{verb} is not portable or not declared")

    def test_the_test_file_is_collectable_without_posix(self):
        source = Path(__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertEqual(imports & {"fcntl", "pwd", "grp", "resource",
                                    "termios", "msvcrt"}, set())
        self.assertIn("unittest.TestCase", source)

    def test_member_keys_are_posix_on_every_platform(self):
        for rel in self.pkg1["members"]:
            self.assertNotIn("\\", rel, rel)

    def test_an_incomplete_revision_is_rebuilt_not_poisoned(self):
        root = self.base / f"incomplete-{self._testMethodName}"
        store = local.PackageStore(
            root, catalog_revision=self.rev1, clock=clock,
            source={"ability_repo": self.repo, "ability_rev": self.rev1})
        build = local._build()
        composition = build.load_composition(BUNDLE)
        version = f"{composition['version']}+{composition['cachebuster']}.plugin"
        target = store.revision_dir(version)
        target.mkdir(parents=True)
        (target / "junk.txt").write_text("a failed attempt\n")
        built = store.build(
            bundle=BUNDLE, ability_repo=self.repo, ability_rev=self.rev1,
            pack_contract=PACK_CONTRACT,
            marketplace=root / ".agents" / "plugins" / "marketplace.json")
        self.assertEqual(built["version"], version)
        self.assertTrue(store.readback(version)["installed"])

    def test_the_package_lives_under_an_existing_top_level_directory(self):
        # No new `layers.yaml` entry is needed: `integrations/` is already
        # registered, and this package is nested inside it (#414 owns the YAML).
        self.assertEqual(PACKAGE_DIR.parent.name, "integrations")
        self.assertEqual(PACKAGE_DIR.name, "fleet-v1-device-ability-local")


if __name__ == "__main__":
    unittest.main()
