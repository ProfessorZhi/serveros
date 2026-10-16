# Fleet V1 Complete-Nodes Delivery Campaign · execution and acceptance plan

> **2026-10-17 accepted delivery scope · DESIGN / TASK PLAN, not deployed evidence.**
> The product authority is [ARCHITECTURE.md](../../ARCHITECTURE.md), the Fleet component contract is [FLEET-V1-TOPOLOGY-OPERATIONS.md](../architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md), and [Issue #244](https://github.com/ProfessorZhi/serveros/issues/244) is the campaign visibility and acceptance ledger. This plan does not replace the existing Work Engine, Agent Control, Ability registry or release owner.
>
> **Changed release goal:** final full-fleet V1 includes actually usable Claude Code **and Codex** on the three authorized device profiles through native/CloudCLI/Fleet where supported, not just a static Codex schema. Claude Code local plugin acceptance is the **first** device/client milestone; ChatGPT plugin registration/Feishu App2 consumer work is a later rollout, not a blocker for initial code integration. OpenAgents and provider-independent dynamic Agent teams remain optional V2.
>
> **Target locations:** Tokyo shared Linux / vince as Work/integration authority, Dell A6000 Linux / fjd as independent Linux/compute test executor, Windows FJPCOS as later targeted native acceptance only. Do not turn Windows into a development/build host or claim a V1 complete acceptance without its required short native check.

## 1. Final operating picture

~~~text
Claude Code Fleet developer plugin + one canonical Operator Skills source
                       |
             Authenticated Fleet Next MCP
     devices / runtimes / native sessions / tools / grants
                       |
      Work Engine + Agent Control current authority
                one writer / epoch / lease
                       |
    +------------------+--------------------+
    |                  |                    |
 Tokyo vince         Dell fjd           Windows FJPCOS
 per-principal       per-principal       per-principal
 C++ Core            C++ Core            C++ Core + Win adapter
 OS supervisor       OS supervisor       SCM/TaskScheduler
 resources/service   resources/GPU       OS resources/SSH tunnel
 Tailscale/Fleet     CloudCLI/Node       Tailscale/Selkies/RDC
 CC + Codex          CC + Codex          CC + Codex
 CloudCLI/Node       Device Abilities    Device Abilities
 Work/Abilities
    +------------------+--------------------+
                       |
            Evidence / Integration / QA
                       |
   fixed multi-repo RC + rollback + real tested capability
~~~

One Core *per authorized OS principal*, not one Core per provider Session. The C++ Core does not run LLM logic, own Work, mint grants, bypass user-scoped service managers, or control other OS users. Real runtime users and effective models are measured rather than assumed. All 33 Fleet Next tools are a **catalogue** (9 authored Fleet + 22 CloudCLI native mirror + 2 capability reads); the six possible scopes are read/send/create/control/approve/navigate. The caller's actually granted scope, local node owner floor, runtime/project binding and provider handler are separately checked.

Local OS managers own actual service lifecycles. If Fleet/Tokyo connectivity is lost, an already-authorized local Core may restore a specifically approved local connection dependency under a bounded persistent policy, but must not mint new Work authorization or pretend to have delivered new messages.

## 2. Service and access topology to measure, not guess

| Surface | Owner, actual probe | Expected recovery policy |
| --- | --- | --- |
| Local Core itself | OS manager state, authenticated bounded IPC, fresh sampling, version and writer identity | OS service manager owns Core process restart; no second Core writer |
| CPU/RAM/swap/PSI, disk, GPU | Per OS principal resource scope, observed_at/TTL; Dell GPU only under legitimate principal authorization | Report pressure; never restart model tasks to lower pressure |
| CloudCLI + native Claude Code/Codex | Actual service + provider Session/Run/real ready + credentials presence, never raw values | Owner-approved exact user unit only, local readiness reprobe; NEEDS_LOGIN requires human |
| Fleet Node Connector / authorized transport | Listener + signed device/runtime identity + actual operation capability | Scoped unit recovery only; do not confuse GET health with runtime authorization |
| Tailscale on each device | Locally observed OS service + tailnet readiness, no credential leakage | Local offline recovery possible only for explicitly owned/user-approved service |
| SSH tunnels | Locally owned unit/task/process + listener + upstream connectivity + freshness | Local bounded restart when permitted, independent of remote application's service state |
| Windows local 127.0.0.1:16095 | User-stated browser endpoint for SSH-forwarded **Selkies / Claude Web**; port is an input to verify, not an observed service name | Windows Core may recover owned SSH forward only; remote Selkies must be fixed at the actual remote owner. Listener/SSH transport/HTTP auth/interactive stream are distinct evidence |
| Selkies / Claude Web server | Actual server owner on resolved node; application and authenticated stream readiness | Only host-owner unit is eligible for host-local repair |
| Remote Desktop Control (RDC) | Windows local adapter/service and authenticated connection readiness, actual application session if observable | Read/health under its own policy; no privileged remote desktop action inferred from tool presence |
| Per-device effective Abilities | Original ability-management manifest/revision joined with actual local installation, readiness and runtime/grants | No second registry or blind Skill installation |

No arbitrary remote shell, port or file path is accepted from a Fleet caller as a recovery action. Manual stop, masked/disabled, AGENT_BUSY, NO_LEASE, stale observation, account mismatch, NEEDS_LOGIN and depleted backoff budget are explicit refusals.

## 3. Work packages (trackers are not the Work authority)

| ID / tracker | Authoritative file-owner area | Default execution | Dependencies | Smallest meaningful deliverable |
| --- | --- | --- | --- | --- |
| S1 [#237](https://github.com/ProfessorZhi/serveros/issues/237) | work-engine exclusive lease, ServerOS Agent Control + coordination/daemon admission | Tokyo Security | Work Engine [#13](https://github.com/ProfessorZhi/work-engine/pull/13), ServerOS [#241](https://github.com/ProfessorZhi/serveros/pull/241) | v2 writer-binding producer+consumer, epoch/TTL/token check on actual mutating path, no stale write |
| S2 [#245](https://github.com/ProfessorZhi/serveros/issues/245) | Existing effect/attempt owner only; avoid simultaneous S1 file edits | Tokyo Safety; separately reviewed | S1 interface frozen | Durable dispatch attempt, same-key payload conflict, restart-reconcile UNKNOWN outcome |
| F1 [#243](https://github.com/ProfessorZhi/serveros/pull/243) | fleet_mcp and Node signed-route | Tokyo Fleet | #225/#228/#229/#231, S1 policy boundary | One SHA-pinned candidate with 33-tool/6-scope honest operation and negative capability tests |
| N1 [#175](https://github.com/ProfessorZhi/serveros/issues/175) | native/node-core Linux C++ and its tests | Tokyo source patches + Dell/fjd bounded Linux run | [#242](https://github.com/ProfessorZhi/serveros/pull/242) | One resident/observe-only Linux Core, reliable resources/service/probes, real toy recovery, portable lease tests |
| N2 [#246](https://github.com/ProfessorZhi/serveros/issues/246) | Windows-native Core/SCM/task scheduler adapter; no conflict with Linux source owner | Tokyo/Dell fixtures; short Windows acceptance at gate | N1 typed interface | Secure Windows provider parity; no POSIX-only O_NOFOLLOW assumption |
| N3 [#247](https://github.com/ProfessorZhi/serveros/issues/247) | Network/access service profiles and bounded health/recovery adapters | Tokyo + Dell; Windows later read-only | N1/N2 | Verified Tailscale/SSH/16095→Selkies/RDC layered probes and explicit safe-owner recovery policy |
| R1 [#248](https://github.com/ProfessorZhi/serveros/issues/248) | Runtime/CloudCLI provider mapping and adapter tests, independent of Fleet MCP code | Tokyo + Dell; Windows native at gate | F1 and S1 contracts | Native CC + Codex installed/authenticated profile and actual requested/resolved/effective/lifecycle matrix |
| G1 [#249](https://github.com/ProfessorZhi/serveros/issues/249) | existing abilities/binding and ability-management source | Tokyo semantics + Dell compute tests | N1/R1 | One effective device Ability per class executes under grant, evidence goes to current Work owner |
| P1 [#186](https://github.com/ProfessorZhi/serveros/issues/186), [#230](https://github.com/ProfessorZhi/serveros/pull/230) | integrations/fleet-cc-dev-plugin and one canonical shared Skills source | Tokyo plugin | F1/R1/S1; approved local auth | Real local CC plugin MCP tools/list, Session/Core/Ability calls and negative/positive receipt verification |
| M1 [#170](https://github.com/ProfessorZhi/serveros/issues/170), [#233](https://github.com/ProfessorZhi/serveros/pull/233) | runtime monitor/cards/snapshot journal | Tokyo monitoring | N1/N2/F1 readback | Trusted multi-device Core summaries, one state journal, V13 LIVE/REPORT safe audience receipts |
| I1 [#250](https://github.com/ProfessorZhi/serveros/issues/250) | Integration branch/manifest + conformance only (no direct changes to lane-owned implementation) | Tokyo Integrator + independent QA | each immutable verified PR | One multi-repo RC, result/evidence/rollback manifest and rigorous local/real acceptance matrix |

A lane which cannot write its files under the actual host/provider permission mode reports BLOCKED_BY_PERMISSION and a proposed patch; it **must not bypass the approval prompt or edit another owner worktree**. In particular, an earlier Dell run timed out waiting for write permission; prefer Tokyo's authorized code writer and Dell's actual test executor until resolved.

## 4. Dependencies and bounded concurrency

~~~text
S1 writer safety -----+------------------+
S2 durable effects ---+                  |
F1 Fleet MCP ---------+----> I1 integration/QA ----> RC
N1 Core Linux --------+           ^
   +-> N2 Windows ----+           |
   +-> N3 Network ----+           |
R1 CC+Codex adapter --+           |
G1 Device Abilities --+           |
P1 local CC plugin ---+-----------+
M1 Fleet/V13 ---------+
~~~

- Package definitions, source inspection and mocks may proceed in parallel. A protected write path or authenticated real device effect cannot be accepted without S1/S2.
- Tokyo: max **1–2** bounded serveros development Runs when observed pressure and unrelated agent activity permit, *not* a hard entitlement. Dell/fjd: max **one** heavy bounded Run when the Linux user and GPU workload allow. Windows: **no routine development Runs**; a short owner-approved native check after mocks is ready.
- No automatic subagent fan-out. Avoid a separate long-lived LLM manager process and ten idle-claiming sessions; one lightweight Coordinator context and one independent QA Session may be resumed as bounded tasks. A Session remaining in the provider index is not a running task.
- Each worker uses one owned isolated branch/worktree and a current base SHA, a narrow file allowlist, a local test recipe and explicit stop conditions.
- Never use a failed GitHub Actions job whose only reason is exhausted hosted minutes as a source-code failure. Document HOSTED_CI_QUOTA_WAIVED; **local bounded tests must still really pass**. A failing local, contract or device test remains a red release gate.

## 5. Hourly continuation protocol (automation-triggered, not a resident daemon)

1. Read [campaign #244](https://github.com/ProfessorZhi/serveros/issues/244), lane trackers and exact PR head/base SHA; read Fleet current device availability and target Sessions/Run metadata. Do not trust historical cached Session state, old Run ID or PR title.
2. Reconcile outcomes using current provider history and authoritative Git/Work receipts. If a run ID was lost across Fleet boot, report UNKNOWN / seek proof; **never create a second paid Run to resolve ambiguity**. Compare expected SHA/branch and worktree owner.
3. When a lane's previous bounded Run is terminal, the file owner and permissions are unchanged, the next dependency is verified, and observed resource gate is GREEN, dispatch **one** carefully scoped next dev turn with a stable idempotency key. At most one new attempt per lane per hour and obey Tokyo/Dell concurrency caps. Do not rerun a successful task because GitHub Actions show no green.
4. If resource gate is RED, do GitHub-only source review or hold; if permission is missing / Session requires human approval, mark NEEDS_HUMAN and do not route around it. If current effects are unknown, mark EFFECT_UNKNOWN and reconcile first. If dependencies are missing, mark WAIT_DEPENDENCY.
5. Inspect any PR, local test receipt, QA review and reader/source status; route a failing implementation back to the **same lane owner** with exact failure, never blindly merge its PR. Integrator and independent QA maintain a single reviewable release candidate and source manifest.
6. Once all full-V1 DoD is evidenced and accepted, **disable the recurring hourly task** and report final SHA/receipt links. A timer notification is not guaranteed unattended native scheduling; critical autonomous dispatch should ultimately be consumed by a durable ServerOS Work/scheduler bridge with effect fencing.
7. No prod restart, grant widening, credential/tunnel login, Feishu real delivery, Windows native action or ChatGPT plugin install via the hourly task. These require separate explicit owner approval.

### Safe Task / Result envelope

Each dispatch: campaign_ref, work_ref, lane_id, accepted AC/non-goals, parent/owner, provider Session ref, device/principal/runtime, source revision, repo base/head, write allowlist, grant reference, resource budget, idempotency key, deadline/expiry, evidence requirements, rollback conditions. Each outcome: actual provider Run, version, accepted/refused, changed SHA/PR, tests with real exit codes, unresolved effects, checkpoint/continuation and next ready dependency. Never embed bearer tokens, raw secrets, other users' transcripts or a granted-rights mirror.

## 6. Final evidence checklist / hard release gates

| Gate | Required actual proof | May mock satisfy it? |
| --- | --- | --- |
| P0 Work/effect safety | #237 T1–T12 under realistic crash/restart/lease expiry/handoff, protected writes fenced, no unbounded replay | **No** for actual commit/effect routes |
| Per-node Native Core | Linux Tokyo + Dell and Windows owner-local Core readbacks, exact versions/identity, fresh CPU/RAM/disk and PSI/GPU where applicable | No for platform LIVE |
| Per-node self-healing | Owner-approved boot, local unit/tunnel/Connector/Tailscale bounded restore/refusal, post-action functional probe, rollback | Toy proves mechanism; not authorized production |
| Windows SSH/Selkies/RDC | Read local 127.0.0.1:16095 listener, owned tunnel/upstream, remote Selkies application and stream (where access authorized), RDC/Tailscale state | No for real network |
| CC/Codex+CloudCLI | Actual per authorized device/credential runtime, model/mode effective, Session create/send/read/interrupt/recovery and account isolation | No for LIVE |
| Fleet Next 33/6 | Authenticated current client discovery and allowed actions, per-tool provider capability, denied grants, signed Core route | No for real authorized route |
| Device Abilities | Current ability-management manifest, actual bound implementation per device + bounded execution result/evidence | No for installation/readiness |
| Local CC Fleet Plugin | CC loads one Skills source and valid MCP, authenticated tools/list and real supported workflow | No |
| Integration + QA | Immutable multi-repo release manifest; isolated local tests at exact SHAs, independent QA, rollback and remaining gaps | Hosted CI waiver allowed; local/real gates not waived |

Until every mandatory row has truthful evidence, status is RC_CANDIDATE or BLOCKED, **not COMPLETE_V1**. Historical documentation, an old CloudCLI declaration, installed plugin component listing, a successful public health GET and a PR draft are not verification.

## 7. What is expressly later

ChatGPT App registration/OAuth App2/brand asset packaging, Feishu production app permissions, arbitrary extra devices, optional OpenAgents SDK and dynamic role-persona federation may continue in separate authorized initiatives. They do not block *source* convergence but can remain user-facing rollout items. If the user's final V1 acceptance later includes a formally connected ChatGPT app, promote that explicit client acceptance gate rather than silently treating a local CC plugin pass as a ChatGPT installation.
