"""The owner-policy/epoch -> lease commit contract is BLOCKED, and the
fail-closed controls still hold.

The runnable probe in `tests/probes/s1_owner_atomic_commit_probe.py` drives the
real chain over a temp AF_UNIX socket and production daemon/store/adapter code.
This test pins its receipt: five controls hold, no lease is written, and there
is no `COMMIT_FENCED` producer. The positive owner-backed lease is NOT_RUN
because D1 (owner-issued policy/epoch authority) and D2 (a lease commit in the
same serializable transaction as the epoch check) are absent.

EVIDENCE: REAL_TEMP socket/peer + production code; synthetic owner facts; one
uid. Not REAL_DEVICE and not a production daemon.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "tests" / "probes" / "s1_owner_atomic_commit_probe.py"


def _run_probe() -> tuple[int, dict]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    completed = subprocess.run(
        [sys.executable, str(PROBE), "--mode", "all"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60)
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"probe printed nothing; stderr={completed.stderr}"
    return completed.returncode, json.loads(lines[-1])


def test_the_atomic_commit_contract_probe_holds_and_reports_the_blocker():
    code, report = _run_probe()
    assert code == 2, (code, report)
    assert report["failed"] == 0, report
    controls = {row["name"]: row for row in report["controls"]}
    assert controls["no-resolver-verify"]["ok"] is True
    assert controls["no-resolver-issue-zero-grant"]["ok"] is True
    assert controls["owner-live-verify-blocks-commit-seam"]["ok"] is True
    assert controls["owner-live-lease-no-write"]["ok"] is True
    assert controls["race-revoke-before-acquire-no-lease"]["ok"] is True

    positive = report["positive"]
    assert positive["status"] == "BLOCKED_OWNER_ATOMIC_AUTHORITY"
    assert positive["positive_authority"] == "NOT_RUN"
    assert positive["commit_fence_methods"] == []
    assert positive["hypothetical_commit_method"] == "UNKNOWN_METHOD"
    assert positive["owner_live_answer"] == {
        "verified": False,
        "reason": "BLOCKED_OWNER_COMMIT_SEAM",
        "grant_present": False,
    }


@pytest.mark.skip(reason=(
    "BLOCKED_OWNER_ATOMIC_AUTHORITY: no owner-issued policy/epoch authority "
    "(D1) and no lease commit in the same serializable transaction as the "
    "epoch check (D2). A synthetic COMMIT_FENCED, HMAC or legacy #272 readback "
    "would not be a fence."))
def test_a_real_owner_issued_commit_fence_can_authorize_a_lease():
    """NOT_RUN: this is the positive the upstream authority must enable."""
    raise AssertionError("a positive path must not exist without D1/D2")


@pytest.mark.skip(reason=(
    "BLOCKED_MULTIPRINCIPAL_NOT_RUN: this process has one uid and no "
    "passwordless sudo/setuid; a second real OS principal cannot be attested "
    "and is not faked with synthetic peer metadata."))
def test_two_real_uids_can_be_attested():
    """NOT_RUN: the two-real-uid environment is absent."""
    raise AssertionError("synthetic peer metadata is not a second OS uid")
