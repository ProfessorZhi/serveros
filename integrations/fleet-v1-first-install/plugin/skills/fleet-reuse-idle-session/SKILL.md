---
name: fleet-reuse-idle-session
description: Continue an existing idle Claude/Codex session instead of starting a new one, and send to it precisely. Use before any fleet_create_session call, and whenever the task is "carry on with" or "ask the agent on that machine".
---

# Reuse the session that is already there

Creating is the expensive, permissioned path. **Look for an idle session that
already owns the context before you ask for a new one**, because a new session
starts with no project context, no prior turns, and a fresh grant requirement.

## The order

1. `fleet_list_sessions` on the device you resolved.
2. Pick by **project** and **state**, not by recency alone. A session whose
   project is not the one you need is not made suitable by being idle.
3. `fleet_read_session` to confirm what it is actually doing before you add to
   it. A session that looks idle in a listing may be between turns of a long
   run.
4. `fleet_send_to_session` with a bounded, self-contained message. The receiving
   agent has none of this conversation.

## Sending well

- Name the device, the runtime and the session in your own reasoning, and pass
  the session reference the listing returned. Do not reconstruct it.
- Say **what you want and what "done" looks like**, and let the agent on the
  machine decide how. It can see the local checkout, the tests and the
  services; you cannot.
- One message, one intent. A message that asks for three things and reports
  partially back is a message whose result cannot be accepted or refused.
- If you are retrying after an unknown outcome, reuse the **idempotency key**
  from the first attempt. A retry with a fresh key is a second delivery, and
  the platform will treat it as one.

## Do not interrupt

`fleet_interrupt_session` stops a run **in flight**. Never call it because a
session has been quiet, because your own turn is ending, or because you want
to change the subject. Someone else's agent may be mid-task behind that
session, and stopping it is not recoverable by you. Interrupt is for a run you
started and know is wrong.

## Close the loop the same way you opened it

Dispatch is the middle of the task, not the end of it. After you send:

1. **Keep the run id** and read it back (`fleet_get_run`) until it settles.
   A message that was accepted is not a task that finished.
2. **Read the later turns and the artefact** — `fleet-evidence-review` says
   what counts as evidence and what only looks like it.
3. **Read the resolved runtime, model and mode back** from the session or the
   run record, and report *those*. Reporting the values you asked for is
   reporting your own request back to yourself.

A reused session is still somebody's session. Do not resume, restart or nudge
it to make your own read-back tidier — a second writer is how a clean checkout
stops being one.
