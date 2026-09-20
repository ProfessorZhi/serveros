# Ability Fabric

The Ability Fabric answers one question, for one device, at one moment:

> **Can this machine do this thing, right now — and if not, why not?**

It exists because the product could already say what abilities *exist* and could
not say what a given device could *do*. Three devices share one catalogue and
three different realities; `fleet/profiles.yaml` has three environment profiles
while `plugin/profiles/` holds one composition, and nothing in between resolved
that gap. The gap had a symptom that looked like a bug and was not one: a device
was reported as able to do something, and the command failed.

This document is the vocabulary and the resolution rule. The contract is
[`../abilities/provider-contract.yaml`](../abilities/provider-contract.yaml);
the resolver is [`../abilities/resolver.py`](../abilities/resolver.py) and it is
pure — no filesystem, no network, no clock.

## The five words

They are kept apart because collapsing any two of them produces a sentence that
is true on one machine and false on the next.

```text
Ability     WHAT      a kind of work, named once, device-free
Provider    HOW       a concrete way to do that work
Profile     WHERE     a reusable class of machine
Binding     WHICH     one concrete machine
Adapter     HOW an Agent runtime consumes a provider
```

Two more words are needed once abilities are grouped:

```text
Pack        a named set of abilities for a purpose; carries no payload
Revision    the pinned version of an ability's canonical source
```

Concretely, for one ability:

```text
Ability     browser-automation
Provider    playwright-mcp          (an MCP server)
Profile     windows-personal
Binding     device:personal-dell
Adapter     claude-code → .mcp.json  ·  codex → config.toml
```

No one of those lines implies another. The ability exists everywhere and runs
nowhere until four of them agree.

## EffectiveAbility

```text
EffectiveAbility =  AbilityDefinition
                  ∩ DeviceCapabilities
                  ∩ DeviceProfile
                  ∩ DeviceBinding
                  ∩ AgentRuntimeCapabilities
                  ∩ RuntimeHealth
                  ∩ Policy
                  ∩ TaskContext
```

This is an **intersection, not a union**, and the direction matters more than
the formula. Union-style resolution is what produced the original defect: a
capability was visible because one input mentioned it, and the other seven were
never consulted. Intersection means the default answer is "no", and a "yes" is
earned by every input agreeing.

The resolver returns a status and a reason, never a bare boolean:

```text
AVAILABLE       every input agreed
DEGRADED        available, but not by the preferred provider, or one axis is unhealthy
UNAVAILABLE     some input ruled it out
INCOMPATIBLE    the ability and this runtime cannot consume each other at all
UNKNOWN         an input could not be resolved
```

```text
Reasons (exactly one per refusal, and it names the input that refused)
AGENT_UNSUPPORTED              AgentRuntimeCapabilities
DEVICE_DEPENDENCY_MISSING      DeviceCapabilities        e.g. no interpreter, no GPU
RUNTIME_UNHEALTHY              RuntimeHealth             owner exists, probe failing
POLICY_DENIED                  Policy
NETWORK_UNAVAILABLE            DeviceCapabilities        e.g. a registry this node cannot reach
PROVIDER_NOT_INSTALLED         DeviceProfile / Binding   the how was never materialized here
```

**`UNKNOWN` is a real answer and must not be flattened into `UNAVAILABLE`.**
They call for opposite actions: `UNAVAILABLE` means find another provider,
`UNKNOWN` means go and resolve the missing input. A resolver that cannot tell
them apart produces the failure this document exists to prevent — a device
being told it cannot do something it can.

### Why "can this machine do it" is not "is it installed"

Installation is one input of eight. The three live cases each fail on a
different input while installation is fine:

```text
Case A  tunnel              RUNTIME_UNHEALTHY — installed, registered, session-scoped
Case B  outbound tunnel     PROVIDER_NOT_INSTALLED — never had a provider at all
Case C  playwright MCP      DEGRADED — installed, and resolving to a new version each start
```

A registry that answered "installed: yes" to all three is not wrong. It is
answering a different question.

## Operations: the unit that actually gets refused

`EffectiveAbility` resolves **one whole ability**. That is coarser than the
question a caller is really asking, and the gap has a shape:

```text
ability:team-document-management#read      works on every device
ability:team-document-management#restore   needs a credential this device lacks
```

One ability, one resolution, and the answer is wrong for one of the two
callers. Reporting the ability `UNAVAILABLE` because restore is impossible
takes away a working read; reporting it `AVAILABLE` makes a restore that will
fail look available. Neither is a rounding error — they are the same defect
from both ends.

The correct unit is `ability#operation`, and **this does not exist yet**.
`abilities/resolver.py` filters on `p.get("ability") == ability`
(`:348-349`), and `abilities/provider-contract.yaml` has no `operations:` key.
Adding it is on the roadmap, not in this round.

### The shape already exists, one plane over

The verb axis is built and running for **Agent Sessions**, and it is the
precedent to copy rather than reinvent:

```text
runtime/agent-session-contract.yaml:95
  support.values: [SUPPORTED, UNSUPPORTED, UNKNOWN]
  support.actions: 11 closed verbs — agent.list · session.create · session.send ·
                   session.handoff · session.stop · …

  rule: "provider support is resolved per action. A stronger provider verb
         never satisfies a narrower ServerOS verb."
```

Per-operation state lives on the **provider instance** as
`action_support: {<verb>: {state, reason}}`, read at
`abilities/target.py:283-298` `_support_state()`, and it preserves exactly the
distinction this document keeps insisting on:

```text
verb absent from a published table   → UNSUPPORTED   (target.py:293)
no table at all                      → UNKNOWN       (target.py:287)
```

Real tables exist and are asserted: Codex declares `session.resume: SUPPORTED`
and `session.stop: UNSUPPORTED` (`mcp/codex_app_server_provider.py:654-679`,
tested at `tests/test_agent_sessions.py:171-172`).

One consequence of that design is worth carrying over wholesale, because it is
subtle and it was reached deliberately: an `UNKNOWN` support state maps to
`INCOMPATIBLE`, the same reason word as a plain `UNSUPPORTED`
(`abilities/target-contract.yaml:244-261`). The reason vocabulary is closed and
has no word for *"nobody asked"*, and rather than invent one, the contract says
so in prose. The alternative — reusing `UNSUPPORTED` silently — is the defect.
**When the vocabulary cannot say the true thing, say that, rather than saying
the nearest false one.**

## Provider status is a claim with a date

A provider status is only meaningful with its inputs recorded: which device,
which runtime, which observation, when. A status stored without those is a
memory of a past resolution being read as a current one — and the whole point of
the resolver being pure is that it can be re-run instead.

```text
PERSISTED   the inputs (device, profile, binding, runtime, revision)
DERIVED     the status, the reason, and the inputs that produced them
NEVER       a status without its device and its date
```

## The three shapes of the taxonomy

The taxonomy is one logical tree, one machine graph, and one flat physical
directory. All three are correct and none may be derived from the others.

```text
Logical     the tree an Agent reasons with
            Domain → optional Subdomain → Ability        max depth three

Machine     the graph a resolver walks
            ability ↔ provider ↔ profile ↔ binding       no hierarchy at all

Physical    the directory a build reads
            ability-management/skills/<ability-id>/      exactly one level, always
```

**Physical stays flat on purpose.** The directory is a materialization address,
not a taxonomy; nesting it would make a move that re-organizes nothing on disk
still invalidate every pinned revision path. The tree lives in the logical
projection, where it can be reorganized without touching a single file's
location.

`domain.primary` names one domain per ability; `taxonomy_refs` may name more,
because an ability genuinely serving two domains is a real thing and a
single-parent tree would force a false choice. The projection carries both. The
authority is `ability-management/abilities-manifest.yaml`; the projection is
generated and carries `GENERATED — DO NOT EDIT`.

## The ability logic view

Abilities are **parallel physically and a tree logically**, and that is not a
figure of speech — it is the rule stated above, and the reason it holds is
narrower than it looks. The physical layout cannot be nested because the
directory is a *materialization address*: a nested tree would mean a
reorganization that moves nothing on disk still invalidates every pinned
revision path. The tree therefore lives where it can be reorganized for free —
in the logical projection.

What that view has to show, and the one thing it must not imply:

```text
ServerOS — product / control model        decides identity, policy, desired state
│
├── Core                                    ONE Core list, TWO members
│   ├── serveros              product entry · composition authority · bootstrap Skill
│   │                         Context/Space/Relations kernels · deterministic build
│   └── ability-management    Capability/Ability registry · reusable Skill canonical
│                             source · revision/compatibility/discovery/reconcile
│
└── Composition                             what gets packaged and installed per runtime
    ├── plugin/bundle.yaml        serveros-core              plugin name hwz-server-os
    └── plugin/profiles/*.yaml    `extends:` core            plugin name hwz-server-os-linux-work
        │
        │  A composition's members come in three shapes. There is no fourth,
        │  and these are not "modes" a member can be switched between.
        │
        ├── skills:   name + source                  procedural knowledge
        │     serveros ............... source: local               ← canonical in THIS repo
        │     runtime-management ..... source: ability-management  ← read from a PINNED revision
        │     ability-management ..... source: ability-management
        │
        ├── mcp: servers:  name + command + entry    an interface, never an authority
        │     serveros-node .......... strictly read-only node surface
        │     serveros-agent ......... may call provider APIs after serverosd admission
        │
        └── path: <module>                           copied into the artifact
              context space relations fleet abilities memory intake documents runtime bin
        │
        ▼
    Two runtime adapters, one world           Claude Code  |  Codex
    one build renders .claude-plugin/ and .codex-plugin/ from the same inputs
```

Below the composition is the only layer where abilities actually live, and it is
flat:

```text
Abilities — physically parallel, logically placed

PHYSICAL   exactly one directory level, always
           ability-management/skills/<ability-id>/
           each ability carries its own identity  ProfessorZhi/<name>  and its own pinned revision
              runtime-management · workspace-management · test-management · … 51 names in the manifest
           nesting is refused: the directory is an address, not a taxonomy

LOGICAL    the tree an Agent reasons with
           Domain → optional Subdomain → Ability          max depth three
```

### The two trees answer different questions

There is more than one tree, and they are allowed to disagree in *shape*. They
are not allowed to disagree about which abilities exist or where they are
placed — `serverosctl taxonomy validate` fails closed if they do.

```text
serverosctl ability tree     placement    "what IS under this node"       basis: domain.primary
serverosctl taxonomy tree    navigation   "what COULD be, and where"       basis: abilities/taxonomy.v2.yaml
```

The difference is not cosmetic. The placement tree is derived, so a node with
nothing under it does not exist in it. The navigation tree is declared, so it
can carry a family that is declared and **not yet occupied** — a reader asking
"where would safety work live" gets an answer even when no safety ability exists
yet. Without the declared tree the honest answer is "nowhere, the node does not
exist", which reads as *this fabric has no safety concept* when the true finding
is *the concept is declared and empty*. Those are different findings and only
one of them is true.

A node's occupancy is therefore one of four states, and they are not
interchangeable:

```text
OCCUPIED       something is placed here
EMPTY          declared, nothing under it yet
SURFACE_ONLY   declared and unoccupied on purpose — a named surface, not a gap
(v1 container) grouping carried over from the older vocabulary, NOT a family
```

### Where the word "family" is already taken

`family` is load-bearing in this product and it does **not** mean "a cluster of
abilities". Two things are already called a family, and neither is a taxonomy
cluster:

```text
runtime family        Claude Code | Codex — the identity an installation is a binding OF
seed family           the ten top-level nodes of the declared taxonomy
                      (agent · creative · human · information · memory · network ·
                       runtime · safety · security · work) — "seed", not "family", is the
                      field name precisely because the other sense owns the word
```

The ability's own placement fields are `domain.primary` and `taxonomy_refs`, and
their authority is `ability-management/abilities-manifest.yaml`. A work-
navigation reading that groups abilities into named clusters also exists
(`CAPABILITY-FAMILIES.md` in `ability-management`), and it says of itself that
it is a *view*: the only thing it declares is which cluster an ability is
navigated under, and a cluster with no member is a named gap rather than a work
item. It is not a second catalogue, and it is not this taxonomy.

## Packs

A pack is a named set of abilities for a purpose. It carries **no payload** —
names and revisions only. Two rules make packs safe to name:

```text
A pack may not name an ability that does not exist.
A pack member that cannot be resolved on the target device is reported,
not silently dropped.
```

The first rule is one this product has already broken. The `recommended:` list
in `plugin/profiles/linux-work.yaml` names `document-stewardship`, whose own
status field reads `missing` — a member declared and absent, in the same file,
in plain sight. It was recorded honestly and it was still a pack naming
something that is not there. Packs now fail on it rather than carrying it.

The second rule is what makes a pack a *question* rather than a promise: a
member that resolves to `UNAVAILABLE` on this node is reported as such, and
`PARTIAL` is a legitimate pack resolution. Silent dropping is what turns "this
pack has nine of ten members here" into a support ticket.

### A pack is a recommendation set, not an installation

That sentence is the whole of it, and it is worth unfolding because the
temptation to read it the other way is constant:

```text
a pack says    a scenario usually needs these abilities
a pack is not  an install list, a promise, or a statement about a machine
```

The consequence people get wrong is what to do when a device cannot satisfy a
member. The answer is **not** to remove the member from the catalogue. A
catalogue that shrank to what one device can do would stop describing the
product and start describing the machine — and then a second device with
Blender installed would find it undocumented, which is invariant 9 read
backwards.

```text
CLOSED        every required member resolves
PARTIAL       a required member is missing; the pack is usable and incomplete
UNAVAILABLE   every required member is missing; there is nothing to use
```

`PARTIAL` is not a failure. It is the honest answer to "can I use this
scenario here", and `abilities/packs.py:81-84` states it in exactly those
words.

### The gap: `PARTIAL` is resolved against a catalogue, not a device

The rule above says a member that "cannot be resolved **on the target
device**" is reported. The code does not do that yet, and the difference is
material rather than pedantic:

```text
packs.py:222      resolve(contract, catalogue, *, catalogue_revision, catalogue_repo)
                  — there is no device, profile, provider or runtime input
catalogue         a frozenset of ability names from the pinned manifest
                  (scripts/build.py:275-300)
so PARTIAL means  "a required member id is absent from the pinned catalogue"
declared to mean "not resolvable on the target device"
                  (abilities/pack-contract.yaml:37)
```

`packs.py:44-50` is explicit that this is deliberate and not a stub:

> *"It is not a resolver. `resolver.py` answers 'can this device do this NOW'
> and consults eight inputs; this answers one question with two inputs — is
> every name in this pack a name in that catalogue — and it is asked once, at
> build time, **about no device in particular**. A pack is a statement about
> the catalogue, not about a machine."*

So the build-time closure check is real, wired (`scripts/build.py:749`) and
worth keeping. What does not exist is the device-relative half, and no code
joins a pack to `resolver.py` or `target.py` at all. Until that exists, a
`PARTIAL` reported to a person means *"this is missing from the registry"*, and
must not be read as *"this device cannot do it"* — the two are one word apart
in the contract and unrelated in the code.

**One provenance correction while this is being read.**
`abilities/pack-contract.yaml:127` cites
`ability-management@7bc7687:ability-packs.yaml` as the declaration file. At
that revision the file does not exist; it lands at `0091d8d`. The location is
right, the revision is not, and it is recorded here rather than edited because
this round does not touch contracts.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the four planes and the invariants
- [`agent-runtime-model.md`](agent-runtime-model.md) — the runtime axis of the intersection
- [`capability-discovery.md`](capability-discovery.md) — how an agent is told, natively
- [`memory-fabric.md`](memory-fabric.md) — what the work remembers between sessions
- [`runtime-resources.md`](runtime-resources.md) — who owns what is running
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — operations, device-relative packs
