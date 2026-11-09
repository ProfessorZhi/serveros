---
name: fleet-commander-orchestration
description: Coordinate several independent tasks without becoming a polling loop — gather only the facts needed, build a dependency graph, dispatch complete long work in parallel or short work as a batch, and keep a slow node from blocking the rest.
---

# The commander coordinates; it does not sit and wait

## At a glance

- **Use when:** more than one task is in play, or one task has dependent parts.
- **Input:** the goal and the facts needed for it, no more.
- **Steps:** (1) gather only the missing facts; (2) write the dependency graph in the Work checkpoint; (3) dispatch complete long tasks in parallel, independent short ones as a batch; (4) return; (5) inspect later and accept with `fleet-evidence-review`; (6) unblock only edges whose upstream evidence exists.
- **Done when:** every task is dispatched with references, and the graph says honestly which edges have no runner.
- **Never:** run a serial dispatch-and-wait loop; treat RDC as a control plane (it is a GUI fallback).

The sections below are the full contract behind each line.

A commander is doing its job when several pieces of work are moving at once.
It is not doing its job when it dispatches one short task, waits for it, then
dispatches the next. That pattern turns the commander into a slow serial
worker and leaves every other eligible task idle.

Inside the owner-max managed profile, dispatch/create is routine and
pre-authorized: do not stop to ask a human for each ordinary task. The profile
is the owner's one-time configuration, not Linux root, not another OS account,
and not a universal bypass. Ask only when the target is outside that profile, a
required capability is absent, identity is ambiguous, or an irreversible action
would exceed the grant.

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

Use one of two shapes, deliberately. (A question the owner asked is neither:
it is one message in the owner's words — see "Write the instruction the way
the owner said it" below.)

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
2. note the send's `delivery_state` — only STARTED is a running task; QUEUED
   and UNCONFIRMED are reported as exactly that, never as "dispatched";
3. return promptly to coordination.

**Write the instruction the way the owner said it.** Owner, 2026-11-09: told
"查一下 xx 问题" directly, the local agent answered in four minutes; the same
question relayed by the commander became a long instruction — a four-layer
plan, a report path, an execution order, a list of don'ts — and the device
agent spent its time following the plan instead of answering. So:

- pass the owner's question in the owner's own words, first;
- add at most three short lines of facts the device agent cannot know (which
  system, which date, an id the owner gave) — nothing it can find itself;
- no plans, steps, checklists, report paths, output formats or lists of things
  not to do: the device agent has its own skills (e.g. its read-only
  production access) and its own safety rules, and knows how to investigate;
- end with one line: "先给结论，再给关键证据。";
- send it to the session on that device that already does this kind of work
  (`fleet_list_sessions`: an idle one in the right project) rather than a fresh
  one, so the agent starts with its context and skills warm.

A follow-up is also one line in the owner's words, to the same session —
never a correction of a plan you wrote.

**Then dispatch and leave — every dispatch goes into a batch, even a single
task.** A chat client times out on a long wait, so the result comes back as a
card, not in this turn. Right after the last ACK, call `fleet_batch` action
`open` with a name the owner will recognise and each run's `session_ref` and
`run_id` exactly as returned. Then **end your turn**: tell the owner in one or
two lines what was dispatched and that the result will arrive as a Feishu
批次完成 card. Do not wait for it.

- The hub watches every run Fleet starts, including ones that end in seconds;
  when the batch's last run has an outcome it sends ONE card listing each task,
  its outcome and the start of its final reply. A failure still alerts at once.
- Do not call `fleet_get_run` with `wait`, do not loop on `fleet_inbox`, and do
  not keep the turn open "until it finishes": a chat client times out long
  before most tasks end, and the owner then has neither your answer nor a card.
- When the owner comes back and asks, read `fleet_batch` (status) or
  `fleet_inbox` (kinds `[batch.completed]`) once, then accept or refuse each run
  on its evidence.
- When something looks wrong — no card came, a result says 没读到结果, a task
  seems stuck — call `fleet_trace` with the batch or run id once and report its
  `diagnosis` (where it stopped, what to check next). Do not re-dispatch or
  guess before reading it.
- Keep one thread of work in one session; a new, unrelated piece of work goes
  to the session that fits it.
- Write the instruction so the device agent answers with its conclusion first,
  in a few lines — that conclusion is what lands on the card.

Report to the owner briefly: the conclusion first, at most about ten lines, each
number once, no repeated tables, no background lecture.

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

When the evidence shows a better default for a device — an allowed entry that
is consistently faster or more reliable there — change it yourself with
`fleet_tune` (action `set`, with a one-sentence reason). It stays inside the
owner's policy, is journalled, the owner is told by card, and `revert` undoes
it. Never try to change the bounds themselves.

Never ask the owner which model, agent or device to use — choosing is your
job, inside the owner's runtime policy. By default omit `model`: the device's
policy `default_model` applies (the answer's `defaults.resolved.by` says
"runtime policy default_model"), or the device's own default where the policy
names none. Use Claude Code unless the task is a Codex one and the device's
policy allows Codex. Name a model only when the task needs a different one:
read `fleet_models_list` for the device — each entry says whether the policy
allows it and why — and pass an allowed entry's `model_id` as `model`. If the right entry does not exist, create it with
`fleet_model_upsert`: credentials go in as `envref` rows naming a variable the
key center delivered there (`fleet_models_list` → `variables`) (`{key: ANTHROPIC_AUTH_TOKEN, kind: envref, variable:
GW0_TOKEN}`), never as values — you never see, ask for, or paste a key. Use
the entry only when the answer says `verified: true`. A policy refusal is the
owner's rule, not an obstacle: choose an allowed entry or say what you would
need.

The commander's output is a set of dispatched tasks with their references, a
graph with honest edges, and later an evidence-based accept/refuse decision —
not a transcript of waiting.
