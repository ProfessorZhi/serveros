"""Tests for the runtime truth inspector.

Hermetic in the same sense as test_build.py: the reusable-skill source is a
synthetic ability-management repository in a temp directory, and every runtime
target — the built artifact, the Claude install record, the Codex cache — is
synthesized here. The only real input is this repository, which is the unit
under test.

The asymmetric case matters most and is tested explicitly: an installed copy
that has the right file NAMES but stale BYTES. A presence-only check calls that
clean, and it is precisely the "I thought the Agent was running the new
version" failure this module exists to catch.
"""
import argparse
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "scripts" / "runtime_truth.py"
BUILDER = ROOT / "scripts" / "build.py"

# The composition the node runs from. A literal here rather than
# `truth.NODE_COMPOSITION` because the synthetic composition file has to declare
# it BEFORE the inspector is loaded, and a name that comes from the module under
# test would let a rename on both sides agree with each other and disagree with
# every machine.
NODE_COMPOSITION = "serveros-node-fixture"
NODE_UNIT = "serverosd.service"

# The fixture's clock, for the one comparison in the inspector that is about
# TIME rather than about bytes. A promoted node is one whose process started
# after `current` was last repointed, and both readings come from the kernel, so
# a fixture that let the symlink take the machine's real clock would compare a
# synthetic process start against a real file mtime and read every healthy node
# as drift. `install_node` stamps the link; a test that means "promoted since"
# stamps it later, and one that means "restarted since" starts the process later.
LINK_MTIME = 1_500_000.0

_spec = importlib.util.spec_from_file_location("runtime_truth", INSPECTOR)
truth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(truth)

# Core's install-record key, DERIVED the way every reader in the inspector now
# derives it. It used to be `truth.CLAUDE_PLUGIN_KEY`, a literal in the script;
# the literal is gone because it named one composition instead of deriving the
# one being measured — a node whose `--output` is a profile got core's record
# back and reported it as the profile's state. A fixture that kept reaching for
# the literal would put the same coupling back on the other side of the test.
# Everything this fixture builds is core, so core is the name it derives.
CORE_NAME = truth.BUNDLE_NAME
CORE_PLUGIN_KEY = truth.plugin_key(CORE_NAME)

_bspec = importlib.util.spec_from_file_location("serveros_build_rt", BUILDER)
build = importlib.util.module_from_spec(_bspec)
_bspec.loader.exec_module(build)

# The release tool, imported for the same reason the inspector imports it: the
# fixture has to PRODUCE a node, and a fixture that hand-wrote the layout would
# be testing this file against its own idea of what `promote` does rather than
# against what `promote` does.
_rspec = importlib.util.spec_from_file_location("serveros_release_rt",
                                                ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(release)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_build import _git as git  # noqa: E402
from test_build import make_ability_repo, write_pack_contract  # noqa: E402

# §J09. The node runtime is asked to answer "which release am I", and the point
# of these tests is that the answer comes from the INSTALLATION rather than from
# a checkout: `_installed_identity` is given the revision directory and reads
# what the installer wrote there. Loaded under a name of its own for the same
# reason `test_daemon.py` does — two modules called `daemon` in one process is
# how a test starts asserting against someone else's copy.
_dspec = importlib.util.spec_from_file_location("serveros_runtime_daemon_rt",
                                               ROOT / "runtime" / "daemon.py")
daemon = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(daemon)

# The rendering half of the same answer. §J09 is not satisfied by a field that
# exists in a JSON payload nobody prints: `serverosctl status` is where an
# operator reads it, so the text is asserted as well as the structure.
sys.path.insert(0, str(ROOT))
from runtime import cli  # noqa: E402


def rows_by(report: dict) -> dict[str, dict]:
    return {r["component"]: r for r in report["rows"]}


# The report deliberately mixes two kinds of statement, and they are not
# equally testable. Rows about the *delivery chain* describe the artifact this
# repository builds and are fully synthesized by this fixture. Rows about the
# *host* describe facts the fixture cannot control: whether the real checkout
# has uncommitted work, and whether the synthetic engine dir happens to be a
# git repo. Asserting "every row is OK" therefore asserted a pristine machine,
# not a correct inspector — it failed on the first run for that reason and no
# other. Chain rows are what this module verifies; host rows are what it
# reports.
HOST_ROWS = {"serveros (bundle source)", "work-engine (canonical)"}

# Both of those rows used to describe the machine the suite happened to run on,
# which is why the original assertion had to exclude them. They are now
# synthesized too — the fixture commits a copy of this bundle and a stub engine
# — so the chain axis is assertable in full. The set is kept because the two
# rows still describe *checkouts* rather than the delivery chain, which is a
# distinction the release axes care about.


def chain_rows(report: dict) -> list[dict]:
    # `applies` is filtered here for the same reason the axis computation
    # filters it: a question this machine cannot be asked is not a chain
    # statement, and asserting that it reads OK would assert a row that says
    # UNKNOWN *because it was not asked*. The node rows are the reason this
    # matters — a machine with no node runtime has no running surface to
    # compare, and the fixture is such a machine until a test installs one.
    return [r for r in report["rows"]
            if r["component"] not in HOST_ROWS and r.get("applies", True)]


# The rows each of the two root-resolver copies names WITHOUT the other naming
# it, declared rather than left to be discovered.
#
# `test_the_two_copies_of_the_root_resolver_agree` compares
# `set(truth.ROOT_SOURCES) & set(check.ROOT_SOURCES)`, an INTERSECTION: a row
# one table carries and the other does not is not compared there, not counted,
# and not named. Measured 2026-10-09, that is 6 rows of the 8 the two tables
# name between them — the gate covers 2. The two tables do different jobs
# (a runtime gauge's inputs and a conformance checker's), so differing is
# legitimate; the reason each individual row sits on one side only is recorded
# nowhere, and this declaration does not invent one. What it does is make a
# row that moves from one side to the other, or a new row added to either
# table, fail where it is written instead of drifting.
ONLY_IN_RUNTIME_TRUTH = ("engine_repo", "marketplace_path", "runtime_root")
ONLY_IN_ARCHITECTURE_CHECK = ("data_root", "ops_repo", "workspace_root")


class RuntimeTruthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.ability, self.rev = make_ability_repo(self.base)
        # The build validates the pack contract, and the repository's own pins
        # a real ability-management commit. Against the synthetic catalogue
        # that pin cannot match, so the builder would refuse for a reason that
        # has nothing to do with the runtime truth this module asserts. The
        # contract is injected rather than the check being skipped: skipping it
        # when the file is absent is exactly the silent pass the check exists
        # to remove.
        self.contract = write_pack_contract(self.base)
        self.out = self.base / "dist/current"
        self.marketplace = self.base / "dist/.agents/plugins/marketplace.json"
        # A committed copy of this repository's bundle, so that the two rows
        # about the *source checkouts* are facts this fixture controls rather
        # than facts about whatever machine runs the suite. Before this, the
        # chain axis inherited the real worktree's dirtiness and could not be
        # asserted at all — the same "asserting a pristine machine" problem the
        # HOST_ROWS note below was written about, one axis further out.
        self.serveros = self.base / "serveros"
        # The whole directory, rather than the two files this used to name.
        # `build.py` loads `scan_secrets.py` by path at build time, so the moment
        # the credential gate was wired into `build()` the hand-written pair fell
        # behind and all 37 tests in this file died on
        # `FileNotFoundError: scripts/scan_secrets.py` — the same silent drift the
        # composition below was re-written to stop, one file over.
        #
        # They are copies and not re-implementations: `release.py` computes the
        # staged tree hash the inspector verifies, and a second implementation of
        # the recipe would produce a correct number that cannot be compared with
        # the recorded one.
        shutil.copytree(ROOT / "scripts", self.serveros / "scripts",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # The composition is read from the bundle, not restated here. As a
        # literal tuple it was a second copy of a vocabulary the bundle already
        # owns, and it silently fell behind the moment a member was added: the
        # synthetic checkout had no `skills/runtime-management` and no `mcp/`,
        # so the artifact stopped being recomputable and the chain axis went red
        # for a reason that had nothing to do with the chain.
        bundle = yaml.safe_load((ROOT / "plugin/bundle.yaml").read_text())
        members = ["plugin"] + [f"skills/{s['name']}" for s in bundle["skills"]
                                if s["source"] == "local"]
        members += [m for m in build.CORE_MODULES if bundle.get(m, {}).get("path")]
        for rel in members:
            shutil.copytree(ROOT / rel, self.serveros / rel)
        # A second composition for the synthetic source, because the node does
        # not run the plugin's composition and this suite has to be able to
        # measure a node. It is minimal on purpose: the real profile's
        # `recommended:` list is checked against the real catalogue, and against
        # a one-ability fixture it can only fail for reasons that have nothing
        # to do with the running surface.
        #
        # `workspace-management` is here so the composition ADD a member core
        # does not carry. Without it the fixture's node composition had exactly
        # the plugin's members, and the test asserting that both are audited
        # could not tell an audit of the node from an audit of the plugin — the
        # two would agree on every member by construction.
        (self.serveros / "plugin/profiles/fixture-node.yaml").write_text(yaml.safe_dump({
            "schema": "serveros/bundle.v1",
            "extends": "../bundle.yaml",
            "composition": NODE_COMPOSITION,
            "name": "hwz-server-os-fixture-node",
            "cachebuster": "fixture.1",
            "artifacts": ["plugin"],
            "skills": [
                {"name": "runtime-management", "source": "ability-management"},
                {"name": "workspace-management", "source": "ability-management"},
            ],
        }, sort_keys=False))
        # Importing build.py writes scripts/__pycache__, which would make the
        # synthetic checkout permanently dirty the moment the inspector loads
        # the builder it is inspecting.
        (self.serveros / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        git(self.serveros, "init", "--quiet", "-b", "main")
        git(self.serveros, "config", "user.email", "test@example.invalid")
        git(self.serveros, "config", "user.name", "test")
        git(self.serveros, "add", "-A")
        git(self.serveros, "commit", "--quiet", "-m", "synthetic serveros")

        # The fixture builds with the builder that belongs to ITS OWN checkout.
        # `build.py` finds its repository from `__file__`, and the copy in this
        # file's `scripts/` resolves to the real worktree — so a profile that
        # `extends:` its parent is resolved against the wrong root and refused
        # for escaping a repository it never left. Binding the loader to the
        # synthetic checkout is what makes `extends` mean here what it means on
        # a machine, and it is the same loader the inspector uses, so the
        # artifact this fixture builds is the artifact the inspector recomputes.
        self.build = truth._load_build(self.serveros)

        self.engine_repo = self.base / "engine"
        (self.engine_repo / "workengine").mkdir(parents=True)
        (self.engine_repo / "workengine" / "cli.py").write_text("# engine\n")
        (self.engine_repo / "workengine" / "federation.py").write_text("# fed\n")
        git(self.engine_repo, "init", "--quiet", "-b", "main")
        git(self.engine_repo, "config", "user.email", "test@example.invalid")
        git(self.engine_repo, "config", "user.name", "test")
        git(self.engine_repo, "add", "-A")
        git(self.engine_repo, "commit", "--quiet", "-m", "synthetic engine")

        self.engine_installed = self.base / "site-packages/workengine"
        self.engine_installed.mkdir(parents=True)
        (self.engine_installed / "cli.py").write_text("# engine\n")
        (self.engine_installed / "federation.py").write_text("# fed\n")
        self.claude_json = self.base / "installed_plugins.json"
        self.claude_settings = self.base / "claude-settings.json"
        self.codex_config = self.base / "config.toml"
        self.codex_cache = self.base / "codex-cache"
        # Runtime skill roots are pointed INSIDE the fixture, at paths that do
        # not exist. Defaulting them to the real ~/.claude/skills would make
        # this suite assert facts about the machine it happens to run on — the
        # same mistake that made "every row is OK" fail on the first run.
        self.claude_skills = self.base / "claude-skills"
        self.codex_skills = self.base / "codex-skills"
        # The node's own surface, pointed at paths inside the fixture that do
        # not exist. A test that does not build a node therefore asserts the
        # file's plugin half, and "this machine has no node runtime" is a fact
        # the fixture states rather than a fact about the machine running the
        # suite. `install_node` turns all three on.
        #
        # It is `dist/` rather than a second root, and that is the node's own
        # arrangement rather than a convenience. `ROOT_SOURCES` derives the
        # marketplace from `runtime_assets_root/hwz-server-os/.agents/...` and
        # the runtime root from `runtime_assets_root/hwz-server-os`, so on every
        # machine the marketplace root IS the runtime root and
        # `<runtime-root>/<composition>/current` is inside it. Two roots here
        # made this fixture a machine the product cannot describe: the derived
        # `--output` escaped the marketplace root, `codex_marketplace` refused it
        # by design, and every row that recomputes the artifact turned UNKNOWN
        # for a reason nothing on a real node can reproduce.
        self.runtime_root = self.base / "dist"
        self.unit_dir = self.base / "systemd" / "user"
        self.proc_dir = self.base / "proc"
        self.node_artifact = self.base / "dist" / "node"

    def tearDown(self):
        self._tmp.cleanup()

    def build_artifact(self):
        rc = self.build.build(self.serveros / "plugin/bundle.yaml", self.ability, self.rev,
                         self.out, self.marketplace, write=True,
                         pack_contract_path=self.contract)
        self.assertEqual(rc, 0)
        version = json.loads((self.out / ".claude-plugin/plugin.json").read_text())["version"]
        return version

    def contract_at(self, rev: str) -> Path:
        """The pack contract to build `rev` against.

        One file for every revision, and that IS the change worth naming. This
        used to be written per revision, because the packs were declared inside
        the contract and the pin had to name the revision being built from.
        The packs are published in the ability-management repository now, so a
        per-revision contract would be a SECOND source for them — precisely
        what the split removed — and the pin travels with the declarations
        instead. What this still does is refuse to hand back a path for a
        revision that cannot answer for its own declarations, because a check
        that a caller can relax is not a check, and the reader is a worse place
        to discover a missing file than here.
        """
        git(self.ability, "show", f"{rev}:ability-packs.yaml")
        return self.contract

    def install_claude(self, version: str, path: Path, rev: str | None = None,
                       key: str | None = None, enabled: bool = True,
                       record: bool = True):
        """Install one plugin the way the platform does, in both files.

        Both, because the platform keeps the two facts apart and so must the
        fixture: `installed_plugins.json` says what is on disk and
        `settings.json` says what a session loads. Writing only the first left
        the fixture unable to express the state a migration leaves behind — the
        old plugin installed and disabled, the new one enabled — which is the
        state this node was actually in when the plugin rows were found to be
        looking at the wrong one.
        """
        key = key or CORE_PLUGIN_KEY
        installed = json.loads(self.claude_json.read_text()) if self.claude_json.is_file() \
            else {"version": 2, "plugins": {}}
        if record:
            installed["plugins"][key] = [{"scope": "user", "installPath": str(path),
                                          "version": version,
                                          "gitCommitSha": rev or self.rev}]
        else:
            installed["plugins"].pop(key, None)
        self.claude_json.write_text(json.dumps(installed))
        settings = json.loads(self.claude_settings.read_text()) \
            if self.claude_settings.is_file() else {}
        settings.setdefault("enabledPlugins", {})[key] = enabled
        self.claude_settings.write_text(json.dumps(settings))

    def build_and_install(self, rev: str | None = None) -> str:
        """Build the artifact from `rev` and install it as that revision.

        The pin is not decoration: `build_readme` embeds the ability revision
        in the payload, so an artifact built from one revision and recorded
        against another can never be chain-consistent. Building and pinning
        together is the only way to reach the interesting case — a chain that
        agrees with itself *and* a revision canonical does not contain.
        """
        rev = rev or self.rev
        rc = self.build.build(self.serveros / "plugin/bundle.yaml", self.ability, rev,
                         self.out, self.marketplace, write=True,
                         pack_contract_path=(self.contract if rev == self.rev
                                             else self.contract_at(rev)))
        self.assertEqual(rc, 0)
        version = json.loads((self.out / ".claude-plugin/plugin.json").read_text())["version"]
        self.install_claude(version, self.out, rev)
        self.install_codex(version, self.out)
        return version

    def install_codex(self, version: str, payload_src: Path):
        leaf = self.codex_cache / truth.CODEX_MARKETPLACE / truth.BUNDLE_NAME / version
        leaf.parent.mkdir(parents=True, exist_ok=True)
        # Codex is single-slot: installing over an existing version replaces it,
        # which is what `codex plugin add` does on the real machine.
        if leaf.exists():
            shutil.rmtree(leaf)
        shutil.copytree(payload_src, leaf)
        self.codex_config.write_text(
            "[marketplaces.%s]\nsource = \"/somewhere\"\n\n"
            "[plugins.\"%s@%s\"]\nenabled = true\n"
            % (truth.CODEX_MARKETPLACE, truth.BUNDLE_NAME, truth.CODEX_MARKETPLACE))

    def args(self, **over):
        base = dict(
            serveros_repo=self.serveros, ability_repo=self.ability, ability_rev=self.rev,
            output=self.out, marketplace_path=self.marketplace,
            engine_repo=self.engine_repo, engine_installed=self.engine_installed,
            claude_plugins_json=self.claude_json,
            claude_settings_json=self.claude_settings,
            codex_config=self.codex_config,
            codex_cache=self.codex_cache,
            canonical_ref=truth.DEFAULT_CANONICAL_REF,
            claude_skills_root=self.claude_skills,
            codex_skills_root=self.codex_skills,
            runtime_root=self.runtime_root,
            composition=NODE_COMPOSITION,
            unit_dir=self.unit_dir,
            proc_root=self.proc_dir,
            # Where the tree of a commit an artifact records is extracted so the
            # artifact can be recomputed from its OWN source. It is under the
            # fixture's temp dir for the same reason every other fixture path
            # is: a run that wrote into the shared scratch root would leave the
            # suite's state behind on the machine for the next run to read.
            source_cache=self.base / "source-cache",
            use_checkout_source=False,
        )
        base.update(over)
        return argparse.Namespace(**base)

    # ---- the node, which is not the plugin distribution ----------------

    def build_node_artifact(self, cachebuster: str = "fixture.1") -> Path:
        """Build the fixture's node composition, the way release.py expects it.

        Composed by `build()` rather than copied from the core artifact: the two
        compositions have different member sets, which is exactly why auditing
        only one of them left the node's members unchecked.
        """
        profile = self.serveros / "plugin/profiles/fixture-node.yaml"
        if cachebuster != "fixture.1":
            doc = yaml.safe_load(profile.read_text())
            doc["cachebuster"] = cachebuster
            profile.write_text(yaml.safe_dump(doc, sort_keys=False))
        rc = self.build.build(profile, self.ability, self.rev, self.node_artifact,
                         self.marketplace, write=True, pack_contract_path=self.contract)
        self.assertEqual(rc, 0)
        return self.node_artifact

    def install_node(self, artifact: Path | None = None, label: str = "main@0a98bb6",
                     revision: str | None = None, manifest: bool = True,
                     declared: str | None = None) -> Path:
        """Stage an artifact and point a unit at it, the way `promote` does.

        `release.stage` is called for the layout and `release.fingerprint` for
        the hash, so the recorded number is the one the real tool would record.
        A hand-written fixture would agree with the inspector about a layout
        neither of them shares with the machine.
        """
        artifact = artifact or self.node_artifact
        if not (artifact / "bin" / "serverosd").is_file():
            self.build_node_artifact()
        root = self.runtime_root / NODE_COMPOSITION
        revisions = root / "revisions"
        revisions.mkdir(parents=True, exist_ok=True)
        # Re-staging one label is how this fixture models a rebuild, and it is
        # the fixture's own act rather than a flag on `stage`: `stage` refuses an
        # existing revision and has no argument that would remove one, because a
        # release tool that deletes a staged revision for the asking cannot tell
        # a deliberate rebuild from another actor's release. Removing it here
        # says which one this is.
        if (revisions / label).exists():
            shutil.rmtree(revisions / label)
        rev_dir = release.stage(artifact, revisions, label)
        if manifest:
            (rev_dir / truth.TREE_MANIFEST).write_text(json.dumps({
                "composition": NODE_COMPOSITION, "revision": label,
                "kind": release.NODE_RUNTIME,
                "tree_hash": release.fingerprint(artifact),
                "runtime_root": str(root / "current"), "previous": None,
            }, indent=2) + "\n")
        link = root / "current"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(rev_dir)
        os.utime(link, (LINK_MTIME, LINK_MTIME), follow_symlinks=False)

        self.unit_dir.mkdir(parents=True, exist_ok=True)
        exec_start = declared if declared is not None else str(link / "bin/serverosd")
        (self.unit_dir / NODE_UNIT).write_text(
            "[Unit]\nDescription=fixture\n\n[Service]\nType=simple\n"
            f"ExecStart={exec_start}\n")
        return rev_dir

    def run_process(self, argv: list[str], pid: int = 4242,
                    started_at: float | None = None) -> None:
        """A synthetic `/proc/<pid>`. `started_at` is wall-clock seconds."""
        (self.proc_dir / str(pid)).mkdir(parents=True, exist_ok=True)
        (self.proc_dir / str(pid) / "cmdline").write_bytes(
            b"".join(part.encode() + b"\0" for part in argv))
        if started_at is None:
            return
        boot = 1_000_000.0
        (self.proc_dir / "stat").write_text(f"cpu  0 0 0 0\nbtime {int(boot)}\n")
        hz = os.sysconf("SC_CLK_TCK")
        ticks = int((started_at - boot) * hz)
        # The `comm` field is wrapped in parentheses and may contain BOTH a space
        # and a `)`, so the parser has to find field 22 by the LAST `)` rather
        # than the first. Both are exercised here because a bare name would make
        # the two parsings agree, and a mutation that swapped one for the other
        # would then be indistinguishable from correct code.
        fields = ["S"] + ["0"] * 18 + [str(ticks)]
        (self.proc_dir / str(pid) / "stat").write_text(
            f"{pid} (python3 (serverosd)) " + " ".join(fields) + "\n")

    def node_rows(self, report) -> dict:
        return rows_by(report)

    def side_branch_commit(self, branch: str = "side") -> str:
        """A commit reachable from a branch but NOT from main.

        Empty on purpose: the point of these tests is that the *chain* stays
        intact while provenance fails, and an ability-repo commit that touched
        any tracked file would change the artifact and break the chain too,
        which is a different scenario.
        """
        git(self.ability, "checkout", "--quiet", "-b", branch)
        git(self.ability, "commit", "--quiet", "--allow-empty", "-m", "side work")
        sha = git(self.ability, "rev-parse", "HEAD")
        git(self.ability, "checkout", "--quiet", "main")
        return sha

    def flatten(self, root: Path, member: str, source: Path | None = None):
        """Copy a bundle member into a runtime skill root, as the pre-F4
        control plane did."""
        import shutil
        src = source or (self.out / "skills" / member)
        dst = root / member
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        return dst

    # ---- the clean case -------------------------------------------------

    def test_a_faithful_runtime_reports_ok(self):
        version = self.build_artifact()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        report = truth.collect(self.args())
        rows = chain_rows(report)
        self.assertEqual([r["component"] for r in rows if r["verdict"] != truth.OK], [])
        self.assertNotEqual(report["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertNotEqual(report["verdict"], truth.MISSING)

    # ---- §J09: the running node proving who it is ------------------------

    def _installed_identity(self, rev_dir: Path) -> dict:
        """The runtime's own answer, read the way it reads it: from the
        revision directory, with no repository and no git anywhere near it."""
        return daemon._installed_identity(rev_dir / "runtime")

    def test_an_installed_node_can_say_which_release_and_source_it_is(self):
        """The five things §J09 requires, from one read of the installation.

        Every one of them is answered from what `release.py` staged, so this
        passes on a machine with no checkout — which is the only kind of machine
        that runs a node.
        """
        rev = self.install_node()
        identity = self._installed_identity(rev)
        fields = identity["fields"]

        self.assertEqual(fields["release_id"], "main@0a98bb6")
        self.assertEqual(fields["tree_hash"], release.fingerprint(self.node_artifact))
        self.assertTrue(fields["artifact_version"], "no artifact version recorded")
        self.assertEqual(fields["source_commit"], git(self.serveros, "rev-parse", "HEAD"))
        self.assertEqual(fields["ability_management_revision"], self.rev)
        # ...and the record that connects the commit to these bytes. Without
        # it the commit names a checkout, not a content.
        self.assertIs(fields["source_tree_clean"], True)

        self.assertEqual(identity["agreement"], "AGREEMENT")
        self.assertEqual(identity["unestablished"], [])
        self.assertEqual(identity["conflicts"], [])
        # The root is where the bytes are, not where the pointer aims: it is
        # this comparison that makes a promote-without-restart visible.
        self.assertEqual(Path(identity["root"]), rev.resolve())
        self.assertEqual(daemon._pointer_state(Path(identity["root"]))["state"],
                         "AGREEMENT")

    def test_a_pointer_moved_without_a_restart_is_drift_not_health(self):
        """`promote` moves `current` and does NOT restart.

        This is the case the whole section is for. The running process is
        unchanged and still healthy; what changed is the release it belongs to.
        Every other answer it gives is now about a node nobody thinks is
        running, so this must read as drift.
        """
        first = self.install_node(label="rev-a@1111111")
        identity = self._installed_identity(first)
        self.assertEqual(daemon._pointer_state(Path(identity["root"]))["state"],
                         "AGREEMENT")

        second = self.install_node(label="rev-b@2222222")

        pointer = daemon._pointer_state(Path(identity["root"]))
        self.assertEqual(pointer["state"], "DRIFT")
        self.assertIs(pointer["agreement"], False)
        self.assertEqual(Path(pointer["current"]), second.resolve())
        # Both names in one sentence, because "there is drift" without "between
        # what and what" is not actionable.
        self.assertIn(first.name, pointer["detail"])
        self.assertIn(second.name, pointer["detail"])
        # The frozen half did NOT move with it. An identity re-read at query
        # time would have followed the pointer and reported agreement.
        self.assertEqual(identity["fields"]["release_id"], "rev-a@1111111")

    def test_an_artifact_and_a_release_record_that_disagree_are_not_a_pass(self):
        """Two records in one revision describing two artifacts.

        Nothing forces these to agree — different steps write them — so when
        they disagree the answer is CONFLICT. Rendering it as whichever record
        was read last is how a machine reports a confident identity for a
        revision that is not internally consistent.
        """
        rev = self.install_node()
        doc = json.loads((rev / "ARTIFACT.json").read_text())
        doc["composition"] = "some-other-composition"
        (rev / "ARTIFACT.json").write_text(json.dumps(doc, indent=2) + "\n")

        identity = self._installed_identity(rev)
        self.assertEqual(identity["agreement"], "CONFLICT")
        self.assertEqual(len(identity["conflicts"]), 1)
        self.assertIn("some-other-composition", identity["conflicts"][0])
        self.assertIn(NODE_COMPOSITION, identity["conflicts"][0])
        # A record that conflicts is still readable, and both spellings are
        # reported rather than one being picked.
        self.assertEqual(identity["fields"]["artifact_version"],
                         json.loads((rev / "ARTIFACT.json").read_text())["version"])

        # ...and the missing half is a DIFFERENT answer, not the same one.
        (rev / "RELEASE.json").unlink()
        gone = self._installed_identity(rev)
        self.assertEqual(gone["agreement"], "UNKNOWN")
        self.assertEqual(gone["conflicts"], [])
        self.assertTrue(any("RELEASE.json" in u for u in gone["unestablished"]))

    def test_a_source_commit_that_cannot_be_proven_is_unknown_not_the_branch(self):
        """The trap this section names: `main@0a98bb6` is a CLAIM.

        A revision label and a README both say which revision the bytes came
        from, and neither is evidence — the label is written at promote time by
        whoever promotes. An artifact with no source record must therefore say
        UNKNOWN, and in particular must not fall back to the branch name, which
        is exactly the string that reads like an answer.
        """
        rev = self.install_node()
        doc = json.loads((rev / "ARTIFACT.json").read_text())
        del doc["source"]
        (rev / "ARTIFACT.json").write_text(json.dumps(doc, indent=2) + "\n")

        identity = self._installed_identity(rev)
        fields = identity["fields"]
        self.assertIsNone(fields["source_commit"])
        self.assertIsNone(fields["source_ref"])
        self.assertIsNone(fields["source_tree_clean"])
        self.assertTrue(any(u.startswith("source commit") for u in identity["unestablished"]),
                        identity["unestablished"])

        lines = cli._identity_lines({"identity": identity})
        source_line = next(l for l in lines if l.startswith("source"))
        self.assertIn("UNKNOWN", source_line)
        # The label is right there in `release_id` and must not leak into the
        # line that answers "which source": `main` is a branch, not a commit.
        self.assertNotIn("main", source_line)
        self.assertNotIn("main", " ".join(identity["unestablished"]))

    def test_after_a_restart_the_running_identity_is_the_current_revision(self):
        """The other half of the drift pair, and the reason it is safe to call
        drift a fault: a restart resolves it, and the identity follows."""
        self.install_node(label="rev-a@1111111")
        second = self.install_node(label="rev-b@2222222")

        restarted = self._installed_identity(second)
        self.assertEqual(restarted["fields"]["release_id"], "rev-b@2222222")
        pointer = daemon._pointer_state(Path(restarted["root"]))
        self.assertEqual(pointer["state"], "AGREEMENT")
        self.assertIs(pointer["agreement"], True)
        self.assertEqual(Path(pointer["current"]), second.resolve())
        self.assertEqual(restarted["agreement"], "AGREEMENT")

    def test_a_rebuild_from_another_checkout_is_agreement_not_drift(self):
        """§J09 — the source record describes the BUILD, not the artifact.

        `expected_artifact` recomputes from the checkout doing the inspecting,
        which is not the checkout that built what is installed: one is a
        worktree, the other canonical. Their HEADs and their uncommitted work
        differ, so an artifact carrying its own source would read as drifted on
        every correctly installed node. The delivery question is "are these the
        bytes canonical builds", and it must survive a different answer to
        "where were they built".
        """
        root = self.base / "drift-probe"
        root.mkdir()
        other = self.base / "other-checkout"
        other.mkdir()

        def artifact(commit: str, dirty: int) -> dict:
            doc = {"schema": "serveros/artifact.v1", "composition": NODE_COMPOSITION,
                   "name": "hwz-server-os-fixture-node", "version": "1.0.0",
                   "members": ["ARTIFACT.json"],
                   "source": {"serveros": {"commit": commit, "ref": "main",
                                           "tree_clean": dirty == 0,
                                           "worktree_changes": dirty}}}
            return doc

        installed = artifact("a" * 40, 5)
        (root / "ARTIFACT.json").write_text(json.dumps(installed, indent=2) + "\n")
        (root / "runtime").mkdir()
        (root / "runtime" / "daemon.py").write_bytes(b"# identical\n")

        expected = {
            Path("ARTIFACT.json"): json.dumps(artifact("b" * 40, 0),
                                              indent=2).encode(),
            Path("runtime/daemon.py"): b"# identical\n",
        }
        self.assertEqual(truth.tree_drift(expected, root), [])

    def test_a_record_without_the_source_key_is_the_same_record(self):
        """§J09 — and this is the case the exclusion missed for one revision.

        A payload built before `ARTIFACT.json` carried `source` is not an older
        VERSION of the artifact; it is the same artifact with one fewer
        attribution key, and the whole point of excluding that key is that its
        presence is not a difference. Normalizing only the side that has it
        compares a record in the checker's serialization against a record in
        whatever serialization wrote it — so the `serveros-testing` plugin
        installed on this node, whose content is identical to what canonical
        builds, was reported as drifted in one path.

        Both orderings are asserted, because the asymmetry had a direction and
        a test that only wrote the newer side first would keep passing if the
        early return came back on the other branch.
        """
        doc = {"schema": "serveros/artifact.v1", "composition": NODE_COMPOSITION,
               "name": "hwz-server-os-fixture-node", "version": "1.0.0",
               "members": ["ARTIFACT.json"]}
        source = {"serveros": {"commit": "a" * 40, "ref": "main",
                               "tree_clean": True, "worktree_changes": 0}}
        for label, with_key, without_key in (
                ("installed predates the key", {**doc, "source": source}, doc),
                ("expected predates the key", doc, {**doc, "source": source})):
            with self.subTest(label):
                root = self.base / f"source-key-{abs(hash(label))}"
                root.mkdir()
                # Written in a serialization neither side would produce on its
                # own, so agreement can only come from the normal form.
                (root / "ARTIFACT.json").write_text(
                    json.dumps(with_key, indent=4, sort_keys=False) + "\n")
                expected = {Path("ARTIFACT.json"):
                            json.dumps(without_key, indent=7).encode()}
                self.assertEqual(truth.tree_drift(expected, root), [])

    def test_but_a_changed_field_of_that_same_record_is_still_drift(self):
        """The exclusion is one key wide, and this is the width.

        A normalizer that quietly accepted the whole file would turn the
        artifact manifest into a file nobody checks. Everything in it except
        `source` is deliverable content and is compared byte for byte.
        """
        root = self.base / "drift-probe-2"
        root.mkdir()
        (root / "ARTIFACT.json").write_text(json.dumps(
            {"schema": "serveros/artifact.v1", "version": "2.0.0",
             "source": {"serveros": {"commit": "a" * 40}}}, indent=2) + "\n")
        expected = {Path("ARTIFACT.json"): json.dumps(
            {"schema": "serveros/artifact.v1", "version": "1.0.0",
             "source": {"serveros": {"commit": "b" * 40}}}, indent=2).encode()}
        self.assertEqual(truth.tree_drift(expected, root),
                         [f"content {truth.ARTIFACT_MANIFEST}"])

        # A file that is not the artifact record is compared whole, source key
        # or not — the exclusion is keyed on the FILE as well as the field.
        (root / "RELEASE.json").write_text(json.dumps({"source": "x"}) + "\n")
        expected[Path("RELEASE.json")] = json.dumps({"source": "y"}).encode()
        self.assertIn(f"content {truth.TREE_MANIFEST}",
                      truth.tree_drift(expected, root))

    def test_the_report_prints_the_artifacts_source_beside_the_label(self):
        """§J09 — the label is a claim; this is what the artifact says.

        Three states that print alike if they are not told apart: a proven
        attribution, an attribution that names a checkout rather than these
        bytes, and none at all. The second is the dangerous one — it looks like
        the first in every respect except the parenthetical.
        """
        for name, source, wanted in (
            ("clean", {"serveros": {"commit": "a" * 40, "tree_clean": True,
                                    "worktree_changes": 0},
                       "ability_management": {"revision": "b" * 40}},
             ["built from aaaaaaaaaaaa", "(clean)", "ability-management@bbbbbbbbbbbb"]),
            ("dirty", {"serveros": {"commit": "a" * 40, "tree_clean": False,
                                    "worktree_changes": 3}},
             ["built from aaaaaaaaaaaa", "3 uncommitted",
              "names the checkout, not these bytes"]),
            ("absent", {}, ["records no source revision"]),
        ):
            root = self.base / f"source-note-{name}"
            root.mkdir()
            doc = {"schema": "serveros/artifact.v1"}
            if source:
                doc["source"] = source
            (root / "ARTIFACT.json").write_text(json.dumps(doc))

            note = truth._source_note(root)
            for fragment in wanted:
                self.assertIn(fragment, note, name)
            # A clean record must not also print the caveat, and vice versa:
            # the parenthetical is the whole difference between them.
            if name == "clean":
                self.assertNotIn("uncommitted", note)

        # No revision to read is not the same finding as a revision with no
        # record, and neither is an empty string that renders as nothing.
        self.assertEqual(truth._source_note(None), "")

    def test_uncommitted_source_work_is_unknown_not_ok(self):
        """A dirty checkout must not be reported as a verified chain.

        This is the provenance caveat that made the fixture's overall verdict
        UNKNOWN, and it is the correct answer: if the source has uncommitted
        work, the installed runtime cannot be attributed to a revision, so
        "installed == canonical" is not a claim anyone can make. Pinned against
        `checkout_state` rather than the real worktree, so the test states the
        rule instead of restating whatever this machine happens to look like.
        """
        repo = self.base / "checkout"
        repo.mkdir()
        self.assertEqual(truth.checkout_state(repo), (False, []))
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        self.assertEqual(truth.checkout_state(repo), (True, []))
        (repo / "wip.py").write_text("# uncommitted\n")
        is_repo, dirty = truth.checkout_state(repo)
        self.assertTrue(is_repo)
        self.assertEqual([line.split()[-1] for line in dirty], ["wip.py"])

    # ---- the case a presence-only check would miss ----------------------

    def test_stale_bytes_under_correct_names_are_detected(self):
        version = self.build_artifact()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        # Rewrite one file in place, keeping every name identical.
        victim = self.out / "context/resolver.py"
        self.assertTrue(victim.is_file(), "fixture expects a context module to tamper with")
        victim.write_text("# stale\n")
        report = truth.collect(self.args())
        self.assertEqual(report["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        built = rows_by(report)[f"{CORE_NAME} @ runtime-assets (built)"]
        self.assertIn("context/resolver.py", built["detail"])

    def test_absent_file_is_named(self):
        self.build_artifact()
        self.install_claude("0", self.out)
        self.install_codex("0", self.out)
        (self.out / "README.md").unlink()
        report = truth.collect(self.args())
        self.assertEqual(report["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        # The row is looked up under `UNKNOWN`, and that is the assertion rather
        # than a detail of it: README.md is the file that declares which
        # composition the artifact is, so an artifact without one has no name
        # to be reported under. The row previously carried core's name here
        # whether or not core was what the artifact claimed — the label was
        # written down instead of read, which is the same defect as reading
        # core's install record for a profile's payload.
        self.assertIn("missing README.md",
                      rows_by(report)[f"{truth.UNKNOWN} @ runtime-assets (built)"]["detail"])

    def test_the_distribution_rows_read_the_record_their_artifact_names(self):
        """§J09 — a profile at `--output` is not core, and core's record is not its.

        The distribution rows used to read core's install record no matter which
        artifact sat at `--output`, because the key was a literal. On this node
        `--output` is a `serveros-linux-work` revision and core is not installed
        at all, so a single substitution produced both halves of one false
        report: `bundle identity` compared the profile's built version against
        an absent record and called it drift, and the Claude-cache hop said
        MISSING for a payload that was present and correct under the profile's
        own plugin name. Measured 2026-09-27.

        The fixture installs BOTH records, at different versions. One record
        would let a reader that asks for the wrong key still come back with
        something, and "it found a record" is not the property being tested —
        naming the subject is.
        """
        self.build_artifact()
        self.build_node_artifact()
        profile = truth.declared_composition(self.node_artifact)
        self.assertEqual(profile, NODE_COMPOSITION)
        profile_name = truth.composition_index(self.build)[profile]["name"]
        self.assertNotEqual(profile_name, CORE_NAME)

        # Core is installed, from a real payload, so that reading it by mistake
        # yields a confident wrong answer rather than an obvious absence.
        self.install_claude("0.0.1-core", self.out, key=CORE_PLUGIN_KEY)
        version = json.loads(
            (self.node_artifact / ".claude-plugin/plugin.json").read_text())["version"]
        cache = self.base / "claude-cache" / profile_name
        shutil.copytree(self.node_artifact, cache)
        self.install_claude(version, cache, key=truth.plugin_key(profile_name))

        rows = rows_by(truth.collect(self.args(output=self.node_artifact)))

        # The subject is read, not written down: the row is named for the
        # composition the artifact declares, and core's row does not exist.
        self.assertNotIn(f"{CORE_NAME} (bundle identity)", rows)
        identity = rows[f"{profile_name} (bundle identity)"]
        self.assertEqual(identity["installed"], version)
        self.assertEqual(identity["verdict"], truth.OK, identity["detail"])

        # The hop that was MISSING. Not `OK` asserted, because whether the copy
        # equals canonical is a different question and one this test does not
        # set up; what it asserts is that the reader FOUND the payload instead
        # of reporting a present directory as absent.
        cached = rows[f"{profile_name} @ Claude plugin cache"]
        self.assertNotEqual(cached["verdict"], truth.MISSING, cached["detail"])
        self.assertNotIn("not present", cached["detail"])
        self.assertTrue(cached["installed"].startswith("sha256="), cached["installed"])

    def test_a_missing_runtime_is_missing_not_ok(self):
        """Codex says enabled; the cache it names holds nothing.

        The row is driven by the config's own claim rather than by the cache
        directory existing, which is what makes the difference visible: a
        checker that only looked for a cache would report nothing at all here,
        and "nothing at all" reads as "fine".
        """
        version = self.build_artifact()
        self.install_claude("0", self.out)
        self.install_codex(version, self.out)
        report = truth.collect(self.args(codex_cache=self.base / "nowhere"))
        row = rows_by(report)[f"{truth.BUNDLE_NAME}@{truth.CODEX_MARKETPLACE} "
                              "(Codex plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.MISSING)

    # ---- installed engine drift ----------------------------------------

    def test_installed_engine_diverging_from_canonical_is_drift(self):
        self.build_artifact()
        self.install_claude("0", self.out)
        self.install_codex("0", self.out)
        (self.engine_repo / "workengine" / "added_later.py").write_text("# new\n")
        report = truth.collect(self.args())
        row = rows_by(report)["workengine (installed)"]
        self.assertEqual(row["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("added_later.py", row["detail"])

    def test_an_undeclared_control_plane_is_not_a_missing_one(self):
        """§J09 — the work-engine control plane is a separate distribution.

        This row used to derive its path from the profile and report MISSING when
        nothing was there, which put it in the chain and blocked
        RELEASE_COMPLETE. On this node nothing has ever been at that path — it is
        the Beijing layout — and nothing should be: `fleet/state-root-contract.yaml`
        names two programs called `serverosctl` and marks the control plane's
        `boards` namespace `required: false` / `absent_means: NO_BOARDS`, while
        `ARCHITECTURE.md` says two repositories must not compete for the same
        installed executable. Measured 2026-09-27: `envs/python/` held only
        `serveros-test`, no `workengine` package existed anywhere, and the row was
        the sole remaining blocker.

        Undeclared must not be silently equivalent to absent, so the pair is
        asserted: this case leaves the chain, and the next one stays in it.
        """
        # Clean, because `blocking == []` is the assertion and it would hold for
        # the wrong reason on a fixture with other drift in it. The pair below
        # then differs in exactly one thing — the declaration.
        self.build_and_install()
        report = truth.collect(self.args(engine_installed=None))
        row = rows_by(report)["workengine (installed)"]
        self.assertFalse(row["applies"])
        self.assertNotIn(row["verdict"], (truth.OK, truth.MISSING))
        self.assertIn("no control-plane installation is declared", row["detail"])
        self.assertIn("workengine (installed)", report["release"]["not_applicable"])
        # The whole point: an undeclared control plane is not a blocking absence.
        self.assertEqual(report["release"]["blocking"], [])

    def test_a_declared_but_absent_control_plane_still_blocks(self):
        """The other half of the pair, and the reason `applies` is not a reading.

        If applicability followed the directory rather than the declaration, this
        row would go quiet exactly when it had something to say — declare the
        control plane, lose it, and the chain would report nothing. Declared and
        absent is the case worth catching, and it is the one that must stay in
        the chain.
        """
        self.build_and_install()
        report = truth.collect(self.args(engine_installed=self.base / "gone"))
        row = rows_by(report)["workengine (installed)"]
        self.assertTrue(row["applies"])
        self.assertEqual(row["verdict"], truth.MISSING)
        self.assertIn("declared but no installed package", row["detail"])
        self.assertNotIn("workengine (installed)", report["release"]["not_applicable"])
        self.assertEqual(report["release"]["blocking"], [truth.AXIS_CHAIN])
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_INCOMPLETE)

    # ---- the defaults argv decides --------------------------------------

    def main_argv(self) -> list[str]:
        """This inspector's own command line, with `--output` left off.

        Every other root is passed explicitly, because the subject of this test
        is the one argument whose default is a RULE rather than a constant, and
        a rule tested by writing its result out by hand tests the hand.
        """
        return [
            "--json",
            "--serveros-repo", str(self.serveros),
            "--ability-repo", str(self.ability),
            "--engine-repo", str(self.engine_repo),
            "--marketplace-path", str(self.marketplace),
            "--runtime-root", str(self.runtime_root),
            "--composition", NODE_COMPOSITION,
            "--canonical-ref", truth.DEFAULT_CANONICAL_REF,
            "--claude-plugins-json", str(self.claude_json),
            "--claude-settings-json", str(self.claude_settings),
            "--codex-config", str(self.codex_config),
            "--codex-cache", str(self.codex_cache),
            "--claude-skills-root", str(self.claude_skills),
            "--codex-skills-root", str(self.codex_skills),
            "--unit-dir", str(self.unit_dir),
            "--proc-root", str(self.proc_dir),
        ]

    def run_main(self) -> tuple[int, dict]:
        """`main()` as a process would run it, with its report parsed back out.

        `main` prints, so stdout is captured — and located rather than assumed,
        because the builder prints its own progress into the same stream.
        """
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["runtime_truth.py", *self.main_argv()]), \
                mock.patch("sys.stdout", out):
            rc = truth.main()
        text = out.getvalue()
        start = text.index('{\n  "schema": "serveros/runtime-truth.v3"')
        return rc, json.loads(text[start:])

    def test_the_output_default_is_the_nodes_own_composition(self):
        """`--output` is where THIS node's composition lives, and argv says which.

        Its default used to be `<runtime-root>/current` — core's position under
        the single-composition layout this node left on 2026-09-23 — so the
        "built" hop was measured against a directory that no release writes and
        no marketplace names. On the real node that read as 64 drifted paths and
        a version two releases old, reported as though the node were stale, while
        the artifact it actually built, installed and ran was never looked at.

        Exit code carries the assertion as well as the row does: with the old
        default the derived subject is core, whose record this fixture does not
        install, so the report drifts and `main` returns 1.
        """
        self.build_artifact()
        self.build_and_install()
        self.install_node()
        node_name = truth.composition_index(self.build)[NODE_COMPOSITION]["name"]
        self.assertNotEqual(node_name, CORE_NAME)
        current = self.runtime_root / NODE_COMPOSITION / "current"
        node_version = json.loads(
            (self.node_artifact / ".claude-plugin/plugin.json").read_text())["version"]
        self.install_claude(node_version, current, key=truth.plugin_key(node_name))
        # A node that is installed and not running is not the state this test is
        # about, and RELEASE_COMPLETE says so: the unit row is part of it. The
        # process is synthetic for the same reason the unit file is — the
        # fixture owns `/proc` rather than reading the host's.
        self.run_process([str(current / "bin/serverosd")], started_at=2_000_000.0)

        rc, report = self.run_main()

        rows = rows_by(report)
        self.assertEqual(
            rc, 0, [(r["component"], r["verdict"], r["detail"])
                    for r in report["rows"]
                    if r["verdict"] != truth.OK and r.get("applies", True)])
        self.assertIn(f"{node_name} @ runtime-assets (built)", rows)
        self.assertNotIn(f"{CORE_NAME} @ runtime-assets (built)", rows)
        self.assertEqual(rows[f"{node_name} (bundle identity)"]["verdict"], truth.OK)
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)

    # ---- primitives -----------------------------------------------------

    def test_tree_hash_ignores_mtime_but_not_content_or_path(self):
        a = self.base / "a"
        (a / "sub").mkdir(parents=True)
        (a / "sub/one.py").write_text("x")
        first = truth.tree_hash(a)
        (a / "sub/one.py").write_text("x")          # same bytes, new mtime
        self.assertEqual(truth.tree_hash(a), first)
        (a / "sub/one.py").write_text("y")          # same path, new bytes
        self.assertNotEqual(truth.tree_hash(a), first)

    def test_tree_hash_ignores_bytecode_caches(self):
        a = self.base / "b"
        a.mkdir()
        (a / "keep.py").write_text("x")
        clean = truth.tree_hash(a)
        (a / "__pycache__").mkdir()
        (a / "__pycache__/keep.cpython-312.pyc").write_bytes(b"\x00")
        self.assertEqual(truth.tree_hash(a), clean)

    def test_tree_drift_separates_missing_unexpected_and_content(self):
        expected = {Path("a.py"): b"1", Path("b.py"): b"2"}
        root = self.base / "t"
        root.mkdir()
        (root / "a.py").write_text("changed")
        (root / "c.py").write_text("extra")
        drift = truth.tree_drift(expected, root)
        self.assertIn("content a.py", drift)
        self.assertIn("missing b.py", drift)
        self.assertIn("unexpected c.py", drift)

    # ---- the install records -------------------------------------------

    def test_claude_install_record_is_read(self):
        self.install_claude("9.9.9", self.out)
        got = truth.read_claude_install(self.claude_json, CORE_NAME)
        self.assertEqual(got["version"], "9.9.9")
        self.assertEqual(got["installPath"], str(self.out))

    def test_missing_claude_record_is_empty_not_an_exception(self):
        self.assertEqual(truth.read_claude_install(self.base / "nope.json", CORE_NAME), {})

    def test_the_record_read_is_the_one_the_plugin_name_names(self):
        """The reader's subject is its argument, and it has no default.

        Two records, two compositions, and the answer has to be the asked-for
        one — which is only checkable because the parameter exists. The absence
        of a default is asserted too: a caller that never names a subject cannot
        be given core's record and report it as somebody else's, because there
        is no call that omits the name.
        """
        other = truth.plugin_key("hwz-server-os-linux-work")
        self.install_claude("9.9.9", self.out, key=CORE_PLUGIN_KEY)
        self.install_claude("1.1.1", self.base / "other", key=other)
        self.assertEqual(
            truth.read_claude_install(self.claude_json, CORE_NAME)["version"], "9.9.9")
        self.assertEqual(
            truth.read_claude_install(self.claude_json, "hwz-server-os-linux-work")["version"],
            "1.1.1")
        with self.assertRaises(TypeError):
            truth.read_claude_install(self.claude_json)  # type: ignore[call-arg]

    def test_codex_config_activation_is_read(self):
        # `install_codex` mirrors a payload, and this test is about the config
        # parse. An empty payload dir is enough — building the artifact here
        # would only make the test slower without touching what it asserts.
        self.out.mkdir(parents=True)
        self.install_codex("1.0.0", self.out)
        got = truth.read_codex_install(self.codex_config)
        self.assertTrue(got["enabled"])
        self.assertEqual(got["marketplace_source"], "/somewhere")

    def test_codex_config_without_the_plugin_is_not_enabled(self):
        self.codex_config.write_text("[plugins.\"other@x\"]\nenabled = true\n")
        self.assertFalse(truth.read_codex_install(self.codex_config)["enabled"])

    def test_the_codex_reader_answers_about_the_composition_it_was_asked(self):
        """The defect this node was in on 2026-09-23, as a fixed input.

        The reader took core's plugin name and marketplace as constants, so a
        node whose Codex activates `hwz-server-os-linux-work` was reported as
        `enabled: false` — a false absence, read off the right key of the wrong
        composition. Both directions are asserted, because a reader that
        answered `true` for everything would pass the first half alone.
        """
        self.codex_config.write_text(
            '[marketplaces.hwz-server-os-linux-work-local]\n'
            'source = "/somewhere"\n\n'
            '[plugins."hwz-server-os-linux-work@hwz-server-os-linux-work-local"]\n'
            'enabled = true\n')
        core = truth.read_codex_install(self.codex_config, "hwz-server-os")
        work = truth.read_codex_install(self.codex_config, "hwz-server-os-linux-work")
        self.assertFalse(core["enabled"])
        self.assertTrue(work["enabled"])
        self.assertEqual(work["marketplace"], "hwz-server-os-linux-work-local")

    def test_a_codex_pair_that_is_not_the_rule_is_not_enabled(self):
        """Half a match is not a match. The pair is the subject.

        `codex_marketplace_name` derives the marketplace from the plugin name,
        and an enabled line whose marketplace is the OTHER composition's is not
        this composition's activation — it is a name that no rule produces. A
        reader that matched on the plugin name alone would call this enabled,
        which is the same conflation in the other direction.
        """
        self.codex_config.write_text(
            '[plugins."hwz-server-os@hwz-server-os-linux-work-local"]\n'
            'enabled = true\n')
        got = truth.read_codex_install(self.codex_config, "hwz-server-os")
        self.assertFalse(got["enabled"])

    # ---- the plugin a session actually loads -----------------------------

    def test_a_disabled_plugin_is_named_disabled_rather_than_left_out(self):
        """The state a migration leaves behind, and the one presence checks miss.

        Measured on this node 2026-09-23: the old core composition stayed in
        `installed_plugins.json` after the default entry moved to the
        `serveros-linux-work` profile. It is installed and in no session, and
        the install record cannot say so — it has no enabled field. Reporting it
        as simply "installed" is how a report ends up describing a plugin that
        nothing loads.
        """
        version = self.build_and_install()
        self.install_claude(version, self.out, enabled=False)
        report = truth.collect(self.args())
        label = f"{CORE_PLUGIN_KEY} (Claude plugin, disabled)"
        self.assertIn(label, rows_by(report))

    def test_the_composition_a_session_loads_is_recomputed_not_trusted(self):
        """The new row is a measurement, not a restatement of the version string."""
        version = self.build_and_install()
        self.install_claude(version, self.out)
        rows = rows_by(truth.collect(self.args()))
        row = rows[f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.OK)

        # A payload that has the right version and the wrong bytes. The version
        # string is untouched, so a row that read it would still say OK.
        (self.out / "skills/runtime-management/SKILL.md").write_text("tampered\n")
        rows = rows_by(truth.collect(self.args()))
        row = rows[f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("SKILL.md", row["detail"])

    def test_an_enabled_plugin_nobody_can_find_is_missing_not_quiet(self):
        self.claude_settings.write_text(json.dumps(
            {"enabledPlugins": {CORE_PLUGIN_KEY: True}}))
        rows = rows_by(truth.collect(self.args()))
        row = rows[f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.MISSING)
        self.assertIn("absent from the install record", row["detail"])

    def test_a_plugin_is_measured_at_the_pin_it_declares_not_the_nodes(self):
        """The row's own question, which is not "the same generation as the node".

        Measured on this node 2026-10-08: the installed `serveros-testing` cache
        declares ability pin `da5f89b`, the node payload declares `c3fa072`, and
        between those two commits ability-management's skill payloads gained
        sixteen files. Recomputing the plugin from the NODE's pin named all
        sixteen as drift — plus `ARTIFACT.json` and `README.md`, which print the
        pin they were built from — and recomputing the same bytes from the pin
        the payload itself declares reproduced every one of them. Those sixteen
        were never missing from the plugin; they are the other generation's
        files, and a reader shown them as `missing` is being told a fact about
        the node's pin as though it were a fact about this payload.

        The two pins are still worth naming, because a plugin a generation behind
        the node payload IS how this machine differs from a released fleet — so
        that is said in the row's note rather than folded into the path list.
        """
        stale_root = self.base / "dist-stale"
        stale = stale_root / "current"
        stale_marketplace = stale_root / ".agents/plugins/marketplace.json"
        rc = self.build.build(self.serveros / "plugin/bundle.yaml", self.ability,
                              self.rev, stale, stale_marketplace, write=True,
                              pack_contract_path=self.contract)
        self.assertEqual(rc, 0)
        stale_version = json.loads(
            (stale / ".claude-plugin/plugin.json").read_text())["version"]

        # A later revision whose skill payload carries one file this one has not.
        later = self.ability / "skills/runtime-management/payload/references"
        later.mkdir(parents=True)
        added = later / "01-later-generation.md"
        added.write_text("a file the installed plugin has never had\n")
        git(self.ability, "add", str(added))
        git(self.ability, "commit", "--quiet", "-m", "a later ability generation")
        newer = git(self.ability, "rev-parse", "HEAD")

        # The NODE payload moves to the later pin. The plugin does not.
        rc = self.build.build(self.serveros / "plugin/bundle.yaml", self.ability,
                              newer, self.out, self.marketplace, write=True,
                              pack_contract_path=self.contract_at(newer))
        self.assertEqual(rc, 0)
        self.install_claude(stale_version, stale, rev=self.rev)

        # No `ability_rev`: the report-wide pin comes from `--output`, which is
        # what makes this the case where the two pins disagree.
        report = truth.collect(self.args(ability_rev=""))
        row = rows_by(report)[f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.OK, row["detail"])
        self.assertNotIn("01-later-generation.md", row["detail"])
        self.assertIn(self.rev[:12], row["note"])
        self.assertIn(newer[:12], row["note"])

    def test_a_payload_that_declares_no_pin_is_not_measured_against_the_nodes(self):
        """The other half of the same rule: with no pin of its own, there is no
        source of its own to be checked against, and borrowing the node's would
        re-create exactly the substitution the row above removes."""
        version = self.build_and_install()
        payload = self.out
        readme = payload / "README.md"
        kept = [line for line in readme.read_text().splitlines(keepends=True)
                if not truth.PIN_PATTERN.search(line)]
        readme.write_text("".join(kept))

        report = truth.collect(self.args())
        row = rows_by(report)[f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(version, json.loads(
            (payload / ".claude-plugin/plugin.json").read_text())["version"])
        self.assertEqual(row["verdict"], truth.UNKNOWN)
        self.assertIn("declares no ability pin", row["detail"])

    def test_the_marketplace_name_mirrors_the_builders_own_rule(self):
        """`<name>-local` is `build.codex_marketplace()`'s rule, restated here.

        Restated rather than imported for the same reason the root resolver is:
        the two files ship from different roots. A restatement that nothing
        compares is how `CLAUDE_PLUGIN_KEY` became a literal that named a
        disabled plugin, so this one is compared against the builder's own
        output for every composition the repository declares.
        """
        for path in truth.composition_files(build).values():
            # Through `load_composition`, because a profile's raw YAML is not a
            # bundle: `linux-work.yaml` declares no `category` and inherits it
            # from the file it extends. Reading the raw mapping asks the builder
            # to render a profile that has not been resolved, and the
            # KeyError it raises is the builder refusing a malformed input
            # rather than a disagreement about the naming rule.
            document = build.load_composition(path)
            rendered = build.codex_marketplace(document, self.out, self.marketplace)
            self.assertEqual(rendered["name"],
                             truth.codex_marketplace_name(document["name"]),
                             f"{path.name} disagrees with the mirrored rule")

    def test_a_codex_plugin_disabled_in_the_config_is_named_disabled(self):
        version = self.build_and_install()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        config = self.codex_config.read_text()
        self.assertIn("enabled = true", config)
        self.codex_config.write_text(config.replace("enabled = true", "enabled = false"))
        rows = rows_by(truth.collect(self.args()))
        label = f"{truth.BUNDLE_NAME}@{truth.CODEX_MARKETPLACE} (Codex plugin, disabled)"
        self.assertIn(label, rows)
        self.assertEqual(rows[label]["verdict"], truth.OK)

    def test_the_codex_summary_is_keyed_by_composition_not_by_runtime(self):
        """The block used to be one answer about core, under the key `codex`.

        A reader of the JSON could not tell which composition `enabled` was
        about, and on this node that turned into a false absence. Every
        declared composition is now asked separately and each entry names its
        own plugin and marketplace, so "Codex is off" is not something this
        block can be read as saying.
        """
        version = self.build_and_install()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        report = truth.collect(self.args())
        # Resolved against the FIXTURE's repository, not the one this test file
        # lives in: `composition_index` takes the repository from the module it
        # is handed, and the module imported here resolves to the real checkout.
        # Asked that way the assertion compares the report against a different
        # repository's compositions and passes or fails for a reason unrelated
        # to what it claims to test.
        declared = set(truth.composition_index(
            SimpleNamespace(repo_root=lambda: self.serveros)))
        self.assertTrue(declared)
        self.assertEqual(set(report["codex"]), declared)
        for composition, entry in report["codex"].items():
            self.assertEqual(entry["marketplace"],
                             truth.codex_marketplace_name(entry["plugin"]),
                             f"{composition} names a marketplace no rule produces")
        self.assertTrue(report["codex"]["serveros-core"]["enabled"])
        self.assertFalse(report["codex"][NODE_COMPOSITION]["enabled"],
                         "the fixture enables core only; the node composition is not on")

    def test_the_codex_row_is_recomputed_not_read_from_the_cache_name(self):
        """The version dir's NAME is the payload's own version inside a payload.

        A row that trusted the directory name would agree with a cache whose
        contents had been replaced, which is the failure the Claude row's
        twin test pins. Same assertion, other runtime.
        """
        version = self.build_and_install()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        label = f"{truth.BUNDLE_NAME}@{truth.CODEX_MARKETPLACE} (Codex plugin, enabled)"
        self.assertEqual(rows_by(truth.collect(self.args()))[label]["verdict"], truth.OK)

        leaf = self.codex_cache / truth.CODEX_MARKETPLACE / truth.BUNDLE_NAME / version
        (leaf / "skills/runtime-management/SKILL.md").write_text("tampered\n")
        row = rows_by(truth.collect(self.args()))[label]
        self.assertEqual(row["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("SKILL.md", row["detail"])

    def test_another_products_plugin_is_not_this_checkers_subject(self):
        """The subject is derived, so a foreign plugin is skipped by the rule.

        A hardcoded list of names to ignore would be a second place the product
        is described, and it would be wrong the moment the product gained a
        composition. The rule is: this checker audits a plugin exactly when the
        plugin's name is one of the compositions THIS repository declares.
        """
        version = self.build_and_install()
        self.install_claude(version, self.out)
        self.install_claude("0.11.0", self.base / "quay-payload",
                            key="quay@quay", enabled=True)
        report = truth.collect(self.args())
        self.assertEqual([n for n in rows_by(report) if n.startswith("quay")], [])

    # ---- the two documented path traps ----------------------------------

    def test_codex_leaf_is_resolved_one_level_deeper_than_the_plugin_dir(self):
        """Codex nests <cache>/<marketplace>/<plugin>/<version>/; Claude does not.

        Both levels were gotten wrong once while writing the inspector — first a
        false MISSING, then a false drift. This test pins the shape so the next
        edit cannot quietly regress it.
        """
        version = self.build_artifact()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        self.assertTrue(
            (self.codex_cache / truth.CODEX_MARKETPLACE / truth.BUNDLE_NAME / version).is_dir())
        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.BUNDLE_NAME}@{truth.CODEX_MARKETPLACE} "
                              "(Codex plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.OK)
        # The row now reports the version the LEAF declares beside the hash of
        # its bytes — the same two fields the Claude row carries. Before, the
        # Codex measurement was a distribution-level row that named only a
        # hash, so a reader could not tell which version had been hashed. This
        # pins both halves: a leaf resolved to the wrong depth would report a
        # different version string even if it happened to hash to something.
        self.assertEqual(row["installed"],
                         f"version={version}  sha256={truth.tree_hash(self.out)}")


    # ---- release axes: the chain can be perfect and the release still wrong

    def faithful_runtime(self) -> str:
        version = self.build_artifact()
        self.install_claude(version, self.out)
        self.install_codex(version, self.out)
        return version

    def axis(self, report, name):
        return report["release"]["axes"][name]

    def test_a_faithful_runtime_is_release_complete(self):
        self.faithful_runtime()
        report = truth.collect(self.args())
        axes = {n: self.axis(report, n)["verdict"] for n in truth.AXES}
        self.assertEqual(axes, {n: truth.PASS for n in truth.AXES})
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)
        self.assertEqual(report["release"]["blocking"], [])

    def test_chain_can_be_consistent_while_provenance_fails(self):
        """The failure this axis exists for.

        The artifact is built from, installed from, and byte-identical to a
        revision on a branch main does not contain. Every byte agrees, so the
        chain verdict stays OK — and the release is still not reproducible,
        because nothing canonical contains the revision it came from.
        """
        sha = self.side_branch_commit()
        self.build_and_install(sha)
        report = truth.collect(self.args(ability_rev=sha))
        self.assertEqual(report["verdict"], truth.OK)          # chain: fine
        provenance = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(provenance["verdict"], truth.FAIL)
        self.assertEqual(provenance["drift"], truth.PIN_NOT_CANONICAL)
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_INCOMPLETE)
        self.assertEqual(report["release"]["blocking"], [truth.AXIS_PROVENANCE])

    def test_a_canonical_record_that_lags_is_not_a_defect(self):
        """The axis reads the PUBLISHED branch, not this clone's copy of it.

        `refs/heads/main` in a shared canonical checkout is a working copy of
        the canonical branch, and it lags until somebody fetches in that
        checkout — which, on this node, is the checkout the policy says not to
        disturb. Resolving that name therefore measured a stale record and
        reported `PIN_NOT_CANONICAL` against a pin that is the published head
        exactly: a false alarm on a correct release, and one whose apparent
        remedy was to pull the one checkout nobody should be pulling.

        Here the published record contains the pin and the local branch does
        not. The release is reproducible from something that will still exist,
        so the axis passes and says which record answered.
        """
        self.faithful_runtime()
        git(self.ability, "update-ref", "refs/remotes/origin/main", self.rev)
        git(self.ability, "update-ref", "refs/heads/main", f"{self.rev}~1")
        report = truth.collect(self.args())
        provenance = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(provenance["verdict"], truth.PASS,
                         provenance["detail"])
        self.assertEqual(provenance["ref_used"], "refs/remotes/origin/main")
        self.assertNotIn(truth.AXIS_PROVENANCE, report["release"]["blocking"])

    def test_a_commit_only_this_clone_has_is_not_canonical(self):
        """The counterexample that keeps the rule above from being a loosening.

        "Prefer the published record" would be a way of passing everything if
        it meant "either record". It does not: a revision on the local branch
        and on no published one is a revision that exists on this machine and
        nowhere that will still be there, which is the question the axis asks.
        """
        self.faithful_runtime()
        git(self.ability, "update-ref", "refs/remotes/origin/main", f"{self.rev}~1")
        report = truth.collect(self.args())
        provenance = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(provenance["verdict"], truth.FAIL)
        self.assertEqual(provenance["drift"], truth.PIN_NOT_CANONICAL)
        self.assertEqual(report["release"]["blocking"], [truth.AXIS_PROVENANCE])

    def test_an_unreachable_pin_is_named_in_the_row_too(self):
        self.faithful_runtime()
        report = truth.collect(self.args(ability_rev=self.side_branch_commit()))
        row = rows_by(report)["ability-management (build pin)"]
        self.assertEqual(row["verdict"], truth.PIN_NOT_CANONICAL)
        self.assertEqual(row["axis"], truth.AXIS_PROVENANCE)

    def test_a_nonexistent_pin_is_not_canonical_not_unknown(self):
        self.faithful_runtime()
        report = truth.collect(self.args(ability_rev="0" * 40))
        axis = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(axis["verdict"], truth.FAIL)
        self.assertEqual(axis["drift"], truth.PIN_NOT_CANONICAL)

    def test_the_pin_comes_from_the_artifact_not_a_stale_install_record(self):
        """Claude's gitCommitSha is carried forward when the marketplace source
        is a local path, so it can name a revision the installed bytes never
        came from. Trusting it made the pin row accuse a correct artifact."""
        self.build_and_install()
        # Rewrite the installed bytes from a different revision WITHOUT
        # touching the install record — which is what `claude plugin update`
        # does when the marketplace source is a local path.
        sha = self.side_branch_commit()
        rc = self.build.build(self.serveros / "plugin/bundle.yaml", self.ability, sha,
                         self.out, self.marketplace, write=True,
                         pack_contract_path=self.contract_at(sha))
        self.assertEqual(rc, 0)
        version = json.loads((self.out / ".claude-plugin/plugin.json").read_text())["version"]
        self.install_codex(version, self.out)
        report = truth.collect(self.args(ability_rev=""))
        pin = report["release"]["pin"]
        self.assertEqual(pin["declared"], sha)
        self.assertFalse(pin["record_agrees"])
        self.assertEqual(pin["used"], sha)
        self.assertEqual(report["verdict"], truth.OK)   # bytes agree with sha
        row = rows_by(report)["ability-management (build pin)"]
        self.assertIn("record is stale", row["note"])

    def test_an_explicit_pin_overrides_the_artifact_declaration(self):
        """The flag stays authoritative — it is how a release is asked about a
        revision other than the one currently installed."""
        self.build_and_install()
        report = truth.collect(self.args(ability_rev=self.rev))
        self.assertEqual(report["release"]["pin"]["used"], self.rev)

    def test_a_pin_behind_canonical_head_is_not_a_defect(self):
        """Pinning an older revision is a legitimate release.

        Flagging this would make the check fire on a correct machine, and a
        check that fires on correct machines gets ignored by the third time it
        does it. This is the distinction that split PIN_NOT_CANONICAL away from
        the PIN_SUPERSEDED name.

        The assertion that the payload comparison actually RAN is the point of
        the second half. `pin_superseded` is tri-state, and a fixture whose
        registry the comparison cannot read makes every case return None — the
        test would still see PASS and still see "behind", while the detector it
        names was never entered.
        """
        self.faithful_runtime()
        git(self.ability, "commit", "--quiet", "--allow-empty", "-m", "later work")
        report = truth.collect(self.args())     # pin = the revision before it
        axis = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(axis["verdict"], truth.PASS)
        self.assertEqual(axis["drift"], "")
        self.assertEqual(axis["state"], truth.PIN_STATE_HISTORICAL)
        self.assertIn("behind", axis["detail"])
        self.assertNotIn("could not be compared", axis["detail"],
                         "the payload comparison must have run, not fallen through")

    def test_a_superseded_pin_is_reported_and_does_not_block(self):
        """The release is real, reproducible, and no longer the one to ship.

        This is the failure that produced this axis in the first place, and it
        is NOT the same as an unreachable pin: every byte agrees, the revision
        exists on canonical, and a rebuild from it reproduces the artifact. What
        is wrong is only that canonical has published a newer revision of the
        ability since. Failing the axis for this would make a correct machine
        report RELEASE_INCOMPLETE, which is how this check would get ignored.
        """
        self.faithful_runtime()
        pinned = self.rev
        # A real change to the ability's payload, the way a released revision
        # changes: content, not an empty commit.
        payload = self.ability / "skills/ability-management/payload/SKILL.md"
        payload.write_text(payload.read_text() + "\nnew instruction\n")
        git(self.ability, "add", "-A")
        git(self.ability, "commit", "--quiet", "-m", "publish a newer revision")
        self.assertTrue(truth.pin_superseded(self.ability, pinned,
                                             git(self.ability, "rev-parse", "HEAD")),
                        "fixture must actually produce a superseded pin")
        report = truth.collect(self.args(ability_rev=pinned))
        axis = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(axis["state"], truth.PIN_STATE_SUPERSEDED)
        self.assertEqual(axis["drift"], truth.PIN_SUPERSEDED)
        self.assertEqual(axis["verdict"], truth.PASS, "a superseded pin is not a byte-level lie")
        self.assertEqual(report["release"]["blocking"], [])
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)
        self.assertEqual(report["release"]["pin"]["state"], truth.PIN_STATE_SUPERSEDED)

    def test_a_pin_whose_payload_is_unchanged_is_current_content(self):
        """Older commit, same bytes => the same revision. Not superseded."""
        self.faithful_runtime()
        git(self.ability, "commit", "--quiet", "--allow-empty", "-m", "unrelated")
        head = git(self.ability, "rev-parse", "HEAD")
        self.assertIs(truth.pin_superseded(self.ability, self.rev, head), False)

    def test_an_unrecorded_pin_is_missing_not_not_canonical(self):
        """`nothing recorded` and `recorded wrong` are different repairs.

        Both used to come out as PIN_NOT_CANONICAL, which says the artifact
        disagrees with canonical — but an artifact that declares no revision at
        all is not disagreeing with anything; it is declining to say.
        """
        self.faithful_runtime()
        (self.out / "README.md").write_text("# built artifact\n")   # no pin line
        # The install record is a second, independent source for the same
        # claim, so it has to be silenced too — otherwise the effective pin
        # comes from there and this tests nothing about a missing declaration.
        doc = json.loads(self.claude_json.read_text())
        doc["plugins"][CORE_PLUGIN_KEY][0].pop("gitCommitSha", None)
        self.claude_json.write_text(json.dumps(doc))
        report = truth.collect(self.args(ability_rev=""))
        pin = report["release"]["pin"]
        self.assertEqual(pin["used"], "", "precondition: nothing anywhere declares a revision")
        axis = self.axis(report, truth.AXIS_PROVENANCE)
        self.assertEqual(axis["drift"], truth.PIN_MISSING)
        self.assertEqual(axis["state"], truth.PIN_STATE_MISSING)
        self.assertEqual(axis["verdict"], truth.FAIL, "an unverifiable pin is not a verified one")

    # ---- materialization uniqueness -------------------------------------

    def test_flattening_a_member_fails_materialization_uniqueness(self):
        self.faithful_runtime()
        members = sorted(p.name for p in (self.out / "skills").iterdir())
        self.flatten(self.claude_skills, members[0])
        report = truth.collect(self.args())
        axis = self.axis(report, truth.AXIS_MATERIALIZATION)
        self.assertEqual(axis["verdict"], truth.FAIL)
        materialization = report["release"]["materialization"]
        self.assertEqual([d["member"] for d in materialization["duplicates"]], [members[0]])
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_INCOMPLETE)
        self.assertEqual(report["release"]["blocking"], [truth.AXIS_MATERIALIZATION])

    def test_a_different_skill_of_the_same_name_is_a_collision_not_a_duplicate(self):
        """Two artifacts sharing one name is worth naming, but it is not this
        contract's violation — nothing was duplicated.

        Without this split the axis is either permanently red on the deferred
        feishu-report item, or someone exempts that item by hardcoding its
        name. Both turn a real check into a decoration.
        """
        self.faithful_runtime()
        members = sorted(p.name for p in (self.out / "skills").iterdir())
        flat = self.flatten(self.claude_skills, members[0])
        (flat / "SKILL.md").write_text("---\nname: local\n---\n\nlocal revision\n")
        report = truth.collect(self.args())
        materialization = report["release"]["materialization"]
        self.assertEqual(materialization["duplicates"], [])
        self.assertEqual([c["member"] for c in materialization["collisions"]], [members[0]])
        self.assertEqual(self.axis(report, truth.AXIS_MATERIALIZATION)["verdict"], truth.PASS)
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)

    def test_an_empty_runtime_root_is_not_a_materialization(self):
        """A member reaching a runtime only through the plugin is exactly one
        materialization, and the absent runtime root must not read as drift."""
        self.faithful_runtime()
        report = truth.collect(self.args())
        materialization = report["release"]["materialization"]
        self.assertTrue(materialization["members"])
        self.assertEqual(self.axis(report, truth.AXIS_MATERIALIZATION)["verdict"], truth.PASS)

    # ---- the axes are or-ed into one verdict, not averaged --------------

    def test_any_failing_axis_blocks_the_release(self):
        sha = self.side_branch_commit()
        self.build_and_install(sha)
        members = sorted(p.name for p in (self.out / "skills").iterdir())
        self.flatten(self.claude_skills, members[0])
        report = truth.collect(self.args(ability_rev=sha))
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_INCOMPLETE)
        self.assertEqual(set(report["release"]["blocking"]),
                         {truth.AXIS_PROVENANCE, truth.AXIS_MATERIALIZATION})

    # ---- the running surface (§AI) --------------------------------------
    #
    # Until 2026-09-15 none of this was measured. The file verified a
    # distribution and called it the runtime; the node runs a different
    # composition from a different path, under a unit the file never opened, in
    # a process it never looked for. These tests are the measurement.

    def test_a_machine_with_no_node_is_reported_and_not_blocking(self):
        """The distribution can be perfect on a machine that has no node runtime.

        A release tool that cannot say RELEASE_COMPLETE on a machine with no
        node installed is a tool nobody runs, so absence is excluded — and the
        exclusion is NAMED in the report rather than being an empty row, because
        a hole in a gate that nobody can see is how a gate stops being one.
        """
        self.faithful_runtime()
        report = truth.collect(self.args())
        rows = rows_by(report)
        self.assertEqual(rows[f"{truth.NODE_UNIT} (running unit)"]["verdict"], truth.UNKNOWN)
        self.assertEqual(rows[f"{NODE_COMPOSITION} (node revision)"]["verdict"],
                         truth.MISSING)
        self.assertIn(f"{truth.NODE_UNIT} (running unit)", report["release"]["not_applicable"])
        self.assertIn(f"{NODE_COMPOSITION} (node revision)",
                      report["release"]["not_applicable"])
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)
        self.assertEqual(self.axis(report, truth.AXIS_CHAIN)["verdict"], truth.PASS)

    def test_a_promoted_node_that_is_running_is_ok(self):
        self.faithful_runtime()
        rev = self.install_node()
        self.run_process(["python3", str(self.runtime_root / NODE_COMPOSITION
                                          / "current" / "bin/serverosd")],
                         started_at=2_000_000.0)
        report = truth.collect(self.args())
        rows = rows_by(report)
        self.assertEqual(rows[f"{truth.NODE_UNIT} (running unit)"]["verdict"], truth.OK)
        self.assertEqual(rows[f"{NODE_COMPOSITION} (node revision)"]["verdict"],
                         truth.OK)
        # The row is attributed to the composition the CALLER asked about. The
        # label is built from `--composition`, and a constant here would report
        # a node measured here as the node of whichever composition the file
        # happens to name — two different machines' business. Only the unit row
        # can state this: the revision row's `canonical` column carries the
        # revision label, which is what tells a reader WHICH bytes, and the
        # composition is already in its name.
        self.assertEqual(rows[f"{truth.NODE_UNIT} (running unit)"]["canonical"],
                         NODE_COMPOSITION)
        node = report["release"]["node"]
        self.assertEqual(node["state"], "PROMOTED")
        self.assertEqual(node["current"], rev.name)
        self.assertEqual(node["processes"]["running"], [4242])
        self.assertTrue(node["tree_hash_ok"])
        self.assertEqual(report["release"]["verdict"], truth.RELEASE_COMPLETE)

    def test_a_node_that_is_not_running_is_named_not_passed(self):
        self.faithful_runtime()
        self.install_node()
        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]
        self.assertEqual(row["verdict"], truth.NOT_RUNNING)
        self.assertIn("declared and is not executing", row["detail"])
        self.assertEqual(set(report["release"]["blocking"]), {truth.AXIS_CHAIN})

    def test_a_promote_without_a_restart_is_drift_not_success(self):
        """The case the whole running surface exists for.

        `release.py promote` swaps the symlink and runs `daemon-reload`; it does
        NOT restart the service. The unit still declares `current/bin/serverosd`,
        the symlink still resolves, and the process is still executing the code
        it imported at start — so every fact except TIME reads as agreement. The
        process started before `current` was last repointed, and that is the
        fact that decides it.
        """
        self.faithful_runtime()
        self.install_node()
        link = self.runtime_root / NODE_COMPOSITION / "current"
        self.run_process(["python3", str(link / "bin/serverosd")], started_at=2_000_000.0)
        # `promote` repoints the link and does not restart the service.
        os.utime(link, (3_000_000.0, 3_000_000.0), follow_symlinks=False)

        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]
        self.assertEqual(row["verdict"], truth.BUILT_RUNTIME_DRIFT)
        self.assertIn("repointed", row["detail"])
        entry = report["release"]["node"]["processes"]["started_before_current_was_repointed"]
        self.assertEqual([e["pid"] for e in entry], [4242])
        self.assertAlmostEqual(entry[0]["current_repointed_at"] - entry[0]["started_at"],
                               1_000_000.0, places=1)

    def test_a_restart_after_the_promote_clears_that_finding(self):
        """The negative control for the test above.

        Without this, a checker that reported drift whenever two clocks were
        compared at all would pass the previous test. Same promote, same
        processes, one difference: the process started AFTER the repoint.
        """
        self.faithful_runtime()
        self.install_node()
        link = self.runtime_root / NODE_COMPOSITION / "current"
        os.utime(link, (3_000_000.0, 3_000_000.0), follow_symlinks=False)
        self.run_process(["python3", str(link / "bin/serverosd")], started_at=3_000_100.0)
        report = truth.collect(self.args())
        self.assertEqual(rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]["verdict"],
                         truth.OK)
        self.assertEqual(report["release"]["node"]["processes"]
                         ["started_before_current_was_repointed"], [])

    def test_a_unit_rendered_for_a_retired_revision_is_stale(self):
        self.faithful_runtime()
        rev = self.install_node()
        retired = rev.parent / "main@2d824b8"
        shutil.copytree(rev, retired)
        self.install_node(declared=str(retired / "bin/serverosd"))
        self.run_process(["python3", str(retired / "bin/serverosd")], started_at=2_000_000.0)
        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]
        self.assertEqual(row["verdict"], truth.UNIT_STALE)
        self.assertIn("rendered for a revision", row["detail"])

    def test_a_hand_started_process_on_another_revision_is_drift(self):
        """`elsewhere`: a live process under this node's revisions/ that is not
        the declared program. Reachable by starting the CLI from a revision path
        by hand, and invisible to a checker that only looks for the declared
        string."""
        self.faithful_runtime()
        rev = self.install_node()
        old = rev.parent / "main@2d824b8"
        shutil.copytree(rev, old)
        self.run_process(["python3", str(old / "bin/serverosd")], started_at=2_000_000.0)
        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]
        self.assertEqual(row["verdict"], truth.BUILT_RUNTIME_DRIFT)
        self.assertEqual(report["release"]["node"]["processes"]
                         ["running_a_superseded_revision"][0]["pid"], 4242)

    def test_the_scan_does_not_find_the_shell_that_ran_it(self):
        """Exact argument match, and here is the positive control for it.

        A substring match on a command line finds any process whose text merely
        mentions the path — including the shell that typed the search. The first
        draft of this scan did exactly that and reported its own parent as the
        running daemon. Both halves are asserted here, because a scan that
        matches nothing would pass the negative half alone.
        """
        self.faithful_runtime()
        self.install_node()
        link = self.runtime_root / NODE_COMPOSITION / "current"
        self.run_process(["/bin/bash", "-c", f"grep {link}/bin/serverosd /proc/*/cmdline"],
                         pid=1111, started_at=2_000_000.0)
        self.run_process(["python3", str(link / "bin/serverosd")],
                         pid=2222, started_at=2_000_000.0)
        report = truth.collect(self.args())
        self.assertEqual(report["release"]["node"]["processes"]["running"], [2222])
        self.assertEqual(rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]["verdict"],
                         truth.OK)

    def test_an_unrendered_unit_is_unknown_and_says_why(self):
        """`release.py` renders `@SERVEROS_RUNTIME@` at install. A unit still
        carrying it installs cleanly — systemd treats it as text — and starts a
        literal path, so it is named rather than resolved."""
        self.faithful_runtime()
        self.install_node(declared="@SERVEROS_RUNTIME@/bin/serverosd")
        report = truth.collect(self.args())
        row = rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]
        self.assertEqual(row["verdict"], truth.UNKNOWN)
        self.assertIn("without being rendered", row["detail"])

    def test_a_revision_whose_bytes_were_replaced_is_drift(self):
        """The recorded hash is checked against the directory it describes.

        A revision is immutable once staged; a byte changed inside one means the
        thing being executed is not the thing that was promoted, and no amount
        of rebuilding from source would say so.
        """
        self.faithful_runtime()
        rev = self.install_node()
        (rev / "runtime/daemon.py").write_text("# replaced after the promote\n")
        report = truth.collect(self.args())
        row = rows_by(report)[f"{NODE_COMPOSITION} (node revision)"]
        self.assertEqual(row["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("not the ones that were promoted", row["detail"])
        self.assertFalse(report["release"]["node"]["tree_hash_ok"])

    def test_a_dangling_current_is_named_rather_than_crashing(self):
        self.faithful_runtime()
        self.install_node()
        link = self.runtime_root / NODE_COMPOSITION / "current"
        link.unlink()
        link.symlink_to(self.runtime_root / NODE_COMPOSITION / "revisions/main@gone")
        report = truth.collect(self.args())
        self.assertEqual(report["release"]["node"]["state"], "REVISION_MISSING")
        self.assertEqual(rows_by(report)[f"{NODE_COMPOSITION} (node revision)"]
                         ["verdict"], truth.UNKNOWN)
        self.assertEqual(set(report["release"]["blocking"]), {truth.AXIS_CHAIN})

    def test_a_revision_is_rebuilt_from_the_source_it_records(self):
        """The revision is judged by the tree it names, not by the worktree.

        A staged revision records the commit it was built from. Rebuilding it
        from the checkout's WORKING TREE asks a different question: the moment
        anyone edits a profile in that checkout, the working tree builds a
        version the revision never claimed to be, and the difference is printed
        as the running node's drift. The one variable here is
        `--use-checkout-source`, which restores exactly that reading, so the
        two answers stand side by side on one fixture.
        """
        self.faithful_runtime()
        self.install_node()
        self.build_node_artifact(cachebuster="fixture.2")
        self.run_process(["python3", str(self.runtime_root / NODE_COMPOSITION
                                          / "current" / "bin/serverosd")],
                         started_at=2_000_000.0)
        row = rows_by(truth.collect(self.args()))[
            f"{NODE_COMPOSITION} (node revision)"]
        self.assertEqual(row["verdict"], truth.OK)
        self.assertIn("recomputed from the artifact's own recorded source",
                      row["note"])

        # ...and the old reading, which answered from the working tree: that
        # tree has been edited since, so it builds a different version and the
        # revision is reported as drifting from a source it never had.
        old = rows_by(truth.collect(self.args(use_checkout_source=True)))[
            f"{NODE_COMPOSITION} (node revision)"]
        self.assertEqual(old["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("builds 3.1.0+fixture.2", old["detail"])
        self.assertNotIn("recomputed from the artifact's own recorded source",
                         old["note"])

    def test_a_checkout_that_has_moved_on_is_not_the_nodes_drift(self):
        """The commit the checkout moved ON to does not enter this question.

        A working copy stops where its last fetch left it, so on any node past
        the first release it stands on a commit the running revision does not
        name. While the comparison ran off that working tree, the path diff it
        produced was indistinguishable from a genuinely stale artifact's, and
        the verdict could only say «cannot decide». With the revision rebuilt
        from the commit it records, the diff is the artifact's own and the
        answer is yes or no.

        The fallback is the other half, and it is tested below: when the
        recorded commit cannot be produced at all, «cannot decide» is still the
        honest answer.
        """
        self.faithful_runtime()
        self.install_node()
        # The one variable: source moves on by COMMIT, not by an uncommitted
        # edit, so the new commit is a real tree the checkout now stands on.
        self.build_node_artifact(cachebuster="fixture.2")
        git(self.serveros, "commit", "--quiet", "-a", "-m", "release train moved on")
        report = truth.collect(self.args())
        row = rows_by(report)[f"{NODE_COMPOSITION} (node revision)"]
        self.assertEqual(row["verdict"], truth.OK)
        self.assertIn("recomputed from the artifact's own recorded source",
                      row["note"])
        # And the old reading of the same fixture, where the moved-on checkout
        # IS the comparison's basis. That is the reading this file used to
        # print always, and it named the instrument instead of the artifact.
        old = rows_by(truth.collect(self.args(use_checkout_source=True)))[
            f"{NODE_COMPOSITION} (node revision)"]
        self.assertEqual(old["verdict"], truth.SOURCE_STALE)
        self.assertIn("The instrument is the one that is behind", old["detail"])
        self.assertNotIn("differs in", old["detail"])

    def test_a_recorded_commit_this_repository_does_not_have_says_so(self):
        """When the tree cannot be produced, the row says which one it wanted.

        A rebuild from the checkout is not a rebuild from the artifact's
        source, and the difference between the two has to survive on the row —
        otherwise a reader sees the fallback's path diff and cannot tell it
        from a comparison that was made correctly.
        """
        self.faithful_runtime()
        version = self.build_artifact()
        # A payload of its own, so that rewriting its record does not also
        # rewrite the `--output` artifact the fixture is measuring.
        payload = self.base / "claude-cache" / CORE_PLUGIN_KEY / version
        payload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.out, payload)
        self.install_claude(version, payload)
        absent = "b" * 40
        # A difference in CONTENT as well as in the record: `tree_drift`
        # compares ARTIFACT.json in a normal form that drops the source block,
        # so on an empty difference the row is OK and attribution is never
        # consulted — the standing behaviour, and not what this test is about.
        # What it is about is a row that HAS a difference and a rebuild that
        # cannot be attributed, so it must have both.
        (payload / "skills/runtime-management/SKILL.md").write_text("elsewhere\n")
        manifest = json.loads((payload / truth.ARTIFACT_MANIFEST).read_text())
        manifest["source"]["serveros"]["commit"] = absent
        (payload / truth.ARTIFACT_MANIFEST).write_text(json.dumps(manifest))

        row = rows_by(truth.collect(self.args()))[
            f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"]
        self.assertEqual(row["verdict"], truth.SOURCE_STALE)
        self.assertIn(f"records {absent[:12]}", row["note"])
        self.assertIn("has no such commit", row["note"])

    def test_a_worktree_build_is_not_accused_of_the_changes_it_records(self):
        """`tree_clean: false` means no rebuild can decide, and it is read.

        The commit an artifact records names the checkout its build ran in. If
        the record ALSO says that checkout had uncommitted changes, then the
        rebuilt tree is close to the artifact's source without being it, and
        every path the difference contains is one the artifact's own record
        already explains. Rebuilding from that tree and calling the result the
        artifact's fault is the same false accusation as measuring a plugin at
        the node's pin, arriving from the other side.

        The control is the same fixture with the one field flipped: clean, and
        the row decides again.
        """
        self.faithful_runtime()
        version = self.build_artifact()
        payload = self.base / "claude-cache" / CORE_PLUGIN_KEY / version
        payload.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.out, payload)
        self.install_claude(version, payload)
        # A real content difference, not an edit to the source record: that
        # record is compared in a normal form that drops the fields describing
        # the build, precisely because a rebuild cannot reproduce them. What a
        # worktree build leaves behind is content, so that is what this makes.
        (payload / "skills/runtime-management/SKILL.md").write_text("from the worktree\n")
        manifest = payload / truth.ARTIFACT_MANIFEST
        record = json.loads(manifest.read_text())
        record["source"]["serveros"].update(
            {"tree_clean": False, "worktree_changes": 3})
        manifest.write_text(json.dumps(record))

        label = f"{CORE_PLUGIN_KEY} (Claude plugin, enabled)"
        dirty = rows_by(truth.collect(self.args()))[label]
        self.assertEqual(dirty["verdict"], truth.SOURCE_STALE)
        self.assertIn("3 uncommitted change(s)", dirty["detail"])
        self.assertIn("names the checkout", dirty["detail"])

        # The control differs in the one field the test is about: the same
        # payload, the same difference, and a record that says the tree it was
        # built from WAS these bytes. Now the difference is the artifact's.
        record["source"]["serveros"].update(
            {"tree_clean": True, "worktree_changes": 0})
        manifest.write_text(json.dumps(record))
        clean = rows_by(truth.collect(self.args()))[label]
        self.assertEqual(clean["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("differs in 1 path(s)", clean["detail"])
        self.assertIn("skills/runtime-management/SKILL.md", clean["detail"])

    def test_an_artifact_with_no_recorded_source_is_not_called_misattributed(self):
        """Silence is not a claim.

        An artifact that predates the source record, or one whose manifest is
        unreadable, would otherwise read as «built from a different checkout»
        — and a verdict that fires on every old machine is a verdict nobody
        reads on a new one. Unknown on either side answers «same».
        """
        empty = self.base / "no-record"
        empty.mkdir()
        head = "a" * 40
        self.assertEqual(truth.source_attribution(empty, head, self.serveros),
                         (True, ""))
        self.assertEqual(truth.source_attribution(None, head, self.serveros),
                         (True, ""))
        # And the comparison is by prefix, because the row labels print twelve.
        rev = self.install_node()
        recorded = truth.recorded_source_commit(rev)
        self.assertTrue(truth.source_attribution(rev, recorded, self.serveros)[0])
        self.assertFalse(truth.source_attribution(rev, "f" * 40, self.serveros)[0])

    def test_the_node_revision_is_hashed_without_its_own_manifest(self):
        """`RELEASE.json` records the tree hash and therefore cannot be inside
        it. A verifier that fingerprinted the staged directory whole would get a
        correct number that matches nothing, and would read every healthy
        revision as tampered with."""
        self.faithful_runtime()
        rev = self.install_node()
        recorded = json.loads((rev / truth.TREE_MANIFEST).read_text())["tree_hash"]
        self.assertEqual(recorded, release.fingerprint(rev, ignore=(truth.TREE_MANIFEST,)))
        self.assertNotEqual(recorded, release.fingerprint(rev))

    def test_the_bytecode_a_revision_wrote_is_not_read_as_tampering(self):
        """The staged revision is a directory the node RUNS out of, so running
        it leaves `__pycache__` inside the tree whose hash was recorded.

        This is the defect `release.fingerprint`'s `BYTECODE_DIRS` note is about,
        and it is not hypothetical: on 2026-09-26 the live node reported
        `SOURCE_RUNTIME_DRIFT` — «these bytes are not the ones that were
        promoted» — against an unmodified revision that differed from its record
        by exactly 34 `.pyc` files. A verifier that reads derived bytes as
        payload accuses the node it was built to exonerate, and it does so on
        every node that has ever been restarted.
        """
        self.faithful_runtime()
        rev = self.install_node()
        recorded = json.loads((rev / truth.TREE_MANIFEST).read_text())["tree_hash"]
        cache = rev / "runtime" / "__pycache__"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "daemon.cpython-312.pyc").write_bytes(b"\x00bytecode, not payload\n")
        (cache / "daemon.cpython-312.pyo").write_bytes(b"\x00optimised\n")
        self.assertEqual(release.fingerprint(rev, ignore=(truth.TREE_MANIFEST,)),
                         recorded)
        report = truth.collect(self.args())
        self.assertEqual(rows_by(report)[f"{NODE_COMPOSITION} (node revision)"]
                         ["verdict"], truth.OK)

    def test_the_exclusion_is_bytecode_and_not_a_blind_eye(self):
        """The filter must skip derived files and nothing else.

        A revision that grows a source file after promotion is exactly the case
        the check exists for, and an exclusion keyed on anything looser than
        «this path is bytecode» would report that revision as intact.
        """
        self.faithful_runtime()
        rev = self.install_node()
        recorded = json.loads((rev / truth.TREE_MANIFEST).read_text())["tree_hash"]
        (rev / "runtime" / "smuggled.py").write_text("# not bytecode\n")
        self.assertNotEqual(release.fingerprint(rev, ignore=(truth.TREE_MANIFEST,)),
                            recorded)
        # And a name that merely mentions bytecode is still a file: what is
        # skipped is the suffix and the directory, not a substring.
        (rev / "runtime" / "smuggled.py").unlink()
        (rev / "runtime" / "pycache_notes.pyc.md").write_text("# a note\n")
        self.assertNotEqual(release.fingerprint(rev, ignore=(truth.TREE_MANIFEST,)),
                            recorded)

    def test_a_revision_with_no_manifest_is_reported_not_assumed_broken(self):
        self.faithful_runtime()
        self.install_node(manifest=False)
        self.run_process(["python3", str(self.runtime_root / NODE_COMPOSITION
                                          / "current" / "bin/serverosd")],
                         started_at=2_000_000.0)
        report = truth.collect(self.args())
        self.assertIsNone(report["release"]["node"]["tree_hash_ok"])
        self.assertIn("cannot be checked against anything", report["release"]["node"]["detail"])
        # The rest of the node still answers: an unverifiable hash does not make
        # the revision unmeasurable, and the row still says what it found.
        self.assertEqual(rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]["verdict"],
                         truth.OK)

    def test_the_process_start_is_read_past_a_comm_that_contains_spaces(self):
        """Field 22 by the LAST `)`, not by counting from the left.

        `comm` is user-controlled: a program named `python3 serverosd` shifts
        every field after it. Counting from the start produces a number that
        looks like a start time and is not one — no exception, no test failure,
        just a comparison against the wrong clock reading.
        """
        self.faithful_runtime()
        self.install_node()
        when = 2_000_000.0
        self.run_process(["python3", "x"], started_at=when)
        # run_process writes a comm containing a space on purpose; assert the
        # parse lands on the tick count it was given rather than on a neighbour.
        got = truth.process_started_at(4242, self.proc_dir, 1_000_000.0)
        self.assertAlmostEqual(got, when, places=1)

    def test_a_missing_proc_root_is_no_processes_not_a_crash(self):
        self.faithful_runtime()
        self.install_node()
        report = truth.collect(self.args(proc_root=self.base / "no-such-proc"))
        self.assertEqual(report["release"]["node"]["processes"]["visible"], 0)
        self.assertEqual(rows_by(report)[f"{truth.NODE_UNIT} (running unit)"]["verdict"],
                         truth.NOT_RUNNING)

    def test_both_compositions_members_are_audited(self):
        """The node ships members the plugin does not. Auditing only the plugin
        left the ones a flat runtime skill root could duplicate unchecked."""
        self.faithful_runtime()
        self.install_node()
        report = truth.collect(self.args())
        compositions = report["release"]["materialization"]["compositions"]
        self.assertEqual(set(compositions), {"plugin payload", "node revision"})
        node_members = set(compositions["node revision"]["members"])
        self.assertTrue(node_members - set(compositions["plugin payload"]["members"]))


    def test_the_two_copies_of_the_root_resolver_agree(self):
        """One authority, two implementations — so the two are compared.

        `runtime_truth.py` carries its own copy of `architecture_check.py`'s
        resolver on purpose (the two ship from different roots and a shared
        import would be a dependency between things the release may move apart).
        The declared invariant is that they share the AUTHORITY: flags, then
        environment, then this host's profile, then a refusal.

        They did not. Both appended the layout suffix to the environment's value,
        so `SERVEROS_ABILITY_REPO=…/src/ability-management` resolved to
        `…/src/ability-management/ability-management` in the gauge while `build.py`,
        `taxonomy.py` and `runtime/cli.py` read the same variable as the checkout.
        A copy that is free to differ is a copy that will; this is the comparison
        that makes the difference a failure rather than a discovery.

        The two tables are compared as TABLES first, because the failure that
        reached a person was a path, and the failure underneath it is that the two
        files disagreed about what a suffix means.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_architecture_check_for_agreement",
            ROOT / "skills" / "serveros" / "scripts" / "architecture_check.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)

        shared = sorted(set(truth.ROOT_SOURCES) & set(check.ROOT_SOURCES))
        self.assertTrue(shared, "the two tables share no argument at all")
        for argument in shared:
            self.assertEqual(truth.ROOT_SOURCES[argument], check.ROOT_SOURCES[argument],
                             f"the two tables disagree about {argument}")

        checkout = self.base / "src" / "ability-management"
        # `patch.dict` restores the WHOLE environment on exit, including keys
        # removed inside it, so the machine's own values are put back and the
        # test cannot leak a synthetic root into a later case.
        with mock.patch.dict(os.environ, {}, clear=False):
            for name, _, _ in truth.ROOT_SOURCES.values():
                os.environ.pop(name, None)
            for name, _, _ in check.ROOT_SOURCES.values():
                os.environ.pop(name, None)
            os.environ.pop(truth.HOST_PROFILE_ENV, None)
            for argument in shared:
                variable = truth.ROOT_SOURCES[argument][0]
                os.environ[variable] = str(checkout)
                self.assertEqual(
                    truth.default_root(argument), checkout,
                    f"runtime_truth doubled the environment's value for {argument}")
                self.assertEqual(
                    check.default_root(argument), checkout,
                    f"architecture_check doubled the environment's value for {argument}")

    def test_the_two_copies_declare_where_they_differ(self):
        """The half an intersection comparison cannot see, and the census that
        found `workspace_root` unread went through this hole.

        The docstring above says of the two copies that "a copy that is free to
        differ is a copy that will", and that the comparison is what "makes the
        difference a failure rather than a discovery". Of the difference the two
        tables actually have — 6 rows of 8 — it is a discovery: `shared` is an
        intersection, so a row only one table names is invisible to it.

        Differing is not the defect. Differing without saying so is what this
        pins: the difference is declared by name above, and a row added to
        either table fails here until the declaration says so.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_architecture_check_for_differences",
            ROOT / "skills" / "serveros" / "scripts" / "architecture_check.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)

        only_in_truth = tuple(sorted(set(truth.ROOT_SOURCES) - set(check.ROOT_SOURCES)))
        only_in_check = tuple(sorted(set(check.ROOT_SOURCES) - set(truth.ROOT_SOURCES)))

        self.assertEqual(
            only_in_truth, ONLY_IN_RUNTIME_TRUTH,
            "the rows `runtime_truth.py` names alone have changed. Either the "
            "table gained or lost a row, or the other copy did. Update the "
            "declaration above — deliberately, and with the reason checked.")
        self.assertEqual(
            only_in_check, ONLY_IN_ARCHITECTURE_CHECK,
            "the rows `architecture_check.py` names alone have changed. "
            "`workspace_root` is one of them, and it is the row the two "
            "implementations of the host-profile read disagreed about — one "
            "read it and one computed it, and no comparison of the two TABLES "
            "could see it, because this table names it and the other does not. "
            "Update the declaration above deliberately.")

    def test_the_declaration_is_not_vacuous(self):
        """NEGATIVE CONTROL for the test above.

        Two tables that shared every row would make both declarations empty and
        the assertions above trivially true. They are not, and the number of
        rows each copy answers alone is the whole point of declaring them.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_architecture_check_for_vacuity",
            ROOT / "skills" / "serveros" / "scripts" / "architecture_check.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)

        self.assertTrue(truth.ROOT_SOURCES, "runtime_truth's table is empty")
        self.assertTrue(check.ROOT_SOURCES, "architecture_check's table is empty")
        self.assertEqual(len(ONLY_IN_RUNTIME_TRUTH), 3)
        self.assertEqual(len(ONLY_IN_ARCHITECTURE_CHECK), 3)
        shared = set(truth.ROOT_SOURCES) & set(check.ROOT_SOURCES)
        self.assertEqual(len(shared), 2,
                         "the two tables now share a different number of rows; "
                         "the census in `test_runtime_truth.py` and the one in "
                         "`tests/test_resolver_reads_the_document.py` both "
                         "assume this count")

    def test_the_profile_branch_appends_the_layout_to_both_copies(self):
        """The other half of the rule, so the fix cannot be "delete the suffix".

        A profile names a ROOT; the product layout hangs under it. Both copies
        must still append, or a node configured by profile would look one
        directory up for every repository it reads — the same defect mirrored.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_architecture_check_for_profile",
            ROOT / "skills" / "serveros" / "scripts" / "architecture_check.py")
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)

        profile = self.base / "host-profile.yaml"
        profile.write_text(
            "schema: serveros/host-profile.v1\n"
            "node:\n"
            "  roots:\n"
            f"    identity_home: {self.base}/home\n"
            f"    workspace_root: {self.base}/home/workspace\n"
            f"    workspace_src: {self.base}/home/workspace/src\n",
            encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=False):
            for name, _, _ in truth.ROOT_SOURCES.values():
                os.environ.pop(name, None)
            for name, _, _ in check.ROOT_SOURCES.values():
                os.environ.pop(name, None)
            os.environ[truth.HOST_PROFILE_ENV] = str(profile)
            expected = self.base / "home/workspace/src" / "ability-management"
            self.assertEqual(truth.default_root("ability_repo"), expected)
            self.assertEqual(check.default_root("ability_repo"), expected)


if __name__ == "__main__":
    unittest.main()
