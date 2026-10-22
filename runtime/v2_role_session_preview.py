"""Fleet V2 role-to-Session routing preview; never admission or delegation.

Role is a project-scoped requirement, not an identity, grant or process.
This pure function reads supplied claims and never authenticates a Node,
touches a provider, issues authority, persists an assignment or writes effects.
Do not consume any proposal as permission to create/resume/send/commit.
"""
from __future__ import annotations

from math import isfinite

SCHEMA = "serveros/v2-role-session-preview.v0"
FRESHNESS_S = 60
NOTE = ("Planning evidence only; no identity, OS isolation, grant, owner epoch, "
        "writer lease, provider entitlement or effect permission is verified. "
        "Re-read these from their authorities at dispatch and effect commit.")


def _string(record, key):
    value = record.get(key)
    return isinstance(value, str) and bool(value.strip())


def _strings(value):
    return (isinstance(value, list)
            and all(isinstance(v, str) and bool(v.strip()) for v in value)
            and len(value) == len(set(value)))


def _valid_input(snapshot, now, max_age_s):
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA:
        return False
    if type(now) not in (int, float) or not isfinite(now):
        return False
    if type(max_age_s) not in (int, float) or not isfinite(max_age_s) or max_age_s <= 0:
        return False
    work, role = snapshot.get("work"), snapshot.get("role")
    if not isinstance(work, dict) or not isinstance(role, dict):
        return False
    if not all(_string(work, k) for k in ("work_ref", "project_ref")):
        return False
    if type(work.get("epoch")) is not int or work["epoch"] < 1:
        return False
    if not _strings(work.get("allowed_executor_devices")) or not work["allowed_executor_devices"]:
        return False
    if type(work.get("protected_effect")) is not bool:
        return False
    if not all(_string(role, k) for k in ("role_ref", "project_ref")):
        return False
    if not _strings(role.get("required_abilities")) or not _strings(role.get("allowed_families")):
        return False
    if not role["allowed_families"]:
        return False
    return isinstance(snapshot.get("runtimes"), list)


def _session_action(row, work):
    """Plan same-Work resume or a new, clean, immutably bound Session only.

    Missing inventory is not empty inventory. Existing conversations are never
    retargeted; busy or unknown Sessions are never preempted.
    """
    if "session" not in row:
        return None, "SESSION_INVENTORY_UNKNOWN"
    session = row["session"]
    if session is not None:
        if not isinstance(session, dict) or not all(
                _string(session, k) for k in (
                    "session_ref", "bound_work_ref", "bound_project_ref",
                    "runtime_ref", "os_principal")):
            return None, "SESSION_PROVENANCE_UNKNOWN"
        if session.get("status") != "IDLE":
            return None, "SESSION_NOT_PROVEN_IDLE"
        if session["runtime_ref"] != row["runtime_ref"] or session["os_principal"] != row["os_principal"]:
            return None, "SESSION_RUNTIME_OR_PRINCIPAL_MISMATCH"
        same_work = (
            session["bound_work_ref"] == work["work_ref"]
            and session["bound_project_ref"] == work["project_ref"]
            and type(session.get("bound_epoch")) is int
            and session["bound_epoch"] == work["epoch"]
        )
        independent = session["session_ref"] == work.get("independent_from_session_ref")
        if same_work and not independent:
            return "RESUME_SAME_WORK_SESSION", "SAME_WORK_AND_EPOCH"
    if row.get("clean_session_support") != "SUPPORTED":
        return None, "CLEAN_SESSION_UNSUPPORTED"
    if row.get("workspace_isolation") != "VERIFIED":
        return None, "CLEAN_WORKSPACE_UNVERIFIED"
    return "CREATE_CLEAN_SESSION", "NEW_IMMUTABLE_WORK_BINDING_REQUIRED"


def preview(snapshot, *, now, max_age_s=FRESHNESS_S):
    """Pure deterministic proposals; no final selection, grants or writes."""
    result = {
        "schema": SCHEMA, "status": "INVALID_PREVIEW_INPUT",
        "proposals": [], "refusals": [], "writes": [],
        "admission": "NOT_EVALUATED", "effect_commit": "NOT_EVALUATED",
        "note": NOTE,
    }
    if not _valid_input(snapshot, now, max_age_s):
        return result
    work, role = snapshot["work"], snapshot["role"]
    if work["project_ref"] != role["project_ref"]:
        result["status"] = "NO_SAFE_PREVIEW"
        result["refusals"].append({"reason": "ROLE_PROJECT_MISMATCH"})
        return result
    seen = set()
    for index, row in enumerate(snapshot["runtimes"]):
        why = []
        if not isinstance(row, dict):
            result["refusals"].append({"index": index, "reason": "RUNTIME_IDENTITY_UNKNOWN"})
            continue
        required = ("device_ref", "os_principal", "runtime_ref", "credential_instance", "family")
        if not all(_string(row, k) for k in required):
            result["refusals"].append({"index": index, "reason": "RUNTIME_IDENTITY_UNKNOWN"})
            continue
        identity = tuple(row[k] for k in required)
        session = row.get("session")
        session_id = session.get("session_ref") if isinstance(session, dict) else "NEW"
        key = (*identity, session_id)
        if key in seen:
            why.append("DUPLICATE_RUNTIME_SESSION_IDENTITY")
        seen.add(key)
        if row["device_ref"] not in work["allowed_executor_devices"]:
            why.append("EXECUTOR_DEVICE_NOT_ALLOWED")
        if row["family"] not in role["allowed_families"]:
            why.append("PROVIDER_NOT_IN_ROLE_REQUIREMENTS")
        if row.get("support") != "SUPPORTED":
            why.append("PROVIDER_SUPPORT_NOT_PROVEN")
        if row.get("readiness") != "READY" or row.get("slot") != "AVAILABLE":
            why.append("RUNTIME_NOT_READY_OR_FREE")
        sample_at = row.get("observed_at")
        if (type(sample_at) not in (int, float) or not isfinite(sample_at)
                or sample_at > now or now - sample_at > max_age_s):
            why.append("RUNTIME_EVIDENCE_STALE_OR_UNKNOWN")
        abilities = row.get("effective_abilities")
        if not _strings(abilities) or not set(role["required_abilities"]).issubset(abilities):
            why.append("ABILITY_NOT_PROVEN_EFFECTIVE")
        if why:
            result["refusals"].append({
                "index": index, "identity": list(identity), "reasons": why,
            })
            continue
        action, reason = _session_action(row, work)
        if action is None:
            result["refusals"].append({
                "index": index, "identity": list(identity), "reasons": [reason],
            })
            continue
        result["proposals"].append({
            "identity": list(identity),
            "session_ref": session_id if action.startswith("RESUME") else None,
            "work_ref": work["work_ref"], "owner_epoch_claim": work["epoch"],
            "role_ref": role["role_ref"], "plan": action, "reason": reason,
            "requires_current_grant": True, "requires_writer_fence": True,
            "requires_effect_approval": work["protected_effect"],
        })
    result["status"] = "ROUTE_PREVIEW_ONLY" if result["proposals"] else "NO_SAFE_PREVIEW"
    return result
