#!/usr/bin/env python3
"""Identity, classification, disclosure and binding rules for the Fleet MCP.

The tests that matter here are the ones about a sentence somebody would
otherwise write. Each class below is one of those sentences, kept false.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import config as fleet_config  # noqa: E402
from fleet_mcp import privacy  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.identity import RunKey, SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    ARCHIVED,
    MANAGED_IDLE,
    RESIDENT_HOST,
    RUNNING_AGENT,
    classify,
    summary_from_dict,
)
from fleet_mcp.registry import (  # noqa: E402
    authored_facts,
    load_registry,
    measured_availability,
    resolve_or_refuse,
)


class SessionIdentity(unittest.TestCase):
    """A session is a triple, and the triple is the point."""

    def test_round_trips_through_its_textual_form(self):
        key = SessionKey(device_ref="device:work-fj", provider="claude",
                         native_session_id="b2cb3f10-1111-2222-3333-444455556666")
        self.assertEqual(key.ref, "device:work-fj|claude|b2cb3f10-1111-2222-3333-444455556666")
        self.assertEqual(SessionKey.parse(key.ref), key)
        self.assertEqual(key.as_dict()["session_ref"], key.ref)

    def test_the_same_native_id_on_two_devices_is_two_sessions(self):
        """The failure this type exists to prevent, stated as a test.

        Two CloudCLI installations that happen to hand out the same id must not
        produce the same key. If this ever passes while the two refs are equal,
        one machine's conversation is addressable as another's.
        """
        native = "same-id-on-both"
        on_windows = SessionKey(device_ref="device:work-fj", provider="claude",
                                native_session_id=native)
        on_tokyo = SessionKey(device_ref="device:tokyo-shared-dev-01", provider="claude",
                              native_session_id=native)
        self.assertNotEqual(on_windows, on_tokyo)
        self.assertNotEqual(on_windows.ref, on_tokyo.ref)
        self.assertEqual(SessionKey.parse(on_windows.ref), on_windows)
        self.assertEqual(SessionKey.parse(on_tokyo.ref), on_tokyo)

    def test_a_bare_session_id_is_not_an_address(self):
        with self.assertRaises(FleetRefusal) as caught:
            SessionKey.parse("b2cb3f10-1111-2222-3333-444455556666")
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")
        self.assertIn("same id can exist on two devices", caught.exception.detail)

    def test_a_transport_name_is_not_an_identity(self):
        with self.assertRaises(FleetRefusal) as caught:
            SessionKey.parse("work-fj|claude|abc")
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")
        self.assertIn("not a typed device ref", caught.exception.detail)

    def test_compose_accepts_a_bare_device_id_once_and_only_here(self):
        key = SessionKey.compose("work-fj", "claude", "abc")
        self.assertEqual(key.device_ref, "device:work-fj")
        self.assertEqual(key.device_id, "work-fj")

    def test_a_native_id_containing_the_separator_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            SessionKey.parse("device:work-fj|claude|a|b")
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")

    def test_runs_are_identified_within_their_session(self):
        key = SessionKey.compose("work-fj", "claude", "abc")
        run = RunKey(session=key, run_id="run-1")
        self.assertEqual(run.ref, "device:work-fj|claude|abc|run:run-1")
        self.assertEqual(run.as_dict()["session_ref"], key.ref)


class Classification(unittest.TestCase):
    """`running` and `hostState` are two answers, and a JSONL on disk is neither."""

    def test_running_is_a_running_agent(self):
        verdict, running, resident, _ = classify({"running": True, "hostState": "busy"})
        self.assertEqual(verdict, RUNNING_AGENT)
        self.assertTrue(running)

    def test_a_resident_host_that_is_idle_is_not_a_running_agent(self):
        verdict, running, resident, evidence = classify({"running": False,
                                                        "hostState": "lingering"})
        self.assertEqual(verdict, RESIDENT_HOST)
        self.assertFalse(running)
        self.assertTrue(resident)
        self.assertEqual(evidence["provider.hostState"], "lingering")

    def test_a_transcript_on_disk_is_not_a_running_session(self):
        """The stated requirement, as a test: a session row with no run is idle.

        A provider row that merely describes history — a title, a message count,
        a timestamp — must come back MANAGED_IDLE. If this ever returns
        RUNNING_AGENT, the surface is reporting stored text as an active agent.
        """
        verdict, running, resident, _ = classify(
            {"id": "abc", "title": "some conversation", "messageCount": 412,
             "lastActivity": "2026-10-09T22:00:00Z"}
        )
        self.assertEqual(verdict, MANAGED_IDLE)
        self.assertFalse(running)
        self.assertFalse(resident)

    def test_an_archived_session_says_so(self):
        verdict, _, _, _ = classify({"running": False, "isArchived": True})
        self.assertEqual(verdict, ARCHIVED)

    def test_a_running_archived_session_is_still_running(self):
        """A run in flight is the strongest evidence there is; archive is a label."""
        verdict, _, _, _ = classify({"running": True, "isArchived": True})
        self.assertEqual(verdict, RUNNING_AGENT)

    def test_snake_case_provider_fields_are_read_too(self):
        verdict, _, resident, _ = classify({"host_state": "busy"})
        self.assertEqual(verdict, RESIDENT_HOST)
        self.assertTrue(resident)

    def test_the_derivation_carries_its_inputs(self):
        _, _, _, evidence = classify({"running": False, "hostState": None})
        self.assertIn("provider.running", evidence)
        self.assertIn("provider.hostState", evidence)
        self.assertIn("rule", evidence)

    def test_a_summary_rehydrates_without_re_reading(self):
        """The connector path must not classify a second time from less data."""
        key = SessionKey.compose("work-fj", "claude", "abc")
        wire = {
            "session_ref": key.ref, "title": "t", "classification": RUNNING_AGENT,
            "running": True, "resident_host": False, "host_state": "busy",
            "lifecycle_mode": "resident", "project_id": "p",
            "last_activity": {"iso": "2026-10-10T00:00:00Z"},
            "evidence": {"provider.running": True},
        }
        summary = summary_from_dict(wire)
        self.assertEqual(summary.key, key)
        self.assertEqual(summary.classification, RUNNING_AGENT)
        self.assertEqual(summary.evidence["provider.running"], True)


class Disclosure(unittest.TestCase):
    """`fleet_read_session` returns the most sensitive thing this surface touches."""

    def test_obvious_credentials_are_masked(self):
        # Assembled rather than written out. A credential-shaped literal in a
        # test file is indistinguishable from a leaked credential to
        # `scripts/scan_secrets.py`, which is the point of that scanner -- and
        # the established way to keep both is to build the sample at runtime,
        # as `tests/test_secret_scan.py` does for its own fixtures.
        sk_key = "sk-ant-api03-" + "A" * 24
        gh_token = "ghp_" + "A" * 36
        cc_pat = "ccp_" + "abcdefghijklmnop"
        text = (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345\n"
            f"key {sk_key}\n"
            f"pat {cc_pat}\n"
            f"gh {gh_token}\n"
            "MY_API_KEY=supersecretvalue\n"
        )
        cleaned, hits = privacy.redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz012345", cleaned)
        self.assertNotIn(sk_key, cleaned)
        self.assertNotIn(cc_pat, cleaned)
        self.assertNotIn(gh_token, cleaned)
        self.assertNotIn("supersecretvalue", cleaned)
        self.assertGreaterEqual(hits, 5)
        self.assertIn(privacy.REDACTION_MARK, cleaned)

    def test_a_jwt_is_masked(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"
        cleaned, hits = privacy.redact_text(f"token={token}")
        self.assertNotIn(token, cleaned)
        # Two patterns can legitimately match one secret — the `..._TOKEN=`
        # assignment and the JWT shape. The count is a lower bound, not an
        # identity: asserting an exact one would make an extra guard a failure.
        self.assertGreaterEqual(hits, 1)

    def test_ordinary_prose_survives_the_pass(self):
        text = "The build failed at step 3. Retry after fixing the import in main.py."
        cleaned, hits = privacy.redact_text(text)
        self.assertEqual(cleaned, text)
        self.assertEqual(hits, 0)

    def test_the_pass_is_declared_partial_not_complete(self):
        """The honest description is part of the contract, not a nicety."""
        self.assertIn("best-effort", privacy.__doc__ or "")
        self.assertIn("UNTRUSTED", privacy.UNTRUSTED_NOTICE)
        self.assertIn("must not be executed", privacy.UNTRUSTED_NOTICE)

    def test_a_read_is_bounded_and_says_so(self):
        self.assertEqual(privacy.clamp_limit(None, 40, default=20), (20, False))
        self.assertEqual(privacy.clamp_limit(5, 40, default=20), (5, False))
        self.assertEqual(privacy.clamp_limit(9999, 40, default=20), (40, True))
        self.assertEqual(privacy.clamp_limit(0, 40, default=20), (1, True))
        self.assertEqual(privacy.clamp_limit("nonsense", 40, default=20), (20, False))

    def test_redaction_walks_nested_payloads(self):
        payload = {"messages": [{"text": "Bearer abcdefghijklmnopqrstuvwxyz012345"}],
                   "meta": {"note": "ccp_abcdefghijklmnop"}}
        cleaned, hits = privacy.redact_payload(payload)
        self.assertEqual(hits, 2)
        self.assertNotIn("ccp_abcdefghijklmnop", cleaned["meta"]["note"])


class Binding(unittest.TestCase):
    """The local binding says how to reach a device. It never says which device exists."""

    def _write(self, tmp: Path, body: dict) -> Path:
        path = tmp / "fleet-mcp.local.yaml"
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        return path

    def _minimal(self, **overrides) -> dict:
        body = {
            "schema": fleet_config.SCHEMA,
            "device_ref": "device:work-fj",
            "providers": {
                "device:tokyo-shared-dev-01": {
                    "kind": "cloudcli-mcp-http",
                    "endpoint": "http://127.0.0.1:3011/mcp",
                    "credential_anchor": "~/.secrets/tokyo-pat",
                }
            },
        }
        body.update(overrides)
        return body

    def test_a_valid_binding_loads(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._minimal())
            config = fleet_config.load_config(path)
        self.assertEqual(config.local_device_ref, "device:work-fj")
        self.assertIn("device:tokyo-shared-dev-01", config.providers)
        self.assertFalse(config.providers["device:tokyo-shared-dev-01"].allow_send)

    def test_control_is_withheld_unless_the_binding_grants_it(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), self._minimal())
            config = fleet_config.load_config(path)
        self.assertFalse(config.provider_for("device:tokyo-shared-dev-01").allow_send)

    def test_a_wildcard_send_allow_is_refused_at_load(self):
        """A misconfiguration must be an outage on start, not a widening at 3am."""
        import tempfile

        body = self._minimal(policy={"send_allow": ["*"]})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(path)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("wildcard", caught.exception.detail)

    def test_a_wildcard_really_intended_is_allowed(self):
        import tempfile

        body = self._minimal(policy={"send_allow": ["*"], "allow_wildcard_send": True})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            config = fleet_config.load_config(path)
        self.assertTrue(config.policy.allow_wildcard_send)

    def test_an_unknown_provider_kind_is_refused(self):
        import tempfile

        body = self._minimal(providers={"device:x": {
            "kind": "cloudcli-shell", "endpoint": "http://x", "credential_anchor": "~/t"}})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(path)
        self.assertIn("unknown kind", caught.exception.detail)

    def test_a_provider_key_that_is_not_a_typed_ref_is_refused(self):
        import tempfile

        body = self._minimal(providers={"laptop": {
            "kind": "fleet-node", "endpoint": "http://x", "credential_anchor": "~/t"}})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(path)
        self.assertIn("not a typed device ref", caught.exception.detail)

    def test_the_wrong_schema_is_refused_rather_than_guessed(self):
        import tempfile

        body = self._minimal(schema="serveros/fleet-mcp/v2")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(path)
        self.assertIn("schema", caught.exception.detail)

    def test_a_hostname_is_not_a_local_device_identity(self):
        import tempfile

        body = self._minimal(device_ref="work-fj")
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), body)
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(path)
        self.assertIn("typed ref", caught.exception.detail)

    def test_no_binding_file_is_a_refusal_not_an_empty_fleet(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent.yaml"
            with self.assertRaises(FleetRefusal) as caught:
                fleet_config.load_config(missing)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("not found", caught.exception.detail)


class RegistryBoundary(unittest.TestCase):
    """The surface has no second idea of what a device is."""

    def test_the_identity_layer_is_the_repository_one(self):
        import fleet_mcp.registry as registry_module
        import fleet.transport as canonical

        self.assertIs(registry_module.DeviceRef, canonical.DeviceRef)
        self.assertIs(registry_module.DeviceRegistry, canonical.DeviceRegistry)

    def test_authored_facts_are_labelled_authored(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:work-fj")
        facts = authored_facts(device)
        self.assertEqual(facts["device_ref"], "device:work-fj")
        self.assertEqual(facts["authority"], "authored: fleet/registry.yaml")

    def test_a_hostname_does_not_resolve(self):
        registry = load_registry()
        with self.assertRaises(FleetRefusal):
            resolve_or_refuse(registry, "work-fj")

    def test_a_device_with_no_route_is_unknown_not_absent(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:personal-dell")
        availability, capability, probe = measured_availability(
            device, provider_present=False, reachable=False, authorized=False, is_local=False
        )
        self.assertEqual(availability.local_agent_controller, "UNKNOWN")
        self.assertEqual(availability.remote_inventory_from_this_runtime, "UNAVAILABLE")
        self.assertEqual(availability.state, "DEVICE_UNOBSERVED")
        # No route means nothing was established about any level. UNKNOWN is not
        # a negative: asserting NOT_GRANTED here would claim the device lacks a
        # capability that was never checked.
        self.assertEqual(capability.state("read"), "UNKNOWN")
        self.assertEqual(capability.endpoint, "ABSENT")
        self.assertFalse(probe["provider_present"])

    def test_an_unreachable_route_is_not_an_unenrolled_device(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:tokyo-shared-dev-01")
        availability, _, _ = measured_availability(
            device, provider_present=True, reachable=False, authorized=False, is_local=False
        )
        self.assertEqual(availability.local_agent_controller, "NOT_EXPOSED")
        self.assertEqual(availability.state, "CONTROLLER_NOT_EXPOSED_AND_PRESENCE_UNKNOWN")

    def test_a_refused_credential_is_not_an_unreachable_device(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:tokyo-shared-dev-01")
        availability, capability, probe = measured_availability(
            device, provider_present=True, reachable=True, authorized=False, is_local=False
        )
        self.assertEqual(availability.local_agent_controller, "NOT_EXPOSED")
        self.assertTrue(probe["reachable"])
        self.assertFalse(probe["authorized"])

    def test_a_reachable_device_reports_its_capable_and_its_external_levels(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:tokyo-shared-dev-01")
        availability, capability, _ = measured_availability(
            device, provider_present=True, reachable=True, authorized=True, is_local=False
        )
        available = set(capability.available_here())
        external = set(capability.external_only())
        self.assertIn("read", available)
        self.assertIn("discover", available)
        # Session control is message delivery, not a filesystem write, so the
        # level this runtime can actually exercise over the device is
        # `execute-safe` — and only through the device's own session semantics.
        self.assertIn("execute-safe", external)
        # Writing this device's filesystem is not a capability this surface has
        # on any plane. NOT_GRANTED is neither available nor external-only, and
        # folding it into either would be the overstatement that matters.
        self.assertEqual(capability.state("write-workspace"), "NOT_GRANTED")
        self.assertNotIn("write-workspace", available | external)
        self.assertEqual(capability.state("admin"), "NOT_GRANTED")

    def test_this_host_knows_itself_to_be_online(self):
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:work-fj")
        availability, _, _ = measured_availability(
            device, provider_present=False, reachable=False, authorized=False, is_local=True
        )
        self.assertEqual(availability.fleet_presence, "KNOWN_ONLINE")
        self.assertEqual(availability.state, "PRESENCE_KNOWN_CONTROLLER_UNKNOWN")

    def test_another_device_is_not_claimed_online_on_a_successful_probe(self):
        """A probe is this runtime's reach. It is not a fleet-plane presence fact."""
        registry = load_registry()
        device = resolve_or_refuse(registry, "device:tokyo-shared-dev-01")
        availability, _, _ = measured_availability(
            device, provider_present=True, reachable=True, authorized=True, is_local=False
        )
        self.assertEqual(availability.fleet_presence, "UNKNOWN")
        self.assertEqual(availability.state, "CONTROLLER_AVAILABLE_PRESENCE_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
