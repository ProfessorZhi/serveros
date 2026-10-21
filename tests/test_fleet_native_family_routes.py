"""Explicit same-gateway families must not become account fallback or grants."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from fleet_mcp.config import FleetConfig, ProviderBinding, CreateGrant, load_config, provider_key
from fleet_mcp import cloudcli_native, tools
from fleet_mcp.runtime_create_policy import RuntimeProjectDefaults
from test_fleet_dispatch_claims import owner_store
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.fleet import FleetService
from fleet_mcp.providers.cloudcli import CloudCliProvider
from fleet_mcp.providers.fleet_node import FleetNodeProvider
from test_fleet_mcp_surface import Harness, FakeProvider, _summary

DEVICE = "device:tokyo-shared-dev-01"
OTHER = "device:work-fj"


def binding(root, device=DEVICE, **kwargs):
    return ProviderBinding(device_ref=device, kind="cloudcli-mcp-http",
                           endpoint="http://fixture.invalid/mcp",
                           credential_anchor=root / "unused-anchor", **kwargs)


class ExplicitDeclaration(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def config(self, providers, **kwargs):
        return FleetConfig(source=None, local_device_ref=DEVICE, providers=providers, **kwargs)

    def yaml(self, providers, **kwargs):
        raw = {"schema": "serveros/fleet-mcp.local/v1", "device_ref": DEVICE,
               "providers": {k: {"kind": "cloudcli-mcp-http",
                   "endpoint": "http://fixture.invalid/mcp",
                   "credential_anchor": str(self.root / "unused-anchor"), **v}
                   for k, v in providers.items()}, **kwargs}
        path = self.root / "config.yaml"
        path.write_text(json.dumps(raw))
        return load_config(path)

    def test_opt_in_selects_only_a_session_namespace_not_a_runtime(self):
        codex = SessionKey(DEVICE, "codex", "same-id")
        for enabled in (False, True):
            b = binding(self.root, multiplex_native_families=enabled)
            config = self.config({DEVICE: b})
            self.assertIsNone(config.provider_for(DEVICE, "codex"))
            self.assertEqual(config.session_binding(codex), b if enabled else None)
            self.assertIsNone(config.session_binding(SessionKey(OTHER, "codex", "same-id")))
            self.assertIsNone(config.session_binding(SessionKey(DEVICE, "codex-work", "same-id")))
        self.assertFalse(binding(self.root).multiplex_native_families)
        self.assertFalse(self.yaml({DEVICE: {}}).provider_for(DEVICE).multiplex_native_families)
        config = self.yaml({DEVICE: {"multiplex_native_families": True}})
        self.assertTrue(config.describe()["providers"][DEVICE]["multiplex_native_families"])

    def test_shape_is_strict_in_direct_and_yaml_construction(self):
        for value in (None, "true", "false", 0, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    binding(self.root, multiplex_native_families=value)
                with self.assertRaises(FleetRefusal) as caught:
                    self.yaml({DEVICE: {"multiplex_native_families": value}})
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
        for changes in ({"runtime": "claude-work"}, {"profile": "work"},
                        {"family": "codex"}, {"runtime": " claude "}, {"runtime": "all"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                binding(self.root, multiplex_native_families=True, **changes)
        self.assertFalse(binding(self.root, runtime="claude-work",
                                 multiplex_native_families=False).multiplex_native_families)

    def test_ambiguous_binding_and_declared_account_cannot_bypass_set_checks(self):
        base = binding(self.root, multiplex_native_families=True)
        for enabled in (False, True):
            exact = binding(self.root, runtime="codex", enabled=enabled)
            with self.subTest(enabled=enabled):
                with self.assertRaises(ValueError):
                    self.config({DEVICE: base, provider_key(DEVICE, "codex"): exact})
                with self.assertRaises(FleetRefusal):
                    self.yaml({DEVICE: {"multiplex_native_families": True},
                               provider_key(DEVICE, "codex"): {"enabled": enabled}})
        self.config({DEVICE: base, provider_key(OTHER, "codex"):
                     binding(self.root, OTHER, runtime="codex")})
        for key in (OTHER, provider_key(DEVICE, "claude-work")):
            with self.assertRaises(ValueError):
                self.config({key: base})
        for owner in (None, "someone-else"):
            with self.assertRaises(ValueError):
                self.config({DEVICE: replace(base, account="owner")}, device_default_account=owner)
        self.config({DEVICE: replace(base, account="owner")}, device_default_account="owner")

    def test_disabled_exact_declaration_does_not_redirect_to_base(self):
        exact = binding(self.root, runtime="codex", enabled=False)
        config = self.config({DEVICE: binding(self.root), provider_key(DEVICE, "codex"): exact})
        self.assertIs(config.session_binding(SessionKey(DEVICE, "codex", "same-id")), exact)
        config = self.config({DEVICE: binding(self.root, enabled=False,
                                               multiplex_native_families=True)})
        self.assertIsNone(config.session_binding(SessionKey(DEVICE, "codex", "same-id")))


class Gateway(FakeProvider):
    def __init__(self):
        super().__init__(DEVICE, sessions=[
            _summary(DEVICE, "claude-id"),
            replace(_summary(DEVICE, "codex-id"), key=SessionKey(DEVICE, "codex", "codex-id"))])
        self.calls = []
        self.created = []

    def get_session(self, key):
        self.calls.append(("get", key.ref))
        return {"session": {"id": key.native_session_id,
                            "provider": "codex" if key.provider == "codex" else "claude"}}

    def read_session(self, key, **kwargs):
        self.calls.append(("read", key.ref))
        return {"messages": []}

    def get_run(self, key, run_id, **kwargs):
        self.calls.append(("run", key.ref))
        return {"state": "completed"}

    def interrupt(self, key):
        self.calls.append(("interrupt", key.ref))
        return {"aborted": True}

    def native_call(self, tool, arguments, **kwargs):
        self.calls.append((tool, dict(arguments)))
        if tool == "session_get":
            return {"session": {"id": arguments["session"], "provider": "codex", "projectId": "p1"}}
        return {"accepted": True}

    def create(self, **kwargs):
        self.created.append(dict(kwargs))
        result = {"sessionId": "created-" + str(len(self.created))}
        if kwargs.get("message") is not None:
            result["queuedMessageUuid"] = "queued-first-message"
        return result


class SessionFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.h = Harness(self.root)
        self.gateway = Gateway()
        self.key = SessionKey(DEVICE, "codex", "codex-id")
        self.config = replace(self.h.config, providers={**self.h.config.providers,
            DEVICE: replace(self.h.config.provider_for(DEVICE), multiplex_native_families=True,
                            allow_send=True, allow_control=True, allow_create=True)})
        self.configure()
        self.principal = self.h.principal("fleet:read", "fleet:send", "fleet:control",
                                          "fleet:create", "fleet:approve", "fleet:navigate")

    def configure(self, **policy):
        if policy:
            self.config = replace(self.config, policy=replace(self.config.policy, **policy))
        self.service = FleetService(self.config, providers={DEVICE: self.gateway, OTHER: self.h.windows})
        self.addCleanup(self.service.discovery.close)


class SessionRouting(SessionFixture):
    def test_default_refuses_but_opt_in_reads_the_listed_native_address(self):
        original = FleetService(self.h.config, providers={DEVICE: self.gateway})
        self.addCleanup(original.discovery.close)
        with self.assertRaises(FleetRefusal) as caught:
            original.get_session(self.principal, self.key)
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")
        listed = self.service.list_sessions(self.principal, device=DEVICE)
        key = SessionKey.parse(next(s["session_ref"] for s in listed["sessions"] if s["provider"] == "codex"))
        self.assertEqual(self.service.get_session(self.principal, key)["session_ref"], key.ref)
        self.assertEqual(self.service.read_session(self.principal, key)["session_ref"], key.ref)
        self.assertEqual(self.service.get_run(self.principal, key, "original-run")["session_ref"], key.ref)
        self.assertEqual([x[0] for x in self.gateway.calls], ["get", "read", "run"])

    def test_base_grant_does_not_authorize_codex_but_typed_grants_do(self):
        self.configure(operator_grants={"tester": {DEVICE: ("send", "control")}})
        self.assertFalse(self.service._session_control_state(self.principal, self.key)["send_allowed"])
        for call in (lambda: self.service.send_to_session(self.principal, self.key, message="task"),
                     lambda: self.service.interrupt_session(self.principal, self.key)):
            with self.assertRaises(FleetRefusal):
                call()
        self.assertEqual(self.gateway.sent + self.gateway.calls, [])
        self.configure(send_allow=(self.key.ref,), control_allow=(self.key.ref,))
        self.assertTrue(self.service._session_control_state(self.principal, self.key)["send_allowed"])
        self.service.send_to_session(self.principal, self.key, message="complete task")
        self.service.interrupt_session(self.principal, self.key)
        self.assertEqual(self.gateway.sent[0][0], self.key.ref)
        self.assertEqual(self.gateway.calls, [("interrupt", self.key.ref)])
        self.configure(send_allow=(), operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send",)}})
        self.service.send_to_session(self.principal, self.key, message="another complete task")
        read_only = self.h.principal("fleet:read")
        with self.assertRaises(FleetRefusal) as caught:
            self.service.send_to_session(read_only, self.key, message="forbidden")
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")
        self.assertEqual(len(self.gateway.sent), 2)

    def test_unknown_profiles_and_disabled_routes_never_fall_back(self):
        for key in (SessionKey(DEVICE, "codex-work", "codex-id"),
                    SessionKey(OTHER, "codex", "codex-id")):
            with self.subTest(key=key), self.assertRaises(FleetRefusal):
                self.service.get_session(self.principal, key)
        self.config = replace(self.config, providers={DEVICE:
            replace(self.config.provider_for(DEVICE), enabled=False)})
        self.configure()
        with self.assertRaises(FleetRefusal):
            self.service.get_session(self.principal, self.key)
        self.assertEqual(self.gateway.calls, [])


class ListingProvenance(SessionFixture):
    def test_binding_metadata_is_separate_from_address_namespace(self):
        out = self.service.list_sessions(self.principal, device=DEVICE)
        codex = next(s for s in out["sessions"] if s["provider"] == "codex")
        self.assertEqual(codex["runtime"], "codex")
        self.assertEqual(codex["binding_runtime"], "claude")
        self.assertEqual(out["sessions_per_runtime"][provider_key(DEVICE, "codex")], 1)
        self.assertEqual(out["sessions_per_binding"][DEVICE], 2)

    def test_real_cloudcli_adapter_preserves_profiles_and_checks_native_family(self):
        for namespace, family in (("claude-work", "claude"), ("codex-work", "codex")):
            with self.subTest(namespace=namespace):
                adapter = CloudCliProvider(binding(self.root, runtime=namespace, family=family))
                adapter._call = lambda *a, **k: {"sessions": [{"id": "profile-id", "provider": family}]}
                row = adapter.list_sessions(limit=1)[0]
                self.assertEqual(row.key, SessionKey(DEVICE, namespace, "profile-id"))
                adapter._call = lambda *a, **k: {"sessions": [{"id": "profile-id", "provider": "contradiction"}]}
                with self.assertRaises(FleetRefusal):
                    adapter.list_sessions(limit=1)

    def test_wire_and_injected_rows_cannot_impersonate_another_binding(self):
        self.h.windows._sessions = [_summary(OTHER, "other-id")]
        for wrong in (SessionKey(OTHER, "claude", "foreign"),
                      SessionKey(DEVICE, "claude-work", "foreign")):
            with self.subTest(wrong=wrong):
                forged = replace(_summary(DEVICE, "foreign"), key=wrong)
                self.gateway._sessions = [_summary(DEVICE, "valid"), forged]
                out = self.service.list_sessions(self.principal)
                self.assertEqual([s["device_ref"] for s in out["sessions"]], [OTHER])
                gap = next(u for u in out["devices_unreachable"] if u["device_ref"] == DEVICE)
                self.assertEqual(gap["code"], "BAD_SESSION_REF")
                node = FleetNodeProvider(self.config.provider_for(DEVICE))
                node._request = lambda *a, **k: {"sessions": [forged.as_dict()]}
                with self.assertRaises(FleetRefusal):
                    node.list_sessions(limit=1)

    def test_legacy_unrouted_observation_cannot_borrow_an_explicit_codex_route(self):
        self.config = replace(self.config, providers={DEVICE:
            replace(self.config.provider_for(DEVICE), multiplex_native_families=False)})
        self.configure()
        out = self.service.list_sessions(self.principal, device=DEVICE)
        self.assertEqual(len(out["sessions"]), 2)
        self.assertFalse(next(s for s in out["sessions"] if s["provider"] == "codex")["control_allowed"]["send_allowed"])
        exact = binding(self.root, runtime="codex")
        self.config = replace(self.config, providers={**self.config.providers,
            provider_key(DEVICE, "codex"): exact})
        self.configure()
        out = self.service.list_sessions(self.principal, device=DEVICE)
        self.assertEqual(out["sessions"], [])
        self.assertEqual(out["devices_unreachable"][0]["code"], "BAD_SESSION_REF")


class NativeRouting(SessionFixture):
    def call(self, tool, arguments, **kwargs):
        return self.service.native_call(self.principal, device_ref=DEVICE,
            native_tool=tool, arguments=arguments, session_ref=self.key.ref, **kwargs)

    def test_existing_public_session_mirror_uses_alias_without_inheriting_grants(self):
        native = cloudcli_native.NATIVE_BY_NAME["session_send"]
        args = {"device_ref": DEVICE, "session_ref": self.key.ref,
                "arguments": {"session": self.key.native_session_id, "message": "complete task"}}
        self.configure(operator_grants={"tester": {DEVICE: ("send",)}})
        result = tools.call_tool(tools.ToolContext(self.service, self.principal), native.namespaced, args)
        self.assertTrue(result["isError"])
        self.assertEqual(self.gateway.calls, [])
        self.configure(operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send",)}})
        result = tools.call_tool(tools.ToolContext(self.service, self.principal), native.namespaced, args)
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.gateway.calls, [("session_send", args["arguments"])])

    def test_other_runtime_selectors_cannot_acquire_session_aliases(self):
        for tool, args in (("session_create", {"project": "p1"}), ("projects_list", {}),
                           ("session_get", {"session": "codex-id"})):
            with self.subTest(tool=tool), self.assertRaises(FleetRefusal) as caught:
                self.call(tool, args)
            self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")
        with self.assertRaises(FleetRefusal):
            self.service.native_capability(self.principal, device_ref=DEVICE, runtime="codex")
        self.assertEqual(self.gateway.calls, [])

    def test_conflicting_coordinates_are_refused_before_any_provider_io(self):
        self.configure(operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send",)}})
        for kwargs, args in (({"runtime": "claude"}, {"session": "codex-id", "message": "x"}),
                             ({}, {"session": "another-id", "message": "x"})):
            with self.subTest(kwargs=kwargs), self.assertRaises(FleetRefusal):
                self.call("session_send", args, **kwargs)
        with self.assertRaises(FleetRefusal):
            self.service.native_call(self.principal, device_ref=DEVICE, native_tool="session_get",
                arguments={"session": "other-id"}, session_ref=DEVICE + "|claude|claude-id")
        self.assertEqual(self.gateway.calls, [])

    def test_reconfigure_uses_actual_account_family_model_and_mode_ceiling(self):
        rule = RuntimeProjectDefaults("codex", "codex", None, None, ("approved",), ("default",))
        grants = {"tester": {provider_key(DEVICE, "codex"): ("send", "control")}}
        self.configure(operator_grants=grants,
            create_allow={"p1": CreateGrant(models=("approved", "forbidden"),
                                             permission_modes=("default", "bypassPermissions"))},
            runtime_create_defaults={DEVICE: {"p1": rule}})
        for extra in ({"model": "forbidden"}, {"permissionMode": "bypassPermissions"}):
            with self.subTest(extra=extra), self.assertRaises(FleetRefusal) as caught:
                self.call("session_reconfigure", {"session": "codex-id", **extra}, project="p1")
            self.assertEqual(caught.exception.code, "WRITE_NOT_GRANTED")
        self.configure(runtime_create_defaults={DEVICE: {"p1": replace(rule, family="claude")}})
        with self.assertRaises(FleetRefusal):
            self.call("session_reconfigure", {"session": "codex-id", "model": "approved"}, project="p1")
        self.assertFalse(any(c[0] == "session_reconfigure" for c in self.gateway.calls))
        self.configure(runtime_create_defaults={DEVICE: {"p1": rule}})
        self.call("session_reconfigure", {"session": "codex-id", "model": "approved"}, project="p1")
        self.assertEqual(self.gateway.calls[-1], ("session_reconfigure", {"session": "codex-id", "model": "approved"}))

    def test_runtime_ceiling_rejects_contradictory_target_metadata(self):
        self.configure(operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send", "control")}},
                       create_allow={"p1": CreateGrant(models=("approved",))})
        for changes in ({"id": "another-session"}, {"provider": "claude"}, {"device_ref": OTHER}):
            with self.subTest(changes=changes):
                calls = []
                def metadata(tool, args, **kwargs):
                    calls.append(tool)
                    return {"session": {"id": "codex-id", "provider": "codex",
                                        "projectId": "p1", **changes}}
                self.gateway.native_call = metadata
                with self.assertRaises(FleetRefusal):
                    self.call("session_reconfigure", {"session": "codex-id", "model": "approved"}, project="p1")
                self.assertEqual(calls, ["session_get"])


class CreateReceipts(SessionFixture):
    def rebuild_owner(self):
        self.service = FleetService(self.config, providers={DEVICE: self.gateway},
                                    attempts=owner_store(self.root / "attempts.json"))
        self.addCleanup(self.service.discovery.close)

    def create(self, **kwargs):
        return self.service.create_session(self.principal, device=DEVICE, project="p1",
            provider=kwargs.pop("provider", "codex"), idempotency_key="create-key", **kwargs)

    def prepare(self):
        self.configure(create_allow={"p1": CreateGrant()},
            operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send",)}})
        self.rebuild_owner()

    def test_codex_create_and_send_replay_retain_references_after_owner_reconstruction(self):
        self.prepare()
        first = self.create(message="PRIVATE_FIRST_TASK")
        key = SessionKey.parse(first["session_ref"])
        self.assertEqual(key.provider, "codex")
        self.assertIsNone(first["run_id"])
        self.assertEqual(first["queued_message_uuid"], "queued-first-message")
        self.rebuild_owner()
        replay = self.create(message="PRIVATE_FIRST_TASK")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["queued_message_uuid"], first["queued_message_uuid"])
        self.assertEqual(replay["session_ref"], first["session_ref"])
        self.assertEqual(len(self.gateway.created), 1)
        sent = self.service.send_to_session(self.principal, key, message="task", idempotency_key="send-key")
        self.rebuild_owner()
        sent_again = self.service.send_to_session(self.principal, key, message="task", idempotency_key="send-key")
        self.assertEqual(sent_again["run_id"], sent["run_id"])
        self.assertEqual(len(self.gateway.sent), 1)
        self.assertNotIn("PRIVATE_FIRST_TASK", (self.root / "attempts.json").read_text())
        with self.assertRaises(FleetRefusal) as caught:
            self.create(provider="claude", message="PRIVATE_FIRST_TASK")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_configuration_changes_cannot_relabel_or_repeat_an_accepted_create(self):
        for originally_enabled in (False, True):
            with self.subTest(originally_enabled=originally_enabled):
                # Separate keys isolate the two cases; do not delete owner state.
                self.configure(create_allow={"p1": CreateGrant()})
                self.config = replace(self.config, providers={DEVICE: replace(
                    self.config.provider_for(DEVICE), multiplex_native_families=originally_enabled)})
                self.rebuild_owner()
                args = dict(device=DEVICE, project="p1", provider="codex",
                            idempotency_key="toggle-" + str(originally_enabled))
                first = self.service.create_session(self.principal, **args)
                count = len(self.gateway.created)
                self.config = replace(self.config, providers={DEVICE: replace(
                    self.config.provider_for(DEVICE), multiplex_native_families=not originally_enabled)})
                self.rebuild_owner()
                with self.assertRaises(FleetRefusal) as caught:
                    self.service.create_session(self.principal, **args)
                self.assertEqual(caught.exception.code, "DISPATCH_STATE_UNAVAILABLE")
                self.assertEqual(len(self.gateway.created), count)
                if originally_enabled:
                    with self.assertRaises(FleetRefusal):
                        self.service.get_session(self.principal, SessionKey.parse(first["session_ref"]))

    def test_no_message_and_unknown_or_unsupported_creation_keep_their_meaning(self):
        self.prepare()
        first = self.create()
        self.assertEqual(SessionKey.parse(first["session_ref"]).provider, "codex")
        self.assertIsNone(first["run_id"])
        self.rebuild_owner()
        self.assertTrue(self.create()["idempotent_replay"])
        self.assertEqual(len(self.gateway.created), 1)
        for family in (None, "unknown-family"):
            with self.subTest(family=family), self.assertRaises(FleetRefusal) as caught:
                self.service.create_session(self.principal, device=DEVICE, project="p1", provider=family)
            self.assertEqual(caught.exception.code, "BAD_REQUEST")
        calls = []
        def lost(**kwargs):
            calls.append(True)
            raise FleetRefusal("PROVIDER_TIMEOUT", "lost after provider IO")
        self.gateway.create = lost
        for _ in range(2):
            with self.assertRaises(FleetRefusal):
                self.service.create_session(self.principal, device=DEVICE, project="p1", provider="codex",
                    message="complete task", idempotency_key="lost-key")
        self.assertEqual(len(calls), 1)
