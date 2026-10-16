#!/usr/bin/env python3
"""The Core reading, over a REAL signed node round trip — and its absence.

The unit tests hand `admit_core_lite` a source and ask what it makes of the
answer. This file asks the question one layer out: does the whole chain work
when the document actually travels?

    FleetNodeProvider ──signed GET /v1/core/status──▶ test node ──▶ document
        └─▶ NodeCoreLiteSource ──▶ admit_core_lite ──▶ readback
              └─▶ fleet_list_devices / fleet_status

Real: an HTTP server on a loopback ephemeral port, the real `node_protocol`
signature (verified by the server exactly as a connector would), the real
serialisation of the document, the real provider, the real service and the real
tools. Fake: the DOCUMENT, and the identity — the test node calls itself
`device:transport-test-node`, which is deliberately NOT the Tokyo device, so a
receipt from this file can never be quoted as a reading of a real machine.

Nothing here starts a service, opens a public port, or touches a real device.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import core_lite_source, node_protocol, tools as tools_mod  # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.providers.fleet_node import CORE_LITE_MAX_BYTES, FleetNodeProvider  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402
from fleet_mcp.runtime_identity import BASE_RUNTIME  # noqa: E402

SECRET = "transport-test-node-secret-not-a-real-credential"
NODE = "device:transport-test-node"
ACCOUNT = "transport-test"
# The reader bounds freshness against ITS clock, so the fixture's stamps are
# taken from the real one. A frozen constant made every document look hours
# stale — or, worse, future-dated — which is a property of the fixture, not
# of the chain under test.
NOW = dt.datetime.now(dt.timezone.utc)


def stamp(offset: float = -1.0) -> str:
    return (NOW + dt.timedelta(seconds=offset)).isoformat()


def core_document(*, node: str = NODE, account: str = ACCOUNT) -> dict:
    """A Core-Lite document shaped exactly as the producer emits one."""
    return {
        "schema": "serveros/core-lite/status/v1",
        "node": node,
        "principal": account,
        "platform": "linux",
        "generation": 1,
        "mode": "observe-only",
        "observed_at": stamp(),
        "freshness": "NOT_ASSERTED",
        "principal_identity": {"state": "MATCHED", "basis": "ATTESTED",
                               "claimed": account, "observed": account},
        "device_identity": {"state": "MATCHED", "basis": "LOCAL_DECLARATION",
                            "claimed": node},
        "live": True,
        "observations": [
            {"name": "cpu", "state": "PRESENT", "observed_at": stamp(),
             "value": {"used_percent": 12.5}, "completeness": "COMPLETE"},
            {"name": "memory", "state": "PRESENT", "observed_at": stamp(),
             "value": {"total_bytes": 1024, "available_bytes": 512,
                       "free_bytes": 256, "used_percent": 50.0},
             "completeness": "COMPLETE"},
        ],
    }


class TestNode:
    """A connector, in a thread, on an ephemeral loopback port.

    It verifies the signature with `node_protocol.verify` — the same call a real
    connector makes — so a provider that signed wrongly is refused here rather
    than politely served. What it returns is decided by the test, not by the
    request: there is no path or query a caller can shape into a different
    answer, which is the property the route exists to have.
    """

    def __init__(self, *, body: object = None, status: int = 200,
                 raw: bytes | None = None, secret: str = SECRET,
                 device_ref: str = NODE) -> None:
        self.body = core_document() if body is None else body
        self.status = status
        self.raw = raw
        self.secret = secret
        self.device_ref = device_ref
        self.seen: list[dict] = []
        self.verify_errors: list[str] = []
        self._replay = node_protocol.ReplayCache()

        test = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # keep the test output clean
                pass

            def do_GET(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                test.seen.append({"method": "GET", "path": self.path})
                try:
                    node_protocol.verify(
                        test.secret, method="GET",
                        path=node_protocol.split_path_and_query(self.path)[0],
                        body=body, headers=dict(self.headers),
                        expected_device_ref=test.device_ref, replay=test._replay)
                except Exception as exc:  # noqa: BLE001 - the point of the test
                    test.verify_errors.append(type(exc).__name__)
                    self.send_response(401)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                payload = test.raw if test.raw is not None else json.dumps(test.body).encode()
                self.send_response(test.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def harness(tmp: Path, node: TestNode, *, account: str = ACCOUNT):
    anchor = tmp / "node.secret"
    anchor.write_text(SECRET, encoding="utf-8")
    registry = tmp / "registry.yaml"
    registry.write_text(
        "devices:\n"
        f"  - device_ref: {NODE}\n"
        "    display_name: transport-test-node\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        "    ownership: company\n", encoding="utf-8")
    config = FleetConfig(
        source=None, local_device_ref=NODE,
        providers={provider_key(NODE, BASE_RUNTIME): ProviderBinding(
            device_ref=NODE, runtime=BASE_RUNTIME, kind="fleet-node",
            endpoint=node.endpoint, credential_anchor=anchor, account=account,
            allow_send=True, allow_control=True)},
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(),
        audit=AuditBinding(path=tmp / "audit.jsonl",
                           rate_limit_per_minute=10_000))
    provider = FleetNodeProvider(config.providers[provider_key(NODE, BASE_RUNTIME)])
    service = FleetService(config, registry=load_registry(registry),
                           providers={provider_key(NODE, BASE_RUNTIME): provider})
    return service, provider


def principal() -> Principal:
    return Principal(name="transport-test", scopes=frozenset({"fleet:read"}),
                     credential_kind="pat")


def with_node(node_factory, body) -> None:
    """Run `body(service, node)` against a live test node, and always stop it."""
    with tempfile.TemporaryDirectory() as raw:
        node = node_factory() if callable(node_factory) else node_factory
        try:
            service, provider = harness(Path(raw), node)
            body(service, node, provider)
        finally:
            node.stop()


class TheDocumentTravelsAndIsShown(unittest.TestCase):
    def test_a_signed_round_trip_puts_a_reading_in_the_status_tools(self):
        def body(service, node, provider):
            self.assertEqual(node.verify_errors, [])
            status = service.status(principal())
            rows = {row["device_ref"]: row for row in status["core_lite"]}
            self.assertIn(NODE, rows)
            summary = rows[NODE]
            # What the chain PROMISES: the route authenticated, the document is
            # this device's, and the readings are there.
            self.assertEqual(summary["route"]["state"], "VERIFIED")
            self.assertEqual(summary["device"]["state"], "MATCHED")
            self.assertEqual(summary["device"]["declared"], NODE)
            # `connected` is a STRONGER verdict than any of that, and this
            # document cannot reach it: its device identity rests on a
            # `LOCAL_DECLARATION`, which is the device's own say-so. The reader
            # declines to raise the verdict on it and does not pretend the
            # reading is absent either — both facts are asserted, because a test
            # that only checked for CONNECTED would be testing the fixture.
            self.assertNotEqual(summary["connected"], "CONNECTED")
            obs = summary["observations"]
            self.assertEqual(obs["cpu"]["state"], "PRESENT")
            self.assertEqual(obs["cpu"]["value"]["used_percent"], 12.5)

            listed = service.list_devices(principal())
            row = listed["devices"][0]
            self.assertEqual(row["core_lite"]["route"]["state"], "VERIFIED")
            # The provider block and the Core block are DIFFERENT answers, and
            # the reachable one must not stand in for the measured one.
            self.assertIsNotNone(row["provider"])

            # The Core document came over the NAMED route and no other. The
            # only other call is the provider-health probe `list_devices` makes,
            # which is a different question answered by a different route — and
            # its presence here is the evidence that the two answers are
            # genuinely separate round trips rather than one dressed as two.
            paths = [entry["path"] for entry in node.seen]
            self.assertIn("/v1/core/status", paths)
            self.assertLessEqual(set(paths), {"/v1/health", "/v1/core/status"})

        with_node(TestNode, body)

    def test_the_reading_is_not_the_provider_health(self):
        """A device whose transport answers, and whose Core says otherwise."""
        def body(service, node, provider):
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertEqual(summary["route"]["state"], "VERIFIED")
            provider_health = provider.health().as_dict()
            self.assertTrue(provider_health["reachable"])
            # The two are separate objects with separate vocabularies: nothing
            # in the Core summary is derived from the health answer.
            self.assertNotIn("reachable", summary)
            self.assertIn("observations", summary)

        with_node(TestNode, body)


class WhatTheChainRefuses(unittest.TestCase):
    def _summary(self, tmp: Path, node: TestNode):
        service, _ = harness(tmp, node)
        return service.core_lite_status(principal(), NODE, received_at=NOW)

    def test_a_node_that_cannot_authenticate_us_is_not_authenticated(self):
        with tempfile.TemporaryDirectory() as raw:
            node = TestNode(secret="a different secret entirely")
            try:
                summary = self._summary(Path(raw), node)
                self.assertEqual(summary["connected"], "NOT_CONNECTED")
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
                self.assertEqual(summary["observations"], {})
            finally:
                node.stop()

    def test_a_document_about_another_node_is_not_this_devices_reading(self):
        with tempfile.TemporaryDirectory() as raw:
            node = TestNode(body=core_document(node="device:somewhere-else"))
            try:
                summary = self._summary(Path(raw), node)
                # The route IS ours — we authenticated to our node — and the
                # DOCUMENT names somebody else. Keeping those apart is the
                # point: a reader that looked only at the route would publish
                # another machine's reading under this one's name.
                self.assertEqual(summary["route"]["state"], "VERIFIED")
                self.assertEqual(summary["device"]["state"], "MISMATCH")
                # The foreign name is not echoed back: `declared` is null rather
                # than the other machine's ref, because the summmary is a
                # surface another system displays and a name this reader has no
                # business holding does not travel through it.
                self.assertIsNone(summary["device"]["declared"])
                self.assertEqual(summary["connected"], "NOT_CONNECTED")
                # NOTED, not asserted away: `readback` still projects the
                # observations and marks the contradiction in `device.state` and
                # `connected`. That is the reader module's existing decision, and
                # a consumer must gate on those two rather than on the presence
                # of observations — which is why this test asserts them.
                self.assertEqual(sorted(summary["observations"]), ["cpu", "memory"])
            finally:
                node.stop()

    def test_a_stale_document_is_not_fresh(self):
        with tempfile.TemporaryDirectory() as raw:
            stale = core_document()
            stale["observed_at"] = (NOW - dt.timedelta(hours=2)).isoformat()
            node = TestNode(body=stale)
            try:
                summary = self._summary(Path(raw), node)
                self.assertEqual(summary["freshness"]["state"], "STALE")
            finally:
                node.stop()

    def test_a_body_past_the_cap_is_refused_rather_than_read(self):
        """A device does not get to decide how much memory Fleet spends."""
        with tempfile.TemporaryDirectory() as raw:
            node = TestNode(raw=b"{" + b" " * (CORE_LITE_MAX_BYTES + 16) + b"}")
            try:
                service, _ = harness(Path(raw), node)
                summary = service.core_lite_status(principal(), NODE, received_at=NOW)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
                self.assertEqual(summary["observations"], {})
            finally:
                node.stop()

    def test_a_node_that_answers_nothing_is_not_a_reading(self):
        """An empty body on a verified route is still not a reading.

        The route authenticated, so it stays VERIFIED — reporting it otherwise
        would blame the tunnel for a document that is simply not there. What
        must NOT happen is an observation set invented to fill the gap.
        """
        with tempfile.TemporaryDirectory() as raw:
            node = TestNode(body={}, status=200)
            try:
                summary = self._summary(Path(raw), node)
                self.assertNotEqual(summary["connected"], "CONNECTED")
                self.assertEqual(summary["observations"], {})
            finally:
                node.stop()

    def test_no_secret_is_unauthenticated_not_a_source_failure(self):
        """The two send an operator to different files."""
        with tempfile.TemporaryDirectory() as raw:
            node = TestNode()
            try:
                tmp = Path(raw)
                service, provider = harness(tmp, node)
                provider.binding.credential_anchor.unlink()
                summary = service.core_lite_status(principal(), NODE, received_at=NOW)
                self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
                self.assertEqual(summary["connected"], "NOT_CONNECTED")
                # ...and nothing was even asked: an unauthenticated route does
                # not get to make the device answer.
                self.assertEqual(node.seen, [])
            finally:
                node.stop()


class ADirectCloudCliDeviceSaysSo(unittest.TestCase):
    """The honest answer for the path this run could not wire.

    A CloudCLI device is reached with a PAT that can DRIVE a session. That is
    not evidence of what the machine's Core measured, and the direct path has no
    signed Core route today, so the block must say `NOT_CONFIGURED` rather than
    borrow a reading from somewhere else to make a device look green.
    """

    def test_a_device_with_no_core_route_is_not_configured(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            anchor = tmp / "pat"
            anchor.write_text("owner-token-value", encoding="utf-8")
            registry = tmp / "registry.yaml"
            registry.write_text(
                "devices:\n"
                f"  - device_ref: {NODE}\n"
                "    display_name: direct\n"
                "    platform: linux\n"
                "    environment_profile: linux-shared-multiuser\n"
                "    ownership: company\n", encoding="utf-8")

            class Direct:
                device_ref = NODE
                kind = "cloudcli-mcp-http"

                def health(self):
                    from fleet_mcp.providers.base import ProviderHealth
                    return ProviderHealth(device_ref=NODE, kind=self.kind,
                                          reachable=True, authorized=True,
                                          detail="ok")

            config = FleetConfig(
                source=None, local_device_ref=NODE,
                providers={NODE: ProviderBinding(
                    device_ref=NODE, kind="cloudcli-mcp-http",
                    endpoint="http://127.0.0.1:0/mcp", credential_anchor=anchor,
                    account=ACCOUNT)},
                auth=AuthBinding(pat_anchor=anchor),
                server=ServerBinding(bind="127.0.0.1", port=0),
                policy=PolicyBinding(),
                audit=AuditBinding(path=tmp / "audit.jsonl",
                                   rate_limit_per_minute=10_000))
            service = FleetService(config, registry=load_registry(registry),
                                   providers={NODE: Direct()})
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertEqual(summary["route"]["state"], "NOT_CONFIGURED")
            self.assertEqual(summary["observations"], {})
            # Its transport is fine; its Core is simply not wired. Both are true
            # at once and must not be collapsed into one another.
            row = service.list_devices(principal())["devices"][0]
            self.assertTrue(row["provider"]["reachable"])
            self.assertEqual(row["core_lite"]["route"]["state"], "NOT_CONFIGURED")


class TheSurfaceStillHoldsItsShape(unittest.TestCase):
    def test_no_tool_and_no_scope_was_added(self):
        from fleet_mcp import scopes
        from fleet_mcp.version import TOOL_SURFACE_REVISION
        self.assertEqual(len(tools_mod.SERVED_TOOLS), 33)
        self.assertEqual(len(scopes.ALLOWED_SCOPES), 6)
        self.assertEqual(TOOL_SURFACE_REVISION, "fleet-mcp-4")
        self.assertNotIn("core_lite_status",
                         [t.name for t in tools_mod.SERVED_TOOLS])

    def test_the_core_route_is_in_the_closed_set(self):
        self.assertIn("GET /v1/core/status", node_protocol.ROUTES)

    def test_a_core_lite_reading_is_not_a_capability_the_source_can_claim(self):
        """`CoreLiteStatusSource` is one method and it is not a verb surface."""
        source = core_lite_source.CoreLiteStatusSource
        self.assertEqual(
            [name for name in dir(source) if not name.startswith("_")],
            ["fetch"])


if __name__ == "__main__":
    unittest.main()
