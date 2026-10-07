# ServerOS work engine

work-engine owns durable work/control transitions, execution receipts and Board
projections for broad ServerOS. It is not the Agent's planner or a second general
workflow engine.

Product authority: [serveros/ARCHITECTURE.md](https://github.com/ProfessorZhi/serveros/blob/main/ARCHITECTURE.md), especially sections 8, 14, 22, 23 and 27.

## Authority boundaries

Existing record/storage contracts distinguish:

- stable Item intent: records/projects/<project>/items/<ITEM>/card.yaml;
- runtime/control state: <state_root>/serveros.db;
- evidence: <artifacts_root>/work-items/<project>/<ITEM>/<RUN>/;

Evidence registration is idempotent only for the same bytes and metadata at the
same Item/Run/locator. A changed file or verdict returns `EVIDENCE_CONFLICT`;
changing `--kind` cannot replace the original record. Keep registered evidence
immutable. For a corrected report, save a new versioned artifact, register it,
and describe the correction with a reference to the prior evidence. The store
does not rewrite historical hashes or reconstruct lost bytes.
- human reasoning documents: records work packets;
- code and repository revision: Git;
- actual cloud bodies, task stores and native sessions: their providers.

A stable intent field and its database projection are not independently editable
truths. Owning APIs govern controlled fields; human documents supply revisioned
semantic content. Report/render failure does not authorize replacing the source.

Roots are resolved from explicit arguments, existing role environment variables or
the executing device profile. Unknown/unbound roots fail specifically rather than
inventing a fallback. ServerOS Space owns semantic placement; work-engine consumes
its contract and does not implement a second directory policy.

The installed product has one serverosctl dispatcher. A component may expose its
own library/compatibility entry internally, but two packages must not fight for the
same public executable. This is a composition requirement, not an assertion that
all current installations have already converged.

## Flexible work semantics

```text
Candidate   cheap sourced suggestion; human selection creates durable work
Item        accepted goal, owning Project, acceptance, envelope and checkpoint
Activity    optional mutable decomposition, not a business stage
Run         execution attempt; immutable historical events, progressing live state
Outcome     business/adoption result, independent of execution state
```

An Item may touch zero or many repositories, environments, Sessions and deliverables.
Research, experiments, design, implementation and tests can be interleaved or skipped
according to the accepted goal. Do not use Item.kind to choose a compulsory workflow.

Code changes normally use worktrees; every read-only action does not need one.
Plans may change; events that actually happened and failed attempts must remain.
A Quay Task is provider-native delegated work, not a mirror of Item lifecycle.

## Current CLI and state compatibility

The repository's existing v3 CLI remains the implementation interface until explicitly
migrated. These examples describe that interface, not new verbs introduced by this
document change:

```bash
serverosctl --json item create --project serveros --prefix SRV --title "Converge work control" --kind ops
serverosctl --json activity add SRV-0001 implement --kind develop
serverosctl --json run begin SRV-0001 implement --worker codex --idempotency-key srv-0001-control-plane
serverosctl --json board render --project serveros --output-dir /absolute/path/records/projects/serveros/board
serverosctl --json federation local inventory --source /absolute/observed.jsonl --target /absolute/projection.jsonl --evidence-run RUN-0001
```

`federation` takes a domain — `feishu`, `github` or `local` — and one of `inventory`,
`check` or `proposal`. `inventory` reads observed rows from a jsonl file and writes the
projection; `check` and `proposal` compare `--observed` against `--desired`. Every one of
them names the Run that produced the observation, and publication goes through a journal
and a receipt rather than replacing the target in place.

Current coarse Item states include INBOX, AVAILABLE, READY, ACTIVE, DELIVERED and
CLOSED. Waiting/delegation/human-attention conditions carry reasons and continuation
information. These states may remain as control semantics; they must not be read as
mandatory research/design/test phases. Reopening and outcome changes remain explicit.

Current Runs use ALLOCATED, PREPARING, RUNNING and FINALIZING plus terminal verdicts.
Finalize owns success/failure verdicts; cancellation/abandonment records actor/reason.
Cancelling a control-plane Run does not by itself prove its native worker stopped or
permit resource cleanup. Execution-context revisions remain authoritative per repo;
an aggregate Run revision must not overwrite the attached repo contexts.

## Boundary contracts to implement without shortcuts

The consolidated architecture requires these behaviors where older interfaces do
not yet provide them:

- Small approved work may go directly Item -> Run without fake Activities.
- Routine, maintenance and source-observation evidence may have a legitimate non-Item
  parent. Do not fabricate a business Item/Run to refresh a map or publish inventory.
- Requests distinguish accepted, completed, failed and effect-unknown results.
- External effects carry stable action IDs, input revisions, authority and receipts.
- Timeout-after-send reconciles actual provider state before retry.
- One trigger owner and one lease prevent duplicate routine/background dispatch.
- Concurrent edits use native revision guards or an explicitly weaker serialized
  fallback, never a false promise of distributed atomicity.

These are **target compatibility gaps**, not permission to bypass current validation
or to claim the new API already works. Change versioned contracts and consumers
together, with affected tests and honest implementation status.

## Federation and publication

Existing federation commands ingest metadata and locators, validate the documented
contract and publish an observed projection with evidence/receipt linkage. Drift
output is a proposal, not an automatic Feishu/GitHub mutation.

Do not couple observation freshness to a fabricated work hierarchy. Partial reads
keep honest coverage and do not relabel the last good projection as fresh. External
publication is not a transaction with the local store: preserve progress and receipts,
then reconcile missing writes or unknown outcomes.

Existing filesystem migration uses a planned journal and content verification.
Future changes must preserve its crash/concurrency safety. No body/descendant loss
may be hidden by a green node-count comparison or a rewritten acceptance criterion.

## Verification and operating boundary

Run tests relevant to the changed interface. Full integration/release checks belong
at their boundary; do not run every suite for a reporting or README edit. A missing
pytest environment is an explicit environment gap, not PASS.

This documentation alignment changes no Python code, state schema, CLI behavior,
active worker, schedule, repository worktree or live cloud object. Implementation
and LIVE evidence remain separate from the accepted design.

## Recovering archived Work history

`workengine migrate restore-archive --archive /absolute/sealed.sqlite3 --project <project>` previews missing Item-owned history. After reviewing it, pass `--apply --sha256 <observed digest>` to restore atomically. Existing Items are never overwritten; repeat imports from the same archive are idempotent, and ID sequences never decrease. Leases, grants and Session control are not imported. Historical nonterminal Runs are reported as such; recovery never resumes their workers. Retain the source archive and a consistent live backup. A source with a WAL must first be backed up through SQLite, not copied as a raw file.

Board rendering refuses loss of any existing Item identity, including a populated board being replaced by a smaller populated board. An unreadable existing board is not treated as empty. `--allow-empty` only acknowledges an explicitly empty replacement.
