#!/usr/bin/env python3
"""§169 · the remote pytest runner is bounded by the kernel, not by ssh.

WHY THIS FILE EXISTS
--------------------
On 2026-10-14 a bare `python -m pytest -q -p no:cacheprovider` started from
`$HOME` on this host recursed into the data root, and PID 288992 held ~145 GiB
of a 247 GiB machine for about nineteen hours with swap exhausted. The launcher
that started it believed it was protected, twice over: it wrapped the local
`ssh` in `timeout`, and it had `trap 'rm -rf "$SB"' EXIT` on the remote side.
Neither is a bound. `timeout` on the local `ssh` bounds the *client*; the
remote process was already reparented away from that session. `trap EXIT` runs a
cleanup when the shell exits, and the shell exited early; it deleted a
directory and left the process. A protection that is not enforced by the kernel
is a story about a protection.

So the thing this file tests is deliberately narrow and mechanical: the run is
placed inside a systemd *user* cgroup, the cgroup is READ BACK while the run is
alive, and anything that disagrees with the request ends the run as a failure
rather than as a green test report. The two properties worth more than the
rest, and the ones every test below is an instance of:

    the bound is on the target host's kernel, and it is verified there
    uncertainty ends the run — it never degrades into a success

WHAT IS NOT TESTED HERE
-----------------------
Nothing in this file allocates a large amount of memory, and nothing replays the
incident. The runaway is not a fixture; it is the reason the fixture is a fake.
Every orchestration test drives a `FakeSystemd` that answers with recorded
shapes, so a bug in this module is a bug about *policy*, never about whether
this test itself is safe to run on a shared host.

ONE REAL TEST, AND WHY IT IS SMALL
-----------------------------------
`test_a_real_user_unit_is_read_back_from_the_kernel` actually calls
`systemd-run --user` and reads the cgroup files. It is the only test that could
notice the difference between "systemd accepted the property" and "the kernel
has the limit", and that difference is the whole subject. It is therefore kept
to `/bin/true` under a 32 MiB cap for under a second, is skipped when the user
manager is unavailable, and is disabled by
`SERVEROS_CONSTRAINED_RUNNER_NO_SYSTEMD=1`. A test proving a memory bound must
not itself be the thing that needs one.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load():
    """Load the module by path, as the sibling runtime tests do.

    `runtime/` is not a package on `sys.path`; every test that reaches a
    runtime module loads it this way, so this module is reachable from the
    suite without a second import convention.
    """
    name = "serveros_runtime_constrained_runner"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, RUNTIME / "constrained_runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cr():
    return _load()


GIB = 1024 ** 3


# ---------------------------------------------------------------------------
# The profiles. This is policy, and policy is the part a runaway argues with.
# ---------------------------------------------------------------------------


class TestProfiles:
    def test_the_default_profile_is_the_one_in_the_issue(self, cr):
        limits = cr.resolve_limits("default")
        assert limits["memory_high_bytes"] == 3 * GIB
        assert limits["memory_max_bytes"] == 4 * GIB
        assert limits["cpu_quota_percent"] == 400
        assert limits["runtime_max_sec"] == 600

    def test_no_profile_may_exceed_the_escalation_ceiling(self, cr):
        """8/16 GiB and 30 min are the authorised maximum, so they are a cap."""
        for name in cr.PROFILES:
            limits = cr.resolve_limits(name, authorized=True)
            assert limits["memory_max_bytes"] <= 16 * GIB, name
            assert limits["runtime_max_sec"] <= 1800, name

    def test_an_unlisted_profile_is_refused_rather_than_defaulted(self, cr):
        """The dangerous default is not "4 GiB" — it is "whatever you asked for"."""
        with pytest.raises(cr.LimitsRefused):
            cr.resolve_limits("no-such-profile")

    def test_elevation_is_refused_without_authorisation(self, cr):
        with pytest.raises(cr.LimitsRefused):
            cr.resolve_limits("large-16")

    def test_elevation_is_granted_with_authorisation(self, cr):
        limits = cr.resolve_limits("large-16", authorized=True)
        assert limits["memory_max_bytes"] == 16 * GIB
        assert limits["runtime_max_sec"] == 1800

    def test_the_default_profile_needs_no_authorisation(self, cr):
        """The common path must not be the one that needs a ceremony."""
        assert cr.resolve_limits("default", authorized=False)["memory_max_bytes"] == 4 * GIB


# ---------------------------------------------------------------------------
# Where pytest is allowed to be started. The incident was a property of the
# directory, not of the arguments: `testpaths = tests` only applies from the
# repository root, and from `$HOME` there is no repository root to find.
# ---------------------------------------------------------------------------


class TestWorktree:
    def _worktree(self, root: Path) -> Path:
        wt = root / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /nowhere\n")
        (wt / "tests").mkdir()
        return wt

    def test_a_real_worktree_is_accepted(self, cr, tmp_path):
        wt = self._worktree(tmp_path)
        assert cr.validate_worktree(str(wt), home=tmp_path / "home") == wt.resolve()

    def test_relative_is_refused(self, cr, tmp_path):
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree("wt", home=tmp_path / "home")

    def test_a_missing_directory_is_refused(self, cr, tmp_path):
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(tmp_path / "absent"), home=tmp_path / "home")

    def test_a_directory_without_a_git_root_is_refused(self, cr, tmp_path):
        """This is the guard against `$HOME`: home is not a worktree."""
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(plain), home=tmp_path / "home")

    def test_home_itself_is_refused_even_when_it_holds_a_git_dir(self, cr, tmp_path):
        home = tmp_path / "home"
        (home / ".git").mkdir(parents=True)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(home), home=home)

    def test_an_ancestor_of_home_is_refused(self, cr, tmp_path):
        home = tmp_path / "a" / "b" / "home"
        (tmp_path / ".git").mkdir(exist_ok=True)
        home.mkdir(parents=True)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(tmp_path), home=home)

    def test_a_symlinked_worktree_is_refused(self, cr, tmp_path):
        wt = self._worktree(tmp_path)
        link = tmp_path / "link"
        link.symlink_to(wt)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(link), home=tmp_path / "home")

    def test_the_worktrees_root_itself_is_refused(self, cr, tmp_path):
        """`worktrees/` is a container of worktrees, and has no `.git` of its own."""
        root = tmp_path / "worktrees"
        root.mkdir()
        (root / "one").mkdir()
        (root / "one" / ".git").write_text("gitdir: /nowhere\n")
        with pytest.raises(cr.RunnerRefused):
            cr.validate_worktree(str(root), home=tmp_path / "home")


# ---------------------------------------------------------------------------
# What pytest may be pointed at. Explicit `tests/` paths are the rule the host
# already carries in memory; this is that rule as code rather than as habit.
# ---------------------------------------------------------------------------


class TestSelection:
    def _tree(self, root: Path) -> Path:
        wt = root / "wt"
        (wt / "tests" / "sub").mkdir(parents=True)
        (wt / "tests" / "test_a.py").write_text("")
        (wt / "tests" / "sub" / "test_b.py").write_text("")
        (wt / "tests" / "conftest.py").write_text("")
        (wt / "runtime.py").write_text("")
        return wt

    def test_an_empty_selection_is_refused(self, cr, tmp_path):
        """No arguments means "collect everything", which is the runaway."""
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=False, paths=[])

    def test_an_explicit_test_file_is_accepted(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        assert cr.validate_selection(
            wt, all_tests=False, paths=["tests/test_a.py"]) == ["tests/test_a.py"]

    def test_a_path_outside_tests_is_refused(self, cr, tmp_path):
        """`runtime/` holds modules, and one of them is why this rule exists."""
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=False, paths=["runtime.py"])

    def test_traversal_is_refused_even_when_it_lands_inside_tests(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(
                wt, all_tests=False, paths=["tests/../tests/test_a.py"])

    def test_a_non_test_module_under_tests_is_refused(self, cr, tmp_path):
        """`conftest.py` is under `tests/` and is not a test to run."""
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=False, paths=["tests/conftest.py"])

    def test_a_missing_path_is_refused(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=False, paths=["tests/test_nope.py"])

    def test_an_absolute_path_inside_the_worktree_is_normalised(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        got = cr.validate_selection(
            wt, all_tests=False, paths=[str(wt / "tests" / "test_a.py")])
        assert got == ["tests/test_a.py"]

    def test_an_absolute_path_outside_the_worktree_is_refused(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        other = tmp_path / "elsewhere" / "tests" / "test_c.py"
        other.parent.mkdir(parents=True)
        other.write_text("")
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=False, paths=[str(other)])

    def test_a_directory_under_tests_is_accepted(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        assert cr.validate_selection(
            wt, all_tests=False, paths=["tests/sub"]) == ["tests/sub"]

    def test_the_whole_suite_is_allowed_only_by_asking_for_it(self, cr, tmp_path):
        wt = self._tree(tmp_path)
        assert cr.validate_selection(wt, all_tests=True, paths=[]) == ["tests"]

    def test_asking_for_the_whole_suite_and_a_path_is_refused(self, cr, tmp_path):
        """Two answers to one question is a caller that has not decided."""
        wt = self._tree(tmp_path)
        with pytest.raises(cr.RunnerRefused):
            cr.validate_selection(wt, all_tests=True, paths=["tests/test_a.py"])


# ---------------------------------------------------------------------------
# The launch argv. The envelope is the product; everything else is spelling.
# ---------------------------------------------------------------------------


class TestLaunchArgv:
    def _argv(self, cr, tmp_path, **overrides):
        kwargs = dict(
            unit="serveros-test-1-abc.service",
            limits=cr.resolve_limits("default"),
            worktree=tmp_path,
            pytest_bin="/venv/bin/pytest",
            test_args=["-q", "-p", "no:cacheprovider", "tests/test_a.py"],
            run_dir=tmp_path / "run",
            systemd_run_bin="/usr/bin/systemd-run",
        )
        kwargs.update(overrides)
        return cr.build_launch_argv(**kwargs)

    def test_it_is_a_user_transient_service(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        assert argv[0] == "/usr/bin/systemd-run"
        assert "--user" in argv
        assert "--unit=serveros-test-1-abc.service" in argv
        assert "--scope" not in argv

    def test_the_four_envelope_properties_are_present(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        assert f"--property=MemoryHigh={3 * GIB}" in argv
        assert f"--property=MemoryMax={4 * GIB}" in argv
        assert "--property=CPUQuota=400%" in argv
        assert "--property=RuntimeMaxSec=600" in argv

    def test_the_whole_tree_is_in_one_kill_unit(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        assert "--property=KillMode=control-group" in argv

    def test_it_stops_for_a_moment_then_stops_for_good(self, cr, tmp_path):
        """TERM, then KILL after a bounded grace — never an unbounded wait."""
        argv = self._argv(cr, tmp_path)
        assert any(a == "--property=TimeoutStopSec=10" for a in argv)

    def test_output_goes_to_the_run_directory_not_to_the_journal(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        assert f"--property=StandardOutput=file:{tmp_path / 'run'}/stdout.log" in argv
        assert f"--property=StandardError=file:{tmp_path / 'run'}/stderr.log" in argv

    def test_the_working_directory_is_the_worktree(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        assert f"--property=WorkingDirectory={tmp_path}" in argv

    def test_no_property_ever_names_a_slice(self, cr, tmp_path):
        """The one thing the issue forbids outright: never cap a whole slice.

        `user.slice` and `root.slice` carry every other account on a shared
        host. A wrapper that set `MemoryMax` there would answer one runaway by
        turning it into everyone's problem, so the rule is structural: the
        builder emits no property whose name or value mentions a slice.
        """
        for name in cr.PROFILES:
            argv = self._argv(cr, tmp_path, limits=cr.resolve_limits(name, authorized=True))
            joined = " ".join(argv)
            assert ".slice" not in joined, name

    def test_the_command_comes_after_the_double_dash(self, cr, tmp_path):
        argv = self._argv(cr, tmp_path)
        idx = argv.index("--")
        assert argv[idx + 1] == "/venv/bin/pytest"
        assert argv[idx + 2:] == ["-q", "-p", "no:cacheprovider", "tests/test_a.py"]

    def test_a_tasks_cap_is_part_of_the_envelope(self, cr, tmp_path):
        """Memory bounds the size of the runaway; this bounds its width."""
        argv = self._argv(cr, tmp_path)
        assert any(a.startswith("--property=TasksMax=") for a in argv)

    def test_the_environment_is_not_inherited_wholesale(self, cr, tmp_path):
        """No `--setenv` of the caller's environment: a receipt must not carry secrets."""
        argv = self._argv(cr, tmp_path)
        assert not any(a.startswith("--setenv") for a in argv)


# ---------------------------------------------------------------------------
# Reading the kernel back. This is the half the incident's launcher lacked.
# ---------------------------------------------------------------------------


class TestShowParsing:
    def test_key_value_lines_become_a_mapping(self, cr):
        text = "ActiveState=active\nSubState=running\nMainPID=42\n"
        assert cr.parse_show(text) == {
            "ActiveState": "active", "SubState": "running", "MainPID": "42"}

    def test_an_unset_property_keeps_its_marker(self, cr):
        """`[not set]` and "absent" are different answers to the same question."""
        assert cr.parse_show("MemoryPeak=[not set]\n") == {"MemoryPeak": "[not set]"}


class TestExpectedEnvelope:
    def test_the_expected_cgroup_values_are_the_kernel_units(self, cr):
        expected = cr.expected_cgroup_values(cr.resolve_limits("default"))
        assert expected["memory.max"] == str(4 * GIB)
        assert expected["memory.high"] == str(3 * GIB)
        assert expected["cpu.max"] == "400000 100000"


class TestEnvelopeVerification:
    def test_a_matching_envelope_produces_no_findings(self, cr):
        expected = cr.expected_cgroup_values(cr.resolve_limits("default"))
        assert cr.verify_envelope(expected, dict(expected)) == []

    def test_an_unlimited_memory_max_is_a_finding(self, cr):
        """`max` is what the kernel writes when nothing bounded it."""
        expected = cr.expected_cgroup_values(cr.resolve_limits("default"))
        observed = dict(expected, **{"memory.max": "max"})
        findings = cr.verify_envelope(expected, observed)
        assert [f["code"] for f in findings] == ["LIMIT_NOT_APPLIED"]
        assert findings[0]["property"] == "memory.max"

    def test_a_missing_file_is_uncertainty_not_agreement(self, cr):
        """A read that failed must never read as a limit that held."""
        expected = cr.expected_cgroup_values(cr.resolve_limits("default"))
        observed = {k: v for k, v in expected.items() if k != "cpu.max"}
        findings = cr.verify_envelope(expected, observed)
        assert [f["code"] for f in findings] == ["LIMIT_UNREADABLE"]
        assert findings[0]["property"] == "cpu.max"

    def test_every_disagreement_is_reported_not_just_the_first(self, cr):
        expected = cr.expected_cgroup_values(cr.resolve_limits("default"))
        observed = dict(expected, **{"memory.max": "max", "cpu.max": "max 100000"})
        assert len(cr.verify_envelope(expected, observed)) == 2


class TestTreeVerification:
    UNIT_CG = "/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"

    def test_the_main_process_in_the_unit_cgroup_is_verified(self, cr):
        assert cr.verify_tree(self.UNIT_CG, 42, f"0::{self.UNIT_CG}") == []

    def test_a_descendant_of_the_unit_cgroup_is_verified(self, cr):
        """A child in a SUB-cgroup is the normal shape, not a violation."""
        assert cr.verify_tree(self.UNIT_CG, 42, f"0::{self.UNIT_CG}/child") == []

    def test_a_process_in_another_unit_is_a_finding(self, cr):
        other = "/user.slice/user-1002.slice/user@1002.service/app.slice/other.service"
        findings = cr.verify_tree(self.UNIT_CG, 42, f"0::{other}")
        assert [f["code"] for f in findings] == ["PROCESS_OUTSIDE_UNIT_CGROUP"]

    def test_an_unreadable_proc_cgroup_is_a_finding(self, cr):
        """The process died between the two reads: that is not a pass."""
        findings = cr.verify_tree(self.UNIT_CG, 42, None)
        assert [f["code"] for f in findings] == ["PROCESS_CGROUP_UNREADABLE"]

    def test_missing_the_unit_cgroup_is_a_finding(self, cr):
        findings = cr.verify_tree("", 42, f"0::{self.UNIT_CG}")
        assert [f["code"] for f in findings] == ["UNIT_CGROUP_UNKNOWN"]

    def test_a_parent_of_the_unit_cgroup_is_not_inside_it(self, cr):
        """`app.slice` contains the unit and is not the unit."""
        findings = cr.verify_tree(self.UNIT_CG, 42,
                                  "0::/user.slice/user-1002.slice/user@1002.service/app.slice")
        assert [f["code"] for f in findings] == ["PROCESS_OUTSIDE_UNIT_CGROUP"]


class TestProcCgroup:
    def test_the_unified_line_is_read(self, cr):
        assert cr.parse_proc_cgroup("0::/a/b.service\n") == "/a/b.service"

    def test_the_v1_shaped_file_is_read_too(self, cr):
        assert cr.parse_proc_cgroup("11:memory:/a/b.service\n") == "/a/b.service"

    def test_an_empty_read_is_none(self, cr):
        assert cr.parse_proc_cgroup("") is None
        assert cr.parse_proc_cgroup(None) is None


# ---------------------------------------------------------------------------
# How a finished unit becomes an exit code. The rule: success is claimed only
# when the unit succeeded AND every claim about the cgroup held.
# ---------------------------------------------------------------------------


class TestOutcome:
    def test_a_successful_unit_yields_its_own_status(self, cr):
        assert cr.classify_result({"Result": "success", "ExecMainStatus": "0"}) == ("success", 0)
        assert cr.classify_result({"Result": "success", "ExecMainStatus": "1"}) == ("success", 1)

    def test_the_deadline_is_a_timeout_not_a_pass(self, cr):
        assert cr.classify_result({"Result": "timeout"})[0] == "timeout"

    def test_a_killed_unit_says_so(self, cr):
        assert cr.classify_result({"Result": "oom-kill"})[0] == "oom-kill"

    def test_a_signal_death_reports_128_plus_the_signal(self, cr):
        outcome, code = cr.classify_result(
            {"Result": "signal", "ExecMainCode": "1", "ExecMainStatus": "9"})
        assert outcome == "signal"
        assert code == 137

    def test_an_unknown_result_is_never_a_pass(self, cr):
        outcome, code = cr.classify_result({"Result": "protocol"})
        assert outcome == "unknown"
        assert code != 0

    def test_a_missing_result_is_never_a_pass(self, cr):
        assert cr.classify_result({})[1] != 0


# ---------------------------------------------------------------------------
# Names and hygiene.
# ---------------------------------------------------------------------------


class TestNamingAndHygiene:
    def test_a_unit_name_is_unique_per_run(self, cr):
        a = cr.unit_name(nonce="aaaa", epoch=1000.0)
        b = cr.unit_name(nonce="bbbb", epoch=1000.0)
        assert a != b

    def test_a_unit_name_is_a_service_and_never_a_slice(self, cr):
        name = cr.unit_name(nonce="aaaa", epoch=1000.0)
        assert name.endswith(".service")
        assert ".slice" not in name

    def test_a_unit_name_is_safe_for_systemd(self, cr):
        name = cr.unit_name(nonce="a1b2c3", epoch=1_760_000_000.0)
        assert cr.UNIT_NAME_RE.fullmatch(name)

    def test_a_secret_shaped_argument_is_redacted_from_a_receipt(self, cr):
        text = "run with --token=abcdef0123456789 and ghp_0123456789012345678901234567890123"
        out = cr.scrub(text)
        assert "abcdef0123456789" not in out
        assert "ghp_0123456789012345678901234567890123" not in out

    def test_ordinary_text_survives_scrubbing(self, cr):
        assert cr.scrub("tests/test_a.py -q") == "tests/test_a.py -q"


# ---------------------------------------------------------------------------
# The orchestrator, driven by a fake systemd. Every branch that can lie about
# a run is exercised here, because each one is a way the incident's launcher
# reported success while a process ran away.
# ---------------------------------------------------------------------------


class FakeSystemd:
    """A scripted `systemd-run`/`systemctl`. It answers; it never allocates."""

    def __init__(self):
        self.launches = []
        self.stopped = []
        self.reset = []
        self.available_value = (True, "ok")
        self.snapshots = []
        self.cgroup_files = {}
        self.proc_cgroup_value = None
        self.proc_starttime_value = 987654
        self.unit_alive = False
        self.launch_rc = 0
        self.launch_stderr = ""

    def available(self):
        return self.available_value

    def launch(self, argv):
        self.launches.append(list(argv))
        return subprocess.CompletedProcess(argv, self.launch_rc, "", self.launch_stderr)

    def unit_snapshot(self, unit):
        if not self.snapshots:
            return {"LoadState": "not-found"}
        snapshot = self.snapshots.pop(0)
        self.unit_alive = snapshot.get("ActiveState") in ("active", "activating")
        return snapshot

    def proc_starttime(self, pid):
        """Answers only while the unit is alive — which is the whole point.

        `/proc/<pid>/stat` stops existing the moment the process does, so a
        runner that reads the starttime after the loop gets `None` on every run
        and never notices, because `None` is also what a permission error
        gives. The fake refuses to answer after the unit has ended so that the
        difference is visible here.
        """
        return self.proc_starttime_value if self.unit_alive else None

    def cgroup_read(self, control_group, filename):
        return self.cgroup_files.get(filename)

    def proc_cgroup(self, pid):
        return self.proc_cgroup_value

    def stop(self, unit):
        self.stopped.append(unit)

    def reset_failed(self, unit):
        self.reset.append(unit)


def _running(**over):
    snap = {
        "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
        "MainPID": "4242",
        "ControlGroup": "/user.slice/user-1002.slice/user@1002.service/app.slice/u.service",
        "MemoryPeak": "1048576",
    }
    snap.update(over)
    return snap


def _finished(**over):
    snap = {"LoadState": "loaded", "ActiveState": "failed", "SubState": "failed",
            "Result": "success", "ExecMainStatus": "0", "MainPID": "0",
            "ControlGroup": "", "MemoryPeak": "[not set]"}
    snap.update(over)
    return snap


def _request(cr, tmp_path, **over):
    wt = tmp_path / "wt"
    (wt / "tests").mkdir(parents=True, exist_ok=True)
    (wt / ".git").write_text("gitdir: /nowhere\n")
    (wt / "tests" / "test_a.py").write_text("")
    kwargs = dict(
        worktree=wt,
        profile="default",
        all_tests=False,
        test_paths=["tests/test_a.py"],
        pytest_bin="/venv/bin/pytest",
        receipt_dir=tmp_path / "receipts",
        reason="unit test",
        authorized=False,
        systemd_run_bin="/usr/bin/systemd-run",
        systemctl_bin="/usr/bin/systemctl",
        home=tmp_path / "home",
    )
    kwargs.update(over)
    return cr.Request(**kwargs)


class TestOrchestrationFailsClosed:
    def _good_envelope(self, cr):
        return cr.expected_cgroup_values(cr.resolve_limits("default"))

    def _arm_ok(self, cr, sysd):
        env = self._good_envelope(cr)
        sysd.snapshots = [_running(), _finished()]
        sysd.cgroup_files = dict(env)
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"

    def test_a_clean_run_is_a_success(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["outcome"] == "success"
        assert receipt["exit_code"] == 0
        assert receipt["verification"] == []

    def test_the_receipt_is_written_to_the_run_directory(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        written = Path(receipt["receipt_path"])
        assert written.is_file()
        assert written.parent.parent == tmp_path / "receipts"
        assert (written.parent / "stdout.log").parent == written.parent

    def test_absent_systemd_refuses_before_launching_anything(self, cr, tmp_path):
        """No systemd is not a reason to fall back to a bare pytest."""
        sysd = FakeSystemd()
        sysd.available_value = (False, "user manager unreachable")
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_PREFLIGHT
        assert sysd.launches == []

    def test_a_drifted_limit_fails_the_run_even_when_pytest_passed(self, cr, tmp_path):
        """The one that matters: pytest exited 0 and the bound was not there."""
        sysd = FakeSystemd()
        sysd.snapshots = [_running(), _finished()]
        sysd.cgroup_files = dict(self._good_envelope(cr), **{"memory.max": "max"})
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_ENVELOPE
        assert receipt["outcome"] != "success"
        assert [f["code"] for f in receipt["verification"]] == ["LIMIT_NOT_APPLIED"]

    def test_a_process_outside_the_unit_cgroup_fails_the_run(self, cr, tmp_path):
        sysd = FakeSystemd()
        sysd.snapshots = [_running(), _finished()]
        sysd.cgroup_files = dict(self._good_envelope(cr))
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/other.service"
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_TREE
        assert [f["code"] for f in receipt["verification"]] == ["PROCESS_OUTSIDE_UNIT_CGROUP"]

    def test_a_timeout_is_reported_as_a_timeout(self, cr, tmp_path):
        sysd = FakeSystemd()
        sysd.snapshots = [_running(), _finished(Result="timeout", ExecMainStatus="0")]
        sysd.cgroup_files = dict(self._good_envelope(cr))
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["outcome"] == "timeout"
        assert receipt["exit_code"] == 124

    def test_an_oom_kill_is_reported_as_one(self, cr, tmp_path):
        sysd = FakeSystemd()
        sysd.snapshots = [_running(), _finished(Result="oom-kill")]
        sysd.cgroup_files = dict(self._good_envelope(cr))
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["outcome"] == "oom-kill"
        assert receipt["exit_code"] == 137

    def test_a_refused_launch_is_not_a_completed_run(self, cr, tmp_path):
        sysd = FakeSystemd()
        sysd.launch_rc = 1
        sysd.launch_stderr = "Failed to start transient service unit: unit already exists"
        sysd.snapshots = [_finished(Result="exit-code", ExecMainStatus="1")]
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] != 0
        assert receipt["outcome"] != "success"

    def test_a_run_that_never_reaches_a_terminal_state_is_stopped_and_failed(self, cr, tmp_path):
        """The wall clock is the last net: if systemd did not end it, we do."""
        sysd = FakeSystemd()
        sysd.snapshots = [_running() for _ in range(10000)]
        sysd.cgroup_files = dict(self._good_envelope(cr))
        sysd.proc_cgroup_value = "0::/user.slice/user-1002.slice/user@1002.service/app.slice/u.service"
        ticks = iter([0.0] + [10_000.0] * 100)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd,
                         clock=lambda: next(ticks), sleep=lambda _: None)
        assert sysd.stopped, "a run past its deadline must be stopped"
        assert receipt["exit_code"] != 0

    def test_the_unit_is_cleaned_up_after_a_run(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert sysd.reset, "a finished transient unit must be reset, not left failed"

    def test_the_receipt_carries_a_replay_safe_process_identity(self, cr, tmp_path):
        """pid alone is reused; pid+starttime+cgroup identifies one process.

        And the starttime has to be read while the process exists, because
        `/proc/<pid>/stat` is gone the moment it is not — so a runner that
        reads it afterwards records `None` on every run and cannot tell that
        from a process it merely lacked permission to look at.
        """
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        ident = receipt["main_process"]
        assert ident["pid"] == 4242
        assert ident["cgroup"].endswith("/u.service")
        assert ident["starttime"] == 987654

    def test_the_receipt_carries_both_the_request_and_what_was_observed(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["schema"] == cr.SCHEMA
        assert receipt["requested"]["memory_max_bytes"] == 4 * GIB
        assert receipt["observed"]["memory.max"] == str(4 * GIB)
        assert receipt["unit"].endswith(".service")

    def test_the_receipt_never_carries_the_environment(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        blob = repr(receipt)
        assert "environ" not in blob.lower()
        assert "PATH=" not in blob

    def test_the_child_tree_share_is_recorded_for_the_alerting_side(self, cr, tmp_path):
        """#167 measures per-uid memory; this is the run's own declared cap."""
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["subject"].startswith("uid:")
        assert receipt["cap_bytes"] == 4 * GIB
        assert receipt["peak_bytes"] == 1048576

    def test_an_unauthorised_elevation_never_launches(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path, profile="large-16"), systemd=sysd,
                         sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_REFUSED
        assert sysd.launches == []

    def test_a_selection_outside_tests_never_launches(self, cr, tmp_path):
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path, test_paths=["runtime.py"]),
                         systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_REFUSED
        assert sysd.launches == []

    def test_a_unit_that_never_names_a_main_pid_fails_the_tree_check(self, cr, tmp_path):
        """`MainPID=0` is systemd saying "not yet", and it is not a pass.

        Reading `/proc/0/cgroup` would fail for a reason that has nothing to do
        with the run, and a runner that turned that into a finding about the
        tree would be reporting the wrong thing. What it must not do is the
        other wrong thing: treat "there was never a process to check" as
        "the tree was fine".
        """
        sysd = FakeSystemd()
        sysd.snapshots = [_running(MainPID="0"), _finished()]
        sysd.cgroup_files = dict(self._good_envelope(cr))
        receipt = cr.run(_request(cr, tmp_path), systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_TREE
        assert [f["code"] for f in receipt["verification"]] == ["MAINPID_UNKNOWN"]

    def test_an_unwritable_receipt_directory_still_reports_the_run(self, cr, tmp_path):
        """Evidence that cannot be written must not turn a failure into a pass."""
        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory")
        sysd = FakeSystemd()
        self._arm_ok(cr, sysd)
        receipt = cr.run(_request(cr, tmp_path, receipt_dir=blocker),
                         systemd=sysd, sleep=lambda _: None)
        assert receipt["exit_code"] == cr.EXIT_EVIDENCE


# ---------------------------------------------------------------------------
# The one test that talks to the kernel. Small on purpose.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("SERVEROS_CONSTRAINED_RUNNER_NO_SYSTEMD") == "1",
    reason="explicitly disabled on this host")
def test_a_real_user_unit_is_read_back_from_the_kernel(cr, tmp_path):
    """The property that no fake can supply: the kernel has the limit.

    `/bin/true` under a 32 MiB cap, read back from `/sys/fs/cgroup` while it is
    alive. If the user manager is absent the test skips rather than fails —
    absence of systemd is a refusal in production, not a broken test.
    """
    sysd = cr.Systemd(systemd_run_bin="systemd-run", systemctl_bin="systemctl")
    ok, why = sysd.available()
    if not ok:
        pytest.skip(f"no user systemd here: {why}")
    limits = {"memory_high_bytes": 16 * 1024 ** 2,
              "memory_max_bytes": 32 * 1024 ** 2,
              "cpu_quota_percent": 50, "runtime_max_sec": 30, "tasks_max": 64}
    unit = cr.unit_name(nonce="smoke", epoch=time.time())
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # Half a second, so the unit is alive when it is read back. A command that
    # exits instantly would be a test of the race, not of the limit.
    argv = cr.build_launch_argv(
        unit=unit, limits=limits, worktree=tmp_path, pytest_bin="/bin/sh",
        test_args=["-c", "sleep 0.5"], run_dir=run_dir,
        systemd_run_bin="systemd-run")
    try:
        proc = sysd.launch(argv)
        assert proc.returncode == 0, proc.stderr
        deadline = time.monotonic() + 10
        snapshot = {}
        observed = {}
        while time.monotonic() < deadline:
            snapshot = sysd.unit_snapshot(unit)
            cg = snapshot.get("ControlGroup", "")
            if cg and snapshot.get("ActiveState") in ("active", "activating"):
                observed = cr.expected_cgroup_values(limits)
                observed = {k: sysd.cgroup_read(cg, k) for k in observed}
                break
            if snapshot.get("ActiveState") in ("inactive", "failed"):
                break
            time.sleep(0.05)
        if not observed:
            pytest.skip("unit finished before it could be read back")
        assert cr.verify_envelope(cr.expected_cgroup_values(limits), observed) == []
    finally:
        sysd.stop(unit)
        sysd.reset_failed(unit)
