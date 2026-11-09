"""`fleet_device_versions`: what each device's last update recorded, read safely.

The record is written by `device_kit.py update` / `update.ps1`. It reaches the
hub two ways: a node-channel device answers `GET /v1/device/versions`, signed
over the caller's nonce like the Core route; the hub's own device is read from
this account's state directory. Either way only the reviewed fields leave.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import device_versions, node_protocol, tools  # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding, provider_key)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.providers.fleet_node import FleetNodeProvider  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402
from fleet_mcp.runtime_identity import BASE_RUNTIME  # noqa: E402


def _transport():
    spec = importlib.util.spec_from_file_location(
        "core_lite_transport_helpers", ROOT / "tests/test_fleet_core_lite_transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


T = _transport()
NODE = T.NODE
HUB = "device:the-hub"


def record(device_ref: str = NODE, **extra) -> dict:
    doc = {
        "schema": device_versions.SCHEMA,
        "device": "node", "device_ref": device_ref,
        "recorded_at": "2026-11-05T06:00:00Z",
        "serveros_commit": "5425361acf88638d97b3e51890ff7072caefe0c0",
        "core_plugin": {"name": "hwz-server-os-linux-work", "version": "3.1.0+srv"},
        "ability_family": {"profile": "dell-gpu-compute", "revision": "e383bfe0"},
        "core_lite_collector": "serveros-node-resource-snapshot 0.1.0",
        "commander": None,
    }
    doc.update(extra)
    return doc


def write_record(path: Path, document: dict) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


def service_for(tmp: Path, node_endpoint: str, *, local_record: Path | None = None):
    """A hub whose own device is HUB, with NODE reached over the node channel."""
    anchor = tmp / "node.secret"
    anchor.write_text(T.SECRET, encoding="utf-8")
    registry = tmp / "registry.yaml"
    registry.write_text(
        "devices:\n"
        f"  - device_ref: {HUB}\n    display_name: hub\n    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n    ownership: company\n"
        f"  - device_ref: {NODE}\n    display_name: node\n    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n    ownership: company\n",
        encoding="utf-8")
    binding = ProviderBinding(device_ref=NODE, runtime=BASE_RUNTIME, kind="fleet-node",
                              endpoint=node_endpoint, credential_anchor=anchor)
    config = FleetConfig(
        source=None, local_device_ref=HUB,
        providers={provider_key(NODE, BASE_RUNTIME): binding},
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(),
        audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        device_versions_path=local_record or (tmp / "hub-has-no-record.json"))
    return FleetService(config, registry=load_registry(registry),
                        providers={provider_key(NODE, BASE_RUNTIME): FleetNodeProvider(binding)})


def principal() -> Principal:
    return Principal(name="versions-test", scopes=frozenset({"fleet:read"}), credential_kind="pat")


class TheRecordIsProjectedNotForwarded(unittest.TestCase):
    def test_the_reviewed_fields_pass(self):
        got = device_versions.project(record(), NODE)
        self.assertEqual(got["state"], "RECORDED")
        self.assertEqual(got["versions"]["ability_family"],
                         {"profile": "dell-gpu-compute", "revision": "e383bfe0"})

    def test_an_unknown_key_does_not_travel(self):
        got = device_versions.project(record(token="do-not-forward"), NODE)
        self.assertNotIn("token", json.dumps(got))

    def test_another_devices_record_is_refused(self):
        got = device_versions.project(record(device_ref="device:someone-else"), NODE)
        self.assertEqual(got["state"], "UNREADABLE")

    def test_a_document_of_another_kind_is_refused(self):
        self.assertEqual(device_versions.project({"schema": "x"}, NODE)["state"], "UNREADABLE")


class TheNodeRouteIsSignedAndNamed(unittest.TestCase):
    def test_the_route_is_declared(self):
        self.assertIn("GET /v1/device/versions", node_protocol.ROUTES)

    def test_a_signed_round_trip_reaches_the_tool(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = T.RealConnector(tmp)
            try:
                node.config.device_versions_path = write_record(tmp / "versions.json", record())
                service = service_for(tmp, node.endpoint)
                result = service.device_versions(principal())
            finally:
                node.stop()
        rows = {row["device_ref"]: row for row in result["devices"]}
        self.assertEqual(rows[NODE]["state"], "RECORDED", rows[NODE])
        self.assertEqual(rows[NODE]["versions"]["serveros_commit"][:7], "5425361")
        self.assertEqual(result["distinct_serveros_commits"],
                         ["5425361acf88638d97b3e51890ff7072caefe0c0"])
        # The hub's own device has no record here, and says so.
        self.assertEqual(rows[HUB]["state"], "NOT_RECORDED")
        self.assertEqual(rows[HUB]["route"], "local")

    def test_a_device_that_never_ran_an_update_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = T.RealConnector(tmp)
            try:
                node.config.device_versions_path = tmp / "absent.json"
                rows = {r["device_ref"]: r for r in
                        service_for(tmp, node.endpoint).device_versions(principal())["devices"]}
            finally:
                node.stop()
        self.assertEqual(rows[NODE]["state"], "NOT_RECORDED")

    def test_an_unsigned_answer_is_not_this_devices_record(self):
        """Something else on the URL answering 200 with a plausible record."""
        fake = T.FakeServer(body={"state": "RECORDED", "versions": {"serveros_commit": "forged"}})
        try:
            with tempfile.TemporaryDirectory() as raw:
                rows = {r["device_ref"]: r for r in
                        service_for(Path(raw), fake.endpoint).device_versions(principal())["devices"]}
        finally:
            fake.stop()
        self.assertEqual(rows[NODE]["state"], "UNREACHABLE")
        self.assertEqual(rows[NODE]["code"], "PROVIDER_UNAUTHORIZED")
        self.assertNotIn("forged", json.dumps(rows))


class TheHubsOwnDeviceIsReadLocally(unittest.TestCase):
    def test_the_hub_record_is_read_from_its_own_path(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            local = write_record(tmp / "hub.json", record(device_ref=HUB, commander="serveros-fleet@serveros-fleet"))
            fake = T.FakeServer()
            try:
                rows = {r["device_ref"]: r for r in service_for(
                    tmp, fake.endpoint, local_record=local).device_versions(principal())["devices"]}
            finally:
                fake.stop()
        self.assertEqual(rows[HUB]["state"], "RECORDED")
        self.assertEqual(rows[HUB]["versions"]["commander"], "serveros-fleet@serveros-fleet")


class TheToolIsServed(unittest.TestCase):
    def test_it_is_a_read_only_tool(self):
        tool = {t.name: t for t in tools.TOOLS}["fleet_device_versions"]
        self.assertTrue(tool.annotations.get("readOnlyHint"))
        self.assertEqual(tool.input_schema["properties"], {})


class KeyCenterNamesTravelWithTheVersions(unittest.TestCase):
    def test_names_only_and_states(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "secret-names.json"
            self.assertEqual(device_versions.secret_names(state)["state"], "NOT_SYNCED")
            state.write_text(json.dumps({"schema": "serveros/secret-names.v1",
                                         "names": ["GW0_TOKEN", "not a name"], "synced_at": "t",
                                         "repo_commit": "abc", "errors": [{"bundle": "x"}],
                                         "bundles": [{"names": ["GW0_TOKEN"]}]}))
            got = device_versions.secret_names(state)
        self.assertEqual(got, {"state": "SYNCED", "names": ["GW0_TOKEN"], "synced_at": "t",
                               "repo_commit": "abc", "errors": 1})


class TheAnsweringCodeIsNamed(unittest.TestCase):
    """Owner, 2026-11-09: devices recorded the new commit while their connector
    still ran an old tree; the versions answer now says which code answers."""

    def test_drift_compares_recorded_and_running(self):
        self.assertTrue(device_versions.drift({"versions": {"serveros_commit": "54ee8312aa"},
                                               "running": {"commit": "f794820"}}))
        self.assertFalse(device_versions.drift({"versions": {"serveros_commit": "54ee8312aa"},
                                                "running": {"commit": "54ee8312"}}))
        self.assertIsNone(device_versions.drift({"versions": {}, "running": {"commit": "x"}}))

    def test_this_process_names_its_code(self):
        running = device_versions.running_code()
        self.assertIn("tree", running)
        self.assertIn("commit", running)
