# Agent Identity, Session Resources and Multi-Agent Delegation

> **Status: Accepted V2 architecture direction · 2026-10-16**
>
> This document records the next architectural step for ServerOS after the V1
> Claude Code / Fleet foundation: a stable logical Agent identity, Sessions as
> replaceable execution resources, explicit Work/Assignment ownership, and
> hierarchical multi-Agent delegation.
>
> This is a **design contract for V2**. It does not claim that V2 Agent identity,
> OpenAgents communication, or Codex integration is already implemented or LIVE.
> V1 contracts remain authoritative until an explicit versioned migration is
> accepted.

## 1. Decision

ServerOS separates five things that are easy to conflate:

1. **Work** — what must be accomplished and what counts as accepted delivery.
2. **Logical Agent** — who/what is responsible for a class of work or a delegated role.
3. **Assignment / Activation** — the bounded appointment of an Agent to a Work item.
4. **Session** — a concrete provider-native execution context that an Agent may use.
5. **Device / Runtime** — where and through which provider that Session actually runs.

The core rule is:

> **A Logical Agent is not a Session. A Session is a resource used by an Agent, not the Agent's permanent identity.**

The same Work may therefore continue through multiple Sessions, and an Agent may
be activated on different compatible Sessions over time.

This separation is the foundation for adding Codex and other native runtimes
without changing the identity of the Work or rebuilding the coordination layer.

## 2. Product shape

The intended V2 relationship is:

```text
Human / ChatGPT / Claude Chat / Feishu
                    |
                    v
          Fleet / Work Control Plane
     durable Work + authorization + routing
                    |
          +---------+---------+
          |                   |
          v                   v
   Logical Agents        Agent messaging
   roles / identity      delegate / ask / result
          |
     Assignment / Activation
          |
          v
    Runtime Session Pool
     +------+------+------+
     |             |      |
   Tokyo         Dell   Windows
   Claude       Claude  native
   Code         Code    specialist
     |
     +-- provider/runtime adapters
          Claude Code / Codex / future agents
```

ServerOS remains the control and continuity layer. Native agents remain responsible
for reasoning, decomposition, investigation, implementation and iteration.

The Fleet layer must **not** become a second LLM planner whose job is to reproduce
the reasoning already performed by native agents.

## 3. Canonical object model

### 3.1 Work

A Work item is the durable unit of responsibility and delivery.

It owns:

- objective and constraints;
- acceptance criteria;
- current checkpoint;
- evidence and Outcomes;
- parent/child relationships;
- authorized effect scope;
- current Assignment(s).

Work survives a Session ending. A Session ending is therefore not equivalent to
Work completion.

### 3.2 Logical Agent

A Logical Agent is a stable identity for a responsibility-bearing participant.

Examples:

- `agent:serveros/architecture`
- `agent:serveros/implementation`
- `agent:serveros/test`
- `agent:litellm/observability`

A Logical Agent may have:

- a stable identity;
- role/capability requirements;
- owner/principal;
- allowed Ability classes;
- model/runtime preferences;
- concurrency and budget limits;
- parent/child delegation rules.

A Logical Agent does **not** own:

- a particular PID;
- a particular tmux pane;
- a particular Claude Code Session;
- a particular device;
- a particular chat window.

Those are execution facts and resources.

### 3.3 Assignment / Activation

An Assignment is the explicit relationship between Work and an Agent for one
bounded execution period.

It should carry:

- `assignment_ref`;
- Work reference;
- logical Agent reference;
- authorized Ability/scope set;
- budget and expiry;
- device/runtime constraints when required;
- activation epoch;
- current Session reference;
- parent Assignment when delegated.

The Assignment is the place where **"this Agent is currently responsible for this
Work"** becomes an enforceable fact.

A new Session can replace the old Session by creating a new Activation/epoch while
preserving the same Work and Logical Agent identity.

### 3.4 Session

A Session is a stateful provider-native execution resource.

It may contain:

- provider Session ID;
- runtime/provider identity;
- device reference;
- workspace/cwd;
- provider state;
- context/history;
- current turn information;
- readiness and lifecycle state.

It does not own the Work, Agent identity or authorization.

A Session can be:

- created;
- resumed;
- paused;
- detached;
- replaced;
- closed.

A provider-native Session remains authoritative for its own provider state. ServerOS
normalizes and admits access to it; it must not invent provider session state.

This extends the existing V1 rule in
`runtime/agent-session-contract.yaml`: Session lifecycle, Turn lifecycle and
request acceptance remain separate.

### 3.5 Device and Runtime

A Device is the physical/virtual execution host.

A Runtime is a concrete provider/runtime implementation on that host, such as:

- Claude Code profile A;
- Claude Code profile B;
- native Claude Code account;
- future Codex runtime.

**Two Claude Code accounts on one Tokyo server are therefore two Runtime/Profile
instances, not two Devices and not automatically two Logical Agents.**

Whether they represent different Logical Agents is an identity/role decision, not
an account-counting rule.

## 4. Session is a resource, but not a disposable context blob

The phrase "Session is a resource" has an important qualification.

A Session is not merely a free compute slot. It is a **stateful execution resource**
with provider-owned context and lifecycle.

Therefore ServerOS should treat:

```text
Logical Agent
    != Session
    != Runtime
    != Device
    != Workspace
    != Work
```

as separate identities.

A Session may use a Workspace, but the Workspace is not owned by the Session.
A Work may use multiple Workspaces over time, but a Workspace is not the Work's
identity.

### Handoff rule

When work moves from Session A to Session B:

```text
Session A
   |
   +--> checkpoint / evidence / code revision / open questions
   |
   v
Assignment epoch N closes for writes
   |
   v
Session B activates epoch N+1
   |
   +--> verifies checkpoint
   +--> continues Work
```

Do not silently repoint a live Session from one Work item to another.

This preserves the V1 invariant that execution bindings are explicit and
non-mutable, while allowing the **Agent identity** above the Session layer to
remain stable.

## 5. Why this removes the central-Agent bottleneck

A flat architecture makes one top-level Agent inspect and coordinate every child:

```text
             One giant coordinator
          /    /    /    /    /    \
       agent agent agent agent agent agent
```

That creates:

- excessive context;
- serial decision pressure;
- duplicated summaries;
- a single reasoning bottleneck;
- poor failure isolation.

V2 instead uses bounded hierarchy:

```text
Human / top-level coordinator
             |
       Project Parent
       /     |      \
   design  impl    test
            |
       specialist(s)
```

Each Parent receives only the information needed to coordinate its scope.
Children return structured results and evidence rather than forcing the root Agent
to inspect every raw turn.

The hierarchy is **responsibility-based**, not permission-inheritance based.

A child Agent never receives a parent's credentials or broader authority merely
because it was delegated a task.

### Bounded hierarchy

The default should remain shallow:

```text
Human
  -> Portfolio / Project Parent
      -> Specialist Agent
          -> Session / Runtime
```

A deeper tree is permitted only when it reduces real coordination cost. Infinite
delegation is not a design goal.

The central Fleet/Work layer remains the durable source of Work state, authorization,
leases and evidence. It can therefore coordinate many Agents without itself needing
to perform every reasoning step.

## 6. Agent-to-Agent communication

V2 should support direct Agent communication, but through a governed message model.

The first semantic operations are:

| Operation | Meaning | Does it grant execution authority? |
|---|---|---|
| `consult` | Ask another Agent for information or judgement | No |
| `delegate` | Assign bounded Work to another Agent | Only the explicitly delegated scope |
| `result` | Return a result/evidence package | No |
| `escalate` | Return an unresolved decision to the Parent | No |

Messages should have stable correlation and provenance:

- `message_ref`;
- source Agent;
- target Agent;
- Work / Assignment reference;
- parent message when applicable;
- input revision;
- requested scope;
- created/accepted/completed timestamps;
- result/evidence reference;
- expiry or timeout where relevant.

"Message sent" is not the same state as "task accepted", "task executed" or
"result accepted".

The communication layer must also support idempotency so that a timeout or retry
does not accidentally create duplicate Work or duplicate external effects.

## 7. Permissions and identity boundaries

These rules are mandatory:

1. **Role does not equal permission.**
2. **Logical Agent identity does not equal provider account identity.**
3. **Delegation does not automatically propagate privileges.**
4. **Session replacement does not create a new Work identity.**
5. **A provider Session cannot be silently rebound to another Work.**
6. **A child Agent cannot approve its own elevation merely because a Parent delegated it.**
7. **Credentials remain owned by the appropriate Provider/Device boundary.**
8. **Agent messages carry references to authorized state; they do not carry raw secrets.**
9. **Every execution-capable Assignment has a bounded scope, epoch and budget.**
10. **Unknown provider state remains UNKNOWN; coordination must not manufacture success.**

These extend the existing ServerOS authorization, Agent Control and evidence rules
rather than replacing them.

## 8. Relationship to the existing V1 contracts

V1 already has useful lower-level distinctions:

```text
Device
  -> Runtime
     -> provider Agent
        -> Session
           -> Turn
```

V2 adds one logical identity layer above the provider-bound runtime identity:

```text
Logical Agent
      |
      v
Assignment / Activation
      |
      v
provider Agent / Runtime
      |
      v
Session
      |
      v
Turn
```

Therefore V2 should **not** redefine the existing `agent_ref` and
`session_ref` semantics in place.

Instead:

- retain current V1 provider/device-bound identifiers;
- introduce a stable `agent_identity_ref` (name subject to final schema review);
- map logical identity -> Assignment -> provider Agent/Runtime -> Session;
- preserve current Session/Turn lifecycle contracts;
- migrate consumers incrementally.

This avoids making an existing identifier mean two incompatible things.

## 9. Runtime selection

The Agent chooses work method; ServerOS supplies suitable resources.

A runtime pool can expose facts such as:

```text
Runtime
  provider = claude-code
  profile = account-A
  device = tokyo
  capabilities = [coding, shell, git, ...]
  readiness = AVAILABLE
  concurrency = ...
```

or:

```text
Runtime
  provider = codex
  profile = account-B
  device = dell-a6000
  capabilities = [...]
```

Selection should consider:

- required Abilities;
- provider support;
- runtime readiness;
- device resources;
- workspace locality;
- data policy;
- authorization;
- budget;
- current load.

It should **not** select solely by "which Session is already open".

That is the practical consequence of making Session a replaceable resource.

## 10. Codex and OpenAgents are V2 extensions

### Codex

Codex is a V2 Runtime/Provider integration target.

The Work and Logical Agent layers should not care whether a particular execution
is carried out by Claude Code or Codex.

The provider adapter owns provider-specific session, turn, transport and lifecycle
details.

### OpenAgents

OpenAgents is a candidate V2 communication/delegation substrate, not a replacement
for ServerOS's Work Engine or authorization authority.

The intended boundary is:

```text
ServerOS
  owns: Work / identity / policy / evidence / device & runtime authority
                     |
                     v
          OpenAgents integration
  supplies: Agent messaging / discovery / delegation transport
                     |
                     v
          Claude Code / Codex / future agents
```

If OpenAgents is adopted, it must be integrated through a thin adapter and must not
create a competing Work database, competing authorization root or competing
definition of Session identity.

The current upstream project describes a network/launcher/workspace model with
Claude Code and Codex support and event/message-based collaboration. These are
useful implementation references, but ServerOS retains its own authority model:
https://github.com/openagents-org/openagents

## 11. V1 acceptance boundary

V1 is considered architecturally complete when the existing ServerOS foundation
can reliably:

- run the ServerOS Core on managed devices;
- expose the existing Fleet MCP surfaces;
- observe and attribute node/runtime/session resources;
- manage Claude Code Sessions with explicit identity and admission;
- discover and use the existing Ability families;
- preserve Work continuity across Session changes where already supported;
- maintain authorization, evidence and audit boundaries;
- provide the Plugin/Skill composition needed by the native Agent;
- complete the local end-to-end test path before adding a public ChatGPT connector.

V1 does **not** require:

- Codex as a fully supported provider;
- OpenAgents;
- dynamic logical Agent teams;
- arbitrary peer-to-peer Agent messaging;
- automatic Agent-to-Session scheduling across a large pool.

Those are V2.

## 12. V2 implementation sequence

The preferred migration order is:

1. **Identity schema** — add logical Agent identity without changing V1 IDs.
2. **Assignment/Activation** — introduce bounded Work-to-Agent responsibility.
3. **Session resource pool** — make Session selection/replacement explicit.
4. **Handoff** — checkpoint + epoch + verification across Sessions.
5. **Hierarchical delegation** — Parent/child Work with bounded scope.
6. **Agent messaging** — consult/delegate/result/escalate with idempotency.
7. **Codex adapter** — reuse the same Runtime/Session/Assignment model.
8. **OpenAgents adapter** — evaluate as the communication substrate.
9. **Multi-Agent acceptance** — verify failure isolation, duplicate suppression,
   authorization non-propagation, recovery and end-to-end delivery.

Each step requires CODE, INTEGRATION and LIVE evidence separately. A schema commit
does not prove a running multi-Agent network.

## 13. Non-goals

This architecture does not attempt to:

- replace Claude Code or Codex's native reasoning loop;
- turn `serverosd` into an LLM planner;
- make every Session persistent forever;
- expose all Agent context to every other Agent;
- make every device equal;
- require every task to use multi-Agent delegation;
- make OpenAgents the system of record;
- make ChatGPT the internal orchestration authority.

The simplest valid path remains the preferred path:

> **one Agent + one suitable Session + one Work item is enough whenever it is enough.**

Multi-Agent orchestration exists to reduce real coordination cost, not to make simple
work look distributed.

## 14. Architecture invariants

The following statements should remain true in future revisions:

- **Work survives Session replacement.**
- **Logical Agent survives Session replacement.**
- **Session does not define Agent identity.**
- **Runtime does not define Agent identity.**
- **Device does not define Agent identity.**
- **Workspace does not define Work identity.**
- **Assignment defines bounded responsibility.**
- **Epoch prevents stale executions from writing after handoff.**
- **Delegation narrows or preserves authority; it never silently widens it.**
- **Messages are evidence-bearing coordination events, not hidden privilege channels.**
- **ServerOS retains Work/authorization/evidence authority even if an external Agent network is adopted.**
- **V1 contracts remain valid until an explicit migration changes them.**
