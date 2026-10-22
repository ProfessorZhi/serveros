"""Use real adapters: native IDs, rather than caller-spelled families, reach IO."""
from dataclasses import replace
import unittest

from fleet_mcp.config import provider_key
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import FleetService
from fleet_mcp.identity import SessionKey
from fleet_mcp.providers.cloudcli import CloudCliProvider
from fleet_mcp.providers.fleet_node import FleetNodeProvider
from fleet_mcp.session_detail import normalize_detail
from test_fleet_dispatch_claims import owner_store
from test_fleet_native_family_routes import SessionFixture, DEVICE


class ActualNativeFamily(SessionFixture):
    def install(self, actual, namespace=None, *, shape="nested", owner=False):
        namespace = namespace or actual
        self.actual_family = actual
        self.key = SessionKey(DEVICE, namespace, "belongs-to-" + actual)
        self.configure(operator_grants={"tester": {
            provider_key(DEVICE, namespace): ("send", "control", "navigate")}})
        self.forwarded = []
        self.adapter = CloudCliProvider(self.config.provider_for(DEVICE))
        self.shape = shape
        self.queue_only = False

        def call(tool, args, **kwargs):
            self.forwarded.append((tool, dict(args)))
            if tool == "session_get":
                native = {"id": "belongs-to-" + actual, "provider": actual, "projectId": "p1"}
                if "missing_family" in self.shape:
                    native.pop("provider")
                if "missing_id" in self.shape:
                    native.pop("id")
                return native if self.shape.startswith("flat") else {"session": native}
            if tool == "session_send":
                if self.queue_only:
                    return {"queuedMessageUuid": "verified-queue", "accepted": True}
                return {"runId": "real-native-run", "accepted": True}
            if tool == "session_interrupt":
                return {"aborted": True}
            if tool == "run_get":
                return {"state": "completed"}
            return {"ok": True}

        self.adapter._call = call
        self.owner_path = self.root / ("owner-" + namespace + "-" + actual + ".json")
        self.service = FleetService(self.config, providers={DEVICE: self.adapter},
            attempts=owner_store(self.owner_path) if owner else None)
        self.addCleanup(self.service.discovery.close)

    def operations(self):
        args = {"session": self.key.native_session_id}
        return {
            "get": lambda: self.service.get_session(self.principal, self.key),
            "read": lambda: self.service.read_session(self.principal, self.key),
            "run": lambda: self.service.get_run(self.principal, self.key, "original-run"),
            "send": lambda: self.service.send_to_session(self.principal, self.key,
                message="complete task", idempotency_key="one-attempt"),
            "interrupt": lambda: self.service.interrupt_session(self.principal, self.key),
            "native_send": lambda: self.service.native_call(self.principal, device_ref=DEVICE,
                native_tool="session_send", arguments={**args, "message": "task"}, session_ref=self.key.ref),
            "native_reconfigure": lambda: self.service.native_call(self.principal, device_ref=DEVICE,
                native_tool="session_reconfigure", arguments=args, session_ref=self.key.ref),
            "native_navigate": lambda: self.service.native_call(self.principal, device_ref=DEVICE,
                native_tool="ui_open_session", arguments=args, session_ref=self.key.ref),
        }

    def test_wrong_family_is_refused_in_both_directions_without_effect_or_accepted_receipt(self):
        for actual, namespace in (("claude", "codex"), ("codex", "claude")):
            with self.subTest(actual=actual, namespace=namespace):
                self.install(actual, namespace, owner=True)
                for operation, invoke in self.operations().items():
                    with self.subTest(operation=operation), self.assertRaises(FleetRefusal):
                        invoke()
                self.assertEqual(set(name for name, _ in self.forwarded), {"session_get"})
                self.assertFalse(any(r["phase"] == "ACCEPTED"
                    for r in owner_store(self.owner_path).dispatch_attempts().values()))

    def test_missing_native_evidence_cannot_be_replaced_by_a_synthesized_outer_key(self):
        for shape in ("nested_missing_family", "flat_missing_family", "nested_missing_id"):
            self.install("codex", shape=shape)
            for operation, invoke in self.operations().items():
                with self.subTest(shape=shape, operation=operation), self.assertRaises(FleetRefusal):
                    invoke()
            self.assertEqual(set(name for name, _ in self.forwarded), {"session_get"})

    def test_verified_delivery_replays_offline_in_memory_and_after_owner_reconstruction(self):
        for family, durable in (("claude", False), ("codex", True)):
            with self.subTest(family=family, durable=durable):
                self.install(family, shape="flat", owner=durable)
                self.queue_only = durable
                args = dict(message="complete task", idempotency_key="verified")
                first = self.service.send_to_session(self.principal, self.key, **args)
                self.assertEqual([name for name, _ in self.forwarded], ["session_get", "session_send"])
                def offline(*args, **kwargs):
                    raise AssertionError("ACCEPTED replay must not contact a provider")
                self.adapter._call = offline
                if durable:
                    self.service = FleetService(self.config, providers={DEVICE: self.adapter},
                        attempts=owner_store(self.owner_path))
                    self.addCleanup(self.service.discovery.close)
                replay = self.service.send_to_session(self.principal, self.key, **args)
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(replay["run_id"], first["run_id"])
                self.assertEqual(replay["session_ref"], self.key.ref)
                if durable:
                    self.assertEqual(replay["queued_message_uuid"], first["queued_message_uuid"])
                self.service.providers.clear()
                self.assertTrue(self.service.send_to_session(self.principal, self.key, **args)["idempotent_replay"])
                disabled = replace(self.config.provider_for(DEVICE), enabled=False)
                self.service.config = replace(self.config, providers={DEVICE: disabled})
                with self.assertRaises(FleetRefusal):
                    self.service.send_to_session(self.principal, self.key, **args)

    def test_early_scope_grant_and_argument_denials_do_not_probe_metadata(self):
        self.install("codex")
        with self.assertRaises(FleetRefusal):
            self.service.send_to_session(self.h.principal("fleet:read"), self.key, message="x")
        self.config = replace(self.config, policy=replace(self.config.policy, operator_grants={}))
        self.service.config = self.config
        with self.assertRaises(FleetRefusal):
            self.service.send_to_session(self.principal, self.key, message="x")
        self.service.config = replace(self.config, policy=replace(self.config.policy,
            operator_grants={"tester": {provider_key(DEVICE, "codex"): ("send",)}}))
        with self.assertRaises(FleetRefusal):
            self.service.native_call(self.principal, device_ref=DEVICE, native_tool="session_send",
                arguments={"session": "another-id", "message": "x"}, session_ref=self.key.ref)
        self.assertEqual(self.forwarded, [])

    def test_provider_map_cannot_supply_an_undeclared_session_route(self):
        self.install("codex")
        self.service.config = replace(self.config, providers={})
        self.service.providers[provider_key(DEVICE, "codex")] = self.adapter
        with self.assertRaises(FleetRefusal) as caught:
            self.service.read_session(self.principal, self.key)
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")
        self.assertEqual(self.forwarded, [])

    def test_node_flat_missing_family_cannot_turn_into_positive_evidence(self):
        self.install("codex")
        node = FleetNodeProvider(self.config.provider_for(DEVICE))
        calls = []
        def request(method, path, payload=None, **kwargs):
            calls.append((method, path))
            return {"id": self.key.native_session_id, "title": "family absent"}
        node._request = request
        self.service.providers[DEVICE] = node
        with self.assertRaises(FleetRefusal):
            self.service.get_session(self.principal, self.key)
        self.assertEqual(len(calls), 1)

    def test_run_payload_cannot_replace_the_verified_address_or_requested_run(self):
        self.install("codex")
        original = self.adapter._call
        for wrong in ({"session_ref": DEVICE + "|claude|belongs-to-codex"},
                      {"native_session_id": "another-session"}, {"run_id": "another-run"},
                      {"provider": "claude", "run_id": "original-run"}):
            with self.subTest(wrong=wrong):
                self.adapter._call = lambda tool, args, **kwargs: (
                    {"state": "completed", **wrong} if tool == "run_get"
                    else original(tool, args, **kwargs))
                with self.assertRaises(FleetRefusal):
                    self.service.get_run(self.principal, self.key, "original-run")

    def test_raw_run_family_can_match_a_declared_profile_without_replacing_its_namespace(self):
        for family in ("claude", "codex"):
            with self.subTest(family=family):
                self.install(family)
                profile = replace(self.config.provider_for(DEVICE), runtime=family + "-work",
                                  family=family, multiplex_native_families=False)
                key = SessionKey(DEVICE, profile.runtime, self.key.native_session_id)
                self.adapter.binding = profile
                self.service.config = replace(self.config, providers={provider_key(DEVICE, profile.runtime): profile})
                self.service.providers = {provider_key(DEVICE, profile.runtime): self.adapter}
                self.adapter._call = lambda *a, **k: {"provider": family, "run_id": "original-run", "state": "completed"}
                result = self.service.get_run(self.principal, key, "original-run")
                self.assertEqual(result["provider"], profile.runtime)
                self.assertEqual(result["session_ref"], key.ref)

    def test_routing_only_node_wrappers_never_become_native_family_proof(self):
        for family in ("claude", "codex"):
            with self.subTest(family=family):
                self.install(family, owner=True)
                node = FleetNodeProvider(self.config.provider_for(DEVICE))
                wrapper = {**self.key.as_dict(), "title": "routing metadata only"}
                # Simulate multiple normalizers, as deployed connector wrappers
                # can be wrapped by both the adapter and the final service.
                wrapper = normalize_detail(self.key, wrapper, node.binding)
                wrapper = normalize_detail(self.key, wrapper, node.binding)
                calls = []
                def request(method, path, payload=None, **kwargs):
                    calls.append(method)
                    return wrapper if method == "GET" else {"runId": "forbidden-effect", "accepted": True}
                node._request = request
                self.service.providers[DEVICE] = node
                with self.assertRaises(FleetRefusal):
                    self.service.send_to_session(self.principal, self.key,
                        message="complete task", idempotency_key="routing-only")
                self.assertEqual(calls, ["GET"])
                self.assertFalse(any(r["phase"] == "ACCEPTED"
                    for r in owner_store(self.owner_path).dispatch_attempts().values()))

    def test_failed_family_proof_remains_unknown_after_owner_reconstruction(self):
        self.install("claude", "codex", owner=True)
        args = dict(message="complete task", idempotency_key="failed-proof")
        with self.assertRaises(FleetRefusal):
            self.service.send_to_session(self.principal, self.key, **args)
        def no_further_probe(*args, **kwargs):
            raise AssertionError("UNKNOWN retry cannot probe or send again")
        self.adapter._call = no_further_probe
        self.service = FleetService(self.config, providers={DEVICE: self.adapter},
                                    attempts=owner_store(self.owner_path))
        self.addCleanup(self.service.discovery.close)
        with self.assertRaises(FleetRefusal) as caught:
            self.service.send_to_session(self.principal, self.key, **args)
        self.assertEqual(caught.exception.code, "DISPATCH_OUTCOME_UNKNOWN")
        self.assertEqual([name for name, _ in self.forwarded], ["session_get"])

