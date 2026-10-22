"""Actual SESSION_BUSY must not turn into a successful delivery receipt."""
import json

import pytest

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.mcp_http import structured
from test_fleet_mcp_surface import Harness, TOKYO
from test_fleet_create_session import Harness as CreateHarness


@pytest.mark.parametrize("body", [
    {"structuredContent": {"code": "SESSION_BUSY", "message": "synthetic-secret"}},
    {"content": [{"type": "text", "text": json.dumps(
        {"code": "SESSION_BUSY", "message": "synthetic-secret"})}]},
])
def test_structured_errors_are_refusals_before_any_payload_is_used(body):
    with pytest.raises(FleetRefusal) as caught:
        structured({"isError": True, **body})
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("keyed", [False, True])
def test_busy_without_run_or_queue_is_never_an_accepted_send(tmp_path, keyed):
    key = SessionKey.compose(TOKYO, "claude", "owned-session")
    h = Harness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    calls = []

    def busy(*args, **kwargs):
        calls.append(True)
        return {"code": "SESSION_BUSY", "retryable": True,
                "details": {"runId": "another-writers-current-run"}}

    h.tokyo.send = busy
    for _ in range(2 if keyed else 1):
        with pytest.raises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), key, message="one whole task",
                                     idempotency_key="same-task" if keyed else None)
        assert caught.value.code == "DISPATCH_OUTCOME_UNKNOWN"
    assert len(calls) == 1


@pytest.mark.parametrize("response,message", [
    ({"sessionId": "new-session", "accepted": False}, None),
    ({"sessionId": "new-session"}, "start the whole task"),
])
def test_unkeyed_create_does_not_claim_a_rejected_or_missing_execution_ack(tmp_path, response, message):
    h = CreateHarness(tmp_path)
    h.providers[TOKYO].create = lambda **kwargs: response
    with pytest.raises(FleetRefusal) as caught:
        h.service.create_session(h.principal(), device=TOKYO, project="p-1", message=message)
    assert caught.value.code == "DISPATCH_OUTCOME_UNKNOWN"
