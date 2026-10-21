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
import subprocess
import time
from pathlib import Path

from stdio_framing import LineFramer

EXPECTED_SCOPES = {
    "fleet:read", "fleet:send", "fleet:control", "fleet:create",
    "fleet:approve", "fleet:navigate",
}

#: Provider-credential variables are not needed by a read-only owned runtime.
#: Their values are never recorded; only the names that were removed are.
CREDENTIAL_ENV_KEYS = (
    "OPENAI_API_KEY", "CODEX_API_KEY",
    "OPENAI_ACCESS_TOKEN", "CODEX_ACCESS_TOKEN",
)


class ProbeError(RuntimeError):
    pass


def _call(proc, reader: LineFramer, request_id: int, method: str,
          params: dict, timeout: float) -> dict:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
                                  "method": method, "params": params})
                      + "\n").encode("utf-8"))
    proc.stdin.flush()
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise ProbeError(f"{method} timed out")
        try:
            line = reader.next_line(remaining)
        except (TimeoutError, EOFError) as exc:
            raise ProbeError(f"{method}: {exc}") from exc
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if message.get("id") == request_id:
            if "error" in message:
                raise ProbeError(f"{method}: {message['error']}")
            return message


def _tool_text(response: dict) -> dict:
    content = (response.get("result") or {}).get("content") or []
    if not content or content[0].get("type") != "text":
        raise ProbeError("tool result has no text content")
    try:
        return json.loads(content[0]["text"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ProbeError("tool result is not a JSON object") from exc


def probe(*, entry: Path, config: Path, state_dir: Path,
          cwd: Path, timeout: float = 60.0,
          tmpdir: Path | None = None) -> dict:
    entry = Path(entry).resolve()
    config = Path(config).resolve()
    state_dir = Path(state_dir).resolve()
    if not entry.is_file() or not os.access(entry, os.X_OK):
        raise ProbeError(f"owned entry is not executable: {entry}")
    if not config.is_file():
        raise ProbeError(f"owned config is not a file: {config}")
    credential_names = sorted(k for k in CREDENTIAL_ENV_KEYS if k in os.environ)
    env = {k: v for k, v in os.environ.items() if k not in CREDENTIAL_ENV_KEYS}
    env["SERVEROS_STATE_DIR"] = str(state_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if tmpdir is not None:
        env["TMPDIR"] = str(Path(tmpdir))
    proc = subprocess.Popen(
        [str(entry), "--stdio", "--config", str(config)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=False, bufsize=0,
        cwd=str(cwd), env=env)
    reader = LineFramer(proc.stdout)
    try:
        init = _call(proc, reader, 1, "initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "fleet-owned-probe", "version": "0"},
        }, timeout)
        proc.stdin.write((json.dumps({"jsonrpc": "2.0",
                                      "method": "notifications/initialized"})
                          + "\n").encode("utf-8"))
        proc.stdin.flush()
        listing = _call(proc, reader, 2, "tools/list", {}, timeout)
        tools = [row["name"] for row in listing["result"]["tools"]]
        project_tool = next(
            (row for row in listing["result"]["tools"]
             if row["name"] == "fleet_create_project"), None)

        status = _tool_text(_call(proc, reader, 3, "tools/call",
                                  {"name": "fleet_status", "arguments": {}}, timeout))
        sessions = _tool_text(_call(proc, reader, 4, "tools/call",
                                    {"name": "fleet_list_sessions",
                                     "arguments": {}}, timeout))
        tokyo_rows = [row for row in sessions.get("sessions", [])
                      if row.get("device_ref") == status["surface"]["local_device_ref"]]
        session_read = None
        if tokyo_rows:
            ref = tokyo_rows[0]["session_ref"]
            session_read = _tool_text(_call(
                proc, reader, 5, "tools/call",
                {"name": "fleet_get_session", "arguments": {"session_ref": ref}},
                timeout))
        capability = _tool_text(_call(
            proc, reader, 6, "tools/call",
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
            "credential_env_names_removed": credential_names,
            "credential_env_values_recorded": False,
            "credential_file_read": False,
            "login_copied": False,
            "framing": "bounded-byte-lines",
            "tmpdir": str(Path(tmpdir)) if tmpdir is not None else None,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
