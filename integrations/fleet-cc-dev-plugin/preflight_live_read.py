#!/usr/bin/env python3
"""Preflight a **live** Fleet Next read, without reading a credential.

The question this answers is narrow and must be answered before anyone tries a
live tool call:

* is the surface up, and what does its own `/health` say?
* what does its OAuth discovery advertise, and is `fleet:read` in it?
* does an *isolated* Claude Code configuration already hold a Fleet MCP entry
  (which is the only way a CC session could have a live grant)?
* what does the surface say to an **unauthenticated** `initialize` and
  `tools/list`? A 401 is a refusal, not an empty tool list.

It never reads a token store, never reads a `headers` or `oauth` value from a
client configuration, and never starts an OAuth flow. A client entry is
reported only as `type`, a sanitized `url`, and whether `oauth`/`headers`
keys exist — the values are not touched. The only requests are GETs of
`/health` and the two discovery documents, and two unauthenticated POSTs to
`/mcp` with no `Authorization` header.

Status is one of:

* `BLOCKED_OWNER_OAUTH` — the surface refuses unauthenticated calls and no CC
  configuration presents a live grant; the owner must complete consent (or a
  client must be registered and consented) before a live read is possible.
* `READY_FOR_AUTHORIZED_READ` — the unauthenticated `initialize` and
  `tools/list` both answered 200. This would mean the surface is open, which
  the live deployment is not; the name says only that the protocol answered.
* `BLOCKED_SURFACE_UNREACHABLE` — `/health` did not answer.
* `BLOCKED_UNEXPECTED` — a status that is neither 200 nor 401; recorded, not
  guessed at.

Exit status: 0 when the preflight completed (including `BLOCKED_OWNER_OAUTH`),
3 when the surface was unreachable, 4 for an unexpected response. A BLOCKED
result is a finding, not a runner error.

Usage:

    python3 integrations/fleet-cc-dev-plugin/preflight_live_read.py \
        --surface http://127.0.0.1:3411 \
        --cc-home /path/to/isolated/CLAUDE_CONFIG_DIR \
        --out /path/to/receipt.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

READY = "READY_FOR_AUTHORIZED_READ"
BLOCKED_OAUTH = "BLOCKED_OWNER_OAUTH"
UNREACHABLE = "BLOCKED_SURFACE_UNREACHABLE"
UNEXPECTED = "BLOCKED_UNEXPECTED"

MCP_PATH = "/mcp"
AUTH_SERVER_PATH = "/.well-known/oauth-authorization-server"
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _request(url: str, *, body: dict | None = None,
             timeout: float = 8.0) -> tuple[int, dict[str, str], bytes]:
    data = None
    headers = {"Accept": "application/json, text/event-stream"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return (response.status,
                    {key.lower(): value for key, value in response.headers.items()},
                    response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {key.lower(): value for key, value in exc.headers.items()}, exc.read()
    except urllib.error.URLError:
        # A closed port is a reading, not an exception: the caller reports
        # BLOCKED_SURFACE_UNREACHABLE rather than a traceback.
        return 0, {}, b""


def _json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sanitize_url(value: object) -> str | None:
    """`scheme://host/path`, without userinfo, query or fragment.

    A client entry is not a credential store, but a malformed one can carry a
    value in its URL. The preflight reports only the part that identifies the
    endpoint, so nothing that looks like a secret is copied into a receipt.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return "<unparseable>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    # Keep the endpoint path only when it is the documented `/mcp`; anything
    # else is redacted, because a path is a place a token can be smuggled into.
    path = parts.path if parts.path in ("", "/", MCP_PATH) else "/<redacted>"
    return urllib.parse.urlunsplit((parts.scheme, host, path, "", ""))


def scan_cc_home(cc_home: Path | None) -> dict[str, Any]:
    """Fleet MCP entries in an isolated CC config, with values left untouched.

    Claude Code keeps MCP servers at the top level and, in some versions, under
    `projects.<path>.mcpServers`. Both are scanned. Only the entry's name,
    `type`, sanitized `url`, and the *presence* of `oauth` / `headers` are
    reported: an `oauth` object can carry a client id and a `headers` object can
    carry a bearer, and neither is this tool's to read.
    """
    result: dict[str, Any] = {
        "path_provided": cc_home is not None,
        "config_present": False,
        "fleet_servers": [],
        "fleet_entry_present": False,
    }
    if cc_home is None:
        return result
    config = Path(cc_home) / ".claude.json"
    if not config.is_file():
        return result
    result["config_present"] = True
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return result
    if not isinstance(data, dict):
        return result

    entries: dict[str, dict[str, Any]] = {}

    def add(mapping: Any) -> None:
        if not isinstance(mapping, dict):
            return
        for name, entry in mapping.items():
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            is_fleet = ("fleet" in str(name).lower()
                        or (isinstance(url, str) and "fleet" in url.lower()))
            if not is_fleet:
                continue
            entries[str(name)] = {
                "name": str(name),
                "type": entry.get("type"),
                "url": sanitize_url(url),
                "has_oauth": "oauth" in entry,
                "has_headers": "headers" in entry,
            }

    add(data.get("mcpServers") or {})
    for project in (data.get("projects") or {}).values():
        if isinstance(project, dict):
            add(project.get("mcpServers") or {})
    result["fleet_servers"] = [entries[name] for name in sorted(entries)]
    result["fleet_entry_present"] = bool(entries)
    return result


def preflight(surface: str, cc_home: Path | None) -> dict[str, Any]:
    base = surface.rstrip("/")
    if base.endswith(MCP_PATH):
        base = base[: -len(MCP_PATH)]
    health_status, _, health_raw = _request(base + "/health")
    health = _json(health_raw)

    auth_status, _, auth_raw = _request(base + AUTH_SERVER_PATH)
    auth = _json(auth_raw)
    protected_status, _, protected_raw = _request(base + PROTECTED_RESOURCE_PATH)
    protected = _json(protected_raw)

    initialize_status, initialize_headers, initialize_raw = _request(
        base + MCP_PATH,
        body={"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "p1-live-preflight", "version": "0"}}},
    )
    initialize = _json(initialize_raw)
    tools_status, _, tools_raw = _request(
        base + MCP_PATH,
        body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    tools = _json(tools_raw)
    initialize_result = initialize.get("result") if isinstance(
        initialize.get("result"), dict) else {}
    tools_result = tools.get("result") if isinstance(tools.get("result"), dict) else {}

    cc = scan_cc_home(cc_home)
    if health_status != 200:
        status = UNREACHABLE
    elif initialize_status == 200 and tools_status == 200:
        status = READY
    elif initialize_status == 401 or tools_status == 401:
        status = BLOCKED_OAUTH
    else:
        status = UNEXPECTED

    return {
        "schema": "serveros/fleet-cc-live-read-preflight.v1",
        "checked_at": _now(),
        "surface": {
            "base": base,
            "health_status": health_status,
            "server": health.get("server"),
            "version": health.get("version"),
            "auth_required": health.get("auth_required"),
        },
        "oauth": {
            "authorization_server_status": auth_status,
            "protected_resource_status": protected_status,
            "scopes_supported": auth.get("scopes_supported")
                or protected.get("scopes_supported"),
            "scopes_grantable": auth.get("scopes_grantable")
                or protected.get("scopes_grantable"),
            "grant_types_supported": auth.get("grant_types_supported"),
            "registration_endpoint_present": bool(auth.get("registration_endpoint")),
            "resource": protected.get("resource"),
        },
        "unauthenticated": {
            "initialize": {
                "status": initialize_status,
                "error": (initialize.get("error") or {}).get("code")
                    if isinstance(initialize.get("error"), dict) else initialize.get("error"),
                "has_result": "result" in initialize,
                "server": (initialize_result.get("serverInfo") or {}).get("name"),
            },
            "tools_list": {
                "status": tools_status,
                "error": (tools.get("error") or {}).get("code")
                    if isinstance(tools.get("error"), dict) else tools.get("error"),
                "has_result": "result" in tools,
                # The MCP result carries the list under `result.tools`, not at
                # the top level. A 401 body has neither, and that absence is
                # the difference between a refusal and an empty list.
                "has_tools": isinstance(tools_result.get("tools"), list),
            },
            "www_authenticate_resource_metadata": (
                "resource_metadata=" in
                (initialize_headers.get("www-authenticate") or "")
            ),
        },
        "cc_home": cc,
        "status": status,
        "credentials_read": False,
        "model_calls": 0,
        "network": "loopback-only",
        "evidence_level": "PREFLIGHT_ONLY",
        "note": (
            "A 401 is a refusal, not an empty tool list. No Authorization header "
            "was sent, no token store was read, and no OAuth flow was started. A "
            "live read requires an owner-completed consent (or a client the owner "
            "has already registered and consented to) plus an isolated CC "
            "configuration that holds it. The owner PAT anchor was not read."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", default="http://127.0.0.1:3411",
                        help="Fleet Next base URL (loopback by default)")
    parser.add_argument("--cc-home", type=Path, default=None,
                        help="an isolated CLAUDE_CONFIG_DIR to inspect (read-only)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON receipt here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        receipt = preflight(args.surface, args.cc_home)
    except Exception as exc:  # noqa: BLE001 - the receipt records the failure
        receipt = {
            "schema": "serveros/fleet-cc-live-read-preflight.v1",
            "checked_at": _now(),
            "surface": {"base": args.surface},
            "status": UNEXPECTED,
            "error": f"{type(exc).__name__}: {exc}",
            "credentials_read": False,
            "model_calls": 0,
        }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
    if args.json:
        sys.stdout.write(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(
            f"status          {receipt['status']}\n"
            f"surface         {receipt['surface'].get('base')}\n"
            f"health          {receipt['surface'].get('health_status')}\n"
            f"initialize      {receipt['unauthenticated']['initialize'].get('status')}\n"
            f"tools/list      {receipt['unauthenticated']['tools_list'].get('status')}\n"
            f"fleet entry     {receipt['cc_home'].get('fleet_entry_present')}\n"
            f"credentials     not read\n"
            f"model calls     0\n")
    if receipt["status"] == UNREACHABLE:
        return 3
    if receipt["status"] == UNEXPECTED:
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
