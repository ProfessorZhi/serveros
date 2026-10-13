#!/usr/bin/env python3
"""`fleet_create_session`: the first verb that *mints* something on a device.

Messaging a session that exists and creating one that does not are different
questions, and the difference is why this tool was left unbuilt in the round
that built the other seven (`fleet/fleet-mcp-contract.yaml#not_implemented`).
This file is the admission question, written before the implementation:

*   the scope is independent (`fleet:create`), so a credential that may send is
    not thereby a credential that may create;
*   the device authorization is independent (`allow_create`), so a device the
    operator authorized for control is not thereby one they authorized for
    creation — and the default is refusal;
*   an unauthorized device is refused *before* any provider is asked, because a
    refusal that happens after the session exists is not a refusal;
*   the arguments are CloudCLI 1.39's own `session_create` schema, carried
    unchanged (the field names below are read from
    `mcp-session-lifecycle.js#SESSION_CREATE_INPUT_SCHEMA`), and nothing on this
    surface is a shell, a path or a generic passthrough.

The three devices named here are the three the operator asked to manage
centrally: `device:tokyo-shared-dev-01`, `device:work-fj`, `device:llm-lab-dell`.
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
    CreateGrant,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp import node_protocol as np  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (  # noqa: E402
    SCOPE_CREATE,
    SCOPE_READ,
    SCOPE_SEND,
    FleetService,
    Principal,
)
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    MANAGED_IDLE,
    ProviderHealth,
    SessionSummary,
    classify,
)

TOKYO = "device:tokyo-shared-dev-01"
WINDOWS = "device:work-fj"
DELL_LLM = "device:llm-lab-dell"
# Enrolled, and deliberately not authorized for creation by this harness.
PERSONAL_DELL = "device:personal-dell"

TARGETS = (TOKYO, WINDOWS, DELL_LLM)


def _summary(device: str, native: str, *, title: str = "a session") -> SessionSummary:
    row = {"running": False, "hostState": None}
    verdict, running, resident, evidence = classify(row)
    return SessionSummary(
        key=SessionKey.compose(device, "claude", native),
        title=title,
        classification=verdict,
        running=running,
        resident_host=resident,
        host_state=None,
        lifecycle_mode=None,
        project_id=None,
        last_activity=None,
        current_run=None,
        evidence=evidence,
    )


class FakeProvider:
    """A device that answers `create` and records exactly what it was asked.

    `create` answers in CloudCLI 1.39's own vocabulary (`sessionId`, optional
    `runId`) because that is what the real adapter returns: the row is minted by
    `sessionsService.createAppSession` and the run id exists only when a first
    message was sent (`mcp-session-lifecycle.js#buildSessionCreate`).
    """

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref
        self.created: list[dict] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref, kind=self.kind, reachable=True,
            authorized=True, detail="fake", server_name="claudecodeui-mcp-gateway",
            server_version="0.1.0", tool_count=23, latency_ms=2.0,
        )

    def list_sessions(self, *, limit: int, project=None, state=None):
        return []

    def get_session(self, key: SessionKey):
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, **kwargs):
        return {"messages": []}

    def send(self, key, message, *, idempotency_key=None):
        return {"runId": "run-send", "accepted": True}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {"state": "completed"}

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        record = {
            "project": project, "message": message, "provider": provider,
            "model": model, "lifecycle_mode": lifecycle_mode,
            "permission_mode": permission_mode,
        }
        self.created.append(record)
        session_id = f"created-{len(self.created)}"
        if message is None or message == "":
            return {"sessionId": session_id}
        return {"sessionId": session_id, "runId": f"run-{len(self.created)}"}


def _binding(device_ref: str, *, allow_create: bool, anchor: Path) -> ProviderBinding:
    return ProviderBinding(
        device_ref=device_ref, kind="cloudcli-mcp-http",
        endpoint="http://127.0.0.1:3011/mcp", credential_anchor=anchor,
        allow_send=False, allow_create=allow_create,
    )


class Harness:
    """A service over the three target devices, one of them unauthorized.

    Two independent authorizations, and the harness states both rather than
    relying on a default: `authorized` is the *device* gate (`allow_create` on
    the binding) and `projects` is the *project* gate (`policy.create_allow`).
    Neither implies the other, and the empty default for each is refusal — so a
    test that wants a create to happen has to say which project, which is the
    property under test elsewhere in this file.
    """

    # The projects these tests name. `p-1` throughout, with the ceiling the
    # `ArgumentsAreCloudclisOwnSchema` case needs: it asserts the snake_case
    # names reach the wire as camelCase, which requires a case that is allowed
    # to name a permission mode and a model at all.
    GRANT = {"p-1": CreateGrant(permission_modes=("bypassPermissions", "plan"),
                                models=("claude-opus-4", "m"))}

    def __init__(self, tmp: Path, *, authorized=TARGETS, rate_limit: int = 10_000,
                 max_message_chars: int = 8000, projects=None):
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        every = (TOKYO, WINDOWS, DELL_LLM, PERSONAL_DELL)
        self.config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                ref: _binding(ref, allow_create=ref in authorized, anchor=anchor)
                for ref in every
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                max_message_chars=max_message_chars,
                create_allow=dict(self.GRANT if projects is None else projects),
            ),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=rate_limit),
        )
        self.anchor = anchor
        self.providers = {ref: FakeProvider(ref) for ref in every}
        self.service = FleetService(self.config, providers=dict(self.providers))

    def principal(self, *scopes: str, name: str = "tester") -> Principal:
        return Principal(name=name, scopes=frozenset(scopes or (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)),
                         credential_kind="pat")

    def context(self, *scopes: str, name: str = "tester") -> "fleet_tools.ToolContext":
        return fleet_tools.ToolContext(service=self.service,
                                       principal=self.principal(*scopes, name=name))


class TheToolIsOnTheSurfaceAndTheOldOnesAreUnchanged(unittest.TestCase):
    def test_the_surface_gained_exactly_one_tool_and_kept_the_seven(self):
        names = fleet_tools.tool_names()
        self.assertIn("fleet_create_session", names)
        self.assertEqual(len(names), 8)
        # The seven that existed are still there, still in their order.
        self.assertEqual(names[:7], [
            "fleet_list_devices", "fleet_list_sessions", "fleet_get_session",
            "fleet_read_session", "fleet_send_to_session", "fleet_get_run",
            "fleet_status",
        ])

    def test_the_new_tool_declares_itself_a_write(self):
        tool = fleet_tools.TOOLS_BY_NAME["fleet_create_session"]
        self.assertFalse(tool.annotations["readOnlyHint"])
        self.assertFalse(tool.annotations["destructiveHint"])
        # CloudCLI has no idempotency of its own; the same call twice is two
        # sessions. This surface approximates it with a caller key only, so the
        # honest annotation is "not idempotent" — exactly as `session_send` says.
        self.assertFalse(tool.annotations["idempotentHint"])
        self.assertTrue(tool.annotations["openWorldHint"])

    def test_the_old_send_tool_is_untouched(self):
        tool = fleet_tools.TOOLS_BY_NAME["fleet_send_to_session"]
        self.assertEqual(sorted(tool.input_schema["properties"]),
                         sorted(["session_ref", "device", "session", "provider",
                                 "message", "idempotency_key"]))

    def test_the_new_tool_takes_no_url_host_path_or_command(self):
        banned = {"url", "uri", "host", "endpoint", "address", "command", "cmd",
                  "shell", "path", "callback", "webhook", "target", "argv",
                  "script", "file"}
        tool = fleet_tools.TOOLS_BY_NAME["fleet_create_session"]
        properties = set(tool.input_schema["properties"])
        self.assertEqual(properties & banned, set())
        # `project` is a provider id, not a filesystem path: it is resolved by
        # CloudCLI against its own project table, and a name that is not a
        # project id is refused there, not here.
        self.assertIn("project", properties)


class ArgumentsAreCloudclisOwnSchema(unittest.TestCase):
    """The field names are CloudCLI 1.39's, carried rather than invented."""

    def test_the_declared_properties_are_the_providers_schema(self):
        tool = fleet_tools.TOOLS_BY_NAME["fleet_create_session"]
        self.assertEqual(
            set(tool.input_schema["properties"]),
            {"device", "project", "message", "provider", "model",
             "lifecycle_mode", "permission_mode", "idempotency_key"},
        )
        self.assertEqual(tool.input_schema["required"], ["device", "project"])

    def test_a_create_carries_the_snake_case_names_onto_the_wire_as_camel(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.service.create_session(
                harness.principal(), device=TOKYO, project="p-1", message="hello",
                model="claude-opus-4", lifecycle_mode="resident",
                permission_mode="bypassPermissions",
            )
        sent = harness.providers[TOKYO].created[-1]
        self.assertEqual(sent["project"], "p-1")
        self.assertEqual(sent["message"], "hello")
        self.assertEqual(sent["model"], "claude-opus-4")
        self.assertEqual(sent["lifecycle_mode"], "resident")
        self.assertEqual(sent["permission_mode"], "bypassPermissions")
        self.assertEqual(sent["provider"], "claude")

    def test_the_provider_defaults_to_claude_and_an_explicit_one_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            ctx = harness.context()
            fleet_tools.call_tool(ctx, "fleet_create_session",
                                  {"device": TOKYO, "project": "p-1"})
            fleet_tools.call_tool(ctx, "fleet_create_session",
                                  {"device": TOKYO, "project": "p-1", "provider": "codex"})
        self.assertEqual(harness.providers[TOKYO].created[0]["provider"], "claude")
        self.assertEqual(harness.providers[TOKYO].created[1]["provider"], "codex")

    def test_an_undeclared_argument_is_refused_rather_than_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = fleet_tools.call_tool(
                harness.context(), "fleet_create_session",
                {"device": TOKYO, "project": "p-1", "cwd": "/tmp"},
            )
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")
        self.assertEqual(harness.providers[TOKYO].created, [])

    def test_an_empty_project_is_refused_before_the_provider_is_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(), device=TOKYO,
                                               project="   ")
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertEqual(harness.providers[TOKYO].created, [])

    def test_a_missing_device_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = fleet_tools.call_tool(harness.context(), "fleet_create_session",
                                           {"project": "p-1"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")


class TheScopeIsIndependent(unittest.TestCase):
    def test_a_send_credential_is_not_a_create_credential(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(
                    harness.principal(SCOPE_READ, SCOPE_SEND),
                    device=TOKYO, project="p-1",
                )
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")
        self.assertEqual(harness.providers[TOKYO].created, [])

    def test_a_read_only_credential_cannot_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(SCOPE_READ),
                                               device=TOKYO, project="p-1")
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")

    def test_the_scope_is_a_separate_name(self):
        self.assertEqual(SCOPE_CREATE, "fleet:create")
        self.assertNotIn(SCOPE_CREATE, (SCOPE_READ, SCOPE_SEND))


class EveryTargetDeviceCanCreateAndOnlyAuthorizedOnesCan(unittest.TestCase):
    def test_each_of_the_three_target_devices_creates(self):
        for device in TARGETS:
            with self.subTest(device=device):
                with tempfile.TemporaryDirectory() as tmp:
                    harness = Harness(Path(tmp))
                    result = harness.service.create_session(
                        harness.principal(), device=device, project="p-1",
                    )
                self.assertEqual(result["device_ref"], device)
                self.assertEqual(result["session_ref"],
                                 f"{device}|claude|created-1")
                self.assertEqual(result["idempotent_replay"], False)
                self.assertEqual(len(harness.providers[device].created), 1)

    def test_an_unauthorized_device_is_refused_before_the_provider_is_asked(self):
        """The refusal has to precede the act. A check that runs after the
        session exists is a report, not a gate."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(),
                                               device=PERSONAL_DELL, project="p-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertIn("allow_create", caught.exception.detail)
        self.assertEqual(harness.providers[PERSONAL_DELL].created, [])

    def test_the_default_is_refusal_for_every_device(self):
        """A binding that does not mention creation does not grant it."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), authorized=())
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(), device=TOKYO,
                                               project="p-1")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(harness.providers[TOKYO].created, [])

    def test_a_hostname_is_not_a_device_to_create_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(), device="tokyo",
                                               project="p-1")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")

    def test_an_enrolled_device_with_no_route_is_unknown_not_unreachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.service.providers.pop(TOKYO)
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(), device=TOKYO,
                                               project="p-1")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")


class TheAnswerNamesTheSessionItMade(unittest.TestCase):
    def test_the_result_carries_the_full_session_ref_not_a_bare_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.create_session(harness.principal(),
                                                    device=TOKYO, project="p-1")
        self.assertEqual(result["session_ref"], f"{TOKYO}|claude|created-1")
        self.assertEqual(result["native_session_id"], "created-1")
        self.assertEqual(result["device_ref"], TOKYO)
        self.assertEqual(result["run_id"], None)

    def test_a_create_with_no_message_starts_no_run(self):
        """`session_create` mints a row, and a run exists only when a first
        message was sent. Reporting a run that was never started would make
        \"created\" and \"started\" the same word."""
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.create_session(harness.principal(),
                                                    device=TOKYO, project="p-1")
        self.assertIsNone(result["run_id"])

    def test_a_create_with_a_message_returns_the_run_it_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.create_session(harness.principal(), device=TOKYO,
                                                    project="p-1", message="go")
        self.assertEqual(result["run_id"], "run-1")

    def test_creating_is_not_authority_and_the_result_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            result = harness.service.create_session(harness.principal(),
                                                    device=TOKYO, project="p-1")
        self.assertIn("grants no authority", result["note"])

    def test_an_oversized_first_message_is_refused_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp), max_message_chars=10)
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.create_session(harness.principal(), device=TOKYO,
                                               project="p-1", message="x" * 11)
        self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.assertEqual(harness.providers[TOKYO].created, [])


class TheRunsAreScoped(unittest.TestCase):
    """Creation is single-device, so nothing fans out and nothing is partial."""

    def test_a_create_audits_the_device_and_the_run_but_never_the_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.service.create_session(harness.principal(), device=TOKYO,
                                           project="p-1", message="deploy the thing")
            lines = (Path(tmp) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertIn("fleet.create_session", lines[-1])
        self.assertIn(TOKYO, lines[-1])
        self.assertNotIn("deploy the thing", lines[-1])


class Idempotency(unittest.TestCase):
    """A retry after an unknown outcome must not mint a second session."""

    def test_a_repeated_key_returns_the_recorded_session_and_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            first = harness.service.create_session(
                harness.principal(), device=TOKYO, project="p-1", message="go",
                idempotency_key="k-1",
            )
            second = harness.service.create_session(
                harness.principal(), device=TOKYO, project="p-1", message="go",
                idempotency_key="k-1",
            )
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(second["code"], "IDEMPOTENT_REPLAY")
        self.assertEqual(second["session_ref"], first["session_ref"])
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(len(harness.providers[TOKYO].created), 1)

    def test_the_key_is_scoped_to_the_credential_that_used_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.service.create_session(harness.principal(name="alice"), device=TOKYO,
                                           project="p-1", idempotency_key="same")
            other = harness.service.create_session(harness.principal(name="bob"),
                                                   device=TOKYO, project="p-1",
                                                   idempotency_key="same")
        self.assertFalse(other["idempotent_replay"])
        self.assertEqual(len(harness.providers[TOKYO].created), 2)

    def test_no_key_records_nothing_to_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            harness.service.create_session(harness.principal(), device=TOKYO, project="p-1")
            harness.service.create_session(harness.principal(), device=TOKYO, project="p-1")
        self.assertEqual(len(harness.providers[TOKYO].created), 2)


class TheNodeRouteIsDeclared(unittest.TestCase):
    """The Windows/Dell path is the closed route table, so the route is a
    deliberate entry — `POST /v1/sessions` — and not a framework default."""

    def test_the_route_is_in_the_closed_table(self):
        self.assertIn("POST /v1/sessions", np.ROUTES)

    def test_the_route_names_no_resource_placeholder(self):
        """Creation names a project in the signed body, never in the path."""
        _, _, path = "POST /v1/sessions".partition(" ")
        self.assertNotIn("{", path)


if __name__ == "__main__":
    unittest.main()
