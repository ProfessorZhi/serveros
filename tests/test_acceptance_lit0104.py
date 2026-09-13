#!/usr/bin/env python3
"""§57 — real acceptance, LIT-0104: Linux HEALTHY and the Windows tunnel gone.

THE ACCEPTANCE THE BRIEF STATES
-------------------------------
```text
Linux service HEALTHY
Windows tunnel missing
```
and explicitly NOT:

```text
LIT-0104 unhealthy → restart container
```

WHY THIS IS AN ARCHITECTURE TEST AND NOT AN INTEGRATION TEST
------------------------------------------------------------
There is nothing to integrate against. The tunnel is gone, the Windows device
is not reachable from here, and §0 forbids restarting anything. What can be
tested — and what the brief calls "必须通过的 architecture test" — is whether
the composition can REPRESENT that pair of facts at all, and whether the
obvious wrong representation is reachable.

It is worth being precise about why the wrong one is reachable in most systems,
because it is not stupidity, it is the default shape of a health check:

    the thing the user asked about is LIT-0104
    the thing that is broken is a tunnel nobody registered
    so the only object with a fault on it is the one the user named

A system with no representation of the tunnel collapses those two into
"LIT-0104 is unhealthy", and the action attached to "unhealthy" is the action
in the runbook, which is `restart`. The restart then does nothing, because the
container was never the broken part — and the case has an extra edge: LIT-0104
is a PROTECTED parallel lane, so the restart is also forbidden outright.

THE THREE ASSERTIONS
--------------------
  1. Both facts are representable AT ONCE, in one report, with the Linux side
     healthy and the Windows side missing.
  2. The fault is attributed to the tunnel and to the device it is missing
     from, not to the container.
  3. No automatic action anywhere in the report names a LIT-0104 container.

The third is written as a property over the whole rendered report rather than
as a check on the one field that currently carries actions, so that a new
field which reintroduces the action fails here instead of shipping.

NOTE ON THE ENDPOINTS. The fixture uses a local port and a remote container
name, and they are NOT offered as LIT-0104's real endpoints: nothing on this
host records them, and §38's rule — do not guess an owner you cannot confirm —
applies to endpoints for the same reason. See the note written into
`state/serveros/runtime/registry/resources.yaml`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"

LINUX_CONTAINER = "lit-0104-dev100-litellm"
LINUX_POSTGRES = "lit-0104-dev100-postgres"
TUNNEL = "tunnel-lit-0104-api"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")
health = _load("serveros_runtime_health", "health.py")
infra = _load("serveros_runtime_infrastructure", "infrastructure.py")
probes = _load("serveros_runtime_probes", "probes.py")


def health_of(resource, observations: dict) -> "health.Health":
    """A resource's four axes, assembled the way `probes.observe` assembles
    them: the axes that were observed as given, and the rest through
    `probes._axis_default`.

    Using the production default rather than `health.unknown()` matters more
    than it looks. A container declares no transport probe, so its transport
    axis is `NOT_DECLARED` — "the axis is outside what this resource declares
    it can be asked", which is NOT a fault and which `Health.reason`
    deliberately skips. Handing it `NOT_OBSERVED` instead would attach the
    reason "we asked and got nothing" to a resource nobody asked, and the
    assertion that the Linux container carries no fault would fail on a
    container that is entirely fine.
    """
    axes = {axis: observations.get(axis) or probes._axis_default(axis, resource)
            for axis in probes._PROBE_ORDER}
    return health.Health(desired=resource.desired, **axes)


# ---------------------------------------------------------------------------
# The registry, as it would be authored on the Linux node
# ---------------------------------------------------------------------------


def a_container(rid: str, container: str) -> dict:
    return {
        "id": rid,
        "kind": "test-runtime",
        "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
        "scope": "user",
        "lifecycle": {"class": "task-scoped"},
        "desired": {"state": "running"},
        # Protected, because that is how the live registry records this lane
        # and because it is the fact that makes "restart it" forbidden rather
        # than merely wrong.
        "protection": {"level": "protected"},
        "probes": [{"id": f"{rid}-liveness", "kind": "container-state",
                    "axis": "liveness", "container": container},
                   {"id": f"{rid}-functional", "kind": "container-healthcheck",
                    "axis": "functional", "container": container}],
    }


def the_tunnel(**overrides) -> dict:
    """LIT-0104's tunnel, as Case A should have registered it.

    `origin_device` is the Windows node and it is the load-bearing field: the
    tunnel is a resource OF the device it starts on, and this one starts on the
    device that is not here. Without it the report can say the tunnel is gone
    and not say where the failure is, which is half the acceptance.
    """
    record = {
        "id": TUNNEL,
        "kind": "tunnel",
        "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
        "scope": "user",
        "lifecycle": {"class": "task-scoped"},
        "desired": {"state": "running"},
        "protection": {"level": "protected"},
        "origin_device": "device:work-fj",
        "transport": {"class": "ssh", "local": "127.0.0.1:26400",
                      "remote": "container:lit-0104-dev100-litellm:4000"},
        "probes": [
            {"id": "lit0104-fwd-liveness", "kind": "process-exists",
             "axis": "liveness", "pattern": "ssh -L 26400"},
            {"id": "lit0104-fwd-chan", "kind": "socket-connect",
             "axis": "transport", "path": "/tmp/lit0104-forward.sock"},
        ],
        "not_applicable": {"functional": {"because":
            "a port forward has no callable surface of its own; a completed "
            "TCP handshake through it is the strongest thing measurable, and "
            "it is measured on the transport axis"}},
    }
    record.update(overrides)
    return record


def the_registry(*extra) -> "registry.Registry":
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [a_container("lit0104-dev-litellm", LINUX_CONTAINER),
                      a_container("lit0104-dev-postgres", LINUX_POSTGRES),
                      the_tunnel(), *extra],
    })


def healthy_linux(*, observed_at="2026-09-13T04:00:00Z") -> dict:
    """What the Linux side actually measured, throughout the incident."""
    def ok(probe, state, evidence="host-measurement"):
        return health.Observation(axis=probe["axis"], state=state,
                                  observed_by={"type": "probe", "id": probe["id"]},
                                  observed_at=observed_at,
                                  evidence={"type": evidence})
    return {
        "lit0104-dev-litellm": {
            "liveness": ok(a_container("x", LINUX_CONTAINER)["probes"][0], "RUNNING"),
            "functional": ok(a_container("x", LINUX_CONTAINER)["probes"][1], "OK",
                             "declared-healthcheck"),
        },
        "lit0104-dev-postgres": {
            "liveness": ok(a_container("x", LINUX_POSTGRES)["probes"][0], "RUNNING"),
            "functional": ok(a_container("x", LINUX_POSTGRES)["probes"][1], "OK",
                             "declared-healthcheck"),
        },
    }


def windows_tunnel_gone(*, observed_at="2026-09-13T04:00:00Z") -> dict:
    """What the Windows side would have measured, if anyone had asked it.

    `STOPPED` on liveness and `DISCONNECTED` on transport, both established by
    measurement on the device that owns the tunnel. Nothing here is inferred
    from a log, and nothing here was measured from this node — which is the
    whole reason §59's handoff exists.
    """
    return {
        TUNNEL: {
            "liveness": health.Observation(
                axis="liveness", state="STOPPED",
                observed_by={"type": "probe", "id": "lit0104-fwd-liveness",
                             "where": "device:work-fj"},
                observed_at=observed_at, evidence={"type": "host-measurement"},
                reason="PROCESS_DOWN"),
            "transport": health.Observation(
                axis="transport", state="DISCONNECTED",
                observed_by={"type": "probe", "id": "lit0104-fwd-chan",
                             "where": "device:work-fj"},
                observed_at=observed_at, evidence={"type": "host-measurement"},
                reason="CHANNEL_DISCONNECTED"),
        },
    }


def whole_report(**kwargs):
    """One call, one document. `view.anomaly_list` is the surface a reader
    would actually consult, so the acceptance is asserted on it rather than on
    the internals each of its sections is built from."""
    view = _load("serveros_runtime_view", "view.py")
    return view.anomaly_list(registry=the_registry(), **kwargs)


# ---------------------------------------------------------------------------
# 1 · Both facts, at once
# ---------------------------------------------------------------------------


class BothFactsAtOnceTests(unittest.TestCase):
    def test_the_linux_side_is_healthy_and_the_windows_tunnel_is_missing(self):
        """The acceptance sentence, asserted field by field.

        This is the test the brief asks for. It fails on any build where the
        two facts cannot coexist — which is the build where the report has to
        pick one of them, and picks the one the user named.
        """
        reg = the_registry()
        observations = {**healthy_linux(), **windows_tunnel_gone()}

        # The health model, applied to each object independently and in the
        # same pass. Neither reading consults the other.
        linux_health = health_of(reg.get("lit0104-dev-litellm"),
                                 observations["lit0104-dev-litellm"])
        self.assertEqual(linux_health.state, "HEALTHY")
        self.assertIsNone(linux_health.reason)

        tunnel_health = infra.tunnel_health(reg.get(TUNNEL), observations[TUNNEL])
        self.assertNotEqual(tunnel_health.state, "HEALTHY")
        self.assertIn(tunnel_health.state, ("NOT_RUNNING", "DEGRADED"))
        self.assertIn(tunnel_health.reason, ("CHANNEL_DISCONNECTED", "PROCESS_DOWN"))

        # Both are in ONE registry, so nothing had to choose between them.
        self.assertIsNotNone(reg.get("lit0104-dev-litellm"))
        self.assertIsNotNone(reg.get(TUNNEL))

    def test_the_container_being_healthy_is_not_what_makes_the_report_healthy(self):
        """The positive control for the pair above: with the tunnel restored,
        the tunnel reports healthy; with it gone, only the tunnel changes. If
        the Linux readings moved when the tunnel did, the two facts would not
        be independent and the report would be describing one thing twice."""
        reg = the_registry()
        tunnel = reg.get(TUNNEL)

        gone = infra.tunnel_health(tunnel, windows_tunnel_gone()[TUNNEL])
        back = infra.tunnel_health(tunnel, {
            "liveness": health.Observation(
                axis="liveness", state="RUNNING",
                observed_by={"type": "probe", "id": "lit0104-fwd-liveness"},
                observed_at="2026-09-13T04:00:00Z",
                evidence={"type": "host-measurement"}),
            "transport": health.Observation(
                axis="transport", state="CONNECTED",
                observed_by={"type": "probe", "id": "lit0104-fwd-chan"},
                observed_at="2026-09-13T04:00:00Z",
                evidence={"type": "host-measurement"}),
        })
        self.assertNotEqual(gone.state, back.state)
        self.assertEqual(back.state, "DEGRADED")  # functional is NOT_APPLICABLE
        # The Linux readings are identical in both worlds. They are not a
        # function of the tunnel, and a report that made them one would be
        # blaming the healthy side.
        self.assertEqual(healthy_linux(), healthy_linux())


# ---------------------------------------------------------------------------
# 2 · The fault is attributed to the tunnel, and to the device it is gone from
# ---------------------------------------------------------------------------


class AttributionTests(unittest.TestCase):
    def test_the_tunnel_report_names_both_endpoints_and_where_it_starts(self):
        """`declared_tunnels` carries the two endpoints because a registration
        without them was refused; what this asserts is that the ORIGIN device
        survives into the report, because that is what turns "the tunnel is
        missing" into "the tunnel is missing from the Windows node"."""
        declared = infra.declared_tunnels(the_registry())
        self.assertEqual([t["id"] for t in declared], [TUNNEL])
        entry = declared[0]
        self.assertEqual(entry["local"], "127.0.0.1:26400")
        self.assertEqual(entry["remote"], "container:lit-0104-dev100-litellm:4000")
        self.assertEqual(entry["transport_probe"], "lit0104-fwd-chan")
        self.assertEqual(entry["functional_probe"], None)
        self.assertIn("no callable surface", entry["functional_not_applicable_because"])

        self.assertEqual(the_registry().get(TUNNEL).record["origin_device"],
                         "device:work-fj")

    def test_the_container_carries_no_fault_to_report(self):
        """The other half of attribution, and the one the incident turned on.
        Nothing about the Linux container is wrong, and a report that gives it
        a reason has invented one."""
        reg = the_registry()
        observations = healthy_linux()
        for rid in ("lit0104-dev-litellm", "lit0104-dev-postgres"):
            h = health_of(reg.get(rid), observations[rid])
            self.assertEqual(h.state, "HEALTHY")
            self.assertIsNone(h.reason, f"{rid} carries reason {h.reason!r}")


# ---------------------------------------------------------------------------
# 3 · Nothing in the report proposes touching a LIT-0104 container
# ---------------------------------------------------------------------------


class NoActionAgainstTheContainerTests(unittest.TestCase):
    def test_a_protected_resource_is_never_automatable_at_any_health(self):
        """`automatability` is the permission split the action model reads, and
        `protected` is the one value no automatic path reaches — on any
        trigger, including low disk. Asserted before the action is, because an
        action model that is right and a permission model that is permissive
        is the same outage with better prose."""
        reg = the_registry()
        for rid in ("lit0104-dev-litellm", "lit0104-dev-postgres", TUNNEL):
            resource = reg.get(rid)
            self.assertEqual(resource.protection, "protected")
            split = resource.automatability()
            self.assertFalse(split["automatable"], f"{rid} is automatable")

    def test_a_stopped_tunnel_leaves_the_containers_with_no_action(self):
        """The pairing the brief forbids, asserted directly: tunnel gone, and
        the action for each Linux container is still NONE."""
        reg = the_registry()
        observations = healthy_linux()
        for rid in ("lit0104-dev-litellm", "lit0104-dev-postgres"):
            resource = reg.get(rid)
            h = health_of(resource, observations[rid])
            self.assertEqual(health.action_for(resource, h),
                             {"action": "NONE", "because": "state=HEALTHY"})

    def test_the_report_contains_no_restart_action_anywhere(self):
        """A property over the rendered document rather than over the field
        that currently carries actions. A new section that reintroduced an
        action would pass the two tests above and fail this one."""
        rendered = json.dumps(whole_report(), sort_keys=True)
        for forbidden in ("restart", "docker restart", "systemctl restart",
                          "recreate", "remove", "prune"):
            self.assertNotIn(forbidden, rendered.lower().replace("_", " "),
                             f"the report mentions {forbidden!r}")
        for container in (LINUX_CONTAINER, LINUX_POSTGRES):
            self.assertNotIn(container, rendered,
                             "the report names a live LIT-0104 container; a "
                             "resource ServerOS may not act on is not a subject "
                             "it should be volunteering")

    def test_the_reclaim_report_proposes_nothing_for_a_protected_lane(self):
        """§17: removal is a recommendation, and protection is checked before
        the owner is even consulted. Both containers and the tunnel are
        protected, so a report that proposes any of them is wrong on the one
        case where being wrong is destructive."""
        report = infra.reclaim_report(the_registry())
        self.assertEqual(report["candidates"], [])
        self.assertEqual(report["by_disposition"], {"NOT_CANDIDATE": 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
