# What Fleet Next can project today — and where that stops

Read from source at the candidate tip, against the contract in
`docs/architecture/FLEET-V1-TOPOLOGY-OPERATIONS.md` §2/§4 and
`docs/plans/plan-02-…` §1. Nothing here is measured on a device; it is what the
code can and cannot answer.

## The catalogue, as the plan defines it

    34 tools = 10 authored Fleet + 22 CloudCLI native mirror + 2 capability reads
    6 scopes = read / send / create / control / approve / navigate

The six are **possible** scopes. Advertising six is not holding six: issuance is
`requested ∩ granted ∩ grantable`, where `scopes_supported` is the six and
`scopes_grantable` is the binding's own ceiling (`fleet_mcp/oauth.py`).

## The two capability reads, and exactly what they project

Both are read-scoped, typed tools on the served surface — not a side channel.

**`fleet_cloudcli_native_manifest`** — the whole inherited surface,
machine-readable: one entry per recorded verb with its governance class, the
native scope it needs, and the JSON schema recorded from CloudCLI 1.40.0. Each
entry carries the `recorded_from` it came from. This is the **closed set**: a
name not in it is refused rather than forwarded, which is what makes "the
surface grew a tool" a decision rather than a side effect. Project creation is
**not** in this set — it is the authored `fleet_create_project`, which goes
through the owner namespace and the provider's own `create_project` operation
rather than through a mirrored CloudCLI verb.

**`fleet_cloudcli_native_capability`** — what ONE device **actually** exposes,
`serveros/fleet-cloudcli-capability.v1`:

| state | meaning |
|---|---|
| `USABLE` | the device's `tools/list` reports `_meta["cloudcli/handlerBound"] == true`. Declared wired, not a verified workflow |
| `DECLARED_NOT_IMPLEMENTED` | the device reports `handlerBound == false` for it: a declared name whose handler is the not-implemented refusal |
| `HANDLER_BINDING_UNKNOWN` | the device's list carries no `handlerBound` evidence (older gateways). Unknown, never guessed from names or recorded default handlers |
| `UNSUPPORTED_BY_DEVICE` | the device does not declare it. The 22 mirrored names are not 22 working verbs on every machine, and a count of 22 is not evidence that any answer |
| `UNKNOWN` | the device's tool list could not be read, with a reason. Never an assumed-complete list |

it also carries `recorded_count`, `source`, `version` and `sampled_at`. A verb the
device declares that this build did **not** record is reported separately rather
than added — a name off the wire is not a capability this build decided to offer.

`placeholder_default: true` on a recorded entry is provenance about the CloudCLI
1.40.0 registry capture — the default body when deps are not injected is the
named `MCP_TOOL_NOT_IMPLEMENTED` refusal — and **not** a verdict about a device.
A running device injects those deps. The state is decided only by the typed
`cloudcli/handlerBound` bit in that device's own `tools/list`; a list that does
not carry it is `HANDLER_BINDING_UNKNOWN`, not `DECLARED_NOT_IMPLEMENTED`.
Measured 2026-10-22 against `device:tokyo-shared-dev-01`: the eight verbs whose
recorded default is the refusal all answered with real data or real handler
errors (`RUN_NOT_FOUND`, `no_quay_config`), and the later live surface reported
`handlerBound: true` for them.

## What that gives the Tokyo local Claude Code Fleet Plugin

The plan's first formal acceptance client needs authenticated `initialize` +
`tools/list`, negative authorization, managed Session lifecycle, local Core and
network health, Device Ability access, and raw receipts. Against the candidate
tip, the first five are answerable **in code** and two of them carry provenance:

* tool discovery and the closed set — from `tools/list` and the manifest;
* negative authorization — the six scopes, the node owner floor, the per-runtime
  binding, the project allowlist and the operator grant are checked *separately*,
  and each has a negative test;
* session lifecycle — the 22 mirrors plus the authored verbs;
* Core and network health — the signed Core route (`#231`), with `core_lite`
  beside `provider` rather than derived from it;
* **raw receipts** — every answer above is the receipt; there is no second,
  friendlier summary behind it.

## The gap this document exists to name

**Fleet Next has no projection for Device Ability (G1) at all.** The architecture
places G1 as *the actual Skill/Plugin/MCP/CLI/Provider implementations and their
source revisions*, discovered and invoked per device, with the express anti-goal
of **not** becoming "a second registry copied per device". The authority is
`ability-management`'s manifest: one Ability is registered once by
source/revision, while *installation / ready / supported* are observed per
device.

The two tools above are **CloudCLI-specific** — they project the 22 mirrored
verbs. They are not the device's Ability registry, and reading them as one
would be exactly the substitution the architecture forbids.

So the next step is a **projection, not a store**:

1. a read tool that answers, for one device: which Abilities are *installed*,
   *ready*, *supported*, and at what **source revision** — sourced from the
   existing `ability-management` manifest and observed device state, never a
   second registry;
2. the same requested / resolved / **effective** discipline used for
   model + `permissionMode` (`#231` `7e75455`): `effective` is `null` unless a
   device actually reported it;
3. and the scope statement the plan already makes — *being able to call* an
   Ability is a conjunction of Ability × Provider × Device Binding × Runtime ×
   Target × Health × Authorization, so a projection that reported only the first
   term would be read as the whole answer.

That is a real tool-shaped gap, it is B's file, and it needs G1's manifest shape
settled first — which is why it is named here rather than invented here.

## Not claimed

No device was contacted for any statement above; the capability reads are
described from their code and their tests. Nothing in this candidate is installed
(the installed Next revision is A `008a336`, which predates the current
`fleet-mcp-6` as well), and no
mock in this repository is evidence about a live machine.
