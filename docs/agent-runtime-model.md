# The Agent Runtime Model

The question: **what is an Agent runtime, as far as ServerOS is concerned, and
what is a *difference between two of them* a difference in?**

The answer is one sentence, and the rest of this document is what it costs:

> **A runtime family is an identity. A particular installation of it on a
> particular machine is data. A feature it happens to have is neither — it is a
> resolution made per device, per operation.**

The canonical contract is
[`abilities/agent-runtime-contract.yaml`](../abilities/agent-runtime-contract.yaml).
This document is the reasoning; where the two disagree, the contract is the one
that is executed and this one is wrong.

## The identity, and the growth rule

```text
ids: [claude-code, codex]        abilities/agent-runtime-contract.yaml:44
```

Two. The list is closed, which is a decision rather than an observation about
what has been implemented so far. A runtime is referenced as
`agent-runtime:<id>` and the ref is parsed and minted in one place —
`abilities/target.py:187` and `:183` — and `parse_runtime_ref` refuses
anything that is not one of the declared ids. There is no free-form path into
this vocabulary.

The growth rule matters more than the list. When a new runtime is added, the
contract states what else has to change:

> *"Adding a runtime means adding an `instruction_surfaces` and an
> `mcp_surfaces` entry and nothing else, or the parity comparison will fail and
> name the field that disagreed."*
> — `abilities/agent-runtime-contract.yaml:194`

That is the shape to preserve. A new runtime is a **new family**, declared
once, and the comparison that fails tells you which declaration you forgot.

### Not a synonym for a managed runtime

`agent-runtime` is also a **kind** of managed runtime — an agent's supervisor
process on this host (`runtime/managed-runtime-contract.yaml:65`,
`runtime/attribution-contract.yaml:64`). The two objects share a name and
nothing else:

```text
agent-runtime:codex        a capability record — what this runtime can consume
kind: agent-runtime        a resource record  — a process on a device, with an owner
```

The contract says so at `:16`: it *"is NOT a second definition of what a runtime
is."* A reader who conflates them will conclude that installing Codex gives the
node a runtime record, which is the same error as reading a registry entry as a
capability — invariant 9, with a different noun.

## Membership is not a binding

This is the distinction the whole document exists to make, and today only the
first half of it is real.

```text
membership   which runtimes exist on this device    fleet/registry.yaml, `agents:`
binding      which version, with which features,
             materialized where                     — does not exist
```

Membership is declared per device and is wired:

```yaml
# fleet/registry.yaml:31, :46, :58 — all three enrolled devices
agents: [claude-code, codex]
```

Read as `Device.agents` (`fleet/transport.py:174-180`), checked against the
closed id list by `abilities/runtime_parity.py:129-146` `check_registry()`
(a device's `agents` must be a subset of `ids`), and surfaced on the device row
by `fleet/view.py:227-239`, which probes `agent-runtime:<first declared id>`.

A binding record does **not** exist. `grep -rn "DeviceRuntimeBinding\|runtime_binding"`
over the tree returns nothing. The contract intends one — `:23-27` says a
runtime record is device-independent *"so that one runtime be described once and
installed on three machines with three different versions of it"*, and `:37`
and `:193` both assert that installations are *"already modelled — one per
device, in the registry, with an owner"* — **but there are no installation
records in `fleet/registry.yaml`.** No per-device runtime version is declared
or consumed anywhere in this repository.

The one per-device, per-runtime fact that does exist lives somewhere else
entirely: `runtime/ability_projection.py:103-110` reads
`installations[].device_id` and `.agent` out of the **ability-management**
manifest. That is *per ability, per device, per agent* — it says where a Skill
is installed, not what a runtime is. It is a real fact and it is not this one.

So the honest statement of the model today is:

```text
identity        real, closed, wired
membership      real, per device, wired
binding         declared as already existing; absent
version         declared as a required field; never read
features        no vocabulary exists, not even declared
```

## What *is* per-runtime today

Two maps, and they are the only ones:

```text
instruction_surfaces   which files a runtime reads at session start
mcp_surfaces           one key, one file, per runtime
```

`{path_role, relative}` pairs (`:88-107`), read by `runtime/cli.py:363-371` and
compared by the parity checker. Everything else in the contract's
`capabilities:` block is a **single global value**:

```text
consumable_provider_kinds   [skill, mcp-server, cli, local-service,
                             remote-service, builtin]     — one list, not per-runtime
skill_discovery             native                       — one value, not per-runtime
session_lifecycle           true                         — one value, not per-runtime
```

The first is wired and load-bearing: `fleet/agent_control_router.py:154-155`
and `abilities/target.py:541` both read it, and it is what makes a provider the
runtime cannot consume `AGENT_UNSUPPORTED` rather than "missing" (`:46-49`).

The second and third are read by **nothing**. `runtime_parity.py:42` mentions
`skill_discovery` in a docstring; no code reads either. And the parity checker
knows: `runtime_parity.py:41-45` states outright that comparing two records on
single global values *"looks like the comparison is vacuous"*, and explains
that it is not an oversight — the values are genuinely properties of the
product, not of one runtime. That is an honest note about a real thing, and it
is also the reason a per-runtime `features:` map would not be a small edit: the
vocabulary it would live in does not exist.

### `version_observed_by`, and what a required field with no reader means

```text
record.required_fields: [id, name, version_observed_by, capability_probe]   :29
```

Neither `version_observed_by` nor `capability_probe` is read by any code.
`capability_probe` appears once in the tree — in this line. `version_observed_by`
appears three times, all inside this file.

The contract does say what a version *is* for:

```text
version_observation.required: [probe, observed_at]                          :145
rule: "a version with no `observed_at` is refused."                         :147
```

That rule has no enforcer. A version without a date is not refused anywhere,
because no version is read anywhere. This is worth writing down precisely
because the surrounding prose is *correct* — the reasoning at `:137-142` about
why a remembered version and a measured version are different facts is exactly
right, and it is reasoning nobody executes. It is
`mem:a-declaration-with-no-reader-never-fails` in its purest form: a field in
`required_fields:` is read by the next author, who believes it.

## Why CLI and desktop must not become two identities

The temptation is concrete and reasonable: Codex running as a CLI on the Linux
node and Codex running as a desktop app on Windows are not the same thing.
They do not have the same surfaces available, they are not started the same way,
and a rule that holds for one may not hold for the other. The obvious fix is
`codex-cli` and `codex-desktop`.

That fix is refused, and the reason is what the id is *for*.

`agent-runtime:<id>` is a key into the **ability question** — it is one of the
eight inputs to `EffectiveAbility` (`abilities/resolver.py:68-77`). Making the
surface part of the identity means every ability resolution, every provider
adapter step (`abilities/target.py:512-550` `_step_adapter()`), every registry
`agents:` list and every parity comparison has to be written twice, and then
kept in agreement by hand. That is precisely the second copy
[`ARCHITECTURE.md`](../ARCHITECTURE.md) says adapters must never become.

The surface difference is real, so it has to live somewhere. It belongs in the
**device binding**: the same family, resolved on *this* device, on *this*
install, for *this* operation — which is where it can be a recorded observation
with a date instead of a name that quietly encodes an assumption.

The shape already exists one plane over, and it is worth copying rather than
inventing. Transport capability levels are exactly this kind of per-entity,
closed, three-valued vocabulary:

```text
fleet/transport-contract.yaml:112    capability_levels.order — 9 closed values
fleet/transport-contract.yaml:155    CapabilityAvailability
                                     ∈ {AVAILABLE, EXTERNAL_ONLY, NOT_GRANTED, UNKNOWN}
```

Nine levels, declared once, resolved per transport, and `UNKNOWN` is one of the
four answers so that "we did not ask" cannot be read as "no". A runtime feature
vocabulary would be the same object with a different subject:
`fleet/transport.py:441-493` is the precedent for how one gets computed and
sold.

## Adapters: where a runtime reads, not what a runtime must have

There are three different things called an adapter in this tree, and the
distinction is load-bearing:

```text
runtime adapter       Claude/Codex — plugin manifests, parity check
provider adapter      adapter:<runtime>/<provider> — a materialization address
provider implementation   mcp/botmux_provider.py — code, not a declaration
```

Only the middle one is a record. Its shape
(`abilities/provider-contract.yaml:190-202`) is:

```yaml
shape:
  runtime: claude-code
  surface: {path_role: home, relative: ".mcp.json"}
  key: "mcpServers.playwright"
```

Read it as a sentence: *on this runtime, this provider is materialized by
writing this key in this file.* `abilities/target.py:512-550` `_step_adapter()`
executes exactly that.

What it is **not** is a requirement. There is no `requires: {runtime, features}`
anywhere, and requirement-checking is done by two other mechanisms:

```text
provider-side requirements:   device capabilities, evaluated in resolver.py:154
consumable_provider_kinds:    the runtime's global consumable set
```

So the current model answers *"can this runtime consume this provider at all"*
and *"does this device have the capability this provider needs"*, but not
*"does this runtime have what this particular surface requires"*. That third
question is the one a per-device binding would answer, and it is the question
the `features:` proposal is actually about.

## What a runtime is not

The contract closes this explicitly at `:180-184`:

> **A runtime is not a provider and never becomes one.** Collapsing them would
> let "Codex can do it" stand in for "this machine can do it", which is the
> union error the Ability Fabric is an intersection to prevent.

Its rules block (`:186-196`) is worth reading as the summary of the model:

- A runtime executes and materializes. **It decides nothing.**
- No runtime-specific fact lives outside an adapter.
- A runtime record carries no path and no device.
- Adding a runtime changes two maps and nothing else.

And one more, from the ownership rule: an agent runtime **is** a session
boundary, so it is exactly the thing that may not own a durable resource —
`may_own_lifecycle_classes: [session-scoped, disposable]` (`:122-124`). It is
also the candidate most likely to be proposed as an owner, because it is
present, it is running, and it already supervises processes. Case A is the
record of what that costs.

## What is missing

The roadmap keeps the full ledger. The entries specific to this page:

```text
absent    a per-device runtime binding record (version, features, install)
absent    a runtime feature vocabulary — no `features:` key exists anywhere
absent    any reader for version_observed_by or capability_probe
absent    an adapter `requires:` — requirements are checked provider-side only
global    skill_discovery and session_lifecycle are single values with no reader
dead      fleet/agent_control_router.py:179-181 reads
          getattr(provider, "runtime_adapters", None) and no provider in the
          tree defines that attribute — a branch that can never be taken and
          cannot fail
```

None of these is a defect in what was built. Each is a place where the
declaration has run ahead of the mechanism, which is the pattern this product
already has a name for — and the reason the model above is written as *what is*
rather than as *what should be*.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the unifying principle, invariant 9
- [`claude-codex-parity.md`](claude-codex-parity.md) — what the two runtimes must share
- [`ability-fabric.md`](ability-fabric.md) — where a runtime becomes one of eight inputs
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — the gap ledger and its order
