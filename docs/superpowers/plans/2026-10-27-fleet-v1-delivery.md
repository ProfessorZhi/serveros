# Fleet V1 Complete Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the complete Fleet V1 with stable CC and Codex control on all three authorized devices, durable asynchronous continuation, owner preemption, separated releases and one native node supervisor.

**Architecture:** Extend the existing owner ledger and typed Fleet boundary. Event persistence is a projection, Work Engine remains writer authority, and ability-management remains capability authority. Move modules only after their consumers and deployment seams are pinned.

**Tech Stack:** Python 3.12+, SQLite, existing MCP/CloudCLI protocol, C++/CMake, PowerShell/systemd, pytest and existing release tooling.

**Spec:** `docs/superpowers/specs/2026-10-27-fleet-v1-completion-design.md` (approved 2026-10-27).

## Global Constraints

- All three devices × Claude Code/Codex are required; discovery is not LIVE acceptance.
- Keep six OAuth scopes; tools may grow. Current authorization is checked before reads, replays and writes.
- No raw prompt, credential or transcript in events. Do not invent a second grant/lease/Work registry.
- Every recovery unit has one actuator; busy/manual-stop/expired-lease/NEEDS_LOGIN conditions refuse recovery.
- Source work is under D:/projects; existing bindings and secrets remain local to each device.
- Keep unknown delivery fenced. Never resend under a new key merely to hide UNKNOWN.
- Use TDD and task review. Do not broaden a known-failure baseline to hide new defects.
- Portable tests run on Windows; POSIX tests run in bounded Linux/hosted CI. Native Windows acceptance is mandatory.
- Use the approved staged deployment and rollback procedure. No public store listing is required.

## Review Focus

1. Crash after provider ACK but before event bookkeeping: recover from existing receipt without new dispatch (Tasks 1–2).
2. Permission changes between inbox read and ack: no cross-principal acknowledgement or data disclosure (Tasks 1–2).
3. UNKNOWN later resolves: preserve uncertainty history, append trusted correction, do not repeat downstream work (Tasks 1, 3–4).
4. Owner preempts while a child completes: old epoch cannot wake or write, even if interruption fails (Tasks 4–5).
5. Downgrade after new state writes: prior binary must read compatible state or use verified recovery, never silently reset (Tasks 10–13).

## File and ownership map

- `fleet_mcp/events.py`: durable events, cursors and watch scheduling only.
- `fleet_mcp/run_watcher.py`: bounded provider observations and terminal mapping.
- `fleet_mcp/fleet.py`, `tools.py`, `runtime.py`: injection, authenticated inbox and lifecycle wiring.
- `runtime/fleet_mcp_owner.py`: existing state owner injection; later moves to fleet_hub.
- `fleet_mcp/principals.py` and `dispatch.py`: trusted kind and work context; no new credentials.
- `runtime/fleet_event_delivery.py`: consumer receipts and existing notification/wake integration; later moves to reports/fleet_hub.
- `workengine/agent_control/`: final owner for directives and migrated control records.
- `scripts/fleet_v1_acceptance.py`: explicit six-cell release evidence validation and bounded live acceptance driver.
- Existing `scripts/build.py`, `release.py`, `fleet_mcp/deploy/`: independently versioned products and reversible deployment.
- `serverosd/`: final C++ node owner, migrated from native/node-core with consumer updates.

Each Task below gets an implementation commit and an independent review. Fixes use the same implementer. Exact migration file inventories are derived from source imports before movement and recorded in the task report; they cannot be guessed from old counts. Execution order is Task1, Task14, Task2, Task3, Task5, Task4, then Tasks6–13. Task14 closes a real native delivery-ID adapter gap discovered while implementing Task1. Task4 preflight found that the admitted-child to Fleet-dispatch binding needs Task5's atomic owner commit boundary; the existing admission action_key belongs to synchronous bounded invocation and cannot be repurposed as a Fleet dispatch key.

### Task 1: F1 durable events, watcher and authenticated inbox

**Files:** Create `fleet_mcp/events.py`, `fleet_mcp/run_watcher.py`, `tests/test_fleet_events.py`, `tests/test_fleet_run_watcher.py`, `tests/test_fleet_inbox.py`; modify `fleet_mcp/fleet.py`, `tools.py`, `runtime.py`, `version.py`, `runtime/fleet_mcp_owner.py`, affected surface-count tests and generated plugin contract.

**Interfaces:** `EventStore(path: Path)` with `append(event: dict) -> int`, `read(subscriber: str, *, since: int | None, limit: int, visible: Callable, kinds: list[str] | None = None, level: str | None = None) -> dict`, `ack(subscriber: str, cursor: int) -> int`; `RunWatcher(service, events, attempts, *, clock=time.time)` with `tick() -> dict`, `start()`, `close()`. `FleetService(..., events=None)` exposes `inbox(principal, *, since=None, kinds=None, level=None, limit=100, ack=False)`. EventStore owns read-delivery bounds for ack, not callers. No paths are guessed inside FleetService.

- [ ] Write RED tests: append duplicate returns same seq; reconstruct retains event and cursor; boolean/negative cursor and invalid limits refuse; forged/high ack refuses; subscriber A cannot ack B; hidden pages advance without leaking hidden values; event summaries reject sensitive/unbounded payload. Watcher tests cover ACK→terminal, restart, absent run_id, provider exception, 10/30/120/600-second backoff, 24-hour UNKNOWN and later correction.
- [ ] Run `python -m pytest tests/test_fleet_events.py tests/test_fleet_run_watcher.py tests/test_fleet_inbox.py -q`; record expected failure before implementation.
- [ ] Implement SQLite transactions and validation, unique event ID and bounded polling. Recover watches from `attempts.dispatch_attempts()` accepted receipts and expose any unsupported/malformed records honestly. Use existing identity/authorization gates; never grant new reads through inbox. For project-restricted visibility, absent project evidence fails closed.
- [ ] Wire `fleet_inbox` (`fleet:read`) and owned runtime state injection. HTTP/stdio lifespan starts and closes the observer; no background thread on import or in ordinary fixture construction. Events missing in standalone runtime produce explicit unavailable status, no in-memory durability claim. Surface revision increases once for contract change; update only current counts, preserve historical reports.
- [ ] Verify focused tests plus existing Fleet surface, dispatch and owned-runtime tests on their supported platform. Run complete hermetic suite on Linux at candidate SHA before release; record Windows full-runner limitation if encountered. Commit exact changed paths.

### Task 2: F3 stable principal kind and dispatch work context

**Files:** Create `fleet_mcp/principals.py`, `tests/test_fleet_principal_context.py`; modify `fleet_mcp/fleet.py`, `http_transport.py`, `config.py`, `dispatch.py`, `tools.py`, `runtime.py`, `runtime/agent_control_store.py` only where the existing record schema requires context.

**Interfaces:** Principal retains old positional arguments and adds `kind='client'`, stable subject from trusted config/auth mapping. Optional `work_ref`, `parent_ref`, `agent_ref` flow through create/send, digest, accepted record and Task 1 events. Owner aliases map through configuration; caller input cannot select kind.

- [ ] RED tests assert old callers unchanged; forging kind/owner aliases refuses; subject survives token refresh; changed context under same key conflicts; successful event contains exact context; revoked identity cannot read or replay.
- [ ] Implement trusted mapping and validated context without raw prompt retention. Patch Task 1 project visibility using authoritative project binding/dispatch metadata, not mutable caller claims.
- [ ] Run new tests and auth/dispatch/create/send regressions. Commit and review.

### Task 3: F2 durable event-to-Feishu delivery

**Files:** Create `runtime/fleet_event_delivery.py`, `tests/test_fleet_event_delivery.py`; modify `runtime/fleet_mcp_owner.py`, existing notifications/direct_lark adapter only for needed typed seam.

**Interfaces:** `FleetEventDelivery(events, sender, binding, *, clock)` exposes `tick() -> dict`; sender is the existing configured notification transport. Durable consumer receipt keys use event IDs/project batch, and reuse owner outbox semantics.

- [ ] RED: five-minute same-project completion aggregation; immediate failed/decide; confirmed receipt never resent; retryable not-sent retained; uncertain delivery remains UNKNOWN; restart retains cursor and receipts; wrong audience never called.
- [ ] Implement consumer and owned-service wiring. Resolve unique owner destination from actual existing binding; no destination guess and no fixture sends live messages.
- [ ] Persist notification ingestion before advancing its cursor, separately from delivery settlement, so a pending completion batch cannot block a later urgent failure. Bind each immutable batch to its exact event IDs, content digest, owner recipient and one stable idempotency key. Reuse the transport's actual finite deduplication window; uncertain outcomes outside it remain UNKNOWN, never blind retry. On first installation, an explicit subscription cutover bounds outbound notifications to newly accepted dispatches; old/unknown-origin events remain visible in the inbox without retroactive notification floods. Carry the trusted dispatch acceptance time from the existing owner into derived event metadata as needed, and test restart/cutover boundaries; do not relabel observation time as dispatch time.
- [ ] Run consumer/outbox/notification regressions, commit and review.

### Task 4: F4 dependency-complete manager wake

**Files:** Create `runtime/fleet_event_wake.py`, `tests/test_fleet_event_wake.py`; modify existing `runtime/wake.py`, `continuation_control.py`, owner wiring minimally.

**Interfaces:** `FleetEventWake(events, control_owner, wake_sender, binding)` exposes `tick() -> dict`; consumes Task 2 parent/work context and authoritative current checkpoint dependency set.

- [ ] RED: two required children yield one wake only after both complete; duplicate event/restart no new wake; stale/cancelled parent refuses; revoked grant/epoch refuses; UNKNOWN does not satisfy completion; failed child yields decision path.
- [ ] Implement durable wake claim keyed to checkpoint revision/dependency set; recheck authority before effect; use actual Tokyo serveros manager binding and approved default model readback. No cross-project session reuse.
- [ ] Bind admitted child cycles to exact Fleet dispatch key/digest/target through an authenticated owner operation after Task5's atomic fence exists. Preserve admission.action_key; do not join by coincidental key equality or caller work/parent refs. Store the immutable binding in the existing admission record, derive Run/delivery from the owner's receipt and verified observer binding, and refuse unbound legacy admissions. Exercise the actual authenticated producer and wake consumer together. Model/mode readback must use a supported actual provider read seam; idle session_get on the inspected CloudCLI1.41.0 does not expose those fields.
- [ ] Verify new tests plus continuation/wake tests, commit and review.

### Task 5: F5 owner directives and bounded preemption

**Files:** Create `workengine/directives.py`, `tests/test_fleet_directives.py`; modify Fleet tools/service and existing writer/lease owner at its authoritative mutation boundary.

**Interfaces:** `fleet_directive(scope, text, supersedes, idempotency_key)` available only to trusted owner. Store returns directive reference/version and per-run interruption status; Work Engine commits direction/fence before remote interrupt.

Execute this task in two sequential reviewed phases: **5A** establishes the production owner-policy source and atomic lease/work operation; **5B** implements directives/preemption on that concrete seam. Inspection found only the owner-policy protocol in production, while the prototype's separate synthetic policy registry is not a valid authority. Persist explicit owner-issued policies in the existing AgentControlStore with a configured trusted issuer, fresh trusted context and original kernel peer checks; never infer or auto-create policies from local uid or caller labels. Policy/grant changes share that store's mutation fence. Acquire control-store then Work SQLite transaction in a documented order. Work mutations and their receipts commit in one SQLite transaction; a control-only binding and receipt commit together in the existing JSON ledger while a Work lease guard stays held. Two locks do not make two stores crash-atomic: any actual dual-write requires a durable recovery protocol or refusal.

- [ ] RED: client cannot issue; scope/actor validation; replay once; concurrent completion after fence cannot wake/write; offline interrupt records partial UNKNOWN; reachable fixture stops within 60 seconds; stale writer is denied at real mutation seam.
- [ ] Implement owner operation and per-target reconciliation without fabricating shutdown or rolling back committed direction on network failure.
- [ ] Close the actual owner/lease commit seam before claiming protected writes: current `runtime/daemon.py::_r_agent_lease_authority_verify` and `workengine/serveros_authority.py` deliberately return BLOCKED_OWNER_COMMIT_SEAM for owner-backed point-in-time reads. Use a trusted owner-side atomic lease/mutation operation with a documented lock order and durable receipt; do not change this refusal into verified=true while leaving a verify-then-write race. Add revoke/epoch-change-during-acquire and crash-boundary tests at the actual producer/consumer path.
- [ ] Provide the concrete fence interface needed by Task4's existing-admission binding and wake eligibility. Keep the existing Work Engine lease as the authority and original kernel peer identity at authenticated local ingress. Do not introduce a second authority registry or pass an arbitrary callback/code payload across the RPC boundary.
- [ ] Verify lease/epoch, directive, continuation and tool tests; commit and review.

### Task 6: F6 commander source and current generated plugin

**Files:** Modify `integrations/fleet-v1-first-install/plugin/skills/*/SKILL.md`, plugin manifest/version, generated `plugins/serveros-fleet-ingress/`, `tests/test_fleet_operator_skills.py` and current plugin tests.

- [ ] Add failing assertions that current Skills teach inbox, directive, retained references, honest UNKNOWN and six-cell readiness without polling the user conversation.
- [ ] Update canonical source, generate via `python scripts/fleet_plugin.py build`; do not hand edit generated files.
- [ ] Run `python scripts/fleet_plugin.py check` and plugin suites; commit and review.

### Task 7: D1 and D7 separate release artifacts

**Files:** Modify `plugin/bundle.yaml`, `scripts/build.py`, `scripts/release.py`, `fleet_mcp/deploy/install.py`, `next_install.py`; tests `test_build.py`, `test_release.py`, new `test_fleet_release_isolation.py`.

**Interfaces:** artifact kind selects dependency closure, verifier and revision; node-runtime, device plugin, connector and hub each carry an immutable manifest. Existing release CLI remains backward compatible or refuses ambiguous old invocations explicitly.

- [ ] RED: Skill-only change leaves node digest unchanged; hub-only change leaves connector digest unchanged; each extracted product starts/imports without source checkout; forbidden reverse dependency fails guard.
- [ ] Split build inputs and packaging without copying a second canonical Skill tree; preserve pin/build/install provenance.
- [ ] Run builders, release/rollback suites and import isolation; commit and review.

### Task 8: D2/D3/D10 move assembly/product modules and split CLI

**Files:** Create `fleet_hub/`, `reports/`, `runtime/commands/`; move only enumerated assembly/report/CLI groups from runtime; update `bin/`, build closure, systemd templates and tests. Adopt #406 `layers.yaml` and repository-layer guard through reviewed changes, retaining attribution.

- [ ] Pin existing CLI --help/subcommands and import behavior in tests before movement. Add dependency assertions: Fleet kernel never imports fleet_hub; L1 never imports reports.
- [ ] Move hub after Tasks 1–4; move cards/reports/notifications by separate commits; split CLI by subcommand keeping public invocation unchanged. Temporary shims have enumerated consumers and are removed when migrated.
- [ ] Run affected behavior suites and repository guards after each group. Commit and review each independently testable group.

### Task 9: D4/D6/D5 unify work control and retire duplicates

**Files:** Create `workengine/agent_control/`; migrate ten control modules enumerated in #406 plus dependencies proven by import scan; replace consumers of `mcp/agent_mcp.py`, `fleet/controller.py`, `fleet/agent_control_router.py`; update bundle/adapters and tests.

- [ ] Pin old store schema/path, trusted producer/consumer behavior and actual current CLI/MCP operations. Add negative guard that workengine does not import runtime.
- [ ] Move ownership without changing safety logic. Route covered operations through Fleet; retain necessary functionality in thin adapters, remove duplicate control implementations only after all consumers migrate.
- [ ] Migrate daemon's semantic RPC handlers with their actual consumers. The inspected70-route census assigns43 clear semantic/context methods to Work/reports/ability owners and27 mixed observation/view methods to native facts plus projection. In the already-running colocated hub, bind a bounded serialized Unix compatibility listener that directly accepts the original caller and reads SO_PEERCRED; do not proxy caller-provided peer JSON. Preserve existing ledger paths, alert/card serialization, tick-driven detector cadence and unknown/freshness views. Noncolocated local consumers require independently authenticated native peer evidence or a bounded original-FD helper only where an actual consumer needs it; do not install extra resident Python hubs by default.
- [ ] Run S1/S2, grant, writer, continuation and plugin startup suites. Commit/review migrations separately from behavior changes.

### Task 10: D8/D9 one native serverosd

**Files:** Existing `native/node-core/{core-lite,supervisor,windows-core,contract}`, final `serverosd/`; `runtime/daemon.py`, `native_core_facts.py`, `bin/serverosd`, platform service templates and native workflows.

**Interfaces:** one revisioned unit-actuator ownership declaration read by Python and C++; duplicate actuators fail closed. Existing typed facts and lease contracts remain the interface to hub.

- [ ] RED native/Python tests: duplicate owner rejected, manual stop/busy/expired lease refuses; C++ can observe and recover fixture while hub disconnected; Windows IPC identity/refusal remains intact.
- [ ] Implement ownership guard; migrate observation, recovery, local lease and facts one at a time. Disable corresponding Python effects as each switches. Do not delete daemon until all remaining consumers resolve elsewhere.
- [ ] Wire the existing Linux generation fence into resident persisted state; currently PlanInput.applied_generation is not supplied. Revalidate ownership/desired state under the same effect lock on every attempt, including expiry and same-generation/different-content refusal. Preserve pending journals and recovery budget across transfer; lock exclusion alone does not prevent alternating actuators. Replace the repeating Python facts publisher with bounded native sources or authenticated connector facts, retaining local facts needed during connector/hub outage. Windows keeps its SID/PID/creation-time proof and separate native lane.
- [ ] Rename only once installed entrypoints/rollback mapping are prepared. Run Linux CMake/ctest, Windows native workflows and real toy recovery. Commit/review each takeover.

### Task 11: Q2/A0 honest quality and six-combination acceptance

**Files:** `scripts/fleet_v1_acceptance.py`, `integrations/fleet-v1-release/` current schema/evidence tooling, `tests/test_fleet_v1_acceptance.py`; fix only proven defects in `tests/hermetic-known-failures.txt` and corresponding modules.

**Interfaces:** acceptance manifest schema binds source/artifact digest, device, OS principal, runtime, test operation, result, observed_at and evidence ref. `validate_acceptance(document: dict) -> list[dict]` refuses missing cells, wrong SHA, stale/fixture evidence and unproven operations.

- [ ] RED: five of six cells refuses; reachable-only refuses; wrong runtime/source refuses; full six-cell fixture with proof metadata can validate shape but is never LIVE; UNKNOWN never passes.
- [ ] Implement bounded live driver using typed controls, dedicated projects/sessions and stable idempotency keys. All create/delete/interrupt confined to this campaign's targets. Resolve runtime aliases from actual binding, never guess.
- [ ] Triage baseline 56 entries by fresh Linux run; repair real drift/order contamination; do not relax POSIX checks to claim Windows support. Record Windows runner's current getuid failure and implement an honest supported-platform path as needed.
- [ ] Run complete Linux hermetic gate and targeted Windows tests/native tests. Commit and review.

### Task 12: Q3 ability and installed client alignment

**Files:** only affected canonical payload/manifest in ability-management; ServerOS composition pins/profiles; current release manifest and operator docs. Installation bindings stay local-only.

- [ ] Capture source/build/install/running tuples for each target; identify old management Skill and obsolete plugin consumers.
- [ ] Apply necessary canonical fixes via existing control plane; fingerprint/sanitize/verify; build pinned device/commander artifacts.
- [ ] Install by target with normal release tool, read back actual loaded version, preserve rollback and active sessions. Run actual bounded ability consumer per target. Commit source changes with exact scope and review.

### Task 13: integrate, deploy and accept full V1

**Files:** current release manifest, evidence index and runbook in `integrations/fleet-v1-release/`; source fixes return to their owning task.

- [ ] Independently review whole candidate against approved spec; all mandatory tasks and device cells must pass. Publish exact versioned artifacts from reviewed SHAs.
- [ ] Inspect ownership/activity of existing Next; stage candidate using real configured identity without replacing credentials. Run authenticated MCP and feature E2E on candidate.
- [ ] Upgrade first Linux node, second Linux, Windows under bounded windows, proving old/new compatibility and active-run preservation. Promote hub/main client route only after all needed proofs.
- [ ] Verify create/send/read/interrupt/continue for all six cells, events→owner notice→manager→downstream, preemption/lease refusal, hub and connector restarts, and actual rollback compatibility. Retain negative evidence.
- [ ] Observe deployed candidate for at least two hours while finishing receipts. Only then mark FINAL_ACCEPTANCE, record running digests and hand off operation/rollback commands. If credentials or approvals block a cell, keep release candidate and report exact missing action; never reduce denominator.

### Task 14: preserve delivery IDs and resolve queued execution evidence

**Why added:** Read-only inspection of installed Tokyo @yalehwang/cloudcli 1.41.0 found AC-386: session_send returns deliveryId, runId and queuedMessageUuid separately; run_get accepts exactly one of runId or deliveryId. Fleet currently drops deliveryId and pins the old runId-only schema. The installed mcp-run-get.js digest is eb8e8c12ceeda3a449a5bfd189b5924a458b6a4379270ea78f0c2046dabbb38a. This is observed source, not a guarantee every device has that version.

**Files:** `fleet_mcp/dispatch.py`, `fleet.py`, `providers/base.py`, `providers/cloudcli.py`, `providers/fleet_node.py`, `cloudcli_native.py`, `tools.py`, `node/connector.py`, existing signed native-call route, `events.py`, `run_watcher.py`; create `tests/test_fleet_delivery_correlation.py`, update affected exact-schema/native-contract/receipt tests.

**Interfaces:** optional `delivery_id` is preserved in accepted/replayed receipts. `DeviceProvider.get_delivery(key: SessionKey, delivery_id: str) -> dict` defaults to explicit unsupported; CloudCliProvider calls run_get with deliveryId and session, FleetNodeProvider uses the existing signed closed native-call transport after schema/capability validation. Extend `fleet_get_run` to accept exactly one run_id/delivery_id with old calls unchanged. A delivery response must match idKind=delivery, deliveryId and native sessionId; never guess queuedMessageUuid equals deliveryId.

- [ ] RED: send result deliveryId survives persistence/reconstruction; old receipts without new field replay unchanged; both/neither IDs refuse; wrong session/delivery ID refuses; older device reports unsupported, never false success; two deliveries in one Run remain distinct.
- [ ] Implement optional receipt/context propagation with strict allowed fields and existing current authorization. Update pinned run_get schema/projection to the verified additive shape and gate actual device support. No second node endpoint or arbitrary native command is necessary.
- [ ] RED observer tests: accepted delivery with null Run resolves to the provider-reported Run; completed delivery alone does not certify Run success; cancelled delivery yields cancellation without re-dispatch; mismatched binding produces UNKNOWN; provider restart/missing Run never attaches the current unrelated Run. Repeated delivery readings cannot generate duplicate events or wakes.
- [ ] Extend durable observation identity to delivery references while preserving existing run watches and stored SQLite state. Distinguish delivery terminal from Run terminal: only use confirmed bound Run outcome for work completion, since native delivery completion and provider RESULT may arrive independently. If no trustworthy bound Run proof exists, retain unresolved and emit bounded decision evidence after timeout; do not parse assistant prose as success.
- [ ] Run delivery/dispatch/native schema/node-signed transport/observer tests; record older-provider compatibility and current-source proof. Run exact-SHA Linux CI, commit and independently review before Tasks2–6 depend on this path.

## Execution record

Approved user scope: implementation and deployment, confirmed by “实施吧” and “继续”. Execution uses sequential task implementers and independent task reviews; no additional plan permission checkpoint is introduced. The per-plan SDD ledger records task states, SHA ranges, exact test results and rulings. No task is completed by a documentation claim alone.
