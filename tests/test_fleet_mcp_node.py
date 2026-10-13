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
        self.interrupted: list[str] = []
        # What `session_interrupt` answers. CloudCLI reports "there was no run
        # in flight" as `aborted: false` with a message, not as an error, so the
        # fake has to be able to say that too.
        self.interrupt_answer: dict = {"aborted": True}
        self.fail = fail

    def _maybe_fail(self):
        if self.fail is not None:
            raise self.fail

    def native_call(self, tool, arguments):
        # The mirrored-native half of CloudCLI. The connector forwards a name
        # from its closed set to here and records what it was asked.
        self.calls.append(("native_call", tool, arguments))
        self._maybe_fail()
        return {"tool": tool, "arguments": arguments}

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

    def interrupt(self, key):
        self.calls.append(("interrupt", key.ref))
        self._maybe_fail()
        self.interrupted.append(key.native_session_id)
        return dict(self.interrupt_answer)

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
    # A create grant is per device *and* per project on this side of the wire,
    # so a class that turns `allow_create` on has to say which project too —
    # the loader refuses `allow_create: true` with no project, because
    # "creation is on, for nothing" is only reachable by accident.
    allow_create_projects: tuple[str, ...] = ("p-1",)
    # Off unless a class turns it on, which is the whole point of the floor:
    # the default posture of a connector that has been asked to stop things is
    # still "no".
    allow_control: bool = False
    allow_control_to: tuple[str, ...] = ()

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
            allow_create_projects=tuple(cls.allow_create_projects),
            allow_control=cls.allow_control,
            allow_control_to=tuple(cls.allow_control_to),
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
        self.provider.created.clear()
        self.provider.interrupted.clear()
        self.provider.interrupt_answer = {"aborted": True}
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
    # Likewise for control: the table's completeness is checked without the
    # control floor confounding it, because that floor has its own class.
    allow_control = True
    allow_control_to = (ALLOWED,)

    def test_every_declared_route_is_actually_reachable(self):
        """The route table is a promise; this checks the promise is kept."""
        cases = [
            ("GET", "/v1/health", None),
            ("GET", "/v1/self", None),
            ("GET", "/v1/sessions", None),
            ("GET", f"/v1/sessions/{ALLOWED}", None),
            ("POST", f"/v1/sessions/{ALLOWED}/read", {"mode": "latest"}),
            ("POST", f"/v1/sessions/{ALLOWED}/send", {"message": "hello"}),
            ("POST", f"/v1/sessions/{ALLOWED}/interrupt", {}),
            ("GET", f"/v1/runs/run-7?session={ALLOWED}", None),
            ("POST", "/v1/sessions", {"project": "p-1"}),
            ("POST", "/v1/native/call", {"tool": "overview", "arguments": {}}),
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
            ["id", "id", "id", "id", "run_id"],
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
        self.assertEqual(payload["control"]["create_projects"], ["p-1"])


class TheProjectFloorOnCreation(_Base):
    """`allow_create` says *whether*; the project list says *what*.

    Until this existed the floor here was device-wide and the contract recorded
    that as a gap rather than a decision (`fleet_create_session.project_floor`).
    A project id is what decides the working tree and the credentials a minted
    session inherits, so a boolean was never an answer to the question that
    matters.
    """

    allow_create = True
    allow_create_projects = ("p-1", "p-2")

    def test_a_listed_project_is_forwarded(self):
        status, _ = self.request("POST", "/v1/sessions", {"project": "p-2"})
        self.assertEqual(status, 200)
        self.assertEqual(self.provider.created[-1]["project"], "p-2")

    def test_an_unlisted_project_is_refused_before_the_provider_is_asked(self):
        status, payload = self.request("POST", "/v1/sessions", {"project": "p-other"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertIn("allow_create_projects", payload["error"]["detail"])
        self.assertEqual(self.provider.created, [])
        self.assertEqual([c for c in self.provider.calls if c[0] == "create"], [])

    def test_the_match_is_exact_and_not_a_prefix(self):
        """`p-1` must not admit `p-10`: a project id is one name.

        The two are different working trees, and a prefix test would let a
        project be reached by naming its first three characters.
        """
        self.request("POST", "/v1/sessions", {"project": "p-10"})
        self.assertEqual(self.provider.created, [])

    def test_a_project_that_is_only_whitespace_is_refused(self):
        """A blank project is a bad request, not a refusal by the floor.

        The emptiness is a property of the request, so it is answered with the
        same 400 a missing project gets, whichever floor happens to be open on
        this machine. Reporting it as 403 would say "this machine declines",
        which is a different sentence from "you did not name a project".
        """
        status, payload = self.request("POST", "/v1/sessions", {"project": "   "})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")
        self.assertEqual(self.provider.created, [])

    def test_the_refusal_names_what_is_allowed(self):
        """The list travels in the message, not in a side field.

        The node's error envelope carries a code and a detail, so an operator
        reading the connector's answer to a caller has the same information the
        operator reading the binding does. A structured `allowed` beside a
        detail that did not mention it would be a second spelling of one answer.
        """
        _, payload = self.request("POST", "/v1/sessions", {"project": "nope"})
        self.assertIn("p-1", payload["error"]["detail"])
        self.assertIn("p-2", payload["error"]["detail"])

    def test_the_device_floor_is_still_asked_first(self):
        """The project floor does not replace the boolean, it narrows it."""
        status, payload = self.request("POST", "/v1/sessions", {"project": "p-1"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(self.provider.created), 1)


# ---------------------------------------------------------------------------
# This machine's own control decision
# ---------------------------------------------------------------------------


class ControlFloorIsOnTheDevice(_Base):
    """Stopping a run is a third, separate grant, and the default is refusal.

    The first of the grants here that *ends* something rather than adding to it,
    and the one an operator is most likely to want off while leaving messaging
    on. So a machine that authorized sending to a session is not thereby a
    machine that authorized ending its run, and this class asserts that the
    distinction survives as far as the provider call — which, on the refusal
    path, it must never reach.
    """

    # Messaging this session is authorized; stopping it is not. The point is
    # that the first does not carry the second.
    allow_send_to = (ALLOWED,)
    allow_control = False
    allow_control_to = (ALLOWED,)

    def test_every_interrupt_is_refused_and_nothing_reaches_cloudcli(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/interrupt", {}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.interrupted, [])
        self.assertEqual([c for c in self.provider.calls if c[0] == "interrupt"], [])

    def test_send_being_allowed_here_does_not_make_control_allowed(self):
        """The negative half of the sentence above, asserted through the socket."""
        status, _ = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/send", {"message": "hello"}
        )
        self.assertEqual(status, 200, "send was expected to work for this contrast")
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/interrupt", {}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")

    def test_the_refusal_says_where_the_decision_lives(self):
        _, payload = self.request("POST", f"/v1/sessions/{ALLOWED}/interrupt", {})
        detail = payload["error"]["detail"]
        self.assertIn("allow_control", detail)
        self.assertIn("this machine", detail)

    def test_the_floor_is_reported_rather_than_left_blank(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertIs(payload["control"]["control_allowed"], False)
        self.assertEqual(payload["control"]["control_default"], "refused")

    def test_reads_are_unaffected_by_the_control_floor(self):
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["sessions"]), 1)


class ControlWithTheGrant(_Base):
    allow_control = True
    allow_control_to = (ALLOWED,)

    def test_an_allow_listed_session_is_interrupted_and_returns_the_verdict(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/interrupt", {}
        )
        self.assertEqual(status, 200, payload)
        self.assertIs(payload["aborted"], True)
        self.assertEqual(self.provider.interrupted, [ALLOWED])

    def test_a_session_outside_the_allow_list_is_still_refused(self):
        """The list is a list, not a switch."""
        status, payload = self.request(
            "POST", f"/v1/sessions/{OTHER}/interrupt", {}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.interrupted, [])

    def test_a_full_ref_is_accepted_in_the_allow_list_too(self):
        ref = f"{DEVICE}|claude|{ALLOWED}"
        self.assertTrue(self.config.permits_control(SessionKey.parse(ref)))
        self.assertTrue(
            Connector(NodeConfig(
                device_ref=DEVICE, secret_anchor=self.config.secret_anchor,
                cloudcli_endpoint=self.config.cloudcli_endpoint,
                cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
                allow_control=True, allow_control_to=(ref,),
            )).config.permits_control(SessionKey.parse(ref))
        )

    def test_the_verdict_is_forwarded_rather_than_flattened(self):
        """`aborted: false` with a sentence is a success, and it survives the hop."""
        sentence = "This session currently has no run in flight, so there is no run to abort."
        self.provider.interrupt_answer = {"aborted": False, "message": sentence}
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/interrupt", {}
        )
        self.assertEqual(status, 200, payload)
        self.assertIs(payload["aborted"], False)
        self.assertEqual(payload["message"], sentence)

    def test_a_body_is_not_read_because_there_is_nothing_to_read(self):
        """The handler takes no parameter, so a smuggled field changes nothing.

        A field that were silently ignored would read, at the caller, as though
        the far side honoured it.
        """
        status, payload = self.request(
            "POST", f"/v1/sessions/{ALLOWED}/interrupt",
            {"signal": "SIGKILL", "force": True, "command": "rm -rf /"},
        )
        self.assertEqual(status, 200, payload)
        self.assertIs(payload["aborted"], True)
        self.assertEqual(self.provider.interrupted, [ALLOWED])
        # The body reached nobody: the one interrupt call the provider saw
        # carries the session and no second value.
        self.assertEqual([c for c in self.provider.calls if c[0] == "interrupt"],
                         [("interrupt", f"{DEVICE}|claude|{ALLOWED}")])

    def test_the_self_report_names_the_grant(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertIs(payload["control"]["control_allowed"], True)
        self.assertEqual(payload["control"]["control_allow_list"], [ALLOWED])
        self.assertEqual(payload["control"]["control_default"],
                         "only the sessions named in allow_control_to")

    def test_the_device_with_no_named_session_reports_a_closed_door(self):
        """`control_allow: true` with an empty list permits nothing.

        The banner has no session to ask about, so it must not report the empty
        list as though it were a list of permissions.
        """
        other = Connector(NodeConfig(
            device_ref=DEVICE, secret_anchor=self.config.secret_anchor,
            cloudcli_endpoint=self.config.cloudcli_endpoint,
            cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
            allow_control=True, allow_control_to=(),
        ))
        self.assertFalse(other.config.permits_any_control())
        self.assertFalse(
            other.config.permits_control(SessionKey.parse(f"{DEVICE}|claude|{ALLOWED}"))
        )


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
            load_node_config(self._write(self._valid(device_ref="VM-16-5-ubuntu")))
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
        text = self._valid() + "allow_create: true\nallow_create_projects:\n  - p-1\n"
        config = load_node_config(self._write(text))
        self.assertIs(config.allow_create, True)
        self.assertEqual(config.allow_create_projects, ("p-1",))
        self.assertIs(load_node_config(self._write(self._valid())).allow_create, False)
        self.assertEqual(
            load_node_config(self._write(self._valid())).allow_create_projects, ())


class TheProjectFloorIsRefusedWhenContradictory(BindingFile):
    """`allow_create: true` with no project is refused at load, not at use.

    "Creation is on, for nothing" is only reachable by accident and is invisible
    at the moment it happens: the connector would start, serve, and refuse every
    project for a reason nobody wrote down. The loader puts it where the operator
    who made the edit is still looking at the file.
    """

    def test_create_with_no_project_is_refused_at_load(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid(allow_create=True)))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("allow_create_projects", caught.exception.detail)

    def test_create_off_with_no_project_is_the_ordinary_case(self):
        config = load_node_config(self._write(self._valid()))
        self.assertIs(config.allow_create, False)
        self.assertEqual(config.allow_create_projects, ())

    def test_create_off_with_projects_is_accepted(self):
        """Turning the device floor off does not require deleting the list.

        An operator withdrawing creation for a while is undoing one edit, not
        two; the projects are inert while `allow_create` is false, and the
        loader says nothing about them.
        """
        config = load_node_config(
            self._write(self._valid(allow_create=False, allow_create_projects=["p-1"])))
        self.assertIs(config.allow_create, False)
        self.assertEqual(config.allow_create_projects, ("p-1",))

    def test_a_wildcard_project_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(
                self._valid(allow_create=True, allow_create_projects=["*"])))
        self.assertIn("wildcard", caught.exception.detail.lower())

    def test_a_bare_string_is_refused_rather_than_wrapped(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(
                self._valid(allow_create=True, allow_create_projects="p-1")))
        self.assertIn("not a str", caught.exception.detail)

    def test_duplicates_collapse(self):
        config = load_node_config(self._write(
            self._valid(allow_create=True, allow_create_projects=["p-1", "p-1", "p-2"])))
        self.assertEqual(config.allow_create_projects, ("p-1", "p-2"))


class TheControlGrantInTheBindingFile(BindingFile):
    """Control is read out of the binding the strict way, and its default is off.

    The grant has two halves that are validated differently, and the difference
    is the point: the boolean is asked for as a boolean, the list is asked for
    as a list of session names. Either half alone is inert — the flag with no
    list interrupts nothing, and a list under a closed flag interrupts nothing —
    and both of those states are checked here rather than at the route, because
    a binding that reads as "on" to its author and as "off" to the connector is
    the failure this whole class exists for.
    """

    def test_the_default_is_off_and_names_nothing(self):
        config = load_node_config(self._write(self._valid()))
        self.assertIs(config.allow_control, False)
        self.assertEqual(config.allow_control_to, ())
        self.assertFalse(config.permits_any_control())
        self.assertEqual(config.control_default(), "refused")

    def test_a_control_grant_survives_the_round_trip(self):
        text = self._valid() + f"allow_control: true\nallow_control_to:\n  - {ALLOWED}\n"
        config = load_node_config(self._write(text))
        self.assertIs(config.allow_control, True)
        self.assertEqual(config.allow_control_to, (ALLOWED,))
        self.assertTrue(config.permits_control(SessionKey.compose(DEVICE, "claude", ALLOWED)))
        self.assertFalse(config.permits_control(SessionKey.compose(DEVICE, "claude", OTHER)))
        self.assertEqual(config.control_default(),
                         "only the sessions named in allow_control_to")

    def test_the_open_flag_with_no_list_is_a_closed_door(self):
        """`allow_control: true` alone permits nothing, and says so.

        A binding is written in two edits more often than one — the flag first,
        then the sessions — so the state in between has to be the safe one. It
        is, and this test is what keeps it that way if anyone later decides the
        flag alone should mean "any session".
        """
        config = load_node_config(self._write(self._valid(allow_control=True)))
        self.assertIs(config.allow_control, True)
        self.assertEqual(config.allow_control_to, ())
        self.assertFalse(config.permits_any_control())
        self.assertFalse(config.permits_control(SessionKey.compose(DEVICE, "claude", ALLOWED)))
        self.assertEqual(config.control_default(), "refused")

    def test_the_list_under_a_closed_flag_is_inert_and_visible(self):
        """Withdrawing the flag does not require deleting the list.

        The same shape as the create floor: an operator turning control off for
        a while is undoing one edit. The list stays in the file, permits nothing,
        and is still reported beside the flag — so an operator reading `/v1/self`
        sees a non-empty `control_allow_list` next to `control_allowed: false`
        and can tell "deleted" from "switched off".
        """
        config = load_node_config(self._write(
            self._valid(allow_control=False, allow_control_to=[ALLOWED])))
        self.assertIs(config.allow_control, False)
        self.assertEqual(config.allow_control_to, (ALLOWED,))
        self.assertFalse(config.permits_control(SessionKey.compose(DEVICE, "claude", ALLOWED)))
        self.assertEqual(config.control_default(), "refused")

    def test_a_wildcard_is_refused_and_the_refusal_names_the_other_field(self):
        """`"*"` in a session list would be a second spelling of the floor.

        The device-wide grant is written `allow_all_sessions_for_owner:
        [control]`, so a wildcard here is refused with a message that says where
        that grant lives. A refusal that only said "wildcard" would leave an
        operator who meant "everything" with nowhere to go.
        """
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(
                self._valid(allow_control=True, allow_control_to=["*"])))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("allow_all_sessions_for_owner", caught.exception.detail)

    def test_a_bare_string_is_refused_rather_than_wrapped(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(
                self._valid(allow_control=True, allow_control_to=ALLOWED)))
        self.assertIn("not a str", caught.exception.detail)

    def test_a_blank_entry_is_refused(self):
        with self.assertRaises(FleetRefusal):
            load_node_config(self._write(
                self._valid(allow_control=True, allow_control_to=["  "])))

    def test_a_non_string_entry_is_refused(self):
        """YAML reads a bare number as an int, and a session id is never one."""
        with self.assertRaises(FleetRefusal):
            load_node_config(self._write(
                self._valid(allow_control=True, allow_control_to=[5])))

    def test_duplicates_collapse_and_order_survives(self):
        config = load_node_config(self._write(
            self._valid(allow_control=True, allow_control_to=[OTHER, OTHER, ALLOWED])))
        self.assertEqual(config.allow_control_to, (OTHER, ALLOWED))

    def test_a_full_ref_in_the_list_matches_the_session_it_names(self):
        ref = f"{DEVICE}|claude|{ALLOWED}"
        config = load_node_config(self._write(
            self._valid(allow_control=True, allow_control_to=[ref])))
        self.assertTrue(config.permits_control(SessionKey.parse(ref)))

    def test_a_quoted_boolean_is_refused_rather_than_read_as_true(self):
        """The one-coercion bug this binding must not have.

        A template that interpolates a flag produces `allow_control: "false"`,
        and `bool("false")` is True. Refusing it is the only answer that is
        right in both directions: the operator who meant false is protected, and
        the operator who meant true is told their file is not what they think.
        """
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid(allow_control='"false"')))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("must be true or false", caught.exception.detail)

    def test_the_same_rule_holds_for_the_create_flag(self):
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid(allow_create='"false"')))
        self.assertIn("allow_create", caught.exception.detail)


class TheDataclassDefaultsAreClosed(unittest.TestCase):
    """Every grant defaults to refusal on the dataclass, not only in the loader.

    The binding file is one door; `NodeConfig(...)` is another. A test suite
    where every class passes its grants explicitly would stay green if the
    dataclass default were flipped to `True`, because nothing would ever build
    the bare object. These tests build exactly that object — the one an embedder
    or a future caller gets when they name no grants at all — and pin every
    default to the closed posture. `allow_control` is the one that matters most:
    it is the only default here that would turn a destructive verb on.
    """

    def _bare(self) -> NodeConfig:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "node.secret").write_text(SECRET, encoding="utf-8")
        (root / "cloudcli.token").write_text("ccp_not-a-real-token", encoding="utf-8")
        return NodeConfig(
            device_ref=DEVICE,
            secret_anchor=root / "node.secret",
            cloudcli_endpoint="http://127.0.0.1:3001/mcp",
            cloudcli_credential_anchor=root / "cloudcli.token",
        )

    def test_no_grant_named_means_no_grant(self):
        config = self._bare()
        self.assertEqual(config.allow_send_to, ())
        self.assertIs(config.allow_create, False)
        self.assertEqual(config.allow_create_projects, ())
        self.assertIs(config.allow_control, False)
        self.assertEqual(config.allow_control_to, ())
        # These two were missing from this list as well as from the loader, which
        # is how a grant can be declared, read by its floor, and still
        # unreachable: nothing here built the object that would have shown it.
        self.assertIs(config.allow_approve, False)
        self.assertIs(config.allow_navigate, False)
        self.assertEqual(config.allow_all_sessions_for_owner, ())

    def test_the_bare_config_permits_none_of_the_three_verbs(self):
        config = self._bare()
        key = SessionKey.compose(DEVICE, "claude", ALLOWED)
        self.assertIsNone(config.send_grant(key))
        self.assertFalse(config.permits_send(key))
        self.assertFalse(config.permits_control(key))
        self.assertFalse(config.permits_any_control())
        self.assertEqual(config.control_default(), "refused")


class TheApproveAndNavigateFlagsInTheBindingFile(BindingFile):
    """`approval_answer` and `ui_open_session` are switchable, and off by default.

    Both flags were declared on `NodeConfig` and read by `_native_floor`, but the
    loader never passed them — so a device owner could write `allow_approve: true`
    in their own binding and get a machine that refused every approval anyway,
    with nothing on it that said why. A field the loader forgets is a field no
    operator can turn on, so what is tested here is the ROUND TRIP: the file says
    true, the loaded config says true.

    The negatives are the same shape the other grants use, because the failure
    they prevent is the same one: a template that interpolates a flag produces
    `"false"`, and `bool("false")` is True.
    """

    def test_the_default_is_off_for_both(self):
        config = load_node_config(self._write(self._valid()))
        self.assertIs(config.allow_approve, False)
        self.assertIs(config.allow_navigate, False)

    def test_an_approve_grant_survives_the_round_trip(self):
        config = load_node_config(self._write(self._valid(allow_approve=True)))
        self.assertIs(config.allow_approve, True)
        self.assertIs(config.allow_navigate, False,
                      "opening one verb must not open the other")

    def test_a_navigate_grant_survives_the_round_trip(self):
        config = load_node_config(self._write(self._valid(allow_navigate=True)))
        self.assertIs(config.allow_navigate, True)
        self.assertIs(config.allow_approve, False,
                      "opening one verb must not open the other")

    def test_both_can_be_opened_together(self):
        config = load_node_config(self._write(
            self._valid(allow_approve=True, allow_navigate=True)))
        self.assertIs(config.allow_approve, True)
        self.assertIs(config.allow_navigate, True)

    def test_either_flag_explicitly_false_stays_false(self):
        config = load_node_config(self._write(
            self._valid(allow_approve=False, allow_navigate=False)))
        self.assertIs(config.allow_approve, False)
        self.assertIs(config.allow_navigate, False)

    def test_a_quoted_boolean_is_refused_rather_than_read_as_true(self):
        for field in ("allow_approve", "allow_navigate"):
            with self.subTest(field=field):
                with self.assertRaises(FleetRefusal) as caught:
                    load_node_config(self._write(self._valid(**{field: '"false"'})))
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIn(field, caught.exception.detail)
                self.assertIn("must be true or false", caught.exception.detail)

    def test_a_non_boolean_is_refused_for_both(self):
        """A list and a number, so the refusal is about the TYPE and not about
        one spelling of one wrong value."""
        for field, rendered in (("allow_approve", "[1, 2]"),
                               ("allow_navigate", "3")):
            with self.subTest(field=field):
                with self.assertRaises(FleetRefusal) as caught:
                    load_node_config(self._write(self._valid(**{field: rendered})))
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIn(field, caught.exception.detail)


class _EnumeratingProvider:
    """A CloudCLI that answers, and enumerates one approval request.

    The device-side ownership check reads `approvals_list` before it forwards an
    answer, so a fake that answers nothing would make the positive case below
    fail for a reason that has nothing to do with the flags under test.
    """

    def __init__(self):
        self.calls: list[tuple] = []

    def native_call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "approvals_list":
            return {"approvals": [{"requestId": "r1"}]}
        return {"ok": True, "tool": tool}


class TheFlagsReachTheFloorThroughTheBindingFile(BindingFile):
    """The whole chain: binding YAML -> `load_node_config` -> `Connector` -> floor.

    Every other device-side approval test builds `NodeConfig(...)` directly, with
    `allow_approve=True` written into the constructor call. That is exactly why
    the loader bug survived a green suite: nothing ever went through the file an
    operator actually edits. These tests start from the YAML and end at the
    provider, so a flag the loader forgets fails here.
    """

    def _connector(self, **over):
        config = load_node_config(self._write(self._valid(**over)))
        connector = Connector(config)
        connector.provider = _EnumeratingProvider()
        return connector

    def _session_ref(self, native="s1"):
        return f"{DEVICE}|claude|{native}"

    def test_an_opened_binding_lets_approval_reach_the_provider(self):
        c = self._connector(allow_approve=True)
        got = c._native_call({"tool": "approval_answer",
                              "session_ref": self._session_ref(),
                              "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(got["ok"], True)
        self.assertEqual([name for name, _ in c.provider.calls],
                         ["approvals_list", "approval_answer"])

    def test_an_opened_binding_lets_navigation_reach_the_provider(self):
        c = self._connector(allow_navigate=True)
        got = c._native_call({"tool": "ui_open_session",
                              "arguments": {"session": "s1"}})
        self.assertEqual(got["ok"], True)
        self.assertEqual([name for name, _ in c.provider.calls], ["ui_open_session"])

    def test_a_binding_that_says_nothing_still_refuses_both(self):
        """The same constructor, the same provider, one edit apart: without the
        flag the call never reaches the device. This is the negative that makes
        the two above mean something."""
        for tool, payload in (("approval_answer",
                               {"session_ref": self._session_ref(),
                                "arguments": {"requestId": "r1", "allow": True}}),
                              ("ui_open_session", {"arguments": {"session": "s1"}})):
            with self.subTest(tool=tool):
                c = self._connector()
                with self.assertRaises(FleetRefusal) as caught:
                    c._native_call({"tool": tool, **payload})
                self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
                self.assertEqual(c.provider.calls, [])

    def test_opening_approve_does_not_open_navigate(self):
        c = self._connector(allow_approve=True)
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "ui_open_session",
                            "arguments": {"session": "s1"}})
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(c.provider.calls, [])


if __name__ == "__main__":
    unittest.main()

