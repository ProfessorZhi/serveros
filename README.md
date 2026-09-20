# ServerOS

> 中文入口：[`README.zh.md`](README.zh.md). This English file is canonical.
>
> **Canonical architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md)

ServerOS is a **personal user-space Agent Work OS** for Claude Code, Codex and
other coding agents.

Its job is simple to state:

> Give the Agent the **right environment, right context, right memory and right
> capability at the right time**, while keeping the active context small.

ServerOS is intentionally large in installed capability and small in what it
shows to an Agent. It strengthens Claude Code / Codex; it must never become a
ceremony that makes them slower.

## What ServerOS should feel like

A normal day should look like this:

```text
Yesterday's Feishu / Docs / Wiki / Minutes / GitHub / Agent sessions
                              ↓
                   incremental source capture
                              ↓
                 Structured Understanding delta
                              ↓
              morning reconciliation with Coordinator
                              ↓
                    Personal Work Board
                              ↓
                  choose today's requirements
                              ↓
               Agent elicits missing intent
                              ↓
              CC drafts requirement / GPT reviews
                              ↓
                 research / design / decision
                              ↓
                    CC + optional Quay
                              ↓
                    E2E + strong evidence
                              ↓
                 Item report + Daily Report
```

At any point, opening a fresh Claude Code or Codex session should also work:

```text
new session
  → tiny ServerOS bootstrap
  → user says what they want
  → ServerOS resolves intent / project / Item
  → loads only the relevant Context Capsule + memory
  → discovers the few needed abilities
  → work starts
```

The user should not have to re-explain yesterday.

---

## The design philosophy

### Environment

Prepare the correct place and resources for the work:

- project / repo role;
- owned Item worktree when code changes are needed;
- test environment;
- toolchains;
- ports / containers / browser / remote device;
- evidence and artifact location;
- delivery target.

> Where should I work, where should outputs go, and how do I avoid interfering
> with somebody else?

### Context

Give the Agent the smallest correct *working* context:

- current intent;
- project;
- Item, if any;
- objective and acceptance;
- current checkpoint;
- relevant constraints and decisions;
- important source locators;
- protected concurrent work.

> What am I doing, what is already known, and what matters now?

### Memory

Recover useful continuity without dumping history:

- personal work preferences;
- role / responsibility knowledge;
- people and organization context;
- project memory;
- Item checkpoints and decisions;
- session-derived experience.

Memory retrieval is not truth authority. Durable understanding and important
changes remain governed and traceable.

### Capabilities

Resolve what the current task needs:

- Skills and procedures;
- MCPs;
- Providers;
- GitHub / Feishu;
- test and E2E tools;
- browser / remote device;
- Quay;
- build / release;
- specialist project abilities.

### Progressive disclosure

**Do not preload the world.**

```text
L0  thin bootstrap
 ↓
L1  working Context Capsule
 ↓
L2  relevant memory / ability / provider details
 ↓
L3  deep source evidence only when needed
```

A large installation must still have a small happy path.

---

## Personal work model

ServerOS is designed for one primary user and that user's devices, projects and
work. It is not a multi-tenant SaaS platform.

The main work lifecycle is:

```text
Source
  → Observation / Archive
  → Structured Understanding
  → Candidate
  → human selection
  → Item
  → requirement / research / design
  → Activity / Run
  → Evidence
  → Deliverable
  → Report / Knowledge / Memory
```

**Candidate is not Item.** A source message, incident or idea may create a
candidate, but long-lived work becomes an Item only after the user selects it.

The Personal Board stays coarse-grained: requirements and meaningful work
packages belong there; implementation steps belong below an Item as Activities.

---

## Sessions are first-class resources

ServerOS treats a Session as a work resource, not just a transcript file.

Logical roles include:

- **Coordinator Session** — reconciliation, board, project switching, work
  routing;
- **Execution Session** — project / Item work;
- **Maintenance Session** — incident diagnosis and repair.

One Item may have multiple sessions.

Live control and history are separate:

- ServerOS Agent Session owns create / resume / send / result / interrupt /
  stop / handoff semantics;
- **MetaCC** is the preferred Session History / Session Intelligence Provider
  for Claude + Codex history, lineage, compaction, timeline, errors, work
  patterns and edit sequences.

Native provider session IDs remain provider locators; ServerOS owns the logical
work relationship.

---

## Memory is a Provider, not a second truth system

A long-lived memory engine such as **OpenViking** may run independently on the
primary Linux node and be consumed through MCP.

```text
approved work truth / understanding
              │
              ├──────────────┐
              ▼              ▼
        durable records   Memory Provider
              │          extraction/retrieval
              └──────┬───────┘
                     ▼
               Context Resolver
                     ▼
               Context Capsule
```

ServerOS owns scope, provenance, promotion, reconciliation and conflict
semantics. The memory engine owns efficient memory extraction and retrieval.

---

## Human + GPT + Agents

The intended collaboration is:

```text
                         Human
                        /     \
                       /       \
                  ChatGPT     ServerOS
              reasoning/review    │
                       \          │
                        \         ▼
                         Claude Code / Codex
                              │
                              └── Quay
```

ChatGPT is especially useful for requirement clarification, product discovery,
research synthesis, technology choice, architecture, trade-offs and review.

Claude Code / Codex are the local execution agents.

Quay is a bounded delegation provider for useful parallel work such as
reproduction, independent verification, test matrices, parallel research and
small implementation slices.

GitHub is the near-term durable collaboration bridge between these actors.

---

## Human interfaces

**CloudCLI is the preferred rich console.**

A ServerOS CloudCLI integration should expose:

- Today / Personal Board;
- Understanding / reconciliation;
- Sessions;
- decisions / approvals;
- Memory;
- Quay;
- E2E evidence;
- environment / health;
- reports.

**Feishu is primarily a work-information and lightweight human-control surface:**

- source capture;
- notifications;
- reconciliation reminders;
- decisions that need attention;
- alerts / recovery;
- daily reports;
- lightweight mobile interaction.

A full Feishu terminal is not required.

tmux / SSH remain raw fallbacks, not Session identity authorities.

---

## Connectivity

ServerOS separates reachability, device control and Agent-to-Agent semantics:

```text
Tailscale       private device-to-device reachability
A2A             Agent-to-Agent delegation / communication
RDC             Agent-to-device GUI / files / terminal, on demand
GitHub          durable async collaboration
SSH / tmux      raw transport / fallback
```

Tailscale is the preferred private connectivity provider, but ServerOS does not
depend on it.

On shared hosts such as Tokyo, kernel/network mutation is high-risk. A userspace
or otherwise isolated Tailscale deployment is preferred unless an explicit
network change is approved.

---

## Repository roles and Bridge repositories

Project != repository.

A project may use several repository roles:

```text
owned-canonical
bridge-writable
upstream-readonly
publication
tool
```

When the user needs to develop against somebody else's or an enterprise
upstream repository, prefer an owned **Bridge repository** such as
`litellm-bridge` or `muc-bridge`.

The bridge is the writable, GitHub-visible engineering surface for Item
worktrees, experiments, evidence, Agent collaboration and GPT review. It does
not become upstream authority.

---

## Compliance comes first

ServerOS must not turn visibility into ownership.

On shared infrastructure:

- default principal is the user's own account;
- foreign and unknown-owned resources are protected;
- destructive shared Docker / storage / network operations fail closed;
- network changes require preflight and conflict checks;
- production promotion requires human approval;
- target-scoped delegated exceptions do not grant general identity escalation.

For the Tokyo instance, the shared AI gateway is an explicit target-scoped
maintenance exception; that does not grant general authority over other
`ubuntu` resources.

---

## The small core

ServerOS has **one resident core daemon by default: `serverosd`**.

`serverosd` stays mechanical:

```text
observe
→ attribute
→ admit
→ bounded reconcile of explicitly-owned desired resources
→ emit observations / events
```

It does not run an LLM planner.

Provider services and managed runtimes such as OpenViking, CloudCLI, Docker,
BotMux or Codex App Server may be long-lived, but they remain outside the
ServerOS core.

The product composition Core has two canonical members:

```text
Core
├── serveros
└── ability-management
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full boundaries and
invariants.

---

## Ability model

External semantics:

```text
Ability#Operation
      ↓
Provider
      ↓
Binding(device, instance)
      ↓
Attachment(session / Item / scope / lease)
```

Three axes remain distinct:

- **Support** — is this operation implemented by this provider?
- **Readiness** — can it work now?
- **Authorization** — may this caller do it?

Installation alone never means readiness or authorization.

Ability discovery is native-first and progressive. If a local ability does not
exist, ServerOS may discover candidate capabilities through trusted external
sources, evaluate provenance / compatibility / risk, and attach them
temporarily or propose long-term adoption.

---

## Verification and delivery

An Item is done when its own acceptance is satisfied with evidence.

```text
acceptance
+ verification
+ E2E evidence where appropriate
+ deliverable
+ final report
```

Unit tests, a green command, a PR or a Quay task are not by themselves Item
completion.

Build / TEST deployment / E2E may advance automatically inside the approved
Execution Envelope. Production promotion remains human-approved.

---

## Canonical repositories

| Repository | Responsibility |
|---|---|
| `ProfessorZhi/serveros` | product architecture, composition, bootstrap, node runtime and deterministic kernels |
| `ProfessorZhi/ability-management` | reusable Ability registry, revisions, compatibility, discovery, installation and distribution |
| `ProfessorZhi/work-engine` | durable Item / Activity / Run state and board projections |
| `ProfessorZhi/hwz-ops` | Tokyo instance topology, policy and operational bindings |
| `ProfessorZhi/hwz-records` | work / information / understanding records |

The detailed ownership rules are in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## Non-goals

ServerOS is not:

- a second operating system;
- a generic workflow engine;
- a second Claude/Codex planner;
- a generic process supervisor;
- a universal remote shell;
- a replacement for GitHub, Feishu, CloudCLI, MetaCC or OpenViking;
- a reason to create a daemon, repository or Skill for every box in a diagram.

Before adding machinery, ask:

> Does this materially improve the Agent's environment, context, memory,
> capability, continuity or safety — while keeping the default path fast?
