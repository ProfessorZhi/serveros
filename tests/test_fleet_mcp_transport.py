#!/usr/bin/env python3
"""The node signature protocol, the HTTP transport, and the OAuth gate.

These are the two places a request crosses a trust boundary. The tests below
are written as the attacker's sentence — "I can replay this", "I can point the
redirect anywhere", "a read token can still send" — and each one is kept false.
"""
from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import re
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import node_protocol as np  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ServerBinding,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import FleetService  # noqa: E402
from fleet_mcp.http_transport import (  # noqa: E402
    HttpSurface,
    _Handler,
    _without_query,
    build_oauth,
    serve,
)
from fleet_mcp.oauth import OAuthError, TokenStore, _b64, _validate_redirect_uri  # noqa: E402
from fleet_mcp.runtime import clients_command  # noqa: E402

WINDOWS = "device:work-fj"
TOKYO = "device:tokyo-shared-dev-01"
CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


class NodeSignature(unittest.TestCase):
    """The connector is dialled by a caller that must prove it holds the secret."""

    def setUp(self):
        self.secret = "node-secret-value"
        self.replay = np.ReplayCache()
        self.device = WINDOWS

    def _headers(self, **overrides):
        headers = np.sign(self.secret, method="POST", path="/v1/sessions/abc/read",
                          body=b'{"limit":5}', device_ref=self.device)
        headers.update(overrides)
        return headers

    def _verify(self, **kwargs):
        base = dict(secret=self.secret, method="POST", path="/v1/sessions/abc/read",
                    body=b'{"limit":5}', headers=self._headers(),
                    expected_device_ref=self.device, replay=self.replay)
        base.update(kwargs)
        return np.verify(base.pop("secret"), **base)

    def test_a_correctly_signed_request_authenticates(self):
        self.assertEqual(self._verify(), self.device)

    def test_a_changed_body_invalidates_the_signature(self):
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(body=b'{"limit":5000}')
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_a_changed_path_invalidates_the_signature(self):
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(path="/v1/sessions/abc/send")
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_a_changed_method_invalidates_the_signature(self):
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(method="GET")
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_another_secret_does_not_verify(self):
        headers = np.sign("a-different-secret", method="POST",
                          path="/v1/sessions/abc/read", body=b'{"limit":5}',
                          device_ref=self.device)
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_an_old_capture_is_refused(self):
        headers = self._headers(**{np.TIMESTAMP_HEADER: str(int(time.time()) - 600)})
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "STALE_REQUEST")

    def test_a_fresh_capture_sent_twice_is_refused(self):
        headers = self._headers()
        self._verify(headers=headers)
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "REPLAY")

    def test_one_devices_secret_is_not_another_devices(self):
        headers = np.sign(self.secret, method="POST", path="/v1/sessions/abc/read",
                          body=b'{"limit":5}', device_ref=TOKYO)
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "DEVICE_MISMATCH")

    def test_a_wrong_device_does_not_burn_the_nonce(self):
        """Ordering, asserted: a wrong-device request must not consume a nonce.

        If the device check ran after the replay check, a caller holding a
        secret bound to another device could spend nonces here — turning a
        rejected request into a denial of service against the real one.
        """
        nonce = "fixed-nonce-for-the-test"
        wrong = np.sign(self.secret, method="GET", path="/v1/sessions", body=b"",
                        device_ref=TOKYO, nonce=nonce)
        with self.assertRaises(np.SignatureError):
            np.verify(self.secret, method="GET", path="/v1/sessions", body=b"",
                      headers=wrong, expected_device_ref=self.device, replay=self.replay)
        right = np.sign(self.secret, method="GET", path="/v1/sessions", body=b"",
                        device_ref=self.device, nonce=nonce)
        who = np.verify(self.secret, method="GET", path="/v1/sessions", body=b"",
                        headers=right, expected_device_ref=self.device, replay=self.replay)
        self.assertEqual(who, self.device)

    def test_a_foreign_protocol_is_refused_before_anything_else(self):
        headers = self._headers(**{np.PROTOCOL_HEADER: "serveros/fleet-node/v2"})
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "PROTOCOL_MISMATCH")

    def test_missing_signature_material_is_refused(self):
        headers = self._headers()
        del headers[np.SIGNATURE_HEADER]
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers=headers)
        self.assertEqual(caught.exception.code, "MISSING_SIGNATURE")

    def test_a_query_string_is_not_part_of_the_signature(self):
        path, query = np.split_path_and_query("/v1/sessions?limit=5&project=p")
        self.assertEqual(path, "/v1/sessions")
        self.assertEqual(query, "limit=5&project=p")
        self.assertEqual(np.split_path_and_query("/v1/health"), ("/v1/health", ""))

    def test_the_route_set_is_closed(self):
        """An added route must be a deliberate edit to this tuple."""
        self.assertNotIn("POST /v1/exec", np.ROUTES)
        for route in np.ROUTES:
            method, _, path = route.partition(" ")
            self.assertIn(method, ("GET", "POST"))
            self.assertTrue(path.startswith("/v1/"))
        self.assertEqual(len(np.ROUTES), 10)
        # The one route control added, named so that a future edit cannot add a
        # second one under cover of this count going up.
        self.assertIn("POST /v1/sessions/{id}/interrupt", np.ROUTES)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return the 302 itself. Following it would hide the code under test."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Client:
    """A small HTTP client that returns 4xx/5xx rather than raising."""

    def __init__(self, base: str) -> None:
        self.base = base
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None, form=None):
        headers = dict(headers or {})
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers=headers)
        try:
            with self._opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def rpc(self, payload: dict, *, token: str | None = None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        status, out_headers, body = self.request(
            "POST", "/mcp", body=json.dumps(payload).encode(), headers=headers
        )
        return status, out_headers, (json.loads(body) if body else None)


class _HttpHarness(unittest.TestCase):
    """A real server on a real loopback port, over fake-free routing.

    No providers are bound: these tests are about the boundary in front of the
    fleet, not about a device. A surface with no route still has to refuse
    correctly, and that is the more interesting case anyway.
    """

    OWNER_TOKEN = "owner-pat-value"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.pat = tmp / "owner-pat"
        cls.pat.write_text(cls.OWNER_TOKEN + "\n", encoding="utf-8")
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={},
            auth=AuthBinding(pat_anchor=cls.pat, access_token_ttl=3600, refresh_token_ttl=3600),
            server=ServerBinding(bind="127.0.0.1", port=cls.port, public_base_url=cls.base),
            policy=PolicyBinding(),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        cls.service = FleetService(cls.config, providers={})
        cls.oauth = build_oauth(cls.config, base_url=cls.base, store_root=tmp / "oauth")
        surface = HttpSurface(service=cls.service, config=cls.config, oauth=cls.oauth,
                              audit=cls.service.audit)
        cls.server = serve(surface, bind="127.0.0.1", port=cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def register_client(self, redirect: str = CHATGPT_REDIRECT, name: str = "ChatGPT") -> str:
        status, _, body = self.client.request(
            "POST", "/oauth/register",
            body=json.dumps({"client_name": name, "redirect_uris": [redirect]}).encode(),
        )
        self.assertEqual(status, 201, body)
        return json.loads(body)["client_id"]


class HttpTransport(_HttpHarness):
    """The endpoint a connector reaches. Everything off-host arrives here."""

    # -- the shape of the boundary ----------------------------------------

    def test_an_unauthenticated_call_is_refused_with_a_pointer_to_the_issuer(self):
        """401 alone is not enough: the challenge is what starts the OAuth flow."""
        status, headers, _ = self.client.request(
            "POST", "/mcp", body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
        )
        self.assertEqual(status, 401)
        challenge = headers.get("WWW-Authenticate", "")
        self.assertIn("resource_metadata=", challenge)
        self.assertIn("/.well-known/oauth-protected-resource", challenge)

    def test_a_garbage_token_is_refused(self):
        status, _, _ = self.client.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token="not-a-real-token"
        )
        self.assertEqual(status, 401)

    def test_health_is_liveness_and_carries_no_fleet_facts(self):
        status, _, body = self.client.request("GET", "/health")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["status"], "ok")
        for leak in ("devices", "providers", "sessions", "send_allow"):
            self.assertNotIn(leak, payload)

    def test_a_get_on_the_mcp_path_does_not_open_a_stream(self):
        status, _, _ = self.client.request("GET", "/mcp")
        self.assertEqual(status, 405)

    def test_a_delete_is_answered_even_though_nothing_is_held(self):
        status, _, _ = self.client.request("DELETE", "/mcp")
        self.assertEqual(status, 204)

    def test_an_oversized_body_is_refused(self):
        status, _, body = self.client.request(
            "POST", "/mcp", body=b"x" * (self.config.server.max_body_bytes + 10),
            headers={"Authorization": f"Bearer {self.OWNER_TOKEN}"},
        )
        self.assertEqual(status, 413)
        self.assertIn("exceeds", json.loads(body)["error_description"])

    def test_there_is_no_proxy_route_and_no_url_parameter(self):
        """The SSRF shape, asserted as an absence: there is nothing to point."""
        status, _, _ = self.client.request("GET", "/proxy")
        self.assertEqual(status, 404)
        self.assertEqual(self.client.request("GET", "/fetch")[0], 404)

    # -- the owner token ---------------------------------------------------

    def test_the_owner_token_drives_the_whole_surface(self):
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"}}},
            token=self.OWNER_TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["serverInfo"]["name"], "serveros-fleet-mcp")
        self.assertEqual(payload["result"]["protocolVersion"], "2025-06-18")

    def test_an_unsupported_protocol_version_is_negotiated_not_echoed(self):
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01"}},
            token=self.OWNER_TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["protocolVersion"], "2025-06-18")

    def test_tools_list_returns_the_served_tools_with_their_annotations(self):
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=self.OWNER_TOKEN
        )
        self.assertEqual(status, 200)
        tools_out = payload["result"]["tools"]
        # The served surface is the hand-written Fleet tools PLUS the 22
        # inherited CloudCLI verbs — one MCP surface, discoverable whole.
        self.assertEqual(len(tools_out), 31)
        names = {tool["name"] for tool in tools_out}
        self.assertIn("fleet_list_devices", names)
        writes = [t["name"] for t in tools_out if not t["annotations"]["readOnlyHint"]]
        # The hand-written Fleet writes are unchanged...
        fleet_writes = [n for n in writes if n.startswith("fleet_")
                        and not n.startswith("fleet_cloudcli_")]
        self.assertEqual(fleet_writes, ["fleet_send_to_session", "fleet_create_session",
                                        "fleet_interrupt_session"])
        # ...and the inherited CloudCLI mirror contributes its own ten writes,
        # carry CloudCLI's annotations rather than this surface's opinion.
        self.assertEqual(
            len([n for n in writes if n.startswith("fleet_cloudcli_")]), 10)

    def test_a_notification_gets_no_body_and_not_an_error(self):
        status, _, body = self.client.request(
            "POST", "/mcp",
            body=b'{"jsonrpc":"2.0","method":"notifications/initialized"}',
            headers={"Authorization": f"Bearer {self.OWNER_TOKEN}"},
        )
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_a_malformed_body_is_a_parse_error_not_a_crash(self):
        status, _, body = self.client.request(
            "POST", "/mcp", body=b"{not json",
            headers={"Authorization": f"Bearer {self.OWNER_TOKEN}"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], -32700)

    def test_a_fleet_refusal_comes_back_as_a_code_the_caller_can_branch_on(self):
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "fleet_list_sessions", "arguments": {"device": "tokyo"}}},
            token=self.OWNER_TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["isError"])
        self.assertEqual(payload["result"]["structuredContent"]["code"], "UNKNOWN_DEVICE")

    def test_a_read_through_a_device_with_no_route_is_refused_not_empty(self):
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "fleet_read_session",
                        "arguments": {"session_ref": f"{TOKYO}|claude|abc"}}},
            token=self.OWNER_TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["isError"])
        self.assertEqual(payload["result"]["structuredContent"]["code"], "UNKNOWN_DEVICE")

    # -- discovery ---------------------------------------------------------

    def test_protected_resource_metadata_names_the_issuer_and_the_mcp_path(self):
        status, _, body = self.client.request("GET", "/.well-known/oauth-protected-resource")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["resource"], f"{self.base}/mcp")
        self.assertEqual(payload["authorization_servers"], [self.base])

    def test_authorization_server_metadata_declares_pkce_and_public_clients(self):
        status, _, body = self.client.request("GET", "/.well-known/oauth-authorization-server")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(payload["token_endpoint_auth_methods_supported"], ["none"])
        self.assertEqual(sorted(payload["scopes_supported"]),
                         ["fleet:approve", "fleet:control", "fleet:create",
                          "fleet:navigate", "fleet:read", "fleet:send"])

    # -- dynamic client registration --------------------------------------

    def test_a_public_client_with_an_https_redirect_registers(self):
        client_id = self.register_client()
        self.assertTrue(client_id.startswith("fleet-"))

    def test_an_insecure_redirect_is_refused(self):
        """Plain http off loopback would send a code in cleartext."""
        status, _, body = self.client.request(
            "POST", "/oauth/register",
            body=json.dumps({"client_name": "c",
                             "redirect_uris": ["http://evil.example/steal"]}).encode(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_redirect_uri")

    def test_dynamic_registration_is_open_and_the_operator_sees_where_it_points(self):
        """DCR has no host allow-list, by design, so consent must be informed.

        An open registration endpoint is what RFC 7591 describes, and a
        host allow-list would break the connector it exists to serve. The
        control that replaces it is the consent form naming the redirect target
        the client registered, so approving is a decision about a visible
        destination rather than about a name.
        """
        client_id = self.register_client("https://some.other.host/cb", name="other")
        self.assertTrue(client_id.startswith("fleet-"))
        params = {
            "response_type": "code", "client_id": client_id,
            "redirect_uri": "https://some.other.host/cb", "scope": "fleet:read",
            "state": "s", "code_challenge": _b64(b"0" * 32), "code_challenge_method": "S256",
        }
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 200)
        self.assertIn("some.other.host", body.decode())

    def test_a_plain_http_redirect_off_loopback_is_refused(self):
        with self.assertRaises(OAuthError):
            _validate_redirect_uri("http://evil.example/cb")
        _validate_redirect_uri("http://127.0.0.1:8765/cb")
        _validate_redirect_uri(CHATGPT_REDIRECT)

    def test_a_confidential_client_is_refused(self):
        """A secret handed to a browser is not a secret."""
        status, _, body = self.client.request(
            "POST", "/oauth/register",
            body=json.dumps({"client_name": "c", "redirect_uris": [CHATGPT_REDIRECT],
                             "token_endpoint_auth_method": "client_secret_basic"}).encode(),
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_client_metadata")


class OAuthFlow(_HttpHarness):
    """The authorization-code flow end to end, including what must not work."""

    VERIFIER = "v" * 64

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.challenge = _b64(hashlib.sha256(cls.VERIFIER.encode()).digest())
        status, _, body = cls.client.request(
            "POST", "/oauth/register",
            body=json.dumps({"client_name": "ChatGPT",
                             "redirect_uris": [CHATGPT_REDIRECT]}).encode(),
        )
        assert status == 201, body
        cls.client_id = json.loads(body)["client_id"]

    def _params(self, scope: str = "fleet:read fleet:send",
                redirect: str = CHATGPT_REDIRECT) -> dict[str, str]:
        return {
            "response_type": "code",
            "client_id": type(self).client_id,
            "redirect_uri": redirect,
            "scope": scope,
            "state": "state-xyz",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }

    def _approve(self, *, scope: str = "fleet:read fleet:send",
                 granted: list[str] | None = None,
                 owner_token: str = _HttpHarness.OWNER_TOKEN):
        params = self._params(scope)
        form = dict(params)
        form["owner_token"] = owner_token
        form["scope"] = granted if granted is not None else scope.split()
        return self.client.request("POST", "/oauth/authorize", form=form)

    def _token_for(self, code: str, *, verifier: str | None = None):
        return self.client.request("POST", "/oauth/token", form={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": type(self).client_id,
            "redirect_uri": CHATGPT_REDIRECT,
            "code_verifier": verifier or self.VERIFIER,
        })

    def _grant(self, *, scope: str = "fleet:read fleet:send",
               granted: list[str] | None = None) -> dict:
        status, headers, _ = self._approve(scope=scope, granted=granted)
        self.assertEqual(status, 302, f"approval failed with {status}")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
        status, _, body = self._token_for(query["code"][0])
        self.assertEqual(status, 200, body)
        return json.loads(body)

    # -- consent -----------------------------------------------------------

    def test_the_consent_form_is_shown_and_asks_for_the_owner_token(self):
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(self._params())
        )
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("Authorize", text)
        self.assertIn('name="owner_token"', text)
        self.assertIn("fleet:send", text)

    def test_the_consent_page_loads_nothing_from_anywhere(self):
        _, headers, _ = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(self._params())
        )
        policy = headers.get("Content-Security-Policy", "")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("form-action 'self'", policy)

    def test_an_unknown_client_gets_an_error_page_not_a_redirect(self):
        params = dict(self._params(), client_id="fleet-does-not-exist")
        status, headers, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 401)
        self.assertNotIn("Location", headers)
        self.assertIn("invalid_client", body.decode())

    def test_an_unregistered_redirect_goes_nowhere(self):
        params = self._params(redirect="https://evil.example/steal")
        status, headers, _ = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 400)
        self.assertNotIn("Location", headers)

    def test_pkce_is_required(self):
        params = self._params()
        del params["code_challenge"]
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 400)
        self.assertIn("PKCE", body.decode())

    def test_a_scope_outside_the_deployments_grant_is_refused(self):
        params = self._params(scope="fleet:read fleet:admin")
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid_scope", body.decode())

    def test_a_wrong_owner_token_issues_nothing(self):
        status, headers, _ = self._approve(owner_token="wrong")
        self.assertEqual(status, 401)
        self.assertNotIn("Location", headers)

    def test_the_approval_returns_the_state_it_was_given(self):
        status, headers, _ = self._approve()
        self.assertEqual(status, 302)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)
        self.assertEqual(query["state"], ["state-xyz"])

    # -- tokens ------------------------------------------------------------

    def test_the_code_buys_a_token_with_the_approved_scope(self):
        payload = self._grant()
        self.assertEqual(payload["token_type"], "Bearer")
        self.assertEqual(payload["scope"], "fleet:read fleet:send")
        self.assertIn("refresh_token", payload)

    def test_the_token_actually_drives_the_mcp_endpoint(self):
        payload = self._grant(scope="fleet:read", granted=["fleet:read"])
        status, _, body = self.client.rpc(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/list"},
            token=payload["access_token"],
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body["result"]["tools"]), 31)  # 9 fleet + 22 inherited

    def test_a_code_is_single_use(self):
        status, headers, _ = self._approve()
        code = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        self.assertEqual(self._token_for(code)[0], 200)
        status, _, body = self._token_for(code)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_grant")

    def test_a_code_cannot_be_redeemed_without_the_verifier(self):
        status, headers, _ = self._approve()
        code = urllib.parse.parse_qs(
            urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        status, _, body = self._token_for(code, verifier="w" * 64)
        self.assertEqual(status, 400)
        self.assertIn("PKCE", json.loads(body)["error_description"])

    def test_a_read_only_grant_cannot_send(self):
        """The scope the operator ticked is the scope the token carries."""
        payload = self._grant(scope="fleet:read", granted=["fleet:read"])
        self.assertEqual(payload["scope"], "fleet:read")
        status, _, body = self.client.rpc(
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
             "params": {"name": "fleet_send_to_session",
                        "arguments": {"session_ref": f"{TOKYO}|claude|abc",
                                      "message": "hello"}}},
            token=payload["access_token"],
        )
        self.assertEqual(status, 200)
        result = body["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")

    def test_a_token_with_the_wrong_audience_is_refused(self):
        """A token this server signed for another resource is not a token here."""
        foreign, _ = self.oauth.store.mint_access_token(
            client_id="c", subject="operator", scopes=("fleet:read",),
            audience="https://other.example",
        )
        with self.assertRaises(OAuthError) as caught:
            self.oauth.store.verify_access_token(foreign, audience=self.base)
        self.assertIn("audience", str(caught.exception))

    def test_a_token_signed_by_another_key_is_refused(self):
        elsewhere = TokenStore(Path(self._tmp.name) / "elsewhere", access_ttl=600,
                               refresh_ttl=600)
        forged, _ = elsewhere.mint_access_token(
            client_id="c", subject="operator", scopes=("fleet:read",),
            audience=self.base,
        )
        with self.assertRaises(OAuthError) as caught:
            self.oauth.store.verify_access_token(forged, audience=self.base)
        self.assertIn("signature", str(caught.exception))

    def test_a_tampered_access_token_does_not_verify(self):
        token = self._grant()["access_token"]
        header, payload, signature = token.split(".")
        flipped = signature[:-2] + ("aa" if signature[-2:] != "aa" else "bb")
        with self.assertRaises(OAuthError):
            self.oauth.store.verify_access_token(f"{header}.{payload}.{flipped}",
                                                 audience=self.base)

    # -- refresh -----------------------------------------------------------

    def test_a_refresh_rotates_and_the_old_one_dies(self):
        first = self._grant()["refresh_token"]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": first,
            "client_id": type(self).client_id,
        })
        self.assertEqual(status, 200, body)
        rotated = json.loads(body)["refresh_token"]
        self.assertNotEqual(rotated, first)
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": first,
            "client_id": type(self).client_id,
        })
        self.assertEqual(status, 400)
        self.assertIn("already used", json.loads(body)["error_description"])

    def test_reusing_a_rotated_refresh_kills_the_whole_chain(self):
        """The only safe reading of a reused rotated token is that it leaked."""
        first = self._grant()["refresh_token"]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": first,
            "client_id": type(self).client_id,
        })
        second = json.loads(body)["refresh_token"]
        self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": first,
            "client_id": type(self).client_id,
        })
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": second,
            "client_id": type(self).client_id,
        })
        self.assertEqual(status, 400)
        self.assertIn("revoked", json.loads(body)["error_description"])

    def test_a_refresh_cannot_widen_the_scope(self):
        refresh = self._grant(scope="fleet:read", granted=["fleet:read"])["refresh_token"]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": type(self).client_id, "scope": "fleet:read fleet:send",
        })
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "invalid_scope")

    def test_a_refresh_can_narrow_the_scope(self):
        refresh = self._grant()["refresh_token"]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": type(self).client_id, "scope": "fleet:read",
        })
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["scope"], "fleet:read")

    def test_another_client_cannot_spend_a_refresh_token(self):
        other = self.register_client(CHATGPT_REDIRECT, name="someone else")
        refresh = self._grant()["refresh_token"]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": other,
        })
        self.assertEqual(status, 400)
        self.assertIn("another client", json.loads(body)["error_description"])

    # -- what the operator ticked is what is granted -----------------------
    #
    # The regression these cover: the consent form used to round-trip the
    # client's requested scope as a hidden field named `scope`, the same name the
    # checkboxes use. Unticking a box therefore changed nothing — the hidden
    # field re-supplied the scope and it was granted. Every test below is that
    # sentence, kept false.

    def _form_as_a_browser_sends_it(self, *, ticked: list[str], scope: str = "fleet:read fleet:send"):
        """Exactly the fields consent_page renders for a given set of ticks."""
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(self._params(scope))
        )
        self.assertEqual(status, 200, body)
        markup = body.decode()
        hidden = {
            name: value for name, value in re.findall(
                r'<input type="hidden" name="([^"]+)" value="([^"]*)"', markup
            )
        }
        self.assertIn("requested_scope", hidden,
                      "the requested scope must travel in its own field, not as `scope`")
        return {**hidden, "owner_token": self.OWNER_TOKEN, "scope": list(ticked)}

    def _redeem(self, form: dict):
        status, headers, body = self.client.request("POST", "/oauth/authorize", form=form)
        if status != 302 or "code=" not in headers.get("Location", ""):
            return status, headers, None
        code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        status, _, token_body = self._token_for(code)
        return status, headers, (json.loads(token_body) if status == 200 else token_body)

    def test_the_requested_scope_is_not_the_field_the_boxes_use(self):
        """One hidden field per request parameter — and `scope` is not one of them."""
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(self._params())
        )
        markup = body.decode()
        hidden = re.findall(r'<input type="hidden" name="([^"]+)"', markup)
        self.assertNotIn("scope", hidden)
        self.assertEqual(hidden.count("requested_scope"), 1)
        self.assertEqual(
            re.findall(r'name="scope" value="([^"]+)"', markup),
            ["fleet:read", "fleet:send", "fleet:control", "fleet:create",
             "fleet:approve", "fleet:navigate"],
            "the only things named `scope` are the checkboxes",
        )

    def test_unticking_a_scope_does_not_grant_it(self):
        """The whole defect: `fleet:send` requested, not ticked, must not be issued."""
        form = self._form_as_a_browser_sends_it(ticked=["fleet:read"])
        self.assertIn("fleet:send", form["requested_scope"])
        status, _, payload = self._redeem(form)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["scope"], "fleet:read")

    def test_ticking_nothing_issues_no_code_at_all(self):
        form = self._form_as_a_browser_sends_it(ticked=[])
        status, headers, _ = self.client.request("POST", "/oauth/authorize", form=form)
        self.assertEqual(status, 302)
        location = headers["Location"]
        self.assertNotIn("code=", location)
        self.assertIn("access_denied", location)

    # -- revocation: the declared semantics, enforced ----------------------

    def _grant_for(self, client_id: str, *, scope: str = "fleet:read fleet:send",
                   granted: list[str] | None = None) -> dict:
        """A grant for a client this test owns, so revoking it touches nothing else."""
        params = self._params(scope)
        params["client_id"] = client_id
        form = {**params, "owner_token": self.OWNER_TOKEN,
                "scope": granted if granted is not None else scope.split()}
        status, headers, _ = self.client.request("POST", "/oauth/authorize", form=form)
        self.assertEqual(status, 302, headers)
        code = urllib.parse.parse_qs(urllib.parse.urlparse(headers["Location"]).query)["code"][0]
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": CHATGPT_REDIRECT, "code_verifier": self.VERIFIER,
        })
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def _revoke(self, token: str, client_id: str | None = None):
        form = {"token": token}
        if client_id:
            form["client_id"] = client_id
        return self.client.request("POST", "/oauth/revoke", form=form)

    def test_revoking_a_client_kills_its_access_token_immediately(self):
        """Not at expiry — the next call. That is what `revoke` has to mean."""
        client_id = self.register_client(CHATGPT_REDIRECT, name="revocable")
        payload = self._grant_for(client_id)
        access, refresh = payload["access_token"], payload["refresh_token"]
        status, _, _ = self.client.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                       token=access)
        self.assertEqual(status, 200, "the token should work before revocation")

        status, _, body = self._revoke(refresh, client_id)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, b"", "RFC 7009 fixes this response body as empty")

        status, _, _ = self.client.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                       token=access)
        self.assertEqual(status, 401, "the access token outlived the revocation")

        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id,
        })
        self.assertEqual(status, 400, body)

    def test_a_revoked_client_cannot_start_a_new_authorization(self):
        client_id = self.register_client(CHATGPT_REDIRECT, name="revoked then re-authorizing")
        self._grant_for(client_id)
        self.oauth.store.revoke_client(client_id)
        params = self._params()
        params["client_id"] = client_id
        status, headers, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 401)
        self.assertNotIn("Location", headers)
        self.assertIn("revoked", body.decode())

    def test_revoking_one_access_token_leaves_its_client_alone(self):
        client_id = self.register_client(CHATGPT_REDIRECT, name="one token revoked")
        payload = self._grant_for(client_id)
        access = payload["access_token"]
        status, _, body = self._revoke(access, client_id)
        self.assertEqual(status, 200, body)
        status, _, _ = self.client.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                       token=access)
        self.assertEqual(status, 401)
        # The refresh chain is untouched, so the client can still renew.
        status, _, body = self.client.request("POST", "/oauth/token", form={
            "grant_type": "refresh_token", "refresh_token": payload["refresh_token"],
            "client_id": client_id,
        })
        self.assertEqual(status, 200, body)

    def test_revocation_does_not_answer_whether_a_token_exists(self):
        """Same 200, same empty body, token or not. An oracle is a bug here."""
        _, _, unknown = self._revoke("frt-" + "z" * 40)
        status, _, body = self._revoke("frt-" + "z" * 40)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        status, _, body = self._revoke("not-a-token-at-all")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        status, _, body = self.client.request("POST", "/oauth/revoke", form={})
        self.assertEqual(status, 400, "a request with no token is the one real error")

    def test_the_issuer_publishes_where_to_revoke(self):
        _, _, body = self.client.request("GET", "/.well-known/oauth-authorization-server")
        metadata = json.loads(body)
        self.assertEqual(metadata["revocation_endpoint"], f"{self.base}/oauth/revoke")

    # -- resource identity: `resource`, `iss`, `aud` ------------------------

    def test_the_published_resource_is_the_address_the_tokens_are_for(self):
        _, _, body = self.client.request("GET", "/.well-known/oauth-protected-resource")
        self.assertEqual(json.loads(body)["resource"], f"{self.base}/mcp")

    def test_the_access_token_separates_issuer_from_resource(self):
        """`iss` is the authorization server; `aud` is the resource. Not one URL twice."""
        token = self._grant()["access_token"]
        claims = self.oauth.store.signed_claims(token)
        self.assertEqual(claims["iss"], self.base)
        self.assertEqual(claims["aud"], f"{self.base}/mcp")

    def test_a_resource_this_server_does_not_serve_is_refused(self):
        params = dict(self._params(), resource="https://somewhere-else.example/mcp")
        status, _, body = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid_target", body.decode())

    def test_the_client_may_ask_for_the_real_resource(self):
        params = dict(self._params(), resource=f"{self.base}/mcp")
        status, _, _ = self.client.request(
            "GET", "/oauth/authorize?" + urllib.parse.urlencode(params)
        )
        self.assertEqual(status, 200)

    def test_a_token_for_another_resource_cannot_call_the_endpoint(self):
        foreign, _ = self.oauth.store.mint_access_token(
            client_id="c", subject="operator", scopes=("fleet:read",),
            audience="https://other.example/mcp", issuer=self.base,
        )
        status, headers, _ = self.client.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                             token=foreign)
        self.assertEqual(status, 401)
        self.assertIn("resource_metadata", headers.get("WWW-Authenticate", ""))


class UrlShapes(unittest.TestCase):
    """What gets advertised, and where it routes. No socket; routing only."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _surface(self, *, public_base_url, mcp_path="/mcp"):
        pat = self.tmp / "owner-pat"
        pat.write_text(self.OWNER_TOKEN, encoding="utf-8")
        config = FleetConfig(
            source=None, local_device_ref=TOKYO, providers={},
            auth=AuthBinding(pat_anchor=pat),
            server=ServerBinding(bind="127.0.0.1", port=3401, public_base_url=public_base_url,
                                 mcp_path=mcp_path),
            policy=PolicyBinding(),
            audit=AuditBinding(path=self.tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        service = FleetService(config, providers={})
        surface = HttpSurface(service=service, config=config, oauth=None, audit=service.audit)
        return config, surface

    OWNER_TOKEN = "owner-pat-value"

    def test_a_non_https_public_url_is_refused_at_build_time(self):
        """It becomes the issuer identity and the token audience, so http is not ok."""
        config, _ = self._surface(public_base_url="http://fleet.example")
        with self.assertRaises(FleetRefusal) as caught:
            build_oauth(config, base_url="http://fleet.example", store_root=self.tmp / "o")
        self.assertIn("public_base_url", caught.exception.detail)

    def test_http_on_loopback_is_still_allowed(self):
        """The local case: no certificate, and the credential never leaves the host."""
        config, _ = self._surface(public_base_url="http://127.0.0.1:3401")
        oauth = build_oauth(config, base_url="http://127.0.0.1:3401", store_root=self.tmp / "o")
        self.assertEqual(oauth.resource_url, "http://127.0.0.1:3401/mcp")

    def test_a_path_in_the_public_url_routes_both_ways(self):
        """A prefix does not break loopback probes, and does not need stripping."""
        _, surface = self._surface(public_base_url="https://fleet.example/fleet-mcp")
        for path in ("/health", "/fleet-mcp/health"):
            status, _, body = surface.handle("GET", path, "", {}, b"")
            self.assertEqual(status, 200, f"{path} -> {body!r}")
            self.assertEqual(json.loads(body)["status"], "ok")
        # The MCP endpoint itself, under the prefix and without it.
        for path in ("/mcp", "/fleet-mcp/mcp"):
            status, _, _ = surface.handle("POST", path, "", {}, b"{}")
            self.assertEqual(status, 401, f"{path} should be an authentication failure")

    def test_the_prefix_does_not_leak_into_the_route_table(self):
        _, surface = self._surface(public_base_url="https://fleet.example/fleet-mcp")
        status, _, body = surface.handle("GET", "/fleet-mcp/nope", "", {}, b"")
        self.assertEqual(status, 404)


class OperatorClientCommands(unittest.TestCase):
    """`--list-clients` / `--revoke-client`.

    The operator's way to stop a client entirely offline: no HTTP endpoint whose
    safety would depend on it being authenticated, and no dependency on the
    server that issued the client still being up.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        pat = self.tmp / "owner-pat"
        pat.write_text("owner-pat-value", encoding="utf-8")
        self.config = FleetConfig(
            source=None, local_device_ref=TOKYO, providers={},
            auth=AuthBinding(pat_anchor=pat),
            server=ServerBinding(bind="127.0.0.1", port=3401),
            policy=PolicyBinding(),
            audit=AuditBinding(path=self.tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _stdout(self, **kwargs) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = clients_command(self.config, **kwargs)
        return code, buffer.getvalue()

    def test_there_is_nothing_to_show_before_anything_is_registered(self):
        code, _ = self._stdout(revoke=None, as_json=False)
        self.assertEqual(code, 4, "no state file is not the same as no clients")

    def test_a_client_can_be_listed_then_revoked_without_a_server(self):
        store = TokenStore(self.tmp / "fleet-mcp-oauth", access_ttl=3600, refresh_ttl=3600)
        client = store.register_client(client_name="ChatGPT", redirect_uris=[CHATGPT_REDIRECT])
        access, _ = store.mint_access_token(
            client_id=client.client_id, subject="operator", scopes=("fleet:read",),
            audience="https://fleet.example/mcp", issuer="https://fleet.example",
        )
        code, text = self._stdout(revoke=None, as_json=True)
        self.assertEqual(code, 0)
        listed = json.loads(text)["clients"]
        self.assertEqual([row["client_id"] for row in listed], [client.client_id])
        self.assertFalse(listed[0]["revoked"])

        code, text = self._stdout(revoke=client.client_id, as_json=False)
        self.assertEqual(code, 0, text)
        self.assertEqual(json.loads(text)["client_id"], client.client_id)

        with self.assertRaises(OAuthError) as caught:
            store.verify_access_token(access, audience="https://fleet.example/mcp")
        self.assertIn("revoked", str(caught.exception))

        code, text = self._stdout(revoke=None, as_json=True)
        self.assertTrue(json.loads(text)["clients"][0]["revoked"])

    def test_revoking_an_unknown_client_refuses_rather_than_guessing(self):
        store = TokenStore(self.tmp / "fleet-mcp-oauth", access_ttl=3600, refresh_ttl=3600)
        store.register_client(client_name="ChatGPT", redirect_uris=[CHATGPT_REDIRECT])
        code, _ = self._stdout(revoke="fleet-nope", as_json=False)
        self.assertEqual(code, 5)


class RequestLinesDoNotCarryQueries(unittest.TestCase):
    """D5: the access log must not become a copy of a code or a verifier."""

    def test_a_query_string_never_reaches_the_log_line(self):
        line = ('"GET /oauth/authorize?response_type=code&state=state-xyz'
                '&code_challenge=abc123&resource=https%3A%2F%2Ffleet.example%2Fmcp HTTP/1.1" 400 -')
        written = _without_query(line)
        for secret in ("state-xyz", "abc123", "fleet.example"):
            self.assertNotIn(secret, written)
        self.assertIn("[redacted]", written)
        # The parts an operator reads keep their shape.
        self.assertIn("GET /oauth/authorize?", written)
        self.assertIn("HTTP/1.1", written)
        self.assertTrue(written.endswith('400 -'))

    def test_a_line_with_no_query_is_unchanged(self):
        line = '"POST /mcp HTTP/1.1" 200 -'
        self.assertEqual(_without_query(line), line)

    def test_the_handler_uses_it(self):
        self.assertIsNotNone(getattr(_Handler, "log_message", None))
        self.assertIn("_without_query", inspect.getsource(_Handler.log_message))


class KeepAliveIsNotStalled(unittest.TestCase):
    """The reused connection must not be the slow one.

    Measured 2026-10-10 against the installed tree: a kept-alive connection cost
    p50 53 ms per call while a fresh one cost 12 ms, because `_respond` sends the
    headers and the body as two separate unbuffered writes and Nagle holds the
    second one for a delayed ACK. Turning Nagle off took it to p50 12 ms.

    The flag is invisible in any offline test and its absence is invisible in any
    test that opens a connection per request — which is exactly how it survived
    until it was measured on the hot path. Pin it here.
    """

    def test_the_handler_turns_nagle_off(self):
        self.assertTrue(
            getattr(_Handler, "disable_nagle_algorithm", False),
            "without TCP_NODELAY a kept-alive client pays a delayed-ACK stall "
            "on every call after the first",
        )

    def test_the_connection_is_meant_to_be_reused(self):
        # The stall only exists because the connection stays open; if this ever
        # becomes HTTP/1.0 the two writes stop being on one socket and the
        # comment above stops describing the program.
        self.assertEqual(_Handler.protocol_version, "HTTP/1.1")


if __name__ == "__main__":
    unittest.main()
