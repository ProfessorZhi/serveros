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
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "scripts" / "runtime_truth.py"
BUILDER = ROOT / "scripts" / "build.py"

_spec = importlib.util.spec_from_file_location("runtime_truth", INSPECTOR)
truth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(truth)

_bspec = importlib.util.spec_from_file_location("serveros_build_rt", BUILDER)
build = importlib.util.module_from_spec(_bspec)
_bspec.loader.exec_module(build)

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_build import _git as git  # noqa: E402
from test_build import make_ability_repo, write_pack_contract  # noqa: E402


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
    return [r for r in report["rows"] if r["component"] not in HOST_ROWS]


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
        (self.serveros / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts/build.py", self.serveros / "scripts/build.py")
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
        # Importing build.py writes scripts/__pycache__, which would make the
        # synthetic checkout permanently dirty the moment the inspector loads
        # the builder it is inspecting.
        (self.serveros / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        git(self.serveros, "init", "--quiet", "-b", "main")
        git(self.serveros, "config", "user.email", "test@example.invalid")
        git(self.serveros, "config", "user.name", "test")
        git(self.serveros, "add", "-A")
        git(self.serveros, "commit", "--quiet", "-m", "synthetic serveros")

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
        self.codex_config = self.base / "config.toml"
        self.codex_cache = self.base / "codex-cache"
        # Runtime skill roots are pointed INSIDE the fixture, at paths that do
        # not exist. Defaulting them to the real ~/.claude/skills would make
        # this suite assert facts about the machine it happens to run on — the
        # same mistake that made "every row is OK" fail on the first run.
        self.claude_skills = self.base / "claude-skills"
        self.codex_skills = self.base / "codex-skills"

    def tearDown(self):
        self._tmp.cleanup()

    def build_artifact(self):
        rc = build.build(self.serveros / "plugin/bundle.yaml", self.ability, self.rev,
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

    def install_claude(self, version: str, path: Path, rev: str | None = None):
        self.claude_json.write_text(json.dumps({"version": 2, "plugins": {
            truth.CLAUDE_PLUGIN_KEY: [{"scope": "user", "installPath": str(path),
                                       "version": version,
                                       "gitCommitSha": rev or self.rev}]}}))

    def build_and_install(self, rev: str | None = None) -> str:
        """Build the artifact from `rev` and install it as that revision.

        The pin is not decoration: `build_readme` embeds the ability revision
        in the payload, so an artifact built from one revision and recorded
        against another can never be chain-consistent. Building and pinning
        together is the only way to reach the interesting case — a chain that
        agrees with itself *and* a revision canonical does not contain.
        """
        rev = rev or self.rev
        rc = build.build(self.serveros / "plugin/bundle.yaml", self.ability, rev,
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
            claude_plugins_json=self.claude_json, codex_config=self.codex_config,
            codex_cache=self.codex_cache,
            canonical_ref=truth.DEFAULT_CANONICAL_REF,
            claude_skills_root=self.claude_skills,
            codex_skills_root=self.codex_skills,
        )
        base.update(over)
        return argparse.Namespace(**base)

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
        built = rows_by(report)["hwz-server-os @ runtime-assets (built)"]
        self.assertIn("context/resolver.py", built["detail"])

    def test_absent_file_is_named(self):
        self.build_artifact()
        self.install_claude("0", self.out)
        self.install_codex("0", self.out)
        (self.out / "README.md").unlink()
        report = truth.collect(self.args())
        self.assertEqual(report["verdict"], truth.SOURCE_RUNTIME_DRIFT)
        self.assertIn("missing README.md",
                      rows_by(report)["hwz-server-os @ runtime-assets (built)"]["detail"])

    def test_a_missing_runtime_is_missing_not_ok(self):
        self.build_artifact()
        self.install_claude("0", self.out)
        report = truth.collect(self.args(codex_cache=self.base / "nowhere"))
        row = rows_by(report)["hwz-server-os @ Codex plugin cache"]
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
        got = truth.read_claude_install(self.claude_json)
        self.assertEqual(got["version"], "9.9.9")
        self.assertEqual(got["installPath"], str(self.out))

    def test_missing_claude_record_is_empty_not_an_exception(self):
        self.assertEqual(truth.read_claude_install(self.base / "nope.json"), {})

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
        row = rows_by(report)["hwz-server-os @ Codex plugin cache"]
        self.assertEqual(row["verdict"], truth.OK)
        self.assertEqual(row["installed"], f"sha256={truth.tree_hash(self.out)}")


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
        rc = build.build(self.serveros / "plugin/bundle.yaml", self.ability, sha,
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
        doc["plugins"][truth.CLAUDE_PLUGIN_KEY][0].pop("gitCommitSha", None)
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


if __name__ == "__main__":
    unittest.main()
