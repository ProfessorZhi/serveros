# Capability Discovery — Native-first

## The constraint

> ServerOS may only **strengthen** Claude Code's and Codex's own native Skill
> discovery. It must never replace it.

There is no enforced Skill Router. There is no allowlist.

## Correct model

```text
Installed native Skills
        │
┌───────┴────────┐
Claude          Codex
native          native
discovery       discovery
        │
        ▼
ServerOS advisory hints
```

Given the Context Capsule, ServerOS may offer a hint:

```text
likely relevant:
- test-management
- work-management
```

That hint is advisory, not a gate. The agent may always:

- discover a different Skill itself
- ignore ServerOS's suggestion
- query the full capability catalog

When discovery confidence is low, suggest nothing.

## Non-regression contract

> **A ServerOS hint must never shrink the set of capabilities the native agent
> could otherwise discover.**

The forbidden failure mode: *"with ServerOS off, Claude Code finds the Skill;
with ServerOS on, it is incorrectly routed away from it."*

This is a hard contract, not a guideline. It is checked in the dual-runtime
smoke ([`claude-codex-parity.md`](claude-codex-parity.md)).

## Skill descriptions are the discovery surface

Because discovery is native, the `description:` field of each Skill is the
highest-leverage text in the system. Each description should state:

- **when to use** it
- **what it owns**
- **what it explicitly does NOT own**
- high-value trigger terminology

Descriptions must **not** carry long procedures. A Skill's detailed rules
occupy context only when the Skill is actually loaded.

## Big package, small context

The installation unit may be large:

```text
hwz-server-os
├── 8 Skills
├── runtime adapters
├── validators
└── CLI bindings
```

But a normal session must never receive all of it in the prompt. Top-level
bootstrap stays tiny — on the order of tens of lines, not hundreds:

```text
ServerOS exists
current context query
native Skills available
serverosctl available
load details on demand
```

## Progressive disclosure

There are three things an Agent can be told, and they are disclosed separately
because they are needed at different times. Collapsing them is how a startup
context becomes a wall of text that is read once and used never.

```text
Context      what is this session working on        needed first
Capability   what can this machine do about it      needed when choosing an approach
Authority    what may I do without asking           needed before acting
```

Each has four levels. Only L0 is unconditional.

### Context

```text
L0  project · Item id · Run id                               always, ~5 lines
L1  the Item's current state, milestone, and open blockers   on request
L2  the Run's evidence locator and recent Activity           on request
L3  the full capsule, claims and provenance                  on demand
```

### Capability

```text
L0  the count of available abilities, and that a query exists   always, 1 line
L1  names of abilities likely relevant to this Item             on request
L2  a specific ability's provider, status and reason            per ability, on demand
L3  the full resolved capability set with every input           on demand
```

The L0 line is deliberately a number and not a list. A list is a menu, a menu
invites browsing, and browsing is how a session ends up reading the catalogue
instead of doing the work. The native agent already has its own Skill
discovery — the number exists to tell it that ServerOS has something to say,
not to say it.

### Authority

```text
L0  the boundary in one line: what needs approval          always
L1  the policy set that applies to this project            on request
L2  a specific denial and its reason                       when a denial occurs
L3  the full authority statement                           on demand
```

### The contract between levels

```text
A level is never disclosed implicitly.      Reading L3 requires asking for L3.
A higher level never contradicts a lower.  If it does, the lower one was wrong.
An agent may always skip levels.            Disclosure is a floor, not a ladder.
```

The last line matters: this is a description of what ServerOS *offers*, not a
protocol an agent must walk. An agent that already knows what it needs asks for
exactly that, and the honest response is the answer plus which level it came
from.

### What the startup view must never contain

```text
not at L0   the ability catalogue, or any ability list longer than a count
not at L0   file trees, map contents, or generated projections
not at L0   any per-device resolution
not at L0   anything a previous session learned that this session has not asked for
```

The fourth line is the one that creeps back. A durable memory fabric makes it
tempting to open a session with everything the last one knew. That is not
context, it is a transcript, and it costs the session the ability to tell what
is *relevant* from what is merely *remembered*. Memory is queried, not
preloaded.
