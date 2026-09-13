# Ability Fabric

The Ability Fabric answers one question, for one device, at one moment:

> **Can this machine do this thing, right now — and if not, why not?**

It exists because the product could already say what abilities *exist* and could
not say what a given device could *do*. Three devices share one catalogue and
three different realities; `fleet/profiles.yaml` has three environment profiles
while `plugin/profiles/` holds one composition, and nothing in between resolved
that gap. The gap had a symptom that looked like a bug and was not one: a device
was reported as able to do something, and the command failed.

This document is the vocabulary and the resolution rule. The schema is
`fleet/ability-provider-contract.yaml`; the resolver is `runtime/capability.py`
and it is pure — no filesystem, no network, no clock.

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

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the four planes and the invariants
- [`capability-discovery.md`](capability-discovery.md) — how an agent is told, natively
- [`memory-fabric.md`](memory-fabric.md) — what the work remembers between sessions
- [`runtime-resources.md`](runtime-resources.md) — who owns what is running
