#!/usr/bin/env python3
"""`fleet_create_session`: the one verb here whose *result* is a new object.

Everything else on this surface answers a question about something that already
exists. Creation is different in one way that decides the whole design: the
caller gets back an address, and the question worth asking is not "can it create
a session" but "what does the thing it created let it do".

The answer this module holds is that it lets it do nothing. Creating a session
confers no control over it — that is a second grant, on a different key, made by
a person. Without that separation an agent that could create would have a way to
manufacture its own control targets, and this surface's central claim (a caller
never chooses what it is authorized to do) would hold for every verb except the
one that makes new things.

Every provider below is a fake. Nothing here sends a model request, and nothing
here closes, starts or interrupts a real session — there is no session to close.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import scopes  # noqa: E402
from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.errors import (  # noqa: E402
    APPROVAL_REQUIRED,
    BAD_REQUEST,
    IDEMPOTENT_REPLAY,
    INTERNAL,
    SCOPE_DENIED,
    UNSUPPORTED_BY_TRANSPORT,
    WRITE_NOT_GRANTED,
    FleetRefusal,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth  # noqa: E402

DELL = "device:personal-dell"
WORK = "device:work-fj"

PROJECT = "serveros"
OTHER_PROJECT = "someone-elses-project"


class FakeCloudCli:
    """A device that answers `session_create` the way CloudCLI does.

    It records what it was asked for, because several assertions below are about
    what this surface *did not send* — a provider that never receives a
    `permissionMode` is the only honest evidence that no field can carry one.
    """

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str, *, session_id: str | None = "new-session-1",
                 run_id: str | None = None) -> None:
        self.device_ref = device_ref
        self._session_id = session_id
        self._run_id = run_id
        self.created: list[dict[str, object]] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(device_ref=self.device_ref, kind=self.kind,
                              reachable=True, authorized=True, detail="fake")

    def create_session(self, *, project, message=None, lifecycle_mode=None):
        call: dict[str, object] = {"project": project, "message": message,
                                   "lifecycle_mode": lifecycle_mode}
        self.created.append(call)
        payload: dict[str, object] = {}
        if self._session_id:
            payload["sessionId"] = f"{self._session_id}-{len(self.created)}"
        # A run comes back only when a first message opened one — that is what
        # CloudCLI does (`buildSessionCreate` returns a runId only on the path
        # where a message was supplied), and a fake that returned one
        # unconditionally would make `run_started` meaningless as an assertion.
        if self._run_id and message:
            payload["runId"] = self._run_id
        return payload

    # The rest of the interface is present and unused: a missing method would
    # raise AttributeError, which reads as a defect in this surface rather than
    # as a verb this test does not exercise.
    def list_sessions(self, *, limit, project=None, state=None):
        return []

    def get_session(self, key):
        raise FleetRefusal("SESSION_NOT_FOUND", "not created by this fake")

    def read_session(self, key, **kwargs):
        return {}

    def send(self, key, message, *, idempotency_key=None):
        return {}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {}


class FakeNodeConnector:
    """A device reached through the outbound connector — which has no create."""

    kind = "fleet-node"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref

    def health(self) -> ProviderHealth:
        return ProviderHealth(device_ref=self.device_ref, kind=self.kind,
                              reachable=True, authorized=True, detail="fake connector")

    def create_session(self, *, project, message=None, lifecycle_mode=None):
        # The real `FleetNodeProvider.create_session` refuses rather than
        # raising `NotImplementedError`; reproduced here so that the assertion in
        # `TheTransportGapIsTyped` is about the surface's handling and not about
        # a stub's choice.
        raise FleetRefusal(
            UNSUPPORTED_BY_TRANSPORT,
            f"{self.device_ref} is reached through a node connector, whose closed "
            f"route table has no creation verb.",
        )


def _binding(device_ref: str, *, anchor: Path, allow_create: bool = False,
             allow_send: bool = False) -> ProviderBinding:
    return ProviderBinding(device_ref=device_ref, kind="cloudcli-mcp-http",
                           endpoint="http://127.0.0.1:3011/mcp",
                           credential_anchor=anchor, allow_send=allow_send,
                           allow_create=allow_create)


class Harness:
    """A service over fake devices, with a real registry and a real audit file.

    `create_allow` and `allow_create` default to empty/False here as they do in
    the shipped configuration: most tests below are about the refusal, and a
    harness that authorized creation by default would make every one of them
    pass for the wrong reason.
    """

    def __init__(self, tmp: Path, *, allow_create: bool = False,
                 create_allow: tuple[str, ...] = (), allow_wildcard_create: bool = False,
                 send_allow: tuple[str, ...] = (), allow_send: bool = False,
                 allow_wildcard_send: bool = False, max_message_chars: int = 8000):
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        self.anchor = anchor
        self.config = FleetConfig(
            source=None,
            local_device_ref=WORK,
            providers={
                DELL: _binding(DELL, anchor=anchor, allow_create=allow_create,
                               allow_send=allow_send),
                WORK: _binding(WORK, anchor=anchor, allow_send=allow_send),
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=send_allow, create_allow=create_allow,
                                 allow_wildcard_send=allow_wildcard_send,
                                 allow_wildcard_create=allow_wildcard_create,
                                 max_message_chars=max_message_chars),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.dell = FakeCloudCli(DELL)
        self.service = FleetService(self.config, providers={DELL: self.dell})

    def principal(self, *scopes: str, name: str = "tester") -> Principal:
        return Principal(
            name=name,
            scopes=frozenset(scopes or ("fleet:read", "fleet:send", "fleet:create")),
            credential_kind="pat",
        )

    def narrow_create_allow(self, value: tuple[str, ...]) -> None:
        """Replace the project allow-list the way an operator's reload would.

        `FleetConfig` is frozen so that no code path can edit a policy in
        place, which is the property being preserved — a service that could
        rewrite its own allow-list is the thing this whole surface is built to
        prevent. The test therefore steps around the freeze deliberately, to
        stand in for an operator editing the binding and the surface reloading
        it. Nothing else in this module mutates a live config.
        """
        from dataclasses import replace

        object.__setattr__(self.config, "policy",
                           replace(self.config.policy, create_allow=value))

    def audit_lines(self) -> list[dict]:
        path = self.config.audit.path
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]


class _HarnessCase(unittest.TestCase):
    def harness(self, **kwargs) -> Harness:
        tmp = Path(tempfile.mkdtemp(prefix="fleet-create-"))
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        return Harness(tmp, **kwargs)


class CreatingConfersNoControl(_HarnessCase):
    """The property the whole design of this verb exists to hold."""

    def test_the_session_it_returns_is_not_sendable(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        record = harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT
        )
        self.assertTrue(record["created"])
        self.assertEqual(record["control"],
                         {"send_allowed": False, "device_authorized": False,
                          "session_allowlisted": False})
        self.assertIn("NOT authorized for control", record["note"])

    def test_sending_to_the_session_it_just_created_is_refused(self):
        """Exercised end to end rather than asserted on the `control` block.

        The block is this surface's report of its own decision; the refusal is
        the decision. A test that only read the block would pass on a surface
        that reported `send_allowed: false` and then sent anyway.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        created = harness.service.create_session(principal, device=DELL, project=PROJECT)
        key = SessionKey.parse(created["session_ref"])
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.send_to_session(principal, key, message="now do this")
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_creating_writes_nothing_to_the_send_allow_list(self):
        """The allow-list is an operator's document and this call cannot edit it.

        Checked against the live config object rather than against a copy: a
        surface that appended to the frozen tuple would be creating a control
        target for itself, which is the failure this whole module is about.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        before = harness.config.policy.send_allow
        created = harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT
        )
        self.assertEqual(harness.config.policy.send_allow, before)
        self.assertNotIn(created["session_ref"], harness.config.policy.send_allow)

    def test_a_session_created_with_a_message_reports_the_run_separately(self):
        """\"I made a session\" and \"I started an agent\" are two facts."""
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        harness.dell._run_id = "run-1"
        with_message = harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT, message="hello"
        )
        self.assertTrue(with_message["run_started"])
        self.assertEqual(with_message["run_id"], "run-1")

        without = harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT
        )
        self.assertFalse(without["run_started"])
        self.assertIsNone(without["run_id"])
        self.assertFalse(without["control"]["send_allowed"])


class TwoKeysAndNeitherIsTheSendKey(_HarnessCase):
    def test_an_unauthorized_device_is_refused_and_says_which_half(self):
        harness = self.harness(allow_create=False, create_allow=(PROJECT,))
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(caught.exception.as_payload().get("device_ref"), DELL)
        self.assertIn("allow_create", caught.exception.detail)
        self.assertEqual(harness.dell.created, [], "the provider was asked anyway")

    def test_an_unauthorized_project_is_refused_and_says_which_half(self):
        harness = self.harness(allow_create=True, create_allow=(OTHER_PROJECT,))
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        payload = caught.exception.as_payload()
        self.assertEqual(payload.get("project"), PROJECT)
        self.assertEqual(payload.get("allowed"), [OTHER_PROJECT])
        self.assertIn("create_allow", caught.exception.detail)
        self.assertEqual(harness.dell.created, [], "the provider was asked anyway")

    def test_the_two_refusals_are_about_different_things(self):
        """A caller told to edit the wrong stanza will edit the wrong stanza."""
        device_denied = self.harness(allow_create=False, create_allow=(PROJECT,))
        project_denied = self.harness(allow_create=True, create_allow=(OTHER_PROJECT,))
        try:
            device_denied.service.create_session(
                device_denied.principal(), device=DELL, project=PROJECT
            )
        except FleetRefusal as exc:
            first = exc.detail
        try:
            project_denied.service.create_session(
                project_denied.principal(), device=DELL, project=PROJECT
            )
        except FleetRefusal as exc:
            second = exc.detail
        self.assertNotEqual(first, second)

    def test_authorization_to_send_is_not_authorization_to_create(self):
        """The keys are deliberately not the same key.

        A device the operator has authorized for control is a device whose
        *existing* sessions may be messaged; it says nothing about whether new
        ones may be made there.
        """
        harness = self.harness(allow_send=True, create_allow=(PROJECT,),
                               send_allow=("*",))
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_a_wildcard_project_grant_needs_its_own_opt_in(self):
        """A created session inherits its project's working directory.

        A wildcard here is therefore a wildcard over *where a new agent will
        run*, which is a different magnitude from a wildcard over which
        sessions may be messaged — so it is refused separately rather than
        inherited from the send side's opt-in.
        """
        harness = self.harness(allow_create=True, create_allow=("*",),
                               allow_wildcard_create=False)
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)

    def test_a_wildcard_with_its_opt_in_is_honoured(self):
        harness = self.harness(allow_create=True, create_allow=("*",),
                               allow_wildcard_create=True)
        record = harness.service.create_session(
            harness.principal(), device=DELL, project=OTHER_PROJECT
        )
        self.assertTrue(record["created"])


class TheCallerCannotExpressPrivilege(_HarnessCase):
    """Five fields are absent on purpose, and the absence is asserted."""

    ABSENT_FIELDS = ("provider", "model", "permission_mode", "permissionMode",
                     "environment", "cwd", "workspace", "env")

    def test_the_schema_has_no_field_for_a_privilege_decision(self):
        schema = fleet_tools.TOOLS_BY_NAME["fleet_create_session"].input_schema
        declared = set(schema["properties"])
        for absent in self.ABSENT_FIELDS:
            with self.subTest(field=absent):
                self.assertNotIn(absent, declared)
        self.assertEqual(sorted(declared),
                         ["device", "idempotency_key", "lifecycle_mode",
                          "message", "project"])

    def test_a_privilege_field_is_refused_rather_than_ignored(self):
        """The failure mode of a filter: an ignored field does not fail, it widens.

        A caller that sends `permission_mode: bypassPermissions` must be told it
        was not accepted, not handed a session that quietly defaults to
        something they did not ask for and cannot see.
        """
        for field in ("permission_mode", "model", "provider"):
            with self.subTest(field=field):
                args = {"device": DELL, "project": PROJECT, field: "anything"}
                refusal = fleet_tools.unknown_argument_refusal(
                    fleet_tools.TOOLS_BY_NAME["fleet_create_session"], args
                )
                self.assertIsNotNone(refusal, f"{field} was accepted by the schema")
                self.assertEqual(refusal.code, BAD_REQUEST)

    def test_the_adapter_never_sends_a_privilege_field_to_the_provider(self):
        """The strongest form of the claim: the far side is not asked.

        Asserted on the recorded call rather than on the schema, because a
        schema is a promise about the front door and this is about the wire.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT, message="hi"
        )
        self.assertEqual(len(harness.dell.created), 1)
        call = harness.dell.created[0]
        self.assertEqual(sorted(call), ["lifecycle_mode", "message", "project"])
        self.assertEqual(call["project"], PROJECT)

    def test_a_provider_family_is_never_taken_from_the_caller(self):
        """CloudCLI defaults the family to `claude`; this surface does not relay it.

        The session_ref is composed with the family the *binding* names, so
        there is no argument here that could compose a different one.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        record = harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT
        )
        self.assertTrue(record["session_ref"].startswith(f"{DELL}|claude|"))

    def test_the_lifecycle_mode_set_is_bounded_here_not_relayed(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        for accepted in ("per-run", "resident"):
            with self.subTest(accepted=accepted):
                harness.service.create_session(
                    harness.principal(), device=DELL, project=PROJECT,
                    lifecycle_mode=accepted,
                )
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT,
                lifecycle_mode="bypassPermissions",
            )
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertIn("resident", caught.exception.detail, "the refusal names the accepted set")


class TheScopeIsNecessaryAndNeverSufficient(_HarnessCase):
    def test_a_credential_without_the_scope_cannot_create(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        reader = harness.principal("fleet:read", "fleet:send")
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(reader, device=DELL, project=PROJECT)
        self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertIn(scopes.SCOPE_CREATE, caught.exception.detail)
        self.assertEqual(harness.dell.created, [])

    def test_the_scope_alone_is_not_enough(self):
        """Every layer below the scope must still be closed.

        A token carrying `fleet:create` against a deployment that has not
        authorized the device or the project is the case where a plausible-
        looking credential meets a refusal — and the refusal must be about the
        deployment's decision, not about the token.
        """
        harness = self.harness(allow_create=False, create_allow=())
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(scopes.SCOPE_CREATE), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertNotEqual(caught.exception.code, SCOPE_DENIED)

    def test_only_one_tool_requires_the_create_scope(self):
        """One scope, one verb. A second tool behind it would be a second
        capability granted by a credential that was asked for one thing."""
        service_source = (ROOT / "fleet_mcp" / "fleet.py").read_text(encoding="utf-8")
        self.assertEqual(service_source.count("require(SCOPE_CREATE)"), 1)


class Idempotency(_HarnessCase):
    def test_a_replayed_key_returns_the_recorded_session_and_creates_no_second(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        first = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        second = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        self.assertEqual(len(harness.dell.created), 1, "a second session was created")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["code"], IDEMPOTENT_REPLAY)
        self.assertEqual(second["session_ref"], first["session_ref"])

    def test_a_different_key_creates_a_different_session(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        first = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        second = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-2"
        )
        self.assertEqual(len(harness.dell.created), 2)
        self.assertNotEqual(first["session_ref"], second["session_ref"])

    def test_one_principals_key_does_not_replay_for_another(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        harness.service.create_session(
            harness.principal(name="alice"), device=DELL, project=PROJECT,
            idempotency_key="shared",
        )
        harness.service.create_session(
            harness.principal(name="bob"), device=DELL, project=PROJECT,
            idempotency_key="shared",
        )
        self.assertEqual(len(harness.dell.created), 2)

    def test_a_withdrawn_grant_is_rechecked_before_the_replay_lookup(self):
        """The order of two statements, and it is a security property.

        A key issued while the project was authorized must not be a way to
        create again after the operator has withdrawn the grant. If the replay
        lookup came first, the recorded session would be returned and the
        withdrawal would be invisible.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        harness.narrow_create_allow(())
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                principal, device=DELL, project=PROJECT, idempotency_key="k-1"
            )
        self.assertEqual(caught.exception.code, APPROVAL_REQUIRED)
        self.assertEqual(len(harness.dell.created), 1, "the replay created a second session")


class TheAuditRecordSaysWhatWasDoneAndNotWhatWasSaid(_HarnessCase):
    def test_a_creation_is_recorded_with_its_principal_device_and_project(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        record = harness.service.create_session(
            harness.principal(name="alice"), device=DELL, project=PROJECT,
            message="a secret instruction", idempotency_key="k-1",
        )
        events = {line["event"]: line for line in harness.audit_lines()}
        self.assertIn("fleet.create_session", events)
        entry = events["fleet.create_session"]
        self.assertEqual(entry["principal"], "alice")
        self.assertEqual(entry["device_ref"], DELL)
        self.assertEqual(entry["project"], PROJECT)
        self.assertEqual(entry["session_ref"], record["session_ref"])
        self.assertEqual(entry["idempotency_key"], "k-1")

    def test_the_message_text_is_not_in_the_audit_trail(self):
        """Counted, never quoted.

        An audit line is durable, is read by people who were not the caller,
        and is not covered by the transcript redaction pass. A message body
        recorded here would be an unbounded copy of remote text in the one
        place nothing scans.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        secret = "the-launch-code-is-0000-do-not-log-me"
        harness.service.create_session(
            harness.principal(), device=DELL, project=PROJECT, message=secret
        )
        raw = harness.config.audit.path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)

    def test_a_refusal_is_not_recorded_as_a_creation(self):
        harness = self.harness(allow_create=False, create_allow=(PROJECT,))
        with self.assertRaises(FleetRefusal):
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertNotIn("fleet.create_session",
                         [line["event"] for line in harness.audit_lines()])

    def test_a_replay_is_recorded_under_its_own_event(self):
        """A replay is not a creation, and the trail must not call it one."""
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        for _ in range(2):
            harness.service.create_session(
                principal, device=DELL, project=PROJECT, idempotency_key="k-1"
            )
        events = [line["event"] for line in harness.audit_lines()]
        self.assertEqual(events.count("fleet.create_session"), 1)
        self.assertEqual(events.count("fleet.create_session.replay"), 1)

    def test_a_replay_reports_false_for_created(self):
        """The one field a caller acts on, asserted on its own.

        A replay that answered `created: true` would tell a caller a second
        session exists when none does — and the caller's next move is usually to
        send to it.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        replay = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        self.assertIs(replay["created"], False)
        self.assertIs(replay["idempotent_replay"], True)

    def test_a_replay_reports_control_as_it_is_now_not_as_it_was(self):
        """A permission is a present-tense fact and is not replayed from a record.

        The session's address is history; whether it may be driven is not. An
        operator who allow-lists the session between the two calls must be told
        so, or the caller will not attempt the send it has just been granted.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        principal = harness.principal()
        first = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        self.assertFalse(first["control"]["send_allowed"])
        # The operator now adds the exact session_ref to the send allow-list and
        # turns on the device's control grant — the documented way to authorize
        # a session that already exists.
        from dataclasses import replace

        object.__setattr__(harness.config, "policy",
                           replace(harness.config.policy, send_allow=(first["session_ref"],)))
        object.__setattr__(
            harness.config, "providers",
            {**harness.config.providers,
             DELL: replace(harness.config.providers[DELL], allow_send=True)},
        )
        replay = harness.service.create_session(
            principal, device=DELL, project=PROJECT, idempotency_key="k-1"
        )
        self.assertTrue(replay["control"]["send_allowed"])
        self.assertIn("ALREADY authorized", replay["note"])
        # And the send really does now succeed, so the block is not merely
        # optimistic prose.
        harness.service.send_to_session(
            principal, SessionKey.parse(first["session_ref"]), message="go"
        )

    def test_the_note_and_the_control_block_never_disagree(self):
        """Two configurations, two notes, and the block is what decides.

        The second case is reachable with a wildcard send grant, which is the
        only way a *newly created* session can already be allow-listed: a
        per-session grant names a session_ref, and a session that does not exist
        yet cannot have been named.
        """
        for case, expected in (("default", "NOT authorized"),
                               ("wildcard", "ALREADY authorized")):
            with self.subTest(case=case):
                harness = self.harness(
                    allow_create=True, create_allow=(PROJECT,),
                    allow_send=(case == "wildcard"),
                    send_allow=("*",) if case == "wildcard" else (),
                    allow_wildcard_send=(case == "wildcard"),
                )
                record = harness.service.create_session(
                    harness.principal(), device=DELL, project=PROJECT
                )
                self.assertEqual(record["control"]["send_allowed"],
                                 case == "wildcard")
                self.assertIn(expected, record["note"])


class BoundedInputs(_HarnessCase):
    def test_a_missing_device_is_refused_at_the_tool_layer(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        ctx = fleet_tools.ToolContext(service=harness.service,
                                      principal=harness.principal())
        result = fleet_tools.call_tool(ctx, "fleet_create_session",
                                       {"project": PROJECT})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], BAD_REQUEST)

    def test_a_missing_project_is_refused_at_the_tool_layer(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        ctx = fleet_tools.ToolContext(service=harness.service,
                                      principal=harness.principal())
        result = fleet_tools.call_tool(ctx, "fleet_create_session", {"device": DELL})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], BAD_REQUEST)

    def test_an_over_long_message_is_refused_rather_than_truncated(self):
        """A silently shortened instruction is executed as if it were the whole one."""
        harness = self.harness(allow_create=True, create_allow=(PROJECT,),
                               max_message_chars=10)
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT, message="x" * 11
            )
        self.assertEqual(caught.exception.code, WRITE_NOT_GRANTED)
        self.assertEqual(harness.dell.created, [])

    def test_a_provider_that_does_not_name_the_session_is_an_internal_fault(self):
        """A session may exist that this surface cannot address — say so.

        Reporting it as a bad request would invite a blind retry, and the retry
        makes a second session.
        """
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        harness.dell._session_id = None
        with self.assertRaises(FleetRefusal) as caught:
            harness.service.create_session(
                harness.principal(), device=DELL, project=PROJECT
            )
        self.assertEqual(caught.exception.code, INTERNAL)
        self.assertIn("do not retry", caught.exception.detail.lower())


class TheTransportGapIsTyped(unittest.TestCase):
    """A verb the far side does not have is not a permission problem."""

    def test_the_node_connector_refuses_with_its_own_code_not_a_crash(self):
        from fleet_mcp.providers.fleet_node import FleetNodeProvider

        binding = ProviderBinding(
            device_ref=WORK, kind="fleet-node",
            endpoint="http://127.0.0.1:3402",
            credential_anchor=Path("/nonexistent/node-secret"),
        )
        provider = FleetNodeProvider(binding)
        with self.assertRaises(FleetRefusal) as caught:
            provider.create_session(project=PROJECT)
        self.assertEqual(caught.exception.code, UNSUPPORTED_BY_TRANSPORT)
        payload = caught.exception.as_payload()
        self.assertEqual(payload.get("device_ref"), WORK)
        self.assertNotEqual(caught.exception.code, WRITE_NOT_GRANTED)

    def test_the_code_is_not_one_a_permission_refusal_uses(self):
        """The distinction is the reason the code exists.

        `WRITE_NOT_GRANTED` sends an operator to an allow-list; this sends them
        to the device. Sharing a code would make the two indistinguishable at
        the point where the caller decides what to do.
        """
        self.assertNotEqual(UNSUPPORTED_BY_TRANSPORT, WRITE_NOT_GRANTED)

    def test_the_connector_route_table_really_has_no_creation_route(self):
        """Checked against the route table, not against the provider's prose."""
        from fleet_mcp import node_protocol as np

        self.assertNotIn("POST /v1/sessions", np.ROUTES)
        self.assertFalse(any(route.upper().startswith("POST") and "session" in route
                             and "sessions" == route.rstrip("/").split("/")[-1]
                             for route in np.ROUTES))


class WhatTheDeviceReportsAboutItself(_HarnessCase):
    def test_a_device_that_does_not_create_says_so_before_being_asked(self):
        """The allow-list is readable, so a caller need not learn by refusal."""
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        payload = harness.service.list_devices(harness.principal())
        dell = next(row for row in payload["devices"] if row["device_ref"] == DELL)
        self.assertTrue(dell["control"]["create_allowed_device"])
        self.assertEqual(dell["control"]["create_allowed_projects"], [PROJECT])

    def test_status_reports_the_create_allow_list(self):
        harness = self.harness(allow_create=True, create_allow=(PROJECT,))
        payload = harness.service.status(harness.principal())
        self.assertEqual(payload["policy"]["create_allow"], [PROJECT])

    def test_an_empty_allow_list_is_reported_as_empty_not_as_absent(self):
        harness = self.harness()
        payload = harness.service.list_devices(harness.principal())
        dell = next(row for row in payload["devices"] if row["device_ref"] == DELL)
        self.assertFalse(dell["control"]["create_allowed_device"])
        self.assertEqual(dell["control"]["create_allowed_projects"], [])


if __name__ == "__main__":
    unittest.main()
