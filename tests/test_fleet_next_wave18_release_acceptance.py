#!/usr/bin/env python3
"""Wave 18: independent release acceptance for the Fleet Next candidate.

The subject under test is `integrations/fleet-next-wave18-release-acceptance/
wave18_acceptance.py` — a six-layer matrix and an offline drill driver.

What this file is allowed to prove
----------------------------------
Every assertion runs offline, against fixtures built in `tmp_path` (or the
pinned release pin, which is read as text). No service is started, no port is
bound, no socket is opened, no credential is read, no device is addressed and
no real Fleet installation is touched. The one thing a green run here does NOT
prove is that a real device is safe: `REAL_DEVICE` is a constant `NOT_RUN` in
the product, and `TheRealDeviceGateIsNotRun` holds it there.

The two controls that keep this from being a tautology
------------------------------------------------------
* **Positive**: `TheDrillCompletesOnAFixtureWorld` runs the whole lifecycle
  (install -> observe -> failed upgrade -> rollback -> revoke) in six separate
  operating-system processes and requires the Legacy snapshot to be identical
  after every one of them.
* **Negative**: `EveryInjectionRefusesWithItsOwnCode` drives each refusal
  shape and requires the *exact* code, so an implementation that blocks
  everything (or nothing) cannot pass. `NoFakeGreen` then mutates the fixture
  engine into one that always says OK and requires the drill to go red.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-wave18-release-acceptance"
PIN_PATH = HERE / "release_pin.json"
MODULE_PATH = HERE / "wave18_acceptance.py"
FIXTURE_ENGINE = HERE / "drill" / "fixture_engine.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Wave18 Acceptance",
    "GIT_AUTHOR_EMAIL": "acceptance@example.invalid",
    "GIT_COMMITTER_NAME": "Wave18 Acceptance",
    "GIT_COMMITTER_EMAIL": "acceptance@example.invalid",
    "GIT_AUTHOR_DATE": "2026-10-29T00:00:00 +0000",
    "GIT_COMMITTER_DATE": "2026-10-29T00:00:00 +0000",
}

DATE_WINDOW_TEST = (
    "tests/test_direct_lark_notify.py::CardCreateThenPatchTest::"
    "test_create_returns_an_id_and_patch_names_it"
)


def load_acceptance():
    """Load the module once, consulting the cache before taking the name.

    The name is process-wide; taking it regardless would hand a second caller
    the first module object (this tree's `test_loader_identity.py` convention).
    """
    name = "wave18_acceptance"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
        if spec is None or spec.loader is None:  # pragma: no cover - RED marker
            raise ImportError(f"wave18_acceptance.py is not at {MODULE_PATH}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def run_git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_ENV)
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=env)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout.strip()


def build_fixture_repo(root: Path) -> dict:
    """A three-commit repository: main, a unit branch, and a second unit."""
    repo = root / "repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-q", "-b", "main")
    run_git(repo, "config", "core.autocrlf", "false")

    def commit(files: dict[str, str], message: str) -> str:
        for rel, body in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body.encode("utf-8"))
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", message)
        return run_git(repo, "rev-parse", "HEAD")

    base = commit({"README.md": "fixture\n"}, "base")
    run_git(repo, "checkout", "-q", "-b", "unit")
    unit = commit({
        "deploy/gate.py": "GATE = 'fixture'\n",
        "deploy/plan.json": '{"plan": true}\n',
        "tools/release.py": (
            "import hashlib\n"
            "def digest(body):\n"
            "    return 'sha256:' + hashlib.sha256(body).hexdigest()\n"
        ),
        # The vocabulary is #504's, verbatim: the drill must not invent a
        # status word that the shipped monitor does not have.
        "next/contract.py": (
            "STATUS_UNKNOWN = 'UNKNOWN'\n"
            "STATUS_OFFLINE = 'OFFLINE'\n"
            "STATUS_TERMINAL_VERIFIED = 'TERMINAL_VERIFIED'\n"
            "NOT_SUCCESS = frozenset({STATUS_UNKNOWN, STATUS_OFFLINE})\n"
            "BLOCKED_SUBJECT = 'BLOCKED_SUBJECT'\n"
            "BLOCKED_WORDS = frozenset({BLOCKED_SUBJECT})\n"
        ),
        "tests/test_fixture_unit.py": (
            "def test_the_fixture_unit_ticks():\n"
            "    assert 1 + 1 == 2\n"
        ),
    }, "unit")

    run_git(repo, "checkout", "-q", "main")
    run_git(repo, "checkout", "-q", "-b", "second")
    second = commit({"docs/SECOND.md": "second\n"}, "second")
    run_git(repo, "checkout", "-q", "main")
    return {"repo": repo, "base": base, "unit": unit, "second": second}


def fixture_pin(repo_info: dict, *, artifact_dir: Path | None = None,
                artifact_digests: dict[str, str] | None = None) -> dict:
    repo = repo_info["repo"]
    unit = repo_info["unit"]
    names = ["fleet-runtime-base-1.0.0+wave18.tar.gz"]
    digests = artifact_digests if artifact_digests is not None else {}
    return {
        "schema": "serveros/fleet-next-release-pin.v1",
        "wave": "wave18",
        "created": "2026-10-29",
        "base": {"commit": repo_info["base"], "branch": "main"},
        "units": [{
            "id": "fixture-unit",
            "pr": 1,
            "commit": unit,
            "role": "fixture",
            "paths": ["deploy/gate.py", "deploy/plan.json", "tools/release.py",
                      "next/contract.py", "tests/test_fixture_unit.py"],
            "unit_tests": ["tests/test_fixture_unit.py"],
            "platforms": ["linux", "win32"],
        }],
        "composition": None,
        "release_package": {
            "name": "fleet-next-sidecar",
            "version": "0.1.0+wave18",
            "files": [
                {"path": "deploy/gate.py", "source": "fixture-unit"},
                {"path": "deploy/plan.json", "source": "fixture-unit"},
                {"path": "tools/release.py", "source": "fixture-unit"},
                {"path": "next/contract.py", "source": "fixture-unit"},
            ],
            "revoke_entrypoint": "tools/release.py",
        },
        "legacy": {
            "fleet_instance_id": "fleet-legacy-0.1.0",
            "route_id": "route-legacy-primary",
            "boot_id": "boot-legacy-20261029",
            "port": 8101,
            "backend_url": "http://127.0.0.1:8101/mcp",
            "oauth_audience": "https://legacy.invalid/mcp",
            "clock": "2026-10-29T00:00:00Z",
            "contract": ["get_session", "get_run", "send"],
        },
        "device": {"device_ref": "device:fixture-01",
                   "boot_id": "boot-wave18-drill"},
        "artifact": {
            "published": True,
            "expected_names": names,
            "expected_sha256": digests,
            "search_paths": [],
        },
        "forbidden": ["systemctl", "oauth", "feishu", "tailscale"],
    }


def write_pin(path: Path, pin: dict) -> Path:
    path.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    return path


def make_tarball(path: Path, body: bytes = b"fixture-artifact\n") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("ARTIFACT.json")
        info.size = len(body)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(body))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def green_ci_reader(pin: dict):
    """The CI reader contract: one report per unit, from the exact head."""
    def reader(pin_arg):
        return {
            unit["id"]: {
                "head": unit["commit"],
                "url": "https://example.invalid/run/1",
                "checks": [{"name": "Suite (shard 1/4)", "status": "completed",
                            "conclusion": "success"}],
                "gates": [{"shard": "1/4", "parsed": True, "failing": 13,
                           "new": [], "flaky": 0, "listed_but_passing": 0}],
            }
            for unit in pin_arg["units"]
        }
    return reader


class ThePinIsAReleaseClaimNotAProse(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def test_the_committed_pin_names_the_six_units_and_their_exact_heads(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        units = {unit["id"]: unit for unit in pin["units"]}
        self.assertEqual(set(units), {
            "rc-wave16", "runtime-abi", "monitor-p0",
            "hub-node-cut", "sidecar-revoke", "release-preflight",
        })
        self.assertEqual(units["rc-wave16"]["pr"], 500)
        self.assertEqual(units["runtime-abi"]["pr"], 503)
        self.assertEqual(units["monitor-p0"]["pr"], 504)
        self.assertEqual(units["hub-node-cut"]["pr"], 505)
        self.assertEqual(units["sidecar-revoke"]["pr"], 506)
        self.assertEqual(units["release-preflight"]["pr"], 502)
        for unit in units.values():
            self.assertRegex(unit["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(pin["base"]["commit"],
                         "e69fc0337ae5d33000478e3d5162077f525bc8cc")

    def test_a_well_formed_pin_has_no_problems(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        self.assertEqual(self.acceptance.validate_pin(pin), ())

    def test_every_release_package_file_names_a_pinned_source(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        ids = {unit["id"] for unit in pin["units"]}
        for member in pin["release_package"]["files"]:
            self.assertIn(member["source"], ids)
            self.assertFalse(member["path"].startswith("/"))
            self.assertNotIn("..", member["path"].split("/"))

    def test_a_floating_version_is_not_a_pin(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["release_package"]["version"] = "latest"
        self.assertIn("version", " ".join(self.acceptance.validate_pin(broken)))

    def test_a_short_commit_is_not_a_pin(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["units"][0]["commit"] = broken["units"][0]["commit"][:7]
        self.assertIn("commit", " ".join(self.acceptance.validate_pin(broken)))

    def test_a_release_member_from_no_recorded_unit_is_refused(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["release_package"]["files"][0]["source"] = "not-a-unit"
        self.assertIn("source", " ".join(self.acceptance.validate_pin(broken)))

    def test_a_path_that_leaves_the_package_is_refused(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["release_package"]["files"][0]["path"] = "../outside.py"
        self.assertIn("path", " ".join(self.acceptance.validate_pin(broken)))

    def test_a_duplicate_unit_id_is_refused(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["units"].append(json.loads(json.dumps(broken["units"][0])))
        self.assertIn("duplicate", " ".join(self.acceptance.validate_pin(broken)))

    def test_a_pin_that_names_no_legacy_contract_is_refused(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        broken = json.loads(json.dumps(pin))
        broken["legacy"].pop("contract")
        self.assertIn("contract", " ".join(self.acceptance.validate_pin(broken)))


class TheMatrixHasSixLayersAndSaysWhatEachProves(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def layer(self, name, state, code):
        return self.acceptance.LayerResult(name=name, state=state, code=code)

    def test_the_layers_are_exactly_these_six_in_this_order(self):
        self.assertEqual(self.acceptance.LAYERS, (
            "SOURCE", "UNIT", "HOSTED_CI", "BUILT_ARTIFACT",
            "LEGACY_SURVIVAL", "REAL_DEVICE",
        ))

    def test_all_five_proven_layers_plus_not_run_is_ready(self):
        layers = [self.layer(name, "PASS", "OK") for name in
                  self.acceptance.LAYERS[:-1]]
        layers.append(self.acceptance.build_real_device_layer())
        verdict = self.acceptance.aggregate(layers)
        self.assertEqual(verdict.state, "READY")
        self.assertEqual(verdict.exit_code, 0)

    def test_one_blocked_layer_blocks_the_whole_matrix(self):
        layers = [self.layer(name, "PASS", "OK") for name in
                  self.acceptance.LAYERS[:-1]]
        layers[4] = self.layer("LEGACY_SURVIVAL", "BLOCKED",
                               "BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT")
        layers.append(self.acceptance.build_real_device_layer())
        verdict = self.acceptance.aggregate(layers)
        self.assertEqual(verdict.state, "BLOCKED")
        self.assertEqual(verdict.exit_code, 2)
        # A reason line names the layer *and* the code ("LEGACY_SURVIVAL:
        # BLOCKED_..."), which is what an operator needs to act on it; the
        # code has to be in there, the exact spelling of the line is not the
        # contract.
        self.assertTrue(any("BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT" in reason
                            for reason in verdict.reasons), verdict.reasons)

    def test_a_missing_layer_is_incomplete_not_green(self):
        verdict = self.acceptance.aggregate(
            [self.layer("SOURCE", "PASS", "OK")])
        self.assertEqual(verdict.state, "INCOMPLETE")
        self.assertEqual(verdict.exit_code, 3)
        self.assertIn("MISSING", " ".join(verdict.reasons))

    def test_real_device_is_never_a_pass_even_when_asked_to_run(self):
        layer = self.acceptance.build_real_device_layer(allow=True)
        self.assertEqual(layer.state, "NOT_RUN")
        self.assertIn("REAL_DEVICE", layer.code)
        self.assertIn("authorization", " ".join(layer.reasons))

    def test_an_unknown_layer_state_is_not_green(self):
        layers = [self.layer(name, "PASS", "OK") for name in
                  self.acceptance.LAYERS[:-1]]
        layers[0] = self.layer("SOURCE", "SOMETHING_NEW", "OK")
        verdict = self.acceptance.aggregate(layers + [
            self.acceptance.build_real_device_layer()])
        self.assertNotEqual(verdict.state, "READY")

    def test_the_matrix_table_names_all_six_layers(self):
        layers = [self.layer(name, "PASS", "OK") for name in
                  self.acceptance.LAYERS[:-1]]
        layers.append(self.acceptance.build_real_device_layer())
        table = self.acceptance.matrix_rows(layers)
        self.assertEqual([row["layer"] for row in table],
                         list(self.acceptance.LAYERS))
        self.assertEqual(table[-1]["state"], "NOT_RUN")


class TheBaselineGateIsParsedNotAssumed(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def gate(self, text):
        return self.acceptance.parse_baseline_gate(text)

    def test_a_clean_shard_parses_to_zero_new(self):
        gate = self.gate(
            "== baseline gate ==\nNEW FAILURES:\n"
            "13 failing, 0 new, 0 flaky, 0 listed-but-passing\n")
        self.assertTrue(gate["parsed"])
        self.assertEqual(gate["new"], ())
        self.assertEqual(gate["failing"], 13)

    def test_a_shard_with_new_failures_lists_each_id(self):
        gate = self.gate(
            "== baseline gate ==\nNEW FAILURES:\n"
            "  tests/test_a.py::test_one\n"
            "  tests/test_b.py::test_two\n"
            "26 failing, 2 new, 0 flaky, 0 listed-but-passing\n")
        self.assertEqual(gate["new"], ("tests/test_a.py::test_one",
                                       "tests/test_b.py::test_two"))

    def test_a_flaky_entry_is_not_a_new_failure(self):
        # The gate prints `FLAKY (passed on retry):` directly above the summary
        # when nothing is new. Counting that entry as a new failure makes the
        # shard "count more NEW FAILURES than it listed" and the whole run
        # unparsed — the exact opposite of what the line says.
        gate = self.gate(
            "== baseline gate ==\n"
            "FLAKY (passed on retry):\n"
            "  tests/test_x.py::Flaky::test_retried\n"
            "25 failing, 0 new, 1 flaky, 0 listed-but-passing\n")
        self.assertTrue(gate["parsed"])
        self.assertEqual(gate["new"], ())
        self.assertEqual(gate["new_count"], 0)

    def test_new_failures_and_flaky_do_not_merge(self):
        gate = self.gate(
            "== baseline gate ==\n"
            "NEW FAILURES:\n"
            "  tests/test_a.py::test_one\n"
            "FLAKY (passed on retry):\n"
            "  tests/test_b.py::test_two\n"
            "3 failing, 1 new, 1 flaky, 0 listed-but-passing\n")
        self.assertEqual(gate["new"], ("tests/test_a.py::test_one",))

    def test_output_with_no_gate_is_unparsed_not_green(self):
        gate = self.gate("pytest crashed before the gate\n")
        self.assertFalse(gate["parsed"])
        self.assertEqual(gate["new"], ())

    def test_the_date_window_test_is_attributed_not_waved_through(self):
        result = self.acceptance.attribute_new_failures(
            ("tests/test_direct_lark_notify.py::CardCreateThenPatchTest::"
             "test_create_returns_an_id_and_patch_names_it",),
            known={DATE_WINDOW_TEST: "date-window fixture"})
        self.assertEqual(result["unattributed"], ())
        self.assertIn(DATE_WINDOW_TEST, result["attributed"])

    def test_an_unlisted_new_failure_stays_unattributed(self):
        result = self.acceptance.attribute_new_failures(
            ("tests/test_mine.py::test_broken",),
            known={DATE_WINDOW_TEST: "date-window fixture"})
        self.assertEqual(result["unattributed"],
                         ("tests/test_mine.py::test_broken",))

    def test_a_failing_shard_with_only_known_failures_is_green_or_attributed(self):
        verdict = self.acceptance.classify_ci_report({
            "head": "a" * 40,
            "gates": [{"shard": "1/4", "parsed": True, "failing": 13,
                       "new": [], "flaky": 0, "listed_but_passing": 0}],
        }, known={})
        self.assertEqual(verdict["state"], "GREEN")

    def test_an_unparsed_shard_is_blocked_not_green(self):
        verdict = self.acceptance.classify_ci_report({
            "head": "a" * 40,
            "gates": [{"shard": "1/4", "parsed": False, "failing": None,
                       "new": [], "flaky": 0, "listed_but_passing": 0}],
        }, known={})
        self.assertEqual(verdict["state"], "BLOCKED")
        self.assertEqual(verdict["code"], "BLOCKED_CI_UNPARSED")

    def test_a_moved_head_is_blocked(self):
        verdict = self.acceptance.classify_ci_report({
            "head": "b" * 40,
            "gates": [{"shard": "1/4", "parsed": True, "failing": 0,
                       "new": [], "flaky": 0, "listed_but_passing": 0}],
            "expected_head": "a" * 40,
        }, known={})
        self.assertEqual(verdict["state"], "BLOCKED")
        self.assertEqual(verdict["code"], "BLOCKED_HEAD_MOVED")

    def test_a_runner_timestamp_does_not_hide_the_gate(self):
        # GitHub prefixes every log line with `2026-10-29T13:12:14.8643Z `. An
        # anchored summary regex then never matches, and a clean shard reads as
        # "printed no gate summary" — the exact false negative this gate exists
        # to avoid.
        raw = (
            "2026-10-29T13:12:14.8643423Z == baseline gate ==\n"
            "2026-10-29T13:12:14.8643423Z NEW FAILURES:\n"
            "2026-10-29T13:12:14.8643423Z   tests/test_a.py::test_one\n"
            "2026-10-29T13:12:14.8643423Z "
            "8 failing, 1 new, 0 flaky, 0 listed-but-passing\n")
        gate = self.gate(self.acceptance.strip_runner_prefix(raw))
        self.assertTrue(gate["parsed"])
        self.assertEqual(gate["failing"], 8)
        self.assertEqual(gate["new"], ("tests/test_a.py::test_one",))

    def test_a_failed_shard_job_with_a_clean_gate_is_not_a_check_failure(self):
        # The baseline gate makes a shard exit non-zero *because* the baseline
        # has failures in it. Reading the job's conclusion instead of its own
        # summary turns every hosted run red.
        verdict = self.acceptance.classify_ci_report({
            "head": "a" * 40,
            "checks": [{"name": "Suite (shard 3/4)", "status": "completed",
                        "conclusion": "failure"}],
            "gates": [{"shard": "3/4", "parsed": True, "failing": 8,
                       "new": [], "flaky": 0, "listed_but_passing": 0}],
        }, known={})
        self.assertEqual(verdict["state"], "GREEN")

    def test_an_attributed_new_failure_explains_its_shard_failure(self):
        verdict = self.acceptance.classify_ci_report({
            "head": "a" * 40,
            "checks": [{"name": "Suite (shard 4/4)", "status": "completed",
                        "conclusion": "failure"}],
            "gates": [{"shard": "4/4", "parsed": True, "failing": 9,
                       "new": [DATE_WINDOW_TEST], "flaky": 0,
                       "listed_but_passing": 0}],
        }, known={DATE_WINDOW_TEST: "date-window fixture"})
        self.assertEqual(verdict["state"], "GREEN")

    def test_a_failed_job_with_no_gate_of_its_own_blocks(self):
        verdict = self.acceptance.classify_ci_report({
            "head": "a" * 40,
            "checks": [{"name": "Claude Code plugin matches its source",
                        "status": "completed", "conclusion": "failure"}],
            "gates": [{"shard": "1/4", "parsed": True, "failing": 0,
                       "new": [], "flaky": 0, "listed_but_passing": 0}],
        }, known={})
        self.assertEqual(verdict["state"], "BLOCKED")
        self.assertEqual(verdict["code"], "BLOCKED_CI_CHECK")


class ArtifactsAreDiscoveredAndDigestsVerified(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def test_a_published_artifact_is_found_by_name_and_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            digests = {}
            for name in ("fleet-runtime-base-1.0.0+wave18.tar.gz",
                         "fleet-hub-1.0.0+wave18.tar.gz",
                         "fleet-authority-1.0.0+wave18.tar.gz",
                         "fleet-node-1.0.0+wave18.tar.gz",
                         "fleet-next-composition-1.0.0+wave18.tar.gz"):
                digests[name] = make_tarball(tmp / name)
            pin = fixture_pin(build_fixture_repo(tmp / "r"),
                              artifact_dir=tmp, artifact_digests=digests)
            pin["artifact"]["expected_names"] = list(digests)
            pin["artifact"]["search_paths"] = [str(tmp)]
            found = self.acceptance.discover_artifacts(
                pin, repo=tmp / "r", artifact_dir=tmp)
            self.assertEqual({a.name for a in found}, set(digests))
            for artifact in found:
                self.assertTrue(self.acceptance.verify_artifact_digest(
                    artifact.path, "sha256:" + digests[artifact.name]))

    def test_no_artifact_is_blocked_artifact_not_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info, artifact_digests={
                "fleet-runtime-base-1.0.0+wave18.tar.gz": "0" * 64})
            pin["artifact"]["search_paths"] = [str(tmp / "empty")]
            layer = self.acceptance.build_artifact_layer(
                pin, repo=info["repo"], artifact_dir=tmp / "empty")
            self.assertEqual(layer.state, "BLOCKED")
            self.assertEqual(layer.code, "BLOCKED_ARTIFACT")

    def test_a_digest_that_does_not_match_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            path = tmp / "fleet-runtime-base-1.0.0+wave18.tar.gz"
            make_tarball(path)
            self.assertFalse(self.acceptance.verify_artifact_digest(
                path, "sha256:" + "0" * 64))

    def test_an_artifact_that_appears_later_flips_blocked_to_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            drop = tmp / "drop"
            drop.mkdir()
            name = "fleet-runtime-base-1.0.0+wave18.tar.gz"
            pin = fixture_pin(info, artifact_digests={name: "0" * 64})
            pin["artifact"]["search_paths"] = [str(drop)]
            first = self.acceptance.build_artifact_layer(
                pin, repo=info["repo"], artifact_dir=drop)
            self.assertEqual(first.code, "BLOCKED_ARTIFACT")
            digest = make_tarball(drop / name)
            pin["artifact"]["expected_sha256"][name] = digest
            second = self.acceptance.build_artifact_layer(
                pin, repo=info["repo"], artifact_dir=drop)
            self.assertEqual(second.state, "PASS")
            self.assertEqual(second.evidence["artifacts"][name],
                             "sha256:" + digest)

    def test_a_rebuilt_artifact_is_labelled_rebuild_not_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            name = "fleet-runtime-base-1.0.0+wave18.tar.gz"
            digest = make_tarball(tmp / name)
            pin = fixture_pin(info, artifact_digests={name: digest})
            pin["artifact"]["expected_names"] = [name]
            layer = self.acceptance.build_artifact_layer(
                pin, repo=info["repo"], artifact_dir=tmp)
            self.assertEqual(layer.state, "PASS")
            self.assertEqual(layer.evidence["mode"], "ARTIFACT_BYTES")


class SourceAndUnitLayersReadTheRepository(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def test_source_layer_verifies_ancestry_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            layer = self.acceptance.build_source_layer(
                info["repo"], pin, workdir=tmp / "w")
            self.assertEqual(layer.state, "PASS", layer.reasons)
            self.assertEqual(layer.evidence["units"]["fixture-unit"]["commit"],
                             info["unit"])
            self.assertEqual(layer.evidence["units"]["fixture-unit"]["files"], 5)

    def test_a_unit_that_does_not_descend_from_the_base_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin["base"]["commit"] = info["unit"]
            pin["units"][0]["commit"] = info["base"]
            layer = self.acceptance.build_source_layer(
                info["repo"], pin, workdir=tmp / "w")
            self.assertEqual(layer.state, "BLOCKED")
            self.assertIn("ANCESTRY", layer.code)

    def test_a_missing_object_is_named_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin["units"][0]["commit"] = "f" * 40
            layer = self.acceptance.build_source_layer(
                info["repo"], pin, workdir=tmp / "w")
            self.assertEqual(layer.state, "BLOCKED")
            self.assertIn("MISSING", layer.code)

    def test_the_unit_layer_runs_the_pinned_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            layer = self.acceptance.build_unit_layer(
                info["repo"], pin, workdir=tmp / "w", platform="linux")
            self.assertEqual(layer.state, "PASS", layer.reasons)
            self.assertEqual(layer.evidence["units"]["fixture-unit"]["failed"], 0)
            self.assertGreaterEqual(
                layer.evidence["units"]["fixture-unit"]["passed"], 1)

    def test_a_unit_that_cannot_run_here_is_not_counted_as_passing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin["units"][0]["platforms"] = ["linux"]
            layer = self.acceptance.build_unit_layer(
                info["repo"], pin, workdir=tmp / "w", platform="win32")
            self.assertNotEqual(layer.state, "PASS")
            self.assertEqual(layer.code, "NOT_RUN_PLATFORM")

    def test_a_failing_unit_test_is_a_blocked_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            repo = info["repo"]
            # The broken test has to be *at the pinned commit*: a failure that
            # only exists on another branch is not what the layer clones.
            run_git(repo, "checkout", "-q", "unit")
            (repo / "tests" / "test_fixture_unit.py").write_text(
                "def test_the_fixture_unit_ticks():\n    assert 1 + 1 == 3\n",
                encoding="utf-8")
            run_git(repo, "add", "-A")
            run_git(repo, "commit", "-q", "-m", "break")
            pin = fixture_pin(info)
            pin["units"][0]["commit"] = run_git(repo, "rev-parse", "HEAD")
            layer = self.acceptance.build_unit_layer(
                repo, pin, workdir=tmp / "w", platform="linux")
            self.assertEqual(layer.state, "BLOCKED")
            self.assertEqual(layer.code, "BLOCKED_UNIT_TEST")


class TheDrillCompletesOnAFixtureWorld(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def drill(self, tmp: Path):
        info = build_fixture_repo(tmp / "r")
        pin = fixture_pin(info)
        pin_path = write_pin(tmp / "pin.json", pin)
        layer = self.acceptance.build_survival_layer(
            info["repo"], pin, workdir=tmp / "world",
            pin_path=pin_path, engine_path=FIXTURE_ENGINE,
            preflight_path=FIXTURE_ENGINE, timeout=120)
        return layer, pin

    def test_the_lifecycle_runs_in_at_least_three_independent_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            self.assertEqual(layer.state, "PASS", layer.reasons)
            processes = layer.evidence["processes"]
            self.assertGreaterEqual(len(processes), 3)
            pids = {entry["pid"] for entry in processes}
            self.assertGreaterEqual(len(pids), 3)
            self.assertNotIn(os.getpid(), pids)
            self.assertEqual([entry["step"] for entry in processes],
                             ["seed", "install", "observe", "failed-upgrade",
                              "rollback", "probe"])

    def test_the_legacy_contract_is_identical_after_every_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            snapshots = layer.evidence["legacy"]["snapshots"]
            baseline = layer.evidence["legacy"]["baseline"]
            for step, snapshot in snapshots.items():
                self.assertEqual(snapshot, baseline, step)
            for field in ("fleet_instance_id", "route_id", "boot_id", "port",
                          "backend_url", "oauth_audience", "db", "state_root",
                          "lock", "clock"):
                self.assertIn(field, baseline)

    def test_no_step_writes_the_legacy_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            self.assertEqual(
                layer.evidence["legacy"]["tree_digest_unchanged"], True)
            self.assertEqual(layer.evidence["legacy"]["deliveries"], ["delivery-1"])

    def test_the_observer_never_says_success_without_native_activity(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            observer = layer.evidence["observer"]
            self.assertTrue(observer["fake_observer"])
            self.assertEqual(observer["completed_without_activity"]["status"],
                             "UNKNOWN")
            self.assertEqual(observer["offline"]["status"], "OFFLINE")
            self.assertEqual(
                observer["typed_subject_without_authorization"]["code"],
                "BLOCKED_SUBJECT")
            self.assertEqual(observer["native_activity"]["status"],
                             "TERMINAL_VERIFIED")

    def test_the_package_is_pinned_by_a_composition_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            package = layer.evidence["package"]
            self.assertRegex(package["commit"], r"^[0-9a-f]{40}$")
            self.assertTrue(package["manifest"].startswith("sha256:"))
            self.assertEqual(package["files"], 4)

    def test_the_rollback_removes_exactly_the_receipt_owned_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            layer, _pin = self.drill(Path(tmp))
            rollback = layer.evidence["rollback"]
            self.assertEqual(rollback["deleted"], sorted(rollback["owned"]))
            self.assertTrue(rollback["receipt_gone"])
            self.assertEqual(rollback["backup_gone"], True)


class EveryInjectionRefusesWithItsOwnCode(unittest.TestCase):
    """One code per shape, so a gate that refuses everything cannot pass."""

    @classmethod
    def setUpClass(cls):
        cls.acceptance = load_acceptance()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        info = build_fixture_repo(cls.tmp / "r")
        pin = fixture_pin(info)
        pin_path = write_pin(cls.tmp / "pin.json", pin)
        cls.layer = cls.acceptance.build_survival_layer(
            info["repo"], pin, workdir=cls.tmp / "world",
            pin_path=pin_path, engine_path=FIXTURE_ENGINE,
            preflight_path=FIXTURE_ENGINE, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def injection(self, name):
        return self.layer.evidence["injections"][name]

    def test_bad_sha_is_refused(self):
        self.assertEqual(self.injection("bad_sha")["code"], "BLOCKED_SOURCE_DIGEST")

    def test_a_foreign_boot_is_refused(self):
        self.assertEqual(self.injection("foreign_boot")["code"],
                         "BLOCKED_BOOT_MISMATCH")

    def test_an_unauthorized_device_is_refused(self):
        self.assertEqual(self.injection("unauthorized_device")["code"],
                         "BLOCKED_IDENTITY_MISMATCH")

    def test_a_missing_receipt_is_refused(self):
        self.assertEqual(self.injection("missing_receipt")["code"],
                         "BLOCKED_NO_RECEIPT")

    def test_a_partial_write_is_named_and_blocks_revoke(self):
        partial = self.injection("partial_write")
        self.assertEqual(partial["outcome"], "PARTIAL_WRITE")
        self.assertEqual(partial["revoke_after_partial"]["code"],
                         "BLOCKED_FOREIGN_PRESENT")
        self.assertEqual(partial["receipt_written"], False)

    def test_a_shared_root_collision_is_refused(self):
        self.assertEqual(self.injection("legacy_root_collision")["code"],
                         "BLOCKED_LEGACY_ROOT")

    def test_a_resource_collision_is_refused(self):
        self.assertEqual(self.injection("resource_collision")["code"],
                         "BLOCKED_RESOURCE_COLLISION")

    def test_an_offline_descriptor_build_is_refused_not_faked(self):
        offline = self.injection("offline")
        self.assertEqual(offline["code"], "OFFLINE_REFUSED")
        self.assertEqual(offline["descriptor"], None)

    def test_a_grant_for_another_action_is_refused(self):
        self.assertEqual(self.injection("no_grant")["code"], "BLOCKED_NO_GRANT")

    def test_a_floating_version_is_refused(self):
        self.assertEqual(self.injection("not_pinned")["code"],
                         "BLOCKED_NOT_PINNED")

    def test_a_failed_upgrade_leaves_the_root_with_its_receipt(self):
        upgrade = self.layer.evidence["failed_upgrade"]
        self.assertEqual(upgrade["receipt_still_names"], "0.1.0+wave18")
        self.assertEqual(upgrade["installed_bytes_unchanged"], True)


class NoFakeGreen(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def test_an_engine_that_always_says_ok_does_not_make_a_green_drill(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin_path = write_pin(tmp / "pin.json", pin)
            fake = tmp / "always_ok_engine.py"
            fake.write_text(
                "class _Result:\n"
                "    def __init__(self, decision='OK', code='OK', reasons=(),\n"
                "                 written=(), deleted=(), receipt_path=''):\n"
                "        self.decision = decision; self.code = code\n"
                "        self.reasons = reasons; self.written = written\n"
                "        self.deleted = deleted; self.receipt_path = receipt_path\n"
                "def install(*a, **k):\n"
                "    return _Result()\n"
                "def revoke(*a, **k):\n"
                "    return _Result()\n"
                "def restore(*a, **k):\n"
                "    return _Result()\n"
                "def read_receipt(root):\n"
                "    return None\n"
                "def descriptor_from_git(*a, **k):\n"
                "    raise RuntimeError('no')\n"
                "class Grant:\n"
                "    def __init__(self, *a, **k): pass\n"
                "    def covers(self, action): return True\n"
                "class Descriptor:\n"
                "    def __init__(self, *a, **k):\n"
                "        self.files = (); self.version = 'x'\n"
                "    def manifest_digest(self): return 'sha256:' + '0' * 64\n"
                "def resource_matrix(*a, **k):\n"
                "    return {'decision': 'OK', 'collisions': []}\n",
                encoding="utf-8")
            layer = self.acceptance.build_survival_layer(
                info["repo"], pin, workdir=tmp / "world",
                pin_path=pin_path, engine_path=fake,
                preflight_path=FIXTURE_ENGINE, timeout=60)
            self.assertNotEqual(layer.state, "PASS")
            self.assertIn("BLOCKED", layer.code)

    def test_readiness_cannot_be_forced_by_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin_path = write_pin(tmp / "pin.json", pin)
            os.environ["FLEET_NEXT_WAVE18_FAKE_GREEN"] = "1"
            try:
                verdict = self.acceptance.run_acceptance(
                    info["repo"], pin_path=pin_path, workdir=tmp / "w",
                    online=False, engine_path=FIXTURE_ENGINE,
                    preflight_path=FIXTURE_ENGINE, ci_reader=None,
                    platform="linux", unit_timeout=120)
            finally:
                os.environ.pop("FLEET_NEXT_WAVE18_FAKE_GREEN", None)
            self.assertNotEqual(verdict.state, "READY")
            self.assertNotIn("FAKE_GREEN", verdict.code)

    def test_a_drill_step_that_did_not_run_cannot_report_pass(self):
        layer = self.acceptance.LayerResult(
            name="LEGACY_SURVIVAL", state="PASS", code="OK",
            evidence={"processes": []})
        verdict = self.acceptance.aggregate([
            self.acceptance.LayerResult(name, "PASS", "OK")
            for name in self.acceptance.LAYERS[:-2]
        ] + [layer, self.acceptance.build_real_device_layer()])
        self.assertNotEqual(verdict.state, "READY")
        self.assertEqual(verdict.state, "BLOCKED")

    def test_the_source_tree_is_not_written_by_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            info = build_fixture_repo(tmp / "r")
            pin = fixture_pin(info)
            pin_path = write_pin(tmp / "pin.json", pin)
            before = run_git(info["repo"], "status", "--porcelain")
            self.acceptance.run_acceptance(
                info["repo"], pin_path=pin_path, workdir=tmp / "w",
                online=False, engine_path=FIXTURE_ENGINE,
                preflight_path=FIXTURE_ENGINE,
                ci_reader=green_ci_reader(pin), platform="linux",
                unit_timeout=120)
            after = run_git(info["repo"], "status", "--porcelain")
            self.assertEqual(before, after)


class ADrillStepsStdoutSurvivesTheConsoleCodepage(unittest.TestCase):
    """A child's stdout encoding is the host's, not UTF-8.

    The drill reports why an upgrade failed by writing the exception's own
    message, and on Windows that message is localized. Under a legacy console
    codepage the child encodes those characters on the way out and the UTF-8
    parent decodes the result into replacement characters — the drill's own
    record of the failure becomes unreadable while still looking like evidence.
    The step's report therefore has to survive a non-UTF-8 stdout.
    """

    STEP_PATH = HERE / "drill" / "drill_step.py"
    # A FileExistsError says this on a zh-CN Windows.
    MESSAGE = "系统找不到指定的路径。"

    def run_emit(self, tmp: Path) -> tuple[bytes, Path]:
        out = tmp / "out"
        out.mkdir()
        program = (
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('drill_step', {str(self.STEP_PATH)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules['drill_step'] = module\n"
            "spec.loader.exec_module(module)\n"
            f"module._emit(Path({str(out)!r}), 'step', "
            f"{{'step': 'partial_write', 'error': {self.MESSAGE!r}}})\n"
        )
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "gbk"  # the child writes GBK, the parent reads UTF-8
        proc = subprocess.run([sys.executable, "-c", program], cwd=str(tmp),
                              capture_output=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return proc.stdout, out / "step.json"

    def test_the_parent_recovers_the_exact_message_from_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            stdout, _ = self.run_emit(Path(tmp))
            line = stdout.decode("utf-8").strip().splitlines()[-1]
            self.assertEqual(json.loads(line)["error"], self.MESSAGE)

    def test_the_step_file_keeps_the_real_characters(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, step_file = self.run_emit(Path(tmp))
            report = json.loads(step_file.read_text(encoding="utf-8"))
            self.assertEqual(report["error"], self.MESSAGE)


class TheCommittedPinIsTheWave18ReleaseCandidate(unittest.TestCase):
    def setUp(self):
        self.acceptance = load_acceptance()

    def test_the_pin_is_this_files_wave_and_base(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        self.assertEqual(pin["wave"], "wave18")
        self.assertEqual(pin["created"], "2026-10-29")
        self.assertEqual(pin["base"]["branch"], "main")

    def test_the_release_package_carries_its_own_revoke_entrypoint(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        files = {member["path"] for member in pin["release_package"]["files"]}
        self.assertIn(pin["release_package"]["revoke_entrypoint"], files)

    def test_the_six_heads_are_the_ones_the_owner_named(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        commits = {unit["id"]: unit["commit"] for unit in pin["units"]}
        self.assertEqual(commits["rc-wave16"],
                         "80220a4ad1b009946f11bda0ce96dd8ff8e05468")
        self.assertEqual(commits["runtime-abi"],
                         "b7f1f6134a1e3dcee4b916068c693ff059c097bd")
        self.assertEqual(commits["monitor-p0"],
                         "6286bfbc885771a45415efcb33405bb21a841fde")
        self.assertEqual(commits["hub-node-cut"],
                         "3512c551574a94ad360f8bde6435bdf07c729167")
        self.assertEqual(commits["sidecar-revoke"],
                         "30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7")
        self.assertEqual(commits["release-preflight"],
                         "8e6be6b1177405c58ebf980dc68c0f0475b76e89")

    def test_the_pin_says_the_artifact_is_not_published_yet(self):
        pin = self.acceptance.load_pin(PIN_PATH)
        artifact = pin["artifact"]
        self.assertFalse(artifact["published"])
        self.assertEqual(artifact["expected_names"], [])
        self.assertTrue(artifact["evidence"]["path"].endswith("ARTIFACTS.sha256"))


class TheRealDeviceGateIsNotRun(unittest.TestCase):
    def test_the_layer_is_a_constant_not_run(self):
        acceptance = load_acceptance()
        layer = acceptance.build_real_device_layer()
        self.assertEqual(layer.state, "NOT_RUN")
        self.assertEqual(layer.evidence["executed"], False)
        self.assertTrue(layer.evidence["requires"])

    def test_no_argument_turns_it_into_a_device_pass(self):
        acceptance = load_acceptance()
        for kwargs in ({"allow": True}, {"allow": True, "authorization": "x"}):
            layer = acceptance.build_real_device_layer(**kwargs)
            self.assertEqual(layer.state, "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
