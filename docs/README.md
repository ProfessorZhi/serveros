# ServerOS documentation map

Start with [the product entry](../README.zh.md) and
[ARCHITECTURE.md](../ARCHITECTURE.md). The latter is the **single normative product
architecture**, consolidated on 2026-09-21. Neither README is a second authority.

## Current focused contracts

| Document | Purpose |
|---|---|
| [Context Capsule](context-capsule.md) | intent-first, sparse context, provenance, budget and recovery |
| [Capability Discovery](capability-discovery.md) | native-first discovery, typed identity, scoped loading and acquisition |
| [Claude/Codex Parity](claude-codex-parity.md) | shared semantics without invented native feature parity |
| [Safety and Concurrency](safety-and-concurrency.md) | ownership, real effect boundaries, concurrent writers and destructive safety |
| [Consolidation review](architecture/CONSOLIDATION-REVIEW-20260921.md) | inspected evidence, corrected contradictions and implementation limits |

These focused contracts explain their named architecture sections; they do not
create independent work/capability authorities or additional mandatory workflows.

## Technical specifications and existing implementation notes

The following retain useful interface, implementation and historical detail:

- [Ability Fabric](ability-fabric.md)
- [Agent Runtime Model](agent-runtime-model.md)
- [Memory Fabric](memory-fabric.md)
- [Human Control](human-control.md)
- [Runtime Resources](runtime-resources.md)
- [Fleet Controller Adapter](fleet-controller-adapter.md)
- [Space Resolver](space-resolver.md)
- [Repository Roles](repository-roles.md)

Their concrete claims must be read at the revision they describe. A versioned schema
or executable remains the implementation contract until deliberately migrated; a
new architecture sentence cannot make a nonexistent CLI/MCP operation real.

Where a technical note conflicts with the consolidated product architecture, record
that exact implementation/specification compatibility gap before changing the owner.
Do not follow obsolete examples to reintroduce fixed work stages, required fake
Item/Run objects, whole-catalogue startup, automatic meeting-to-Wiki moves, or assumed
cross-runtime plugin APIs. Equally, do not bypass existing checks and call the new
architecture implemented.

This map intentionally preserves technical history without copying or deleting it.
Relevant owning specifications are reconciled with code when that subsystem is
implemented; unrelated old roadmaps do not become new tasks automatically.

## Historical design inputs

[Product discovery decisions](architecture/PRODUCT-DISCOVERY-DECISIONS-20260921.md)
and [the earlier fabric roadmap](fabric-v1-roadmap.md) are historical inputs once
absorbed into ARCHITECTURE.md. They are not instructions to replay old migrations,
install all proposed packages or recreate retired topology.

## Owner routing

Product choices and cross-fabric semantics live in serveros. Registry identity and
canonical reusable payloads live in ability-management. Work/control transitions
live in work-engine. Concrete roots, targets and instance maps live in hwz-ops.
Human semantic documents live in hwz-records or their declared cloud authority.

Do not regenerate machine declarations, refresh live observations or mutate runtime
state merely to make this documentation look synchronized. Claims about DESIGN,
CODE, INTEGRATION and LIVE require their own evidence.
