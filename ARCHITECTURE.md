# Architecture

> **This is the single canonical architecture statement.** Where any other
> document, diagram, README section or generated payload describes the shape of
> ServerOS differently, that document is wrong and this one is right. There is
> no second statement that is "also correct for a different audience".

## The definition (frozen)

**ServerOS = a user-space Agent control plane.**

It does not schedule the kernel, it does not supervise every process on the
machine, and it is not a workflow engine. It is the layer that knows *which
machine this is*, *what is running on it*, *who owns it*, *what is protected*,
and *how a declared piece of it may be brought back* — and it exposes that to
agents under the user's own authority, never root's.

What the control plane delivers to a working agent is still Environment, Context
and Capabilities; that is the *purpose*, not a second architecture.

ServerOS consists of exactly these parts:

```text
small node runtime          serverosd — the local control loop
semantic services           the read-only decision kernels and projections
Agent-facing MCP            a thin stdio adapter over the node runtime
bootstrap Skill             what an agent reads first
Ability-driven procedures   specialist procedures, resolved on demand
Plugin composition          what gets packaged and installed per runtime
optional Fleet              topology across devices, when a device declares it
```

**Availability is per-device declared.** ServerOS is not a product-global
always-on promise. A device declares whether it runs the node runtime, which
abilities it composes, and which resources it is willing to reconcile. A
machine with no `serverosd` is a supported configuration, not a broken one.

The path `information → requirement → analysis/design → development → test →
report` describes a *common* route, not a state machine. An agent may skip
steps, go back, work in parallel, re-analyze after testing, or ship a Milestone
and keep developing.

## The node control loop (frozen)

`serverosd` is small on purpose, and its responsibilities are closed:

```text
serverosd
├── Host Inventory
├── Managed Runtime Registry
├── Probe Engine
├── Runtime Attribution / Graph
├── Anomaly / Hygiene
├── Policy / Admission
├── Reconciler
├── Observation / Provenance
└── Local IPC
```

Context / Path / Fleet may be **exposed as ServerOS services**, but they may
not inflate the control loop. A new entry in the list above is an architecture
change, not an implementation detail.

The loop is one direction and one shape:

```text
Inventory ─▶ Probe ─▶ Attribution ─▶ Policy/Admission ─▶ Reconciler
     ▲                                                        │
     └──────────────── Observation / Provenance ◀─────────────┘
                            │
                            ▼
                        Local IPC  ──  serverosctl / Agent-facing MCP
```

Every actuator decision is gated by admission, every admission decision is
bounded by a retry budget, and an exhausted budget escalates to a person rather
than looping. Nothing in the loop reaches the network or a cloud API.

## Core-2, and what composes around it

There is **one** Core list. It has two members:

```text
Core
├── serveros             product entry, composition authority, bootstrap Skill,
│                        Context / Space / Relations kernels, deterministic build
└── ability-management   Capability / Ability registry, reusable Skill canonical
                         source, revision / compatibility / discovery / reconcile
```

`workspace-management` is an important specialist capability, **not** a third
Core module. Everything below Core is an Ability, resolved when a node reaches
for it:

```text
ServerOS product baseline     serveros bootstrap + runtime-management
Linux work profile            serveros-core + workspace-management + test-management
everything else              information-management, document-stewardship,
                             corporate and engineering abilities — on demand
```

A composition is not a fork and not a checkout. `plugin/bundle.yaml` is the
`serveros-core` composition; `plugin/profiles/*.yaml` are wider sets that
`extends:` it, carry no payload of their own, and copy nothing they inherit — so
two compositions cannot disagree about a member they share. Members a
composition does not ship are `recommended` and resolved per node; the builder
refuses both a recommendation that cannot be resolved and a stale `missing`
marker. **Nothing eagerly bundles the catalogue.**

There is no second Core list, no `seven-service` variant, and no always-on
bundle. If you find one, it is retired.

## Two runtime adapters, one world

> **Same capability, same semantics, same context; different runtime adapter
> only.**

```text
              serveros deterministic builder
                         │
                pinned ability revision
                         │
                  ┌──────┴──────┐
                  ▼             ▼
              Claude Code      Codex
              adapter/runtime  adapter/runtime
```

Claude Code and Codex must see the same `DATA_ROOT` / `WORKSPACE_ROOT`, the same
project / Item identity, repo role, worktree and test-env placement, artifacts
contract, generated map projections, `serverosctl`, Skill semantics and
concurrency protection. The only permitted differences are runtime adapters —
`.claude-plugin/` + `CLAUDE.md` versus `.codex-plugin/` + `AGENTS.md`. Adapters
must never evolve into two copies of business logic.

Dual-runtime does **not** mean every task runs twice. It means whichever runtime
the user picks can take over the *same* Item without re-initializing anything.

## Component boundaries

| Component | Owns | Does not own |
|---|---|---|
| `serveros` | architecture contract; hwz-server-os composition; bootstrap Skill; Context/Space/Relations kernels; node runtime; deterministic build | reusable Skill source, runtime state, machine topology instance, Item state |
| `ability-management` | Capability/Ability registry; reusable Skill source; revisions; compatibility; discovery; reconcile; installation/distribution | plugin composition, bootstrap Skill source, runtime state, machine topology instance, Item state |
| `workspace-management` | workspace governance/reconcile specialist capability | Core membership, plugin composition |
| `work-engine` | `serverosctl`: deterministic control plane — Item/Activity/Run/lease state, board projection | Skill content, topology, cloud objects, placement policy |
| `hwz-ops` | this machine's topology/policies and projection targets | Skill content, runtime installation, plugin composition |
| `records` | human-readable work records (cards, briefs, board views) | machine state, cloud bodies |
| `feishu-space-management` | Feishu placement, maps, drift, lifecycle | semantic interpretation of Feishu content |
| `information-management` | raw observations, traceable claims, understanding | Item lifecycle, test environments |

### Two decision kernels, one ownership rule

`context/` (what is my working environment) and `space/` (where does this object
canonically belong) are deterministic, read-only resolvers shipped inside the
runtime artifact. Neither may hold a tenant-specific path or call a cloud API —
that is an **adapter** concern, and adapters live with the capability that owns
the surface. See [`docs/space-resolver.md`](docs/space-resolver.md).

## Planes, kept apart

```text
Control plane   state/serveros/                    node runtime state, registry, lease
Human plane     records/projects/<project>/        cards, briefs, board views, reports
Evidence plane  artifacts/work-items/<project>/<ITEM>/<RUN>/
Cloud plane     Feishu / GitHub                    bodies stay with the cloud owner
```

A board view is a *recomputable projection* of card + state + claims. It is
never a second state database.

## Canonical ownership and distribution

```text
serveros/skills/serveros/                 bootstrap Skill canonical
serveros/plugin/bundle.yaml               serveros-core composition canonical
serveros/plugin/profiles/*.yaml           wider compositions, each `extends:` core
serveros/runtime/                         node runtime canonical (control_plane.py)
ability-management/skills/<id>/payload    reusable Skill canonical
ability-management/plugins/<id>/          plugin canonical
ability-management/mcps/<id>/             MCP canonical
```

**No reusable Ability payload is ever copied into a plugin source tree.** A
composition names an Ability by `AbilityRef` + pinned revision; the build
materializes the payload from that revision. A checked-in copy of a payload
inside a plugin tree is a second source of truth and is removed, not
documented-around. Any generated payload carries `GENERATED — DO NOT EDIT`;
nothing in a distribution artifact is edited in place and synced back.

Generation is one-way. Nothing under a materialization directory may become
authority, and `sync-self` / manifest fingerprints must describe the payload the
build actually materializes — never a stale copy that happens to sit nearby.

## The fleet (optional)

```text
ServerOS owns the questions, not the paths:
which machine is this · where does its source/toolchain/evidence live ·
how may another authorized agent reach it · what is protected
```

Ability Management references a stable `device:<id>` and owns capability
desired/observed state; it does not keep a second device list. Fleet is
**optional per device**: a device that declares no fleet participation is a
normal ServerOS node that simply has no topology neighbours.

```text
One ServerOS product
One ServerOS node per participating device
One device profile per node
Different physical directories
Different desired ability graphs
Same semantic contracts
```

**Stable boundaries, adaptive interiors.** The machines are not meant to
converge on the same directories. They converge on the same *questions*:
`fleet/profiles.yaml` binds one closed role vocabulary to unrelated physical
paths per device, so a consumer asks for `workspace.src` and never learns
whether that is a POSIX path or a drive letter.

Device identity is **authored, not discovered**. A hostname, computer name or
relay UUID is a route, not a name — it is recorded as an observation and never
used to key, join or deduplicate a device. Rename adds an alias so earlier
Installation and Revision evidence keeps resolving.

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
  is no default `remote.shell(device, arbitrary_command)` agent API.

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
them acquires authority by being written down. When a projection and its source
disagree, the projection is regenerated — never hand-corrected.

## NOT SERVEROS RESPONSIBILITY

This list is closed. It exists so these do not grow back.

```text
kernel scheduling
system-wide process supervision
container orchestration
generic workflow execution
generic job scheduler
secret storage
full APM
metrics warehouse
automatic destructive cleanup
```

ServerOS observes all of these and may attribute them to an owner. It does not
*implement* them, and a proposal to implement one is an architecture change that
this list exists to reject.

Consequences worth stating explicitly, because they are the ones most likely to
be re-proposed:

- No global workflow engine and no forced Activity pipeline.
- No new daemon, lease system, approval layer, or directory layer beyond the
  frozen `serverosd` above.
- No second source of truth for Skills, maps, or Item state.
- No automatic deletion, prune, or volume/container cleanup. Docker and disk
  findings are **observed and attributed**; acting on them is a person's call.
- No documentation manufactured merely to close a schema.

Before adding anything, ask: **does this improve Environment, Context, or
Capabilities — and which real failure case fails without it?** If neither has an
answer, it is not ServerOS.

---

## Status of this document

Round 9 froze this statement. **No more architecture expansion.** The next
permitted actions are promotion / cutover of the built candidate, or porting a
new device profile — not another round of Core design.
