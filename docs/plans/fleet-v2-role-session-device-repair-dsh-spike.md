# Fleet V2：角色与 Session 分离、设备修复 Agent、DeepSeek Harness

> Status: **DESIGN + PURE OFFLINE PREVIEW** (2026-10-22). Source branch is an independent development spike, not installed, deployed, integrated, or V1 signed off. It does not touch real OS identities, Node Core, grants, production state, or providers.

## Product decision: Role is not Session, and routing is not authorization

Reuse the canonical Work Engine, Agent Control, Node Core, Ability Fabric, Fleet and their existing records. A project Role is a versioned capability/competence template. Its current Work responsibility is a **derived view** from the current Work, immutable session binding, owner epoch, independently verified grant, and exclusive writer lease. Do not add a second Assignment DB, Agent persona process pool, message-bus authority, Work ledger, or another daemon.

~~~
Incident / accepted Work Item
  -> project Role requirements (diagnostician, implementer, reviewer)
  -> effective Abilities, data location, runtime constraints
  -> candidate Device + OS principal + credential + provider + capacity
  -> same-work idle Session OR new clean isolated Session proposal
  -> authoritative Grant / epoch / writer lease / effect admission
  -> bounded native Run -> actual evidence -> independent acceptance
~~~

The V1 'coordinator/execution' session_role enum is a *thread purpose*, not the V2 project roles. Preserve immutable bound_item_ref, provider-native session IDs, and current V1 reader compatibility. A Session is stateful context, not a fungible or clearable pool slot. One write scope and owner epoch must have at most one active authorized writer.

## Device-repair example

1. The existing Node Core observes a specific service incident and may perform only its **already owner-approved deterministic bounded self-heal**. Successful systemd command means HANDLING; only a later verified readiness transition means RECOVERED. See runtime/fleet_recovery_events.py.
2. If still unhealthy, produce an incident-linked *proposed* Work for read-only diagnosis. Resolve a device-diagnostician Role to actual fresh-effective Abilities and a suitable Runtime/Session. If the target device is down, consider a different authorized diagnostic node without assuming the target is controllable.
3. An idle provider Session may be proposed for **the same Work, project and epoch**. Otherwise propose a clean Session in a verified isolated workspace with an immutable new binding. No cross-project context recycling, silent handoff or interruption of a busy/unknown Agent.
4. The specialist reads logs/status only through its individually permitted diagnostic operations. It returns evidence, root cause, repair plan, rollback and affected resources. It **cannot** approve its own repair.
5. Protected repairs (restart, OS permission/user/account, service config, packages, production or secrets) require the *current* canonical Owner/Grant/lease/effect boundary and specific approval. An unavailable trustworthy Writer is **BLOCKED**, not an invitation to access raw SQLite, use another UID or bypass Fleet.
6. An independent reviewer uses a different context to verify post-repair health and any side effects. Successful model text or exit code alone does not close the incident; the Work Parent accepts verified evidence.

Do not start an LLM when the deterministic recovery path already solved the problem.

## R0 deliverable in this branch

'runtime/v2_role_session_preview.py' is a pure, deterministic preview over a narrow schema ('serveros/v2-role-session-preview.v0'). It reads synthetic Work claims, Role requirements and Runtime observations. It emits ROUTE_PREVIEW_ONLY candidate proposals, NO_SAFE_PREVIEW refusals, or INVALID_PREVIEW_INPUT. It never chooses a winner, issues a grant, verifies identity, creates/resumes/sends to a Session, stops an Agent, persists state, or writes a protected effect. Output always has writes=[] and explicitly marks admission/effect_commit=NOT_EVALUATED.

The input requires explicit Work epoch, project, executor-device scope, protected-effect flag; role required Abilities/provider families; a candidate device/OS principal/credential/runtime, effective Abilities, readiness, free-slot evidence, timestamp, and **explicit Session inventory field**. Missing Session inventory is UNKNOWN, not an empty slot. Session resume requires the same Work+project+epoch and a proven IDLE provider Session. Otherwise a new clean Session is only a proposal if clean-session support and workspace isolation are explicitly reported.

**Trust note:** fixture strings SUPPORTED, VERIFIED and READY are only claims from the snapshot. They are not attestation or live authority. An integrated consumer must verify actual current Node/profile/provider facts and fresh grant/epoch/lease at dispatch **and at effect commit**. Never turn a role/routing preview into a capability token.

Hermetic test suite: 'tests/test_v2_role_session_preview.py' covers current Work continuation, cross-Work separation, epoch ABA, independent reviewer, busy/unknown Session refusal, omitted Session inventory, exact OS principal/runtime, project/device scoping, provider/Ability readiness, stale and duplicate data, protected-effect approval and no writes.

## DeepSeek Harness: a runtime candidate, not control authority

Official sources: https://github.com/deepseek-ai/deepseek-harness and its SAFETY.md and python/sdk/README.md. DeepSeek Harness is MIT-licensed developer-preview software with potential breaking changes, and its own security notice says its sandbox is not an adequate trust boundary.

Prefer a later pinned-version Python 'deepseek-harness-sdk' proof over launching a web UI. The SDK drives a dsh profile through subprocess stdio JSON-RPC; explicit isolated dsh_home, cwd/workspace, credentials, plugin list, cost limits and output receipt are mandatory. Provider-native 'resume_session(id)' restores history; it does not restore external shells/browser effects, and 'start_session(id)' is not persisted restoration. No actual provider installation, authentication, model turn or endpoint access occurs in R0.

Native RunResult/final_response or agent-idle is not canonical Work ACCEPTED. Persist a stable attempt/Run mapping, distinguish input acknowledgment from accepted result, and represent an ambiguous post-dispatch timeout as EFFECT_UNKNOWN until independently reconciled. Do not let the Harness execute privileged operations or read production secrets just because an Agent has a 'repair' Role.

## OpenAgents: optional delegation transport

Evaluate https://github.com/openagents-org/openagents-sdk, not historical xlang-ai/OpenAgents. Its task_delegation/messages may serve as an optional **transport** after native Work Parent → worker → independent reviewer has a proved baseline. OpenAgents is not authoritative for Node identity, Work state, grants, leases or task acceptance. The SDK's current lightweight validate_agent implementation and optional in-memory task storage are not sufficient for trusted ServerOS identity or durable recovery. Pin and audit the version before any loopback PoC.

Map third-party task IDs idempotently to existing Work attempts, never reproduce work authority in SDK messages. Compare native delegation versus OpenAgents on the same scenario by accepted-outcome correctness, delayed/duplicate messages, human interventions, latency, token/device cost, and crash recovery. Hosted Workspace/network pairing, installing SDK/daemon, opening ports, external data sharing and device resource use are separately authorized, never implied by a design document.

## Yale/quay lessons

Reviewed https://github.com/yaleh/quay and especially packages/quay-native/README.md and orchestration/SPEC-web-session-observability-and-control-2026-08-24.md. Reuse **patterns**, not Quay's task store: Provider ABI, explicit native sessionId separate from task ID/PID, reproducible criteria/DoD, worker-driver lifecycle and readback. Quay itself warns about unverified session-target inference, unclear interactive Session stop semantics, and unauthenticated cross-session message injection. ServerOS must not import a second task authority or treat tmux/PID as security identity.

## Release gates (all independent)

| Phase | Scope | Acceptance | State |
| --- | --- | --- | --- |
| R0 | Pure offline role → Session preview | negative matrix; cannot write or dispatch | local tests; external CI NOT_RUN |
| R1 | Canonical read-only data joins | authenticated Work/Role/Ability/Device observations; stale facts refuse | NOT_STARTED |
| R2 | Pinned isolated DeepSeek SDK adapter | exact session resume, crash/timeouts, no credentials leaked, bounded paid actions | NOT_STARTED; needs a device-test authorization |
| R3 | Trustworthy Writer + effect fencing | dual UID/IPC/DB isolation, real owner and epoch, effect-time checks | BLOCKED by #237 and separate OS permission approval |
| R4 | Optional OpenAgents localhost PoC | recoverable delegation, no identity takeover, independent QA, measurable benefit | DEFERRED, #187 |
| R5 | Live bounded device repair | owner-granted scope, no busy-session takeover, rollback/health readback | NOT_AUTHORIZED |

Pre-existing release gate: https://github.com/ProfessorZhi/serveros/issues/237. Campaign: https://github.com/ProfessorZhi/serveros/issues/244. Existing V2 decision: docs/architecture/AGENT-IDENTITY-SESSION-DELEGATION.md. Existing research: docs/plans/fleet-next-agent-federation-v0.md.

**This branch is a preview only; the earlier A-prime raw-SQLite Writer isolation problem remains open, and no production V1 or V2 signing claim follows.**
