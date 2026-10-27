#!/usr/bin/env python3
"""Capture a CloudCLI MCP gateway's live ``tools/list``, portably.

Why this exists
---------------
A compatibility record is only as good as it is reproducible. This script is the
reproduction: standard library only (no node, no pip install), it performs the
three read-only calls that describe a build's verb surface, and it writes a
version-stamped JSON file that `tests/test_cloudcli_windows_compat.py` and a
human reviewer can both read.

It never writes, prints or logs the PAT: the token is read from a file named by
an environment variable, used as a request header, and checked for absence in
everything this script emits.

Usage
-----
    export CLOUDCLI_MCP_URL="http://127.0.0.1:3001/mcp"
    export CLOUDCLI_PAT_FILE="$HOME/.cloudcli/mcp-pat-claude-code.txt"
    python docs/compat/capture_cloudcli_tools_list.py --out /tmp/tools-list-1.41.0.json

    # optional: compare the parameter surface against an earlier capture
    python docs/compat/capture_cloudcli_tools_list.py --out /tmp/new.json \
        --compare tests/data/capture-cloudcli-1.41.0-tools-list-2026-10-27.json

Exit codes: 0 captured, 2 the gateway refused or answered unusably, 3 a
credential reached the output (a bug in this script, not in the device).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import socket
import sys
import urllib.error
import urllib.request

DEFAULT_PROTOCOL = "2025-06-18"


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def _parse_body(text: str, content_type: str) -> dict:
    """Accept either a JSON body or an SSE frame stream (the gateway may use both)."""
    if not text.strip():
        return {}
    stripped = text.lstrip()
    if "text/event-stream" in (content_type or "") or stripped.startswith(("event:", "data:")):
        payloads = []
        for frame in text.replace("\r\n", "\n").split("\n\n"):
            data = [
                line[len("data:"):].strip()
                for line in frame.split("\n")
                if line.startswith("data:")
            ]
            if data:
                payloads.append("\n".join(data))
        if not payloads:
            raise ValueError("SSE response carried no data frames")
        return json.loads(payloads[-1])
    return json.loads(text)


class Gateway:
    def __init__(self, url: str, token: str, protocol: str, timeout: float = 30.0):
        self.url = url
        self._token = token
        self.protocol = protocol
        self.timeout = timeout
        self.session_id: str | None = None

    def rpc(self, method: str, params: dict | None, request_id: int | None) -> dict:
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id

        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {self._token}",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(message).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self.session_id = response.headers.get("mcp-session-id") or self.session_id
                body = response.read().decode("utf-8", "replace")
                status = response.status
                content_type = response.headers.get("content-type", "")
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", "replace")[:2000]
            status = error.code
            content_type = error.headers.get("content-type", "") if error.headers else ""
            raise SystemExit(
                f"gateway answered HTTP {status} to {method}; body starts: {body!r}"
            ) from None
        except urllib.error.URLError as error:
            raise SystemExit(f"cannot reach {self.url}: {error.reason}") from None

        if request_id is None:  # notification: no JSON-RPC reply expected
            return {"status": status}
        payload = _parse_body(body, content_type)
        if "error" in payload:
            raise SystemExit(f"{method} returned a JSON-RPC error: {json.dumps(payload['error'])}")
        return payload.get("result", {})


# --------------------------------------------------------------------------- #
# shape of the record
# --------------------------------------------------------------------------- #
def _surface(tools: list[dict]) -> dict[str, dict]:
    return {
        tool["name"]: {
            "input_properties": sorted(tool.get("inputSchema", {}).get("properties", {})),
            "required": tool.get("inputSchema", {}).get("required", []),
            "output_schema": "outputSchema" in tool,
            "annotations": sorted(tool.get("annotations", {})),
            "handler_bound_advertised": "cloudcli/handlerBound" in tool.get("_meta", {}),
            "not_implemented_code": "MCP_TOOL_NOT_IMPLEMENTED"
            in tool.get("_meta", {}).get("cloudcli/errorCodes", []),
        }
        for tool in tools
    }


def _diff(previous: dict, current: dict) -> dict:
    old = _surface(previous.get("tools", []))
    new = _surface(current["tools"])
    return {
        "added_tools": sorted(set(new) - set(old)),
        "removed_tools": sorted(set(old) - set(new)),
        "gained_parameters": {
            name: sorted(set(new[name]["input_properties"]) - set(old[name]["input_properties"]))
            for name in sorted(set(new) & set(old))
            if set(new[name]["input_properties"]) - set(old[name]["input_properties"])
        },
        "lost_parameters": {
            name: sorted(set(old[name]["input_properties"]) - set(new[name]["input_properties"]))
            for name in sorted(set(new) & set(old))
            if set(old[name]["input_properties"]) - set(new[name]["input_properties"])
        },
        "gained_required": {
            name: sorted(set(new[name]["required"]) - set(old[name]["required"]))
            for name in sorted(set(new) & set(old))
            if set(new[name]["required"]) - set(old[name]["required"])
        },
        "handler_bound_advertised_now": sorted(
            name for name, entry in new.items() if entry["handler_bound_advertised"]
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("CLOUDCLI_MCP_URL", "http://127.0.0.1:3001/mcp"))
    parser.add_argument("--token-file", default=os.environ.get("CLOUDCLI_PAT_FILE"))
    parser.add_argument("--protocol", default=DEFAULT_PROTOCOL)
    parser.add_argument("--out", required=True, help="where to write the capture JSON")
    parser.add_argument("--compare", help="an earlier capture to diff the parameter surface against")
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="free-text record note (repeatable), e.g. install path and patch states",
    )
    args = parser.parse_args(argv)

    if not args.token_file:
        parser.error("no PAT file: set CLOUDCLI_PAT_FILE or pass --token-file")
    try:
        with open(args.token_file, encoding="utf-8") as handle:
            token = handle.read().strip()
    except OSError as error:
        raise SystemExit(f"cannot read the PAT file: {error.strerror}") from None
    if not token:
        raise SystemExit("the PAT file is empty")

    gateway = Gateway(args.url, token, args.protocol)
    init = gateway.rpc(
        "initialize",
        {
            "protocolVersion": args.protocol,
            "capabilities": {},
            "clientInfo": {"name": "serveros-cloudcli-compat-capture", "version": "1.0.0"},
        },
        1,
    )
    gateway.rpc("notifications/initialized", {}, None)
    listed = gateway.rpc("tools/list", {}, 2)

    tools = sorted(listed.get("tools", []), key=lambda tool: tool["name"])
    if not tools:
        raise SystemExit("tools/list returned no tools")

    capture = {
        "schema": "serveros/cloudcli-tools-list-capture.v2",
        "captured_at_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "captured_from": f"{args.url} (MCP {init.get('protocolVersion')}, initialize -> notifications/initialized -> tools/list)",
        "device": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "server": (init.get("serverInfo") or {}).get("name"),
        "server_version": (init.get("serverInfo") or {}).get("version"),
        "protocolVersion": init.get("protocolVersion"),
        "tool_count": len(tools),
        "notes": args.note,
        "verbatim_fields": ["name", "annotations", "execution", "_meta", "inputSchema", "outputSchema"],
        "tools": [
            {
                key: tool[key]
                for key in ("name", "annotations", "execution", "_meta", "inputSchema", "outputSchema")
                if key in tool
            }
            for tool in tools
        ],
    }
    if listed.get("nextCursor"):
        capture["nextCursor"] = listed["nextCursor"]

    summary: dict = {
        "tool_count": len(tools),
        "protocolVersion": capture["protocolVersion"],
        "server": f"{capture['server']} {capture['server_version']}",
        "tools_without_output_schema": [
            tool["name"] for tool in capture["tools"] if "outputSchema" not in tool
        ],
        "tools_advertising_handler_bound": sorted(
            name
            for name, entry in _surface(capture["tools"]).items()
            if entry["handler_bound_advertised"]
        ),
    }
    if args.compare:
        with open(args.compare, encoding="utf-8") as handle:
            summary["diff_vs"] = args.compare
            summary["diff"] = _diff(json.load(handle), capture)

    rendered = json.dumps(capture, indent=2, ensure_ascii=False) + "\n"
    if token in rendered or token in json.dumps(summary):
        # Never let a credential reach stdout or a checked-in file.
        return 3
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nwrote {args.out} ({len(rendered)} bytes, {len(tools)} tools)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
