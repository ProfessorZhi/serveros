# Product discovery decisions — 2026-09-21

> Status: **working decision ledger, not a second architecture authority**.
>
> This file records product decisions reached during the 2026-09-20/21 discovery
> sessions. It exists so the next rewrite of the root `ARCHITECTURE.md` is driven
> by the user's real daily work rather than by the current implementation shape.
> Once reviewed, these decisions should be folded into `ARCHITECTURE.md` and this
> ledger can become historical.
>
> Do **not** treat a sentence here as proof that the implementation already has
> the corresponding capability.

## 1. Product north star

ServerOS is a **personal Agent Work OS** for one user, not a multi-tenant agent
platform.

The primary promise is:

> Open a fresh Claude Code or Codex session, mention any work topic, and within
> seconds the Agent can identify the relevant project / Item / prior context,
> retrieve the right memory, discover the right capability and environment, and
> continue work without the user re-explaining yesterday.

The first screen of the product must communicate **both** the daily work
experience and the architectural philosophy that makes that experience fast.

The core design philosophy remains:

- **Environment** — prepare the right place and resources for this work.
- **Context** — provide the smallest correct working context for the current
  intent.
- **Capabilities** — make the right Ability / Provider discoverable and usable
  when needed.
- **Progressive disclosure** — do not preload the world. Start thin, then expand
  context, memory, provider details, Skills and schemas only on demand.

The highest-value automatic outcomes are therefore **right context, right
memory, right capability and right environment at the right time**.

The system must strengthen Claude Code / Codex rather than become a ceremony
around them. A large installed system must still expose a **small active
context**. The daily operating loop in §3 is the user experience built on top
of these principles; it does not replace them.

## 2. Layer 0 — compliance and non-interference

Compliance is prior to every other capability.

On shared infrastructure the default principal is `vince`. Foreign or
unknown-owned resources are not mutable merely because they are visible.

Current explicit delegated exception:

- the shared AI gateway is system-owned under `ubuntu`;
- Vince is explicitly allowed to maintain that exact gateway;
- this delegation is **target-scoped**, not a general permission to operate as
  `ubuntu`.

Network changes are high-risk on the shared Tokyo host. Any Docker/Tailscale/VPN/
route/listener/subnet mutation must pass a network preflight and netshadow-style
conflict check. Unknown/incomplete network policy fails closed.

ServerOS must never turn "I can see it" into "I own it".

## 3. Daily operating loop

The desired daily loop is first-class product behavior:

```text
Feishu / Docs / Wiki / Minutes / GitHub / sessions
                    ↓
           incremental source capture
                    ↓
        relevance / privacy / blacklist
                    ↓
       provisional structured understanding
                    ↓
           Understanding Delta
                    ↓
     mandatory human reconciliation until aligned
                    ↓
      approved Understanding Checkpoint
                    ↓
           Candidate Work Board
                    ↓
       ServerOS priority suggestions
                    ↓
          human selects Today
                    ↓
              Candidate → Item
                    ↓
 idea / requirement discovery → requirement draft
                    ↓
            GPT review / decision
                    ↓
        research / design / decision record
                    ↓
        Agent execution + optional Quay
                    ↓
          verification / E2E evidence
                    ↓
             Item report + Daily Report
```

The reconciliation gate is mandatory for important understanding changes, but
unrelated already-approved Items may continue autonomously.

## 4. Intent-first session start

A new session starts nearly empty. It does **not** bind to the most recent Item
just because that Item was recent.

Flow:

```text
session starts
  → thin bootstrap
  → user says what they want
  → intent resolution
  → identify general discussion / project / existing Item / new investigation
  → load an 800–1500 token working capsule
  → expand context on demand
```

Read-only context expansion is automatic by default.

A session may begin as general and become project- or Item-bound later.

## 5. Session is a first-class resource

One Item may have multiple sessions. ServerOS does not force "one Item = one
chat".

Logical session roles:

- **Coordinator Session** — personal reconciliation, board, project switching,
  routing into work.
- **Execution Session** — Item-bound implementation/research/test work.
- **Maintenance Session** — incident diagnosis and repair.

Cross-device continuation policy:

1. prefer native provider resume when continuity is real;
2. otherwise create a new session with checkpoint + Context Capsule;
3. both are valid forms of handoff.

### 5.1 Live Session vs history/intelligence

Do not rebuild MetaCC.

**ServerOS Agent Session** owns live control semantics:

- discover
- create
- resume
- send
- result
- interrupt
- stop
- handoff
- stable logical session reference

**MetaCC** is the preferred Session History / Session Intelligence Provider.
Its existing model already provides:

- Claude + Codex history normalization;
- metadata-first session discovery;
- exact session-id lookup;
- project/cwd boundary enforcement;
- active vs archived filtering;
- parent/subagent lineage;
- compaction/lifecycle awareness;
- timeline, errors, work patterns, token usage, edit sequences and quality
  analysis.

ServerOS should adapt these capabilities instead of writing another transcript
parser.

MetaCC history facts are observations, not live-session authority.

### 5.2 Archival policy

Raw Claude/Codex transcripts are intended to be retained long-term.

- no automatic session archival without user confirmation;
- Session Intelligence may propose archival candidates;
- metadata/checkpoints/memory may be generated automatically;
- raw deletion is a separate retention/destructive decision;
- transcripts must remain private and secret-scanned/sanitized where they are
  published or copied.

## 6. Memory as an independent provider

Memory may run as an independent long-lived service on the Linux/Tokyo primary
node and be consumed through MCP.

Preferred shape:

```text
ServerOS plugin
  ├── Memory governance Skill
  ├── Memory adapter
  └── OpenViking-like Memory Provider (independent service / MCP)
```

Memory Provider responsibilities:

- extraction
- retrieval
- association
- experience/tool memory
- hierarchical context
- session-derived memory

ServerOS responsibilities:

- scope
- provenance
- promotion
- approval
- conflict/supersession
- deciding what becomes trusted durable understanding

Memory is **not** truth authority.

Low-risk operational experience and Item checkpoints may be written
automatically. Role, people, commitments, durable project facts and major
preference changes enter reconciliation.

## 7. Archive / Understanding / Knowledge / Memory are different

```text
Archive        = what happened
Understanding  = what we currently believe the world is
Knowledge      = what we curate for future reuse
Memory         = how Agents quickly recall the above plus prior experience
Work           = what we choose to do next
```

Knowledge promotion may be automated to **draft** when repeated evidence
supports it; human review can promote the draft.

## 8. Feishu information model

Default behavior:

- shallow-archive accessible chats unless the source is on an approved
  blacklist;
- stable chat/app IDs, not names, become blacklist authority;
- support screenshots are part of shallow archive where useful;
- large/non-image attachments and deep link bodies are deferred unless needed;
- shared documents automatically enter a Follow list and may be snapshotted
  locally;
- followed Wiki/knowledge sources are watched incrementally rather than fully
  recopied every cycle;
- useful Meeting/Minutes content may generate a derived note in the appropriate
  cloud space while the original object stays canonical.

The real Feishu surfaces remain:

- My Docs
- Drive
- Wiki / Knowledge Base

ServerOS may classify and place content across them, but does not invent a
second fake three-space model.

Structured Understanding is project-first, with people, responsibilities,
commitments and topics attached to projects.

## 9. Work discovery and requirement formation

There is one personal cross-project board.

Sources produce **Candidates**, never automatic Items.

```text
understanding / incident / support / research signal
  → Candidate
  → ServerOS suggests priority
  → human selects work
  → Item
```

The board should stay coarse enough to resemble the real LiteLLM work document;
small implementation details belong below an Item as Activities, not as dozens
of board cards.

Even incident follow-up that needs durable engineering work must first surface
as a Candidate and be selected before becoming a long-lived Item.

## 10. Idea / Requirement Discovery is a first-class Ability

Before requirement/design on ambiguous or greenfield work, an Agent should
actively elicit the user's intent:

- clarify goals;
- probe assumptions;
- expose trade-offs;
- find missing cases;
- challenge contradictions;
- summarize and confirm.

Completion is semantic, not "asked N questions": the important ambiguity has
converged enough to enter design.

Long-term target:

```text
CC drafts requirement
  → GPT reviews
  → human only handles meaningful ambiguity/decision
```

Human involvement should decrease as the system becomes trustworthy.

## 11. Research and decisions

Research must combine:

- local repositories;
- runtime/test evidence;
- Feishu/raw information;
- approved Structured Understanding;
- external sources.

External research alone is insufficient for project decisions.

Evidence acquisition should preserve provenance and distinguish searchable hints
from literal evidence.

Meaningful technology/product/architecture selections create a lightweight
Decision Record by default.

## 12. ChatGPT role

ChatGPT is a high-level reasoning/review participant, especially for:

- requirement clarification;
- product exploration;
- research synthesis;
- technology selection;
- architecture and trade-offs;
- evidence/deliverable review.

Near-term durable bridge: GitHub documents / decision packets.

ServerOS should produce clean context packets rather than requiring ChatGPT to
re-read raw Feishu history.

## 13. Execution and Quay

After requirement/design acceptance, the Agent may autonomously decompose work
into Activities.

Quay should be used aggressively where it creates real value, especially:

- independent verification;
- reproduction;
- test matrices;
- parallel research;
- bounded implementation slices.

Delegation is suggested automatically but currently requires the user's choice.
Do not create meaningless Quay work just to increase usage metrics.

## 14. Verification and E2E evidence

Item completion is:

```text
acceptance satisfied
+ verification evidence
+ deliverable
+ final report
```

It is not synonymous with "code written", "unit tests green" or "PR merged".

E2E is a core infrastructure capability. It may orchestrate Browser, containers,
test environments, Feishu TEST, GitHub, Windows/RDC, Tailscale, Quay and
serverosd.

Every completed Item should produce a concise human report with strong evidence
and drill-down links.

## 15. Workspace: placement and isolation first

Workspace Management exists primarily to answer:

> where should this object go, and how do parallel Agents avoid damaging each
> other?

Priority order:

1. placement resolver
2. project/repo resolution
3. Item worktree isolation
4. writer lease + resource lease
5. test environment
6. evidence/artifact path
7. branch lifecycle
8. commit/push/PR convenience
9. cleanup

Code changes default to a dedicated Item worktree. Research/docs/support do not
require one unless needed.

Parallel isolation = worktree + writer lease + resource lease + checkpoint/event.

## 16. Capability discovery and acquisition

Ability search is built into the ServerOS bootstrap surface; a Skill is not
required in order to discover Skills.

When no local ability matches:

```text
local ability search
  → native tool/provider fallback
  → remote capability discovery (GitHub / registries / web)
  → provenance/compatibility/risk evaluation
  → temporary attach or install
  → repeated usefulness may become a Candidate Ability
```

A friendly `find-capability` Skill may teach the procedure, but the discovery
mechanism itself is bootstrap functionality.

## 17. Runtime configuration is per-device, not global sync

Real current pattern:

- Windows Codex / WorkBuddy may depend on CC Switch;
- Claude Code is configured directly through its own settings;
- Tokyo/Linux has its own Claude/Codex provider and network reality.

Therefore Runtime Configuration Management must model:

- desired config;
- observed config;
- shared preferences;
- device-specific overrides;
- diff / plan / reconcile / verify.

It must **not** blindly mirror a Windows provider/proxy/credential configuration
onto Linux.

Cross-device collaboration synchronizes work identity, checkpoint and intent,
not necessarily provider configuration.

## 18. Connectivity and device access

Preferred layers:

```text
Tailscale       private device-to-device reachability
A2A             Agent-to-Agent semantic delegation/communication
RDC             Agent-to-device GUI/files/terminal, on demand
GitHub          durable asynchronous collaboration
SSH/tmux        raw transport / fallback
```

Tailscale is the preferred personal private network but not a ServerOS hard
dependency.

On the shared Tokyo host, kernel-level network mutation is high-risk; prefer a
userspace/isolated deployment mode unless an explicit network change is
approved.

SSH is directional per connection. True bidirectional access requires both
devices to expose reachable SSH services, or a reverse tunnel. For the intended
design, Tailscale + an explicitly enabled SSH service on the device is cleaner
than long-lived reverse tunnels.

## 19. Human interfaces

Primary rich console: **CloudCLI**.

It should eventually surface ServerOS-specific views through a plugin:

- Today / Board
- Understanding / Reconciliation
- Sessions
- Decisions / approvals
- Memory
- Quay
- E2E evidence
- Environment / health
- reports

Feishu becomes primarily:

- notification;
- daily report;
- alert/recovery;
- reconciliation/decision reminder;
- lightweight mobile interaction.

A full Feishu remote-terminal experience is not required for the first product
shape.

tmux remains a raw-shell fallback for sessions that must outlive UI/PTY
disconnect behavior; it is not Session identity authority.

## 20. serverosd stays small

`serverosd` remains mechanical:

- observe;
- attribute;
- admit;
- reconcile simple explicitly-owned desired resources;
- emit events.

It does not run an LLM planner.

Repair policy:

- deterministic owned disposable resource → bounded automatic repair;
- diagnosis required → create/resume Maintenance Session;
- shared/foreign/network/production/destructive boundary → explicit policy/gate.

Repair then verifies and notifies.

## 21. Incidents and board granularity

A short incident that is deterministically repaired may remain an Incident
record and does not need an Item.

A repeated or durable follow-up problem becomes a Candidate. It becomes an Item
only after board selection.

This keeps the board at requirement/work-package granularity instead of
polluting it with every alert.

## 22. Background autonomy and budgets

Approved Items may continue overnight inside their Execution Envelope:

- research
- implementation
- tests/E2E
- Quay
- build
- understanding proposals
- report preparation

Production promotion, high-risk network change, destructive operations and
similar gates remain human-approved.

Cost policy: no low hard budget for important work, but mandatory runaway
guards such as retry/no-progress/delegation-depth bounds.

## 23. Proposed capability topology

This is a navigation view, not a physical directory layout or daemon list.

```text
A · Agent Working Infrastructure
├── A0 Compliance & Safety
├── A1 Runtime / Session
│   ├── live Agent Session
│   ├── MetaCC history/intelligence
│   └── runtime configuration
├── A2 Context & Memory
├── A3 Capability Fabric
├── A4 Workspace / Isolation
├── A5 Connectivity / Device / UI
├── A6 Verification / E2E / Release
└── A7 Node Runtime / Operations

B · Information → Delivery
├── B0 Source Capture / Follow
├── B1 Structured Understanding / Reconciliation
├── B2 Work Discovery / Personal Board
├── B3 Idea / Requirement Discovery
├── B4 Requirements Engineering
├── B5 Research / Evidence Acquisition
├── B6 Decision / Architecture
├── B7 Execution
├── B8 Incident / Operations
├── B9 Knowledge Stewardship
├── B10 Remote User Assistance
└── B11 Reporting / Publication
```

Do **not** turn every box above into a daemon, repository or Skill. Several are
recipes over shared primitives.

## 24. Real-work coverage used in this discovery

The decisions above were checked against real work surfaces rather than only
the current ServerOS implementation:

- LiteLLM observability, core development, production/model operations and user
  support;
- Tokyo shared-host governance and network-safety incidents;
- Windows CC Switch / SSH bridge / remote provider configuration;
- Windows OpenClaw launcher build/data isolation;
- MarketOps research/evidence pipeline;
- Support Bot and TroubleBuddy knowledge lifecycle;
- Feishu raw archive and Structured Understanding;
- Claude/Codex session archive and MetaCC history analysis;
- ServerOS itself.

## 25. Still to resolve before rewriting the canonical architecture

The large product shape is now stable enough to move into detailed design.
Remaining questions are implementation-level:

1. exact OpenViking deployment/binding and backup contract;
2. exact CloudCLI ServerOS plugin surface and Session control boundary;
3. Tokyo Tailscale userspace deployment and compliance preflight;
4. Feishu source inventory + initial blacklist;
5. A2A adapter timing and protocol mapping;
6. exact MetaCC-to-ServerOS Session History adapter schema.

After these are reviewed, fold this ledger into the root `ARCHITECTURE.md`
and rewrite the root `README.md` around the daily operating loop and the
intent-first Agent experience.