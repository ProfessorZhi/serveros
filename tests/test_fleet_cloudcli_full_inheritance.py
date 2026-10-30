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
from fleet_mcp.config import (AuditBinding, AuthBinding, CreateGrant, FleetConfig,
                              PolicyBinding, ProviderBinding, ServerBinding)
from fleet_mcp.errors import (APPROVAL_REQUIRED, BAD_REQUEST, BAD_SESSION_REF,
                              PROVIDER_UNREACHABLE, RATE_LIMITED, SCOPE_DENIED,
                              UNKNOWN_DEVICE, WRITE_NOT_GRANTED, FleetRefusal)
from fleet_mcp.fleet import (SCOPE_APPROVE, SCOPE_CONTROL, SCOPE_CREATE,
                             SCOPE_NAVIGATE, SCOPE_READ, SCOPE_SEND,
                             FleetService, Principal, RateLimiter)
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
                 native=True, project="p", result=None):
        self.device_ref = device_ref
        self.kind = kind
        self.calls: list = []
        self._fail = fail
        self._has_native = native
        self.project = project
        self.result = result

    def native_call(self, tool, arguments, *, session_ref=None):
        # A provider with no native route must NOT even define this method, so
        # the attribute check in `native_call` is what exercises that path.
        self.calls.append((tool, arguments))
        if self._fail is not None:
            raise self._fail
        if self.result is not None:
            return self.result
        if tool == "approvals_list":
            # The device's own register. Ownership of an `approval_answer` is
            # checked against THIS, so the fake must enumerate ids.
            return {"approvals": [{"requestId": "r1"}, {"requestId": "r2"}]}
        if tool == "session_get":
            # The device's own statement of which project the session belongs to.
            return {"tool": tool, "session": {"projectId": self.project}}
        return {"ok": True, "tool": tool}


class Harness:
    def __init__(self, tmp: Path, *, owner="company", grants=None, send_allow=(),
                 control_allow=(), create_allow=None, allow_send=True,
                 allow_control=True, allow_create=True, kinds=None,
                 no_route=(), eligible_ownership=None):
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
            policy=PolicyBinding(
                send_allow=tuple(send_allow),
                control_allow=tuple(control_allow),
                create_allow=dict(create_allow or {}),
                operator_grants=dict(grants or {}),
                **({"grant_eligible_ownership": tuple(eligible_ownership)}
                   if eligible_ownership is not None else {})),
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
    def test_all_thirty_one_native_tools_are_recorded(self):
        self.assertEqual(len(cloudcli_native.NATIVE_TOOLS), 31)
        self.assertEqual(cloudcli_native.NATIVE_TOOL_COUNT, 31)

    def test_names_are_the_recorded_native_set(self):
        expected = {
            "overview", "projects_list", "sessions_list", "session_get",
            "session_read", "run_get", "quay_snapshot", "ui_last_opened_session",
            "ui_visible_context", "ui_clients_list", "session_search",
            "session_send", "session_create", "session_interrupt", "session_start",
            "session_close", "ui_open_session", "session_cancel_queued",
            "session_reconfigure", "session_background", "approvals_list",
            "approval_answer",
            # CloudCLI 1.40's nine additions.
            "changes", "mcp_usage_stats", "ui_selected_text", "draft_get",
            "draft_set", "draft_submit", "ui_focus_turn", "ui_show_diff",
            "ui_open_task",
        }
        self.assertEqual(set(cloudcli_native.NATIVE_BY_NAME), expected)

    def test_read_write_split_is_sixteen_and_fifteen(self):
        ro = [t for t in cloudcli_native.NATIVE_TOOLS if t.read_only]
        self.assertEqual(len(ro), 16)
        self.assertEqual(len(cloudcli_native.NATIVE_TOOLS) - len(ro), 15)

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
        self.assertEqual(m["count"], 31)
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
        # `arguments` is required because the native tool has required params,
        # and `session_ref` because this is a session verb whose floor is stated
        # over one (LOW-13).
        self.assertEqual(schema["required"],
                         ["device_ref", "arguments", "session_ref"])
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
        got = h.call("sessions_list", args={"limit": 5},
                     principal=h.principal(SCOPE_READ))
        self.assertEqual(got["tool"], "sessions_list")
        # A mirrored read carries the data-not-instruction marking.
        self.assertIs(got["untrusted_content"], True)
        self.assertTrue(got["untrusted_notice"])
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

    def test_interrupt_refused_when_neither_the_list_nor_a_grant_admits(self):
        """The session arm and the grant arm are both closed here.

        The device is control-enabled (the harness sets `allow_control: true`),
        and neither `policy.control_allow` nor an operator grant names this
        session, so the run may not be stopped.
        """
        h = Harness(self.tmp)                      # no grant, no control_allow
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_control_grant_admits_a_session_the_list_never_named(self):
        """The point of the grant arm, and the reason it exists.

        A session minted after the last edit to `control_allow` is controllable
        without a second, manual edit to a file on another machine. Before this
        arm, every newly created session had to be added to `control_allow` by
        hand before it could be stopped — a floor nobody keeps up, and one that
        gets widened to `*` instead.
        """
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("control",)}})
        result = h.call("session_interrupt", args={"session": "brand-new"},
                        session_ref=ref(TOKYO, "brand-new"),
                        principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(result["tool"], "session_interrupt")
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls],
                         ["session_interrupt"])

    def test_a_control_grant_on_a_personal_device_is_inert_when_the_binding_narrows(self):
        """Eligibility is `policy.grant_eligible_ownership`, and a binding that
        names only `company` makes a personal device inert — the same rule the
        send arm already had. (Unnarrowed, the default admits `personal` too.)"""
        h = Harness(self.tmp, owner="personal",
                    grants={OAUTH: {TOKYO: ("control",)}},
                    eligible_ownership=("company",))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_interrupt", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
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
        # The session floor is satisfied for `h` so the cases below reach the
        # gates they are about; case 2 uses its own harness to show the floor
        # refusing first.
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("approve",)}})
        # 1. no `fleet:approve` scope -> refused at the scope gate.
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"requestId": "r1", "allow": True},
                   principal=h.principal(SCOPE_READ, SCOPE_SEND, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"requestId": "r1", "allow": True},
                   principal=h.principal(SCOPE_READ, SCOPE_SEND, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])
        # 2. scope present, no operator grant -> refused at the grant gate.
        #    The SESSION floor is closed here too (no `send_allow`, no grant), so
        #    that arm refuses first: an approval is answered for one session, and
        #    a caller with no reach into that session is refused before the
        #    device-level grant is read at all.
        ungranted = Harness(self.tmp, grants={})
        with self.assertRaises(FleetRefusal) as caught:
            ungranted.call("approval_answer",
                           args={"requestId": "r1", "allow": True},
                           session_ref=ref(TOKYO, "s1"),
                           principal=ungranted.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(ungranted.providers[TOKYO].calls, [])
        # 3. granted, but no requestId -> refused (an answer to nothing).
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"allow": True},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        # 4. granted, requestId present, but no user decision -> refused.
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer", args={"requestId": "r1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])
        # 5. scope + grant + a request the device ENUMERATES + the decision.
        result = h.call("approval_answer",
                        args={"requestId": "r1", "allow": True, "answers": {"a": "b"}},
                        session_ref=ref(TOKYO, "s1"),
                        principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(result["tool"], "approval_answer")
        # The ownership READ ran first, then the answer.
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls],
                         ["approvals_list", "approval_answer"])

    def test_approvals_list_is_read_only(self):
        h = Harness(self.tmp)
        got = h.call("approvals_list", session_ref=ref(TOKYO, "s1"),
                     principal=h.principal(SCOPE_READ))
        self.assertEqual(got["approvals"][0]["requestId"], "r1")

    def test_approvals_list_must_be_scoped_to_a_session(self):
        # Minimal disclosure: an unscoped register is other principals'
        # decisions, so it is refused rather than enumerated device-wide.
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approvals_list", principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_request_id_the_device_does_not_enumerate_is_refused(self):
        # The session floor is satisfied, so the ownership read is what refuses.
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("approve",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer",
                   args={"requestId": "someone-elses", "allow": True},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        # The answer was NEVER forwarded — only the ownership read ran.
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls], ["approvals_list"])

    def test_a_device_that_will_not_enumerate_refuses_the_answer(self):
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("approve",)}})
        h.providers[TOKYO]._fail = FleetRefusal("PROVIDER_UNREACHABLE", "no route")
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer",
                   args={"requestId": "r1", "allow": True},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_approvals_list_is_a_read(self):
        h = Harness(self.tmp)
        got = h.call("approvals_list", session_ref=ref(TOKYO, "s1"),
                     principal=h.principal(SCOPE_READ))
        self.assertIn("approvals", got)

    def test_ui_open_session_needs_the_navigate_scope_and_a_grant(self):
        """Navigation is bounded like every other session-addressed verb.

        It used to be gated on the DEVICE alone — the `fleet:navigate` scope plus
        the `navigate` operator grant — so on a shared company machine a caller
        could put any session on that machine in front of whoever was sitting at
        it, without being able to name that session in any per-session list. It
        now names the session at the outer layer and passes that session's own
        floor first.
        """
        # A scope it does not hold is still refused first.
        h = Harness(self.tmp, grants={OAUTH: {TOKYO: ("navigate",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("ui_open_session", args={"session": "s1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)

        # The scope, but no per-session floor for THIS session: refused before
        # the navigate grant is consulted at all.
        ungranted = Harness(self.tmp, grants={})
        with self.assertRaises(FleetRefusal) as caught:
            ungranted.call("ui_open_session", args={"session": "s1"},
                           session_ref=ref(TOKYO, "s1"),
                           principal=ungranted.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(ungranted.providers[TOKYO].calls, [])

        # The session floor open, the navigate grant still required.
        no_grant = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),))
        with self.assertRaises(FleetRefusal) as caught:
            no_grant.call("ui_open_session", args={"session": "s1"},
                          session_ref=ref(TOKYO, "s1"),
                          principal=no_grant.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(no_grant.providers[TOKYO].calls, [])

        # All three, and it reaches the device.
        allowed = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),),
                          grants={OAUTH: {TOKYO: ("navigate",)}})
        allowed.call("ui_open_session", args={"session": "s1"},
                     session_ref=ref(TOKYO, "s1"),
                     principal=allowed.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual([c[0] for c in allowed.providers[TOKYO].calls],
                         ["ui_open_session"])

    def test_navigation_must_name_the_session_it_navigates(self):
        """No `session_ref` at all, and a `session_ref` for a DIFFERENT session:
        the first is refused for naming nothing, the second for naming one thing
        while asking about another."""
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("navigate",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("ui_open_session", args={"session": "s1"},
                   principal=h.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("ui_open_session", args={"session": "s2"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_navigation_cannot_reach_a_session_the_send_floor_excludes(self):
        """The containment the change exists for: the device and the grant are
        not enough to reach a session the per-session floor does not name."""
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("navigate",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("ui_open_session", args={"session": "someone-elses"},
                   session_ref=ref(TOKYO, "someone-elses"),
                   principal=h.principal(SCOPE_READ, SCOPE_NAVIGATE))
        self.assertIn(caught.exception.code, (APPROVAL_REQUIRED, WRITE_NOT_GRANTED))
        self.assertEqual(h.providers[TOKYO].calls, [])

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
    def test_the_served_surface_is_the_eleven_plus_the_thirty_one(self):
        names = {t.name for t in tools_mod.SERVED_TOOLS}
        for tool in cloudcli_native.NATIVE_TOOLS:
            self.assertIn(tool.namespaced, names)
        mirrored = {t.name for t in tools_mod.NATIVE_MIRROR_TOOLS}
        self.assertEqual(len(mirrored), 31)
        # twelve hand-written + 31 mirrored + manifest reader + capability reader.
        self.assertEqual(len(names), 48)
        self.assertIn("fleet_cloudcli_native_manifest", names)
        self.assertIn("fleet_cloudcli_native_capability", names)
        self.assertEqual(len(tools_mod.tool_names()), 15)

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
    """All 31 tools × the routing and the deny-path, counted, not sampled."""

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
                extra = ({"session_ref": ref(TOKYO, "s1")}
                         if tool.name == "approvals_list" else {})
                result = h.call(tool.name, args=self._minimal_args(tool),
                                principal=h.principal(SCOPE_READ), **extra)
                if tool.name == "approvals_list":
                    self.assertIn("approvals", result)
                else:
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
        mirrored = {t.name for t in tools_mod.NATIVE_MIRROR_TOOLS}
        self.assertEqual(len(mirrored), 31)
        self.assertTrue(mirrored <= {t.name for t in tools_mod.SERVED_TOOLS})


class IdentityMismatchTest(unittest.TestCase):
    """BLOCKER-1: the address the floor passed on is the address forwarded."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _session_cases(self):
        # (tool, scope, grant verb) for every session-addressed write.
        return [
            ("session_send", (SCOPE_READ, SCOPE_SEND), None),
            ("session_interrupt", (SCOPE_READ, SCOPE_CONTROL), "control"),
            ("session_start", (SCOPE_READ, SCOPE_CONTROL), "control"),
            ("session_close", (SCOPE_READ, SCOPE_CONTROL), "control"),
            ("session_reconfigure", (SCOPE_READ, SCOPE_CONTROL), "control"),
            ("session_cancel_queued", (SCOPE_READ, SCOPE_CONTROL), "control"),
            ("session_background", (SCOPE_READ, SCOPE_CONTROL), "control"),
        ]

    def test_a_session_id_that_disagrees_with_the_floor_is_refused(self):
        for tool, scopes, verb in self._session_cases():
            with self.subTest(tool=tool):
                grants = {OAUTH: {TOKYO: (verb,)}} if verb else {}
                h = Harness(self.tmp, grants=grants,
                            send_allow=(ref(TOKYO, "s1"),) if not verb else (),
                            control_allow=(ref(TOKYO, "s1"),) if verb else ())
                args = {"session": "s2"}
                if tool == "session_send":
                    args["message"] = "hi"
                if tool == "session_cancel_queued":
                    args["messageUuid"] = "m1"
                if tool == "session_background":
                    args["stopTaskId"] = "t1"
                with self.assertRaises(FleetRefusal) as caught:
                    h.call(tool, args=args, session_ref=ref(TOKYO, "s1"),
                           principal=h.principal(*scopes))
                self.assertEqual(caught.exception.code, BAD_REQUEST)
                # The device was never reached with the mismatched address.
                self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_matching_session_id_is_forwarded(self):
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),))
        h.call("session_send", args={"session": "s1", "message": "hi"},
               session_ref=ref(TOKYO, "s1"),
               principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(h.providers[TOKYO].calls,
                         [("session_send", {"session": "s1", "message": "hi"})])

    def test_a_missing_session_id_in_arguments_is_refused(self):
        h = Harness(self.tmp, send_allow=(ref(TOKYO, "s1"),))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_send", args={"message": "hi"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_project_that_disagrees_with_the_floor_is_refused(self):
        from fleet_mcp.config import CreateGrant
        h = Harness(self.tmp, create_allow={"p1": CreateGrant()},
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create", args={"project": "p2"}, project="p1",
                   principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])


class ElevationCeilingTest(unittest.TestCase):
    """HIGH-3 / HIGH-4: a mirrored verb may not name more privilege than granted."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_may_not_name_a_permission_mode_the_project_forbids(self):
        from fleet_mcp.config import CreateGrant
        # The project grant allows no permission modes and no models by default.
        h = Harness(self.tmp, create_allow={"p": CreateGrant()},
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create",
                   args={"project": "p", "permissionMode": "bypassPermissions"},
                   project="p", principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_reconfigure_with_a_mode_needs_a_project_and_a_ceiling(self):
        h = Harness(self.tmp, control_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("control",)}})
        # No project named -> there is no ceiling to check the mode against.
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_reconfigure",
                   args={"session": "s1", "permissionMode": "bypassPermissions"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_reconfigure_with_a_mode_and_no_grant_for_the_project_is_refused(self):
        h = Harness(self.tmp, control_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("control",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_reconfigure",
                   args={"session": "s1", "permissionMode": "bypassPermissions"},
                   session_ref=ref(TOKYO, "s1"), project="p",
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        # The project was READ from the device; the reconfigure itself was not
        # forwarded.
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls], ["session_get"])


class ArgumentValidationTest(unittest.TestCase):
    """HIGH-7: an argument the native schema does not declare is refused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_an_unknown_native_argument_is_refused(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_get", args={"session": "s1", "oops": 1},
                   principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_missing_required_native_argument_is_refused(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_get", args={}, principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, BAD_REQUEST)

    def test_a_read_limit_is_clamped_to_the_surface_bound(self):
        h = Harness(self.tmp)
        h.call("session_read", args={"session": "s1", "limit": 1_000_000},
               principal=h.principal(SCOPE_READ))
        sent = h.providers[TOKYO].calls[0][1]
        self.assertLessEqual(sent["limit"], h.config.policy.max_read_messages)


class ConnectorFloorTest(unittest.TestCase):
    """BLOCKER-2: the DEVICE's own floor, on the mirrored route."""

    class _Provider:
        kind = "cloudcli-mcp-http"

        def __init__(self):
            self.calls = []

        def native_call(self, tool, arguments, *, session_ref=None):
            self.calls.append((tool, arguments))
            return {"ok": True}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _connector(self, **over):
        from fleet_mcp.node.connector import Connector, NodeConfig
        fields = dict(device_ref="device:tokyo-shared-dev-01",
                      secret_anchor=self.tmp / "secret",
                      cloudcli_endpoint="http://127.0.0.1:1",
                      cloudcli_credential_anchor=self.tmp / "cred")
        fields.update(over)
        connector = Connector(NodeConfig(**fields))
        connector.provider = self._Provider()
        return connector

    def _call(self, connector, tool, arguments):
        return connector._native_call({"tool": tool, "arguments": arguments})

    def test_send_is_refused_with_empty_allow_send_to(self):
        c = self._connector()
        with self.assertRaises(FleetRefusal) as caught:
            self._call(c, "session_send", {"session": "s1", "message": "hi"})
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(c.provider.calls, [])

    def test_send_is_allowed_when_the_session_is_named(self):
        c = self._connector(allow_send_to=("s1",))
        self.assertEqual(self._call(c, "session_send",
                                    {"session": "s1", "message": "hi"}), {"ok": True})
        self.assertEqual(c.provider.calls,
                         [("session_send", {"session": "s1", "message": "hi"})])

    def test_control_is_refused_when_allow_control_is_off(self):
        for tool in ("session_interrupt", "session_start", "session_close"):
            with self.subTest(tool=tool):
                c = self._connector()
                with self.assertRaises(FleetRefusal) as caught:
                    self._call(c, tool, {"session": "s1"})
                self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
                self.assertEqual(c.provider.calls, [])

    def test_create_refused_without_allow_create_then_project(self):
        c = self._connector()
        with self.assertRaises(FleetRefusal):
            self._call(c, "session_create", {"project": "p"})
        self.assertEqual(c.provider.calls, [])
        c2 = self._connector(allow_create=True)   # but names no project
        with self.assertRaises(FleetRefusal):
            self._call(c2, "session_create", {"project": "p"})
        self.assertEqual(c2.provider.calls, [])

    def test_approve_and_navigate_are_default_denied_on_the_device(self):
        for tool, args in (("approval_answer", {"requestId": "r", "allow": True}),
                           ("ui_open_session", {"session": "s1"})):
            with self.subTest(tool=tool):
                c = self._connector()
                with self.assertRaises(FleetRefusal) as caught:
                    self._call(c, tool, args)
                self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
                self.assertEqual(c.provider.calls, [])

    def test_reads_need_no_device_floor(self):
        c = self._connector()
        self.assertEqual(self._call(c, "sessions_list", {}), {"ok": True})

    def test_an_unrecorded_tool_is_refused(self):
        c = self._connector()
        with self.assertRaises(FleetRefusal) as caught:
            self._call(c, "session_teleport", {})
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(c.provider.calls, [])


class BackgroundStopGateTest(unittest.TestCase):
    """(1) `stopTaskId` PRESENCE decides the class; an empty value is refused."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_absent_stop_task_id_is_a_read(self):
        h = Harness(self.tmp)
        got = h.call("session_background", args={"session": "s1"},
                     principal=h.principal(SCOPE_READ))
        self.assertEqual(got["tool"], "session_background")

    def test_each_empty_value_is_refused_and_never_reads_as_a_list(self):
        h = Harness(self.tmp)
        for value in ("", "   ", None, False, 0):
            with self.subTest(value=value):
                with self.assertRaises(FleetRefusal) as caught:
                    h.call("session_background",
                           args={"session": "s1", "stopTaskId": value},
                           session_ref=ref(TOKYO, "s1"),
                           principal=h.principal(SCOPE_READ))
                self.assertEqual(caught.exception.code, BAD_REQUEST)
        # Nothing reached the device, under any of the empty spellings.
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_non_empty_stop_task_id_needs_control_not_read(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_background",
                   args={"session": "s1", "stopTaskId": "t1"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_the_device_floor_refuses_an_empty_stop_task_id_too(self):
        from fleet_mcp.node.connector import Connector, NodeConfig
        c = Connector(NodeConfig(device_ref=TOKYO, secret_anchor=self.tmp / "s",
                                 cloudcli_endpoint="http://x",
                                 cloudcli_credential_anchor=self.tmp / "c"))
        c.provider = FakeProvider(TOKYO)
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "session_background",
                            "arguments": {"session": "s1", "stopTaskId": ""}})
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(c.provider.calls, [])


class PublishedSchemaClosureTest(unittest.TestCase):
    """(2) The advertised closure is the enforced closure."""

    def test_every_mirrored_arguments_object_is_closed(self):
        for native in cloudcli_native.NATIVE_TOOLS:
            inner = cloudcli_native.mirrored_schema(native)["properties"]["arguments"]
            with self.subTest(tool=native.name):
                self.assertIs(inner.get("additionalProperties"), False,
                              f"{native.name} advertises an open arguments object")

    def test_the_served_tool_and_the_runtime_rule_agree_on_unknown_keys(self):
        tool = tools_mod.SERVED_BY_NAME["fleet_cloudcli_session_send"]
        inner = tool.input_schema["properties"]["arguments"]
        self.assertIs(inner["additionalProperties"], False)
        self.assertEqual(inner["required"], ["session", "message"])
        # The runtime refuses exactly what the schema says it will.
        self.assertEqual(
            cloudcli_native.validate_arguments({"session": "s", "message": "m", "x": 1},
                                               cloudcli_native.input_schema("session_send")),
            ["unknown parameter 'x'"])

    def test_a_tool_with_no_parameters_advertises_an_empty_closed_object(self):
        inner = cloudcli_native.mirrored_schema(
            cloudcli_native.NATIVE_BY_NAME["ui_clients_list"])["properties"]["arguments"]
        self.assertEqual(inner.get("properties"), {})
        self.assertIs(inner.get("additionalProperties"), False)


class ReconfigureProjectOwnershipTest(unittest.TestCase):
    """(3) The ceiling is the SESSION's real project, not the caller's claim."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _harness(self, **over):
        from fleet_mcp.config import CreateGrant
        return Harness(self.tmp,
                       control_allow=(ref(TOKYO, "s1"),),
                       grants={OAUTH: {TOKYO: ("control",)}},
                       create_allow=over.pop("create_allow", {"p": CreateGrant()}),
                       **over)

    def test_a_forged_project_is_refused(self):
        from fleet_mcp.config import CreateGrant
        # The session belongs to "p"; the caller declares a laxer "wide".
        h = self._harness(create_allow={"p": CreateGrant(), "wide": CreateGrant()})
        h.providers[TOKYO].project = "p"
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_reconfigure",
                   args={"session": "s1", "permissionMode": "acceptEdits"},
                   session_ref=ref(TOKYO, "s1"), project="wide",
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_a_session_whose_project_the_device_omits_is_refused(self):
        h = self._harness()
        h.providers[TOKYO].project = None      # device states no project
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_reconfigure",
                   args={"session": "s1", "permissionMode": "acceptEdits"},
                   session_ref=ref(TOKYO, "s1"), project="p",
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_a_project_not_in_create_allow_is_refused(self):
        h = self._harness(create_allow={})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_reconfigure",
                   args={"session": "s1", "permissionMode": "acceptEdits"},
                   session_ref=ref(TOKYO, "s1"), project="p",
                   principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_a_permitted_mode_on_the_sessions_own_project_is_allowed(self):
        from fleet_mcp.config import CreateGrant
        h = self._harness(create_allow={"p": CreateGrant(permission_modes=("acceptEdits",))})
        got = h.call("session_reconfigure",
                     args={"session": "s1", "permissionMode": "acceptEdits"},
                     session_ref=ref(TOKYO, "s1"), project="p",
                     principal=h.principal(SCOPE_READ, SCOPE_CONTROL))
        self.assertEqual(got["tool"], "session_reconfigure")
        # The device was asked for the project BEFORE it was reconfigured.
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls],
                         ["session_get", "session_reconfigure"])


class ReadSizeCapTest(unittest.TestCase):
    """The mirrored answer is measured before it leaves; oversize is REFUSED."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _overhead():
        """Bytes a read result carries before its content: the markers."""
        from fleet_mcp.privacy import UNTRUSTED_NOTICE
        payload = {"blob": "", "untrusted_content": True,
                   "untrusted_notice": UNTRUSTED_NOTICE}
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _sized(self, nbytes):
        return {"blob": "x" * (nbytes - self._overhead())}

    def _harness(self, result, **over):
        h = Harness(self.tmp, **over)
        h.providers[TOKYO].result = result
        return h

    def test_exactly_at_the_limit_is_allowed(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        h = self._harness(self._sized(MAX_NATIVE_RESULT_BYTES))
        got = h.call("sessions_list", principal=h.principal(SCOPE_READ))
        self.assertEqual(got["blob"][:1], "x")

    def test_one_byte_over_is_refused_without_relaying_the_text(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        from fleet_mcp.errors import OUTPUT_TOO_LARGE
        h = self._harness(self._sized(MAX_NATIVE_RESULT_BYTES + 1))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("sessions_list", principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, OUTPUT_TOO_LARGE)
        detail = caught.exception.detail
        self.assertIn(str(MAX_NATIVE_RESULT_BYTES), detail)
        # The oversized body is MEASURED, never copied into the error.
        self.assertNotIn("xxxx", detail)

    def test_multibyte_is_measured_in_bytes_not_characters(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        from fleet_mcp.errors import OUTPUT_TOO_LARGE
        # Fewer characters than the limit, more BYTES than the limit.
        over = MAX_NATIVE_RESULT_BYTES - self._overhead() + 3
        blob = "\u3042" * (over // 3)          # 3 bytes each in UTF-8
        h = self._harness({"blob": blob})
        self.assertLess(len(blob), MAX_NATIVE_RESULT_BYTES)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("sessions_list", principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, OUTPUT_TOO_LARGE)

    def test_a_batch_of_many_messages_is_measured_as_a_whole(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        from fleet_mcp.errors import OUTPUT_TOO_LARGE
        many = {"messages": [{"i": i, "t": "m" * 400} for i in range(100000)]}
        h = self._harness(many)
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_read", args={"session": "s1"},
                   principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, OUTPUT_TOO_LARGE)

    def test_the_bounded_read_is_the_same_on_a_node_device(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        from fleet_mcp.errors import OUTPUT_TOO_LARGE
        h = self._harness(self._sized(MAX_NATIVE_RESULT_BYTES + 1),
                          kinds={TOKYO: "fleet-node"})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("sessions_list", principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, OUTPUT_TOO_LARGE)

    def test_a_write_result_is_bounded_too(self):
        from fleet_mcp.fleet import MAX_NATIVE_RESULT_BYTES
        from fleet_mcp.errors import OUTPUT_TOO_LARGE
        h = self._harness({"blob": "x" * (MAX_NATIVE_RESULT_BYTES + 1)},
                          send_allow=(ref(TOKYO, "s1"),))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_send", args={"session": "s1", "message": "hi"},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_SEND))
        self.assertEqual(caught.exception.code, OUTPUT_TOO_LARGE)


class DeviceApprovalOwnershipTest(unittest.TestCase):
    """(A) The DEVICE re-checks approval ownership from its OWN register."""

    class _Provider:
        kind = "cloudcli-mcp-http"

        def __init__(self, ids=("r1",)):
            self.calls = []
            self.ids = ids
            self.fail = None

        def native_call(self, tool, arguments, *, session_ref=None):
            self.calls.append((tool, arguments))
            if self.fail is not None:
                raise self.fail
            if tool == "approvals_list":
                return {"approvals": [{"requestId": i} for i in self.ids]}
            return {"ok": True, "tool": tool}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _connector(self, **over):
        from fleet_mcp.node.connector import Connector, NodeConfig
        fields = dict(device_ref=TOKYO, secret_anchor=self.tmp / "s",
                      cloudcli_endpoint="http://x",
                      cloudcli_credential_anchor=self.tmp / "c", allow_approve=True)
        fields.update(over)
        c = Connector(NodeConfig(**fields))
        c.provider = self._Provider()
        return c

    def _answered(self, connector):
        return "approval_answer" in [c[0] for c in connector.provider.calls]

    def test_allow_approve_off_refuses_before_anything(self):
        c = self._connector(allow_approve=False)
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "approval_answer",
                            "session_ref": ref(TOKYO, "s1"),
                            "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertFalse(self._answered(c))

    def test_an_answer_with_no_session_proof_is_refused(self):
        c = self._connector()
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "approval_answer",
                            "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(c.provider.calls, [])

    def test_a_proof_for_another_device_is_refused(self):
        c = self._connector()
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "approval_answer",
                            "session_ref": ref(WINDOWS, "s1"),
                            "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertFalse(self._answered(c))

    def test_a_request_this_machine_does_not_enumerate_is_refused(self):
        c = self._connector()          # enumerates only r1
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "approval_answer",
                            "session_ref": ref(TOKYO, "s1"),
                            "arguments": {"requestId": "someone-elses",
                                          "allow": True}})
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertFalse(self._answered(c))

    def test_a_device_that_will_not_enumerate_refuses_the_answer(self):
        c = self._connector()
        c.provider.fail = FleetRefusal("PROVIDER_UNREACHABLE", "no route")
        with self.assertRaises(FleetRefusal) as caught:
            c._native_call({"tool": "approval_answer",
                            "session_ref": ref(TOKYO, "s1"),
                            "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertFalse(self._answered(c))

    def test_an_enumerated_request_is_forwarded_only_after_the_check(self):
        c = self._connector()
        got = c._native_call({"tool": "approval_answer",
                              "session_ref": ref(TOKYO, "s1"),
                              "arguments": {"requestId": "r1", "allow": True}})
        self.assertEqual(got["ok"], True)
        self.assertEqual([x[0] for x in c.provider.calls],
                         ["approvals_list", "approval_answer"])


class CapabilitySnapshotTest(unittest.TestCase):
    """(B) A read-only per-device snapshot; a missing verb is named, not assumed."""

    def test_a_missing_verb_is_unsupported_by_device(self):
        snap = cloudcli_native.capability_snapshot(
            device_ref=TOKYO, observed={"session_send": {"annotations": {}, "schema": {}}})
        self.assertEqual(snap["state"], "OBSERVED")
        self.assertEqual(snap["observed_count"], 1)
        self.assertEqual(snap["tools"]["session_send"]["state"],
                         cloudcli_native.HANDLER_BINDING_UNKNOWN)
        self.assertEqual(snap["tools"]["overview"]["state"],
                         cloudcli_native.UNSUPPORTED_BY_DEVICE)
        self.assertEqual(len(snap["unsupported_by_device"]), 30)

    def test_an_unreadable_device_is_unknown_not_complete(self):
        snap = cloudcli_native.capability_snapshot(device_ref=TOKYO,
                                                   reason="needs a credential")
        self.assertEqual(snap["state"], "UNKNOWN")
        self.assertIsNone(snap["observed_count"])
        self.assertEqual(len(snap["unsupported_by_device"]), 31)
        self.assertIn("credential", snap["reason"])

    def test_a_verb_the_build_did_not_record_is_reported_not_added(self):
        snap = cloudcli_native.capability_snapshot(
            device_ref=TOKYO, observed={"overview": {}, "session_teleport": {}})
        self.assertEqual(snap["unrecorded_by_build"], ["session_teleport"])
        self.assertEqual(len(snap["tools"]), 31)   # the recorded set, unchanged

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_service_snapshot_is_read_scoped(self):
        h = Harness(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.native_capability(h.principal(), device_ref=TOKYO)
        self.assertEqual(caught.exception.code, SCOPE_DENIED)

    def test_a_provider_that_cannot_enumerate_is_unknown(self):
        h = Harness(self.tmp)
        snap = h.service.native_capability(h.principal(SCOPE_READ), device_ref=TOKYO)
        self.assertEqual(snap["state"], "UNKNOWN")
        self.assertIn("capability snapshot", snap["reason"])

    def test_the_tool_exposes_the_snapshot(self):
        from fleet_mcp.tools import ToolContext
        h = Harness(self.tmp)
        ctx = ToolContext(service=h.service, principal=h.principal(SCOPE_READ))
        got = tools_mod.SERVED_BY_NAME["fleet_cloudcli_native_capability"].handler(
            ctx, {"device_ref": TOKYO})
        self.assertEqual(got["state"], "UNKNOWN")

    # -- a declared verb is not a verb that answers -------------------------

    def test_an_explicitly_unbound_placeholder_is_never_usable(self):
        """An absent handler must be reported from live registration evidence."""
        observed = {t.name: {"annotations": {}, "schema": {},
                            "_meta": {"cloudcli/handlerBound": not t.placeholder_default}}
                    for t in cloudcli_native.NATIVE_TOOLS}
        snap = cloudcli_native.capability_snapshot(device_ref=TOKYO, observed=observed)
        placeholders = sorted(t.name for t in cloudcli_native.NATIVE_TOOLS
                              if t.placeholder_default)
        self.assertTrue(placeholders, "the recorded set must still carry placeholders")
        for name in placeholders:
            self.assertEqual(snap["tools"][name]["state"],
                             cloudcli_native.DECLARED_NOT_IMPLEMENTED)
            self.assertNotEqual(snap["tools"][name]["state"], cloudcli_native.USABLE)
            self.assertIs(snap["tools"][name]["placeholder_default"], True)
        self.assertEqual(snap["declared_not_implemented"], placeholders)

    def test_usable_is_exactly_the_live_wired_verbs(self):
        """Availability is based on current registration, not old defaults."""
        observed = {t.name: {"_meta": {"cloudcli/handlerBound": not t.placeholder_default}}
                    for t in cloudcli_native.NATIVE_TOOLS}
        snap = cloudcli_native.capability_snapshot(device_ref=TOKYO, observed=observed)
        usable = sorted(n for n, v in snap["tools"].items()
                        if v["state"] == cloudcli_native.USABLE)
        wired = sorted(t.name for t in cloudcli_native.NATIVE_TOOLS
                       if not t.placeholder_default)
        self.assertEqual(usable, wired)
        self.assertLess(len(usable), cloudcli_native.NATIVE_TOOL_COUNT)
        self.assertEqual(len(usable) + len(snap["declared_not_implemented"]),
                         cloudcli_native.NATIVE_TOOL_COUNT)

    def test_an_unknown_snapshot_claims_no_usable_verb(self):
        snap = cloudcli_native.capability_snapshot(device_ref=TOKYO, reason="no list")
        self.assertEqual(snap["state"], "UNKNOWN")
        self.assertEqual(snap["tools"], {})
        self.assertEqual(snap["declared_not_implemented"], [])

    def test_the_capability_snapshot_is_rate_limited(self):
        """It reads the device's `tools/list`, so it is an outbound call like any
        other and must pass the limiter every other read passes."""
        h = Harness(self.tmp)
        h.service.limiter = RateLimiter(1)
        first = h.service.native_capability(h.principal(SCOPE_READ), device_ref=TOKYO)
        self.assertEqual(first["state"], "UNKNOWN")   # the fake has no snapshot
        with self.assertRaises(FleetRefusal) as caught:
            h.service.native_capability(h.principal(SCOPE_READ), device_ref=TOKYO)
        self.assertEqual(caught.exception.code, RATE_LIMITED)


# ---------------------------------------------------------------------------
# The recorded table, held against the build it claims to record
# ---------------------------------------------------------------------------
#
# Everything above tests the mirror's BEHAVIOUR. This block tests its
# PROVENANCE: `NATIVE_INPUT_SCHEMAS`, the four annotation hints and
# `placeholder_default` are restatements of what CloudCLI 1.39.0 declares, and a
# restatement is worth exactly as much as the check that keeps it in step with
# the original. That check is a verbatim capture of the live gateway's own
# `tools/list`:
#
#     tests/data/capture-cloudcli-1.39.0-tools-list-2026-10-14.json
#
# The sentences these tests keep false:
#
#     "the recorded schema is the device's schema"       — a constraint the
#                               device declares and the table drops is a
#                               published schema describing a DIFFERENT tool
#                               than the one it is published for
#     "placeholder is a matter of opinion"               — a tool whose default
#                               handler is the named refusal says so in the
#                               device's own `_meta`, so the flag is checkable
#                               against the wire rather than against prose
#     "the capture is the contract"                      — it is an
#                               OBSERVATION, true of the one build it names;
#                               the name is pinned here so that comparing a
#                               different build's capture is a deliberate act
#
# The direction of every check below is one-way on purpose: the recorded table
# may be STRICTER than the device (it publishes `additionalProperties: false`
# where the device states nothing — that refuses earlier and never widens), but
# it may never state LESS, because a parameter the caller is told about and the
# device does not accept is a lie the other way round.

CAPTURE = (Path(__file__).resolve().parent / "data"
           / "capture-cloudcli-1.40.0-tools-list-2026-10-23.json")

#: The build the recorded baseline claims to be. A capture naming another build
#: must not be quietly held against this table. The value is the package version
#: the device reported, suffix included — the fork's revision is part of which
#: build the schemas were read from.
CAPTURED_CLOUDCLI_VERSION = "1.40.0-fleet.1"

#: CloudCLI's code for "registered, but its behaviour is delivered elsewhere" —
#: the fact `placeholder_default` records.
NOT_IMPLEMENTED_CODE = "MCP_TOOL_NOT_IMPLEMENTED"


def _capture():
    with CAPTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def _missing_constraints(recorded, live, where):
    """Constraints `live` states that `recorded` does not, as strings."""
    if not isinstance(live, dict):
        return []
    if not isinstance(recorded, dict):
        return [f"{where}: device states {json.dumps(live, sort_keys=True)}; "
                f"recorded is {recorded!r}"]
    problems = []
    for key, want in live.items():
        if key in ("description", "title", "$schema"):
            continue
        if key == "properties":
            got = recorded.get("properties")
            if not isinstance(got, dict):
                problems.append(f"{where}/properties: device declares properties; "
                                f"recorded has none")
                continue
            for name, sub in want.items():
                if name not in got:
                    problems.append(f"{where}/properties: device declares {name!r}; "
                                    f"recorded omits it")
                else:
                    problems.extend(_missing_constraints(
                        got[name], sub, f"{where}/properties/{name}"))
            continue
        if key == "required":
            got = recorded.get("required") or []
            for name in want:
                if name not in got:
                    problems.append(f"{where}/required: device requires {name!r}; "
                                    f"recorded does not")
            continue
        if key in ("anyOf", "oneOf", "allOf"):
            got = recorded.get(key)
            if not isinstance(got, list) or len(got) != len(want):
                problems.append(f"{where}/{key}: device declares {len(want)} branches; "
                                f"recorded has {got!r}")
                continue
            for index, (branch_want, branch_got) in enumerate(zip(want, got)):
                problems.extend(_missing_constraints(
                    branch_got, branch_want, f"{where}/{key}[{index}]"))
            continue
        got = recorded.get(key, "<absent>")
        if got != want:
            problems.append(f"{where}/{key}: device says {want!r}; recorded says {got!r}")
    return problems


class RecordedTableMatchesTheBuildTest(unittest.TestCase):
    """Every restatement in `cloudcli_native`, held against the capture it restates."""

    def test_the_capture_is_the_build_the_table_claims(self):
        capture = _capture()
        self.assertEqual(capture["server"]["name"], "claudecodeui-mcp-gateway")
        self.assertEqual(capture["protocolVersion"], "2025-06-18")
        self.assertEqual(capture["cloudcli_package_version"], CAPTURED_CLOUDCLI_VERSION)
        self.assertEqual(
            len(capture["tools"]),
            cloudcli_native.NATIVE_TOOL_COUNT
            + len(cloudcli_native.EXTENSION_CLAIMED_NATIVE_TOOLS),
        )
        # The device's list is the MIRRORED set PLUS the verbs this build
        # reaches through authored tools (`project_create` / `project_delete`).
        # Comparing against the mirror alone would report those two as drift on
        # every device, and comparing against the capture alone would let a
        # mirrored verb disappear unnoticed.
        self.assertEqual(
            sorted(entry["name"] for entry in capture["tools"]),
            sorted(set(cloudcli_native.NATIVE_BY_NAME)
                   | set(cloudcli_native.EXTENSION_CLAIMED_NATIVE_TOOLS)),
        )

    def test_no_recorded_schema_omits_a_constraint_the_device_declares(self):
        problems = []
        for entry in _capture()["tools"]:
            recorded = cloudcli_native.NATIVE_INPUT_SCHEMAS.get(entry["name"])
            self.assertIsNotNone(recorded, f"{entry['name']} has no recorded schema")
            problems.extend(_missing_constraints(
                recorded, entry["inputSchema"] or {}, entry["name"]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_the_PUBLISHED_arguments_schema_omits_nothing_the_device_declares(self):
        """The same check against the schema a caller actually receives.

        The recorded table is only half the story — `mirrored_schema` decides
        what `tools/list` shows, so a table that were right and a projection
        that dropped it would still publish a wrong tool.
        """
        problems = []
        for entry in _capture()["tools"]:
            # A claimed verb has no mirror to compare: its authored tool takes
            # the caller's own fields (`project_id` / `confirm_path`), and that
            # schema is asserted in `test_fleet_mcp_surface`. Holding it against
            # the native schema would compare two deliberately different shapes.
            if entry["name"] in cloudcli_native.EXTENSION_CLAIMED_NATIVE_TOOLS:
                continue
            native = cloudcli_native.NATIVE_BY_NAME[entry["name"]]
            published = tools_mod.SERVED_BY_NAME[native.namespaced].input_schema
            arguments = (published.get("properties") or {}).get("arguments") or {}
            problems.extend(_missing_constraints(
                arguments, entry["inputSchema"] or {}, entry["name"]))
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_recorded_annotations_are_the_capture_s_annotations(self):
        moved = []
        for entry in _capture()["tools"]:
            # Claimed verbs are not mirrored, so the hints a caller sees for
            # them are Fleet's own (on `fleet_create_project` /
            # `fleet_delete_project`), not a restatement of the device's.
            if entry["name"] in cloudcli_native.EXTENSION_CLAIMED_NATIVE_TOOLS:
                continue
            tool = cloudcli_native.NATIVE_BY_NAME[entry["name"]]
            declared = {
                "readOnlyHint": tool.read_only,
                "destructiveHint": tool.destructive,
                "idempotentHint": tool.idempotent,
                "openWorldHint": tool.open_world,
            }
            for hint, want in (entry["annotations"] or {}).items():
                if declared.get(hint) != want:
                    moved.append(f"{entry['name']}/{hint}: device {want!r}; "
                                 f"recorded {declared.get(hint)!r}")
        self.assertEqual(moved, [], "\n" + "\n".join(moved))

    def test_placeholder_default_is_the_capture_s_not_implemented_set(self):
        """Checkable against the wire, not against prose.

        A tool whose default handler is the named `MCP_TOOL_NOT_IMPLEMENTED`
        refusal advertises that code in its own `_meta`, so the recorded flag is
        a fact the device states. A flag that were set from memory would be
        wrong in whichever direction the memory drifted.
        """
        on_the_wire = {
            entry["name"] for entry in _capture()["tools"]
            # A claimed verb advertises the code too — the gateway publishes it
            # on every handler that can ever reach an unimplemented body — but
            # it is not in `NATIVE_TOOLS`, so `placeholder_default` has no row
            # to hold the claim and the comparison would be against a set this
            # build never mirrors. Excluding it here is the same fact recorded
            # in `EXTENSION_CLAIMED_NATIVE_TOOLS`.
            if entry["name"] not in cloudcli_native.EXTENSION_CLAIMED_NATIVE_TOOLS
            and NOT_IMPLEMENTED_CODE
            in ((entry.get("_meta") or {}).get("cloudcli/errorCodes") or [])
        }
        recorded = {tool.name for tool in cloudcli_native.NATIVE_TOOLS
                    if tool.placeholder_default}
        self.assertEqual(recorded, on_the_wire,
                         f"recorded {sorted(recorded)}; "
                         f"the device declares {sorted(on_the_wire)}")


class AdvertisedSchemaInvariantTest(unittest.TestCase):
    """Every published mirror schema is one a client can actually satisfy.

    `fleet_cloudcli_session_reconfigure` and `fleet_cloudcli_approval_answer`
    required an outer `session_ref` that their `properties` never declared, under
    a top-level `additionalProperties: false`. A client that validates its call
    against the published schema therefore had to send a field the same schema
    forbade, and both tools were uncallable by anything that validates. No
    runtime check could see it: the runtime reads `session_ref` from the request
    and never from the schema, so the two halves of the contract drifted with
    every test still green.

    The invariant is stated once, structurally, and walked at every depth of
    every mirrored tool: **a key in `required` is a key in `properties`**. It is
    checked by walking rather than by a JSON Schema library because this property
    is exactly that walk, and this repository carries no schema validator (the
    evidence tests carry their own assertions for the same reason).
    """

    def _undeclared_required(self, node, path, out):
        if not isinstance(node, dict):
            return
        props = node.get("properties") or {}
        for name in node.get("required") or []:
            if name not in props:
                out.append(f"{path or 'root'}.{name}")
        for name, sub in props.items():
            self._undeclared_required(sub, f"{path}.{name}" if path else name, out)

    def test_no_mirrored_schema_requires_a_key_it_does_not_declare(self):
        broken: list[str] = []
        for native in cloudcli_native.NATIVE_TOOLS:
            self._undeclared_required(cloudcli_native.mirrored_schema(native), "", broken)
        self.assertEqual(broken, [], "a required key the schema does not declare "
                                     "is a call no validating client can make")

    def test_the_two_that_were_broken_declare_what_they_require(self):
        for name in ("session_reconfigure", "approval_answer"):
            with self.subTest(tool=name):
                schema = cloudcli_native.mirrored_schema(cloudcli_native.NATIVE_BY_NAME[name])
                self.assertIn("session_ref", schema["properties"])
                self.assertIn("session_ref", schema["required"])

    def test_approvals_list_declares_the_session_ref_the_runtime_requires(self):
        """A READ whose disclosure the runtime scopes to one session.

        `fleet.py` refuses an `approvals_list` that names no session and then
        writes the named session into `arguments.session`, so the published
        schema owes the caller the same key. It declared nothing: the tool is
        `GOV_READ`, and the session axis was read off the governance class
        alone, so a validating client had to supply a key the same schema's
        top-level `additionalProperties: false` forbade. That is the defect
        `session_reconfigure` and `approval_answer` already carried, surviving
        in the one class the predicate did not cover.
        """
        native = cloudcli_native.NATIVE_BY_NAME["approvals_list"]
        self.assertEqual(native.governance, cloudcli_native.GOV_READ)
        schema = cloudcli_native.mirrored_schema(native)
        self.assertIn("session_ref", schema["properties"])
        self.assertIn("session_ref", schema["required"])
        # Still a read: no project axis, and `arguments` itself stays optional
        # because the native `session` is optional — the runtime fills it from
        # `session_ref` rather than reading it out of the payload.
        self.assertNotIn("project", schema["properties"])
        self.assertEqual(schema["required"], ["device_ref", "session_ref"])

    def test_the_session_addressed_reads_are_named_one_by_one(self):
        """A read the runtime scopes to a session is added deliberately.

        Derived from nothing: the governance-class predicate cannot express it,
        so the exception is written down where a reviewer reads it.
        """
        self.assertEqual(cloudcli_native.SESSION_ADDRESSED_READS,
                         frozenset({"approvals_list"}))
        for name in cloudcli_native.SESSION_ADDRESSED_READS:
            native = cloudcli_native.NATIVE_BY_NAME[name]
            with self.subTest(tool=name):
                self.assertTrue(native.read_only,
                                "a session-addressed READ must still be a read")

    def test_every_session_addressed_class_declares_and_requires_session_ref(self):
        """Read from the same predicate the schema builder uses, so a class added
        to one half later cannot leave the other behind — which is the shape of
        the defect, not the two names it happened to hit."""
        for governance in cloudcli_native.SESSION_ADDRESSED_GOVERNANCE:
            native = next((t for t in cloudcli_native.NATIVE_TOOLS
                           if t.governance == governance), None)
            with self.subTest(governance=governance):
                self.assertIsNotNone(native, f"no recorded tool uses {governance}")
                schema = cloudcli_native.mirrored_schema(native)
                self.assertIn("session_ref", schema["properties"])
                self.assertIn("session_ref", schema["required"])

    def test_the_project_axis_is_a_floor_for_create_and_a_claim_for_reconfigure(self):
        """`project` means two different things, and the schema says which.

        For CREATE it is the FLOOR — the project the new session is minted
        under — so it is a property and required. For RECONFIGURE it is a
        CLAIM: the ceiling is the project the target session really belongs to,
        read from the device, and the caller's `project` only has to agree with
        it. It therefore has to exist in the schema (the runtime refuses a
        model/mode change that names none, and `session_reconfigure`'s native
        schema carries no `project` to inherit), but it cannot be REQUIRED: a
        reconfigure that changes neither `model` nor `permissionMode` declares
        no ceiling at all. Until 2026-10-23 this test asserted the axis belonged
        to create alone, which described the intent but not the satisfiability —
        every model/mode reconfigure was refused for naming no project that the
        schema would not accept.
        """
        for native in cloudcli_native.NATIVE_TOOLS:
            with self.subTest(tool=native.name):
                schema = cloudcli_native.mirrored_schema(native)
                if native.governance == cloudcli_native.GOV_CREATE:
                    self.assertIn("project", schema["properties"])
                    self.assertIn("project", schema["required"])
                elif native.governance == cloudcli_native.GOV_RECONFIGURE:
                    self.assertIn("project", schema["properties"])
                    self.assertNotIn("project", schema["required"])
                else:
                    self.assertNotIn("project", schema["properties"])
                    self.assertNotIn("project", schema["required"])

    def test_a_nested_required_key_is_declared_at_its_own_level(self):
        """`ui_open_session.at` is an `anyOf` of two objects, each with its own
        `required` — the depth the top-level check alone would miss."""
        schema = cloudcli_native.mirrored_schema(
            cloudcli_native.NATIVE_BY_NAME["ui_open_session"])
        branches = schema["properties"]["arguments"]["properties"]["at"]["anyOf"]
        self.assertTrue(branches)
        for branch in branches:
            self.assertTrue(set(branch.get("required") or [])
                            <= set(branch.get("properties") or {}), branch)

    def test_a_valid_call_exists_for_every_mirrored_tool(self):
        """The satisfiability half, stated as construction: fill in exactly what
        each schema asks for, at every depth, and never invent a key the schema
        does not carry. Before the fix this raised for the two broken tools."""
        def least(node, path, out, depth=0):
            if depth > 8 or not isinstance(node, dict):
                return "x"
            if "anyOf" in node:
                return least((node["anyOf"] or [{}])[0], path, out, depth + 1)
            if "properties" in node or node.get("type") == "object":
                props = node.get("properties") or {}
                built = {}
                for name in node.get("required") or []:
                    if name not in props:
                        out.append(f"{path}.{name}")
                        continue
                    built[name] = least(props[name], f"{path}.{name}", out, depth + 1)
                return built
            if node.get("type") == "boolean":
                return True
            if node.get("type") in ("integer", "number"):
                return 1
            return "x"

        for native in cloudcli_native.NATIVE_TOOLS:
            with self.subTest(tool=native.name):
                missing: list[str] = []
                instance = least(cloudcli_native.mirrored_schema(native), "root", missing)
                self.assertEqual(missing, [])
                self.assertEqual(set(instance) >= set(
                    cloudcli_native.mirrored_schema(native)["required"]), True)

    def test_the_outer_closure_still_refuses_an_unknown_key(self):
        """The fix declares `session_ref`; it must not have opened the outer
        object to keep the schema satisfiable."""
        for native in cloudcli_native.NATIVE_TOOLS:
            with self.subTest(tool=native.name):
                self.assertIs(cloudcli_native.mirrored_schema(native)
                              .get("additionalProperties"), False)

    def test_the_arguments_object_mirrors_the_native_closure(self):
        """What is advertised inside `arguments` is what the runtime validator
        enforces, so a call that validates is a call that passes."""
        for native in cloudcli_native.NATIVE_TOOLS:
            with self.subTest(tool=native.name):
                recorded = cloudcli_native.NATIVE_INPUT_SCHEMAS.get(native.name) or {}
                advertised = (cloudcli_native.mirrored_schema(native)
                              ["properties"]["arguments"])
                self.assertEqual(advertised.get("additionalProperties"),
                                 recorded.get("additionalProperties", True))
                self.assertEqual(sorted(advertised.get("properties") or {}),
                                 sorted(recorded.get("properties") or {}))


class ApprovalSessionFloorTest(unittest.TestCase):
    """An approval is answered for ONE session, and the device arm is not enough.

    Before this floor the `approve` branch checked the requestId, the device and
    the device-level `approve` grant — and nothing about the session beyond its
    address. A principal holding `approve` for a machine could therefore answer
    an approval raised in ANY session on it, which on a shared company
    workstation means answering a request whose owner never gave that decision.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _h(self, **over):
        return Harness(self.tmp, **over)

    def test_a_device_wide_approve_grant_does_not_reach_another_session(self):
        """The reported defect, as a test: the grant names the device, the floor
        names the session, and the session is the one that decides."""
        h = self._h(send_allow=(ref(TOKYO, "mine"),),
                    grants={OAUTH: {TOKYO: ("approve",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer",
                   args={"requestId": "r1", "allow": True},
                   session_ref=ref(TOKYO, "someone-elses"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_the_same_grant_does_answer_its_own_session(self):
        h = self._h(send_allow=(ref(TOKYO, "mine"),),
                    grants={OAUTH: {TOKYO: ("approve",)}})
        got = h.call("approval_answer",
                     args={"requestId": "r1", "allow": True},
                     session_ref=ref(TOKYO, "mine"),
                     principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(got["ok"], True)
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls],
                         ["approvals_list", "approval_answer"])

    def test_no_reach_into_the_session_refuses_before_the_grant_is_read(self):
        """No `send_allow`, no send grant: the session arm refuses first."""
        h = self._h(grants={OAUTH: {TOKYO: ("approve",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer",
                   args={"requestId": "r1", "allow": True},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_the_human_decision_is_still_required_and_still_a_boolean(self):
        """The floor was ADDED; the allow/deny requirement is unchanged."""
        h = self._h(send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("approve", "send")}})
        for bad in ({}, {"requestId": "r1"}, {"requestId": "r1", "allow": "yes"},
                    {"requestId": "r1", "allow": 1}):
            with self.subTest(args=bad):
                with self.assertRaises(FleetRefusal) as caught:
                    h.call("approval_answer", args=bad,
                           session_ref=ref(TOKYO, "s1"),
                           principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
                self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_personal_device_cannot_be_approved_on_either_way(self):
        """The device-level grant is inert on a machine the organization does
        not own, so the `approve` operator verb refuses — and the session floor
        would have refused it too had the grant been read first."""
        h = self._h(owner="personal", send_allow=(ref(TOKYO, "s1"),),
                    grants={OAUTH: {TOKYO: ("approve", "send")}},
                    eligible_ownership=("company",))
        with self.assertRaises(FleetRefusal) as caught:
            h.call("approval_answer",
                   args={"requestId": "r1", "allow": True},
                   session_ref=ref(TOKYO, "s1"),
                   principal=h.principal(SCOPE_READ, SCOPE_APPROVE))
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.providers[TOKYO].calls, [])


class CreateAuthorizationMatrixTest(unittest.TestCase):
    """Creation needs four host gates together, and each one alone is not enough.

    `OAuth fleet:create` scope, a `create` operator grant for this principal on
    this device, the provider's own `allow_create`, and the project named in
    `policy.create_allow`. The two device-side gates (`Node.allow_create` and
    `Node.allow_create_projects`) live on the connector and are covered by
    `tests/test_fleet_mcp_node.py`; these are the four this side owns.
    """

    PROJECT = "p-1"
    GRANT = {PROJECT: CreateGrant(permission_modes=("default",), models=())}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _call(self, h):
        return h.call("session_create", args={"project": self.PROJECT},
                      project=self.PROJECT,
                      principal=h.principal(SCOPE_READ, SCOPE_CREATE))

    def test_all_four_together_admit(self):
        h = Harness(self.tmp, create_allow=dict(self.GRANT),
                    allow_create=True, grants={OAUTH: {TOKYO: ("create",)}})
        self.assertEqual(self._call(h)["tool"], "session_create")
        self.assertEqual([c[0] for c in h.providers[TOKYO].calls], ["session_create"])

    def test_without_the_scope_it_is_refused(self):
        h = Harness(self.tmp, create_allow=dict(self.GRANT), allow_create=True,
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create", args={"project": self.PROJECT},
                   project=self.PROJECT,
                   principal=h.principal(SCOPE_READ))
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_without_the_operator_grant_it_is_refused(self):
        h = Harness(self.tmp, create_allow=dict(self.GRANT), allow_create=True,
                    grants={})
        with self.assertRaises(FleetRefusal) as caught:
            self._call(h)
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_without_the_device_flag_it_is_refused(self):
        h = Harness(self.tmp, create_allow=dict(self.GRANT), allow_create=False,
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            self._call(h)
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_project_outside_the_allowlist_is_refused(self):
        h = Harness(self.tmp, create_allow={"some-other": CreateGrant()},
                    allow_create=True, grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            self._call(h)
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_permission_mode_the_project_does_not_allow_is_refused(self):
        """`bypassPermissions` is not reachable by naming it: the project's own
        `permission_modes` decides, and this project allows only `default`."""
        h = Harness(self.tmp, create_allow=dict(self.GRANT), allow_create=True,
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.call("session_create",
                   args={"project": self.PROJECT, "permissionMode": "bypassPermissions"},
                   project=self.PROJECT,
                   principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertIn("bypassPermissions", str(caught.exception.detail)
                      + str(getattr(caught.exception, "detail", "")))
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_project_that_names_bypass_permissions_does_allow_it(self):
        """The other direction, so the ceiling is a ceiling and not a blanket
        ban: a project whose operator wrote the mode admits it."""
        h = Harness(self.tmp,
                    create_allow={self.PROJECT: CreateGrant(
                        permission_modes=("bypassPermissions",))},
                    allow_create=True, grants={OAUTH: {TOKYO: ("create",)}})
        got = h.call("session_create",
                     args={"project": self.PROJECT, "permissionMode": "bypassPermissions"},
                     project=self.PROJECT,
                     principal=h.principal(SCOPE_READ, SCOPE_CREATE))
        self.assertEqual(got["tool"], "session_create")


class ListToolsResultShapeTest(unittest.TestCase):
    """`tools/list` answers with the keys the spec defines, and no others.

    The result used to carry a top-level `ttl: 0`. `ttl` is a field of the TASK
    schemas, not of `ListToolsResult`, whose own keys are `tools` (required),
    `nextCursor` and `_meta`. The official SDK parses this result with
    `z.core.$strip`, so a conformant client dropped the key and never read the
    "do not cache" hint it was meant to carry — which is exactly why removing it
    cannot change what a conformant client does, and why a client that validated
    strictly was the only one it could have hurt.
    """

    #: The keys `ListToolsResult` declares, from the MCP SDK's own schema.
    ALLOWED = {"tools", "nextCursor", "_meta"}

    def _result(self):
        from fleet_mcp import server as srv
        handler = next(c for n, c in vars(srv).items()
                       if isinstance(c, type) and hasattr(c, "_dispatch"))
        return handler(lambda: None).handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})["result"]

    def test_the_result_carries_only_spec_keys(self):
        result = self._result()
        self.assertEqual(set(result) - self.ALLOWED, set())

    def test_the_result_still_carries_every_served_tool(self):
        from fleet_mcp import tools as tools_mod
        result = self._result()
        self.assertEqual(len(result["tools"]), len(tools_mod.SERVED_TOOLS))

    def test_the_cache_hint_is_gone_from_the_wire(self):
        self.assertNotIn("ttl", self._result())


if __name__ == "__main__":
    unittest.main(verbosity=2)
