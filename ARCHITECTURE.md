# Architecture

## The one-sentence definition

**ServerOS = Environment + Context + Capabilities.**

It is an Agent Engineering Workbench / Toolbox. It is not a Workflow Engine.

The path `information → requirement → analysis/design → development → test →
report` describes a *common* route, not a state machine. An agent may skip
steps, go back, work in parallel, re-analyze after testing, or ship a Milestone
and keep developing.

## Core-2, one capability system, two runtime adapters

```text
                 ServerOS Core-2
        ┌──────────────────────────────────────────────┐
        │ serveros                                     │
        │ Environment / Context                        │
        │ bootstrap Skill                              │
        │ Context / Space / Relations kernels          │
        │ plugin composition + deterministic build     │
        ├──────────────────────────────────────────────┤
        │ ability-management                           │
        │ Capability / Ability registry                │
        │ reusable Skill canonical source              │
        │ revision / compatibility / discovery         │
        │ reconcile / installation / distribution      │
        └──────────────────────┬───────────────────────┘
                               │ pinned build / install
                    ┌──────────┴──────────┐
                    ▼                     ▼
              Claude Code               Codex
              adapter/runtime           adapter/runtime
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
same generated map projections
same serverosctl
same Skill semantics
same concurrency protection
```

The only permitted differences are runtime adapters — e.g. `.claude-plugin/`
and `CLAUDE.md` for Claude Code; `.codex-plugin/` and `AGENTS.md` for Codex.
Adapters must never evolve into two copies of business logic.

Canonical ownership is split by concern, not duplicated:

```text
serveros/skills/serveros/                 bootstrap Skill canonical
serveros/plugin/bundle.yaml               serveros-core composition canonical
serveros/plugin/profiles/*.yaml           wider compositions, each `extends:` core
ability-management/skills/<skill>/payload reusable Skill canonical

              serveros deterministic builder
                         │
                pinned ability revision
                         │
                  ┌──────┴──────┐
                  ▼             ▼
               Claude          Codex
```

A reusable Skill changes once in `ability-management`; composition changes once
in `serveros`; the deterministic builder then regenerates both runtime adapters.

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
| `serveros` | architecture contract; hwz-server-os composition; bootstrap Skill; Context/Space/Relations kernels; deterministic build | reusable Skill source, runtime state, machine topology instance, Item state |
| `ability-management` | Capability/Ability registry; reusable Skill source; revisions; compatibility; discovery; reconcile; installation/distribution; Foundry | plugin composition, bootstrap Skill source, runtime state, machine topology instance, Item state |
| `workspace-management` | workspace governance/reconcile specialist capability | Core membership, plugin composition |
| `work-engine` | `serverosctl`: deterministic control plane — Item/Activity/Run/lease state, board projection | Skill content, topology, cloud objects, placement policy |
| `hwz-ops` | this machine's topology/policies and projection targets | Skill content, runtime installation, plugin composition |
| `records` | human-readable work records (cards, briefs, board views) | machine state, cloud bodies |
| `feishu-space-management` | Feishu placement, maps, drift, lifecycle | semantic interpretation of Feishu content |
| `information-management` | raw observations, traceable claims, understanding | Item lifecycle, test environments |

The Core is exactly `serveros + ability-management`. Specialist Skills remain
native-discoverable capabilities rather than being promoted into Core merely
because they are important.

### Two decision kernels, one ownership rule

`context/` (what is my working environment) and `space/` (where does this object
canonically belong) are deterministic, read-only resolvers shipped inside the
runtime artifact. Neither may hold a tenant-specific path or call a cloud API —
that is an **adapter** concern, and adapters live with the capability that owns
the surface. See [`docs/space-resolver.md`](docs/space-resolver.md).

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
serveros/plugin/bundle.yaml                     serveros-core composition
serveros/plugin/profiles/<name>.yaml            profile composition (extends core)
serveros bootstrap + Context/Space/Relations   local Core payload
ability-management@<full SHA>                  reusable Skill payloads
                    │
                    ▼
        serveros deterministic builder
                    │
                    ▼
   hwz-server-os / hwz-server-os-linux-work
                release artifacts (one per composition)
                    │
              ┌─────┴─────┐
              ▼           ▼
            Claude       Codex
```

A composition is what the builder reads, and an artifact names the composition
it was built from in its `README.md` (`Composition: \`<name>\`.`). A profile is
a composition, not a fork: it `extends:` core, carries no payload of its own,
and copies nothing it inherits — so the two cannot disagree about a member they
share. Members a composition does not ship are listed under `recommended` and
resolved per node; the builder refuses both a recommendation that cannot be
resolved and a `missing` marker on something that now exists.

Generation is one-way. `ability-management/plugins/hwz-server-os` may exist
only as a transitional/generated compatibility surface; it cannot become a
second composition authority. Any generated payload carries
`GENERATED — DO NOT EDIT`. Nothing in a distribution artifact may be edited in
place and synced back; the fix always goes to the owning canonical concern and
is rebuilt.

## The fleet: one product, one node per device, no forks

```text
ServerOS
├── Environment   Device Identity · Storage / Path Semantics ·
│                 Connectivity / Transport · Protected Resources
├── Context
└── Capabilities
    └── Ability Management
```

ServerOS owns the questions "which machine is this", "where does its source,
toolchain and evidence live", "how may another authorized agent reach it", and
"what is protected". Ability Management references a stable `device:<id>` and
owns capability desired/observed state. It does not keep a second device list.

```text
One ServerOS product
One ServerOS node per device
One device profile per node
Different physical directories
Different desired ability graphs
Same semantic contracts
```

**Stable boundaries, adaptive interiors.** The three machines are not meant to
converge on the same directories. They converge on the same *questions*:
`fleet/profiles.yaml` binds one closed role vocabulary to unrelated physical
paths per device, so a consumer asks for `workspace.src` and never learns
whether that is a POSIX path or a drive letter. `docs/fleet-controller-adapter.md`
covers the transport boundary.

Device identity is **authored, not discovered**. A hostname, computer name or
relay UUID is a route, not a name — it is recorded as an observation and never
used to key, join or deduplicate a device. Two Windows laptops with the same
installed skills are still two laptops, and merging them is unrecoverable.
Rename adds an alias so earlier Installation and Revision evidence keeps
resolving.

## Vocabulary boundaries (frozen)

```text
ServerOS    product / control model   decides identity, policy, desired state
Skill       procedural knowledge      decides when and how
MCP         typed tool/context boundary   an interface, never an authority
Plugin      packaging / distribution  carries the others to a runtime
Transport   execution backend         moves bytes, decides nothing
A2A         Agent ↔ Agent delegation  preferred over remote shell
```

Two rules make this enforceable rather than aspirational:

- **MCP carries no policy of its own.** A rule that exists only inside an MCP
  server is not a ServerOS rule, because no other consumer will honor it.
- **Remote execution is admitted by intent level**, not by raw command. There
  is no default `remote.shell(device, arbitrary_command)` agent API; a
  transport may support shell internally, but the semantic layer above it
  admits calls whose intent can be named and therefore governed.

## Authority

```text
ServerOS              top-level product and control model
ability-management    capability control plane
onboard knowledge     corporate knowledge source — not a control plane
agent runtimes        materialization only — they execute, they do not decide
maps                  generated projections — never authority
```

There is no second MASTER CONTROL PLANE. A document, a directory or a server
that describes the fleet is a source, an observation or a projection; none of
them acquires authority by being written down. When a projection and its
source disagree, the projection is regenerated — never hand-corrected.

## Non-goals

No global workflow engine. No forced Activity pipeline. No new daemon, lease
system, approval layer, or directory layer. No second source of truth. No
documentation manufactured merely to close a schema.
