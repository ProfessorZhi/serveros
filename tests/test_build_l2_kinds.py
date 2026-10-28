#!/usr/bin/env python3
"""L2 release kinds vs the two BUILD faces, and the paths a composition may name.

WHY THIS FILE EXISTS
--------------------
`release.py` (#433/#436) implements five verifiers: `node-runtime`, `plugin`,
`native-runtime`, `l2-node`, `l2-hub`. `build.py` produces only the first two.
The other three are payloads assembled by their own owners — the C++ package by
`serverosd`'s `make dist`, the L2 halves by the L2 release tool (#431).

The build's "absent but present" guard meant "a plugin payload must not carry
the daemon, and a node payload must not pretend to be a plugin". Applying it to
the L2 kinds made the legacy combined payload illegal: the node artifact ships
`fleet_mcp/` as a Core module, so it necessarily contains
`fleet_mcp/node/connector.py` (`l2-node`'s required member) and
`fleet_mcp/server.py` (`l2-hub`'s) — and no composition declares those, because
a combined payload is not a standalone L2 artifact.

So the guard now names the two faces it can decide, while the declaration check
refuses a composition asking this builder to claim an L2/native kind at all.
Nothing is weakened for the faces: a plugin payload with a daemon is still
refused, a node claim without its daemon is still refused, and a standalone L2
artifact is still proved by the L2 release path, never by this build.

Also pinned: a composition may only name one relative path component for a Skill
and for a module, and artifact members are POSIX paths with no `..`, absolute
form or drive letter — on Windows as on POSIX.

Hermetic: synthetic ability-management repository + temp compositions. No
install, service, network or global state.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
BUNDLE = REPO / "plugin" / "bundle.yaml"
PACK_CONTRACT = REPO / "abilities" / "pack-contract.yaml"
BUILD = REPO / "scripts" / "build.py"
RELEASE = REPO / "scripts" / "release.py"

sys.path.insert(0, str(TESTS))
import test_build  # noqa: E402


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = _load("serveros_build_l2_kinds", BUILD)
release = _load("serveros_release_l2_kinds", RELEASE)


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev = test_build.make_ability_repo(cls.base / "ability")
        cls.market = cls.base / ".agents" / "plugins" / "marketplace.json"
        cls.combined = cls._build("combined", None)
        cls.node = cls._build("node", "node-runtime")
        cls.plugin = cls._build("plugin", "plugin")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _build(cls, tag: str, face):
        out = cls.base / tag / "payload"
        assert build.build(BUNDLE, cls.repo, cls.rev, out, cls.market, True,
                           PACK_CONTRACT, face=face) == 0
        return out

    def composition(self, mutate) -> Path:
        document = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))
        mutate(document)
        path = self.base / f"composition-{self._testMethodName}.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        return path

    def plan(self, bundle: Path, face=None):
        out = self.base / f"plan-{self._testMethodName}" / "payload"
        return build.plan(bundle, self.repo, self.rev, out, self.market, face=face)

    @staticmethod
    def artifact(path: Path) -> dict:
        return json.loads((path / "ARTIFACT.json").read_text())


class TheCombinedPayloadKeepsItsIdentity(Fixture):
    def test_it_declares_only_the_two_build_faces(self):
        artifact = self.artifact(self.combined)
        self.assertEqual(set(artifact["kinds"]), {"node-runtime", "plugin"})
        self.assertEqual(build.build_face_kinds(),
                         (release.NODE_RUNTIME, release.PLUGIN))

    def test_it_still_ships_every_l2_source_file_as_a_core_module(self):
        members = set(self.artifact(self.combined)["members"])
        self.assertIn("fleet_mcp/node/connector.py", members)
        self.assertIn("fleet_mcp/server.py", members)
        # Every file the module contributes is present; the L2 kinds were not
        # removed from the payload to make a test pass.
        expected = {f"fleet_mcp/{rel.as_posix()}" for _src, rel in
                    build.local_skill_files(REPO / "fleet_mcp")}
        self.assertLessEqual(expected, members)

    def test_it_does_not_claim_an_l2_or_native_identity(self):
        artifact = self.artifact(self.combined)
        for kind in ("l2-node", "l2-hub", "native-runtime"):
            self.assertNotIn(kind, artifact["kinds"])
        # The combined payload deliberately keeps its pre-face bytes, so it has
        # no face-only evidence key; the face artifacts do.
        if "kind_evidence" in artifact:
            self.assertEqual(set(artifact["kind_evidence"]),
                             {"node-runtime", "plugin"})
        self.assertEqual(set(self.artifact(self.node)["kind_evidence"]),
                         {"node-runtime"})

    def test_the_build_faces_are_still_mutually_exclusive(self):
        node = set(self.artifact(self.node)["members"])
        plugin = set(self.artifact(self.plugin)["members"])
        self.assertIn("fleet_mcp/node/connector.py", node)
        self.assertFalse([m for m in plugin if m.startswith("fleet_mcp/")])
        self.assertFalse([m for m in plugin if m.startswith(("runtime/", "bin/"))])

    def test_the_l2_verifiers_remain_the_l2_authority(self):
        self.assertEqual(set(release.L2_KINDS), {"l2-node", "l2-hub"})
        self.assertIn("fleet_mcp/node/connector.py",
                      release.REQUIRED_MEMBERS[release.L2_NODE])
        self.assertIn("fleet_mcp/server.py",
                      release.REQUIRED_MEMBERS[release.L2_HUB])


class TheGuardStillRefusesTheFaces(Fixture):
    def test_a_plugin_payload_with_a_daemon_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            build.verify_artifact_kinds(
                ["plugin"], [".claude-plugin/plugin.json",
                             ".codex-plugin/plugin.json", "bin/serverosd"],
                BUNDLE)
        self.assertIn("does not declare `node-runtime`", str(caught.exception))

    def test_a_node_claim_without_its_daemon_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            build.verify_artifact_kinds(
                ["node-runtime"], ["bin/serverosctl"], BUNDLE)
        self.assertIn("bin/serverosd", str(caught.exception))

    def test_the_l2_members_do_not_trip_the_face_guard(self):
        evidence = build.verify_artifact_kinds(
            ["node-runtime", "plugin"],
            ["bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
             "runtime/cli.py", "runtime/serverosd.service",
             "workengine/schema.sql", ".claude-plugin/plugin.json",
             ".codex-plugin/plugin.json", "fleet_mcp/node/connector.py",
             "fleet_mcp/server.py"],
            BUNDLE)
        self.assertEqual(set(evidence), {"node-runtime", "plugin"})

    def test_a_non_build_kind_cannot_be_claimed(self):
        for kind in ("l2-node", "l2-hub", "native-runtime"):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError) as caught:
                    build.verify_artifact_kinds([kind], [], BUNDLE)
                self.assertIn("not a kind this build produces", str(caught.exception))

    def test_a_composition_declaring_an_l2_kind_is_refused(self):
        bundle = self.composition(
            lambda doc: doc["artifacts"].append("l2-node"))
        with self.assertRaises(ValueError) as caught:
            self.plan(bundle)
        self.assertIn("cannot produce", str(caught.exception))

    def test_an_unknown_release_kind_is_still_named(self):
        with self.assertRaises(ValueError) as caught:
            build.verify_artifact_kinds(["ghost"], [], BUNDLE)
        self.assertIn("not a kind the release path implements", str(caught.exception))


class APathCannotEscapeTheArtifact(Fixture):
    def test_a_skill_name_with_a_separator_is_refused(self):
        for name in ("../escape", "..\\escape", "/absolute", "C:drive",
                     ".hidden", "nested/name"):
            with self.subTest(name=name):
                bundle = self.composition(
                    lambda doc, n=name: doc["skills"].__setitem__(
                        0, {"name": n, "source": "local"}))
                with self.assertRaises(ValueError) as caught:
                    self.plan(bundle)
                self.assertIn("one relative path component", str(caught.exception))

    def test_a_module_path_with_a_traversal_is_refused(self):
        for rel in ("..\\runtime", "../runtime", "/runtime"):
            with self.subTest(rel=rel):
                bundle = self.composition(
                    lambda doc, r=rel: doc["runtime"].__setitem__("path", r))
                with self.assertRaises(ValueError):
                    self.plan(bundle)

    def test_members_are_posix_and_contain_no_traversal(self):
        for artifact_dir in (self.combined, self.node, self.plugin):
            for member in self.artifact(artifact_dir)["members"]:
                with self.subTest(member=member):
                    self.assertNotIn("\\", member)
                    self.assertFalse(member.startswith("/"))
                    self.assertNotIn("..", Path(member).parts)
                    self.assertFalse(len(member.split("/", 1)[0]) == 2
                                     and member[1] == ":")

    def test_the_member_path_helper_refuses_escape_shapes(self):
        for rel in (Path("../x"), Path("/x"), Path("C:/x")):
            with self.subTest(rel=rel):
                with self.assertRaises(ValueError):
                    build._member_path(rel, "test")


class TheCliOffersOnlyBuildFaces(unittest.TestCase):
    def test_help_does_not_offer_a_release_only_kind(self):
        result = __import__("subprocess").run(
            [sys.executable, str(BUILD), "--help"],
            stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").STDOUT, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("node-runtime,plugin", result.stdout.replace(" ", ""))
        for kind in ("l2-node", "l2-hub", "native-runtime"):
            self.assertNotIn(kind, result.stdout)


if __name__ == "__main__":
    unittest.main()
