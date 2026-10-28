"""The CloudCLI 1.41.0 device contract as the Windows lane observed it.

What this file is
-----------------
Two captures taken from a real CloudCLI gateway on a company Windows machine
(2026-10-27) are held here as data:

    tests/data/capture-cloudcli-1.41.0-tools-list-2026-10-27.json
        the verbatim ``tools/list`` answer, minus prose descriptions.
    tests/data/capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json
        the V1 round trips (create / send / read / get-run / stop / permission)
        run on scratch sessions this lane created, plus their refusals.

The tests make NO network call and read no credential. They assert the facts a
Fleet connector has to be written against — which verbs exist, which parameters
are refused, and which identifier answers another call accepts — so that a later
CloudCLI upgrade shows up as a failing assertion with a diff rather than as a
remote verb that mysteriously stops working.

Why the fixtures, not a live gateway
-----------------------------------
A compatibility test that dials the device only runs where the device is, and
runs against whatever the device happens to be today. These files are the
version-pinned observation; `docs/compat/cloudcli-windows-1.41.0.md` records how
to re-capture, and the re-capture is what turns a new build into a reviewable
diff.

Scope
-----
V1's L2 lane covers project/session/create/send/read/get_run/stop, real
permission modes, and upgrade compatibility. Event inbox, wake-up, OpenAgents
and UI navigation are out of scope here and are covered, where at all, in the
Draft #407 line.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
TOOLS_CAPTURE = DATA / "capture-cloudcli-1.41.0-tools-list-2026-10-27.json"
ROUNDTRIP_CAPTURE = DATA / "capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json"

#: The V1 verbs the L2 lane must be able to reach on a CloudCLI device.
V1_VERBS = {
    "projects_list",
    "sessions_list",
    "session_get",
    "session_read",
    "session_create",
    "session_send",
    "run_get",
    "session_interrupt",
}

#: The permission modes the device listed for `claude` when refusing an unknown
#: one. The refusal is the only place the device publishes this list.
CLAUDE_PERMISSION_MODES = ("default", "auto", "acceptEdits", "bypassPermissions", "plan")

#: Tools whose `_meta.cloudcli/errorCodes` names MCP_TOOL_NOT_IMPLEMENTED in
#: 1.41.0. Recorded, NOT interpreted: see `test_the_not_implemented_code_is_a_
#: vocabulary_not_a_stub_marker`, because run_get is in this list and works.
NOT_IMPLEMENTED_CODE_HOLDERS = {
    "changes",
    "overview",
    "quay_snapshot",
    "run_get",
    "session_search",
    "ui_clients_list",
    "ui_focus_turn",
    "ui_last_opened_session",
    "ui_open_session",
    "ui_open_task",
    "ui_selected_text",
    "ui_show_diff",
    "ui_visible_context",
}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MASKED_ID_RE = re.compile(r"<[a-z0-9-]+>")


def assert_identifier(testcase: unittest.TestCase, value: str) -> None:
    """An id is either a real UUID or the capture's explicit ``<...>`` mask.

    The round-trip capture masks the ids of the scratch sessions this lane
    created, but keeps the ids that appear *inside* a rendered transcript (a
    read case cannot be read without them). Both forms prove the field is
    populated; where the capture kept the real value the caller asserts the UUID
    shape directly, so the mask never stands in for a shape check.
    """
    testcase.assertTrue(
        UUID_RE.fullmatch(value) is not None or MASKED_ID_RE.fullmatch(value) is not None,
        f"not an identifier: {value!r}",
    )


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _tools_capture() -> dict:
    return _load(TOOLS_CAPTURE)


def _roundtrips() -> dict:
    return _load(ROUNDTRIP_CAPTURE)


def _case(name: str) -> dict:
    return _roundtrips()["cases"][name]


class CaptureShapeTest(unittest.TestCase):
    """The capture says which build and protocol it describes, or it proves nothing."""

    def test_capture_names_the_build_and_protocol(self):
        capture = _tools_capture()
        self.assertEqual(capture["cloudcli_package"]["name"], "@yalehwang/cloudcli")
        self.assertEqual(capture["cloudcli_package"]["version"], "1.41.0")
        self.assertEqual(capture["server"], "claudecodeui-mcp-gateway")
        self.assertEqual(capture["protocolVersion"], "2025-06-18")
        # The five local patches change session-default behaviour, so a capture
        # that forgot them would attribute a patched answer to stock 1.41.0.
        states = [patch["state"] for patch in capture["cloudcli_package"]["patches"]]
        self.assertEqual(states, ["PATCHED"] * 5)

    def test_thirty_one_tools_each_carry_the_advertised_schemas(self):
        tools = _tools_capture()["tools"]
        self.assertEqual(len(tools), 31)
        self.assertEqual(len({tool["name"] for tool in tools}), 31)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertIsInstance(tool["inputSchema"], dict)
                # 1.41.0 is the first build this lane saw that publishes an
                # outputSchema for every verb; 1.40's capture had none.
                self.assertIsInstance(tool["outputSchema"], dict)
                self.assertEqual(
                    set(tool["annotations"]),
                    {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"},
                )

    def test_the_device_never_declares_additional_properties_on_input(self):
        for tool in _tools_capture()["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("additionalProperties", tool["inputSchema"])

    def test_handler_bound_is_no_longer_advertised(self):
        # 1.40's capture carried `_meta["cloudcli/handlerBound"]` per tool; 1.41.0
        # carries only the error-code vocabulary. A capability reader that
        # infers "wired" from the absence must therefore have a third state,
        # which `fleet_mcp.cloudcli_native` calls HANDLER_BINDING_UNKNOWN.
        for tool in _tools_capture()["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("cloudcli/handlerBound", tool["_meta"])

    def test_the_not_implemented_code_is_a_vocabulary_not_a_stub_marker(self):
        tools = {tool["name"]: tool for tool in _tools_capture()["tools"]}
        holders = {
            name
            for name, tool in tools.items()
            if "MCP_TOOL_NOT_IMPLEMENTED" in tool["_meta"].get("cloudcli/errorCodes", [])
        }
        self.assertEqual(holders, NOT_IMPLEMENTED_CODE_HOLDERS)
        # ... and the proof that membership is not a stub: run_get is in the set
        # AND returned a completed run reading in the same build.
        hit = _case("run_get_hit_completed")
        self.assertFalse(hit["isError"])
        self.assertEqual(hit["result"]["status"], "completed")


class ProjectAndSurfaceTest(unittest.TestCase):
    """Which of V1's verbs the device actually declares."""

    def test_v1_verbs_are_declared(self):
        names = {tool["name"] for tool in _tools_capture()["tools"]}
        self.assertEqual(V1_VERBS - names, set())
        # `session_close` exists but is resident-only; see ControlTest.
        self.assertIn("session_close", names)

    def test_project_create_and_delete_left_the_native_surface(self):
        # 1.40.0-fleet.1 exposed 33 tools including project_create/project_delete.
        # Stock 1.41.0 exposes 31 and neither project verb: a Fleet "create
        # project" cannot be a native mirror on this build.
        names = {tool["name"] for tool in _tools_capture()["tools"]}
        self.assertNotIn("project_create", names)
        self.assertNotIn("project_delete", names)
        self.assertIn("projects_list", names)

    def test_session_create_requires_only_a_project(self):
        schema = _schema("session_create")
        self.assertEqual(schema["required"], ["project"])
        self.assertEqual(
            set(schema["properties"]),
            {"project", "message", "provider", "model", "lifecycleMode", "permissionMode", "correlationId"},
        )

    def test_session_send_requires_the_session_and_the_message(self):
        schema = _schema("session_send")
        self.assertEqual(schema["required"], ["session", "message"])
        self.assertEqual(
            set(schema["properties"]),
            {"session", "message", "waitSeconds", "correlationId"},
        )

    def test_run_get_publishes_both_id_spaces_without_a_static_required(self):
        schema = _schema("run_get")
        self.assertEqual(
            set(schema["properties"]),
            # runId/deliveryId are the two id spaces; run/wait are the 1.40
            # aliases the device still accepts (RunGetIdentityTest proves it).
            {"runId", "deliveryId", "waitSeconds", "session", "run", "wait"},
        )
        # 1.40 made `runId` required. 1.41.0 dropped the static `required` and
        # enforces "exactly one of runId / deliveryId" at call time instead.
        self.assertNotIn("required", schema)
        self.assertNotIn("oneOf", schema)

    def test_session_read_publishes_its_window_parameters(self):
        schema = _schema("session_read")
        self.assertEqual(schema["required"], ["session"])
        self.assertEqual(
            set(schema["properties"]),
            {"session", "mode", "limit", "aroundId", "before", "after", "cursor"},
        )


class CreateTest(unittest.TestCase):
    """`session_create` answers two different shapes depending on `message`."""

    def test_create_without_a_message_returns_only_the_session(self):
        case = _case("create_no_message_claude_per_run")
        self.assertFalse(case["isError"])
        self.assertEqual(set(case["result"]), {"sessionId"})
        assert_identifier(self, case["result"]["sessionId"])

    def test_create_with_a_message_returns_a_run_but_no_delivery(self):
        case = _case("create_with_message_claude_per_run")
        self.assertFalse(case["isError"])
        self.assertEqual(set(case["result"]), {"sessionId", "deliveryId", "runId"})
        # The one-shot create path mints a run and no delivery handle; a
        # connector that waits for a deliveryId from create would wait forever.
        self.assertIsNone(case["result"]["deliveryId"])
        assert_identifier(self, case["result"]["runId"])

    def test_codex_create_answers_the_same_minimal_shape(self):
        case = _case("create_no_message_codex_per_run")
        self.assertFalse(case["isError"])
        self.assertEqual(set(case["result"]), {"sessionId"})

    def test_an_unknown_permission_mode_publishes_the_supported_list(self):
        case = _case("reconfigure_unsupported_mode")
        self.assertTrue(case["isError"])
        self.assertEqual(case["result"]["code"], "UNSUPPORTED_PERMISSION_MODE")
        self.assertEqual(tuple(case["result"]["details"]["supported"]), CLAUDE_PERMISSION_MODES)

    def test_create_validates_the_mode_before_it_creates_anything(self):
        # The refusal happens before a session row exists: the same invalid mode
        # is refused at the create gate, with the same list.
        case = _case("create_project_path_with_forward_slashes")
        self.assertEqual(case["result"]["code"], "UNSUPPORTED_PERMISSION_MODE")
        self.assertEqual(tuple(case["result"]["details"]["supported"]), CLAUDE_PERMISSION_MODES)

    def test_a_windows_project_path_must_be_forward_slashed(self):
        # Both calls name the SAME project; only the separator differs. The
        # backslash form never reaches the path comparison and falls through to
        # title matching, so it reads as "no such project". Windows callers must
        # normalise to "/" before naming a project.
        backslash = _case("create_project_path_with_backslashes")
        forward = _case("create_project_path_with_forward_slashes")
        self.assertEqual(backslash["result"]["code"], "PROJECT_NOT_FOUND")
        self.assertNotEqual(forward["result"]["code"], "PROJECT_NOT_FOUND")


class SendIdentityTest(unittest.TestCase):
    """Which identifier a send hands back, and whether anything can read it."""

    def test_per_run_send_returns_a_run_and_no_delivery(self):
        case = _case("send_per_run")
        self.assertFalse(case["isError"])
        self.assertEqual(
            set(case["result"]),
            {"deliveryId", "runId", "queued", "queuedMessageUuid", "source", "blockedBy"},
        )
        self.assertIsNone(case["result"]["deliveryId"])
        self.assertIsNone(case["result"]["queuedMessageUuid"])
        self.assertFalse(case["result"]["queued"])
        self.assertEqual(case["result"]["source"], "mcp")
        self.assertRegex(case["result"]["runId"], UUID_RE)

    def test_a_busy_per_run_session_is_refused_not_queued(self):
        case = _case("send_busy_per_run")
        self.assertTrue(case["isError"])
        self.assertEqual(case["result"]["code"], "SESSION_BUSY")
        self.assertTrue(case["result"]["retryable"])
        # The refusal is widened with the in-flight run, so a caller has
        # something to follow instead of blind retrying.
        self.assertRegex(case["result"]["details"]["runId"], UUID_RE)
        self.assertNotIn("queuedMessageUuid", case["result"]["details"])

    def test_a_queued_resident_send_uses_one_uuid_for_two_names(self):
        case = _case("send_resident_queued")
        self.assertFalse(case["isError"])
        self.assertTrue(case["result"]["queued"])
        # deliveryId and queuedMessageUuid are the SAME value, not two handles.
        self.assertEqual(case["result"]["deliveryId"], case["result"]["queuedMessageUuid"])
        self.assertRegex(case["result"]["deliveryId"], UUID_RE)
        self.assertRegex(case["result"]["runId"], UUID_RE)

    def test_a_first_resident_send_also_has_no_delivery(self):
        case = _case("send_resident_first")
        self.assertIsNone(case["result"]["deliveryId"])
        self.assertFalse(case["result"]["queued"])

    def test_the_delivery_a_send_returns_cannot_be_read_back(self):
        # This is the finding, stated as a test: the deliveryId in
        # send_resident_queued is the exact value run_get was given, and the
        # gateway answered "never issued" both immediately and 1.5 s later
        # while the run was alive. A delivery is not a read handle in this
        # build, whatever the schema advertises.
        sent = _case("send_resident_queued")["result"]
        miss = _case("run_get_delivery_miss")
        self.assertTrue(miss["isError"])
        self.assertEqual(miss["result"]["code"], "RUN_NOT_FOUND")
        self.assertEqual(miss["result"]["details"]["idKind"], "delivery")
        self.assertEqual(miss["result"]["details"]["deliveryId"], sent["deliveryId"])
        self.assertEqual(miss["result"]["details"]["reason"], "never_issued")
        self.assertEqual(miss["result"]["message"], "This deliveryId was never issued.")

    def test_the_run_of_that_queued_send_is_readable_and_carries_no_delivery(self):
        case = _case("run_get_run_hit_from_queued_send")
        self.assertFalse(case["isError"])
        self.assertEqual(case["result"]["status"], "running")
        # The run reading cannot repair the broken delivery arm: it states its
        # own deliveryId as null for a send that HAD one.
        self.assertIsNone(case["result"]["deliveryId"])


class RunGetIdentityTest(unittest.TestCase):
    """run_get's parameter rules and the lifetime of the ids it reads."""

    def test_naming_both_id_spaces_is_a_parameter_error(self):
        case = _case("run_get_both_ids")
        self.assertEqual(case["result"]["code"], "INVALID_ARGUMENT")
        self.assertIn("cannot be combined", case["result"]["message"])

    def test_naming_neither_id_space_is_a_parameter_error(self):
        case = _case("run_get_neither_id")
        self.assertEqual(case["result"]["code"], "INVALID_ARGUMENT")
        self.assertIn('One of "runId" or "deliveryId" is required', case["result"]["message"])

    def test_the_1_40_aliases_are_still_accepted(self):
        # `session` and `wait` are not refusals: the call reaches the registry
        # (which then reports the run expired). An unknown-parameter refusal
        # would have said INVALID_ARGUMENT instead.
        case = _case("run_get_legacy_aliases_accepted_run_expired")
        self.assertEqual(case["result"]["code"], "RUN_NOT_FOUND")
        self.assertEqual(case["result"]["details"]["reason"], "expired")

    def test_a_run_reading_is_bound_to_one_gateway_boot(self):
        hit = _case("run_get_hit_completed")["result"]
        miss = _case("run_get_delivery_miss")["result"]["details"]
        self.assertRegex(hit["bootId"], UUID_RE)
        # Every answer states the boot it came from, so a caller can tell "gone"
        # from "I am asking a different process".
        self.assertEqual(hit["bootId"], miss["bootId"])

    def test_a_settled_run_reading_describes_the_run(self):
        hit = _case("run_get_hit_completed")["result"]
        self.assertEqual(hit["idKind"], "run")
        self.assertEqual(hit["status"], "completed")
        self.assertEqual(hit["outcome"], "settled")
        self.assertEqual(hit["source"], "mcp")
        self.assertEqual(hit["lastAssistantMessage"]["content"], "COMPAT-OK")
        self.assertIsInstance(hit["elapsedMs"], int)

    def test_an_aborted_run_settles_with_the_previous_turns_message(self):
        hit = _case("run_get_hit_aborted")["result"]
        self.assertEqual(hit["status"], "aborted")
        self.assertEqual(hit["outcome"], "settled")
        # `lastAssistantMessage` is "the session's last assistant message", not
        # "this run's output": here it is the PREVIOUS turn's reply. A receipt
        # built from this field alone would attribute stale text to a cancelled
        # turn.
        self.assertEqual(hit["lastAssistantMessage"]["content"], "COMPAT-SEND-OK")


class ReadTest(unittest.TestCase):
    """`session_read` returns prose, not messages — with one exception."""

    def test_latest_is_a_rendered_transcript_string(self):
        case = _case("session_read_latest")
        self.assertFalse(case["isError"])
        self.assertEqual(case["result"]["mode"], "latest")
        self.assertIsInstance(case["result"]["content"], str)
        self.assertIn("assistant: COMPAT-OK", case["result"]["content"])
        # There is no structured message list to consume: no ids, no roles, no
        # turn keys in this mode.
        self.assertNotIn("messages", case["result"])
        self.assertNotIn("cursor", case["result"])

    def test_outline_is_the_only_mode_that_exposes_message_ids(self):
        case = _case("session_read_outline")
        self.assertEqual(case["result"]["mode"], "outline")
        body = case["result"]["content"]
        self.assertRegex(body, r"#0 " + UUID_RE.pattern + ":")
        self.assertRegex(body, r"#\d+ " + UUID_RE.pattern + ":")

    def test_codex_message_ids_live_in_their_own_namespace(self):
        case = _case("codex_session_read_outline")
        self.assertIn("codex_", case["result"]["content"])
        self.assertRegex(case["result"]["content"], r"codex_" + UUID_RE.pattern)

    def test_an_unknown_message_id_is_an_internal_error(self):
        case = _case("session_read_around_unknown")
        self.assertTrue(case["isError"])
        # Not SESSION_NOT_FOUND and not INVALID_ARGUMENT: the session exists and
        # the argument is well-formed, so a client cannot tell "bad id" from
        # "server fault" except by this code.
        self.assertEqual(case["result"]["code"], "INTERNAL_ERROR")
        self.assertFalse(case["result"]["retryable"])


class ControlTest(unittest.TestCase):
    """stop and close: two different verbs with two different conditions."""

    def test_interrupt_reports_whether_it_aborted(self):
        running = _case("interrupt_running")["result"]
        idle = _case("interrupt_idle")["result"]
        self.assertIs(running["aborted"], True)
        self.assertIs(idle["aborted"], False)
        self.assertIn("no run in flight", idle["message"])

    def test_close_is_resident_only(self):
        case = _case("close_per_run_refused")
        self.assertTrue(case["isError"])
        self.assertEqual(case["result"]["code"], "LIFECYCLE_MODE_NOT_RESIDENT")

    def test_close_returns_a_host_receipt(self):
        case = _case("close_resident")
        self.assertFalse(case["isError"])
        self.assertRegex(case["result"]["hostId"], r"^host-" + UUID_RE.pattern + "$")
        self.assertEqual(case["result"]["mode"], "resident")
        self.assertEqual(case["result"]["closeReason"], "user")
        self.assertIn({"kind": "resident-policy"}, case["result"]["leases"])

    def test_session_get_separates_a_host_from_its_leases(self):
        idle = _case("session_get_idle_no_host")["result"]
        busy = _case("session_get_resident_host")["result"]
        self.assertIsNone(idle["host"])
        self.assertTrue(idle["hostNote"].startswith("No host"))
        self.assertEqual(busy["host"]["mode"], "resident")
        self.assertIsInstance(busy["host"]["pid"], int)
        self.assertEqual(busy["host"]["peerName"], "tmp-15")
        self.assertIsNone(busy["hostNote"])


class CodexTest(unittest.TestCase):
    """Codex answers the same envelopes, with two differences worth naming."""

    def test_a_completed_codex_run_may_carry_no_assistant_message(self):
        case = _case("codex_run_completed_without_message")
        self.assertFalse(case["isError"])
        self.assertEqual(case["result"]["status"], "completed")
        self.assertEqual(case["result"]["outcome"], "settled")
        # This run actually failed at the provider ("usage limit reached", read
        # from the native rollout by the operator) yet still reads settled with
        # no message. "completed" answers "did the turn end", not "did it
        # produce anything" — a Fleet receipt must not turn it into success.
        self.assertIsNone(case["result"]["lastAssistantMessage"])
        self.assertIn("no readable assistant message", case["result"]["note"])


class ConnectorCrossCheckTest(unittest.TestCase):
    """The Fleet side, held against the capture it claims to describe.

    These read `fleet_mcp.cloudcli_native`'s recorded table. They never write to
    it: the L2 compatibility lane owns fixtures and docs, and a change to that
    module belongs to the Draft #407 owner.
    """

    @classmethod
    def setUpClass(cls):
        try:
            from fleet_mcp import cloudcli_native  # noqa: F401
        except Exception as exc:  # pragma: no cover - import path only
            raise unittest.SkipTest(f"fleet_mcp not importable here: {exc}")

    def _module(self):
        from fleet_mcp import cloudcli_native

        return cloudcli_native

    def test_the_recorded_set_is_the_device_set(self):
        module = self._module()
        observed = {tool["name"] for tool in _tools_capture()["tools"]}
        self.assertEqual(set(module.NATIVE_BY_NAME), observed)
        self.assertEqual(module.NATIVE_TOOL_COUNT, 31)

    def test_coverage_agrees_and_reports_the_extension_gap(self):
        module = self._module()
        tools = _tools_capture()["tools"]
        report = module.coverage(
            [tool["name"] for tool in tools],
            {tool["name"]: tool["annotations"] for tool in tools},
        )
        self.assertTrue(report["agrees"])
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["extra"], [])
        self.assertEqual(report["annotation_drift"], [])
        # 1.40.0-fleet.1 declared both project verbs and this build reaches them
        # through authored tools; 1.41.0 declares neither. The gap must be
        # visible, and must NOT be reported as drift.
        self.assertEqual(
            report["extension_claimed_missing"],
            ["project_create", "project_delete"],
        )

    def test_capability_snapshot_cannot_claim_a_bound_handler(self):
        module = self._module()
        tools = _tools_capture()["tools"]
        observed = {
            tool["name"]: {
                "annotations": tool["annotations"],
                "schema": tool["inputSchema"],
                "_meta": tool["_meta"],
            }
            for tool in tools
        }
        snapshot = module.capability_snapshot(device_ref="device:compat", observed=observed)
        self.assertEqual(snapshot["state"], "OBSERVED")
        states = {name: entry["state"] for name, entry in snapshot["tools"].items()}
        self.assertEqual(set(states.values()), {module.HANDLER_BINDING_UNKNOWN})
        self.assertTrue(
            all(entry["binding_evidence"] == "NOT_OBSERVED" for entry in snapshot["tools"].values())
        )

    @unittest.expectedFailure
    def test_the_recorded_run_get_schema_is_not_narrower_than_the_device(self):
        # EXPECTED FAILURE on main today: the table still records 1.40's
        # run_get (runId required; no deliveryId) while the device declares
        # deliveryId, run and wait. Because the mirror refuses unknown
        # parameters, the recorded table would make a device-accepted
        # deliveryId call impossible — the exact drift this lane is testing for.
        # Draft #407 already carries the fix; when it lands this test turns
        # into an unexpected success, which is the signal to delete the
        # decorator.
        module = self._module()
        device = _schema("run_get")
        recorded = module.NATIVE_INPUT_SCHEMAS["run_get"]
        self.assertEqual(
            set(device["properties"]) - set(recorded["properties"]),
            set(),
            "the device declares parameters the recorded schema omits",
        )


def _schema(name: str) -> dict:
    tools = {tool["name"]: tool for tool in _tools_capture()["tools"]}
    return tools[name]["inputSchema"]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
