# Context Capsule

Product authority: [ARCHITECTURE.md](../ARCHITECTURE.md), sections 1, 6, 7 and 23.
This is the focused semantic contract. It does not invent new installed CLI verbs
or silently change a versioned serialization schema.

## Purpose

Give the native Agent the minimum correct context needed to begin or continue the
current work. The capsule is a derived view, not another durable work database.

The user does not need to remember a document's physical location, replay an old
transcript, or start every Session inside an Item worktree.

## Resolve after intent

A fresh Session may be unbound. Unconditional ServerOS context contains its entry,
current device/principal/cwd and essential boundaries, not an assumed Project/Item/Run.
Native memory/instruction loading is separate and remains runtime-native.

After the current prompt:

1. Prefer explicit current intent over worktree identity, cwd and history hints.
2. Use a managed Item worktree as strong evidence only when it matches that intent.
3. Return a small relevant capsule, or keep the binding ambiguous and clarify.
4. Expand sources, memory and capability details only when useful.

An intent switch does not silently move the shell or authorize writes in the old
worktree. Resolve the correct execution destination before acting.

## Content, not a fixed field checklist

A normal capsule answers:

- What is the goal and current work identity, if any?
- What has already been decided and where did work stop?
- What must not be changed or assumed?
- What environment, writer/resource boundaries and human gates apply?
- Which exact source, design, code and evidence references are useful next?

Project, Item, repository context, test environment, delivery target and Run may be
absent. Pure research must not fabricate development/test/delivery objects. A routine
or maintenance operation can have a legitimate non-Item work reference.

When present, context can include several repositories, not one assumed development
repo. Paths are device-bound locators; checkpoints also preserve stable identity and
revision so a receiving Windows Session resolves its own valid paths.

## Sources and truth

| Fragment | Authority |
|---|---|
| work intent/acceptance | approved records and owning work contract |
| current control/checkpoint | owning work/session state |
| repository revision | Git and the declared execution context |
| runtime/target readiness | current provider observations |
| concurrent protection | current writer/resource ownership and policy |
| design decision | accepted document revision |
| suggested ability/memory | advisory retrieval with provenance |

Capsules include source revisions/freshness and a short relevance explanation.
They are invalidated selectively when the referenced work, policy or evidence changes.
A stale capsule may aid reading but is not authority for a new risky operation.

## Size and latency

Initial ServerOS-added design budgets are about 500 tokens for L0 and 2,000 for an
ordinary capsule; configuration may refine them. Do not silently omit an essential
boundary to hit the budget. This is a target, not a current performance measurement.

The warm path uses a local index/checkpoint. It must not invoke a full scan, remote
catalogue refresh, unrelated validation, or an extra LLM routing call. Slow retrieval
has a deadline and can degrade to current records/code. Zero memory results is valid.

Do not include full maps, whole transcripts, the architecture document, every Skill,
all Items, or secrets. Stable project rules belong in native instruction files;
dynamic work state does not get rewritten into CLAUDE.md on every turn.

## Recovery

Checkpoint at meaningful progress and supported compaction/handoff/completion events.
Abrupt death may prevent the last checkpoint; reconcile actual effects against the
last durable checkpoint before retrying. A native Session ID, PID or tmux pane is not
sufficient continuity evidence.

## Acceptance examples

A blank-cwd Session can resolve a named Project without inventing an Item. A prompt
about MarketOps from a LiteLLM cwd does not receive the wrong project history or write
in that repo. Missing memory service does not block safe work. An unrelated catalogue
update does not invalidate the capsule. A cross-device handoff preserves intent and
revisions while resolving different local paths.

These are focused integration scenarios, not a requirement to rerun all runtime
suites for an ordinary capsule or document edit.

## Continuation responsibility and capability exposure

Daily Coordinator and Work Item Parent are separate logical roles. A Parent checkpoint
contains the accepted Requirement revision, required/remaining results, accepted evidence
locators, actual child attempts and unresolved effects, write scope and the next safe action.
This is recoverable work state, not a transcript summary or a new database.

Resolve relevant G1/Ability metadata after intent; load only selected Skill/route details.
A native tool can be used directly when already sufficient. Shortlisting does not deny
access to another eligible capability; Registry lookup remains available when the initial
surface misses a real need. Do not probe every Provider before an ordinary local task.

An invalidated fact or permission must be rejected at its authoritative read/effect boundary.
Do not assume every terminal supports hot attachment or deletion of previously loaded text.
Where necessary, mark supersession, stop re-injecting stale content and use supported
compaction or a fresh native Session. Preserve the Work Item identity and safe checkpoint.

Tests include a completed child received after Parent replacement, no duplicate dispatch
after UI reconnect, a revoked source not revived by old transcript, and a trivial read that
uses no fabricated Requirement/Run. If no real continuation trigger is installed, the user
sees “saved, awaiting continuation”, not an unobserved background promise.
