# ServerOS Architecture

> **Canonical architecture.**
>
> This document is the single product-architecture authority for ServerOS.
> Earlier architecture statements and
> `docs/architecture/PRODUCT-DISCOVERY-DECISIONS-20260921.md` are historical
> inputs once their decisions are absorbed here.
>
> A mechanism described here is a **target architecture** unless its
> implementation/evidence is explicitly identified elsewhere. Architecture,
> implementation and LIVE deployment are different claims.

---

## 0. Definition

**ServerOS is a personal user-space Agent Work OS.**

It exists to make Claude Code, Codex and future coding agents materially better
at long-running real work without wrapping them in a heavier workflow system.

Its primary promise is:

> Open a fresh Agent session, mention a work topic, and within seconds the
> Agent can determine the relevant project / Item / prior work, recover the
> right memory, discover the right capability and environment, and continue
> without the user re-explaining yesterday.

ServerOS optimizes for:

```text
right Environment
+ right Context
+ right Memory
+ right Capability
+ right Safety boundary
at the right time
```

The product is intentionally **large in installed capability and small in
active context**.

ServerOS is not a second operating system, not a workflow engine, and not a
second planner above Claude Code or Codex.

---

## 1. Product principles

### 1.1 Progressive disclosure is load-bearing

A normal Agent session must not preload:

- the full Ability catalogue;
- all Skills;
- every MCP schema;
- all machine status;
- all project history;
- all Feishu sources;
- all Memory;
- every Session transcript.

Instead:

```text
L0  Thin bootstrap
    device ref / current cwd / ServerOS entrypoints
        ↓
L1  Working Context Capsule
    resolved intent / project / Item / checkpoint / constraints
        ↓
L2  Relevant expansion
    memory / ability / provider / decision / document details
        ↓
L3  Deep source evidence
    raw Feishu / GitHub / transcript / logs / original documents
```

The Agent may skip discovery steps when it already knows what it needs.

Progressive disclosure is not a wizard. It is a latency/context discipline.

### 1.2 Intent first, binding second

A new session starts nearly empty.

ServerOS does **not** bind the session to the most recent Item merely because it
was recent.

```text
session starts
  → thin bootstrap
  → user prompt
  → intent resolution
  → identify general / project / existing Item / new investigation
  → assemble working capsule
  → expand on demand
```

Read-only context expansion is automatic by default.

### 1.3 Personal first

The primary user is one person and that person's devices, work, projects and
work habits.

ServerOS may remain technically extensible, but it must not accumulate
multi-tenant SaaS machinery unless a real need appears.

### 1.4 Native Agent capability stays primary

ServerOS supplies context, memory, environment, ability resolution, continuity
and safety.

Claude Code / Codex decide how to reason and execute:

- grep vs docs;
- experiment vs static analysis;
- test-first vs another local method;
- subagents;
- implementation strategy.

ServerOS must not force ceremony that makes the Agent worse.

---

## 2. Layer 0 — Compliance and non-interference

Compliance precedes every other capability.

The first question is not "can I do this?" but:

> **Am I allowed to affect this resource, and can I do so without surprising
> other users or systems?**

### 2.1 Ownership is evidence, not inference

Visibility does not imply ownership.

Unknown ownership is not "unowned".

Foreign / unknown-owned resources are protected by default.

### 2.2 Tokyo principal policy

For the Tokyo shared development host:

```text
default principal = vince
```

Current explicit exception:

```text
target          shared AI gateway
system owner    ubuntu
maintenance     explicitly delegated to Vince
principal       ubuntu only for that target/scope
```

This does **not** grant authority over arbitrary `ubuntu` files, processes,
containers, repositories or services.

Privilege is target-scoped, not username-scoped.

### 2.3 Network safety

Network mutation on a shared host is high risk.

Changes involving any of the following require a network preflight:

- Docker network creation/change;
- route changes;
- iptables/nftables;
- VPN / Tailscale kernel routing;
- subnet routers / exit nodes;
- reverse tunnels;
- new non-loopback listeners;
- shared proxy/gateway mutation.

Preflight must consider at least:

```text
current routes
+ existing Docker networks
+ reserved / real destination CIDRs
+ current listeners/tunnels/VPNs
+ ownership
+ planned bind address/subnet
+ rollback
```

A netshadow-style conflict check is a hard gate.

Unknown/incomplete policy fails closed.

### 2.4 Shared host safety

Default expectations:

- no global Docker prune;
- no destructive shared-volume cleanup;
- no daemon reconfiguration without approval;
- no system package/service mutation without approval;
- user-space tooling preferred;
- loopback-only service bind unless a wider bind is explicitly intended;
- no assumption that file mode alone provides isolation from other privileged
  users on the host.

---

## 3. Daily operating model

ServerOS is not only a runtime helper. It maintains a daily work loop.

```text
Feishu / Docs / Wiki / Minutes / GitHub / Sessions
                    ↓
           Incremental Source Capture
                    ↓
              Archive / Observation
                    ↓
         Provisional Understanding
                    ↓
             Understanding Delta
                    ↓
          Human Reconciliation
                    ↓
       Approved Understanding Checkpoint
                    ↓
            Personal Candidate Board
                    ↓
        ServerOS priority suggestions
                    ↓
              Human selects Today
                    ↓
              Candidate → Item
                    ↓
        Idea / Requirement Discovery
                    ↓
             Requirement Draft
                    ↓
              GPT Review / Decision
                    ↓
        Research / Design / Architecture
                    ↓
           Agent Execution + Quay
                    ↓
          Verification / E2E Evidence
                    ↓
           Item Report + Daily Report
```

This is a product loop, not a forced state machine.

Small work may skip steps. Research may not produce code. A no-code validation
may satisfy an Item. A test may send work back to design.

---

## 4. Structured Understanding

Structured Understanding is the durable, current model of the user's work
world.

It is project-first.

A project view may include:

- current state;
- responsibilities;
- people and their project roles;
- commitments;
- requirements / candidates;
- decisions;
- risks / unknowns;
- important knowledge;
- recent deltas.

People and role information are relational: the same person may play different
roles in different projects.

### 4.1 Reconciliation is mandatory for important understanding changes

Important deltas do not silently become durable truth.

Examples that require reconciliation:

- responsibility changes;
- project status changes;
- people/ownership changes;
- commitments;
- new requirements;
- priority changes;
- durable project facts.

Reconciliation continues until every required delta is in a clear state:

```text
ACCEPTED
EDITED
REJECTED
DEFERRED_WITH_REASON
```

The output is an **Understanding Checkpoint**.

An unresolved understanding delta does not freeze unrelated, already-approved
Items.

### 4.2 Archive, Understanding, Knowledge, Memory and Work are different

```text
Archive        what happened
Understanding  what we currently believe reality is
Knowledge      what we curate for future reuse
Memory         how Agents efficiently recall the above + prior experience
Work           what we choose to do next
```

Collapsing these concepts creates false authority.

---

## 5. Source capture, Follow and Feishu

### 5.1 Source capture

ServerOS may shallow-archive authorized Feishu chat sources by default.

The archive policy is source-scoped and supports an explicit blacklist.

Blacklist authority uses stable chat/app IDs, not display names.

A shallow message archive should prefer:

- native chat/message ID;
- timestamp;
- sender identity/type;
- text;
- reply/thread relation;
- message type;
- attachment locator and metadata;
- provenance/cursor.

Large files, video/audio and expensive linked content are not downloaded merely
because a message exists; deeper capture is demand/relevance driven.

### 5.2 Follow

A durable `Follow` relationship answers:

> What should ServerOS continue watching?

Possible sources:

- Feishu chat;
- Feishu document;
- Wiki subtree;
- GitHub repository / issue / release;
- external research source.

Follow records source identity, scope/project, snapshot policy, understanding
policy and notification policy.

### 5.3 Feishu's real content surfaces remain real

ServerOS does not invent a fake cloud hierarchy.

The three major Feishu surfaces remain:

- **My Docs**;
- **Drive**;
- **Wiki / Knowledge Base**.

ServerOS may maintain locators, semantic roles and local snapshots across those
surfaces.

It does not blindly move other people's documents.

### 5.4 Knowledge-base watching is incremental

Large knowledge bases should not be recopied in full on every cycle.

Use:

```text
metadata inventory
+ Follow
+ revision/change detection
+ changed-page archive
+ meaningful delta
+ reconciliation / knowledge update
```

### 5.5 Meetings / Minutes

Original Minutes objects remain canonical.

Useful meeting content may produce a derived project note with:

- summary;
- decisions;
- actions;
- relevant excerpts;
- source locator.

Meeting relevance and retention remain separate from deletion authority.

Incidental audio does not become work understanding merely because it was
recorded.

---

## 6. Session Fabric

Session is a first-class ServerOS resource.

A Session answers:

> Which Agent work thread is this, what is it for, where is it running, what
> does it belong to, and how can it continue?

### 6.1 Logical roles

Common roles:

- **Coordinator Session** — reconciliation, board, project switching, routing;
- **Execution Session** — project/Item implementation, research or test work;
- **Maintenance Session** — incident diagnosis and repair.

These are semantic roles, not mandatory process classes.

### 6.2 One Item may have many sessions

Example:

```text
SRV-0123
├── implementation session
├── research session
├── verification session
└── review session
```

The system does not force one Item into one ever-growing transcript.

### 6.3 Live Session and history are separate

**ServerOS Agent Session** owns live control semantics:

- discover;
- inspect;
- create;
- resume;
- send;
- result;
- interrupt;
- stop;
- handoff.

A stable ServerOS session ref is not a PID, tmux session, chat thread or native
provider ID.

Provider-native IDs remain locators.

### 6.4 MetaCC is the preferred Session History / Intelligence Provider

ServerOS should not reimplement the mature parts of MetaCC.

MetaCC already provides useful Claude/Codex history semantics including:

- metadata-first session discovery;
- exact session ID targeting;
- provider normalization;
- project/cwd boundaries;
- active/archive distinction;
- parent/subagent lineage;
- compaction/lifecycle awareness;
- error/timeline/work-pattern/token analysis;
- edit sequence analysis.

MetaCC observations do not become live-session authority.

### 6.5 Cross-device continuation

Preferred order:

1. native resume if continuity is real and supported;
2. otherwise checkpoint + Context Capsule + new session;
3. both preserve the same project/Item work identity.

A session must not silently rebind from one Item to another.

### 6.6 Session archival

Raw Claude/Codex transcripts are intended for long-term retention.

Policy:

- Session Intelligence may propose archival;
- archival requires user confirmation;
- raw deletion is a separate destructive/retention action;
- checkpoint and useful memory extraction may happen before archival;
- published/cross-boundary copies must be sanitized appropriately.

Session may end while its work memory survives.

---

## 7. Memory-Context Fabric

Memory is powerful, but it is not the system of record.

### 7.1 Independent Memory Provider

A mature memory engine such as **OpenViking** may run as an independent,
long-lived Provider on the primary Linux/Tokyo node and expose MCP/HTTP
interfaces.

This does not violate the small-core rule because it is a Provider/Managed
Runtime, not part of `serverosd`.

Preferred composition:

```text
ServerOS plugin
├── memory governance Skill
├── memory adapter
└── Memory Provider (OpenViking-like service / MCP)
```

### 7.2 Memory scopes

Memory may cover:

- Personal;
- Role;
- People / Organization;
- Project;
- Item;
- Session / Experience.

### 7.3 Automatic vs governed memory

Low-risk memories may be promoted automatically, for example:

- tool usage experience;
- Item checkpoint;
- known successful local procedure;
- recurring workflow preference.

High-impact durable claims enter reconciliation:

- responsibilities;
- commitments;
- people ownership;
- major project facts;
- meaningful personal preference changes.

### 7.4 Memory retrieval is progressive

Default retrieval should be metadata/abstract-first.

A Context Capsule should contain only a few highly relevant memories. The Agent
can expand when needed.

Memory retrieval must preserve provenance and avoid turning an old inference
into current truth.

---

## 8. Work Fabric

The durable work model is:

```text
Source
→ Snapshot / Observation
→ Claim
→ Candidate
→ Item
→ Activity
→ Run
→ Evidence
→ Deliverable
```

`work-engine` remains the durable Item/Activity/Run control plane. ServerOS
does not create a second workflow database.

### 8.1 One Personal Board

The default work surface is one personal cross-project board.

Projects are filters/relationships, not separate truth islands.

### 8.2 Candidate before Item

Source signals, incidents, ideas and understanding changes create Candidates.

Only human selection promotes a Candidate into a durable Item.

This preserves board granularity.

### 8.3 Board granularity

The board should resemble meaningful work in a real work document:

- "LiteLLM long-term log storage";
- "upgrade LiteLLM";
- "ServerOS Session Management";
- "Support Bot".

It should not become:

- "edit function X";
- "restart container Y";
- "change config line Z".

Those belong to Activities/Runs.

### 8.4 Today

After reconciliation, ServerOS may suggest priorities.

The user selects today's work.

That selection is durable enough to drive background continuation but remains
human-owned.

---

## 9. Idea / Requirement Discovery

A first-class Ability exists to **elicit the user's actual intent** before
design on ambiguous/greenfield work.

Useful operations include:

- clarify;
- probe;
- surface assumptions;
- surface trade-offs;
- find missing cases;
- challenge contradictions;
- summarize;
- confirm.

Completion is semantic: enough product ambiguity has converged to enter design.

The long-term default is:

```text
CC elicits / drafts requirement
       ↓
GPT reviews
       ↓
human handles meaningful remaining decisions
```

The purpose is to reduce low-value human participation, not remove human
judgment from important decisions.

---

## 10. Research, evidence and decisions

Project decisions must use local truth, not web research alone.

A research packet may combine:

- local repositories;
- runtime observations;
- test/experiment evidence;
- Feishu raw information;
- Structured Understanding;
- existing decisions;
- external research.

Evidence acquisition preserves provenance and distinguishes discovery hints from
literal evidence.

Meaningful product/technology/architecture choices create lightweight Decision
Records by default:

```text
question
context
options
chosen
why
consequences
evidence
supersedes?
```

### 10.1 ChatGPT

ChatGPT is a first-class reasoning/review participant for:

- requirement clarification;
- product discovery;
- research synthesis;
- technology selection;
- architecture;
- trade-offs;
- evidence/deliverable review.

Near term, GitHub documents/Decision Packets are the durable bridge.

ServerOS prepares clean Context/Decision Packets instead of forcing GPT to
re-read raw work history.

---

## 11. Execution and Quay

After requirement/design acceptance, Claude Code/Codex may autonomously split
the Item into Activities.

Planning is an execution tool, not a mandatory human approval ceremony.

Human re-entry is required when execution discovers:

- a changed goal;
- changed acceptance;
- a new strategic/architecture decision;
- a permission boundary;
- production/destructive/high-risk scope.

### 11.1 Quay

Quay is a bounded delegation Provider.

High-value use cases:

- reproduction;
- independent verification;
- test matrices;
- parallel research;
- bounded implementation slices.

ServerOS/CC may recommend Quay automatically.

Current policy: the user decides whether a proposed slice is delegated.

Quay Task done != Item done.

Quay's native Goal/Task/result records remain Quay authority; ServerOS stores
the mapping back to parent Item/Activity/Run and evidence.

Daily reporting should show real Quay contribution, not decorative usage.

---

## 12. Verification, E2E and Evidence

Verification is a core infrastructure capability.

An Item is complete when:

```text
its acceptance is satisfied
+ sufficient verification exists
+ evidence is durable
+ deliverable exists
+ final report exists
```

None of these alone imply Item completion:

- code written;
- command exit 0;
- unit tests green;
- PR opened/merged;
- Quay task done.

### 12.1 Minimum sufficient validation

Validation scales with change/risk.

```text
read/reason
  → source/version/permission checks

tiny docs/config
  → diff + targeted structure/link checks

small local behavior
  → nearest unit/static/type regression

cross-module
  → affected integration path

contract/release
  → contract + integration + required wider regression

high-risk/live
  → fresh target/owner/approval/recovery + postcondition
```

Do not run the whole world merely because tests exist.

### 12.2 E2E

E2E may orchestrate Providers such as:

- Browser;
- containers;
- test environment;
- Feishu TEST;
- GitHub;
- Windows/RDC;
- Tailscale;
- Quay;
- serverosd.

Evidence can include:

- revision;
- commands/actions;
- test result;
- E2E result;
- screenshots/readback;
- bounded logs;
- deployment state;
- acceptance mapping.

---

## 13. Reporting

Reports are projections of durable work/evidence, not session-memory summaries.

Daily Report should be understandable by someone who did not watch the terminal.

Typical structure:

- outcomes;
- Item progress/completion;
- decisions;
- verification/E2E;
- Quay contribution;
- blockers;
- next suggested work.

The default format is public-quality/professional even when the primary audience
is the user.

---

## 14. Workspace and placement

Workspace Management exists primarily for **placement and isolation**.

It answers:

> Where should this object go, and how can parallel Agents work without
> damaging each other?

Priority:

1. placement resolver;
2. project/repo resolution;
3. worktree isolation;
4. writer/resource leases;
5. test environment;
6. evidence/artifact path;
7. branch lifecycle;
8. commit/push/PR convenience;
9. cleanup.

### 14.1 Code worktrees

Code-changing Items default to dedicated worktrees.

Research/docs/support do not require worktrees unless isolation is useful.

### 14.2 Parallel isolation

Parallel Agent safety is more than Git worktrees:

```text
worktree
+ writer lease
+ resource lease
+ checkpoint/event
```

Resource leases may cover:

- ports;
- containers;
- test databases;
- browser profiles;
- shared test credentials;
- TEST Feishu bot;
- special devices.

### 14.3 Placement

Agents should not invent output paths.

Placement semantics cover:

- source;
- worktrees;
- test definitions;
- artifacts;
- durable documents;
- temporary files;
- cache;
- runtime state;
- downloads.

Missing roots resolve to explicit UNBOUND, never to a guessed fallback.

---

## 15. Project and repository model

Project != repository.

A project may span many repos and one repo may serve several work contexts.

Repository roles:

```text
owned-canonical
bridge-writable
upstream-readonly
publication
tool
```

### 15.1 Bridge repository pattern

When the user needs to develop against an upstream/enterprise repository that
is not their writable authority, prefer an owned Bridge repository.

```text
upstream / enterprise repo
  role: upstream-readonly
        ↓ explicit tracked revision
ProfessorZhi/<project>-bridge
  role: bridge-writable
        ↓
Item worktrees / experiments / commits / evidence / GPT review
```

Existing examples include `litellm-bridge` and `muc-bridge`.

Bridge rules:

- preserve upstream provenance;
- do not claim upstream authority;
- do not push to foreign/protected upstream by default;
- delivery back upstream is an explicit PR/export/promotion step;
- GitHub makes the writable engineering state visible to ChatGPT and other
  Agents.

---

## 16. Ability Fabric

External semantic model:

```text
Ability        WHAT
Operation      semantic verb
Provider       HOW
Binding        concrete provider instance on a device/environment
Attachment     current consumer relationship
Revision       pinned managed artifact version
Profile        declarative configuration
Adapter        how an Agent runtime consumes the Provider
```

Resolution chain:

```text
Ability#Operation
      ↓
Provider
      ↓
Binding(device, instance)
      ↓
Attachment(Agent / Session / Item / scope / lease)
```

### 16.1 Three distinct axes

Never collapse:

```text
Support        SUPPORTED / UNSUPPORTED / UNKNOWN
Readiness      READY / DEGRADED / NOT_READY / UNKNOWN
Authorization  ALLOW / ASK / DENY / UNKNOWN
```

Installation is one input, not the answer.

### 16.2 Native-first discovery

ServerOS strengthens Claude/Codex native discovery rather than replacing it.

The bootstrap must expose a small read path such as:

- `context.current`;
- `ability.search`;
- `ability.inspect`;
- `ability.resolve`.

Normal search returns a small top-k set.

### 16.3 Capability acquisition

If local abilities do not solve a need:

```text
local search
→ native tools/providers
→ remote discovery (GitHub / registries / web)
→ provenance / compatibility / risk evaluation
→ temporary attach/install
→ repeated value may become Candidate Ability
```

The mechanism is builtin. A `find-capability` Skill may teach the procedure.

Executable MCP/plugin/hook acquisition is more restrictive than text-only Skill
discovery.

### 16.4 Physical vs logical topology

Ability payload paths stay physically flat.

Logical navigation may group:

```text
Domain → optional Subdomain → Ability
```

The logical tree is navigation, not package ownership.

---

## 17. Dynamic Environment and resource lifecycle

Environment capabilities are resources, not static documentation.

Typical flow:

```text
Session/Item needs Browser/MCP/TestEnv/Tunnel/Device
        ↓
Desired reference
        ↓
Provider/Binding resolution
        ↓
Admission
        ↓
Attachment / lease / refcount
        ↓
READY
        ↓
consumer ends
        ↓
release according to scope/policy
```

Scopes may include:

- turn;
- session;
- Item;
- user;
- persistent.

A resource required by two consumers must not disappear when only one consumer
ends.

Dynamic readiness is checked on demand, not globally on every Agent turn.

---

## 18. Connectivity and devices

Connectivity is Provider territory, not identity authority.

Preferred layers:

```text
Tailscale       private device-to-device reachability
A2A             Agent-to-Agent semantic delegation/communication
RDC             Agent-to-device GUI/files/terminal, on demand
GitHub          durable asynchronous collaboration
SSH/tmux        raw transport / fallback
```

### 18.1 Tailscale

Tailscale is the preferred personal private-network Provider.

It is optional: ServerOS must continue functioning without it.

Useful roles:

- private device reachability;
- CloudCLI access;
- private services;
- optional SSH reachability;
- future A2A transport.

On Tokyo, prefer userspace/isolated deployment where feasible. Kernel routing,
subnet-router, exit-node, firewall and similar host mutations require explicit
network admission.

Tailscale Serve is preferred over public exposure for private UI/services.

Funnel is default-deny.

### 18.2 SSH

SSH is directional per connection.

Bidirectional device access requires:

- both sides exposing reachable SSH services, or
- a reverse tunnel.

For the intended topology, Tailscale reachability + explicitly enabled SSH is
cleaner than long-lived reverse tunnels.

### 18.3 RDC

Remote Desktop / Desktop Commander is a device-control Provider.

It is attached on demand for:

- Windows-specific workflows;
- GUI interaction;
- local files/apps;
- user assistance.

It is not Agent-to-Agent communication.

### 18.4 A2A

A2A is a future structured Agent-to-Agent adapter.

Primary use should be task delegation/handoff; free-form Agent chatter is
secondary because it can waste tokens without improving work.

### 18.5 Linux primary, Windows specialist

Linux/Tokyo is the primary development/control node.

Windows is an on-demand specialist device for Windows-specific builds/tests,
desktop software and workflows requiring local GUI/configuration.

Windows does not need a duplicate global ServerOS authority.

---

## 19. Runtime configuration

Runtime configuration is per-device/per-runtime state, not blind cross-device
synchronization.

Current pattern can legitimately differ:

- Windows Codex / WorkBuddy may depend on CC Switch;
- Windows Claude Code may use its own settings;
- Tokyo Claude/Codex use Tokyo-specific providers/network paths.

Runtime Configuration Management models:

- desired configuration;
- observed configuration;
- shared preference;
- device-specific override;
- diff;
- plan;
- reconcile;
- verify.

Cross-device work handoff synchronizes project/Item/checkpoint/intent, not
necessarily provider/proxy/credential bytes.

---

## 20. Human interfaces

### 20.1 CloudCLI is the primary rich console

CloudCLI already provides valuable UI primitives:

- Claude/Codex chat/session surfaces;
- terminal;
- files;
- Git;
- browser;
- Web/mobile access;
- plugin capability.

ServerOS should integrate rather than fork/reimplement those surfaces where
possible.

A ServerOS CloudCLI plugin should expose:

- Today / Personal Board;
- Understanding / Reconciliation;
- Sessions;
- Decisions / approvals;
- Memory;
- Quay;
- E2E evidence;
- Environment / health;
- Reports.

CloudCLI native shell/Git/files may perform operations outside ServerOS semantic
APIs; the product must not falsely claim total interception.

### 20.2 Feishu is a lightweight human-control and information surface

Feishu is primarily:

- source capture;
- notifications;
- reconciliation/decision reminders;
- alerts/recovery;
- reports;
- lightweight mobile actions.

A full remote terminal inside Feishu is not required by the core product.

### 20.3 tmux

tmux remains a useful persistent raw-shell fallback.

It is not Session identity authority and does not need to wrap every Agent
session.

---

## 21. Node Runtime — serverosd

There is exactly **one resident ServerOS core daemon by default: `serverosd`**.

Provider/Managed Runtime processes may also be long-lived outside the core.

`serverosd` stays small and mechanical.

Closed responsibilities:

```text
Device / Host identity references
Host/User resource observation
filesystem/quota/inode/shm facts
process/runtime/container observations
Managed Runtime Registry
ownership / attribution graph
Probe Engine
desired runtime/environment records
runtime anomaly/event facts
Policy / Admission
bounded Reconciler
Observation / Provenance / Freshness
Local IPC
core runtime references / leases needed for lifecycle
essential self-health
```

It does not contain:

- Feishu business logic;
- GitHub business workflow;
- Quay planning;
- LLM planning;
- semantic memory generation;
- Work Item planning;
- report generation;
- generic process supervision;
- generic container orchestration;
- arbitrary remote shell.

The control loop is mechanical:

```text
Inventory
  → Probe
  → Attribution
  → Policy / Admission
  → bounded Reconcile
  → Observation / Event
  → Local IPC
```

No LLM planner runs inside the daemon.

### 21.1 Self-healing

Repair tiers:

```text
T0 deterministic owned repair
   explicit ServerOS-owned disposable resource
   → bounded auto repair + verify + notify

T1 Agent repair
   diagnosis required, within approved scope
   → Maintenance Session + verify + report

T2 delegated exception
   exact approved shared target (for example shared gateway)
   → exact delegated principal/scope

T3 shared/foreign/network/production/destructive
   → no autonomous mutation without explicit policy/approval
```

Hooks are Agent lifecycle integration; infrastructure anomalies are Events.
Do not misuse Agent hooks as the monitoring/event bus.

---

## 22. Core composition and component boundaries

The product composition Core has exactly two canonical members:

```text
Core
├── serveros
└── ability-management
```

This does not mean every useful capability is in Core.

Component ownership:

| Component | Owns | Does not own |
|---|---|---|
| `serveros` | product architecture, composition, bootstrap, deterministic Context/Space/Relations kernels, node runtime | reusable Ability source, Item state, cloud content |
| `ability-management` | Ability identity, Revision, compatibility, discovery, installation/distribution | product composition, Item lifecycle |
| `work-engine` | Item/Activity/Run durable state, board/work projections, execution envelope facts | Skills, topology, cloud bodies |
| `hwz-ops` | Tokyo instance policy/topology/bindings | generic product semantics |
| `hwz-records` | work/information/understanding records | machine runtime authority |
| Workspace capability | placement/isolation/reconcile | Ability registry |
| Feishu capability | cloud placement/lifecycle | semantic truth |
| MetaCC | session history/intelligence | live session authority |
| Memory Provider | memory extraction/retrieval | approved understanding authority |
| CloudCLI | UI / Agent console | ServerOS semantic authority |

Do not create a second source of truth to make integration convenient.

---

## 23. Four internal planes

The earlier four-plane model remains valid, but it is now an **internal
architecture decomposition**, not the top-level product story.

```text
Node Runtime Plane
  what is running here, who owns it, is it healthy?

Ability Fabric
  what can this device do, through which provider/operation?

Memory-Context Fabric
  what does the work remember, and what context should be loaded now?

Coordination Fabric
  how do devices/sessions/handoffs/delegations relate?
```

These planes divide questions, not directories or daemons.

No new plane is added merely for taxonomy neatness.

---

## 24. Storage and authority

Storage roles remain explicit.

Typical user-space topology:

```text
workspace/
  src/
  worktrees/
  test-envs/
  ops/
  knowledge/
  records/

state/
  serveros/
  ability-management/
  browser/

runtime-assets/
  ServerOS/plugin revisions
  Ability artifacts

artifacts/
  work-items/
  skills/
  ops/
  repro/
  serveros-releases/

caches/
data/
downloads/
envs/
logs/
software/
toolchains/
tmp/
```

Not all state is disposable.

Durable examples include:

- authorization/work state;
- human decisions;
- cursors;
- delivery receipts;
- ownership/reference state;
- Item/Run facts.

Observed caches may be rebuildable.

Provider-native sensitive state such as `~/.claude`, `~/.codex`, browser
profiles and secret stores remain provider-native.

IPC should use an OS runtime directory such as `$XDG_RUNTIME_DIR/serveros`
rather than durable storage.

---

## 25. Build, Release, Deploy and Rollback

Release correctness is artifact/revision centric:

```text
Source Revision
      ↓ build
Release Artifact + Manifest + Digest
      ↓ install
Installed Revision
      ↓ start
Observed Live Revision
```

Build PASS != deploy PASS != LIVE PASS.

Generic build/release capability must exist independently of any project-
specific image-build recipe.

A specialist `litellm-image-build` ability may preserve LiteLLM-specific
knowledge, but ServerOS must still be able to build ordinary Docker/project
artifacts without it.

Inside an approved envelope, automation may perform:

- build;
- package;
- isolated install;
- TEST deploy;
- E2E;
- rollback testing.

Production promotion remains human-approved.

---

## 26. Incidents and autonomous background work

### 26.1 Incident is not automatically Item

A short incident may be:

```text
detect
→ deterministic repair
→ verify
→ Incident record
→ report
```

No Item is required.

If the issue needs durable engineering/follow-up:

```text
Incident
→ Candidate
→ Personal Board
→ human selection
→ Item
```

### 26.2 Background autonomy

Approved Items may continue in the background/night inside their Execution
Envelope:

- research;
- implementation;
- tests/E2E;
- Quay;
- build;
- understanding proposals;
- report preparation.

Stop/escalate on:

- decision gate;
- blocker;
- no progress;
- budget/retry guard;
- production/destructive/high-risk boundary.

Important work does not need an artificially tiny monetary budget, but it must
have runaway controls such as maximum retries, no-progress rounds and delegation
depth.

---

## 27. Scheduled operating recipes

No `workflowd` exists.

Finite OS-scheduled recipes drive recurring work.

### Perception / Understanding Loop

```text
authorized source delta
→ snapshot/observation
→ relevance/privacy filter
→ understanding proposal
→ reconciliation queue
→ Candidate Board refresh
```

No-change skips unnecessary LLM work.

### Work Advancement Loop

```text
Today / approved Items
→ current checkpoint
→ next approved Activity
→ direct Agent or approved Quay
→ targeted verification
→ evidence/checkpoint
→ continue or stop
```

### Reporting Loop

```text
durable work facts
+ evidence
+ decisions
+ Quay ledger
→ Daily / Weekly / Decision report
→ publication/delivery receipt
```

Use OS schedulers such as systemd user timers; do not create another resident
business scheduler.

---

## 28. Ability navigation view

The product can be reasoned about in two broad families.

This is navigation, not physical nesting and not a daemon list.

```text
A · Agent Working Infrastructure
├── Compliance & Safety
├── Runtime / Session
├── Context & Memory
├── Capability Fabric
├── Workspace / Isolation
├── Connectivity / Device / UI
├── Verification / E2E / Release
└── Node Runtime / Operations

B · Information → Delivery
├── Source Capture / Follow
├── Structured Understanding / Reconciliation
├── Work Discovery / Personal Board
├── Idea / Requirement Discovery
├── Requirements Engineering
├── Research / Evidence Acquisition
├── Decision / Architecture
├── Execution
├── Incident / Operations
├── Knowledge Stewardship
├── Remote User Assistance
└── Reporting / Publication
```

Do not mechanically create one Skill/repository/daemon for every box.

Several boxes are recipes over common primitives.

---

## 29. Claude Code / Codex parity

One world, two runtime adapters.

They should share:

- project/Item identity;
- workspace semantics;
- Context Capsule semantics;
- Memory scopes;
- Ability identities;
- evidence/artifact contract;
- safety/admission semantics.

They may differ in:

- native plugin packaging;
- hot-attach capability;
- native session APIs;
- configuration format;
- provider-specific features.

Runtime differences belong in adapters/bindings, not duplicated business logic.

---

## 30. Non-goals

ServerOS does not become:

- a kernel/OS scheduler;
- a generic process supervisor;
- a generic workflow platform;
- a second Claude/Codex planner;
- a universal MCP registry;
- a generic secret store;
- a generic remote shell;
- a full container orchestrator;
- a replacement for GitHub;
- a replacement for Feishu;
- a replacement for CloudCLI;
- a replacement for MetaCC;
- a replacement for a memory engine;
- an excuse to duplicate provider-native functionality.

It also does not:

- preload every capability/history into every session;
- automatically delete cloud/local history to make maps clean;
- manufacture documentation to satisfy a schema;
- treat unsupported/unverified behavior as LIVE;
- let a green test replace acceptance/evidence;
- let a Quay task close a parent Item;
- let an unknown owner become a cleanup target;
- let a bridge repository silently become upstream authority.

---

## 31. Architecture change test

Before adding new core machinery, answer all of:

1. Which real user/work failure does this fix?
2. Why cannot an existing Provider/Ability/recipe solve it?
3. Does it improve Environment, Context, Memory, Capability, continuity or
   safety?
4. Does it keep the default Agent path fast?
5. What is the authority/source of truth?
6. How is it removed or degraded when unavailable?

If those answers are weak, the machinery does not belong in ServerOS.

---

## 32. Current design status

The product shape in this document is now the architecture baseline.

Large remaining design work is subsystem-detail work, especially:

1. exact Session logical schema and Coordinator lifecycle;
2. MetaCC-to-ServerOS history/intelligence adapter;
3. OpenViking deployment/binding/backup contract;
4. Feishu inventory/blacklist/Follow policy;
5. CloudCLI ServerOS plugin surfaces;
6. Tokyo Tailscale userspace deployment and network admission;
7. A2A adapter timing/mapping;
8. dynamic environment Attachment/lease/refcount behavior;
9. E2E evidence schema and provider adapters.

Those details may refine contracts. They do not reopen the product definition.

The implementation must continue to distinguish:

```text
ARCHITECTURE
CODE
INTEGRATION
LIVE
```

A design statement is not implementation evidence, and implementation is not
LIVE proof.
