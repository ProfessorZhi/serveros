---
name: fleet-async-dispatch
description: Preserve the exact execution coordinates after a Fleet create/send ACK, return promptly, and inspect the run later — a timeout or unknown outcome is not a failure and never authorises a fresh-key resend.
---

# Dispatch is a handoff, not a wait

`fleet_create_session` and `fleet_send_to_session` return an **ACK**. The ACK
says the request was accepted or delivered. It does **not** say the business
task finished, and it is **not a result**. The result arrives later, from the
run, the session read and the artefact.

The commander's job after the ACK is to make the handoff durable enough that a
fresh session can resume it, then return to coordination.

## The record to preserve

Write this into the existing Work item/checkpoint — not a second database, not
a new task store. The exact values matter; a reconstructed reference is a
different address.

```text
verb:              create | send
device_ref:        device:<id>
runtime:           the provider/runtime that holds the session
session_ref:       device:<id>|<provider>|<native id>   # when returned
run_id:            the run id the ACK reports             # when returned
idempotency_key:   exactly the key sent, never regenerated
accepted_at:       when the ACK was observed
idempotency_durability: PROCESS_LOCAL | owner-backed
last_known_state:  ACKED | RUN_UNSETTLED | EFFECT_UNKNOWN | SETTLED_*
```

Also record the receiving owner/model and the task's stop condition. Do not
store the instruction text in the ACK record if the Work item already holds it;
do not store a token, credential or raw provider blob anywhere.

If the ACK has no `run_id`, record `run_id: NOT_RETURNED`. Do not invent one,
and do not turn a successful create into a started run: "created" and "started"
are different facts.

## Return promptly

After the ACK is persisted:

1. confirm the exact five coordinates — `device_ref`, `runtime`, `session_ref`,
   `run_id`, `idempotency_key`;
2. note `idempotent_replay` if it is present;
3. return to coordination.

Do not poll the run until it settles. A long task is allowed to be long.
Polling burns the commander's turn and produces no new fact. Inspect later,
when there is a reason to, or when the run is expected to have settled.

## Later inspection

Use the recorded references, not a fresh guess:

- `fleet_get_run` with the `run_id` and the same session address. A bounded
  `wait` is optional; `RUN_UNSETTLED` means **still working**, not failed.
- `fleet_read_session` when later turns are part of the evidence.
- the artefact itself — branch, diff, test output, file — when the claim is
  about code.

Then accept or refuse the result with `fleet-evidence-review`. Delivery is one
fact; completion is another.

## Timeout, unknown outcome, and the no-resend rule

A timeout, a lost response, a malformed ACK, a transport error or a missing
receipt means the outcome is **UNKNOWN**, not failed. Record
`EFFECT_UNKNOWN` and preserve the original `idempotency_key`.

A retry must reuse that exact key. `fleet_send_to_session` and
`fleet_create_session` replay the recorded run/session for the same key and do
not send a second time. A **fresh key** is a second delivery, even if the
instruction text is identical. Never use a fresh key to "make sure" after a
timeout.

The replay guarantee is scoped to what the receipt says. Read
`idempotency_durability`:

- `PROCESS_LOCAL` — the guard lives in this process and is lost on restart;
- owner-backed — the owner store retains it, but the provider still has no
  independent idempotency contract.

Do not describe either as a provider-side exactly-once guarantee. If the
durability is unknown, say so; an unresolved unknown is not permission to
resend.

Only the owner's reconciliation path may resolve an unknown effect. If you do
not have that path, report the unresolved state instead of dispatching again.

## Resume after a disconnect or a new session

A new session resumes from the **existing execution reference**:

1. read the Work/checkpoint record;
2. verify the `device_ref`, `runtime` and `session_ref` still address the same
   session — a reference is an address, not a hostname;
3. if the record has a `run_id`, inspect that run; if it has only an ACK, read
   the session for the delivery;
4. continue the same task, or report the exact missing coordinate.

Do not recreate the task because your session restarted, and do not silently
retarget a reference to a different device or runtime. If the original address
is gone, say which coordinate is gone and ask for a decision.

## Skills are not persistent schedulers

A Skill is instructions read by an agent while that agent is running. It does
not survive its host, does not wake up later, and is not a persistent scheduler. A task that must continue after this session ends needs an actual
continuing Agent or a verified mechanism that owns the trigger and the
observation. Writing "resume later" in a Skill does not create one.

Dependencies follow the same rule: an edge is real only when a continuing
agent or verified mechanism will observe the upstream terminal state and start
the downstream task. Otherwise it is a plan with a missing runner, and the
honest report says so.
