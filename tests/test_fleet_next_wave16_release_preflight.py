#!/usr/bin/env python3
"""Wave 16: offline release-safety preflight for a Legacy/Next co-install.

What this file is allowed to prove, and what it is not
------------------------------------------------------
Every assertion here runs in a fake, offline environment. No service is
started, no port is bound, no socket is opened, no systemd unit is touched, no
credential is read, no Feishu message is sent, and no device is addressed. A
green run therefore says the *rules* refuse a resource takeover — it is not a
real-device pass, and no test in this file may be cited as one.

The subject under test is `integrations/fleet-next-wave16-release-preflight/
wave16_preflight.py`: a pure decision surface plus one read-only repository
reader (git `ls-tree`). The reader exists because a reproducible release unit
has to be described by content hashes that do not move with a checkout's line
endings; blob SHAs come from the object store, not the worktree.

Positive controls
-----------------
The gate is a veto table, and a veto table that can only say "OK" is
indistinguishable from no gate at all. So every one of the eight resources is
driven twice in `EveryResourceIsAVeto`: once with a value that must collide and
once with a value that must not, and the collision is required to name exactly
that resource. Without the second half, an implementation that blocked
everything would pass.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-wave16-release-preflight"

wave16 = sys.modules.get("wave16_preflight")
if wave16 is None:
    spec = importlib.util.spec_from_file_location(
        "wave16_preflight", HERE / "wave16_preflight.py")
    if spec is None or spec.loader is None:  # pragma: no cover - RED marker
        raise ImportError(f"wave16_preflight.py is not at {HERE}")
    wave16 = importlib.util.module_from_spec(spec)
    sys.modules["wave16_preflight"] = wave16
    spec.loader.exec_module(wave16)


# --------------------------------------------------------------------------
# fixtures: two footprints that are disjoint until one field is changed
# --------------------------------------------------------------------------

def legacy_footprint(**overrides):
    base = dict(
        track="legacy",
        install_path="/home/operator/runtime-assets/fleet-v1",
        port=8101,
        socket=None,
        backend_id="plugin_asdk_legacy_000000000001",
        oauth_audience="https://legacy.example.invalid/mcp",
        cred_state_root="/home/operator/.local/state/fleet-v1",
        lock="/home/operator/.local/lock/fleet-v1",
        unit="serveros-fleet-mcp.service",
        device_ref="device:tokyo-shared-dev-01",
        version="0.1.0",
        artifact_digest="sha256:" + "a" * 64,
    )
    base.update(overrides)
    return wave16.Footprint(**base)


def next_footprint(**overrides):
    base = dict(
        track="next",
        install_path="/home/operator/runtime-assets/fleet-next",
        port=8102,
        socket=None,
        backend_id="plugin_asdk_next_000000000002",
        oauth_audience="https://next.example.invalid/mcp",
        cred_state_root="/home/operator/.local/state/fleet-next",
        lock="/home/operator/.local/lock/fleet-next",
        unit="serveros-fleet-next-mcp.service",
        device_ref="device:tokyo-shared-dev-01",
        version="1.0.0",
        artifact_digest="sha256:" + "b" * 64,
    )
    base.update(overrides)
    return wave16.Footprint(**base)


class FakeLegacyGateway:
    """The Legacy controller as a test double: three verbs, one call log.

    `send` is idempotent by key on purpose. The property under test is not
    "the fake is stable" — it is that a Next lifecycle step cannot cause the
    Legacy send contract to emit a second delivery for an intent it already
    accepted.
    """

    def __init__(self, *, instance="fleet-instance-legacy", boot="boot-legacy-1"):
        self.calls = []
        self.deliveries = []
        self.instance = instance
        self.boot = boot
        self.run_status = "completed"
        self._receipts = {}
        self.fail_from = None

    def call(self, verb, key):
        self.calls.append((verb, key))
        if self.fail_from is not None and len(self.calls) >= self.fail_from:
            raise RuntimeError("legacy gateway unavailable")
        if verb == "get_session":
            return {"sessionId": key, "title": "held", "status": "idle",
                    "instance": self.instance, "boot": self.boot}
        if verb == "get_run":
            return {"runId": key, "status": self.run_status, "sessionId": "s-" + key,
                    "instance": self.instance, "boot": self.boot}
        if verb == "send":
            if key not in self._receipts:
                receipt = {"deliveryId": "d-" + key, "accepted": True,
                           "sessionId": "s-" + key}
                self._receipts[key] = receipt
                self.deliveries.append(key)
            return dict(self._receipts[key])
        raise AssertionError(f"unexpected verb {verb!r}")


class FakeShadowGateway:
    """A Next shadow that records every call it was asked to make."""

    def __init__(self):
        self.calls = []

    def call(self, verb, key=None):
        self.calls.append(verb)
        return {"ok": True, "verb": verb}


class FakeNextInstaller:
    """A Next installer whose only durable effect is a `current` pointer.

    `writes` is the list of paths it claims to have written. A failed install
    must leave both `pointer` and the Legacy footprint untouched, and the test
    reads them off this object rather than off a return value.
    """

    def __init__(self, *, fail=False):
        self.fail = fail
        self.pointer = "/home/operator/runtime-assets/fleet-next/revisions/next@1111111"
        self.writes = []

    def run(self):
        if self.fail:
            self.writes.append("/home/operator/runtime-assets/fleet-next/revisions/next@2222222")
            raise OSError("staging failed before the pointer swap")
        self.pointer = "/home/operator/runtime-assets/fleet-next/revisions/next@2222222"
        self.writes.append(self.pointer)
        return self.pointer


class FakeSystemctl:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.calls = []

    def show(self, name):
        self.calls.append(name)
        return dict(self.state.get(name, {"enabled": False, "active": False}))


# --------------------------------------------------------------------------
# 1. the veto table: eight resources, each proven able to block AND to pass
# --------------------------------------------------------------------------

class EveryResourceIsAVeto(unittest.TestCase):

    def collide(self, resource, **next_overrides):
        plan = wave16.resource_matrix(
            legacy_footprint(), next_footprint(**next_overrides))
        self.assertEqual(plan.decision, wave16.BLOCKED, plan)
        self.assertEqual(plan.code, wave16.BLOCKED_RESOURCE_COLLISION, plan)
        named = [c.resource for c in plan.collisions]
        self.assertEqual(named, [resource], plan.collisions)
        return plan.collisions[0]

    def test_a_disjoint_pair_passes(self):
        plan = wave16.resource_matrix(legacy_footprint(), next_footprint())
        self.assertEqual(plan.decision, wave16.OK, plan)
        self.assertEqual(plan.code, wave16.OK)
        self.assertEqual(plan.collisions, ())

    def test_install_path(self):
        self.collide("install_path", install_path="/home/operator/runtime-assets/fleet-v1")

    def test_port(self):
        self.collide("port", port=8101)

    def test_socket(self):
        legacy = legacy_footprint(socket="/run/fleet/shared.sock")
        plan = wave16.resource_matrix(
            legacy, next_footprint(socket="/run/fleet/shared.sock"))
        self.assertEqual(plan.decision, wave16.BLOCKED)
        self.assertEqual([c.resource for c in plan.collisions], ["socket"])

    def test_a_track_without_a_unix_socket_does_not_collide_on_one(self):
        plan = wave16.resource_matrix(legacy_footprint(), next_footprint())
        self.assertEqual([c.resource for c in plan.collisions], [])

    def test_backend_id(self):
        self.collide("backend_id", backend_id="plugin_asdk_legacy_000000000001")

    def test_oauth_audience(self):
        self.collide("oauth_audience",
                     oauth_audience="https://legacy.example.invalid/mcp")

    def test_audience_normalises_only_the_spelling(self):
        # A trailing slash and an explicit default port are the same resource.
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(oauth_audience="https://legacy.example.invalid:443/mcp/"))
        self.assertEqual([c.resource for c in plan.collisions], ["oauth_audience"])
        # A different path on the same origin is a different protected resource.
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(oauth_audience="https://legacy.example.invalid/mcp-next"))
        self.assertEqual(plan.decision, wave16.OK, plan)

    def test_cred_state_root(self):
        self.collide("cred_state_root",
                     cred_state_root="/home/operator/.local/state/fleet-v1")

    def test_lock(self):
        self.collide("lock", **{"lock": "/home/operator/.local/lock/fleet-v1"})

    def test_unit(self):
        self.collide("unit", unit="serveros-fleet-mcp.service")

    def test_every_collision_is_reported_at_once(self):
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(port=8101, unit="serveros-fleet-mcp.service",
                           backend_id="plugin_asdk_legacy_000000000001"))
        self.assertEqual(
            [c.resource for c in plan.collisions],
            ["port", "backend_id", "unit"])
        self.assertEqual(
            [c.resource for c in plan.collisions],
            sorted((c.resource for c in plan.collisions),
                   key=wave16.RESOURCES.index))


# --------------------------------------------------------------------------
# 2. a different field name is not a different boundary
# --------------------------------------------------------------------------

class OverlapIsCheckedAcrossFieldNames(unittest.TestCase):

    def test_next_state_root_inside_legacy_install_path(self):
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(cred_state_root="/home/operator/runtime-assets/fleet-v1/state"))
        self.assertEqual(plan.decision, wave16.BLOCKED)
        self.assertEqual([c.resource for c in plan.collisions], ["cred_state_root"])

    def test_next_install_path_inside_legacy_lock_root(self):
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(install_path="/home/operator/.local/lock/fleet-v1/next"))
        self.assertEqual([c.resource for c in plan.collisions], ["install_path"])

    def test_a_socket_inside_the_other_tracks_root_is_a_collision(self):
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(socket="/home/operator/runtime-assets/fleet-v1/next.sock"))
        self.assertEqual([c.resource for c in plan.collisions], ["socket"])

    def test_a_sibling_with_a_matching_prefix_is_not_nested(self):
        plan = wave16.resource_matrix(
            legacy_footprint(install_path="/opt/fleet"),
            next_footprint(install_path="/opt/fleet-next"))
        self.assertEqual(plan.decision, wave16.OK, plan)

    def test_nesting_is_checked_in_both_directions(self):
        plan = wave16.resource_matrix(
            legacy_footprint(install_path="/opt/fleet/next"),
            next_footprint(install_path="/opt/fleet"))
        self.assertEqual(plan.decision, wave16.BLOCKED)
        self.assertIn("install_path", [c.resource for c in plan.collisions])


# --------------------------------------------------------------------------
# 3. identity: backend id differs, device agrees
# --------------------------------------------------------------------------

class IdentityHasTwoPolarities(unittest.TestCase):
    """`backend_id` must differ; `device_ref` must be equal.

    This is the pair that a single "identity" check gets wrong. Two connectors
    under one backend id are one connector, so they must differ. But both
    tracks run on ONE host, and the host is what `device_ref` declares — a
    second value means one of the two bindings is about another machine. Both
    services run on one device, so this one must agree.
    """

    def test_device_disagreement_blocks_even_when_everything_else_is_clean(self):
        plan = wave16.resource_matrix(
            legacy_footprint(), next_footprint(device_ref="device:dell-fjd-02"))
        self.assertEqual(plan.decision, wave16.BLOCKED)
        self.assertEqual(plan.code, wave16.BLOCKED_DEVICE_DISAGREEMENT)
        self.assertEqual(plan.collisions, ())

    def test_device_agreement_is_not_a_collision(self):
        self.assertEqual(legacy_footprint().device_ref, next_footprint().device_ref)
        plan = wave16.resource_matrix(legacy_footprint(), next_footprint())
        self.assertEqual(plan.decision, wave16.OK)

    def test_collisions_and_device_disagreement_are_both_reported(self):
        plan = wave16.resource_matrix(
            legacy_footprint(),
            next_footprint(port=8101, device_ref="device:dell-fjd-02"))
        self.assertEqual(plan.code, wave16.BLOCKED_RESOURCE_COLLISION)
        self.assertEqual([c.resource for c in plan.collisions], ["port"])
        self.assertFalse(plan.device_agreement)


# --------------------------------------------------------------------------
# 4. an unusable footprint is refused, not half-checked
# --------------------------------------------------------------------------

class IncompleteFootprintIsRefused(unittest.TestCase):

    def test_relative_paths_are_not_resolved(self):
        plan = wave16.resource_matrix(
            legacy_footprint(), next_footprint(**{"lock": "lock/fleet-next"}))
        self.assertEqual(plan.decision, wave16.BLOCKED)
        self.assertEqual(plan.code, wave16.BLOCKED_INCOMPLETE)
        self.assertIn("lock", plan.incomplete)

    def test_a_digest_that_is_not_pinned_is_refused(self):
        plan = wave16.resource_matrix(
            legacy_footprint(), next_footprint(artifact_digest="sha256:"))
        self.assertEqual(plan.code, wave16.BLOCKED_INCOMPLETE)
        self.assertIn("artifact_digest", plan.incomplete)

    def test_gaps_are_deduplicated_and_stable(self):
        plan = wave16.resource_matrix(
            legacy_footprint(install_path="relative/one", **{"lock": "relative/two"}),
            next_footprint(install_path="relative/one", **{"lock": "relative/two"}))
        self.assertEqual(plan.code, wave16.BLOCKED_INCOMPLETE)
        self.assertEqual(list(plan.incomplete), ["install_path", "lock"])


# --------------------------------------------------------------------------
# 5. the Next shadow writes nothing: create/send = 0 effects
# --------------------------------------------------------------------------

class ShadowWritesNothing(unittest.TestCase):

    def test_create_and_send_are_refused_without_reaching_the_gateway(self):
        gateway = FakeShadowGateway()
        ledger = wave16.shadow_tally(gateway, ["create", "send"])
        self.assertEqual(ledger.decision, wave16.BLOCKED)
        self.assertEqual(ledger.writes, 0)
        self.assertEqual(ledger.gateway_calls, 0)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(ledger.refused, 2)
        self.assertEqual(set(ledger.codes), {wave16.SHADOW_WRITE_FORBIDDEN})

    def test_granting_a_write_verb_does_not_un_refuse_it(self):
        gateway = FakeShadowGateway()
        ledger = wave16.shadow_tally(
            gateway, ["create", "send"], granted=["create", "send", "get_session"])
        self.assertEqual(ledger.writes, 0)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(set(ledger.codes), {wave16.SHADOW_WRITE_FORBIDDEN})

    def test_a_mixed_batch_still_writes_nothing(self):
        gateway = FakeShadowGateway()
        ledger = wave16.shadow_tally(
            gateway, ["get_session", "create", "get_run", "send"],
            granted=["get_session", "get_run", "create", "send"])
        self.assertEqual(ledger.writes, 0)
        self.assertEqual(ledger.refused, 2)
        self.assertEqual(gateway.calls, ["get_session", "get_run"])
        self.assertEqual(ledger.decision, wave16.OK)

    def test_an_unknown_verb_is_refused_rather_than_attempted(self):
        gateway = FakeShadowGateway()
        ledger = wave16.shadow_tally(gateway, ["teleport"], granted=["teleport"])
        self.assertEqual(gateway.calls, [])
        self.assertEqual(ledger.codes, (wave16.SHADOW_VERB_UNKNOWN,))

    def test_an_observed_read_is_counted_without_being_a_write(self):
        gateway = FakeShadowGateway()
        ledger = wave16.shadow_tally(gateway, ["get_run"], granted=["get_run"])
        self.assertEqual(ledger.writes, 0)
        self.assertEqual(ledger.observed, 1)
        self.assertEqual(ledger.decision, wave16.OK)


# --------------------------------------------------------------------------
# 6. the Legacy contract: idempotent reads, one delivery per intent
# --------------------------------------------------------------------------

class LegacyContractIsIdempotent(unittest.TestCase):

    def test_reads_repeat_without_moving(self):
        gateway = FakeLegacyGateway()
        self.assertTrue(wave16.read_idempotence(gateway, key="probe-1"))
        self.assertEqual(
            [verb for verb, _ in gateway.calls], ["get_session", "get_session",
                                                  "get_run", "get_run"])

    def test_a_failing_read_is_reported_as_not_idempotent_not_as_equal(self):
        gateway = FakeLegacyGateway()
        gateway.fail_from = 2
        # Two failures are not evidence of equality; the probe refuses to call
        # "both raised" idempotence.
        self.assertFalse(wave16.read_idempotence(gateway, key="probe-1"))


class LegacySurvivesEveryNextStep(unittest.TestCase):

    def setUp(self):
        self.gateway = FakeLegacyGateway()

    def steps(self):
        footprint = next_footprint()
        failed = FakeNextInstaller(fail=True)
        upgraded = wave16.next_upgrade(
            footprint, version="1.1.0", artifact_digest="sha256:" + "c" * 64)

        def install_failure():
            result = wave16.next_install_failure(footprint, installer=failed)
            self.assertEqual(result.decision, wave16.BLOCKED)
            self.assertEqual(result.code, wave16.INSTALL_FAILED)
            self.assertEqual(result.footprint, footprint)

        def upgrade():
            self.assertEqual(upgraded.decision, wave16.OK, upgraded)

        def rollback():
            result = wave16.next_rollback(upgraded.footprint)
            self.assertEqual(result.decision, wave16.OK, result)
            self.assertEqual(result.footprint.version, footprint.version)

        return [("next-install-failure", install_failure),
                ("next-upgrade", upgrade),
                ("next-rollback", rollback)], failed

    def test_no_step_drifts_the_legacy_contract(self):
        steps, _failed = self.steps()
        report = wave16.survival(self.gateway, steps, key="intent-7")
        self.assertEqual([name for name, _ in report],
                         ["next-install-failure", "next-upgrade", "next-rollback"])
        for name, check in report:
            self.assertEqual(check.decision, wave16.OK, (name, check))
            self.assertEqual(check.drifted, (), name)

    def test_a_failed_install_moves_no_pointer(self):
        steps, failed = self.steps()
        step = dict(steps)["next-install-failure"]
        before = failed.pointer
        step()
        self.assertEqual(failed.pointer, before)
        # Staging may have written; the `current` pointer is the line that
        # makes a service run new bytes, and it must not have been crossed.
        self.assertEqual([w for w in failed.writes if w.endswith("/current")], [])

    def test_send_stays_one_delivery_per_intent_across_the_whole_lifecycle(self):
        steps, _failed = self.steps()
        wave16.survival(self.gateway, steps, key="intent-7")
        self.assertEqual(self.gateway.deliveries, ["intent-7"])

    def test_the_probe_can_actually_see_drift(self):
        # Positive control: a step that DOES move an answer must be caught,
        # otherwise "SAME" is a statement about the probe, not about Legacy.
        report = wave16.survival(
            self.gateway,
            [("mutate-log-only", lambda: self.gateway.calls.clear())],
            key="intent-8")
        self.assertEqual(report[0][1].decision, wave16.OK)

        changed = FakeLegacyGateway()

        def answer_moves():
            changed.run_status = "running"

        report = wave16.survival(
            changed, [("mutate", answer_moves)], key="intent-9")
        self.assertEqual(report[0][1].decision, wave16.BLOCKED)
        self.assertEqual(report[0][1].code, wave16.LEGACY_CONTRACT_DRIFT)
        self.assertEqual(report[0][1].drifted, ("get_run",))


class UpgradeAndRollbackRefuseBadSteps(unittest.TestCase):

    def test_only_the_next_track_may_advance_from_here(self):
        result = wave16.next_upgrade(
            legacy_footprint(), version="0.2.0", artifact_digest="sha256:" + "d" * 64)
        self.assertEqual(result.decision, wave16.BLOCKED)
        self.assertEqual(result.code, wave16.BLOCKED_CROSS_TRACK)
        self.assertEqual(result.footprint, legacy_footprint())

    def test_a_version_that_does_not_advance_is_refused(self):
        result = wave16.next_upgrade(
            next_footprint(), version="1.0.0", artifact_digest="sha256:" + "d" * 64)
        self.assertEqual(result.code, wave16.BLOCKED_VERSION_NOT_ADVANCING)

    def test_upgrade_records_what_to_go_back_to(self):
        result = wave16.next_upgrade(
            next_footprint(), version="1.1.0", artifact_digest="sha256:" + "c" * 64)
        self.assertEqual(result.footprint.rollback_version, "1.0.0")
        self.assertEqual(result.footprint.rollback_digest, "sha256:" + "b" * 64)

    def test_rollback_without_a_recorded_digest_is_refused(self):
        result = wave16.next_rollback(next_footprint())
        self.assertEqual(result.decision, wave16.BLOCKED)
        self.assertEqual(result.code, wave16.BLOCKED_INCOMPLETE)

    def test_rollback_restores_the_exact_placement(self):
        upgraded = wave16.next_upgrade(
            next_footprint(), version="1.1.0", artifact_digest="sha256:" + "c" * 64)
        restored = wave16.next_rollback(upgraded.footprint)
        self.assertEqual(restored.footprint, next_footprint())
        self.assertEqual(restored.footprint.rollback_version, "")


# --------------------------------------------------------------------------
# 7. the new Timer is not activated and the plan has no side effect
# --------------------------------------------------------------------------

class TimerIsNotActivated(unittest.TestCase):

    def test_every_timer_in_the_tree_is_found(self):
        names = wave16.timers_in(ROOT)
        self.assertIn("v13-live-heartbeat", names)
        self.assertIn("v13-report-period", names)
        self.assertEqual(list(names), sorted(names))

    def test_an_inactive_disabled_set_passes(self):
        gate = wave16.timer_gate(FakeSystemctl(), wave16.timers_in(ROOT))
        self.assertEqual(gate.decision, wave16.OK, gate)
        self.assertEqual(gate.writes, ())
        self.assertEqual(gate.executes, ())

    def test_an_enabled_timer_blocks(self):
        names = wave16.timers_in(ROOT)
        systemctl = FakeSystemctl({"v13-live-heartbeat": {"enabled": True, "active": False}})
        gate = wave16.timer_gate(systemctl, names)
        self.assertEqual(gate.decision, wave16.BLOCKED)
        self.assertEqual(gate.code, wave16.BLOCKED_TIMER_ARMED)
        self.assertEqual(gate.armed, ("v13-live-heartbeat",))

    def test_an_active_timer_blocks_even_when_not_enabled(self):
        names = wave16.timers_in(ROOT)
        systemctl = FakeSystemctl({"v13-report-period": {"enabled": False, "active": True}})
        gate = wave16.timer_gate(systemctl, names)
        self.assertEqual(gate.armed, ("v13-report-period",))

    def test_a_plan_that_would_enable_a_timer_is_refused(self):
        names = wave16.timers_in(ROOT)
        gate = wave16.timer_gate(
            FakeSystemctl(), names,
            plan={"enable": ["v13-live-heartbeat.timer"], "start": []})
        self.assertEqual(gate.decision, wave16.BLOCKED)
        self.assertEqual(gate.armed, ("v13-live-heartbeat",))

    def test_the_gate_only_ever_shows(self):
        systemctl = FakeSystemctl()
        wave16.timer_gate(systemctl, wave16.timers_in(ROOT))
        self.assertEqual(sorted(systemctl.calls), sorted(wave16.timers_in(ROOT)))


# --------------------------------------------------------------------------
# 8. reproducible release units, and where they are not independent
# --------------------------------------------------------------------------

class ReleaseUnitsAreReproducible(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ref = wave16.head_ref(ROOT)
        cls.manifests = {name: wave16.manifest(ROOT, cls.ref, name)
                         for name in wave16.COMPONENTS}

    def test_every_component_has_files(self):
        for name, files in self.manifests.items():
            self.assertTrue(files, name)

    def test_components_partition_the_tree_without_overlap(self):
        seen = {}
        for name, files in self.manifests.items():
            for path in files:
                self.assertNotIn(path, seen, f"{path} claimed by {name} and {seen.get(path)}")
                seen[path] = name

    def test_a_manifest_is_byte_stable_across_calls(self):
        again = wave16.manifest(ROOT, self.ref, "hub")
        self.assertEqual(again, self.manifests["hub"])

    def test_hashes_are_blob_sha_not_worktree_bytes(self):
        # A git blob SHA is 40 hex characters and is derived from the object
        # store; a worktree hash would move with CRLF translation.
        for files in self.manifests.values():
            for path, sha in files.items():
                self.assertRegex(sha, r"^[0-9a-f]{40}$", path)

    def test_the_tree_hash_covers_the_file_list(self):
        report = wave16.component_report(ROOT, self.ref, "hub")
        self.assertRegex(report.tree_sha256, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(report.files, len(self.manifests["hub"]))


class ComponentsSayVerifiedOrBlocked(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.ref = wave16.head_ref(ROOT)
        cls.reports = {name: wave16.component_report(ROOT, cls.ref, name)
                       for name in wave16.COMPONENTS}

    def test_only_two_answers_exist(self):
        for name, report in self.reports.items():
            self.assertIn(report.decision, (wave16.VERIFIED, wave16.BLOCKED), name)

    def test_a_blocked_report_names_its_reasons(self):
        for name, report in self.reports.items():
            if report.decision == wave16.BLOCKED:
                self.assertTrue(report.reasons, name)
                self.assertEqual(report.code, wave16.BLOCKED_RELEASE_UNIT, name)

    def test_the_node_depends_on_the_hub_package(self):
        report = self.reports["node"]
        self.assertFalse(report.independent)
        self.assertIn("fleet_mcp.errors",
                      {imp for imp in report.foreign_imports})

    def test_the_hub_depends_on_the_node_package(self):
        report = self.reports["hub"]
        self.assertFalse(report.independent)
        self.assertIn("fleet_mcp.node.connector", set(report.foreign_imports))

    def test_the_sidecar_is_import_independent_but_has_no_revoke_entrypoint(self):
        report = self.reports["sidecar"]
        self.assertTrue(report.independent)
        self.assertFalse(report.revocable)
        self.assertEqual(report.decision, wave16.BLOCKED)

    def test_revocability_is_a_file_fact_not_a_sentence(self):
        self.assertTrue(self.reports["hub"].revocable)
        self.assertTrue(self.reports["node"].revocable)


# --------------------------------------------------------------------------
# 9. what a real install still needs from the owner
# --------------------------------------------------------------------------

class OwnerInputsAreNamed(unittest.TestCase):

    def test_the_list_is_not_empty_and_names_the_unmeasurable_facts(self):
        inputs = wave16.owner_inputs()
        self.assertTrue(inputs)
        joined = " ".join(inputs)
        for token in ("plugin_id", "public_mcp_url", "oauth", "pat_anchor",
                      "device_ref", "attestations"):
            self.assertIn(token, joined)

    def test_no_input_is_a_secret_value(self):
        for line in wave16.owner_inputs():
            self.assertNotRegex(line, r"(?i)bearer\s|password\s*=|token\s*=")


# --------------------------------------------------------------------------
# 10. the reader is read-only
# --------------------------------------------------------------------------

class TheRepositoryReaderOnlyReads(unittest.TestCase):

    def test_the_only_git_verbs_used_are_reads(self):
        self.assertEqual(set(wave16.READ_ONLY_GIT),
                         {"ls-tree", "rev-parse", "cat-file"})

    def test_the_module_does_not_reach_for_a_shell(self):
        source = (HERE / "wave16_preflight.py").read_text(encoding="utf-8")
        self.assertNotIn("os.system", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("systemctl ", source)


if __name__ == "__main__":
    unittest.main()
