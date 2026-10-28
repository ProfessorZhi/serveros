"""The Hub's identity authority as its own versioned, verified sidecar.

WHY THIS FILE EXISTS
--------------------
The Hub's last repository dependency is the identity authority. The Hub cannot
carry it (a second `DeviceRef` diverges at the first rename) and must not fake it
(a local fallback is a fleet that exists only inside the Hub). The answer is a
second artifact: the canonical module *and its own contract*, a manifest that
names the ABI and digests every file, and a Hub that declares it needs exactly
that.

Two claims are kept apart on purpose, and most of this file is about not
confusing them:

  * `installable` (alone)  — is the dependency inside the payload?  For a Hub:
    never.
  * `installable with N`   — may it be deployed when a verified companion
    supplies the dependency? Only when BOTH artifacts verify.

A Hub that reported the second as the first would be claiming to be standalone
while failing at import, which is the exact defect the Hub's own port exists to
prevent.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "integrations" / "fleet-v1-l2-release" / "l2_release.py"
VERSION = "1.0.0"


def builder():
    spec = importlib.util.spec_from_file_location("l2_release_hub_under_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["l2_release_hub_under_test"] = module
    spec.loader.exec_module(module)
    return module


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TheSidecarIsOneVersionedPayload(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.manifest, self.members = self.m.authority_manifest_for(VERSION, root=ROOT)

    def test_it_carries_the_module_and_its_own_contract_and_nothing_else(self):
        self.assertEqual(sorted(self.members),
                         ["fleet-authority.json", "fleet/transport-contract.yaml",
                          "fleet/transport.py"])

    def test_the_manifest_names_the_abi_and_digests_every_file(self):
        declared = json.loads(self.members[self.m.AUTHORITY_MANIFEST_NAME])
        self.assertEqual(declared["abi"], "serveros.fleet.transport/v1")
        self.assertEqual(declared["module"], "fleet.transport")
        for rel, sha in declared["files"].items():
            self.assertEqual(sha, hashlib.sha256(self.members[rel]).hexdigest())

    def test_it_never_carries_authored_fleet_data(self):
        declared = json.loads(self.members[self.m.AUTHORITY_MANIFEST_NAME])
        self.assertIn("fleet/registry.yaml", declared["data_not_bundled"])
        self.assertNotIn("fleet/registry.yaml", self.members)

    def test_the_manifest_agrees_with_the_payload_digest(self):
        declared = json.loads(self.members[self.m.AUTHORITY_MANIFEST_NAME])
        self.assertEqual(self.manifest["authority"]["payload_sha256"],
                         declared["files"]["fleet/transport.py"])
        self.assertIs(self.manifest["authority"]["single_authority"], True)


class BuildingTwiceProducesTheSameBytes(unittest.TestCase):
    def test_two_builds_of_one_tree_are_byte_identical(self):
        m = builder()
        work = Path(tempfile.mkdtemp(prefix="l2-sidecar-repro-"))
        self.addCleanup(shutil.rmtree, work, True)
        first = m.write_artifact(*m.authority_manifest_for(VERSION, root=ROOT),
                                 work / "a.tar.gz")
        second = m.write_artifact(*m.authority_manifest_for(VERSION, root=ROOT),
                                  work / "b.tar.gz")
        self.assertEqual(digest(first), digest(second),
                         "a sidecar whose bytes move between builds cannot be "
                         "pinned by a Hub")


class ThePayloadIsVerifiedBeforeItIsImported(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.work = Path(tempfile.mkdtemp(prefix="l2-sidecar-verify-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.manifest, self.members = self.m.authority_manifest_for(VERSION, root=ROOT)
        self.good = m_write(self.m, self.manifest, self.members, self.work / "good.tar.gz")

    def test_a_sound_sidecar_verifies(self):
        artifact = self.m.verify_authority_artifact(self.good)
        self.assertEqual(artifact.kind, self.m.AUTHORITY_KIND)
        self.assertEqual(artifact.version, VERSION)

    def test_it_imports_the_authority_in_an_empty_tree_without_a_checkout(self):
        artifact = self.m.verify_authority_artifact(self.good)
        self.assertTrue(self.m.authority_imports_without_checkout(artifact))

    def test_a_tampered_member_is_refused(self):
        artifact = self.m.read_artifact(self.good)
        members = dict(artifact.members)
        members["fleet/transport.py"] = members["fleet/transport.py"] + b"\n# tampered\n"
        bad = self.work / "tampered.tar.gz"
        with tarfile.open(bad, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
            for name, data in sorted(dict(members, **{
                    self.m.ARTIFACT_MANIFEST:
                    json.dumps(artifact.manifest, indent=2, sort_keys=True).encode() + b"\n"
                    }).items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(Exception) as caught:
            self.m.verify_authority_artifact(bad)
        self.assertTrue(str(caught.exception))

    def test_an_undeclared_python_file_is_refused(self):
        members = dict(self.members)
        members["fleet/sneaky.py"] = b"VALUE = 1\n"
        with self.assertRaises(Exception) as caught:
            self.m.verify_authority_members(members)
        self.assertIn("closed authority set", str(caught.exception))

    def test_a_declared_but_absent_file_is_refused(self):
        members = dict(self.members)
        declared = json.loads(members[self.m.AUTHORITY_MANIFEST_NAME])
        declared["files"]["fleet/missing.py"] = "0" * 64
        members[self.m.AUTHORITY_MANIFEST_NAME] = json.dumps(declared).encode()
        with self.assertRaises(Exception):
            self.m.verify_authority_members(members)

    def test_a_wrong_abi_is_refused(self):
        members = dict(self.members)
        declared = json.loads(members[self.m.AUTHORITY_MANIFEST_NAME])
        declared["abi"] = "serveros.fleet.transport/v2"
        members[self.m.AUTHORITY_MANIFEST_NAME] = json.dumps(declared).encode()
        with self.assertRaises(Exception) as caught:
            self.m.verify_authority_members(members)
        self.assertIn("ABI", str(caught.exception))

    def test_carrying_the_authored_registry_is_refused(self):
        members = dict(self.members)
        members["fleet/registry.yaml"] = b"devices: []\n"
        with self.assertRaises(Exception) as caught:
            self.m.verify_authority_members(members)
        message = str(caught.exception)
        self.assertTrue("closed authority set" in message or "invent" in message)

    def test_a_path_that_escapes_the_payload_is_refused(self):
        members = dict(self.members)
        members["../escape.py"] = b"VALUE = 1\n"
        with self.assertRaises(Exception) as caught:
            self.m.verify_authority_members(members)
        self.assertIn("escapes", str(caught.exception))


def m_write(m, manifest, members, path):
    return m.write_artifact(manifest, members, path)


class TheHubNeverClaimsToBeStandalone(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.work = Path(tempfile.mkdtemp(prefix="l2-hub-plan-"))
        self.addCleanup(shutil.rmtree, self.work, True)
        manifest, members = self.m.manifest_for(
            "l2-hub", VERSION, root=ROOT,
            allow_external_in_repo=("fleet",))
        self.hub_manifest = manifest
        self.hub = self.m.write_artifact(manifest, members, self.work / "hub.tar.gz")
        auth_manifest, auth_members = self.m.authority_manifest_for(VERSION, root=ROOT)
        self.authority = self.m.write_artifact(auth_manifest, auth_members,
                                               self.work / "authority.tar.gz")

    def test_the_hub_manifest_declares_the_dependency_and_the_limits(self):
        block = self.hub_manifest["l2"]
        self.assertIs(block["standalone"], False)
        self.assertIs(block["import_standalone"], False)
        self.assertIs(block["install"]["independently_installable"], False)
        declared = block["install"]["authority"]
        self.assertEqual(declared["abi"], "serveros.fleet.transport/v1")
        self.assertEqual(declared["provided_by"], "fleet-authority")
        self.assertIn("fleet-authority sidecar not supplied",
                      block["install"]["blocked_by"])

    def test_a_hub_without_its_authority_is_not_installable(self):
        plan = self.m.hub_install_plan(self.hub, None)
        self.assertIs(plan["installable"], False)
        self.assertEqual(plan["with"], [])
        self.assertTrue(any("sidecar" in b for b in plan["blocked_by"]))

    def test_a_hub_with_a_verified_authority_is_installable_with_it(self):
        plan = self.m.hub_install_plan(self.hub, self.authority)
        self.assertIs(plan["installable"], True)
        self.assertEqual(plan["with"], ["fleet-authority"])
        self.assertEqual(plan["blocked_by"], [])
        # …and it is still not standing alone.
        self.assertIs(plan["hub_alone_standalone"], False)

    def test_a_hub_that_pinned_a_different_payload_refuses_this_sidecar(self):
        # Pin the Hub to a digest that is not the sidecar's.
        original = self.m.authority_declaration
        self.addCleanup(setattr, self.m, "authority_declaration", original)
        self.m.authority_declaration = lambda expected_payload_sha256=None: original("0" * 64)
        manifest, members = self.m.manifest_for(
            "l2-hub", VERSION, root=ROOT, allow_external_in_repo=("fleet",))
        pinned = self.m.write_artifact(manifest, members, self.work / "pinned.tar.gz")
        plan = self.m.hub_install_plan(pinned, self.authority)
        self.assertIs(plan["installable"], False)
        self.assertTrue(any("pins an authority payload" in b for b in plan["blocked_by"]))
        self.assertIs(plan["authority_pinned"], True)

    def test_an_l2_artifact_is_not_accepted_as_the_authority(self):
        with self.assertRaises(Exception):
            self.m.verify_authority_artifact(self.hub)

    def test_rollback_verifies_the_sidecar_before_naming_it(self):
        artifact = self.m.authority_rollback_target(self.authority, version=VERSION)
        self.assertEqual(artifact.kind, "fleet-authority")
        with self.assertRaises(Exception):
            self.m.authority_rollback_target(self.hub)


class TheEdgePinFollowsTheMovedImport(unittest.TestCase):
    def test_the_authority_importer_is_the_one_that_actually_imports_it(self):
        m = builder()
        self.assertEqual(m.EXTERNAL_EDGES["fleet"]["module"], "fleet.transport")
        self.assertEqual(m.EXTERNAL_EDGES["fleet"]["holder"],
                         "fleet_mcp.identity_adapter_port")
        source = (ROOT / "fleet_mcp" / "identity_adapter_port.py").read_text(encoding="utf-8")
        self.assertIn("from fleet.transport import", source,
                      "the pin names a holder; the holder must hold the import")


if __name__ == "__main__":
    unittest.main()
