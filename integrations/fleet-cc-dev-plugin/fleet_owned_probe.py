#!/usr/bin/env python3
"""Read-only probe of the real ServerOS owned stdio runtime.

This is not a fixture and not a provider substitute. It starts the actual
`bin/serveros-fleet-owned --stdio --config PATH`, uses the existing ServerOS
AgentControlStore selected by the runtime's own state root, and performs only
reads: initialize, tools/list, status, list_sessions, get_session and
native-capability. It never sends or creates anything.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import time
from pathlib import Path

EXPECTED_SCOPES = {
    "fleet:read", "fleet:send", "fleet:control", "fleet:create",
    "fleet:approve", "fleet:navigate",
}


class ProbeError(RuntimeError):
    pass


def _call(proc, request_id: int, method: str, params: dict, timeout: float) -> dict:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id,
                                 "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    end = time.time() + timeout
    while time.time() < end:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if not ready:
            continue
        line = proc.stdout.readline()
        if not line:
            raise ProbeError("owned runtime closed stdout")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise ProbeError(f"{method}: {message['error']}")
            return message
    raise ProbeError(f"{method} timed out")


def _tool_text(response: dict) -> dict:
    content = (response.get("result") or {}).get("content") or []
    if not content or content[0].get("type") != "text":
        raise ProbeError("tool result has no text content")
    try:
        return json.loads(content[0]["text"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ProbeError("tool result is not a JSON object") from exc


def probe(*, entry: Path, config: Path, state_dir: Path,
          cwd: Path, timeout: float = 60.0) -> dict:
    entry = Path(entry).resolve()
    config = Path(config).resolve()
    state_dir = Path(state_dir).resolve()
    if not entry.is_file() or not os.access(entry, os.X_OK):
        raise ProbeError(f"owned entry is not executable: {entry}")
    if not config.is_file():
        raise ProbeError(f"owned config is not a file: {config}")
    env = dict(os.environ)
    env["SERVEROS_STATE_DIR"] = str(state_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen(
        [str(entry), "--stdio", "--config", str(config)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, bufsize=1,
        cwd=str(cwd), env=env)
    try:
        init = _call(proc, 1, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "fleet-owned-probe", "version": "0"},
        }, timeout)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0",
                                     "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()
        listing = _call(proc, 2, "tools/list", {}, timeout)
        tools = [row["name"] for row in listing["result"]["tools"]]
        project_tool = next(
            (row for row in listing["result"]["tools"]
             if row["name"] == "fleet_create_project"), None)

        status = _tool_text(_call(proc, 3, "tools/call",
                                  {"name": "fleet_status", "arguments": {}}, timeout))
        sessions = _tool_text(_call(proc, 4, "tools/call",
                                    {"name": "fleet_list_sessions",
                                     "arguments": {}}, timeout))
        tokyo_rows = [row for row in sessions.get("sessions", [])
                      if row.get("device_ref") == status["surface"]["local_device_ref"]]
        session_read = None
        if tokyo_rows:
            ref = tokyo_rows[0]["session_ref"]
            session_read = _tool_text(_call(
                proc, 5, "tools/call",
                {"name": "fleet_get_session", "arguments": {"session_ref": ref}},
                timeout))
        capability = _tool_text(_call(
            proc, 6, "tools/call",
            {"name": "fleet_cloudcli_native_capability",
             "arguments": {"device_ref": status["surface"]["local_device_ref"]}},
            timeout))
        principal = status.get("principal") or {}
        scopes = sorted(principal.get("scopes") or [])
        owner_access = status.get("owner_access") or []
        surface = status.get("surface") or {}
        return {
            "schema": "serveros/fleet-owned-readback.v1",
            "entry": str(entry),
            "config": str(config),
            "state_dir": str(state_dir),
            "server_name": init["result"]["serverInfo"]["name"],
            "server_version": init["result"]["serverInfo"]["version"],
            "tools": sorted(tools),
            "tool_count_actual": len(tools),
            "project_create_present": project_tool is not None,
            "project_create_schema": project_tool,
            "principal": principal,
            "scopes": scopes,
            "six_scopes": EXPECTED_SCOPES <= set(scopes),
            "durability": surface.get("idempotency_durability"),
            "owner_access": owner_access,
            "owner_access_families": sorted(
                {row.get("runtime") for row in owner_access if isinstance(row, dict)}),
            "status": {
                "surface": surface,
                "identity_layer": status.get("identity_layer"),
                "providers": status.get("providers"),
                "policy": status.get("policy"),
            },
            "sessions_total": sessions.get("total"),
            "tokyo_sessions": len(tokyo_rows),
            "session_read": session_read,
            "native_capability": {
                "state": capability.get("state"),
                "recorded_count": capability.get("recorded_count"),
                "observed_count": capability.get("observed_count"),
            },
            "read_only": True,
            "messages_sent": 0,
            "creates_attempted": 0,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
