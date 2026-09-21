"""Normalize a completed native GPT review; model text never grants authority."""
from __future__ import annotations
import json
from intake.daily_cycle import instant

BINDINGS = ("id", "scope_digest", "source_digest", "design_digest", "plan_digest", "baseline_digest")


def normalize_review(*, events: list[dict], final_text: str | None, process_receipt: dict,
                     packet: dict, configured_model: str, reasoning_effort: str,
                     observed_at: str) -> dict:
    """Called by the trusted invoker with its own inputs, not fields from a model.

    The invoker attests its requested configuration and session, not cryptographic
    proof of hosted model weights. Admission independently checks scope/expiry.
    """
    def reject(reason):
        return {"adapter_verified": False, "verdict": "INCOMPLETE", "reason": reason}
    instant(observed_at)
    rc = process_receipt.get("process_exit_code")
    if process_receipt.get("status") != "EXITED" or type(rc) is not int or rc != 0:
        return reject("PROCESS_NOT_SUCCESSFUL")
    if any(e.get("type") in {"error", "turn.failed"} for e in events):
        return reject("REVIEW_STREAM_FAILED")
    threads = {e.get("thread_id") for e in events if e.get("type") == "thread.started"}
    if len(threads) != 1 or not next(iter(threads), None):
        return reject("REVIEW_SESSION_UNRESOLVED")
    kinds = [e.get("type") for e in events]
    if "turn.completed" not in kinds:
        return reject("NO_COMPLETED_TURN")
    if (kinds.count("turn.started") != 1 or kinds.count("turn.completed") != 1 or
            kinds.index("turn.started") >= kinds.index("turn.completed")):
        return reject("REVIEW_TURN_AMBIGUOUS")
    try:
        body = json.loads(final_text) if final_text else None
    except (ValueError, TypeError):
        return reject("INVALID_REVIEW_JSON")
    if not isinstance(body, dict) or set(body) != {"verdict", "findings", "limitations"}:
        return reject("REVIEW_SCHEMA_MISMATCH")
    if not isinstance(body["verdict"], str) or body["verdict"] not in {"APPROVE", "CHANGES_REQUIRED"}:
        return reject("INVALID_REVIEW_VERDICT")
    if any(not isinstance(body[k], list) or any(not isinstance(x, str) for x in body[k])
           for k in ("findings", "limitations")):
        return reject("REVIEW_SCHEMA_MISMATCH")
    # The output file alone could be stale; bind it to this completed native turn.
    start, end = kinds.index("turn.started"), kinds.index("turn.completed")
    messages = [e.get("item", {}).get("text") for e in events[start+1:end]
                if e.get("type") == "item.completed" and e.get("item", {}).get("type") == "agent_message"]
    try:
        if not messages or json.loads(messages[-1]) != body:
            return reject("FINAL_NOT_BOUND_TO_COMPLETED_TURN")
    except (ValueError, TypeError):
        return reject("FINAL_NOT_BOUND_TO_COMPLETED_TURN")
    if any(not isinstance(packet.get(k), str) or not packet[k] for k in BINDINGS):
        return reject("REVIEW_INPUT_BINDINGS_MISSING")
    return {**{k: packet[k] for k in BINDINGS}, **body, "adapter_verified": True,
            "reviewer_session": next(iter(threads)), "model": configured_model,
            "reasoning_effort": reasoning_effort, "observed_at": observed_at,
            "process_exit_code": rc, "execution_authorized": False}
