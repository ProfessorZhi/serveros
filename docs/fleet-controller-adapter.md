# Fleet Controller Adapter

## What this is for

ServerOS knows *who* a device is. A transport knows *how* to reach it. The
adapter is the layer that keeps those two sentences from becoming one.

```text
device:work-fj                  ← stable identity, authored, public
      │
      ▼
ServerOS device registry        ← fleet/registry.yaml
      │
      ▼
transport binding               ← local-only, disposable, never committed
      │
      ▼
transport adapter               ← fleet/remote_desktop_commander.py
      │
      ▼
remote endpoint
```

Swapping Remote Desktop Commander for SSH, WinRM or A2A changes the last two
lines and nothing above them. That is the whole test of whether the boundary
is real.

## Current status

Split deliberately, because the two halves have different blockers.

```text
semantic adapter contract        READY_TO_PROMOTE
server-side controller binding   NEEDS_EXTERNAL_CONTROLLER   (blocked)
```

### What is READY

| Component | File |
|---|---|
| Identity + typed refs + legacy aliasing | `fleet/transport.py` |
| Three-axis availability model | `fleet/transport.py`, `fleet/transport-contract.yaml` |
| Transport-neutral `DeviceTransport` interface | `fleet/transport.py` |
| First adapter, honest about absence | `fleet/remote_desktop_commander.py` |
| Device registry (3 devices) | `fleet/registry.yaml` |
| Environment profiles + path roles | `fleet/profiles.yaml` |
| MCP surface ownership | `fleet/mcp-contract.yaml` |

### What is BLOCKED, and by exactly what

Measured on the server node:

```text
endpoint process            PRESENT   (running for days)
listening sockets           0         (it listens on nothing)
established connections     1         outbound to its relay
local controller declaration absent   (~/.serveros/controller.yaml)
CLI introspection surface   none      (--help yields no commands)
```

So the device is reachable *fleet-wide* and unreachable *from this runtime*.
Those are two facts and the model keeps them apart:

```yaml
fleet_presence:                        KNOWN_ONLINE
local_agent_controller:                NOT_EXPOSED
remote_inventory_from_this_runtime:    UNAVAILABLE
```

derived state: `REMOTE_CONTROLLER_NOT_EXPOSED`.

**This is not `DEVICE_UNOBSERVED`.** It is a statement about this runtime's
integration, and recording it as a statement about the device would be wrong
in a way that invites wrong actions — re-enrolling a device that is fine,
deleting a record that is valid, or "fixing" the network.

## What unblocks it

A callable controller surface, declared locally:

```yaml
# ~/.serveros/controller.yaml   (local-only; never committed)
kind: remote-desktop-commander
surface:
  transport: unix | tcp | mcp
  path: <endpoint the controller listens on>
```

`declared_controller()` returns this or `None`, and `controller_available()`
is exactly that test. Once it resolves, the adapter's operations stop raising
and the `NotImplementedError` call sites become the only remaining work.

The declaration is deliberately **not** inferred from "the package is
installed" or "the daemon is running". Either of those would make the adapter
claim a capability it does not have, which is the failure this whole layer
exists to prevent.

## What must not be done to unblock it

Do **not** implement the adapter over SSH or WinRM instead.

That would produce a working demo and a broken model: the fleet's trust
boundary would silently move from a controller that authenticates devices to a
shell that authenticates whoever holds a key, and nothing above the adapter
would have recorded a decision. `transport-contract.yaml` sets
`no_silent_fallback: true`, and a test asserts the adapter references no other
transport.

If a second transport is genuinely wanted, it is a new class implementing the
same interface, selected explicitly — not a fallback.

## Security notes

- Transport bindings live at `~/.serveros/targets.yaml` and are never
  committed. `validate_binding()` rejects a binding carrying any credential
  field, at any nesting depth.
- The endpoint's own credential file is a local `0600` file outside any git
  repository. It was verified by permission bits and key names only; no value
  was read, printed, or copied.
- `device-contract.yaml` continues to forbid hostnames, addresses, SSH
  targets, machine ids, MACs, asset tags and hardware serials from the public
  registry, and a test asserts none of them appear there.

## Diagnostic helper

`diagnose_local_endpoint()` reports what is *running*, shaped so it cannot be
mistaken for something callable:

```python
{"endpoint_present": True, "endpoints": [...],
 "controller_declared": False, "note": "..."}
```

It skips its own process and ancestors so the probe cannot observe itself.
