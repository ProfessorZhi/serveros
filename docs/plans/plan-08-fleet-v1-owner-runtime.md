# Fleet V1 owner/runtime implementation plan

Implements [the reviewed proposal](../proposals/proposal-fleet-v1-owner-runtime.md)
for SRV-0011. Root base: 69a18bc; root Activity fleet-v1-owner-runtime-20261021,
Run RUN-0046. Its artifact directory contains historical evidence, which is
preserved; new evidence uses the exclusive v1-owner-runtime-20261021 namespace.
No terminal finalize may overwrite the historical finalize.json.

## File ownership and dependencies

Root owns Fleet policy/dispatch/providers/Node/protocol/tools, existing owner
store integration, full runtime entry/build closure and core tests. The pure
owner-access helper is an independent reviewed input at 3e239971. Tokyo's worker
owns integrations/client/Skills/package tests. CloudCLI's worker owns a separate
repo at upstream v1.39.0 / 300e5fa93a269661132a4992f707e1197d608844.
Dell owns Linux supervisor adaptation. A separate Linux worktree implements
Windows source after the real Windows executor was denied; real Windows
execution is not bypassed or claimed. Each lane keeps its own source/evidence.

Stages below are sequential; independent native/client/CloudCLI lanes run in
parallel under their own reviewed designs. Each core stage first records a
meaningful failing test, then implementation and focused passing validation.

## 1. Owner policy and effective session defaults

Import the pure helper from 3e239971 after reading it. Extend ProviderBinding
with owner_access and PolicyBinding with exact principal-to-namespace owner_max.
Both constructors and YAML loading reject malformed/unbound/wildcard entries.
Namespace resolution preserves an explicitly opted-in multiplex Codex namespace
without borrowing Claude rights. An owner profile cannot authorize another
account, disabled binding or unregistered/unsupported namespace.

Refactor `_may_create` and effective create/reconfigure policy to receive the
Principal, actual binding runtime and native authorization namespace separately.
Owner-max removes per-project configuration for real projects on the selected
runtime. It supplies maximum execution mode while retaining family/model
boundaries described in the proposal. Only new effects perform dynamic native
project verification; accepted replay retains no-provider-IO behavior. Both
handwritten and mirrored native create/reconfigure share these decisions.

Condition local-owner/owner-pat six scopes on each exact principal's configured
owner-max entry. OAuth/Feishu tokens keep their actual granted scopes. Node
owner_access opens only its existing owner-session floors/implemented verbs;
project IDs are checked against that provider, not against a stale static list.

Acceptance/tests: new project IDs work without grant edits; two native families
get maximum mode; explicit wrong family/model and another principal/account
refuse before effect; native and friendly calls agree; omitted reconfigure
fields stay omitted; old closed defaults and explicit grant tests still pass.

## 2. Existing durable owner and typed project receipts

Extend DispatchGuard with project.create and a closed project receipt while
preserving session wire/receipt behavior. Add expected_project_path only to the
owner claim envelope; AgentControlStore validates/stores it separately from
kernel.attempt_record arguments. Stored verb selects valid completion/replay
type. Unknown outcomes never transition back to retryable through this adapter.

Add opt-in require_existing to AgentControlStore, retaining owner/path/schema
checks on every load/mutation and refusing deletion before the first write.
The ServerOS-owned factory derives the existing store anchor from the same
registry state root as the daemon/CLI. It opens no second DB and never creates
a blank store on a missing/invalid anchor. Add bin/serveros-fleet-owned to call
HTTP/stdio using the existing service injection seam; source-relative runtime
loading uses ServerOS's established canonical module names.

Acceptance/tests: real separate process restart/reconstruction replays accepted
send/create/project without new provider calls; changed payload conflicts;
UNKNOWN survives restart; concurrent claim sends once; malformed/cross-target
or cross-type completion refuses; missing/replaced/symlink/wrong-owner/bad-schema
anchor refuses before provider IO. Existing store sections survive all writes.
Default Fleet-only artifact still imports and operates without runtime sources.

## 3. Explicit project operation and target-local preconditions

Add FleetService.create_project and the authored fleet_create_project tool,
using fleet:create plus exact owner-max create authority and the profile root.
Its parameters are device/root/name/display_name/runtime/idempotency_key, never
caller paths or commands. Update DeviceProvider's explicit operation and both
CloudCLI/FleetNode providers. New closed Node route is POST /v1/projects.

The provider checks the actual project_create schema advertises requireFresh,
then sends requireFresh:true. Old gateways return a precise capability gap; no
REST/browser authentication fallback. Target ACK must have a real project ID,
the requested path and freshDirectoryCreated:true. Node independently resolves
its own root, validates the safe name/host filesystem and forwards its path.
Fleet verifies the Node reply path matches its declared profile; grant mismatch
or ambiguous effect stays refused/UNKNOWN. Accepted replay runs before live
capability/directory checks. No key means honestly NOT_REQUESTED durability.

Acceptance/tests: real fake-device integration creates a project then maximum
mode Session without allowlist edits; scope/owner/root/runtime failures have no
effect; all path escapes and old capabilities refuse; same key offline replay,
changed name conflict and lost ACK fencing work. Node protocol signatures cover
the new route. Native mirror remains the captured 22; total catalogue is 34.
CloudCLI separately proves real PAT/scope, SQLite ID, exclusive fresh directory,
existing directory/archive conflict, concurrency, symlinks and retained native
default archived behavior. Patch applicability is pinned to upstream 300e5fa.

## 4. Full artifact closure and setup

Add fleet_mcp to scripts/build.py CORE_MODULES AND plugin/bundle.yaml path.
Build/readback tests launch bin/serveros-fleet-owned from the produced full
runtime outside the checkout. Fleet-only install PAYLOAD stays standalone and
does not masquerade as owner-backed. Status distinguishes actual durability.

Add a non-secret owner setup generator using explicit principals/runtime keys,
existing binding and named observed project roots. It produces complete,
reviewable Fleet/Node config updates and a rollback/readback plan, preserving
unselected accounts, anchors, endpoint identity and credentials. Schema/duplicate
key validation and exclusive output prevent a silent partial update. Selected
configuration requests all implemented scopes once; it does not invent consent
or widen already-issued tokens. Integrate the client adapter's shared Skills
against actual tool schema/count and the owned runtime entry.

Acceptance/tests: generated configs load and admit selected owner workflows,
other users/profiles stay unchanged, no credential values enter artifacts,
duplicate/malformed input and unsafe output refuse, built runtime startup is
real, and clean standalone import/old client paths remain valid.

## 5. Review, integration and actual acceptance

Freeze exact core and dependency SHAs, review independently and repair confirmed
issues. Create a new isolated integration worktree; do not modify older accepted
candidates. Merge client/CloudCLI/native lanes only after their scoped review.
Run affected Python/Node/C++ checks once per changed candidate, bounded to
2 GiB/no swap/one CPU and artifact output; broaden only on unresolved failures.

Use real isolated local CC and Codex to verify owned MCP/Skills and actual
supported workflows. Real tests use the authorized managed profile, retain
original task/queue/run refs and verify ACK-and-return, concurrency, disconnect
reconciliation and results without duplicate effects. Test native recovery on
isolated toy services, including functional failure and refusal; production
self-healing and unavailable Windows execution remain distinct gates.

Deliver exact commits/Draft PRs, source manifest, artifact hashes, test exits and
current acceptance matrix. Attach every created PR. Prepare same-ID update and
rollback against the actual existing plugin identity; do not invent a new App
or claim a synthetic update was uploaded. Apply only authorization already given
for selected managed accounts; unrelated services/credentials/network stay with
their actual owners. GPT rollout follows local acceptance. Report any actual
external/runtime denial precisely after all independent work is completed.

## Review refinements

- A multiplexed owner alias uses only its exact typed-namespace model rule;
  missing Codex rules leave model omitted rather than borrowing Claude defaults.
- project.create's idempotency target is the actual bound device/runtime key.
  Its intent includes authorization namespace, root, name and display_name.
  Changing a name under that same target/key conflicts; existing session
  send/create target scopes retain their original independent semantics.
- Anchor validation refuses illegal owner/schema/path replacements and missing
  stores, while allowing the owner's ordinary atomic os.replace updates.
  It must not pin one inode across legitimate writes.
- Strict owned mode validates the persistent lock sidecar too: no symlink,
  hardlink, foreign owner or writable-by-others file; O_NOFOLLOW plus descriptor
  identity checks surround locking. Contention is bounded, not a task wait.
- Add explicit owner-max revocation tests: accepted replay against an offline
  provider refuses after the current selected namespace grant is removed.

- Setup preserves the global OAuth issuer scope ceiling even for an owner-max
  request. Its readback plan separates required owner scopes from the existing
  issuer ceiling; local/PAT owner rights do not expand other OAuth clients.
