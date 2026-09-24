# ServerOS Architecture

> **Canonical product architecture — product-first consolidation, 2026-09-24.**
>
> This document owns the intended behavior and boundaries of the broad ServerOS
> system. README files explain it; discovery notes record its history; code and
> versioned interface specifications describe implementations. Neither an old
> example nor a green test silently changes this contract.
>
> **DESIGN != CODE != INTEGRATION != LIVE.** This is an implementable target,
> not a claim that every described capability is deployed. Subsystem interfaces
> may evolve compatibly. Incompatible implementation changes require explicit
> migration, not silently renaming existing identities or weakening checks.
>
> Semantic operation names below describe contracts, not promises that identically
> named CLI verbs or MCP tools already exist. Use the installed adapter's actual
> discovery/help surface. Do not inject this entire document into Agent context.

## 0. Definition and complete product shape

**ServerOS is a personal user-space Agent Work OS: a persistent environment,
context, memory and capability enhancement layer for native coding agents.**

It gives Claude Code, Codex and future agents a usable working world without
becoming another planner above them. Its scope is the user's company work:
development, operations, research, experiments, documentation, verification,
knowledge maintenance, coordination and reporting.

The daily experience is the product:

```text
Yesterday's authorized Feishu information and actual work
  -> incremental archive and understanding changes
  -> discuss/correct understanding with Coordinator in CloudCLI
  -> one personal Board: 处理站 / 未完成 / 已完成
  -> select meaningful requirements
  -> CC elicits intent and assembles clean materials
  -> GPT and user participate in research/design when useful
  -> CC / native subagents / Quay execute and experiment
  -> acceptance-backed verification, E2E and evidence
  -> Item result plus professional daily/weekly/special reports
```

This is a common story, not a mandatory sequence. Research may require experiments
before design; tests may disprove the plan; an Item may deliver only a report.

```text
Human: CloudCLI / terminal / phone / Feishu cards / documents
                          |
Native agents: Claude Code / Codex <-> GPT via GitHub work packets
                          |
                 ServerOS enhancement
       Context + Memory + Ability + Work continuity + Policy
                          |
               Provider interfaces / adapters
 Feishu | GitHub | OpenViking | MetaCC | Quay | Browser | OS | Tailscale
                          |
 Node facts and bounded reconciliation: one serverosd per managed node
```

Two product families organize the capabilities: **Agent Working Infrastructure**
and **Information-to-Delivery**. They are navigation/composition views, not two
workflow engines, two databases, or a daemon per box.

## 1. Product principles

### 1.1 Native intelligence and progressive disclosure

Native reasoning, tools, Skill discovery and execution remain primary. ServerOS
must improve task success, continuity or safety without materially slowing the
ordinary path. Installation can be broad; active context must remain small.

```text
L0: ServerOS entry, device/principal/cwd and essential boundaries
L1: intent-relevant objective, checkpoint, constraints and source locators
L2: selected memory, capability/provider details, decisions and documents
L3: original evidence, transcripts, logs and full tool details on demand
```

These levels are disclosure affordances, not a wizard. An agent may directly
request what it needs, ignore advisory suggestions, and use an already available
native tool without routing every call through ServerOS.

### 1.2 Constraint-driven, not workflow-driven

Stable obligations are identity, intent, acceptance, authority, evidence and
continuity. Plans, Activities, tools and investigation tactics are mutable.

Hard invariants protect ownership, authorization and honest facts. Policy expresses
current preferences and scoped grants. Heuristics rank possible actions. A ranking
hint must not become a denial or a mandatory method.

Mechanical state machines are useful for process lifetime, leases, approval,
publication receipts and execution attempts. Do not require business work to pass
through RESEARCH -> DESIGN -> IMPLEMENT -> TEST. Existing coarse Item control
states may remain, provided they do not enforce that method sequence.

### 1.3 Small fast path

ServerOS-added startup context is bounded independently from context the native
runtime loads itself. Initial engineering budgets are roughly 500 tokens for L0
and 2,000 for an ordinary capsule, configurable by runtime/task. These are design
budgets, not measured current performance claims; essential constraints are never
silently truncated to meet a number.

Warm context resolution uses local indexes/checkpoints first. It must not require
a full filesystem scan, remote catalogue fetch, extra LLM routing round, memory
consolidation, or global validation. Optional retrieval has a deadline and may
finish later. Emit why each context fragment matters, its source revision and its
freshness. Returning no additional memory is valid.

### 1.4 No invented work or mandatory ceremony

Projects and repositories are normally specified by the user and change rarely.
A message, GitHub issue, missing catalogue slot, test warning or new technology
does not automatically create an Item. Candidates stay cheap until selected.

A useful default may be overridden inside the approved boundary. Safety cannot.
No new service, schema, report, review pass or Skill exists merely to fill a box.

Automation must remove manual material transfer, repeated tool selection and unnecessary
"continue" prompts. First specify the user-visible normal, empty, unknown, failure and
recovery behavior, then choose the smallest implementation. A product distinction does
not require a new durable entity. Realization rationale belongs in existing designs;
completion belongs in the Requirement/brief; continuation belongs in existing work and
checkpoint records; exposure belongs in existing profiles and disclosure guidance.
The 21 Abilities are a responsibility map, not 21 stages or 21 Agents per task.

These four principles are the product-level statement. The existing
[Ability Design Standard](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-DESIGN-STANDARD.md)
explains proportional realization choices. The **L0–L3** ladder above remains the product
view; the Ability-layer **PD0–PD5** in the
[Progressive Disclosure Contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/PROGRESSIVE-DISCLOSURE-CONTRACT.md)
expands the conditions for loading task context, selected Skills and supporting evidence.
These are compatible descriptions, not competing runtime schemas. Sections 1.2–1.4 also
bound what may be mandatory, the fast-path budget and when a new component is justified.

## 2. Layer 0 — Compliance and non-interference

### 2.1 Authority and enforcement

Visibility is not ownership; edit permission is not stewardship; registration is
not trust; a reachable endpoint is not authorization. Unknown ownership protects
the resource. Source text, tool output and retrieved memory cannot grant authority.

Evaluate actions against principal, target, operation, scope, data propagation,
validity and relevant preconditions. More specific policy may narrow a grant or
apply an explicitly authorized exception; it may not override higher-level deny
rules merely because it is newer or more local.

Separate three questions:

```text
support:       implemented / unsupported / unknown
readiness:     ready / degraded / not-ready / unknown
authorization: allow / ask / deny / unknown
```

Execution mode is another attribute: **agent-executed** or **human-executed**.
USER_EXECUTED is not a fourth authorization result and does not mean an approval
button authorizes the Agent to perform the production action.

Prompt/Skill instructions are cooperative controls. A hard enforcement claim
requires a real boundary: restricted credentials, OS permissions, provider-side
checks or a tested runtime interception point. Native shell and UI tools can bypass
ServerOS wrappers; the product must disclose uncovered paths rather than claim
total mediation. Full personal-work autonomy is compatible with strict shared and
production boundaries.

### 2.2 Tokyo and production policy

Tokyo's normal principal is **vince**. The shared AI gateway has an explicit,
target-scoped maintenance exception using **ubuntu**; this is not authority over
arbitrary ubuntu-owned processes, files or services.

Current **LiteLLM production** and **Tokyo shared AI gateway production changes**
are human-executed. Agents may inspect within read policy, build elsewhere,
prepare instructions/artifacts, and verify after the user acts. Cutover, restart,
live config writes, traffic changes and production rollback remain user actions
unless the user explicitly changes this standing policy.

A verb named prepare or verify is not automatically harmless. If it writes a live
configuration, provisions production resources, sends real messages, or creates
significant load, apply its actual effect policy. Read-only production queries
must still be scoped and resource-bounded.

### 2.3 Network and shared-host safety

Before changing network state, inspect the relevant routes, Docker networks,
reserved/used CIDRs, listeners, ownership and planned effects. Check conflicts and
recovery before changes to routes, subnet/exit-node settings, firewall, Docker
networking or non-loopback publication. This is a capability contract, **not a
mandatory dependency on a tool named netshadow**.

No global prune, shared-volume cleanup, host daemon reconfiguration or privileged
package installation by convenience. Prefer user-space tooling. Do not scan or
modify unrelated users' data. A file mode is not isolation from host administrators.

### 2.4 Information-flow and third-party content

Read, retrieve, store in memory, report, publish and export are separate permissions.
Work material may reach the user's authorized GPT/GitHub collaboration surface
where project/source policy permits it; credential bytes are excluded.

Treat external Skills, documents, IM, websites and MCP annotations as untrusted
inputs until assessed for their use. Text-only Skills can still contain prompt
injection. Provenance and review are proportional to effects; do not silently run
installation hooks or commands found in retrieved text.

Secrets live in the protected vault or existing native stores. Avoid secret values
in command arguments, logs, screenshots, prompts and Git. A leaked value requires
containment and owner notification; redacting a later report does not undo leakage.

## 3. Daily operating model

Feishu remains the primary enterprise source; GitHub, native Sessions and records
provide engineering evidence. CloudCLI is the main conversational/control surface;
Feishu App1 delivers reminders, summaries, status and notifications. The Daily
Coordinator is a replaceable logical role, not an immortal transcript or a second planner.

The selected profile prepares source catch-up, Structured Understanding and a preliminary
Requirement delta around 19:xx, then reminds the user at 20:00 Asia/Shanghai. The conversation
handles relevant understanding changes, Requirement changes and selection of 0–2 root
Work Items. These are topics to reconcile, not a three-page wizard. No-decision days can
be brief; absence of a reply is not acceptance and does not freeze already-authorized work.

An accepted Focus may start the same evening. Continuation within its accepted goal,
effects and budget should be automatic when a real continuation receiver/trigger exists.
A morning check is optional catch-up, not an embargo on night execution. Completion may
leave Focus at zero; do not refill from backlog without a separately accepted policy.
A formal Requirement is not itself permission to execute, and new source information is
not automatically a formal Requirement.

Existing unfinished, delegated, waiting, cancelling or uncertain-effect Focus work counts
toward the limit. Parking requires an explicit disposition, useful checkpoint and safe
writer/resource/effect handling, not just an enum change. Do not manufacture Quay work.

Standing delegation is revocable and scoped to projects, effects, data, budgets and
publication targets. It may authorize defined routine work and plan refinement without
repeated prompts; this design does not mint that delegation. Material goals, ownership,
deadlines, external commitments and permission changes use a real accepted decision.
Ordinary supported facts may be agent_reconciled and are never called user_confirmed.

The longstanding Requirements Board is a human-facing document. Accepted Requirement
semantics, work control and execution observations retain their respective owners.
Evidence can be updated without changing accepted goal/AC/priority. Protect direct human
Board edits and reconcile their semantic delta; do not silently override them or treat
unverified edits as grants. No second daily task database is introduced.

See [Daily Work Cycle](docs/daily-work-cycle.md) and the capability repository's
[Requirements/Work Item contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/REQUIREMENTS-WORKITEM-DELIVERY-ARCHITECTURE.md).
Historical measurements in integration-readiness documents do not prove current deployment.

## 4. Structured Understanding

Understanding is a versioned model of the user's role, projects, people,
responsibilities, commitments, constraints, decisions and open questions.

### 4.1 Claims and alignment

A Claim retains subject, assertion, provenance, observed time, applicability,
confidence and its confirmation/conflict/supersession relationship. Records own
confirmed semantic content; work-engine owns associated work/control transitions.
Do not maintain independently editable copies of the same fact in both.

Machine observations may be recorded automatically. Interpreting a message as a
new commitment, changed responsibility, new requirement or strategic decision
requires alignment unless already covered by an explicit delegation.

A material delta ends as accepted, corrected, rejected or deferred with a reason.
Keep conflicting evidence visible until resolved. A human confirmation determines
business intent; it does not make an unobserved deployment a live fact.

### 4.2 Authority is question-specific

```text
business intent       -> explicit user/authorized stakeholder decision
intended design       -> accepted design and project policy
implemented change    -> exact Git/config/artifact revision
current live behavior -> fresh observations and target-scoped verification
historical events     -> original sources and recorded evidence
recall/experience     -> memory, with provenance; not overriding authority
```

Do not use last-write-wins across these categories. Corrections preserve history
and invalidate affected projections/memory references. Do not repeatedly surface
a rejected demand unless material new evidence changes it.

### 4.3 Reconciliation documents

Keep one daily human document and one master index containing daily summaries and
links. Conversation in CloudCLI updates these outputs and an understanding
checkpoint. They are not forms the user must fill or a second truth database.

## 5. Source capture, Follow and Feishu

### 5.1 Two acquisition paths

**IM shallow archive:** capture authorized messages incrementally, including stable
chat/message IDs, sender, timestamps, text, edits, reply/thread relationships and
attachment locators. Blacklists use stable chat/app IDs; names only help discovery.
The Agent may propose an initial blacklist for user correction.

**Followed documents and Wiki subtrees:** maintain metadata/revision inventories
and archive the selected pages or changed parts. Follow is not permission to clone
an entire large knowledge base. New descendants and expensive linked material obey
the declared scope and expansion policy. Uncertainty goes to review.

Use page/cursor checkpoints, overlap windows where needed, stable-ID deduplication
and source revisions. A failed or partial fetch does not advance a completed
coverage watermark or replace the last good snapshot. An empty response may mean
no data, insufficient permission, a wrong scope or a transient error; preserve the
distinction. Store original source references even when a local snapshot is kept.

Directly shared repository links may lead to a user-confirmed inspection. Reading
or cloning for inspection never adopts a repository, creates a Project or creates
a Bridge automatically. A group-chat mention alone does not trigger cloning.

### 5.2 Meetings and incidental speech

Minutes is a separate source domain. Recording, transcript, AI summary and derived
notes form a **MeetingBundle relationship**, not necessarily a physical folder.

The user's support sessions are commonly muted and may record nearby unrelated
conversation. Classify meaningful content with source/context evidence; do not turn
incidental speech into requirements, knowledge, reports or commitments. The known
Marek/Polish-group open-microphone exception is an instance rule bound to actual
source IDs, not a universal name-matching rule. Ambiguity means review, not deletion.

Relevant meeting interpretation enters MyDocs/Understanding for alignment.
Attachments and selected sidecars belong in Drive. Only reusable confirmed knowledge
is published to an appropriate knowledge space. **Do not automatically move every
confirmed meeting note into Wiki.** Original Minutes remains the source.

### 5.3 Semantic roles and physical surfaces

| Surface | Normal role | Not its responsibility |
|---|---|---|
| My Document Library | dynamic work surface: workbench, daily alignment, project docs, formal reports | raw source archive or long-term external mirror library |
| 个人笔记 | curated long-term private knowledge | daily task ledger, reports, migration scratch |
| 公开文档 | owned formal guides we maintain for broader readers | unrelated external source mirrors or automatic Internet-public access |
| 外部文档 | stable FOLLOW_MIRROR / Source Library for external read-only sources | taking over source authority or storing externally-maintained canonical docs |
| External/shared canonical documents | source or scoped maintenance target kept in original location | duplicated editable body in our own space |
| Drive | Asset Plane: recordings, files, screenshots, PDFs/PPTX/XLSX/ZIP, E2E evidence and meeting sidecars | authoritative Board or mandatory Git-blob mirror |
| IM / Minutes | original work information / source events | approved business conclusions merely because it exists |

My Document Library uses the Wiki provider's my_library space and supports
node/subpage organization; Drive uses file/folder organization. Product roles stay
distinct even when an API adapter is shared. Names and locations do not determine
ACLs, ownership or canonical status.

### 5.4 Document Stewardship and canonical content

A lightweight record relates a document to role, audience, canonical source,
stewardship, project/Item, applicability, freshness and locator. Maintenance authority
may cover an external owner's document without permitting move, rename of shared
navigation, ownership change or permission expansion.

Same title or same bytes does not choose a canonical copy. Relate equivalent,
derived and superseded objects, and retain an unresolved canonical choice when the
owner has not decided. Do not promote the copy in our own space by location alone.

For engineering work packets, GitHub normally owns the versioned body. For a
published Feishu-native guide, its Feishu object may own the body. Each document
has one declared authoring authority; other copies are revision-linked projections
or snapshots. A human edit to a projection is a correction to reconcile, not content
to overwrite silently on the next render.

### 5.5 Mutation, publication and cleanliness

Before a cloud write, bind the operation to an exact object and baseline revision.
Afterward read back the target content/location. Cross-space moves additionally
check owner, inherited and explicit sharing, target audience and old entry links;
unchanged explicit collaborators alone do not prove effective access is preserved.

Use a provider revision guard when supported. Otherwise serialize managed writes,
read immediately before/after, and report the remaining race; do not claim atomic
CAS over an API that lacks it. On a concurrent edit, rebase the intended change or
ask about the actual conflict, not replace the entire body blindly.

Record intent before destructive actions. A parent with zero children is not an
empty document: inspect its own body, attachments and external references. Unknown
residual content blocks deletion. Prefer recoverable removal when available.
Migration journals, hashes, probe nodes and cleanup lists stay in local evidence,
not the visible knowledge tree. Historical useful content is not garbage.

Published support knowledge is a **separate project product**. ServerOS supplies
acquisition, provenance, publishing and freshness primitives; Support Bot/KB owns
its own review, release and retrieval eligibility rules.

## 6. Session Fabric

### 6.1 Session identity and native control

One Item may have many focused Sessions. A Session may start unbound, serve general
work, or switch its work context explicitly. Project, Item, Session, native thread
ID, OS process and tmux pane are different identities. A stable Daily Coordinator role
and each Work Item Parent role bind to actual native sessions independently. A new native
transcript may resume the same responsibility; a provider Session is not inferred from
a login/cgroup process tree.

The Session record needs only logical ID, device/runtime, native locator, current
work reference, predecessor/parent, controlling actor and last observed state.
Provider adapters declare which operations they actually support: discover,
inspect, start, send, stream/result, resume, interrupt, stop and remote attach.
An unsupported operation is not emulated by claiming success.

Multiple clients may observe one Session. At most one active control lease sends
mutating input at a time; request IDs deduplicate messages. UI disconnect means
loss of observation, not automatic cancellation. A lost heartbeat is UNKNOWN until
reconciled, not proof the worker is dead.

### 6.2 Bootstrap and context switching

Every enrolled CC/Codex Session gets the thin entry by default. Native instruction
and auto-memory loading remain native; ServerOS cannot promise an actually empty
runtime context.

Current explicit intent outranks a managed Item/worktree identity, which outranks
repository/cwd hints, which outrank recent history. Cwd alone normally identifies
Project/Repository, not Item. Ambiguous resolution stays unbound or asks one focused
question. A topic switch changes logical context visibly; it must not let MarketOps
writes occur inside a LiteLLM worktree because the shell cwd was left there.

A shortlist is advisory and leaves a way to discover more eligible capabilities. Loaded
text cannot be assumed removable from every native runtime. Stop reinjection, explicitly
correct applicability, and use supported compaction or a fresh checkpoint-based Session
when needed. Permission/claim invalidation must reach the actual read/effect boundary.

### 6.3 Checkpoints, history and handoff

Checkpoint at meaningful progress, handoff, compaction when supported, and graceful
completion. Include changed facts, exact revisions, evidence, unresolved effects,
next safe action and pending decisions. Hook failure or abrupt process death may
prevent a final checkpoint: recover from the last durable one and observed effects,
never promise lossless summary on every shutdown.

MetaCC is the preferred session-history/intelligence provider. Native transcripts
remain the raw record; MetaCC indexes/analyzes them, not controls live process
lifetime. Retain history long-term; archival requires user confirmation and deletion
is separate. Scope retrieval to the relevant project/Item/device when appropriate.

Remote attach from a phone/Windows leaves execution on Tokyo. Handoff to Windows
creates or attaches a different native Session for the same work. Before transferring
writer authority, save the source checkpoint, resolve destination paths/abilities,
confirm target acceptance, then transfer the lease. Do not share a writable SQLite
database or copy the entire .claude/.codex profile between devices.

## 7. Memory-Context Fabric

### 7.1 One logical memory world, several sources

| Source | Owns | Does not override |
|---|---|---|
| native current context | immediate reasoning and tool interaction | durable accepted decisions |
| CLAUDE.md / AGENTS.md / native rules | stable repository/runtime instructions | live work state |
| native auto memory | local learned convenience | current authoritative evidence |
| records/checkpoints | confirmed understanding, decisions and continuity | fresh runtime facts with contrary evidence |
| OpenViking-like provider | associative/episodic/procedural retrieval | registry identity, permissions or business authority |
| MetaCC/native transcripts | historical discovery and analysis | live-session ownership |

OpenViking is the selected default shared Memory Provider direction. The target is
one separately supervised service on Tokyo with persistent private storage; local agents
connect locally and approved remote devices connect through an authenticated private
(Tailscale-capable) Binding. It is not a serverosd subsystem and it is not synchronized
between devices through Git. Claude Code and Codex should prefer the official OpenViking
memory integrations when compatible, because they already provide lifecycle recall/capture
and MCP access. Their Skill-catalog injection is disabled for ServerOS profiles:
ability-management/G1 remains the only Ability catalogue and progressive-disclosure owner.

OpenViking may retain derived user/experience memory, but it is never authority for current
business facts, Requirement/work state, permissions or Ability identity. Every high-impact
claim retrieved from it is resolved against the current source/Claims/Views before use.
GitHub stores reviewable/versioned records, designs and suitable knowledge; it does not
mirror the vector index or live OpenViking database. ChatGPT-class consumers that lack an
approved OpenViking connector read the GitHub/records plane instead of forcing public
exposure of the memory service.

### 7.2 Retrieval and lifecycle

Scopes include personal preferences, role, people, Project, Item and Session-derived
experience. Device metadata describes provenance/applicability, not a separate
memory universe. Normal assembly selects a small relevant slice, deduplicates
repeated instructions and keeps provenance visible. Zero matches is valid.

Experience may be recorded automatically within policy. Commitments, responsibility
changes and important project facts become Understanding Deltas. Do not full-sync
native memory, OpenViking and records in both directions.

Corrections and withdrawals propagate to derived indexes/memories using stable
source links and revision/invalidation markers. Deleted/restricted material must
not reappear from embeddings or summaries that escaped the original boundary.
Index refresh and consolidation are asynchronous, not startup work.

When the memory provider is unavailable, work continues from approved records,
checkpoints, code and native history. Preserve failed memory writes for bounded
retry; do not block Item progress or pretend memory was committed.

## 8. Work Fabric

### 8.1 Minimal durable model

Records own accepted semantic intent; Work Engine owns current work/control relationships
and meaningful Run receipts; Agent Control owns trusted delegation. The Board joins them
for human use and is not another independently editable worker/grant authority.

```text
Draft Requirement: cheap sourced proposal; no canonical Work Item yet
Formal Requirement: accepted goal, scope, AC and required completion boundary
Work Item: exactly one canonical delivery-responsibility object per formal Requirement
Activity: optional mutable plan, not a required stage or permanent record
Run: meaningful durable execution/recovery/evidence boundary, not every tool call
Outcome: actual result and accepted evidence, distinct from process exit
```

The implementation name Item may remain for compatibility. Updates in the same Requirement
lineage reconcile the same Work Item; independently acceptable/cancellable goals are split
at B3, not hidden as competing root Items under one Requirement.

Read/grep, small calculations and disposable local checks need not create a Run. Independent
workers, material cost, long/cross-session execution, code/environment changes, real acceptance
or uncertain external effects need appropriate durable relationships and receipts. A single
Run can contain many operations. Routine/non-Item evidence remains legitimate; do not fabricate
a business Requirement simply because an old API requires an Item-bound record.

Preserve actual attempts and corrections. Plans may change without rewriting historical
effects; the absence of a planned step does not mean a required outcome is missing.

### 8.2 Scope, planning and completion

An Item has one owning Project but may touch multiple repos, services, environments,
Sessions and deliverables. Relationships may be sparse: research need not invent a
Service or deployment environment. Plans and Activities can be replaced freely
inside the accepted goal/scope; actual effects remain recorded.

Completion means acceptance verdicts, expected deliverables, sufficient verification,
durable evidence and a readable result. Every durable Item has a concise final
summary; small work need not generate several new documents. A final report does
not itself close the Item.

The required result set is agreed before closure: design, code, candidate, installation,
real-client verification, publication and delivery are included only when this goal requires
them. Completion depth and verification rigor are independent. Parent/assigned validators
accept version- and environment-scoped evidence; child DONE is only a local result. An
unfulfilled required predicate remains incomplete despite substantial partial work. Later
operational drift does not rewrite a correctly recorded historical completion.

Continuation stays with a unique logical owner until completion, safe parking, reconciled
cancellation or explicit supersession/transfer. Checkpoints name outstanding obligations,
actual child/effect state and the next safe action. Unknown effects fence only the affected
operations and prevent unsafe replay; old worker terminal labels alone do not release leases.
Standing monitoring/intake responsibilities are operating policies, not eternal Work Items.

Business closure can be NOT_ADOPTED, SUPERSEDED, CANCELLED or IMPLEMENTED_ELSEWHERE
without pretending technical acceptance or live delivery passed. Record engineering
results and business outcome separately. Reopening is explicit.

### 8.3 Reconciliation is not an authority election

Cards own stable semantic intent; Work Engine owns current lifecycle/control state;
Boards join those with execution observations as rebuildable projections. A stale
board and a newer store showing different Item sets is a migration/reconciliation
finding, not a reason to choose a second state authority.

Classify controlled-with-card, controlled-without-card, record-only and historical
projection-only entries. Preserve historical outcomes and cancellations. Re-enrol only
when current evidence and the legitimate migration path justify it; never import old
ACTIVE labels as current work or resurrect cancelled objectives. Unknown records can
remain visible for reconciliation without occupying the daily focus as invented live
workers. Missing liveness/resource fields require actual owner evidence, not inference
from file presence or a Run terminal label.

Standing delegation extends the existing trusted actor/approval/control boundary,
not a new grant database. A candidate recommendation is not execution admission.
Resolve current project/Item scope, revocation, budgets and resources before effects.

## 9. Idea and requirement discovery

CC helps uncover the user's actual intent, especially for greenfield work: clarify
purpose, gather original company sources, challenge assumptions, compare tradeoffs
and surface decisions before freezing technology.

Research, prototypes and controlled experiments may precede a finished design.
Requirements describe the goal, evidence-backed context, acceptance, exclusions and
expected deliverables at the necessary depth. They do not dictate every method.

GPT is a first-class reasoning/review participant. The user decides material business
choices, priorities, scope expansion and protected operations. Repeatedly asking the
user factual questions available in source records is a failure of evidence gathering.

## 10. Research, decisions and GitHub collaboration

### 10.1 Work Packet and authoring authority

Substantial work normally uses a small packet in hwz-records:

```text
projects/<project>/items/<ITEM>/
  brief.md       need, context, sources, acceptance and exclusions
  design.md      research synthesis, decisions and solution
  execution.md   compact Agent checkpoint, not an endless transcript
  report.md      verification, deliverables, adoption and final result
```

Use existing equivalent filenames through locators; do not rename past Items merely
to match this example. Large research may split into research.md. Small work may use
one document. Implementation remains in the designated code/Bridge repository.

CC prepares grounded context; GPT reads GitHub code/packet, researches, reviews and
writes design material back. CC consumes the revision and continues. GPT may write
owned docs/records main where authorized; code changes still use worktree/branch/PR.

### 10.2 Accepted revisions, not magic status text

READY_FOR_EXECUTION is a presentation signal, not authority. Continuation also checks
accepted goal/acceptance revision, accepted design reference, actor/standing policy
and remaining gates. A random document edit cannot authorize a production action.
An older queued design cannot override a newer accepted one.

GitHub collaboration uses current blob SHA/revision guards and a fresh read before
write. Reconcile concurrent edits; never force-push to solve docs/implementation
coordination. Freeze code inputs per Run; integrate current upstream changes at a
safe boundary. Do not replace another CC thread's worktree or WIP.

### 10.3 Native GPT reasoning and human-mediated ChatGPT

Two paths coexist. Human-mediated ChatGPT uses versioned GitHub work packets.
Unattended bounded GPT reasoning may use an authorized native Codex/API Provider;
this does not mean ServerOS controls the ChatGPT GUI. The 2026-09-22 inspection
successfully exercised a native Codex high-reasoning call using the user's existing
login; a successful call is not proof that every review or the daily loop is live.

The Daily Work Cycle requests a verified model and explicit high reasoning rather
than inheriting a low-latency user default. Design and independent plan-review Sessions
are distinct. A trusted invoker binds the result to scope, source/design/plan/baseline
hashes, model configuration, actual Session and time. Exit 0 without a completed turn
and valid final result is INCOMPLETE, never approval.

A review is not authorization. The existing control plane re-checks current grants,
revocation, artifacts, environment and resource reservations at admission. Important
input changes invalidate affected reviews. Failed or incomplete reviews stop that
branch with evidence instead of silently downgrading models or accepting no answer.

This profile does not impose a universal CC -> GPT -> CC order on other work.

## 11. Execution and Quay

### 11.1 Quay as an Execution Accelerator

Quay is a first-class, bounded execution/delegation provider, not the ServerOS Item
store. Prefer meaningful use for independent research, experiments, repro, E2E,
verification and bounded implementation. Do not create decorative tasks for usage
metrics or daily reports.

Within an approved Item/envelope, CC may delegate without a new question. Native
direct execution remains appropriate when delegation costs more than it helps.
Quay runs on its supported native harness; Codex must use a verified delegation
adapter to a Quay-capable runtime, not assume Claude-specific plugins/subagent APIs
work inside Codex.

```text
Packet: parent work, objective, acceptance subset, minimum context,
        exact code/environment inputs, effect boundary, resource/evidence limits
Quay:   native Goal/Task/worker/gate/audit records and supported execution behavior
Result: native refs, actual artifacts/revisions, verdicts, evidence,
        cost/time/intervention where observed, unresolved issues
```

ServerOS stores relationships and absorbs evidence; it does not mirror the entire
Quay board or translate every Quay status into Item state. Quay done != Item done.
A provider's needs-human is first an execution condition to interpret; the coordinator
may investigate or retry safely within policy rather than automatically page the user.

### 11.2 Avoid two orchestrators

For a delegated slice, choose one owner of worker dispatch/worktree allocation and
one integration owner. If Quay creates worktrees, integrate those through the shared
placement/lease contract instead of also creating a second ServerOS worktree layer.
Only declared, necessary environment material crosses into workers; never copy all
untracked files or the user's vault by default.

Do not install Quay's full methodology into every repository. Use its supported
selective configuration and native gates; do not bypass upstream invariants through
ad-hoc edits. Reuse matching verification receipts instead of rerunning an identical
full suite merely because a second framework owns another checkbox.

CLI, UI, provider and driver revisions are independently observed and pinned to a
tested compatible composition. Reading the same task IDs is not sufficient upgrade
verification. In-flight jobs retain their pinned composition; do not silently follow
whatever plugin cache directory sorts as latest.

## 12. Verification, E2E and Evidence

### 12.1 Minimum sufficient assurance

Verification derives from acceptance, changed surface and risk, not a fixed work
class. A small document edit may need content/link checks; a research prototype may
need genuine browser E2E. Full regression belongs at relevant integration/release
boundaries, not every small edit or report update.

Record target revision, environment/identity, test definition, result and evidence.
Reuse an existing result only when those inputs and the affected dependency surface
still match. A different revision, relevant configuration change, stale live evidence
or discovered defect invalidates the appropriate result, not every test in the world.

Do not chase test-count changes or unrelated skips unless they affect confidence in
the changed surface. Missing test tooling is an environment gap, not PASS and not
proof the patch is defective. Prepare a declared test environment instead of silently
changing the host or substituting prose for runnable tests.

### 12.2 Autonomous E2E

Support Item verification and a branch/release/scenario campaign using the same Run
and evidence model. Campaign is a grouping, not another business lifecycle.

Discover/reuse a suitable isolated environment, resolve tool prerequisites, establish
a known target, choose identity, execute actual journeys and collect assertions,
screenshots, traces and bounded logs. Selection is risk/coverage-driven, not a full
Cartesian product by default. Shared test accounts, browser profiles, ports and data
stores need explicit ownership/leases.

Playwright native execution is a preferred starting point for deterministic tests;
Playwright MCP supports adaptive exploration; OpenCLI or another provider may reuse
a permitted real browser state. These are defaults to measure, not universal speed
rankings or hard-coded executors.

Dedicated test identities are preferred. Real-user identities are available within
explicit target/action scope; their existence does not authorize real sends, deletes,
purchases or production changes. Preserve test data boundaries and clean only owned
fixtures. Never use a fixture, screenshot or HTTP 200 alone as proof of live acceptance.

### 12.3 Evidence-rich human report

Capture meaningful before/after and journey checkpoints, especially failure states.
The report shows acceptance coverage, PASS/FAIL/BLOCKED/NOT_RUN, actual target and
identity class, key screenshots, reproduction and evidence links, plus a clear
conclusion. Screenshots supplement assertions; they are not a substitute.

Keep raw evidence locally under the owning Run. Publish selected/redacted artifacts
to Drive where useful; do not upload secrets, broad production logs or unrelated
personal content. The user may inspect CC's terminal throughout.

## 13. Reporting and publication

Reports derive from checkpoints, observed work, decisions, PR/adoption, verification,
Quay results and user corrections. Avoid rereading all chat history to write prose.

| Intent | Primary reader and form |
|---|---|
| Item final report | self/future maintainer; concise technical closure and honest unknowns |
| Daily report | colleagues/manager; formal, results-first account of actual daily progress |
| Weekly report | colleagues/manager; synthesis across days, not concatenated daily logs |
| Special report | topic/stakeholders; may span several Items |
| Published guide | independent reader; prerequisites, applicability, steps and validation |

Daily reconciliation is not a daily report. Final report is not synonymous with a
special report. Professional writing never converts an unverified result into success.

The daily report includes a substantive Quay section based on evidence: what was
delegated, what resulted, whether useful and any remaining issue. If no useful Quay
work occurred, say so rather than manufacture activity. A report can remain private
while written for colleagues; **generation, publication and sending are separate
effects with separate audience/recipient policy**.

Use a stable report identity by Item or period/audience, a source revision set and
publication receipt. Retries update the same intended object, not duplicate daily
reports. Preserve deliberate human edits through managed sections or revision-aware
reconciliation. Previously issued reports can receive explicit corrections without
erasing their publication history. Ordinary prose should not expose tokens, hashes,
raw event codes or migration paperwork.

## 14. Workspace and placement

### 14.1 One placement authority

ServerOS Space resolves logical role/ownership/lifecycle to a device-bound root or
cloud destination. Instance policy binds concrete paths/tokens. Workspace Skills,
work-engine and Quay consume this contract; they must not define parallel root rules.
A missing binding returns a specific need, not a guessed fallback directory.

A device's already declared temporary workspace can host an authorized experiment
without global catalogue/Project registration. This is a resolved temporary scope,
not an ungoverned directory invented to bypass placement.

### 14.2 Parallel execution

Code changes default to isolated worktrees. Read-only research and small docs work
need isolation only where concurrency or fixed revisions justify it.

Protect repository worktrees, files with concurrent writers, ports, containers, test
DBs, accounts, browser profiles and devices. Leases identify owner, resource and
validity. Expiry permits reconciliation, not immediate destruction of a possibly
live worker. Native OS locks/transactions implement exclusion where available;
cooperative leases must not be described as mandatory isolation from arbitrary tools.

Use one integration writer per branch/target; parallel workers return commits/diffs
for serial integration. A source checkout stays canonical; an instruction to keep
main converged does not authorize editing shared src checkouts directly.

### 14.3 Safe organization

Move only resolved owned objects; retain identity and update locators/references.
Do not infer duplicate content from names or count reductions. Deletion inspects
own body and descendants, captures necessary recovery evidence, journals intent and
verifies independently. Do not remove active WIP or unknown-owned material.

Maps have authored desired policy and timestamped observed projections. Generated
views never manufacture observations or require deletion to make counts green.
Partial inventory is explicit; old good observations remain timestamped, not relabeled
fresh. Legacy locations remain readable until their explicit retirement.

## 15. Project world model

Project is a stable, user-designated work domain, not a repository. It relates to
People/roles, Sources, Repositories, Services, Environments, Items, Knowledge,
credentials by reference, deliverables and runtime evidence.

Person identity is lightweight and reused across projects. Confirmed responsibilities
belong in Understanding; softer communication experience may live in memory with
provenance. Source-to-project and repo-to-context relationships may be many-to-many.

Service is a stable business/operational identity; process/container/endpoint is a
current runtime realization. Environment describes risk, data and target context;
DEV/TEST/STAGING/PROD are customary labels, not an exhaustive enum or a requirement
that every Project have all four.

```text
repository revision -> artifact digest -> Service x Environment
                    -> runtime instance -> observations/evidence
```

Project Home is a logical current view in MyDocs/CloudCLI by default, not a compulsory
Wiki page. It links to authoritative sources, work and reports; publication knowledge
is a separate audience surface.

### 15.1 Bridge repositories and parallel business lines

Bridge is the writable, GitHub-visible engineering surface for collaboration with
GPT. Synchronize against an explicit enterprise/upstream baseline without overwriting
WIP. The enterprise checkout may still serve comparisons and isolated direct tests.
Code-changing company Items deliver an actual PR to the agreed target; a mock PR or
local commit is not the final submission. Submission, merge, adoption and live release
are separate outcomes.

Current identities to preserve in instance/project records:

| Work domain | Repository relationship |
|---|---|
| MarketOps | marketops-research-pipeline is the stated main research/pipeline repo; the active marketops repository-convergence checkout is a protected separate development surface until its owner closes it |
| Support Bot code | ProfessorZhi/litellm-support-bot |
| Support Bot knowledge | ProfessorZhi/troublebuddy-kb-bridge |
| LiteLLM shallow support archive | ProfessorZhi/litellm-support; not the Bot code repo |
| LiteLLM engineering | designated litellm-bridge plus enterprise litellm-fjd validation/delivery relationship |

MarketOps experimentation and Support Bot/KB development remain independent projects
with their own concurrent CC threads. ServerOS supplies reusable infrastructure;
it does not take over their roadmaps, test data, secrets or production decisions.
Hosting/Support Bot answers end users; CC primarily develops and maintains supporting
code, configuration and knowledge.

## 16. Ability Fabric

### 16.1 Shared catalogue and unambiguous terms

**Registry is global; availability is device-bound; attachment is contextual.**

Preserve existing registry identities. In ability-management an Ability record is a
typed artifact identity (type + canonical source/identity); Skill, plugin and MCP
records are not collapsed merely because names match. Capability/Operation describes
the semantic need; an Ability advertises what it provides. Earlier WHAT shorthand
must not erase this distinction.

```text
semantic need / operation
  -> matching registered Abilities / native capabilities
  -> Provider implementation
  -> Binding(device, target, configuration, revision)
  -> current readiness + authorization
  -> Session attachment/use
```

Authorship, registration, distribution and installation are separate. External
sources remain attributed; prefer pinned references to licensed upstream artifacts
rather than copying every third-party implementation into our repository. Signatures
are useful evidence where available, not a demand that every third-party author join
our device-signing scheme. A device signature proves a device claim, not code safety.

### 16.2 Discovery without hijacking native agents

Known native tools may be used directly. Local catalogue search augments discovery;
network acquisition is needed only for missing capabilities. An unfamiliar capability
may be tried in an authorized temporary scope without first expanding the global
semantic vocabulary. Promote repeated useful capability deliberately.

Registration does not imply install, enable, attach, load, trust or permission.
Third-party text needs provenance/prompt-injection review; executable packages,
hooks, MCPs and new network/credential scopes need stronger admission.

Avoid duplicate exposure when a native plugin already supplies a tool. ServerOS's
index may point to it instead of registering another identical MCP server. Hot
attachment is used only if the installed runtime supports it; otherwise checkpoint
and start an appropriate new Session, or use an available safe native/CLI interface.

### 16.3 Global families, device projections and Packs

Keep payload paths flat within their existing type namespace. Domains/families/
clusters are many-to-many navigation metadata. Device projections can emphasize
Windows production observation or Tokyo E2E/Quay without redefining global identity.
No new registry per device and no physical nesting merely to mirror a taxonomy.

Packs describe selected dependencies; Profiles combine model, harness, providers,
Skills, parameters and relevant environment choices. Neither grants permission.
A Session sees only the useful subset; global search remains available.

### 16.4 Experiments and experience

Ordinary Items/Runs support baseline, variants, comparable tasks and an evidence-backed
comparison. Hold conditions stable where practical; record unavoidable confounders,
web freshness and repeated-trial variance. Measure quality, provenance, latency/cost,
failure recovery and human intervention for unattended work. A Skill is retained
when evidence shows value, not simply because a procedure happened once.

Experience is keyed by operation, artifact/profile revision, Binding and environment.
Do not average incompatible devices/contexts into a misleading global success rate.
Stable and candidate Profiles are distinct. Local authorized overrides do not rewrite
the stable one. Promotion is explicit and revisioned; experimentation can continue.

### 16.5 Distribution without catalogue-wide breakage

A release lock identifies the selected artifacts and their transitive dependencies
at reproducible revisions/digests. A harmless addition elsewhere in the global
catalogue must not invalidate a running device profile or trigger a rebuild of every
pack. Relevant dependency/schema changes must still be checked. This is a target
contract requiring an explicit upgrade of any older whole-manifest pin rule, not
permission to skip the currently enforced verifier.

### 16.6 Ability engineering is research-first

The quality goal is not to maximize the number of in-house Skills. Before adding a
significant Ability implementation, research the native runtime, project-native
tools, existing registry, official/vendor solutions and mature open source.

Default preference:

~~~text
USE_NATIVE
-> ADOPT_UPSTREAM
-> WRAP with thin ServerOS boundary/evidence adapter
-> COMPOSE existing providers
-> maintained fork only when justified
-> BUILD only genuine missing glue
~~~

A reusable Skill should teach judgment, ownership and composition. Upstream manuals
remain upstream. Registration preserves original authorship/provenance; it does not
claim the implementation is ours.

Executable/production-capable Abilities require proportionately stronger research:
source/maintenance/licence, compatibility, actual effects, failure modes, observability
and sandbox evidence. A new catalogue name is not justification for new code.

The focused design standard and portfolio live in ProfessorZhi/ability-management:

- design/ABILITY-DESIGN-STANDARD.md
- design/ABILITY-PORTFOLIO.md
- design/AUTOMATED-TESTING-ABILITY.md
- design/PRODUCTION-OPERATIONS-ABILITY.md
- design/REPORTING-PUBLICATION-ABILITY.md
- design/RESEARCH-EVIDENCE-ABILITY.md
- design/BUILD-RELEASE-ABILITY.md
- design/DOCUMENT-STEWARDSHIP-ABILITY.md
- design/TERMINAL-CAPABILITY-PROFILES.md

Those documents guide registration/implementation without becoming a second product
architecture.

G1.4 authors the smallest necessary artifact and exposure changes using the capability
repository's [Ability Authoring Standard](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-AUTHORING-STANDARD.md).
G1.5 evaluates candidates against unchanged responsibility/permission boundaries. This is
a governance route, not a bootstrap requirement for ordinary tasks.

### 16.7 Execution Terminal and device Ability projection

Ability availability is computed for an execution context, not merely a machine:

~~~text
Execution Terminal
= Device
+ Principal
+ Agent Runtime revision
+ current Project / Environment / Target
~~~

The same Windows device can expose different native/plugin capability to Claude Code
and Codex; the same logical production Ability can have Windows and later Tokyo
Bindings without becoming two identities.

A phone is normally a Human Terminal: it can discuss, inspect, approve and read
reports through CloudCLI/Feishu without installing production tooling locally.

Remote Ability use is valid when the destination Binding, data-flow policy, readiness
and operation authorization are explicit and the request/result is durably related to
the same work. It is not native support merely because another device can perform it.

The focused target profiles live in
ProfessorZhi/ability-management/design/TERMINAL-CAPABILITY-PROFILES.md.

### 16.8 Shared decision and search Providers in development

The user-selected development topology is ONE governed Laya service on Tokyo,
consumed by both ServerOS and MarketOps through light clients. Initial deployment
uses CPU, one model process and one resident checkpoint with bounded queue, identity,
per-consumer isolation and OS resource limits. Available whole-host capacity is not
permission to consume it. No model work enters serverosd's tick or session bootstrap.

Laya and hosted Jev implement bounded decision suggestions, not permissions, work
state, test verdicts or factual evidence. Start shadow/advisory per evaluated task,
checkpoint and language; reject insufficient/overlong inputs rather than silently
truncating important constraints. High model confidence is not a safety gate.

Reuse the existing Serper search adapter and protected credential binding; SERP
snippets discover candidate URLs and do not establish final entity/contact facts.
Jev and Serper are external data transfers: local inference privacy does not grant
permission to send private state, raw logs or secrets to either.

MarketOps keeps its own application/MCP and business truth. Development clients use
the same Laya endpoint without importing ServerOS source or duplicating weights.
MarketOps Docker packaging, host-network changes and production Hosting integration
are deferred; endpoint/config boundaries are sufficient preparation for that future.

See [Shared decision/search](docs/shared-decision-search.md) and the owning provider
contract and full realization plan in ability-management/design. Shared reusable
provider code belongs to capability infrastructure, instance bindings to ops, and
business DecisionSpecs/validation to consumers. Do not add one MCP/plugin per vendor
or a universal intelligence gateway merely to make the diagram symmetrical.

## 17. Dynamic Environment and resource lifecycle

Desired Environment comes from references held by Sessions, Items, Routines and
persistent instance policy. Observed Environment comes from real providers/kernel/
filesystem. Ownership and policy determine permitted reconciliation; Effective
Environment describes actual usable capability and gaps, never fabricated reality.

Resolve -> inspect -> admit -> acquire/attach -> verify -> release is a resource
protocol, not a work-method sequence. Bindings record owner, configuration/revision,
health source and dependents. Two consumers sharing a resource prevent its retirement
when only one exits.

A zero reference count makes a resource eligible for scope-aware retirement; it does
not authorize deleting browser state, tasks, databases or human documents. Persistent
provider state survives process restarts. Runtime candidates discovered by an Agent
can be session-only, project-scoped, persistent, external or ignored; discovery alone
never permanently adopts them.

Repairs use bounded attempts/backoff and a known owner. Loss of policy blocks new
risky effects, not harmless read-only reasoning. Do not create a feedback loop where
a failing probe continuously creates replacement processes or alerts.

## 18. Connectivity and devices

### 18.1 Tailnet-first, tunnel-by-exception

Current personal execution topology is Tokyo primary, Windows specialist and phone
human client. Beijing is retired historical/reference material, not a normal fallback.
The generic architecture permits later devices without hard-coded hostnames.

Tailscale is the selected default private-network Provider. SSH is a remote protocol;
tmux preserves a terminal; RDC is on-demand device control; GitHub is asynchronous
collaboration; a future A2A adapter supplies delegation semantics. They are not
interchangeable layers and installing Tailscale does not install an Agent endpoint.

### 18.2 Endpoint publication and verification

Tokyo prefers supervised user-space Tailscale where feasible. Service records bind
a real local endpoint to a private publication method such as Serve. Do not infer
that all localhost ports are reachable just because the node is online; do not infer
that all ingress is impossible just because no TUN exists. Verify the actual path.

Check from the intended Windows/phone client: target DNS/IP, protocol, application
identity, authentication and relevant WebSocket/stream behavior. Account for proxies,
VS Code forwarding and redirects. HTTP 200 or TCP connect alone is not proof of the
intended application path. Distinguish local endpoint, private advertised endpoint,
configured publication and observed reachability.

A 0.0.0.0 listener is wider-bound, not by itself proof of public Internet exposure;
routes, firewall and cloud policy also matter. Prefer authenticated private HTTPS
for human control surfaces where supported. Tailnet membership is not unrestricted
shell authorization.

### 18.3 Operations and exceptions

Tailscaled and required services need an actual supervisor/startup contract, persistent
identity/state, bounded recovery and endpoint verification. User-unit enablement
alone does not prove boot-without-login behavior; verify the allowed host mechanism.
Serve background persistence and tailscaled process supervision are separate.
Do not alter shared-host linger/network policy without admission.

Retire old tunnels only after the replacement path is verified and dependencies are
known, with a controlled rollback path. No mass tunnel deletion and no restart of
unrelated services. Shared gateway publication to a personal tailnet is a separate
scope/ACL decision, not permission implied by gateway maintenance rights.

Special tunnels remain valid for non-enrolled targets, constrained protocols or
approved public ingress. Funnel is default-deny. RDC and Sunlogin remain on-demand
remote-assistance providers; assisting a colleague does not enroll their machine.
Network unavailability does not prevent local work using an approved local binding.

## 19. Runtime configuration and compatibility

Device-local provider settings are not blindly synchronized. Windows CC Switch,
Codex/WorkBuddy configuration, Tokyo endpoints and local paths may legitimately
differ while sharing the same work/capability identities.

Every adapter records tested runtime/version support for session control, metadata
loading, Skill invocation, MCP connection, cancellation and result collection.
Missing support is visible. Do not emit a fictitious Codex plugin manifest merely
because the Claude plugin format exists.

Changes prepare/validate a new revision before activation, preserve old bindings for
in-flight work where needed, and roll back configuration separately from durable
state. Executable revisions are pinned; deliberate updates occur at safe boundaries.
Native auth caches and browser profiles remain native, protected and unsynchronized
unless a scoped supported transfer is explicitly required.

## 20. Human interfaces

### 20.1 CloudCLI and terminal

Preserve CloudCLI's native project/session/chat/files/shell/Git experience. Add a
ServerOS contextual integration using supported plugin surfaces; do not require a
fork or global homepage rewrite. The user mainly discusses work in CloudCLI or CC's
terminal and reads understanding, requirements, design and final reports.

The integration links relevant work, sessions, pending decisions, environment,
Quay inspection and evidence. It does not replicate an entire Quay board or expose
Activity/Run/lease bureaucracy by default. Quay Dashboard is a secondary drill-down.
Phone access is a real interaction path, not merely a monitoring screenshot.

### 20.2 Feishu cards and clean presentation

Use the existing presentation model and sender; do not add a notification platform.
The six communication intents (Status, Alert, Work progress, Reconciliation, Decision,
Report) are purposes, not six authorities or resident services.

Daily Status Card means 每日状态播报卡. A Status Update refreshes that same card. The
personal profile has one card per CN-Mainland business day, updated at 08:00–20:00 hourly
in Asia/Shanghai, including officially declared makeup workdays. Latest status is fully
readable; prior updates are compact rows in collapsed 今日历史. No routine card on days off.

Status answers current health, relevant resources/dependencies, observation sufficiency,
known impact, responsibility and changes since the last update. No human action does not
mean healthy; unknown does not mean zero; historical/diagnostic counts do not prove active
anomalies either. Preserve known failures when other observations are missing. A healthy
verdict requires sufficient relevant observation, not merely an empty actionable alert list.

An Incident Card tracks one meaningful episode across hours/days. New interruption follows
impact and actual attention needs, not every sample. Patch ongoing, acknowledged, recovering
and closed episodes; a snooze does not change the condition or grant permission. A new alert
send with no eligible episode is suppressed, not rendered as an empty green alert. Scheduled
healthy Status updates remain valid. These two delivery paths must not be inferred from
visual appearance; retain card purpose, message identity and payload/delivery evidence.

P1 may interrupt 24×7; P2 new interruptions use the business window; P3 is diagnostic/review.
Full rules, unknown calendar handling, recovery and scope are in
[Monitoring Scope/Recovery/Cards](docs/MONITORING-SCOPE-RECOVERY-CARDS.md).

### 20.3 Actions are not credentials

A card button identifies an intended operation and expected state; its callback
reauthenticates the actor, checks scope/expiry/current state, records a decision and
then executes only if authorized. Old/replayed cards cannot repeat a cutover or cloud
write. For human-executed production, the button opens instructions/context; it is
not a hidden Agent execution grant.

Feishu interaction is optional beyond sources/notifications/reports. A full terminal
inside Feishu is not required. A unavailable renderer may show a safe deterministic
summary; LLM unavailability must not suppress an important machine alert.

## 21. Node Runtime — serverosd

Exactly one resident ServerOS core daemon is active per managed node/runtime scope
in normal operation. Provider processes may be long-lived separately.

serverosd owns mechanical local observations, attribution, admission at its boundaries,
managed-environment references, bounded reconciliation, local IPC and self-health.
It does not own business planning, semantic memory extraction, report writing,
Feishu workflows, Quay worker planning or a second process supervisor.

Observe self, user, host, managed runtime, filesystem, quota/inodes and shared memory
where available. Every result carries source, observed time, freshness and scope.
A profile states intent; live observation states reality. Permission-denied or absent
probes remain unavailable/unknown, not invented healthy values.

Use bounded/cached sampling, timeouts and backpressure. Observe CPU/memory/queue age
of the daemon itself. Production and test instances need separate sockets and state;
a lifetime lock prevents accidental duplicate writers for one scope. Stale PID files
alone do not justify killing a process.

The OS supervisor owns process lifetime; serverosd owns whether a declared resource
is desired and healthy. Avoid competing restart loops. An Agent diagnosis runs in a
bounded Maintenance Session, not inside the daemon. Generic on-demand readonly CLI
inspection remains possible if daemon observation is unavailable, with provenance.

## The resident surface (frozen)

*Unnumbered on purpose.* Every other section here is a design that changes; this
one is a refusal that is supposed to be hard to change, and a numbered section
sits in a document whose whole convention is that numbers move.

**Exactly one thing is resident, and it is `serverosd`.**

Here “one thing” refers to the ServerOS semantic Node Core per managed scope, not
a ban on native supervisors, CloudCLI, Tailscale, shared Providers or currently
executing Agents. Finite scheduled operations may wake an existing Parent after
a result arrives; this is continuation plumbing, not a resident business planner.

This is frozen because the pressure against it is steady and each step is
individually reasonable. Discovery feels like it deserves a supervisor; memory
feels like it deserves a writer; coordination feels like it deserves a
scheduler. Each of those is a *semantic service* — a resolver, a schema, a
refusal rule — and none of them is a process. A service that answers in-process
becomes a question that can be unanswered the moment it is given a process, and
"the answer is unavailable" is the one output this product exists to make
impossible.

```text
serverosd          resident, supervised by systemd (§20)
serverosctl        on demand — the operator surface
serveros-*-mcp     on demand — spawned per Agent session, owned by that session

refused as resident units, by name:
  abilityd                 discovery is a resolver, not a supervisor
  memoryd                  the ledger is written by the operation that needs it
  coordinationd            bindings are a durable record, not a running thing
  resource-governanced     observation is a tick, and admission is a policy read
  feishud                  Feishu is a Provider, reached when a command needs it
```

Each refused name is the obvious way to make one of the four semantic services
into a process. Refusing them is not a claim that a second resident unit could
never be justified — it is that the justification would have to survive this
paragraph, in a diff, with a name.

**What may be a process is a Provider or a Managed Runtime, and both live
outside the core.** BotMux, the Codex App Server, Docker, Feishu and OpenViking
are reached through a module under `mcp/` or `abilities/`; the core's loader
closure may not reach any of them, and `serverosd` starts with every one of
them absent. `tests/test_core_freeze.py` asserts the surface is exactly the
three rows above, that no refused name has appeared on it, and that none of
these outer systems is in the core's closure. `tests/test_core_ability_boundary.py`
asserts the closure itself.

**This section was deleted from the document and is restored here on
2026-09-22.** The gate that holds it — `test_the_refusal_is_written_down`, whose
own failure message is "the freeze was deleted rather than changed" — stayed in
the tree and stayed red while the text was gone. Nothing in the refusal had
become false: `test_no_such_daemon_exists` never stopped passing, and no refused
name has appeared since. What went missing was the paragraph a person reads
before proposing the sixth one, which is the only thing the section was ever
for. The subsection above is where the refusal lives; this one is why it is
still refused.

## 22. Core composition and component boundaries

The product's two canonical Core members remain **serveros + ability-management**.
This is source/composition membership, not two resident daemons.

| Owner | Authority |
|---|---|
| serveros | product architecture, composition, bootstrap, Context/Space/Relations contracts and node runtime |
| ability-management | typed Ability/Capability registry, provenance/revisions, reusable Skill source and distribution |
| work-engine | durable work/control transitions, Run and effect receipts, Board and work projections |
| hwz-records | approved understanding, stable work intent, designs, reports and human semantic records |
| hwz-ops | instance roots, topology, concrete targets/bindings, local policy and observed maps |
| native providers | their actual sessions, task stores, cloud bodies, runtime facts and supported effects |

One public serverosctl entry dispatches to the owning component; two repositories
must not compete for the same installed executable. work-engine may consume a
placement interface without implementing a second placement authority or importing
the entire product back into itself. CLI and MCP adapters share the owning use-case
contract rather than duplicate business decisions.

ServerOS plugin packages the bootstrap and selected reusable capabilities. A local
bootstrap Skill may live in serveros; reusable Skill canonical sources remain in
ability-management. Composition references external plugins rather than pretending
they are authored here. Knowledge, session data, credentials and live maps are not
plugin payloads. Package presence is not runtime availability.

## 23. Internal planes and effect protocol

The internal planes remain Node Runtime, Ability, Memory-Context and Coordination.
They partition questions, not create new services. Use existing Python/SQLite/files
and OS scheduling where sufficient; no new distributed database or message broker
is required by this architecture.

### 23.1 Shared boundary contract

An effectful request needs a stable request/action ID, actor, target, operation,
work reference when any, input revisions, authorization/envelope reference and
expected state where concurrency matters. A result reports acceptance vs completion,
actual effect, outputs, evidence, retryability and unresolved uncertainty.

An accepted asynchronous job is not completed work. A command exit code is not the
business verdict. Check JSON/tool error envelopes as well as transport/exit status.
An unavailable provider never returns fabricated empty success.

### 23.2 Retry, cancellation and crash recovery

Persist intent before a risky external effect and persist its receipt after observed
completion. Deduplicate by request/action ID and the provider's native idempotency
feature when available. Where the remote service lacks it, reconcile actual state
before retry. Timeout-after-send means **effect unknown**, not safe-to-repeat.

Cancellation distinguishes request accepted from worker actually stopped. Do not
release an exclusive resource until stop/relinquish is observed. Reconcile orphaned
workers with exact ownership and runtime evidence.

There is no global transaction across GitHub, Feishu, Git and local state. Persist
partial progress and repair each boundary. An outbox/receipt table in the existing
owning store is enough; do not add an event platform for conceptual neatness.

### 23.3 Authoring and caches

Content edits and control-state changes use version guards at the real authority.
Views carry source revisions and freshness. A stale projection can inform reading
but cannot authorize a fresh risky write. Derived rendering failure leaves the
source intact and queues a bounded refresh, not a second competing state database.

## 24. Storage and authority

Physical roots are role/lifecycle based and bound per device. Do not add a duplicate
workspace/projects symlink forest or require identical Windows/Linux paths.

```text
workspace/
  src/           canonical/Bridge/tool checkouts
  worktrees/     isolated work, governed by actual repo/Item identity
  test-envs/     environment definitions and declared fixtures
  ops/           instance governance
  records/       structured understanding and work packets
  knowledge/     reusable local knowledge/index material
artifacts/
  work-items/    Run outputs and verification evidence
  ops/           operational and migration evidence
  repro/         reproducibility evidence
  serveros-releases/  installed/build/release evidence where bound
data/            large source archives and provider bulk data
state/           operational persistence, cursors, receipts and memory state
runtime-assets/  installed managed revisions
vault/           protected secrets or locator to existing approved secret store
logs/ downloads/ caches/ tmp/ envs/ toolchains/ software/
```

Examples are not instructions to move existing roots. Persistent state is not cache.
Keep SQLite local to its owning service; other devices use an authenticated interface
or durable handoff, not network-share writes. Simple known-state snapshots/recovery
are required; active-active multi-node disaster machinery is not.

### 24.1 Local, GitHub and Feishu placement

| Material | Normal authority and publication |
|---|---|
| source code | designated Git repository; not copied to Feishu |
| brief/design/execution/report packet | records/GitHub; MyDocs may render selected human views |
| understanding | confirmed records; MyDocs/CloudCLI current projection |
| raw IM/Minutes snapshots and large logs | protected source archive; not automatically published |
| E2E screenshots/traces | local artifact; selected safe files to Drive |
| readable Item result | one logical report with revision-linked GitHub/MyDocs renderings |
| daily/weekly/special report | formal MyDocs reporting surface, recipient delivery separately governed |
| personal reusable method | Personal Knowledge |
| maintained broad-audience guide | 公开文档 for owned guides, or the original shared canonical location |
| credentials/native auth state | vault/native store only |

### 24.2 Desired human navigation

```text
My Document Library
  工作台
  每日对齐 / 总览 / 年月 / 当日文档
  项目 / <Project> / 工作台、结构化认识、需求、报告
  汇报 / 日报 / 年月
       / 周报 / 年
       / 专项汇报
  Inbox
  归档  (useful historical snapshots only)

个人笔记: curated private long-term topics; no operational scratch archive
公开文档: owned reader-facing formal guides; preserve per-document style profiles
外部文档: read-only FOLLOW_MIRROR / Source Library grouped by Feishu source-space/person and network publisher
Drive: 收件箱 / 工作资料 / 临时 / 归档
  工作资料 / <Project or 通用工作> / 输入、会议、测试与 E2E、交付 as needed
```

An existing Item report bundle may contain its final report and supporting reports.
Do not create near-duplicate sibling pages to satisfy a naming template. Create
folders/pages only when real content or a real entrypoint needs them. File identity,
semantic role and links survive relocation; exact tokens belong to instance records.

## 25. Build, release, deploy and rollback

### 25.1 Artifact-centered delivery

```text
source revisions -> reproducible artifact + selected dependency lock/digests
                 -> installed revision -> actual running revision -> evidence
```

Code/config changes share provenance requirements: before, after, reason, owner,
Item/scope, approval when needed and verification. Ordinary build capability is
independent from a specialist image-build Skill. No arbitrary toolchain installation
or registry edits merely to prove a concept.

A release combines exact compatible code, abilities and adapters. Availability is
proved by fetching pinned content from a durable permitted source, not by requiring
all repositories to have identically named branches. Mutable distribution branches
need captured artifact digests/revisions. Published, installed and live are separate.

### 25.2 Promotion and recovery

Prepare a candidate separately, test the relevant contracts, then activate the managed
revision at a safe boundary. Existing Runs retain their inputs. Runtime rollback
must not rewind business records, receipts or secrets. State migration compatibility
and recovery must be checked before activation; do not claim rollback merely because
a symlink can point to an old binary.

TEST execution and bounded rollback are autonomous inside scope. General production
follows its target policy. LiteLLM/shared-gateway user-executed rules in section 2
remain stricter. Destructive/data migrations require explicit impact, preconditions,
recovery feasibility and authorization. Observation windows are target/risk-specific;
never claim LIVE solely because the container started.

### 25.3 Closure

Code work has an actual PR deliverable. PR rejection or another implementation is a
recorded business result, not manufactured adoption. Check linked docs/knowledge for
real impact; do not rewrite every Wiki because one file changed. Release installers
and uninstallers act only on owned, receipt-tracked files/resources and preserve
persistent/native state.

## 26. Incidents and autonomous background work

A short owned incident can be observed, repaired under policy and reported without a
new Item. A durable fix becomes a sourced Candidate for prioritization. New autonomous
work must not silently rewrite the user's priorities.

Approved work may continue while the user is away. Inside the same scope the Agent
may change method, experiment, test, delegate, repair ordinary transient failures and
prepare reports. Pause the affected boundary for changed goal/acceptance, new
privilege/target, destructive effects or material unresolved decisions.

Use bounded retry/backoff, no-progress detection, concurrency/delegation limits and
cost visibility. Do not hard-code a tiny budget for every important task; do not allow
runaway work. A cancelled/paused Item stops new dispatch but existing effects still
need reconciliation. Other independent approved Items continue.

Before asking a factual question, search the relevant code, records and observed
state. UNKNOWN with a feasible investigation is not automatically HUMAN_REQUIRED.
On escalation, give the smallest decision, recommended option, consequence and
checkpoint. Do not deliver a screen of raw failure codes.

## 27. Routines and scheduling

A Routine has objective, trigger/timezone, scoped grant, selected execution profile,
output/receipt contract and continuation policy. The user-selected Daily Work Cycle
composes existing information, work, research, testing and reporting abilities; it
is neither a new workflowd nor a new authoritative state machine.

Use one authorized scheduler per trigger to start finite workers. A ServerOS timer
and Quay routine must not independently dispatch the same job. The existing work
store owns business leases/cursors/receipts; component receipts describe effects,
not a second version of Item status. The old daily workbench-render unit alone does
not update understanding, select work or implement requirements.

Coalesce missed triggers, preserve checkpoints, and reconcile unknown effects before
retrying. Do not release resources solely because a Run says cancelled. Bound each
phase's time, output, attempts and cumulative costs; stop on real failures without
hiding them. Model IO does not enter serverosd's tick or startup requirements.

Optional OpenViking, Windows-only execution and production access gate only the
steps that need them. Tokyo work can continue from records/checkpoints and existing
tools. Publication without proven conflict protection must not overwrite shared
human-authored text. Daily source changes are evidence, not automatic permission
for new commitments or wider collection.

Normal operation avoids repeated approvals inside the accepted envelope. Human
attention is reserved for material intent/policy conflicts, incomplete required
reviews, product verification failures and protected effects. Production cutover
and rollback remain human-executed under current policy.

See the Daily Work Cycle contract for selection, review and end-to-end activation
criteria. Neither code helpers, green fixtures nor an installed timer alone prove
that unattended business delivery is live.

## 28. Ability families and product coverage

These clusters cover the intended work, not mandatory packages or stages. Implement
using existing abilities where possible; register gaps honestly rather than creating
empty Skills to make a coverage table green.

| A — Agent Working Infrastructure | Responsibility |
|---|---|
| Compliance and credentials | effective policy, target-scoped authority, safe information flow |
| Runtime and resource | node facts, dynamic desired environment, supervised providers |
| Session and handoff | native sessions, checkpoints, observation/control ownership |
| Context and memory | intent resolution, bounded retrieval, native-memory coordination |
| Ability management | global catalogue, provenance, discovery, selective distribution |
| Workspace and Git | correct placement, worktrees, concurrency, integration and PR |
| Tools/build/test environments | resolve and prepare actual dependencies and fixtures |
| Verification and E2E | acceptance-derived tests, independent evidence, readable reports |
| Connectivity and remote devices | Tailscale/Serve, SSH/RDC, Windows specialty access |
| Documentation engineering | small-change guidance, maintained guides, links and publication |
| Release and operations | reproducible builds, compatible promotion, observation and recovery |

| B — Information-to-Delivery | Responsibility |
|---|---|
| Source acquisition/Follow | Feishu IM plus selected docs/Wiki/Minutes and referenced engineering sources |
| Understanding/alignment | project/role/person model, conflict resolution and user feedback |
| Board and requirement discovery | coarse candidates, daily focus, elicitation and requirement writing |
| Research and decisions | original evidence, comparison/experiments, GPT review and design |
| Execution and delegation | native CC/Codex plus bounded Quay contribution |
| Knowledge/document stewardship | private/published/external content maintenance within authority |
| Reporting/communication | Item final, daily, weekly, special reports and audience-aware delivery |
| Routines and improvement | unattended approved objectives and evidence-backed enhancement proposals |

E2E, research and communication are cross-cutting reusable capabilities. Reusing a
cluster in both families does not create two implementations. Family membership is
metadata and views, not hard-coded renderer membership inside Core.

## 29. Claude Code / Codex and provider compatibility

One work world, runtime-specific adapters. Share identity, intent, evidence, policy,
placement and memory scopes. Do not require identical plugin packaging, hooks, MCP
hot-load behavior, native memory paths or subagent APIs.

For each runtime revision, test the actual entrypoints and report supported features.
Claude-specific Quay execution may be consumed through a compatible delegated CC
runtime; it is not automatically a native Codex capability. Public provider docs are
inputs to adapter design, not proof that the user's installed version has every
feature.

Native tools remain usable when an optional memory/search/UI provider is down. When
an authorization boundary cannot be enforced or a required target cannot be observed,
block the risky effect specifically, not all reasoning or read-only work.

## 30. Non-goals

Do not build another general workflow/BPM engine, multi-tenant SaaS platform,
universal registry, distributed database, VPN protocol, secret-management product,
container orchestrator, generic supervisor, remote shell or planner above CC.
Do not replace GitHub, Feishu, CloudCLI, MetaCC, OpenViking or Quay where an adapter
is enough. Do not require active-active multi-node recovery for this personal setup.

Do not preload the catalogue, mirror every native state store, expose migration
scratch to humans, invent business work, copy all historical sources to every device,
or turn validation into the main workload.

## 31. Architecture change and conformance test

A change must identify its real user failure, owner, smallest effective mechanism,
non-interference boundary, fast-path cost and unavailable/degraded behavior. Removing
an optional recipe/provider must not destroy work identity or evidence.

The following are product-level acceptance scenarios, not requirements to run every
suite on every edit:

| Scenario | Required behavior |
|---|---|
| Fresh Session, vague prompt | thin bootstrap, no invented Item; resolve or clarify narrowly |
| Prompt contradicts cwd | correct topic, safe destination, no wrong-repo writes |
| Research -> experiment -> redesign | same work identity, freely revised plan, retained failed evidence |
| Memory/provider unavailable | explicit degradation; continue from durable sources where safe |
| New external Skill/MCP | native discovery preserved, provenance/effect policy, no implicit trust |
| Windows-only production access | correct device/target, scoped readonly observation; cutover remains human |
| Parallel workers | independent workspaces/resources and one safe integration writer |
| Quay returns done | result/evidence absorbed; parent acceptance assessed independently |
| Process dies after cloud write | reconcile actual effect; no duplicate publish or blind retry |
| Concurrent GPT/CC/human edits | revision-aware reconciliation, no lost update or force overwrite |
| Garbage meeting transcript | excluded from understanding/work; no unsupported destructive cleanup |
| Partial Follow inventory | honest coverage/cursor, no fabricated fresh/full snapshot |
| E2E with real identity | scoped effects, genuine target evidence, useful safe screenshots |
| Report generation | formal factual daily/weekly prose, real Quay contribution, no duplicate send |
| Tailnet node online | endpoint-specific remote verification, not automatic all-port readiness |
| Runtime restart | one writer per scope, preserved durable state, no restart storm |
| Routine overdue or cancelled | no duplicate backlog launch; persisted continuation and effect accounting |
| Catalogue unrelated change | current selected composition remains reproducible and usable |
| Human surfaces | current useful documents, no probe/journal construction debris |
| Business projects concurrent | MarketOps and Support Bot/KB work remains untouched by ServerOS governance |

Failures are assigned to the owning contract. Structural docs checks run on docs
changes; affected code tests run on code changes; full release/live scenarios run at
appropriate integration/promotion boundaries. No false equivalence between these.

## 32. Current design status

This baseline closes the product-level choices for the current scope: small Core,
native-first context/memory/capabilities, flexible work and experiments, safe shared
execution, cross-device connectivity, Feishu/document stewardship, Quay, reporting
and unattended routines.

The remaining task is implementation/conformance against these contracts, not another
unbounded architecture questionnaire. Unknown installed capabilities, credentials,
source IDs, environment facts and deployment permissions remain **instance facts to
verify**, never invented architecture constants.

Earlier detailed specs and discovery notes must be interpreted with this baseline.
When a legacy API or implementation conflicts, record the exact compatibility gap and
migrate that interface deliberately. Do not satisfy this architecture by relabeling
old tests, silently weakening a check, or claiming a doc edit deployed functionality.

A separate review record identifies inspected sources, concrete corrected defects and
remaining implementation verification. Future changes refine this baseline from real
work evidence, with one authority and a proportionate change surface.


## Product Ability Ownership & Operating Model · 2026-09-23 addendum

The 21 G/A/B entries are stable **semantic product responsibilities**, not a requirement for 21 physical Skills. Their realizations may be Core code, Skills, specialist Skills, MCPs, Providers, Plugins or native tools, selected per terminal/runtime. The complete five-layer product tree is owned by ability-management [PRODUCT-ABILITY-TREE](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-ABILITY-TREE.md); artifact discovery remains a separate generated view.

Scheduled daily work is a **Profile that composes Abilities**, not a new workflow Ability or daemon. OS/authorized scheduler wakes finite routines; B1 owns source capture, B2 semantic understanding, A2 invalidation/recall, B10 daily human reconciliation, B3/B4 requirement/board/focus, A1 session preparation, A3 workspace preparation and A4 runtime readiness. Details and the recommended instance cadence are in [Ability Operating Model](docs/ABILITY-OPERATING-MODEL.md).

`serverosd` stays the single resident Node Core: runtime/resource facts, IPC, Agent Control durable mechanical state, leases/effects, bounded reconciliation and its own observable health. It does **not** become a Feishu crawler, semantic reader, business scheduler, Session archive, Workspace manager or self-improvement daemon.

Ability self-improvement is **G1.5 Ability Evaluation & Evolution**, a sub-ability of governance rather than a 22nd top-level Ability. It compares stable Responsibility Contracts with real outcomes/evals, uses MetaCC/A7/human feedback as evidence, proposes the smallest realization change and promotes only after isolated and fresh-consumer verification. It cannot rewrite its own responsibility, authorization or safety boundary to make a failing eval pass.


## G1 Core Governance and Ability Card semantics · 2026-09-24 addendum

G1 is the **Core Governance / Capability Supply Chain** for ServerOS. It is not A0 and not part of A1; A1 remains Session Continuity. G1 owns product-capability modelling, external/internal sourcing, artifact identity/provenance, adoption, distribution, installation/runtime resolution, evaluation/evolution and retirement.

The product vocabulary is fixed as:

```text
Product Ability / Sub-Ability
→ Capability Operation
→ Ability Artifact
→ Distribution
→ Materialization
→ Binding
```

A Skill/MCP/Plugin/Provider is an Ability Artifact realization, not a peer to A1/B1 product responsibilities.

Every managed Artifact has a generated Ability Card. Card shows product-role mapping, what/when/not-for, applicability gates/signals, provenance/author/maintainer/publisher, license, adoption reason/status, current Revision/Content Digest, known limits/evaluation and current runtime projection. Card is not a second machine authority.

Revision Content Digest remains required for exact artifact/materialization matching, package locks, drift and rollback, but digest does not establish identity, authorship, trust, authorization or business correctness. A reference-only Card digest must never be presented as the upstream source artifact digest.

Author/provenance and Device/runtime identity are separate authority domains. Device/principal/runtime observations belong to A4/A5 Runtime/Fleet and are joined into Card runtime projections; authors do not own devices merely because a historical registry schema nested them.

Detailed semantics and compatibility migration are owned by ability-management [Ability Card & Registry Contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CARD-REGISTRY-CONTRACT.md). `serverosd` consumes only the small current registry/profile/runtime projection needed for low-latency resolution; it does not crawl GitHub/marketplaces, review licenses, clone candidates, run external evaluations, build packages or self-upgrade inside its resident tick.


## Feishu information surfaces and three-surface continuity · 2026-09-24 addendum

A live read-only Feishu CLI inventory on 2026-09-24 confirmed the current human surfaces:

- My Document Library already contains workbench/alignment/project/report trees;
- `个人笔记` is the curated private knowledge space;
- the former `配置文档` stable space is now live as `公开文档` and contains the Chinese/English Codex guides;
- `外部文档` already exists as private team space `7688789520019360968`; it currently has only a revision-1 default-template `首页`, so the space exists but the Source Library/FOLLOW_MIRROR integration is **not yet live**;
- Drive already has `收件箱 / 工作资料 / 临时 / 归档` and real meeting/source assets.

The final document relationships are:

```text
FOLLOW_MIRROR
  external read-only source
  -> original source stays authoritative
  -> stable mirror in 外部文档
  -> records/Git preserve source+mirror revisions

EXTERNAL_MAINTAINED
  shared/external canonical document that we help maintain
  -> update original document in authorized scope
  -> records/Git preserve revision/history
  -> no second editable body in our own cloud

OWNED
  -> private knowledge: 个人笔记
  -> formal broad-reader guides: 公开文档
  -> formal work reports: My Document Library/汇报
```

Textual managed documents use **Feishu ↔ local hwz-records ↔ private GitHub** continuity. Local checkout and GitHub are two materializations of the same Records authority, not competing writers. Each logical body declares its actual body authority and sync/write policy; no general mtime-based three-master sync is allowed.

Drive is the Asset Plane. Large/binary MP3, image, PDF, PPTX/XLSX, ZIP, installer, trace and similar bytes normally remain in Drive or controlled local archive; Records/Git preserve identity, relation, MIME/size, content hash, locator and retention. IM is already a three-surface capture path through Feishu IM → records → private GitHub and is not copied into 外部文档.

Meeting recording, Miaoji/transcript, auto-minutes and attachments are a MeetingBundle over Drive/source objects. Raw/automatic meeting materials do not automatically enter MyDocs; only a useful curated human note may be created there. Remote-control support sessions treat unattributed/background audio as untrusted for business semantics.

Detailed directory, style-profile, Records layout and B1/B8/B9 ownership are canonicalized in ability-management [Feishu Information Surfaces & Three-Surface Sync Architecture](https://github.com/ProfessorZhi/ability-management/blob/main/design/FEISHU-INFORMATION-SYNC-ARCHITECTURE.md).


## Monitoring product model · 2026-09-24 addendum

Monitoring has three human surfaces only: **Current Status Card**, **Incident Card**, and on-demand **Diagnostic View**. The status card shows the user's own CPU/RAM/quota, host CPU/RAM, root and data filesystem capacity, and a small set of real infrastructure dependencies. Incident cards represent real failure episodes; process hygiene and historical alert ledgers do not page the user.

The default Tokyo infrastructure set is resolved from actual bindings/dependencies rather than container names. Current important examples include ServerOS Core, Tailscale, Shared Laya, Shared LiteLLM Gateway and any currently required remote/browser/project runtime. ServerOS Core is critical; Tailscale peers are checked only when a specific peer/endpoint is expected; Laya availability is operational health while answer/language quality belongs to evaluation; the shared gateway is monitored as one logical service across app/readiness/database/dependencies.

Resource display and resource alerts are separate. CPU/RAM are always visible, but high single samples or per-process usage do not become incidents without sustained pressure/impact. Disk/quota use role-specific percentage plus absolute remaining capacity. Root /, data /data, and the user's data quota are separate first-class numbers. Same-filesystem roles are deduplicated.

Notifications are episode/transition driven: NEW, ESCALATED, REOPENED or materially changed impact/action may notify; STILL_OPEN with only age increasing does not. One episode maps to one Feishu card and is updated in place. The rolling status card is also patched in place rather than adding an hourly history message; observation history remains in durable records.

Canonical details and current live gaps are in [Monitoring Product Architecture](docs/MONITORING-PRODUCT-ARCHITECTURE.md).


## Understanding, Memory and Daily Reconciliation · 2026-09-24 addendum

Structured Understanding uses **Atomic Claims + Current Views**. Claims carry source/revision/support/confirmation/correction semantics; `people/projects/topics/responsibilities` are concise current human views, not history archives. Decisions, meetings, requirements and work states do not become extra understanding directories.

The reader side expands across IM spans, exact Doc/Wiki revisions, qualified Meeting/Miaoji material and Web snapshots; the existing semantic writer/receipt/claim-recall path remains the single semantic commit mechanism.

Daily Reconciliation is a B10 human surface with four first-level buckets: **automatic updates, decisions needed, conflicts, understanding gaps**. It is not a raw-message review and not the daily report. MyDocs `每日对齐` is a projection of this semantic reconciliation, not a second authority.

Ordinary observed facts/supported inferences may be agent-reconciled inside standing delegation. Goal, responsible party, deadline, external commitment and permission changes require real human acceptance. Source invalidation may immediately make a claim ineligible for current recall; it does not by itself cancel a business decision.

Memory remains federated retrieval, not authority: current records/source first, applicable history second, MetaCC/native session history after that, optional vector retrieval last. Provider lag cannot revive stale claims.

Detailed composition and current implementation gaps are in [Understanding, Memory & Daily Reconciliation](docs/UNDERSTANDING-MEMORY-RECONCILIATION.md) and the ability-management canonical contract.


### Session hygiene and workflow incidents

Monitoring must not confuse OS login sessions with A1 Agent Sessions. cgroup/loginctl session identity is transport/login provenance, not proof of a Claude/Codex session. Long-lived/background/resumable Agent sessions and IDE/tmux helpers are normal unless they violate an explicit lifecycle, hold conflicting resources, create unknown effects, or cause real capacity/Work Item impact.

Process/session hygiene is diagnostic by default. User-facing workflow incidents are restricted to current Active Focus delivery integrity: lost Parent continuation without checkpoint/receiver, conflicting writer/lease, commit-unknown effects, required child output that cannot be accepted and blocks the critical path, unexpected A7 acceptance failure requiring a stop, invalidated accepted plan still about to execute, or required publication/delivery repeatedly failing.

Current Status uses one rolling Feishu card per local day; incident episodes use one card for their entire lifecycle. Details are canonicalized in Monitoring Product Architecture.


### Shared-node alert ownership

Tokyo is shared. Monitoring may observe cross-user process facts, but personal Human Control surfaces are scoped to the current principal. They show the principal's own actionable objects, shared infrastructure and device-wide pressure; another user's WIP is not turned into the current user's cleanup/confirmation task.

OS login sessions are provenance containers, not Agent Sessions or Work Item owners. Incident subjects resolve to the smallest actionable object: logical service, filesystem/quota, Work Item/Run, runtime instance or verified Agent Session.

A runtime that outlives a known terminal Work Item/Run is a known-owner lifecycle drift, not an orphan. Before any attention escalation or cleanup, ServerOS revalidates owner state, active lease, remaining consumer, write/socket scope, resource impact and effect safety. A historical PID or alert is never sufficient authorization to stop it.

Daily status is one rolling Feishu card per local day; each incident is one card per episode. Sampling updates cards and durable observations rather than generating chat history.

### Monitoring scope, recovery, work calendar and card lifecycle

The personal monitored set is dynamically resolved from the current principal's resource/quota and managed runtimes, Active Focus dependencies, standing/shared infrastructure and host-level aggregate pressure. It is not every process/container visible on the shared node.

Recovery is layered: native systemd/Docker supervision first, optional bounded functional recovery only for explicitly registered/authorized resources, and human action for auth/config/data/effect ambiguity or exhausted recovery. Resident serverosd observes and coordinates facts; it is not an unbounded second supervisor.

Routine Broadcast uses the CN-Mainland business calendar in Asia/Shanghai: official workdays, including makeup Saturdays/Sundays, update from 08:00 through 20:00; statutory/non-working days have no routine status broadcast. P1 incidents may bypass quiet hours; ordinary P2 attention waits for the next work window.

Daily status uses one Feishu card per workday. The latest snapshot stays fully visible; earlier same-day broadcasts are compacted into a collapsible history within the same card. Incidents remain separate, one card per episode. Full observations live in records.

Detailed contract: docs/MONITORING-SCOPE-RECOVERY-CARDS.md.
