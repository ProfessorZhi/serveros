---
name: fleet-work-drive
description: Take one requirement from the hub's board (or the user's words) to a delivered, evidenced result across devices — intake, reproduce, fix, real E2E A/B, real-client check, PR, report. The user only states the need, and optionally a device and agent type; numbering, naming, A/B and test design are done by the device agents' own abilities. Use when the user says "do this", "pick today's items", or names a bug or feature to get done.
---

# Drive one requirement to delivery

## At a glance

**Use when:** the owner states a need (a bug, a feature, "pick today's
items") and wants it delivered with evidence, on a device and agent type they
may or may not name.

**Does:** picks the work from the board, routes each phase to the right
device, waits on the hub's inbox, checks the evidence, reports once.

**Never:** ask the owner for an item number, branch, container name or A/B
design — the device's `work-management`, `workspace-management` and
`test-management` decide those; accept a candidate, merge, deploy or spend on
the owner's behalf; report a result you did not read back.

## 1. Pick the work

- "What should we do today?" → `fleet_work_board` for the project. Offer the
  owner one or two candidates or `todo` items, with their sources. The owner
  chooses; you do not accept a candidate yourself.
- A new need in conversation (a talk, a Feishu message, an idea, an online
  error) → `fleet_work_candidate_add` with the source and why it matters, then
  ask the owner whether to start it now.
- `fleet_project_understanding` before dispatching, so the task you send
  carries the project's current goals and decisions, not your guess.

## 2. Choose where it runs

| part of the work | default device | why |
|---|---|---|
| intake, reproduce, fix, container A/B | Tokyo (`development-primary`) | the project checkouts and Docker live there |
| GPU or long compute | Dell A6000 (`fjd`) | compute node; never preempt others' GPU work, never restart Strata |
| real Codex / Claude Code client as a user | FJPCOS (Windows) | the real client the user's bug was seen in |
| gateway / production logs | Tokyo, read-only | evidence only; never a change |

The owner's choice of device or agent type overrides this table. Use
`fleet-device-and-session-discovery` to find a live session; prefer
`fleet-reuse-idle-session` over creating one.

## 3. Dispatch in phases, one message each

Send each phase with `fleet_send_to_session` (or `fleet_create_session` per
`fleet-create-with-grants`). The message states the need and the outcome
wanted, plus the earlier phases' read-back facts. It does not prescribe file
paths or names.

1. **Intake and reproduce.** "Open a work item for: <need> (source: <link>).
   Reproduce it first, in a container that will serve as the A side of the
   A/B, and report the item id, the baseline revision and the reproduction
   evidence location." A bug that does not reproduce stops here and comes back
   to the owner.
2. **Fix and A/B.** "In the same item: fix it in a worktree, stand up the B
   side from the candidate revision, run the same E2E matrix against A and B,
   and report both revisions, the running source SHA in each container, and
   the result table." State the A/B type the device chose: `VERSION` (old vs
   new code) or `CONFIG` (same code, feature off vs on).
3. **Real client (when the user's path goes through a client).** To FJPCOS:
   "Using the real <Codex / Claude Code>, perform <the user's steps> against
   A at <address> and against B at <address>; keep screenshots and the
   client's log; report what the user would have seen on each."
4. **Logs (when the bug was seen in production).** Read-only on Tokyo: the
   gateway and production log lines for the same request shape, before and
   after, with where each line came from.
5. **PR.** "Open the PR with the A/B evidence of the latest commit." If the
   last commit changed behaviour after the A/B, the A/B is re-run first.

Wait with `fleet-async-dispatch`: the hub's watcher records the run's outcome
once, and `fleet_inbox` (or the Feishu completion card) tells you it finished.
Do not poll a session in a loop.

## 4. Verify before you report

Run `fleet-evidence-review` on each phase's answer. Accept a result only when:

- the item id, revisions and evidence locations are present and readable;
- A and B ran the same matrix, and each container's running SHA was checked;
- every red check is named, with its cause; "same as baseline" needs the
  baseline failure set, not an assertion;
- a missing real verification is `BLOCKED`, never `PASS`.

## 5. Report

One message to the owner: the need, the item id the device assigned, the A/B
table, the real-client result, the PR link, and what remains for them to
decide (merge, deploy, spend). Merging, deploying and production changes stay
the owner's decision.

## Stop and ask the owner

- the bug does not reproduce;
- the fix needs a paid model call, a production change, or a credential;
- two phases disagree (A/B passes but the real client still fails).
