"""Pure, offline checks for a ChatGPT Fleet batch plan and its completion.

The cloud-side Skill is prose; these functions are the executable part that a
test can hold to account. Nothing here calls Fleet, a model, a device or the
network.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

TASK_FIELDS = (
    "task_id", "device_ref", "runtime", "project_id", "worktree", "scope",
    "acceptance", "tests", "report", "idempotency_key",
)
IDLE_STATES = frozenset({"MANAGED_IDLE", "RESIDENT_HOST"})
TERMINAL_FAILURE = frozenset({"FAILED", "failed", "FAILURE"})
RUNNING_STATES = frozenset({"RUNNING", "RUN_UNSETTLED", "RUNNING_AGENT", "running"})

#: The provider release this contract reasons about. A different release is not
#: "close enough"; its evidence is stale because the shape may have moved.
MODEL_RELEASE = "1.41.0"
MODEL_MAX_AGE = dt.timedelta(hours=24)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _task_refusal(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}



def _iso(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _evidence_view(record: object, now: dt.datetime) -> tuple[dict | None, str | None]:
    """Validate one provider-evidence record and return its model view.

    An observation is evidence only for one exact `(device, runtime)` at the
    pinned release, with a timezone-aware capture inside the freshness window
    and a capture digest. A list of ids is not enough: the digest and release
    are what make it an observation rather than a caller's assertion.
    """
    if not isinstance(record, dict):
        return None, "MODEL_EVIDENCE_MALFORMED"
    if record.get("release") != MODEL_RELEASE:
        return None, "MODEL_EVIDENCE_STALE"
    captured = _iso(record.get("captured_at"))
    if captured is None:
        return None, "MODEL_EVIDENCE_MALFORMED"
    age = now - captured
    if age < dt.timedelta(0) or age > MODEL_MAX_AGE:
        return None, "MODEL_EVIDENCE_STALE"
    if not _SHA256.match(str(record.get("capture_sha256") or "")):
        return None, "MODEL_EVIDENCE_MALFORMED"
    schema = record.get("schema_types")
    declared = record.get("declared")
    if schema is None:
        schema = {}
    if declared is None:
        declared = {}
    if not isinstance(schema, dict) or not isinstance(declared, dict):
        return None, "MODEL_EVIDENCE_MALFORMED"
    models = declared.get("model") or ()
    if not isinstance(models, (list, tuple)) or any(
            not isinstance(item, str) for item in models):
        return None, "MODEL_EVIDENCE_MALFORMED"
    return {
        "record": record,
        "declared": tuple(models),
        "schema_only": bool(schema.get("model")),
    }, None


def _evidence_records(snapshot: dict[str, Any]) -> tuple[list[Any] | None, str | None]:
    raw = snapshot.get("model_evidence")
    if raw is None:
        # `snapshot.models` is deliberately not a fallback: a caller-authored
        # list is not a provider observation, and reading it here is exactly the
        # fabrication this contract refuses.
        return None, "MODEL_EVIDENCE_MISSING"
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return None, "MODEL_EVIDENCE_MALFORMED"
    return raw, None


def _find_evidence(snapshot: dict[str, Any], device_ref: str, runtime: str,
                   now: dt.datetime) -> tuple[dict | None, str | None]:
    records, code = _evidence_records(snapshot)
    if code:
        return None, code
    matches = [row for row in records
               if isinstance(row, dict) and row.get("device_ref") == device_ref
               and row.get("runtime") == runtime]
    if not matches:
        return None, "MODEL_EVIDENCE_MISSING"
    if len(matches) > 1:
        return None, "MODEL_EVIDENCE_MALFORMED"
    return _evidence_view(matches[0], now)


def _declared_elsewhere(snapshot: dict[str, Any], device_ref: str, runtime: str,
                        model: str, now: dt.datetime) -> str | None:
    """Name a cross-target id instead of calling it unavailable."""
    records, code = _evidence_records(snapshot)
    if code:
        return None
    for row in records:
        if not isinstance(row, dict):
            continue
        if (row.get("device_ref"), row.get("runtime")) == (device_ref, runtime):
            continue
        view, _ = _evidence_view(row, now)
        if view is None or model not in view["declared"]:
            continue
        if row.get("runtime") == runtime:
            return "MODEL_DEVICE_MISMATCH"
        return "MODEL_RUNTIME_MISMATCH"
    return None


def _model_refusal(code: str, detail: str, *, evidence_class: str = "UNKNOWN",
                   source: str = "none") -> dict[str, Any]:
    return {
        "decision": "REFUSED", "model": None, "requested_model": None,
        "model_identity": "UNKNOWN", "evidence_class": evidence_class,
        "support": "POLICY_ALLOWED_ONLY" if source == "grant" else "UNKNOWN",
        "code": code, "source": source, "detail": detail,
    }


def _model_decision(task: dict[str, Any], snapshot: dict[str, Any],
                    now: dt.datetime) -> dict[str, Any]:
    """Decide the model argument from project authority and provider evidence.

    The project `create_allow.models` gate is checked first: a model the grant
    does not name is refused before any evidence is read, so a permission
    refusal is never disguised as a catalogue miss. With no requested model the
    provider default is allowed, but its identity is UNKNOWN. `require_explicit_model`
    turns that default into a fail-closed refusal.
    """
    requested = task.get("required_model")
    require_explicit = bool(task.get("require_explicit_model"))
    if not requested:
        if require_explicit:
            return _model_refusal(
                "MODEL_NOT_NAMED",
                "the task requires an explicit model but names none")
        return {
            "decision": "PROVIDER_DEFAULT", "model": None,
            "requested_model": None, "model_identity": "UNKNOWN",
            "evidence_class": "UNKNOWN", "support": "UNKNOWN",
            "code": "PROVIDER_DEFAULT", "source": "none",
            "detail": "no model requested; the provider default is used and "
                      "its identity is UNKNOWN",
        }

    grant = ((snapshot.get("create_allow") or {}).get(task["project_id"]))
    if not isinstance(grant, dict):
        return _model_refusal(
            "PROJECT_NOT_AUTHORIZED",
            f"project {task['project_id']} has no CreateGrant",
            source="grant")
    allowed_models = grant.get("models")
    if not isinstance(allowed_models, (list, tuple)) or any(
            not isinstance(item, str) for item in allowed_models):
        return _model_refusal(
            "MODEL_POLICY_MALFORMED",
            f"project {task['project_id']} CreateGrant.models is malformed",
            source="grant")
    if requested not in allowed_models:
        return _model_refusal(
            "MODEL_POLICY_DENIED",
            f"project {task['project_id']} CreateGrant.models does not name "
            f"{requested}",
            source="grant")

    view, code = _find_evidence(snapshot, task["device_ref"], task["runtime"], now)
    if code:
        mismatch = (_declared_elsewhere(snapshot, task["device_ref"],
                                        task["runtime"], requested, now)
                    if code == "MODEL_EVIDENCE_MISSING" else None)
        if mismatch:
            return _model_refusal(
                mismatch,
                f"{requested} is enumerated on another device or runtime; "
                f"it is not evidence for {task['device_ref']}/{task['runtime']}",
                source="provider")
        return _model_refusal(
            code,
            f"{task['device_ref']}/{task['runtime']} has no usable provider "
            f"model evidence for {requested}",
            source="provider")
    assert view is not None
    evidence_class = ("PROVIDER_ADVERTISED" if view["declared"]
                      else "SCHEMA_ONLY" if view["schema_only"] else "UNKNOWN")
    if requested in view["declared"]:
        return {
            "decision": "EXPLICIT", "model": requested,
            "requested_model": requested, "model_identity": "EXPLICIT",
            "evidence_class": "PROVIDER_ADVERTISED",
            "support": "PROVIDER_ADVERTISED", "code": "MODEL_ADVERTISED",
            "source": "provider",
            "detail": f"{requested} is enumerated for "
                      f"{task['device_ref']}/{task['runtime']}",
        }
    if view["declared"]:
        mismatch = _declared_elsewhere(snapshot, task["device_ref"],
                                       task["runtime"], requested, now)
        if mismatch:
            return _model_refusal(
                mismatch,
                f"{requested} is enumerated on another device or runtime; "
                f"it is not evidence for {task['device_ref']}/{task['runtime']}",
                evidence_class=evidence_class, source="provider")
        return _model_refusal(
            "MODEL_NOT_AVAILABLE",
            f"{requested} is not in the published enumeration for "
            f"{task['device_ref']}/{task['runtime']}: {list(view['declared'])}",
            evidence_class=evidence_class, source="provider")
    return _model_refusal(
        "MODEL_UNVERIFIED",
        f"{task['device_ref']}/{task['runtime']} publishes no model enumeration; "
        f"{requested} cannot be verified, so it is not chosen",
        evidence_class=evidence_class, source="provider")

def _check_task(task: dict[str, Any], snapshot: dict[str, Any],
                seen_worktrees: set[str],
                now: dt.datetime) -> tuple[list[dict[str, str]], dict[str, Any]]:
    refusals: list[dict[str, str]] = []
    missing = [field for field in TASK_FIELDS if not task.get(field)]
    if missing:
        return [_task_refusal("TASK_INCOMPLETE", f"missing {missing}")], {}

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

    model_decision = _model_decision(task, snapshot, now)
    if model_decision["decision"] == "REFUSED":
        refusals.append(_task_refusal(
            model_decision["code"], model_decision["detail"]))

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
    return refusals, model_decision


def validate_batch(batch: dict[str, Any], snapshot: dict[str, Any], *,
                   now: dt.datetime | None = None) -> dict[str, Any]:
    """Return PASS only when every task can be dispatched as written.

    `now` is injectable for deterministic freshness tests; it defaults to the
    current UTC time. No provider is called, so `provider_calls` is always 0.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    batch_id = batch.get("batch_id")
    tasks = batch.get("tasks") or []
    if not isinstance(batch_id, str) or not batch_id.strip():
        return {"verdict": "BLOCKED", "batch_id": batch_id,
                "blocked": [{"task_id": None, "code": "BATCH_ID_MISSING",
                             "detail": "a persistent Batch ID is required"}],
                "model_decisions": {}, "provider_calls": 0}
    if not tasks:
        return {"verdict": "BLOCKED", "batch_id": batch_id,
                "blocked": [{"task_id": None, "code": "BATCH_EMPTY",
                             "detail": "a batch needs at least one task"}],
                "model_decisions": {}, "provider_calls": 0}

    seen_ids: set[str] = set()
    seen_worktrees: set[str] = set()
    blocked: list[dict[str, Any]] = []
    model_decisions: dict[str, Any] = {}
    for task in tasks:
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in seen_ids:
            blocked.append({"task_id": task_id, "code": "TASK_ID_DUPLICATE",
                            "detail": "task_id must be unique and non-empty"})
            continue
        seen_ids.add(task_id)
        refusals, decision = _check_task(task, snapshot, seen_worktrees, now)
        if decision:
            model_decisions[task_id] = decision
        for refusal in refusals:
            blocked.append({"task_id": task_id, **refusal})
    return {
        "verdict": "PASS" if not blocked else "BLOCKED",
        "batch_id": batch_id,
        "task_count": len(tasks),
        "blocked": blocked,
        "model_decisions": model_decisions,
        "provider_calls": 0,
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
