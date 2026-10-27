"""CloudCLI 1.41's `run_get` contract, as the Windows device actually answered it.

Where the payloads come from
----------------------------
Every payload below is copied verbatim from the offline capture in PR #411
(`tests/data/capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json`); nothing
here was re-captured, and no model request is made. PR #411 owns that capture on
`main`; this module only needs three of its answers, so it carries them inline
instead of a second 130 KB copy of the whole file, and it checks the inline copy
against the capture whenever the capture is present.

What the device established, and what these tests hold:

* `run_get` declares `runId`, `deliveryId`, `waitSeconds`, `session`, and the
  1.40-era aliases `run` and `wait`, with NO static `required`; "exactly one
  identifier" is enforced at call time. A mirror that recorded only 1.40's
  shape refuses arguments the device accepts.
* A `deliveryId` a queued send hands back is NOT resolvable: the device answers
  `RUN_NOT_FOUND` / `never_issued` while that send's `runId` is readable. So a
  delivery is correlation, and the acknowledged `runId` is the read handle — a
  receipt that carries both must still be observable, and must not become a
  provider-error loop or a resend.
* A run can read `status: "completed"` with `lastAssistantMessage: null` and the
  device's own note that the session has no readable assistant message. That is
  not success, and must not be projected as COMPLETED.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.run_watcher import RunWatcher
from test_fleet_events import store
from test_fleet_run_watcher import rows

DATA = Path(__file__).resolve().parent / "data"
TOOLS_CAPTURE = DATA / "capture-cloudcli-1.41.0-tools-list-2026-10-27.json"
ROUNDTRIP_CAPTURE = DATA / "capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json"

#: The six properties the device's `run_get.inputSchema` declares (capture,
#: 2026-10-27). Four are 1.40's; `deliveryId` is new in 1.41 and `run`/`wait` are
#: the aliases the device still accepts.
DEVICE_RUN_GET_PROPERTIES = frozenset(
    {"runId", "deliveryId", "waitSeconds", "session", "run", "wait"}
)

RUN_ID = "a1b1c184-ea82-4079-8da9-a96bd3568cc3"
SESSION_ID = "676632e0-630a-4ad3-8fab-6e977aa19e9a"
QUEUED_DELIVERY = "f484d317-523d-4f4e-87f6-13e5ca0beee8"
EMPTY_RUN_ID = "6d0aaa3b-7cfa-4dea-bf12-09746727a212"
EMPTY_SESSION_ID = "094e49af-7547-409a-b32b-c945203d9d14"

#: `run_get_hit_completed.result`, verbatim.
RUN_COMPLETED = {
    "idKind": "run",
    "runId": RUN_ID,
    "deliveryId": None,
    "sessionId": SESSION_ID,
    "source": "mcp",
    "status": "completed",
    "phase": None,
    "toolName": None,
    "activityNote": "This session has no activity record.",
    "startedAt": {"relative": "just now", "iso": "2026-10-27T08:16:21.201Z"},
    "completedAt": {"relative": "just now", "iso": "2026-10-27T08:16:31.734Z"},
    "elapsedMs": 10615,
    "bootId": "a6d0f308-81df-4edb-8811-66400b51870e",
    "outcome": "settled",
    "reason": None,
    "lastAssistantMessage": {
        "id": "45dee66c-1e85-45ac-96b9-ec35b1a74af7_0",
        "sessionId": SESSION_ID,
        "timestamp": "2026-10-27T08:16:31.167Z",
        "provider": "claude",
        "kind": "text",
        "role": "assistant",
        "content": "COMPAT-OK",
        "forkAnchorId": "45dee66c-1e85-45ac-96b9-ec35b1a74af7",
    },
    "note": None,
    "explanation": None,
}

#: `codex_run_completed_without_message.result`, verbatim: the provider failed
#: (usage limit, read from the native rollout) and the gateway still says completed.
RUN_COMPLETED_WITHOUT_OUTPUT = {
    "idKind": "run",
    "runId": EMPTY_RUN_ID,
    "deliveryId": None,
    "sessionId": EMPTY_SESSION_ID,
    "source": "mcp",
    "status": "completed",
    "phase": None,
    "toolName": None,
    "activityNote": "This session has no activity record.",
    "startedAt": {"relative": "just now", "iso": "2026-10-27T08:20:15.738Z"},
    "completedAt": {"relative": "just now", "iso": "2026-10-27T08:20:26.174Z"},
    "elapsedMs": 10506,
    "bootId": "a6d0f308-81df-4edb-8811-66400b51870e",
    "outcome": "settled",
    "reason": None,
    "lastAssistantMessage": None,
    "note": "This run has ended, but the session has no readable assistant message.",
    "explanation": None,
}

#: `run_get_delivery_miss.result`, verbatim: never_issued for a delivery the
#: same build had just handed back from a queued send.
DELIVERY_NEVER_ISSUED = {
    "code": "RUN_NOT_FOUND",
    "message": "This deliveryId was never issued.",
    "retryable": False,
    "details": {
        "idKind": "delivery",
        "runId": None,
        "deliveryId": QUEUED_DELIVERY,
        "reason": "never_issued",
        "bootId": "a6d0f308-81df-4edb-8811-66400b51870e",
        "fallback": {
            "session": None,
            "messages": [],
            "note": "No session can be determined, so there is nothing to fall back to.",
        },
    },
}


def _capture(name: str) -> dict:
    if not ROUNDTRIP_CAPTURE.exists():
        pytest.skip("PR #411 offline capture is not on this base")
    with ROUNDTRIP_CAPTURE.open(encoding="utf-8") as handle:
        return json.load(handle)["cases"][name]["result"]


def test_inline_payloads_are_the_capture_verbatim():
    # Guards the inline copies against drift: if #411's capture changes, this
    # test says so rather than quietly testing a stale shape.
    assert _capture("run_get_hit_completed") == RUN_COMPLETED
    assert _capture("codex_run_completed_without_message") == RUN_COMPLETED_WITHOUT_OUTPUT
    assert _capture("run_get_delivery_miss") == DELIVERY_NEVER_ISSUED


def test_device_capture_declares_exactly_the_six_run_get_parameters():
    if not TOOLS_CAPTURE.exists():
        pytest.skip("PR #411 offline capture is not on this base")
    with TOOLS_CAPTURE.open(encoding="utf-8") as handle:
        tools = {tool["name"]: tool for tool in json.load(handle)["tools"]}
    schema = tools["run_get"]["inputSchema"]
    assert set(schema["properties"]) == set(DEVICE_RUN_GET_PROPERTIES)
    assert "required" not in schema


# --------------------------------------------------------------------------- #
# the mirror's declaration and validation
# --------------------------------------------------------------------------- #
def _run_get_schemas():
    from fleet_mcp import cloudcli_native as native

    return [
        native.input_schema("run_get"),
        native.mirrored_schema(native.NATIVE_BY_NAME["run_get"])["properties"]["arguments"],
    ]


def test_mirror_declares_every_parameter_the_device_declares():
    # A mirror that omits `deliveryId`/`run`/`wait` refuses, as an unknown
    # parameter, an argument set the device accepts.
    for schema in _run_get_schemas():
        assert set(DEVICE_RUN_GET_PROPERTIES) - set(schema["properties"]) == set()


@pytest.mark.parametrize("arguments", [
    {"runId": RUN_ID, "session": SESSION_ID},
    {"run": RUN_ID, "session": SESSION_ID},
    {"deliveryId": QUEUED_DELIVERY, "session": SESSION_ID},
    {"runId": RUN_ID, "session": SESSION_ID, "wait": True},
    {"runId": RUN_ID, "waitSeconds": 5},
])
def test_mirror_accepts_every_argument_shape_the_evidence_supports(arguments):
    from fleet_mcp import cloudcli_native as native

    for schema in _run_get_schemas():
        assert native.validate_arguments(arguments, schema) == []


@pytest.mark.parametrize("arguments", [
    {},
    {"runId": RUN_ID, "deliveryId": QUEUED_DELIVERY},
    {"runId": RUN_ID, "run": RUN_ID},
    {"run": "bad\nid"},
])
def test_mirror_still_refuses_ambiguous_or_missing_identifiers(arguments):
    from fleet_mcp import cloudcli_native as native

    for schema in _run_get_schemas():
        assert native.validate_arguments(arguments, schema)


# --------------------------------------------------------------------------- #
# the delivery arm, which the device does not support
# --------------------------------------------------------------------------- #
class Attempts:
    """One accepted `session.send` receipt, as the owner stores it."""

    def __init__(self, *, run_id, delivery_id, session_ref):
        self.records = {"attempt-1": {
            "key": "attempt-1", "phase": "ACCEPTED", "verb": "session.send",
            "device_ref": "device:work-fj", "runtime": "claude",
            "principal": '["pat","owner"]',
            "recorded_at": "1970-01-01T00:00:00+00:00",
            "result": {"accepted": True, "session_ref": session_ref,
                       "run_id": run_id, "delivery_id": delivery_id,
                       "queued_message_uuid": None},
        }}

    def dispatch_attempts(self):
        return self.records


class Provider:
    def __init__(self, *, run=None, delivery_error=None):
        self.run_payload = run
        self.delivery_error = delivery_error
        self.calls = []

    def get_delivery(self, key, delivery_id):
        self.calls.append(("delivery", delivery_id))
        if self.delivery_error is not None:
            raise self.delivery_error
        raise AssertionError("this device never resolves a delivery")

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0):
        self.calls.append(("run", run_id))
        return copy.deepcopy(self.run_payload)


class Service:
    def __init__(self, provider):
        self.provider = provider
        # The provider's own failure handling only reads this for multiplexed
        # families; these devices name `claude` directly.
        self.config = type("Config", (), {"session_binding": staticmethod(lambda key: None)})()

    def _session_provider(self, key):
        return self.provider

    def _verified_family_detail(self, key, provider, detail=None):
        return None


def _refusal(payload):
    return FleetRefusal(payload["code"], payload["message"], retryable=payload["retryable"])


def _watcher(provider, tmp_path, attempts, clock=lambda: 0):
    return RunWatcher(Service(provider), store(tmp_path / "events.db"), attempts, clock=clock)


def test_a_never_issued_delivery_falls_back_to_the_acknowledged_run(tmp_path):
    session_ref = f"device:work-fj|claude|{SESSION_ID}"
    provider = Provider(run=RUN_COMPLETED, delivery_error=_refusal(DELIVERY_NEVER_ISSUED))
    attempts = Attempts(run_id=RUN_ID, delivery_id=QUEUED_DELIVERY, session_ref=session_ref)
    watcher = _watcher(provider, tmp_path, attempts)
    result = watcher.tick()

    # The delivery was asked once, refused, and the run was then read by the id
    # the ACK itself supplied. The refusal is not a provider error and must not
    # be retried as one.
    assert provider.calls == [("delivery", QUEUED_DELIVERY), ("run", RUN_ID)]
    assert result["errors"] == 0
    assert [(row["kind"], row["run_id"], row["delivery_id"]) for row in rows(watcher.events)] == [
        ("run.completed", RUN_ID, QUEUED_DELIVERY)]


def test_a_delivery_only_receipt_that_cannot_be_read_stays_unknown_without_errors(tmp_path):
    session_ref = f"device:work-fj|claude|{SESSION_ID}"
    provider = Provider(delivery_error=_refusal(DELIVERY_NEVER_ISSUED))
    attempts = Attempts(run_id=None, delivery_id=QUEUED_DELIVERY, session_ref=session_ref)
    now = [0]
    watcher = _watcher(provider, tmp_path, attempts, clock=lambda: now[0])
    events = watcher.events

    result = watcher.tick()
    assert result["errors"] == 0
    assert rows(events) == []
    # Nothing was resolved, so the watch keeps saying UNKNOWN — and it is still
    # UNKNOWN one simulation day later rather than silently dropping.
    now[0] = 86400
    watcher.tick()
    assert [(row["kind"], row["summary"]["status"]) for row in rows(events)] == [
        ("run.unknown", "UNKNOWN")]


def test_a_completed_run_with_no_readable_output_is_not_success(tmp_path):
    session_ref = f"device:work-fj|claude|{EMPTY_SESSION_ID}"
    provider = Provider(run=RUN_COMPLETED_WITHOUT_OUTPUT)
    attempts = Attempts(run_id=EMPTY_RUN_ID, delivery_id=None, session_ref=session_ref)
    watcher = _watcher(provider, tmp_path, attempts)
    result = watcher.tick()

    rows_out = rows(watcher.events)
    assert [(row["kind"], row["summary"]["status"]) for row in rows_out] == [("run.unknown", "UNKNOWN")]
    assert result["terminal"] == 0 and result["unknown"] == 1


def test_a_completed_run_with_its_assistant_message_is_still_completed(tmp_path):
    # The guard above keys on the device's own note, not on "no message": a
    # completed run that carries its reply stays COMPLETED.
    session_ref = f"device:work-fj|claude|{SESSION_ID}"
    provider = Provider(run=RUN_COMPLETED)
    attempts = Attempts(run_id=RUN_ID, delivery_id=None, session_ref=session_ref)
    watcher = _watcher(provider, tmp_path, attempts)
    watcher.tick()
    assert [(row["kind"], row["summary"]["status"]) for row in rows(watcher.events)] == [
        ("run.completed", "COMPLETED")]


# --------------------------------------------------------------------------- #
# the provider keeps the three identities apart
# --------------------------------------------------------------------------- #
def test_provider_preserves_run_boot_and_delivery_as_distinct_values():
    from test_fleet_mcp_cloudcli_provider import FakeClient, provider

    key = SessionKey.parse(f"device:work-fj|claude|{SESSION_ID}")
    client = FakeClient(responses={"run_get": RUN_COMPLETED})
    payload = provider(client).get_run(key, RUN_ID)

    assert client.calls == [("run_get", {"runId": RUN_ID, "session": SESSION_ID})]
    # A run addressed by runId reports its own runId and boot, and states that
    # it has no delivery — the three are never collapsed into one field.
    assert payload["runId"] == RUN_ID
    assert payload["bootId"] == RUN_COMPLETED["bootId"] != RUN_ID
    assert "deliveryId" in payload and payload["deliveryId"] is None
