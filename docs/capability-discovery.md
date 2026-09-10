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
