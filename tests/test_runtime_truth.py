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
import subprocess
import tempfile
import unittest
from pathlib import Path

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
from test_build import make_ability_repo  # noqa: E402  (shares the synthetic fixture)


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


def chain_rows(report: dict) -> list[dict]:
    return [r for r in report["rows"] if r["component"] not in HOST_ROWS]


class RuntimeTruthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.ability, self.rev = make_ability_repo(self.base)
        self.out = self.base / "dist/current"
        self.marketplace = self.base / "dist/.agents/plugins/marketplace.json"
        self.engine_repo = self.base / "engine"
        (self.engine_repo / "workengine").mkdir(parents=True)
        (self.engine_repo / "workengine" / "cli.py").write_text("# engine\n")
        (self.engine_repo / "workengine" / "federation.py").write_text("# fed\n")
        self.engine_installed = self.base / "site-packages/workengine"
        self.engine_installed.mkdir(parents=True)
        (self.engine_installed / "cli.py").write_text("# engine\n")
        (self.engine_installed / "federation.py").write_text("# fed\n")
        self.claude_json = self.base / "installed_plugins.json"
        self.codex_config = self.base / "config.toml"
        self.codex_cache = self.base / "codex-cache"

    def tearDown(self):
        self._tmp.cleanup()

    def build_artifact(self):
        rc = build.build(ROOT / "plugin/bundle.yaml", self.ability, self.rev,
                         self.out, self.marketplace, write=True)
        self.assertEqual(rc, 0)
        version = json.loads((self.out / ".claude-plugin/plugin.json").read_text())["version"]
        return version

    def install_claude(self, version: str, path: Path):
        self.claude_json.write_text(json.dumps({"version": 2, "plugins": {
            truth.CLAUDE_PLUGIN_KEY: [{"scope": "user", "installPath": str(path),
                                       "version": version, "gitCommitSha": self.rev}]}}))

    def install_codex(self, version: str, payload_src: Path):
        leaf = self.codex_cache / truth.CODEX_MARKETPLACE / truth.BUNDLE_NAME / version
        leaf.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copytree(payload_src, leaf)
        self.codex_config.write_text(
            "[marketplaces.%s]\nsource = \"/somewhere\"\n\n"
            "[plugins.\"%s@%s\"]\nenabled = true\n"
            % (truth.CODEX_MARKETPLACE, truth.BUNDLE_NAME, truth.CODEX_MARKETPLACE))

    def args(self, **over):
        base = dict(
            serveros_repo=ROOT, ability_repo=self.ability, ability_rev=self.rev,
            output=self.out, marketplace_path=self.marketplace,
            engine_repo=self.engine_repo, engine_installed=self.engine_installed,
            claude_plugins_json=self.claude_json, codex_config=self.codex_config,
            codex_cache=self.codex_cache,
        )
        base.update(over)
        return argparse.Namespace(**base)

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


if __name__ == "__main__":
    unittest.main()
