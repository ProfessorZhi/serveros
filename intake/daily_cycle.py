"""Daily-work policy over trusted Work Engine projections, not another scheduler.

No IO, model calls, Item writes or authorization minting occur here. The caller
must supply authenticated grants and fresh observations from their owners.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

BUSY = frozenset({"ACTIVE", "DELEGATED", "WAITING", "NEEDS_HUMAN", "CANCELLING", "EFFECT_UNKNOWN"})
ELIGIBLE = frozenset({"AVAILABLE", "READY"})
TERMINAL = frozenset({"CLOSED", "DELIVERED", "CANCELLED"})
PROTECTED_EFFECTS = frozenset({"production.cutover", "production.rollback", "production.mutate"})

# The two fields a projection may carry about whether something is still held
# on an Item's behalf. Neither is produced anywhere on this node as of
# 2026-09-22: `intake/focus_source.py` reads the personal board, which carries
# neither, and says so per row. See `liveness` for why their absence is not
# read as evidence.
LIVENESS_FIELDS = ("worker_stopped", "resources_released")
# The accepted priority vocabulary. A row whose `priority` is not one of these
# strings has no established priority — checked with `isinstance` rather than
# with a bare membership test, because the personal board publishes priority as
# a `{personal, source}` object and `{...} in {"P0"}` raises TypeError: a
# projection this policy is pointed at must not crash it.
PRIORITY_LEVELS = frozenset({"P0", "P1", "P2", "P3", "P4"})


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def fresh(observed_at: str, now: datetime, ttl_seconds: int) -> bool:
    if now.tzinfo is None or type(ttl_seconds) is not int or ttl_seconds <= 0:
        raise ValueError("aware now and positive integer TTL required")
    age = (now - instant(observed_at)).total_seconds()
    return 0 <= age <= ttl_seconds


def grant_reason(grant: dict, *, item: dict, action: str, now: datetime) -> str | None:
    """Validate scope/time/budget, not the authenticity of the supplied grant."""
    if not grant.get("ref") or grant.get("revoked") is not False:
        return "MISSING_OR_REVOKED_GRANT"
    try:
        if not instant(grant["valid_from"]) <= now < instant(grant["expires_at"]):
            return "GRANT_NOT_CURRENT"
    except (KeyError, ValueError, TypeError):
        return "INVALID_GRANT_TIME"
    if item.get("project") not in grant.get("projects", []):
        return "PROJECT_OUT_OF_SCOPE"
    if item.get("id") not in grant.get("items", []):
        return "ITEM_NOT_AUTHORIZED"
    if action not in grant.get("actions", []):
        return "ACTION_NOT_AUTHORIZED"
    if not item.get("scope_digest") or item["scope_digest"] != grant.get("scope_digests", {}).get(item.get("id")):
        return "SCOPE_CHANGED"
    budget = grant.get("budget", {})
    for key in ("wall_seconds", "max_attempts", "max_output_bytes"):
        if type(budget.get(key)) is not int or budget[key] <= 0:
            return "MISSING_BOUNDED_BUDGET"
    if budget.get("exhausted") is not False:
        return "BUDGET_UNAVAILABLE"
    return None


def liveness(row: dict) -> tuple[bool | None, list[str]]:
    """Is something still held on this Item's behalf, and on whose word.

    THREE-valued, and the third value is the point. `True` and `False` are the
    row saying so; `None` is the row not saying. Only fields that are actually
    present are read, so a projection that carries one of the two answers about
    that one.

    The version this replaces was a single expression —
    `row.get("worker_stopped") is not True or row.get("resources_released") is
    not True` — which collapses "the field is absent" into "the field is False"
    and makes a silent projection read as a live worker. Measured against the
    live 17-row personal board, that returned all 17 rows as carryover,
    including the 7 CLOSED and 4 DELIVERED ones, and reported a node with four
    Items in flight as OVER_CAPACITY on the strength of fourteen that had
    finished. `tests/test_focus_source.py` carries that measurement against the
    captured board; the direction is not a matter of taste, because a
    projection that is silent about a worker is not evidence of one.
    """
    present = [(field, row[field]) for field in LIVENESS_FIELDS
               if row.get(field) is not None]
    if not present:
        return None, []
    held = [field for field, value in present if value is not True]
    return bool(held), held


def select_focus(rows: list[dict], grants: dict[str, dict], *, now: datetime,
                 observed_at: str, ttl_seconds: int = 900, limit: int = 2) -> dict:
    """At most two root objectives INCLUDING unresolved work already in progress.

    Rows are normalized by the board adapter (`intake/focus_source.py`, reached
    as `serverosctl work focus`); absence is not an empty project. PARKED
    requires positive evidence that no worker or reservation is still held
    before it releases its slot.

    Two things this reads in the three-valued way rather than the permissive
    one:

      * `worker_stopped` / `resources_released` — see `liveness`. Where a
        projection is silent, `state` is what decides: work-engine's state
        machine is the authority for whether work is in progress, and these two
        fields are a secondary alarm, read only when the row actually asserts
        them. Rows whose liveness was not decidable are listed in
        `unevaluated`, so a reader is told rather than reassured.
      * `TERMINAL` was declared and never consulted. 11 of the 17 live rows are
        in a terminal state (7 CLOSED, 4 DELIVERED) and each of them used to be
        reported as in flight. A terminal row now leaves the decision with the
        reason `TERMINAL_STATE`: it neither holds a slot nor accepts new work.
        DELIVERED is in that set deliberately — an Item delivered and not yet
        closed has finished its implementation, and what it is waiting for is
        acceptance, which is not something this function can start.
    """
    if type(limit) is not int or not 0 < limit <= 2:
        raise ValueError("this profile supports one or two focus objectives")
    if not fresh(observed_at, now, ttl_seconds):
        return {"status": "BLOCKED", "reason": "STALE_BOARD", "selected": [], "carryover": []}
    ids = [r.get("id") for r in rows]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise ValueError("missing or duplicate canonical Item ID")
    carry, candidates, excluded, unevaluated = [], [], [], []
    carryover_because: dict[str, str] = {}
    for row in rows:
        held, held_fields = liveness(row)
        state = row.get("state")
        parked = (row.get("focus_disposition") == "PARKED" and
                  row.get("parking_acknowledged") is True)
        if held:
            carry.append(row["id"])
            carryover_because[row["id"]] = (
                f"WORKER_OR_RESERVATION_HELD:{','.join(held_fields)}")
            continue
        if parked and held is not False:
            # Undoing a park needs positive evidence that nothing is held any
            # more. The board carries no liveness field, so on this node a
            # parked row never releases its slot by being read — which is the
            # answer the design asks for: parking is acknowledged, not asserted.
            carry.append(row["id"])
            carryover_because[row["id"]] = "PARKED_RELEASE_UNCONFIRMED"
            continue
        if state in TERMINAL:
            excluded.append({"id": row["id"], "reason": "TERMINAL_STATE"})
            continue
        if parked:
            excluded.append({"id": row["id"], "reason": "PARKED_NOT_ELIGIBLE"})
            continue
        if state in BUSY:
            carry.append(row["id"])
            carryover_because[row["id"]] = "STATE_HOLDS_SLOT"
            continue
        if state not in ELIGIBLE:
            excluded.append({"id": row["id"], "reason": "STATE_NOT_ELIGIBLE"})
            continue
        if held is None:
            unevaluated.append({
                "id": row["id"], "fields": list(LIVENESS_FIELDS),
                "because": "this projection carries neither field, so whether a "
                           "worker still holds the Item was not decided here; "
                           "the Item's own state is what admitted it"})
        action = row.get("next_action", "research")
        reason = ("HUMAN_EXECUTED" if action in PROTECTED_EFFECTS else
                  grant_reason(grants.get(row.get("grant_ref"), {}), item=row,
                               action=action, now=now))
        priority = row.get("priority")
        if not (isinstance(priority, str) and priority in PRIORITY_LEVELS):
            reason = reason or "PRIORITY_NOT_ESTABLISHED"
        if row.get("blocked_by"):
            reason = reason or "DEPENDENCY_BLOCKED"
        if reason:
            excluded.append({"id": row["id"], "reason": reason})
        else:
            candidates.append(row)
    # Priority must come from the accepted board, not LLM-generated popularity.
    candidates.sort(key=lambda r: (r["priority"], r.get("created_at", ""), r["id"]))
    slots = max(0, limit - len(carry))
    chosen = candidates[:slots]
    return {"status": "OVER_CAPACITY" if len(carry) > limit else "PLANNED",
            "selected": [r["id"] for r in chosen], "carryover": carry,
            "carryover_because": carryover_because, "unevaluated": unevaluated,
            "unused_slots": max(0, slots-len(chosen)), "excluded": excluded,
            "selection_digest": fingerprint({"selected": chosen, "carryover": carry,
                                              "observed_at": observed_at}),
            "admission_required": True}


def review_gate(packet: dict, review: dict, grant: dict, *, now: datetime) -> dict:
    """Check a receipt from a TRUSTED review adapter, never raw model JSON alone.

    This read-only decision is not a Work Engine admission or filesystem sandbox.
    The authoritative adapter must re-check it under its existing admission lock.
    """
    def stop(reason: str) -> dict:
        return {"decision": "BLOCKED", "reason": reason}
    action = packet.get("action")
    if action in PROTECTED_EFFECTS:
        return {"decision": "HUMAN_EXECUTED", "reason": "PROTECTED_PRODUCTION_EFFECT"}
    reason = grant_reason(grant, item=packet, action=action, now=now)
    if reason:
        return stop(reason)
    if (review.get("adapter_verified") is not True or
            type(review.get("process_exit_code")) is not int or review["process_exit_code"] != 0):
        return stop("REVIEW_RECEIPT_UNVERIFIED")
    if review.get("verdict") != "APPROVE":
        return stop("REVIEW_NOT_APPROVED")
    if not isinstance(packet.get("author_session"), str) or not packet["author_session"].strip():
        return stop("AUTHOR_SESSION_UNRESOLVED")
    if (not isinstance(review.get("reviewer_session"), str) or
            not review["reviewer_session"].strip() or review["reviewer_session"] == packet["author_session"]):
        return stop("REVIEW_NOT_INDEPENDENT")
    if review.get("model") not in packet.get("allowed_review_models", []):
        return stop("REVIEW_MODEL_MISMATCH")
    if review.get("reasoning_effort") not in {"high", "xhigh"}:
        return stop("REVIEW_DEPTH_INSUFFICIENT")
    try:
        if not fresh(review["observed_at"], now, packet["review_ttl_seconds"]):
            return stop("REVIEW_EXPIRED")
    except (KeyError, TypeError, ValueError):
        return stop("INVALID_REVIEW_TIME")
    bindings = ("id", "scope_digest", "source_digest", "design_digest", "plan_digest", "baseline_digest")
    if any(not packet.get(k) or review.get(k) != packet[k] for k in bindings):
        return stop("REVIEW_INPUT_CHANGED")
    if packet.get("binding_ready") is not True:
        return stop("ENVIRONMENT_NOT_READY")
    if packet.get("prior_effect") in {"UNKNOWN", "PARTIAL"}:
        return stop("RECONCILE_PRIOR_EFFECT_FIRST")
    return {"decision": "ELIGIBLE_FOR_ADMISSION", "reason": "CURRENT_SCOPE_AND_REVIEW_MATCH",
            "execution_authorized": False}


def invalidate_claims(claims: list[dict], source_revisions: dict[str, str]) -> list[dict]:
    """Preserve history while preventing superseded/withdrawn facts from recall."""
    result = []
    for claim in claims:
        row = dict(claim)
        refs = row.get("source_revisions", {})
        valid_refs = (isinstance(refs, dict) and bool(refs) and
                      all(isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
                          and k in source_revisions for k, v in refs.items()))
        if not valid_refs or any(source_revisions[k] != v for k, v in refs.items()):
            row.update(recallable=False, verification="STALE_OR_UNRESOLVED_SOURCE")
        elif row.get("verification") == "EVIDENCED" and row.get("withdrawn") is False:
            row["recallable"] = True
        else:
            row["recallable"] = False
        result.append(row)
    return result
