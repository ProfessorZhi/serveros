#!/usr/bin/env python3
"""Tests for the fleet view and its disclosure levels (§12, §13).

The sentence these tests keep false is the one §13 names outright:

    "the registry says device:work-fj has claude-code, so Claude runs there"

It is false for a reason that is easy to lose: a registry declaration is an
authoring statement about what a device is FOR, and a view that renders it as a
measurement produces a fleet-wide table of green ticks describing machines
nobody contacted. So most of what follows is about an unreached device, and
about the difference between "nobody looked" and "looked, found none".
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import threads

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


view_mod = _load("serveros_fleet_view", ROOT / "fleet" / "view.py")
router_mod = _load("serveros_fleet_agent_control_router",
                   ROOT / "fleet" / "agent_control_router.py")
provider_instance = _load("serveros_abilities_provider_instance",
                          ROOT / "abilities" / "provider_instance.py")

LOCAL = "device:server-beijing"
FJ = "device:work-fj"
DELL = "device:personal-dell"
# See test_agent_mcp: enrolled 2026-09-17.
TOKYO = "device:tokyo-shared-dev-01"
LEVELS = ("L0", "L1", "L2", "L3")

# Every device in `fleet/registry.yaml` declares both agents. That declaration
# is what the view must not promote into a claim.
DECLARED = ["claude-code", "codex"]


class FakeProvider:
    def __init__(self, provider_id: str) -> None:
        self.provider_id = provider_id

    def actions(self) -> dict:
        return {name: {"state": "SUPPORTED"}
                for name in ("session.create", "session.inspect", "session.send",
                             "session.result", "session.resume", "session.interrupt",
                             "session.stop", "session.handoff")}


class LocalService:
    """A local provider inventory and readiness record. Nothing else."""

    def __init__(self, *, provider_id="provider:test-agent-session", ready=True):
        self.providers = {provider_id: FakeProvider(provider_id)}
        self._ready = ready

    def readiness(self, provider_id: str) -> dict:
        if not self._ready:
            raise RuntimeError("provider would not answer a readiness probe")
        return {
            "schema": "serveros/provider-instance.v1",
            "provider": provider_id,
            "device_ref": LOCAL,
            "observed_at": provider_instance.now_utc(),
            "status": "AVAILABLE",
            "status_provenance": "INFERRED",
            "reason": None,
        }


def deployed(*, service=None, sessions=None):
    """The configuration this release deploys: loopback here, no node there.

    Built rather than defaulted, because a view with no routing layer answers
    UNKNOWN everywhere and that would let every assertion below pass for the
    wrong reason.
    """
    node = router_mod.NodeControlTransport(connector=None, device_refs=(FJ, DELL))
    loopback = router_mod.LocalLoopbackTransport(
        device_ref=LOCAL, service=service or LocalService(),
        bindings=threads.MemoryBindings(unknown=threads.ASSUME_BOUND))
    routing = router_mod.FleetAgentControlRouter(
        local_device_ref=LOCAL, transports=[loopback, node])
    return view_mod.FleetView(routing=routing, sessions=sessions)


def row_for(envelope: dict, device_ref: str) -> dict:
    for row in envelope["devices"]:
        if row["device_ref"] == device_ref:
            return row
    raise AssertionError(f"{device_ref} is missing from {[r['device_ref'] for r in envelope['devices']]}")


class StubAvailability:
    def __init__(self) -> None:
        self.fleet_presence = "REGISTERED"
        self.local_agent_controller = "ABSENT"
        self.remote_inventory_from_this_runtime = "UNREACHABLE"
        self.state = "DECLARED_ONLY"


class StubDevice:
    """Stands in for `fleet/transport.Device`, field for field.

    `runtime_bindings` is here because the real dataclass carries it as of W7
    and the view reads it without a `getattr`. A tolerant read would let a
    fleet layer that stopped populating the field render `NOT_DECLARED` for
    every device — an answer where the truthful one is "this view was handed a
    device that is not the shape it agreed to". The stub declaring a binding
    that agrees with its `agents` list is deliberate: a mismatch is a finding
    for `abilities/runtime_parity.py`, not something a view stub should model.
    """

    def __init__(self, ref: str) -> None:
        self.device_ref = type("Ref", (), {"ref": ref})()
        self.display_name = ref
        self.platform = "linux"
        self.environment_profile = "headless"
        self.enrollment = "enrolled"
        self.agents = ["codex"]
        self.runtime_bindings = ("agent-runtime:codex",)
        self.legacy_ids = []
        self.roles = []
        self.ownership = "hwz"
        self.os_family = "linux"


class StubRegistry:
    """A fleet the view has never seen, to prove it delegates rather than agrees."""

    def __init__(self, refs) -> None:
        self._devices = [StubDevice(ref) for ref in refs]

    def all(self):
        return list(self._devices)

    def resolve(self, ref, *, unbound_ok=False):
        for device in self._devices:
            if device.device_ref.ref == str(ref):
                return device
        raise KeyError(f"{ref} is not in this stub registry")


class StubInterfaces:
    def __init__(self, refs) -> None:
        self._registry = StubRegistry(refs)

    def _availability(self, device_ref):
        return StubAvailability()

    def _path_map(self, device):
        return {"node": {"state": "NOT_BOUND"}}


class TestTheLevelsAreDifferent(unittest.TestCase):
    def test_each_level_serves_a_different_field_set(self):
        """§12 asks for exactly this, and it is the whole feature.

        A disclosure level that returns everything is not a level. The test
        compares the served KEY SETS rather than the values, because equal
        values across levels are normal — an unreachable device looks
        unreachable at every level — while equal key sets mean the level
        boundary does not exist.
        """
        view = deployed()
        envelopes = {level: view.render(level=level) for level in LEVELS}
        served = {
            level: {key for row in envelope["devices"] for key in row}
            for level, envelope in envelopes.items()
        }
        for earlier, later in zip(LEVELS, LEVELS[1:]):
            with self.subTest(pair=(earlier, later)):
                self.assertLess(served[earlier], served[later],
                                f"{later} must serve strictly more row fields than {earlier}")

    def test_each_level_declares_the_fields_it_serves(self):
        view = deployed()
        for level in LEVELS:
            with self.subTest(level=level):
                envelope = view.render(level=level)
                declared = view_mod.cumulative_fields(level)
                for row in envelope["devices"]:
                    self.assertLessEqual(set(row), declared)

    def test_the_declared_sets_are_cumulative(self):
        for earlier, later in zip(LEVELS, LEVELS[1:]):
            with self.subTest(pair=(earlier, later)):
                self.assertLessEqual(view_mod.fields_for(earlier),
                                     view_mod.cumulative_fields(later))

    def test_an_undeclared_level_is_refused(self):
        view = deployed()
        for bad in ("L4", "l1", "L", "", "all"):
            with self.subTest(level=bad):
                with self.assertRaises(view_mod.ViewError):
                    view.render(level=bad)

    def test_the_envelope_names_the_level_it_answered(self):
        # A caller that asked cheaply must be able to tell, from the answer,
        # that it asked cheaply.
        view = deployed()
        for level in LEVELS:
            with self.subTest(level=level):
                self.assertEqual(view.render(level=level)["level"], level)

    def test_l0_carries_no_clock(self):
        """The cheapest level must not acquire an observation.

        `last_seen` is the field most likely to creep in — it is one line, and
        a default of "now" would make L0 read as a freshly measured level.
        """
        row = deployed().render(level="L0")["devices"][0]
        for key in ("last_seen", "observed_at", "presence_provenance",
                    "node_runtime", "reachability", "providers"):
            with self.subTest(field=key):
                self.assertNotIn(key, row)
        text = repr(row)
        for stamp in ("T00:", "Z'", "2026-", "observed_at"):
            with self.subTest(stamp=stamp):
                self.assertNotIn(stamp, text)

    def test_l1_does_not_contact_the_device(self):
        """L1 is one local observation per device, not a conversation.

        The node transport raises if its `resolve_inputs` is called without a
        connector... it returns nothing, which is silent. So this asserts on the
        field that would only exist if the contact happened.
        """
        row = row_for(deployed().render(level="L1"), FJ)
        self.assertNotIn("providers", row)
        self.assertNotIn("provider_readiness", row)


class TestAnUnreachedDeviceIsTyped(unittest.TestCase):
    def test_a_device_with_no_node_is_unavailable_not_merely_unknown(self):
        """§13: FJ and Dell have no node, and that is a definite fact."""
        row = row_for(deployed().render(level="L1"), FJ)
        self.assertEqual(row["runtime_summary"]["state"], "UNAVAILABLE")
        self.assertEqual(row["runtime_summary"]["reason"], "NODE_RUNTIME_UNAVAILABLE")
        self.assertFalse(row["runtime_summary"]["runnable"])

    def test_the_reason_word_comes_from_the_resolver(self):
        # A view that spelled the word itself would be a second home for §8's
        # vocabulary, and the second home is the one that drifts.
        target = _load("serveros_abilities_target", ROOT / "abilities" / "target.py")
        self.assertIn("NODE_RUNTIME_UNAVAILABLE", target.reason_vocabulary())

    def test_a_declared_agent_is_never_rendered_as_a_runnable_one(self):
        """The §13 sentence, kept false at every level.

        Every device in the registry declares `claude-code`. A device with no
        node runtime must not carry a field anywhere in its row that says so
        in the present tense.
        """
        view = deployed()
        # L0 has no declaration field yet — it carries identity only — so the
        # claim cannot be made at L0 and is not absent because it was dropped.
        for level in LEVELS[1:]:
            row = row_for(view.render(level=level), FJ)
            self.assertEqual(row["declared_agents"], DECLARED)
            self.assertIn("declared", row["agent_runtimes"]["state"].lower())
            self.assertEqual(row["agent_runtimes"]["measured"]["state"], "UNKNOWN")
            if "runtime_summary" in row:
                self.assertIsNot(row["runtime_summary"]["runnable"], True)

    def test_an_unreached_device_is_still_in_the_list(self):
        # §13 asks for the device, not for a reachable device. Dropping the row
        # is the other way this view could lie: an unreachable machine would
        # leave a fleet that looks smaller rather than one that looks broken.
        envelope = deployed().render(level="L2")
        self.assertEqual({row["device_ref"] for row in envelope["devices"]},
                         {LOCAL, FJ, DELL, TOKYO})
        self.assertEqual(envelope["count"], 4)

    def test_a_device_the_runtime_cannot_see_is_unknown_not_absent(self):
        # No routing layer at all is not the same as a device that is down.
        bare = view_mod.FleetView()
        row = row_for(bare.render(level="L1"), LOCAL)
        self.assertEqual(row["node_runtime"]["state"], "UNKNOWN")
        self.assertEqual(row["runtime_summary"]["state"], "UNKNOWN")
        self.assertIsNone(row["runtime_summary"]["runnable"])


class TestNoEmptyListMeansUnknown(unittest.TestCase):
    """§12's rule at this layer, checked structurally rather than by eye."""

    UNKNOWABLE = ("providers", "provider_instances", "active_sessions",
                  "reachability", "node_runtime")

    def test_no_row_answers_unknown_with_an_empty_list(self):
        view = deployed(sessions=lambda ref: view_mod.unknown("no session source"))
        for level in LEVELS:
            envelope = view.render(level=level)
            for row in envelope["devices"]:
                for key in self.UNKNOWABLE:
                    if key not in row:
                        continue
                    value = row[key]
                    with self.subTest(level=level, device=row["device_ref"], field=key):
                        if isinstance(value, list):
                            self.assertTrue(value, f"{key} is an empty list")
                        elif isinstance(value, dict) and value.get("state") == "UNKNOWN":
                            self.assertTrue(value.get("reason"),
                                            f"{key} says UNKNOWN with no reason")

    def test_a_device_that_answers_none_says_none(self):
        """The other half: "answered, and there are none" is a real answer."""
        view = deployed()
        envelope = view.render(level="L2")
        row = row_for(envelope, LOCAL)
        self.assertEqual(row["providers"]["state"], "KNOWN")
        self.assertTrue(row["providers"]["providers"])

    def test_a_device_that_was_never_asked_says_so(self):
        # The node transport returns an empty `resolved` payload when it has no
        # connector. Empty means unnoticed, not "none".
        row = row_for(deployed().render(level="L2"), FJ)
        self.assertEqual(row["providers"]["state"], "UNKNOWN")
        self.assertIn("has not asked", row["providers"]["reason"])

    def test_a_session_source_that_raises_is_unknown_not_empty(self):
        def explode(device_ref):
            raise RuntimeError("the session service is down")

        row = row_for(deployed(sessions=explode).render(level="L2"), LOCAL)
        self.assertEqual(row["active_sessions"]["state"], "UNKNOWN")
        self.assertIn("RuntimeError", row["active_sessions"]["reason"])

    def test_no_session_source_is_named_as_such(self):
        row = row_for(deployed().render(level="L2"), LOCAL)
        self.assertEqual(row["active_sessions"]["state"], "UNKNOWN")
        self.assertIn("no session source", row["active_sessions"]["reason"])

    def test_a_session_source_is_reported_when_it_answers(self):
        seen = []

        def sessions(device_ref):
            seen.append(device_ref)
            return {"state": "KNOWN", "count": 0, "sessions": []}

        row = row_for(deployed(sessions=sessions).render(level="L2"), LOCAL)
        self.assertEqual(row["active_sessions"], {"state": "KNOWN", "count": 0,
                                                  "sessions": []})
        # Once per device in the answer, because "what is running there now" is
        # a question about a device. Asking for one unnamed fleet-wide total
        # would be the view inventing a scope the provider never agreed to.
        self.assertEqual(seen, [LOCAL, FJ, DELL, TOKYO])


class TestTheViewDoesNotInventProvenance(unittest.TestCase):
    def test_l3_says_where_each_field_came_from(self):
        row = row_for(deployed().render(level="L3"), FJ)
        provenance = row["field_provenance"]
        self.assertIn("fleet/registry.yaml", provenance["device_ref"])
        self.assertIn("declaration", provenance["declared_agents"])
        self.assertEqual(provenance["reachability"], row["presence_provenance"])

    def test_l3_names_what_it_did_not_measure(self):
        # A provenance block that lists only sources reads as "this is
        # everything". The list of what was not measured is the half that
        # stops a reader concluding the row is complete.
        row = row_for(deployed().render(level="L3"), LOCAL)
        self.assertTrue(row["field_provenance"]["not_measured_here"])

    def test_the_routing_block_names_the_transport_that_would_carry_it(self):
        # §7's answer to "why can't you just run it there" is a transport fact.
        row = row_for(deployed().render(level="L3"), FJ)
        self.assertEqual(row["routing"]["serving_transport"], "node-control")
        self.assertFalse(row["routing"]["is_local"])
        self.assertEqual(row["routing"]["local_device_ref"], LOCAL)

    def test_the_availability_axes_survive_to_l3(self):
        row = row_for(deployed().render(level="L3"), FJ)
        self.assertIn("state", row["availability_axes"])
        self.assertIn("fleet_presence", row["availability_axes"])


class TestTheViewHoldsNoState(unittest.TestCase):
    def test_two_reads_of_an_unchanged_fleet_agree(self):
        view = deployed()
        first = view.render(level="L2")
        second = view.render(level="L2")
        # The only field that may differ is the clock inside an observation,
        # and even that comes from the loopback's own read.
        def strip(envelope):
            return [{k: v for k, v in row.items()
                     if k not in ("active_sessions",)} for row in envelope["devices"]]
        self.assertEqual(len(strip(first)), len(strip(second)))

    def test_a_single_device_read_is_a_subset_of_the_fleet_read(self):
        view = deployed()
        one = view.render(level="L1", device_refs=[FJ])
        self.assertEqual(one["count"], 1)
        self.assertEqual(one["devices"][0]["device_ref"], FJ)

    def test_an_unknown_device_ref_is_refused_rather_than_invented(self):
        with self.assertRaises(Exception):
            deployed().render(level="L0", device_refs=["device:not-enrolled"])

    def test_the_device_list_comes_from_the_fleet_layer(self):
        """Not a source scan: a registry the view has never seen.

        A view that carried its own device list would still answer about the
        real fleet and pass every other test here. Handing it a registry that
        contains a device no other file mentions is the only way to tell
        "delegates" from "happens to agree today".
        """
        invented = "device:somewhere-else"
        view = view_mod.FleetView(interfaces=StubInterfaces([invented]))
        envelope = view.render(level="L3")
        self.assertEqual([row["device_ref"] for row in envelope["devices"]],
                         [invented])
        self.assertEqual(envelope["count"], 1)

    def test_the_registry_path_is_the_transports_path(self):
        # The provenance line names where a declaration came from. It is read
        # from the layer that loads the file, so moving the file cannot leave a
        # confident provenance line pointing at nothing.
        transport = _load("serveros_fleet_transport", ROOT / "fleet" / "transport.py")
        row = row_for(deployed().render(level="L3"), LOCAL)
        self.assertEqual(row["field_provenance"]["declared_agents"],
                         f"{transport.REGISTRY_PATH.relative_to(transport.ROOT)} "
                         f"(declaration)")


class TestTheLevelsAreUsedForTheirCost(unittest.TestCase):
    def test_l2_is_the_first_level_that_reaches_the_device(self):
        """Cost is the reason the levels exist, so it is asserted.

        The node transport needs a connector to answer anything; without one it
        returns nothing. So the observable difference between L1 and L2 for a
        remote device is the provider block, and that block is what a caller is
        paying for.
        """
        view = deployed()
        self.assertNotIn("providers", row_for(view.render(level="L1"), FJ))
        self.assertIn("providers", row_for(view.render(level="L2"), FJ))

    def test_the_l0_field_set_is_small(self):
        self.assertLessEqual(len(view_mod.cumulative_fields("L0")), 8)
        self.assertLess(len(view_mod.cumulative_fields("L0")),
                        len(view_mod.cumulative_fields("L2")))

    def test_a_device_row_does_not_borrow_the_envelopes_keys(self):
        # `count` and `state` describe the answer; `platform` describes a
        # machine. Declaring them in one list is how a device row acquires a
        # `count` in a reader's head — and then in the code.
        for level in LEVELS:
            with self.subTest(level=level):
                overlap = view_mod.fields_for(level) & view_mod.envelope_fields()
                self.assertFalse(overlap, f"{level} declares envelope keys as row fields")

    def test_the_envelope_is_exactly_what_the_contract_declares(self):
        envelope = deployed().render(level="L1")
        self.assertEqual(set(envelope), set(view_mod.envelope_fields()))


if __name__ == "__main__":
    unittest.main()
