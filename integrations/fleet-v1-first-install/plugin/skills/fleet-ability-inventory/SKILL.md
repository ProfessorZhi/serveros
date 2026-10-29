---
name: fleet-ability-inventory
description: Read what Ability family and norms a device's agent actually has installed — skills and the CLAUDE.md/AGENTS.md entry files, by pinned revision — by asking that device's agent for a readback receipt. Use before changing a device's abilities, after a rollout, or whenever someone asks "what does that machine's agent follow right now?".
---

# Ability inventory: ask the device, read the receipt

## When to use

- Before `fleet-ability-change`: you need the current state to diff against.
- After a rollout: the receipt is the only proof the change landed.
- When an agent on a device behaves unlike the norms you expect.

## Inputs

| input | where it comes from |
|---|---|
| device | `fleet_list_devices` or the user's words, resolved to a `device_ref` |
| agent family | `claude` or `codex` — one runtime per readback |
| profile | the device's profile name in `ability-management` `abilities-manifest.yaml` |

If the device has no profile in the registry, stop and report
`PROFILE_MISSING`: creating one is a registry change, made through
`fleet-ability-change`, not something to improvise on the device.

## Steps

1. Find an idle session on that device and runtime (`fleet_list_sessions`
   with the `device` filter), or create one with `fleet_create_session`
   following `fleet-create-with-grants`.
2. Send this task with `fleet_send_to_session` and a stable `idempotency_key`:

   > In your ability-management checkout, run
   > `python skills/ability-management/payload/control_plane.py readback --profile <profile> --agent <claude|codex> --host-profile <this node's host profile>`
   > and reply with the JSON receipt exactly as printed, plus the checkout's
   > `git rev-parse HEAD`. Do not change anything.

   Do not send absolute paths: the device agent resolves its own checkout and
   host profile.
3. Return to other work. Later, read the result with `fleet_get_run` and
   `fleet_read_session`.
4. Parse the receipt (`schema: serveros/ability-rollout-receipt.v1`).

## Reading the receipt

| field | meaning |
|---|---|
| `result: CONVERGED` | every managed skill and every norm file matches the checkout |
| `result: DRIFT` | something is `MISSING` or `DRIFT` — list which, by `cid` / `target` |
| `skills[].state` | `MATCH`, `DRIFT`, `MISSING`, or `NOT_MANAGED_HERE` (a plugin member) |
| `instructions.state` | `MATCH`, `DRIFT`, `NOT_REQUESTED` (no host profile given), `ERROR` |
| `hooks.state` | always `NOT_MANAGED` for now — hooks are not inventoried |
| `registry.head` | the revision the comparison was made against |

`CONVERGED` means "matches that checkout", not "matches main": compare
`registry.head` with the revision you expect before calling the device current.

## Success

You report, per device and runtime: `registry.head`, `result`, and every row
that is not `MATCH`.

## Never

- Never accept a paraphrase ("all good") in place of the JSON receipt.
- Never report `hooks` as checked.
- Never fix drift during an inventory: report it, then use
  `fleet-ability-change`.
