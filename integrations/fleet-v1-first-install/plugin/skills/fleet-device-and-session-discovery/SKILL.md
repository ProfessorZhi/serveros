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
