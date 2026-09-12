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
remote endpoint                 ← fleet/controller-contract.yaml describes what
                                  this can and cannot be reached through
```

Swapping Remote Desktop Commander for SSH, WinRM or A2A changes the last two
lines and nothing above them. That is the whole test of whether the boundary
is real.

## Where the controller is

The previous version of this document said the blocker was "no controller
surface" and stopped there. It was true and it was useless: it did not say
whether the controller was missing, uninstalled, undocumented, or simply
somewhere else. It is somewhere else, and finding out changed the design.

Read-only inspection of `@wonderwhy-er/desktop-commander` 0.2.48 established:

| Question | Answer |
|---|---|
| Remote roles in the package | **One — the endpoint.** `desktop-commander remote` starts `MCPDevice`. |
| Controller CLI | None. `dist/server.js` exposes only local tools. |
| Controller library to wrap | None. There is nothing here to adapt. |
| Where the controller is | A **remote MCP service**, consumed by external MCP clients. |
| Its protocol | Published MCP + OAuth 2.0, not a private wire format. |

The controller surface is a standard MCP endpoint that advertises its own
metadata: OAuth 2.0 authorization-code with PKCE S256, RFC 8414 discovery,
RFC 7591 dynamic client registration, RFC 8707 resource indicators, bearer in
header.

**That last row decides the whole design.** A private protocol would have to
be left alone. A public, standards-based surface may be integrated against
legitimately — through an MCP client, which Claude Code and Codex already are.

### Why ChatGPT can control all three devices and this runtime cannot

Not a permission difference and not a network difference. ChatGPT is
configured as an **authorized MCP client** of the controller. This runtime is
an **endpoint host** and nothing else. The two roles are shipped by different
parties, sit on different planes, and neither implies the other.

```
control plane   external MCP client ──OAuth──▶ controller ──▶ relay ──▶ devices
                                              (exists; not callable here)

this host       desktop-commander remote ─────▶ relay ▶ devices
                (endpoint role; dials out, listens on nothing)
```

## Current status

```text
ServerOS semantic layer          READY_TO_PROMOTE
Remote endpoint                  CLOSED
External Fleet visibility        CLOSED
Local controller exposure        NEEDS_HUMAN   (one browser consent)
Windows live inventory           DEFERRED_PARALLEL_WIP
```

### READY

| Component | File |
|---|---|
| Identity + typed refs + legacy aliasing | `fleet/transport.py` |
| Three-axis availability model | `fleet/transport.py`, `fleet/transport-contract.yaml` |
| Per-capability availability | `fleet/transport.py`, `fleet/transport-contract.yaml` |
| Transport-neutral `DeviceTransport` interface | `fleet/transport.py` |
| First adapter, honest about absence | `fleet/remote_desktop_commander.py` |
| Controller boundary + external surface | `fleet/controller.py`, `fleet/controller-contract.yaml` |
| Device registry (3 devices) | `fleet/registry.yaml` |
| Environment profiles + path roles | `fleet/profiles.yaml` |
| MCP surface ownership | `fleet/mcp-contract.yaml` |

### CLOSED: the endpoint half

Measured on this host, reproducibly, by `observe_local_endpoint()`:

```text
endpoint processes          3          all genuine invocations
listening sockets           0          it dials out and listens on nothing
local controller declared   absent     (~/.serveros/controller.yaml)
```

`listening_sockets: 0` is the measurement that separates the two roles, and it
is in the diagnostic because it is the one number that cannot be argued with.
A process can run for days, healthy and connected, while holding no listening
socket — at which point nothing on this machine can call it, however present
it looks in `ps`.

### CLOSED: the external-visibility half

The controller plane exists, it sees all three devices, and its surface is
public and standards-compliant. That question is answered.

### NEEDS_HUMAN: the local-exposure half

One step clears it, and it is not a configuration change ServerOS can make:
**a person authorizes an MCP client against the controller.**

ServerOS cannot self-authorize, and must not try. Once a client is authorized,
a declaration records that fact:

```yaml
# ~/.serveros/controller.yaml   (local-only; never committed)
kind: mcp-http           # or mcp-stdio — must be a kind the contract names
surface:
  resource: <the authorized MCP endpoint>
```

There is deliberately **no field for a token**. The authorization belongs to
the MCP client that performed it, and a schema with nowhere to put a
credential cannot leak one. `declared_controller()` returns this or `None`,
and `controller_available()` is exactly that test.

The declaration is not inferred from "the package is installed" or "the
daemon is running". Either would make the adapter claim a capability it does
not have, which is the failure this layer exists to prevent.

## Availability is per capability

One boolean could not express the situation, and that was the actual defect.
The endpoint is `AVAILABLE` while every capability is unreachable from here,
so a consumer handed one bit has to discard one of two true facts.

```yaml
transport: remote-desktop-commander
availability:
  endpoint: AVAILABLE          # the endpoint role is running on this host
  discover: EXTERNAL_ONLY      # the surface exists; not callable from here
  inspect: EXTERNAL_ONLY
  read: EXTERNAL_ONLY
  execute-safe: EXTERNAL_ONLY
```

Four values, each earned:

| Value | Means |
|---|---|
| `AVAILABLE` | this runtime can exercise it now |
| `EXTERNAL_ONLY` | a controller surface exists on another plane; not callable here |
| `NOT_GRANTED` | the surface is callable, this level is withheld by decision |
| `UNKNOWN` | not established — not a no |

`EXTERNAL_ONLY` must survive to the consumer. A layer that renders it as
"unavailable" and then as "unsupported" has discarded the only fact that makes
the gap actionable: that a specific, nameable step closes it.

Capability levels are declared **once**, in `transport-contract.yaml`, weakest
first: `discover · inspect · read · execute-safe · write-workspace ·
install-ability · system-change · gui-control · admin`. `mcp-contract.yaml`
references that list rather than restating it — it did restate it, and two
copies of a closed vocabulary agree exactly until the day someone edits one.

## What must not be done to unblock it

Four routes would work and must not be taken. `controller-contract.yaml`
records each by name so a future reader can cite one rather than re-derive it:

- **Relay reverse-engineering.** Do not talk to the relay's realtime channel,
  its tables, or its database. That binds ServerOS to an unpublished
  implementation that can change without notice.
- **Credential capture.** Do not read, copy, print or reuse a token, refresh
  token or session blob. Authorization belongs to the client that obtained it.
- **Identity impersonation.** Do not present as or proxy for the first-party
  connector.
- **Silent transport substitution.** Do not reach a device over SSH, WinRM,
  SMB or RDP because the controller is not exposed. That is not a workaround;
  it is a different trust boundary entered without a decision.

`no_silent_fallback: true`, and a test reads the adapter and controller source
to confirm neither references another transport or an HTTP client.

## The connector boundary

If a ServerOS-local controller is ever wanted, this is its shape — recorded
now and **not implemented**, because implementing it before a human
authorizes a client would be implementing an unused surface:

```yaml
connector:
  name: serveros-fleet-controller-connector
  status: NOT_IMPLEMENTED
  role: injected by an authorized external controller; presents the controller
        as a DeviceTransport; holds no credential of its own
  must_not: [hold or copy credentials, reimplement the relay protocol,
             become the place device identity is decided]
  blocked_on: human OAuth consent to an MCP client
```

## Security notes

- Transport bindings live at `~/.serveros/targets.yaml`, controller
  declarations at `~/.serveros/controller.yaml`; neither is committed.
  `validate_binding()` and `validate_controller_declaration()` reject a
  credential field at any nesting depth.
- `controller-contract.yaml` records a public resource URL and a protocol
  version. Both are published material. No token, client secret or code
  verifier appears in it, and a test greps for the patterns that would mean
  one had.
- The controller's `device_name` and `device_uuid` are now listed in
  `never_identity` alongside `hostname` and `computer_name`. `device_uuid` is
  assigned at registration and changes when a device re-enrols — the opposite
  of an identity.
- The endpoint's own credential file is a local `0600` file outside any git
  repository. It was verified by permission bits and key names only; no value
  was read, printed, or copied.

## Diagnostic helper

`observe_local_endpoint()` reports what is *running*, shaped so it cannot be
mistaken for something callable — it has no `kind` and no `surface`, so it
cannot be passed to `declared_controller()`:

```python
{"endpoint_present": True,
 "endpoints": [{"pid": ..., "mode": "remote", "listening_sockets": 0}, ...],
 "listening_sockets": 0,
 "controller_declared": False, "note": "..."}
```

Two properties it must keep, both learned the hard way:

- It **skips its own process and ancestors**, so it cannot observe itself.
- It matches an endpoint invocation by its **argument shape**, not by substring
  across argv. Substring matching counted a stale `bash -c ".../desktop-
  commander..."` from an earlier session as a fourth endpoint — a false
  positive in precisely the direction this module exists to prevent.
