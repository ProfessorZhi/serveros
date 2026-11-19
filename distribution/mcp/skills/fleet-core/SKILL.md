---
name: fleet-core
description: Read this first, and usually only this, for any Fleet request — sending work to a device, asking how a device or a task is doing, or recording what you told the owner. The other fleet-* skills are detail for rare cases.
---

# Fleet — the core

## At a glance

- **Use when:** any Fleet request — send work, ask how a device or a task is doing, record what you told the owner.
- **Steps:** pick the target → send with the owner's words → open a batch → tell the owner and record it → end the turn.
- **Done when:** the owner knows what was sent and that a Feishu card will bring the result.
- **Never:** wait or poll for a result; ask again for a target the owner named; touch production, credentials or a running service unasked.

## What Fleet is

You are one of the owner's commanders. The agents on the devices do the work;
Fleet carries the request, watches it, and tells the owner on Feishu. Your job
is to get the request to the right place quickly and get out of the way.

## Devices

| device | use it for |
|---|---|
| Tokyo-Shared-Dev-01 | development, the hub, records and the work flow (the default) |
| Dell-LLM-Lab (A6000) | GPU, models, **production log queries and analysis** |
| FJPCOS (Windows) | real-client tests (Feishu, ChatGPT, browsers); the Windows-side log channel |

## Send work

1. **Target.** If the owner named a device or a session, that is the authorization: use it, do not ask again. Otherwise pick the device from the
   table. A new task gets a new session (`fleet_create_session`); a follow-up to
   a task you sent goes to that task's session (`fleet_send_to_session`). Only
   list sessions (`fleet_list_sessions`) when you need one you do not know.
2. **Prompt.** Short and complete: what to do, the conclusion first in the
   answer, read-only unless the owner asked for a change. Check
   `fleet_memory` `search` first; the dispatch also carries relevant notes.
   A bug or test task: record the input once in `test-envs/<ITEM>/inputs`,
   reproduce on A (old), fix, run the same input on B, report A vs B.
   A device skill or slash command: `fleet_agent_catalog`, then
   `fleet_agent_invoke` with a listed name.
3. **Send**, with `owner_request` = the owner's message, verbatim.
4. **Batch** every dispatch, even one: `fleet_batch` action `open`, a name the
   owner will recognise, each run's `session_ref` and `run_id` exactly as
   returned, `skills` you used.
5. **Tell the owner** in one or two lines what was sent and that the result
   comes as a Feishu card; then `fleet_batch` action `reply` with those words.
   **End your turn.** Do not wait, do not call `fleet_get_run` in a loop.

## When the owner comes back

- Read once: `fleet_batch` action `status` (or `fleet_inbox` kinds
  `[batch.completed]`). Judge each run by what it returned, not by "completed".
- Something looks wrong (no card, 结果不明, stuck): `fleet_trace` with the batch
  or run id, once, and report its diagnosis. Never re-send on a guess.
- After you answer, `fleet_batch` action `reply` with your words (with
  `batch_id`, or with `owner_request` when there is no batch).
- A finding is confirmed: `fleet_memory` `add` one short note (never a value).

## Never

- change production, credentials or a running service without the owner's
  explicit request;
- print a key, token or password, or read a file that holds one;
- send the same task twice because a reply is slow.

Read another fleet-* skill only when the task is that skill's subject:
creating a project or a session profile, plugin updates, acceptance, norms,
abilities, card wording, a multi-step work drive.
