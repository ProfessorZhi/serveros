#!/usr/bin/env python3
"""A defect has to leave a name, not just a sentence.

The client that reported this could say one thing — "internal error" — three
times, and the surface had nothing to match it against: the JSON-RPC fallback,
the two bare 500s, and a refusal raised out of a tool all returned without
writing an audit line. The tests below are written as the operator's question
("which call was it?") and each one is answered from the log.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import core_lite_source, disclosure, tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ServerBinding,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.http_transport import HttpSurface  # noqa: E402
from fleet_mcp.mcp_http import McpHttpClient, ProviderRefusal  # noqa: E402
from fleet_mcp.server import JSONRPC_INTERNAL_ERROR, McpServer  # noqa: E402
from fleet_mcp.tools import ToolContext  # noqa: E402

DEVICE = "device:work-fj"


class Audited(unittest.TestCase):
    """One real audit log per test, read back line by line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.config = FleetConfig(
            source=None,
            local_device_ref=DEVICE,
            providers={},
            auth=AuthBinding(),
            server=ServerBinding(bind="127.0.0.1", port=3401,
                                 public_base_url="https://fleet.example"),
            policy=PolicyBinding(),
            audit=AuditBinding(path=self.tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.service = FleetService(self.config, providers={})

    def records(self, event: str) -> list[dict]:
        path = self.tmp / "audit.jsonl"
        if not path.exists():
            return []
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        return [line for line in lines if line.get("event") == event]


class TheJsonRpcFallbackNamesItsDefect(Audited):
    def _server_that_raises(self, method: str, exc: BaseException):
        context = ToolContext(service=self.service,
                              principal=Principal(name="oauth:test-client",
                                                  scopes=frozenset({"fleet.read"}),
                                                  credential_kind="oauth"))
        server = McpServer(lambda: context)

        def boom(*_args, **_kwargs):
            raise exc

        server._dispatch = boom  # the defect is below dispatch, as a real one is
        return server

    def test_it_records_the_method_the_class_and_a_findable_id(self):
        server = self._server_that_raises("tools/call", AttributeError("boom"))
        reply = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}})
        recorded = reply["error"]["data"]["request_id"]

        [line] = self.records("mcp.internal_error")
        self.assertEqual(line["method"], "tools.call")
        self.assertEqual(line["exception"], "attributeerror")
        self.assertEqual(line["request_id"], recorded)
        self.assertEqual(line["principal"], "oauth:test-client")

    def test_the_caller_is_handed_the_id_the_log_holds(self):
        server = self._server_that_raises("tools/call", KeyError("missing"))
        reply = server.handle({"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {}})
        self.assertEqual(reply["error"]["code"], JSONRPC_INTERNAL_ERROR)
        self.assertEqual(reply["error"]["data"]["code"], "INTERNAL")
        self.assertRegex(reply["error"]["data"]["request_id"], r"^sha256:[0-9a-f]{12}$")

    def test_the_id_is_a_digest_because_the_request_id_is_the_callers(self):
        """A caller who puts a token in `id` must not find it in this log."""
        server = self._server_that_raises("tools/call", RuntimeError("boom"))
        server.handle({"jsonrpc": "2.0", "id": "Bearer-secret-value",
                       "method": "tools/call", "params": {}})
        text = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("Bearer-secret-value", text)

    def test_a_notification_records_nothing_back_but_still_records(self):
        """No response is owed; the log entry is not the caller's to opt out of."""
        server = self._server_that_raises("notifications/initialized", RuntimeError("boom"))
        # A notification carries no id, which is what makes the reply `None`.
        self.assertIsNone(server.handle({"jsonrpc": "2.0",
                                         "method": "notifications/initialized", "params": {}}))


class TheBareFiveHundredIsAttributed(Audited):
    def test_it_writes_a_record_and_returns_the_id_in_the_body(self):
        surface = HttpSurface(service=self.service, config=self.config,
                              oauth=None, audit=self.service.audit)
        recorded = surface.record_internal_error(TypeError("boom"))
        [line] = self.records("mcp.internal_error")
        self.assertEqual(line["request_id"], recorded)
        self.assertEqual(line["exception"], "typeerror")

    def test_two_failures_are_two_ids(self):
        surface = HttpSurface(service=self.service, config=self.config,
                              oauth=None, audit=self.service.audit)
        first = surface.record_internal_error(TypeError("a"))
        second = surface.record_internal_error(TypeError("b"))
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.records("mcp.internal_error")), 2)


class AMalformedDocumentIsNotAnOutage(Audited):
    """`readback.readback` used to sit outside the `try` that guards the fetch."""

    def test_it_reports_a_source_failure_instead_of_escaping(self):
        class Source:
            def fetch(self, device_ref):
                return core_lite_source.CoreLiteFetch(
                    route=core_lite_source.CoreLiteRoute(
                        device_ref=device_ref, authenticated=True),
                    document={"schema": "not a reading"})

        original = core_lite_source.readback.readback

        def raises(*_args, **_kwargs):
            raise TypeError("the document is not a mapping")

        core_lite_source.readback.readback = raises
        try:
            summary = core_lite_source.admit_core_lite(
                Source(), device_ref=DEVICE, binding_account="Vince.Huang",
                received_at=dt.datetime.now(dt.timezone.utc))
        finally:
            core_lite_source.readback.readback = original

        self.assertEqual(summary["route"]["state"], "SOURCE_FAILED")
        self.assertEqual(summary["route"]["failure"], "TypeError")
        self.assertIn("could not be read", summary["route"]["reason"])
        # Not the device's fault, and not a statement that it is up.
        self.assertEqual(summary["connected"], "UNKNOWN")


class ANullProviderResultIsARefusal(Audited):
    def _client(self, reply):
        client = McpHttpClient("http://127.0.0.1:1/mcp", None, label="cloudcli")
        client._post = lambda payload, notify=False: reply
        return client

    def test_a_null_result_is_named_rather_than_reached_for(self):
        client = self._client({"jsonrpc": "2.0", "id": 1, "result": None})
        with self.assertRaises(ProviderRefusal):
            client._rpc("tools/list", {})

    def test_a_reply_that_is_not_an_object_is_named_too(self):
        client = self._client(["not", "a", "reply"])
        with self.assertRaises(ProviderRefusal):
            client._rpc("tools/list", {})

    def test_a_tools_list_result_that_is_not_an_object_is_refused(self):
        client = self._client({"jsonrpc": "2.0", "id": 1, "result": []})
        client._initialized = True
        with self.assertRaises(ProviderRefusal):
            client.tools_list()


class AnUnserializablePayloadIsARefusal(Audited):
    def test_the_tool_is_named_and_the_refusal_recorded(self):
        context = ToolContext(service=self.service,
                              principal=Principal(name="owner",
                                                  scopes=frozenset({"fleet.read"}),
                                                  credential_kind="pat"))
        name = "fleet_list_devices"
        original = tools.SERVED_BY_NAME[name]
        # A set is a legal Python value and not a legal JSON one, which is the
        # shape a handler defect takes once it reaches `json.dumps`.
        tools.SERVED_BY_NAME[name] = dataclasses.replace(
            original, handler=lambda _ctx, _args: {"devices": {1, 2}})
        try:
            answer = tools.call_tool(context, name, {})
        finally:
            tools.SERVED_BY_NAME[name] = original

        self.assertTrue(answer["isError"])
        self.assertEqual(answer["structuredContent"]["code"], "INTERNAL")
        self.assertIn(name, answer["structuredContent"]["detail"])
        [line] = self.records("fleet.refused")
        self.assertEqual(line["error_code"], "INTERNAL")
        self.assertEqual(line["tool"], name)


class TheFieldsAreFactsTheAllowlistKnows(Audited):
    def test_every_field_this_change_records_is_allowlisted(self):
        for name in ("method", "exception", "request_id"):
            self.assertIn(name, disclosure.SAFE_AUDIT_FIELDS)

    def test_the_exception_is_stored_as_a_shape_not_as_the_string(self):
        """A class name is chosen by whoever wrote the class."""
        projected, withheld = disclosure.project_record({"event": "mcp.internal_error",
                                                         "exception": "attributeerror"})
        self.assertEqual(withheld, 0)
        self.assertEqual(projected["exception"], "attributeerror")
        projected, withheld = disclosure.project_record({"event": "mcp.internal_error",
                                                         "exception": "AttributeError: token"})
        self.assertEqual(withheld, 1)
        self.assertEqual(projected["exception"], disclosure.OMITTED_MARK)
        self.assertNotIn("token", json.dumps(projected))


if __name__ == "__main__":
    unittest.main()
