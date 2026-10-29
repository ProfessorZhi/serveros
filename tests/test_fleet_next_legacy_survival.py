"""#478: Next may be installed *beside* Legacy, and never *over* it.

The Owner's dual-track rule is one sentence of policy and a pile of ways to get
it wrong. This file pins the ones that can be decided offline, against fakes:

*   **Placement.** Two tracks that share a plugin backend ID, an MCP endpoint, a
    port, a unix socket, an installation/state/lock root, or a unit name are not
    two tracks. Every shared resource is reported at once as
    `BLOCKED_LEGACY_COLLISION` — never silently re-routed, never renamed aside.
*   **Lifecycle.** Versions and rollback are per-track: upgrading Next records
    what Next would return to, and rolling back restores exactly that placement.
    A rollback that names the Legacy unit is refused, not "redirected".
*   **Startup.** A Next start is refused when Legacy's observed identity is not
    the recorded one, and it is refused when the plan collides. Next being
    unreachable is *not* a refusal and *not* a success: its facts are `UNKNOWN`.
*   **Shadow.** A shadow call may read a fact it was granted, and may not call
    create/send/stop/restart at all — the refusal happens before the gateway is
    touched, which the fakes prove by recording every call.
*   **Identity.** A run/delivery/session id is scoped to
    `(track, fleet_instance_id, boot_id)`. A same-named id from another instance,
    another boot, or the other track is a refusal, not a hit; a delivery id
    offered as a run id is `ID_KIND_MISMATCH`, not a lookup miss.
*   **Survival.** This is the part that cannot be proved by scanning strings.
    `FakeLegacyGateway` serves `get_session` / `get_run` / `send`; the Next
    lifecycle (install, fail, upgrade, roll back) runs around it; and the
    Legacy answers, handler object and call log must be *identical* before and
    after each step. `test_the_survival_harness_detects_a_real_drift` is the
    positive control: the same comparison must go red when the fake is perturbed,
    so a green result here means "nothing moved", not "the test cannot fail".

Everything is offline and in-process. No service, port, socket, OAuth, Creator,
device, model or network is touched, and nothing here is a deployment command.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-next-legacy-safety"


def _load(name: str, path: Path):
    """Load by path, asking `sys.modules` first (see tests/test_loader_identity.py)."""
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


safety = _load("fleet_next_legacy_safety", PACKAGE / "legacy_safety.py")


# --------------------------------------------------------------------------
# fakes — the two tracks, as objects that only this file can mutate
# --------------------------------------------------------------------------

class FakeLegacyGateway:
    """The Legacy controller's fact surface, served from a fixed table.

    `handler` is a plain attribute so a test can assert it is the *same object*
    after a whole Next lifecycle — a rewrite would show up as a different
    function, and a monkeypatch would show up as a different attribute.
    """

    def __init__(self, identity, facts, *, authorized=True):
        self.identity = identity
        self._facts = {k: dict(v) for k, v in facts.items()}
        self.authorized = authorized
        self.calls: list[str] = []
        self.handler = self._answer

    def _answer(self, verb):
        if verb not in self._facts:
            raise KeyError(verb)
        if verb == "send" and not self.authorized:
            raise PermissionError("NOT_AUTHORIZED")
        return dict(self._facts[verb])

    def call(self, verb):
        self.calls.append(verb)
        return dict(self.handler(verb))


class FakeNextGateway:
    """A Next installation that can be installed, broken, upgraded and rolled back.

    It owns its own state and never receives a reference to the Legacy gateway,
    so "Next did not disturb Legacy" is enforced by the construction of the
    fixture as well as by the assertions.
    """

    def __init__(self, placement, identity=None):
        self.placement = placement
        self.identity = identity if identity is not None else next_identity()
        self.state = "ABSENT"
        self.installed_version = ""
        self.log: list[str] = []

    def install(self):
        self.state = "UP"
        self.installed_version = self.placement.version
        self.log.append("install")
        return self

    def fail(self):
        self.state = "FAILED"
        self.log.append("fail")
        return self

    def upgrade(self, placement):
        self.placement = safety.upgrade(
            self.placement, version=placement.version,
            artifact_digest=placement.artifact_digest)
        self.installed_version = placement.version
        self.log.append("upgrade")
        return self

    def rollback(self):
        decision = safety.rollback(self.placement)
        assert decision.decision == safety.OK, decision.code
        self.placement = decision.placement
        self.installed_version = self.placement.version
        self.log.append("rollback")
        return self

    def call(self, verb):
        self.log.append(verb)
        if self.state != "UP":
            raise ConnectionError("NOT_UP")
        return {"status": "UNKNOWN", "verb": verb}


def legacy_placement(**over):
    base = dict(
        track=safety.LEGACY,
        backend_plugin_id="plugin_legacy_0_1_0",
        mcp_url="https://legacy.example.invalid/mcp",
        listen_port=8101,
        socket_path="/run/serveros/legacy-fleet.sock",
        install_root="/opt/serveros/fleet-legacy",
        state_root="/var/lib/serveros/fleet-legacy",
        lock_root="/var/lock/serveros/fleet-legacy",
        unit="serveros-fleet-mcp.service",
        version="0.1.0",
        artifact_digest="sha256:" + "a" * 64,
    )
    base.update(over)
    return safety.Placement(**base)


def next_placement(**over):
    base = dict(
        track=safety.NEXT,
        backend_plugin_id="plugin_next_0_2_0",
        mcp_url="https://next.example.invalid/mcp",
        listen_port=8102,
        socket_path="/run/serveros/fleet-next.sock",
        install_root="/opt/serveros/fleet-next",
        state_root="/var/lib/serveros/fleet-next",
        lock_root="/var/lock/serveros/fleet-next",
        unit="serveros-fleet-next-mcp.service",
        version="0.2.0",
        artifact_digest="sha256:" + "b" * 64,
    )
    base.update(over)
    return safety.Placement(**base)


def legacy_identity(**over):
    base = dict(track=safety.LEGACY, fleet_instance_id="instance-legacy-1",
                route_id="route-legacy-a", boot_id="boot-legacy-7",
                schema_version="serveros/fleet-legacy.v1",
                source_version="0.1.0")
    base.update(over)
    return safety.Identity(**base)


def next_identity(**over):
    base = dict(track=safety.NEXT, fleet_instance_id="instance-next-1",
                route_id="route-next-a", boot_id="boot-next-1",
                schema_version="serveros/fleet-next.v1",
                source_version="0.2.0")
    base.update(over)
    return safety.Identity(**base)


LEGACY_FACTS = {
    "get_session": {"sessionId": "legacy-s1", "project": "p1",
                    "state": "MANAGED_IDLE"},
    "get_run": {"runId": "legacy-r1", "status": "COMPLETED",
                "outcome": "EVIDENCE_READ"},
    "send": {"deliveryId": "legacy-d1", "queued": True,
             "queuedMessageUuid": "legacy-q1", "source": "delivery"},
}
LEGACY_VERBS = ("get_session", "get_run", "send")


def fact(track, kind, fact_id, status, **over):
    base = dict(track=track,
                instance_id="instance-legacy-1" if track == safety.LEGACY
                else "instance-next-1",
                route_id="route-legacy-a" if track == safety.LEGACY
                else "route-next-a",
                boot_id="boot-legacy-7" if track == safety.LEGACY
                else "boot-next-1",
                kind=kind, fact_id=fact_id, payload={"status": status})
    base.update(over)
    return safety.Fact(**base)


def ref(track, kind, fact_id, **over):
    base = dict(track=track,
                instance_id="instance-legacy-1" if track == safety.LEGACY
                else "instance-next-1",
                boot_id="boot-legacy-7" if track == safety.LEGACY
                else "boot-next-1",
                kind=kind, fact_id=fact_id)
    base.update(over)
    return safety.Ref(**base)


# --------------------------------------------------------------------------
# A. placement: two tracks, or one track wearing two names
# --------------------------------------------------------------------------

def test_a_separate_pair_is_accepted():
    plan = safety.install_plan(legacy=legacy_placement(), next=next_placement())
    assert plan.decision == safety.OK
    assert plan.code == safety.OK
    assert plan.collisions == ()


def test_the_accepted_plan_echoes_the_legacy_placement_untouched():
    """Not a copy that could be edited: the same object comes back."""
    legacy = legacy_placement()
    plan = safety.install_plan(legacy=legacy, next=next_placement())
    assert plan.legacy is legacy
    assert plan.legacy == legacy_placement()


def test_a_shared_plugin_backend_id_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(backend_plugin_id="plugin_legacy_0_1_0"))
    assert plan.decision == safety.BLOCKED
    assert plan.code == safety.BLOCKED_LEGACY_COLLISION
    assert [c.resource for c in plan.collisions] == ["plugin_id"]


def test_a_shared_mcp_endpoint_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(mcp_url="https://legacy.example.invalid/mcp"))
    assert plan.code == safety.BLOCKED_LEGACY_COLLISION
    assert "mcp_endpoint" in [c.resource for c in plan.collisions]


def test_the_same_host_and_port_under_another_path_is_a_port_collision():
    """A different path on Legacy's host:port is still Legacy's listener."""
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(mcp_url="https://legacy.example.invalid/other/mcp"))
    resources = [c.resource for c in plan.collisions]
    assert "port" in resources
    assert "mcp_endpoint" not in resources


def test_a_shared_listen_port_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(listen_port=8101, mcp_url="https://next.example.invalid/mcp"))
    assert "port" in [c.resource for c in plan.collisions]


def test_a_shared_unix_socket_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(socket_path="/run/serveros/legacy-fleet.sock"))
    assert "socket" in [c.resource for c in plan.collisions]


def test_two_socketless_placements_do_not_collide_on_sockets():
    plan = safety.install_plan(
        legacy=legacy_placement(socket_path=None),
        next=next_placement(socket_path=None))
    assert plan.decision == safety.OK


def test_an_identical_install_root_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(install_root="/opt/serveros/fleet-legacy"))
    assert "install_root" in [c.resource for c in plan.collisions]


def test_a_nested_install_root_is_a_collision_not_a_subdirectory():
    """`/opt/x/next` inside `/opt/x` is the legacy tree, whatever it is called."""
    plan = safety.install_plan(
        legacy=legacy_placement(install_root="/opt/serveros/fleet"),
        next=next_placement(install_root="/opt/serveros/fleet/next"))
    assert "install_root" in [c.resource for c in plan.collisions]


def test_a_sibling_prefix_is_not_a_nested_root():
    """`/opt/fleet-next` is not inside `/opt/fleet` — the boundary is a slash."""
    plan = safety.install_plan(
        legacy=legacy_placement(install_root="/opt/fleet"),
        next=next_placement(install_root="/opt/fleet-next"))
    assert "install_root" not in [c.resource for c in plan.collisions]


def test_a_shared_state_root_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(state_root="/var/lib/serveros/fleet-legacy/next"))
    assert "state_root" in [c.resource for c in plan.collisions]


def test_a_shared_lock_root_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(lock_root="/var/lock/serveros/fleet-legacy"))
    assert "lock_root" in [c.resource for c in plan.collisions]


def test_a_shared_unit_name_is_a_collision():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(unit="serveros-fleet-mcp.service"))
    assert "unit" in [c.resource for c in plan.collisions]


def test_every_collision_is_reported_not_just_the_first():
    """An operator who fixes one shared field must not be sent back three times."""
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(
            backend_plugin_id="plugin_legacy_0_1_0",
            mcp_url="https://legacy.example.invalid/mcp",
            listen_port=8101,
            socket_path="/run/serveros/legacy-fleet.sock",
            install_root="/opt/serveros/fleet-legacy",
            state_root="/var/lib/serveros/fleet-legacy",
            lock_root="/var/lock/serveros/fleet-legacy",
            unit="serveros-fleet-mcp.service"))
    assert set(c.resource for c in plan.collisions) == set(
        safety.COLLISION_RESOURCES)


def test_a_collision_is_always_blocked_and_never_renamed_aside():
    """No code path returns a substitute; the only answers are OK and BLOCKED."""
    plan = safety.install_plan(
        legacy=legacy_placement(), next=next_placement(listen_port=8101))
    assert plan.decision == safety.BLOCKED
    assert plan.next == next_placement(listen_port=8101)


def test_an_incomplete_placement_is_refused_before_any_comparison():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(install_root="", mcp_url=""))
    assert plan.decision == safety.BLOCKED
    assert plan.code == safety.BLOCKED_PLACEMENT_INCOMPLETE
    assert set(plan.incomplete) == {"install_root", "mcp_url"}


def test_the_tracks_cannot_be_swapped():
    plan = safety.install_plan(
        legacy=legacy_placement(track=safety.NEXT),
        next=next_placement(track=safety.LEGACY))
    assert plan.decision == safety.BLOCKED
    assert plan.code == safety.BLOCKED_PLACEMENT_INCOMPLETE


def test_a_relative_root_is_incomplete_not_resolved_against_the_cwd():
    plan = safety.install_plan(
        legacy=legacy_placement(),
        next=next_placement(state_root="var/lib/next"))
    assert plan.code == safety.BLOCKED_PLACEMENT_INCOMPLETE
    assert "state_root" in plan.incomplete


def test_equal_versions_are_not_a_collision():
    """The two tracks version independently; equal numbers are allowed."""
    plan = safety.install_plan(
        legacy=legacy_placement(version="0.2.0"),
        next=next_placement(version="0.2.0"))
    assert plan.decision == safety.OK


# --------------------------------------------------------------------------
# B. version and rollback are per-track
# --------------------------------------------------------------------------

def test_upgrade_records_the_previous_placement_as_the_rollback_target():
    before = next_placement(version="0.2.0",
                            artifact_digest="sha256:" + "b" * 64)
    after = safety.upgrade(before, version="0.2.1",
                           artifact_digest="sha256:" + "c" * 64)
    assert after.version == "0.2.1"
    assert after.rollback_version == "0.2.0"
    assert after.rollback_digest == "sha256:" + "b" * 64


def test_rollback_restores_exactly_the_pre_upgrade_placement():
    before = next_placement()
    after = safety.upgrade(before, version="0.2.1",
                           artifact_digest="sha256:" + "c" * 64)
    decision = safety.rollback(after)
    assert decision.decision == safety.OK
    assert decision.placement == before


def test_a_rollback_must_carry_a_digest():
    before = next_placement()
    after = safety.upgrade(before, version="0.2.1",
                           artifact_digest="sha256:" + "c" * 64)
    decision = safety.rollback(
        safety.Placement(**{**after.placement.__dict__, "rollback_digest": ""}))
    assert decision.decision == safety.BLOCKED
    assert decision.code == safety.ROLLBACK_WITHOUT_DIGEST


def test_a_rollback_cannot_target_the_legacy_track():
    """Rolling the *Next* track back must never be a way to touch Legacy."""
    decision = safety.rollback(
        safety.Placement(**{**legacy_placement().__dict__,
                            "rollback_version": "0.0.9",
                            "rollback_digest": "sha256:" + "d" * 64}))
    assert decision.decision == safety.BLOCKED
    assert decision.code == safety.ROLLBACK_CROSSES_TRACKS


def test_a_rollback_with_no_recorded_target_is_refused():
    decision = safety.rollback(next_placement())
    assert decision.decision == safety.BLOCKED
    assert decision.code == safety.BLOCKED_PLACEMENT_INCOMPLETE


def test_a_downgrade_is_not_an_upgrade():
    decision = safety.upgrade(next_placement(version="0.2.1"),
                              version="0.2.0",
                              artifact_digest="sha256:" + "e" * 64)
    assert decision.decision == safety.BLOCKED
    assert decision.code == safety.VERSION_NOT_ADVANCING


def test_upgrading_next_does_not_change_the_legacy_placement():
    legacy = legacy_placement()
    upgraded = safety.upgrade(next_placement(), version="0.2.1",
                              artifact_digest="sha256:" + "c" * 64)
    assert upgraded.track == safety.NEXT
    assert legacy == legacy_placement()
    assert safety.install_plan(legacy=legacy, next=upgraded).decision == safety.OK


# --------------------------------------------------------------------------
# C. startup preflight
# --------------------------------------------------------------------------

def _clean_plan():
    return safety.install_plan(legacy=legacy_placement(), next=next_placement())


def test_a_clean_plan_with_a_matching_legacy_identity_may_start():
    result = safety.startup_preflight(
        recorded=legacy_identity(), observed=legacy_identity(),
        plan=_clean_plan(), next_reachable=True)
    assert result.decision == safety.OK
    assert result.legacy_usable is True
    assert result.next_facts == "OBSERVED"


def test_an_unreachable_next_is_unknown_and_not_a_refusal():
    """Next down is Next's problem; it is not evidence about Legacy."""
    result = safety.startup_preflight(
        recorded=legacy_identity(), observed=legacy_identity(),
        plan=_clean_plan(), next_reachable=False)
    assert result.decision == safety.OK
    assert result.legacy_usable is True
    assert result.next_facts == "UNKNOWN"


def test_a_colliding_plan_blocks_the_start():
    plan = safety.install_plan(
        legacy=legacy_placement(), next=next_placement(listen_port=8101))
    result = safety.startup_preflight(
        recorded=legacy_identity(), observed=legacy_identity(),
        plan=plan, next_reachable=True)
    assert result.decision == safety.BLOCKED
    assert result.code == safety.BLOCKED_LEGACY_COLLISION


def test_a_changed_legacy_identity_blocks_the_start():
    result = safety.startup_preflight(
        recorded=legacy_identity(),
        observed=legacy_identity(boot_id="boot-legacy-8"),
        plan=_clean_plan(), next_reachable=True)
    assert result.decision == safety.BLOCKED
    assert result.code == safety.LEGACY_IDENTITY_DRIFT
    assert "boot_id" in result.drift


def test_a_changed_legacy_route_blocks_even_when_next_is_clean():
    result = safety.startup_preflight(
        recorded=legacy_identity(),
        observed=legacy_identity(route_id="route-legacy-b"),
        plan=_clean_plan(), next_reachable=True)
    assert result.code == safety.LEGACY_IDENTITY_DRIFT
    assert "route_id" in result.drift


def test_drift_is_reported_field_by_field():
    result = safety.startup_preflight(
        recorded=legacy_identity(),
        observed=legacy_identity(route_id="route-legacy-b",
                                 schema_version="serveros/fleet-legacy.v2"),
        plan=_clean_plan(), next_reachable=False)
    assert set(result.drift) == {"route_id", "schema_version"}


def test_the_preflight_does_not_mutate_its_inputs():
    recorded, observed, plan = legacy_identity(), legacy_identity(), _clean_plan()
    before = (recorded, observed, plan)
    safety.startup_preflight(recorded=recorded, observed=observed, plan=plan,
                             next_reachable=True)
    assert (recorded, observed, plan) == before


# --------------------------------------------------------------------------
# D. shadow: read only, and only what was granted
# --------------------------------------------------------------------------

def test_a_granted_read_reaches_the_next_gateway():
    gateway = FakeNextGateway(next_placement()).install()
    result = safety.shadow_call("get_session", granted={"get_session"},
                                gateway=gateway)
    assert result.allowed is True
    assert result.gateway_called is True
    assert result.state == "OBSERVED"


def test_create_is_refused_in_shadow_and_the_gateway_is_never_called():
    gateway = FakeNextGateway(next_placement()).install()
    result = safety.shadow_call("create", granted={"create"}, gateway=gateway)
    assert result.allowed is False
    assert result.code == safety.SHADOW_WRITE_FORBIDDEN
    assert result.gateway_called is False
    assert gateway.log == ["install"]


def test_every_write_verb_is_refused_in_shadow():
    gateway = FakeNextGateway(next_placement()).install()
    for verb in ("create", "send", "stop", "restart", "wake", "manager_wake",
                 "session_create", "session_send", "session_interrupt"):
        result = safety.shadow_call(verb, granted={verb}, gateway=gateway)
        assert result.allowed is False, verb
        assert result.code == safety.SHADOW_WRITE_FORBIDDEN, verb
    assert gateway.log == ["install"]


def test_the_shadow_write_code_is_the_one_the_parallel_plugin_preflight_uses():
    """One code, two modules: #480 emits this string, and it must not fork."""
    assert safety.SHADOW_WRITE_FORBIDDEN == "NEXT_SHADOW_WRITE_FORBIDDEN"


def test_an_ungranted_read_is_refused_before_the_gateway():
    gateway = FakeNextGateway(next_placement()).install()
    result = safety.shadow_call("get_run", granted={"get_session"},
                                gateway=gateway)
    assert result.allowed is False
    assert result.code == safety.SHADOW_NOT_GRANTED
    assert result.gateway_called is False


def test_an_unknown_verb_fails_closed():
    gateway = FakeNextGateway(next_placement())
    result = safety.shadow_call("frobnicate", granted={"frobnicate"},
                                gateway=gateway)
    assert result.allowed is False
    assert result.code == safety.SHADOW_VERB_UNKNOWN
    assert result.gateway_called is False


def test_an_unreachable_next_yields_unknown_and_never_success():
    gateway = FakeNextGateway(next_placement())          # ABSENT, not installed
    result = safety.shadow_call("get_session", granted={"get_session"},
                                gateway=gateway)
    assert result.allowed is True
    assert result.state == "UNKNOWN"
    assert result.code == safety.SHADOW_FACT_UNKNOWN
    assert result.fact is None


def test_a_shadow_fact_carries_the_identity_that_produced_it():
    """A non-default boot id, so the module cannot be holding a constant."""
    gateway = FakeNextGateway(next_placement(),
                              identity=next_identity(boot_id="boot-next-9")).install()
    result = safety.shadow_call("get_session", granted={"get_session"},
                                gateway=gateway)
    assert result.track == safety.NEXT
    assert result.instance_id == "instance-next-1"
    assert result.boot_id == "boot-next-9"
    assert result.fact.boot_id == "boot-next-9"


def test_the_write_set_and_the_read_set_do_not_overlap():
    assert safety.WRITE_VERBS & safety.READ_ONLY_FACT_VERBS == frozenset()


# --------------------------------------------------------------------------
# E. identity: an id belongs to one instance, one boot, one track
# --------------------------------------------------------------------------

def test_a_run_from_the_owning_boot_resolves():
    index = safety.index_facts([fact(safety.LEGACY, "run", "r1", "COMPLETED")])
    resolution = safety.resolve(ref(safety.LEGACY, "run", "r1"),
                                owner=legacy_identity(), index=index)
    assert resolution.code == "HIT"
    assert resolution.fact.payload["status"] == "COMPLETED"


def test_a_run_from_another_instance_is_refused():
    index = safety.index_facts([fact(safety.LEGACY, "run", "r1", "COMPLETED")])
    resolution = safety.resolve(
        ref(safety.LEGACY, "run", "r1", instance_id="instance-legacy-2"),
        owner=legacy_identity(), index=index)
    assert resolution.code == "FOREIGN_INSTANCE"
    assert resolution.fact is None


def test_a_run_from_another_boot_is_refused():
    index = safety.index_facts([fact(safety.LEGACY, "run", "r1", "COMPLETED")])
    resolution = safety.resolve(
        ref(safety.LEGACY, "run", "r1", boot_id="boot-legacy-6"),
        owner=legacy_identity(), index=index)
    assert resolution.code == "STALE_BOOT"


def test_a_legacy_ref_cannot_be_answered_from_the_next_track():
    index = safety.index_facts([fact(safety.NEXT, "run", "r1", "COMPLETED")])
    resolution = safety.resolve(ref(safety.NEXT, "run", "r1"),
                                owner=legacy_identity(), index=index)
    assert resolution.code == "CROSS_TRACK"


def test_a_delivery_id_offered_as_a_run_id_is_a_kind_mismatch():
    index = safety.index_facts(
        [fact(safety.LEGACY, "delivery", "d1", "QUEUED")])
    resolution = safety.resolve(ref(safety.LEGACY, "run", "d1"),
                                owner=legacy_identity(), index=index)
    assert resolution.code == "ID_KIND_MISMATCH"
    assert resolution.fact.kind == "delivery"


def test_a_missing_id_is_not_found_and_not_unknown_success():
    index = safety.index_facts([fact(safety.LEGACY, "run", "r1", "COMPLETED")])
    resolution = safety.resolve(ref(safety.LEGACY, "run", "r2"),
                                owner=legacy_identity(), index=index)
    assert resolution.code == "NOT_FOUND"
    assert resolution.fact is None


def test_the_same_id_on_two_tracks_is_two_objects():
    assert safety.same_object(ref(safety.LEGACY, "run", "r1"),
                              ref(safety.NEXT, "run", "r1")) is False


def test_the_same_id_on_two_boots_is_two_objects():
    assert safety.same_object(
        ref(safety.LEGACY, "run", "r1"),
        ref(safety.LEGACY, "run", "r1", boot_id="boot-legacy-6")) is False


def test_an_identical_ref_is_the_same_object():
    assert safety.same_object(ref(safety.LEGACY, "run", "r1"),
                              ref(safety.LEGACY, "run", "r1")) is True


def test_a_next_completion_does_not_complete_a_legacy_run():
    """The one cross-track claim the Owner forbids by name."""
    facts = [fact(safety.LEGACY, "run", "r1", "RUNNING"),
             fact(safety.NEXT, "run", "r1", "COMPLETED")]
    assert safety.run_state(facts, ref(safety.LEGACY, "run", "r1")) == "RUNNING"
    assert safety.run_state(facts, ref(safety.NEXT, "run", "r1")) == "COMPLETED"


def test_a_run_only_next_knows_is_not_a_legacy_run():
    facts = [fact(safety.NEXT, "run", "r9", "COMPLETED")]
    assert safety.run_state(facts, ref(safety.LEGACY, "run", "r9")) == "NOT_FOUND"


def test_a_status_the_fact_does_not_carry_is_unknown_not_failed():
    facts = [safety.Fact(track=safety.LEGACY, instance_id="instance-legacy-1",
                         route_id="route-legacy-a", boot_id="boot-legacy-7",
                         kind="run", fact_id="r1", payload={})]
    assert safety.run_state(facts, ref(safety.LEGACY, "run", "r1")) == "UNKNOWN"


# --------------------------------------------------------------------------
# F. survival: the Legacy controller keeps answering, identically
# --------------------------------------------------------------------------

def _lifecycle():
    """Each step is a *fresh* Next installation doing exactly one thing."""
    upgraded = next_placement(version="0.2.1",
                              artifact_digest="sha256:" + "c" * 64)
    return [
        ("install", lambda: FakeNextGateway(next_placement()).install()),
        ("fail", lambda: FakeNextGateway(next_placement()).install().fail()),
        ("upgrade",
         lambda: FakeNextGateway(next_placement()).install().upgrade(upgraded)),
        ("rollback",
         lambda: FakeNextGateway(next_placement()).install()
         .upgrade(upgraded).rollback()),
    ]


def test_each_lifecycle_step_actually_happened():
    """A no-op step would make every `SAME` below vacuous.

    So the fakes report what they did, and this asserts the doing.
    """
    upgraded = next_placement(version="0.2.1",
                              artifact_digest="sha256:" + "c" * 64)
    seen: list[FakeNextGateway] = []
    steps = [
        ("install", lambda: seen.append(
            FakeNextGateway(next_placement()).install())),
        ("fail", lambda: seen.append(
            FakeNextGateway(next_placement()).install().fail())),
        ("upgrade", lambda: seen.append(
            FakeNextGateway(next_placement()).install().upgrade(upgraded))),
        ("rollback", lambda: seen.append(
            FakeNextGateway(next_placement()).install()
            .upgrade(upgraded).rollback())),
    ]

    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    report = safety.run_lifecycle(gateway, LEGACY_VERBS, steps)

    assert [g.log[-1] for g in seen] == ["install", "fail", "upgrade",
                                         "rollback"]
    assert seen[3].placement.version == "0.2.0"      # rolled back for real
    assert all(survival.decision == "SAME" for _, survival in report)


def test_the_lifecycle_never_asks_the_legacy_gateway_for_anything_else():
    """Every call the harness makes on Legacy is one of the read verbs."""
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert set(gateway.calls) <= set(LEGACY_VERBS)


def test_the_legacy_answers_are_identical_across_the_whole_next_lifecycle():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    report = safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert [name for name, _ in report] == ["install", "fail", "upgrade",
                                            "rollback"]
    for name, survival in report:
        assert survival.decision == "SAME", (name, survival.drifted)


def test_the_legacy_handler_object_is_untouched():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    handler = gateway.handler
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert gateway.handler is handler


def test_the_legacy_identity_is_untouched():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    before = gateway.identity
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert gateway.identity is before


def test_the_legacy_controller_still_serves_get_session_get_run_and_send():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert gateway.call("get_session")["sessionId"] == "legacy-s1"
    assert gateway.call("get_run")["status"] == "COMPLETED"
    assert gateway.call("send")["deliveryId"] == "legacy-d1"


def test_an_authorized_send_still_goes_out_after_the_lifecycle():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS, authorized=True)
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    assert gateway.call("send")["queued"] is True


def test_an_unauthorized_send_is_still_refused_after_the_lifecycle():
    """Next must not have widened Legacy's permissions either."""
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS, authorized=False)
    safety.run_lifecycle(gateway, LEGACY_VERBS, _lifecycle())
    try:
        gateway.call("send")
    except PermissionError as exc:
        assert str(exc) == "NOT_AUTHORIZED"
    else:                                          # pragma: no cover
        raise AssertionError("an unauthorized send was not refused")


def test_the_survival_harness_detects_a_real_drift():
    """The positive control: the comparison must be able to say no.

    Without this, `SAME` everywhere above could mean "the harness never looks".
    """
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    before = safety.snapshot(gateway, LEGACY_VERBS)

    original = gateway.handler

    def rewritten(verb):
        if verb == "get_run":
            return {"runId": "legacy-r1", "status": "RUNNING"}
        return original(verb)

    gateway.handler = rewritten
    after = safety.snapshot(gateway, LEGACY_VERBS)

    survival = safety.compare_survival(before, after)
    assert survival.decision == "DRIFTED"
    assert "get_run" in survival.drifted


def test_a_vanished_verb_is_drift_not_an_empty_answer():
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    before = safety.snapshot(gateway, LEGACY_VERBS)
    gateway._facts.pop("send")
    after = safety.snapshot(gateway, LEGACY_VERBS)
    survival = safety.compare_survival(before, after)
    assert survival.decision == "DRIFTED"
    assert "send" in survival.drifted


def test_the_survival_snapshot_never_contains_a_mutable_shared_payload():
    """A snapshot copied by reference would compare equal to itself forever."""
    gateway = FakeLegacyGateway(legacy_identity(), LEGACY_FACTS)
    before = safety.snapshot(gateway, LEGACY_VERBS)
    gateway._facts["get_run"]["status"] = "FAILED"
    after = safety.snapshot(gateway, LEGACY_VERBS)
    assert safety.compare_survival(before, after).decision == "DRIFTED"


# --------------------------------------------------------------------------
# G. scope: this package stays out of Legacy's files
# --------------------------------------------------------------------------

SOURCE = PACKAGE / "legacy_safety.py"


def test_the_package_imports_nothing_from_legacy():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "typing", "collections",
                        "posixpath", "urllib", "re", "enum"}
    assert not (imported & {"mcp", "fleet", "fleet_mcp", "runtime", "workengine"})


def test_the_package_cannot_touch_the_filesystem_or_a_socket():
    """Structural, and only supporting evidence — see the survival tests above.

    A string scan cannot prove Legacy is unrewritten; it can prove this module
    has no way to try. The behavioural proof is section F.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    banned = {"open", "socket", "subprocess", "requests", "shutil", "os",
              "pathlib", "tempfile", "system", "popen", "remove", "unlink",
              "rename", "rmdir", "chmod", "chown", "monkeypatch"}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not ((names | attrs) & banned)


def test_the_package_names_no_legacy_installation_path():
    source = SOURCE.read_text(encoding="utf-8")
    for marker in ("/opt/serveros/fleet-legacy", "/etc/systemd/system",
                   "serveros-fleet-mcp.service", "/var/lib/serveros"):
        assert marker not in source


def test_the_package_does_not_write_the_coexistence_directory():
    assert "fleet-next-coexistence" not in SOURCE.read_text(encoding="utf-8")
