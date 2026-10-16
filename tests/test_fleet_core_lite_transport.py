#!/usr/bin/env python3
"""The Core reading over the REAL connector, and every way it is refused.

The unit tests hand `admit_core_lite` a source. This file asks the question one
layer out, against the actual server a device runs:

    FleetNodeProvider ──signed GET /v1/core/status──▶ Connector (real) ──▶ snapshot file
        └─▶ NodeCoreLiteSource ──▶ admit_core_lite ──▶ readback
              └─▶ fleet_list_devices / fleet_status

Real: `fleet_mcp.node.connector.Connector` — its routing, its signature check,
its route dispatch, its owner-fixed snapshot reader, its RESPONSE SIGNING — plus
the real `node_protocol`, the real provider, service and tools, on a loopback
ephemeral port. Fake: the snapshot FILE's contents, and the identity — this node
calls itself `device:transport-test-node`, deliberately not Tokyo, so no receipt
here can be quoted as a reading of a real machine.

The response signature is the point of half of these tests. Signing the REQUEST
proves who the caller is; it says nothing about who answered, and a 200 from any
process on that URL is exactly what a wrong `endpoint` produces.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

from fleet_mcp import node_protocol, tools as tools_mod  # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.node import connector as node_connector  # noqa: E402
from fleet_mcp.providers.fleet_node import CORE_LITE_MAX_BYTES, FleetNodeProvider  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402
from fleet_mcp.runtime_identity import BASE_RUNTIME  # noqa: E402

SECRET = "transport-test-node-secret-not-a-real-credential"
NODE = "device:transport-test-node"
ACCOUNT = "transport-test"
OTHER_RUNTIME = "claude-other"
# The reader bounds freshness against ITS clock, so the fixture's stamps come
# from the real one: a frozen constant makes every document look stale, which is
# a property of the fixture rather than of the chain.
NOW = dt.datetime.now(dt.timezone.utc)


def stamp(offset: float = -1.0) -> str:
    return (NOW + dt.timedelta(seconds=offset)).isoformat()


def core_document(*, node: str = NODE, account: str = ACCOUNT) -> dict:
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
        ],
    }


class RealConnector:
    """The device's own server class, on a loopback ephemeral port."""

    def __init__(self, tmp: Path, *, snapshot=None, raw: bytes | None = None,
                 configure: bool = True, runtime: str = BASE_RUNTIME,
                 secret: str = SECRET) -> None:
        self.secret_anchor = tmp / "node.secret"
        self.secret_anchor.write_text(secret, encoding="utf-8")
        self.snapshot = tmp / "core-status.json"
        if configure:
            payload = raw if raw is not None else json.dumps(
                core_document() if snapshot is None else snapshot).encode("utf-8")
            self.snapshot.write_bytes(payload)
        cloudcli_anchor = tmp / "cloudcli.pat"
        cloudcli_anchor.write_text("unused-by-this-route", encoding="utf-8")
        self.config = node_connector.NodeConfig(
            device_ref=NODE, secret_anchor=self.secret_anchor,
            cloudcli_endpoint="http://127.0.0.1:0/mcp",
            cloudcli_credential_anchor=cloudcli_anchor,
            bind="127.0.0.1", port=0, runtime=runtime,
            core_snapshot_path=self.snapshot if configure else None)
        self.connector = node_connector.Connector(self.config)
        # `serve()` RETURNS the server; it does not serve. A fixture that
        # forgot this would leave every request waiting out the client's own
        # read timeout, which looks like a hanging chain rather than a missing
        # thread.
        self.server = node_connector.serve(self.config, connector=self.connector)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


class FakeServer:
    """A process on the same URL that knows nothing about us.

    It answers 200 with a perfectly well-formed document and never looks at the
    request. This is what a wrong `endpoint`, or anything else listening on that
    port, produces — and a chain that reported it as VERIFIED would be reporting
    a stranger's body as the machine's Core.
    """

    def __init__(self, *, body=None, headers: dict | None = None) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        payload = json.dumps(core_document() if body is None else body).encode()
        extra = headers or {}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                for name, value in extra.items():
                    self.send_header(name, value)
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


def harness(tmp: Path, node, *, account: str = ACCOUNT, runtime: str = BASE_RUNTIME):
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
    binding = ProviderBinding(
        device_ref=NODE, runtime=runtime, kind="fleet-node",
        endpoint=node.endpoint, credential_anchor=anchor, account=account,
        allow_send=True, allow_control=True)
    config = FleetConfig(
        source=None, local_device_ref=NODE,
        providers={provider_key(NODE, runtime): binding},
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(),
        audit=AuditBinding(path=tmp / "audit.jsonl",
                           rate_limit_per_minute=10_000))
    provider = FleetNodeProvider(binding)
    service = FleetService(config, registry=load_registry(registry),
                           providers={provider_key(NODE, runtime): provider})
    return service, provider


def principal(name: str = "transport-test") -> Principal:
    return Principal(name=name, scopes=frozenset({"fleet:read"}), credential_kind="pat")


def with_real(body, **kwargs) -> None:
    with tempfile.TemporaryDirectory() as raw:
        node = RealConnector(Path(raw), **kwargs)
        try:
            service, provider = harness(Path(raw), node, runtime=kwargs.get("runtime", BASE_RUNTIME))
            body(service, node, provider, Path(raw))
        finally:
            node.stop()


class TheRealConnectorAnswersAndTheAnswerIsSigned(unittest.TestCase):
    def test_a_signed_round_trip_reaches_the_status_tools(self):
        def body(service, node, provider, tmp):
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertEqual(summary["route"]["state"], "VERIFIED", summary["route"])
            self.assertEqual(summary["device"]["state"], "MATCHED")
            self.assertEqual(summary["observations"]["cpu"]["value"]["used_percent"], 12.5)
            # WHICH account's Core this is, carried rather than assumed.
            self.assertEqual(summary["runtime"], BASE_RUNTIME)
            self.assertEqual(summary["account"], ACCOUNT)
            self.assertEqual(summary["source"], "node-core-route")

            listed = service.list_devices(principal())["devices"][0]
            self.assertEqual(listed["core_lite"]["route"]["state"], "VERIFIED")
            # The provider's health and the Core reading are separate answers.
            self.assertIsNotNone(listed["provider"])

        with_real(body)

    def test_the_status_surface_carries_the_reading(self):
        def body(service, node, provider, tmp):
            rows = service.status(principal())["core_lite"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["route"]["state"], "VERIFIED")
            self.assertEqual(rows[0]["runtime"], BASE_RUNTIME)

        with_real(body)

    def test_a_second_runtime_is_its_own_row_and_its_own_source(self):
        """Two accounts on one machine are two Cores, not one answer."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp, runtime=OTHER_RUNTIME)
            try:
                service, _ = harness(tmp, node, runtime=BASE_RUNTIME)
                # A second binding for the same device, different runtime.
                extra = ProviderBinding(
                    device_ref=NODE, runtime=OTHER_RUNTIME, kind="fleet-node",
                    endpoint=node.endpoint, credential_anchor=tmp / "node.secret",
                    account="other-account")
                service.config.providers[provider_key(NODE, OTHER_RUNTIME)] = extra
                service.providers[provider_key(NODE, OTHER_RUNTIME)] = FleetNodeProvider(extra)
                rows = {row["runtime"]: row for row in service.status(principal())["core_lite"]}
                self.assertIn(BASE_RUNTIME, rows)
                self.assertIn(OTHER_RUNTIME, rows)
                self.assertEqual(rows[OTHER_RUNTIME]["account"], "other-account")
            finally:
                node.stop()


class AnUnsignedOrUnboundAnswerIsNotThisMachinesCore(unittest.TestCase):
    """The review's sharpest case: a 200 from something that is not the device."""

    def _summary(self, tmp: Path, fake):
        service, _ = harness(tmp, fake)
        return service.core_lite_status(principal(), NODE, received_at=NOW)

    def test_a_plain_200_from_a_stranger_is_not_verified(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeServer()
            try:
                summary = self._summary(Path(raw), fake)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
                self.assertNotEqual(summary["connected"], "CONNECTED")
                self.assertEqual(summary["observations"], {})
            finally:
                fake.stop()

    def test_a_forged_signature_is_not_verified(self):
        with tempfile.TemporaryDirectory() as raw:
            fake = FakeServer(headers={node_protocol.CORE_SIGNATURE_HEADER: "00" * 32})
            try:
                summary = self._summary(Path(raw), fake)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
                self.assertEqual(summary["observations"], {})
            finally:
                fake.stop()

    def test_a_response_signed_over_a_different_nonce_is_refused(self):
        """A replayed answer from an earlier question.

        The signature is a REAL one, made with the real secret — over a nonce
        that is not this call's. That is precisely what a replay is, and it is
        why the nonce is inside the signed text rather than beside it.
        """
        with tempfile.TemporaryDirectory() as raw:
            body = json.dumps(core_document()).encode()
            forged = node_protocol.sign_core_response(
                SECRET, device_ref=NODE, runtime=BASE_RUNTIME,
                request_nonce="not-this-calls-nonce", body=body)
            fake = FakeServer(headers={node_protocol.CORE_SIGNATURE_HEADER: forged})
            try:
                summary = self._summary(Path(raw), fake)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
                self.assertEqual(summary["observations"], {})
            finally:
                fake.stop()

    def test_a_wrong_runtime_signature_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            body = json.dumps(core_document()).encode()
            forged = node_protocol.sign_core_response(
                SECRET, device_ref=NODE, runtime=OTHER_RUNTIME,
                request_nonce="x", body=body)
            fake = FakeServer(headers={node_protocol.CORE_SIGNATURE_HEADER: forged})
            try:
                summary = self._summary(Path(raw), fake)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
            finally:
                fake.stop()

    def test_a_tampered_body_under_a_valid_signature_is_refused(self):
        """The signature covers the BYTES, so swapping the document fails."""
        with tempfile.TemporaryDirectory() as raw:
            original = json.dumps(core_document()).encode()
            tampered = json.dumps(core_document(node="device:somewhere-else")).encode()
            signature = node_protocol.sign_core_response(
                SECRET, device_ref=NODE, runtime=BASE_RUNTIME,
                request_nonce="x", body=original)
            fake = FakeServer(body=json.loads(tampered),
                              headers={node_protocol.CORE_SIGNATURE_HEADER: signature})
            try:
                summary = self._summary(Path(raw), fake)
                self.assertNotEqual(summary["route"]["state"], "VERIFIED")
            finally:
                fake.stop()


class TheRealConnectorRefusesOnItsOwnTerms(unittest.TestCase):
    def test_an_unconfigured_core_source_says_so(self):
        def body(service, node, provider, tmp):
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertEqual(summary["route"]["state"], "NOT_CONFIGURED")
            self.assertEqual(summary["observations"], {})

        with_real(body, configure=False)

    def test_a_missing_snapshot_file_is_refused(self):
        def body(service, node, provider, tmp):
            node.snapshot.unlink()
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")
            self.assertEqual(summary["observations"], {})

        with_real(body)

    def test_a_symlinked_snapshot_is_refused(self):
        def body(service, node, provider, tmp):
            real = tmp / "elsewhere.json"
            real.write_bytes(node.snapshot.read_bytes())
            node.snapshot.unlink()
            node.snapshot.symlink_to(real)
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")

        with_real(body)

    def test_bad_json_is_refused(self):
        def body(service, node, provider, tmp):
            node.snapshot.write_bytes(b"{not json")
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")

        with_real(body)

    def test_a_snapshot_past_the_cap_is_refused(self):
        def body(service, node, provider, tmp):
            node.snapshot.write_bytes(b"{" + b" " * (node_connector.CORE_SNAPSHOT_MAX_BYTES + 8) + b"}")
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")

        with_real(body)

    def test_a_stale_document_is_not_fresh(self):
        def body(service, node, provider, tmp):
            stale = core_document()
            stale["observed_at"] = (NOW - dt.timedelta(hours=2)).isoformat()
            node.snapshot.write_text(json.dumps(stale), encoding="utf-8")
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertEqual(summary["freshness"]["state"], "STALE")

        with_real(body)

    def test_no_secret_means_the_device_is_never_asked(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp)
            try:
                service, provider = harness(tmp, node)
                provider.binding.credential_anchor.unlink()
                summary = service.core_lite_status(principal(), NODE, received_at=NOW)
                self.assertEqual(summary["route"]["state"], "UNAUTHENTICATED")
                self.assertEqual(summary["observations"], {})
            finally:
                node.stop()

    def test_a_badly_signed_request_does_not_reach_the_snapshot(self):
        """The signature is checked BEFORE the source is read.

        Proved by DELETING the snapshot: if the connector read it first, the
        answer would be about a missing file; because it refuses the peer first,
        the answer is about the peer.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp)
            try:
                node.snapshot.unlink()
                request = urllib.request.Request(
                    node.endpoint + "/v1/core/status",
                    headers={node_protocol.DEVICE_HEADER: NODE,
                             node_protocol.SIGNATURE_HEADER: "00" * 32,
                             node_protocol.TIMESTAMP_HEADER: str(int(NOW.timestamp())),
                             node_protocol.NONCE_HEADER: "n",
                             node_protocol.PROTOCOL_HEADER: node_protocol.PROTOCOL})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                body = json.loads(caught.exception.read().decode())
                self.assertEqual(caught.exception.code, 401)
                # The answer is about the PEER, not about the file: the snapshot
                # is missing and the connector never got far enough to notice.
                blob = json.dumps(body).upper()
                self.assertIn("SIGNATURE", blob)
                self.assertNotIn("SNAPSHOT", blob)
            finally:
                node.stop()


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

    def test_the_response_signature_is_domain_separated(self):
        """A request signature must not verify as a response one."""
        request_sig = node_protocol.sign(
            SECRET, method="GET", path="/v1/core/status", body=b"",
            device_ref=NODE)["X-Fleet-Signature"]
        self.assertFalse(node_protocol.verify_core_response(
            SECRET, device_ref=NODE, runtime=BASE_RUNTIME,
            request_nonce="", body=b"", signature=request_sig))

    def test_a_missing_signature_is_false_not_permissive(self):
        for absent in (None, "", "x"):
            with self.subTest(signature=absent):
                self.assertFalse(node_protocol.verify_core_response(
                    SECRET, device_ref=NODE, runtime=BASE_RUNTIME,
                    request_nonce="n", body=b"{}", signature=absent))


if __name__ == "__main__":
    unittest.main()
