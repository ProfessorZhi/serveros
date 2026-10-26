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

Each Task below gets an implementation commit and an independent review. Fixes use the same implementer. Exact migration file inventories are derived from source imports before movement and recorded in the task report; they cannot be guessed from old counts.

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
- [ ] Run consumer/outbox/notification regressions, commit and review.

### Task 4: F4 dependency-complete manager wake

**Files:** Create `runtime/fleet_event_wake.py`, `tests/test_fleet_event_wake.py`; modify existing `runtime/wake.py`, `continuation_control.py`, owner wiring minimally.

**Interfaces:** `FleetEventWake(events, control_owner, wake_sender, binding)` exposes `tick() -> dict`; consumes Task 2 parent/work context and authoritative current checkpoint dependency set.

- [ ] RED: two required children yield one wake only after both complete; duplicate event/restart no new wake; stale/cancelled parent refuses; revoked grant/epoch refuses; UNKNOWN does not satisfy completion; failed child yields decision path.
- [ ] Implement durable wake claim keyed to checkpoint revision/dependency set; recheck authority before effect; use actual Tokyo serveros manager binding and approved default model readback. No cross-project session reuse.
- [ ] Verify new tests plus continuation/wake tests, commit and review.

### Task 5: F5 owner directives and bounded preemption

**Files:** Create `workengine/directives.py`, `tests/test_fleet_directives.py`; modify Fleet tools/service and existing writer/lease owner at its authoritative mutation boundary.

**Interfaces:** `fleet_directive(scope, text, supersedes, idempotency_key)` available only to trusted owner. Store returns directive reference/version and per-run interruption status; Work Engine commits direction/fence before remote interrupt.

- [ ] RED: client cannot issue; scope/actor validation; replay once; concurrent completion after fence cannot wake/write; offline interrupt records partial UNKNOWN; reachable fixture stops within 60 seconds; stale writer is denied at real mutation seam.
- [ ] Implement owner operation and per-target reconciliation without fabricating shutdown or rolling back committed direction on network failure.
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
- [ ] Run S1/S2, grant, writer, continuation and plugin startup suites. Commit/review migrations separately from behavior changes.

### Task 10: D8/D9 one native serverosd

**Files:** Existing `native/node-core/{core-lite,supervisor,windows-core,contract}`, final `serverosd/`; `runtime/daemon.py`, `native_core_facts.py`, `bin/serverosd`, platform service templates and native workflows.

**Interfaces:** one revisioned unit-actuator ownership declaration read by Python and C++; duplicate actuators fail closed. Existing typed facts and lease contracts remain the interface to hub.

- [ ] RED native/Python tests: duplicate owner rejected, manual stop/busy/expired lease refuses; C++ can observe and recover fixture while hub disconnected; Windows IPC identity/refusal remains intact.
- [ ] Implement ownership guard; migrate observation, recovery, local lease and facts one at a time. Disable corresponding Python effects as each switches. Do not delete daemon until all remaining consumers resolve elsewhere.
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

## Execution record

Approved user scope: implementation and deployment, confirmed by “实施吧” and “继续”. Execution uses sequential task implementers and independent task reviews; no additional plan permission checkpoint is introduced. The per-plan SDD ledger records task states, SHA ranges, exact test results and rulings. No task is completed by a documentation claim alone.
