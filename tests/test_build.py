"""Tests for the deterministic bundle build.

These are hermetic: the reusable-skill source is a synthetic ability-management
repository created in a temp directory, committed to a real git repository and
read by revision. Nothing here depends on this machine's checkouts.
"""
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
BUILDER = ROOT / "scripts" / "build.py"

_spec = importlib.util.spec_from_file_location("serveros_build", BUILDER)
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

# Skills canonical in this repository, in bundle order. §24 left exactly one:
# runtime-management became an ability-management source, so a second entry
# here would be a second source of truth rather than a composition choice.
LOCAL_SKILLS = ("serveros",)

# Everything any real composition references — shipped by core, added by a
# profile, or recommended on demand. One synthetic source builds them all, so a
# profile test does not need a second fixture.
#
# `document-stewardship` is deliberately ABSENT: `serveros-linux-work` marks it
# `missing`, and the builder treats a `missing` entry that resolves as an error,
# so a fixture that provided it would make the profile unbuildable and hide the
# reason.
ABILITY_SKILLS = (
    "runtime-management",
    "ability-management",
    "workspace-management",
    "test-management",
    "information-management",
    "work-management",
    "feishu-space-management",
    "feishu-report",
)

# What `serveros-core` itself composes from ability-management. A subset of the
# above, and asserted as such rather than restated independently.
CORE_ABILITY_SKILLS = ("runtime-management", "ability-management")


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
    # The registry, committed with the payload. Without it a pin cannot be
    # compared against canonical at all, and every provenance case silently
    # falls into the "could not determine" branch — which passes the tests that
    # assert nothing blocks while never exercising the detector.
    (repo / "abilities-manifest.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "authors": [],
        "abilities": [{"type": "skill", "id": "ProfessorZhi/ability-management",
                       "name": "ability-management", "path": "skills/ability-management",
                       "artifact_mode": "vendored", "revisions": []}],
    }, sort_keys=False, allow_unicode=True))
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
        repo, rev = make_ability_repo(self.base / "withcache", pycache_in="runtime-management")
        args = [
            "--ability-repo", str(repo), "--ability-rev", rev,
            "--output", str(self.out), "--marketplace-path", str(self.marketplace),
        ]
        self.assertEqual(run_build(args + ["--write"]).returncode, 0)
        stale = [p for p in self.out.rglob("*") if p.suffix == ".pyc" or p.name == "__pycache__"]
        self.assertEqual(stale, [])
        # The sibling SKILL.md must still be present, so the exclusion is precise.
        self.assertTrue((self.out / "skills/runtime-management/SKILL.md").is_file())

    def test_a_recommended_ability_never_reaches_the_payload(self):
        """§5: `recommended` is a claim about the node, not a member of the artifact.

        The profile names four abilities it does not ship. Each of them resolves
        at the pinned revision, so a builder that merged `skills` and
        `recommended` — or that copied everything the registry can resolve —
        would put them in the payload and nothing would look wrong: the artifact
        would be a superset, the manifests would list them, and the profile
        would still say they are on demand.

        The core composition is asserted alongside it, so this is a statement
        about the recommendation rather than about the fixture being empty.
        """
        profile = ROOT / "plugin/profiles/linux-work.yaml"
        recommended = {e["name"] for e in
                       (yaml.safe_load(profile.read_text()) or {}).get("recommended") or []}
        self.assertTrue(recommended, "the profile must recommend something for this to mean anything")
        self.assertEqual(run_build(self.args("--write") + ["--bundle", str(profile)]).returncode, 0)
        shipped = json.loads((self.out / ".claude-plugin/plugin.json").read_text())["skills"]
        for name in sorted(recommended):
            self.assertFalse((self.out / f"skills/{name}").exists(),
                             f"{name} is recommended, not composed, and must not ship")
            self.assertNotIn(f"./skills/{name}", shipped)
        # The members the same profile DOES compose are present, so the absence
        # above is about the recommendation.
        for name in ("workspace-management", "test-management"):
            self.assertTrue((self.out / f"skills/{name}").is_dir())

    def test_reusable_skills_come_from_the_pinned_revision(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        content = (self.out / "skills/runtime-management/SKILL.md").read_text()
        self.assertIn("name: runtime-management", content)

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

    def test_the_artifact_ships_the_daemon_its_adapter_calls(self):
        """The composition ships an MCP adapter whose only job is to call
        `serverosd`. Shipping the caller without the callee describes a node
        that cannot exist from the distribution alone, which is why an operator
        had to point a unit at a source checkout. So the adapter's dependency is
        asserted as a file in the artifact, not assumed from the bundle text.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertTrue((self.out / "mcp/node_mcp.py").is_file())
        self.assertTrue((self.out / "bin/serverosd").is_file(),
                        "the artifact ships the adapter but not the daemon")
        self.assertTrue((self.out / "bin/serverosctl").is_file())
        self.assertTrue((self.out / "runtime/daemon.py").is_file())
        self.assertTrue((self.out / "runtime/cli.py").is_file())

    def test_the_shipped_entry_point_resolves_the_shipped_runtime(self):
        """`bin/` and `runtime/` are one unit, not two directories.

        Both entry points find the runtime as `parent.parent / "runtime"`, so
        shipping either without the other yields a shim that cannot import what
        it names. Executing the shim from the artifact is the only assertion
        that catches that; checking both paths exist would not, because the bug
        is in the relationship between them.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        proc = subprocess.run(
            [sys.executable, str(self.out / "bin" / "serverosctl"), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(self.base),
        )
        self.assertNotIn("ModuleNotFoundError", proc.stderr)
        self.assertNotIn("ImportError", proc.stderr)
        self.assertEqual(proc.returncode, 0,
                         f"shipped entry point could not run:\n{proc.stderr}")

    def test_the_shipped_entry_points_are_executable(self):
        """An artifact can have every byte right and still not run.

        This is not hypothetical. The first promotion of this composition
        produced a `bin/serverosd` with correct contents and mode 0644, and
        systemd answered `status=203/EXEC` on a node whose previous daemon had
        been up for 375 ticks. Both entry points are extensionless, and the
        writer restored a mode only for `.sh` and `.py` — so the rule was wrong
        for precisely the files whose mode is the whole point.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        for rel in ("bin/serverosd", "bin/serverosctl"):
            self.assertTrue(os.access(self.out / rel, os.X_OK),
                            f"{rel} shipped without its executable bit")

    def test_check_reports_a_lost_executable_bit(self):
        """The mode must be visible to `--check`, not silently tolerated.

        Without this, a build that dropped the bit again would report clean, and
        the next thing to notice would be a supervisor refusing to start.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        (self.out / "bin" / "serverosd").chmod(0o644)
        result = run_build(self.args("--check"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mode bin/serverosd", result.stderr)

    def test_the_shipped_unit_declares_no_build_machine_path(self):
        """The unit template travels inside the artifact, so it must not name
        the machine that built it.

        The builder promises that no generated artifact references this
        machine's build paths. A template carrying one worktree's absolute
        ExecStart would have broken that promise in the one file whose whole
        purpose is to be read by a supervisor on another day — and the failure
        would be a service that stops starting when the worktree is retired.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        unit = (self.out / "runtime/serverosd.service").read_text()
        self.assertIn("@SERVEROS_RUNTIME@/bin/serverosd", unit)
        self.assertIn("@SERVEROS_STATE_DIR@", unit)

        # Directives only. The unit's comments record measurements taken on a
        # named machine, and a path in prose reaches no supervisor; what must
        # not survive is a path in an assignment, because that is the one a
        # systemd on another node would try to honour.
        directives = [
            line for line in unit.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", ";"))
        ]
        for line in directives:
            for machine_path in ("/opt/applications/", "/home/hwz", "/workspace/worktrees"):
                self.assertNotIn(
                    machine_path, line,
                    f"the shipped unit names the build machine in a directive: {line.strip()}")

    def test_plugin_manifests_list_every_bundled_skill(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        claude = json.loads((self.out / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(len(claude["skills"]), len(LOCAL_SKILLS) + len(CORE_ABILITY_SKILLS))
        self.assertEqual(set(claude["skills"]),
                         {f"./skills/{n}" for n in LOCAL_SKILLS + CORE_ABILITY_SKILLS})
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
        self.assertEqual([s["name"] for s in local], list(LOCAL_SKILLS))
        external = {s["name"] for s in bundle["skills"] if s["source"] == "ability-management"}
        self.assertEqual(external, set(CORE_ABILITY_SKILLS))

    # -----------------------------------------------------------------------
    # §26 · the node surface is declared, in the shape each runtime reads
    # -----------------------------------------------------------------------

    def test_the_node_surface_ships_and_is_declared(self):
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        claude = json.loads((self.out / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((self.out / ".codex-plugin/plugin.json").read_text())
        self.assertEqual(claude["mcpServers"], "./.mcp.json")
        self.assertEqual(codex["mcpServers"], "./.codex-mcp.json")
        self.assertTrue((self.out / ".mcp.json").is_file())
        self.assertTrue((self.out / ".codex-mcp.json").is_file())
        # The payload the manifest points at must actually be in the artifact.
        self.assertTrue((self.out / "mcp/bin/serveros-node-mcp").is_file())
        self.assertTrue((self.out / "mcp/node_mcp.py").is_file())

    def test_the_claude_mcp_file_uses_the_documented_wrapper(self):
        """The plugin-root loader is `document.mcpServers || document`, so the
        bare map and the wrapped one both load. We ship the wrapped one, and
        this test exists so that a future "simplification" to the bare form —
        which the official plugins happen to use — has to argue with the
        documented schema and with `claude plugin validate` before it lands."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        document = json.loads((self.out / ".mcp.json").read_text())
        self.assertEqual(list(document), ["mcpServers"])
        server = document["mcpServers"]["serveros-node"]
        self.assertIn("command", server)          # stdio, not http
        self.assertNotIn("url", server)
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/mcp/bin/serveros-node-mcp", server["args"])

    def test_running_the_adapter_does_not_write_into_the_installed_plugin(self):
        """Observed, not theorised: starting the adapter from a built artifact
        leaves a `__pycache__` in the directory it was started from. Inside an
        installed plugin that directory is replaced on the next update, so the
        cache is discarded by the event it was meant to speed up — and until
        then the artifact holds files the build did not produce. The env entry
        that prevents this is part of the declaration, so it is asserted here
        rather than trusted to survive the next edit of bundle.yaml."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        for name in (".mcp.json", ".codex-mcp.json"):
            server = json.loads((self.out / name).read_text())["mcpServers"]["serveros-node"]
            self.assertEqual(server["env"], {"PYTHONDONTWRITEBYTECODE": "1"})

    def test_starting_the_declared_server_writes_nothing(self):
        """The same property end-to-end: run the entry point the manifest names,
        in the exact directory it would run from, and check that the file tree
        is unchanged afterwards."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        document = json.loads((self.out / ".mcp.json").read_text())
        server = document["mcpServers"]["serveros-node"]
        argv = [server["command"]] + [
            a.replace("${CLAUDE_PLUGIN_ROOT}", str(self.out)) for a in server["args"]
        ]
        before = {p for p in self.out.rglob("*")}
        subprocess.run(
            argv, input='{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n',
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, timeout=60, cwd=self.out, env={**os.environ, **server["env"]},
        )
        self.assertEqual({p for p in self.out.rglob("*")} - before, set())

    def test_the_codex_mcp_file_is_reachable_only_through_its_manifest_field(self):
        """`.codex-mcp.json` is not a filename Codex looks for — the string does
        not occur in the codex binary. It resolves only because the manifest
        names it. So the manifest field and the file are one artefact in two
        halves, and the test that matters is that both halves are present."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        manifest = json.loads((self.out / ".codex-plugin/plugin.json").read_text())
        # removeprefix, not lstrip: lstrip("./") strips characters, not a
        # prefix, and eats the leading dot of a dotfile.
        document = json.loads((self.out / manifest["mcpServers"].removeprefix("./")).read_text())
        server = document["mcpServers"]["serveros-node"]
        self.assertEqual(server["cwd"], ".")
        self.assertEqual(server["args"], ["./mcp/bin/serveros-node-mcp"])

    def test_a_manifest_pointing_at_a_missing_entry_is_refused(self):
        """Declared-but-absent, caught at build time. Without this the artifact
        would ship a server declaration naming a file it does not contain, and
        the failure would surface as a runtime fault nobody could trace to the
        bundle."""
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        bundle["mcp"]["servers"][0]["entry"] = "bin/no-such-entry"
        path = self.base / "bundle.yaml"
        path.write_text(yaml.safe_dump(bundle, allow_unicode=True))
        result = run_build(["--check", "--bundle", str(path)] + self.args())
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not exist", result.stderr)

    def test_an_mcp_entry_may_not_escape_its_module(self):
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        bundle["mcp"]["servers"][0]["entry"] = "../runtime/daemon.py"
        path = self.base / "bundle.yaml"
        path.write_text(yaml.safe_dump(bundle, allow_unicode=True))
        result = run_build(["--check", "--bundle", str(path)] + self.args())
        self.assertEqual(result.returncode, 2)
        self.assertIn("inside", result.stderr)

    def test_the_declared_mcp_file_is_the_one_that_was_written(self):
        """The builder emits the pointer and the file, so they cannot disagree
        today. This asserts the invariant rather than the mechanism, because the
        runtime's own validator does NOT check it: `mcpServers: "./nope.json"`
        passes `claude plugin validate --strict` in silence (verified — a typo
        in the field *name* is caught, a typo in its *value* is not). A dangling
        pointer is the declared-but-absent shape, and nothing but this test
        would notice one."""
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        for manifest_path, expected in ((".claude-plugin/plugin.json", ".mcp.json"),
                                        (".codex-plugin/plugin.json", ".codex-mcp.json")):
            manifest = json.loads((self.out / manifest_path).read_text())
            target = self.out / manifest["mcpServers"].removeprefix("./")
            self.assertEqual(target.name, expected)
            self.assertTrue(target.is_file(), f"{manifest_path} points at a file that is not there")

    def test_the_generated_artifact_passes_the_runtimes_own_validator(self):
        """`claude plugin validate --strict` is the strongest available check on
        the manifests, and it is not a formality: it was confirmed to fail on a
        misspelled `mcpservers` and on a wrong-typed `mcpServers`, which are the
        two ways this file can be present and inert. Skipped rather than failed
        when the CLI is absent, so the suite stays hermetic on a machine that
        does not have it."""
        cli = shutil.which("claude")
        if cli is None:
            self.skipTest("claude CLI is not on PATH")
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        result = subprocess.run([cli, "plugin", "validate", "--strict", str(self.out)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_a_bundle_without_servers_declares_no_mcp_surface(self):
        """A plugin with no node surface must not advertise an empty one — an
        empty `mcpServers` reads as "there are none" rather than "not offered",
        which is the same mistake as returning `[]` for an unavailable daemon."""
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        del bundle["mcp"]["servers"]
        path = self.base / "bundle.yaml"
        path.write_text(yaml.safe_dump(bundle, allow_unicode=True))
        self.assertEqual(
            run_build(["--write", "--bundle", str(path)] + self.args()).returncode, 0)
        claude = json.loads((self.out / ".claude-plugin/plugin.json").read_text())
        self.assertNotIn("mcpServers", claude)
        self.assertFalse((self.out / ".mcp.json").exists())
        self.assertFalse((self.out / ".codex-mcp.json").exists())
        # The module itself still ships: the adapter is usable without a plugin.
        self.assertTrue((self.out / "mcp/bin/serveros-node-mcp").is_file())


if __name__ == "__main__":
    unittest.main()
