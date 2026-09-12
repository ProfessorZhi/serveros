#!/usr/bin/env python3
"""Tests for the ServerOS runtime layer: registry, health, probes, inventory,
hygiene.

These are hermetic. They build registries from documents, hosts from
dictionaries, and containers from literal label strings — no docker daemon, no
systemd, no network, no real filesystem beyond `tmp_path`.

Several of them are regression tests for defects found by RUNNING this code
against the live host rather than by reading it, and each names the incident:

  * the `com.docker.compose.project` label read as ownership, which put a
    protected parallel workstream into a reclaim bucket;
  * the `reason` pseudo-axis missing from the contract;
  * the journal diagnostic silently overwriting a real measurement, which
    discarded the only actionable signal on this host's endpoint.

The first one is the reason this file exists at all: it is the exact class of
error the whole architecture is built to make impossible, and it survived
review, type-checking and a live run before a person looked at the output and
asked why eight containers had the same owner.
"""
from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    """Load a module by path, reusing `sys.modules`.

    The reuse matters: `health.py` and `probes.py` both load `registry.py`, and
    loading it twice would give two distinct `RegistryError` classes, so an
    `except RegistryError` in a test would not catch the one the module raised.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_mod = _load("serveros_runtime_registry", "registry.py")
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")

Registry = registry_mod.Registry
RegistryInvalid = registry_mod.RegistryInvalid
UnknownResource = registry_mod.UnknownResource
Health = health_mod.Health
Observation = health_mod.Observation
HealthError = health_mod.HealthError
UnprovenancedObservation = health_mod.UnprovenancedObservation
HealthUndetermined = health_mod.HealthUndetermined
action_for = health_mod.action_for
Inventory = inventory_mod.Inventory
Hygiene = hygiene_mod.Hygiene


# ---------------------------------------------------------------------------
# Fixtures — documents and fakes, so nothing here touches the real host
# ---------------------------------------------------------------------------


def resource_document(*resources, protected_refs=None, defer_refs=None):
    doc = {
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": list(resources),
    }
    if protected_refs is not None:
        doc["protected_refs"] = protected_refs
    if defer_refs is not None:
        doc["defer_refs"] = defer_refs
    return doc


def a_resource(resource_id="thing", **overrides):
    record = {
        "id": resource_id,
        "kind": "container",
        "owner": {"type": "project", "ref": "project:litellm"},
        "scope": "user",
        "lifecycle": {"class": "persistent"},
        "desired": {"state": "running"},
        "protection": {"level": "protected"},
    }
    record.update(overrides)
    return record


class FakeHost:
    """An inventory host backed by dictionaries.

    Only the methods `Inventory` actually calls are implemented, and any read
    that was not configured returns a failure — so a test cannot accidentally
    pass because the fake silently answered something plausible.
    """

    def __init__(self, files=None, commands=None, usage=None, paths=None):
        self.files = files or {}
        self.commands = commands or {}
        self.usage = usage or {}
        self.paths = set(paths or ())

    def read(self, args, timeout):
        key = " ".join(args)
        for pattern, result in self.commands.items():
            if key.startswith(pattern):
                if isinstance(result, Exception):
                    raise result
                return result
        return 127, "", f"not configured: {key}"

    def read_text(self, path):
        return self.files.get(path)

    def statvfs(self, path):
        return self.usage.get(path)

    def exists(self, path):
        return path in self.paths


@pytest.fixture
def reg():
    return Registry(document=resource_document(a_resource()))


# ---------------------------------------------------------------------------
# §46 · Managed Runtime schema is closed and typed
# ---------------------------------------------------------------------------


def test_unknown_kind_is_rejected_not_coerced():
    """`service` is the word this schema exists to avoid."""
    with pytest.raises(RegistryInvalid) as excinfo:
        Registry(document=resource_document(a_resource(kind="service")))
    assert "service" in str(excinfo.value)
    # The message has to name the alternatives, because the whole point is
    # that "service" collapses six different lifetimes into one word.
    assert "container" in str(excinfo.value)


@pytest.mark.parametrize("field,value", [
    ("scope", "root"),
    ("lifecycle", {"class": "forever"}),
    ("desired", {"state": "maybe"}),
    ("protection", {"level": "kinda"}),
])
def test_every_vocabulary_is_closed(field, value):
    with pytest.raises(RegistryInvalid):
        Registry(document=resource_document(a_resource(**{field: value})))


def test_owner_must_be_a_typed_ref():
    """`{type, id}` is the identity contract; a bare string is not an owner."""
    with pytest.raises(RegistryInvalid):
        Registry(document=resource_document(
            a_resource(owner={"type": "project", "ref": "litellm"})
        ))


def test_owner_type_must_be_declared():
    with pytest.raises(RegistryInvalid):
        Registry(document=resource_document(
            a_resource(owner={"type": "whoever", "ref": "project:litellm"})
        ))


def test_duplicate_ids_are_rejected():
    """Two registrations for one id is how one of them silently wins."""
    doc = resource_document(a_resource("dup"), a_resource("dup"))
    with pytest.raises(RegistryInvalid, match="duplicate"):
        Registry(document=doc)


def test_a_registry_is_never_a_credential_store():
    with pytest.raises(RegistryInvalid, match="credential"):
        Registry(document=resource_document(
            a_resource(note={"api_key": "sk-not-a-real-key"})
        ))


def test_credential_check_descends_into_nested_structures():
    """A secret one level down is still a secret."""
    with pytest.raises(RegistryInvalid, match="credential"):
        Registry(document=resource_document(
            a_resource(probes=[{"id": "p", "kind": "http-health", "axis": "functional",
                                "headers": {"authorization_token": "x"}}])
        ))


def test_asking_for_an_unregistered_resource_is_an_error_not_a_default():
    """Managed means registered. A resource is never managed by being noticed."""
    r = Registry(document=resource_document(a_resource("known")))
    assert len(r) == 1
    with pytest.raises(UnknownResource, match="never by being noticed"):
        r.get("unknown")


def test_two_probes_on_one_axis_are_rejected():
    """Two probes answering one axis means one silently wins."""
    with pytest.raises(RegistryInvalid, match="axis"):
        Registry(document=resource_document(a_resource(probes=[
            {"id": "a", "kind": "container-state", "axis": "liveness", "container": "c"},
            {"id": "b", "kind": "container-state", "axis": "liveness", "container": "c"},
        ])))


# ---------------------------------------------------------------------------
# §46 · persistent and task-scoped are different lifetimes, and behave so
# ---------------------------------------------------------------------------


def test_persistent_protected_is_not_automatable():
    r = Registry(document=resource_document(a_resource("baseline")))
    resource = r.get("baseline")
    assert resource.lifecycle == "persistent"
    assert not resource.is_automatable
    assert any("protection" in reason for reason in resource.why_not_automatable())


def test_task_scoped_but_protected_is_still_not_automatable():
    """An active parallel lane is registered to be protected, not managed."""
    r = Registry(document=resource_document(
        a_resource("lane", lifecycle={"class": "task-scoped"},
                   owner={"type": "work-item", "ref": "work-item:LIT-0104"})
    ))
    assert not r.get("lane").is_automatable


def test_a_disposable_resource_is_a_recommendation_and_never_an_action():
    """This test used to assert the opposite, and the change is §10.

    The one combination the automatic path may reach used to be "unprotected,
    user-scoped, owned, desired-running" — and a disposable scratch container
    satisfied it. §10 requires `persistent` as well, plus an explicit
    `reconcile` policy, a budget and a functional probe, so a disposable
    resource is now *recommendation only*: §9's category, distinct from
    NEEDS_HUMAN.

    Recording the change here rather than deleting the test, because the old
    assertion is the evidence that the gate was wider than the brief allows.
    """
    r = Registry(document=resource_document(a_resource(
        "scratch", lifecycle={"class": "disposable"}, protection={"level": "disposable"},
    )))
    split = r.get("scratch").automatability()
    assert split["automatable"] is False
    assert split["permission"] == [], "nothing forbids it outright"
    assert split["recommendation_only"] is True
    assert any("lifecycle=disposable" in reason for reason in split["policy"])


def test_system_scope_is_declared_only_and_never_automatable():
    """ServerOS reports NEEDS_HUMAN rather than escalating."""
    r = Registry(document=resource_document(a_resource(
        "sys-thing", scope="system", lifecycle={"class": "disposable"},
        protection={"level": "disposable"},
    )))
    resource = r.get("sys-thing")
    assert not resource.is_automatable
    assert any("scope" in reason for reason in resource.why_not_automatable())


def test_unknown_owner_fails_safe():
    """No owner type `unknown` exists, so an unowned resource cannot be
    declared automatable at all — the schema refuses the shape rather than
    the policy refusing the action."""
    with pytest.raises(RegistryInvalid):
        Registry(document=resource_document(a_resource(
            owner={"type": "unknown", "ref": "unknown:whatever"},
            lifecycle={"class": "disposable"}, protection={"level": "disposable"},
        )))


# ---------------------------------------------------------------------------
# §46 · process alive != functional healthy
# ---------------------------------------------------------------------------


def ok(axis, state, *, evidence="host-measurement", observer=("probe", "p"), reason=None):
    return Observation(
        axis=axis, state=state,
        observed_by={"type": observer[0], "id": observer[1]},
        observed_at="2026-09-12T00:00:00Z",
        evidence={"type": evidence}, reason=reason,
    )


def unknown(axis, *, why="NOT_OBSERVED"):
    return health_mod.unknown(axis, why=why)


def build_health(*, liveness, transport, functional, readiness, desired="running",
                 diagnostic=None):
    return Health(
        liveness=liveness, transport=transport, functional=functional,
        readiness=readiness, desired=desired, diagnostic=diagnostic,
    )


def test_process_running_is_not_healthy():
    """The exact 2026-09-12 shape: up, connected, and nobody asked."""
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=ok("transport", "CONNECTED"),
        functional=unknown("functional"),
        readiness=unknown("readiness"),
    )
    assert h.state != "HEALTHY"
    assert h.state == "DEGRADED"


def test_transport_connected_is_not_functional_health():
    """The measured fact: the endpoint held an ESTABLISHED socket to its own
    relay host through the entire 54-minute outage, because it opens the
    socket before authorizing and keeps it while it waits."""
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=ok("transport", "CONNECTED"),
        functional=unknown("functional", why="DECLARED_SURFACE_ABSENT"),
        readiness=unknown("readiness"),
    )
    assert h.state == "DEGRADED"
    assert h.state != "HEALTHY"


def test_healthy_requires_the_functional_axis():
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=ok("transport", "CONNECTED"),
        functional=ok("functional", "OK"),
        readiness=ok("readiness", "READY"),
    )
    assert h.state == "HEALTHY"


def test_functionally_failed_outranks_a_healthy_process():
    """Ordered ahead of the liveness-derived states on purpose: this is the
    condition that would otherwise read as healthy."""
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=ok("transport", "CONNECTED"),
        functional=ok("functional", "FAILED"),
        readiness=unknown("readiness"),
    )
    assert h.state == "FUNCTIONALLY_FAILED"


def test_stopped_and_desired_stopped_is_not_a_fault():
    h = build_health(
        liveness=ok("liveness", "STOPPED"),
        transport=unknown("transport"), functional=unknown("functional"),
        readiness=unknown("readiness"), desired="stopped",
    )
    assert h.state == "STOPPED"
    assert action_for(None, h)["action"] == "NONE"


def test_unobserved_is_not_reported_as_healthy():
    h = build_health(
        liveness=unknown("liveness"), transport=unknown("transport"),
        functional=unknown("functional"), readiness=unknown("readiness"),
    )
    assert h.state == "UNKNOWN"
    assert not health_mod.action_for(None, h)["action"] == "NONE"


# ---------------------------------------------------------------------------
# §46 + totality · the rule table has no fallback
# ---------------------------------------------------------------------------


def test_rule_table_is_total_over_every_declared_combination():
    """Every combination of declared axis values must match exactly one rule.

    This is the safety property that replaces a lookup table. A partial table
    with a silent fallback is exactly how the fleet transport contract came to
    emit its own forbidden word, one layer down.
    """
    contract = registry_mod.contract()
    axes = contract["health_axes"]
    value_lists = []
    for axis in ("liveness", "transport", "functional", "readiness"):
        value_lists.append(list(axes[axis]["values"]))
    value_lists.append(["running", "stopped"])

    total = 0
    seen_states = set()
    for combo in itertools.product(*value_lists):
        liveness, transport, functional, readiness, desired = combo
        h = build_health(
            liveness=ok("liveness", liveness) if liveness != "UNKNOWN" else unknown("liveness"),
            transport=ok("transport", transport) if transport != "UNKNOWN" else unknown("transport"),
            functional=ok("functional", functional) if functional != "UNKNOWN" else unknown("functional"),
            readiness=ok("readiness", readiness) if readiness != "UNKNOWN" else unknown("readiness"),
            desired=desired,
        )
        state = h.state  # raises HealthUndetermined if uncovered
        assert state, combo
        seen_states.add(state)
        total += 1

    expected = 4 * 4 * 4 * 4 * 2
    assert total == expected == 512
    assert seen_states == {
        "UNAVAILABLE", "UNKNOWN", "STOPPED", "NOT_RUNNING", "NOT_READY",
        "FUNCTIONALLY_FAILED", "DEGRADED", "HEALTHY",
    }


def test_an_uncovered_combination_raises_rather_than_returning_a_nearby_word(monkeypatch):
    """A wrong answer that looks like an answer is worse than a crash."""
    original = registry_mod.contract()

    def broken():
        doc = dict(original)
        doc["derived_states"] = {"rules": [{"state": "HEALTHY", "when": {"liveness": "RUNNING"}}]}
        return doc

    monkeypatch.setattr(registry_mod, "contract", broken)
    h = build_health(
        liveness=ok("liveness", "STOPPED"), transport=unknown("transport"),
        functional=unknown("functional"), readiness=unknown("readiness"),
    )
    with pytest.raises(HealthUndetermined):
        h.state


# ---------------------------------------------------------------------------
# §9 · provenance, and the wall between a diagnostic and a contract
# ---------------------------------------------------------------------------


def test_a_resource_with_no_transport_probe_has_no_reason():
    """Measured on 2026-09-13 during the §53 acceptance run, on a HEALTHY
    resource: `reason: NOT_DECLARED`.

    It is the wrong field for that word. `NOT_DECLARED` says the axis is
    outside what the resource declares it can be asked — which the derived
    state rules already ignore, which is why the state came out HEALTHY while
    the reason said something was wrong. Two fields disagreeing about the same
    resource is how a reader learns to trust neither.
    """
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=unknown("transport", why="NOT_DECLARED"),
        functional=ok("functional", "OK"),
        readiness=unknown("readiness", why="NOT_DECLARED"),
    )
    assert h.state == "HEALTHY"
    assert h.reason is None
    assert h.reason_is_authoritative is False
    # The word is still on the axis it belongs to, so nothing was lost — the
    # distinction "never asked" vs "asked and got nothing" is intact.
    assert h.transport.reason == "NOT_DECLARED"
    assert h.readiness.reason == "NOT_DECLARED"


def test_a_reason_on_a_faulty_axis_still_surfaces():
    """The positive control. Without it the test above would pass against a
    `reason` that returned None unconditionally."""
    h = build_health(
        liveness=ok("liveness", "RUNNING"),
        transport=unknown("transport", why="NOT_DECLARED"),
        functional=ok("functional", "FAILED", reason="CHANNEL_DISCONNECTED"),
        readiness=unknown("readiness", why="NOT_DECLARED"),
    )
    assert h.reason == "CHANNEL_DISCONNECTED"


def test_an_observation_without_an_observer_is_refused_at_construction():
    with pytest.raises(UnprovenancedObservation):
        Observation(axis="liveness", state="RUNNING", observed_by={},
                    observed_at=None, evidence={"type": "host-measurement"})


def test_an_observation_without_provenance_must_be_reported_unknown_instead():
    """The refusal is the point: an invented observer is how an unmeasured
    axis becomes indistinguishable from a measured one."""
    obs = health_mod.unknown("liveness", why="NOT_OBSERVED")
    assert obs.state == "UNKNOWN"
    assert obs.observed_by["type"] == "none"


def test_a_journal_line_cannot_carry_a_non_unknown_state():
    with pytest.raises(HealthError, match="cannot carry"):
        Observation(
            axis="functional", state="FAILED",
            observed_by={"type": "probe", "id": "journal"},
            observed_at="2026-09-12T00:00:00Z",
            evidence={"type": "journal-diagnostic"},
        )


def test_an_unknown_reason_is_refused():
    with pytest.raises(HealthError, match="unknown reason"):
        Observation(
            axis="transport", state="DISCONNECTED",
            observed_by={"type": "probe", "id": "p"}, observed_at=None,
            evidence={"type": "host-measurement"}, reason="MADE_UP_REASON",
        )


def test_provenance_survives_into_the_rendered_record():
    h = build_health(
        liveness=ok("liveness", "RUNNING", observer=("probe", "rdc-liveness")),
        transport=ok("transport", "DISCONNECTED", observer=("probe", "rdc-relay-socket")),
        functional=unknown("functional"), readiness=unknown("readiness"),
    )
    shown = h.show()
    assert shown["axes"]["liveness"]["observed_by"]["id"] == "rdc-liveness"
    assert shown["axes"]["liveness"]["observed_at"] == "2026-09-12T00:00:00Z"
    assert shown["axes"]["functional"]["state"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# §11 · the RDC failure model, and §19's no-infinite-restart-loop
# ---------------------------------------------------------------------------


AUTH_DIAGNOSTIC = {
    "reason": "AUTH_FAILURE_SUSPECTED",
    "observed_by": {"type": "diagnostic", "id": "rdc-auth-diagnostic"},
    "observed_at": "2026-09-12T00:00:00Z",
    "evidence": {"type": "journal-diagnostic"},
    "authoritative": False,
}


def rdc_health(diagnostic=AUTH_DIAGNOSTIC):
    return build_health(
        liveness=ok("liveness", "RUNNING", observer=("probe", "rdc-liveness")),
        transport=ok("transport", "DISCONNECTED", observer=("probe", "rdc-relay-socket")),
        functional=unknown("functional", why="DECLARED_SURFACE_ABSENT"),
        readiness=unknown("readiness"),
        diagnostic=diagnostic,
    )


def rdc_resource():
    return Registry(document=resource_document(
        a_resource("remote-device-endpoint", kind="transport-endpoint",
                   owner={"type": "serveros", "ref": "device:server-beijing"})
    )).get("remote-device-endpoint")


def test_a_diagnostic_never_overwrites_a_measurement():
    """REGRESSION. The first implementation merged the journal diagnostic into
    the first axis lacking a reason. Because `functional` already carried
    DECLARED_SURFACE_ABSENT, the actionable AUTH_FAILURE_SUSPECTED was
    silently discarded and the action came back as a generic protection block
    instead of naming the auth failure."""
    h = rdc_health()
    assert h.reason == "DECLARED_SURFACE_ABSENT"
    assert h.diagnostic_reason == "AUTH_FAILURE_SUSPECTED"
    assert h.reason != h.diagnostic_reason


def test_a_diagnostic_still_routes_the_action_to_a_person():
    """Dropping it would have discarded the only actionable signal available."""
    decision = action_for(rdc_resource(), rdc_health())
    assert decision["action"] == "NEEDS_HUMAN"
    assert decision["reason"] == "AUTH_FAILURE_SUSPECTED"
    assert decision["held_on"] == "diagnostic"
    assert decision["authoritative"] is False


def test_restarting_is_named_as_the_wrong_response():
    """`Restart=always` supervises a process; it cannot renew a session. The
    endpoint restarted 5 times in ~2 hours as each device code expired, and
    each restart issued a new code nobody could authorize."""
    decision = action_for(rdc_resource(), rdc_health())
    assert "restarting_is_the_wrong_response" in decision
    assert "supervises a process" in decision["because"]


def test_the_interval_is_not_the_wrong_number():
    """NOT a repeat every N minutes. A consumed refresh token is not a
    transient fault and retrying it is the loop, not the fix."""
    decision = action_for(rdc_resource(), rdc_health())
    assert "retry" not in decision
    assert "backoff" not in decision


def test_a_protected_resource_is_never_selected_for_reconcile():
    resource = rdc_resource()
    assert resource.protection == "protected"
    assert not resource.is_automatable


def test_without_the_diagnostic_the_state_is_unchanged_and_honest():
    """functional stays UNKNOWN rather than degrading to the liveness value:
    from this runtime a broken authorization and a broken network are
    indistinguishable, and claiming FAILED on a journal regex is precisely the
    mistake the architecture exists to prevent."""
    h = rdc_health(diagnostic=None)
    assert h.functional.state == "UNKNOWN"
    assert h.state == "DEGRADED"
    assert action_for(rdc_resource(), h)["action"] == "NEEDS_HUMAN"


# ---------------------------------------------------------------------------
# §12/§4 · inventory: unobservable is a result, not a zero
# ---------------------------------------------------------------------------


def test_absent_gpu_is_unavailable_not_zero():
    host = FakeHost(commands={"nvidia-smi": (127, "", "command not found"),
                              "lspci": (0, "00:02.0 VGA compatible controller: Cirrus Logic GD 5446\n", "")})
    gpu = Inventory(host)._gpu()
    assert gpu["state"] == "UNAVAILABLE"
    assert gpu["compute_capable"] is False
    assert gpu.get("vram_total_mib", None) is None
    assert "Cirrus Logic" in gpu["display_controllers"][0]


def test_a_host_with_no_display_adapter_still_reports_unavailable():
    host = FakeHost(commands={"nvidia-smi": (127, "", "nope"), "lspci": (0, "", "")})
    gpu = Inventory(host)._gpu()
    assert gpu["state"] == "UNAVAILABLE"
    assert gpu["display_controllers"] == "UNAVAILABLE"


def test_swap_pressure_is_reported_separately_from_ram():
    """This host runs at ~100% swap with load above its core count. A memory
    report that only showed RAM would call that healthy."""
    host = FakeHost(files={"/proc/meminfo": (
        "MemTotal:       32500000 kB\n"
        "MemFree:         1000000 kB\n"
        "MemAvailable:   10000000 kB\n"
        "Cached:          5000000 kB\n"
        "SwapTotal:       2000000 kB\n"
        "SwapFree:           4000 kB\n"
    )})
    mem = Inventory(host)._memory()
    assert mem["total_bytes"] == 32500000 * 1024
    assert mem["swap"]["used_percent"] == pytest.approx(99.8, abs=0.1)


def test_process_read_that_times_out_is_unavailable_not_an_empty_list():
    host = FakeHost(commands={"ps": (-1, "", "timed out after 8.0s")})
    result = Inventory(host).processes_top()
    assert result["state"] == "UNAVAILABLE"
    assert "timed out" in result["reason"]


def test_unobservable_filesystem_reports_a_named_state():
    host = FakeHost(paths=set())
    mount = Inventory(host)._mount("/opt/applications", "DATA_FS")
    assert mount["state"] == "ABSENT"
    assert mount["role"] == "DATA_FS"


def test_disk_roles_are_named_by_role_not_device():
    """Policy depends on the role: "protect the system disk", not
    "protect /dev/vda2"."""
    assert Inventory.FILESYSTEM_ROLES["/"] == "ROOT_FS"
    assert Inventory.FILESYSTEM_ROLES["/opt/applications"] == "DATA_FS"


def test_docker_summary_never_runs_the_unbounded_df():
    """`docker system df` was measured on this host at over 120 seconds. A
    hygiene command that can hang is worse than one that reports less."""
    host = FakeHost(commands={"docker info": (0, '{"Containers": 29, "Images": 57}', "")})
    summary = Inventory(host).docker_summary()
    assert summary["Containers"] == 29
    assert "system df" in summary["_note"]


def test_root_pressure_produces_advice_and_never_a_deletion():
    host = FakeHost(
        files={"/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\n"},
        usage={"/": type("U", (), {"total": 100, "used": 92, "free": 8})(),
               "/opt/applications": type("U", (), {"total": 100, "used": 10, "free": 90})()},
        paths={"/", "/opt/applications"},
        commands={"findmnt": (0, "/dev/vda2 ext4\n", ""), "df": (0, "header\n", "")},
    )
    pressure = Inventory(host).pressure()
    assert pressure["advisory_only"] is True
    assert any("ROOT_FS" in note for note in pressure["notes"])
    assert any("will not remove anything automatically" in note for note in pressure["notes"])


# ---------------------------------------------------------------------------
# §13/§14 · hygiene: age is not staleness, a name is not an owner
# ---------------------------------------------------------------------------


def a_container(name, *, state="running", labels="", container_id="abcdef1234567890"):
    return {"Names": name, "State": state, "Image": "img:1", "Status": "Up 2 days",
            "ID": container_id, "Labels": labels}


COMPOSE_LABELS = (
    "com.docker.compose.project=lit-0101,"
    "com.docker.compose.project.working_dir=/opt/applications/data/home-hwz/workspace/test-envs/litellm-bridge/litellm-e2e"
)


class FakeInventory:
    """Docker-inventory double. Only `docker_list` is reachable here."""

    def __init__(self, containers):
        self._containers = containers

    def docker_list(self, all_=True):
        return {"count": len(self._containers), "containers": self._containers}

    def disk_status(self):
        return {"roles": [], "others": []}

    def pressure(self):
        return {"advisory_only": True, "notes": []}

    def processes_top(self, limit=15, by="cpu"):
        return {"sorted_by": by, "rows": [], "scope_note": "fake"}


def hygiene_for(containers, *, reg=None, known_work_items=None, worktree_root=None):
    # `reg is None`, not `reg or ...`. `Registry.__len__` counts RESOURCES, so
    # a registry that declares only `protected_refs` is falsy — and `or` would
    # silently substitute an empty registry, which is exactly the kind of
    # quiet default that makes a protection test pass for the wrong reason.
    if reg is None:
        reg = Registry(document=resource_document())
    return Hygiene(reg, FakeInventory(containers), known_work_items=known_work_items,
                   worktree_root=worktree_root)


def test_a_compose_label_is_not_an_owner():
    """REGRESSION, and the most important test in this file.

    Measured on this host: `lit-0101`, `lit-0101-after`, `lit-0103-r3` and
    `fjd-req1-e2e` all carry the SAME
    `com.docker.compose.project.working_dir`. A compose working directory is
    where a config file lives; it is provenance, not ownership. Reading it as
    ownership put eight containers — including a protected parallel
    workstream — into a reclaim bucket.
    """
    report = hygiene_for([a_container("lit-0101-litellm", state="exited",
                                      labels=COMPOSE_LABELS)])
    row = report.containers()["containers"][0]
    assert row["classification"] == "UNKNOWN_OWNER"
    assert row["evidence"]["compose_project"] == "lit-0101"
    assert row["evidence"]["compose_working_dir"].endswith("litellm-e2e")
    # §55 workspace lifecycle: the compose dir is under `test-envs/`, not under
    # `worktrees/`, so there IS no worktree placement and the answer says so
    # rather than guessing one.
    assert row["workspace_placement"] is None


def a_worktree(root, repo, item, *, exists=True):
    """A compose working_dir inside `root`, plus the directory itself when the
    worktree is meant to be on disk."""
    worktree = Path(root) / repo / item
    if exists:
        worktree.mkdir(parents=True, exist_ok=True)
    return (
        f"com.docker.compose.project={repo},"
        f"com.docker.compose.project.working_dir={worktree}"
    )


def test_a_container_built_in_a_worktree_reports_which_one(tmp_path):
    """`Hygiene.worktree_root` was a constructor argument read by nobody.

    It is now the §55 workspace-lifecycle link: a container whose compose file
    lives in `<worktree_root>/<repo>/<ITEM>/` can be traced back to that
    worktree, which is a fact nothing in `runtime/` could state before.
    """
    root = tmp_path / "worktrees"
    row = hygiene_for([a_container("x", labels=a_worktree(root, "litellm-bridge", "LIT-0101"))],
                      worktree_root=root).containers()["containers"][0]
    placement = row["workspace_placement"]
    assert placement["repo"] == "litellm-bridge"
    assert placement["relative"] == "litellm-bridge/LIT-0101"
    assert placement["work_item"] == "work-item:LIT-0101"
    assert placement["worktree_exists"] is True


def test_a_container_outliving_its_worktree_is_visible(tmp_path):
    """The failure case this link exists for: the worktree was retired, the
    container was not. `worktree_exists` is False and the placement still
    names the item, so a reader can go ask whether that item is closed."""
    root = tmp_path / "worktrees"
    labels = a_worktree(root, "litellm-bridge", "LIT-0101", exists=False)
    row = hygiene_for([a_container("x", labels=labels)],
                      worktree_root=root).containers()["containers"][0]
    assert row["workspace_placement"]["worktree_exists"] is False
    assert row["workspace_placement"]["work_item"] == "work-item:LIT-0101"


def test_the_default_worktree_root_comes_from_the_root_contract():
    """A hard-coded default would keep pointing at a directory that moved."""
    assert hygiene_for([]).worktree_root == paths_mod.Paths().root("workspace") / "worktrees"


def test_worktree_placement_is_evidence_and_moves_no_classification(tmp_path):
    """Placement must not become ownership.

    Same failure mode as the compose-label regression above, one level up: a
    container that can be traced to a worktree is still classified entirely by
    its registry declaration, its protection refs and its labels. The
    placement line is added to the row; nothing reads it to decide.
    """
    root = tmp_path / "worktrees"
    with_placement = hygiene_for([a_container("x", labels=a_worktree(root, "litellm-bridge", "LIT-0101"))],
                                 worktree_root=root).containers()
    without = hygiene_for([a_container("y", labels=COMPOSE_LABELS)],
                          worktree_root=root).containers()
    assert with_placement["by_classification"] == without["by_classification"]
    assert with_placement["actionable"] == without["actionable"] == []
    row = with_placement["containers"][0]
    assert row["classification"] == "UNKNOWN_OWNER"
    assert row["actionable"] is False


def test_a_typed_label_is_an_owner_and_a_malformed_one_is_not():
    good = hygiene_for([a_container("worker", state="exited",
                                    labels="serveros.owner=work-item:WI-123")]).containers()
    assert good["containers"][0]["classification"] == "STOPPED_RECLAIM_CANDIDATE"

    bad = hygiene_for([a_container("worker", state="exited",
                                   labels="serveros.owner=WI-123")]).containers()
    assert bad["containers"][0]["classification"] == "UNKNOWN_OWNER"


def test_an_empty_registry_is_falsy_which_is_a_trap():
    """`Registry.__len__` counts RESOURCES, so a registry that declares only
    `protected_refs` is falsy. This was found by a test helper writing
    `reg or default`, which silently swapped in an empty registry and made a
    protection assertion fail for a reason that had nothing to do with
    protection.

    Recorded as a test rather than a comment because the next person to write
    `if registry:` deserves to find out here.
    """
    only_protections = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "p"}],
    ))
    assert len(only_protections) == 0
    assert bool(only_protections) is False
    # ...and it is still fully configured.
    assert only_protections.protected_refs()[0]["ref"] == "work-item:LIT-0103"


def test_a_name_that_matches_a_protected_ref_is_never_a_candidate():
    """REGRESSION. `lit-0103-r3-*` was classified as a reclaim candidate by the
    ownership bug above. §3 protects LIT-0103 regardless of anything else the
    classifier concludes."""
    reg = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "protected lane"}],
    ))
    row = hygiene_for([a_container("lit-0103-r3-litellm", state="exited")],
                      reg=reg).containers()["containers"][0]
    assert row["classification"] == "PROTECTED"
    assert row["actionable"] is False
    assert row["evidence"]["protected_ref_hit"] == ["work-item:LIT-0103"]


def test_protection_is_checked_before_ownership_and_before_registration():
    """No other conclusion may override it, including a valid typed owner."""
    reg = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0104", "because": "protected lane"}],
    ))
    row = hygiene_for([a_container("lit-0104-dev100-litellm", state="exited",
                                   labels="serveros.owner=work-item:LIT-0104")],
                      reg=reg).containers()["containers"][0]
    assert row["classification"] == "PROTECTED"


def test_a_similar_name_does_not_match_a_protected_ref():
    """`lit-01030` is not `lit-0103`. The match is delimited, so protection
    does not smear across every id that shares a prefix."""
    reg = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "protected"}],
    ))
    row = hygiene_for([a_container("lit-01030-litellm", state="exited")],
                      reg=reg).containers()["containers"][0]
    assert row["classification"] == "UNKNOWN_OWNER"


def test_name_matching_only_ever_protects_and_never_claims_ownership():
    """One direction only. The cost of a false positive is that something
    harmless is left alone; the cost of a false negative is a protected
    workstream behind a disk-pressure trigger."""
    hits = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "p"}],
    )).refs_matching("lit-0103-r3-litellm")
    assert hits["protected"][0]["ref"] == "work-item:LIT-0103"
    # The same registry offers no API that turns a matching name into a
    # registration, an owner, or an automatable resource.
    assert not hasattr(Registry, "claim_by_name")


def test_a_foreign_users_working_directory_is_deferred():
    """Another actor's resources are DEFER, always."""
    labels = ("com.docker.compose.project=openclaw,"
              "com.docker.compose.project.working_dir=/opt/applications/data/home-ubuntu/openclaw")
    row = hygiene_for([a_container("openclaw-gateway", labels=labels)]).containers()["containers"][0]
    assert row["classification"] == "UNKNOWN_OWNER"
    assert "different user" in row["reason"]


def test_an_orphaned_container_is_named_orphaned_not_reclaimable():
    labels = ("com.docker.compose.project=gone,"
              "com.docker.compose.project.working_dir=/nonexistent/path/that/is/gone")
    row = hygiene_for([a_container("ghost", state="exited", labels=labels)]).containers()["containers"][0]
    assert row["classification"] == "ORPHANED"
    assert row["actionable"] is False
    assert "not thereby worthless" in row["reason"]


def test_a_protected_registered_container_is_never_stale():
    """§46: the protected LiteLLM reference container is never stale."""
    reg = Registry(document=resource_document(a_resource("litellm-baseline-litellm", probes=[
        {"id": "p", "kind": "container-state", "axis": "liveness",
         "container": "litellm-container0-litellm"},
    ])))
    for state in ("running", "exited"):
        row = hygiene_for([a_container("litellm-container0-litellm", state=state)],
                          reg=reg).containers()["containers"][0]
        assert row["classification"] == "PROTECTED"
        assert row["actionable"] is False


def test_a_closed_item_container_becomes_a_stale_candidate():
    """§46: a closed-item container becomes a stale candidate — and only then."""
    reg = Registry(document=resource_document(
        a_resource("lane", kind="test-runtime", lifecycle={"class": "task-scoped"},
                   protection={"level": "normal"},
                   owner={"type": "work-item", "ref": "work-item:WI-1"},
                   probes=[{"id": "p", "kind": "container-state", "axis": "liveness",
                            "container": "wi-1-dev"}])
    ))
    closed = hygiene_for([a_container("wi-1-dev", state="exited")], reg=reg,
                         known_work_items=set()).containers()
    assert closed["containers"][0]["classification"] == "STALE_CANDIDATE"
    assert closed["actionable"] == ["wi-1-dev"]

    still_open = hygiene_for([a_container("wi-1-dev", state="exited")], reg=reg,
                             known_work_items={"WI-1"}).containers()
    assert still_open["containers"][0]["classification"] == "ACTIVE"
    assert still_open["actionable"] == []


def test_not_knowing_which_items_are_open_yields_no_candidates():
    """Not knowing is not permission."""
    reg = Registry(document=resource_document(
        a_resource("lane", kind="test-runtime", lifecycle={"class": "task-scoped"},
                   protection={"level": "normal"},
                   owner={"type": "work-item", "ref": "work-item:WI-1"},
                   probes=[{"id": "p", "kind": "container-state", "axis": "liveness",
                            "container": "wi-1-dev"}])
    ))
    classified = hygiene_for([a_container("wi-1-dev", state="exited")], reg=reg).containers()
    assert classified["containers"][0]["classification"] != "STALE_CANDIDATE"
    assert classified["actionable"] == []


def test_a_persistent_container_that_stopped_is_a_fault_not_garbage():
    reg = Registry(document=resource_document(a_resource(
        "baseline", protection={"level": "normal"},
        probes=[{"id": "p", "kind": "container-state", "axis": "liveness",
                 "container": "baseline-db"}],
    )))
    row = hygiene_for([a_container("baseline-db", state="exited")], reg=reg).containers()["containers"][0]
    assert row["classification"] == "ACTIVE"
    assert "fault to report" in row["reason"]


def test_the_live_host_has_nothing_actionable_and_says_why():
    """The expected result on this host, and not because it is tidy."""
    containers = [
        a_container("lit-0103-r3-litellm", state="exited"),
        a_container("fjd-req1-e2e-litellm", state="exited", labels=COMPOSE_LABELS),
        a_container("xpra-gui-hwz", state="exited"),
    ]
    reg = Registry(document=resource_document(
        protected_refs=[{"ref": "work-item:LIT-0103", "because": "p"}],
        defer_refs=[{"ref": "project:fjd-req1-e2e", "because": "unknown owner"}],
    ))
    report = hygiene_for(containers, reg=reg).report()
    assert report["docker"]["actionable"] == []
    assert report["summary"]["actionable_count"] == 0
    assert "either" in report["summary"]["note"] or "not because" in report["summary"]["note"]


# ---------------------------------------------------------------------------
# §14 · risk tiers
# ---------------------------------------------------------------------------


def test_prune_is_denied_with_no_override():
    decision = hygiene_mod.admission("PRUNE", "STALE_CANDIDATE", approved=True)
    assert decision["allowed"] is False
    assert decision["decision"] == "DENY_BY_DEFAULT"
    assert hygiene_mod.TIER_POLICY["PRUNE"]["implemented"] is False


def test_remove_volume_needs_approval_and_a_verified_backup():
    """The container can be rebuilt from an image; the volume cannot be
    rebuilt from anything."""
    blocked = hygiene_mod.admission("REMOVE_VOLUME", "STALE_CANDIDATE")
    assert blocked["allowed"] is False
    assert blocked["decision"] == "NEEDS_APPROVAL"

    approved_without_backup = hygiene_mod.admission("REMOVE_VOLUME", "STALE_CANDIDATE", approved=True)
    assert approved_without_backup["allowed"] is False
    assert "cannot be rebuilt from anything" in approved_without_backup["because"]

    approved_with_backup = hygiene_mod.admission(
        "REMOVE_VOLUME", "STALE_CANDIDATE", approved=True,
        owner_evidence={"backup_verified": True},
    )
    assert approved_with_backup["allowed"] is True


def test_removing_a_container_is_stricter_than_stopping_it():
    assert hygiene_mod.admission("STOP", "STALE_CANDIDATE")["allowed"] is True
    assert hygiene_mod.admission("REMOVE_CONTAINER", "STALE_CANDIDATE")["allowed"] is False
    assert hygiene_mod.admission("REMOVE_CONTAINER", "STALE_CANDIDATE",
                                 approved=True)["allowed"] is True


def test_protection_beats_every_tier():
    for tier in ("STOP", "REMOVE_CONTAINER", "REMOVE_IMAGE", "REMOVE_VOLUME"):
        decision = hygiene_mod.admission(tier, "PROTECTED", protection="protected", approved=True)
        assert decision["allowed"] is False, tier
        assert decision["decision"] == "DENY_PROTECTED"


def test_protected_is_denied_even_when_its_class_would_otherwise_allow():
    """`protection: protected` is checked before the class, so a protected
    resource cannot become actionable by being reclassified."""
    decision = hygiene_mod.admission("STOP", "STALE_CANDIDATE", protection="protected")
    assert decision["allowed"] is False


def test_unknown_owner_is_denied_at_every_tier():
    for tier in ("STOP", "REMOVE_CONTAINER", "REMOVE_IMAGE", "REMOVE_VOLUME"):
        decision = hygiene_mod.admission(tier, "UNKNOWN_OWNER", approved=True)
        assert decision["allowed"] is False, tier
        assert "Guessing an owner from a name" in decision["because"]


def test_every_class_has_a_decided_policy():
    """Adding a class forces a decision about it, rather than defaulting into
    the actionable set by omission."""
    for klass in hygiene_mod.CLASSES:
        if klass in hygiene_mod.ACTIONABLE_CLASSES:
            continue
        assert klass in hygiene_mod.NEVER_ACTIONABLE, klass


# ---------------------------------------------------------------------------
# §46 · the hygiene module cannot act, and the contract has no supervisor
# ---------------------------------------------------------------------------


def test_hygiene_cannot_stop_remove_or_prune_anything():
    """It returns decisions. The component that executes is a different one,
    on purpose, so "what should happen" and "do it" are reviewable apart."""
    for name in ("reduce", "remove", "rm", "stop", "prune", "delete", "kill"):
        assert not hasattr(Hygiene, name), name


def test_the_registry_has_desired_state_and_no_restart_policy():
    """systemd, Docker and the provider own the lifecycle. A `restart_policy`
    field here would make ServerOS a supervisor, and process supervision
    cannot renew a session."""
    contract = registry_mod.contract()
    fields = set(contract["kinds"]) | {"managed-runtime"}
    record = a_resource()
    assert "desired" in record
    assert "restart_policy" not in record
    assert "auto_restart" not in record
    assert fields  # the vocabulary is data, not code


def test_reconcile_is_only_reachable_for_an_automatable_resource():
    r = Registry(document=resource_document(
        a_resource("scratch", lifecycle={"class": "task-scoped"},
                   protection={"level": "normal"},
                   owner={"type": "work-item", "ref": "work-item:WI-9"}),
    ))
    resource = r.get("scratch")
    h = build_health(
        liveness=ok("liveness", "STOPPED"), transport=unknown("transport"),
        functional=unknown("functional"), readiness=unknown("readiness"),
    )
    decision = action_for(resource, h)
    assert decision["action"] == "RECONCILE"
    assert "budget" in decision


def test_an_unobserved_resource_defers_rather_than_reporting_nothing_to_do():
    resource = Registry(document=resource_document(
        a_resource("scratch", protection={"level": "normal"}),
    )).get("scratch")
    h = build_health(
        liveness=unknown("liveness"), transport=unknown("transport"),
        functional=unknown("functional"), readiness=unknown("readiness"),
    )
    decision = action_for(resource, h)
    assert decision["action"] == "DEFER"
    assert "an unobserved resource is not a healthy one" in decision["because"]


# ---------------------------------------------------------------------------
# §47 · `run_probe`, the record the CLI renders
# ---------------------------------------------------------------------------


class ProbeHost:
    """A host that answers exactly what `container-state` asks for."""

    def __init__(self, status="running", unavailable=False):
        self.status = status
        self.unavailable = unavailable

    def now(self):
        return "2026-09-12T00:00:00Z"

    def docker_inspect(self, name):
        if self.unavailable:
            raise probes_mod.ProbeUnavailable(f"no container {name!r}")
        return {
            "State": {"Status": self.status, "StartedAt": "2026-09-12T00:00:00Z",
                      "OOMKilled": False},
            "RestartCount": 0,
        }


# The keys `cmd_probe` indexes rather than `.get()`s. Everything else in the
# record is optional by design — the renderer asks for it defensively — but
# these three it reads directly, so an arm that omits one crashes the verb.
RENDERER_INDEXES = ("id", "axis", "state")


@pytest.mark.parametrize("arm", ["succeeded", "unavailable", "raised", "unknown-kind"])
def test_every_arm_of_run_probe_carries_what_the_renderer_indexes(arm, monkeypatch):
    """`serverosctl probe <id>` crashed on every probe that answered.

    `Observation.show()` leaves `axis` out on purpose — everywhere else an
    observation is rendered it is already the key it hangs under
    (`{"liveness": obs.show()}`). `run_probe` is the one caller that returns a
    flat list, so it is the one caller that has to put the axis back, and it did
    — on both of its error arms. The success arm did not, so the failure path
    rendered and the happy path raised `KeyError: 'axis'`.

    A defect that only shows up when the probe WORKS is invisible to a suite
    that only exercises failures, so this asserts every arm at once: the shape
    has to be the same one whether the probe answered, was unavailable, raised,
    or was never known.
    """

    class Exploding(probes_mod.Probe):
        kind = "exploding-test"

        def run(self, resource, host):
            raise registry_mod.RegistryError("this probe cannot answer")

    monkeypatch.setitem(probes_mod.PROBE_KINDS, "exploding-test", Exploding)

    specs = {
        "succeeded": ({"id": "p", "kind": "container-state", "axis": "liveness",
                       "container": "c"}, ProbeHost()),
        "unavailable": ({"id": "p", "kind": "container-state", "axis": "liveness",
                         "container": "c"}, ProbeHost(unavailable=True)),
        "raised": ({"id": "p", "kind": "exploding-test", "axis": "liveness"}, ProbeHost()),
        "unknown-kind": ({"id": "p", "kind": "no-such-kind", "axis": "liveness"}, ProbeHost()),
    }
    spec, host = specs[arm]
    resource = Registry(document=resource_document(a_resource())).get("thing")

    record = probes_mod.run_probe(resource, spec, host)
    for key in RENDERER_INDEXES:
        assert key in record, f"{arm}: {key!r} missing from {sorted(record)}"


def test_a_probe_that_answered_is_marked_authoritative_and_reports_its_axis():
    """The success arm, asserted positively rather than only for shape."""
    resource = Registry(document=resource_document(a_resource())).get("thing")
    record = probes_mod.run_probe(
        resource,
        {"id": "p", "kind": "container-state", "axis": "liveness", "container": "c"},
        ProbeHost(),
    )
    assert record["axis"] == "liveness"
    assert record["id"] == "p"
    assert record["kind"] == "container-state"
    assert record["state"] == "RUNNING"
    # Measured, not inferred: this is what separates it from the journal line.
    assert record["authoritative"] is True
    assert record["evidence"]["type"] == "host-measurement"


def test_a_probe_that_could_not_run_is_not_authoritative():
    """The other half of the distinction. `UNAVAILABLE` is a real answer about
    the probe, and it must never read as a measurement of the resource."""
    resource = Registry(document=resource_document(a_resource())).get("thing")
    record = probes_mod.run_probe(
        resource,
        {"id": "p", "kind": "container-state", "axis": "liveness", "container": "c"},
        ProbeHost(unavailable=True),
    )
    assert record["state"] == "UNAVAILABLE"
    assert record["reason"] == "NOT_OBSERVABLE"
