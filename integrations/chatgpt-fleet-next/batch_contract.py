"""Pure, offline checks for a ChatGPT Fleet batch plan and its completion.

The cloud-side Skill is prose; these functions are the executable part that a
test can hold to account. Nothing here calls Fleet, a model, a device or the
network.
"""
from __future__ import annotations

from typing import Any

TASK_FIELDS = (
    "task_id", "device_ref", "runtime", "project_id", "worktree", "scope",
    "acceptance", "tests", "report", "idempotency_key",
)
IDLE_STATES = frozenset({"MANAGED_IDLE", "RESIDENT_HOST"})
TERMINAL_FAILURE = frozenset({"FAILED", "failed", "FAILURE"})
RUNNING_STATES = frozenset({"RUNNING", "RUN_UNSETTLED", "RUNNING_AGENT", "running"})


def _task_refusal(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _check_task(task: dict[str, Any], snapshot: dict[str, Any],
                seen_worktrees: set[str]) -> list[dict[str, str]]:
    refusals: list[dict[str, str]] = []
    missing = [field for field in TASK_FIELDS if not task.get(field)]
    if missing:
        return [_task_refusal("TASK_INCOMPLETE", f"missing {missing}")]

    device_ref = task["device_ref"]
    runtime = task["runtime"]
    devices = snapshot.get("devices") or {}
    device = devices.get(device_ref)
    if not device:
        return [_task_refusal("UNKNOWN_DEVICE", f"{device_ref} is not in the live device list")]

    required_roles = set(task.get("required_roles") or ())
    if required_roles and not required_roles <= set(device.get("roles") or ()):
        refusals.append(_task_refusal(
            "MISSELECTED_DEVICE",
            f"{device_ref} roles {sorted(device.get('roles') or ())} do not cover "
            f"{sorted(required_roles)}"))

    runtimes = set(device.get("runtimes") or ())
    if runtime not in runtimes:
        refusals.append(_task_refusal(
            "MISSELECTED_RUNTIME", f"{device_ref} does not expose {runtime}"))

    ability = task.get("required_ability")
    if ability:
        row = ((snapshot.get("abilities") or {}).get(device_ref) or {}).get(ability) or {}
        if row.get("stale") or row.get("state") != "READY" or not row.get("effective"):
            refusals.append(_task_refusal(
                "STALE_CAPABILITY",
                f"{device_ref}/{ability} is {row.get('state', 'UNKNOWN')}; "
                f"stale={bool(row.get('stale'))}, effective={bool(row.get('effective'))}"))
        if row and not row.get("granted"):
            refusals.append(_task_refusal(
                "GRANT_DENIED", f"{device_ref}/{ability} is not granted"))

    required_model = task.get("required_model")
    if required_model:
        models = (((snapshot.get("models") or {}).get(device_ref) or {})
                  .get(runtime) or [])
        if required_model not in models:
            refusals.append(_task_refusal(
                "MODEL_NOT_AVAILABLE",
                f"{required_model} is not in {device_ref}/{runtime} models {models}"))

    required_grant = task.get("required_grant")
    if required_grant:
        grants = ((snapshot.get("grants") or {}).get(device_ref) or {})
        if required_grant not in set(grants.get(runtime) or ()):
            refusals.append(_task_refusal(
                "GRANT_DENIED",
                f"{device_ref}/{runtime} lacks {required_grant}"))

    session_ref = task.get("session_ref")
    sessions = {row.get("session_ref"): row for row in snapshot.get("sessions") or []}
    if session_ref:
        session = sessions.get(session_ref)
        if not session:
            refusals.append(_task_refusal(
                "SESSION_NOT_FOUND", f"{session_ref} is not in the live session list"))
        else:
            if session.get("device_ref") != device_ref or session.get("runtime") != runtime:
                refusals.append(_task_refusal(
                    "SESSION_PROJECT_MISMATCH",
                    f"{session_ref} belongs to {session.get('device_ref')}/"
                    f"{session.get('runtime')}, not {device_ref}/{runtime}"))
            if session.get("project_id") != task["project_id"]:
                refusals.append(_task_refusal(
                    "SESSION_PROJECT_MISMATCH",
                    f"{session_ref} project {session.get('project_id')} != "
                    f"{task['project_id']}"))
            if session.get("state") in RUNNING_STATES:
                refusals.append(_task_refusal(
                    "SESSION_BUSY", f"{session_ref} is {session.get('state')}"))
            elif session.get("state") not in IDLE_STATES:
                refusals.append(_task_refusal(
                    "SESSION_NOT_IDLE", f"{session_ref} is {session.get('state')}"))
    elif not ((snapshot.get("create_allowed") or {}).get(device_ref)):
        refusals.append(_task_refusal(
            "SESSION_REQUIRED",
            f"no idle session and create is not allowed on {device_ref}"))

    worktree = task["worktree"]
    if worktree in seen_worktrees:
        refusals.append(_task_refusal(
            "WORKTREE_CONFLICT", f"{worktree} is assigned to more than one task"))
    seen_worktrees.add(worktree)
    return refusals


def validate_batch(batch: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return PASS only when every task can be dispatched as written."""
    batch_id = batch.get("batch_id")
    tasks = batch.get("tasks") or []
    if not isinstance(batch_id, str) or not batch_id.strip():
        return {"verdict": "BLOCKED", "batch_id": batch_id,
                "blocked": [{"task_id": None, "code": "BATCH_ID_MISSING",
                             "detail": "a persistent Batch ID is required"}]}
    if not tasks:
        return {"verdict": "BLOCKED", "batch_id": batch_id,
                "blocked": [{"task_id": None, "code": "BATCH_EMPTY",
                             "detail": "a batch needs at least one task"}]}

    seen_ids: set[str] = set()
    seen_worktrees: set[str] = set()
    blocked: list[dict[str, Any]] = []
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen_ids:
            blocked.append({"task_id": task_id, "code": "TASK_ID_DUPLICATE",
                            "detail": "task_id must be unique and non-empty"})
            continue
        seen_ids.add(task_id)
        for refusal in _check_task(task, snapshot, seen_worktrees):
            blocked.append({"task_id": task_id, **refusal})
    return {
        "verdict": "PASS" if not blocked else "BLOCKED",
        "batch_id": batch_id,
        "task_count": len(tasks),
        "blocked": blocked,
    }


def completion_verdict(run: dict[str, Any]) -> dict[str, Any]:
    """Classify one task's readback; ACK-only and bare completed are UNKNOWN."""
    if not run.get("run_id"):
        return {"verdict": "UNKNOWN", "code": "ACK_ONLY",
                "detail": "no run_id; delivery is not execution"}
    state = str(run.get("state") or "UNKNOWN")
    if run.get("ack_only") is True:
        return {"verdict": "UNKNOWN", "code": "ACK_ONLY",
                "detail": "the receipt is delivery-only"}
    if state in {"RUN_NOT_FOUND", "never_issued"}:
        return {"verdict": "UNKNOWN", "code": "RUN_NOT_FOUND",
                "detail": "absence of a run record is not proof of no effect"}
    if state in RUNNING_STATES:
        return {"verdict": "RUNNING", "code": "RUN_UNSETTLED",
                "detail": "still working; not failed and not done"}
    if state in TERMINAL_FAILURE:
        return {"verdict": "FAILED", "code": "RUN_FAILED",
                "detail": "the run reported failure"}
    if state.lower() in {"completed", "succeeded", "success"}:
        if not run.get("native_result"):
            return {"verdict": "UNKNOWN", "code": "FALSE_COMPLETION",
                    "detail": "completed without a trusted native result"}
        if not run.get("evidence_ref"):
            return {"verdict": "UNKNOWN", "code": "FALSE_COMPLETION",
                    "detail": "completed without a readback/acceptance artifact"}
        if run.get("acceptance") is False:
            return {"verdict": "FAILED", "code": "ACCEPTANCE_FAILED",
                    "detail": "the acceptance check did not pass"}
        return {"verdict": "PASS", "code": "EVIDENCE_READ",
                "detail": "native result and acceptance evidence both read"}
    return {"verdict": "UNKNOWN", "code": "UNKNOWN_STATE",
            "detail": f"state {state!r} is not a verified outcome"}


def batch_verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    """One UNKNOWN keeps the batch UNKNOWN; one failure makes it FAILED."""
    verdicts = [row.get("verdict") for row in results]
    if not verdicts:
        return {"verdict": "UNKNOWN", "code": "NO_TASKS"}
    if any(v == "FAILED" for v in verdicts):
        return {"verdict": "FAILED", "code": "TASK_FAILED"}
    if any(v != "PASS" for v in verdicts):
        return {"verdict": "UNKNOWN", "code": "TASK_NOT_VERIFIED"}
    return {"verdict": "PASS", "code": "ALL_TASKS_VERIFIED"}
