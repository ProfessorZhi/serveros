"""The real Windows shape: native Codex detail wrapped in a synthesized Claude ref."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from fleet_mcp.config import ProviderBinding
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.providers.fleet_node import FleetNodeProvider
from test_fleet_mcp_surface import Harness, TOKYO
from test_fleet_mcp_cloudcli_provider import FakeClient, provider as cloudcli
from fleet_mcp.session_detail import normalize_detail

DEVICE = "device:work-fj"
NATIVE = "codex-session-1"


def wrapper(*, family="codex"):
    return {"session": {"id": NATIVE, "provider": family, "title": "native work"},
            **SessionKey.compose(DEVICE, "claude", NATIVE).as_dict(),
            "classification": "MANAGED_IDLE", "running": False}


def node(payload, *, runtime="claude", family="claude"):
    binding = ProviderBinding(device_ref=DEVICE, kind="fleet-node",
                              endpoint="http://127.0.0.1:1",
                              credential_anchor=Path("/nonexistent/fixture-anchor"),
                              runtime=runtime, family=family,
                              multiplex_native_families=runtime == "claude" and family == "claude")
    provider = FleetNodeProvider(binding)
    provider._request = lambda *args, **kwargs: copy.deepcopy(payload)
    return provider


class DetailIdentity(unittest.TestCase):
    def test_native_codex_repairs_only_the_synthesized_outer_claude_address(self):
        key = SessionKey.compose(DEVICE, "codex", NATIVE)
        detail = node(wrapper()).get_session(key)
        self.assertEqual(detail["provider"], "codex")
        self.assertEqual(detail["session_ref"], key.ref)
        self.assertEqual(detail["session"]["provider"], "codex")

    def test_different_native_session_device_or_family_is_refused(self):
        key = SessionKey.compose(DEVICE, "codex", NATIVE)
        variants = []
        different_id = wrapper()
        different_id["session"]["id"] = "a-different-session"
        variants.append(different_id)
        different_device = wrapper()
        different_device["device_ref"] = "device:other"
        variants.append(different_device)
        variants.append(wrapper(family="claude"))
        other_account = wrapper()
        other_account.update(SessionKey.compose(DEVICE, "claude-other", NATIVE).as_dict())
        variants.append(other_account)
        for payload in variants:
            with self.subTest(payload=payload):
                with self.assertRaises(FleetRefusal) as caught:
                    node(payload).get_session(key)
                self.assertEqual(caught.exception.code, "BAD_SESSION_REF")

    def test_account_runtime_is_preserved_instead_of_replaced_with_family(self):
        key = SessionKey.compose(DEVICE, "claude-fjd", NATIVE)
        detail = node(wrapper(family="claude"), runtime="claude-fjd").get_session(key)
        self.assertEqual(detail["session_ref"], key.ref)
        self.assertEqual(detail["provider"], "claude-fjd")
        self.assertEqual(detail["session"]["provider"], "claude")

    def test_legacy_matching_wire_identity_remains_compatible(self):
        key = SessionKey.compose(DEVICE, "claude", NATIVE)
        payload = {**key.as_dict(), "title": "legacy", "classification": "MANAGED_IDLE"}
        detail = node(payload).get_session(key)
        self.assertEqual(detail["session_ref"], key.ref)
        self.assertEqual(detail["title"], "legacy")

    def test_flat_native_and_nested_complete_addresses_cannot_contradict_target(self):
        key = SessionKey.compose(DEVICE, "codex", NATIVE)
        variants = [{"id": "other", **key.as_dict()}]
        variants.extend([{**key.as_dict(), "session": None},
                         {**key.as_dict(), "session": []}])
        for name, value in (
            ("native_session_id", "other"), ("device_ref", "device:other"),
            ("session_ref", "device:work-fj|codex|another"),
            ("session_ref", "device:work-fj|claude-other|" + NATIVE),
        ):
            payload = wrapper()
            payload["session"][name] = value
            variants.append(payload)
        for payload in variants:
            with self.subTest(payload=payload):
                with self.assertRaises(FleetRefusal) as caught:
                    node(payload).get_session(key)
                self.assertEqual(caught.exception.code, "BAD_SESSION_REF")

    def test_adapter_cannot_answer_for_a_different_device_or_account(self):
        for key in (SessionKey.compose("device:other", "claude", NATIVE),
                    SessionKey.compose(DEVICE, "claude-other", NATIVE)):
            with self.subTest(key=key):
                with self.assertRaises(FleetRefusal) as caught:
                    node({**key.as_dict()}).get_session(key)
                self.assertEqual(caught.exception.code, "BAD_SESSION_REF")

    def test_service_rejects_a_wrong_address_from_any_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            key = SessionKey.compose(TOKYO, "claude", NATIVE)
            h.tokyo.get_session = lambda key: {
                **SessionKey.compose(DEVICE, "claude", NATIVE).as_dict()}
            with self.assertRaises(FleetRefusal) as caught:
                h.service.get_session(h.principal("fleet:read"), key)
            self.assertEqual(caught.exception.code, "BAD_SESSION_REF")

    def test_cloudcli_flat_family_evidence_survives_before_outer_key_attachment(self):
        upstream = cloudcli(FakeClient(responses={
            "session_get": {"id": NATIVE, "provider": "codex", "title": "native"}}))
        old_connector_key = SessionKey.compose(DEVICE, "claude", NATIVE)
        payload = upstream.get_session(old_connector_key)
        with self.assertRaises(FleetRefusal) as caught:
            normalize_detail(old_connector_key, payload, upstream.binding)
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")
        actual_key = SessionKey.compose(DEVICE, "codex", NATIVE)
        repaired = node(payload).get_session(actual_key)
        self.assertEqual(repaired["provider"], "codex")
        self.assertEqual(repaired["session"]["provider"], "codex")

    def test_cloudcli_flat_typed_identity_is_preserved_before_key_attachment(self):
        key = SessionKey.compose(DEVICE, "claude", NATIVE)
        for source in ({"native_session_id": "another"},
                       {"device_ref": "device:other"},
                       {"session_ref": "device:other|claude|another"}):
            with self.subTest(source=source):
                upstream = cloudcli(FakeClient(responses={"session_get": source}))
                result = upstream.get_session(key)
                with self.assertRaises(FleetRefusal) as caught:
                    normalize_detail(key, result, upstream.binding)
                self.assertEqual(caught.exception.code, "BAD_SESSION_REF")


if __name__ == "__main__":
    unittest.main()
