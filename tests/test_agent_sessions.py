#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


semantics = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")
service_mod = _load("serveros_agent_session_service", MCP / "agent_session_service.py")
botmux_mod = _load("serveros_botmux_provider", MCP / "botmux_provider.py")

BotMuxConfig = botmux_mod.BotMuxConfig
BotMuxProvider = botmux_mod.BotMuxProvider
BotMuxUnsupported = botmux_mod.BotMuxUnsupported
ActionNotAdmitted = service_mod.ActionNotAdmitted
SessionService = service_mod.SessionService
parse_session_ref = semantics.parse_session_ref
session_ref = semantics.session_ref


class FakeProvider:
    provider_id = "fake"

    def __init__(self, *, fail_list=False):
        self.calls = []
        self.fail_list = fail_list

    def list_agents(self, *, device_ref):
        if self.fail_list:
            raise RuntimeError("offline")
        return [{"agent_id": "a", "agent_ref": "agent:a", "device_ref": device_ref}]

    def list_sessions(self, *, device_ref):
        if self.fail_list:
            raise RuntimeError("offline")
        ref = session_ref(device_ref, self.provider_id, "s1")
        # `action_support` is a REQUIRED session field in the contract, so a
        # fixture row without one is a row no provider may return. Declaring it
        # here is not a loosened assertion; the alternative was a fake that
        # exercises the fail-closed path by accident.
        return [{"session_id": ref, "session_ref": ref,
                 "provider_session_id": "s1", "last_activity": "2026-01-01T00:00:00Z",
                 "action_support": {
                     "session.send": semantics.support("SUPPORTED"),
                     "session.result": semantics.support("SUPPORTED"),
                 }}]

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"created": True}

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        return {"sent": True}

    def result(self, **kwargs):
        self.calls.append(("result", kwargs))
        return {"state": "completed"}

    def stop(self, **kwargs):
        self.calls.append(("stop", kwargs))
        return {"state": "CLOSED"}


def test_session_ref_round_trips_device_identity_without_treating_colons_as_routes():
    ref = session_ref("device:server-beijing", "provider:botmux-agent-session", "abc-123")
    assert ref.startswith("agent-session:device%3Aserver-beijing/")
    assert parse_session_ref(ref) == ("device:server-beijing", "provider:botmux-agent-session", "abc-123")


def test_botmux_runtime_mapping_is_closed_by_the_agent_runtime_contract():
    assert BotMuxProvider._runtime_ref("codex-app") == "agent-runtime:codex"
    assert BotMuxProvider._runtime_ref("codex") == "agent-runtime:codex"
    assert BotMuxProvider._runtime_ref("claude-code") == "agent-runtime:claude-code"
    assert BotMuxProvider._runtime_ref("opencode") is None


def test_provider_failure_is_reported_not_laundered_into_an_empty_complete_fleet():
    service = SessionService(device_ref="device:x", providers=[FakeProvider(fail_list=True)])
    view = service.session_list()
    assert view["sessions"] == []
    assert view["complete"] is False
    assert view["provider_errors"][0]["provider"] == "fake"


def test_mutation_without_serveros_admission_is_refused_before_provider_call():
    provider = FakeProvider()
    service = SessionService(device_ref="device:x", providers=[provider])
    with pytest.raises(ActionNotAdmitted):
        service.create(provider_id="fake", agent_id="a", instruction="x",
                       idempotency_key="k")
    assert provider.calls == []


def test_mutation_runs_only_after_allow_decision_and_carries_the_decision():
    provider = FakeProvider()
    decisions = []

    def admit(action, context):
        decisions.append((action, context))
        return {"decision": "ALLOW", "reason": "fixture"}

    service = SessionService(device_ref="device:x", providers=[provider], admission=admit)
    ref = session_ref("device:x", "fake", "s1")
    result = service.send(session_ref_value=ref, instruction="do it",
                          turn_idempotency_key="turn-1")
    assert provider.calls[0][0] == "send"
    assert decisions[0][0] == "session.send"
    assert result["admission"]["decision"] == "ALLOW"


def _botmux_with(responses):
    provider = BotMuxProvider(BotMuxConfig(dashboard_token="test-token"))
    calls = []

    def request(method, path, *, body=None, require_auth=False):
        calls.append((method, path, body, require_auth))
        key = (method, path.split("?", 1)[0])
        value = responses[key]
        return value() if callable(value) else value

    provider._request = request
    return provider, calls


def test_botmux_session_projection_preserves_native_state_and_status_provenance():
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [{
            "sessionId": "s1", "larkAppId": "bot1", "cliId": "claude-code",
            "status": "working", "workingDir": "/work/x",
            "spawnedAt": 1000, "lastMessageAt": 2000,
        }]},
    })
    row = provider.list_sessions(device_ref="device:server-beijing")[0]
    assert row["provider_session_id"] == "s1"
    assert row["agent_runtime"] == "agent-runtime:claude-code"
    assert row["status"] == "BUSY"
    assert row["status_provenance"] == "INFERRED"
    assert row["provider_status"] == "working"
    assert row["workspace"] == "/work/x"


def test_botmux_dormant_means_detached_but_still_accepts_a_followup_turn():
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [{
            "sessionId": "s1", "larkAppId": "bot1", "cliId": "claude-code",
            "status": "dormant",
        }]},
    })
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["status"] == "DETACHED"
    assert row["status_provenance"] == "FACT"
    assert row["can_accept_input"] is True
    assert row["action_support"]["session.send"]["state"] == "SUPPORTED"
    assert row["action_support"]["session.resume"]["state"] == "UNSUPPORTED"


def test_botmux_does_not_lie_that_close_is_turn_interrupt():
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": []}})
    actions = provider._actions()
    assert actions["session.interrupt"]["state"] == "UNSUPPORTED"
    assert actions["session.stop"]["state"] == "SUPPORTED"


def test_botmux_create_rejects_workspace_binding_the_deployed_api_cannot_honor():
    provider = BotMuxProvider(BotMuxConfig(dashboard_token="test-token"))
    with pytest.raises(BotMuxUnsupported) as excinfo:
        provider.create(device_ref="device:x", agent_id="bot1", instruction="x",
                        idempotency_key="k", workspace="/work/x")
    assert "workingDir" in str(excinfo.value)


def test_botmux_fresh_trigger_uses_async_at_most_once_contract():
    provider, calls = _botmux_with({
        ("POST", "/api/trigger"): {
            "ok": True, "triggerId": "trg-1",
            "async": {"status": "pending", "sessionId": "s1"},
        },
    })
    result = provider.create(device_ref="device:x", agent_id="bot1",
                             instruction="hello", idempotency_key="work-42")
    body = calls[0][2]
    assert body["target"] == {"kind": "turn", "botId": "bot1"}
    assert body["options"] == {"asyncReturnSessionId": True,
                               "idempotencyKey": "work-42"}
    assert body["envelope"]["trusted"] is False
    assert result["provider_session_id"] == "s1"


def test_botmux_followup_uses_turn_idempotency_and_the_existing_session():
    responses = {
        ("GET", "/api/sessions"): {"sessions": [{
            "sessionId": "s1", "larkAppId": "bot1", "cliId": "claude-code",
            "status": "dormant",
        }]},
        ("POST", "/api/trigger"): {
            "ok": True, "triggerId": "trg-2",
            "async": {"status": "pending", "sessionId": "s1"},
        },
    }
    provider, calls = _botmux_with(responses)
    provider.send(device_ref="device:x", provider_session_id="s1",
                  instruction="continue", turn_idempotency_key="turn-7")
    body = [c for c in calls if c[0] == "POST"][0][2]
    assert body["target"]["sessionId"] == "s1"
    assert body["options"]["turnIdempotencyKey"] == "turn-7"


# ---------------------------------------------------------------------------
# Coverage required by the vertical-slice brief, section 17.
# ---------------------------------------------------------------------------

import yaml as _yaml

from urllib.error import URLError as _URLError

ActionNotSupported = service_mod.ActionNotSupported
UnknownSession = service_mod.UnknownSession


def _contract_files():
    return [
        _yaml.safe_load((REPO / "runtime/agent-session-contract.yaml").read_text()),
        _yaml.safe_load((REPO / "abilities/agent-runtime-contract.yaml").read_text()),
    ]


def test_provider_availability_distinguishes_offline_from_unknown():
    """An explicit `online: false` is a fact; only an absent field is unknown."""
    provider, _ = _botmux_with({("GET", "/api/bots"): {"bots": [
        {"larkAppId": "b1", "botName": "up", "cliId": "claude-code", "online": True},
        {"larkAppId": "b2", "botName": "down", "cliId": "claude-code", "online": False},
        {"larkAppId": "b3", "botName": "silent", "cliId": "claude-code"},
    ]}})
    agents = {a["agent_id"]: a for a in provider.list_agents(device_ref="device:x")}
    assert agents["b1"]["availability"] == "AVAILABLE"
    assert agents["b2"]["availability"] == "OFFLINE"
    assert agents["b3"]["availability"] == "UNKNOWN"
    assert agents["b2"]["availability_provenance"] == "FACT"
    assert agents["b3"]["availability_provenance"] == "UNKNOWN"


def test_botmux_credential_missing_fails_before_any_network_call():
    provider = BotMuxProvider(BotMuxConfig(dashboard_token=None))
    with pytest.raises(botmux_mod.BotMuxCredentialMissing):
        provider.list_agents(device_ref="device:x")


def test_botmux_unavailable_is_an_error_not_an_empty_fleet(monkeypatch):
    def explode(*args, **kwargs):
        raise _URLError("connection refused")

    monkeypatch.setattr(botmux_mod, "urlopen", explode)
    provider = BotMuxProvider(BotMuxConfig(dashboard_token="t"))
    with pytest.raises(botmux_mod.BotMuxError) as excinfo:
        provider.list_sessions(device_ref="device:x")
    assert "unavailable" in str(excinfo.value)


def test_unreachable_provider_never_becomes_a_closed_session():
    class DeadProvider:
        provider_id = "dead"

        def list_sessions(self, *, device_ref):
            raise botmux_mod.BotMuxError("BotMux GET /api/sessions unavailable: URLError")

    service = SessionService(device_ref="device:x", providers=[DeadProvider()])
    view = service.session_list()
    assert view["sessions"] == []
    assert view["complete"] is False
    assert "unavailable" in view["provider_errors"][0]["error"]


def test_queued_is_evidence_and_never_overrides_a_mapped_status():
    """A running session with a queued follow-up is still running."""
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
         "status": "working", "queued": True},
    ]}})
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["status"] == "BUSY"
    assert row["provider_evidence"]["queued"] is True


def test_queued_alone_never_invents_a_session_state():
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code", "queued": True},
    ]}})
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["status"] == "UNKNOWN"
    assert row["status_provenance"] == "UNKNOWN"


def test_unknown_provider_fields_stay_unknown_rather_than_being_flattened():
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
         "status": "teleporting", "shinyNewField": 7},
    ]}})
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["status"] == "UNKNOWN"
    assert row["status_provenance"] == "UNKNOWN"
    assert row["provider_status"] == "teleporting"
    assert "teleporting" in row["status_reason"]
    assert row["can_accept_input"] is True


def test_botmux_turn_result_is_normalized_as_a_turn_not_a_session():
    for raw, expected in (("running", "TURN_RUNNING"), ("completed", "TURN_COMPLETED"),
                          ("failed", "TURN_FAILED"), ("not_found", "TURN_NOT_FOUND"),
                          ("something-else", "TURN_UNKNOWN"), (None, "TURN_UNKNOWN")):
        provider, _ = _botmux_with({
            ("GET", "/api/sessions/s1/trigger-result"): {"state": raw, "triggerId": "t1"},
        })
        out = provider.result(provider_session_id="s1")
        assert out["turn_state"] == expected, raw
        assert out["scope"] == "current-turn"
        assert out["provider_state"] == raw


def test_trigger_acceptance_is_not_reported_as_a_session_state():
    provider, _ = _botmux_with({
        ("POST", "/api/trigger"): {"triggerId": "t1",
                                   "async": {"status": "pending", "sessionId": "s1"}},
    })
    out = provider.create(device_ref="device:x", agent_id="b1",
                          instruction="hi", idempotency_key="k")
    assert out["turn_state"] == "TURN_UNKNOWN"
    assert out["turn_state_provenance"] == "UNKNOWN"
    assert out["provider_async_status"] == "pending"
    assert "state" not in out


def test_create_without_a_provider_session_id_fails_rather_than_inventing_one():
    """An accepted trigger with no sessionId must not become a fabricated ref."""
    provider, _ = _botmux_with({
        ("POST", "/api/trigger"): {"triggerId": "t1", "async": {"status": "pending"}},
    })
    with pytest.raises(botmux_mod.BotMuxError) as excinfo:
        provider.create(device_ref="device:x", agent_id="b1",
                        instruction="hi", idempotency_key="k")
    assert "no sessionId" in str(excinfo.value)


def test_emitted_status_and_support_vocabulary_is_closed_by_the_contract():
    session_contract, _ = _contract_files()
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code", "status": "closed"},
        {"sessionId": "s2", "larkAppId": "b1", "cliId": "claude-code", "status": "dormant"},
        {"sessionId": "s3", "larkAppId": "b1", "cliId": "codex", "status": "working"},
        {"sessionId": "s4", "larkAppId": "b1", "cliId": "codex", "status": "nope"},
    ]}})
    states = set(session_contract["status"]["values"])
    supports = set(session_contract["support"]["values"])
    actions = set(session_contract["support"]["actions"])
    for row in provider.list_sessions(device_ref="device:x"):
        assert row["status"] in states
        assert row["status_provenance"] in set(session_contract["status"]["provenance"])
        assert set(row["action_support"]) <= actions
        for entry in row["action_support"].values():
            assert entry["state"] in supports


def test_agent_runtime_ids_come_from_the_existing_runtime_contract():
    _, runtime_contract = _contract_files()
    declared = set(runtime_contract["ids"])
    emitted = {BotMuxProvider._runtime_ref(native)
               for native in ("claude-code", "codex", "codex-app", "opencode", "")}
    for ref in emitted - {None}:
        assert ref.startswith("agent-runtime:")
        assert ref.split(":", 1)[1] in declared
    assert None in emitted  # an unknown native runtime yields no runtime identity


def test_provider_session_id_never_becomes_device_identity():
    """A provider session id may contain anything, including a device ref."""
    device = "device:server-beijing"
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": device, "larkAppId": "b1", "cliId": "claude-code", "status": "dormant"},
    ]}})
    row = provider.list_sessions(device_ref=device)[0]
    assert row["device_ref"] == device
    assert row["provider_session_id"] == device
    parsed_device, parsed_provider, parsed_native = parse_session_ref(row["session_ref"])
    assert parsed_device == device
    assert parsed_provider == BotMuxProvider.provider_id
    assert parsed_native == device


def test_botmux_provider_id_is_the_canonical_singular_form():
    assert BotMuxProvider.provider_id == "provider:botmux-agent-session"
    source = (REPO / "mcp/botmux_provider.py").read_text()
    assert "provider:botmux-agent-sessions" not in source


def test_unknown_session_is_refused_by_inspect_and_by_control():
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": []}})
    service = SessionService(device_ref="device:x", providers=[provider],
                             admission=lambda action, context: {"decision": "ALLOW"})
    missing = session_ref("device:x", BotMuxProvider.provider_id, "nope")
    with pytest.raises(UnknownSession):
        service.session_inspect(missing)
    with pytest.raises(UnknownSession):
        service.stop(session_ref_value=missing)


class _ControlProvider:
    """Declares exactly the support states it is told to declare."""

    provider_id = "control"

    def __init__(self, support_by_action, row_support=None):
        self.calls = []
        self._support = support_by_action

    def list_sessions(self, *, device_ref):
        ref = session_ref(device_ref, self.provider_id, "s1")
        return [{
            "session_id": ref, "session_ref": ref, "provider_session_id": "s1",
            "status": "BUSY", "status_provenance": "FACT",
            "action_support": {a: semantics.support(s) for a, s in self._support.items()},
        }]

    def interrupt(self, *, provider_session_id):
        self.calls.append(("interrupt", provider_session_id))
        return {"interrupted": True}

    def stop(self, *, provider_session_id):
        self.calls.append(("stop", provider_session_id))
        return {"state": "CLOSED"}


def test_destructive_control_without_human_approval_is_refused_before_the_provider():
    provider = _ControlProvider({"session.interrupt": "SUPPORTED"})
    service = SessionService(
        device_ref="device:x", providers=[provider],
        admission=lambda action, context: session_contract_admission(action, context),
    )
    ref = session_ref("device:x", provider.provider_id, "s1")
    with pytest.raises(ActionNotAdmitted) as excinfo:
        service.interrupt(session_ref_value=ref)
    assert "REQUIRE_HUMAN" in str(excinfo.value)
    assert provider.calls == []


def session_contract_admission(action, context):
    return semantics.admission(action, context)


def test_approved_destructive_control_reaches_the_provider_and_carries_the_decision():
    provider = _ControlProvider({"session.interrupt": "SUPPORTED"})
    service = SessionService(
        device_ref="device:x", providers=[provider],
        admission=lambda action, context: {"decision": "ALLOW", "class": "human_gated",
                                           "reason": "human approved"},
    )
    ref = session_ref("device:x", provider.provider_id, "s1")
    out = service.interrupt(session_ref_value=ref)
    assert provider.calls == [("interrupt", "s1")]
    assert out["action"] == "session.interrupt"
    assert out["admission"]["decision"] == "ALLOW"


def test_unsupported_control_verb_is_not_reported_as_a_pending_approval():
    """No approval can grant a capability the provider does not have."""
    provider = _ControlProvider({"session.interrupt": "UNSUPPORTED"})
    service = SessionService(
        device_ref="device:x", providers=[provider],
        admission=lambda action, context: {"decision": "ALLOW"},
    )
    ref = session_ref("device:x", provider.provider_id, "s1")
    with pytest.raises(ActionNotSupported) as excinfo:
        service.interrupt(session_ref_value=ref)
    assert excinfo.value.state == "UNSUPPORTED"
    assert provider.calls == []


def test_unknown_provider_support_fails_closed_rather_than_assuming_yes():
    provider = _ControlProvider({"session.stop": "UNKNOWN"})
    service = SessionService(
        device_ref="device:x", providers=[provider],
        admission=lambda action, context: {"decision": "ALLOW"},
    )
    ref = session_ref("device:x", provider.provider_id, "s1")
    with pytest.raises(ActionNotSupported) as excinfo:
        service.stop(session_ref_value=ref)
    assert excinfo.value.state == "UNKNOWN"
    assert provider.calls == []


def test_provider_that_hides_its_support_behind_absence_is_refused():
    class Opaque:
        provider_id = "opaque"

        def list_sessions(self, *, device_ref):
            ref = session_ref(device_ref, self.provider_id, "s1")
            return [{"session_id": ref, "session_ref": ref,
                     "provider_session_id": "s1", "status": "BUSY"}]

        def stop(self, *, provider_session_id):
            raise AssertionError("must not be reached")

    service = SessionService(device_ref="device:x", providers=[Opaque()],
                             admission=lambda action, context: {"decision": "ALLOW"})
    with pytest.raises(ActionNotSupported):
        service.stop(session_ref_value=session_ref("device:x", "opaque", "s1"))


def test_botmux_close_is_never_reachable_as_interrupt():
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code", "status": "dormant"},
    ]}})
    service = SessionService(device_ref="device:x", providers=[provider],
                             admission=lambda action, context: {"decision": "ALLOW"})
    ref = session_ref("device:x", BotMuxProvider.provider_id, "s1")
    with pytest.raises(ActionNotSupported) as excinfo:
        service.interrupt(session_ref_value=ref)
    assert "not only the current turn" in str(excinfo.value)
    with pytest.raises(ActionNotSupported):
        service.resume(session_ref_value=ref)


def test_admission_decision_echoes_the_context_it_adjudicated():
    decision = semantics.admission("session.send", {"provider": "p", "session_ref": "r"})
    assert decision["decision"] == "ALLOW"
    assert decision["context"] == {"provider": "p", "session_ref": "r"}
    assert semantics.admission("agent.teleport", {})["decision"] == "DENY"


def test_control_verbs_are_declared_by_the_contract_and_none_are_undeclared():
    session_contract, _ = _contract_files()
    declared = set(session_contract["support"]["actions"])
    assert set(service_mod.CONTROL_VERBS) <= declared
    assert declared == (set(session_contract["admission"]["read_only"])
                        | set(session_contract["admission"]["delegation"])
                        | set(session_contract["admission"]["human_gated"]))


def test_send_to_a_closed_session_is_refused_as_unsupported_not_as_a_policy_denial():
    """Found by the live slice: the closed session's own row already said no."""
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
         "status": "closed", "workingDir": "/home/hwz",
         "chatId": "http_async_0", "workerPid": None},
    ]}})
    service = SessionService(device_ref="device:x", providers=[provider],
                             admission=lambda action, context: {"decision": "ALLOW"})
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["can_accept_input"] is False
    assert row["action_support"]["session.send"]["state"] == "UNSUPPORTED"
    ref = session_ref("device:x", BotMuxProvider.provider_id, "s1")
    with pytest.raises(ActionNotSupported) as excinfo:
        service.send(session_ref_value=ref, instruction="hi",
                     turn_idempotency_key="turn-1")
    assert excinfo.value.action == "session.send"


def test_worker_pid_and_chat_id_never_become_session_identity():
    """Both appear in the live BotMux row. Both are locators, not keys."""
    device = "device:server-beijing"
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
         "status": "dormant", "workerPid": 942206,
         "chatId": "http_async_7128b9d3", "backendSessionName": "bmx-s1"},
    ]}})
    row = provider.list_sessions(device_ref=device)[0]
    assert row["provider_session_id"] == "s1"
    assert "942206" not in row["session_ref"]
    assert "http_async" not in row["session_ref"]
    assert "bmx-s1" not in row["session_ref"]
    assert row["device_ref"] == device
    assert parse_session_ref(row["session_ref"]) == (device, BotMuxProvider.provider_id, "s1")
