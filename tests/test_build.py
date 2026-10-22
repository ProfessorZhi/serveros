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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loader_graph import missing_from, production_closure  # noqa: E402

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
    # §35: named by `pack:cross-device-collaboration` in the repository's own
    # pack contract. They are here so the fixture catalogue can satisfy that
    # contract, and their absence would otherwise surface as a pack failure
    # with nothing to do with the code under test.
    "fleet-coordination",
    "shared-memory",
    "ability-management",
    "workspace-management",
    "test-management",
    "information-management",
    "work-management",
    "feishu-space-management",
    "feishu-report",
    "inventory-publishing",
    # The profile marks this one `available`, and this tuple is the registry
    # that claim is checked against — the fixture is passed as `--ability-repo`
    # and IS the pinned revision from the build's point of view. So a name the
    # profile can resolve has to be resolvable here too, or the both-directions
    # check fires on the fixture rather than on the world.
    #
    # It was absent while the profile said `missing`, and that was the accident
    # worth naming: the fixture was modelling the old truth without ever
    # asserting it, so the day the canonical `document-stewardship` Skill
    # landed and the profile moved to `available`, the check fired here first.
    # The fixture was right to fire — it just had to be told the news.
    "document-stewardship",
    # Same rule, second time. `delivery-pr-report` is the thin orchestration
    # over members this composition already resolves (`good-pr`, `feishu-report`,
    # `implementation-delivery`), and the profile marks it `available`, so the
    # pinned registry this fixture stands in for has to resolve it too. It is
    # named here rather than left to the pack contract because the claim under
    # test is the profile's, not the pack's.
    "delivery-pr-report",
)

# What `serveros-core` itself composes from ability-management. A subset of the
# above, and asserted as such rather than restated independently.
CORE_ABILITY_SKILLS = ("runtime-management", "ability-management")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return proc.stdout.decode().strip()


def make_ability_repo(base: Path, pycache_in: str | None = None,
                      packs: tuple[str, ...] = ("runtime-management",),
                      pin: str | None = None) -> tuple[Path, str]:
    """A minimal ability-management stand-in, committed, returning (repo, sha).

    TWO commits, because that is what the real repository does and because it
    is what makes the pin rule exercisable here. The first carries the
    catalogue; the second publishes the pack declarations, which name abilities
    from the first. So the pin is a revision whose manifest is unchanged and
    which is nevertheless not the revision being read — `scripts/build.py`
    accepts exactly that (an ancestor with an identical manifest) and refuses
    anything else, and a fixture with a single commit could not tell the two
    apart. `pin=` overrides it for the case that must be refused.
    """
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
    #
    # Every skill directory above is DECLARED here, not just the one the
    # provenance check reads. A manifest that created sixteen payloads and
    # named one of them was a catalogue that disagreed with its own disk, and
    # the pack closure check is what found it: the packs name abilities by
    # name, and a fixture whose manifest lists one ability can satisfy no pack
    # at all.
    (repo / "abilities-manifest.yaml").write_text(yaml.safe_dump({
        "schema_version": 3, "authors": [],
        "abilities": [
            {"type": "skill", "id": f"ProfessorZhi/{name}", "name": name,
             "path": f"skills/{name}", "artifact_mode": "vendored", "revisions": []}
            for name in ABILITY_SKILLS
        ],
    }, sort_keys=False, allow_unicode=True))
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "synthetic")
    catalogue = _git(repo, "rev-parse", "HEAD")

    # The declarations, published in the same repository as the catalogue they
    # name — the shape `publishing.location` asks for, and the one the real
    # repository now has. The pin names `catalogue`, the commit below, so this
    # second commit is the case the ancestor rule exists for: same catalogue,
    # different revision.
    (repo / "ability-packs.yaml").write_text(yaml.safe_dump({
        "schema": "serveros/ability-pack.v1", "version": 1,
        "packs": [{"id": f"pack:{i}", "name": f"Pack {i}", "purpose": "to exist",
                   "authority": {"repo": "ProfessorZhi/ability-management",
                                 "revision": pin or catalogue},
                   "members": [{"ability": f"ability:{name}", "role": "required"}],
                   } for i, name in enumerate(packs)],
    }, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "--quiet", "-m", "declarations")
    return repo, _git(repo, "rev-parse", "HEAD")


def write_pack_contract(base: Path) -> Path:
    """The SCHEMA half of the pack contract, which is all this repository owns.

    The packs themselves are published in ability-management and read from the
    git object at the pinned revision. So a hermetic build needs a hermetic
    CATALOGUE REPOSITORY and not merely a hermetic contract file, and
    `make_ability_repo()` is that: it writes the catalogue AND the declarations
    into one commit history, which is how the real one is laid out.

    Writing the packs into an injectable contract would have been the smaller
    change and the wrong one. It would have kept a second source for the packs
    alive in the one place nobody thinks to look for one — the fixture — so the
    tests would have been exercising a code path that no build in production
    has. The file this writes is what is left after the split: the rules.
    """
    path = base / "pack-contract.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(
        {"schema": "serveros/ability-pack.v1", "version": 1},
        sort_keys=False), encoding="utf-8")
    return path


def run_build(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BUILDER), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.rev = make_ability_repo(self.base)
        self.contract = write_pack_contract(self.base)
        self.out = self.base / "dist/current"
        self.marketplace = self.base / "dist/.agents/plugins/marketplace.json"

    def tearDown(self):
        self._tmp.cleanup()

    def args_for(self, repo: Path, rev: str, *extra: str) -> list[str]:
        """Build arguments against any ability-management repository.

        Parameterised because the pack fixtures now need their own REPOSITORY
        rather than their own contract file: the declarations live in the
        catalogue repository and are read from its git object, so a test that
        wants different declarations has to publish a different repository.
        """
        return [
            "--ability-repo", str(repo),
            "--ability-rev", rev,
            "--output", str(self.out),
            "--marketplace-path", str(self.marketplace),
            "--pack-contract", str(self.contract),
            *extra,
        ]

    def args(self, *extra: str) -> list[str]:
        return self.args_for(self.repo, self.rev, *extra)

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
            "--pack-contract", str(write_pack_contract(self.base)),
        ]
        self.assertEqual(run_build(args + ["--write"]).returncode, 0)
        stale = [p for p in self.out.rglob("*") if p.suffix == ".pyc" or p.name == "__pycache__"]
        self.assertEqual(stale, [])
        # The sibling SKILL.md must still be present, so the exclusion is precise.
        self.assertTrue((self.out / "skills/runtime-management/SKILL.md").is_file())

    def test_a_recommended_ability_never_reaches_the_payload(self):
        """§5: `recommended` is a claim about the node, not a member of the artifact.

        The profile names several abilities it does not ship, and no count is
        written down here because the last one to be written down ("four") went
        stale the moment a sixth arrived. Each of them resolves
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

    def test_a_pack_naming_an_absent_ability_fails_the_build(self):
        """The contract says "refused at build time, not carried with a marker
        beside it". This is the build saying it.

        `tests/test_packs.py` tests `packs.py`; this tests that the builder
        calls it. Both matter, and only this one can catch the call site going
        away — which is how the rule survived unimplemented for as long as it
        did.

        The name is `no-such-ability` rather than a real one that happens to be
        missing. `document-stewardship` was the example here until the profile
        above started claiming it resolves — and then one name was doing two
        jobs, "an ability the catalogue has" in one test and "an ability it
        does not" in this one. Which of the two a name is should not depend on
        what the rest of the suite has not got round to needing yet; a sentinel
        that cannot ever be registered says it by construction.
        """
        repo, rev = make_ability_repo(self.base / "absent",
                                      packs=("no-such-ability",))
        result = run_build(self.args_for(repo, rev, "--write"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-such-ability", result.stderr)
        self.assertIn("does not exist", result.stderr)

    def test_a_pack_pinned_to_another_revision_fails_the_build(self):
        """The case that goes green: every member resolves, and the answer is
        about a catalogue that is not the one being built against."""
        repo, rev = make_ability_repo(self.base / "badpin", pin="0" * 40)
        result = run_build(self.args_for(repo, rev, "--write"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("pin", result.stderr.lower())

    def test_a_closed_pack_does_not_block_the_build(self):
        """The gate has to be able to say yes, or the two tests above pass on a
        builder that refuses everything."""
        result = run_build(self.args("--write"))
        self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_intake_ships_inside_the_artifact(self):
        """§J's support chain reads its contract out of the artifact.

        The contract is a `.yaml` beside the module, and the builder excludes
        only byte-code -- so the module shipping while its contract did not
        would be a runtime that reads an empty rule set and answers nothing,
        which is the failure mode that looks like a knowledge base with no
        entries in it.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        self.assertTrue((self.out / "intake/support.py").is_file())
        self.assertTrue((self.out / "intake/support-contract.yaml").is_file())
        # The fallback's own paths, asserted by name for the same reason: the
        # module reads TWO files beside it -- the support contract for the
        # redaction patterns, and its own for the triggers and the two anchors.
        # Naming only one would let the other stop shipping silently, and the
        # failure would read as 「no fallback fires」 rather than 「the fallback
        # came without its contract」.
        self.assertTrue((self.out / "intake/support_handoff.py").is_file())
        self.assertTrue((self.out / "intake/support-handoff-contract.yaml").is_file())

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

    def test_the_artifact_answers_the_production_loader_closure(self):
        """The artifact must contain everything its own surfaces load.

        This is the gate the C4 defect needed and did not have. The test above
        it iterates `build.CORE_MODULES`, so it can only confirm modules
        somebody already remembered to list — `runtime/cli.py` loaded
        `intake/global_board.py` for `serverosctl requirement board`, and
        `intake` was on no list, so the verb worked from a source checkout and
        raised FileNotFoundError on every installed node. Measured, on the
        running node and on a clean external install.

        The requirement here comes from the loader graph — what these surfaces
        actually load — so a module nobody lists is a failure rather than an
        absence of evidence. `CORE_MODULES` stays the builder's implementation
        input, and it is no longer the authority for this question.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        missing = missing_from(self.out)
        self.assertEqual(
            missing, [],
            "the artifact omits modules its own entry points load: "
            f"{[str(p) for p in missing]}")

    def test_the_loader_closure_gate_can_go_red(self):
        """A gate that cannot fail certifies nothing.

        Removing a required module from a copy of the artifact must make the
        closure check report exactly that module — and, in the same breath, the
        `CORE_MODULES` test must still pass on the same damaged tree. That
        second half is the point: it is the demonstration that the old gate was
        blind here, not merely unlucky, and it is why the requirement had to
        move rather than the list grow a third time.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)

        # The exact module that shipped missing. Asserted to be required rather
        # than assumed, so this control cannot quietly stop testing anything.
        victim = Path("intake/global_board.py")
        self.assertIn(victim, production_closure(),
                      "the closure no longer requires the module this control "
                      "removes; pick the one it does require")

        damaged = self.base / "damaged"
        shutil.copytree(self.out, damaged)
        (damaged / victim).unlink()

        self.assertEqual(missing_from(damaged), [victim],
                         "the closure gate did not notice the removal")
        self.assertEqual(missing_from(self.out), [])

        # The old predicate, applied to the same damaged tree, still passes.
        for module in build.CORE_MODULES:
            self.assertTrue(
                (damaged / module).is_dir(),
                f"expected the CORE_MODULES gate to be blind to this removal, "
                f"but it flagged {module}")

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

    def test_every_declared_mcp_surface_starts_from_the_artifact(self):
        """Every surface the artifact declares must import, from the artifact.

        The check that would have caught the `fleet/` gap is not a file-exists
        check on `fleet/`: the gap was that `mcp/agent_mcp.py` loads
        `fleet/agent_control_router.py` at module scope, so the **entry point**
        raises before it reads a byte of stdin. Nothing about the artifact's
        own contents makes that visible unless something starts the entry
        point the way an agent will.

        Each surface is asked for its tool list rather than merely imported:
        an import that succeeds and a surface that answers are different
        claims, and the manifest in `.mcp.json` claims the second.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        servers = json.loads((self.out / ".mcp.json").read_text())["mcpServers"]
        self.assertTrue(servers, "the composition declares no MCP surface")
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        for name in sorted(servers):
            entry = Path(servers[name]["args"][0].replace(
                "${CLAUDE_PLUGIN_ROOT}", str(self.out)))
            with self.subTest(surface=name):
                proc = subprocess.run(
                    [sys.executable, str(entry)], input=request,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    cwd=str(self.base), timeout=120)
                self.assertEqual(
                    proc.returncode, 0,
                    f"{name} could not start from the artifact:\n{proc.stderr}")
                answer = json.loads(proc.stdout.strip().splitlines()[0])
                self.assertTrue(answer["result"]["tools"],
                                f"{name} started but declared no tools")

    def test_the_built_artifact_carries_no_credential_shape(self):
        """§28: the credential gate, pointed at the thing that actually ships.

        `test_secret_scan.py` proves the scanner fires and does not cry wolf;
        this proves it is wired to a release. Neither half is a gate alone — a
        scanner that is never run on an artifact is one that would not have
        caught one in an artifact.

        The artifact is a strictly larger surface than the source tree here: it
        carries the pinned ability-management skills, which this repository does
        not contain and therefore cannot scan.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        scanner = ROOT / "scripts" / "scan_secrets.py"
        proc = subprocess.run([sys.executable, str(scanner), str(self.out), "--json"],
                              capture_output=True, text=True, timeout=180)
        report = json.loads(proc.stdout)
        self.assertEqual(
            proc.returncode, 0,
            f"credential shape(s) in the built artifact: {report.get('findings')}")
        self.assertEqual(report["verdict"], "CLEAN")
        # A gate that scanned nothing also exits 0; assert it looked at something.
        self.assertGreater(report["scanned_files"], 0,
                           "the scanner read no files, so its CLEAN means nothing")

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

    def test_codex_marketplace_does_not_name_a_staging_directory(self):
        """A build into a scratch directory is fine; recording it is not.

        `release.py` promotes a composition into `<runtime-root>/<composition>/
        current`, so that is what the descriptor has to name — the build's own
        output directory survives only until the release moves it. On this node
        the file named `./.staging-c233946`, which is the directory `current`
        was built from and which a staging cleanup is entitled to consider.
        """
        staging = self.base / "dist/.staging-deadbeef"
        args = [
            "--ability-repo", str(self.repo), "--ability-rev", str(self.rev),
            "--output", str(staging), "--marketplace-path", str(self.marketplace),
            "--pack-contract", str(self.contract), "--write",
        ]
        self.assertEqual(run_build(args).returncode, 0)
        market = json.loads(self.marketplace.read_text())
        composition = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())["composition"]
        self.assertEqual(market["plugins"][0]["source"]["path"],
                         f"./{composition}/current")
        self.assertNotIn(".staging", market["plugins"][0]["source"]["path"],
                         "a descriptor that names a disposable directory is one "
                         "cleanup away from naming nothing")

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
        agents = ROOT / ".agents"
        def snapshot():
            return {str(p.relative_to(ROOT)): (p.lstat().st_mode, p.lstat().st_size,
                                               p.lstat().st_mtime_ns)
                    for p in agents.rglob("*")} if agents.exists() else None
        before = snapshot()
        result = run_build([
            "--check",
            "--ability-repo", str(self.repo), "--ability-rev", self.rev,
        ])
        self.assertEqual(result.returncode, 2)
        self.assertIn("--output", result.stderr)
        # Review metadata may already exist here. Missing --output must leave
        # it untouched rather than requiring all unrelated source metadata absent.
        self.assertEqual(snapshot(), before)
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

    def test_running_the_entry_points_does_not_write_into_the_artifact(self):
        """The same property as the server above, for the two entry points that
        have no config file to carry it.

        `serverosctl` and `serverosd` import their runtime out of the revision
        they were launched from, so running either one wrote `__pycache__` into
        it. Measured on the live node: 16 files from a single `serverosctl
        --help`, after which the revision's recorded tree hash — which is the
        node's identity for it — no longer matched anything on disk.

        `release.py` cannot prevent this. It does force the variable for its own
        verification probe, but the shim is also run from a plain shell, where
        no unit file applies. So the fix lives in the shim, and the env is
        deliberately NOT supplied here: a test that inherited
        `PYTHONDONTWRITEBYTECODE` from the runner would pass against a shim
        without it, which is exactly how the verifier's version of this bug
        stayed hidden.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        env = {k: v for k, v in os.environ.items()
               if k != "PYTHONDONTWRITEBYTECODE"}
        before = {p for p in self.out.rglob("*")}
        proc = subprocess.run([str(self.out / "bin" / "serverosctl"), "--help"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=60, cwd=self.out, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual({p for p in self.out.rglob("*")} - before, set(),
                         "running the shipped CLI wrote into the artifact")

        # `serverosd` gets the same line, but cannot be exercised this way — it
        # would start the daemon's loop rather than return. So it is asserted
        # structurally, which catches its removal and nothing subtler.
        for name in ("serverosctl", "serverosd"):
            self.assertIn("sys.dont_write_bytecode = True",
                          (self.out / "bin" / name).read_text(),
                          f"{name} does not suppress bytecode writing")

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


class TheBuildRefusesToShipACredential(unittest.TestCase):
    """§28's scan as a GATE, with the control that makes it a gate.

    `scripts/scan_secrets.py` has said since it was written that it exists "so a
    release can fail instead of shipping one", and nothing in `build.py` or
    `release.py` called it. A docstring is not a check — the same sentence
    `scripts/publication.py` writes about its own rules — so the scan is now
    inside `build()` and these tests are what separates "the build scans" from
    "the build claims to scan".

    The gate is deliberately paired with a clean build in the same test. A gate
    that is only ever observed refusing is indistinguishable from a gate that
    refuses everything.
    """

    # Assembled at runtime so this file carries no matchable credential shape
    # of its own — otherwise the fixture that proves the scanner works is
    # itself a finding when the scanner is pointed at the repository.
    FAKE_AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo, self.rev = make_ability_repo(self.base)
        self.contract = write_pack_contract(self.base)
        self.out = self.base / "dist/current"
        self.marketplace = self.base / "dist/.agents/plugins/marketplace.json"

    def tearDown(self):
        self._tmp.cleanup()

    def args_for(self, repo: Path, rev: str, *extra: str) -> list[str]:
        return [
            "--ability-repo", str(repo),
            "--ability-rev", rev,
            "--output", str(self.out),
            "--marketplace-path", str(self.marketplace),
            "--pack-contract", str(self.contract),
            *extra,
        ]

    def args(self, *extra: str) -> list[str]:
        return self.args_for(self.repo, self.rev, *extra)

    def _repo_carrying(self, payload_text: str) -> tuple[Path, str]:
        """The synthetic catalogue with one more committed payload file."""
        target = (self.repo / "skills" / "runtime-management" / "payload"
                  / "case-notes.md")
        target.write_text(payload_text, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "--quiet", "-m", "payload")
        return self.repo, _git(self.repo, "rev-parse", "HEAD")

    def test_a_clean_composition_is_written_and_a_credentialed_one_is_not(self):
        # Control: the same build, without the credential, succeeds.
        self.assertEqual(
            run_build(self.args_for(*self._repo_carrying("# notes\nnothing here\n"))
                      + ["--write"]).returncode, 0)
        before = {p.relative_to(self.out): p.read_bytes()
                  for p in self.out.rglob("*") if p.is_file()}
        self.assertTrue(before)

        repo, rev = self._repo_carrying(
            f"# notes\n\naws_access_key_id = {self.FAKE_AWS_KEY}\n")
        result = run_build(self.args_for(repo, rev) + ["--write"])

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("SECRET", result.stderr)
        self.assertIn("aws-access-key-id", result.stderr)
        self.assertIn("REFUSING", result.stderr)
        # Nothing was written. The composition is judged before the output
        # directory is pruned, so a refusal cannot have already deleted files.
        after = {p.relative_to(self.out): p.read_bytes()
                 for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(before, after,
                         "the build refused but had already changed the artifact")

    def test_check_finds_a_credential_standing_in_the_artifact(self):
        """The half an operator runs against a promoted node.

        `--write` judges the composition in memory; `--check` also judges the
        tree on disk, because a file nobody composed into the artifact is
        exactly where one would be hiding and `--check` is the mode that looks
        at a node rather than at a source tree.
        """
        self.assertEqual(run_build(self.args("--write")).returncode, 0)
        stray = self.out / "context/stray.toml"
        stray.write_text(f'key = "{self.FAKE_AWS_KEY}"\n', encoding="utf-8")

        result = run_build(self.args("--check"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("SECRET context/stray.toml:1 aws-access-key-id", result.stderr)
        # And it is still reported as unexpected, because it is both things.
        self.assertIn("DRIFT unexpected context/stray.toml", result.stderr)


if __name__ == "__main__":
    unittest.main()
