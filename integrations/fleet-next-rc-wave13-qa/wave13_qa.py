"""Wave 13 compose QA: #487 + #477 + #481 + #480, offline only.

The producer modules remain read-only. This module calls their public
functions and records one owner-level conflict that cannot be resolved from
this lane: #481 changes the batch README whose exact bytes #487's Wave 12
ledger pins. The conflict is reported as BLOCKED_OWNER, never papered over by
editing either owner's file.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WAVE13_BASE = "d8dc1f92b4252338e52271ca3f74b4d05ee39fbc"

SOURCE_HEADS = {
    "rc_487": WAVE13_BASE,
    "model_477": "be2c1cccd6a506485c9465967b2531facffe459e",
    "cli_481": "9b7710a26180e1e6d00ce597827d447d0d2348b7",
    "parallel_480": "3777d134be02d896c9e36bc2ffd3a25baf9aaa04",
}
PARENTS = {
    "rc_487": "26ad7245d2b065f96edad5aecf271d4e1cd1bd23",
    "model_477": "7b4b9d66d6dad9af90e9632a26e2f33b541d73e6",
    "cli_481": "0c588896a7b4b6b122a593c83683342429020cd6",
    "parallel_480": "be2c1cccd6a506485c9465967b2531facffe459e",
}
MERGE_BASES = {
    "model_477": "7b4b9d66d6dad9af90e9632a26e2f33b541d73e6",
    "cli_481": "0c588896a7b4b6b122a593c83683342429020cd6",
    "parallel_480": "7b4b9d66d6dad9af90e9632a26e2f33b541d73e6",
}
MERGE_COMMITS = {
    "model_477": "950d05c",
    "cli_481": "0ca9b8b",
    "parallel_480": "f8a7e13",
}
WAVE12_LEDGER = "tests/test_fleet_next_rc_wave12_ledger.py"
WAVE12_README = "integrations/fleet-v1-milestone-batch/README.md"
WAVE12_EXPECTED_README_SHA256 = (
    "d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf")


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


batch = _load("wave13_batch_contract",
              ROOT / "integrations" / "chatgpt-fleet-next" / "batch_contract.py")
parallel = _load("wave13_parallel_plugin",
                 ROOT / "integrations" / "fleet-next-parallel-plugin" / "parallel_plugin.py")
build_zip = _load("wave13_build_zip",
                  ROOT / "integrations" / "chatgpt-fleet-next" / "build_zip.py")

NOW = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.timezone.utc)
SHA = "a" * 64


def _task() -> dict[str, Any]:
    return {
        "task_id": "T1", "device_ref": "device:a", "runtime": "claude",
        "project_id": "p1", "worktree": "wt/t1", "scope": "slice",
        "acceptance": "tests pass", "tests": "pytest", "report": "r.json",
        "idempotency_key": "i1", "required_grant": "send",
        "session_ref": "s:a", "required_model": "sonnet",
    }


def _snapshot(*, create_models: list[str], with_evidence: bool) -> dict[str, Any]:
    evidence = []
    if with_evidence:
        evidence = [{
            "device_ref": "device:a", "runtime": "claude", "release": "1.41.0",
            "captured_at": "2026-10-28T11:00:00Z",
            "schema_types": {"model": "string"},
            "declared": {"model": ["sonnet"]}, "capture_sha256": SHA,
        }]
    return {
        "devices": {"device:a": {"roles": ["dev"], "runtimes": ["claude"]}},
        "grants": {"device:a": {"claude": ["send"]}},
        "create_allowed": {"device:a": True},
        "create_allow": {"p1": {"models": list(create_models)}},
        "sessions": [{
            "session_ref": "s:a", "device_ref": "device:a", "runtime": "claude",
            "project_id": "p1", "state": "MANAGED_IDLE",
        }],
        "model_evidence": evidence,
    }


def model_grant_facts() -> dict[str, Any]:
    """#477: project grant is checked before provider evidence, no calls."""
    denied = batch.validate_batch(
        {"batch_id": "B", "tasks": [_task()]},
        _snapshot(create_models=[], with_evidence=True), now=NOW)
    allowed = batch.validate_batch(
        {"batch_id": "B", "tasks": [_task()]},
        _snapshot(create_models=["sonnet"], with_evidence=True), now=NOW)
    missing = batch.validate_batch(
        {"batch_id": "B", "tasks": [_task()]},
        _snapshot(create_models=["sonnet"], with_evidence=False), now=NOW)
    return {
        "denied_verdict": denied["verdict"],
        "denied_codes": [row["code"] for row in denied["blocked"]],
        "allowed_verdict": allowed["verdict"],
        "allowed_decision": allowed["model_decisions"]["T1"]["decision"],
        "missing_verdict": missing["verdict"],
        "missing_codes": [row["code"] for row in missing["blocked"]],
        "provider_calls": (denied["provider_calls"] + allowed["provider_calls"]
                           + missing["provider_calls"]),
    }


def _legacy_record() -> dict[str, Any]:
    return {
        "track": "legacy", "backend_plugin_id": "plugin_legacy_synthetic",
        "release_id": "PluginRelease_LEGACY_SYNTHETIC", "name": "serveros-fleet-ingress",
        "version": "0.1.0", "mcp_url": "https://legacy.example/mcp",
        "oauth_audience": "legacy-aud", "credential_anchor": "vault:legacy",
        "port": 3411, "datastore": "/var/lib/serveros/legacy.db",
        "tool_namespace": "serveros-fleet",
        "skills": sorted(parallel.LEGACY_SKILLS),
        "source_pin": "legacy_install", "archive_sha256": "1" * 64,
        "content_sha256": "2" * 64, "members": 14,
    }


def _next_record() -> dict[str, Any]:
    pin = parallel.TRUSTED_SOURCE_PINS["fleet_v1_9skill"]
    return {
        "track": "next", "backend_plugin_id": "plugin_next_synthetic",
        "release_id": "PluginRelease_NEXT_SYNTHETIC", "name": "serveros-fleet-next",
        "version": "1.0.0", "mcp_url": "https://next.example/mcp",
        "oauth_audience": "next-aud", "credential_anchor": "vault:next",
        "port": 3412, "datastore": "/var/lib/serveros/next.db",
        "tool_namespace": "serveros-fleet-next",
        "skills": sorted(parallel.NEXT_SKILLS),
        "source_pin": "fleet_v1_9skill",
        "archive_sha256": pin["archive_sha256"],
        "content_sha256": pin["content_sha256"], "members": pin["members"],
    }


def parallel_gate_facts() -> dict[str, Any]:
    """#480: no Legacy identity/URL reuse and no shadow write."""
    legacy = _legacy_record()
    next_plugin = _next_record()
    same_id = dict(next_plugin, backend_plugin_id=legacy["backend_plugin_id"])
    same_url = dict(next_plugin, mcp_url=legacy["mcp_url"])
    shadow = parallel.preflight(
        legacy_baseline=dict(legacy), legacy_current=dict(legacy),
        next_plugin=dict(next_plugin),
        shadow={"requested_writes": ["send", "create"]})
    return {
        "same_id": parallel.preflight(
            legacy_baseline=dict(legacy), legacy_current=dict(legacy),
            next_plugin=same_id)["code"],
        "same_url": parallel.preflight(
            legacy_baseline=dict(legacy), legacy_current=dict(legacy),
            next_plugin=same_url)["code"],
        "shadow": shadow["code"],
        "shadow_creator_calls": shadow["creator_calls"],
        "shadow_network": shadow["network"],
    }


def archive_facts() -> dict[str, Any]:
    """Compare the canonical 9-Skill 15-member artifact with the 7-member one."""
    pin9 = parallel.TRUSTED_SOURCE_PINS["fleet_v1_9skill"]
    pin7 = parallel.TRUSTED_SOURCE_PINS["chatgpt_fleet_next_7member"]
    with tempfile.TemporaryDirectory() as tmp:
        built = build_zip.build(ROOT / "integrations" / "chatgpt-fleet-next",
                                Path(tmp))
    members = built["members"]
    return {
        "nine": {
            "archive_sha256": pin9["archive_sha256"],
            "content_sha256": pin9["content_sha256"],
            "members": pin9["members"],
            "skills": pin9["skills"],
        },
        "seven": {
            "archive_sha256": built["archive_sha256"],
            "content_sha256": built["content_sha256"],
            "members": len(members),
            "skills": sum(1 for m in members if m.endswith("/SKILL.md")),
        },
        "distinct": pin9["archive_sha256"] != built["archive_sha256"],
    }


def _lf_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def wave12_conflict() -> dict[str, Any]:
    """#487's ledger pins a README byte string that #481 legitimately changes."""
    actual = _lf_digest(ROOT / WAVE12_README)
    return {
        "code": "BLOCKED_OWNER" if actual != WAVE12_EXPECTED_README_SHA256 else "OK",
        "file": WAVE12_README,
        "expected_sha256": WAVE12_EXPECTED_README_SHA256,
        "actual_sha256": actual,
        "ledger": WAVE12_LEDGER,
        "owner": "#487 (ledger digest) / #481 (README delta)",
        "resolution": ("owner must re-derive the Wave 12 ledger for the new "
                       "composition; this lane will not edit either file"),
    }


def legacy_surface_touched() -> list[str]:
    """Paths in the P2 delta that belong to the old Fleet control surface."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{WAVE13_BASE}..HEAD"],
            cwd=ROOT, check=True, text=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    forbidden = ("fleet_mcp/", "runtime/", "services/", "plugin/", "deploy/")
    return [line for line in out.splitlines() if line.startswith(forbidden)]
