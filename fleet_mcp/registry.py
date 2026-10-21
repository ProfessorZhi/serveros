"""The device-identity boundary.

This module exists to make one thing true: **the Fleet surface does not have
its own idea of what a device is.** It imports `fleet/transport.py`'s registry
and `DeviceRef`, or it refuses to start.

The alternative was a small YAML reader here, and it was rejected on this
repository's own rule — two implementations of one policy are not redundancy,
they are an eventual disagreement, and the caller has no way to know which one
it just talked to. A second reader would agree with `fleet/transport.py` on the
day it was written and diverge the first time a device was renamed, at which
point `fleet_list_devices` and `device.list` would give different answers about
the same fleet and neither would look broken.

So the dependency is explicit and failure is loud: no `fleet/` on the path, no
Fleet surface. `fleet` ships inside the node-runtime artifact
(`plugin/bundle.yaml`: `fleet: path: fleet`), so this is satisfied by both the
source tree and an installed node.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .errors import BAD_REQUEST, INTERNAL, UNKNOWN_DEVICE, refuse

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:  # the canonical identity layer, or nothing
    from fleet.transport import (  # type: ignore
        Availability,
        CapabilityAvailability,
        Device,
        DeviceRef,
        DeviceRegistry,
        UnknownDevice,
        canonical_level,
    )
except Exception as exc:  # noqa: BLE001 - reported, not swallowed
    raise refuse(
        INTERNAL,
        f"the ServerOS fleet identity layer could not be imported from "
        f"{_REPO_ROOT / 'fleet'} ({type(exc).__name__}: {exc}). This surface "
        f"does not carry a second copy of device identity; without the "
        f"canonical one it has no fleet to report.",
    ) from None


def load_registry(path: str | Path | None = None) -> DeviceRegistry:
    """Load the authored registry, optionally from an override path.

    The override exists so a test can pin a known fleet without editing the
    repository, and so an operator can run this surface against a candidate
    registry. It is never used to *augment* the shipped registry: the whole
    fleet comes from one file.
    """
    if path is None:
        return DeviceRegistry()
    import yaml

    candidate = Path(path)
    if not candidate.exists():
        raise refuse(BAD_REQUEST, f"registry override not found at {candidate}")
    try:
        data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise refuse(BAD_REQUEST, f"registry override at {candidate} is not valid YAML: {exc}") from None
    return DeviceRegistry(data)


def resolve_or_refuse(registry: DeviceRegistry, value: str) -> Device:
    """Resolve a caller-supplied device reference, or refuse by name.

    Refused as UNKNOWN_DEVICE rather than BAD_REQUEST, which is the code the
    taxonomy reserves for exactly this: the ref did not resolve to an enrolled
    device. The distinction matters to the caller — BAD_REQUEST says "fix your
    arguments", and here the argument is well-formed and names something that
    simply is not in the fleet, so the fix is to read `fleet_list_devices`
    rather than to reread the schema.
    """
    try:
        return registry.resolve(value)
    except UnknownDevice as exc:
        raise refuse(UNKNOWN_DEVICE, str(exc)) from None


def authored_facts(device: Device) -> dict[str, Any]:
    """What the registry says about a device. Authored, never measured."""
    return {
        "device_ref": device.device_ref.ref,
        "device_id": device.device_ref.id,
        "display_name": device.display_name,
        "platform": device.platform,
        "os_family": device.os_family,
        "ownership": device.ownership,
        "roles": list(device.roles),
        "environment_profile": device.environment_profile,
        "agents": list(device.agents),
        "runtime_bindings": list(device.runtime_bindings),
        "legacy_ids": list(device.legacy_ids),
        "enrollment": device.enrollment,
        "authority": "authored: fleet/registry.yaml",
    }


def measured_availability(
    device: Device,
    *,
    provider_present: bool,
    reachable: bool,
    authorized: bool,
    is_local: bool,
) -> tuple[Availability, CapabilityAvailability, dict[str, Any]]:
    """Turn one provider probe into the contract's three separate answers.

    The point of routing this through `Availability` rather than emitting a
    boolean is that the three questions have genuinely different answers here,
    and the answers are what a consumer needs to know what to do next:

    *   the device is enrolled and this runtime holds no route to it
        (`NOT_EXPOSED`) — fix the binding;
    *   the device is enrolled, this runtime has a route and it did not answer
        (`NOT_EXPOSED` + the probe's own error) — fix the device or the tunnel;
    *   the device answered and refused the credential
        (`AVAILABLE` controller, `authorized: False`) — fix a token.

    Collapsing these into "device unavailable" is what makes an operator
    re-enrol a machine that was never missing.
    """
    if not provider_present:
        controller = "UNKNOWN"
        inventory = "UNAVAILABLE"
    elif reachable and authorized:
        controller = "AVAILABLE"
        inventory = "AVAILABLE"
    else:
        controller = "NOT_EXPOSED"
        inventory = "UNAVAILABLE"

    # `fleet_presence` is a fleet-plane fact — a relay or an operator console
    # observes it. Nothing local produces it, and inventing KNOWN_ONLINE from a
    # successful probe would be reporting a different measurement under this
    # name. The one exception is this host itself, which is online by
    # construction: it is answering the call.
    presence = "KNOWN_ONLINE" if is_local else "UNKNOWN"

    availability = Availability(
        device_ref=device.device_ref,
        fleet_presence=presence,
        local_agent_controller=controller,
        remote_inventory_from_this_runtime=inventory,
        evidence_source="fleet_mcp_provider_probe",
    )

    if controller == "AVAILABLE":
        caps = {
            "discover": "AVAILABLE",
            "inspect": "AVAILABLE",
            "read": "AVAILABLE",
            # Session control is not a filesystem write and not shell access:
            # the capability the provider actually exposes is "send a typed
            # message to a session it owns". Saying `write-workspace` here
            # would claim this runtime can write files on the device, which it
            # cannot, and nothing above would notice the overstatement.
            "execute-safe": "EXTERNAL_ONLY",
            "write-workspace": "NOT_GRANTED",
            "install-ability": "NOT_GRANTED",
            "system-change": "NOT_GRANTED",
            "gui-control": "NOT_GRANTED",
            "admin": "NOT_GRANTED",
        }
    else:
        caps = {}

    capability = CapabilityAvailability(
        device_ref=device.device_ref,
        transport_kind="cloudcli-gateway",
        endpoint="AVAILABLE" if provider_present else "ABSENT",
        capabilities=tuple((canonical_level(k), v) for k, v in caps.items()),
        evidence_source="fleet_mcp_provider_probe",
    )
    detail = {
        "provider_present": provider_present,
        "reachable": reachable,
        "authorized": authorized,
        "is_local": is_local,
    }
    return availability, capability, detail
