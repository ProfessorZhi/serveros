---
name: fleet-ability-change
description: Change how agents on one or more devices behave — their skills and their CLAUDE.md/AGENTS.md norms — by landing the change in ability-management, rolling the pinned revision out through Fleet, reading it back, checking behaviour, and rolling back if it fails. Use whenever the user wants device agents to follow a new or changed rule, skill or convention.
---

# Ability change: registry first, then roll out, then prove it

The norms a device's agent follows live in `ProfessorZhi/ability-management`:
skills under `skills/`, the shared policy in
`instructions/machine-policy-invariant.md` (rendered into each device's
`CLAUDE.md` / `AGENTS.md`), and which skills each device gets under `profiles`
in `abilities-manifest.yaml`. Git is the only source. A device is never edited
by hand.

## When to use

- "Make every agent do X / never do Y."
- "Give the Dell agent this skill." / "Remove that skill from Tokyo."
- A `fleet-norm-audit` found an agent ignoring a norm because the norm was
  unclear or missing.

## Inputs

| input | note |
|---|---|
| the change in words | what behaviour should change, and how you will recognise it |
| target devices × runtimes | e.g. Tokyo `claude`, Dell `claude-fjd` |
| a check task | one short task whose answer shows whether the new norm is followed |

## Steps

1. **Inventory.** Run `fleet-ability-inventory` on every target. Record each
   `registry.head`: that is the rollback revision.
2. **Change the registry.** Dispatch one agent (any device with an
   ability-management checkout) to edit the skill / invariant / profile, run
   the repo tests, and open a pull request. Read the diff yourself
   (`fleet-evidence-review`). Merge only with the owner's approval.
3. **Pin.** The merged commit sha is the rollout revision `REV`.
4. **Dry-run on each target** with `fleet_send_to_session`:

   > In your ability-management checkout: `git fetch origin` and
   > `git checkout REV`, then run
   > `python skills/ability-management/payload/control_plane.py rollout --profile <profile> --agent <claude|codex> --rev REV --host-profile <this node's host profile>`
   > and reply with the JSON receipt exactly as printed. Do not pass `--apply`.

   Expect `result: PLAN` (or `NO_CHANGE_NEEDED`). `REFUSED` means the checkout
   is dirty or not at `REV` — have the agent report `problems`, do not work
   around it.
5. **Apply on one device first.** Same message with `--apply`. Expect
   `result: APPLIED`. `PARTIAL` or `FAILED` → go to step 8 on that device.
6. **Check behaviour.** Start a fresh session on that device (a new session
   loads the new `CLAUDE.md`) and send the check task. Judge the answer with
   `fleet-evidence-review`.
7. **Widen.** Repeat 5–6 for each remaining target, one at a time.
8. **Roll back** on any failure: the same rollout message with the
   step-1 revision instead of `REV`, `--apply`, then `fleet-ability-inventory`
   to confirm `CONVERGED` at that revision.

## Success

Every target: receipt `APPLIED` at `REV`, the check task passes in a fresh
session, and a final inventory reads `CONVERGED` with `registry.head == REV`.

## Report

One table: device × runtime → before revision, `REV`, rollout `result`,
check task verdict, rolled back (yes/no). Name any `NOT_MANAGED` part
(hooks) as unchanged.

## Never

- Never tell a device agent to edit its own `CLAUDE.md`, `AGENTS.md` or skill
  directory directly; that is drift, and the next inventory will show it.
- Never roll out an unmerged branch or a revision you have not read.
- Never apply to every device at once before one device has passed step 6.
- Never use this to grant a device or an agent more authority: permissions,
  OS accounts and Fleet grants are not Ability changes.
