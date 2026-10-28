"""The node release pin, measured against the cut source it ships with.

WHY THIS FILE EXISTS
--------------------
The L2 builder pins the shared surface (`NODE_SHARED_SURFACE`) so that the two
artifacts cannot drift apart silently. The pin was written against a source tree
where `connector` still imported the service, and the node cut removed that
import. Two things broke at once, and only one of them is a subtraction:

* five pinned modules stopped being reached (`audit`, `dispatch`, `fleet`,
  `privacy`, `registry`) — the stale-pin half;
* one module the pin never named started being reached (`principals`, read by
  `config`) — the unpinned half.

A reduction that only deletes entries turns the second failure into a mystery,
so the tests below assert BOTH directions and re-assert that the validator still
refuses each one. Deleting a rule is how a pin stops meaning anything; these
tests are written so that removing the rule makes them red rather than quiet.

Everything here is measured from the tree it runs in. Nothing is hard-coded
except the numbers the measurement is supposed to produce.
"""

from __future__ import annotations

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

NODE_ENTRY = "fleet_mcp.node.connector"
EXPECTED_NODE_MODULES = 26
EXPECTED_PINS = 13
#: The five the cut stopped reaching. Named so a future cut that re-introduces
#: one of them fails loudly here instead of silently re-pinning it.
CUT_UNREACHABLE = (
    "fleet_mcp.audit",
    "fleet_mcp.dispatch",
    "fleet_mcp.fleet",
    "fleet_mcp.privacy",
    "fleet_mcp.registry",
)
#: The one the cut started reaching. It is pinned because it is REACHED, not
#: because it used to be.
CUT_NEWLY_REACHED = "fleet_mcp.principals"


def builder():
    spec = importlib.util.spec_from_file_location("l2_release_under_test", BUILDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["l2_release_under_test"] = module
    spec.loader.exec_module(module)
    return module


class TheNodeClosureIsTheCutItClaims(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.closure = self.m.closure(NODE_ENTRY, ROOT)
        self.pins = set(self.m.NODE_SHARED_SURFACE)

    def test_the_node_closure_is_exactly_the_measured_count(self):
        self.assertEqual(len(self.closure.modules), EXPECTED_NODE_MODULES,
                         "the node closure changed size; re-measure before "
                         "changing this number, and say why in the same change")

    def test_the_node_closure_leaves_no_repository_package(self):
        self.assertEqual(self.closure.external_in_repo, (),
                         "the node payload imports a package this repository does "
                         "not ship inside it — that is not standalone")

    def test_the_pin_is_the_measured_count(self):
        self.assertEqual(len(self.pins), EXPECTED_PINS)


class ThePinFollowsTheClosure(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.closure = self.m.closure(NODE_ENTRY, ROOT)
        self.pins = set(self.m.NODE_SHARED_SURFACE)
        self.members = tuple(str(p.relative_to(ROOT)) for p in self.closure.files.values())

    def test_no_pin_is_stale(self):
        stale = sorted(self.pins - set(self.closure.modules))
        self.assertEqual(stale, [], "the pin lists modules the closure no longer "
                                    "reaches; delete them in the same change")

    def test_the_five_cut_modules_are_gone_from_the_pin(self):
        for module in CUT_UNREACHABLE:
            with self.subTest(module=module):
                self.assertNotIn(module, self.pins)
                self.assertNotIn(module, self.closure.modules)

    def test_the_newly_reached_module_is_pinned(self):
        self.assertIn(CUT_NEWLY_REACHED, self.closure.modules,
                      "the cut no longer reads this module; the pin must say so "
                      "in the same change")
        self.assertIn(CUT_NEWLY_REACHED, self.pins,
                      "the closure reaches a module the pin does not name; that is "
                      "new coupling and it must be reviewed into the pin")

    def test_the_validator_accepts_the_reduced_pin(self):
        self.m._refuse_node_members(self.members)  # raises on refusal


class TheValidatorWasNotWeakened(unittest.TestCase):
    """Each case puts back one of the two defects and expects a refusal.

    If a later change makes the pin "pass" by deleting these rules, these tests
    go red — which is the point. A validator that cannot fail is not a gate.
    """

    def setUp(self):
        self.m = builder()
        self.closure = self.m.closure(NODE_ENTRY, ROOT)
        self.members = tuple(str(p.relative_to(ROOT)) for p in self.closure.files.values())
        self.pins = tuple(self.m.NODE_SHARED_SURFACE)

    def tearDown(self):
        self.m.NODE_SHARED_SURFACE = self.pins

    def test_a_re_added_stale_pin_is_refused(self):
        self.m.NODE_SHARED_SURFACE = self.pins + ("fleet_mcp.audit",)
        with self.assertRaises(Exception) as caught:
            self.m._refuse_node_members(self.members)
        self.assertIn("no longer reaches", str(caught.exception))

    def test_dropping_the_new_pin_is_refused(self):
        self.m.NODE_SHARED_SURFACE = tuple(
            p for p in self.pins if p != CUT_NEWLY_REACHED)
        with self.assertRaises(Exception) as caught:
            self.m._refuse_node_members(self.members)
        self.assertIn("new hub coupling", str(caught.exception))


class TheArtifactSaysWhatItIs(unittest.TestCase):
    def setUp(self):
        self.m = builder()
        self.manifest, self.members = self.m.manifest_for("l2-node", "1.0.0", root=ROOT)

    def test_every_member_is_inside_the_payload_and_none_is_the_repository(self):
        self.assertEqual(len(self.members), EXPECTED_NODE_MODULES)
        self.assertFalse([n for n in self.members if n.startswith("fleet/")])

    def test_the_standalone_flags_are_measured_not_asserted(self):
        block = self.manifest["l2"]
        self.assertEqual(block["external_in_repo"], [])
        self.assertIs(block["import_standalone"], True)
        self.assertIs(block["runtime_standalone"], True,
                      "runtime_standalone must follow the measurement; if it "
                      "cannot be measured it is not true")
        self.assertIs(block["install"]["independently_installable"], True)
        self.assertEqual(block["install"]["blocked_by"], [])

    def test_the_manifest_publishes_the_same_pin_the_source_has(self):
        self.assertEqual(self.manifest["l2"]["shared_surface"],
                         list(self.m.NODE_SHARED_SURFACE))


class TheHubIsStillBlocked(unittest.TestCase):
    """Node readiness is not Hub readiness, and this test keeps them apart."""

    def test_the_hub_cannot_be_manifested(self):
        m = builder()
        with self.assertRaises(Exception) as caught:
            m.manifest_for("l2-hub", "1.0.0", root=ROOT)
        message = str(caught.exception)
        self.assertIn("fleet", message,
                      "the hub must refuse for the reason it actually has")
        self.assertIn("fleet_mcp.registry", message)


class TheInstalledPayloadRuns(unittest.TestCase):
    def test_it_installs_into_an_empty_tree_and_imports(self):
        m = builder()
        manifest, members = m.manifest_for("l2-node", "1.0.0", root=ROOT)
        work = Path(tempfile.mkdtemp(prefix="l2-node-install-"))
        self.addCleanup(shutil.rmtree, work, True)
        archive = m.write_artifact(manifest, members, work / "artifact.tar.gz")

        tree = work / "tree"
        tree.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tree)  # noqa: S202 - a payload this test just built

        self.assertFalse((tree / "fleet").exists(),
                         "the payload must not carry the repository it was built in")

        import subprocess
        done = subprocess.run(
            [sys.executable, "-I", "-c",
             "import sys; sys.path.insert(0, %r)\n"
             "import fleet_mcp.node.connector\n"
             "import fleet_mcp.owner_access\n"
             "print('ok')\n" % str(tree)],
            capture_output=True, text=True, timeout=120,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(done.returncode, 0, done.stderr[-800:])

    def test_a_tampered_payload_is_refused(self):
        m = builder()
        manifest, members = m.manifest_for("l2-node", "1.0.0", root=ROOT)
        work = Path(tempfile.mkdtemp(prefix="l2-node-tamper-"))
        self.addCleanup(shutil.rmtree, work, True)

        # Rebuild the archive by hand with one member's bytes changed, keeping
        # its manifest digest: a payload that still claims the old tree.
        archive = m.write_artifact(manifest, members, work / "clean.tar.gz")
        tampered = dict(members)
        victim = sorted(tampered)[0]
        original = tampered[victim]
        tampered[victim] = original + b"\n# tampered\n"
        bad = work / "tampered.tar.gz"
        with tarfile.open(bad, "w:gz", format=tarfile.USTAR_FORMAT) as tar:
            for name, data in sorted(tampered.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(data))
            blob = json.dumps(manifest, indent=2, sort_keys=True).encode()
            info = tarfile.TarInfo(m.ARTIFACT_MANIFEST)
            info.size = len(blob)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(blob))

        with self.assertRaises(Exception) as caught:
            m.read_artifact(bad)
        self.assertTrue(str(caught.exception),
                        "a tampered payload must refuse with a reason, not pass")


if __name__ == "__main__":
    unittest.main()
