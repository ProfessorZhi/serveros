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
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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
from fleet_mcp.errors import FleetRefusal  # noqa: E402
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
            # 0600, because that is what a Core snapshot is: this account's
            # own. The reader refuses a group- or world-writable one, and the
            # fixture must not be looser than the thing it stands for.
            self.snapshot.chmod(0o600)
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
            # The SAME shape as `fleet_status`: a list, one entry per bound
            # runtime. Two shapes for one fact is how a reader picks the wrong
            # one, and a two-account machine would have shown one Core here and
            # two there.
            self.assertEqual(len(listed["core_lite"]), 1)
            self.assertEqual(listed["core_lite"][0]["route"]["state"], "VERIFIED")
            self.assertEqual(listed["core_lite"][0]["runtime"], BASE_RUNTIME)
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

    def test_both_tools_agree_on_a_two_runtime_machine(self):
        """`list_devices` and `status` must not disagree about how many Cores.

        They did: `status` reported per runtime while `list_devices` reported
        only the base one, so a machine serving two accounts showed two rows in
        one tool and one in the other. The disagreement is the defect — either
        shape could look right on its own.
        """
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp, runtime=OTHER_RUNTIME)
            try:
                service, _ = harness(tmp, node, runtime=BASE_RUNTIME)
                extra = ProviderBinding(
                    device_ref=NODE, runtime=OTHER_RUNTIME, kind="fleet-node",
                    endpoint=node.endpoint, credential_anchor=tmp / "node.secret",
                    account="other-account")
                service.config.providers[provider_key(NODE, OTHER_RUNTIME)] = extra
                service.providers[provider_key(NODE, OTHER_RUNTIME)] = FleetNodeProvider(extra)

                row = service.list_devices(principal())["devices"][0]
                listed = sorted(entry["runtime"] for entry in row["core_lite"])
                stated = sorted(entry["runtime"] for entry in
                                service.status(principal())["core_lite"])
                self.assertEqual(listed, [BASE_RUNTIME, OTHER_RUNTIME])
                self.assertEqual(listed, stated)
            finally:
                node.stop()

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
            node.snapshot.write_bytes(b"{not json"); node.snapshot.chmod(0o600)
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")

        with_real(body)

    def test_a_snapshot_past_the_cap_is_refused(self):
        def body(service, node, provider, tmp):
            node.snapshot.write_bytes(b"{" + b" " * (node_connector.CORE_SNAPSHOT_MAX_BYTES + 8) + b"}"); node.snapshot.chmod(0o600)
            summary = service.core_lite_status(principal(), NODE, received_at=NOW)
            self.assertNotEqual(summary["route"]["state"], "VERIFIED")

        with_real(body)

    def test_a_stale_document_is_not_fresh(self):
        def body(service, node, provider, tmp):
            stale = core_document()
            stale["observed_at"] = (NOW - dt.timedelta(hours=2)).isoformat()
            node.snapshot.write_text(json.dumps(stale), encoding="utf-8")
            node.snapshot.chmod(0o600)
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
        self.assertEqual(len(tools_mod.SERVED_TOOLS), 34)
        self.assertEqual(len(scopes.ALLOWED_SCOPES), 6)
        # `fleet-mcp-5` is the 23rd mirrored verb, not a core-lite tool: this
        # module still adds none, which is what the name below checks.
        self.assertEqual(TOOL_SURFACE_REVISION, "fleet-mcp-5")
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


class TheSnapshotReadIsBoundedAndVerified(unittest.TestCase):
    """The reader's own guarantees, tested at the reader.

    Every fixture here lives in a fresh temporary directory. Nothing in this
    class opens a real snapshot, a real home, or any path it did not create —
    the point is the READER's behaviour, and reaching outside the test's own
    directory to demonstrate it would be the same mistake in the test.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def snapshot(self, payload: bytes = b'{"ok": true}', *, mode: int = 0o600) -> Path:
        path = self.tmp / "core-status.json"
        path.write_bytes(payload)
        path.chmod(mode)
        return path

    def test_a_normal_snapshot_is_read(self):
        path = self.snapshot()
        self.assertEqual(node_connector._read_bounded_no_follow(path, 1024),
                         b'{"ok": true}')

    def test_at_most_one_byte_past_the_cap_is_ever_read(self):
        """A spy on `os.read`, because "bounded" is a claim about the KERNEL
        calls, not about the length of the list afterwards.

        The file is well under the cap and the cap is small, so the assertion is
        about the requested size: no call may ask for more than cap + 1, which is
        what makes an oversized file impossible to load on the way to refusing it.
        """
        cap = 16
        path = self.snapshot(b"x" * (cap * 4))
        seen: list[int] = []
        real_read = os.read

        def spy(fd, size):
            seen.append(size)
            return real_read(fd, size)

        with mock.patch.object(os, "read", spy):
            with self.assertRaises(FleetRefusal):
                node_connector._read_bounded_no_follow(path, cap)
        self.assertTrue(seen)
        self.assertLessEqual(max(seen), cap + 1, seen)
        self.assertLessEqual(sum(seen), cap + 1, seen)

    def test_a_sparse_oversized_snapshot_is_refused(self):
        """A file whose SIZE is large and whose content is not.

        `read_bytes()` would allocate the whole apparent length before the
        comparison. The bounded loop cannot: it stops one byte past the cap
        whatever the file claims to be.
        """
        cap = 1024
        path = self.tmp / "sparse.json"
        with open(path, "wb") as handle:
            handle.truncate(8 * 1024 * 1024)      # sparse: 8 MiB of nothing
        path.chmod(0o600)
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(path, cap)
        self.assertIn("past the", caught.exception.detail)

    def test_a_symlinked_snapshot_is_refused(self):
        real = self.snapshot()
        link = self.tmp / "link.json"
        link.symlink_to(real)
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(link, 1024)
        self.assertIn("symlink", caught.exception.detail.lower())

    def test_a_symlinked_ancestor_directory_is_refused(self):
        """`O_NOFOLLOW` covers the last component only, so this is the case it
        does NOT catch — and the one the resolved-path comparison exists for."""
        real_dir = self.tmp / "real"
        real_dir.mkdir()
        target = real_dir / "core-status.json"
        target.write_bytes(b'{"ok": true}')
        target.chmod(0o600)
        linked_dir = self.tmp / "linked"
        linked_dir.symlink_to(real_dir)
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(linked_dir / "core-status.json", 1024)
        self.assertIn("resolve to itself", caught.exception.detail)

    def test_a_non_regular_file_is_refused(self):
        fifo = self.tmp / "pipe"
        os.mkfifo(fifo)
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(fifo, 1024)
        self.assertIn("regular file", caught.exception.detail)

    def test_a_snapshot_owned_by_another_account_is_refused(self):
        """The owner check, driven without needing a second account.

        `geteuid` is the thing being compared against, so it is what is moved —
        which is also the honest way to test it: the reader compares the FILE's
        uid with the PROCESS's, and this makes those differ.
        """
        path = self.snapshot()
        with mock.patch.object(os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(FleetRefusal) as caught:
                node_connector._read_bounded_no_follow(path, 1024)
        self.assertIn("different OS account", caught.exception.detail)

    def test_a_group_or_world_writable_snapshot_is_refused(self):
        for mode in (0o660, 0o664, 0o666, 0o606, 0o622):
            with self.subTest(mode=oct(mode)):
                path = self.snapshot(mode=mode)
                with self.assertRaises(FleetRefusal) as caught:
                    node_connector._read_bounded_no_follow(path, 1024)
                self.assertIn("group or other", caught.exception.detail)

    def test_a_missing_snapshot_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(self.tmp / "absent.json", 1024)
        self.assertIn("no Core snapshot", caught.exception.detail)

    def test_an_atomic_replacement_is_read_as_the_new_file(self):
        """The write the producer actually does: rename over the path.

        `os.replace` is atomic, so a reader either sees the old file or the new
        one and never a half-written one. Reading the SAME path twice around the
        swap must yield the two documents, which is what "a normal atomic
        replacement reads cleanly" means.
        """
        path = self.snapshot(b'{"generation": 1}')
        self.assertEqual(json.loads(node_connector._read_bounded_no_follow(path, 1024)),
                         {"generation": 1})
        pending = self.tmp / "core-status.json.new"
        pending.write_bytes(b'{"generation": 2}')
        pending.chmod(0o600)
        os.replace(pending, path)
        self.assertEqual(json.loads(node_connector._read_bounded_no_follow(path, 1024)),
                         {"generation": 2})

    def test_the_refusals_do_not_echo_the_path_or_the_content(self):
        """A message is a surface. It names the KIND of failure and stops."""
        secret = b'{"secret": "do-not-echo-me"}'
        path = self.snapshot(secret, mode=0o666)
        with self.assertRaises(FleetRefusal) as caught:
            node_connector._read_bounded_no_follow(path, 1024)
        self.assertNotIn(str(path), caught.exception.detail)
        self.assertNotIn("do-not-echo-me", caught.exception.detail)

    def test_a_platform_without_o_nofollow_is_refused_by_name(self):
        """No silent weakening: the route refuses rather than reading unverified."""
        path = self.snapshot()
        with mock.patch.object(os, "O_NOFOLLOW", None, create=True):
            with self.assertRaises(FleetRefusal) as caught:
                node_connector._read_bounded_no_follow(path, 1024)
        self.assertEqual(caught.exception.code, "UNSUPPORTED")


class TheCoreProjectionIsNotAnAbilityRegistry(unittest.TestCase):
    """The bridge contract between Fleet and G1, asserted from Fleet's side.

    `FLEET-V1-TOPOLOGY-OPERATIONS.md` §2 puts Device Ability (G1) at "the actual
    Skill/Plugin/MCP/CLI/Provider implementations and their source revisions",
    with one express anti-goal: it must not become *a second registry copied per
    device*. G1 owns that projection and this lane does not edit it.

    The risk this test exists for is the quiet one: Fleet already carries a
    per-device block (`core_lite`) in two of its 34 tools, so the cheapest way to
    "support Device Ability" would be to grow that block until it answered
    questions G1 owns. Then two surfaces would answer "what can this device do",
    and the one that is not the authority would be the one somebody read.

    So the block's shape is CLOSED here, and the keys that would make it an
    Ability answer are named and forbidden explicitly rather than left to the
    subset check alone -- a subset of a set that quietly grew would pass.
    """

    #: Every key this lane's per-device Core summary is allowed to carry. Adding
    #: one is a contract decision, and this list is where it is written down.
    ALLOWED_SUMMARY_KEYS = frozenset({
        "schema", "device_ref", "device", "principal", "connected", "freshness",
        "integrity", "live", "observations", "observations_unavailable",
        "refusals", "route", "source", "runtime", "account",
    })

    #: Anything shaped like a Device Ability answer. None of these may appear in
    #: Fleet's Core block: G1 is the authority for all of them.
    FORBIDDEN_KEYS = frozenset({
        "abilities", "ability", "effective_ability", "effective_abilities",
        "skills", "plugins", "providers_installed", "installed_abilities",
    })

    def _summaries(self, service):
        rows = []
        for entry in service.list_devices(principal())["devices"]:
            rows.extend(entry["core_lite"])
        rows.extend(service.status(principal())["core_lite"])
        return rows

    def test_the_core_block_carries_no_ability_shaped_key(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp)
            try:
                service, _ = harness(tmp, node)
                rows = self._summaries(service)
                self.assertTrue(rows, "no summary was produced, so this proves nothing")
                for row in rows:
                    keys = set(row)
                    with self.subTest(keys=sorted(keys)):
                        self.assertLessEqual(keys, self.ALLOWED_SUMMARY_KEYS,
                                             f"undeclared key(s): {sorted(keys - self.ALLOWED_SUMMARY_KEYS)}")
                        self.assertEqual(keys & self.FORBIDDEN_KEYS, set(),
                                         "Fleet's Core block must not answer a "
                                         "Device Ability question; G1 owns that")
            finally:
                node.stop()

    def test_no_core_observation_claims_a_capability(self):
        """`observations` is CPU/memory/PSI/gpu/services -- measurements, not a
        list of what the machine can do. An observation named like an ability
        would be the same substitution one level down."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = RealConnector(tmp)
            try:
                service, _ = harness(tmp, node)
                for row in self._summaries(service):
                    for name in row.get("observations") or {}:
                        with self.subTest(observation=name):
                            self.assertNotIn(name.lower(), self.FORBIDDEN_KEYS)
            finally:
                node.stop()

    def test_fleet_exposes_no_device_ability_tool(self):
        """The catalogue is 34 and none of them is an Ability projection."""
        names = [tool.name for tool in tools_mod.SERVED_TOOLS]
        self.assertEqual(len(names), 34)
        # TOKENS, not substrings: `fleet_cloudcli_native_capability` is the
        # CloudCLI capability read and contains "ability" inside "capability".
        # A substring check would fail on the one word it must not confuse, and
        # the fix for that is to say what is meant rather than to drop the test.
        words = {word for name in names for word in name.split("_")}
        self.assertNotIn("ability", words)
        self.assertNotIn("abilities", words)
        self.assertNotIn("effective", words)
        self.assertIn("capability", words)   # the two reads are still there
