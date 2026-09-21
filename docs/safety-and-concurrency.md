# Safety and Concurrency

Product authority: [ARCHITECTURE.md](../ARCHITECTURE.md), sections 2, 5, 14 and 23.

Directory governance is not an exclusive maintenance window. MarketOps, Support Bot,
knowledge work and other approved Items may create resources concurrently. Their
continuity takes precedence over tidiness.

## Ownership and scope

Visibility, age, a familiar path or an empty child list does not prove a resource
is disposable. Protect active/resumable work, unknown-owned resources and resources
with valid consumers. Resolve the current ownership/reference state for the exact
candidate before a consequential mutation.

Do not reset, stash, clean, force-push, move or overwrite another actor's WIP.
Code changes default to isolated worktrees with one integration writer. A request
to converge main is not permission to edit the shared canonical src checkout.
Stage only scoped paths; do not sweep unrelated changes into a commit.

## Concurrency is more than Git

Use the owning provider's concurrency primitive where possible: database transaction,
OS lock, Git/blob revision or native conditional write. Cooperative writer/resource
leases coordinate Agents but do not magically restrict arbitrary shell processes.

Reserve ports, databases, containers, browser profiles and shared test identities as
well as worktrees. Lease expiry permits reconciliation, not blind process termination.
A stop request is distinct from an observed stop; retain exclusion until the writer
has actually relinquished the resource.

## Planning and applying

The Space Resolver remains a deterministic placement/advice authority. A provider
adapter applies an operation only under its actual authorization. Personal owned
organization may be automated under standing policy; not every move needs another
human question. Destructive effects, foreign/shared scope and production use their
stricter rules.

Prior to deletion, inspect the object's own body/attachments, descendants and
remaining references, preserve required recovery evidence and persist the intent.
Independent readback verifies the result. Never infer empty body from no children.
A transient read error or partial inventory is not an empty object.

MeetingBundle is a relationship among source and derivatives, not a demand to delete
all components together. Retaining original Minutes while organizing derived files
is normal. No derivative cleanup silently grants authority to delete the source.

## Production and credentials

Tokyo defaults to vince. The ubuntu shared-gateway exception is target/operation
scoped. Current LiteLLM and shared-gateway production changes are user-executed.
Permission to inspect, draft a runbook or review a card does not authorize cutover,
live config mutation, restart or rollback.

A verb named prepare can still mutate production; evaluate real effects. Read-only
queries must respect data and load limits. Network changes inspect relevant current
routes, networks and listeners without scanning unrelated users. No mandatory
netshadow installation is implied.

Credentials stay in protected vault/native stores. Secret values must not enter
ordinary registry metadata, prompts, Git, logs, report bodies or screenshots. If a
worker can directly read an unrestricted production credential, a Skill instruction
alone cannot truthfully be called a hard enforcement boundary.

## Unknown effects and recovery

An external timeout after sending a write leaves effect status unknown. Reconcile
actual target state before retry; do not duplicate a document, PR, message or deployment.
Use action IDs, provider idempotency where available, revision guards and receipts.
There is no promised global transaction across GitHub, Feishu, Git and local state.

## Human surfaces

Keep migration/probe journals and detailed verification records under operational
evidence. Human documents should contain current useful content, not empty-container
inventories or stale pending-deletion notes. Clean presentation never justifies losing
source material or hiding an unresolved data-loss finding.
