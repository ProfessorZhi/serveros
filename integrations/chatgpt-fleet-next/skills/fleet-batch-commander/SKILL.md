---
name: fleet-batch-commander
description: Use when ChatGPT must turn one complex goal into a batch of independent Fleet tasks across devices and Sessions, or when deciding which live device, runtime, model, grant and idle Session may receive one.
---

# A batch is independently acceptable tasks, not a long prompt

This is the cloud-side playbook. The detailed policies remain in the shared
Operator Skills: `fleet-device-and-session-discovery`, `fleet-safety-boundaries`,
`fleet-create-with-grants`, `fleet-reuse-idle-session`,
`fleet-commander-orchestration`, `fleet-async-dispatch`,
`fleet-evidence-review`, and `fleet-plugin-update`. Read the relevant one
before a write; this Skill does not replace its refusals.

## Read the live facts before naming a target

For every candidate device, read the actual fields rather than a remembered
machine name:

- **role and ownership** — authored roles, platform and OS family; a GPU worker
  is not a Windows compatibility node unless the device says so;
- **runtimes and bindings** — each runtime family and credential instance is
  its own address; a Codex alias is not the base runtime;
- **installed / ready / granted / effective ability** — keep the layers
  separate; an installed name is not a ready operation, and a ready operation
  is not a grant;
- **occupancy** — a Session that is `RUNNING_AGENT` is occupied; do not steal
  it. Prefer a `MANAGED_IDLE` Session with the matching project;
- **models and grants** — choose only a model present in the runtime's actual
  available list and covered by the project/device grant; a model id written
  from memory is a request, not a selection;
- **project and worktree** — the task's project must match the Session; one
  writable checkout has one writer.

A snapshot is evidence only while it is current. If a capability, model,
Session state or grant cannot be read now, report it as `UNKNOWN`; do not
reuse an earlier answer because it made the plan convenient.

## Every task carries its own acceptance

A complex goal is split along lines that can be accepted independently. Two
children that can only be judged together are one child. Before dispatch, give
each task:

| field | meaning |
|---|---|
| `task_id` | stable id, unique inside the batch |
| `device_ref` | live device identity, not a hostname |
| `runtime` | exact runtime/credential instance |
| `project_id` | the project whose checkout and grants the Session inherits |
| `session_ref` | an existing idle Session, or a create intent |
| `worktree` | the isolated branch/worktree the task owns |
| `scope` | allowed effects and explicit non-goals |
| `acceptance` | the check that decides pass/fail |
| `tests` | commands/evidence the worker must run |
| `report` | artifact locator the worker must return |
| `idempotency_key` | stable key for this task delivery |

The batch itself has a stable **Batch ID**. Persist it with every task's exact
`device_ref`, `runtime`, `session_ref`, `run_id`, `queued_message_uuid`, and
`idempotency_key` in the existing Work/checkpoint. A Batch ID that lives only
in the chat transcript is not persistent.

## Select, dispatch, return

1. Choose the device whose **role and current readiness** fit the task, not the
   most familiar name. If no device has the required ability, model and grant,
   return `BLOCKED` with the missing fact; do not silently fall back.
2. Prefer an idle Session that already owns the project. If none exists, create
   only inside the owner-configured profile and after the create preconditions
   in `fleet-create-with-grants`.
3. Send a bounded task with its AC, scope, stop condition and report
   locator. Use the stable idempotency key. For several independent tasks,
   dispatch them together or in parallel; do not run one short task and wait.
4. The ACK is delivery — not execution and not success. After recording the
   ACK/receipt identifiers, return to the chat promptly. Do not poll until a run
   settles, and do not hold the turn open for completion.
5. A separate observer/IM path may notify the owner later. If no observer is
   configured, say `OBSERVER_NOT_AVAILABLE` and return the Batch ID and task
   references; do not invent a completion.

## Later readback and acceptance

On a later turn, use `fleet_get_run` with the recorded `run_id`, then
`fleet_read_session`/the artifact when the transcript or output is the evidence.
Classify exactly:

- `PASS` only when the task's native result and the acceptance evidence were
  actually read;
- `FAILED` when the run or the evidence says it failed;
- `RUNNING` while the run is still unsettled;
- `UNKNOWN` for ACK-only, a bare `completed` with no trusted result, a missing
  `run_id`, `RUN_NOT_FOUND`/`never_issued`, or any outcome you did not observe.

A batch is `GREEN` only when every required task is `PASS`. Any `UNKNOWN` keeps
the batch `UNKNOWN`; one failed task makes it `FAILED`. Never report "all done"
because the sends were accepted.

## Refusals are information

Return the specific typed answer instead of a generic retry:

- `MISSELECTED_DEVICE` — role/readiness does not fit the task;
- `STALE_CAPABILITY` — the ability snapshot is missing, stale or not effective;
- `MODEL_NOT_AVAILABLE` — the model is not in the runtime's live list;
- `GRANT_DENIED` — scope/device/verb grant does not cover the action;
- `SESSION_BUSY` — the only candidate Session is running or lacks the project;
- `WORKTREE_CONFLICT` — two tasks would write the same checkout;
- `ACK_ONLY` / `FALSE_COMPLETION` — delivery/`completed` is not acceptance.

Do not bypass a grant by changing the device, model, project or account. Do not
interrupt another agent's run to free a slot. Do not turn a skip, an unknown,
or a receipt into a success.
