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

## Complete Fleet V1 provider acceptance — 2026-10-17 owner revision

[Per-device Fleet topology](architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md) and [full V1 campaign](plans/plan-02-complete-fleet-v1-delivery-campaign.md) require **actual Claude Code and Codex** installation/authentication/CloudCLI provider and Session lifecycle on authorized profiles for Tokyo, Dell A6000 and Windows, subject to owner login and real platform support. CC-first local Fleet Plugin acceptance is an intermediate milestone; the former V2-only Codex release deferral does not apply to the final Fleet V1 target. Catalog membership, a declared runtime binding, a Codex binary version, or a requested/default model are **not** live acceptance.

Measure five distinct axes: Device stable identity; OS principal / credential owner; Runtime family and actual provider instance; model requested/resolved/effective + permissionMode; supported Session create/list/read/send/run/interrupt/continue + verified grants. Windows desktop/app-server and CLI variants are **surface differences**, not new top-level runtime identities. Do not fall back to another account or model on an unauthorized route. If a provider cannot show effective model or method, report UNKNOWN instead of inventing it. Track per-profile acceptance in [R1 #248](https://github.com/ProfessorZhi/serveros/issues/248).

## The identity, and the growth rule

```text
ids: [claude-code, codex]        abilities/agent-runtime-contract.yaml:69
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
> — `abilities/agent-runtime-contract.yaml`, the `addition` rule

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

The contract says so in its header: it *"is NOT a second definition of what a runtime
is."* A reader who conflates them will conclude that installing Codex gives the
node a runtime record, which is the same error as reading a registry entry as a
capability — invariant 9, with a different noun.

## Membership is not a binding

This is the distinction the whole document exists to make, and since W7 both
halves of it are real.

```text
membership   which runtimes exist on this device    fleet/registry.yaml, `agents:`
binding      which version, on which surface,
             established when, by which probe       fleet/registry.yaml,
                                                    `runtime_bindings:`
```

`agents:` is still there. It is no longer the authority — it is a **projection**
of `runtime_bindings`, and where the two disagree the binding is the answer.
The word for the disagreement is `BINDING_MEMBERSHIP_MISMATCH`, and it is
raised by `abilities/runtime_binding.py#membership_findings()` and turned into
a failed check by `abilities/runtime_parity.py#check_registry()`.

The choice between "the binding is authoritative" and "both are" was made
rather than avoided: two lists that disagree are two statements about one
device, and a checker that silently preferred one would make the other
unfalsifiable. So the disagreement is reported and the binding is what
initialises. Both directions are reported separately, because they mean
different things — an agent with no binding is a runtime the device claims and
cannot resolve; a binding with no agent is a runtime this device resolves and
the older view does not know about.

```yaml
# fleet/registry.yaml — all three enrolled devices, still
agents: [claude-code, codex]
runtime_bindings:
  - {runtime_ref: agent-runtime:claude-code, declared_at: 2026-09-14}
  - {runtime_ref: agent-runtime:codex,       declared_at: 2026-09-14}
```

Read as `Device.runtime_bindings` (`fleet/transport.py:196`), with `agents`
kept as `Device.agents` for the consumers that already read it. The device row
serves the bindings under `agent_runtimes.bindings` and names
`runtime_bindings` as the authority (`fleet/view.py#_agent_runtimes`).

What the binding carries and what it does not is the whole of §3, and the split
is the one the rest of this document argues for: **`runtime_ref` and
`declared_at` are authored; surface, version and features are observed.** A
surface written into the registry for a laptop nobody has queried would be
indistinguishable in the record from one written after checking, so the
registry holds none of them. `fleet/view-contract.yaml#binding_is_not_a_measurement`
states it as a rule at the level the view is held to.

The record the contract always intended now exists, and it is
`abilities/runtime_binding.py`. The shape is the contract's (`bindings:`), and
the module is the one place a caller asks what a device's runtime relationship
is — so the Ability resolver, the fleet view and the CLI do not each grow a
private reading of the registry.

What it is **not** is a store. There is no `bindings.json`, no writer, no cache
and nothing on disk to go stale. A binding is resolved from two inputs the
caller already has — the authored registry and an observation — and returned.
That is `abilities/provider_instance.py`'s `persistence: none` for the same
reason: a stored observation is a memory of a past machine being read as a
statement about the present one.

```text
identity        real, closed, wired
membership      real, per device, and now a projection of the binding
binding         real, per device, resolved — `abilities/runtime_binding.py`
version         observed by a probe carrying `observed_at`, and refused without one
features        a closed four-valued vocabulary, answered per binding
```

## What *is* per-runtime today

Three maps now, and they are the contract's whole per-runtime surface:

```text
instruction_surfaces   which files a runtime reads at session start
mcp_surfaces           one key, one file, per runtime
consumable_provider_kinds   which provider kinds a runtime can be handed
```

`{path_role, relative}` pairs for the first two, read by `runtime/cli.py`'s
surface measurement and compared by the parity checker.
`consumable_provider_kinds` used to be a single global list, and it is the one
that changed shape in W7:

And one thing that is deliberately **not** in that list, because it is not a
per-runtime map and cannot be made into one:

```text
session_drivers        which drivers must fire at session start, and where
```

A driver is declared as a driver — one entry naming the runtime that fires it,
the event, the surface it must be registered on, the marker that identifies the
registration, and the program the registration would run. The question it exists
to answer is "is this driver wired on this node", and the two maps above cannot
ask it: a hook's file is present on a node where the driver fires nowhere, so a
presence probe on `~/.claude/settings.json` answers `true` while the collector it
was meant to fire never runs. Two runtimes do not both have a hook surface and
must not be forced to declare one, so this is a register of drivers rather than
a map keyed by runtime. `abilities/runtime_parity.py#check_drivers` reads it;
`serverosctl device agents` measures it.

```text
before   consumable_provider_kinds: [skill, mcp-server, cli, …]   one list
after    consumable_provider_kinds: {claude-code: [...], codex: [...]}
```

It is wired and load-bearing at both ends: `abilities/resolver.py#_input_agent_runtime()`
and `fleet/agent_control_router.py#runtime_record()` read it, and it is what
makes a provider the runtime cannot consume `AGENT_UNSUPPORTED` rather than
"missing".

The other two former globals, `skill_discovery` and `session_lifecycle`, left
the `capabilities:` block entirely. They are now entries in
`product_invariants:` — statements about the *product*, not about one runtime
or one device — and the question they answer moved to the feature axis:

```text
skill.native        AVAILABLE for every declared runtime, from the declaration
session.lifecycle   AVAILABLE for every declared runtime, from the declaration
```

The reason for the move is the reason `runtime_parity.py` gave for the old
arrangement being *not* an oversight: a comparison between two values that are
always equal cannot differ, so leaving them in `compared_fields` produced a
check that could only ever report agreement. The parity block now enforces the
shape rather than trusting it — `PARITY_COMPARES_A_SHARED_VALUE` fails any
`compared_fields` entry whose value is not keyed by every declared runtime id,
so restoring a global to that list is a red check instead of a quiet
regression. One entry remains: `consumable_provider_kinds`.

### `version_observed_by`, and what happened to `capability_probe`

```text
record.required_fields: [id, name]
```

Both fields left that list, and the two departures are different findings
rather than one tidy-up. Both had been required by a record no code ever wrote
and no code ever read.

`version_observed_by` was **implemented**, and it moved to where its subject is.
How a runtime's version is established is a property of the runtime *family*,
not of a record about one device, so it is now
`version_observation.version_observed_by` — a two-entry map — and
`abilities/runtime_binding.py#version_observation()` refuses a version whose
probe is not the one declared for that family. The contract's rule now has an
enforcer:

```text
version_observation.rule: "a version with no `observed_at` is refused."
```

`capability_probe` was **deleted**, and it is the honest half of the pair. It
named how to probe what a runtime can do, written before there was a vocabulary
of things to probe *for*. The vocabulary now exists
(`runtime_features.order`), its producer exists, and a second mechanism that
described the same probe in a different place would be the second copy this
document spends its length arguing against.

The tree still has one lesson from that block worth keeping: a field in
`required_fields:` is read by the next author, who believes it. Both fields were
believed for as long as they were listed, and neither was ever executed. What
changed in W7 is not that someone remembered them — it is that
`tests/test_declared_implementations.py` fails when a declared field has no
reader, and that the exemption table it consults cannot keep an entry whose
field has since gained one.

### The feature vocabulary, and the line it may not cross

```text
runtime_features.order: [mcp.client, skill.native, instruction.home,
                         instruction.workspace, session.lifecycle,
                         app.server, gui]
```

Seven, closed, and each of them a real surface of a real runtime — `app.server`
is the Codex app-server (`mcp/codex_app_server_provider.py`), not an invented
axis. `native.extension` was considered and is explicitly **omitted** in the
contract rather than marked planned: there is no native-extension surface
anywhere in the tree, so declaring it would have produced a feature nothing
could ever answer about.

Every state is one of four, and the fourth is what makes the vocabulary a
measurement rather than a restatement:

```text
AVAILABLE      the binding has it, and that was established
UNAVAILABLE    the binding does not have it, and that was established
UNKNOWN        nobody established it — NOT a no
INCOMPATIBLE   this runtime FAMILY has no such axis
```

`INCOMPATIBLE` is the axis-applicability answer, and it comes from
`runtime_features.axes`, not from a branch on the runtime's name.
`abilities/runtime_binding.py#feature()` is the only function that answers the
question, and a test asserts there is no runtime id used as a *value* anywhere
in that module — so `if runtime == "codex": app_server = True` is
unrepresentable rather than merely disallowed, and "Codex Desktop should have a
GUI" has nowhere to be written.

The order in `feature()` is the design: applicability first, then a product
invariant, then the observation, then `UNKNOWN`. Nothing reads a runtime's name
to decide a state.

## Why CLI and desktop must not become two identities

The temptation is concrete and reasonable: Codex running as a CLI on the Linux
node and Codex running as a desktop app on Windows are not the same thing.
They do not have the same surfaces available, they are not started the same way,
and a rule that holds for one may not hold for the other. The obvious fix is
`codex-cli` and `codex-desktop`.

That fix is refused, and the reason is what the id is *for*.

`agent-runtime:<id>` is a key into the **ability question** — it is one of the
eight inputs to `EffectiveAbility` (`abilities/resolver.py#resolve()`, the `inputs_seen` loop). Making the
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
four answers so that "we did not ask" cannot be read as "no". The runtime
feature vocabulary is that object with a different subject, and it copied the
shape rather than inventing one: `runtime_features.order` for the closed list,
`runtime_features.states` for the four answers, `bindings.surfaces.order` for
the surfaces a binding may report. `fleet/transport.py#CapabilityAvailability`
is the precedent for how one gets computed and sold.

The refusals were copied too, and that is the part that keeps the identity from
growing names. `agent-runtime-contract.yaml#bindings.authority` states that
`agents:` is a projection; the mismatch word is
`BINDING_MEMBERSHIP_MISMATCH`, and the six forbidden spellings —
`codex-cli`, `codex-desktop`, `codex-vscode`, `claude-desktop` and their kind —
are not refused by a check that could be relaxed. They have nowhere to live:
the id list is closed, `parse_runtime_ref` refuses anything not in it, and the
surface is a field of a *binding*. `codex` on a desktop surface is one family
with a different observation, which is what §13 asks for and what
`tests/test_runtime_binding.py#IdentityTests` runs.

## How to ask

One surface, and it is an existing command. There is no `runtime binding`
verb and no `bindings.json` to read, because a second way to ask would be a
second answer to one question.

```text
serverosctl device agents                      L0-ish: the authored bindings, no probe
serverosctl device agents --probe              reaches the machine for version + features
serverosctl device agents --probe --json       the record, for a program
serverosctl device agents --device device:work-fj --probe
```

Without `--probe` a binding is returned with `observation: UNKNOWN` and a
reason saying the probe was not requested, which is the honest answer and the
cheap one. With `--probe` the CLI runs
`agent-runtime-contract.yaml#version_observation.cli_program` for each declared
family, takes the first line of `<program> --version` verbatim, and measures
the instruction and MCP surfaces
the contract declares — no more. It installs nothing, upgrades nothing, writes
nothing, and does not touch a user's agent configuration.

Features with no producer are **omitted**, so they come back `UNKNOWN` rather
than being inferred: `gui` and `app.server` are not probed by the CLI, and on
a host where the Codex app-server has not been reached `app.server` reads
`UNKNOWN` even though the family declares the axis. That is the §6 proof, and
it is visible in the live output rather than merely asserted.

## Adapters: where a runtime reads, not what a runtime must have

There are three different things called an adapter in this tree, and the
distinction is load-bearing:

```text
runtime adapter       Claude/Codex — plugin manifests, parity check
provider adapter      adapter:<runtime>/<provider> — a materialization address
provider implementation   mcp/botmux_provider.py — code, not a declaration
```

Only the middle one is a record. Its shape
(`abilities/provider-contract.yaml#adapters.shape`) is:

```yaml
shape:
  runtime: claude-code
  surface: {path_role: home, relative: ".mcp.json"}
  key: "mcpServers.playwright"
```

Read it as a sentence: *on this runtime, this provider is materialized by
writing this key in this file.* `abilities/target.py#_step_adapter()` executes
exactly that.

What it is **not** is a requirement. There is no `requires:` on an adapter, and
requirement-checking is done by three other mechanisms, at three different
subjects:

```text
does this DEVICE have the capability?     device capabilities, resolver.py
can this runtime FAMILY consume it?       consumable_provider_kinds, per runtime
does this INSTALLED runtime have it?      required_runtime_features, per binding
```

The third is the one W7 added, and it is the one an adapter is not allowed to
be. `abilities/provider-contract.yaml#required_runtime_features` names the
features a provider needs; the vocabulary is not restated there but referenced
(`agent-runtime-contract.yaml#runtime_features`), and
`abilities/resolver.py#_input_runtime_features()` is the reader.

The subject distinction is the reason all three exist rather than one. A
provider needing `instruction.workspace` is not making a claim about the device
or about Codex: it is making a claim about *this binding of Codex on this
machine*, which is the only one of the three that a probe can settle and the
only one that can come back `UNKNOWN` without anything being wrong.

## What a runtime is not

The contract closes this explicitly under `capabilities`, in the comment
`A runtime is not a provider and never becomes one`:

> **A runtime is not a provider and never becomes one.** Collapsing them would
> let "Codex can do it" stand in for "this machine can do it", which is the
> union error the Ability Fabric is an intersection to prevent.

Its `rules` block is worth reading as the summary of the model:

- A runtime executes and materializes. **It decides nothing.**
- No runtime-specific fact lives outside an adapter.
- A runtime record carries no path and no device.
- Adding a runtime changes two maps and nothing else.

And one more, from the ownership rule: an agent runtime **is** a session
boundary, so it is exactly the thing that may not own a durable resource —
`may_own_lifecycle_classes: [session-scoped, disposable]`
(`abilities/agent-runtime-contract.yaml#ownership`). It is
also the candidate most likely to be proposed as an owner, because it is
present, it is running, and it already supervises processes. Case A is the
record of what that costs.

## What is missing

The roadmap keeps the full ledger. The entries specific to this page:

```text
EXISTS    a per-device runtime binding record
EXISTS    a runtime feature vocabulary, four-valued, with a producer per feature
EXISTS    a version observed by a declared probe, refused without `observed_at`
EXISTS    `required_runtime_features` on a provider, evaluated per binding
EXISTS    the CLI read surface: `serverosctl device agents [--probe]`
LEDGER    `agents:` is still authored on all three devices. It is a projection
          and the mismatch check is live, but the migration is not finished, and
          finishing it means removing a key three device rows and the fleet view
          read. Until then `BINDING_MEMBERSHIP_MISMATCH` is doing real work and
          is not a gate over an empty surface.
LEDGER    two of the three accepted version probes have no producer.
          `package-manifest` and `service-handshake` are accepted and unimplemented,
          and the contract says so per probe rather than leaving three accepted
          probes reading as three implemented ones.
UNKNOWN   device:work-fj and device:personal-dell have bindings and no
          observation. That is not a gap to close by writing values in: nobody
          has reached either machine from this node, and the honest record is
          the binding with `observation: UNKNOWN` and a reason.
DEAD      fleet/agent_control_router.py reads
          `getattr(provider, "runtime_adapters", None)` and no provider in the
          tree defines that attribute — a branch that can never be taken and
          cannot fail.
```

Nothing in the first group was closed by writing a name into a ledger. Each is
a mechanism with a producer, a reader and a test that fails when either stops.

The second group matters as much. A binding record exists for all three devices
and there is an observation of exactly one of them, because that is the only one
this node can reach. Filling the other two columns from the Beijing row is the
specific failure §14 names, and the record is shaped so that it cannot happen
quietly: an unobserved binding has `observation: UNKNOWN` and an
`observation_because` sentence, and a test asserts it stays that way.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the unifying principle, invariant 9
- [`claude-codex-parity.md`](claude-codex-parity.md) — what the two runtimes must share
- [`ability-fabric.md`](ability-fabric.md) — where a runtime becomes one of eight inputs
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — the gap ledger and its order
