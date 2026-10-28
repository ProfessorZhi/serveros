---
name: fleet-commander-orchestration
description: Use when coordinating several independent Fleet tasks that should run in parallel or as a batch, or when one slow device must not block the others.
---

# The commander coordinates; it does not sit and wait

A commander is doing its job when several pieces of work are moving at once.
It is not doing its job when it dispatches one short task, waits for it, then
dispatches the next. That pattern turns the commander into a slow serial
worker and leaves every other eligible task idle.

Inside the owner-max managed profile, dispatch/create is routine and
pre-authorized: do not stop to ask a human for each ordinary task. The profile
boundary — what it does and does not grant — is in
`fleet-safety-boundaries`; ask a human only outside that profile, when a
required capability is absent, identity is ambiguous, or an irreversible
action would exceed the grant.

This Skill is about the shape of the work, not about a new scheduler. There is
no batch verb and no scheduler tool. The commander uses the existing Fleet
verbs and the existing Work/Parent records.

## 1. Gather the minimum facts, then stop gathering

Start with the question you were actually asked. A **targeted** query is
usually enough:

- a named session: `fleet_get_session` or `fleet_read_session`;
- a named device: `fleet_list_sessions` with that `device` filter;
- a named run: `fleet_get_run` with the recorded `run_id`.

Do **not** make a global status read a prerequisite for every task. Call
`fleet_status` when a call failed, when the ingress or a route is genuinely in
question, or when you need the credential/binding facts before a write. A
global status read is a diagnostic, not a ritual.

The same rule applies to a **slow node**: one device that is not answering is
one device's answer. It does not freeze the other devices, and it does not
authorise a new global sweep. Keep the slow node's gap visible and continue
with the work that has its facts.

## 2. Build a dependency graph before dispatching

Write the graph down in the existing Work/checkpoint, not in your head:

- **nodes** are complete deliverables, each with an owner, a stop condition and
  the evidence that would accept it;
- **edges** are real consumption: the downstream task needs the upstream
  result, path, commit, session reference or decision;
- **execution references** are the exact coordinates from the upstream ACK
  (`device_ref`, `runtime`, `session_ref`, `run_id`, `idempotency_key`).

An edge is executable only when an **actual continuing agent** or a
**verified mechanism** will observe the upstream terminal state and start the
downstream task. A Skill that describes the edge is not that mechanism. If no
continuing agent or verified trigger exists, the dependency is a plan, not an
executable edge: mark it blocked and say what is missing. A Skill is not a scheduler, and a graph with no runner is not running.

## 3. Choose the dispatch shape

Use one of two shapes, deliberately:

1. **Several complete long tasks, in parallel.** Each task is independently
   accept-or-refuse-able: it has its own deliverable, its own tests/evidence and
   its own stop condition. Send each as one bounded message and keep its ACK.
   These are the tasks worth spending a long-running agent on.
2. **A batch of independent short tasks.** Group short, non-dependent checks or
   edits into one envelope, or dispatch them together to separate sessions.
   Do not repeatedly dispatch one short task and wait for it before sending the
   next. If the tasks are truly independent, they can be in flight together.

Do not put dependent steps in one "parallel" batch. A batch whose members wait
on each other is a chain wearing a batch's clothes.

## 4. Dispatch and return

Before a write, resolve the five coordinates: **device × runtime × project ×
grant × effective model**. Prefer an idle session that already owns the
context; when none can carry the task, create inside the owner-max profile
without a fresh human approval. Use `fleet_send_to_session` for an existing
session and `fleet_create_session` for a new one.

For a **new project**, confirm the capability in the actual tool list, then
take a root ref from `fleet_status.owner_access` and call
`fleet_create_project` with a stable `idempotency_key`; use the returned
`project_id` for `fleet_create_session`. Inside owner-max this needs no
per-project approval. If the served list has no project-create capability,
report `PROJECT_CREATE_CAPABILITY_NOT_EXPOSED` and return the decision to the
core/owner. Do not guess a verb name or a tool count. The full procedure and
its refusals are in `fleet-create-with-grants`.
After create, read back the effective model and native permission mode for that
CC or Codex runtime; they are per-runtime facts, not a fleet default.

Pass a stable `idempotency_key` for every create/send that may be retried after
an unknown outcome. After the ACK:

1. preserve the exact execution coordinates in the existing Work/checkpoint;
2. note whether the ACK is delivery-only or carries a `run_id`;
3. return promptly to coordination.

The ACK contract and the no-resend rule are in `fleet-async-dispatch`. Do not
poll a run until it settles just to make your own turn feel finished. Owner-max
does not change delivery semantics: an ACK is still not completion, and an
UNKNOWN outcome still does not authorize a blind resend.

## 5. Inspect, accept, and keep the graph honest

Later — not in a spin — inspect the run:

1. `fleet_get_run` with the recorded `run_id`, using a bounded `wait` only when
   a short block is useful;
2. `fleet_read_session` for later turns when the transcript is part of the
   evidence;
3. the artefact itself: branch, diff, test output, or a file read back from the
   device.

Accept only evidence you actually read, following `fleet-evidence-review`.
A delivery ACK is not a completed task, a green status is not a test result,
and `RUN_UNSETTLED` means "still working", not "failed" and not "done".

When a task settles, unblock only the graph edges whose upstream evidence now
exists. If the edge needed a continuing agent and none is running, do not
invent one by re-dispatching the same short task in a loop. Report the blocked
edge.

## 6. Prefer Fleet; use RDC only for the GUI gap

**Prefer Fleet** for discovery, session addressing, dispatch, reads and
control. It carries identity, authorization and evidence. RDC is useful when a
question is genuinely visual or GUI-only and cannot be answered through Fleet:
a window that must be seen, a desktop interaction with no Fleet verb. RDC is a
**GUI fallback**, not a second control plane, not a source of device identity,
and not evidence that a Fleet session exists or succeeded. Never infer
privileges, session state or completion from the fact that RDC is reachable.

If a targeted Fleet read can answer the question, use it and do not open a GUI.
If the question really needs a person or a visual check, say so and hand that
piece to the person; do not call the GUI path "Fleet control".

## 7. Coordinate people and models, not just calls

Give each task the owner and model that fit it: a difficult control change and
an independent QA read are not the same job. Say which person or model owns
each node and what would make the result unacceptable. Keep one writer per
checkout and do not let two agents "help" the same worktree.

The commander's output is a set of dispatched tasks with their references, a
graph with honest edges, and later an evidence-based accept/refuse decision —
not a transcript of waiting.
