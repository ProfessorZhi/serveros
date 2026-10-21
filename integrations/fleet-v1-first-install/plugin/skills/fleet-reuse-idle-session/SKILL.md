---
name: fleet-reuse-idle-session
description: Continue an existing idle Claude/Codex session instead of starting a new one, and send to it precisely. Use before any fleet_create_session call, and whenever the task is "carry on with" or "ask the agent on that machine".
---

# Reuse the session that is already there

Creating is the expensive, permissioned path. **Look for an idle session that
already owns the context before you ask for a new one**, because a new session
starts with no project context, no prior turns, and a fresh grant requirement.

## Resolve the session before sending

Use the coordinates you already have:

1. If a checkpoint or the request already names a `session_ref`, read that
   session directly with `fleet_get_session` or `fleet_read_session`. Do not
   start with a global listing to rediscover an address you were given.
2. If you know the device but not the session, call `fleet_list_sessions` with
   that `device` filter. Pick by **project** and **state**, not by recency
   alone. A session whose project is not the one you need is not made suitable
   by being idle.
3. `fleet_read_session` to confirm what the session is actually doing before
   you add to it. A session that looks idle in a listing may be between turns
   of a long run.
4. `fleet_send_to_session` with a bounded, self-contained message. The receiving
   agent has none of this conversation.

If the address is not in the facts you have, discover only the missing fact:
`fleet_list_devices` for a device identity, `fleet_status` or the failed call's
diagnosis for a route/credential problem, and the filtered listing for a
session. A global status read is a diagnostic, not a prerequisite.

## Sending well

- Name the device, the runtime and the session in your own reasoning, and pass
  the session reference the listing or checkpoint returned. Do not reconstruct
  it.
- Say **what you want and what "done" looks like**, and let the agent on the
  machine decide how. It can see the local checkout, the tests and the
  services; you cannot.
- One bounded message may carry a **complete long delivery with internal
  steps**, or a **batch of independent short tasks**. What it must not do is
  dispatch one short task per message and wait for each in turn. If several
  independent short tasks are ready, send them together or in parallel; do not
  serialize them through your own polling loop.
- When one message carries several items, make every item independently
  identifiable and separately acceptable or refusable. A partial result is not
  one pass, and a dependent chain is not a batch: keep the dependency explicit
  or split it into separate tasks.
- If you are retrying after an unknown outcome, the original **idempotency
  key** is necessary. It is not a cross-restart guarantee. Read
  `fleet-async-dispatch` before deciding that a replay is safe; if the original
  owner record or an explicit replay cannot be established, do not send or
  create again by guessing.

## Do not interrupt

`fleet_interrupt_session` stops a run **in flight**. Never call it because a
session has been quiet, because your own turn is ending, or because you want
to change the subject. Someone else's agent may be mid-task behind that
session, and stopping it is not recoverable by you. Interrupt is for a run you
started and know is wrong.

## After the ACK, keep the handoff and return

The send ACK is a delivery receipt, not a finished task. After it:

1. Preserve the exact execution references in the existing Work/checkpoint:
   `device_ref`, `runtime`, `session_ref`, `run_id` if one was returned,
   `queued_message_uuid` if one was returned, and the original
   `idempotency_key`. A missing `run_id` is `NOT_RETURNED`, not a fabricated
   failure.
2. Return to coordination. Do not poll the run until it settles; inspect later,
   on demand, with the preserved references.
3. When you inspect, use `fleet_get_run` and `fleet_read_session`, then read the
   artefact. A `RUN_NOT_FOUND` or `never_issued` answer is not proof that the
   task did not run: the native session history or the artefact may show that
   it completed. Reconcile the original session/native history and the owner
   record before treating the delivery as absent.
4. Read the resolved runtime, model and mode back from the session or the run
   record, and report *those*. Reporting the values you asked for is reporting
   your own request back to yourself.

A reused session is still somebody's session. Do not resume, restart or nudge
it to make your own read-back tidier — a second writer is how a clean checkout
stops being one.
