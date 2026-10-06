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

    def __init__(self, files=None, commands=None, usage=None, paths=None,
                 inodes=None, clocks=None, file_sizes=None, denied=None):
        self.files = files or {}
        self.commands = commands or {}
        self.usage = usage or {}
        self.paths = set(paths or ())
        self.inodes = inodes or {}
        self.file_sizes = file_sizes or {}
        self.denied = set(denied or ())
        # Successive `/proc/uptime` values, consumed one read at a time. A
        # counter sample is a DELTA, so a fake clock that returns one value
        # forever can only ever measure a zero-length window.
        self.clocks = list(clocks or ())
        self.slept = []

    def read_text(self, path):
        """One file's contents, or the next of several.

        A configured value that is a LIST is consumed one entry per read. That
        is what a counter sample is: two reads of the same file that must
        differ, and a fake that returns one string forever can only ever
        measure a zero-length window — which is the shape of defect the tests
        below exist to catch, so the fake must not be able to fake its way
        into passing them.
        """
        if path == "/proc/uptime" and self.clocks:
            return f"{self.clocks.pop(0)} 0.00\n"
        value = self.files.get(path)
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def read(self, args, timeout):
        key = " ".join(args)
        for pattern, result in self.commands.items():
            if key.startswith(pattern):
                if isinstance(result, Exception):
                    raise result
                return result
        return 127, "", f"not configured: {key}"

    def statvfs(self, path):
        return self.usage.get(path)

    def inode_usage(self, path):
        return self.inodes.get(path)

    def path_stat(self, path):
        if path in self.denied:
            return {"state": "PERMISSION_DENIED",
                    "reason": "a directory on the way to it is not traversable"}
        if path in self.file_sizes:
            return {"state": "PRESENT", "size_bytes": self.file_sizes[path],
                    "modified_at": 1_700_000_000.0}
        return {"state": "UNAVAILABLE", "reason": "FileNotFoundError: no such file"}

    def exists(self, path):
        return path in self.paths

    def sleep(self, seconds):
        # Recorded rather than slept: a test that is measuring a counter delta
        # supplies the second sample itself, and waiting 150 ms per call to
        # arrive at the same numbers is 150 ms of nothing. The requested
        # interval is kept so a test can assert the module ASKED for one — a
        # sampler that forgot to wait would otherwise look identical.
        self.slept.append(seconds)


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


# ---------------------------------------------------------------------------
# §T/§U · a watermark is not a pressure, and the note has to be able to say so
# ---------------------------------------------------------------------------

# This host, measured 2026-09-15: 99.2% of an 8.0 GiB swap file in use — while
# memory PSI read some/full avg10/avg60/avg300 all 0.00 and 15.7 GiB of 31.0
# GiB was still available. `pswpout` read 3208181 at two moments forty minutes
# apart, unchanged: nothing was paging. The note said "Real memory pressure".
# Every constant below is one of those readings, so the test is written against
# the machine it was found on rather than against a plausible one.
A_FULL_SWAP_HOST = (
    "MemTotal:       32472892 kB\n"
    "MemAvailable:   16842260 kB\n"
    "SwapTotal:       8388608 kB\n"
    "SwapFree:          67092 kB\n"
)
PSI_CALM = (
    "some avg10=0.00 avg60=0.00 avg300=0.00 total=59254097\n"
    "full avg10=0.00 avg60=0.00 avg300=0.00 total=55493135\n"
)
PSI_STALLING = (
    "some avg10=1.20 avg60=4.10 avg300=3.80 total=59254097\n"
    "full avg10=0.30 avg60=0.90 avg300=0.80 total=55493135\n"
)


def swap_notes(host):
    notes = Inventory(host).pressure()["notes"]
    return [n for n in notes if n.startswith("swap is")]


def test_a_full_swap_file_with_no_stall_is_not_reported_as_pressure():
    """The watermark is a high-water mark, not a measurement of now.

    This is the defect the test is named for, taken from the host it was found
    on: the note asserted "Real memory pressure" while the kernel said nothing
    was stalled on memory and half the RAM was free. Pages come back out of
    swap only when they are touched again, so a swap file that filled once
    stays full while the machine is idle.
    """
    host = FakeHost(files={"/proc/meminfo": A_FULL_SWAP_HOST,
                           "/proc/pressure/memory": PSI_CALM})
    (note,) = swap_notes(host)
    assert "Real memory pressure" not in note
    assert "not current memory pressure" in note
    assert "99.2% used" in note
    # And it names the measurement rather than only its conclusion.
    assert "some avg60=0.0%" in note and "full avg60=0.0%" in note


def test_a_full_swap_file_under_a_real_stall_is_still_reported_as_pressure():
    """The positive control. A rule that never fires is indistinguishable from
    a rule that was deleted, and this one has to keep its teeth: at 4.1% some
    and 0.9% full over a minute, the process really does have to justify
    itself."""
    host = FakeHost(files={"/proc/meminfo": A_FULL_SWAP_HOST,
                           "/proc/pressure/memory": PSI_STALLING})
    (note,) = swap_notes(host)
    assert "Real memory pressure" in note
    assert "some avg60=4.1%" in note
    assert "not current memory pressure" not in note


def test_a_full_swap_file_with_unreadable_psi_claims_neither():
    """`psi=0` on the command line, a pre-4.20 kernel, and a container without
    the file are indistinguishable from here. "No pressure" and "cannot tell"
    are different answers, and a note that picked one would be guessing about
    a machine it could not read — the same conflation `pressure_stall` refuses
    one method above."""
    host = FakeHost(files={"/proc/meminfo": A_FULL_SWAP_HOST})
    (note,) = swap_notes(host)
    assert "Real memory pressure" not in note
    assert "not current memory pressure" not in note
    assert "was not established" in note
    assert "is not readable" in note


def test_the_swap_note_reports_the_pressure_thresholds_it_used():
    """A reader has to be able to disagree with the rule, not only the number."""
    host = FakeHost(files={"/proc/meminfo": A_FULL_SWAP_HOST,
                           "/proc/pressure/memory": PSI_CALM})
    pressure = Inventory(host).pressure()
    assert pressure["thresholds"]["swap_percent_high"] == 90.0
    assert pressure["thresholds"]["psi_memory_some_avg60_percent_high"] == 1.0
    assert pressure["thresholds"]["psi_memory_full_avg60_percent_high"] == 0.5
    (note,) = [n for n in pressure["notes"] if n.startswith("swap is")]
    assert "threshold 1.0%" in note and "threshold 0.5%" in note


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
    assert Inventory.SEMANTIC_FILESYSTEM_ROLES["DATA_FS"] == "data"


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
# §4/§5 · filesystem roles are resolved, and a threshold is a policy
# ---------------------------------------------------------------------------

def a_disk(total=1000, used=100):
    return type("U", (), {"total": total, "used": used, "free": total - used})()


def test_the_docker_data_root_is_asked_for_and_not_assumed():
    """`/var/lib/docker` is a default, not a fact about this machine. This
    host's is somewhere else entirely, and a policy that protects the assumed
    path protects the wrong disk."""
    host = FakeHost(
        commands={"docker info --format {{.DockerRootDir}}": (0, "/srv/docker\n", "")},
        usage={"/srv/docker": a_disk()}, paths={"/srv/docker"},
    )
    roles = {r["role"]: r for r in Inventory(host)._resolved_roles()}
    assert roles["DOCKER_FS"]["path"] == "/srv/docker"
    assert "/var/lib/docker" not in str(roles)


def test_an_unresolvable_role_is_reported_not_dropped():
    """A role that vanishes from the list makes the report shorter and reads
    as "nothing to see". It has to be present and named as unmeasured."""
    host = FakeHost(commands={"docker info --format {{.DockerRootDir}}": (127, "", "no daemon")})
    roles = {r["role"]: r for r in Inventory(host)._resolved_roles()}
    assert roles["DOCKER_FS"]["path"] is None
    assert "DockerRootDir" in roles["DOCKER_FS"]["reason"]

    mount = [m for m in Inventory(host).disk_status()["roles"]
             if m["role"] == "DOCKER_FS"][0]
    assert mount["state"] == "UNAVAILABLE"
    assert mount["path"] is None
    # Not ABSENT: absent would be a measurement, and nothing was measured.
    assert mount["state"] != "ABSENT"


def test_a_template_that_did_not_resolve_is_not_used_as_a_path():
    """A wrong `--format` returns whatever else it matched. Using that as a
    directory puts a confident `PRESENT` on a role nobody resolved."""
    host = FakeHost(commands={
        "docker info --format {{.DockerRootDir}}": (0, "<no value>\n", ""),
        "docker info": (0, '{"Containers": 29}', ""),
    })
    assert Inventory(host)._docker_root_dir() is None


def test_roles_sharing_a_filesystem_say_so():
    """"Docker's data root is on the root disk" is only visible when the roles
    are compared to each other. Per-role thresholds applied to one filesystem
    twice read as two healthy disks."""
    host = FakeHost(
        commands={"docker info --format {{.DockerRootDir}}": (0, "/var/lib/docker\n", ""),
                  "findmnt": (0, "/dev/vda2 ext4 /\n", "")},
        usage={"/": a_disk(), "/var/lib/docker": a_disk()},
        paths={"/", "/var/lib/docker"},
    )
    mounts = {m["role"]: m for m in Inventory(host).disk_status()["roles"]}
    assert "ROOT_FS" in mounts["DOCKER_FS"]["shares_filesystem_with"]
    assert mounts["DOCKER_FS"]["mount_point"] == "/"
    assert mounts["ROOT_FS"].get("shares_filesystem_with")  # symmetric


def test_an_unreadable_contract_yields_no_advice_rather_than_all_of_it():
    """`_at_least(None, None)` must not be True. A contract that failed to load
    would otherwise flag every reading on the machine."""
    assert inventory_mod._at_least(None, 85) is False
    assert inventory_mod._at_least(99, None) is False
    assert inventory_mod._at_least("62%", 85) is False
    assert inventory_mod._at_least(85.0, 85.0) is True


def test_inode_exhaustion_is_a_separate_gate_from_free_bytes():
    """A filesystem with free space and no free inodes cannot create a file.
    Bytes are not the limit here, and before this the two role mounts — the
    ones policy reads — carried no inode figure at all."""
    host = FakeHost(
        files={"findmnt": None},
        usage={"/": a_disk(total=1000, used=100)}, paths={"/"},
        inodes={"/": {"total": 100, "free": 2, "used": 98, "used_percent": 98.0}},
        commands={"findmnt": (0, "/dev/vda2 ext4 /\n", ""), "df": (0, "header\n", "")},
    )
    mount = Inventory(host)._mount("/", "ROOT_FS")
    assert mount["inodes"]["used_percent"] == 98.0
    notes = inventory_mod.Inventory(host).pressure()["notes"]
    assert any("inodes" in n for n in notes)
    assert not any("% full" in n for n in notes)  # 10% of bytes used


def test_pressure_reports_the_thresholds_it_used():
    """A reading a reader cannot argue with is a reading they can only
    believe. The three comparisons here were literals in the function."""
    host = FakeHost(
        files={"/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 10 kB\nSwapTotal: 100 kB\nSwapFree: 5 kB\n"},
        usage={"/": a_disk(total=1000, used=920)}, paths={"/"},
        commands={"findmnt": (0, "/dev/vda2 ext4 /\n", ""), "df": (0, "header\n", "")},
    )
    pressure = Inventory(host).pressure()
    assert pressure["thresholds"]["filesystem_percent_high"] == 85.0
    assert pressure["thresholds"]["swap_percent_high"] == 90.0
    assert any("threshold 85.0" in n for n in pressure["notes"])


# ---------------------------------------------------------------------------
# §4 · counters: a sample is a delta, and a missing sample is not zero
# ---------------------------------------------------------------------------

def test_cpu_usage_needs_two_reads_and_waits_between_them():
    """One read of `/proc/stat` is a lifetime average since boot, which is a
    number about the machine's birthday and not about now."""
    host = FakeHost(files={"/proc/stat": [
        "cpu  100 0 100 800 0 0 0 0 0 0\n",
        "cpu  200 0 200 1600 0 0 0 0 0 0\n",   # +100 user, +100 sys, +800 idle
    ]})
    usage = Inventory(host).cpu_usage()
    assert usage["state"] == "PRESENT"
    assert usage["used_percent"] == 20.0
    assert usage["sample_seconds"] == Inventory.CPU_SAMPLE_SECONDS
    assert host.slept == [Inventory.CPU_SAMPLE_SECONDS]


def test_iowait_is_idle_time_not_busy_time():
    """A machine waiting on a disk is not a machine doing work. Counting
    iowait as busy is how a stalled host reports 100% and looks healthy."""
    first = {"user": 0, "idle": 0, "iowait": 0}
    second = {"user": 100, "idle": 800, "iowait": 100}
    delta = Inventory._cpu_delta(first, second, 0.15)
    assert delta["used_percent"] == 10.0


def test_a_sample_that_caught_no_ticks_is_unavailable_not_zero_percent():
    same = {"user": 5, "idle": 5, "iowait": 0}
    result = Inventory._cpu_delta(same, dict(same), 0.15)
    assert result["state"] == "UNAVAILABLE"
    assert result.get("used_percent") is None


def test_absent_psi_is_unavailable_per_resource_not_zero():
    """`psi=0` on the kernel command line, a pre-4.20 kernel and a container
    without the file all look identical from here. "No stall" and "cannot
    tell" are different claims and only one of them is safe to print."""
    host = FakeHost(files={"/proc/pressure/cpu": (
        "some avg10=1.00 avg60=2.00 avg300=3.00 total=1000\n"
        "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )})
    psi = Inventory(host).pressure_stall()
    assert psi["cpu"]["state"] == "PRESENT"
    assert psi["cpu"]["some"]["avg10"] == 1.0
    assert psi["memory"]["state"] == "UNAVAILABLE"
    assert psi["memory"].get("some") is None
    assert psi["provider"] == "PRESENT"


def test_disk_io_measures_over_a_real_gap_and_reports_it():
    """Two reads taken back to back measure nothing: the counters would not
    have moved, and "0 B/s" would be reported on a disk that is busy."""
    host = FakeHost(
        files={"/proc/diskstats": [
            "   8       0 vdb 100 0 2000 0 50 0 400 0 0 0 0\n",
            "   8       0 vdb 200 0 4000 0 150 0 1200 0 0 0 0\n",
        ]},
        commands={"findmnt": (0, "/dev/vdb xfs /opt/applications\n", "")},
        paths={"/opt/applications"}, usage={"/opt/applications": a_disk()},
        clocks=[100.0, 100.4],
    )
    result = Inventory(host).disk_io()
    assert host.slept == [Inventory.CPU_SAMPLE_SECONDS]
    # The window comes from the clock in the samples, not from what was
    # requested: `sleep` returns after *at least* the interval, and on a loaded
    # host the difference is the load.
    assert result["sample_seconds"] == pytest.approx(0.4)
    row = result["roles"]["DATA_FS"]
    # (4000 - 2000) sectors x 512 bytes, over 0.4s.
    assert row["read_bytes_per_second"] == pytest.approx(2560000.0)
    assert row["write_bytes_per_second"] == pytest.approx(1024000.0)


def test_docker_sizes_parses_the_one_field_the_cli_actually_emits():
    """`docker ps` reports `"77.7MB (virtual 1.28GB)"` in ONE field. The Engine
    API's separate `SizeRw` / `SizeRootFs` do not exist in the CLI's JSON, and
    a reader who looked for them here would find `None` for every container."""
    host = FakeHost(commands={"docker ps -a --size": (0, "\n".join([
        '{"ID":"a1","Names":"x","State":"running","Size":"77.7MB (virtual 1.28GB)"}',
        '{"ID":"b2","Names":"y","State":"created","Size":"0B (virtual 1.22GB)"}',
    ]) + "\n", "")})
    sizes = Inventory(host).docker_sizes()
    x, y = sizes["containers"]
    assert x["size_rw_bytes"] == 77_700_000
    assert x["size_root_fs_bytes"] == 1_280_000_000
    assert x["size_image_layers_bytes"] == 1_202_300_000
    # A container with nothing written to it is a REAL zero, not a missing
    # measurement, and it stays distinguishable from one.
    assert y["size_rw_bytes"] == 0
    assert y["size_image_layers_bytes"] == 1_220_000_000
    assert "SI units" in sizes["note"]


def test_docker_sizes_without_the_size_flag_reports_nothing_not_zero():
    host = FakeHost(commands={"docker ps -a --size": (0,
        '{"ID":"a1","Names":"x","State":"running"}\n', "")})
    sizes = Inventory(host).docker_sizes()
    assert sizes["containers"][0]["size_rw_bytes"] is None
    assert sizes["containers"][0]["size_image_layers_bytes"] is None


def test_si_and_iec_suffixes_are_not_the_same_number():
    """`docker images` uses 1000-based units and `docker stats` uses 1024-based
    ones. Treating the suffix as decoration is a consistent 7% error in the
    direction of understating disk use."""
    assert inventory_mod._parse_size("1GB") == 1_000_000_000
    assert inventory_mod._parse_size("1GiB") == 1_073_741_824
    assert inventory_mod._parse_size("1.62GB") == 1_620_000_000


def test_a_missing_docker_size_is_none_and_never_zero():
    """`N/A` on SharedSize is what this host prints for every image. Zero would
    say "this image shares nothing", which is a different and false claim."""
    assert inventory_mod._parse_size("N/A") is None
    assert inventory_mod._parse_size("") is None
    assert inventory_mod._parse_size(None) is None
    assert inventory_mod._parse_percent("N/A") is None
    assert inventory_mod._parse_percent("0.00%") == 0.0


def test_an_unreachable_docker_is_not_an_empty_one():
    """§27. `docker is down` and `there are zero containers` are different
    facts, and a caller that conflates them reports a clean machine during an
    outage — which is exactly when somebody is reading it."""
    host = FakeHost(commands={"docker": (1, "", "Cannot connect to the Docker daemon")})
    for method in (Inventory(host).docker_list, Inventory(host).docker_images,
                   Inventory(host).docker_stats, Inventory(host).docker_networks,
                   Inventory(host).docker_sizes, Inventory(host).container_logs,
                   Inventory(host).docker_space):
        result = method()
        assert result.get("state") == "UNAVAILABLE", method.__name__
        assert result.get("count") is None, method.__name__
        assert "Docker daemon" in result["reason"] or result["reason"], method.__name__


def test_a_reachable_daemon_with_no_containers_is_an_empty_list():
    """The other half of the same rule: an empty answer from a reachable
    provider IS an answer."""
    host = FakeHost(commands={"docker ps -aq": (0, "", "")})
    logs = Inventory(host).container_logs()
    assert logs["state"] != "UNAVAILABLE"
    assert logs["count"] == 0
    assert logs["unobservable"] == 0


def test_container_images_report_how_many_containers_use_them():
    """Zero users is a candidate and two is not, and that is a fact about
    containers rather than about the image."""
    host = FakeHost(commands={"docker images": (0, "\n".join([
        '{"ID":"i1","Repository":"litellm","Tag":"latest","Size":"1.62GB","Containers":"2","Digest":"<none>"}',
        '{"ID":"i2","Repository":"<none>","Tag":"<none>","Size":"900MB","Containers":"0","Digest":"<none>"}',
        '{"ID":"i3","Repository":"litellm-base","Tag":"<none>","Size":"1.46GB","Containers":"0","Digest":"<none>"}',
    ]) + "\n", "")})
    images = Inventory(host).docker_images()
    assert images["count"] == 3
    # i2 and i3 are both dangling: a missing REPOSITORY and a missing TAG are
    # each enough, and matching on the literal "<none>" string alone misses i3.
    assert images["dangling"] == 2
    assert images["images"][0]["containers_using"] == 2
    assert images["containers_using_fewest"] == 0


def test_container_logs_report_the_reason_and_never_a_zero():
    """This host's Docker data root is `0710 root:root` and the observer is
    `hwz`. Every log is UNREADABLE, and the honest total is unknown — a zero
    total would read as "no logs on this machine"."""
    host = FakeHost(
        commands={"docker ps -aq": (0, "abc123\n", ""),
                  "docker inspect": (0,
                      "abc123|/litellm|json-file|/opt/applications/data/docker/"
                      "containers/abc/abc-json.log\n", "")},
        denied={"/opt/applications/data/docker/containers/abc/abc-json.log"},
    )
    logs = Inventory(host).container_logs()
    assert logs["observable"] == 0
    assert logs["unobservable"] == 1
    assert logs["logs_total_bytes"] is None
    entry = logs["containers"][0]
    assert entry["state"] == "UNREADABLE"
    assert entry["unreadable_because"] == "PERMISSION_DENIED"
    assert entry["size_bytes"] is None
    # The path is reported. The CONTENT is never read: a container log is
    # application output and can carry tokens.
    assert entry["log_path"].endswith("-json.log")


def test_a_log_driver_with_no_file_is_a_different_fact_from_a_denied_read():
    host = FakeHost(
        commands={"docker ps -aq": (0, "abc123\n", ""),
                  "docker inspect": (0, "abc123|/litellm|journald|\n", "")},
    )
    entry = Inventory(host).container_logs()["containers"][0]
    assert entry["state"] == "NO_LOG_FILE"
    assert entry["state"] != "UNREADABLE"
    assert "journald" in entry["reason"]


def test_a_readable_log_file_is_measured():
    path = "/var/log/containers/abc-json.log"
    host = FakeHost(
        commands={"docker ps -aq": (0, "abc123\n", ""),
                  "docker inspect": (0, f"abc123|/litellm|json-file|{path}\n", "")},
        file_sizes={path: 4096},
    )
    logs = Inventory(host).container_logs()
    assert logs["observable"] == 1
    assert logs["logs_total_bytes"] == 4096
    assert logs["containers"][0]["modified_at"] == 1_700_000_000.0


def test_all_never_pays_for_the_twenty_two_second_read():
    """22s on this host against a 60s tick. It is correct; it is just not
    something every tick can afford. Asserted by BEHAVIOUR — the daemon-wide
    read is made to raise, and `all()` must not notice — rather than by
    grepping the source, which a comment mentioning the name would defeat."""
    def exploding(args, timeout):
        key = " ".join(args)
        if key.startswith("docker system df"):
            raise AssertionError("all() ran docker system df")
        if key.startswith("docker"):
            return 0, "", ""
        return 127, "", f"not configured: {key}"

    host = FakeHost()
    host.read = exploding
    everything = Inventory(host).all()
    # The live-usage reads (2.15s and 0.54s) are inside the tick.
    for present in ("stats", "sizes", "images", "networks", "logs"):
        assert present in everything["docker"], present
    assert "space" not in everything["docker"]


def test_docker_space_sums_reclaimable_without_offering_to_reclaim():
    rows = "\n".join([
        '{"Type":"Images","TotalCount":"56","Active":"22","Size":"191.6GB","Reclaimable":"177.7GB (92%)"}',
        '{"Type":"Build Cache","TotalCount":"1513","Active":"0","Size":"162.1GB","Reclaimable":"140.9GB"}',
    ]) + "\n"
    host = FakeHost(commands={"docker system df": (0, rows, "")})
    space = Inventory(host).docker_space()
    assert space["sections"]["images"]["reclaimable_bytes"] == 177_700_000_000
    assert space["sections"]["build_cache"]["count"] == 1513
    assert space["reclaimable_bytes_total"] == 177_700_000_000 + 140_900_000_000
    assert "report only" in space["note"]


def test_docker_space_falls_back_to_the_table_when_format_is_ignored():
    """`--format` is not honoured by this verb on every daemon version, and it
    yields the plain table rather than an error."""
    table = (
        "TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE\n"
        "Images          56        22        191.6GB   177.7GB (92%)\n"
        "Local Volumes   643       25        83.57GB   465.6MB (0%)\n"
        "Build Cache     1513      0         162.1GB   140.9GB\n"
    )
    host = FakeHost(commands={"docker system df": (0, table, "")})
    space = Inventory(host).docker_space()
    assert space["sections"]["local_volumes"]["count"] == 643
    assert space["sections"]["build_cache"]["reclaimable_bytes"] == 140_900_000_000


def test_docker_stats_omits_a_container_it_has_no_reading_for():
    """`docker stats` reports running containers only. A stopped one is ABSENT
    rather than present with zeros — a zero would read as "running and idle"."""
    host = FakeHost(commands={"docker stats": (0,
        '{"ID":"a1","Name":"litellm","CPUPerc":"1.79%","MemUsage":"524.7MiB / 30.97GiB",'
        '"MemPerc":"1.65%","NetIO":"392kB / 544kB","BlockIO":"258MB / 200MB","PIDs":"17"}\n', "")})
    stats = Inventory(host).docker_stats()
    assert stats["count"] == 1
    row = stats["containers"][0]
    assert row["cpu_percent"] == 1.79
    assert row["memory_used_bytes"] == int(524.7 * 1024 ** 2)
    assert row["pids"] == 17


def test_disk_io_with_no_clock_says_so_instead_of_dividing_by_zero():
    host = FakeHost(
        files={"/proc/diskstats": "   8       0 vdb 1 0 2 0 3 0 4 0 0 0 0\n"},
        commands={"findmnt": (0, "/dev/vdb xfs /opt/applications\n", "")},
        paths={"/opt/applications"}, usage={"/opt/applications": a_disk()},
    )
    result = Inventory(host).disk_io()
    # No clock is configured, so /proc/uptime is unreadable and the requested
    # interval is the honest fallback — the sample still reports a window.
    assert result["sample_seconds"] == Inventory.CPU_SAMPLE_SECONDS
    assert result["roles"]["DATA_FS"]["read_bytes_per_second"] == 0.0



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
    assert good["containers"][0]["classification"] == "STOPPED_REVIEW_CANDIDATE"

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
