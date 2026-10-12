"""The Windows-side node connector, tested as the security boundary it is.

These tests are not about whether the connector can fetch a session. They are
about what it refuses, and the refusals are checked in the order they have to
happen: a caller with no secret never reaches a route, a caller that names the
wrong device never consumes a nonce, and a send that this machine has not
authorized fails here even when every layer above it said yes.

The fake provider stands in for CloudCLI so that a refusal about *this* machine
cannot be confused with a refusal about the gateway's.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fleet_mcp.errors import FleetRefusal, refuse
from fleet_mcp.identity import SessionKey
from fleet_mcp.node import connector as connector_module
from fleet_mcp.node.connector import Connector, NodeConfig, load_node_config, serve
from fleet_mcp import node_protocol as np
from fleet_mcp.providers.base import (
    MANAGED_IDLE,
    ProviderHealth,
    SessionSummary,
)

DEVICE = "device:work-fj"
SECRET = "node-secret-do-not-commit"
ALLOWED = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def _health(**over) -> ProviderHealth:
    base = dict(
        device_ref=DEVICE,
        kind="cloudcli-mcp-http",
        reachable=True,
        authorized=True,
        detail="gateway answered; 22 tools declared",
        server_name="claudecodeui-mcp-gateway",
        server_version="0.1.0",
        tool_count=22,
        latency_ms=12.5,
    )
    base.update(over)
    return ProviderHealth(**base)


class FakeProvider:
    """A CloudCLI that answers, and records what it was asked."""

    def __init__(self, *, fail: FleetRefusal | None = None) -> None:
        self.calls: list[tuple] = []
        self.sent: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.fail = fail

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    def health(self) -> ProviderHealth:
        self.calls.append(("health",))
        if self.fail is not None:
            return _health(reachable=False, authorized=True, detail=self.fail.detail,
                           error_code=self.fail.code, server_name=None,
                           server_version=None, tool_count=None)
        return _health()

    def list_sessions(self, *, limit, project=None, state=None):
        self.calls.append(("list_sessions", limit, project, state))
        self._maybe_fail()
        key = SessionKey.compose(DEVICE, "claude", ALLOWED)
        return [
            SessionSummary(
                key=key, title="warm host", classification=MANAGED_IDLE,
                running=False, resident_host=False, host_state=None,
                lifecycle_mode="default", project_id="p-1", last_activity=None,
            )
        ]

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        self._maybe_fail()
        return {"session_ref": key.ref, "title": "warm host"}

    def read_session(self, key, **kwargs):
        self.calls.append(("read_session", key.ref, kwargs))
        self._maybe_fail()
        return {"session_ref": key.ref, "mode": kwargs.get("mode"), "messages": []}

    def send(self, key, message, *, idempotency_key=None):
        self.calls.append(("send", key.ref, message))
        self._maybe_fail()
        self.sent.append((key.native_session_id, message))
        return {"run_id": "run-7", "session_ref": key.ref, "queued": False}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        self.calls.append(("get_run", key.ref, run_id, wait))
        self._maybe_fail()
        return {"run_id": run_id, "status": "succeeded"}

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        self.calls.append(("create", project, message))
        self._maybe_fail()
        self.created.append({
            "project": project, "message": message, "provider": provider,
            "model": model, "lifecycle_mode": lifecycle_mode,
            "permission_mode": permission_mode,
        })
        return {"sessionId": "created-1", "runId": "run-7" if message else None}


class _Base(unittest.TestCase):
    allow_send_to: tuple[str, ...] = ()
    allow_create: bool = False

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "node.secret").write_text(SECRET, encoding="utf-8")
        (root / "cloudcli.token").write_text("ccp_not-a-real-token", encoding="utf-8")
        cls.config = NodeConfig(
            device_ref=DEVICE,
            secret_anchor=root / "node.secret",
            cloudcli_endpoint="http://127.0.0.1:3001/mcp",
            cloudcli_credential_anchor=root / "cloudcli.token",
            port=0,
            allow_send_to=tuple(cls.allow_send_to),
            allow_create=cls.allow_create,
        )
        cls.connector = Connector(cls.config)
        cls.provider = FakeProvider()
        cls.connector.provider = cls.provider
        cls.server = serve(cls.config, connector=cls.connector)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.provider.calls.clear()
        self.provider.sent.clear()
        self.provider.fail = None
        self.connector.replay = np.ReplayCache()

    # -- request helper ----------------------------------------------------

    def request(self, method: str, path: str, body=None, *, secret=SECRET,
                device=DEVICE, headers=None, timestamp=None, nonce=None, sign_path=None):
        """Send a signed request and return (status, payload).

        The signature is always computed over `sign_path or path`, matching what
        `FleetNodeProvider._request` does: the path, never the query.
        """
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        # The signature covers the path and never the query — the same rule
        # `FleetNodeProvider._request` follows. Signing the raw target here
        # would make every filtered call fail in a way that looks like a
        # secret problem.
        target = np.split_path_and_query(path)[0] if sign_path is None else sign_path
        signed = np.sign(
            secret, method=method, path=target,
            body=raw, device_ref=device, timestamp=timestamp, nonce=nonce,
        )
        sent = dict(signed)
        if headers:
            sent.update(headers)
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=raw or None,
            headers=sent, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8", "replace")
            return exc.code, json.loads(raw_body or "{}")

    def route(self, method: str, target: str, body=b"", headers=None):
        """In-process call, for the cases that need no socket."""
        raw = b"" if body is None else body
        sent = dict(headers or {})
        status, payload = self.connector.route(method, target, raw, sent)
        return status, payload


# ---------------------------------------------------------------------------
# What the connector will not reach
# ---------------------------------------------------------------------------


class RouteClosure(_Base):
    # One allow-listed session, so that "is this route reachable" is answered
    # without the send floor confounding it — that floor has its own class.
    allow_send_to = (ALLOWED,)
    # Creation is a separate grant and is on for this class so that the route
    # table's completeness is checked without the create floor confounding it.
    allow_create = True

    def test_every_declared_route_is_actually_reachable(self):
        """The route table is a promise; this checks the promise is kept."""
        cases = [
            ("GET", "/v1/health", None),
            ("GET", "/v1/self", None),
            ("GET", "/v1/sessions", None),
            ("GET", f"/v1/sessions/{ALLOWED}", None),
            ("POST", f"/v1/sessions/{ALLOWED}/read", {"mode": "latest"}),
            ("POST", f"/v1/sessions/{ALLOWED}/send", {"message": "hello"}),
            ("GET", f"/v1/runs/run-7?session={ALLOWED}", None),
            ("POST", "/v1/sessions", {"project": "p-1"}),
        ]
        self.assertEqual(len(cases), len(np.ROUTES))
        for method, path, body in cases:
            with self.subTest(route=f"{method} {path}"):
                status, payload = self.request(method, path, body)
                self.assertEqual(status, 200, payload)

    def test_an_undeclared_route_is_refused_by_name(self):
        for method, path in (
            ("POST", "/v1/exec"),
            ("POST", "/v1/shell"),
            ("GET", "/v1/files"),
            ("GET", "/v1/sessions/x/shell"),
            ("GET", "/v1/sessions/x/read"),          # read is a POST
            ("POST", "/v1/sessions/x/start"),        # start is not built
            ("GET", "/v1/sessions/x/read/extra"),
            ("GET", "/v1/runs"),
            ("GET", "/health"),
            ("GET", "/v1/diagnostics"),
        ):
            with self.subTest(route=f"{method} {path}"):
                status, payload = self.request(method, path, {"message": "x"})
                self.assertEqual(status, 404, payload)
                self.assertEqual(payload["error"]["code"], "UNKNOWN_ROUTE")
                # The refusal names the whole surface, so an operator does not
                # have to guess what else might be behind this port.
                self.assertIn("GET /v1/health", payload["error"]["detail"])

    def test_the_route_table_carries_no_parameter_that_could_name_a_resource(self):
        """No route takes a path, a command, a url or a tool name.

        A `{path}` or `{command}` placeholder would turn this connector into a
        general-purpose proxy to the workstation, which is the one thing it must
        not be.
        """
        forbidden = ("path", "file", "command", "cmd", "shell", "url", "uri",
                     "endpoint", "host", "tool", "argv", "script")
        for route in np.ROUTES:
            _, _, path = route.partition(" ")
            for segment in path.split("/"):
                if not segment.startswith("{"):
                    continue
                name = segment.strip("{}").lower()
                with self.subTest(route=route, placeholder=name):
                    self.assertNotIn(name, forbidden)
        # And the placeholders that do exist name only ids.
        self.assertEqual(
            sorted(s.strip("{}") for r in np.ROUTES for s in r.partition(" ")[2].split("/") if s.startswith("{")),
            ["id", "id", "id", "run_id"],
        )


# ---------------------------------------------------------------------------
# Authentication, in the order it has to happen
# ---------------------------------------------------------------------------


class SignatureGate(_Base):
    def test_an_unsigned_request_is_refused(self):
        status, payload = self.route("GET", "/v1/health")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "PROTOCOL_MISMATCH")

    def test_a_request_signed_with_the_wrong_secret_is_refused(self):
        status, payload = self.request("GET", "/v1/health", secret="not-the-secret")
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "BAD_SIGNATURE")
        self.assertEqual(self.provider.calls, [])

    def test_a_body_swapped_after_signing_is_refused(self):
        raw = json.dumps({"message": "harmless"}).encode("utf-8")
        headers = np.sign(SECRET, method="POST",
                          path=f"/v1/sessions/{ALLOWED}/send", body=raw, device_ref=DEVICE)
        status, payload = self.route(
            "POST", f"/v1/sessions/{ALLOWED}/send",
            json.dumps({"message": "and now something else"}).encode("utf-8"), headers,
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "BAD_SIGNATURE")
        self.assertEqual(self.provider.sent, [])

    def test_a_path_swapped_after_signing_is_refused(self):
        headers = np.sign(SECRET, method="GET", path=f"/v1/sessions/{ALLOWED}",
                          body=b"", device_ref=DEVICE)
        status, payload = self.route("GET", f"/v1/sessions/{OTHER}", body=b"", headers=headers)
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "BAD_SIGNATURE")

    def test_a_fresh_capture_replayed_is_refused(self):
        raw = b""
        headers = np.sign(SECRET, method="GET", path="/v1/sessions",
                          body=raw, device_ref=DEVICE)
        first = self.route("GET", "/v1/sessions", raw, headers)
        second = self.route("GET", "/v1/sessions", raw, headers)
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 400)
        self.assertEqual(second[1]["error"]["code"], "REPLAY")

    def test_a_stale_capture_is_refused(self):
        status, payload = self.request("GET", "/v1/health", timestamp="1")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "STALE_REQUEST")

    def test_a_signature_for_another_device_is_refused(self):
        status, payload = self.request("GET", "/v1/health", device="device:personal-dell")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "DEVICE_MISMATCH")

    def test_a_wrong_device_does_not_consume_a_nonce(self):
        """Order matters: identity is checked before the replay cache is touched.

        If it were the other way round, one misdirected request would burn a
        nonce and the correctly-addressed retry would look like a replay.
        """
        nonce = "0123456789abcdef0123456789abcdef"
        wrong = self.request("GET", "/v1/health", device="device:personal-dell", nonce=nonce)
        self.assertEqual(wrong[1]["error"]["code"], "DEVICE_MISMATCH")
        right = self.request("GET", "/v1/health", nonce=nonce)
        self.assertEqual(right[0], 200, right[1])

    def test_the_protocol_version_is_pinned(self):
        status, payload = self.request(
            "GET", "/v1/health", headers={"X-Fleet-Protocol": "serveros/fleet-node/v2"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"]["code"], "PROTOCOL_MISMATCH")

    def test_the_query_string_is_not_part_of_the_signature(self):
        """Reordered filters are the same request, and must verify as one.

        `sign_path` is the path alone — which is exactly what the Fleet-side
        provider signs — so this asserts both halves of the contract at once.
        """
        status, payload = self.request(
            "GET", "/v1/sessions?state=idle&limit=5", sign_path="/v1/sessions"
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.provider.calls[-1][1:], (5, None, "idle"))


# ---------------------------------------------------------------------------
# This machine's own control decision
# ---------------------------------------------------------------------------


class SendFloorIsOnTheDevice(_Base):
    """With an empty allow list, every send is refused here.

    Not "refused by the Fleet surface" — refused by the machine the sessions
    live on, which is the only place whose answer survives the Fleet surface
    being reconfigured, replaced or wrong.
    """

    allow_send_to: tuple[str, ...] = ()

    def test_every_send_is_refused_and_nothing_reaches_cloudcli(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/send", {"message": "hello"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.sent, [])
        self.assertEqual([c for c in self.provider.calls if c[0] == "send"], [])

    def test_the_refusal_says_where_the_decision_lives(self):
        _, payload = self.request("POST", f"/v1/sessions/{ALLOWED}/send", {"message": "hi"})
        detail = payload["error"]["detail"]
        self.assertIn("allow_send_to", detail)
        self.assertIn("this machine", detail)

    def test_reads_are_unaffected_by_the_send_allow_list(self):
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["sessions"]), 1)

    def test_self_reports_the_control_decision_rather_than_a_blank(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control"]["send_allow_list"], [])
        self.assertEqual(payload["control"]["send_default"], "refused")
        self.assertTrue(payload["credential_stays_here"])
        self.assertEqual(payload["device_ref"], DEVICE)


class SendWithAnAllowList(_Base):
    allow_send_to = (ALLOWED,)

    def test_an_allow_listed_session_is_sent_to_and_returns_a_run(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/send", {"message": "status?"}
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["run_id"], "run-7")
        self.assertEqual(self.provider.sent, [(ALLOWED, "status?")])

    def test_a_session_outside_the_allow_list_is_still_refused(self):
        """The list is a list, not a switch."""
        status, payload = self.request(
            "POST", f"/v1/sessions/{OTHER}/send", {"message": "hello"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.sent, [])

    def test_a_full_ref_is_accepted_in_the_allow_list_too(self):
        """`SessionKey.compose` produces `device:work-fj|claude|<id>` either way;
        an operator who wrote the whole ref should not be silently ignored."""
        ref = f"{DEVICE}|claude|{ALLOWED}"
        self.assertTrue(self.config.permits_send(SessionKey.parse(ref)))
        self.assertTrue(
            Connector(NodeConfig(
                device_ref=DEVICE, secret_anchor=self.config.secret_anchor,
                cloudcli_endpoint=self.config.cloudcli_endpoint,
                cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
                allow_send_to=(ref,),
            )).config.permits_send(SessionKey.parse(ref))
        )

    def test_an_empty_message_is_refused_before_the_allow_list_is_consulted(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/send", {"message": "   "}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")

    def test_a_missing_message_is_refused(self):
        status, payload = self.request("POST", f"/v1/sessions/{ALLOWED}/send", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")


# ---------------------------------------------------------------------------
# This machine's own creation decision
# ---------------------------------------------------------------------------


class CreateFloorIsOnTheDevice(_Base):
    """Creation is a second, separate grant, and the default is refusal.

    Sending to a session and minting one are different acts, so a machine that
    authorized the first is not thereby a machine that authorized the second.
    The refusal names the file it lives in, exactly as the send floor does, for
    the same reason: the decision belongs to the machine whose sessions would be
    created.
    """

    allow_create = False

    def test_every_create_is_refused_and_nothing_reaches_cloudcli(self):
        status, payload = self.request("POST", "/v1/sessions", {"project": "p-1"})
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.created, [])
        self.assertEqual([c for c in self.provider.calls if c[0] == "create"], [])

    def test_the_refusal_says_where_the_decision_lives(self):
        _, payload = self.request("POST", "/v1/sessions", {"project": "p-1"})
        detail = payload["error"]["detail"]
        self.assertIn("allow_create", detail)
        self.assertIn("this machine", detail)

    def test_a_missing_project_is_refused_before_the_floor_is_consulted(self):
        status, payload = self.request("POST", "/v1/sessions", {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")

    def test_the_floor_is_reported_rather_than_left_blank(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control"]["create_default"], "refused")
        self.assertIs(payload["control"]["create_allowed"], False)

    def test_reads_are_unaffected_by_the_create_floor(self):
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)


class CreateWithTheGrant(_Base):
    allow_create = True

    def test_a_create_is_forwarded_and_returns_the_session_it_minted(self):
        status, payload = self.request("POST", "/v1/sessions",
                                       {"project": "p-1", "message": "go"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["sessionId"], "created-1")
        self.assertEqual(payload["runId"], "run-7")
        self.assertEqual(self.provider.created[-1]["project"], "p-1")
        self.assertEqual(self.provider.created[-1]["message"], "go")

    def test_the_providers_own_field_names_travel_unchanged(self):
        self.request("POST", "/v1/sessions", {
            "project": "p-1", "provider": "codex", "model": "m",
            "lifecycleMode": "resident", "permissionMode": "bypassPermissions",
        })
        sent = self.provider.created[-1]
        self.assertEqual(sent["provider"], "codex")
        self.assertEqual(sent["model"], "m")
        self.assertEqual(sent["lifecycle_mode"], "resident")
        self.assertEqual(sent["permission_mode"], "bypassPermissions")

    def test_an_empty_message_is_not_a_message(self):
        self.request("POST", "/v1/sessions", {"project": "p-1", "message": "   "})
        self.assertEqual(self.provider.created[-1]["message"], "   ")

    def test_the_self_report_names_the_grant(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control"]["create_default"], "allowed")
        self.assertIs(payload["control"]["create_allowed"], True)


# ---------------------------------------------------------------------------
# Identity handling on the wire
# ---------------------------------------------------------------------------


class PathHandling(_Base):
    def test_a_percent_encoded_session_id_is_decoded_after_verification(self):
        """The signature covers the encoded target; the provider sees the id.

        Decoding before verification would give two different byte strings one
        signature — and an id with a `%` in it must still name the same session
        on both sides.
        """
        native = "abc,def"                       # the id CloudCLI would report
        encoded = urllib.parse.quote(native)     # `abc%2Cdef`, what goes on the wire
        self.assertNotEqual(encoded, native)
        status, payload = self.request("GET", f"/v1/sessions/{encoded}")
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.provider.calls[-1][1], f"{DEVICE}|claude|{native}")

    def test_a_session_id_that_cannot_be_an_identity_is_a_bad_request(self):
        status, payload = self.request("GET", "/v1/sessions/" + "x" * 400)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")

    def test_a_non_json_body_is_a_bad_request_not_an_internal_error(self):
        raw = b"not json at all"
        headers = np.sign(SECRET, method="POST", path=f"/v1/sessions/{ALLOWED}/read",
                          body=raw, device_ref=DEVICE)
        status, payload = self.route(
            "POST", f"/v1/sessions/{ALLOWED}/read", raw, headers
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")
        self.assertNotEqual(payload["error"]["code"], "INTERNAL")

    def test_a_json_body_that_is_not_an_object_is_refused(self):
        raw = b'["a", "list"]'
        headers = np.sign(SECRET, method="POST", path=f"/v1/sessions/{ALLOWED}/read",
                          body=raw, device_ref=DEVICE)
        status, payload = self.route("POST", f"/v1/sessions/{ALLOWED}/read", raw, headers)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")


# ---------------------------------------------------------------------------
# What the gateway's answers become
# ---------------------------------------------------------------------------


class UpstreamTranslation(_Base):
    def test_a_missing_session_stays_a_missing_session(self):
        self.provider.fail = refuse("SESSION_NOT_FOUND", "no such session")
        status, payload = self.request("GET", f"/v1/sessions/{OTHER}")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "SESSION_NOT_FOUND")

    def test_a_rejected_token_is_reported_as_an_upstream_problem(self):
        """Not as a bad request: the caller's arguments were fine.

        The distinction matters because it sends the reader to the CloudCLI
        token on the workstation rather than to their own request.
        """
        self.provider.fail = refuse("PROVIDER_UNAUTHORIZED", "gateway said 401")
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "UPSTREAM_UNAUTHORIZED")

    def test_an_unreachable_gateway_is_a_gateway_problem(self):
        self.provider.fail = refuse("PROVIDER_UNREACHABLE", "connection refused")
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "UPSTREAM_UNREACHABLE")

    def test_a_timeout_is_not_reported_as_unreachable(self):
        """A busy runtime and a missing one are different words upstream too."""
        self.provider.fail = refuse("PROVIDER_TIMEOUT", "took too long")
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "UPSTREAM_UNREACHABLE")
        self.assertIn("took too long", payload["error"]["detail"])

    def test_health_reports_a_failure_without_raising(self):
        self.provider.fail = refuse("PROVIDER_UNAUTHORIZED", "gateway said 401")
        status, payload = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertFalse(payload["reachable"])
        self.assertEqual(payload["upstream_error_code"], "PROVIDER_UNAUTHORIZED")

    def test_health_reports_what_the_gateway_declared(self):
        status, payload = self.request("GET", "/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["upstream"]["tool_count"], 22)
        self.assertEqual(payload["upstream"]["server_name"], "claudecodeui-mcp-gateway")


# ---------------------------------------------------------------------------
# Body limits, logging, and what never appears in a response
# ---------------------------------------------------------------------------


class Exposure(_Base):
    def test_an_oversized_body_is_refused_before_it_is_read(self):
        stretched = NodeConfig(
            device_ref=DEVICE, secret_anchor=self.config.secret_anchor,
            cloudcli_endpoint=self.config.cloudcli_endpoint,
            cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
            port=0, max_body_bytes=64,
        )
        connector = Connector(stretched)
        connector.provider = FakeProvider()
        server = serve(stretched, connector=connector)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            raw = json.dumps({"message": "x" * 500}).encode("utf-8")
            headers = np.sign(SECRET, method="POST", path=f"/v1/sessions/{ALLOWED}/send",
                              body=raw, device_ref=DEVICE)
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/sessions/{ALLOWED}/send",
                data=raw, headers=headers, method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=10)
            self.assertEqual(caught.exception.code, 413)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_no_response_carries_the_cloudcli_credential(self):
        """The token is read on this machine and must not appear in an answer."""
        token = (self.config.cloudcli_credential_anchor).read_text(encoding="utf-8").strip()
        for method, path, body in (
            ("GET", "/v1/health", None),
            ("GET", "/v1/self", None),
            ("GET", "/v1/sessions", None),
            ("GET", f"/v1/sessions/{ALLOWED}", None),
        ):
            with self.subTest(route=f"{method} {path}"):
                _, payload = self.request(method, path, body)
                self.assertNotIn(token, json.dumps(payload))

    def test_a_connector_with_no_secret_refuses_to_start(self):
        empty = NodeConfig(
            device_ref=DEVICE, secret_anchor=Path(self.tmp.name) / "absent",
            cloudcli_endpoint="http://127.0.0.1:3001/mcp",
            cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
        )
        with self.assertRaises(FleetRefusal) as caught:
            Connector(empty)._secret()
        self.assertEqual(caught.exception.code, "BAD_REQUEST")


# ---------------------------------------------------------------------------
# The binding file
# ---------------------------------------------------------------------------


class BindingFile(unittest.TestCase):
    def _write(self, text: str) -> Path:
        handle, path = tempfile.mkstemp(suffix=".yaml")
        os.close(handle)
        Path(path).write_text(text, encoding="utf-8")
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        return Path(path)

    def _valid(self, **over) -> str:
        fields = {
            "schema": "serveros/fleet-node.local/v1",
            "device_ref": DEVICE,
            "secret_anchor": "~/.serveros/fleet-node.secret",
            "cloudcli_endpoint": "http://127.0.0.1:3001/mcp",
            "cloudcli_credential_anchor": "~/.serveros/cloudcli.pat",
        }
        fields.update(over)
        return "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n"

    def test_a_valid_binding_loads(self):
        config = load_node_config(self._write(self._valid()))
        self.assertEqual(config.device_ref, DEVICE)
        self.assertEqual(config.bind, "127.0.0.1")
        self.assertEqual(config.port, 3402)
        self.assertEqual(config.allow_send_to, ())
        # Creation is a separate grant and its default is refusal, so a binding
        # that says nothing about it does not authorize it.
        self.assertIs(config.allow_create, False)
        self.assertEqual(config.cloudcli_endpoint, "http://127.0.0.1:3001/mcp")

    def test_a_hostname_is_not_an_identity(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid(device_ref="vm-example")))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("device:<id>", caught.exception.detail)

    def test_a_wrong_schema_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid(schema="serveros/fleet-node.local/v2")))
        self.assertIn("schema", caught.exception.detail)

    def test_a_missing_binding_is_a_refusal_not_an_empty_default(self):
        with self.assertRaises(FleetRefusal):
            load_node_config(Path(tempfile.gettempdir()) / "definitely-absent-node.yaml")

    def test_an_allow_list_survives_the_round_trip(self):
        text = self._valid() + f"allow_send_to:\n  - {ALLOWED}\n"
        config = load_node_config(self._write(text))
        self.assertEqual(config.allow_send_to, (ALLOWED,))
        self.assertTrue(config.permits_send(SessionKey.compose(DEVICE, "claude", ALLOWED)))
        self.assertFalse(config.permits_send(SessionKey.compose(DEVICE, "claude", OTHER)))

    def test_a_create_grant_survives_the_round_trip(self):
        config = load_node_config(self._write(self._valid() + "allow_create: true\n"))
        self.assertIs(config.allow_create, True)
        self.assertIs(load_node_config(self._write(self._valid())).allow_create, False)


if __name__ == "__main__":
    unittest.main()
