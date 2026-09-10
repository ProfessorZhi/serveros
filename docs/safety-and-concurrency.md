# Safety and Concurrency

The server is shared by concurrent agents, Items, containers, and test
environments. Directory governance is **not** an exclusive maintenance window:
business Items may create worktrees, test-envs, containers, browser profiles
and Run evidence in parallel, and governance must not interrupt them for the
sake of tidiness.

## Freshness

Every move / delete / retire decision must re-resolve:

```text
project / Item / Activity / Run / process / container / git worktree / open-file
```

and read the **current** Item/lease state, `git worktree list`, container /
process, and open-file references.

> An earlier ownership snapshot must never be reused.

Each cleanup proposal's re-inspection is valid only for that candidate and that
evidence.

## Protection

- `ACTIVE`, resumable-waiting, unknown-owner, and active-lease resources are
  always **PROTECT or DEFER**.
- Idleness, an old date, a path name, or an old branch name is **not** evidence
  that a resource is reclaimable.
- Named strong-protection sets cover, for each protected Item, its
  `worktree / test-env / records card / artifacts / current development branch`.
  For these, `rename / move / delete / checkout / reset / clean / stash /
  rebase / cherry-pick / write` are forbidden, as is modifying their Git refs.
- The protected set is **resolved dynamically from current state** — not
  hardcoded into a global `CLAUDE.md` / `AGENTS.md`.

## Proposal-only

- Only after a fresh re-resolution confirms `confirmed_stale=true`, all
  reference checks are empty, and the proposal carries evidence, may a `RETIRE`
  be *proposed*. The policy layer generates proposals; it does not implement
  destructive apply.
- Cleanup stays proposal-only until explicitly approved.
- Historical evidence is preserved regardless of old naming. Legacy names
  (REQ / WI / run-001 forms) are historical evidence, **not** deletion signals.

## Cloud objects

Feishu and GitHub objects follow the same shape:
**inventory → classify → proposal**. A MeetingBundle (minute, audio,
transcript, summary) is atomic — components may not be deleted separately.

## Git safety

- Never reset, clean, stash, discard, or force-push another actor's WIP.
- Never `git add .`.
- Do not modify canonical checkouts; work in Item worktrees.
- Protected remotes are denied push unless explicitly authorized per operation.
- Structural changes to an existing repo stop at an isolated worktree plus
  tests, behind a commit decision gate.

## Secrets

Never expose or commit credentials, tokens, cookies, private keys, secrets, or
raw hardware identifiers. Secret *values* never enter Git, state, or evidence.

## Conflict resolution

> Business execution continuity takes priority over topology convergence.

When governance conflicts with active business work, mark `DEFER` and do not
stop or move the business environment.
