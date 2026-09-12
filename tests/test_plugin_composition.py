"""§27–§30 — the product as compositions, and what a profile may not become.

§27 splits the product: `plugin/bundle.yaml` is `serveros-core`, and the wider
sets are profiles that `extends:` it. §28 insists a profile is a composition
rather than a fork, which is a claim about *structure* — so it is tested by
building both and comparing bytes, not by asserting that the file looks
additive. §29 requires the build stay pinned and deterministic with a profile in
the picture. §30's ablations are the counterexamples that say what the design is
protecting against.

Hermetic: the reusable-skill source is the same synthetic ability-management
repository `test_build` uses, created in a temp directory and read by revision.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build.py"

sys.path.insert(0, str(ROOT / "tests"))
from test_build import _git, make_ability_repo  # noqa: E402  (shares the fixture)

_spec = importlib.util.spec_from_file_location("serveros_build", BUILDER)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

CORE = ROOT / "plugin/bundle.yaml"
PROFILE = ROOT / "plugin/profiles/linux-work.yaml"

# The files that carry the artifact's IDENTITY. They are expected to differ
# between two compositions — a profile that shipped core's `plugin.json`
# verbatim would be claiming core's name and version — so the byte-equality
# assertion below has to exclude them, and say so, rather than be loosened into
# a subset check that would hide a real divergence.
IDENTITY_FILES = {
    Path("README.md"),
    Path(".claude-plugin/plugin.json"),
    Path(".claude-plugin/marketplace.json"),
    Path(".codex-plugin/plugin.json"),
}


def run_build(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILDER), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def digest(root: Path) -> str:
    """One digest over the whole tree, path and content, in sorted order."""
    accumulator = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        accumulator.update(path.relative_to(root).as_posix().encode())
        accumulator.update(path.read_bytes())
    return accumulator.hexdigest()


def payload_files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


def mutated(profile: dict, dest: Path) -> Path:
    """Write a modified copy of the profile, still extending the real core.

    The copy lives in a temp directory, so a relative `extends` would point
    outside the repository and be refused for that reason instead of the one
    under test. Pointing it at the absolute path of the real core keeps the
    mutation the only difference between this file and the one it came from.
    """
    profile["extends"] = str(CORE)
    dest.write_text(yaml.safe_dump(profile, sort_keys=False))
    return dest


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.rev = make_ability_repo(self.base)

    def tearDown(self):
        self._tmp.cleanup()

    def build_into(self, key: str, bundle: Path = None, rev: str = None,
                   write: bool = True) -> subprocess.CompletedProcess:
        root = self.base / key
        return run_build([
            "--write" if write else "--check",
            "--bundle", str(bundle or CORE),
            "--ability-repo", str(self.repo),
            "--ability-rev", rev or self.rev,
            "--output", str(root / "payload"),
            "--marketplace-path", str(root / ".agents/plugins/marketplace.json"),
        ])

    def out(self, key: str) -> Path:
        return self.base / key / "payload"

    # -----------------------------------------------------------------------
    # §28 · composes, rather than forks
    # -----------------------------------------------------------------------

    def test_a_profile_is_core_plus_its_own_members(self):
        """The composition relation, read off the built artifacts.

        Not "the profile lists `extends`" — that is a statement about a file's
        text. This builds both and compares the payloads, which is the claim
        that actually matters and the one a fork would fail.
        """
        self.assertEqual(self.build_into("core").returncode, 0)
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)

        core, profile = self.out("core"), self.out("profile")
        added = payload_files(profile) - payload_files(core)
        self.assertEqual(
            {p.parts[1] for p in added if p.parts[0] == "skills"},
            {"workspace-management", "test-management"},
            "the profile must add exactly its two specialists and nothing else")
        for path in added:
            self.assertEqual(path.parts[0], "skills",
                             f"a profile added a non-skill file: {path}")

    def test_every_file_core_and_the_profile_share_is_byte_identical(self):
        """What "not a fork" means mechanically.

        A fork carries its own copy of what it inherits, and copies drift. Here
        there is one copy — the profile's payload is read from the same pinned
        revision — so the shared bytes are the same bytes. If this ever fails,
        the profile has started carrying its own version of something.
        """
        self.assertEqual(self.build_into("core").returncode, 0)
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)
        core, profile = self.out("core"), self.out("profile")

        shared = payload_files(core) & payload_files(profile)
        divergent = sorted(
            p for p in shared - IDENTITY_FILES
            if (core / p).read_bytes() != (profile / p).read_bytes())
        self.assertEqual(divergent, [], f"shared paths diverged: {divergent}")
        self.assertTrue(IDENTITY_FILES <= shared,
                        "the identity files must be present in both, or the "
                        "exclusion above is hiding their absence")

    def test_a_profile_may_not_redefine_a_member_it_inherits(self):
        """Re-stating a member is allowed; changing its source is not.

        This is the gate that makes `extends` an addition rather than an
        override. Without it a profile could quietly re-source a member core
        already composes, and the two files would disagree about where an
        ability comes from — with the profile winning silently.
        """
        profile = yaml.safe_load(PROFILE.read_text())
        for entry in profile["skills"]:
            if entry["name"] == "runtime-management":
                entry["source"] = "local"
        path = mutated(profile, self.base / "redefine.yaml")

        result = self.build_into("redefine", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("one ability cannot have two sources", result.stderr)

    def test_a_profile_must_carry_its_own_cache_identity(self):
        """A shared cachebuster would let two payloads claim one version."""
        profile = yaml.safe_load(PROFILE.read_text())
        profile.pop("cachebuster")
        path = mutated(profile, self.base / "nocache.yaml")

        result = self.build_into("nocache", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must declare its own `cachebuster`", result.stderr)

    def test_a_profile_cannot_escape_the_repository_through_extends(self):
        path = self.base / "escaping.yaml"
        path.write_text(yaml.safe_dump({
            "schema": "serveros/bundle.v1",
            "extends": "../../../../etc/passwd",
            "composition": "serveros-escaping",
            "cachebuster": "x",
        }, sort_keys=False))
        result = self.build_into("escaping", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("escapes the repository root", result.stderr)

    # -----------------------------------------------------------------------
    # §28 · the on-demand layer is a claim, checked in both directions
    # -----------------------------------------------------------------------

    def test_a_recommended_ability_is_not_in_the_payload(self):
        """The half that makes "on demand" mean something."""
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)
        payload = self.out("profile")
        for name in ("information-management", "document-stewardship"):
            self.assertFalse((payload / "skills" / name).exists(),
                             f"{name} is recommended, not shipped, but is in the artifact")

    def test_a_recommendation_that_does_not_resolve_is_refused(self):
        """`available` is a promise; the build checks the world can keep it."""
        profile = yaml.safe_load(PROFILE.read_text())
        profile["recommended"][0]["name"] = "no-such-ability"
        path = mutated(profile, self.base / "phantom.yaml")

        result = self.build_into("phantom", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not resolve", result.stderr)

    def test_a_missing_marker_on_a_thing_that_exists_is_refused(self):
        """The other direction, and the one that rots silently.

        A `missing` note that outlives the gap it records reads as a known
        limitation forever. The build is the only place that can notice, so it
        notices.
        """
        profile = yaml.safe_load(PROFILE.read_text())
        for entry in profile["recommended"]:
            if entry["name"] == "information-management":
                entry["status"] = "missing"
        path = mutated(profile, self.base / "stale.yaml")

        result = self.build_into("stale", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("marked `missing` but resolves", result.stderr)

    def test_a_bundled_ability_cannot_also_be_recommended(self):
        """Shipping and recommending the same ability makes the profile lie."""
        profile = yaml.safe_load(PROFILE.read_text())
        profile["recommended"].append({
            "name": "workspace-management", "source": "ability-management",
            "status": "available"})
        path = mutated(profile, self.base / "both.yaml")

        result = self.build_into("both", path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("also composed into `skills`", result.stderr)

    # -----------------------------------------------------------------------
    # §29 · the pin is the source, and the build is deterministic
    # -----------------------------------------------------------------------

    def test_the_build_reads_the_pin_not_a_moving_checkout(self):
        """Dirty the source working tree; the artifact must not move.

        §29 forbids reading "whatever checkout currently happens to exist". The
        way to test that is to make the checkout disagree with the revision, and
        there are two ways it can: a file the revision does not have, and
        different bytes in a file it does. Both are exercised, because a check
        that only looks at the file list passes against a build that reads file
        CONTENTS from the tree — which is most of what reading a tree means.
        """
        self.assertEqual(self.build_into("pinned").returncode, 0)
        before = digest(self.out("pinned"))

        payload_dir = self.repo / "skills" / "runtime-management" / "payload"
        (payload_dir / "EXTRA.md").write_text("only in the working tree\n")
        (payload_dir / "SKILL.md").write_text(
            "---\nname: runtime-management\n---\n\nuncommitted local edit\n")

        self.assertEqual(self.build_into("pinned").returncode, 0)
        self.assertEqual(digest(self.out("pinned")), before,
                         "the build changed when the working tree did; it is reading a "
                         "checkout rather than the pinned revision")
        self.assertFalse((self.out("pinned") / "skills/runtime-management/EXTRA.md").exists())
        self.assertIn("body",
                      (self.out("pinned") / "skills/runtime-management/SKILL.md").read_text())

    def test_two_builds_of_one_composition_produce_one_digest(self):
        """§29: same input, same digest — asserted, not hoped for."""
        self.assertEqual(self.build_into("first").returncode, 0)
        self.assertEqual(self.build_into("second").returncode, 0)
        self.assertEqual(digest(self.out("first")), digest(self.out("second")))

    def test_a_profile_build_is_deterministic_too(self):
        """The property has to hold for the composition, not only for core."""
        self.assertEqual(self.build_into("p1", PROFILE).returncode, 0)
        self.assertEqual(self.build_into("p2", PROFILE).returncode, 0)
        self.assertEqual(digest(self.out("p1")), digest(self.out("p2")))

    def test_the_artifacts_declare_which_composition_they_are(self):
        """The declaration a verifier reads instead of assuming core.

        Without it, `runtime_truth` recomputing every artifact against
        `plugin/bundle.yaml` would accuse a correctly-built profile of drifting
        from a composition it never claimed.
        """
        self.assertEqual(self.build_into("core").returncode, 0)
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)
        self.assertIn("Composition: `serveros-core`",
                      (self.out("core") / "README.md").read_text())
        self.assertIn("Composition: `serveros-linux-work`",
                      (self.out("profile") / "README.md").read_text())

    def test_the_names_of_the_two_compositions_do_not_collide(self):
        """Two payloads, one plugin name, would be distinguishable only by version."""
        self.assertEqual(self.build_into("core").returncode, 0)
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)
        core = json.loads((self.out("core") / ".claude-plugin/plugin.json").read_text())
        profile = json.loads((self.out("profile") / ".claude-plugin/plugin.json").read_text())
        self.assertNotEqual(core["name"], profile["name"])
        self.assertNotEqual(core["version"], profile["version"])

    # -----------------------------------------------------------------------
    # §30 · ablation C — the same ability is not bundled twice
    # -----------------------------------------------------------------------

    def test_one_ability_is_one_file_in_one_place(self):
        """§30C: no `serveros/`, `corporate/`, `engineering/` copies.

        An ability that appears under two compositions must still be ONE
        directory in the artifact, produced from ONE source. Counting files
        rather than trusting the declaration is what makes this a test of the
        build instead of a test of the YAML.
        """
        self.assertEqual(self.build_into("profile", PROFILE).returncode, 0)
        payload = self.out("profile")
        for name in ("runtime-management", "ability-management"):
            entries = [p for p in payload.rglob(name) if p.name == name]
            self.assertEqual(len(entries), 1,
                             f"{name} appears at {len(entries)} paths: {entries}")
        # Derived, not restated: the manifest lists each composed skill once.
        manifest = json.loads((payload / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(len(manifest["skills"]), len(set(manifest["skills"])))


if __name__ == "__main__":
    unittest.main()
