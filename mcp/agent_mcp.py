#!/usr/bin/env python3
"""serveros-agent-mcp — Agent Session control facade.

Policy stays in serverosd. Provider-native session truth stays in the provider.
This process only composes those two into typed Agent/Session operations.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


node_mcp = _load("serveros_mcp_node_shared", "node_mcp.py")
service_mod = _load("serveros_agent_session_service", "agent_session_service.py")
botmux_mod = _load("serveros_botmux_provider", "botmux_provider.py")

PROTOCOL_VERSION = node_mcp.PROTOCOL_VERSION
SERVER_NAME = "serveros-agent-mcp"
SERVER_VERSION = "0.1.0"


def _schema(properties=None, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


TOOLS: tuple[dict, ...] = (
    {"name": "agent.list",
     "description": "Agents exposed by configured local Agent Session providers.",
     "inputSchema": _schema()},
    {"name": "agent.inspect",
     "description": "One provider-backed Agent and its runtime identity.",
     "inputSchema": _schema({"id": {"type": "string"}}, ["id"])},
    {"name": "session.list",
     "description": "Normalized local Agent Sessions with raw provider status and provenance.",
     "inputSchema": _schema()},
    {"name": "session.inspect",
     "description": "One normalized Agent Session. Does not infer a session from a PID.",
     "inputSchema": _schema({"ref": {"type": "string"}}, ["ref"])},
    {"name": "session.create",
     "description": "Create a provider session after ServerOS admission.",
     "inputSchema": _schema({
         "provider": {"type": "string"}, "agent_id": {"type": "string"},
         "instruction": {"type": "string"}, "idempotency_key": {"type": "string"},
         "workspace": {"type": "string"},
     }, ["provider", "agent_id", "instruction", "idempotency_key"])},
    {"name": "session.send",
     "description": "Send one follow-up turn to an existing session after admission.",
     "inputSchema": _schema({
         "ref": {"type": "string"}, "instruction": {"type": "string"},
         "turn_idempotency_key": {"type": "string"},
     }, ["ref", "instruction", "turn_idempotency_key"])},
    {"name": "session.result",
     "description": "Read the provider's async result for a session/turn.",
     "inputSchema": _schema({
         "ref": {"type": "string"}, "trigger_id": {"type": "string"},
     }, ["ref"])},
)


def tool_names() -> tuple[str, ...]:
    return tuple(tool["name"] for tool in TOOLS)


class AgentControlClient:
    """Calls provider mechanisms, with every mutation admitted by serverosd."""

    def __init__(self, daemon=None, providers=None) -> None:
        self.daemon = daemon or node_mcp.DaemonClient()
        self._providers = providers

    def _device_ref(self) -> str:
        view = self.daemon.call("device.show", {})
        ref = str((view or {}).get("device_ref") or "").strip()
        if not ref:
            raise RuntimeError("serverosd device.show returned no authored device_ref")
        return ref

    def _service(self):
        providers = self._providers
        if providers is None:
            providers = [botmux_mod.BotMuxProvider()]

        def admit(action: str, context: dict) -> dict:
            return self.daemon.call("agent.action.admit", {
                "action": action,
                "context": context,
            })

        return service_mod.SessionService(
            device_ref=self._device_ref(), providers=list(providers), admission=admit,
        )

    def call(self, name: str, arguments: dict | None = None) -> dict:
        args = dict(arguments or {})
        service = self._service()
        if name == "agent.list":
            return service.agent_list()
        if name == "agent.inspect":
            return service.agent_inspect(args["id"])
        if name == "session.list":
            return service.session_list()
        if name == "session.inspect":
            return service.session_inspect(args["ref"])
        if name == "session.create":
            return service.create(
                provider_id=args["provider"], agent_id=args["agent_id"],
                instruction=args["instruction"], idempotency_key=args["idempotency_key"],
                workspace=args.get("workspace"),
            )
        if name == "session.send":
            return service.send(
                session_ref_value=args["ref"], instruction=args["instruction"],
                turn_idempotency_key=args["turn_idempotency_key"],
            )
        if name == "session.result":
            return service.result(
                session_ref_value=args["ref"], trigger_id=args.get("trigger_id"),
            )
        raise RuntimeError(f"unimplemented Agent MCP operation: {name}")


class AgentMcpServer:
    def __init__(self, client=None) -> None:
        self.client = client or AgentControlClient()

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}
        if method == "initialize":
            return node_mcp._result(request_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Agent Session state is provider-backed. ServerOS owns device identity "
                    "and admission; this MCP owns neither. Mutating tools call serverosd "
                    "admission before the provider. Exact unsupported actions remain visible "
                    "inside action_support rather than being translated to stronger verbs."
                ),
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return node_mcp._result(request_id, {"tools": [dict(tool) for tool in TOOLS]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "ping":
            return node_mcp._result(request_id, {})
        if str(method).startswith("notifications/"):
            return None
        return node_mcp._error(request_id, -32601, f"Method not found: {method!r}")

    def _call_tool(self, request_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in tool_names():
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"Unknown tool {name!r}. Available: {list(tool_names())}"}],
            })
        tool = TOOLS[tool_names().index(name)]
        allowed = set(tool["inputSchema"]["properties"])
        required = set(tool["inputSchema"].get("required") or [])
        unexpected = set(arguments) - allowed
        missing = required - set(arguments)
        if unexpected or missing:
            problem = []
            if unexpected:
                problem.append(f"unexpected={sorted(unexpected)}")
            if missing:
                problem.append(f"missing={sorted(missing)}")
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"{name}: {'; '.join(problem)}"}],
            })
        try:
            result = self.client.call(name, arguments)
        except Exception as exc:  # provider/admission failures are named tool errors
            return node_mcp._result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} failed: {type(exc).__name__}: {exc}"}],
            })
        return node_mcp._result(request_id, {
            "content": [{"type": "text", "text":
                         json.dumps(result, indent=2, sort_keys=True, default=str)}],
            "isError": False,
        })


def serve(instream=None, outstream=None, server=None) -> int:
    return node_mcp.serve(instream=instream, outstream=outstream,
                          server=server or AgentMcpServer())


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="serveros-agent-mcp")
    parser.add_argument("--socket", help="serverosd Unix socket path")
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args(argv)
    if args.list_tools:
        for tool in TOOLS:
            print(f"{tool['name']:<20} {tool['description']}")
        return 0
    daemon = node_mcp.DaemonClient(Path(args.socket) if args.socket else None)
    return serve(server=AgentMcpServer(AgentControlClient(daemon=daemon)))


if __name__ == "__main__":
    sys.exit(main())
