#!/usr/bin/env python3
"""D2 — runtime-truth reads the face an artifact declares, not the combined one.

WHY THIS FILE EXISTS
--------------------
`scripts/runtime_truth.py` recomputes an installed artifact from its own
composition and pin, and calls the result "current canonical or drifted". Until
this change it always recomputed the composition's COMBINED payload, because
before Fleet V1's D1 split there was nothing else to recompute.

After D1 one composition produces two disjoint artifacts (`--face plugin` and
`--face node-runtime`). The readback therefore had two false answers:

  * a plugin-face payload was compared against the combined payload, so every
    node module and both MCP manifests read as drift on a correct artifact;
  * a node-runtime face was blocked before any comparison, because it composes
    no Skill and therefore carries no ability pin — the readback asked it for
    one and reported "README declares no ability pin".

`ARTIFACT.json` already records `face`, `declared_kinds` and the version that
belongs to those bytes. This file pins that the readback uses them, validates
them, and still reads a combined artifact exactly as it did.

THE ERROR CLASSES THIS PINS
---------------------------
* wrong face — a name the release path does not implement;
* inconsistent face — a face the artifact's own `declared_kinds` excludes;
* forged face — a real face name on bytes that belong to the other face;
* missing record — a face artifact with no `ARTIFACT.json` must fall back to the
  combined reading and then report the difference, never pass;
* upgrade/rollback — two versions of one face each recompute to their own
  version and bytes.

Hermetic: a synthetic ability-management repository (as `tests/test_build.py`
builds one) and temp directories. No installation, no service, no release root
on this machine, no GPU.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
BUNDLE = ROOT / "plugin" / "bundle.yaml"
PACK_CONTRACT = ROOT / "abilities" / "pack-contract.yaml"

sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(ROOT / "scripts"))
import test_build  # noqa: E402  (the synthetic ability-management source)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = _load("serveros_build_d2", ROOT / "scripts" / "build.py")
truth = _load("serveros_runtime_truth_d2", ROOT / "scripts" / "runtime_truth.py")


def read_record(artifact: Path) -> dict:
    return json.loads((artifact / "ARTIFACT.json").read_text(encoding="utf-8"))


def rewrite_record(artifact: Path, **changes) -> None:
    doc = read_record(artifact)
    doc.update(changes)
    (artifact / "ARTIFACT.json").write_text(json.dumps(doc, indent=2) + "\n")


def recompute(artifact: Path, repo: Path, pin: str, market: Path,
              face: str | None = None):
    return truth.expected_artifact(build, repo, pin, artifact, market,
                                   layout_at=artifact, face=face)


class _Faces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name)
        cls.repo, cls.rev = test_build.make_ability_repo(cls.base / "ability")
        cls.combined = cls._build("combined", None, BUNDLE)
        cls.plugin = cls._build("plugin", "plugin", BUNDLE)
        cls.node = cls._build("node", "node-runtime", BUNDLE)
        # Recomputation uses ONE marketplace position whose root contains every
        # scratch artifact: `codex_marketplace()` refuses an output that escapes
        # the marketplace root, and that refusal is about where a payload would
        # be served from, not about the face being recomputed.
        cls.recompute_market = cls.base / ".agents/plugins/marketplace.json"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def _build(cls, tag: str, face: str | None, bundle: Path,
               cachebuster: str | None = None) -> Path:
        source = bundle
        if cachebuster is not None:
            doc = yaml.safe_load(bundle.read_text(encoding="utf-8"))
            doc["cachebuster"] = cachebuster
            source = cls.base / f"{tag}.yaml"
            source.write_text(yaml.safe_dump(doc, sort_keys=False))
        out = cls.base / tag / "codex" / "payload"
        market = cls.base / tag / "codex" / ".agents" / "plugins" / "marketplace.json"
        assert build.build(source, cls.repo, cls.rev, out, market, True,
                           PACK_CONTRACT, face=face) == 0
        return out


class APluginFaceReadsItsOwnPayload(_Faces):
    def test_the_declared_face_is_read_from_the_artifact_record(self):
        self.assertEqual(truth.declared_face(self.plugin), "plugin")
        self.assertEqual(truth.declared_face(self.node), "node-runtime")
        # The combined payload records no face, and still means "combined".
        self.assertIsNone(truth.declared_face(self.combined))

    def test_the_payload_recomputes_without_node_members(self):
        face = truth.declared_face(self.plugin)
        pin = truth.declared_pin(self.plugin)
        got = recompute(self.plugin, self.repo, pin,
                        self.recompute_market,
                        face=face)
        self.assertIsNotNone(got, "a correct plugin face must be recomputable")
        payload, version = got
        record = read_record(self.plugin)
        self.assertEqual(version, record["version"])
        self.assertEqual(truth.tree_drift(payload, self.plugin, ignore=()), [])
        for rel in ("bin/serverosd", "runtime/daemon.py", "workengine/schema.sql"):
            self.assertNotIn(Path(rel), payload)

    def test_recomputing_it_as_combined_is_the_old_false_drift(self):
        """The RED this file replaces: no face => the node modules are 'drift'."""
        pin = truth.declared_pin(self.plugin)
        got = recompute(self.plugin, self.repo, pin,
                        self.recompute_market)
        payload, version = got
        self.assertNotEqual(version, read_record(self.plugin)["version"])
        drift = truth.tree_drift(payload, self.plugin, ignore=())
        self.assertTrue(any("missing bin/serverosd" in d for d in drift), drift)

    def test_a_byte_readback_is_not_a_readiness_claim(self):
        """What is verified is installed bytes. Nothing here says READY."""
        record = read_record(self.plugin)
        for word in ("installed", "ready", "supported", "granted", "effective"):
            self.assertNotIn(word, record)
        payload, version = recompute(
            self.plugin, self.repo, truth.declared_pin(self.plugin),
            self.recompute_market,
            face="plugin")
        self.assertIsInstance(payload, dict)
        self.assertIsInstance(version, str)


class ANodeFaceNeedsNoAbilityPin(_Faces):
    def test_the_blocker_does_not_ask_a_node_face_for_a_pin(self):
        self.assertTrue(truth.face_reads_no_skill(build, "node-runtime"))
        self.assertFalse(truth.face_reads_no_skill(build, "plugin"))
        self.assertFalse(truth.face_reads_no_skill(build, None))
        # The node face's README genuinely carries no pin; that is the split
        # working, not a missing declaration.
        self.assertEqual(truth.declared_pin(self.node), "")
        blocker = truth.recompute_blocker(
            build, self.node, "", face="node-runtime")
        self.assertNotIn("declares no ability pin", blocker)
        # And a plugin face with no pin still gets the honest blocker.
        self.assertIn("declares no ability pin", truth.recompute_blocker(
            build, self.plugin, "", face="plugin"))

    def test_its_members_and_version_are_verifiable(self):
        got = recompute(self.node, Path("."), "",
                        self.recompute_market,
                        face="node-runtime")
        self.assertIsNotNone(got)
        payload, version = got
        record = read_record(self.node)
        self.assertEqual(version, record["version"])
        self.assertTrue(version.endswith(".node-runtime"))
        self.assertEqual(truth.tree_drift(payload, self.node, ignore=()), [])
        self.assertIn(Path("bin/serverosd"), payload)
        self.assertIn(Path("runtime/serverosd.service"), payload)
        self.assertNotIn(Path("skills/serveros/SKILL.md"), payload)

    def test_the_node_face_without_face_still_drifts(self):
        """Without the recorded face, the node payload is read as combined."""
        got = recompute(self.node, Path("."), "",
                        self.recompute_market)
        if got is None:
            # Equally honest: the combined reading needs a pin this face lacks.
            self.assertIn("declares no ability pin", truth.recompute_blocker(
                build, self.node, ""))
        else:
            payload, _version = got
            self.assertTrue(truth.tree_drift(payload, self.node, ignore=()))


class ACombinedArtifactIsUnchanged(_Faces):
    def test_it_recomputes_to_the_same_bytes_and_version(self):
        pin = truth.declared_pin(self.combined)
        got = recompute(
            self.combined, self.repo, pin,
            self.recompute_market)
        self.assertIsNotNone(got)
        payload, version = got
        record = read_record(self.combined)
        self.assertEqual(version, record["version"])
        self.assertEqual(truth.tree_drift(payload, self.combined, ignore=()), [])
        self.assertTrue(payload)

    def test_the_fallback_blocker_text_is_unchanged(self):
        # With a README, a composition and a face that needs a pin, the only
        # remaining reason is the pre-existing fallback wording.
        self.assertEqual(truth.recompute_blocker(
            build, self.combined, truth.declared_pin(self.combined)),
            "artifact not recomputable")


class ADeclaredFaceIsCheckedBeforeItIsUsed(_Faces):
    def _copy(self, name: str, source: Path) -> Path:
        dest = self.base / name
        shutil.copytree(source, dest)
        return dest

    def test_a_wrong_face_is_named_and_never_recomputed(self):
        forged = self._copy("wrong-face", self.plugin)
        rewrite_record(forged, face="daemon")
        problem = truth.face_problem(build, forged)
        self.assertIn("does not implement", problem)
        self.assertIn("daemon", problem)
        self.assertIsNone(recompute(
            forged, self.repo, truth.declared_pin(forged),
            self.recompute_market,
            face="daemon"))
        self.assertIn("does not implement", truth.recompute_blocker(
            build, forged, truth.declared_pin(forged), face="daemon"))

    def test_a_face_its_own_declared_kinds_exclude_is_refused(self):
        forged = self._copy("inconsistent-face", self.plugin)
        rewrite_record(forged, face="node-runtime", declared_kinds=["plugin"])
        problem = truth.face_problem(build, forged)
        self.assertIn("inconsistent", problem)
        self.assertIn("node-runtime", problem)

    def test_a_forged_face_cannot_bless_the_other_face_bytes(self):
        """A real face name on the wrong bytes is drift, not a pass."""
        forged = self._copy("forged-face", self.node)
        # A complete forgery: the record says plugin, and the README carries the
        # plugin face's composition and ability pin. Only the BYTES are the node
        # face's. This is what a declaration that outlives its payload looks
        # like, and it is why the recomputation is compared to the bytes rather
        # than trusted from the record.
        shutil.copy2(self.plugin / "README.md", forged / "README.md")
        rewrite_record(forged, face="plugin", declared_kinds=["plugin"])
        self.assertIsNone(truth.face_problem(build, forged))
        got = recompute(
            forged, self.repo, truth.declared_pin(forged),
            self.recompute_market,
            face="plugin")
        # The plugin face recomputes from a composition that has Skills; these
        # bytes do not, so the comparison names the difference instead of OK.
        if got is None:
            self.fail("the plugin face should recompute; the bytes are what differ")
        payload, _version = got
        drift = truth.tree_drift(payload, forged, ignore=())
        self.assertTrue(drift, "a forged face must not compare clean")
        self.assertTrue(any("skills/" in d for d in drift), drift)

    def test_a_missing_record_falls_back_to_combined_and_still_drifts(self):
        forged = self._copy("no-record", self.plugin)
        (forged / "ARTIFACT.json").unlink()
        self.assertIsNone(truth.declared_face(forged))
        self.assertIsNone(truth.face_problem(build, forged))
        got = recompute(
            forged, self.repo, truth.declared_pin(forged),
            self.recompute_market)
        self.assertIsNotNone(got)
        payload, _version = got
        drift = truth.tree_drift(payload, forged, ignore=())
        self.assertTrue(drift, "no face record must not turn a face payload green")
        self.assertTrue(any("missing bin/serverosd" in d for d in drift), drift)


class UpgradeAndRollbackKeepTheirOwnVersions(_Faces):
    """Two revisions of one face each recompute from their OWN source pin.

    Upgrade and rollback are two payloads built from two different
    ability-management revisions. The readback must not collapse them: each
    artifact's README carries its own pin, and recomputing it must reproduce
    that artifact's bytes. "Rollback" is reading the older revision again after
    the newer one existed — it still resolves to the older revision's record.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A second ability-management revision, differing in one reusable Skill
        # so the two payloads are genuinely different bytes under one
        # composition version.
        skill = cls.repo / "skills/runtime-management/payload/SKILL.md"
        skill.write_text(skill.read_text() + "\nsecond revision\n")
        test_build._git(cls.repo, "add", "-A")
        test_build._git(cls.repo, "commit", "--quiet", "-m", "second revision")
        cls.rev_two = test_build._git(cls.repo, "rev-parse", "HEAD")
        # Built at the two pins, same composition and cachebuster.
        cls.v1 = cls._build_at("device-v1", "plugin", cls.rev)
        cls.v2 = cls._build_at("device-v2", "plugin", cls.rev_two)

    @classmethod
    def _build_at(cls, tag: str, face: str, rev: str) -> Path:
        out = cls.base / tag / "codex" / "payload"
        market = cls.base / tag / "codex" / ".agents" / "plugins" / "marketplace.json"
        assert build.build(BUNDLE, cls.repo, rev, out, market, True,
                           PACK_CONTRACT, face=face) == 0
        return out

    def test_each_revision_recomputes_from_its_own_pin(self):
        market = self.recompute_market
        pin_one = truth.declared_pin(self.v1)
        pin_two = truth.declared_pin(self.v2)
        self.assertEqual((pin_one, pin_two), (self.rev, self.rev_two))
        got_one = recompute(self.v1, self.repo, pin_one, market, face="plugin")
        got_two = recompute(self.v2, self.repo, pin_two, market, face="plugin")
        self.assertIsNotNone(got_one)
        self.assertIsNotNone(got_two)
        payload_one, version_one = got_one
        payload_two, version_two = got_two
        # Same composition version; the difference is the source, not the label.
        self.assertEqual(version_one, read_record(self.v1)["version"])
        self.assertEqual(version_two, read_record(self.v2)["version"])
        self.assertEqual(truth.tree_drift(payload_one, self.v1, ignore=()), [])
        self.assertEqual(truth.tree_drift(payload_two, self.v2, ignore=()), [])
        # Reading v1 against v2's source (the upgrade direction) is drift, so
        # the readback is not merely comparing a payload to itself.
        self.assertTrue(truth.tree_drift(payload_two, self.v1, ignore=()))

    def test_a_rollback_to_the_older_revision_still_reads_the_older_bytes(self):
        market = self.recompute_market
        pin_one = truth.declared_pin(self.v1)
        again, version_again = recompute(self.v1, self.repo, pin_one, market,
                                         face="plugin")
        self.assertEqual(version_again, read_record(self.v1)["version"])
        self.assertEqual(truth.tree_drift(again, self.v1, ignore=()), [])
        # The newer revision beside it is unaffected by reading the older one.
        self.assertTrue((self.v2 / "skills/runtime-management/SKILL.md").is_file())


if __name__ == "__main__":
    unittest.main()
