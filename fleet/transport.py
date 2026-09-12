#!/usr/bin/env python3
"""ServerOS fleet identity, availability and the transport-neutral boundary.

Three things live here, and they are deliberately in one module because they
are one decision:

  1. Identity  — what a device IS (`device:<id>`), and what is merely how a
                 transport happens to name it.
  2. Availability — whether we can currently reach it, and *which* of the
                 three independent questions is failing.
  3. DeviceTransport — the interface every transport implements, so that
                 swapping Remote Desktop Commander for SSH, WinRM or A2A
                 changes nothing above this line.

Why one module
--------------
The failure this file exists to prevent is a caller reaching for the only
handle it has. If identity and reachability live in different modules, the
first thing that happens is that a hostname gets used as a key "just for now",
and afterwards nothing can tell which records were keyed by identity and which
by a route that later changed.

The availability states come from `fleet/transport-contract.yaml` rather than
from constants here, so the contract is the authority and cannot drift from
the code that implements it.
"""
from __future__ import annotations

import abc
import importlib.util
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read fleet contracts\n")
    raise SystemExit(2)

FLEET_DIR = Path(__file__).resolve().parent
ROOT = FLEET_DIR.parent


# ---------------------------------------------------------------------------
# Errors
#
# These are separated because they call for different responses. Conflating
# them is how "I cannot reach it from here" becomes "it does not exist".
# ---------------------------------------------------------------------------


class FleetError(Exception):
    """Base class for fleet-layer failures."""


class TransportUnavailable(FleetError):
    """This runtime cannot serve the call through the requested transport."""


class ControllerNotExposed(TransportUnavailable):
    """The transport exists, but no callable controller surface is exposed
    to this agent runtime.

    This says nothing about the device. The device may be online and fully
    reachable from another plane. It says this runtime has no route in.
    """


class DeviceUnreachable(FleetError):
    """A controller is available, but the device did not answer."""


class UnknownDevice(FleetError):
    """The reference does not resolve to an enrolled device."""


class UnknownPathRole(FleetError):
    """The profile does not declare this path role."""


class PathRoleUnbound(FleetError):
    """The role is declared for this profile but no physical path is bound."""


class PathRoleNotApplicable(PathRoleUnbound):
    """The role has no meaning on this device.

    Subclasses PathRoleUnbound so that a caller which only cares "there is no
    path" still catches it, while a caller asking "is this a gap" can tell
    "we have not bound it yet" apart from "it does not exist here".
    """


class CapabilityDenied(FleetError):
    """The requested capability level is not granted to this transport."""


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def transport_contract() -> dict:
    return load_yaml(FLEET_DIR / "transport-contract.yaml")


def registry_contract() -> dict:
    return load_yaml(FLEET_DIR / "registry.yaml")


def profiles_contract() -> dict:
    return load_yaml(FLEET_DIR / "profiles.yaml")


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

DEVICE_REF_PREFIX = "device:"


@dataclass(frozen=True, order=True)
class DeviceRef:
    """A stable, typed device reference — `device:<id>`.

    The same `{type, id}` shape AbilityRef uses. One relation vocabulary for
    abilities and devices means a policy that says "this ability runs on that
    device" cannot accidentally be expressed with two different key shapes.
    """

    id: str

    def __post_init__(self) -> None:
        pattern = re.compile(transport_contract()["identity"]["id_pattern"])
        if not pattern.fullmatch(self.id):
            raise ValueError(
                f"invalid device id {self.id!r}: must match "
                f"{transport_contract()['identity']['id_pattern']!r}"
            )

    @property
    def ref(self) -> str:
        return f"{DEVICE_REF_PREFIX}{self.id}"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.ref

    @classmethod
    def parse(cls, value: str) -> "DeviceRef":
        if not value.startswith(DEVICE_REF_PREFIX):
            raise ValueError(
                f"{value!r} is not a typed device ref. A bare id or a transport "
                f"name is not an identity; use '{DEVICE_REF_PREFIX}<id>'."
            )
        return cls(value[len(DEVICE_REF_PREFIX):])


@dataclass(frozen=True)
class Device:
    device_ref: DeviceRef
    display_name: str
    platform: str
    roles: tuple[str, ...]
    environment_profile: str
    agents: tuple[str, ...]
    legacy_ids: tuple[str, ...] = ()
    ownership: str | None = None
    os_family: str | None = None
    enrollment: str = "enrolled"


class DeviceRegistry:
    """Authored, stable device identity.

    Never rewritten by a scanner. Never keyed by anything a transport chose.
    """

    def __init__(self, data: dict | None = None) -> None:
        data = data if data is not None else registry_contract()
        self._devices: list[Device] = []
        self._by_id: dict[str, Device] = {}
        self._legacy: dict[str, Device] = {}
        for entry in data.get("devices") or []:
            dev = Device(
                device_ref=DeviceRef.parse(entry["device_ref"]),
                display_name=entry["display_name"],
                platform=entry["platform"],
                roles=tuple(entry.get("roles") or ()),
                environment_profile=entry["environment_profile"],
                agents=tuple(entry.get("agents") or ()),
                legacy_ids=tuple(entry.get("legacy_ids") or ()),
                ownership=entry.get("ownership"),
                os_family=entry.get("os_family"),
                enrollment=entry.get("enrollment", "enrolled"),
            )
            self._devices.append(dev)
            self._by_id[dev.device_ref.id] = dev
            for alias in dev.legacy_ids:
                self._legacy[alias] = dev

    def all(self) -> list[Device]:
        return list(self._devices)

    def resolve(self, value: str | DeviceRef) -> Device:
        """Resolve a device ref, or a historical bare id, to a Device.

        Historical ids are accepted so that earlier Installation and Revision
        evidence keeps resolving after a rename. They resolve to the same
        Device; they do not create a second one.
        """
        if isinstance(value, DeviceRef):
            value = value.ref
        if value.startswith(DEVICE_REF_PREFIX):
            did = value[len(DEVICE_REF_PREFIX):]
            if did in self._by_id:
                return self._by_id[did]
            raise UnknownDevice(f"no enrolled device with id {did!r}")
        if value in self._legacy:
            return self._legacy[value]
        raise UnknownDevice(
            f"{value!r} is not a device ref and not a known legacy id. "
            f"Transport names (hostnames, computer names, relay uuids) are not "
            f"identities and do not resolve here."
        )

    def by_platform(self, platform: str) -> list[Device]:
        return [d for d in self._devices if d.platform == platform]


# ---------------------------------------------------------------------------
# Profiles and path roles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathBinding:
    role: str
    path: str | None
    state: str
    provenance: str
    note: str | None = None


class Profiles:
    """Semantic path roles, bound to device-specific physical paths."""

    def __init__(self, data: dict | None = None) -> None:
        data = data if data is not None else profiles_contract()
        self.roles: tuple[str, ...] = tuple((data.get("path_roles") or {}).keys())
        self._profiles: dict[str, dict] = data.get("profiles") or {}

    def for_device(self, device: Device) -> dict:
        name = device.environment_profile
        if name not in self._profiles:
            raise FleetError(f"no profile {name!r} declared for {device.device_ref.ref}")
        return self._profiles[name]

    def resolve(self, device: Device, role: str) -> PathBinding:
        """Resolve a path ROLE for a device.

        Callers ask for the role. They never ask for the literal path, which is
        why the same call works on three machines with unrelated layouts.
        """
        profile = self.for_device(device)
        paths = profile.get("paths") or {}
        if role not in paths:
            raise UnknownPathRole(
                f"profile {device.environment_profile!r} does not declare role {role!r}"
            )
        entry = paths[role]
        state = entry.get("state", "BOUND")
        if state == "NOT_APPLICABLE":
            raise PathRoleNotApplicable(
                f"role {role!r} has no meaning on {device.device_ref.ref} "
                f"(NOT_APPLICABLE); it is not a missing binding"
            )
        if state == "UNBOUND" or not entry.get("path"):
            raise PathRoleUnbound(
                f"role {role!r} is UNBOUND on {device.device_ref.ref}: "
                f"no physical path is known yet"
            )
        return PathBinding(
            role=role,
            path=entry["path"],
            state=state,
            provenance=entry.get("provenance", "unknown"),
            note=entry.get("note"),
        )


# ---------------------------------------------------------------------------
# Transport bindings (local-only)
# ---------------------------------------------------------------------------

# Secrets must never be *described* by a binding. A binding says where a
# transport is; the transport holds its own credentials.
FORBIDDEN_BINDING_KEYS = tuple(
    transport_contract()["transport"]["forbidden_binding_fields"]
)


def validate_binding(binding: dict) -> dict:
    """Reject a transport binding that carries credentials.

    Raises rather than warns: a binding is the one object guaranteed to be
    copied between machines, pasted into a shell, and read by a future agent
    that has no idea it is looking at a live token.
    """
    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in FORBIDDEN_BINDING_KEYS:
                    raise ValueError(
                        f"transport binding may not carry {trail}{key!r}. "
                        f"Credentials belong to the transport, not to the "
                        f"binding that points at it."
                    )
                walk(value, f"{trail}{key}.")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}].")

    walk(binding)
    return binding


def load_bindings(path: Path | None = None) -> dict[str, dict]:
    """Load local-only transport bindings.

    Default location is the one named by the contract. Absent file means no
    bindings, which is a legitimate state and not an error: a fleet whose
    controller is not exposed has bindings that cannot be used anyway.
    """
    if path is None:
        configured = transport_contract()["transport"]["binding_location"]
        path = Path(os.path.expanduser(configured))
    if not Path(path).exists():
        return {}
    data = load_yaml(Path(path)) or {}
    bindings = data.get("bindings") or {}
    for ref, binding in bindings.items():
        validate_binding(binding)
    return bindings


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

FLEET_PRESENCE = ("KNOWN_ONLINE", "KNOWN_OFFLINE", "UNKNOWN")
CONTROLLER_STATES = ("AVAILABLE", "NOT_EXPOSED", "UNKNOWN")
INVENTORY_STATES = ("AVAILABLE", "UNAVAILABLE")


@dataclass(frozen=True)
class Availability:
    """Three independent answers, plus one derived word for convenience.

    Collapsing these into a single boolean is the bug this type exists to
    prevent: "I have no controller" and "the device is not there" are
    different, and only one of them justifies touching the device record.
    """

    device_ref: DeviceRef
    fleet_presence: str = "UNKNOWN"
    local_agent_controller: str = "UNKNOWN"
    remote_inventory_from_this_runtime: str = "UNAVAILABLE"
    evidence_source: str = "server_local_agent"

    def __post_init__(self) -> None:
        if self.fleet_presence not in FLEET_PRESENCE:
            raise ValueError(f"bad fleet_presence {self.fleet_presence!r}")
        if self.local_agent_controller not in CONTROLLER_STATES:
            raise ValueError(f"bad local_agent_controller {self.local_agent_controller!r}")
        if self.remote_inventory_from_this_runtime not in INVENTORY_STATES:
            raise ValueError(
                f"bad remote_inventory_from_this_runtime "
                f"{self.remote_inventory_from_this_runtime!r}"
            )

    @property
    def state(self) -> str:
        """The derived single state, ordered by the contract, not by this code.

        Reading the rules from the contract keeps one authority. A second copy
        of the ordering here would agree with the YAML until the day someone
        edits one of them.
        """
        for row in transport_contract()["availability"]["derived_states"]:
            when = row["when"]
            if all(getattr(self, k) == v for k, v in when.items()):
                return row["state"]
        return "DEVICE_UNOBSERVED"

    @property
    def observed(self) -> bool:
        """True only when this runtime actually inspected the device."""
        return self.remote_inventory_from_this_runtime == "AVAILABLE"


@dataclass(frozen=True)
class CapabilityAvailability:
    """Per-capability availability for one transport on one device.

    The single boolean this replaces had one failure mode, and it is the
    failure this round exists to correct: `endpoint: AVAILABLE` on this host
    is true while every capability is unreachable from this host, so any
    consumer handed one bit has to choose which truth to discard.

    Reporting EXTERNAL_ONLY is a claim about this runtime's reach. It is NOT a
    claim that the capability is absent from the product, and a consumer that
    folds it into "unavailable → unsupported" has thrown away the only fact
    that makes the gap actionable: that a specific, nameable step closes it.
    """

    device_ref: DeviceRef
    transport_kind: str
    endpoint: str = "UNKNOWN"
    # Sparse on purpose: a level nobody established is UNKNOWN, which is not
    # the same as a level someone checked and found NOT_GRANTED.
    capabilities: tuple[tuple[str, str], ...] = ()
    evidence_source: str = "server_local_agent"

    def __post_init__(self) -> None:
        if self.endpoint not in ENDPOINT_STATES:
            raise ValueError(
                f"bad endpoint state {self.endpoint!r}; expected one of {list(ENDPOINT_STATES)}"
            )
        for level, state in self.capabilities:
            if level not in CAPABILITY_LEVELS:
                raise ValueError(f"unknown capability level {level!r}")
            if state not in CAPABILITY_STATES:
                raise ValueError(
                    f"bad availability {state!r} for {level!r}; "
                    f"expected one of {list(CAPABILITY_STATES)}"
                )

    @property
    def _map(self) -> dict[str, str]:
        return dict(self.capabilities)

    def state(self, level: str) -> str:
        """One level's availability. An unstated level is UNKNOWN, not a no.

        Defaulting to a negative would make "we did not check" read as "it is
        not possible", which is the exact substitution this whole contract
        exists to prevent.
        """
        return self._map.get(canonical_level(level), "UNKNOWN")

    def states(self) -> dict[str, str]:
        """Every level, in contract order, with gaps filled as UNKNOWN."""
        present = self._map
        return {level: present.get(level, "UNKNOWN") for level in CAPABILITY_LEVELS}

    def available_here(self) -> tuple[str, ...]:
        """Levels this runtime can exercise right now."""
        return tuple(lv for lv in CAPABILITY_LEVELS if self.state(lv) == "AVAILABLE")

    def external_only(self) -> tuple[str, ...]:
        """Levels that exist, but not on a plane this runtime can call."""
        return tuple(lv for lv in CAPABILITY_LEVELS if self.state(lv) == "EXTERNAL_ONLY")

    def reachable_from_this_runtime(self) -> bool:
        """Whether ANY capability is actually callable here.

        Distinct from `endpoint == "AVAILABLE"`, and that distinction is the
        one that was missing.
        """
        return bool(self.available_here())


# ---------------------------------------------------------------------------
# The transport boundary
# ---------------------------------------------------------------------------

# Capability levels, weakest first, read from the contract rather than kept
# here. The list previously existed in this module AND in `mcp-contract.yaml`
# as `remote_execution.levels`; two declarations of one closed vocabulary
# agree exactly until the day someone edits one of them, and then the code
# grants a level the contract does not describe.
CAPABILITY_LEVELS = tuple(transport_contract()["capability_levels"]["order"])

# The brief's spelling of the same levels. Kept as an alias map rather than a
# second list, so there is one vocabulary with two spellings.
CAPABILITY_ALIASES = dict(transport_contract()["capability_levels"]["alias_of"])

# Per-capability availability. Not one boolean: "can this runtime reach that
# machine" and "can this runtime read a file there" have different answers,
# and EXTERNAL_ONLY is the value that keeps a real, reachable-from-elsewhere
# capability from being reported as a missing one.
CAPABILITY_STATES = tuple(transport_contract()["capability_availability"]["values"])
ENDPOINT_STATES = tuple(
    transport_contract()["capability_availability"]["endpoint"]["values"]
)


def canonical_level(level: str) -> str:
    """Map either spelling of a capability level onto the canonical one."""
    return CAPABILITY_ALIASES.get(level, level)


class DeviceTransport(abc.ABC):
    """Transport-neutral device access.

    Implementations answer "how do I reach it". They do not decide who may do
    what, and they do not decide what a device is.

    Deliberately absent: a generic `shell(device, arbitrary_command)` as the
    default agent-facing API. A transport may support shell internally, but the
    semantic layer above it admits calls by intent and capability level. An
    interface whose only method is "run anything anywhere" cannot be governed,
    because there is nothing in the call to govern.
    """

    kind: str = "abstract"

    def __init__(self, *, granted_levels: tuple[str, ...] = ()) -> None:
        # Either spelling is accepted and stored canonically, so a caller that
        # grants "READ_FS" and a caller that checks "read" cannot disagree
        # about what was granted.
        normalized = tuple(canonical_level(lv) for lv in granted_levels)
        unknown = set(normalized) - set(CAPABILITY_LEVELS)
        if unknown:
            raise ValueError(f"unknown capability levels: {sorted(unknown)}")
        self._granted = normalized

    @property
    def granted_levels(self) -> tuple[str, ...]:
        return self._granted

    def require(self, level: str) -> None:
        level = canonical_level(level)
        if level not in CAPABILITY_LEVELS:
            raise ValueError(f"unknown capability level {level!r}")
        if level not in self._granted:
            raise CapabilityDenied(
                f"{self.kind} transport was not granted {level!r}; "
                f"granted: {list(self._granted)}"
            )

    # -- availability ------------------------------------------------------

    @abc.abstractmethod
    def controller_available(self) -> bool:
        """Whether a callable controller surface exists for THIS runtime.

        A transport process being installed and running is not sufficient. A
        process that only dials outbound, and listens on no local socket,
        cannot be called by anything here.
        """

    def availability(self, device_ref: DeviceRef) -> Availability:
        if not self.controller_available():
            return Availability(
                device_ref=device_ref,
                # Presence is a fleet-plane fact. A controller-less runtime
                # does not get to downgrade it to UNKNOWN; it simply has no
                # local evidence either way.
                fleet_presence="UNKNOWN",
                local_agent_controller="NOT_EXPOSED",
                remote_inventory_from_this_runtime="UNAVAILABLE",
            )
        return Availability(
            device_ref=device_ref,
            fleet_presence="KNOWN_ONLINE",
            local_agent_controller="AVAILABLE",
            remote_inventory_from_this_runtime="AVAILABLE",
        )

    def capability_availability(self, device_ref: DeviceRef) -> "CapabilityAvailability":
        """Per-capability availability, defaulting to "not established".

        The base class deliberately answers UNKNOWN for every level and claims
        nothing about EXTERNAL_ONLY. Only a transport that has actually located
        a controller surface on another plane may say EXTERNAL_ONLY, because
        saying it asserts that the capability exists somewhere — and a base
        class has no way to know that.
        """
        endpoint = "AVAILABLE" if self.controller_available() else "UNKNOWN"
        return CapabilityAvailability(
            device_ref=device_ref,
            transport_kind=self.kind,
            endpoint=endpoint,
        )

    # -- operations --------------------------------------------------------

    @abc.abstractmethod
    def list_devices(self, **kwargs) -> list[dict]:
        """Devices this transport can see, as opaque transport-side records."""

    @abc.abstractmethod
    def inspect_device(self, device_ref: DeviceRef, **kwargs) -> dict:
        """Environment facts for one device."""

    @abc.abstractmethod
    def list_directory(self, device_ref: DeviceRef, path: str, depth: int = 1, **kwargs) -> list[dict]:
        """Depth-aware directory inventory."""

    @abc.abstractmethod
    def read_file(self, device_ref: DeviceRef, path: str, **kwargs) -> bytes:
        """Read one file's bytes."""

    @abc.abstractmethod
    def run_command(
        self,
        device_ref: DeviceRef,
        command: str,
        *,
        level: str = "execute-safe",
        **kwargs,
    ) -> dict:
        """Run a command, admitted at an explicit capability level."""
