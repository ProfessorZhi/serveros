"""Receipt truth: keep what the device said, and never invent what it did not.

Two independent losses this file pins, both offline and with synthetic fixtures:

1.  A device refusal arrives as an error envelope, and this surface used to relay
    only its `code`. The machine fields that decide what a caller may do next —
    `retryable`, `details.reason` (`never_issued` vs `expired`), `details.bootId`,
    `details.supported` — were dropped, so `RUN_NOT_FOUND` arrived without the
    `retryable: false` that exists to stop a blind retry, and an unsupported
    permission mode arrived without the mode set it carried.

2.  A native reply has several identities — `deliveryId`, `runId`,
    `queuedMessageUuid`, `blockedBy`, `idKind` — and "the device accepted the
    request" is not any of them. The provider must preserve all of them and must
    not synthesize `accepted: true`. A run that was accepted has not started; a
    run that reports `completed` with no readable native activity is UNKNOWN, not
    DONE; `RUN_NOT_FOUND` is not a completion.

No device, socket, credential or user session is involved. Every payload below is
a shape written by hand.
"""
from __future__ import annotations

import json

import pytest

from fleet_mcp import cloudcli_native
from fleet_mcp.config import ProviderBinding
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.mcp_http import structured
from fleet_mcp.providers import cloudcli as cloudcli_provider

DEVICE = "device:synthetic-receipt"
SESSION = "synthetic-native-session"


# ---------------------------------------------------------------------------
# 1. Structured refusal: a strict whitelist, never remote text
# ---------------------------------------------------------------------------


def _error_envelope(**envelope):
    return {
        "isError": True,
        # The remote prose is deliberately credential-shaped: if this surface
        # ever relays the message, the test says so.
        "content": [{"type": "text", "text": "remote prose sk-live-SYNTHETIC-0123456789"}],
        "structuredContent": envelope,
    }


def test_run_not_found_keeps_retryable_and_reason():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND",
            retryable=False,
            details={"reason": "never_issued", "bootId": "boot-synthetic-1"},
        ))
    refusal = caught.value
    assert refusal.code == "RUN_NOT_FOUND"
    assert refusal.extra.get("retryable") is False
    assert refusal.extra["details"]["reason"] == "never_issued"
    assert refusal.extra["details"]["bootId"] == "boot-synthetic-1"


def test_expired_is_distinct_from_never_issued():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND", retryable=True,
            details={"reason": "expired", "bootId": "boot-synthetic-2"}))
    assert caught.value.extra["details"]["reason"] == "expired"
    assert caught.value.extra["retryable"] is True


def test_supported_mode_set_is_relayed():
    """`UNSUPPORTED_PERMISSION_MODE` carries the set it would have accepted."""
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="UNSUPPORTED_PERMISSION_MODE",
            retryable=False,
            details={"supported": ["default", "acceptEdits"]}))
    assert caught.value.extra["details"]["supported"] == ["default", "acceptEdits"]


def test_remote_message_and_credential_are_never_relayed():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND",
            message="please use sk-live-SYNTHETIC-0123456789",
            retryable=False,
            details={"reason": "never_issued",
                     "secret": "sk-live-SYNTHETIC-0123456789",
                     "token": "ghp_SYNTHETIC0123456789"}))
    rendered = str(caught.value) + json.dumps(caught.value.extra, default=str)
    assert "sk-live-SYNTHETIC" not in rendered
    assert "ghp_SYNTHETIC" not in rendered
    assert "please use" not in rendered


def test_only_the_whitelisted_detail_keys_cross():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND",
            details={"reason": "never_issued", "stack": "at remote()",
                     "path": "/home/remote/secret", "message": "remote text"}))
    details = caught.value.extra["details"]
    assert set(details) <= {"reason", "bootId", "supported", "reason_known"}
    assert "stack" not in details and "path" not in details


def test_malformed_projection_fails_closed():
    """Wrong types are not coerced into a machine field."""
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND",
            retryable="yes",              # not a bool
            details="not-a-dict",         # not an object
        ))
    assert "retryable" not in caught.value.extra
    assert "details" not in caught.value.extra


def test_an_unknown_reason_is_withheld_not_echoed():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="RUN_NOT_FOUND",
            details={"reason": "sk-live-SYNTHETIC-reason"}))
    details = caught.value.extra.get("details") or {}
    assert "reason" not in details
    assert details.get("reason_known") is False
    assert "sk-live" not in json.dumps(caught.value.extra, default=str)


def test_a_non_list_supported_set_is_withheld():
    with pytest.raises(FleetRefusal) as caught:
        structured(_error_envelope(
            code="UNSUPPORTED_PERMISSION_MODE", details={"supported": "default"}))
    assert "supported" not in (caught.value.extra.get("details") or {})


# ---------------------------------------------------------------------------
# 2. Receipt truth: identities preserved, acceptance never invented
# ---------------------------------------------------------------------------


def test_native_receipt_preserves_every_identity_and_never_adds_accepted():
    payload = {
        "deliveryId": "d-1", "runId": "r-1", "queued": True,
        "queuedMessageUuid": "q-1", "blockedBy": "other-writer-run",
        "idKind": "delivery", "bootId": "boot-1",
    }
    truth = cloudcli_provider.receipt_truth(payload)
    assert "accepted" not in truth
    assert truth["native"] == payload
    assert truth["acceptance"] == "QUEUED"


def test_a_run_id_is_started_not_completed():
    truth = cloudcli_provider.receipt_truth(
        {"deliveryId": "d-1", "runId": "r-1", "idKind": "run"})
    assert truth["acceptance"] == "STARTED"
    assert truth["acceptance"] not in {"DONE", "COMPLETED"}


def test_a_session_only_create_is_accepted_without_a_run():
    truth = cloudcli_provider.receipt_truth({"sessionId": "s-1"})
    assert truth["acceptance"] == "ACCEPTED_NO_RUN"


def test_completed_without_native_activity_is_unknown():
    """#394's symptom: `completed` with no readable assistant message is not DONE."""
    truth = cloudcli_provider.receipt_truth({
        "status": "completed", "outcome": "settled",
        "lastAssistantMessage": None,
        "activityNote": "This session has no activity record.",
    })
    assert truth["acceptance"] == "UNKNOWN"
    assert truth["acceptance"] != "DONE"


def test_run_not_found_is_not_done():
    truth = cloudcli_provider.receipt_truth(
        {"code": "RUN_NOT_FOUND", "retryable": False,
         "details": {"reason": "never_issued"}})
    assert truth["acceptance"] == "UNKNOWN"
    assert truth["acceptance"] != "DONE"


def test_an_explicit_failure_still_reads_as_failed():
    truth = cloudcli_provider.receipt_truth({"failed": True, "runId": "r-1"})
    assert truth["acceptance"] == "FAILED"


def test_a_foreign_boot_is_preserved_and_not_read_as_completion():
    """One run id can exist under two device boots. `bootId` crosses verbatim so
    the two are distinguishable; neither is a completion."""
    truth = cloudcli_provider.receipt_truth({
        "idKind": "delivery", "deliveryId": "d-2", "runId": "r-1",
        "bootId": "boot-2", "queued": True,
    })
    assert truth["native"]["bootId"] == "boot-2"
    assert truth["acceptance"] == "QUEUED"
    assert truth["acceptance"] != "DONE"


def test_two_boots_with_one_run_id_are_two_receipts():
    first = cloudcli_provider.receipt_truth({"runId": "r-1", "bootId": "boot-1"})
    second = cloudcli_provider.receipt_truth({"runId": "r-1", "bootId": "boot-2"})
    assert first["native"]["bootId"] != second["native"]["bootId"]


# ---------------------------------------------------------------------------
# 3. ID kinds are explicit; an ambiguous id is refused, never swapped
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, *, response=None):
        self.calls: list[tuple[str, dict]] = []
        self._response = response or {}
        self._schema = {
            "name": "run_get",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "runId": {"type": "string"},
                    "deliveryId": {"type": "string"},
                    "session": {"type": "string"},
                },
                "oneOf": [{"required": ["runId"]}, {"required": ["deliveryId"]}],
                "additionalProperties": False,
            },
        }

    def tools_list(self):
        return [self._schema]

    def tools_call(self, tool, arguments):
        self.calls.append((tool, arguments))
        return {"structuredContent": dict(self._response)}


def _provider(client):
    binding = ProviderBinding(
        device_ref=DEVICE, kind="cloudcli-mcp-http",
        endpoint="http://127.0.0.1:9/mcp", credential_anchor="unused",
    )
    p = cloudcli_provider.CloudCliProvider(binding)
    p._client = lambda: client
    return p


def _key():
    return SessionKey.compose(DEVICE, "claude", SESSION)


def test_get_run_by_delivery_sends_delivery_id_not_run_id():
    client = _FakeClient(response={"idKind": "delivery", "deliveryId": "d-1",
                                   "runId": "r-1", "sessionId": SESSION})
    cloudcli_provider.CloudCliProvider.get_run(
        _provider(client), _key(), "d-1", id_kind="delivery")
    tool, arguments = client.calls[-1]
    assert tool == "run_get"
    assert arguments == {"deliveryId": "d-1", "session": SESSION}
    assert "runId" not in arguments


def test_get_run_defaults_to_run_id():
    client = _FakeClient(response={"idKind": "run", "runId": "r-1"})
    cloudcli_provider.CloudCliProvider.get_run(_provider(client), _key(), "r-1")
    _, arguments = client.calls[-1]
    assert arguments == {"runId": "r-1", "session": SESSION}


def test_a_contradicting_id_kind_is_refused():
    """The device said it answered by run; the caller asked by delivery."""
    client = _FakeClient(response={"idKind": "run", "runId": "r-1"})
    with pytest.raises(FleetRefusal) as caught:
        cloudcli_provider.CloudCliProvider.get_run(
            _provider(client), _key(), "d-1", id_kind="delivery")
    assert caught.value.code == "BAD_SESSION_REF"


def test_an_unknown_id_kind_is_refused():
    client = _FakeClient(response={"idKind": "uuid", "deliveryId": "d-1"})
    with pytest.raises(FleetRefusal):
        cloudcli_provider.CloudCliProvider.get_run(
            _provider(client), _key(), "d-1", id_kind="delivery")


def test_an_unknown_id_kind_argument_is_refused_before_a_call():
    client = _FakeClient()
    with pytest.raises(FleetRefusal):
        cloudcli_provider.CloudCliProvider.get_run(
            _provider(client), _key(), "x", id_kind="guess")
    assert client.calls == []


def test_a_refusal_is_not_retried_by_the_provider():
    """One refusal is one answer. The surface above may decide; this layer does not."""
    from fleet_mcp.mcp_http import ProviderRefusal

    class _Refuses(_FakeClient):
        def tools_call(self, tool, arguments):
            self.calls.append((tool, arguments))
            raise ProviderRefusal("RUN_NOT_FOUND", "refused", status=404)

    client = _Refuses()
    with pytest.raises(FleetRefusal):
        _provider(client).send(_key(), "one whole task")
    assert len(client.calls) == 1


# ---------------------------------------------------------------------------
# 4. A permission-mode set is not a model list, and a refusal is not a stop
# ---------------------------------------------------------------------------


def test_a_permission_mode_set_is_named_as_modes_never_as_models():
    interpreted = cloudcli_native.interpret_supported(
        code="UNSUPPORTED_PERMISSION_MODE",
        details={"supported": ["default", "acceptEdits"]})
    assert interpreted == {"permission_modes": ["default", "acceptEdits"]}
    assert "models" not in interpreted


def test_a_model_allowlist_is_not_a_live_model_list():
    """No code path here enumerates live models from an allowlist."""
    assert not hasattr(cloudcli_native, "list_models")
    interpreted = cloudcli_native.interpret_supported(
        code="SOMETHING_ELSE", details={"supported": ["m-1", "m-2"]})
    assert interpreted == {}


def test_no_run_level_stop_is_invented():
    """The provider exposes session/message/task stops, never a run stop."""
    for name in ("stop_run", "cancel_run", "kill_run"):
        assert not hasattr(cloudcli_provider.CloudCliProvider, name), name
