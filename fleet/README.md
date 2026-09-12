# ServerOS Fleet

ServerOS owns **device identity, environment role and connection topology** because these answer Environment questions: where am I, what machine is this, and how may another authorized agent reach it?

Ability Management does not duplicate this registry. It references `device_ref` and owns only capability desired/observed state for that device.

## Authority split

```text
serveros/fleet device registry        stable identity / role / public environment facts
~/.serveros/node.yaml                 which enrolled device THIS host is
~/.serveros/targets.yaml              local-only connection locator (routes only)
~/.serveros/controller.yaml           local-only statement that a controller is callable here
state/serveros/fleet/<device>/        dynamic reachability / heartbeat / observations
ability-management fleet policy       desired capability packages/profiles
state/serveros/ability-management/    current ability materialization snapshot
```

No hostname, IP, SSH user/key path, token, machine-id, MAC, asset tag or hardware serial belongs in the public device registry.

`~/.serveros/` **binds; it never declares.** `node.yaml` says which registry row
this host is and nothing more — display name, platform, roles and profile are
read from the registry at read time. The two layers must not both be able to
answer "what is this device": they would agree on the day they were written and
diverge silently afterwards, each remaining self-consistent. `node.py` rejects
unknown keys in that file rather than ignoring them, because an ignored
attribute looks exactly like one that took effect.

## Files

```text
registry.yaml             device identity — authored, stable, public
profiles.yaml             semantic path roles, bound per device profile; also
                          the volumes each device is known to have
transport-contract.yaml   identity rules, transport kinds, capability levels,
                          per-capability availability, the availability table,
                          provenance sources
mcp-contract.yaml         what each MCP surface owns, and what it must not
controller-contract.yaml  where the fleet controller actually is, the boundary
                          ServerOS keeps to it, and the connector shape if one
                          is ever wanted
node-root-contract.yaml   what may live in ~/.serveros/ and what may never
node-interface-contract.yaml  the nine read-only node/fleet interfaces, and the
                          mutations deliberately not implemented
transport.py              identity, availability, DeviceTransport
controller.py             controller declaration + external-surface description
node.py                   the local control root — binds, never declares
interfaces.py             deterministic read-only node/fleet calls
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

## Process supervision is not session renewal

The server node's endpoint runs under a user systemd unit with `Restart=always`
and `WantedBy=default.target`, and `NRestarts=2` shows the restart path has
fired in production. On 2026-09-12 that same endpoint was **functionally
offline for 35 minutes** while every process-level indicator was healthy: the
process was alive, `active (running)`, and holding an established connection to
its relay. The journal showed why — `Invalid Refresh Token: Already Used`, and
a device authorization flow it could not complete without a person.

Two consequences, and the second is the one that generalizes:

1. `Restart=always` supervises the **process**. It cannot renew an
   **authorization**. Recovery from an expired or consumed refresh token is a
   human completing a device authorization, and no amount of restart policy
   substitutes for it.
2. The endpoint probe in `remote_desktop_commander.py` measures **presence**,
   not channel health. `endpoint_present: true` beside `listening_sockets: 0`
   was, in this incident, a true report about a service that could not serve a
   single request. A probe that reports what it measured is still not a probe
   that measured the thing you need to know — see
   `docs/fleet-controller-adapter.md`.

This is the same "presence ≠ capability" split the module already enforces for
controllers, one layer further down. It is why `fleet_presence` has no default
in the availability table: nothing local distinguishes a healthy endpoint from
a functionally offline one, so a default of `KNOWN_ONLINE` would have been
wrong for the exact incident that motivated writing this section.