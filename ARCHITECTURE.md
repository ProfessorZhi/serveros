# Architecture

> **This is the single canonical architecture statement.** Where any other
> document, diagram, README section or generated payload describes the shape of
> ServerOS differently, that document is wrong and this one is right. There is
> no second statement that is "also correct for a different audience".

## The definition (frozen)

**ServerOS = a user-space Agent Operating Fabric.**

It does not schedule the kernel, it does not supervise every process on the
machine, and it is not a workflow engine. It is the layer between an Agent
runtime and a machine that makes *what an Agent can do here, right now, without
surprising anyone* answerable — which machine this is, which ability is actually
available on it, what durable memory the work already has, and who owns the
resource that is running.

**This supersedes the earlier sentence "ServerOS = a user-space Agent control
plane".** That sentence was not wrong, it was incomplete in a way that cost real
time: it named the daemon's job and called it the product, so every question
about abilities, memory or a second device was answered by pointing at the
registry. The control plane is now exactly one of four planes — the Node Runtime
Plane — and the other three are not new machinery bolted on beside it. They are
the parts the control-plane framing left out and that three live failures ran
into:

```text
Case A  a tunnel required by a task vanished when an unrelated Agent session
        ended. Nothing was broken and nothing was wrong on the machine: the
        tunnel had been registered with session lifetime, so it was retired
        exactly on time.
Case B  127.0.0.1:17890 is the documented outbound tunnel on the Linux node.
        It exists in a `.bashrc` comment. There is no listener, no service
        owner, and no declaration anywhere that says it should exist.
Case C  the Dell node reached Playwright through `npx @playwright/mcp@latest`,
        a dependency that floats by design and resolves to something new on
        every start.
```

Each is a capability that was *stated* and not *bound*: to a lifetime, to an
owner, or to a version. The four planes are the four places a capability has to
be bound before a device can be said to have it.

```text
Node Runtime Plane      what is running here, who owns it, is it healthy
Ability Fabric          what this machine can do, and through which provider
Memory-Context Fabric   what the work remembers, across sessions and devices
Coordination Fabric     what several devices do together
```

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

The parts do not change. What changes is that the four planes are now named, and
each of the three planes that were implicit gets a resolver, a schema and a
refusal rule of its own — see [`docs/ability-fabric.md`](docs/ability-fabric.md),
[`docs/memory-fabric.md`](docs/memory-fabric.md) and
[`docs/runtime-resources.md`](docs/runtime-resources.md). An abstract plane with
no resolver is exactly the defect these documents exist to stop repeating.

**Availability is per-device declared.** ServerOS is not a product-global
always-on promise. A device declares whether it runs the node runtime, which
abilities it composes, and which resources it is willing to reconcile. A
machine with no `serverosd` is a supported configuration, not a broken one.

The path `information → requirement → analysis/design → development → test →
report` describes a *common* route, not a state machine. An agent may skip
steps, go back, work in parallel, re-analyze after testing, or ship a Milestone
and keep developing.

## The resident surface (frozen)

**Exactly one thing is resident, and it is `serverosd`.**

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

## Four planes

The planes divide *questions*, not directories — that is why they are planes
and not folders. Storage is a separate, older taxonomy that stays as it was:

```text
Planes — the questions

Node Runtime Plane      what is running here, who owns it, is it healthy
Ability Fabric          what this machine can do, and through which provider
Memory-Context Fabric   what the work remembers, across sessions and devices
Coordination Fabric     what several devices do together

Storage — where the answers are kept

Control         state/serveros/                                       node runtime state
Human           records/projects/<project>/                          cards, briefs, board views
Evidence        artifacts/work-items/<project>/<ITEM>/<RUN>/
Cloud           Feishu / GitHub                                       bodies stay with the owner
```

The two taxonomies are deliberately not aligned. One question may be answered
from two stores — "who owns this tunnel" is control state, "when was it
promised" is an evidence Run — and forcing them into one tree is how a
projection becomes a second authority.

A board view is a *recomputable projection* of card + state + claims. It is
never a second state database.

### The invariants

These are the properties a change must not break. They are stated as invariants
rather than as advice because every one of them has already been violated once.
An invariant that no test or resolver enforces is where the next violation
lands, so each names the enforcement that exists today.

```text
1  One architecture statement.        ARCHITECTURE.md is it; other documents link, never restate.
2  Mechanism, API, procedure, package stay apart.
                                      serverosd / MCP / Skill / Plugin, one job each.
3  Nothing is authority by being written down.
                                      maps, projections and summaries are regenerated, never hand-fixed.
4  Device identity is authored.       A hostname, UUID or address is a route, never a key.
5  Availability is per-device declared.
                                      A machine with no serverosd is supported, not broken.
6  Nothing beyond a session boundary may be owned by that session.
                                      See the ownership rule below. Case A.
7  A durable resource declares durable desired state.
                                      A comment is not a declaration. Case B.
8  A managed dependency is pinned or refused.
                                      Floating versions are admitted, never silent. Case C.
9  An ability is not available because it is documented.
                                      Availability is resolved against a device, or it is UNKNOWN.
10 Evidence is written once, at a path derived from identity.
                                      project / Item / Run, never from cwd or a guess.
11 An unavailable provider is not an empty answer.
                                      runtime/resource_anomaly.py grades only what it could evaluate.
12 Unattributed is not reclaimable.   A resource with no proven owner may be reported,
                                      never proposed for removal. runtime/hygiene.py admission.
13 An irreversible action needs evidence this is not the only copy.
                                      runtime/hygiene.py — REMOVE_VOLUME without a verified
                                      backup is refused, with approval or without it.
14 Unsupported is refused by name, never degraded quietly.
                                      HumanControlUnsupported · ActionNotSupported ·
                                      per-verb action_support on the provider instance.
15 Every place the code can change the world is declared once, and checked in
   both directions.                   skills/serveros/scripts/mutation_guard.py ·
                                      runtime/actuator-contract.yaml.
16 The surface that decides is not the surface that delivers.
                                      serverosd evaluates and records an intent and
                                      holds no transport; `serverosctl alert sync` is
                                      the only surface that does, and it cannot act.
                                      tests/test_core_freeze.py (the daemon's closure)
                                      · tests/test_alert_authorization.py.
17 A worker is not a daemon.           Exactly one resident unit exists, and every
                                      other unit in the tree is provably
                                      non-resident — `Type=`/`Restart=`, read rather
                                      than trusted. tests/test_core_freeze.py::The
                                      ResidentSurfaceIsClosed ·
                                      runtime/serveros-alert-sync.{service,timer}.
```

Invariants 1–5 and 10 were already implicit in the frozen statement and are
written down here so that "it was always understood" stops being an argument.
Invariants 6–9 are new, and each is a real failure rather than a hypothetical:
they are Cases A, B and C, and the last is the `declared-but-absent` pattern
this product has now hit often enough to name.

Invariants 11–15 arrived with the resource-observation round, and they are
different in kind from 6–9 in one way worth stating: each is the *fixed* form
of a defect that round found **in this product's own readers**, not in the
world it observes. Seven of those readers answered a question nobody had asked
them — a timeout reported as an empty cache, an unreadable log as a small one,
a comparison in which every row failed as "every registration is in desired
state" — and the test suite was green through all seven. 11 is that whole
family, named once.

Invariants 16–17 arrived with the delivery runtime. **16 is enforced more
weakly than it reads, and the difference is measured rather than glossed**: the
daemon's loader closure retains the *capability* to reach the network —
`runtime/probes.py` opens an outbound connection for the `http-health` and
`mcp-tools-list` probe kinds — and the property holds today **by registry
contents and not by construction**: none of the seven live registrations
declares either kind, so no HTTP probe runs, and adding one registration is all
it would take to make one run. The gate therefore does not claim "nothing in
the closure can reach the network". It claims the set of things that can is
exactly the set somebody has looked at and written down, so a second one is a
red test. Repairing it properly is an architecture change — moving the HTTP
probes behind a provider, or refusing those kinds from the daemon's own probe
engine — and is recorded in the roadmap rather than improvised here.

### The ownership rule (new)

> **Anything that must outlive the Agent session that caused it requires an
> explicit durable owner and an explicit lifecycle. An Agent session may cause
> creation; it may not be the owner of record.**

That single sentence is what Case A needed. The tunnel was not misconfigured —
it was correctly configured for the lifecycle it had been given, and nobody had
ever asked it to be anything else. The rule makes the question compulsory at
registration: a resource whose only owner is a session is either refused or
downgraded to `disposable`, and it is never the one a task depends on.

### What ServerOS does not promise

ServerOS does not promise that an ability works. It promises that a question
about an ability has an answer: available, degraded, unavailable, incompatible,
or unknown — and *why*. A node that cannot resolve an ability says `UNKNOWN`
with a reason. It never says nothing, and it never says "should work".

ServerOS does not promise durable memory is complete. It promises that memory
has a named authority, a version, and a rebuild path, so that a projection can
always be discarded and recomputed rather than argued with.

## Unify the control plane, not the execution personality (frozen)

> **One identity, one ability vocabulary, one memory, one set of device and
> resource facts, one ownership rule, one safety boundary, one coordination
> model — and as many ways of executing as there are runtimes and devices.**

This is what the four planes are an implementation of, and it is written down
because it is the one thing a product of this shape gets wrong by default.

Unifying execution is easy to attempt and impossible to finish. Claude Code and
Codex do not have the same tool surface; the Linux node and the Windows node do
not have the same filesystem; an Agent turned loose on a machine genuinely
needs to use what that machine has. A product that tries to make those agree
ends up maintaining a second, worse copy of each runtime — which is the
`declared-but-absent` pattern with a maintainer attached.

So the line is drawn by **question**, not by capability:

```text
Unified — declared once; every runtime and device gets the same answer
  identity        which machine, which Item, which Run, which session
  ability         what a kind of work is, and how it is resolved here
  memory          what the work remembers, and which layer is its authority
  device facts    what this machine is, and what it is willing to own
  ownership       who owns a resource, and since when
  safety          what may be done without a person, and what may not
  coordination    what several devices do together

Free — varies per runtime and per device, and must stay free
  how an Agent is invoked, and what one turn looks like
  which provider implements an ability on this machine
  where a device keeps its source, toolchain, evidence and state
  how a runtime materializes an MCP server or a Skill
  how a device is administered
```

Two consequences follow, and both are refusals rather than features.

**A capability difference is a resolution, not a second identity.** Codex and
Claude Code are one ability question answered twice, not two products. When a
runtime-only difference changes what a node can do, it belongs in **that
device's binding** — the same ability, resolved differently here — and never in
a new top-level name for the runtime. The identity is the runtime *family*;
per-surface differences are data about one installation, and they are resolved
per device, per operation. This is developed in
[`docs/agent-runtime-model.md`](docs/agent-runtime-model.md).

**A device difference is a path, not a semantic.** The nodes converge on the
same questions and not on the same directories. `fleet/profiles.yaml` binds one
closed role vocabulary to unrelated physical paths per device, so a consumer
asks for `workspace.src` and never learns whether that is a POSIX path or a
drive letter.

The test for any proposal under this principle is which list it touches.
Unifying one of the freedoms is how the second copy gets built. Preserving a
difference in the unified list is how the four-plane framing is quietly
abandoned — and that is not hypothetical either: it is what "ServerOS = a
control plane" did to abilities, memory and a second device, and each of those
had to be re-derived from a live failure.

### Where the unified answers are not yet real

Naming a question as unified does not make it answered, and this product has a
documented tendency to let the declaration run ahead of the mechanism. Two of
the seven above are, today, **statements of intent with no resolver behind
them**, and they are listed here so that reading this section does not confer
on them a status they do not have:

```text
runtime facts   version and features are declared fields with no reader.
```

The two that were on this list until v1.2 are worth keeping in view, because
each one closed by shrinking the claim rather than by widening the code:

```text
coordination    CLOSED (§17) — session_role at create, an immutable Item for
                an execution thread, and three refusals. It was not answered
                by a schema; it was answered by discovering that a session
                must say what it is FOR before anything can be enforced.
memory recall   CLOSED (§18) — memory/scope.py. It was not answered by an
                access-control layer; it was answered by one field on the
                record and a required `reader` on the read path, because the
                phrase the prose had used all along was "the promotion is the
                consent, and it is recorded".
```

[`docs/fabric-v1-roadmap.md`](docs/fabric-v1-roadmap.md) is the ledger: what
exists, what is missing, which real failure each gap answers, and what has to
happen first. A unified answer that is not in that ledger's "exists" column is
not a unified answer yet.

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

Round 9 froze this statement and closed with **"no more architecture
expansion"**. This is the round that supersedes that sentence, and it does so
openly rather than by quietly editing around it.

Round 9 was right about the thing it was protecting. What it called expansion
was Core design with no failure case behind it, and there was plenty of that —
the four-plane framing is not a licence to resume it. The two rounds differ in
one testable way: **every plane named above arrived with a failure that already
happened, and left with a resolver, a schema and a refusal.** The Node Runtime
Plane already had all three. The other three did not, which is why the failures
were possible.

The freeze is therefore amended, not lifted:

```text
Closed       the four planes and the invariants above.
Closed       a plane may not be added without a named real failure.
Open         the resolvers, schemas, adapters and fixtures that make each
             plane answerable — and nothing else.
Closed again anything that improves none of the three answers:
             can this machine do it · does the work remember it · who owns it.
```

### The Fabric V1 round, and what it added

The round after the release added one thing and refused several, and the way it
added it is the part worth recording. A full product-architecture design was
written for the next version — runtime families, per-device bindings, an
ability operation vocabulary, five memory semantics, coordination, human
control, monitoring and alerts. It was **not** integrated as written, for the
reason the whole document exists:

```text
it described mechanisms as though they were present
```

The design was checked against the tree, line by line, and the result is worth
stating plainly: most of its fabric was already built — the four-plane framing,
the eight-axis resolver, the five words, the three memory layers, the pack
contract, the HumanControl capability table with its honest `UNKNOWN`s — and
three of its planes were **not built at all**, not even as a contract. So the
round split the design in two:

```text
frozen here    the principles that decisions must be consistent with, and
               the invariants that already name an enforcement
recorded there the mechanisms that do not exist yet, each with the failure it
               answers and what has to be true first — docs/fabric-v1-roadmap.md
```

The unifying principle above is frozen. The roadmap is explicitly **not** a
commitment that any of it will be built. A gap that is written down is a
question; the same gap written as architecture is a lie with a maintainer.

The next permitted actions are the ones in this round's implementation order,
then promotion / cutover of the built candidate, then porting a new device
profile. A new Core module, a fifth plane, or a service added because it would
be tidy is still refused by the same test Round 9 used.
