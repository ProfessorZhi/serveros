#!/usr/bin/env python3
"""The local control root — which device this host is, and how it reaches out.

`~/.serveros/` carries three small files and one deliberate absence:

    node.yaml        this host is device:<id>            (a binding)
    targets.yaml     where this host's transports point  (local-only routes)
    controller.yaml  a human authorized a controller     (usually ABSENT)
    (large state)    NOT here — `DATA_ROOT/state/serveros`

The rule this module enforces is narrow and load-bearing: **this root binds,
it never declares.** A file here may say "this machine is `device:work-fj`".
It may not say what `device:work-fj` *is*. Every attribute — display name,
platform, roles, profile, agents — is read from `fleet/registry.yaml` at read
time, so there is exactly one answer to "what is this device" and it lives
where the fleet keeps it.

That asymmetry is the whole design. When both layers may declare, they agree
on the day they are written and diverge silently afterwards, and the divergence
is undetectable because each layer is self-consistent. So `node.yaml` rejects
unknown keys at load rather than ignoring them: an attribute written here would
otherwise appear to have taken effect.

Absence is likewise reported as absence. A host with no `node.yaml` is
`NODE_UNBOUND` — a real state, not an error and not an empty fleet.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Load a sibling module by path, reusing an already-loaded copy.

    Reuse matters: loading the same file under a second module name produces
    a *second* class object, so `except UnknownDevice` would silently fail to
    catch a `UnknownDevice` raised by the other copy.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", "transport.py")
controller = _load("serveros_fleet_controller", "controller.py")

FleetError = transport.FleetError
DeviceRef = transport.DeviceRef
Device = transport.Device
DeviceRegistry = transport.DeviceRegistry
UnknownDevice = transport.UnknownDevice


class NodeUnbound(FleetError):
    """This host has no node binding.

    Not an error condition and emphatically not "the fleet is empty" — it says
    this machine has not been told which enrolled device it is. A caller that
    treats it as a failure ends up inventing an identity, which is the one
    thing this layer must never do.
    """


class NodeBindingInvalid(FleetError):
    """The binding exists but is not a binding.

    Raised when a file in the control root declares device *attributes*, or
    names a device the registry does not have. Either way the correct response
    is to fix the binding, never to adopt the file's version of the fleet.
    """


CONTROL_ROOT_CONTRACT = _HERE / "node-root-contract.yaml"
NODE_FILE = "node.yaml"
TARGETS_FILE = "targets.yaml"


def control_root_contract() -> dict:
    return transport.load_yaml(CONTROL_ROOT_CONTRACT)


def control_root(path: str | Path | None = None) -> Path:
    """The local control root, expanded.

    Overridable because every test of this module must run against a temporary
    directory. A resolver whose only target is the real `~/.serveros` cannot be
    tested without editing the machine it is testing.
    """
    if path is not None:
        return Path(path).expanduser()
    configured = control_root_contract()["root"]["default"]
    return Path(os.path.expanduser(configured))


def _allowed_node_fields() -> tuple[str, ...]:
    spec = control_root_contract()["files"][NODE_FILE]
    return tuple(spec["required_fields"]) + tuple(spec.get("optional_fields") or ())


def _forbidden_keys() -> tuple[str, ...]:
    """Everything the control root may not carry.

    The union of what a transport binding may not carry and what a controller
    declaration may not carry. Both are already declared elsewhere; this only
    refuses to let the control root be the place where either rule is dodged.
    """
    return tuple(
        set(transport.FORBIDDEN_BINDING_KEYS) | set(controller.forbidden_controller_keys())
    )


def _reject_credentials(data, where: str) -> dict:
    forbidden = _forbidden_keys()

    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in forbidden:
                    raise NodeBindingInvalid(
                        f"{where} may not carry {trail}{key!r}. A control root "
                        f"records which device this is and where its transports "
                        f"point; credentials live in the transport's own store."
                    )
                walk(value, f"{trail}{key}.")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}].")

    walk(data)
    return data


@dataclass(frozen=True)
class LocalNode:
    """This host, bound to its enrolled device.

    `device` is read from the registry, not from the binding. Nothing in the
    binding is copied into it, because a copied attribute is a second answer.
    """

    device: Device
    root: Path
    binding_provenance: str = "local_control_root"

    @property
    def device_ref(self) -> DeviceRef:
        return self.device.device_ref

    def show(self) -> dict:
        """The identity half of `device.show`, without any transport detail.

        Contains no hostname, no transport name and no uuid — those are routes,
        and a caller that receives one in this shape will key on it.
        """
        return {
            "device_ref": self.device.device_ref.ref,
            "display_name": self.device.display_name,
            "platform": self.device.platform,
            "os_family": self.device.os_family,
            "ownership": self.device.ownership,
            "roles": list(self.device.roles),
            "environment_profile": self.device.environment_profile,
            "agents": list(self.device.agents),
            "enrollment": self.device.enrollment,
            "identity_source": "fleet/registry.yaml",
            "bound_locally_by": str(self.root / NODE_FILE),
        }


def read_node_binding(root: str | Path | None = None) -> dict | None:
    """Read `node.yaml` raw, or None when this host is unbound.

    Unknown keys are rejected, not ignored. See the module docstring: a
    silently ignored attribute looks exactly like one that took effect.
    """
    path = control_root(root) / NODE_FILE
    if not path.exists():
        return None
    data = transport.load_yaml(path) or {}
    _reject_credentials(data, NODE_FILE)
    allowed = _allowed_node_fields()
    unexpected = sorted(set(data) - set(allowed))
    if unexpected:
        raise NodeBindingInvalid(
            f"{NODE_FILE} declares {unexpected}, which are device attributes. "
            f"Attributes are owned by fleet/registry.yaml; a second copy here "
            f"would agree today and diverge silently. Allowed: {list(allowed)}."
        )
    if "device_ref" not in data:
        raise NodeBindingInvalid(f"{NODE_FILE} is present but declares no device_ref")
    return data


def local_node(
    root: str | Path | None = None,
    registry: DeviceRegistry | None = None,
) -> LocalNode:
    """This host as an enrolled device.

    Raises `NodeUnbound` when this host has no binding, and
    `NodeBindingInvalid` when the binding names a device the fleet does not
    know. Neither case creates a device: enrolment is a human decision recorded
    in the registry, and a file in a home directory cannot perform one.
    """
    binding = read_node_binding(root)
    if binding is None:
        raise NodeUnbound(
            f"no {NODE_FILE} under {control_root(root)}: this host is not bound "
            f"to an enrolled device. That is a state, not a failure — and it is "
            f"not an empty fleet."
        )
    registry = registry if registry is not None else DeviceRegistry()
    try:
        device = registry.resolve(binding["device_ref"])
    except UnknownDevice as exc:
        raise NodeBindingInvalid(
            f"{NODE_FILE} binds this host to {binding['device_ref']!r}, which is "
            f"not an enrolled device. A local file cannot enrol one."
        ) from exc
    return LocalNode(device=device, root=control_root(root))


def load_local_bindings(root: str | Path | None = None) -> dict[str, dict]:
    """Transport bindings from this host's control root.

    Delegates to `transport.load_bindings`, which validates against the
    contract's forbidden-field list. Absent file → `{}`, which is a legitimate
    state: a fleet whose controller is not exposed has bindings it cannot use
    anyway.
    """
    path = control_root(root) / TARGETS_FILE
    if not path.exists():
        return {}
    return transport.load_bindings(path)


def local_controller_declaration(root: str | Path | None = None) -> dict | None:
    """Whether THIS runtime holds a callable controller surface.

    None means none was declared, which the contract spells
    `REMOTE_CONTROLLER_NOT_EXPOSED`. This function reads the declaration; it
    never infers one from an installed binary, a running process or an open
    socket, because none of those are callable surfaces.
    """
    return controller.declared_controller(control_root(root) / "controller.yaml")


def node_state(root: str | Path | None = None) -> dict:
    """One deterministic summary of this host's binding layer.

    Deterministic meaning: same inputs, same output. No clock, no pid, no
    hostname, nothing that differs between two runs on an unchanged machine —
    so a caller can diff two readings and have the difference mean something.
    """
    root_path = control_root(root)
    try:
        node = local_node(root_path)
        binding = {
            "state": "BOUND",
            "device_ref": node.device_ref.ref,
        }
    except NodeUnbound:
        binding = {"state": "NODE_UNBOUND", "device_ref": None}
    except NodeBindingInvalid as exc:
        binding = {"state": "NODE_BINDING_INVALID", "device_ref": None, "reason": str(exc)}

    declaration = local_controller_declaration(root_path)
    return {
        "schema": "serveros/node-state.v1",
        "control_root": str(root_path),
        "binding": binding,
        "transport_bindings": {
            "state": "BOUND" if load_local_bindings(root_path) else "NO_BINDINGS",
            "count": len(load_local_bindings(root_path)),
        },
        "controller": {
            "state": "DECLARED" if declaration else "REMOTE_CONTROLLER_NOT_EXPOSED",
        },
        "large_state_root": "DATA_ROOT/state/serveros",
    }
