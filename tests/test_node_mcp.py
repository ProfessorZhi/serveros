#!/usr/bin/env python3
"""Tests for serveros-node-mcp — the thin adapter.

The properties here are mostly negative, and that is the point. An MCP surface
is easy to make useful and hard to keep thin: the failure mode is not a crash,
it is an adapter that grows a cache, then a fallback, then a small piece of
logic "just for this case", until the policy it was supposed to forward exists
in two places and the two disagree.

So the tests below assert what the adapter does NOT have: no docker, no
systemctl, no writing, no cached answer when the daemon is down, and no tool
whose name implies an action. Each one would fail on a plausible future edit,
which is the only kind of gate worth writing.

Hermetic: a fake daemon client. The one live test is at the bottom and is
skipped unless a socket is actually there.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mcp_mod = _load("serveros_runtime_node_mcp", "node_mcp.py")
NodeMcpServer = mcp_mod.NodeMcpServer
DaemonUnavailable = mcp_mod.DaemonUnavailable


class FakeDaemon:
    """Answers like serverosd, and records what it was asked."""

    def __init__(self, *, fail=None, results=None):
        self.calls = []
        self.fail = fail
        self.results = results or {}

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if self.fail:
            raise DaemonUnavailable(self.fail)
        return self.results.get(method, {"answered": method})


@pytest.fixture
def server():
    def build(**kw):
        return NodeMcpServer(FakeDaemon(**kw))
    return build


def rpc(server, method, **params):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params})


# ---------------------------------------------------------------------------
# §23 · it is a thin adapter, and that is enforced rather than described
# ---------------------------------------------------------------------------


def test_the_adapter_cannot_execute_anything():
    """The single most important negative in this file.

    §23: an MCP that can bypass ServerOS admission to `docker rm` or
    `systemctl restart` is a control plane with a documented way around it.

    Checked structurally rather than by searching the text, because the words
    `docker` and `systemctl` legitimately appear in the message this adapter
    shows when the daemon is down — and a test that fails on a help string is
    a test somebody deletes. What matters is that no code path can reach a
    shell: `subprocess` is not imported, and neither is any `os` function that
    spawns. The import list is asserted exactly, so a future `import pty` or
    `import asyncio` shows up as an edit rather than slipping through.
    """
    import ast

    tree = ast.parse((RUNTIME / "node_mcp.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported == {"__future__", "json", "os", "socket", "sys",
                        "pathlib", "argparse"}, imported

    # And even `os` may not be used to spawn. Narrow on purpose: `self.client
    # .call(...)` is an ordinary method call, and a rule that flags it would
    # force this test to be deleted the first time somebody added a client.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "os":
                assert not node.attr.startswith(("system", "popen", "exec", "spawn", "fork")), \
                    f"os.{node.attr}"
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("Popen", "check_output"), node.attr


def test_the_adapter_writes_nothing():
    """No file is opened for writing, and the only file it opens at all is
    the daemon's socket, whose existence it checks."""
    source = (RUNTIME / "node_mcp.py").read_text()
    for forbidden in ('open(', '"w"', "'w'", "write_text", "mkdir", "unlink"):
        assert forbidden not in source, forbidden


def test_no_side_effecting_tool_is_declared():
    """§24: restart / stop / reconcile must go through admission if they
    exist. They do not exist yet, so they are not declared — an absent tool is
    a clearer fact than one that exists and refuses, and a caller cannot build
    a workflow on something that was never going to work."""
    names = mcp_mod.tool_names()
    for forbidden in ("runtime.restart", "runtime.stop", "runtime.reconcile",
                      "resource.reclaim", "container.remove", "path.create"):
        assert forbidden not in names, forbidden
    assert "runtime.status" in names and "path.admit" in names


def test_every_declared_tool_forwards_to_a_daemon_method_of_the_same_name(server):
    """No tool may be served from local logic. If a name has no counterpart in
    the daemon, this surface would have to invent the answer."""
    daemon = FakeDaemon()
    srv = NodeMcpServer(daemon)
    for name in mcp_mod.tool_names():
        payload = {"name": name, "arguments": {}}
        if name == "runtime.status":
            payload["arguments"] = {"id": "x"}
        if name == "runtime.explain":
            payload["arguments"] = {"id": "x"}
        if name == "ability.status":
            payload["arguments"] = {"name": "x"}
        if name == "path.resolve":
            payload["arguments"] = {"ref": "state/runtime/observed"}
        if name == "path.admit":
            payload["arguments"] = {"path": "/tmp/x"}
        rpc(srv, "tools/call", **payload)
    assert [m for m, _ in daemon.calls] == list(mcp_mod.tool_names())


def test_the_adapter_holds_no_state_between_calls(server):
    """Two identical calls must produce two identical daemon calls. A cache
    here is how a stale answer outlives the condition that produced it."""
    daemon = FakeDaemon()
    srv = NodeMcpServer(daemon)
    for _ in range(3):
        rpc(srv, "tools/call", name="runtime.list", arguments={})
    assert daemon.calls == [("runtime.list", {})] * 3


# ---------------------------------------------------------------------------
# §23 · an unavailable daemon is refused, never approximated
# ---------------------------------------------------------------------------


def test_a_dead_daemon_produces_an_error_not_an_empty_success(server):
    """The failure this architecture exists to prevent, at the surface where
    it would be easiest to get wrong: `[]` reads as "nothing is wrong"."""
    srv = server(fail="serverosd is not listening: /run/user/1000/serveros/serveros.sock")
    response = rpc(srv, "tools/call", name="runtime.list", arguments={})
    result = response["result"]
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "unavailable" in text
    assert "serverosd is not listening" in text


def test_the_daemon_unavailable_message_survives_into_the_tool_result(server):
    """`[]` would be the same answer for "nothing registered" and "I could not
    ask". The reason has to reach the caller for those to differ."""
    srv = server(fail="connection refused")
    text = rpc(srv, "tools/call", name="runtime.status",
               arguments={"id": "remote-device-endpoint"})["result"]["content"][0]["text"]
    assert "connection refused" in text


def test_a_missing_socket_refuses_by_name(tmp_path):
    """Not even an attempt to connect: the path is named in the refusal, since
    "cannot connect" sends people to look at the daemon and "no socket at X"
    sends them to look at X."""
    client = mcp_mod.DaemonClient(tmp_path / "absent.sock")
    with pytest.raises(DaemonUnavailable) as excinfo:
        client.call("runtime.list")
    assert "does not exist" in str(excinfo.value)
    assert "will not invent one" in str(excinfo.value)


def test_a_daemon_refusal_is_not_reported_as_a_success(server):
    """The daemon refuses for reasons — unknown method, unknown resource. The
    adapter must not launder a refusal into a result."""
    srv = server(fail="serverosd refused 'runtime.status': UnknownResource: no such resource")
    response = rpc(srv, "tools/call", name="runtime.status", arguments={"id": "nope"})
    assert response["result"]["isError"] is True


# ---------------------------------------------------------------------------
# protocol conformance
# ---------------------------------------------------------------------------


def test_initialize_reports_the_tools_capability_and_the_authority_boundary(server):
    result = rpc(server(), "initialize", protocolVersion="2025-06-18")["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "serveros-node-mcp"
    # Said once up front rather than left to be discovered from an error.
    assert "ability-management is the authority" in result["instructions"]


def test_notifications_get_no_response(server):
    """Answering a notification is a protocol violation and hangs some
    clients."""
    assert server().handle({"jsonrpc": "2.0",
                            "method": "notifications/initialized"}) is None


def test_an_unknown_protocol_method_is_a_json_rpc_error_not_a_crash(server):
    response = rpc(server(), "resources/list")
    assert response["error"]["code"] == -32601
    assert "Method not found" in response["error"]["message"]


def test_resources_and_prompts_are_not_offered_as_empty_lists(server):
    """An empty list for a capability that was never offered is a claim that
    it is empty rather than that it does not exist."""
    for method in ("resources/list", "prompts/list"):
        assert "error" in rpc(server(), method)


def test_an_unknown_tool_names_the_ones_that_exist(server):
    result = rpc(server(), "tools/call", name="runtime.reboot", arguments={})["result"]
    assert result["isError"] is True
    assert "runtime.list" in result["content"][0]["text"]


def test_an_unexpected_argument_is_refused_rather_than_ignored(server):
    """Silently dropping `force: true` teaches the caller it worked."""
    result = rpc(server(), "tools/call", name="runtime.list",
                 arguments={"force": True})["result"]
    assert result["isError"] is True
    assert "unexpected argument" in result["content"][0]["text"]


def test_tools_list_declares_a_closed_input_schema(server):
    tools = rpc(server(), "tools/list")["result"]["tools"]
    assert len(tools) == len(mcp_mod.tool_names())
    for tool in tools:
        assert tool["inputSchema"]["additionalProperties"] is False
        assert tool["description"]
        # A tool whose schema is not closed is a tool that will be called with
        # an argument nobody implemented.
        assert "id" not in tool["inputSchema"].get("properties", {}) or \
            tool["name"] in ("runtime.status", "runtime.explain")


def test_a_valid_tool_call_returns_the_daemons_answer_unchanged(server):
    daemon = FakeDaemon(results={"runtime.list": {"count": 5, "resources": [{"id": "a"}]}})
    srv = NodeMcpServer(daemon)
    result = rpc(srv, "tools/call", name="runtime.list", arguments={})["result"]
    assert json.loads(result["content"][0]["text"]) == {"count": 5, "resources": [{"id": "a"}]}
    assert result["isError"] is False


# ---------------------------------------------------------------------------
# the stdio loop
# ---------------------------------------------------------------------------


def run_stdio(lines: list[str], server) -> list[dict]:
    out = io.StringIO()
    mcp_mod.serve(instream=io.StringIO("\n".join(lines) + "\n"), outstream=out,
                  server=server)
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def test_the_stdio_loop_answers_one_request_per_line(server):
    responses = run_stdio([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ], server())
    assert [r["id"] for r in responses] == [1, 2]


def test_unparseable_input_gets_a_parse_error_instead_of_a_hang(server):
    """No id is recoverable, so id is null. Silence here hangs the client."""
    responses = run_stdio(["this is not json"], server())
    assert responses[0]["error"]["code"] == -32700
    assert responses[0]["id"] is None


def test_a_notification_in_the_stream_produces_no_output_line(server):
    responses = run_stdio([
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping", "params": {}}),
    ], server())
    assert [r["id"] for r in responses] == [7]


def test_an_internal_error_does_not_close_the_stream(server):
    class Exploding:
        def call(self, method, params=None):
            raise RuntimeError("boom")

    responses = run_stdio([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "runtime.list", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}}),
    ], NodeMcpServer(Exploding()))
    assert responses[0]["error"]["code"] == -32603
    assert responses[1]["id"] == 2  # the loop survived


# ---------------------------------------------------------------------------
# the socket the adapter will actually use
# ---------------------------------------------------------------------------


def test_the_default_socket_matches_the_daemons(tmp_path):
    """The two components must agree on the path without either importing the
    other's constant — they are separate processes and separate payloads."""
    daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
    assert mcp_mod.default_socket_path() == daemon_mod.default_socket_path()
