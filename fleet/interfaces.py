#!/usr/bin/env python3
"""Deterministic, read-only node and fleet interfaces.

Nine calls, declared in `node-interface-contract.yaml` and implemented here.
Two properties are load-bearing and every one of them holds both:

**Answerable with no controller.** `device.show`, `environment.inspect`,
`path.resolve`, `fleet.devices` and the rest read the local control root, the
fleet registry and recorded observations. None of them needs another machine.
That is deliberate: if establishing "this node is ready" required reaching a
remote device, then a broken relay would read as an unprepared host, and the
two failures would be debugged as one.

**Absence has a name.** No interface returns an empty list to mean "unknown",
because an empty list is itself an answer — a wrong one that looks right.
A device nobody scanned has no ability list; a runtime with no controller has
the same fleet it always had, with `local_agent_controller: NOT_EXPOSED`.
`OBSERVATION_UNAVAILABLE` and `NODE_UNBOUND` are results, not errors.

Determinism is a testable property here, not a slogan: no clock, no pid, no
hostname, and no ordering that depends on directory enumeration. Two readings
of an unchanged host compare equal, so when two readings differ the difference
means something.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", "transport.py")
node = _load("serveros_fleet_node", "node.py")

FleetError = transport.FleetError
DeviceRef = transport.DeviceRef
DeviceRegistry = transport.DeviceRegistry
Profiles = transport.Profiles
Device = transport.Device

INTERFACE_CONTRACT = _HERE / "node-interface-contract.yaml"


class InterfaceError(FleetError):
    """Base class for interface-layer failures."""


class UnknownInterface(InterfaceError):
    """The call is not declared in the node interface contract.

    Raised rather than returning a generic payload. An interface that exists
    because someone called it is an interface nobody reviewed.
    """


class InterfaceNotImplemented(InterfaceError):
    """The call is declared, and explicitly not implemented this round.

    A different failure from `UnknownInterface`, and the difference is the
    whole reason the contract lists these by name: "we decided not to" and
    "nobody built it" call for different responses.
    """


def interface_contract() -> dict:
    return transport.load_yaml(INTERFACE_CONTRACT)


def declared_interfaces() -> tuple[str, ...]:
    return tuple(interface_contract()["interfaces"])


def not_implemented() -> tuple[str, ...]:
    return tuple(interface_contract()["not_implemented"])


class Interfaces:
    """The read-only node/fleet surface, bound to explicit inputs.

    Every input is injectable and defaults to the real one. A resolver that
    can only read the live machine cannot be tested without editing the machine
    it is testing, which is how a suite goes green while the thing it describes
    is broken.
    """

    def __init__(
        self,
        *,
        registry: DeviceRegistry | None = None,
        profiles: Profiles | None = None,
        control_root: str | Path | None = None,
        observation_roots: tuple[Path, ...] | None = None,
        presence: dict[str, dict] | None = None,
    ) -> None:
        self._registry = registry if registry is not None else DeviceRegistry()
        self._profiles = profiles if profiles is not None else Profiles()
        self._root = control_root
        self._observation_roots = observation_roots
        # Fleet-plane presence, supplied as evidence rather than assumed.
        # Shape: {device_ref: {"state": KNOWN_ONLINE, "provenance": <source>}}.
        # Absent means UNKNOWN — never KNOWN_ONLINE by default, because a
        # default of "online" is a claim this runtime has not earned.
        self._presence = presence or {}

    # -- internals ---------------------------------------------------------

    def _device(self, device_ref: str | DeviceRef) -> Device:
        return self._registry.resolve(device_ref)

    def _presence_for(self, ref: str) -> tuple[str, str]:
        entry = self._presence.get(ref) or {}
        return entry.get("state", "UNKNOWN"), entry.get("provenance", "NOT_MEASURED")

    def _controller_state(self) -> str:
        declared = node.local_controller_declaration(self._root)
        return "AVAILABLE" if declared else "NOT_EXPOSED"

    def _availability(self, device_ref: DeviceRef) -> transport.Availability:
        """The three axes, each from its own source.

        Presence is a fleet-plane fact supplied as evidence; controller and
        inventory are this runtime's own. A runtime that answered all three
        would be claiming to see a plane it has no route to.
        """
        presence, _ = self._presence_for(device_ref.ref)
        controller = self._controller_state()
        return transport.Availability(
            device_ref=device_ref,
            fleet_presence=presence,
            local_agent_controller=controller,
            remote_inventory_from_this_runtime=(
                "AVAILABLE" if controller == "AVAILABLE" else "UNAVAILABLE"
            ),
            evidence_source="server_local_agent",
        )

    def _available_transport_kinds(self) -> tuple[str, ...]:
        """Transport kinds that can reach a device from this runtime.

        Empty when no controller is declared — and note the empty tuple means
        "no route from here", not "no transports exist". The contract's list of
        kinds is a separate, non-empty fact.
        """
        declared = node.local_controller_declaration(self._root)
        if declared is None:
            return ()
        return tuple(transport.transport_contract()["transport"]["kinds"])

    def _path_map(self, device: Device) -> dict[str, dict]:
        """Every path role for a device, with its binding state.

        Roles the profile does not declare are reported as UNKNOWN rather than
        omitted, so a role this profile has never heard of is visible as a
        question instead of an absence.
        """
        profile = self._profiles.for_device(device)
        declared = profile.get("paths") or {}
        out: dict[str, dict] = {}
        for role in self._profiles.roles:
            entry = declared.get(role)
            if entry is None:
                out[role] = {"state": "NOT_DECLARED_FOR_PROFILE", "path": None}
                continue
            state = entry.get("state", "BOUND")
            out[role] = {
                "state": state,
                "path": entry.get("path"),
                "provenance": entry.get("provenance", "unknown"),
            }
            if entry.get("note"):
                out[role]["note"] = entry["note"]
        return out

    def _resolved_observation_roots(self) -> tuple[Path, ...]:
        if self._observation_roots is not None:
            return self._observation_roots
        env = os.environ.get("SERVEROS_ABILITY_OBSERVATIONS")
        candidates = [Path(env)] if env else []
        candidates += [Path(p) for p in interface_contract().get("observation_roots") or []]
        return tuple(p for p in candidates if p.exists())

    def _observations_for(self, device_ref: str) -> list[dict]:
        """Observation records for one device, newest first.

        Sorted by `observed_at`, which is an ISO-8601 UTC string and therefore
        sorts lexically. Not by mtime: a checkout, a copy or a restore changes
        mtime, and the order of evidence must not be a property of the
        filesystem it happens to sit on.
        """
        records: list[dict] = []
        for root in self._resolved_observation_roots():
            for path in sorted(root.rglob("*.observed.yaml")):
                try:
                    rec = transport.load_yaml(path)
                except Exception:  # unreadable evidence is not evidence
                    continue
                if rec.get("device_ref") == device_ref:
                    rec["_record"] = str(path)
                    records.append(rec)
        records.sort(key=lambda r: str(r.get("observed_at") or ""), reverse=True)
        return records

    # -- node --------------------------------------------------------------

    def device_show(self) -> dict:
        """Which enrolled device this host is. No transport names, by design."""
        try:
            return node.local_node(self._root, self._registry).show()
        except node.NodeUnbound:
            return {
                "state": "NODE_UNBOUND",
                "device_ref": None,
                "identity_source": str(transport.REGISTRY_PATH.relative_to(transport.ROOT)),
                "note": (
                    "This host is not bound to an enrolled device. That is a "
                    "state, not a failure, and not an empty fleet."
                ),
            }

    def environment_inspect(self, device_ref: str | DeviceRef | None = None) -> dict:
        """The DECLARED environment of a device — not a scan of it."""
        device = self._device(device_ref) if device_ref is not None else node.local_node(
            self._root, self._registry
        ).device
        return {
            "device_ref": device.device_ref.ref,
            "environment_profile": device.environment_profile,
            "platform": device.platform,
            "paths": self._path_map(device),
            "provenance": "declared",
            "note": (
                "Every entry carries its own provenance. `user_reported` bindings "
                "are design inputs until a device-local scan replaces them, and "
                "are never evidence that the device was inspected."
            ),
        }

    def context_resolve(self, role: str, device_ref: str | DeviceRef | None = None) -> dict:
        """One capsule: identity, profile, one resolved role, and all roles.

        Assembled in one read so a caller cannot combine three reads taken at
        three moments and get a context that never existed.
        """
        device = (
            self._device(device_ref)
            if device_ref is not None
            else node.local_node(self._root, self._registry).device
        )
        capsule = {
            "device": {
                "device_ref": device.device_ref.ref,
                "display_name": device.display_name,
                "platform": device.platform,
                "roles": list(device.roles),
                "environment_profile": device.environment_profile,
                "agents": list(device.agents),
            },
            "role": role,
            "paths": self._path_map(device),
            "availability": self._availability(device.device_ref).state,
        }
        try:
            binding = self._profiles.resolve(device, role)
        except FleetError as exc:
            # Named, not raised: a caller assembling a context wants the gap in
            # the capsule, where it can be reported, rather than as an exception
            # that hides the parts of the capsule that did resolve.
            capsule["resolved"] = {"state": type(exc).__name__, "reason": str(exc)}
            return capsule
        capsule["resolved"] = {
            "state": "BOUND",
            "role": binding.role,
            "path": binding.path,
            "provenance": binding.provenance,
        }
        return capsule

    def path_resolve(self, role: str, device_ref: str | DeviceRef) -> dict:
        """One path role, or a named failure.

        `PathRoleUnbound` and `PathRoleNotApplicable` are deliberately distinct
        and both propagate. A caller told "no path" cannot tell whether to wait
        for a binding or to stop asking, and the two call for opposite actions.
        """
        device = self._device(device_ref)
        binding = self._profiles.resolve(device, role)
        return {
            "device_ref": device.device_ref.ref,
            "role": binding.role,
            "path": binding.path,
            "state": binding.state,
            "provenance": binding.provenance,
        }

    def ability_list(self, device_ref: str | DeviceRef) -> dict:
        """Abilities OBSERVED on a device, grouped by agent runtime.

        Returns `OBSERVATION_UNAVAILABLE` rather than an empty mapping when no
        run has looked. An empty mapping says "this device has no abilities" —
        a far stronger claim than "nobody checked", and the one a reader will
        act on.
        """
        ref = self._device(device_ref).device_ref.ref
        records = self._observations_for(ref)
        if not records:
            return {
                "device_ref": ref,
                "state": "OBSERVATION_UNAVAILABLE",
                "groups": None,
                "note": "No observation run has recorded this device's abilities.",
            }
        newest = records[0]
        return {
            "device_ref": ref,
            "state": "OBSERVED",
            "observed_at": newest.get("observed_at"),
            "observed_by": newest.get("observed_by") or "UNRECORDED",
            "transport": newest.get("transport") or "UNRECORDED",
            "provenance": newest.get("provenance") or "UNRECORDED",
            "raw_input": newest.get("raw_input"),
            "groups": newest.get("groups") or {},
            "record": newest.get("_record"),
        }

    def ability_status(self, device_ref: str | DeviceRef, ability: str) -> dict:
        """Which observed groups contain a named ability."""
        listing = self.ability_list(device_ref)
        if listing["state"] != "OBSERVED":
            return {
                "device_ref": listing["device_ref"],
                "ability": ability,
                "state": "UNKNOWN_NOT_OBSERVED",
            }
        found: dict[str, list[str]] = {}
        for agent, groups in (listing["groups"] or {}).items():
            for group, names in (groups or {}).items():
                if ability in (names or []):
                    found.setdefault(agent, []).append(group)
        return {
            "device_ref": listing["device_ref"],
            "ability": ability,
            "state": "OBSERVED_PRESENT" if found else "OBSERVED_ABSENT",
            "groups": {k: sorted(v) for k, v in sorted(found.items())},
            "observed_at": listing.get("observed_at"),
        }

    # -- fleet -------------------------------------------------------------

    def fleet_devices(self) -> dict:
        """Every enrolled device, with its three-axis availability.

        Never empty. A runtime with no controller lists the same devices it
        always had; the difference is the `local_agent_controller` value. This
        is the interface where "unavailable controller" would most plausibly
        become "empty fleet", which is why the rule is written into the
        contract rather than left to this docstring.
        """
        devices = []
        for device in self._registry.all():
            availability = self._availability(device.device_ref)
            presence, provenance = self._presence_for(device.device_ref.ref)
            devices.append(
                {
                    "device_ref": device.device_ref.ref,
                    "display_name": device.display_name,
                    "platform": device.platform,
                    "environment_profile": device.environment_profile,
                    "enrollment": device.enrollment,
                    "availability": {
                        "fleet_presence": availability.fleet_presence,
                        "fleet_presence_provenance": provenance,
                        "local_agent_controller": availability.local_agent_controller,
                        "remote_inventory_from_this_runtime": (
                            availability.remote_inventory_from_this_runtime
                        ),
                        "state": availability.state,
                    },
                }
            )
        return {
            "state": "KNOWN" if devices else "FLEET_REGISTRY_EMPTY",
            "count": len(devices),
            "devices": devices,
            "controller": self._controller_state(),
            "note": (
                "An unavailable controller is reported per device as "
                "local_agent_controller, never by shortening this list."
            ),
        }

    def fleet_device_inspect(self, device_ref: str | DeviceRef) -> dict:
        device = self._device(device_ref)
        availability = self._availability(device.device_ref)
        presence, provenance = self._presence_for(device.device_ref.ref)
        return {
            "device_ref": device.device_ref.ref,
            "identity": {
                "display_name": device.display_name,
                "platform": device.platform,
                "os_family": device.os_family,
                "ownership": device.ownership,
                "roles": list(device.roles),
                "environment_profile": device.environment_profile,
                "agents": list(device.agents),
                "legacy_ids": list(device.legacy_ids),
                "enrollment": device.enrollment,
            },
            "declared_paths": self._path_map(device),
            "availability": {
                "fleet_presence": availability.fleet_presence,
                "fleet_presence_provenance": provenance,
                "local_agent_controller": availability.local_agent_controller,
                "remote_inventory_from_this_runtime": (
                    availability.remote_inventory_from_this_runtime
                ),
                "state": availability.state,
            },
            "reachable_transport_kinds": list(self._available_transport_kinds()),
        }

    def fleet_capability_find(
        self, level: str, transport_kinds: tuple[str, ...] = ("remote-desktop-commander",)
    ) -> dict:
        """Where a capability level can be exercised, and by what route.

        Only the transport kinds named are reported, and only for the devices
        they are actually a route to. Reporting `ssh: EXTERNAL_ONLY` on a fleet
        with no SSH transport would be a claim about a route that does not
        exist; reporting it `UNKNOWN` would be a claim that nobody checked,
        which is also untrue — nobody intends to.

        So an unreported kind is simply not a column. Every enrolled device
        still appears, because EXTERNAL_ONLY filtered out would leave an empty
        result, and empty reads as "nowhere" when the truth is "not from here".
        """
        canonical = transport.canonical_level(level)
        if canonical not in transport.CAPABILITY_LEVELS:
            raise ValueError(
                f"unknown capability level {level!r}; expected one of "
                f"{list(transport.CAPABILITY_LEVELS)}"
            )
        known_kinds = tuple(transport.transport_contract()["transport"]["kinds"])
        unknown_kinds = sorted(set(transport_kinds) - set(known_kinds))
        if unknown_kinds:
            raise ValueError(f"unknown transport kinds: {unknown_kinds}")

        declared = node.local_controller_declaration(self._root)
        bound = node.load_local_bindings(self._root)
        rows = []
        for device in self._registry.all():
            by_kind: dict[str, str] = {}
            for kind in transport_kinds:
                is_bound = any(
                    (b.get("kind") == kind) for b in bound.values()
                )
                if declared is not None:
                    # A controller exists for this runtime; whether a given
                    # device is reachable through it is not established here.
                    by_kind[kind] = "UNKNOWN"
                elif is_bound:
                    # The endpoint role exists on this host and points at the
                    # fleet, but nothing here can call it: the surface is on the
                    # control plane, not on this machine.
                    by_kind[kind] = "EXTERNAL_ONLY"
                else:
                    by_kind[kind] = "UNKNOWN"
            rows.append({"device_ref": device.device_ref.ref, "availability": by_kind})

        states = {v for row in rows for v in row["availability"].values()}
        return {
            "level": canonical,
            "requested_as": level,
            "aliased": canonical != level,
            "devices": rows,
            "summary": sorted(states),
            "note": (
                "EXTERNAL_ONLY means the capability exists and is reachable from "
                "the control plane, not from this runtime. It is not 'unavailable' "
                "and it is not 'unsupported'."
            ),
        }

    # -- dispatch ----------------------------------------------------------

    def call(self, name: str, **kwargs) -> dict:
        """Invoke a declared interface by name.

        Both failure modes are explicit and distinct, and the two sets are
        asserted equal by the test suite in both directions — so a name added
        to `not_implemented` without being handled, or a name implemented while
        still listed there, is a test failure rather than a surprise at runtime.
        """
        if name in not_implemented():
            raise InterfaceNotImplemented(
                f"{name!r} is declared and deliberately not implemented this "
                f"round. Read-only discovery must be correct first. See "
                f"{INTERFACE_CONTRACT.name}."
            )
        if name not in declared_interfaces():
            raise UnknownInterface(
                f"{name!r} is not a declared node interface. Declared: "
                f"{list(declared_interfaces())}"
            )
        handler = getattr(self, name.replace(".", "_"))
        return handler(**kwargs)
