from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MCP = REPO / "mcp"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_mcp = _load("serveros_test_agent_mcp", MCP / "agent_mcp.py")
# Loaded under the name the contract tests below already use, so the whole file
# reads one module instance rather than one per test.
service_mod = _load("serveros_test_service_for_contract",
                    MCP / "agent_session_service.py")
semantics = sys.modules["serveros_runtime_agent_sessions"]

# Named once, because the fake daemon's device identity and the refs the fake
# provider hands out have to be the same device or `session.inspect` would
# reject a ref this module itself produced.
DEVICE_REF = "device:server-beijing"


class FakeDaemon:
    def __init__(self, decision="ALLOW"):
        self.decision = decision
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "device.show":
            return {"device_ref": DEVICE_REF}
        if method == "agent.action.admit":
            return {"decision": self.decision, "reason": "fixture"}
        raise AssertionError(method)


class FakeProvider:
    provider_id = "fake"

    def __init__(self):
        self.calls = []

    @property
    def ref(self):
        """The ref this provider would hand out for its one session."""
        return semantics.session_ref(DEVICE_REF, self.provider_id, "s1")

    def list_agents(self, *, device_ref):
        return [{"agent_id": "a1", "agent_ref": "agent:a1", "device_ref": device_ref}]

    def list_sessions(self, *, device_ref):
        ref = semantics.session_ref(device_ref, "fake", "s1")
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


def rpc(server, method, **params):
    return server.handle({"jsonrpc": "2.0", "id": 1,
                          "method": method, "params": params})


def test_agent_mcp_surface_matches_declared_contract():
    contract = yaml.safe_load((REPO / "fleet/mcp-contract.yaml").read_text())
    surface = contract["surfaces"]["serveros-agent-mcp"]
    assert tuple(surface["tools"]) == agent_mcp.tool_names()
    assert surface["resources"] == []
    assert surface["implementation"] == "mcp/agent_mcp.py"


def test_vertical_slice_exposes_only_operations_with_real_provider_paths():
    names = set(agent_mcp.tool_names())
    assert names == {"agent.list", "agent.inspect", "session.list", "session.inspect",
                     "session.create", "session.send", "session.result"}
    for absent in ("session.resume", "session.interrupt", "session.stop", "session.handoff"):
        assert absent not in names


def test_all_tool_schemas_are_closed_and_require_business_keys():
    for tool in agent_mcp.TOOLS:
        assert tool["inputSchema"]["additionalProperties"] is False
    create = next(t for t in agent_mcp.TOOLS if t["name"] == "session.create")
    assert set(create["inputSchema"]["required"]) == {
        "provider", "agent_id", "instruction", "idempotency_key"
    }
    send = next(t for t in agent_mcp.TOOLS if t["name"] == "session.send")
    assert "turn_idempotency_key" in send["inputSchema"]["required"]


def test_mutation_is_admitted_before_provider_is_called():
    daemon, provider = FakeDaemon(), FakeProvider()
    client = agent_mcp.AgentControlClient(daemon=daemon, providers=[provider])
    result = client.call("session.create", {
        "provider": "fake", "agent_id": "a1", "instruction": "do work",
        "idempotency_key": "work-1",
    })
    assert provider.calls[0][0] == "create"
    assert [m for m, _ in daemon.calls] == ["device.show", "agent.action.admit"]
    assert daemon.calls[1][1]["action"] == "session.create"
    assert result["admission"]["decision"] == "ALLOW"


def test_denied_mutation_never_reaches_provider():
    daemon, provider = FakeDaemon(decision="REQUIRE_HUMAN"), FakeProvider()
    client = agent_mcp.AgentControlClient(daemon=daemon, providers=[provider])
    server = agent_mcp.AgentMcpServer(client)
    response = rpc(server, "tools/call", name="session.create", arguments={
        "provider": "fake", "agent_id": "a1", "instruction": "do work",
        "idempotency_key": "work-1",
    })
    assert response["result"]["isError"] is True
    assert "REQUIRE_HUMAN" in response["result"]["content"][0]["text"]
    assert provider.calls == []


def test_read_path_does_not_request_mutation_admission():
    daemon, provider = FakeDaemon(), FakeProvider()
    client = agent_mcp.AgentControlClient(daemon=daemon, providers=[provider])
    result = client.call("session.list", {})
    assert len(result["sessions"]) == 1
    assert daemon.calls == [("device.show", {})]


def test_initialize_states_the_authority_boundary():
    server = agent_mcp.AgentMcpServer(client=object())
    result = rpc(server, "initialize", protocolVersion="2025-06-18")["result"]
    assert result["serverInfo"]["name"] == "serveros-agent-mcp"
    assert "serverosd admission" in result["instructions"]
    assert "provider-backed" in result["instructions"]


def test_daemon_never_imports_or_calls_botmux_provider():
    source = (REPO / "runtime/daemon.py").read_text().lower()
    assert "botmux" not in source
    assert "urllib" not in source
    assert "http://" not in source and "https://" not in source


def test_agent_mcp_stdio_round_trip_uses_same_protocol_loop():
    class ReadClient:
        def call(self, name, arguments=None):
            assert name == "session.list"
            return {"sessions": [], "complete": True}

    import io
    out = io.StringIO()
    agent_mcp.serve(
        instream=io.StringIO(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "session.list", "arguments": {}},
        }) + "\n"),
        outstream=out,
        server=agent_mcp.AgentMcpServer(ReadClient()),
    )
    response = json.loads(out.getvalue())
    assert response["id"] == 3
    assert json.loads(response["result"]["content"][0]["text"])["complete"] is True


# ---------------------------------------------------------------------------
# The MCP surface and the semantic contract must not be able to disagree.
# ---------------------------------------------------------------------------

def test_every_declared_semantic_action_is_either_served_or_named_absent():
    """The gap between "the contract declares it" and "a surface serves it" is
    exactly where this project keeps finding declared-but-absent behaviour, so
    it gets a gate rather than a convention."""
    contract = yaml.safe_load((REPO / "runtime/agent-session-contract.yaml").read_text())
    surface = yaml.safe_load((REPO / "fleet/mcp-contract.yaml").read_text())
    agent = surface["surfaces"]["serveros-agent-mcp"]
    declared = set(contract["support"]["actions"])
    served = set(agent["tools"])
    absent = set(agent["explicitly_absent"])

    assert served <= declared, sorted(served - declared)
    assert not (served & absent), "a tool cannot be both served and declared absent"
    assert declared - served == absent - {"remote.shell"}, (
        "every declared action the surface does not serve must be named in "
        "explicitly_absent, and every other name there must be a real declared "
        "action. `remote.shell` is the one exception, and it is named rather "
        "than skipped: it is not an Agent Session verb at all, so this surface "
        "declines an operation the semantic contract never offered it."
    )


def test_control_verbs_are_absent_from_the_surface_but_present_in_the_service():
    assert set(service_mod.CONTROL_VERBS) == {
        "session.interrupt", "session.stop", "session.handoff", "session.resume",
    }
    assert not (set(service_mod.CONTROL_VERBS) & set(agent_mcp.tool_names()))


def test_every_public_service_method_has_a_caller_that_can_actually_reach_it():
    """The service is the middle of the stack, so nothing checks it from above.

    A public method here that no dispatch calls and no other method calls is a
    capability with no path to a caller — and, worse, its docstring is free to
    claim otherwise. `readiness_list` did exactly that: it described itself as
    "the only way an operator can see that support = SUPPORTED and readiness =
    UNAVAILABLE are both true of the same provider", and no surface dispatched
    it, no method called it and no test exercised it. The fact it advertised was
    reachable, but through `agent.list`, which the contract already names.

    This is a structural read of the two real call sites rather than a
    hand-kept list, because a hand-kept list is the other half of the same
    defect: it would be updated by whoever moved the call, or not.
    """
    import ast

    service_tree = ast.parse((REPO / "mcp/agent_session_service.py").read_text())
    klass = next(n for n in service_tree.body
                 if isinstance(n, ast.ClassDef) and n.name == "SessionService")
    public = {
        n.name for n in klass.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    }

    # Callers on the object itself: `self.foo(...)` anywhere in the class.
    internal = {
        node.func.attr for node in ast.walk(klass)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
    }

    # The surface's own dispatch: `service.foo(...)` inside AgentControlClient.
    mcp_tree = ast.parse((REPO / "mcp/agent_mcp.py").read_text())
    client = next(n for n in mcp_tree.body
                  if isinstance(n, ast.ClassDef) and n.name == "AgentControlClient")
    dispatched = {
        node.func.attr for node in ast.walk(client)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "service"
    }

    # The four control verbs are served by methods the surface declines to
    # dispatch. They are named here by mapping rather than by a second literal
    # list, so that adding a verb without a method — or the reverse — fails.
    declared_absent = set(service_mod.CONTROL_VERBS.values())

    unreachable = public - internal - dispatched - declared_absent
    assert not unreachable, (
        f"public SessionService methods nothing can reach: {sorted(unreachable)}. "
        "Either wire it to a surface and a test, or make it private — a public "
        "method whose docstring promises reachability is the claim, not the "
        "capability."
    )

    # And the reverse, so this cannot be satisfied by deleting the caller and
    # widening the exception set: every name excused as a control verb must be
    # a method that really exists on the service.
    assert declared_absent <= public, sorted(declared_absent - public)


def test_declared_absent_verbs_have_no_reachable_mcp_path():
    server = agent_mcp.AgentMcpServer(client=object())
    for verb in ("session.interrupt", "session.stop", "session.handoff", "session.resume"):
        response = rpc(server, "tools/call", name=verb, arguments={"ref": "x"})
        assert response["result"]["isError"] is True
        assert "Unknown tool" in response["result"]["content"][0]["text"]


def test_readiness_does_not_widen_the_mcp_surface():
    """No new tool, because the contract never declared one.

    Readiness is a provider-instance fact, not an Agent Session verb. A
    `readiness.list` tool would be a name in this surface that
    `runtime/agent-session-contract.yaml` does not declare, and the gate that
    keeps the surface honest is exactly `served <= declared`. Widening the
    surface to make a new fact reachable would trade one honesty check for one
    convenience, which is the wrong direction.
    """
    contract = yaml.safe_load((REPO / "runtime/agent-session-contract.yaml").read_text())
    declared = set(contract["support"]["actions"])
    assert not [n for n in agent_mcp.tool_names() if "readiness" in n]
    assert set(agent_mcp.tool_names()) <= declared


def test_readiness_is_reachable_through_the_read_path_that_already_existed():
    """The fact is visible where an operator looks before driving something."""
    daemon, provider = FakeDaemon(), FakeProvider()
    client = agent_mcp.AgentControlClient(daemon=daemon, providers=[provider])
    view = client.call("agent.list", {})
    assert "providers" in view
    assert view["providers"], "a configured provider must report readiness, even if UNKNOWN"
    assert view["providers"][0]["status"] in ("AVAILABLE", "DEGRADED", "UNAVAILABLE",
                                              "INCOMPATIBLE", "UNKNOWN")


def test_a_mutation_result_carries_the_readiness_it_was_made_under():
    daemon, provider = FakeDaemon(), FakeProvider()
    client = agent_mcp.AgentControlClient(daemon=daemon, providers=[provider])
    out = client.call("session.send", {"ref": provider.ref,
                                       "instruction": "hi",
                                       "turn_idempotency_key": "t1"})
    assert "provider_readiness" in out
    assert out["provider_readiness"]["status"] in (
        "AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN")


def test_the_surface_does_not_serve_anything_the_contract_cannot_name():
    """The gate, restated against the new fact so it cannot drift."""
    contract = yaml.safe_load((REPO / "runtime/agent-session-contract.yaml").read_text())
    readiness_block = contract["readiness"]
    assert readiness_block["refusal"] == "ProviderNotReady"
    assert readiness_block["refusal_is_not"] == ["ActionNotSupported",
                                                 "ActionNotAdmitted"]
    assert "skills" not in agent_mcp.tool_names()
