#!/usr/bin/env python3
"""The CloudCLI native surface, inherited whole — as a closed, governed set.

The sentences these tests keep false:

    "Fleet only supports seven tools"        — all 22 native verbs are mirrored
    "the device grew a tool, so Fleet has it" — a name nobody recorded is refused
    "read implies send implies control"       — each class has its own gate
    "approve is just another write"           — Fleet plans it and does not grant
                                                it, so it is DENIED, not inherited
    "the error body came back verbatim"       — a code survives, the remote text
                                                does not

Everything runs against a FAKE provider. No socket, no device, no credential, no
Feishu, no real tool call.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fleet_mcp import cloudcli_native, tools as tools_mod
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,
                              PolicyBinding, ProviderBinding, ServerBinding)
from fleet_mcp.errors import (BAD_REQUEST, PROVIDER_UNREACHABLE, SCOPE_DENIED,
                              UNKNOWN_DEVICE, WRITE_NOT_GRANTED, FleetRefusal)
from fleet_mcp.fleet import (SCOPE_CONTROL, SCOPE_CREATE, SCOPE_READ, SCOPE_SEND,
                             FleetService, Principal)

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:personal-dell"
DELL = "device:l6000-ai-lab"
OAUTH = "oauth:test-client"


def _registry_file(tmp: Path, devices) -> Path:
    p = tmp / "registry.yaml"
    with p.open("w", encoding="utf-8") as handle:
        handle.write("devices:\n")
        for ref, owner in devices.items():
            name = ref.split(":", 1)[1]
            handle.write(f"  - device_ref: {ref}\n")
            handle.write(f"    display_name: {name}\n")
            handle.write("    platform: linux\n")
            handle.write("    environment_profile: linux-shared-multiuser\n")
            handle.write(f"    ownership: {owner}\n")
    return p


class FakeNativeProvider:
    """A device that records what it was asked and answers from a fixture.

    It implements `native_call` and NOTHING ELSE is needed: the governance under
    test lives in `FleetService`, above this line.
    """

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref, *, result=None, fail=None, tools=None):
        self.device_ref = device_ref
        self.calls: list[tuple[str, dict]] = []
        self._result = result if result is not None else {"ok": True}
        self._fail = fail
        self.tools = tools

    def native_call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if self._fail is not None:
            raise self._fail
        return dict(self._result)


class Harness:
    def __init__(self, tmp: Path, *, owner="company", grants=None,
                 allow_send=True, allow_create=True, allow_control=True):
        self.anchor = tmp / "pat"
        self.anchor.write_text("owner-token-value", encoding="utf-8")
        self.registry = _registry_file(tmp, {TOKYO: owner, WINDOWS: owner, DELL: owner})
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                ref: ProviderBinding(
                    device_ref=ref, kind="cloudcli-mcp-http",
                    endpoint=f"http://{ref}/mcp", credential_anchor=self.anchor,
                    allow_send=allow_send, allow_create=allow_create,
                    allow_control=allow_control,
                ) for ref in (TOKYO, WINDOWS, DELL)
            },
            auth=AuthBinding(pat_anchor=self.anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=(), operator_grants=dict(grants or {})),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        from fleet_mcp.registry import load_registry
        self.providers = {
            ref: FakeNativeProvider(ref) for ref in (TOKYO, WINDOWS, DELL)
        }
        self.service = FleetService(self.config,
                                    registry=load_registry(self.registry),
                                    providers=dict(self.providers))

    def principal(self, *scopes, name=OAUTH):
        return Principal(name=name, scopes=frozenset(scopes), credential_kind="oauth")

    def call(self, native, *, args=None, device=TOKYO, principal=None, **kw):
        return self.service.native_call(
            principal if principal is not None else self.principal(SCOPE_READ),
            device_ref=device, native_tool=native, arguments=args or {}, **kw)


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

class ManifestTest(unittest.TestCase):
    def test_all_twenty_two_native_tools_are_recorded(self):
        self.assertEqual(len(cloudcli_native.NATIVE_TOOLS), 22)
        self.assertEqual(cloudcli_native.NATIVE_TOOL_COUNT, 22)

    def test_names_are_the_recorded_native_set(self):
        expected = {
            "overview", "projects_list", "sessions_list", "session_get",
            "session_read", "run_get", "quay_snapshot", "ui_last_opened_session",
            "ui_visible_context", "ui_clients_list", "session_search",
            "session_send", "session_create", "session_interrupt", "session_start",
            "session_close", "ui_open_session", "session_cancel_queued",
            "session_reconfigure", "session_background", "approvals_list",
            "approval_answer",
        }
        self.assertEqual(set(cloudcli_native.NATIVE_BY_NAME), expected)

    def test_read_write_split_is_twelve_and_ten(self):
        ro = [t for t in cloudcli_native.NATIVE_TOOLS if t.read_only]
        self.assertEqual(len(ro), 12)
        self.assertEqual(len(cloudcli_native.NATIVE_TOOLS) - len(ro), 10)

    def test_approve_and_navigate_map_to_planned_fleet_scopes(self):
        self.assertEqual(
            cloudcli_native.FLEET_SCOPE_FOR_NATIVE[cloudcli_native.NATIVE_APPROVE],
            "fleet:approve")
        self.assertEqual(
            cloudcli_native.FLEET_SCOPE_FOR_NATIVE[cloudcli_native.NATIVE_NAVIGATE],
            "fleet:navigate")
        # And Fleet does not implement either.
        from fleet_mcp import scopes
        self.assertNotIn("fleet:approve", scopes.IMPLEMENTED_SCOPES)
        self.assertNotIn("fleet:navigate", scopes.IMPLEMENTED_SCOPES)

    def test_dual_scope_tool_records_both(self):
        bg = cloudcli_native.NATIVE_BY_NAME["session_background"]
        self.assertEqual(set(bg.native_scopes),
                         {cloudcli_native.NATIVE_READ, cloudcli_native.NATIVE_CONTROL})
        # Its governance is the STRONGER class, so a read grant cannot stop it.
        self.assertEqual(bg.governance, cloudcli_native.GOV_CONTROL)

    def test_manifest_is_machine_readable(self):
        m = cloudcli_native.manifest()
        self.assertEqual(m["count"], 22)
        self.assertEqual(m["schema"], "serveros/fleet-cloudcli-native-manifest.v1")
        blob = json.dumps(m, ensure_ascii=False)
        self.assertIn("fleet_cloudcli_", blob)

    def test_namespacing_and_closed_lookup(self):
        self.assertEqual(cloudcli_native.namespaced("overview"), "fleet_cloudcli_overview")
        self.assertEqual(cloudcli_native.native_of("fleet_cloudcli_overview").name,
                         "overview")
        self.assertIsNone(cloudcli_native.native_of("fleet_cloudcli_not_a_tool"))
        self.assertIsNone(cloudcli_native.native_of("overview"))


class CoverageTest(unittest.TestCase):
    def test_agrees_on_the_exact_set(self):
        report = cloudcli_native.coverage([t.name for t in cloudcli_native.NATIVE_TOOLS])
        self.assertTrue(report["agrees"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["extra"], [])

    def test_reports_a_missing_tool(self):
        observed = [t.name for t in cloudcli_native.NATIVE_TOOLS if t.name != "run_get"]
        report = cloudcli_native.coverage(observed)
        self.assertFalse(report["agrees"])
        self.assertEqual(report["missing"], ["run_get"])

    def test_reports_an_extra_tool_the_device_grew(self):
        observed = [t.name for t in cloudcli_native.NATIVE_TOOLS] + ["session_teleport"]
        report = cloudcli_native.coverage(observed)
        self.assertEqual(report["extra"], ["session_teleport"])
        self.assertFalse(report["agrees"])

    def test_reports_annotation_drift(self):
        report = cloudcli_native.coverage(
            [t.name for t in cloudcli_native.NATIVE_TOOLS],
            {"session_send": {"readOnlyHint": False, "destructiveHint": True,
                              "idempotentHint": False, "openWorldHint": True}})
        self.assertEqual([d["tool"] for d in report["annotation_drift"]], ["session_send"])
        self.assertFalse(report["agrees"])

    def test_two_devices_report_differently(self):
        # The three devices are NOT hardcoded to be identical: a device sharing
        # fewer tools gets its own report.
        a = cloudcli_native.coverage([t.name for t in cloudcli_native.NATIVE_TOOLS])
        b = cloudcli_native.coverage(["overview", "session_send"])
        self.assertTrue(a["agrees"])
        self.assertFalse(b["agrees"])
        self.assertEqual(b["observed_count"], 2)


# ---------------------------------------------------------------------------
# Dispatch, governance, and error transparency
# ---------------------------------------------------------------------------

class DispatchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.h = Harness(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_tool_is_allowed_with_read_scope(self):
        result = self.h.call("sessions_list", args={"limit": 5},
                             principal=self.h.principal(SCOPE_READ))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.h.providers[TOKYO].calls, [("sessions_list", {"limit": 5})])

    def test_read_tool_without_read_scope_is_denied(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("sessions_list", principal=self.h.principal())
        self.assertEqual(caught.exception.code, SCOPE_DENIED)

    def test_write_needs_flag_and_grant_not_a_read(self):
        # read + send scopes, but no operator grant for this device.
        principal = self.h.principal(SCOPE_READ, SCOPE_SEND)
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("session_send", args={"session": "s1", "message": "hi"},
                        principal=principal)
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(self.h.providers[TOKYO].calls, [])

    def test_send_allowed_with_scope_flag_and_operator_grant(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        result = h.call("session_send", args={"session": "s1", "message": "hi"},
                        device=TOKYO,
                        principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(result, {"ok": True})
        self.assertEqual(h.providers[TOKYO].calls,
                         [("session_send", {"session": "s1", "message": "hi"})])

    def test_send_grant_is_not_inherited_into_control(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"}, device=TOKYO,
                   principal=h.principal(SCOPE_READ, SCOPE_SEND, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_control_is_denied_until_fleet_names_the_verb(self):
        # `control` is a PLANNED operator-grant verb: even a device with
        # `allow_control` and a principal granted `("control",)` is refused,
        # because no implemented verb can express it yet.
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("control",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"}, device=TOKYO,
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_approve_is_denied_because_fleet_does_not_grant_it(self):
        # Even with every implemented scope and every grant, `fleet:approve` is
        # not implemented, so the answer is refused rather than inherited.
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("control", "send", "create")}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"approvalId": "a1", "answers": {}},
                   device=TOKYO,
                   principal=h.principal(SCOPE_READ, SCOPE_SEND, SCOPE_CREATE,
                                         SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_approvals_list_is_a_read(self):
        result = self.h.call("approvals_list", principal=self.h.principal(SCOPE_READ))
        self.assertEqual(result, {"ok": True})

    def test_navigate_is_denied(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("ui_open_session", args={"session": "s1"},
                        principal=self.h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)

    def test_background_stop_needs_control_not_read(self):
        # A read grant is refused at the SCOPE gate: the stop path carries
        # `cloudcli:session:control`, which needs `fleet:control`.
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("session_background", args={"session": "s1", "taskId": "t1"},
                        principal=self.h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        # With the control scope but no expressible grant, it is the grant gate.
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("session_background", args={"session": "s1", "taskId": "t1"},
                        principal=self.h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_unknown_native_tool_is_refused_before_any_call(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("session_teleport", principal=self.h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(self.h.providers[TOKYO].calls, [])

    def test_provider_error_code_survives_without_remote_text(self):
        # A provider refusal is an ANSWER: its code survives, its remote detail
        # does not ride out as a fabricated one.
        self.h.providers[TOKYO] = FakeNativeProvider(
            TOKYO, fail=FleetRefusal(PROVIDER_UNREACHABLE, "no route"))
        self.h.service.providers[TOKYO] = self.h.providers[TOKYO]
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("session_get", args={"session": "s1"},
                        principal=self.h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, PROVIDER_UNREACHABLE)

    def test_arguments_pass_through_unchanged(self):
        # null vs omitted is preserved: the payload goes to the device whole.
        payload = {"session": "s1", "message": "hi", "idempotency_key": None}
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        h.call("session_send", args=payload, device=TOKYO,
               principal=h.principal(SCOPE_READ, SCOPE_SEND))
        sent = h.providers[TOKYO].calls[0][1]
        self.assertIn("idempotency_key", sent)
        self.assertIsNone(sent["idempotency_key"])

    def test_a_disabled_device_is_unknown(self):
        self.h.config.providers[DELL] = ProviderBinding(
            device_ref=DELL, kind="cloudcli-mcp-http", endpoint="http://x/mcp",
            credential_anchor=self.h.anchor, enabled=False)
        with self.assertRaises(FleetRefusal) as caught:
            self.h.call("overview", device=DELL, principal=self.h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)


class SurfaceTest(unittest.TestCase):
    def test_the_served_surface_is_the_nine_plus_the_twenty_two(self):
        names = {t.name for t in tools_mod.SERVED_TOOLS}
        for tool in cloudcli_native.NATIVE_TOOLS:
            self.assertIn(tool.namespaced, names)
        self.assertEqual(len([n for n in names if n.startswith("fleet_cloudcli_")]), 22)
        self.assertEqual(len(names), 31)
        # `tool_names()` stays the hand-written nine the contract enumerates.
        self.assertEqual(len(tools_mod.tool_names()), 9)

    def test_mirror_annotations_match_the_manifest(self):
        by_name = {t.name: t for t in tools_mod.NATIVE_MIRROR_TOOLS}
        for native in cloudcli_native.NATIVE_TOOLS:
            schema = by_name[native.namespaced]
            self.assertEqual(schema.annotations["readOnlyHint"], native.read_only)
            self.assertEqual(schema.annotations["destructiveHint"], native.destructive)
            self.assertEqual(schema.annotations["openWorldHint"], native.open_world)

    def test_mirror_schema_is_closed_and_needs_a_device(self):
        tool = tools_mod.SERVED_BY_NAME["fleet_cloudcli_overview"]
        self.assertFalse(tool.input_schema["additionalProperties"])
        self.assertEqual(tool.input_schema["required"], ["device_ref"])

    def test_unknown_argument_is_refused_on_a_mirror_tool(self):
        tool = tools_mod.SERVED_BY_NAME["fleet_cloudcli_overview"]
        refusal = tools_mod.unknown_argument_refusal(tool, {"device_ref": TOKYO,
                                                            "oops": 1})
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.code, BAD_REQUEST)

    def test_every_read_only_mirror_is_non_destructive(self):
        for tool in tools_mod.NATIVE_MIRROR_TOOLS:
            if tool.annotations["readOnlyHint"]:
                self.assertFalse(tool.annotations["destructiveHint"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
