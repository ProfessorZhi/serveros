"""`CloudCliProvider` against the payload shapes the gateway actually returns.

Why this file exists
--------------------
Everything else about this surface is tested through a `FakeProvider` that
returns rows in this surface's own vocabulary. That is the right test for the
surface, and it is why the provider itself was untested — and why a real defect
survived: `session_get` nests the session object under `"session"` while
`sessions_list` returns the same fields flat, so a verdict computed from the
envelope found no `hostState`, fell through to MANAGED_IDLE, and disagreed with
the listing about the same session.

The row shapes below are the ones observed from the live gateway
(`claudecodeui-mcp-gateway` 0.1.0 on `127.0.0.1:3001`), trimmed to the fields
the provider reads. They are recorded here rather than paraphrased, because a
test written against an imagined shape would have passed.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from fleet_mcp.config import ProviderBinding
from fleet_mcp.errors import (
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNREACHABLE,
    SESSION_NOT_FOUND,
    FleetRefusal,
)
from fleet_mcp.identity import SessionKey
from fleet_mcp.mcp_http import ProviderRefusal
from fleet_mcp.providers.cloudcli import CloudCliProvider

DEVICE = "device:work-fj"
SESSION_ID = "ee6894aa-afb4-421b-bec1-fd445cfb04e4"

# One session, one host, in the two shapes the gateway uses for it.
LIST_ROW = {
    "id": SESSION_ID,
    "title": "qwen deploy",
    "provider": "claude",
    "projectId": "44235a69-cba3-4f71-b16a-4052c7d3f241",
    "lifecycleMode": "per-run",
    "hostState": "lingering",
    "running": False,
    "lastActivity": {"relative": "just now", "iso": "2026-10-09T18:02:05.834Z"},
}

DETAIL_PAYLOAD = {
    "session": {
        "id": SESSION_ID,
        "title": "qwen deploy",
        "provider": "claude",
        "projectId": "44235a69-cba3-4f71-b16a-4052c7d3f241",
        "lifecycleMode": "per-run",
        "hostState": "lingering",
        "running": False,
        "lastActivity": {"relative": "just now", "iso": "2026-10-09T18:02:05.834Z"},
    },
    "host": {"hostId": "host-2cca7a97", "mode": "per-run", "state": "lingering", "pid": None},
    "hostNote": None,
    "run": None,
}


class FakeClient:
    """A stand-in for `McpHttpClient` that records what it was asked."""

    def __init__(self, *, responses=None, raises=None):
        self.calls: list[tuple[str, dict]] = []
        self._responses = responses or {}
        self._raises = raises or {}

    def initialize(self):
        return {"protocolVersion": "2024-11-05",
                "serverInfo": {"name": "claudecodeui-mcp-gateway", "version": "0.1.0"}}

    def tools_list(self):
        return [{"name": "sessions_list", "annotations": {"readOnlyHint": True}},
                {"name": "session_send", "annotations": {"readOnlyHint": False}}]

    def tools_call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool in self._raises:
            raise self._raises[tool]
        return {"structuredContent": self._responses.get(tool, {})}


def provider(client: FakeClient, *, allow_send: bool = True) -> CloudCliProvider:
    binding = ProviderBinding(
        device_ref=DEVICE,
        kind="cloudcli-mcp-http",
        endpoint="http://127.0.0.1:3001/mcp",
        credential_anchor=Path("/nonexistent/pat"),
        allow_send=allow_send,
    )
    p = CloudCliProvider(binding)
    p._client = lambda: client  # type: ignore[method-assign]
    return p


class TheVerdictComesFromWhereTheFieldsAre(unittest.TestCase):
    def test_a_lingering_host_is_a_resident_host_when_fetched(self):
        """The regression. `session_get` nests; reading the envelope misses
        `hostState` and reports this session as idle while the listing calls it
        resident."""
        client = FakeClient(responses={"session_get": DETAIL_PAYLOAD})
        detail = provider(client).get_session(SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["classification"], "RESIDENT_HOST")
        self.assertTrue(detail["resident_host"])
        self.assertEqual(detail["evidence"]["provider.hostState"], "lingering")

    def test_the_listing_and_the_detail_agree_about_the_same_session(self):
        """The property that was broken, stated as the property rather than as
        two expected strings: a client that lists and then fetches must not be
        handed two answers."""
        rows = provider(FakeClient(responses={"sessions_list": {"sessions": [LIST_ROW]}})).list_sessions(limit=10)
        detail = provider(FakeClient(responses={"session_get": DETAIL_PAYLOAD})).get_session(rows[0].key)
        self.assertEqual(rows[0].classification, detail["classification"])
        self.assertEqual(rows[0].resident_host, detail["resident_host"])

    def test_a_flat_payload_is_still_classified(self):
        """Not every gateway version nests. A flat payload must keep working."""
        flat = dict(DETAIL_PAYLOAD)
        flat.pop("session")
        flat.update(DETAIL_PAYLOAD["session"])
        detail = provider(FakeClient(responses={"session_get": flat})).get_session(
            SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["classification"], "RESIDENT_HOST")

    def test_a_running_session_is_not_reported_as_resident(self):
        running = {"session": {**DETAIL_PAYLOAD["session"], "running": True, "hostState": "busy"}}
        detail = provider(FakeClient(responses={"session_get": running})).get_session(
            SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["classification"], "RUNNING_AGENT")
        self.assertFalse(detail["resident_host"])

    def test_a_transcript_on_disk_is_not_a_running_session(self):
        """The brief's explicit warning, as a test: a stored session with no run
        and no host is MANAGED_IDLE, never RUNNING_AGENT."""
        idle = {"session": {"id": SESSION_ID, "provider": "claude", "title": "old",
                            "projectId": "p", "lifecycleMode": "per-run"}}
        detail = provider(FakeClient(responses={"session_get": idle})).get_session(
            SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["classification"], "MANAGED_IDLE")
        self.assertFalse(detail["running"])


class TheIdentityIsAttachedNotDerived(unittest.TestCase):
    def test_the_detail_carries_the_device_qualified_reference(self):
        detail = provider(FakeClient(responses={"session_get": DETAIL_PAYLOAD})).get_session(
            SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["session_ref"], f"{DEVICE}|claude|{SESSION_ID}")
        self.assertEqual(detail["device_ref"], DEVICE)
        self.assertEqual(detail["native_session_id"], SESSION_ID)

    def test_the_providers_own_fields_survive_normalization(self):
        detail = provider(FakeClient(responses={"session_get": DETAIL_PAYLOAD})).get_session(
            SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(detail["host"]["state"], "lingering")
        self.assertEqual(detail["session"]["title"], "qwen deploy")


class TheCallsCarryTheNativeId(unittest.TestCase):
    """The gateway is spoken to in its own ids. The device-qualified ref is
    this surface's addressing, and must never be sent to the provider."""

    def _p(self):
        return provider(FakeClient(responses={
            "session_read": {"content": [], "cursor": None, "mode": "latest", "session": SESSION_ID},
            "session_send": {"runId": "run-7"},
            "run_get": {"id": "run-7", "state": "completed"},
        }))

    def test_a_read_sends_the_bare_native_id(self):
        client = FakeClient(responses={"session_read": {"content": [], "cursor": None}})
        provider(client).read_session(SessionKey.compose(DEVICE, "claude", SESSION_ID), limit=3)
        tool, arguments = client.calls[-1]
        self.assertEqual(tool, "session_read")
        self.assertEqual(arguments["session"], SESSION_ID)
        self.assertNotIn("|", arguments["session"])
        self.assertEqual(arguments["limit"], 3)

    def test_a_send_sends_the_bare_native_id_and_no_idempotency_key(self):
        """CloudCLI has no idempotency concept. Forwarding the surface's key
        would imply a guarantee this hop cannot make."""
        client = FakeClient(responses={"session_send": {"runId": "run-7"}})
        provider(client).send(SessionKey.compose(DEVICE, "claude", SESSION_ID), "hello",
                              idempotency_key="abc")
        tool, arguments = client.calls[-1]
        self.assertEqual(tool, "session_send")
        self.assertEqual(arguments, {"session": SESSION_ID, "message": "hello"})

    def test_a_run_lookup_pairs_the_run_with_its_session(self):
        client = FakeClient(responses={"run_get": {"id": "run-7"}})
        provider(client).get_run(SessionKey.compose(DEVICE, "claude", SESSION_ID), "run-7")
        tool, arguments = client.calls[-1]
        self.assertEqual(tool, "run_get")
        self.assertEqual(arguments["runId"], "run-7")
        self.assertEqual(arguments["session"], SESSION_ID)

    def test_an_unknown_state_filter_is_refused_before_the_call(self):
        """A state the gateway does not know would be silently dropped by it.
        Refusing here says which states exist."""
        client = FakeClient(responses={"sessions_list": {"sessions": []}})
        with self.assertRaises(FleetRefusal):
            provider(client).list_sessions(limit=5, state="finished")
        self.assertEqual(client.calls, [])


class ProviderFailuresKeepTheirMeaning(unittest.TestCase):
    def test_unauthorized_stays_unauthorized(self):
        client = FakeClient(raises={"sessions_list": ProviderRefusal(PROVIDER_UNAUTHORIZED, "401 from gateway")})
        with self.assertRaises(FleetRefusal) as caught:
            provider(client).list_sessions(limit=5)
        self.assertEqual(caught.exception.code, PROVIDER_UNAUTHORIZED)

    def test_unreachable_is_not_reported_as_a_missing_session(self):
        """A device that cannot be asked and a session that is not there send an
        operator to different places."""
        client = FakeClient(raises={"session_get": ProviderRefusal(PROVIDER_UNREACHABLE, "connection refused")})
        with self.assertRaises(FleetRefusal) as caught:
            provider(client).get_session(SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(caught.exception.code, PROVIDER_UNREACHABLE)

    def test_a_404_during_a_session_read_is_a_missing_session(self):
        client = FakeClient(raises={"session_get": ProviderRefusal("BAD_REQUEST", "404", status=404)})
        with self.assertRaises(FleetRefusal) as caught:
            provider(client).get_session(SessionKey.compose(DEVICE, "claude", SESSION_ID))
        self.assertEqual(caught.exception.code, SESSION_NOT_FOUND)

    def test_a_timeout_is_reported_as_a_timeout(self):
        client = FakeClient(raises={"sessions_list": ProviderRefusal(PROVIDER_TIMEOUT, "no answer in 45s")})
        with self.assertRaises(FleetRefusal) as caught:
            provider(client).list_sessions(limit=5)
        self.assertEqual(caught.exception.code, PROVIDER_TIMEOUT)


class HealthReadsTheGatewaysOwnIdentification(unittest.TestCase):
    def test_health_reports_the_gateway_name_and_tool_count(self):
        health = provider(FakeClient()).health()
        self.assertTrue(health.reachable)
        self.assertTrue(health.authorized)
        self.assertEqual(health.server_name, "claudecodeui-mcp-gateway")
        self.assertEqual(health.server_version, "0.1.0")
        self.assertEqual(health.tool_count, 2)

    def test_annotations_are_passed_through_rather_than_reinterpreted(self):
        """CloudCLI's read/write split is CloudCLI's fact. This surface does not
        have a second opinion about which of its verbs mutate."""
        annotations = provider(FakeClient()).tool_annotations()
        self.assertEqual(annotations["sessions_list"], {"readOnlyHint": True})
        self.assertEqual(annotations["session_send"], {"readOnlyHint": False})

    def test_an_unreachable_gateway_is_reachable_false_rather_than_an_exception(self):
        client = FakeClient(raises={"sessions_list": ProviderRefusal(PROVIDER_UNREACHABLE, "refused")})
        client.initialize = lambda: (_ for _ in ()).throw(ProviderRefusal(PROVIDER_UNREACHABLE, "refused"))
        health = provider(client).health()
        self.assertFalse(health.reachable)
        self.assertEqual(health.error_code, PROVIDER_UNREACHABLE)


if __name__ == "__main__":
    unittest.main()
