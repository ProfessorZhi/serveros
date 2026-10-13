#!/usr/bin/env python3
"""The CloudCLI native surface, inherited whole — closed, governed, and not a
way around any existing Fleet floor.

The sentences these tests keep false:

    "the mirror is a second, weaker door"     — every write is routed through the
                                                SAME `_may_send` / `_may_control`
                                                / `_may_create` its Fleet verb uses
    "a session id inside `arguments` is an address" — it is not, and a write with
                                                no `session_ref` is REFUSED
    "read implies send implies control"       — each class has its own floor
    "only Tokyo can be called"                — the fleet-node route carries the
                                                same closed set over HMAC
    "the device grew a tool, so Fleet has it" — a name nobody recorded is refused
    "it is the same schema everywhere"        — a per-device difference is
                                                UNSUPPORTED_BY_DEVICE, not widened

Everything runs against FAKE providers and a fake node. No socket, no device, no
credential, no Feishu, no real tool call, no approval answered.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fleet_mcp import cloudcli_native, node_protocol, tools as tools_mod
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,
                              PolicyBinding, ProviderBinding, ServerBinding)
from fleet_mcp.errors import (APPROVAL_REQUIRED, BAD_REQUEST, BAD_SESSION_REF,
                              PROVIDER_UNREACHABLE, SCOPE_DENIED,
                              UNKNOWN_DEVICE, WRITE_NOT_GRANTED, FleetRefusal)
from fleet_mcp.fleet import (SCOPE_APPROVE, SCOPE_CONTROL, SCOPE_CREATE,
                             SCOPE_NAVIGATE, SCOPE_READ, SCOPE_SEND,
                             FleetService, Principal)
from fleet_mcp.identity import SessionKey
from fleet_mcp.providers.fleet_node import FleetNodeProvider

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:personal-dell"
DELL = "device:l6000-ai-lab"
OAUTH = "oauth:test-client"


def ref(device, native, provider="claude"):
    return f"{device}|{provider}|{native}"


def _registry_file(tmp: Path, devices) -> Path:
    p = tmp / "registry.yaml"
    with p.open("w", encoding="utf-8") as handle:
        handle.write("devices:\n")
        for device_ref, owner in devices.items():
            handle.write(f"  - device_ref: {device_ref}\n")
            handle.write(f"    display_name: {device_ref.split(':', 1)[1]}\n")
            handle.write("    platform: linux\n")
            handle.write("    environment_profile: linux-shared-multiuser\n")
            handle.write(f"    ownership: {owner}\n")
    return p


class FakeProvider:
    """A device that records what it was asked. Governance lives above it."""

    def __init__(self, device_ref, kind="cloudcli-mcp-http", *, fail=None,
                 native=True):
        self.device_ref = device_ref
        self.kind = kind
        self.calls: list = []
        self._fail = fail
        self._has_native = native

    def native_call(self, tool, arguments):
        # A provider with no native route must NOT even define this method, so
        # the attribute check in `native_call` is what exercises that path.
        self.calls.append((tool, arguments))
        if self._fail is not None:
            raise self._fail
        return {"ok": True, "tool": tool}


class Harness:
    def __init__(self, tmp: Path, *, owner="company", grants=None, send_allow=(),
                 control_allow=(), create_allow=None, allow_send=True,
                 allow_control=True, allow_create=True, kinds=None,
                 no_route=()):
        self.anchor = tmp / "pat"
        self.anchor.write_text("owner-token-value", encoding="utf-8")
        self.registry = _registry_file(tmp, {TOKYO: owner, WINDOWS: owner, DELL: owner})
        kinds = kinds or {}
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                device: ProviderBinding(
                    device_ref=device, kind=kinds.get(device, "cloudcli-mcp-http"),
                    endpoint=f"http://{device}/mcp", credential_anchor=self.anchor,
                    allow_send=allow_send, allow_create=allow_create,
                    allow_control=allow_control,
                ) for device in (TOKYO, WINDOWS, DELL)
            },
            auth=AuthBinding(pat_anchor=self.anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=tuple(send_allow),
                                 control_allow=tuple(control_allow),
                                 create_allow=dict(create_allow or {}),
                                 operator_grants=dict(grants or {})),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.providers = {}
        for device in (TOKYO, WINDOWS, DELL):
            if device in no_route:
                provider = FakeProvider(device, kind=kinds.get(device, "fleet-node"))
                # Remove the method so the surface sees a provider without one.
                provider.native_call = None
            else:
                provider = FakeProvider(device, kind=kinds.get(device, "cloudcli-mcp-http"))
            self.providers[device] = provider
        from fleet_mcp.registry import load_registry
        self.service = FleetService(self.config,
                                    registry=load_registry(self.registry),
                                    providers=dict(self.providers))

    def principal(self, *scopes, name=OAUTH):
        return Principal(name=name, scopes=frozenset(scopes), credential_kind="oauth")

    def call(self, native, *, args=None, device=TOKYO, principal=None,
             session_ref=None, project=None):
        return self.service.native_call(
            principal if principal is not None else self.principal(SCOPE_READ),
            device_ref=device, native_tool=native, arguments=args or {},
            session_ref=session_ref, project=project)


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

    def test_approve_and_navigate_are_implemented_but_not_default_granted(self):
        self.assertEqual(
            cloudcli_native.FLEET_SCOPE_FOR_NATIVE[cloudcli_native.NATIVE_APPROVE],
            "fleet:approve")
        self.assertEqual(
            cloudcli_native.FLEET_SCOPE_FOR_NATIVE[cloudcli_native.NATIVE_NAVIGATE],
            "fleet:navigate")
        from fleet_mcp import scopes
        # Implemented so the two verbs have a real floor, but NOT default
        # grantable — a credential that was not granted them is refused.
        self.assertIn("fleet:approve", scopes.IMPLEMENTED_SCOPES)
        self.assertIn("fleet:navigate", scopes.IMPLEMENTED_SCOPES)
        self.assertNotIn("fleet:approve", scopes.DEFAULT_GRANTABLE_SCOPES)
        self.assertNotIn("fleet:navigate", scopes.DEFAULT_GRANTABLE_SCOPES)

    def test_every_recorded_tool_has_a_schema(self):
        for tool in cloudcli_native.NATIVE_TOOLS:
            self.assertIn(tool.name, cloudcli_native.NATIVE_INPUT_SCHEMAS)
        # The write tools carry their REAL required parameters, read from the
        # registry — not an empty object.
        self.assertEqual(
            cloudcli_native.NATIVE_INPUT_SCHEMAS["session_send"]["required"],
            ["session", "message"])
        self.assertEqual(
            cloudcli_native.NATIVE_INPUT_SCHEMAS["approval_answer"]["required"],
            ["requestId", "allow"])

    def test_background_governance_is_per_action(self):
        bg = cloudcli_native.NATIVE_BY_NAME["session_background"]
        self.assertEqual(cloudcli_native.governance_for(bg, {}),
                         cloudcli_native.GOV_READ)
        self.assertEqual(cloudcli_native.governance_for(bg, {"stopTaskId": "t"}),
                         cloudcli_native.GOV_CONTROL)

    def test_dual_scope_tool_records_both(self):
        bg = cloudcli_native.NATIVE_BY_NAME["session_background"]
        self.assertEqual(set(bg.native_scopes),
                         {cloudcli_native.NATIVE_READ, cloudcli_native.NATIVE_CONTROL})
        self.assertEqual(bg.governance, cloudcli_native.GOV_CONTROL)

    def test_manifest_is_machine_readable(self):
        m = cloudcli_native.manifest()
        self.assertEqual(m["count"], 22)
        self.assertEqual(m["schema"], "serveros/fleet-cloudcli-native-manifest.v1")
        self.assertIn("fleet_cloudcli_", json.dumps(m, ensure_ascii=False))

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

    def test_reports_missing_and_extra(self):
        observed = [t.name for t in cloudcli_native.NATIVE_TOOLS if t.name != "run_get"]
        observed.append("session_teleport")
        report = cloudcli_native.coverage(observed)
        self.assertEqual(report["missing"], ["run_get"])
        self.assertEqual(report["extra"], ["session_teleport"])

    def test_reports_annotation_drift(self):
        report = cloudcli_native.coverage(
            [t.name for t in cloudcli_native.NATIVE_TOOLS],
            {"session_send": {"readOnlyHint": False, "destructiveHint": True,
                              "idempotentHint": False, "openWorldHint": True}})
        self.assertEqual([d["tool"] for d in report["annotation_drift"]], ["session_send"])

    def test_two_devices_report_differently(self):
        a = cloudcli_native.coverage([t.name for t in cloudcli_native.NATIVE_TOOLS])
        b = cloudcli_native.coverage(["overview", "session_send"])
        self.assertTrue(a["agrees"])
        self.assertFalse(b["agrees"])


class NativeSchemaTest(unittest.TestCase):
    """Per-tool schemas from the device, and drift that is named not widened."""

    LIVE = {"type": "object",
            "properties": {"session": {"type": "string"},
                           "message": {"type": "string"},
                           "idempotency_key": {"type": ["string", "null"]}},
            "required": ["session", "message"],
            "additionalProperties": False}

    def test_mirrored_schema_adds_device_and_keeps_native_shape(self):
        native = cloudcli_native.NATIVE_BY_NAME["session_send"]
        schema = cloudcli_native.mirrored_schema(native, self.LIVE)
        # `arguments` is required because the native tool has required params.
        self.assertEqual(schema["required"], ["device_ref", "arguments"])
        self.assertEqual(set(schema["properties"]),
                         {"device_ref", "session_ref", "arguments"})
        inner = schema["properties"]["arguments"]
        self.assertEqual(inner["required"], ["session", "message"])
        # The nullable type is carried, not flattened.
        self.assertEqual(inner["properties"]["idempotency_key"]["type"],
                         ["string", "null"])

    def test_a_recorded_schema_is_used_without_a_device(self):
        # No live schema handed in: the schema RECORDED from CloudCLI 1.39.0 is
        # used, so the served tool still carries real parameters.
        schema = cloudcli_native.mirrored_schema(
            cloudcli_native.NATIVE_BY_NAME["session_create"])
        self.assertEqual(schema["properties"]["arguments"]["required"], ["project"])
        self.assertIn("message", schema["properties"]["arguments"]["properties"])

    def test_create_schema_needs_a_project_not_a_session_ref(self):
        native = cloudcli_native.NATIVE_BY_NAME["session_create"]
        schema = cloudcli_native.mirrored_schema(native, self.LIVE)
        self.assertIn("project", schema["properties"])
        self.assertNotIn("session_ref", schema["properties"])

    def test_validate_refuses_missing_required_and_unknown(self):
        self.assertEqual(
            cloudcli_native.validate_arguments({"session": "s"},
                                               {"required": ["session", "message"],
                                                "properties": {"session": {}, "message": {}},
                                                "additionalProperties": False}),
            ["missing required parameter 'message'"])
        self.assertEqual(
            cloudcli_native.validate_arguments({"x": 1},
                                               {"properties": {}, "additionalProperties": False}),
            ["unknown parameter 'x'"])

    def test_null_and_omitted_stay_distinct(self):
        schema = {"properties": {"idempotency_key": {"type": ["string", "null"]},
                                 "message": {}},
                  "required": ["message"], "additionalProperties": False}
        # present-and-null is accepted and NOT reported as missing.
        self.assertEqual(cloudcli_native.validate_arguments(
            {"message": "m", "idempotency_key": None}, schema), [])
        # omitted is fine too, and neither becomes the other.
        self.assertEqual(cloudcli_native.validate_arguments({"message": "m"}, schema), [])

    def test_a_placeholder_schema_checks_nothing(self):
        # The write tools register `{}` until wired: no authority to check.
        self.assertEqual(cloudcli_native.validate_arguments({"anything": 1}, {}), [])
        self.assertEqual(cloudcli_native.validate_arguments({"anything": 1}, None), [])

    def test_per_device_schema_drift_is_named(self):
        report = cloudcli_native.surface_drift({
            TOKYO: {"session_send": self.LIVE},
            DELL: {"session_send": {"type": "object", "properties": {"sessionId": {}},
                                    "required": ["sessionId"]}},
            WINDOWS: {"session_send": self.LIVE},
        })
        self.assertEqual(report["drifted"], ["session_send"])
        self.assertFalse(report["tools"]["session_send"]["agrees"])
        self.assertEqual(report["tools"]["session_send"]["unsupported_by_device"], [DELL])


# ---------------------------------------------------------------------------
# Governance — every write through its own floor
# ---------------------------------------------------------------------------

class DispatchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_tool_is_allowed_with_read_scope(self):
        h = Harness(self.tmp)
        self.assertEqual(h.call("sessions_list", args={"limit": 5},
                                principal=h.principal(SCOPE_READ)), {"ok": True, "tool": "sessions_list"})
        self.assertEqual(h.providers[TOKYO].calls, [("sessions_list", {"limit": 5})])

    def test_read_tool_without_read_scope_is_denied(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("sessions_list", principal=h.principal())
        self.assertEqual(caught.exception.code, SCOPE_DENIED)

    def test_a_write_with_no_session_ref_is_refused_not_guessed(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        # A native `session` id inside `arguments` is NOT an address.
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_send", args={"session": "s1", "message": "hi"},
                   principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_session_ref_for_another_device_is_refused(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_send", args={"session": "s1", "message": "hi"},
                   device=TOKYO, session_ref=ref(DELL, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(caught.exception.code, BAD_SESSION_REF)

    def test_send_refused_when_not_in_send_allow_and_no_grant(self):
        # allow_send on the device, but neither a grant nor a `send_allow` entry.
        h = Harness(self.tmp, send_allow=())
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_send", args={"session": "s1", "message": "hi"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_send_allowed_when_the_session_is_in_send_allow(self):
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),))
        result = h.call("session_send", args={"session": "s1", "message": "hi"},
                        session_ref=ref(TOKYO, "s1"),
                        principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(result, {"ok": True, "tool": "session_send"})
        self.assertEqual(h.providers[TOKYO].calls,
                         [("session_send", {"session": "s1", "message": "hi"})])

    def test_interrupt_refused_when_not_in_control_allow(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("control",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        # device allow_control is on, but the SESSION is not in control_allow.
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_interrupt_allowed_with_control_allow_and_grant(self):
        h = Harness(self.tmp, control_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("control",)}})
        result = h.call("session_interrupt", args={"session": "s1"},
                        session_ref=ref(TOKYO, "s1"),
                        principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(result, {"ok": True, "tool": "session_interrupt"})

    def test_interrupt_needs_the_operator_grant_even_with_control_allow(self):
        h = Harness(self.tmp, control_allow=(ref(TOKYO, "s1"),), grants={})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_a_send_grant_is_not_inherited_into_control(self):
        h = Harness(self.tmp, control_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_create_refused_without_a_project(self):
        h = Harness(self.tmp, create_allow={}, grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create", args={"project": "p1"},
                   principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)

    def test_create_refused_when_project_not_in_create_allow(self):
        from fleet_mcp.config import CreateGrant
        h = Harness(self.tmp, create_allow={"other": CreateGrant()},
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create", args={"project": "p1"}, project="p1",
                   principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_create_allowed_with_project_allow_and_grant(self):
        from fleet_mcp.config import CreateGrant
        h = Harness(self.tmp, create_allow={"p1": CreateGrant()},
                    grants={OAUTH: {TOKYO: ("create",)}})
        result = h.call("session_create", args={"project": "p1"}, project="p1",
                        principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(result, {"ok": True, "tool": "session_create"})

    def test_approval_answer_needs_the_approve_scope_then_a_grant_then_a_choice(self):
        # 1. no `fleet:approve` scope -> refused at the scope gate.
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("approve",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"requestId": "r1", "allow": True},
                   principal=h.principal(SCOPE_READ, SCOPE_SEND, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])
        # 2. scope present, no operator grant -> refused at the grant gate.
        ungranted = Harness(self.tmp, grants={})
        with self.assertRaises(FleetRefusal) as caught:
            ungranted.call("approval_answer", args={"requestId": "r1", "allow": True},
                           principal=ungranted.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(ungranted.providers[TOKYO].calls, [])
        # 3. granted, but no requestId -> refused (an answer to nothing).
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"allow": True},
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        # 4. granted, requestId present, but no user decision -> refused.
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"requestId": "r1"},
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])
        # 5. scope + grant + a named request + the user's decision -> forwarded.
        result = h.call("approval_answer",
                        args={"requestId": "r1", "allow": True, "answers": {"a": "b"}},
                        principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(result["tool"], "approval_answer")
        self.assertEqual(h.providers[TOKYO].calls,
                         [("approval_answer", {"requestId": "r1", "allow": True,
                                               "answers": {"a": "b"}})])

    def test_approvals_list_is_read_only(self):
        h = Harness(self.tmp)
        self.assertEqual(h.call("approvals_list", principal=h.principal(SCOPE_READ)),
                         {"ok": True, "tool": "approvals_list"})

    def test_approvals_list_is_a_read(self):
        h = Harness(self.tmp)
        self.assertEqual(h.call("approvals_list", principal=h.principal(SCOPE_READ)),
                         {"ok": True, "tool": "approvals_list"})

    def test_ui_open_session_needs_the_navigate_scope_and_a_grant(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("navigate",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("ui_open_session", args={"session": "s1"},
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        ungranted = Harness(self.tmp, grants={})
        with self.assertRaises(FleetRefusal) as caught:
            ungranted.call("ui_open_session", args={"session": "s1"},
                           principal=ungranted.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        result = h.call("ui_open_session", args={"session": "s1"},
                        principal=h.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(result["tool"], "ui_open_session")

    def test_unknown_native_tool_is_refused_before_any_call(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_teleport", principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_provider_error_code_survives_without_remote_text(self):
        h = Harness(self.tmp)
        h.providers[TOKYO]._fail = FleetRefusal(PROVIDER_UNREACHABLE, "no route")
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_get", args={"session": "s1"}, principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, PROVIDER_UNREACHABLE)

    def test_a_read_only_list_has_no_side_effect(self):
        h = Harness(self.tmp)
        h.call("sessions_list", principal=h.principal(SCOPE_READ))
        # exactly one call, and it is the read; nothing was mutated.
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls], ["sessions_list"])


class ProviderRouteTest(unittest.TestCase):
    """Three provider kinds, a missing route, and the fleet-node closed route."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_devices_of_every_kind_are_addressable(self):
        h = Harness(self.tmp, kinds={TOKYO: "cloudcli-mcp-http",
                                     WINDOWS: "fleet-node", DELL: "fleet-node"})
        for device in (TOKYO, WINDOWS, DELL):
            result = h.call("overview", device=device,
                            principal=h.principal(SCOPE_READ))
            self.assertEqual(result["tool"], "overview")
        self.assertEqual(h.providers[WINDOWS].calls, [("overview", {})])

    def test_a_provider_with_no_native_route_is_refused(self):
        h = Harness(self.tmp, kinds={DELL: "fleet-node"}, no_route=(DELL,))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("overview", device=DELL, principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)

    def test_fleet_node_provider_posts_the_closed_route(self):
        provider = FleetNodeProvider(ProviderBinding(
            device_ref=WINDOWS, kind="fleet-node", endpoint="http://x",
            credential_anchor=self.tmp / "s"))
        seen = {}

        def _fake_request(method, path, payload=None):
            seen.update(method=method, path=path, payload=payload)
            return {"ok": True}

        provider._request = _fake_request
        provider.native_call("session_send", {"session": "s1"})
        self.assertEqual(seen["path"], "/v1/native/call")
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["payload"], {"tool": "session_send",
                                           "arguments": {"session": "s1"}})

    def test_fleet_node_provider_refuses_an_unrecorded_tool(self):
        provider = FleetNodeProvider(ProviderBinding(
            device_ref=WINDOWS, kind="fleet-node", endpoint="http://x",
            credential_anchor=self.tmp / "s"))
        with self.assertRaises(FleetRefusal):
            provider.native_call("session_teleport", {})


class NodeRouteSignatureTest(unittest.TestCase):
    """The signed, closed node route: replay, nonce, wrong device, tamper."""

    SECRET = "s3cr3t-value-for-tests-only"

    def _headers(self, *, device=WINDOWS, ts=None, nonce=None, body=b'{"a":1}'):
        return node_protocol.sign(self.SECRET, method="POST", path="/v1/native/call",
                                  body=body, device_ref=device,
                                  timestamp=ts, nonce=nonce), body

    def test_a_signed_request_verifies(self):
        headers, body = self._headers()
        cache = node_protocol.ReplayCache()
        who = node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                                   body=body, headers=headers,
                                   expected_device_ref=WINDOWS, replay=cache)
        self.assertEqual(who, WINDOWS)

    def test_a_replayed_nonce_is_refused(self):
        headers, body = self._headers(nonce="fixed-nonce")
        cache = node_protocol.ReplayCache()
        node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                             body=body, headers=headers,
                             expected_device_ref=WINDOWS, replay=cache)
        with self.assertRaises(node_protocol.SignatureError) as caught:
            node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                                 body=body, headers=headers,
                                 expected_device_ref=WINDOWS, replay=cache)
        self.assertEqual(caught.exception.code, "REPLAY")

    def test_a_wrong_device_is_refused(self):
        headers, body = self._headers(device=TOKYO)
        with self.assertRaises(node_protocol.SignatureError):
            node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                                 body=body, headers=headers,
                                 expected_device_ref=WINDOWS,
                                 replay=node_protocol.ReplayCache())

    def test_a_tampered_body_is_refused(self):
        # A current timestamp, so the body digest is what fails — not the clock.
        headers, _ = self._headers()
        with self.assertRaises(node_protocol.SignatureError) as caught:
            node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                                 body=b'{"a":2}', headers=headers,
                                 expected_device_ref=WINDOWS,
                                 replay=node_protocol.ReplayCache())
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_a_stale_timestamp_is_refused(self):
        headers, body = self._headers(ts="9999999999")
        with self.assertRaises(node_protocol.SignatureError) as caught:
            node_protocol.verify(self.SECRET, method="POST", path="/v1/native/call",
                                 body=body, headers=headers,
                                 expected_device_ref=WINDOWS,
                                 replay=node_protocol.ReplayCache())
        self.assertEqual(caught.exception.code, "STALE_REQUEST")

    def test_the_native_route_is_in_the_closed_route_table(self):
        self.assertIn("POST /v1/native/call", node_protocol.ROUTES)
        # And it is a FIXED path: the tool name is in the body, not the path.
        self.assertFalse(any("{tool}" in r for r in node_protocol.ROUTES))


class SurfaceTest(unittest.TestCase):
    def test_the_served_surface_is_the_nine_plus_the_twenty_two(self):
        names = {t.name for t in tools_mod.SERVED_TOOLS}
        for tool in cloudcli_native.NATIVE_TOOLS:
            self.assertIn(tool.namespaced, names)
        self.assertEqual(len([n for n in names if n.startswith("fleet_cloudcli_")]), 22)
        self.assertEqual(len(tools_mod.tool_names()), 9)

    def test_mirror_annotations_match_the_manifest(self):
        by_name = {t.name: t for t in tools_mod.NATIVE_MIRROR_TOOLS}
        for native in cloudcli_native.NATIVE_TOOLS:
            schema = by_name[native.namespaced]
            self.assertEqual(schema.annotations["readOnlyHint"], native.read_only)
            self.assertEqual(schema.annotations["destructiveHint"], native.destructive)
            self.assertEqual(schema.annotations["openWorldHint"], native.open_world)

    def test_a_write_mirror_declares_the_address_its_floor_needs(self):
        send = tools_mod.SERVED_BY_NAME["fleet_cloudcli_session_send"]
        self.assertIn("session_ref", send.input_schema["properties"])
        create = tools_mod.SERVED_BY_NAME["fleet_cloudcli_session_create"]
        self.assertIn("project", create.input_schema["properties"])
        read = tools_mod.SERVED_BY_NAME["fleet_cloudcli_overview"]
        self.assertNotIn("session_ref", read.input_schema["properties"])

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


class MatrixTest(unittest.TestCase):
    """All 22 tools × the routing and the deny-path, counted, not sampled."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _minimal_args(native):
        required = cloudcli_native.NATIVE_INPUT_SCHEMAS[native.name].get("required", [])
        out = {}
        props = cloudcli_native.NATIVE_INPUT_SCHEMAS[native.name].get("properties", {})
        for name in required:
            out[name] = True if props.get(name, {}).get("type") == "boolean" else "x"
        return out

    def test_every_read_tool_routes_on_both_provider_kinds(self):
        for kind in ("cloudcli-mcp-http", "fleet-node"):
            h = Harness(self.tmp, kinds={TOKYO: kind, WINDOWS: kind, DELL: kind})
            reads = [t for t in cloudcli_native.NATIVE_TOOLS if t.read_only]
            for tool in reads:
                result = h.call(tool.name, args=self._minimal_args(tool),
                                principal=h.principal(SCOPE_READ))
                self.assertEqual(result["tool"], tool.name, f"{kind}/{tool.name}")
            self.assertEqual(len(h.providers[TOKYO].calls), len(reads))

    def test_every_write_tool_is_denied_with_only_a_read_credential(self):
        h = Harness(self.tmp)
        writes = [t for t in cloudcli_native.NATIVE_TOOLS
                  if not t.read_only and t.name != "session_background"]
        for tool in writes:
            with self.assertRaises(FleetRefusal, msg=tool.name) as caught:
                h.call(tool.name, args=self._minimal_args(tool),
                       session_ref=ref(TOKYO, "x"), project="p",
                       principal=h.principal(SCOPE_READ))
            # Refused at the scope gate or the grant gate — never forwarded.
            self.assertIn(caught.exception.code,
                          (SCOPE_DENIED, WRITE_NOT_GRANTED), tool.name)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_the_whole_surface_is_discoverable_and_counted(self):
        names = {t.name for t in tools_mod.SERVED_TOOLS}
        self.assertEqual(len([n for n in names if n.startswith("fleet_cloudcli_")]), 22)


if __name__ == "__main__":
    unittest.main(verbosity=2)
