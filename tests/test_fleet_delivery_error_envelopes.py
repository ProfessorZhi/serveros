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


@pytest.mark.parametrize("remote_code", [
    "SESSION_NOT_FOUND",
    "PROJECT_NOT_FOUND",
    "RUN_NOT_FOUND",
    "SESSION_BUSY",
])
def test_a_device_code_with_an_exact_counterpart_keeps_its_meaning(remote_code):
    """Same word on both sides means it is not reported as a Request problem."""
    with pytest.raises(FleetRefusal) as caught:
        structured({
            "isError": True,
            "content": [{"type": "text", "text": "synthetic-secret"}],
            "structuredContent": {"code": remote_code, "message": "synthetic-secret"},
        })
    assert caught.value.code == remote_code
    assert "synthetic-secret" not in str(caught.value)


@pytest.mark.parametrize("remote_code", [
    "SESSION_HOST_NOT_FOUND",
    "PROJECT_WORKSPACE_ROOT_REQUIRED",
    "NO_CLIENT",
])
def test_an_untranslated_device_code_is_a_bad_request_not_our_defect(remote_code):
    """The device declining is the device's behaviour.

    `INTERNAL` means "a defect in this surface". Reporting a device's own
    refusal under it sends an operator to the wrong machine — which is exactly
    what a close on a never-started session did on 2026-10-23, arriving as
    `INTERNAL: SESSION_HOST_NOT_FOUND`.
    """
    with pytest.raises(FleetRefusal) as caught:
        structured({
            "isError": True,
            "content": [{"type": "text", "text": "synthetic-secret"}],
            "structuredContent": {"code": remote_code, "message": "synthetic-secret"},
        })
    assert caught.value.code == "BAD_REQUEST"
    assert remote_code in caught.value.detail
    assert "synthetic-secret" not in str(caught.value)


def test_a_device_refusal_is_described_as_one():
    """It answered. "answered unusably" would describe the shape, not the fact."""
    with pytest.raises(FleetRefusal) as caught:
        structured({
            "isError": True,
            "content": [{"type": "text", "text": "no host for this session"}],
            "structuredContent": {"code": "SESSION_HOST_NOT_FOUND"},
        })
    assert "refused the call" in caught.value.detail
    assert "answered unusably" not in caught.value.detail


def test_an_error_with_text_but_no_code_is_a_bad_request():
    with pytest.raises(FleetRefusal) as caught:
        structured({"isError": True, "content": [{"type": "text", "text": "refused"}]})
    assert caught.value.code == "BAD_REQUEST"


def test_an_error_with_neither_text_nor_code_is_still_our_gap():
    """Nothing to report and nothing to name: this surface cannot tell what happened."""
    with pytest.raises(FleetRefusal) as caught:
        structured({"isError": True, "content": []})
    assert caught.value.code == "INTERNAL"
