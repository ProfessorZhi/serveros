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
        return [{"session_ref": ref,
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
    assert row["provider_evidence"]["fields"]["queued"] is True


def test_evidence_is_a_record_and_a_reason_is_a_sentence():
    """`reason`, `evidence`, `provenance` and `observed_at` are four things.

    They were two: a normalisation sentence had been written into the field
    named `provider_evidence`, because a helper that existed was never called.
    A record whose evidence slot holds prose cannot be aged, cannot be compared
    against another record, and cannot tell a reader what was observed versus
    what somebody concluded about it.
    """
    provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
        {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
         "status": "limited", "queued": False, "locked": True},
    ]}})
    row = provider.list_sessions(device_ref="device:x")[0]

    # reason: a sentence, and only that.
    assert isinstance(row["status_reason"], str)
    # evidence: a record, and not the sentence.
    evidence = row["provider_evidence"]
    assert isinstance(evidence, dict)
    assert set(evidence) == {"source", "observed_at", "provenance",
                             "evidence_type", "authoritative", "fields"}
    assert evidence["source"] == "botmux:/api/sessions"
    assert evidence["fields"] == {"queued": False, "locked": True}
    # provenance and freshness are their own keys, not carried inside `fields`.
    assert evidence["provenance"] == "FACT"
    assert evidence["observed_at"].endswith("Z")
    assert evidence["authoritative"] is True
    # `status_provenance` (about the mapping) is not `evidence.provenance`
    # (about the fields). Different subjects; both present; neither inferred
    # from the other.
    assert row["status_provenance"] == "INFERRED"


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
            "session_ref": ref, "provider_session_id": "s1",
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
            return [{"session_ref": ref,
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


# ---------------------------------------------------------------------------
# The three layers. Session lifecycle, turn lifecycle, request acceptance —
# separately sourced, separately named, and never standing in for each other.
#
# These are not new behaviour; they are the assertions that were missing while
# the behaviour was already load-bearing. Every one of them describes something
# that was at some point reported as something else.
# ---------------------------------------------------------------------------

def test_the_contract_declares_three_layers_and_names_their_fields():
    contract = semantics.contract()
    layers = contract["layers"]
    assert layers["session_lifecycle"]["field"] == "status"
    assert layers["turn_lifecycle"]["field"] == "turn_state"
    # The rule that ties them, present as a rule and not as prose about one.
    assert any("never overwrites" in r for r in layers["cross_layer_rules"])

    # And the named fields are really produced, per layer and at its own source.
    #
    # Asserting only that the contract NAMES a field is how `accepted` survived a
    # revision: the contract said a field by that name carried request
    # acceptance, no record in the repo had one, and the test agreed with the
    # contract instead of with the code. Each layer is resolved through the call
    # that produces it, so a rename on either side breaks this rather than
    # passing quietly.
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [{
            "sessionId": "s1", "larkAppId": "bot1", "cliId": "claude-code",
            "status": "working", "workingDir": "/work/x",
            "spawnedAt": 1000, "lastMessageAt": 2000,
        }]},
        ("POST", "/api/trigger"): {"triggerId": "t1",
                                   "async": {"status": "pending", "sessionId": "s1"}},
    })
    session = provider.list_sessions(device_ref="device:x")[0]
    assert layers["session_lifecycle"]["field"] in session

    created = provider.create(device_ref="device:x", agent_id="b1",
                              instruction="hi", idempotency_key="k")
    assert layers["turn_lifecycle"]["field"] in created, (
        "session.create is declared a carrier of the turn layer and produced no "
        f"{layers['turn_lifecycle']['field']!r}"
    )
    assert layers["request_acceptance"]["field"] in created, (
        "the contract names "
        f"{layers['request_acceptance']['field']!r} as the field that carries "
        "request acceptance; create produced no such field"
    )


def test_an_accepted_request_is_never_reported_as_a_session_state():
    """Acceptance is the weakest fact in the system and the easiest to inflate.

    `{"async": {"status": "pending", "sessionId": "s1"}}` says BotMux took the
    request. It does not say the session started, is queued, is running, or
    will ever run — and this round exists because a ServerOS revision inferred
    `QUEUED` from exactly this response.
    """
    provider, _ = _botmux_with({
        ("POST", "/api/trigger"): {"triggerId": "t1",
                                   "async": {"status": "pending", "sessionId": "s1"}},
    })
    out = provider.create(device_ref="device:x", agent_id="b1",
                          instruction="hi", idempotency_key="k")
    # The acceptance, carried as itself.
    assert out["provider_async_status"] == "pending"
    assert out["turn_state"] == "TURN_UNKNOWN"
    # And no lifecycle value anywhere in the result.
    assert "state" not in out
    assert "status" not in out
    # Compared as values and not as substrings: `TURN_UNKNOWN` contains the
    # lifecycle word `UNKNOWN`, and a test that flagged that would be flagging
    # the turn layer for being named honestly.
    lifecycle = set(semantics.contract()["status"]["values"])
    for key, value in out.items():
        if key.startswith("turn_"):
            continue
        assert not (isinstance(value, str) and value in lifecycle), \
            f"create reported lifecycle {value!r} in {key!r}"


def test_no_session_lifecycle_word_is_reachable_from_a_trigger_response():
    """Checked against the vocabulary rather than against one example.

    A single example can only catch the one word somebody thought of. `QUEUED`
    is gone from this codebase, and the reason it cannot come back is that
    nothing in the create path maps anything to a lifecycle value at all.
    """
    provider, _ = _botmux_with({
        ("POST", "/api/trigger"): {"triggerId": "t1",
                                   "async": {"status": "queued", "sessionId": "s1"},
                                   "state": "completed", "status": "queued"},
    })
    out = provider.create(device_ref="device:x", agent_id="b1",
                          instruction="hi", idempotency_key="k")
    for lifecycle in semantics.contract()["status"]["values"]:
        assert out.get("status") != lifecycle
    assert out["turn_state"] == "TURN_UNKNOWN"
    assert out["turn_state_provenance"] == "UNKNOWN"


def test_a_running_turn_does_not_make_the_session_run():
    """The two are sourced independently and can disagree. They should.

    BotMux reports the session as `dormant` — a logical session with no
    resident worker — while a turn it accepted is `running`. Both are true. A
    mapping that made the session RUNNING from the turn would be reporting a
    resident worker that does not exist.
    """
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [
            {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
             "status": "dormant"}]},
        ("GET", "/api/sessions/s1/trigger-result"): {"state": "running"},
    })
    session = provider.list_sessions(device_ref="device:x")[0]
    turn = provider.result(provider_session_id="s1")
    assert session["status"] == "DETACHED"
    assert turn["turn_state"] == "TURN_RUNNING"
    assert session["status"] != "RUNNING"


def test_a_failed_turn_does_not_make_the_session_fail():
    """The expensive one. A failed turn is news about a turn.

    BotMux reports this session as `idle` — the turn failed fast and the
    session is still there, still accepting, `can_accept_input: true`. A
    mapping that inferred FAILED from TURN_FAILED would report a live session
    as dead, and every caller that trusted it would stop driving a session that
    works.
    """
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [
            {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
             "status": "idle"}]},
        ("GET", "/api/sessions/s1/trigger-result"): {"state": "failed",
                                                     "errorCode": "no_output"},
    })
    turn = provider.result(provider_session_id="s1")
    session = provider.list_sessions(device_ref="device:x")[0]
    assert turn["turn_state"] == "TURN_FAILED"
    assert session["status"] == "IDLE"
    assert session["can_accept_input"] is True


def test_failed_requires_session_level_evidence_and_this_provider_never_manufactures_it():
    """`FAILED` is declared, and no BotMux status maps to it.

    The vocabulary is deliberately wider than one adapter's mapping: another
    provider may say a session has failed. What matters is that the width is
    not closed by this adapter inventing the missing values. So the assertion
    is not "FAILED is unreachable" — it is "FAILED is reachable only from a
    provider that said so", and BotMux's `/api/sessions` never does.
    """
    contract = semantics.contract()
    assert "FAILED" in contract["status"]["values"]
    assert "session-level provider evidence" in contract["status"]["evidence_rule"]
    # Every raw status BotMux can report for a session, run through the mapping.
    for raw in ("starting", "working", "analyzing", "idle", "dormant",
                "limited", "stalled", "closed", "queued", "completed", "failed"):
        provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [
            {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
             "status": raw}]}})
        assert provider.list_sessions(device_ref="device:x")[0]["status"] != "FAILED", raw


def test_request_evidence_never_overrides_a_lifecycle_mapping():
    """`queued` is evidence. It is never a state, and it is never applied.

    Three shapes of the same rule: with a mapped status, with no status at all,
    and with an unmappable status. In none of them does a provider request
    field change the answer, because a field about a REQUEST is not a field
    about a SESSION.
    """
    cases = [
        ({"status": "working", "queued": True}, "BUSY"),
        ({"queued": True}, "UNKNOWN"),
        ({"status": "teleporting", "queued": True}, "UNKNOWN"),
        ({"status": "dormant", "queued": True, "locked": True, "adopt": True}, "DETACHED"),
    ]
    for raw_row, expected in cases:
        row_in = {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code", **raw_row}
        provider, _ = _botmux_with({("GET", "/api/sessions"): {"sessions": [row_in]}})
        row = provider.list_sessions(device_ref="device:x")[0]
        assert row["status"] == expected, raw_row
        # ...and the evidence was still kept, because dropping it would be a
        # different way of losing information.
        assert row["provider_evidence"]["fields"]["queued"] is True


def test_the_three_layers_are_three_fields_on_one_session_row():
    """A reader gets all three at once and has to be able to tell them apart."""
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [
            {"sessionId": "s1", "larkAppId": "b1", "cliId": "claude-code",
             "status": "idle", "queued": True}]},
        ("GET", "/api/sessions/s1/trigger-result"): {"state": "running"},
    })
    row = provider.list_sessions(device_ref="device:x")[0]
    assert row["status"] == "IDLE"                          # session lifecycle
    assert row["status_provenance"] == "INFERRED"
    assert "turn_state" not in row                          # not a session field
    assert row["status_reason"]                             # a sentence
    assert row["provider_evidence"]["fields"]["queued"]     # a fact
    turn = provider.result(provider_session_id="s1")
    assert turn["scope"] == "current-turn"
    assert turn["turn_state"] == "TURN_RUNNING"
    assert turn["turn_state_provenance"] == "FACT"
    # The session row still says IDLE, because that is what the provider said
    # about the SESSION, and the turn is not evidence about it.
    assert row["status"] == "IDLE"


def test_a_session_record_carries_exactly_one_identity_field():
    """Two names for one identity is how a reader picks the wrong one.

    The record carried `session_id` beside `session_ref`, always the same
    string, and nothing in the repo read it. It is not declared in
    `session_fields`, and its name invites exactly one wrong reading: that it is
    the PROVIDER's id, which is what `provider_session_id` already means and is
    a different value on every device. A field with no reader and a misleading
    name is not harmless — it is a trap that costs a reader one wrong guess and
    costs a maintainer nothing to keep, which is why it survives.

    Asserted as a count rather than as an absence, so that replacing it with a
    differently-spelled duplicate fails here too.
    """
    provider, _ = _botmux_with({
        ("GET", "/api/sessions"): {"sessions": [{
            "sessionId": "s1", "larkAppId": "bot1", "cliId": "claude-code",
            "status": "working",
        }]},
    })
    row = provider.list_sessions(device_ref="device:x")[0]

    declared = semantics.contract()["session_fields"]["required"]
    assert "session_ref" in declared, "the contract must name the identity field"

    # Any ServerOS-side name for the session, matched by SHAPE rather than by a
    # list of the two names seen so far — a list would only catch the duplicate
    # somebody already thought of. `provider_session_id` is excluded because it
    # names the provider's id, which is a different value on purpose.
    #
    # The first version of this used `endswith("_session_id")`, which is False
    # for `session_id` — no leading underscore — so it could never have fired on
    # the field it was written for. Caught by re-adding the field and watching
    # the gate stay green.
    identity_keys = [k for k in row
                     if k.endswith(("_ref", "_id"))
                     and "session" in k
                     and k != "provider_session_id"]
    assert identity_keys == ["session_ref"], (
        f"a session record names its identity {len(identity_keys)} times: {identity_keys}. "
        "One identity, one name — and the provider's id has its own field."
    )
    # The two that legitimately remain are the ServerOS identity and the
    # provider's native id, and they are different strings.
    assert row["session_ref"] != row["provider_session_id"]
