---
name: fleet-device-and-session-discovery
description: Find out which ServerOS device, Linux account, credential profile and runtime actually exist before naming one. Use at the start of any Fleet task, and whenever a device name, session id or "my laptop" appears in a request.
---

# Discover the device and the runtime before you name one

Every Fleet tool takes a **device reference**, a **runtime** and a **session
reference**. None of them may be guessed, and none of them is a hostname.

## Read, do not assume

Call, in this order, and read the answers rather than the request:

1. `fleet_status` — is the ingress answering at all, and which tool-surface
   revision is it serving.
2. `fleet_list_devices` — the enrolled devices. A device is
   `device:<id>`, authored in the fleet registry. **A hostname, a computer
   name, or the machine you are running on is a route, not an identity**, and
   passing one returns `UNKNOWN_DEVICE` rather than a best guess.
3. `fleet_list_sessions` — per device, the sessions that exist now, with their
   project and state.

For each device, `fleet_list_devices` reports three separate things that are
often collapsed into one word and must not be:

- **presence** — whether the fleet plane has seen it;
- **controller availability** — whether this ingress has a route to it;
- **inventory availability** — whether it answered.

`UNKNOWN`, `NOT_EXPOSED` and `UNAVAILABLE` are different answers with different
fixes. A device that did not answer is not a device that does not exist.

An `UNKNOWN` is **never** a permission. A gate whose state could not be read is
not an open gate: "I could not tell" resolves to *do not act, and say so*, never
to *proceed*. The same rule holds at every layer below — presence, reachability,
inventory, and the node's own floor. A refusal outranks an unknown, and an
unknown outranks nothing at all.

## OS user and credential profile are part of the address

One physical machine can run several Linux accounts, and one account can hold
several credential instances. Two runtimes on one device are not the same
runtime because they share a device reference, and the same session id can
exist under both. Before you send anything, be able to say which
**device × OS user × credential instance × runtime × project** you are
addressing — that is what the session reference encodes, and a reference that
only names the device is not enough.

If the answer you need is not in the listing, say so. Do not infer a device, a
user or a session from a friendly name, a screenshot or a previous
conversation.

## The four layers, and which one you are talking to

"Fleet" is one word for four different things. A request that collapses them
cannot be routed, and a refusal from one layer will be misread as a refusal
from another:

| Layer | What it is | What it decides |
|---|---|---|
| the **central Fleet control plane** | this MCP surface: the ingress holding the device registry, the session index and the run index | who may be reached at all, and what the calling credential carries |
| the **ServerOS core on each device** | the node runtime *on that machine* — the **Core-Lite** observed today is **per-OS-user and observe-only** | what the machine is, which OS accounts and runtimes exist, and its own floor. A reading, never a control channel |
| the **Ability family inside the device's agent** | the capabilities the coding agent on that machine can actually invoke | *how* the work is done there |
| the **cloud-side Work handoff** | the message and the run: dispatch, tracking, read-back | whether what you asked for happened |

The first is the only one you can call; the second is authoritative about
itself and answers *through* the first; the third is not exposed here at all —
it belongs to the agent on the device. The fourth is what you actually do.

**This surface routes; it does not carry the work.** These Skills decide where
work belongs and how to tell whether it happened. The device agent's own
ability family owns the doing, with its own registry and its own
responsibilities. Do not restate those responsibilities here, do not keep a
second copy of them, and do not describe a capability as available because a
similar-sounding one exists on the machine you are running on.

## Verify the five coordinates, not the two you can see

Before dispatch, be able to name all five — **device × runtime × model ×
project × grant** — because each is decided somewhere different and a request
missing any one of them will be answered with a refusal that names that one:

- **device** — from `fleet_list_devices`, never from a hostname;
- **runtime** — the credential instance on that device, not the agent family,
  and not the account name;
- **model** — the *effective* model for that runtime's owner-approved entry,
  read back, not assumed from the default;
- **project** — an approved project id, which is what selects the working tree
  and the credentials the session inherits;
- **grant** — the scope on the calling credential *and* the device's own
  binding, which are two different gates in two different places.
