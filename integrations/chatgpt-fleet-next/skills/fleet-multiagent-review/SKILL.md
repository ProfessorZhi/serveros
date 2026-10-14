---
name: fleet-multiagent-review
description: Split one Work Item into independently checkable worker and reviewer roles with exactly one writable Parent, bounded budgets and idempotent children. Use when a task is large enough to delegate but not large enough to lose its single acceptance owner.
---

# Fleet Multi-Agent Review (Cloud-side thin Skill)

This is **an uninstalled design candidate** for Fleet Next. It describes a way to
organise work; it does not create a scheduler, a queue or a second Work engine.

## The rule that makes the rest work

**One Work Item has exactly one writable Parent at a time.** The Parent is the
only role that may accept a result. Splitting a task into roles is a way to get
independent evidence, not a way to create a second authority — if two agents can
both accept, the acceptance means nothing.

## Roles, and what each may NOT do

- **Parent** — owns decomposition, worktree assignment, budget, receipt
  consumption and acceptance. May choose which runtime or provider a child runs
  on. Is not thereby entitled to production changes; that stays with the human
  decision owner.
- **Worker** — implements exactly its bounded slice on its own branch or
  worktree. Cannot widen its own scope, cannot read another account's secret,
  and cannot mark its own work ACCEPTED.
- **Reviewer** — a *different* context that reads the raw artifact: the diff,
  the test output, the source revision, the safety checks. A reviewer that only
  reads the worker's summary is not a reviewer.

Naming a role does not confer its permission. A worker is a worker because of
its device × runtime × verb grant, not because a message called it one.

## Method

1. State the Work Item's acceptance check before splitting anything. A task that
   cannot be checked cannot be delegated — it can only be done.
2. Split along lines that can be **accepted independently**. If two children can
   only be judged together, they are one child.
3. Give each child a budget (wall clock, resources) and an idempotency key.
   Replaying a child with the same key must return the earlier association
   rather than starting a second run.
4. Pin the source revision into the child's envelope. A result that does not
   name the revision it was produced from cannot be accepted into the Parent.
5. Review in an independent context, against raw evidence, before acceptance.
6. Accept or fail explicitly. `RESULT_READY` is what a worker's own "done"
   produces; `ACCEPTED` is what the Parent produces after checking. The two are
   not the same word and must not be reported as one.

## Failure and honesty

- A child that goes silent is not a failed child. Check the last receipt and the
  actual effect before re-dispatching; re-dispatching first is how one task
  becomes two.
- A late result must not overwrite a newer epoch. If the inputs, baseline or
  authorised scope changed, the epoch changes and the old result is history.
- Cancellation is not complete until the process confirms it. Until then the
  writer lease is still held.
- Roles never pass authority downward. A parent's grant is not inherited by its
  child, and a shared conversation is not an entitlement.

## When NOT to use this

Most tasks do not need it. A single Agent, or a direct repository change, is
cheaper and easier to verify. Adopt the split only when independent verification
genuinely buys something a single reviewer could not — and compare the outcome
against the single-agent baseline before making it the default.
