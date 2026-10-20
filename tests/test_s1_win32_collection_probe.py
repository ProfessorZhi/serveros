"""The synthetic-Windows collection probe must not be vacuously green.

Cross-platform: this file runs on Linux and Windows and never imports the
POSIX daemon/store. It pins the probe's invocation guards: no arguments, an
invalid file and a run with zero collected modules are non-zero with a reason;
the normal POSIX modules + adapter set is 3 skipped / 1 collected / rc=0.

The probe itself is SYNTHETIC_WIN32 evidence, not a real Windows run.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tests" / "probes" / "s1_win32_collection_probe.py"
POSIX_FILES = (
    "tests/test_owner_policy_binding.py",
    "tests/test_owner_commit_fence.py",
    "tests/test_owner_policy_reduced_gate.py",
)
ADAPTER = "tests/test_owner_commit_fence_adapter.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, str(PROBE), *args], cwd=str(REPO), env=env,
        capture_output=True, text=True, timeout=60)


def test_no_arguments_is_nonzero_with_usage():
    for args in ((), (str(REPO),)):
        completed = _run(*args)
        assert completed.returncode == 2, completed.stdout + completed.stderr
        assert "no test files" in completed.stdout
        assert "usage:" in completed.stdout


def test_invalid_file_arguments_are_nonzero_with_reason():
    missing = _run(str(REPO), "tests/definitely_missing_test.py")
    assert missing.returncode == 2
    assert "test file does not exist" in missing.stdout

    not_python = _run(str(REPO), "pytest.ini")
    assert not_python.returncode == 2
    assert "not a .py file" in not_python.stdout


def test_zero_collected_is_nonzero_and_explained():
    # The epoch module is POSIX-only and skips at module level. A suite of
    # skips alone is not evidence that the probe ran.
    completed = _run(str(REPO), "tests/test_v1_grant_epoch_authority.py")
    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "zero modules collected" in completed.stdout
    assert "skipped=1 errors=0 collected=0" in completed.stdout


def test_normal_posix_plus_adapter_is_three_skips_one_collected():
    completed = _run(str(REPO), *POSIX_FILES, ADAPTER)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "summary skipped=3 errors=0 collected=1 rc=0" in completed.stdout
    for name in POSIX_FILES:
        assert f"SKIPPED {name}: POSIX_ONLY:" in completed.stdout
    assert f"COLLECTED {ADAPTER}" in completed.stdout


def test_epoch_plus_adapter_is_one_skip_one_collected():
    completed = _run(
        str(REPO), "tests/test_v1_grant_epoch_authority.py", ADAPTER)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert ("SKIPPED tests/test_v1_grant_epoch_authority.py: POSIX_ONLY:"
            in completed.stdout)
    assert "summary skipped=1 errors=0 collected=1 rc=0" in completed.stdout
