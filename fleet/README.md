# ServerOS Fleet

ServerOS owns **device identity, environment role and connection topology** because these answer Environment questions: where am I, what machine is this, and how may another authorized agent reach it?

Ability Management does not duplicate this registry. It references `device_ref` and owns only capability desired/observed state for that device.

## Authority split

```text
serveros/fleet device registry        stable identity / role / public environment facts
~/.serveros/targets.yaml              local-only connection locator / credential references
~/.serveros/controller.yaml           local-only statement that a controller is callable here
state/serveros/fleet/<device>/        dynamic reachability / heartbeat / observations
ability-management fleet policy       desired capability packages/profiles
state/serveros/ability-management/    current ability materialization snapshot
```

No hostname, IP, SSH user/key path, token, machine-id, MAC, asset tag or hardware serial belongs in the public device registry.

## Files

```text
registry.yaml             device identity — authored, stable, public
profiles.yaml             semantic path roles, bound per device profile
transport-contract.yaml   identity rules, transport kinds, capability levels,
                          per-capability availability, provenance sources
mcp-contract.yaml         what each MCP surface owns, and what it must not
controller-contract.yaml   where the fleet controller actually is, the boundary
                          ServerOS keeps to it, and the connector shape if one
                          is ever wanted
transport.py              identity, availability, DeviceTransport
controller.py             controller declaration + external-surface description
remote_desktop_commander.py  first transport adapter
device-contract.yaml      what may not appear in the public registry
```

## Endpoint and controller are different roles

`desktop-commander remote` starts the **endpoint**: this machine becomes
controllable *by* the control plane. It dials out and listens on nothing. The
**controller** lives on the control plane and is consumed by external MCP
clients. A device can be online fleet-wide, with a healthy endpoint, while
this runtime has no route to it at all — that state is
`REMOTE_CONTROLLER_NOT_EXPOSED` and it is never `DEVICE_UNOBSERVED`.

## Enrollment

A discovered endpoint is not a device identity until explicitly enrolled or matched by cryptographic/stable evidence. Unknown mapping stays `UNRESOLVED`; never merge two device identities because their installed Skills look similar.

The current migration therefore does **not** assume `FJ-NJ-CL-250028 == dell-work` without an enrollment proof.