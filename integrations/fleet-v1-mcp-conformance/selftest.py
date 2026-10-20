#!/usr/bin/env python3
"""The negative half of the Fleet MCP conformance story, run in-process.

`run.py` proves the surface answers a well-formed client. This file proves it
refuses the calls a well-formed client is not entitled to make, and that a
refusal is a typed answer rather than a traceback. Both run against the same
fake device and neither opens a socket, reads a credential or contacts a real
device: the mode is FAKE_DEVICE throughout.

Run it directly:

    python3 integrations/fleet-v1-mcp-conformance/selftest.py

Exit status is 0 only when every check passes. A failing check is a real
contract failure, not a warning: this is the file a release candidate runs when
it wants to say "the negative cases were exercised", and a suite that passed by
skipping them would be saying something it did not do.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import authority_matrix as matrix  # noqa: E402
import run as conformance  # noqa: E402
from fleet_mcp import tools as tools_mod  # noqa: E402
from fleet_mcp.fleet import Principal  # noqa: E402
from fleet_mcp.scopes import (ALLOWED_SCOPES, DEFAULT_GRANTABLE_SCOPES,  # noqa: E402
                              IMPLEMENTED_SCOPES, SCOPE_CONTROL, SCOPE_CREATE,
                              SCOPE_READ, SCOPE_SEND)
from fleet_mcp.server import (JSONRPC_METHOD_NOT_FOUND,  # noqa: E402
                              McpServer)


class Harness:
    """One in-process surface over one fake device, built like `run.py`."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fleet-conformance-")
        self.tmp = Path(self._tmp.name)
        self.service, self.devices = conformance.build(
            self.tmp,
            grants={conformance.PRINCIPAL: {
                conformance.DEVICE: ("send", "create", "control")}},
            create_allow={conformance.PROJECT: conformance.CreateGrant()},
            send_allow=(conformance.SESSION_REF,),
        )
        self.device = self.devices[conformance.DEVICE]

    def close(self) -> None:
        self._tmp.cleanup()

    def server(self, scopes, name: str = conformance.PRINCIPAL) -> McpServer:
        principal = Principal(name=name, scopes=frozenset(scopes),
                              credential_kind="oauth")
        return McpServer(lambda: tools_mod.ToolContext(service=self.service,
                                                       principal=principal))

    def call(self, server: McpServer, name: str, arguments):
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": name, "arguments": arguments}})

    @staticmethod
    def refusal(payload) -> str:
        """The typed code a refusal carries, or a marker that it was none."""
        if payload is None:
            return "NO_RESPONSE"
        if "error" in payload:
            return f"RPC_{payload['error'].get('code')}"
        result = payload.get("result", {})
        if not result.get("isError"):
            return "OK"
        return result.get("structuredContent", {}).get("code", "UNKNOWN")


class TheTableCannotDriftFromTheCatalogue(unittest.TestCase):
    def test_every_served_tool_has_exactly_one_row(self):
        served = sorted(tools_mod.SERVED_BY_NAME)
        declared = sorted(matrix.BY_NAME)
        self.assertEqual(declared, served)
        self.assertEqual(len(matrix.AUTHORITY), 33)

    def test_every_tool_is_named_in_the_human_matrix(self):
        text = (HERE / "AUTH-MATRIX.md").read_text(encoding="utf-8")
        missing = [row.name for row in matrix.AUTHORITY if row.name not in text]
        self.assertEqual(missing, [], "AUTH-MATRIX.md is missing these tools")

    def test_the_six_advertised_scopes_and_the_two_default_grantable_are_three_facts(self):
        # The set is the contract; the tuple order is `scopes.py`'s own.
        self.assertEqual(set(ALLOWED_SCOPES), set(matrix.SIX_SCOPES))
        self.assertEqual(set(IMPLEMENTED_SCOPES), set(matrix.SIX_SCOPES))
        self.assertEqual(tuple(DEFAULT_GRANTABLE_SCOPES), matrix.DEFAULT_GRANTABLE)
        self.assertEqual(len(matrix.OWNER_PAT), 3)
        self.assertNotIn(SCOPE_CONTROL, matrix.OWNER_PAT)

    def test_the_default_grant_admits_23_and_denies_10(self):
        admitted = matrix.scope_admitted(matrix.DEFAULT_GRANTABLE)
        denied = matrix.scope_denied(matrix.DEFAULT_GRANTABLE)
        self.assertEqual(len(admitted), 23, admitted)
        self.assertEqual(len(denied), 10, denied)
        self.assertEqual(
            set(denied),
            {
                "fleet_create_session", "fleet_interrupt_session",
                "fleet_cloudcli_session_create", "fleet_cloudcli_session_interrupt",
                "fleet_cloudcli_session_start", "fleet_cloudcli_session_close",
                "fleet_cloudcli_session_cancel_queued",
                "fleet_cloudcli_session_reconfigure",
                "fleet_cloudcli_ui_open_session", "fleet_cloudcli_approval_answer",
            },
        )

    def test_the_owner_pat_admits_25_and_still_cannot_control_navigate_or_approve(self):
        admitted = matrix.scope_admitted(matrix.OWNER_PAT)
        denied = set(matrix.scope_denied(matrix.OWNER_PAT))
        self.assertEqual(len(admitted), 25, admitted)
        self.assertEqual(len(denied), 8, sorted(denied))
        self.assertIn("fleet_cloudcli_ui_open_session", denied)
        self.assertIn("fleet_cloudcli_approval_answer", denied)
        self.assertIn("fleet_cloudcli_session_interrupt", denied)

    def test_the_manifest_is_the_only_tool_callable_with_no_scope(self):
        zero = [row.name for row in matrix.AUTHORITY
                if row.scope_admits(frozenset())]
        self.assertEqual(zero, ["fleet_cloudcli_native_manifest"])

    def test_the_scope_admission_count_is_monotonic_across_the_three_ceilings(self):
        default = set(matrix.scope_admitted(matrix.DEFAULT_GRANTABLE))
        owner = set(matrix.scope_admitted(matrix.OWNER_PAT))
        all_six = set(matrix.scope_admitted(matrix.SIX_SCOPES))
        self.assertLess(default, owner)
        self.assertEqual(len(all_six), 33)


class StatusIsAReadAndMustBeGatedAsOne(unittest.TestCase):
    """`fleet/owner-grant-3devices.md` names `fleet:read` for `fleet_status`.

    The implementation used to call `admit()` and nothing else, so a credential
    with no scope — or with only `fleet:send` — could read the enrolled-device
    inventory, every route's health, `policy.send_allow` and the audit tail.
    The documented contract and the code disagreed; these checks pin the
    document, not the old code.
    """

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_zero_scope_cannot_read_status(self):
        self.assertEqual(
            self.h.refusal(self.h.call(self.h.server(set()), "fleet_status", {})),
            "SCOPE_DENIED")

    def test_send_only_cannot_read_status(self):
        self.assertEqual(
            self.h.refusal(self.h.call(self.h.server({SCOPE_SEND}), "fleet_status", {})),
            "SCOPE_DENIED")

    def test_read_can_read_status_and_gets_the_audit_tail(self):
        result = self.h.call(self.h.server({SCOPE_READ}), "fleet_status", {})
        self.assertFalse(result["result"]["isError"])
        payload = result["result"]["structuredContent"]
        self.assertIn("recent_audit", payload)
        self.assertIn("identity_layer", payload)


class ANotificationIsNotAToolCall(unittest.TestCase):
    """A `tools/call` without an id is not a request this surface may execute.

    JSON-RPC allows any method as a notification, but MCP defines `tools/call`
    as a request. Executing the notification form delivers a message the caller
    can never observe the result of — and the old dispatcher did exactly that,
    because it ran `_dispatch` before it noticed there was no id.
    """

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_a_notification_form_send_does_not_reach_the_device(self):
        self.h.device.calls.clear()
        response = self.h.server({SCOPE_READ, SCOPE_SEND}).handle(
            {"jsonrpc": "2.0", "method": "tools/call",
             "params": {"name": "fleet_send_to_session",
                        "arguments": {"session_ref": conformance.SESSION_REF,
                                      "message": "notification-write"}}})
        self.assertIsNone(response)
        self.assertEqual(self.h.device.calls, [])


class AnUnknownMethodIsMethodNotFound(unittest.TestCase):
    """JSON-RPC has a code for "no such method" and the surface defined it.

    `JSONRPC_METHOD_NOT_FOUND` was declared and never used, so every unknown
    method came back as `-32602` (invalid params). A client that branches on the
    code cannot tell "this server predates the method" from "your parameters are
    wrong", which are fixed by different people.
    """

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_an_unknown_method_is_minus_32601(self):
        response = self.h.server(set()).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/does-not-exist",
             "params": {}})
        self.assertEqual(response["error"]["code"], JSONRPC_METHOD_NOT_FOUND)

    def test_an_unknown_notification_has_no_response(self):
        response = self.h.server(set()).handle(
            {"jsonrpc": "2.0", "method": "tools/does-not-exist", "params": {}})
        self.assertIsNone(response)


class TheControlScopeHasAFloorOnBothSides(unittest.TestCase):
    """Holding `fleet:control` is necessary and not sufficient.

    The device must allow control and the principal must hold the operator
    grant. The positive case is here too: a suite that only proved refusals
    could pass by refusing everything.
    """

    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_control_with_the_operator_grant_reaches_the_device(self):
        self.h.device.calls.clear()
        result = self.h.call(self.h.server({SCOPE_CONTROL}), "fleet_interrupt_session",
                             {"session_ref": conformance.SESSION_REF})
        self.assertFalse(result["result"]["isError"], result)
        self.assertEqual([call[0] for call in self.h.device.calls], ["interrupt"])

    def test_control_without_the_operator_grant_is_refused(self):
        result = self.h.call(
            self.h.server({SCOPE_CONTROL}, name="oauth:no-grant"),
            "fleet_interrupt_session", {"session_ref": conformance.SESSION_REF})
        self.assertEqual(self.h.refusal(result), "APPROVAL_REQUIRED")
        self.assertEqual(self.h.device.calls, [])

    def test_read_cannot_stop_a_run(self):
        result = self.h.call(self.h.server({SCOPE_READ}), "fleet_interrupt_session",
                             {"session_ref": conformance.SESSION_REF})
        self.assertEqual(self.h.refusal(result), "SCOPE_DENIED")

    def test_create_without_a_create_allow_entry_is_refused(self):
        result = self.h.call(
            self.h.server({SCOPE_READ, SCOPE_CREATE}), "fleet_create_session",
            {"device": conformance.DEVICE, "project": "not-allowed"})
        self.assertIn(self.h.refusal(result), {"APPROVAL_REQUIRED", "WRITE_NOT_GRANTED"})


class DiscoveryIsNotEntitlement(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_zero_scope_can_read_the_static_manifest(self):
        result = self.h.call(self.h.server(set()),
                             "fleet_cloudcli_native_manifest", {})
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(result["result"]["structuredContent"]["count"], 22)

    def test_zero_scope_cannot_read_live_capability(self):
        result = self.h.call(self.h.server(set()),
                             "fleet_cloudcli_native_capability",
                             {"device_ref": conformance.DEVICE})
        self.assertEqual(self.h.refusal(result), "SCOPE_DENIED")

    def test_zero_scope_can_still_list_tools(self):
        result = self.h.server(set()).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertEqual(len(result["result"]["tools"]), 33)


class UnknownsStayDistinct(unittest.TestCase):
    def setUp(self):
        self.h = Harness()

    def tearDown(self):
        self.h.close()

    def test_an_unenrolled_device_is_unknown_device(self):
        result = self.h.call(self.h.server({SCOPE_READ}), "fleet_list_sessions",
                             {"device": conformance.UNROUTED})
        self.assertEqual(self.h.refusal(result), "UNKNOWN_DEVICE")

    def test_a_blank_runtime_is_refused_not_defaulted(self):
        result = self.h.call(self.h.server({SCOPE_READ}),
                             "fleet_cloudcli_session_get",
                             {"device_ref": conformance.DEVICE, "runtime": "",
                              "arguments": {"session": "x"}})
        self.assertEqual(self.h.refusal(result), "BAD_REQUEST")

    def test_an_unknown_tool_is_an_answer_not_a_crash(self):
        result = self.h.call(self.h.server(set()), "fleet_not_a_tool", {})
        self.assertEqual(self.h.refusal(result), "BAD_REQUEST")


if __name__ == "__main__":
    # A machine-readable line so a receipt can quote the counts without
    # re-parsing unittest's prose.
    print(json.dumps({"mode": "FAKE_DEVICE", "matrix": matrix.summary()},
                     sort_keys=True))
    unittest.main(verbosity=2)
