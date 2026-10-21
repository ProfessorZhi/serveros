#!/usr/bin/env python3
"""`fleet_interrupt_session`: stopping a run, and nothing else.

Messaging a session, minting one, and stopping one are three questions, and this
file is the admission question for the third — written before the implementation
so that the answer is a list of refusals rather than a list of hopes.

Everything here runs against fake providers and a fake node. No real CloudCLI is
contacted, no real session is touched, and the point of the file is what the
surface *will not* do:

*   the scope is independent (`fleet:control`), so a credential that may send and
    one that may create are not thereby credentials that may stop work in flight;
*   the device authorization is independent (`allow_control`), default off,
    checked *before* any provider is asked — a refusal that happens after the run
    is stopped is not a refusal;
*   the session authorization is independent (`policy.control_allow`), and it is
    not a superset of `send_allow`: an operator who authorized messaging a
    session has not thereby authorized ending its run;
*   the verb is a single auditable action with one argument — the session — so
    there is no message, no reason string, no signal, no force flag and no path
    for a caller to name. The only fields the schema declares are the four ways
    of writing down a session identity, and nothing else survives the filter;
*   `aborted: false` is a success carrying a fact, not a failure. Collapsing it
    into "false" would tell a caller their run was stopped when nothing was ever
    running, or that the call failed when it did not.

The three devices named here are the three the operator asked to manage
centrally: `device:tokyo-shared-dev-01`, `device:work-fj`, `device:llm-lab-dell`.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    CreateGrant,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
    load_config,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (  # noqa: E402
    SCOPE_CONTROL,
    SCOPE_CREATE,
    SCOPE_READ,
    SCOPE_SEND,
    FleetService,
    Principal,
)
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth  # noqa: E402

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:work-fj"
DELL_LLM = "device:llm-lab-dell"
# Enrolled, and deliberately not authorized for control by this harness.
PERSONAL_DELL = "device:personal-dell"

TARGETS = (TOKYO, WINDOWS, DELL_LLM)
EVERY = (TOKYO, WINDOWS, DELL_LLM, PERSONAL_DELL)

NATIVE = "11111111-2222-3333-4444-555555555555"
OTHER_NATIVE = "99999999-8888-7777-6666-555555555555"
REF = f"{TOKYO}|claude|{NATIVE}"


class FakeProvider:
    """A device that answers `interrupt` and records exactly what it was asked.

    The answer shape is CloudCLI 1.39's own, read from
    `mcp-session-lifecycle.js#session_interrupt`: `{"aborted": true}` when a run
    was stopped, or `{"aborted": false, "message": "<sentence>"}` when there was
    no run in flight. The second is what the real gateway answers, and it is a
    successful call — so the fake has to be able to say it, or the tests below
    would only ever exercise the happy half of the verb.
    """

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref
        self.interrupted: list[str] = []
        self.asked: list[dict] = []
        # What this provider answers. A test that wants the "nothing was
        # running" case replaces this with the message-bearing shape.
        self.answer: dict = {"aborted": True}
        self.fail: FleetRefusal | None = None

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref, kind=self.kind, reachable=True, authorized=True,
            detail="fake", server_name="claudecodeui-mcp-gateway", server_version="0.1.0",
            tool_count=22, latency_ms=1.0,
        )

    def list_sessions(self, *, limit, project=None, state=None):
        return []

    def get_session(self, key):
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, **kwargs):
        return {"messages": []}

    def send(self, key, message, *, idempotency_key=None):
        return {"runId": "run-send", "accepted": True}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {"state": "completed"}

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        return {"sessionId": "created-1"}

    def interrupt(self, key):
        self.asked.append({"native": key.native_session_id, "ref": key.ref})
        if self.fail is not None:
            raise self.fail
        self.interrupted.append(key.native_session_id)
        return dict(self.answer)


def _binding(device_ref: str, *, anchor: Path, allow_send: bool = False,
             allow_control: bool = False, allow_create: bool = False) -> ProviderBinding:
    return ProviderBinding(
        device_ref=device_ref, kind="cloudcli-mcp-http",
        endpoint=f"http://{device_ref}/mcp", credential_anchor=anchor,
        allow_send=allow_send, allow_create=allow_create, allow_control=allow_control,
    )


class Harness:
    """A service over the three target devices, one of them unauthorized.

    Every gate is stated rather than defaulted: an empty `control_allow` means
    refusal, and `allow_control=False` on a binding means refusal, which is the
    property most of this file is about. The defaults below are deliberately the
    "yes" answer *for one device and one session* so that the refusal cases have
    to name the thing they are removing.
    """

    def __init__(self, tmp: Path, *, authorized=TARGETS, allow_control: bool = True,
                 allow_create: bool = False, control_allow=None, send_allow=(),
                 allow_send: bool = False, rate_limit: int = 10_000,
                 wildcard_control: bool = False):
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                ref: _binding(ref, anchor=anchor,
                              allow_send=allow_send or ref in authorized and allow_send,
                              allow_control=allow_control and ref in authorized,
                              allow_create=allow_create and ref in authorized)
                for ref in EVERY
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                create_allow={"p-1": CreateGrant()},
                send_allow=tuple(send_allow),
                control_allow=tuple((REF,) if control_allow is None else control_allow),
                allow_wildcard_control=wildcard_control,
            ),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=rate_limit),
        )
        self.anchor = anchor
        self.providers = {ref: FakeProvider(ref) for ref in EVERY}
        self.service = FleetService(self.config, providers=dict(self.providers))

    def principal(self, *scopes: str, name: str = "tester") -> Principal:
        return Principal(
            name=name,
            scopes=frozenset(scopes or (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE, SCOPE_CONTROL)),
            credential_kind="pat",
        )

    def context(self, *scopes: str, name: str = "tester") -> "fleet_tools.ToolContext":
        return fleet_tools.ToolContext(service=self.service,
                                       principal=self.principal(*scopes, name=name))

    def interrupt(self, session_ref: str = REF, **kwargs):
        return self.service.interrupt_session(
            self.principal(), SessionKey.parse(session_ref), **kwargs
        )

    def interrupt_via_tool(self, *scopes: str, **args):
        """Through the real dispatch path, refusals included.

        `call_tool` turns a typed refusal into an `isError` *answer* rather than
        letting it escape, so a test that wants to see a refusal has to read the
        envelope — which is also what a caller sees.
        """
        return fleet_tools.call_tool(self.context(*scopes), "fleet_interrupt_session", args)

    def audit_lines(self, tmp) -> list[str]:
        """The audit file, or nothing when no line was ever written.

        Absence is the assertion several of these tests make — a refused
        interrupt must not be recorded as one that happened — so reading a file
        that was never created has to mean "no lines" rather than an error.
        """
        path = Path(tmp) / "audit.jsonl"
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()

    # -- the operator's hand on the binding, mid-run ----------------------

    def edit_policy(self, **changes) -> None:
        self.config = replace(self.config, policy=replace(self.config.policy, **changes))
        self.service.config = self.config

    def edit_provider(self, device_ref: str, **changes) -> None:
        providers = dict(self.config.providers)
        providers[device_ref] = replace(providers[device_ref], **changes)
        self.config = replace(self.config, providers=providers)
        self.service.config = self.config


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


class TheToolIsOnTheSurfaceAndSaysWhatItIs(unittest.TestCase):
    def test_interrupt_stays_ninth_before_the_project_extension(self):
        names = fleet_tools.tool_names()
        self.assertEqual(names[8:], ["fleet_interrupt_session", "fleet_create_project"])
        # The eight that came before are still there, still in their order.
        self.assertEqual(names[:8], [
            "fleet_list_devices", "fleet_list_sessions", "fleet_get_session",
            "fleet_read_session", "fleet_send_to_session", "fleet_get_run",
            "fleet_status", "fleet_create_session",
        ])

    def test_it_is_the_only_tool_this_surface_calls_destructive(self):
        destructive = [t.name for t in fleet_tools.TOOLS
                       if t.annotations.get("destructiveHint")]
        self.assertEqual(destructive, ["fleet_interrupt_session"])
        tool = fleet_tools.TOOLS_BY_NAME["fleet_interrupt_session"]
        self.assertFalse(tool.annotations["readOnlyHint"])
        # Repeating it is safe: a second interrupt on an idle session is not an
        # error. That is the MCP sense of the hint, and it is not the
        # send/create sense (a key that replays the first answer).
        self.assertTrue(tool.annotations["idempotentHint"])

    def test_the_schema_names_a_session_and_nothing_else(self):
        tool = fleet_tools.TOOLS_BY_NAME["fleet_interrupt_session"]
        schema = tool.input_schema
        self.assertEqual(sorted(schema["properties"]),
                         ["device", "provider", "session", "session_ref"])
        self.assertEqual(schema["required"], [])
        self.assertIs(schema["additionalProperties"], False)

    def test_there_is_no_argument_for_a_message_a_signal_or_a_force(self):
        """The verb has one thing to say and it is the session.

        A `reason`, a `signal`, a `force` or a `command` field would each be a
        door with nothing behind it today — and a door the far side would have to
        be trusted not to open tomorrow. The absence is the design.
        """
        tool = fleet_tools.TOOLS_BY_NAME["fleet_interrupt_session"]
        properties = set(tool.input_schema["properties"])
        for forbidden in ("message", "reason", "note", "signal", "sig", "force",
                          "kill", "command", "cmd", "exec", "script", "path",
                          "url", "uri", "argv", "idempotency_key", "run_id"):
            with self.subTest(parameter=forbidden):
                self.assertNotIn(forbidden, properties)

    def test_an_undeclared_argument_is_refused_rather_than_ignored(self):
        """A field that is ignored does not fail — it widens."""
        tool = fleet_tools.TOOLS_BY_NAME["fleet_interrupt_session"]
        refusal = fleet_tools.unknown_argument_refusal(tool, {"session_ref": REF, "force": True})
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.code, "BAD_REQUEST")
        self.assertIn("force", refusal.detail)

    def test_naming_no_session_at_all_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            result = h.interrupt_via_tool()
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")
        self.assertEqual(h.providers[TOKYO].asked, [])

    def test_the_answer_does_not_come_back_as_a_write(self):
        """`_interrupt_session` forwards the provider's answer untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            result = h.interrupt_via_tool(session_ref=REF)
        self.assertFalse(result["isError"])
        answer = result["structuredContent"]
        self.assertTrue(answer["aborted"])
        self.assertEqual(answer["session_ref"], REF)
        self.assertEqual(answer["native_session_id"], NATIVE)


# ---------------------------------------------------------------------------
# The scope
# ---------------------------------------------------------------------------


class TheScopeIsItsOwn(unittest.TestCase):
    def test_a_credential_without_fleet_control_is_refused_before_the_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            result = h.interrupt_via_tool(SCOPE_READ, SCOPE_SEND, SCOPE_CREATE,
                                          session_ref=REF)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")
        self.assertEqual(h.providers[TOKYO].asked,
                         [], "the device was asked before the scope was checked")

    def test_may_create_does_not_imply_may_stop(self):
        """The two heavy verbs are separate scopes for a reason.

        A credential trusted to mint sessions is a credential trusted to *add*
        something; ending work already under way is the opposite direction, and
        an operator who granted the first has not answered the second.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), allow_create=True)
            self.assertIn("fleet_create_session", fleet_tools.tool_names())
            result = h.interrupt_via_tool(SCOPE_READ, SCOPE_SEND, SCOPE_CREATE,
                                          session_ref=REF)
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")
        self.assertEqual(h.providers[TOKYO].asked, [])

    def test_may_send_does_not_imply_may_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), send_allow=(REF,))
            result = h.interrupt_via_tool(SCOPE_READ, SCOPE_SEND, session_ref=REF)
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")
        self.assertEqual(h.providers[TOKYO].asked, [])

    def test_the_control_scope_is_not_grantable_by_default(self):
        """The default ceiling is read and send — neither heavy verb."""
        from fleet_mcp import scopes as scopes_module

        self.assertEqual(scopes_module.DEFAULT_GRANTABLE_SCOPES,
                         (SCOPE_READ, SCOPE_SEND))
        self.assertNotIn(SCOPE_CONTROL, scopes_module.DEFAULT_GRANTABLE_SCOPES)
        self.assertNotIn(SCOPE_CREATE, scopes_module.DEFAULT_GRANTABLE_SCOPES)


# ---------------------------------------------------------------------------
# The two floors
# ---------------------------------------------------------------------------


class TheDeviceGateIsItsOwn(unittest.TestCase):
    def test_a_device_that_never_authorized_control_is_refused_and_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), authorized=(WINDOWS, DELL_LLM))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("allow_control", caught.exception.detail)
        self.assertEqual(h.providers[TOKYO].asked, [],
                         "the run was stopped before the device gate said no")

    def test_an_enrolled_but_unauthorized_device_is_still_refused(self):
        """`device:personal-dell` is in the config and is not in `authorized`."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt(f"{PERSONAL_DELL}|claude|{NATIVE}")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[PERSONAL_DELL].asked, [])

    def test_the_device_default_is_closed_when_the_binding_says_nothing(self):
        """A binding that never considered the question answers with silence."""
        binding = ProviderBinding(device_ref=TOKYO, kind="cloudcli-mcp-http",
                                  endpoint="http://x/mcp", credential_anchor=Path("/dev/null"))
        self.assertFalse(binding.allow_control)

    def test_turning_the_device_gate_off_mid_run_stops_the_next_call(self):
        """The operator's hand on the file is read fresh, not cached."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            self.assertTrue(h.interrupt()["aborted"])
            h.edit_provider(TOKYO, allow_control=False)
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(len(h.providers[TOKYO].asked), 1,
                         "the second call reached the device after the gate closed")

    def test_the_device_gate_does_not_widen_the_session_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), control_allow=())
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("control_allow", caught.exception.detail)
        self.assertEqual(h.providers[TOKYO].asked, [])


class TheSessionGateIsSeparateFromSend(unittest.TestCase):
    def test_a_session_not_on_the_control_list_is_refused_and_not_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt(f"{TOKYO}|claude|{OTHER_NATIVE}")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(caught.exception.extra["session_ref"],
                         f"{TOKYO}|claude|{OTHER_NATIVE}")
        self.assertEqual(h.providers[TOKYO].asked, [],
                         "another session's run was stopped by naming it")

    def test_the_refusal_names_what_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt(f"{TOKYO}|claude|{OTHER_NATIVE}")
        self.assertEqual(caught.exception.extra["allowed"], [REF])

    def test_a_send_allowlist_entry_does_not_stop_anything(self):
        """The two lists are not one list with two names.

        The socket-shaped failure this rules out: a connector that read
        `send_allow` when deciding about control would answer a question nobody
        was asked, and an operator who authorized a message would find they had
        authorized ending the work it started.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), send_allow=(REF,), control_allow=())
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("control_allow", caught.exception.detail)
        self.assertEqual(h.providers[TOKYO].asked, [])

    def test_adding_the_session_to_the_control_list_mid_run_takes_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), control_allow=())
            with self.assertRaises(FleetRefusal):
                h.interrupt()
            h.edit_policy(control_allow=(REF,))
            self.assertTrue(h.interrupt()["aborted"])
        self.assertEqual(h.providers[TOKYO].interrupted, [NATIVE])

    def test_the_wildcard_is_refused_at_load_unless_it_is_asked_for(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(_binding_yaml(policy_extra='control_allow:\n    "*"\n'),
                            encoding="utf-8")
            with self.assertRaises(FleetRefusal) as caught:
                load_config(path)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("allow_wildcard_control", caught.exception.detail)

    def test_the_wildcard_is_a_deliberate_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            path.write_text(
                _binding_yaml(policy_extra='control_allow:\n    "*"\n  allow_wildcard_control: true\n'),
                encoding="utf-8",
            )
            config = load_config(path)
        self.assertEqual(config.policy.control_allow, ("*",))
        self.assertTrue(config.policy.allow_wildcard_control)

    def test_the_wildcard_arms_the_device_gate_but_not_the_scope(self):
        """The wildcard widens one gate, and the scope is still checked first."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), control_allow=("*",), wildcard_control=True)
            self.assertTrue(h.interrupt(f"{TOKYO}|claude|{OTHER_NATIVE}")["aborted"])
            result = h.interrupt_via_tool(SCOPE_READ, SCOPE_SEND, session_ref=REF)
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")


# ---------------------------------------------------------------------------
# What the provider answers, and what the caller is told
# ---------------------------------------------------------------------------


class AbortedIsForwardedNotInvented(unittest.TestCase):
    def test_a_stopped_run_comes_back_as_aborted_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            answer = h.interrupt()
        self.assertIs(answer["aborted"], True)
        self.assertNotIn("message", answer,
                         "a successful abort has nothing to explain and the surface "
                         "must not invent a sentence for it")
        self.assertEqual(answer["device_ref"], TOKYO)
        self.assertEqual(answer["provider"], "claude")
        self.assertEqual(answer["native_session_id"], NATIVE)
        self.assertEqual(h.providers[TOKYO].asked, [{"native": NATIVE, "ref": REF}])

    def test_no_run_in_flight_is_a_success_with_cloudclis_own_sentence(self):
        """The distinction the verb exists to preserve.

        CloudCLI answers `{"aborted": false, "message": "..."}` when there was
        nothing to stop. That is a true statement about the world, so it comes
        back as a result rather than as a refusal — and its sentence is forwarded
        rather than paraphrased, because the caller reads it to decide what to do
        next.
        """
        sentence = ("This session currently has no run in flight, so there is "
                    "no run to abort.")
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.providers[TOKYO].answer = {"aborted": False, "message": sentence}
            answer = h.interrupt()
        self.assertIs(answer["aborted"], False)
        self.assertEqual(answer["message"], sentence)
        self.assertIn("not that the call failed", answer["note"])

    def test_a_run_that_says_false_without_a_reason_still_says_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.providers[TOKYO].answer = {"aborted": False}
            answer = h.interrupt()
        self.assertIs(answer["aborted"], False)
        self.assertNotIn("message", answer)

    def test_a_truthy_string_is_read_as_true_and_not_as_a_string(self):
        """The answer is normalised to a boolean, because callers branch on it."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.providers[TOKYO].answer = {"aborted": "true"}
            answer = h.interrupt()
        self.assertIs(answer["aborted"], True)

    def test_an_answer_with_no_aborted_field_is_reported_rather_than_read_as_false(self):
        """The worst possible reading, ruled out.

        `structured()` returns `{}` for a provider result it could not parse, and
        `bool({}.get("aborted"))` is `False` — so without this guard a garbled
        reply would be reported as the specific, confident claim "there was no
        run in flight", which tells the caller to stop worrying about a run that
        may well still be going.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.providers[TOKYO].answer = {}
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "INTERNAL")
        self.assertIn("aborted", caught.exception.detail)

    def test_a_provider_refusal_is_not_translated_into_an_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.providers[TOKYO].fail = FleetRefusal(
                "SESSION_NOT_FOUND", "no such session on this device"
            )
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt()
        self.assertEqual(caught.exception.code, "SESSION_NOT_FOUND")


class TheVerbTakesNoSecondArgument(unittest.TestCase):
    def test_the_adapter_is_called_with_the_key_and_nothing_else(self):
        """A second argument would have to be invented by someone, later.

        `interrupt_with_a_parameter` raises. Reaching it — or reaching it at all
        — is the failure this asserts against: the surface has exactly one way to
        ask, and it is `interrupt(key)`.
        """

        class OnlyOneArgument(FakeProvider):
            def __init__(self, device_ref: str) -> None:
                super().__init__(device_ref)
                self.positional: int | None = None
                self.extra_kwargs: dict = {}

            def interrupt(self, *args, **kwargs):
                self.positional = len(args)
                self.extra_kwargs = dict(kwargs)
                return {"aborted": True}

        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            provider = OnlyOneArgument(TOKYO)
            h.providers[TOKYO] = provider
            h.service.providers[TOKYO] = provider
            answer = h.interrupt()
        self.assertTrue(answer["aborted"])
        self.assertEqual(provider.positional, 1, "the surface passed more than the key")
        self.assertEqual(provider.extra_kwargs, {},
                         "the surface passed a keyword argument to the adapter")

    def test_the_reason_it_has_no_idempotency_key_is_that_the_answer_is_about_now(self):
        """Two calls are two questions, and the second one gets a fresh answer.

        A cached `aborted: true` is a statement about the past wearing the
        present's clothes: it could tell a caller their run was stopped after a
        later one had already started. Replaying the *action* is the safe
        reading, because aborting a session with nothing running is not an error.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            first = h.interrupt()
            # Something new starts in the same session between the two calls.
            h.providers[TOKYO].answer = {"aborted": False, "message": "nothing running"}
            second = h.interrupt()
        self.assertTrue(first["aborted"])
        self.assertFalse(second["aborted"],
                         "the second call was answered from the first call's cache")
        self.assertEqual(len(h.providers[TOKYO].asked), 2,
                         "the provider was not asked the second time")


# ---------------------------------------------------------------------------
# Scope, audit and reach
# ---------------------------------------------------------------------------


class TheVerbIsScopedToTheDeviceNamed(unittest.TestCase):
    def test_only_the_named_devices_provider_is_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.interrupt()
        for ref in (WINDOWS, DELL_LLM, PERSONAL_DELL):
            with self.subTest(device=ref):
                self.assertEqual(h.providers[ref].asked, [],
                                 "an interrupt fanned out to a device nobody named")

    def test_the_same_native_id_on_another_device_is_a_different_session(self):
        """The native id alone is not an address; the device is part of it."""
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), control_allow=(f"{WINDOWS}|claude|{NATIVE}",))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt(REF)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[WINDOWS].asked, [],
                         "an id authorized on one device was honoured on another")
        self.assertEqual(h.providers[TOKYO].asked, [])

    def test_a_bare_native_id_is_not_an_address(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                h.interrupt(NATIVE)
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")
        self.assertEqual(h.providers[TOKYO].asked, [])


class TheAuditRecordsTheVerdictAndNoContent(unittest.TestCase):
    def test_the_line_names_the_session_and_the_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.interrupt()
            lines = h.audit_lines(tmp)
        record = json.loads(lines[-1])
        self.assertEqual(record["event"], "fleet.interrupt")
        self.assertEqual(record["session_ref"], REF)
        self.assertIs(record["aborted"], True)

    def test_a_refused_interrupt_is_not_audited_as_one_that_happened(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp), control_allow=())
            with self.assertRaises(FleetRefusal):
                h.interrupt()
            lines = h.audit_lines(tmp)
        self.assertNotIn("fleet.interrupt", "\n".join(lines))

    def test_the_line_carries_no_message_because_there_is_none_to_carry(self):
        """By construction, not by redaction.

        An `interrupt` that also accepted a reason string would be a
        transcript-adjacent write in a log that promises not to hold one. The
        schema has one argument, so the audit line has nothing to leak — and this
        asserts that the argument list has not grown since.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(Path(tmp))
            h.interrupt_via_tool(session_ref=REF)
            lines = h.audit_lines(tmp)
        record = json.loads(lines[-1])
        self.assertEqual(set(record) & {"message", "reason", "note", "run_id"}, set())


class TheNodeTransportSignsAndCloses(unittest.TestCase):
    """The route the far side of an interrupt is reached over.

    The connector itself has its own tests (`test_fleet_mcp_node.py`); what is
    asserted here is the contract between the two halves: the route is declared,
    it is in the closed set, and it carries no parameter that could name anything
    other than a session.
    """

    def test_the_interrupt_route_is_declared_once(self):
        from fleet_mcp import node_protocol as np

        self.assertEqual(np.ROUTES.count("POST /v1/sessions/{id}/interrupt"), 1)
        self.assertIn("POST /v1/sessions/{id}/interrupt", np.ROUTES)

    def test_the_route_table_contains_one_interrupt_entry(self):
        from fleet_mcp import node_protocol as np

        # The session routes, native mirror, core status and fresh projects.
        self.assertEqual(len(np.ROUTES), 12)
        # Named so that a future edit cannot add a second one under cover of
        # this count going up.
        self.assertEqual([r for r in np.ROUTES if "interrupt" in r],
                         ["POST /v1/sessions/{id}/interrupt"])

    def test_the_route_names_only_a_session(self):
        from fleet_mcp import node_protocol as np

        route = "POST /v1/sessions/{id}/interrupt"
        placeholders = [s.strip("{}") for s in route.partition(" ")[2].split("/")
                        if s.startswith("{")]
        self.assertEqual(placeholders, ["id"])

    def test_the_node_adapter_posts_to_that_route_with_an_empty_body(self):
        """A body the far side does not read would read here as honoured."""
        from fleet_mcp.providers.fleet_node import FleetNodeProvider

        seen: list[tuple] = []

        class Recorder(FleetNodeProvider):
            def _request(self, method, path, payload=None):
                seen.append((method, path, payload))
                return {"aborted": True}

        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp) / "node.secret"
            anchor.write_text("node-secret-value", encoding="utf-8")
            provider = Recorder(_binding(TOKYO, anchor=anchor, allow_control=True))
            key = SessionKey.parse(REF)
            provider.interrupt(key)
        self.assertEqual(seen, [("POST", f"/v1/sessions/{NATIVE}/interrupt", {})])

    def test_a_refusal_from_the_other_machines_floor_keeps_its_own_name(self):
        """Two doors, and an operator diagnosing a refusal needs to know which.

        The connector's floor answers `WRITE_NOT_GRANTED`; that is *that
        machine's* answer and it is reported as such rather than restated as this
        side's.
        """
        from fleet_mcp.providers.fleet_node import FleetNodeProvider

        class Refusing(FleetNodeProvider):
            def _request(self, method, path, payload=None):
                raise FleetRefusal("WRITE_NOT_GRANTED",
                                   "this connector is not authorized to interrupt runs")

        with tempfile.TemporaryDirectory() as tmp:
            anchor = Path(tmp) / "node.secret"
            anchor.write_text("node-secret-value", encoding="utf-8")
            provider = Refusing(_binding(TOKYO, anchor=anchor, allow_control=True))
            with self.assertRaises(FleetRefusal) as caught:
                provider.interrupt(SessionKey.parse(REF))
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")


class TheDeviceBindingDefaultsToRefusal(unittest.TestCase):
    """A provider entry that says nothing about a verb has not authorized it.

    The device gate is the outermost of the control gates, and the only one an
    operator writes by hand in the local binding. Every boolean on it therefore
    needs a default, and every default has to be the closed one — `allow_send`
    for the reason the send gates gave, `allow_create` for the create gate's,
    and `allow_control` because it is the one whose flip would end a run in
    flight. A suite that writes all three explicitly in every harness would
    stay green if any of those defaults were inverted, which is why this class
    writes none of them.
    """

    def _load(self, tmp: str, extra: str = ""):
        path = Path(tmp) / "binding.yaml"
        path.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: device:work-fj\n"
            "providers:\n"
            "  device:work-fj:\n"
            "    kind: cloudcli-mcp-http\n"
            "    endpoint: http://127.0.0.1:13001/mcp\n"
            "    credential_anchor: ~/.fleet/pat\n"
            + extra,
            encoding="utf-8",
        )
        return load_config(path)

    def test_a_binding_that_names_no_verb_authorizes_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load(tmp)
        binding = config.providers[WINDOWS]
        self.assertIs(binding.allow_send, False)
        self.assertIs(binding.allow_create, False)
        self.assertIs(binding.allow_control, False)
        # And the session half is empty rather than a wildcard nobody wrote.
        self.assertEqual(config.policy.control_allow, ())
        self.assertIs(config.policy.allow_wildcard_control, False)

    def test_the_control_flag_survives_the_round_trip_on_the_device_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load(tmp, "    allow_control: true\n")
        self.assertIs(config.providers[WINDOWS].allow_control, True)
        # The flag alone is the device half; it names no session.
        self.assertEqual(config.policy.control_allow, ())

    def test_the_device_flag_alone_does_not_admit_an_interrupt(self):
        """Both halves, or nothing — measured through the service, not the field.

        `allow_control: true` with nothing in `policy.control_allow` is the
        state a binding passes through while it is being written, and it has to
        be a refusal rather than an accidental grant, so that a caller cannot
        reach a run by naming it before the operator has named it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = self._load(tmp, "    allow_control: true\n")
        device = FakeProvider(WINDOWS)
        service = FleetService(config, providers={WINDOWS: device})
        principal = Principal(
            name="owner",
            scopes=frozenset((SCOPE_READ, SCOPE_SEND, SCOPE_CREATE, SCOPE_CONTROL)),
            credential_kind="pat",
        )
        with self.assertRaises(FleetRefusal) as caught:
            service.interrupt_session(principal, SessionKey.parse(f"{WINDOWS}|claude|{NATIVE}"))
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("control_allow", caught.exception.detail)
        self.assertEqual(device.asked, [], "the device was asked before its session was named")


def _binding_yaml(auth_extra: str = "", *, policy_extra: str = "") -> str:
    """A minimal valid local binding, with one line's worth of room to vary."""
    return (
        "schema: serveros/fleet-mcp.local/v1\n"
        "device_ref: device:work-fj\n"
        "providers:\n"
        "  device:work-fj:\n"
        "    kind: cloudcli-mcp-http\n"
        "    endpoint: http://127.0.0.1:13001/mcp\n"
        "    credential_anchor: ~/.fleet/pat\n"
        + (f"auth:\n  {auth_extra}" if auth_extra else "")
        + ("policy:\n  " + policy_extra if policy_extra else "")
    )


if __name__ == "__main__":
    unittest.main()
