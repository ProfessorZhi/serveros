#!/usr/bin/env python3
"""The fleet view: one device per row, and four levels of how much of it to say.

§13 names what a device row must carry. §12 names how much of it a caller may
get at once. This module is both, and the two are the same design: the fields
that cost nothing are the fields a startup read can afford, and the fields that
require reaching another machine are the ones a caller should have to ask for by
name.

WHAT THIS MODULE IS CAREFUL ABOUT
---------------------------------
`fleet/registry.yaml` declares that every enrolled device has a `claude-code`
and a `codex` agent. That declaration is about what each device is FOR. It is
not a measurement, and §13 names the exact sentence it must not produce: the
existence of a `device:work-fj` entry must not make this view claim Claude is
runnable there.

So declared agents and measured runtimes are different fields. `declared_agents`
comes from the registry and says what was authored. `agent_runtimes` and
`provider_readiness` come from an observation and say what was found. A device
this runtime could not reach has the first and not the second, and the second is
present-and-typed rather than absent: `providers: []` would read as "this device
has no providers", which is a claim nobody made.

Every list that could be empty is instead a state word with a reason. That is
§12's rule at this layer, and it is checked: `test_a_row_never_answers_unknown
_with_an_empty_list` walks the served envelope looking for an empty list in a
field that means "unknown".
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

CONTRACT = _HERE / "view-contract.yaml"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", _HERE / "transport.py")
interfaces_mod = _load("serveros_fleet_interfaces", _HERE / "interfaces.py")
target = _load("serveros_abilities_target", _ROOT / "abilities" / "target.py")


class ViewError(RuntimeError):
    pass


def contract() -> dict:
    return transport.load_yaml(CONTRACT)


def levels() -> tuple[str, ...]:
    return tuple(contract()["levels"]["order"])


def fields_for(level: str) -> frozenset[str]:
    """The exact field set a level serves. Not a filter over a bigger one."""
    table = contract()["levels"]["fields"]
    if level not in table:
        raise ViewError(f"{level!r} is not a disclosure level; known={list(levels())}")
    return frozenset(table[level])


def envelope_fields() -> frozenset[str]:
    """Keys the answer carries, as opposed to keys a device row carries.

    Kept apart from the per-level sets because the two answer different
    questions: `count` is how many rows came back, `platform` is a fact about a
    machine. One list containing both reads as though a device has a `count`.
    """
    return frozenset(contract()["levels"]["envelope"])


def cumulative_fields(level: str) -> frozenset[str]:
    """What a caller at `level` receives, in total.

    §12 describes L1 as a summary of L2 rather than a replacement for it, so a
    caller asking for L2 gets L1's fields too. The declared set stays per-level
    so the *cost* is visible; this is the union the envelope is checked against.
    """
    order = levels()
    index = order.index(level)
    out: set[str] = set()
    for name in order[: index + 1]:
        out |= fields_for(name)
    return frozenset(out)


def unknown(reason: str, *, detail: str | None = None) -> dict:
    """The shape every unestablished fact takes.

    One constructor, so that "we did not measure this" has one spelling across
    the view. A caller can branch on `state` without knowing which field it
    came from, and no field quietly differs in how it says "I don't know".
    """
    payload = {"state": "UNKNOWN", "reason": reason}
    if detail:
        payload["detail"] = detail
    return payload


def establish(value, *, reason: str) -> dict:
    """Wrap a measured value, or produce `unknown` when there is none."""
    if value is None or value == [] or value == {}:
        return unknown(reason)
    return {"state": "KNOWN", "value": value}


class FleetView:
    """Composes the read-only fleet interfaces with routing and session evidence.

    Every dependency is injectable. `routing` and `sessions` default to the real
    deployments, and a test that injects a stub gets a view that differs only in
    where the facts came from — which is the point, because the failure this
    module is about is a view that answers confidently about a machine nobody
    reached.
    """

    def __init__(self, *, interfaces=None, routing=None, sessions=None,
                 clock=None) -> None:
        self._interfaces = interfaces if interfaces is not None else interfaces_mod.Interfaces()
        # `routing` is the §7 router. It is where presence and node_runtime come
        # from for devices this runtime does not own, and it is the only object
        # here that knows the difference between "reachable and refused" and
        # "not reachable at all".
        self._routing = routing
        # `sessions` answers for ONE device: what is running there now. Local
        # for the node this process is on; typed-unavailable otherwise. It is a
        # callable rather than a store so the view cannot grow its own session
        # ledger, which would be a second record of what the providers own.
        self._sessions = sessions

    # -- facts, each from its own source ------------------------------------

    def _rows(self, device_refs) -> list:
        registry = self._interfaces._registry
        if device_refs is None:
            return list(registry.all())
        wanted = [str(ref) for ref in device_refs]
        return [registry.resolve(ref) for ref in wanted]

    def _observation(self, device_ref: str) -> dict:
        """Presence and node runtime for ONE device, from the router.

        With no router this returns two explicit unknowns rather than two
        defaults. A view with no way to look must not look like a view that
        looked and found nothing.

        The router answers in resolver shape — two maps keyed by device ref,
        each omitting what nobody observed — so an absent key is the honest
        "no transport reported this", not a missing lookup to be defaulted
        around.
        """
        reason = "no routing layer is bound to this view"
        if self._routing is None:
            return {"presence": unknown(reason), "node_runtime": unknown(reason)}
        try:
            observed = self._routing.evidence((device_ref,))
        except Exception as exc:  # a router that raised established nothing
            failed = f"the routing layer did not answer: {type(exc).__name__}"
            return {"presence": unknown(failed), "node_runtime": unknown(failed)}
        return {
            "presence": observed.get("presence", {}).get(device_ref)
                        or unknown("no transport observed this device's presence"),
            "node_runtime": observed.get("node_runtime", {}).get(device_ref)
                            or unknown("no transport observed a node runtime on "
                                       "this device"),
        }

    def _resolution(self, device_ref: str) -> dict:
        """The resolver's own device step, for one device.

        The reason word is NOT composed here. `abilities/target.py` owns that
        vocabulary and decides what an ABSENT node runtime is called; a view
        that spelled the word itself would be a second place where §8's
        vocabulary lives, and the second place is the one that drifts.

        Only the first step is read. Later steps refuse for reasons that are
        about a provider rather than about the device, and reporting one of
        those as this device's reachability would answer a question nobody
        asked with a fact from somewhere else.
        """
        reason = "no routing layer is bound to this view"
        if self._routing is None:
            return unknown(reason)
        request = {
            "device_ref": device_ref,
            # `session.inspect` because it is read-only: a probe must not need
            # an approval, and asking for one to draw a table would make the
            # cheapest level the one that wakes somebody up.
            "action": "session.inspect",
            "agent_runtime": f"agent-runtime:{self._probe_runtime(device_ref)}",
            "local_device_ref": self._routing.local_device_ref,
            "registry": self._interfaces._registry,
            **self._routing.evidence((device_ref,)),
        }
        try:
            record = target.resolve(request)
        except Exception as exc:
            return unknown(f"the resolver did not answer: {type(exc).__name__}")
        steps = record.get("steps") or []
        if not steps:
            return unknown("the resolver returned no steps")
        first = steps[0]
        return {
            "state": first.get("outcome") or "UNKNOWN",
            "step": first.get("step"),
            "reason": first.get("reason"),
            "detail": first.get("detail"),
            "route": record.get("target", {}).get("route"),
        }

    def _probe_runtime(self, device_ref: str) -> str:
        """Which runtime to ask the device step about.

        Any runtime answers the device question identically — the step runs
        before provider selection, and its inputs are the device's presence and
        node runtime. The first id the runtime contract declares is used, and
        it is read from the contract rather than written down, so a renamed
        runtime is a contract edit rather than a stale literal here.
        """
        ids = target.runtime_ids()
        if not ids:
            raise ViewError("abilities/agent-runtime-contract.yaml declares no runtimes")
        return ids[0]

    def _inventory(self, device_ref: str) -> dict:
        """Providers and their instances, once per reachable device.

        Reached only at L2. The rule the transport layer already follows is
        applied one level up: an empty `resolved` payload means this control
        plane asked nothing and learned nothing, which is UNKNOWN — not
        `providers: []`, which reads as "this device has no providers" and is a
        claim about a machine nobody reached.
        """
        if self._routing is None:
            return {"state": "UNKNOWN",
                    "reason": "no routing layer is bound to this view"}
        try:
            transport_for_device = self._routing.transport_for(device_ref)
        except Exception as exc:
            return {"state": "UNKNOWN",
                    "reason": f"no transport for {device_ref}: {type(exc).__name__}"}
        if transport_for_device is None:
            return {"state": "UNKNOWN",
                    "reason": f"no transport serves {device_ref}"}
        try:
            resolved = transport_for_device.resolve_inputs(device_ref, None)
        except Exception as exc:
            name = type(exc).__name__
            reason = getattr(exc, "reason", None) or name
            return {"state": "UNAVAILABLE", "reason": str(reason),
                    "detail": f"{name}: {exc}"}
        if not resolved:
            # The transport answered nothing, which is not the device answering
            # "none". Naming the distinction is the whole point of this branch.
            return {"state": "UNKNOWN",
                    "reason": "the transport returned no inventory; this control "
                              "plane has not asked the device anything"}
        return {"state": "KNOWN", "resolved": resolved}

    def _session_summary(self, device_ref: str) -> dict:
        if self._sessions is None:
            return unknown("no session source is bound to this view")
        try:
            return self._sessions(device_ref)
        except Exception as exc:
            return unknown(f"the session source did not answer: {type(exc).__name__}")

    # -- the row ------------------------------------------------------------

    def row(self, device, *, level: str) -> dict:
        ref = device.device_ref.ref
        availability = self._interfaces._availability(device.device_ref)
        axes = {
            "fleet_presence": availability.fleet_presence,
            "local_agent_controller": availability.local_agent_controller,
            "remote_inventory_from_this_runtime": (
                availability.remote_inventory_from_this_runtime
            ),
            "state": availability.state,
        }
        # L0. Identity and enrollment only — no observation, no clock.
        row: dict = {
            "device_ref": ref,
            "display_name": device.display_name,
            "platform": device.platform,
            "environment_profile": device.environment_profile,
            "enrollment": device.enrollment,
            "registered": True,
        }
        if level == "L0":
            return row

        observation = self._observation(ref)
        presence = observation.get("presence") or unknown("presence was not observed")
        node_runtime = observation.get("node_runtime") or unknown(
            "the node runtime was not observed")

        # L1. Reachability and what was authored, still without contacting the
        # device. `declared_agents` is named as a declaration so that a reader
        # has somewhere to look for the difference between it and what runs.
        row["reachability"] = presence
        row["last_seen"] = presence.get("observed_at") or None
        row["presence_provenance"] = presence.get("provenance") or "NOT_MEASURED"
        row["node_runtime"] = node_runtime
        row["declared_agents"] = list(device.agents)
        row["agent_runtimes"] = self._agent_runtimes(device)
        row["runtime_summary"] = self._runtime_summary(ref)
        if level == "L1":
            return row

        inventory = self._inventory(ref)
        # L2. Contact. Everything below this line can be slow, and everything
        # below it is typed rather than empty when the device did not answer.
        row["providers"] = self._providers(inventory)
        row["provider_instances"] = self._instances(inventory)
        row["provider_readiness"] = self._readiness(inventory)
        row["active_sessions"] = self._session_summary(ref)
        if level == "L2":
            return row

        # L3. Provenance. Every entry here answers "how do you know", and the
        # row's own fields are the claim each entry is about.
        row["field_provenance"] = self._field_provenance(row)
        row["declared_paths"] = self._interfaces._path_map(device)
        row["legacy_ids"] = list(device.legacy_ids)
        row["roles"] = list(device.roles)
        row["ownership"] = device.ownership
        row["os_family"] = device.os_family
        row["availability_axes"] = axes
        row["observation_sources"] = {
            "presence": row["presence_provenance"],
            "inventory": inventory.get("state"),
            "sessions": (row["active_sessions"] or {}).get("state"),
        }
        row["routing"] = self._routing_evidence(ref)
        return row

    # -- per-field derivations ---------------------------------------------

    def _agent_runtimes(self, device) -> dict:
        """Which Agent Runtimes the device is authored as running.

        Names are checked against `abilities/agent-runtime-contract.yaml`, not
        trusted from the registry's `agents` list. The two happen to agree
        today; a view that assumed they must would report a device as declaring
        a runtime that does not exist the first time they diverge.

        Two lists appear here, and this is the one place both are visible side
        by side, which is why neither is reconciled. `bindings` is the
        authority — `fleet/registry.yaml`'s `runtime_bindings` — and `declared`
        is `agents`, its older spelling and now a projection of it. The view
        reports them and does NOT compare them: the comparison is
        `BINDING_MEMBERSHIP_MISMATCH`, computed once in
        `abilities/runtime_binding.py` and raised by `abilities/runtime_parity.py`.
        A second comparison here would be a second answer to one question, and
        the first time the two implementations disagreed, the view would be the
        one a reader happened to see.

        Still L1, and still the declared half only: a binding says a family
        belongs on this device. On which surface, at which version, with which
        features — none of that is here, because none of it may be authored and
        nothing at this level has reached the machine to measure it.
        """
        contract_ids = tuple(target.runtime_ids())
        declared = [name for name in device.agents if name in contract_ids]
        bindings = list(device.runtime_bindings)
        return {
            "state": "DECLARED",
            "authority": "runtime_bindings",
            "binding_state": "BOUND" if bindings else "NOT_DECLARED",
            "bindings": bindings,
            "declared": declared,
            "not_a_runtime_contract_id": sorted(set(device.agents) - set(contract_ids)),
            "measured": unknown(
                "a binding is an authoring statement about which runtime "
                "families this device is for, not a measurement of what is "
                "installed on it or of what can run there; surface, version and "
                "feature observations come from `serverosctl device agents` and "
                "carry their own provenance"),
        }

    def _runtime_summary(self, device_ref: str) -> dict:
        """The one sentence a reader at L1 actually wants, kept honest.

        "Can this device run an agent" has three answers, not two: yes, no, and
        nobody asked. The third is the one a summary is tempted to round to
        either of the others, so it keeps its own state — and `runnable` is
        `false` or `null`, never `true`, because a device step that did not
        refuse is not a device that can run anything. Whether a provider would
        answer is an L2 question and is answered there.
        """
        resolution = self._resolution(device_ref)
        if resolution.get("state") == "REFUSED":
            return {
                "state": "UNAVAILABLE",
                "reason": resolution.get("reason"),
                "detail": resolution.get("detail"),
                "runnable": False,
            }
        if resolution.get("state") == "AGREED":
            return {
                "state": "UNKNOWN",
                "reason": None,
                "runnable": None,
                "detail": "the device step did not refuse; no provider has been "
                          "asked, so this is not yet an answer about running "
                          "anything",
            }
        return {
            "state": "UNKNOWN",
            "reason": resolution.get("reason"),
            "detail": resolution.get("detail"),
            "runnable": None,
        }

    def _inventory_payload(self, inventory: dict) -> dict | None:
        if inventory.get("state") != "KNOWN":
            return None
        return inventory.get("resolved") or {}

    def _providers(self, inventory: dict) -> dict:
        payload = self._inventory_payload(inventory)
        if payload is None:
            return {"state": inventory.get("state", "UNKNOWN"),
                    "reason": inventory.get("reason"),
                    "detail": inventory.get("detail")}
        rows = payload.get("providers")
        if not rows:
            # The device answered and listed none. That IS the answer, and it is
            # different from the device not answering — which is why it is
            # nested here rather than collapsed into the envelope above.
            return {"state": "NONE_REPORTED", "providers": []}
        return {"state": "KNOWN", "providers": [dict(row) for row in rows]}

    def _instances(self, inventory: dict) -> dict:
        payload = self._inventory_payload(inventory)
        if payload is None:
            return {"state": inventory.get("state", "UNKNOWN"),
                    "reason": inventory.get("reason")}
        return establish(payload.get("instances"),
                         reason="the device reported no provider instances")

    def _readiness(self, inventory: dict) -> dict:
        """Per-instance readiness, kept as a fresh observation.

        The resolver already downgrades a stale observation to UNKNOWN rather
        than calling the provider dead. This method does not second-guess that:
        it reports the `status` and `observed_at` the instance carried, so a
        reader can see the age instead of being handed a verdict.
        """
        payload = self._inventory_payload(inventory)
        if payload is None:
            return {"state": inventory.get("state", "UNKNOWN"),
                    "reason": inventory.get("reason")}
        instances = payload.get("instances") or []
        if not instances:
            return {"state": "UNKNOWN",
                    "reason": "the device reported no provider instance to be "
                              "ready or not ready"}
        return {
            "state": "OBSERVED",
            "instances": [
                {
                    "provider": instance.get("provider"),
                    "status": instance.get("status"),
                    "observed_at": instance.get("observed_at"),
                    "provenance": instance.get("status_provenance"),
                    "reason": instance.get("reason"),
                }
                for instance in instances
            ],
        }

    def _field_provenance(self, row: dict) -> dict:
        """Where each claim in this row came from, at the granularity offered.

        Not a hash and not a run reference: this release has no per-field
        evidence ledger, and inventing one here would be a second provenance
        store. It names the SOURCE of each field, which is what a reader needs
        in order to know which claim to re-check first.
        """
        registry_source = str(transport.REGISTRY_PATH.relative_to(transport.ROOT))
        return {
            "device_ref": registry_source,
            "enrollment": registry_source,
            "declared_agents": f"{registry_source} (declaration)",
            "agent_runtimes": "abilities/agent-runtime-contract.yaml (declaration)",
            "reachability": row.get("presence_provenance") or "NOT_MEASURED",
            "node_runtime": "the target device, through the §7 router",
            "providers": "the target device, through resolve-inputs",
            "provider_readiness": "the target device, per provider instance",
            "active_sessions": "the session source bound to this view",
            "not_measured_here": [
                "provider-native session state",
                "whether a provider would succeed if asked",
            ],
        }

    def _routing_evidence(self, device_ref: str) -> dict:
        """Which transport would carry a control request to this device.

        Named because §7's answer to "why can't you just run it there" is a
        transport fact, and a view that reported only `node_runtime: ABSENT`
        would leave a reader guessing which layer to fix.
        """
        if self._routing is None:
            return unknown("no routing layer is bound to this view")
        transport_for = self._routing.transport_for(device_ref)
        return {
            "state": "KNOWN",
            "transports": sorted(
                str(getattr(node, "name", type(node).__name__))
                for node in self._routing.transports),
            "serving_transport": (
                str(getattr(transport_for, "name", type(transport_for).__name__))
                if transport_for is not None else None),
            "local_device_ref": self._routing.local_device_ref,
            "is_local": device_ref == self._routing.local_device_ref,
        }

    # -- the envelope --------------------------------------------------------

    def render(self, *, level: str = "L1", device_refs=None) -> dict:
        if level not in levels():
            raise ViewError(f"{level!r} is not a disclosure level; known={list(levels())}")
        rows = [self.row(device, level=level) for device in self._rows(device_refs)]
        envelope: dict = {
            "state": "KNOWN" if rows else "FLEET_REGISTRY_EMPTY",
            "level": level,
            "fields": sorted(cumulative_fields(level)),
            "count": len(rows),
            "devices": rows,
            "note": (
                "A field this runtime did not establish is served as a state "
                "word with a reason, never as an empty list. An empty list is "
                "an answer, and it is the wrong one."
            ),
        }
        declared_envelope = envelope_fields()
        stray_envelope = set(envelope) - declared_envelope
        if stray_envelope:
            raise ViewError(
                f"the envelope served undeclared key(s) {sorted(stray_envelope)}; "
                f"{CONTRACT.name} lists the shape this level answers with"
            )
        # A row carrying a field its level did not declare is the failure
        # checked here, and it is the one that happens by accident — a field
        # added to `row()` for L2 is added to L0 and L1 too, and the cheap call
        # stops being cheap without anybody noticing.
        declared = cumulative_fields(level)
        for row in rows:
            stray = set(row) - declared
            if stray:
                raise ViewError(
                    f"{level} served undeclared field(s) {sorted(stray)}; the "
                    f"declared set is in {CONTRACT.name} and a level that grows "
                    f"a field silently stops being the cheap call"
                )
        return envelope


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--level", default="L1", choices=list(levels()))
    parser.add_argument("--device", action="append", default=[])
    args = parser.parse_args(argv)
    view = FleetView()
    json.dump(view.render(level=args.level, device_refs=args.device or None),
              sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
