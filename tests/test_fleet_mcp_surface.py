#!/usr/bin/env python3
"""The seven tools, the aggregation service, and the refusals between them.

Most of what is asserted here is negative, because the interesting question
about a surface that can message an agent on another machine is not "does it
work" but "what stops it". Each class below is one sentence worth keeping
false.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    MANAGED_IDLE,
    RUNNING_AGENT,
    ProviderHealth,
    SessionSummary,
    classify,
)

WINDOWS = "device:work-fj"
TOKYO = "device:tokyo-shared-dev-01"
DELL = "device:personal-dell"


def _summary(device: str, native: str, *, title: str = "a session", **raw) -> SessionSummary:
    row = {"running": False, "hostState": None, **raw}
    verdict, running, resident, evidence = classify(row)
    return SessionSummary(
        key=SessionKey.compose(device, "claude", native),
        title=title,
        classification=verdict,
        running=running,
        resident_host=resident,
        host_state=row.get("hostState"),
        lifecycle_mode=row.get("lifecycleMode"),
        project_id=row.get("projectId"),
        last_activity=row.get("lastActivity"),
        current_run=None,
        evidence=evidence,
    )


class FakeProvider:
    """A device that answers whatever the test needs it to answer."""

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str, *, reachable: bool = True, authorized: bool = True,
                 sessions: list[SessionSummary] | None = None,
                 fail: Exception | None = None) -> None:
        self.device_ref = device_ref
        self._reachable = reachable
        self._authorized = authorized
        self._sessions = sessions or []
        self._fail = fail
        self.sent: list[tuple[str, str, str | None]] = []
        self.reads: list[tuple[str, int]] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref,
            kind=self.kind,
            reachable=self._reachable,
            authorized=self._authorized,
            detail="fake",
            server_name="claudecodeui-mcp-gateway",
            server_version="0.1.0",
            tool_count=22,
            latency_ms=3.0,
        )

    def list_sessions(self, *, limit: int, project=None, state=None):
        if self._fail is not None:
            raise self._fail
        return self._sessions

    def get_session(self, key):
        for summary in self._sessions:
            if summary.key == key:
                return {"session": summary.as_dict()}
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, *, mode="latest", limit=20, around_id=None, before=None,
                     after=None, cursor=None):
        self.reads.append((key.ref, limit))
        return {"messages": [{"id": "m1", "text": "Bearer abcdefghijklmnopqrstuvwxyz012345"}]}

    def send(self, key, message, *, idempotency_key=None):
        self.sent.append((key.ref, message, idempotency_key))
        return {"runId": f"run-{len(self.sent)}", "queuedMessageUuid": None, "accepted": True}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {"state": "completed", "output": "done"}


def _binding(device_ref: str, *, allow_send: bool = False, anchor: Path) -> ProviderBinding:
    return ProviderBinding(device_ref=device_ref, kind="cloudcli-mcp-http",
                           endpoint="http://127.0.0.1:3011/mcp",
                           credential_anchor=anchor, allow_send=allow_send)


class Harness:
    """A service over fake devices, with a real registry and a real audit file."""

    def __init__(self, tmp: Path, *, send_allow: tuple[str, ...] = (),
                 allow_windows_send: bool = False, allow_tokyo_send: bool = False,
                 rate_limit: int = 10_000, max_read: int = 40, redact: bool = True):
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                WINDOWS: _binding(WINDOWS, allow_send=allow_windows_send, anchor=anchor),
                TOKYO: _binding(TOKYO, allow_send=allow_tokyo_send, anchor=anchor),
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=send_allow, max_read_messages=max_read,
                                 read_redact=redact),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=rate_limit),
        )
        self.anchor = anchor
        self.config = config
        self.windows = FakeProvider(WINDOWS)
        self.tokyo = FakeProvider(TOKYO)
        self.service = FleetService(config, providers={WINDOWS: self.windows, TOKYO: self.tokyo})

    def principal(self, *scopes: str, name: str = "tester") -> Principal:
        return Principal(name=name, scopes=frozenset(scopes or ("fleet:read", "fleet:send")),
                         credential_kind="pat")


class ToolSurface(unittest.TestCase):
    """What a connector reads before it decides what it is allowed to do."""

    def test_the_surface_is_exactly_the_seven_named_tools(self):
        self.assertEqual(
            sorted(fleet_tools.tool_names()),
            sorted([
                "fleet_list_devices", "fleet_list_sessions", "fleet_get_session",
                "fleet_read_session", "fleet_send_to_session", "fleet_get_run",
                "fleet_status",
            ]),
        )

    def test_exactly_one_tool_is_not_read_only(self):
        writes = [t.name for t in fleet_tools.TOOLS if not t.annotations["readOnlyHint"]]
        self.assertEqual(writes, ["fleet_send_to_session"])

    def test_no_tool_claims_to_be_idempotent_while_writing(self):
        tool = fleet_tools.TOOLS_BY_NAME["fleet_send_to_session"]
        self.assertFalse(tool.annotations["idempotentHint"])
        self.assertFalse(tool.annotations["destructiveHint"])

    def test_every_tool_declares_all_four_hints(self):
        for tool in fleet_tools.TOOLS:
            for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIn(hint, tool.annotations, tool.name)

    def test_no_tool_takes_a_url_host_or_command(self):
        """There is nothing to point at an internal address, so there is no SSRF shape."""
        banned = {"url", "uri", "host", "endpoint", "address", "command", "cmd", "shell",
                  "path", "callback", "webhook"}
        for tool in fleet_tools.TOOLS:
            properties = set((tool.input_schema.get("properties") or {}).keys())
            self.assertEqual(properties & banned, set(), tool.name)

    def test_every_tool_has_a_description_worth_reading(self):
        for tool in fleet_tools.TOOLS:
            self.assertGreater(len(tool.description), 80, tool.name)

    def test_the_mcp_rendering_carries_the_schema_and_the_annotations(self):
        rendered = fleet_tools.TOOLS_BY_NAME["fleet_read_session"].as_mcp()
        self.assertEqual(rendered["name"], "fleet_read_session")
        self.assertIn("inputSchema", rendered)
        self.assertTrue(rendered["annotations"]["readOnlyHint"])

    def test_an_unknown_tool_is_a_typed_refusal_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = fleet_tools.call_tool(
                fleet_tools.ToolContext(service=harness.service, principal=harness.principal()),
                "fleet_shell",
                {},
            )
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")
        self.assertIn("fleet_list_devices", result["structuredContent"]["detail"])


class DeviceListing(unittest.TestCase):
    """Enrolment and reachability are two answers, reported side by side."""

    def test_enrolled_devices_without_a_route_are_still_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.list_devices(harness.principal())
        by_ref = {row["device_ref"]: row for row in result["devices"]}
        self.assertIn(DELL, by_ref)
        self.assertEqual(by_ref[DELL]["availability"]["local_agent_controller"], "UNKNOWN")
        self.assertIsNone(by_ref[DELL]["provider"])

    def test_a_routed_device_reports_measured_reachability(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.list_devices(harness.principal())
        by_ref = {row["device_ref"]: row for row in result["devices"]}
        self.assertEqual(by_ref[TOKYO]["availability"]["local_agent_controller"], "AVAILABLE")
        self.assertEqual(by_ref[TOKYO]["provider"]["tool_count"], 22)

    def test_only_this_host_claims_fleet_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.list_devices(harness.principal())
        by_ref = {row["device_ref"]: row for row in result["devices"]}
        self.assertEqual(by_ref[WINDOWS]["availability"]["fleet_presence"], "KNOWN_ONLINE")
        self.assertEqual(by_ref[TOKYO]["availability"]["fleet_presence"], "UNKNOWN")

    def test_control_state_is_reported_per_device_and_per_session(self):
        allow = f"{TOKYO}|claude|abc"
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True, send_allow=(allow,))
            result = harness.service.list_devices(harness.principal())
        by_ref = {row["device_ref"]: row for row in result["devices"]}
        self.assertTrue(by_ref[TOKYO]["control"]["send_allowed_device"])
        self.assertEqual(by_ref[TOKYO]["control"]["send_allowed_sessions"], [allow])
        self.assertFalse(by_ref[WINDOWS]["control"]["send_allowed_device"])

    def test_a_read_only_credential_cannot_list_devices_at_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.list_devices(
                    Principal(name="x", scopes=frozenset(), credential_kind="oauth")
                )
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")


class SessionFanOut(unittest.TestCase):
    """One device's failure is one device's failure."""

    def test_sessions_from_two_devices_with_one_native_id_stay_apart(self):
        native = "identical-on-both"
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.windows._sessions = [_summary(WINDOWS, native, title="windows one")]
            harness.tokyo._sessions = [_summary(TOKYO, native, title="tokyo one")]
            result = harness.service.list_sessions(harness.principal())
        refs = {row["session_ref"] for row in result["sessions"]}
        self.assertEqual(len(refs), 2)
        self.assertIn(f"{WINDOWS}|claude|{native}", refs)
        self.assertIn(f"{TOKYO}|claude|{native}", refs)

    def test_an_unreachable_device_does_not_hide_another_devices_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._fail = FleetRefusal("PROVIDER_UNREACHABLE", "connection refused")
            harness.windows._sessions = [_summary(WINDOWS, "w1", title="still here")]
            result = harness.service.list_sessions(harness.principal())
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["title"], "still here")
        unreachable = {u["device_ref"]: u["code"] for u in result["devices_unreachable"]}
        # Tokyo failed to answer; the enrolled-but-unrouted devices are reported
        # alongside it rather than dropped, and neither group affects the other.
        self.assertEqual(unreachable[TOKYO], "PROVIDER_UNREACHABLE")
        self.assertEqual(unreachable[DELL], "UNKNOWN_DEVICE")
        self.assertNotIn(WINDOWS, unreachable)

    def test_a_device_with_no_route_is_reported_not_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.list_sessions(harness.principal())
        unreachable = {u["device_ref"]: u for u in result["devices_unreachable"]}
        self.assertIn(DELL, unreachable)
        self.assertEqual(unreachable[DELL]["code"], "UNKNOWN_DEVICE")

    def test_running_and_idle_sessions_are_not_the_same_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [
                _summary(TOKYO, "busy", title="working", running=True, hostState="busy"),
                _summary(TOKYO, "warm", title="warm host", running=False, hostState="lingering"),
                _summary(TOKYO, "cold", title="just history", running=False,
                         hostState=None, messageCount=900),
            ]
            result = harness.service.list_sessions(harness.principal())
        by_title = {row["title"]: row["classification"] for row in result["sessions"]}
        self.assertEqual(by_title["working"], RUNNING_AGENT)
        self.assertEqual(by_title["warm host"], "RESIDENT_HOST")
        self.assertEqual(by_title["just history"], MANAGED_IDLE)
        self.assertEqual(result["total"], 3)

    def test_a_device_filter_narrows_to_one_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.windows._sessions = [_summary(WINDOWS, "w1")]
            harness.tokyo._sessions = [_summary(TOKYO, "t1")]
            result = harness.service.list_sessions(harness.principal(), device=TOKYO)
        self.assertEqual([row["native_session_id"] for row in result["sessions"]], ["t1"])
        self.assertEqual(result["devices_queried"], 1)

    def test_a_hostname_is_not_a_device_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.list_sessions(harness.principal(), device="tokyo")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")

    def test_a_classification_filter_keeps_the_provider_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [
                _summary(TOKYO, "busy", title="working", running=True),
                _summary(TOKYO, "cold", title="idle"),
            ]
            result = harness.service.list_sessions(harness.principal(),
                                                   classification=RUNNING_AGENT)
        self.assertEqual([row["title"] for row in result["sessions"]], ["working"])


class ReadBoundary(unittest.TestCase):
    """`fleet_read_session` returns the most sensitive thing this surface touches."""

    def _key(self, native: str = "abc") -> SessionKey:
        return SessionKey.compose(TOKYO, "claude", native)

    def test_a_read_is_capped_and_says_it_was(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), max_read=5)
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            result = harness.service.read_session(harness.principal(), self._key(), limit=500)
        self.assertEqual(result["limit_applied"], 5)
        self.assertTrue(result["limit_clamped"])
        self.assertEqual(harness.tokyo.reads[-1][1], 5)

    def test_a_transcript_is_labelled_untrusted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            result = harness.service.read_session(harness.principal(), self._key())
        self.assertTrue(result["untrusted_content"])
        self.assertIn("must not be executed", result["untrusted_notice"])

    def test_credentials_in_a_transcript_are_masked(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            result = harness.service.read_session(harness.principal(), self._key())
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345",
                         str(result["transcript"]))
        self.assertGreaterEqual(result["redactions"], 1)

    def test_the_masking_is_described_as_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            result = harness.service.read_session(harness.principal(), self._key())
        self.assertIn("not a guarantee", result["redaction_note"])

    def test_redaction_can_be_turned_off_and_then_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), redact=False)
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            result = harness.service.read_session(harness.principal(), self._key())
        self.assertEqual(result["redactions"], 0)
        self.assertEqual(result["redaction_note"], "disabled by policy")
        self.assertIn("abcdefghijklmnopqrstuvwxyz012345", str(result["transcript"]))

    def test_a_read_on_a_device_with_no_route_is_a_typed_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.read_session(
                    harness.principal(), SessionKey.compose(DELL, "claude", "abc")
                )
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")


class SendAdmission(unittest.TestCase):
    """Control is granted per device and per session, and never by naming an id."""

    def test_a_device_that_is_not_authorized_for_control_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), send_allow=(f"{TOKYO}|claude|abc",))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.send_to_session(
                    harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                    message="hello",
                )
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("allow_send", caught.exception.detail)

    def test_a_session_not_on_the_allow_list_is_refused_even_on_an_authorized_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|only-this-one",))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.send_to_session(
                    harness.principal(), SessionKey.compose(TOKYO, "claude", "some-other"),
                    message="hello",
                )
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(caught.exception.extra["session_ref"], f"{TOKYO}|claude|some-other")
        self.assertEqual(harness.tokyo.sent, [])

    def test_an_authorized_session_is_accepted_and_returns_a_real_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            result = harness.service.send_to_session(
                harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                message="status?",
            )
        self.assertEqual(result["run_id"], "run-1")
        self.assertFalse(result["idempotent_replay"])
        self.assertEqual(harness.tokyo.sent, [(f"{TOKYO}|claude|abc", "status?", None)])

    def test_sending_is_delivery_and_the_result_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            result = harness.service.send_to_session(
                harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                message="status?",
            )
        self.assertIn("grants no authority", result["note"])

    def test_a_read_only_credential_cannot_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.send_to_session(
                    harness.principal("fleet:read"),
                    SessionKey.compose(TOKYO, "claude", "abc"), message="hello",
                )
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")
        self.assertEqual(harness.tokyo.sent, [])

    def test_an_oversized_message_is_refused_not_truncated(self):
        """A silently shortened instruction is executed as if it were the whole one."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            harness.config = FleetConfig(
                source=None, local_device_ref=WINDOWS, providers=harness.config.providers,
                auth=harness.config.auth, server=harness.config.server,
                policy=PolicyBinding(send_allow=(f"{TOKYO}|claude|abc",), max_message_chars=10),
                audit=harness.config.audit,
            )
            service = FleetService(harness.config, providers={
                WINDOWS: harness.windows, TOKYO: harness.tokyo})
            with self.assertRaises(FleetRefusal) as caught:
                service.send_to_session(
                    harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                    message="x" * 11,
                )
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(harness.tokyo.sent, [])

    def test_an_empty_message_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.send_to_session(
                    harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                    message="   ",
                )
        self.assertEqual(caught.exception.code, "BAD_REQUEST")


class Idempotency(unittest.TestCase):
    """A retry after an unknown outcome must not deliver a second message."""

    def _harness(self, tmp: str) -> Harness:
        return Harness(Path(tmp), allow_tokyo_send=True, send_allow=(f"{TOKYO}|claude|abc",))

    def test_a_repeated_key_returns_the_recorded_run_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._harness(tmp)
            key = SessionKey.compose(TOKYO, "claude", "abc")
            first = harness.service.send_to_session(harness.principal(), key,
                                                    message="do it", idempotency_key="k-1")
            second = harness.service.send_to_session(harness.principal(), key,
                                                     message="do it", idempotency_key="k-1")
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["code"], "IDEMPOTENT_REPLAY")
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(len(harness.tokyo.sent), 1)

    def test_a_different_key_is_a_different_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._harness(tmp)
            key = SessionKey.compose(TOKYO, "claude", "abc")
            harness.service.send_to_session(harness.principal(), key,
                                            message="one", idempotency_key="k-1")
            harness.service.send_to_session(harness.principal(), key,
                                            message="two", idempotency_key="k-2")
        self.assertEqual(len(harness.tokyo.sent), 2)

    def test_the_key_is_scoped_to_the_credential_that_used_it(self):
        """One caller's key must not let another caller read its run back."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._harness(tmp)
            key = SessionKey.compose(TOKYO, "claude", "abc")
            harness.service.send_to_session(harness.principal(name="alice"), key,
                                            message="one", idempotency_key="same")
            other = harness.service.send_to_session(harness.principal(name="bob"), key,
                                                    message="two", idempotency_key="same")
        self.assertFalse(other["idempotent_replay"])
        self.assertEqual(other["run_id"], "run-2")
        self.assertEqual(len(harness.tokyo.sent), 2)

    def test_a_send_without_a_key_records_nothing_to_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._harness(tmp)
            key = SessionKey.compose(TOKYO, "claude", "abc")
            harness.service.send_to_session(harness.principal(), key, message="one")
            harness.service.send_to_session(harness.principal(), key, message="one")
        self.assertEqual(len(harness.tokyo.sent), 2)


class RunLookup(unittest.TestCase):
    def test_a_run_is_answered_in_its_sessions_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.get_run(
                harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"), "run-7"
            )
        self.assertEqual(result["run_id"], "run-7")
        self.assertEqual(result["session_ref"], f"{TOKYO}|claude|abc")
        self.assertEqual(result["state"], "completed")

    def test_a_returned_run_state_is_not_rewritten(self):
        """A terminal state from the provider is the provider's answer, not ours."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo.get_run = lambda key, run_id, wait=False, wait_seconds=0.0: {
                "state": "BUSY"}
            result = harness.service.get_run(
                harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"), "run-7"
            )
        self.assertEqual(result["state"], "BUSY")


class Status(unittest.TestCase):
    def test_status_reports_every_route_and_every_enrolled_device_without_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.status(harness.principal())
        refs = {row["device_ref"] for row in result["providers"]}
        self.assertIn(WINDOWS, refs)
        self.assertIn(TOKYO, refs)
        self.assertIn(DELL, refs)
        by_ref = {row["device_ref"]: row for row in result["providers"]}
        self.assertEqual(by_ref[DELL]["error_code"], "UNKNOWN_DEVICE")

    def test_status_names_the_authority_for_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.status(harness.principal())
        self.assertEqual(result["identity_layer"]["authority"], "fleet/registry.yaml")

    def test_status_carries_audit_records_but_no_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            harness.service.read_session(harness.principal(),
                                         SessionKey.compose(TOKYO, "claude", "abc"))
            result = harness.service.status(harness.principal())
        events = {row["event"] for row in result["recent_audit"]}
        self.assertIn("fleet.read_session", events)
        self.assertNotIn("Bearer", str(result["recent_audit"]))

    def test_status_reports_the_calling_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.status(
                Principal(name="oauth:fleet-abc", scopes=frozenset({"fleet:read"}),
                          credential_kind="oauth")
            )
        self.assertEqual(result["principal"]["name"], "oauth:fleet-abc")
        self.assertEqual(result["principal"]["scopes"], ["fleet:read"])


class RateLimit(unittest.TestCase):
    def test_a_credential_that_exceeds_its_rate_is_refused_not_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), rate_limit=3)
            principal = harness.principal()
            for _ in range(3):
                harness.service.list_devices(principal)
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.list_devices(principal)
        self.assertEqual(caught.exception.code, "RATE_LIMITED")
        self.assertIn("retry_after_seconds", caught.exception.extra)

    def test_the_limit_is_per_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), rate_limit=2)
            for _ in range(2):
                harness.service.list_devices(harness.principal(name="alice"))
            harness.service.list_devices(harness.principal(name="bob"))


class AuditTrail(unittest.TestCase):
    def test_a_read_leaves_a_control_plane_record_without_its_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.tokyo._sessions = [_summary(TOKYO, "abc")]
            harness.service.read_session(harness.principal(),
                                         SessionKey.compose(TOKYO, "claude", "abc"))
            lines = (Path(tmp) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines)
        self.assertIn("fleet.read_session", lines[-1])
        self.assertNotIn("Bearer", "\n".join(lines))

    def test_a_send_records_the_run_it_produced(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), allow_tokyo_send=True,
                              send_allow=(f"{TOKYO}|claude|abc",))
            harness.service.send_to_session(
                harness.principal(), SessionKey.compose(TOKYO, "claude", "abc"),
                message="hello",
            )
            lines = (Path(tmp) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("fleet.send", lines[-1])
        self.assertIn("run-1", lines[-1])
        self.assertNotIn("hello", lines[-1])


if __name__ == "__main__":
    unittest.main()
