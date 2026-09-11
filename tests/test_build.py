"""Tests for the deterministic bundle build.

These are hermetic: the reusable-skill source is a synthetic ability-management
repository created in a temp directory, committed to a real git repository and
read by revision. Nothing here depends on this machine's checkouts.
"""
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

_spec = importlib.util.spec_from_file_location("serveros_build", BUILDER)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

# Skills the real bundle sources from ability-management. The synthetic source
# repo must provide all of them.
ABILITY_SKILLS = (
    "ability-management",
    "workspace-management",
    "information-management",
    "work-management",
    "test-management",
    "feishu-space-management",
    "feishu-report",
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return proc.stdout.decode().strip()


def make_ability_repo(base: Path, pycache_in: str | None = None) -> tuple[Path, str]:
    """A minimal ability-management stand-in, committed, returning (repo, sha)."""
    repo = base / "ability-management"
    repo.mkdir(parents=True)
    _git(repo, "init", "--quiet", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    for name in ABILITY_SKILLS:
        payload = repo / "skills" / name / "payload"
        payload.mkdir(parents=True)
        (payload / "SKILL.md").write_text(f"---\nname: {name}\n---\n\nbody\n")
    if pycache_in:
        cache = repo / "skills" / pycache_in / "payload" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "stale.cpython-312.pyc").write_bytes(b"\x00compiled")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "synthetic")
    return repo, _git(repo, "rev-parse", "HEAD")


def run_build(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILDER), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.rev = make_ability_repo(self.base)
        self.out = self.base / "dist/current"
        self.marketplace = self.base / "dist/.agents/plugins/marketplace.json"

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra: str) -> list[str]:
        return [
            "--ability-repo", str(self.repo),
            "--ability-rev", self.rev,
            "--output", str(self.out),
            "--marketplace-path", str(self.marketplace),
            *extra,
        ]

    def test_write_then_check_is_idempotent(self):
        self.assertNotEqual(run_build(self.args("--check")).returncode, 0)
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertEqual(run_build(self.args("--check")).returncode, 0)

    def test_check_names_what_drifted(self):
        """A red --check must be actionable, not just a bare exit code."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        (self.out / "context/resolver.py").write_text("# tampered\n")
        (self.out / "context/extra.py").write_text("# unexpected\n")
        result = run_build(self.args("--check"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("DRIFT content context/resolver.py", result.stderr)
        self.assertIn("DRIFT unexpected context/extra.py", result.stderr)

    def test_rebuild_is_byte_identical(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        first = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        second = {p.relative_to(self.out): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(first, second)

    def test_bytecode_caches_never_reach_the_artifact(self):
        repo, rev = make_ability_repo(self.base / "withcache", pycache_in="feishu-report")
        args = [
            "--ability-repo", str(repo), "--ability-rev", rev,
            "--output", str(self.out), "--marketplace-path", str(self.marketplace),
        ]
        self.assertEqual(run_build(args + ["--write"]).returncode, 0)
        stale = [p for p in self.out.rglob("*") if p.suffix == ".pyc" or p.name == "__pycache__"]
        self.assertEqual(stale, [])
        # The sibling SKILL.md must still be present, so the exclusion is precise.
        self.assertTrue((self.out / "skills/feishu-report/SKILL.md").is_file())

    def test_reusable_skills_come_from_the_pinned_revision(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        content = (self.out / "skills/feishu-report/SKILL.md").read_text()
        self.assertIn("name: feishu-report", content)

    def test_context_ships_inside_the_artifact(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertTrue((self.out / "context/schema.yaml").is_file())
        self.assertTrue((self.out / "context/resolver.py").is_file())

    def test_space_ships_inside_the_artifact(self):
        """A Core module the bundle declares must actually be in the artifact.

        A bundle key with no matching directory is exactly the silent
        false-declaration this whole closure exists to remove, so it is asserted
        rather than assumed.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertTrue((self.out / "space/schema.yaml").is_file())
        self.assertTrue((self.out / "space/resolver.py").is_file())
        self.assertTrue((self.out / "space/vocabulary.json").is_file())

    def test_relations_ships_inside_the_artifact(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertTrue((self.out / "relations/project.py").is_file())
        self.assertTrue((self.out / "relations/schema.yaml").is_file())
        self.assertTrue((self.out / "relations/relations.schema.json").is_file())

    def test_every_declared_core_module_is_present(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        for module in build.CORE_MODULES:
            self.assertTrue((self.out / module).is_dir(),
                            f"bundle declares {module} but the artifact lacks it")

    def test_plugin_manifests_list_every_bundled_skill(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        claude = json.loads((self.out / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(len(claude["skills"]), 8)
        self.assertEqual(set(claude["skills"]),
                         {f"./skills/{n}" for n in ("serveros",) + ABILITY_SKILLS})
        codex = json.loads((self.out / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(codex["version"], claude["version"])

    def test_codex_marketplace_points_at_the_payload(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        market = json.loads(self.marketplace.read_text())
        self.assertEqual(market["name"], "hwz-server-os-local")
        self.assertEqual(market["plugins"][0]["source"]["path"], "./current")

    def test_short_revision_is_rejected(self):
        result = run_build([
            "--check",
            "--ability-repo", str(self.repo),
            "--ability-rev", "main",
            "--output", str(self.out),
            "--marketplace-path", str(self.marketplace),
        ])
        self.assertEqual(result.returncode, 2)
        self.assertIn("full commit SHA", result.stderr)

    def test_unknown_skill_is_rejected(self):
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        bundle["skills"].append({"name": "no-such-skill", "source": "ability-management"})
        path = self.base / "bundle.yaml"
        path.write_text(yaml.safe_dump(bundle, allow_unicode=True))
        result = run_build(["--check", "--bundle", str(path)] + self.args())
        self.assertEqual(result.returncode, 2)
        self.assertIn("no payload for skill", result.stderr)

    def test_output_destination_is_mandatory(self):
        """The artifact has exactly one home; no repo-local default payload."""
        result = run_build([
            "--check",
            "--ability-repo", str(self.repo), "--ability-rev", self.rev,
        ])
        self.assertEqual(result.returncode, 2)
        self.assertIn("--output", result.stderr)
        self.assertFalse((ROOT / ".agents").exists())
        self.assertFalse((ROOT / "plugin/build").exists())

    def test_bundle_declares_expected_schema_and_sources(self):
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        self.assertEqual(bundle["schema"], "serveros/bundle.v1")
        local = [s for s in bundle["skills"] if s["source"] == "local"]
        self.assertEqual([s["name"] for s in local], ["serveros"])
        external = {s["name"] for s in bundle["skills"] if s["source"] == "ability-management"}
        self.assertEqual(external, set(ABILITY_SKILLS))


if __name__ == "__main__":
    unittest.main()
