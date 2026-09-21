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

A working Context Capsule should answer, compactly:

```text
what am I working on?
what is the current goal?
where did prior work stop?
which approved facts/decisions constrain this work?
which environment/target am I operating in?
is a human decision currently pending?
where are the important sources/evidence?
```

It should not expose internal schema merely for completeness.

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
- research vs prototype vs implementation;
- test-first vs another local method;
- which native Skill/MCP/tool to use;
- whether to create subagents;
- decomposition and implementation strategy.

ServerOS must not force ceremony that makes the Agent worse.

### 1.5 ServerOS is constraint-driven, not workflow-driven

The business world is fluid. The same Item may move naturally through:

```text
conversation
→ research
→ experiment
→ implementation
→ test
→ more research
→ redesign
→ documentation
→ operational follow-up
```

or skip most of those entirely.

ServerOS therefore does **not** encode a mandatory global lifecycle such as
`RESEARCH → DESIGN → BUILD → TEST → RELEASE`.

Its durable kernel should capture only what remains meaningful across changing
methods:

```text
identity
intent / requirement / acceptance
authority / constraints
current checkpoint
relationships / provenance
evidence / deliverables
resource ownership / leases
decisions / outcomes
```

Plans, Activities, experiment sequences, research tactics and tool choices are
Agent working structures and may be created, revised, abandoned or replaced
without migrating the durable work object.

Execution Envelopes define **what may be affected and what gates apply**, not
the exact sequence of steps the Agent must follow.

Recipes and Skills are optional accelerators for recurring patterns, never a
second workflow engine.

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

Delegated authority is never a blanket Session capability. It is bound to:

```text
principal
+ target
+ operation
+ scope
+ expiry / validity
```

Changing Item, target or operation does not silently carry elevated authority
forward.

Privilege is target-scoped, not username-scoped.

### 2.3 Policy inheritance

Compliance policy composes from broad to narrow:

```text
global personal policy
        ↓
host / device profile
        ↓
project / environment policy
        ↓
target-specific delegated exception
        ↓
per-operation admission
```

Narrower layers may add constraints or explicitly delegated exceptions; they do
not redefine ServerOS's generic safety semantics.

This keeps Tokyo, Windows, future servers and production/jump hosts consistent
without copying an unrelated policy file per machine.

### 2.4 Network safety

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

### 2.5 Shared host safety

Default expectations:

- no global Docker prune;
- no destructive shared-volume cleanup;
- no daemon reconfiguration without approval;
- no system package/service mutation without approval;
- user-space tooling preferred;
- loopback-only service bind unless a wider bind is explicitly intended;
- no assumption that file mode alone provides isolation from other privileged
  users on the host.

### 2.6 Information-flow policy

Read authorization and propagation authorization are different.

A source may be readable for a task while still being ineligible for Memory,
GitHub, reports, publication or export.

ServerOS therefore models information-flow permissions separately from mutation
permissions. At minimum, policy must be able to distinguish:

```text
readable
retrievable
memory-eligible
report-eligible
publishable
exportable
```

The exact vocabulary may evolve, but the distinction is an invariant.

Raw sensitive material should remain in its protected source/archive boundary.
Derived Context, Memory, reports and GitHub artifacts must respect propagation
policy rather than assuming "I was allowed to read it, therefore I may copy it".

---

## 3. Daily operating model

ServerOS is not only a runtime helper. It maintains a daily work loop.

ServerOS is **demand-driven**. It does not create work merely because it can
imagine something useful to do.

Primary work-demand inputs are:

```text
Feishu online messages / requests
offline conversations → recording / transcription / minutes
direct user oral additions / corrections
existing project requirements / incidents / routines
```

Most projects are stable and explicitly known. A new project is created or
reactivated because the user recognizes a real work demand, not because ServerOS
autonomously invents one.

```text
Feishu / Transcripts / Minutes / User Input / GitHub / Sessions
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

It is project-first and **claim-based rather than overwrite-based**.

Important facts are represented as versioned Claims with provenance. When new
evidence conflicts with old understanding, ServerOS preserves both until the
conflict is reconciled instead of silently replacing history.

A durable Claim may therefore carry:

```text
subject / predicate / value
source / provenance
observed_at
effective_from / effective_to when known
confidence
status
supersedes / conflicts_with
```

Current Understanding is a projection over these Claims, not a mutable
paragraph that forgets how it got there.

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

Daily reconciliation still happens when the system finds no material delta.
In that case ServerOS presents the current structured understanding and a
concise "no material change detected" view so the user can discuss, correct or
confirm it. Reconciliation is therefore a daily alignment ritual, not merely an
exception inbox.

An unresolved understanding delta does not freeze unrelated, already-approved
Items.

### 4.1.1 Reconciliation documents

The human-facing reconciliation surface uses **one document per day** plus a
long-lived master document/index.

```text
Daily Reconciliation / YYYY-MM-DD
  current understanding summary
  material deltas
  corrections / accepted edits
  candidate work
  unresolved questions
  checkpoint result

Reconciliation Index / Master
  date index
  short daily summaries
  major decision/understanding links
  links to each daily document
```

The daily document is the discussion/confirmation surface. The master document
is navigation and longitudinal summary, not a second truth database.

The normal interaction happens conversationally in CloudCLI with the
Coordinator. The user corrects/approves in natural language; ServerOS updates
the day's document and structured checkpoint as a consequence. Feishu documents
are durable human-readable outputs, not mandatory form-entry UI.

### 4.2 Archive, Understanding, Knowledge, Memory and Work are different

```text
Archive        what happened
Understanding  what we currently believe reality is
Knowledge      what we curate for future reuse
Memory         how Agents efficiently recall the above + prior experience
Work           what we choose to do next
```

Collapsing these concepts creates false authority.

### 4.3 Source authority is contextual, not last-write-wins

A newer message does not automatically replace stronger evidence.

Typical authority depends on the question:

```text
explicit user confirmation
  → highest business-intent/alignment authority

approved project docs / decisions
  → intended policy/design authority

Git / code / configuration
  → implementation authority

runtime observation / E2E
  → current LIVE-behavior authority

Feishu discussion / meeting source
  → evidence, request, hypothesis or intent input

Memory
  → retrieval/experience aid, never automatic truth authority
```

Conflicts remain explicit until the relevant authority question is resolved.

### 4.4 Location-independent retrieval

The user should not need to remember where a fact was stored.

A Project-aware search/retrieval path may federate across:

```text
Feishu IM
Minutes
My Docs
Wiki
Drive metadata
GitHub / Bridge
local records / knowledge
MetaCC session history
Memory Provider
```

Results preserve source/provenance and should prefer authoritative/current
sources over merely semantically similar ones.

---

## 5. Source capture, Follow and Feishu

Feishu is the **primary enterprise information and work-demand source family**
for the current product.

Most relevant external work information arrives through:

```text
Feishu IM
Feishu My Docs / Drive / Wiki
Feishu Minutes / meeting transcripts
offline conversations uploaded/transcribed into Feishu
explicit user corrections / additions
repo/document links mentioned in Feishu
```

GitHub does not independently create work merely because an issue/repo exists.
A GitHub object becomes work context because an existing Project, Follow,
Requirement or explicit human instruction references it.

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

Raw work information is **retention-first**. Feishu messages, real work Minutes
and source documents normally remain in their native system as canonical
sources. ServerOS keeps locators, indexes and only the snapshots needed for
continuity/search/provenance.

Deletion/cleanup is reserved for clearly identified garbage such as empty/test
meeting records, probe artifacts and disposable temporary content. "No longer
important to today's work" is not by itself a deletion reason.

### 5.2 Follow

A durable `Follow` relationship answers:

> What should ServerOS continue watching?

Possible sources:

- Feishu chat;
- Feishu document;
- Wiki subtree;
- offline conversation transcript / meeting minutes;
- explicit user oral/manual input;
- GitHub repository / issue / release;
- external research source.

Follow records source identity, scope/project, snapshot policy, understanding
policy and notification policy.

### 5.2.1 Repository links mentioned in Feishu

Repository URLs mentioned in Feishu are evidence/context, not automatic Project
or work adoption.

For direct/private messages where a repository is intentionally sent to the
user, ServerOS may surface an **inspection proposal**:

```text
private/direct repo reference
→ show user
→ user confirms inspection
→ temporary clone/read-only analysis
→ optional recommendation
```

A group-chat mention does not automatically trigger a clone. Inspection does not
create a Project, Bridge or Item. Those remain explicit user decisions.

### 5.3 Feishu surfaces have different jobs

ServerOS does not flatten Feishu into one fake filesystem.

The three primary human content spaces are:

- **My Docs** — the user's active private workbench;
- **Drive** — files, attachments and binary deliverables;
- **Wiki / Knowledge Base** — curated long-lived human knowledge.

My Docs is not modeled as a Drive-style folder filesystem. Its useful structure
is a document/subpage hierarchy plus search/recency; physical folder placement
belongs to Drive.

**Minutes / 妙记 is a separate source surface**, not a Drive folder. A Minute
may have related recording/transcript/summary objects and derived notes, but its
source identity remains Minutes-native.

Recommended semantic use:

```text
My Docs
  active private work
  daily reconciliation
  drafts
  decision packets
  temporary analysis
  inbox / unclassified notes

Drive
  attachments
  PDFs / spreadsheets / slides / images / archives
  project files
  meeting files
  deliverable binaries
  temporary staging

Wiki
  project home
  architecture / decisions
  runbooks
  project knowledge
  confirmed meeting notes
  reports / reusable documentation

Minutes
  recording / transcript / AI summary source
  meeting metadata
  source provenance
```

ServerOS may maintain locators, semantic roles and local snapshots across these
surfaces.

Objects owned by other people or external/shared spaces are not blindly moved.
Owned personal My Docs/Drive content may be reorganized automatically under
stable rules when the action does not affect other users.

### 5.4 Knowledge promotion and freshness

A conversation, hypothesis or draft does not become Knowledge merely because it
was captured.

```text
Source / discussion
→ Observation / provisional understanding
→ human alignment when material
→ confirmed reusable content
→ Knowledge / Wiki
```

My Docs is the normal drafting/workbench surface. Confirmed reusable material
may be promoted into Wiki. Agents may organize, rewrite and maintain the user's
own content aggressively, but the promotion semantics must remain visible.

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

Code/config/service/version changes should trigger a generic knowledge impact
scan. Linked documents may become `STALE_SUSPECT`; the Agent verifies them and
may update user-owned internal documentation automatically when the evidence is
clear, otherwise it proposes a correction.

The separate Support/Support Bot project owns its own support-knowledge
publication lifecycle. General Project Knowledge and Support Knowledge are not
one product merely because both are documents.

### 5.4.1 Knowledge spaces are audience/authority boundaries

A Feishu Wiki **knowledge space** is not merely a folder above Projects.

ServerOS may use:

```text
Private work knowledge space
  internal cross-project notes / architecture / meetings / reports

Project publication knowledge space (only when needed)
  public/team-consumed configuration, usage, runbooks or product docs

External corporate knowledge spaces
  read/index/follow sources; never reorganized by ServerOS
```

A Project does not automatically receive its own knowledge space. A separate
space is created only when that Project has a real published/team knowledge
surface, such as configuration or user-facing documentation.

### 5.5 Meetings / Minutes

Original Minutes objects remain canonical source objects.

A real work meeting normally creates a **MeetingBundle relationship** rather
than physically forcing every artifact into one folder:

```text
Minute source
├── recording
├── transcript
├── AI summary / chapters / todos
├── meeting/calendar metadata
└── derived Meeting Note
```

For relevant work meetings, ServerOS should by default create a **Meeting Note
draft** containing:

- project classification;
- summary;
- decisions;
- actions / commitments;
- people / ownership changes;
- requirements / candidate work;
- open questions;
- relevant excerpts;
- source locator.

The draft enters the daily reconciliation flow. After the user aligns/corrects
it, the durable human-facing note is promoted into the owning project's Wiki
meeting area and Structured Understanding is updated from the confirmed
interpretation.

Minutes source objects remain where Feishu owns them. ServerOS organizes their
meaning and indexes, not by pretending Minutes is a normal Drive folder.

Meeting relevance and retention remain separate from deletion authority.

Incidental audio does not become work understanding merely because it was
recorded.

---

## 6. Session Fabric

Session is a first-class ServerOS resource, but it is **not the primary
long-term continuity authority**.

### 6.0 Every Agent Session enters ServerOS through a thin bootstrap

Claude Code / Codex sessions should see ServerOS by default, but startup context
must remain intentionally tiny.

```text
L0 bootstrap
  ServerOS available
  device / principal / cwd
  small semantic entrypoints

user prompt
  ↓
intent resolution
  ↓
Project / Item / general-work resolution
  ↓
L1 Context Capsule
  ↓
Agent starts real work
  ↓
L2/L3 expansion on demand
```

The bootstrap does not preload Project history, Memory, Ability catalogues,
Skills, MCP schemas or recent Sessions.

Intent authority is ordered roughly as:

```text
explicit current user intent
> explicit Item/worktree identity
> cwd Project relationship
> recent Session/history hints
```

A repository cwd may strongly resolve Project/Repository but does not by itself
bind an Item. A ServerOS-managed Item worktree may resolve both Project and Item.

Dynamic work state does not belong in `CLAUDE.md`. Stable repository/project
instructions may live there; current Item/checkpoint/decision state comes from
ServerOS Context.

Long-lived work continuity is anchored by:

```text
Project / Item
+ durable documents
+ code / artifacts
+ Memory
+ decisions / checkpoints
```

A Session is a bounded, relatively clean **working context** attached to that
durable work.

This deliberately prefers:

```text
one Item
  → many focused Sessions
```

over one enormous transcript that must survive for months.

### 6.1 Session hierarchy in the human UI

The default human navigation is semantic:

```text
Project
└── Work / Item
    └── Sessions
```

CloudCLI may naturally discover/group native sessions by working directory or
project path, but ServerOS adds the semantic overlay: Project, Item, role,
device and current checkpoint. Native directory grouping is useful evidence,
not the work model itself.

Top-level views should emphasize the Coordinator and recently active work rather
than dumping every native session into one flat list.

### 6.2 Logical roles

Common roles:

- **Coordinator Session** — reconciliation, board, project switching, routing;
- **Execution Session** — project/Item implementation, research or test work;
- **Maintenance Session** — incident diagnosis and repair.

These are semantic roles, not mandatory process classes.

A Coordinator is a role and continuity pattern, not a requirement to keep one
native Claude/Codex transcript alive forever. A fresh Coordinator Session can
recover from durable understanding, board state, Memory and checkpoints.

### 6.3 One Item may have many sessions

Example:

```text
SRV-0123
├── product/requirement discussion
├── implementation session
├── research session
├── verification session
└── review session
```

Each Session may carry a narrower context than the Item as a whole.

The durable Item is the continuity anchor; Session turnover is expected.

### 6.3.1 Automatic checkpoints

A Session should produce a compact checkpoint automatically at meaningful
boundaries such as completion, handoff, compaction or clean shutdown.

Checkpoint semantics:

```text
what changed
current state
important evidence
what was learned
next likely step
unresolved blocker / decision gate
```

Checkpoint routing:

```text
work continuity
  → Item checkpoint

episodic / procedural experience
  → Memory candidate

high-impact business fact
  → Understanding Delta

raw transcript
  → MetaCC / native history
```

The user should not need to ask the Agent to summarize before a Session can be
continued elsewhere.

### 6.4 Live Session and history are separate

**ServerOS Agent Session** owns live control semantics:

- discover;
- inspect;
- create;
- resume;
- send;
- result;
- interrupt;
- stop;
- handoff;
- remote attach when the Provider supports it.

A stable ServerOS session ref is not a PID, tmux session, chat thread or native
provider ID.

Provider-native IDs remain locators.

### 6.5 MetaCC is the preferred Session History / Intelligence Provider

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

### 6.5.1 Agent Improvement through MetaCC

The separate Log Analysis project targets organization/user workload logs and is
not ServerOS's own telemetry subsystem.

For ServerOS self-improvement, first use MetaCC over the user's own Claude/Codex
sessions.

A periodic improvement recipe may analyze:

```text
repeated tool failures
compaction / oversized sessions
repeated human corrections
poor handoffs
duplicated investigation
slow capability discovery
unproductive retry patterns
editing / verification patterns
```

and produce **Improvement Findings** with evidence.

```text
MetaCC session history
→ analysis
→ recurring friction / success pattern
→ Improvement Finding
→ optional Candidate
→ user selection
→ ServerOS/Ability improvement Item
```

It does not silently change prompts, Skills or runtime configuration merely
because a pattern was detected.

Subagents and Quay runs are normally shown underneath their parent
Session/Activity rather than promoted into the top-level Session list. If a
delegated thread becomes independently durable work, it may be promoted to its
own Session/Item relationship.

### 6.6 Cross-device operation has two different meanings

ServerOS must support both when the underlying Providers allow them.

**Remote attach to the same running Session**

```text
Session runs on Tokyo
      ↓
CloudCLI / Tailscale / remote transport
      ↓
user or Agent interacts from Windows/phone
```

Execution location did not move.

**Work handoff to another device**

```text
Tokyo Session
→ checkpoint / memory extraction
→ new Windows Session
→ same Project / Item
```

The native Session changes; durable work identity does not.

Handoff normally prefers a fresh Session with Memory/Checkpoint links because it
keeps context cleaner. Native resume remains available when it is genuinely the
better option.

A Session must not silently rebind from one Item to another.

### 6.7 Device provenance

Session and history records carry device provenance.

At minimum:

```text
execution_device_ref
provider/runtime
native_session_ref
origin / predecessor / handoff refs
```

Device provenance is metadata about where work happened, not a reason to split
the user's global work history into device silos.

### 6.8 Session archival and raw history

Raw Claude/Codex transcripts are intended for long-term retention.

Policy:

- Session Intelligence may propose archival;
- archival requires user confirmation;
- raw deletion is a separate destructive/retention action;
- checkpoint and useful memory extraction may happen before archival.

For sensitive history, prefer:

```text
immutable/protected raw transcript
        ↓
sanitized projection
        ↓
Memory / GitHub / report / publication as permitted
```

A secret or restricted datum accidentally printed into a Session does not grant
permission to propagate it.

Session may end while its durable Item/Memory continuity survives.

---

## 7. Memory

### 7.0 Memory Federation, not one competing memory database

ServerOS coordinates several memory/continuity authorities without pretending
they are interchangeable:

```text
Hot working context
  current Claude/Codex Session

Native near-memory
  provider-native project/session memory and stable instructions

Durable approved truth
  Structured Understanding / decisions / checkpoints / code/runtime facts

Associative long-term memory
  OpenViking-like episodic/procedural/entity memory

Raw session history/intelligence
  MetaCC + provider-native transcripts
```

OpenViking and native Agent memory are retrieval aids, not business truth
authorities.

Normal Context Assembly retrieves only a small relevant slice; Memory has a
bounded context budget rather than a fixed requirement to inject N memories.
Zero retrieved memories is a valid result.

ServerOS does not continuously copy dynamic business facts into CLAUDE.md or
provider-native memory. High-impact facts become Understanding Deltas and are
reconciled before becoming durable truth.

-Context Fabric

Memory is powerful, but it is not the system of record.

### 7.1 Independent Memory Provider

A mature memory engine such as **OpenViking** may run as an independent,
long-lived Provider on the primary Linux/Tokyo node and expose MCP/HTTP
interfaces.

This does not violate the small-core rule because it is a Provider/Managed
Runtime, not part of `serverosd`.

The Memory Provider is **important but non-blocking**. If it is unavailable,
ServerOS enters a degraded-memory mode and still starts work from:

```text
approved Structured Understanding
+ Item checkpoint
+ durable documents / code / decisions
+ native Session history when available
```

Memory outage must not turn the whole Work OS into a single point of failure.

Preferred composition:

```text
ServerOS plugin
├── memory governance Skill
├── memory adapter
└── Memory Provider (OpenViking-like service / MCP)
```

### 7.2 Memory topology and device provenance

Memory is logically global to the user's work, not one isolated memory database
per device.

The preferred topology is a central Memory Provider on the primary Linux/Tokyo
node, with other devices accessing it through an approved private path when
needed.

Memory entries may carry device metadata such as:

```text
origin_device_ref
observed_runtime
source_session_ref
```

and, where relevant, applicability such as:

```text
applies_to: all devices
applies_to: windows
applies_to: device:<id>
```

This matters for operational memories ("this Windows path works") without
fragmenting project/personal memory by machine.

### 7.3 Memory scopes and precedence

Memory may cover:

- Personal / global;
- Role;
- People / Organization;
- Project;
- Item;
- Session / Experience.

When several scopes provide applicable guidance, more specific scope refines
broader scope rather than destroying it:

```text
global / personal
      ↓
role / organization
      ↓
project
      ↓
Item
      ↓
Session / current execution
```

A narrower memory may override or qualify a broader preference for its scope,
while provenance/history remain available.

### 7.4 Automatic vs governed memory

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

### 7.5 Memory retrieval is progressive

Default retrieval should be metadata/abstract-first.

A Context Capsule should contain only a few highly relevant memories. The Agent
can expand when needed.

Memory retrieval must preserve provenance and avoid turning an old inference
into current truth.

### 7.6 Knowledge freshness is a generic capability, domain curation is not

ServerOS may provide generic mechanics for:

```text
knowledge source linkage
version/applicability metadata
last_verified
STALE_SUSPECT detection
change impact assessment
draft update proposal
```

For example, a code/version/config change may make linked knowledge suspect.

But ServerOS does **not** own the domain-specific knowledge product. LiteLLM
Support KB curation, classification and publishing remain responsibilities of
the Support/Knowledge project. ServerOS supplies the generic change/freshness
machinery and routes resulting proposals to the owning project.

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

**Item means an approved requirement / durable work package.** It does not mean
"code change" and it does not mean "every action the Agent performs".

An Item may deliver:

```text
code / PR
deployment / configuration change
document / guide / report
analysis / findings / evidence
test / benchmark / validation
operational change
other acceptance-backed deliverable
```

The current company-work domain has three primary work classes:

```text
1. Development & Operations
2. Log Analysis / Observability
3. Technical Support
```

These are work modes, not three separate workflow engines.

**Development & Operations** commonly produces durable Items and may include
code, deployment, infrastructure/configuration, verification and documentation.

**Log Analysis / Observability** may be an Item when there is a formal
requirement/deliverable, or a Routine/ad-hoc analysis when there is not. Its
deliverable may be findings/evidence/report with no code change.

**Technical Support** is normally conversational and does **not** create an
Item.

Claude Code / Codex are not the primary end-user support responders. Their main
role in the support ecosystem is to maintain the product, support knowledge,
configuration, evidence and the systems that make support reliable.

The separate Hosting / Support Bot project owns automatic end-user Q&A and
reply behavior. ServerOS supplies reusable Context, Knowledge, Evidence,
Communication and project/service resolution capabilities to that project.

Only a durable engineering follow-up, knowledge-maintenance requirement,
repeated product problem or explicit requirement is promoted to Candidate →
Item.

### 8.1 Human work board is a simple work document

The durable underlying board remains cross-project, but the primary human view
should feel like a concise work document rather than Jira/Kanban.

A Project work document may use:

```text
处理站 / Today's Focus
  a small set intentionally selected for current attention

未完成
  remaining accepted requirements/work, ordered by current importance

已完成
  completed/closed outcomes, kept after unfinished work for history
```

Each day, reconciliation may propose a few unfinished entries for the
**处理站**; the user confirms the focus set. There is no need to expose every
internal state as a board column.

Projects remain filters/relationships, not separate truth islands.

### 8.2 Candidate before Item

Explicit human demand, incidents and reconciled understanding changes may create
Candidates.

ServerOS may consolidate repeated **human-demand signals** into a coarse-grained
Candidate, for example several support requests that point to the same product
problem. Logs/telemetry may strengthen the evidence, but ServerOS does not scan
for arbitrary opportunities simply to manufacture work.

```text
repeated human/support demand
+ related logs/evidence
→ propose one coarse-grained Candidate
```

The proposal retains source/evidence links and confidence. It still does not
become an Item automatically.

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

### 8.5 Item is internal continuity; documents are the primary human surface

Item remains the durable work/control identity, but the user is not expected to
live in Item-detail pages.

The normal human-facing materials are:

```text
Requirement / Brief
Structured Understanding / project context
Design / Decision
Final Report / Verification Report
```

Agent-facing execution/checkpoint material may exist underneath without becoming
a primary user UI.

Board entries should link to the relevant human documents. The Item id is stable
metadata/continuity, not the product experience itself.

### 8.6 Not every operation is an Item

A durable requirement becomes an Item. A small operational/support action may
not.

Examples:

```text
"upgrade this service safely"
→ durable requirement
→ Candidate / Item

"restart this already-owned failed disposable runtime"
→ bounded incident/maintenance action
→ no new Item required

"answer this support question"
→ Support interaction
→ no Item required

"produce this deployment guide"
→ durable deliverable requirement
→ Item
```

Therefore ServerOS does **not** introduce a separate `EnvironmentChange`
work-object. Environment/tool/software changes are either part of an approved
Item, a Routine, or a bounded Incident/Maintenance action depending on why the
change exists.

### 8.7 Project lifecycle

Projects are not simply present or deleted.

At minimum:

```text
ACTIVE
→ DORMANT
→ ARCHIVED
```

Dormant/Archived projects remain searchable through Memory, Knowledge, GitHub
and records, but are excluded from default Daily Understanding, Personal Board
and Context unless relevant.

A meaningful new source signal may create a **Reactivate Project** proposal for
daily reconciliation.

Reactivation restores the project to active daily context; it does not create a
new project identity.

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

Web research is a generic Evidence Acquisition capability available across
Development & Operations, Technical Support and analysis work. The current
baseline may be simple search/browser use; richer online-research Providers can
be added later without changing the work model.

ServerOS does not need a separate "research platform" to support this.

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

Near term, each GPT round still requires human participation and GitHub
documents/Decision Packets are the durable bridge.

The target architecture should nevertheless model **external reasoning
collaboration** as an Ability/Provider boundary rather than hard-code one manual
workflow.

Useful semantic operations include:

```text
reasoning.consult
reasoning.requirement_review
reasoning.design_review
reasoning.research_synthesis
reasoning.challenge
reasoning.evidence_review
reasoning.decision_review
```

A future ChatGPT-capable Provider may implement some or all of these operations.
Skills/recipes teach Claude/Codex **when and how to ask GPT well**: what packet
to prepare, what question to ask, what evidence to include and how to consume
the response.

This belongs in the Ability/Coordination layers, not in `serverosd`.

Until a direct provider exists, the human remains the transport between CC and
GPT.

For the user's own work environment, the default future policy is that **work
content may be sent to the trusted GPT reasoning Provider**, including code,
Feishu-derived work information, logs, decisions and project documents.

Capability secrets remain a different class: credentials, private keys, access
tokens and authorization material are not normal reasoning context and are not
propagated merely because the work content itself is allowed.

Project/source policy may still narrow this if a future source carries an
external restriction.

Automated CC↔GPT review, when a direct Provider exists, should use a bounded
review cycle. A reasonable default is **2–3 autonomous review/revision rounds**.
If material disagreement, scope expansion or unresolved decision remains after
the convergence guard, escalate to the user.

ServerOS prepares clean Context/Decision Packets instead of forcing GPT to
re-read raw work history.

### 10.2 GitHub Work Packet: CC → GPT → CC

For substantial Items, durable reasoning material lives in an owned records/docs
repository (for example `hwz-records`), while implementation remains in the
Bridge/code repository.

A deliberately small packet is preferred:

```text
brief.md       requirement / background / source links / acceptance
design.md      research synthesis / decision / design
execution.md   agent checkpoint / implementation notes (primarily agent-facing)
report.md      verification / E2E / delivery / final outcome
```

Large research may split into `research.md`; do not create files merely to
mirror workflow states.

Target loop:

```text
CC
→ collect Feishu/context/code evidence
→ maintain brief/work packet in GitHub

GPT
→ read Work Packet + Bridge code
→ research/challenge/decide/design
→ write design/decision material back to owned docs/records main

CC
→ observe approved design revision
→ refresh Context Capsule
→ implement in Bridge worktree/branch
→ test / E2E / PR
→ maintain execution/report

GPT
→ optional final design/evidence review
```

GPT may directly write owned reasoning/docs records where authorized. Code still
follows normal branch/worktree/PR delivery and GPT does not directly replace
code-main authority.

If the design contains no unresolved `HUMAN_DECISION_REQUIRED` gate, CC should
be able to continue automatically from the accepted design instead of waiting
for the user to manually retransmit it.

---

## 11. Execution and Quay

Claude Code/Codex may autonomously split an Item into Activities whenever that
helps execution.

Activities are **mutable working decomposition**, not a fixed lifecycle. The
Agent may reorder, add, collapse, replace or abandon Activities as evidence
changes, provided durable acceptance, authority and provenance remain intact.

Planning is an execution tool, not a mandatory human approval ceremony and not
a durable workflow contract.

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
+ expected deliverables exist
+ sufficient verification exists
+ evidence is durable
+ final report exists
+ business/adoption outcome is recorded when relevant
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

### 12.2 E2E is a first-class Ability

E2E is not merely the last command in a development recipe.

It supports at least two major modes:

```text
Item Verification
  prove one Requirement/Item acceptance end to end

E2E Campaign
  verify a branch / release / migration candidate
  across a large scenario matrix
```

A campaign may vary:

```text
scenario
feature/acceptance point
provider/model
device
browser
configuration
test environment
version/branch
```

The goal is to minimize routine human participation:

```text
acceptance / branch target
→ plan scenario matrix
→ prepare isolated environments
→ execute
→ observe
→ capture evidence
→ compare expected/actual
→ verdict
→ cleanup
→ concise report
```

Human involvement is reserved for ambiguous acceptance, authorization gates,
environment constraints and unexpected product decisions.

E2E should discover and provision the required test environment as
automatically as possible, then resolve browser/test tooling through the Ability
Fabric rather than hard-coding one executor.

Preferred browser strategy:

```text
Playwright native/CLI
  default deterministic and fast executor
  machine-readable assertions / traces / screenshots

Playwright MCP
  interactive Agent exploration/debugging/adaptive browser control

OpenCLI / real browser profile
  specialized path for reusing a real logged-in browser/session when needed
  useful but potentially slower than fresh deterministic Playwright
```

Screenshots are first-class evidence and should be captured generously at
meaningful journey checkpoints, especially for real UI E2E. They complement
machine-readable assertions; screenshots alone do not prove PASS.

E2E may run with a dedicated test identity or a real user identity. Identity is
part of the test-environment contract and evidence, not an implicit browser
accident. A test identity is preferred for repeatable/isolation-sensitive flows;
real identity may be required when acceptance specifically depends on real
permissions/account state.

Every substantial E2E verification/campaign produces a readable report with:

```text
target revision / branch
environment
identity type used
scenario / acceptance matrix
PASS / FAIL / BLOCKED
key screenshots
machine-readable assertions
reproduction/evidence links
final verdict
```

The user may still inspect the live CC/terminal interaction; the report is the
durable acceptance artifact.

An E2E Campaign is a verification plan/run grouping, not a new top-level work
object. It belongs to an Item/Release/explicit verification target.

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

### 13.1 Communication is cross-cutting

Outbound communication is a reusable Ability, not duplicated separately inside
Support, Incident, Reporting and Engineering.

Possible operations include:

```text
communication.draft
communication.review
communication.send
communication.reply
communication.publish
```

Feishu is the first-class communication Provider for the current product.

GitHub PR/issue/comment actions remain part of normal engineering work.
Email or other channels are future Providers and do not need pre-built product
machinery merely for completeness.

Support Bot automatic Hosting replies remain owned by the separate Support Bot
project; ServerOS supplies generic Communication, Context, Knowledge and
Evidence capabilities.

Providers may include Feishu, GitHub and future approved channels.

Low-risk notifications/report delivery may eventually be automated by policy.
External commitments, sensitive statements or other high-impact communication
may require explicit confirmation.

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

### 14.4 Space Fabric

Space governance is shared across Local, Git and Feishu surfaces.

The core resolver answers logical placement:

```text
what is this object?
which Project / Item does it belong to?
why does it exist?
what lifecycle does it have?
which surface is canonical?
```

It does **not** hard-code today's filesystem path, Wiki token or Drive folder.

```text
                         Space Fabric
                              │
                         space.resolve
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
        Local                Git               Feishu
          │                   │                   │
   Workspace adapter     Repo adapter    ┌────────┼────────┬────────┐
                                         │        │        │        │
                                      My Docs   Drive     Wiki    Minutes
```

The same semantic identity may therefore have different physical shapes on
different surfaces without copying one directory tree everywhere.

Example:

```text
Project: LiteLLM
Item: LIT-0123

Local     code / worktree / evidence
My Docs   draft / reconciliation / decision packet
Drive     attachments / binary deliverables
Wiki      confirmed guide / meeting note / project knowledge
Minutes   source recording / transcript
```

Shared keys such as `project_ref` and `item_ref` connect the objects. Physical
folder trees do not need to mirror each other.

Do not create another local `workspace/projects/<project>` symlink/index tree
merely for convenience. The role-based physical layout plus semantic resolver
is sufficient; Project views belong in CloudCLI/Wiki/search, not as another
filesystem hierarchy.

### 14.5 Inbox is a placement state, not a junk drawer

Each relevant surface may expose an Inbox/unclassified entry point:

```text
Local       downloads / intake
My Docs     Inbox
Drive       Inbox
Minutes     unclassified/recent logical view
```

Inbox means "captured but not yet confidently placed".

Stable classification may move owned content automatically. Ambiguous objects
enter reconciliation rather than being guessed into a permanent location.

### 14.6 Space Stewardship recipe

Automatic organization is a recipe over existing owners, not a second
Workspace Manager.

```text
Inventory / new object
        ↓
Information Management
  identify / classify / project / Item / semantic role
        ↓
space.resolve
        ↓
KEEP / INDEX / MOVE / PROPOSAL_REQUIRED
        ↓
correct surface adapter
        ↓
verify
        ↓
update locator / index / provenance
```

Responsibilities remain separated:

- **Information Management** — what the object means and what work it belongs to;
- **Workspace Management** — local disk, repos, worktrees, artifacts;
- **Feishu Space Management** — My Docs, Drive, Wiki and Minutes locators;
- **Space Resolver** — deterministic logical placement.

### 14.7 Automation authority for owned personal spaces

For content clearly owned by the user, ServerOS may have high automation
authority to organize:

- personal My Docs;
- personal Drive;
- local user files;
- local downloads / intake / temporary staging.

The governing invariant is **non-interference with others**, not manual approval
for every move.

Shared/external objects remain proposal/index-first.

Physical deletion remains lifecycle/policy governed and must not be inferred
from "misplaced".

### 14.8 Periodic local housekeeping

A Routine may periodically organize clearly-owned cold/unbound inputs and
temporary content such as:

- downloads;
- local intake;
- tmp;
- expired staging;
- orphaned generated outputs with known provenance.

It must not casually rewrite or clean:

- active repositories;
- dirty worktrees;
- live runtime state;
- unknown-owned files;
- other users' data.

The goal is semantic convergence, not making directory counts look clean.

---

## 15. Project world model

Project is one of the main long-lived organizing objects in ServerOS.

```text
Project
├── People / project roles
├── Sources
├── Repositories
├── Services
├── Environments
├── Items
├── Knowledge
├── Credentials / vault refs
├── Deliverables
└── Runtime / evidence relationships
```

A Project is a stable business/work domain, not a requirement and not a
repository. Projects are explicitly created/confirmed by the user and change
infrequently. Normal work changes Items inside an existing Project; it does not
rebuild Project topology.

Examples include LiteLLM, ServerOS, LiteLLM Log Analysis, Support Bot and
MarketOps.

ServerOS may resolve and remember Project relationships, but it does not
auto-adopt a repository, create a Bridge or invent a Project merely because an
Agent opened or inspected a repo.

### 15.1 People and Project Roles

Person identity is lightweight and reusable across Projects.

The durable relationship is:

```text
Person ↔ Project Role
```

Examples:

```text
requester
owner
developer
operator
stakeholder
reviewer
support contact
```

Structured Understanding stores confirmed person/project-role facts and their
provenance. Richer soft context about people — recurring preferences,
communication patterns, prior interactions and similar experience — may live in
the Memory Provider such as OpenViking.

Memory does not override confirmed responsibility/ownership facts.

### 15.2 Sources belong to Projects by relationship, not by physical move

Feishu chats, documents, Wiki nodes, Minutes and other sources may relate to one
or more Projects.

```text
Source ↔ Project
```

is many-to-many.

A group/chat/document is not physically "moved into" a Project merely because
some of its content is relevant. Message/document-level relevance and
provenance determine what enters Structured Understanding.

### 15.3 Service is a first-class semantic object

A **Service** is the stable operational/business thing being built, operated,
observed or supported.

Examples inside a Project may include:

```text
LiteLLM API
shared AI gateway
PostgreSQL
OpenSearch
MinIO
Support Bot
log pipeline
```

Service is not a process, container or host.

```text
Service
= stable semantic identity

Runtime Resource
= current concrete process/container/unit/endpoint instance
```

This separation lets ServerOS translate low-level runtime evidence into useful
human context.

Example:

```text
service: litellm-api
    ↓ deployed into
environment: PROD
    ↓ realized by
container/runtime resources
```

Alerts, E2E, deploys and support context should prefer Project / Service /
Environment names over raw runtime IDs when attribution evidence allows it.

### 15.4 Environment is a first-class risk/execution context

An **Environment** describes the operational context in which a Service is
built/tested/deployed/run.

Typical labels may include:

```text
DEV
TEST
STAGING
PROD
LOCAL/TOKYO
```

No Project is required to have all of them.

Environment carries meaning for:

- authorization/risk;
- data sensitivity;
- deployment target;
- test isolation;
- runtime bindings;
- verification expectations.

For example, TEST may allow broad autonomous execution while PROD promotion
remains a human gate.

### 15.5 Repo → Artifact → Service × Environment → Runtime

The engineering chain is:

```text
Repository revision
        ↓ build
Release Artifact
        ↓ deploy
Service × Environment
        ↓
Runtime Resource(s)
        ↓
Observation / Evidence / Alert
```

This chain is the basis for tracing code to LIVE behavior without confusing any
two layers.

### 15.6 Item ownership and cross-cutting relationships

An Item has exactly one owning Project for board/understanding purposes.

It may relate to multiple:

- repositories;
- Services;
- Environments;
- Sessions;
- Runs;
- Deliverables.

Example:

```text
Project: LiteLLM
Item: upgrade LiteLLM

touches:
  litellm-bridge
  litellm-fjd validation checkout
  litellm-api
  gateway
  TEST
  PROD
  docs
  E2E campaign
```

Do not split one coherent requirement into artificial Items merely because it
crosses repositories or Services.

### 15.7 Project Home in Wiki

Each active Project may have one stable Wiki **Project Home** as the main human
entry point.

Typical sections:

```text
Project Home
├── current state
├── current requirements / work
├── important decisions
├── key links / repositories
├── Services / Environments
├── people / responsibilities
├── meetings
├── knowledge / runbooks
└── deliverables / reports
```

The Project Home is a Current View generated/maintained from ServerOS durable
facts and curated knowledge. It is not a second state database and does not
duplicate every Item artifact.

Because it is the user's own derived view, ServerOS may maintain it proactively:
completed/started Items, accepted decisions, important meetings, Service/
Environment changes and key links may update the page automatically from
durable facts.

### 15.8 Repository relationships

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

### 15.9 Bridge repository pattern

When the user explicitly needs to develop against an upstream/enterprise
repository that is not their writable authority, prefer an owned Bridge
repository. Repository inspection alone never creates a Bridge.

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

For development work, the Bridge is normally the writable engineering surface
used for:

- Item worktrees;
- implementation;
- commits/branches;
- mock/simulated PR preparation;
- GPT review through the GitHub connector;
- durable collaboration evidence.

The enterprise/upstream checkout such as `litellm-fjd` may still be cloned or
used locally for read-only comparison, integration validation and direct test
execution when needed. That does not make it the normal long-lived writable
development surface.

The Bridge must remain synchronized with the enterprise/upstream lineage closely
enough that new Item work and PR preparation start from a known current base.
Synchronization is explicit and provenance-preserving:

```text
enterprise/upstream revision
→ refresh/compare
→ update Bridge tracked base
→ Item branch/worktree
→ implementation + GPT review
→ PR back to enterprise/upstream
```

Bridge synchronization does not make the Bridge upstream authority.

Bridge rules:

- preserve upstream provenance;
- do not claim upstream authority;
- do not push to foreign/protected upstream by default;
- delivery back upstream is an explicit PR/export/promotion step;
- GitHub makes the writable engineering state visible to ChatGPT and other
  Agents.

### 15.10 Project context resolution

Given an incoming work demand, ServerOS should be able to resolve a compact
Project Context using the stable world model.

Example operations/support-system context:

```text
Project: LiteLLM
Service: API / Gateway
Environment: PROD
Requester: <person>
Relevant docs / knowledge
Recent deploys
Relevant logs/evidence
Known issues
Current Items
```

This is reusable infrastructure for Hosting/Support Bot, operations and human
Agent sessions. It does not make Claude Code itself the user-facing support bot.

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

If the Agent already sees an appropriate native Skill, Plugin, MCP or tool and
knows how to use it, it may use it directly. ServerOS is not a mandatory proxy
for every tool call.

ServerOS becomes important when the Agent needs to:

```text
search for an unknown capability
choose among multiple Providers
recover a previously successful capability profile
evaluate readiness / authorization
acquire a missing capability
```

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

Default acquisition tiers:

```text
A  text-only Skill / instruction content
   low-risk provenance-checked discovery
   → may be temporarily attached automatically

B  already-installed / already-authorized executable Provider
   → may be attached/used automatically within current policy

C  new MCP / Plugin / executable / daemon / credential / new network boundary
   → admission + provenance/trust check
   → explicit approval when policy requires
```

### 16.4 Capability Intelligence learns from use

Ability discovery is not only a catalogue lookup.

ServerOS may retain **Ability Experience** from real Runs:

```text
semantic operation
context / environment
Provider / model / Skill / configuration
success / failure
human intervention
latency / cost
failure modes
evidence
```

This experience influences future ranking but never silently overrides current
readiness, authorization or an explicit user choice.

A **Capability Profile** is a named/reproducible combination of model,
Providers, Skills and configuration for a semantic task. It is not a new
top-level work object.

Capability exploration stays inside ordinary Items/Runs:

```text
baseline
→ candidate profile A
→ candidate profile B
→ controlled comparison
→ evidence
→ selected stable profile
```

The comparison should hold task/acceptance conditions stable where practical and
treat human-intervention rate as a first-class metric for unattended work.

Candidate and stable profiles are separate. New tools/models/Skills may be
tested automatically inside authorized experimental scope, but production
profiles are promoted explicitly and traceably.

Do not create a Skill merely because a procedure occurred once. A Skill is
worth preserving when repeated evidence shows that explicit guidance improves
quality, reliability, latency, autonomy or safety over native Agent behavior.

### 16.5 Physical vs logical topology

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
- **phone → CloudCLI private access**;
- CloudCLI access from laptops/Windows;
- private services;
- optional SSH reachability;
- future A2A transport.

The phone is a first-class Human Client:

```text
Phone Tailscale client
      ↓ tailnet
Tokyo private endpoint / Tailscale Serve
      ↓
CloudCLI
      ↓
ServerOS / Claude / Codex sessions
```

The preferred path does not require opening CloudCLI to the public Internet.

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

### 18.3 Remote assistance / device control

Remote assistance is a Provider/recipe boundary, not a reason to enroll every
other person's computer into the user's long-lived Fleet.

For ordinary colleague/user assistance, the current practical tool is typically
**Sunlogin (向日葵)**. RDC/Desktop Commander may still be used where appropriate
for Agent-controlled Windows workflows.

Typical support flow:

```text
support conversation
→ temporary remote assistance when needed
→ bounded action
→ verify
→ return answer/result
```

A temporarily assisted third-party device does not automatically become a
persistent ServerOS device.

Remote assistance is not Agent-to-Agent communication.

### 18.4 A2A

A2A is a future structured Agent-to-Agent adapter.

Primary use should be task delegation/handoff; free-form Agent chatter is
secondary because it can waste tokens without improving work.

### 18.5 Linux primary, Windows specialist

Linux/Tokyo is the primary development/control node.

Windows is an on-demand specialist device for Windows-specific builds/tests,
desktop software and workflows requiring local GUI/configuration.

Windows does not need a duplicate global ServerOS authority.

### 18.6 Execution placement and handoff

ServerOS may automatically **recommend execution placement** based on:

```text
required Ability/Provider
device readiness
OS/platform requirement
current environment
data locality
compliance
resource availability
```

Tokyo is the normal default.

When an Activity reaches a Windows-specific boundary, the Tokyo Agent may
propose a handoff:

```text
Tokyo CC detects Windows requirement
→ prepares checkpoint/context
→ prompts for Windows handoff
→ Windows accepts
→ new/attached Windows Session continues the same Item
→ evidence/checkpoint returns to Item
```

The placement recommendation is automatic; a cross-device handoff that changes
the active execution context is explicit and visible.

Execution placement belongs to the Agent/Work coordination layer, not Fleet
topology itself.

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

The mobile experience is not merely monitoring. From a phone connected through
the private connectivity layer, the user should be able to open CloudCLI,
inspect/switch work, continue a Session when the provider supports it, approve
decisions and review evidence.

ServerOS should integrate rather than fork/reimplement those surfaces where
possible.

A ServerOS CloudCLI plugin should preserve CloudCLI's useful native
project/directory grouping while adding a semantic work hierarchy:

```text
Project
→ Item / durable work
→ Sessions
→ delegated Runs/Subagents
```

It should expose:

- Today / Personal Board;
- Understanding / Reconciliation;
- Project → Item → Sessions;

- Decisions / approvals;
- Memory;
- Quay;
- E2E evidence;
- Environment / health;
- Reports.

CloudCLI native shell/Git/files may perform operations outside ServerOS semantic
APIs; the product must not falsely claim total interception.

The initial product should preserve CloudCLI's default home/sidebar/session UX.
CloudCLI's plugin system is sufficient for ServerOS contextual tabs and backend
integration; replacing/reordering the global home page is not required. A core
CloudCLI fork/change should only happen later if a real product need cannot be
served by plugins.

### 20.2 Feishu is a lightweight human-control and information surface

Feishu is primarily:

- source capture;
- notifications;
- reconciliation/decision reminders;
- alerts/recovery;
- reports;
- lightweight mobile actions.

A full remote terminal inside Feishu is not required by the core product.

### 20.3 Human Presentation Model

ServerOS machine truth is not the same thing as human UI.

The same underlying fact may be rendered differently for CloudCLI, Feishu,
CLI and reports, but all presentations must derive from the same authoritative
state.

```text
ServerOS fact / Alert state
        ↓
Human Presentation Model
        ↓
┌────────────┬──────────┬──────────────┬──────────┐
│ CloudCLI   │ Feishu   │ CLI          │ Reports  │
└────────────┴──────────┴──────────────┴──────────┘
```

The presentation model may add human-oriented semantics such as:

```text
human_title
human_summary
impact
attention
recommended_action
target_display
category
technical_details
```

These fields may be derived from an Alert/Event taxonomy plus current facts.
Renderers must not independently invent different meanings for the same code.

Machine identifiers such as raw alert codes, subject refs, PIDs, generations
and registry counters belong in progressive-disclosure detail unless they are
directly actionable.

### 20.4 Alert / notification UX

Alert UI is optimized for human decisions, not database inspection.

The first screen answers:

```text
1. Do I need to care now?
2. What happened?
3. What is affected?
4. What should I do?
```

Technical detail comes later.

A useful attention axis is separate from machine severity:

```text
ACTION_REQUIRED
REVIEW
WATCH
INFORMATIONAL
NONE
```

For example, a high-severity historical/known event may deserve less immediate
attention than a new medium-severity event requiring a user decision.

Notifications should distinguish at least:

```text
Immediate Alert
  urgent / user action needed

Change Digest
  meaningful changes, not necessarily urgent

Daily Health Summary
  periodic compact health/progress view
```

Historical backlog and resolved counts remain available but should not dominate
the visual hierarchy of an incremental notification.

Feishu cards should use progressive disclosure: top-level human summary,
actionable items, compact current health, then collapsed history/technical
details.

### 20.5 tmux

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

Typical user-space topology is role/lifecycle-based:

```text
workspace/
  src/                canonical / bridge / tool repositories
  worktrees/          Item code workspaces
  test-envs/          Item test definitions/environments
  ops/                instance governance
  knowledge/          durable local knowledge/indexes
  records/            structured records / Understanding

artifacts/
  work-items/<project>/<ITEM>/<RUN>/
  reports/
  e2e/
  repro/
  releases/
  skills/
  ops/

data/
  source-archive/     large raw sources not suited to Git
  provider-data/      durable provider-owned bulk data

state/
  serveros/
  ability-management/
  memory/
  browser/
  ...

runtime-assets/
  ServerOS/plugin revisions
  Ability artifacts

vault/                centralized user-owned credentials/secrets
downloads/            unclassified/new inputs
logs/
caches/
tmp/                  disposable/transient
envs/
toolchains/
software/
```

The important distinction is semantic:

```text
records     durable structured meaning / claims / work facts
data        large raw source material
artifacts   outputs/evidence tied to a Run/Item
state       durable machine/provider operational state
vault       centralized secret material used by the user's Agents/tools
```

### 24.1 Vault

The current design intentionally keeps credential storage simple.

```text
/data/home/vince/vault/
```

is the primary centralized secret directory for the user's Tokyo environment.

Rules:

- user-owned and access-restricted on disk;
- never committed to Git;
- never treated as ordinary Memory/Knowledge/Report content;
- Claude Code / Codex running as the authorized user may directly read needed
  secret files for approved work;
- secret bytes do not need a separate ServerOS Vault service;
- ServerOS may maintain a lightweight semantic index without duplicating the
  secret value.

Useful metadata may include:

```text
secret_ref / file
Project
Service
Environment
purpose / used_for
owner
last_verified
```

This lets an Agent find the correct credential among many files while keeping
actual secret bytes only in the vault directory.

This is centralized operational storage, not a generic enterprise Secret
Manager product.

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

### 25.1 Expected deliverables are part of the requirement

Before execution, an Item should state the intended delivery outcome at the
appropriate level of detail.

Examples:

```text
code change + upstream PR
configuration change
image / package / release artifact
TEST deployment
production-ready runbook
document / guide
analysis report
knowledge update
```

This prevents "code exists" from being mistaken for "the requirement was
delivered".

The Item lifecycle remains unified across work types. Different Item types
produce different evidence/deliverables; they do not need separate workflow
engines.

### 25.2 Code-changing Items require a PR

For current company development, a code-changing Item is not complete at a
local commit or Bridge branch.

```text
Bridge implementation
→ review / E2E / evidence
→ PR to enterprise/upstream repository
→ adoption outcome recorded
```

A real PR is a required deliverable for code changes.

PR **adoption** is a separate business outcome from completion of the requested
engineering work. A PR may be rejected/not adopted because:

- another person implemented the same requirement;
- the upstream owner chose a different solution;
- the requirement was superseded or withdrawn;
- priorities changed.

In those cases the Item can close with an explicit outcome such as
`NOT_ADOPTED`, `SUPERSEDED` or `IMPLEMENTED_ELSEWHERE` plus evidence. It
must not claim the change is LIVE when it was never adopted.

### 25.3 Test environments are ephemeral by default

Where feasible, an Item should use an isolated test lifecycle:

```text
worktree / revision
→ create isolated test environment
→ deploy/start target
→ E2E / verification
→ preserve evidence
→ cleanup/release resources
```

Persistent shared TEST environments remain valid when a Project actually needs
them. They are not required merely because an Item exists.

### 25.4 Code and configuration changes have equal provenance requirements

Configuration is a first-class change surface.

For meaningful configuration/deployment changes, evidence should answer:

```text
before
after
why
owning Item / requirement
approval when required
verification / observed result
```

This applies to model/provider mappings, environment configuration, Docker
configuration, gateway settings and similar operational state. Secret values
remain redacted/protected even when the change itself is auditable.

### 25.5 High-risk migration/destructive work uses the Execution Envelope

Database migrations, destructive operations and irreversible changes do not
need a separate workflow engine.

Their Execution Envelope carries stronger gates such as:

```text
impact/preconditions
backup or recovery plan
rollback feasibility
explicit approval
postcondition verification
```

### 25.6 Automation boundary

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
- rollback testing;
- preparation of production runbooks/artifacts;
- post-change verification/observation.

Production promotion is human-approved by default. After approval, Projects may
allow automated production deployment and bounded rollback when explicitly
covered by policy.

Two current target-specific exceptions are stricter:

```text
LiteLLM production
Tokyo shared AI gateway
```

For these targets, the **production mutation/cutover is user-executed**.
Claude/Codex may prepare the exact change, artifacts, checks and rollback plan;
after the user performs the production action, Agents may verify, observe and
report. Any production rollback/mutation follows the same target-specific human
gate unless explicitly authorized otherwise.

### 25.7 Knowledge/document impact scan at closure

A meaningful code/config/service change should check linked documentation and
knowledge before closure:

```text
change
→ identify linked docs / Wiki / KB / runbooks
→ assess staleness/impact
→ update directly when in scope or propose update
→ verify links/publication
```

ServerOS owns the generic impact/freshness mechanism. Domain-specific knowledge
curation remains with the owning Project (for example Support Bot/Support KB).

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

The Execution Envelope therefore includes an **Autonomy Guard**, not only
permission scope.

Stop/escalate on signals such as:

- decision gate;
- repeated same failure;
- no-progress rounds;
- acceptance moving backwards;
- unexpected scope growth;
- new repository / host / credential boundary;
- abnormal cost/token growth;
- verification confidence falling;
- blocker;
- production/destructive/high-risk boundary.

Important work does not need an artificially tiny monetary budget, but it must
have runaway controls such as maximum retries, no-progress rounds, duplicate
work detection and delegation depth.

---

## 27. Routine and scheduled operating recipes

No `workflowd` exists.

A lightweight **Routine** represents recurring work that should run repeatedly
without inventing a new Requirement/Item each time.

A Routine is normally created only after the work pattern has enough evidence
to justify operationalization. Exploration may be Agent-heavy; a mature Routine
should make stable/mechanical steps deterministic where useful and retain Agent
reasoning only where judgment adds value.

A Routine is still not a rigid workflow engine. It may call an Agent with an
objective/checkpoint and let that Agent choose tactics inside the approved
Execution Envelope.

```text
Routine
├── purpose / scope
├── schedule or trigger
├── policy / authorization
└── Runs
```

A Routine Run may produce a report/result directly. If it discovers durable
work that requires prioritization, it produces a Candidate:

```text
Routine Run
→ result / evidence
→ optional Candidate
→ human selection
→ Item
```

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

No-change skips unnecessary source-analysis LLM work, **but not the daily human
alignment checkpoint**. The daily reconciliation can truthfully say "no
material delta detected" and still allow the user to inspect/correct current
Understanding.

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
├── Workspace / Isolation / Space Stewardship
├── Connectivity / Device / UI
├── Verification / E2E / Release
└── Node Runtime / Operations

B · Information → Delivery
├── Source Capture / Follow
├── Structured Understanding / Reconciliation
├── Project World Model / Service / Environment
├── Work Discovery / Personal Board
├── Requirements Engineering
├── Development & Operations
├── Log Analysis / Observability
├── Technical Support
├── Agent Improvement / Session Intelligence
├── Research / Evidence Acquisition / Web Research
├── Decision / Architecture
├── Verification / E2E
├── Routine
├── Incident / Operations
├── Knowledge Stewardship
├── Remote Assistance
├── Communication
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

Current product scope is the user's **company-development/work environment**.
The underlying primitives may generalize later, but the architecture should not
add lifestyle/personal-domain complexity before a real need exists.

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

1. exact Session record schema and cross-device attachment/handoff contract;
2. MetaCC-to-ServerOS history/intelligence adapter and Agent Improvement recipe;
3. OpenViking deployment/binding/backup contract and device provenance fields;
4. versioned Claim/conflict projection and reconciliation schema;
5. information-flow policy vocabulary and enforcement points;
6. Feishu inventory/blacklist/Follow policy and My Docs/Drive/Wiki/Minutes adapters;
7. Space Stewardship automation policy, Inbox classification and local housekeeping;
8. Project lifecycle plus Person/Role, Service, Environment and runtime attribution model;
9. CloudCLI Project → Item → Session mobile/desktop plugin surfaces;
10. external reasoning / future CC↔GPT Provider contract, Skills and 2–3 round convergence guard;
11. Tokyo Tailscale userspace deployment and phone/private-CloudCLI access;
12. A2A adapter timing/mapping;
13. dynamic environment Attachment/lease/refcount behavior;
14. E2E Item Verification / Campaign contract, scenario matrix and evidence schema;
15. execution placement / Tokyo→Windows handoff contract;
16. Repo→Artifact→Service×Environment→Runtime provenance and presentation model;
17. Bridge↔enterprise synchronization, mandatory code PR delivery and adoption outcome;
18. target-specific production gates and post-deploy LIVE verification;
19. cross-surface retrieval/ranking with provenance and contextual authority;
20. My Docs→Wiki promotion, generic knowledge freshness and Project Home maintenance;
21. human work-document/处理站 projection and docs-first Item presentation;
22. GitHub Work Packet CC↔GPT handoff and automatic design-to-execution continuation;
23. E2E environment discovery, browser-provider selection, identity handling and screenshot-rich report;
24. thin default Session bootstrap + intent-first Context Capsule contract;
25. Memory Federation, bounded retrieval and native-memory authority boundaries;
26. native-first Ability discovery, acquisition risk tiers and automatic Session checkpoints;
27. anti-rigidity kernel: constraint-driven execution, mutable Activities and acceptance-driven completion;
28. Ability Experience / Capability Profiles / candidate-vs-stable promotion and unattended-work metrics.

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
