# ServerOS federated object model

| Object | Stable identity | Authority |
|---|---|---|
| Observation/Snapshot | external source ID + revision/hash | `records/information/raw` |
| Claim | claim ID + raw locator | `records/information/understanding/claims` |
| Item/Card | `<PROJECT_PREFIX>-<NNNN>` | `records/projects/<project>/items/<ITEM>/card.yaml` |
| Activity declaration | Item + activity ID + graph revision | Item `plan.yaml` |
| Activity runtime | Item + activity ID | `state/serveros/serveros.db` |
| Run | Item + `RUN-NNNN` | `state/serveros/serveros.db` |
| Evidence | project + Item + Run + relative file | `artifacts/work-items/...` |
| Execution context | Item + activity + repo | state; Git remains code truth |
| Deliverable | Item + type + stable locator | originating system + locator in state/card |
| Board | project projection | generated from card + state + claims |
| Source | external origin + stable locator | owning external system |
| Candidate Item | candidate ID + claim set | `records/projects/<project>/items` |
| Feedback | feedback ID + deliverable/item ref | originating system + state |

Stable cards do not copy current branch, HEAD, dirty state, lease, PR state, or test verdict. Those facts are queried from their owning system or recorded in the exact Run that observed them.

`outcome` is recorded independently from lifecycle state. Deliverables are append-only locators. `board/current.{json,md}` is an atomically regenerated local view of SQLite state plus those locators, never an input to scheduling.

## Lifecycle

Item:

```text
INBOX → AVAILABLE → READY → ACTIVE → DELIVERED → CLOSED
```

`WAITING / NEEDS_HUMAN / DELEGATED` must record a reason, the state to return to, and the
condition that clears it. `CANCELLED / CLOSED` may only be reopened explicitly.

Outcome is separate from state: `SUCCESS / FAIL / PARTIAL / INCONCLUSIVE / NOT_APPLICABLE`.

Item shape:

```text
Item 1
├── 0..N execution contexts / worktrees
├── 0..N test environments
├── 1..N Activities
├── N Runs
├── N evidence sets
└── 1..N deliverables
```

An Item is not a PR and does not require Git — tests, investigations, daily reports,
deployments and support conclusions can all be Items directly. An Item touching two repos
has two worktrees and is still one Item. `project` scopes records/artifacts/state; `repo`
scopes src/worktrees/test-envs.

Run is strongly linear and resumable; the store enforces stepwise transitions, so a skipped
step is rejected rather than silently accepted:

```text
ALLOCATED → PREPARING → RUNNING → FINALIZING
          → SUCCEEDED | FAILED | CANCELLED | INVALID
```

Activity is coarser and may run in parallel across a DAG:

```text
PENDING → READY → RUNNING → SUCCEEDED | FAILED | BLOCKED | CANCELLED
```

`latest` Run is ordering only — never a writable target. A Run may be written to only by the
execution that attached to it via `serverosctl context attach`.

## Project cutover

Switching a project off the legacy model runs:

```text
inspect → plan → preflight → lease → stage → validate
        → intent → ordered switch → verify → complete
```

- A project's records/state/worktree/test-env/artifact switch together; projects may go in batches.
- A failure before `intent` switches nothing; after `intent`, resume from the journal or compensate in reverse.
- No `legacy/`, `compat/`, old canonical directory, or double-write survives the switch.
- A dirty worktree must show identical HEAD/status/patch/hash before and after — never reset/clean/stash it.
