# Architecture

## The one-sentence definition

**ServerOS = Environment + Context + Capabilities.**

It is an Agent Engineering Workbench / Toolbox. It is not a Workflow Engine.

The path `information → requirement → analysis/design → development → test →
report` describes a *common* route, not a state machine. An agent may skip
steps, go back, work in parallel, re-analyze after testing, or ship a Milestone
and keep developing.

## One capability system, two runtime adapters

```text
                 ServerOS Canonical
        ┌────────────────────────────────┐
        │ ability-management             │
        │ workspace / maps / policies    │
        │ serverosctl                    │
        │ Skill canonical source         │
        │ project / Item context contract│
        └───────────────┬────────────────┘
                        │ build / install
              ┌─────────┴─────────┐
              ▼                   ▼
        Claude Code             Codex
        adapter/runtime         adapter/runtime
              │                   │
              └─────────┬─────────┘
                        ▼
               同一个工作环境
```

> **Same capability, same semantics, same context; different runtime adapter
> only.**

Claude Code and Codex must see the same world:

```text
same DATA_ROOT / WORKSPACE_ROOT
same project / Item identity
same repo role
same worktree placement
same test-env placement
same artifacts / evidence contract
same Local / Feishu / GitHub maps
same serverosctl
same Skill semantics
same concurrency protection
```

The only permitted differences are runtime adapters — e.g. `.claude-plugin/`
and `CLAUDE.md` for Claude Code; `.codex-plugin/` and `AGENTS.md` for Codex.
Adapters must never evolve into two copies of business logic.

We do **not** hand-maintain a "Claude version" and a "Codex version" of a Skill.
There is one canonical source, and a builder fans it out:

```text
ability-management/skills/<skill>/
                 ↓
          canonical source
                 ↓
             builder
          ┌──────┴──────┐
          ▼             ▼
       Claude          Codex
```

Change a directory rule once, regenerate and install both sides.

## Dual-line ≠ run everything twice

Dual-runtime adaptation does **not** mean every task is executed once by Claude
and once by Codex. That would be pure waste.

It means: whichever runtime the user picks, it can take over the *same* Item
without re-initializing anything.

```text
LIT-0101
workspace/worktrees/litellm-bridge/LIT-0101
```

Claude works half-way and exits; Codex arrives later and, through ServerOS,
quickly learns:

```text
this is LIT-0101
current branch
current baseline
existing milestone
current Run / evidence
which resources are protected
which test environments exist
final delivery target is litellm-fjd
```

— rather than starting a Codex-private directory tree because the agent changed.

That hand-off — in **both** directions — is the real acceptance criterion.

## Component boundaries

| Component | Owns | Does not own |
|---|---|---|
| `ability-management` | Skill/plugin canonical source, ability registry, revisions, compatibility, installation | runtime state, topology, Item state |
| `work-engine` | `serverosctl`: deterministic control plane — Item/Activity/Run/lease state, board projection | Skill content, topology, cloud objects, **placement policy** |
| `hwz-ops` | this server's topology, maps, policies | Skill content, runtime installation, placement decisions |
| `records` | human-readable work records (cards, briefs, board views) | machine state, cloud bodies |
| `feishu-space-management` | Feishu placement, maps, drift, lifecycle | semantic interpretation of Feishu content |
| `information-management` | raw observations, traceable claims, understanding | Item lifecycle, test environments |

`serveros` itself is a **router/orchestrator**: it owns relations and hand-offs,
not the leaf objects of any single domain.

### Two decision kernels, one ownership rule

`context/` (what is my working environment) and `space/` (where does this object
canonically belong) are the same kind of thing: deterministic, read-only
resolvers that ship inside the runtime artifact and are invoked through a thin
delegation in `work-engine`. Neither may hold a tenant-specific path or call a
cloud API — that is an **adapter** concern, and adapters live with the capability
that owns the surface. See [`docs/space-resolver.md`](docs/space-resolver.md).

## Two planes, kept apart

```text
Control plane   state/serveros/serveros.db        Item / Activity / Run / lease current state
Human plane     records/projects/<project>/       cards, briefs, board views, reports
Evidence plane  artifacts/work-items/<project>/<ITEM>/<RUN>/
Cloud plane     Feishu / GitHub                   bodies stay with the cloud owner
```

A board view is a *recomputable projection* of card + state + claims. It is
never a second state database.

## Distribution

```text
ability-management  (canonical)
        ↓ deterministic build
serveros release artifact
```

Generation is one-way. Any generated payload carries
`GENERATED — DO NOT EDIT`. Nothing in a distribution artifact may be edited in
place and synced back; the fix always goes to canonical and is rebuilt.

## Non-goals

No global workflow engine. No forced Activity pipeline. No new daemon, lease
system, approval layer, or directory layer. No second source of truth. No
documentation manufactured merely to close a schema.
