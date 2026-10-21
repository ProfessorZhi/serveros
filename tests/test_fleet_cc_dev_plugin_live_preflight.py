#!/usr/bin/env python3
"""The live-read preflight: refusals, redaction, and the BLOCKED verdict.

The preflight is the gate in front of a live Fleet Next read. These tests pin
the two properties that make it safe to run without an owner token:

* a 401 is reported as `BLOCKED_OWNER_OAUTH` with no `tools`/`result` keys, not
  as an empty tool list;
* an isolated CC config is inspected for the *presence* of a Fleet entry, but
  its `headers` and `oauth` values are never copied into the receipt. The test
  plants fake secrets in both and asserts the serialized receipt does not
  contain them.

The HTTP surface is a loopback fixture with no external network. No credential,
token store, model call or OAuth flow is involved.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import preflight_live_read as preflight  # noqa: E402

SCOPES = ["fleet:read", "fleet:send", "fleet:control", "fleet:create",
          "fleet:approve", "fleet:navigate"]


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - silence the test server
        return

    def _send(self, status: int, body: dict, headers: dict | None = None) -> None:
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok", "server": "serveros-fleet-mcp",
                             "version": "0.1.0", "auth_required": True})
        elif self.path == preflight.AUTH_SERVER_PATH:
            self._send(200, {"issuer": "https://fleet.example",
                             "scopes_supported": SCOPES,
                             "scopes_grantable": SCOPES,
                             "grant_types_supported": ["authorization_code",
                                                       "refresh_token"],
                             "registration_endpoint": "https://fleet.example/oauth/register"})
        elif self.path == preflight.PROTECTED_RESOURCE_PATH:
            self._send(200, {"scopes_supported": SCOPES,
                             "resource": "https://fleet.example/mcp"})
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        status = getattr(self.server, "mcp_status", 401)
        body = getattr(self.server, "mcp_body", {
            "error": "invalid_token",
            "error_description": "no Authorization header",
        })
        headers = {}
        if status == 401:
            headers["WWW-Authenticate"] = (
                'Bearer resource_metadata="https://fleet.example/'
                '.well-known/oauth-protected-resource"')
        self._send(status, body, headers)


class _Server:
    def __init__(self, *, mcp_status: int = 401, mcp_body: dict | None = None) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.mcp_status = mcp_status
        if mcp_body is not None:
            self.httpd.mcp_body = mcp_body
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ThePreflightRefusesToInventARead(unittest.TestCase):
    def test_a_401_is_a_blocked_owner_oauth_and_not_an_empty_tool_list(self) -> None:
        with _Server(mcp_status=401) as server:
            receipt = preflight.preflight(server.base, None)
        self.assertEqual(receipt["status"], preflight.BLOCKED_OAUTH)
        self.assertEqual(receipt["unauthenticated"]["initialize"]["status"], 401)
        self.assertEqual(receipt["unauthenticated"]["tools_list"]["status"], 401)
        self.assertFalse(receipt["unauthenticated"]["tools_list"]["has_tools"])
        self.assertFalse(receipt["unauthenticated"]["tools_list"]["has_result"])
        self.assertTrue(receipt["unauthenticated"]["www_authenticate_resource_metadata"])
        self.assertIn("fleet:read", receipt["oauth"]["scopes_supported"])
        self.assertTrue(receipt["oauth"]["registration_endpoint_present"])
        self.assertFalse(receipt["credentials_read"])
        self.assertEqual(receipt["model_calls"], 0)

    def test_a_200_initialize_and_tools_list_is_ready_not_blocked(self) -> None:
        body = {"jsonrpc": "2.0", "id": 1,
                "result": {"tools": [{"name": "fleet_list_devices"}]}}
        with _Server(mcp_status=200, mcp_body=body) as server:
            receipt = preflight.preflight(server.base, None)
        self.assertEqual(receipt["status"], preflight.READY)
        self.assertTrue(receipt["unauthenticated"]["tools_list"]["has_tools"])

    def test_a_closed_port_is_unreachable_and_not_a_traceback(self) -> None:
        receipt = preflight.preflight(f"http://127.0.0.1:{_free_port()}", None)
        self.assertEqual(receipt["status"], preflight.UNREACHABLE)

    def test_an_unexpected_status_is_recorded_not_guessed(self) -> None:
        with _Server(mcp_status=500, mcp_body={"error": "boom"}) as server:
            receipt = preflight.preflight(server.base, None)
        self.assertEqual(receipt["status"], preflight.UNEXPECTED)


class TheCcHomeScanRedactsValues(unittest.TestCase):
    SECRET_HEADER = "Bearer THIS-MUST-NOT-APPEAR"
    SECRET_CLIENT = "client-id-THIS-MUST-NOT-APPEAR"

    def _cc_home(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="cc-live-preflight-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, True))
        (tmp / ".claude.json").write_text(json.dumps({
            "mcpServers": {
                "serveros-fleet-live": {
                    "type": "http",
                    "url": "https://user:pass@fleet.example/mcp?token=THIS-MUST-NOT-APPEAR",
                    "headers": {"Authorization": self.SECRET_HEADER},
                    "oauth": {"clientId": self.SECRET_CLIENT,
                              "clientSecret": "THIS-MUST-NOT-APPEAR"},
                },
                "unrelated": {"type": "stdio", "command": "echo"},
            },
            "projects": {
                "/some/project": {
                    "mcpServers": {
                        "fleet-project": {
                            "type": "http",
                            "url": "https://fleet.example/mcp",
                            "headers": {"Authorization": self.SECRET_HEADER},
                        }
                    }
                }
            },
        }), encoding="utf-8")
        return tmp

    def test_the_fleet_entry_is_reported_without_its_secret_values(self) -> None:
        with _Server(mcp_status=401) as server:
            receipt = preflight.preflight(server.base, self._cc_home())
        self.assertEqual(receipt["status"], preflight.BLOCKED_OAUTH)
        self.assertTrue(receipt["cc_home"]["config_present"])
        self.assertTrue(receipt["cc_home"]["fleet_entry_present"])
        names = {row["name"] for row in receipt["cc_home"]["fleet_servers"]}
        self.assertEqual(names, {"serveros-fleet-live", "fleet-project"})
        for row in receipt["cc_home"]["fleet_servers"]:
            self.assertTrue(row["has_headers"])
            self.assertNotIn("THIS-MUST-NOT-APPEAR", json.dumps(row))
            self.assertNotIn("user:pass", row.get("url") or "")
            self.assertNotIn("token=", row.get("url") or "")
        encoded = json.dumps(receipt)
        self.assertNotIn(self.SECRET_HEADER, encoded)
        self.assertNotIn(self.SECRET_CLIENT, encoded)
        self.assertNotIn("THIS-MUST-NOT-APPEAR", encoded)
        self.assertNotIn("echo", encoded, "a stdio command is not reported")

    def test_a_url_path_that_is_not_mcp_is_redacted(self) -> None:
        self.assertEqual(
            preflight.sanitize_url("https://user:pw@fleet.example/secret-path?q=1"),
            "https://fleet.example/<redacted>")
        self.assertEqual(
            preflight.sanitize_url("https://fleet.example/mcp"),
            "https://fleet.example/mcp")


if __name__ == "__main__":
    unittest.main()
