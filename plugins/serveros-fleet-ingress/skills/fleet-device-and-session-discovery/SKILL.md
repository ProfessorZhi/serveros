---
name: fleet-device-and-session-discovery
description: Use when a Fleet task starts, or when a device, session, runtime or credential profile must be resolved before naming it.
---

# Discover the device and the runtime before you name one

Every Fleet tool takes a **device reference**, a **runtime** and a **session
reference**. None of them may be guessed, and none of them is a hostname.

## Resolve only the facts you lack

Start with the coordinates the request or the existing checkpoint already
gives you. Do not make a global status probe the first step of every task.

- **Target already known** — a `device_ref`, `session_ref` or `run_id` from the
  request or checkpoint — read it directly with `fleet_get_session`,
  `fleet_read_session`, `fleet_list_sessions` with that `device` filter, or
  `fleet_get_run`.
- **Device identity unknown** — call `fleet_list_devices` to resolve the
  enrolled `device:<id>`. A device is authored in the fleet registry.
  **A hostname, a computer name, or the machine you are running on is a route,
  not an identity**, and passing one returns `UNKNOWN_DEVICE` rather than a
  best guess.
- **Route, credential or binding unclear** — diagnose the specific problem:
  a failed call, `fleet_status`, or the relevant device entry. Do not make a
  fleet-wide slow query a prerequisite for a targeted read.
- **Session identity unknown** — call `fleet_list_sessions` for the resolved
  device and pick by project and state.

A global status read is a diagnostic, not a prerequisite. One slow or
unreachable device is one device's answer. Keep its gap visible and
continue with the devices and sessions whose facts are available; do not wait
for a global probe to finish before doing a targeted read.

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

## Discover capabilities, not names

The surface's capability list is read, not remembered. Use `tools/list` (or the
plugin's recorded list from the same source) before assuming a verb exists.
The **project-create** capability is `fleet_create_project` where the served
list carries it, and the roots it may use are in `fleet_status.owner_access`
for the device and runtime you resolved. Existing projects, archived ones
included, are in `fleet_cloudcli_projects_list`. If the served list has no
project-create capability, report `PROJECT_CREATE_CAPABILITY_NOT_EXPOSED` and
return the decision to the core/owner. Do not hardcode a tool count (the
current count is not a permanent fact), and do not guess another verb name.

The owner-max managed profile is configured once by the owner. Inside it,
routine create/send/dispatch is pre-authorized; the client does not ask a human
to reconfirm each call. Outside it, a refusal or a human decision is the
correct answer.

## OS user and credential profile are part of the address

One physical machine can run several Linux accounts, and one account can hold
several credential instances. Two runtimes on one device are not the same
runtime because they share a device reference, and the same session id can
exist under both. Before you send anything, be able to say which
**device × OS user × credential instance × runtime × project** you are
addressing — that is what the session reference encodes, and a reference that
only names the device is not enough. Claude and Codex are different runtimes,
and a Codex alias runtime is its own address; do not infer it from the base
runtime or from the agent family, and do not assume a provider family has a
runtime binding just because another family does.

If the answer you need is not in the facts you have, say so. Do not infer a
device, a user or a session from a friendly name, a screenshot or a previous
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
itself and answers *through* the first; the third is not exposed as a control
channel here — it belongs to the agent on the device. The fourth is what you
actually do.

**This surface routes; it does not carry the work.** Ability authority is the
shared `ability-management` catalogue, not a new registry per device. A device
exposes an **installed / ready / granted projection** of that catalogue: what
is installed there, what the runtime can actually execute now, and what the
owner has granted. Read that projection; do not create a second ability
registry on the device or restate the catalogue here. A similar-sounding
capability on the machine you are running on is not evidence that the target
device has it.

## Verify the five coordinates, not the two you can see

Before dispatch, be able to name all five — **device × runtime × model ×
project × grant** — because each is decided somewhere different and a request
missing any one of them will be answered with a refusal that names that one:

- **device** — from the checkpoint or `fleet_list_devices`, never from a
  hostname;
- **runtime** — the credential instance on that device, not the agent family,
  and not the account name;
- **model** — the *effective* model for that runtime's owner-approved entry,
  read back, not assumed from a fleet-wide default;
- **project** — an approved project id, which is what selects the working tree
  and the credentials the session inherits;
- **grant** — the scope on the calling credential *and* the device's own
  binding, which are two different gates in two different places.
