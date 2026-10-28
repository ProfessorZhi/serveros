"""Acceptance for stacking the two published fixes onto the exact #479 RC.

This is a QA harness, not a second RC. It does not merge, deploy or alter any
source. It records the exact stack, runs the two original red test ids, and
keeps the #478 Legacy-untouched gate separate from Next shadow reads.

Nothing here calls Creator, a device, a service, OAuth, a model or IM.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

#: The exact #479 tree the owner named, and the two fix commits. These are
#: commits in the PRs' ancestry, not synthetic branch names.
MAIN_RC_STACK = (
    {"label": "#479 exact", "commit": "d037588723172816b8af12cefb2fffe6afe09f79"},
    {"label": "#471 payload allowlist", "commit": "7b4b9d66d6dad9af90e9632a26e2f33b541d73e6"},
    {"label": "#472 live Commander session fixture", "commit": "0c588896a7b4b6b122a593c83683342429020cd6"},
)

#: #477 and #480 depend on #471, so they are a separate replayable candidate,
#: never the main RC.
CANDIDATE_STACK = MAIN_RC_STACK + (
    {"label": "#477 model evidence", "commit": "be2c1cccd6a506485c9465967b2531facffe459e"},
    {"label": "#480 parallel plugin preflight", "commit": "3777d134be02d896c9e36bc2ffd3a25baf9aaa04"},
)

#: These are conditional sources only; this harness makes no deployment or IM
#: claim about them.
CONDITIONAL_SOURCES = (
    {"label": "#481 CLI", "status": "NOT_RUN_NOT_DEPLOYED"},
    {"label": "#473 Hub sidecar", "status": "NOT_RUN_NOT_DEPLOYED"},
)

ORIGINAL_RED_TESTS = (
    "tests/test_fleet_v1_milestone_batch_registration.py::"
    "TheLiveCommanderContractIsConsumedAsIs::"
    "test_a_plan_the_live_contract_accepts_becomes_a_registration",
    "tests/test_fleet_next_plugin_package.py::"
    "ThisIsNotTheNativeServerOSPlugin::"
    "test_the_clean_package_is_not_caught_by_the_layout_rule",
)

LEGACY_FIELDS = ("backend_plugin_id", "release_id", "mcp_url", "port",
                 "datastore", "tool_namespace")
LEGACY_SKILLS = frozenset({
    "fleet-async-dispatch", "fleet-commander-orchestration",
    "fleet-create-with-grants", "fleet-device-and-session-discovery",
    "fleet-evidence-review", "fleet-plugin-update",
    "fleet-reuse-idle-session", "fleet-safety-boundaries",
})
SHADOW_WRITES = frozenset({"create", "send", "stop", "restart", "wake",
                           "manager_wake", "interrupt", "update"})


def run_original_reds(repo: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Run the two original ids and return the child's measured result."""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *ORIGINAL_RED_TESTS],
        cwd=repo, env=env, capture_output=True, text=True, timeout=timeout)
    text = proc.stdout + proc.stderr
    return {
        "exit_code": proc.returncode,
        "passed": proc.returncode == 0,
        "tests": list(ORIGINAL_RED_TESTS),
        "summary": text.strip().splitlines()[-1] if text.strip() else "",
        "output_tail": "\n".join(text.strip().splitlines()[-20:]),
    }


def legacy_gate(*, legacy_baseline: dict[str, Any],
                legacy_current: dict[str, Any],
                shadow_writes: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """#478 gate: Legacy identity/resources/controller unchanged; shadow read-only."""
    changed = [field for field in LEGACY_FIELDS
               if legacy_current.get(field) != legacy_baseline.get(field)]
    if changed:
        return {"verdict": "BLOCKED", "code": "LEGACY_CONTROLLER_CHANGED",
                "changed": changed, "legacy_untouched": False,
                "next_shadow_read_only": True}
    skills = legacy_current.get("skills")
    if not isinstance(skills, (list, tuple, set)) or set(skills) != LEGACY_SKILLS:
        return {"verdict": "BLOCKED", "code": "LEGACY_CONTROLLER_MUTATED",
                "legacy_untouched": False, "next_shadow_read_only": True}
    writes = sorted(set(shadow_writes) & SHADOW_WRITES)
    if writes:
        return {"verdict": "BLOCKED", "code": "NEXT_SHADOW_WRITE_FORBIDDEN",
                "writes": writes, "legacy_untouched": True,
                "next_shadow_read_only": False}
    return {"verdict": "PASS", "legacy_untouched": True,
            "next_shadow_read_only": True,
            "legacy_backend_plugin_id": legacy_baseline.get("backend_plugin_id"),
            "legacy_release_id": legacy_baseline.get("release_id"),
            "legacy_skills": len(LEGACY_SKILLS)}


def dependency_graph() -> dict[str, Any]:
    """Main RC vs separate candidate vs conditional sources, explicitly."""
    return {
        "main_rc": [dict(row) for row in MAIN_RC_STACK],
        "candidate_replayable_not_main_rc": [dict(row) for row in CANDIDATE_STACK],
        "conditional_sources": [dict(row) for row in CONDITIONAL_SOURCES],
        "v2_407_draft_merged": False,
        "legacy_gate": "REQUIRED_LEGACY_UNTOUCHED",
        "next_shadow": "READ_ONLY",
        "creator_calls": 0,
        "network": False,
        "production_verified": False,
    }
