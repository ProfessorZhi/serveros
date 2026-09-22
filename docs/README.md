# ServerOS documentation map

Start with [the product entry](../README.zh.md) and [ARCHITECTURE.md](../ARCHITECTURE.md). The latter remains the single normative product architecture. Focused contracts below explain its named responsibilities; they do not create a second work/capability authority or a mandatory workflow for every task.

## Current detailed design and execution entry

| Document | Purpose |
|---|---|
| [Information and document lifecycle](INFORMATION-DOCUMENT-LIFECYCLE.md) | user-confirmed shallow IM default, exclusion/deep materialization, meeting notes, documents that are both inputs and maintained outputs, cloud placement and change feedback |
| [Broadcast and alert design](BROADCAST-ALERT-DESIGN.md) | actual card implementation, one presentation owner, scheduled status versus actionable alerts, frozen payload receipts, in-flight changes and transport acceptance |
| [CC final execution plan](CC-EXECUTION-PLAN.md) | P0–P12 work packages tied to current code, producer/consumer migration, acceptance, rollback and precise deployment boundaries |
| [Ability canonical blueprint](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md) | 21 semantic responsibilities, chosen minimal artifact shapes and the detailed per-ability contracts; not 21 plugins |
| [Source/Document contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/SOURCE-DOCUMENT-CONTRACT.md) | shared identity, roles, versions, archive depth versus actual coverage, ownership and input/output coexistence |
| [Read-only evidence review](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-DESIGN-EVIDENCE-20260922.md) | actual source ranges/code/archived chats and which live permissions or behavior were not verified |

The newest archive/maintenance choices come from the user's current request. Historical instructions that default to no archive, treat every shared space as globally unmaintainable, or equate text sanitization with image safety are migration gaps, not instructions to repeat those behaviors. Runtime policy and cloud ACL changes still require their proper authority.

## Existing focused contracts

| Document | Purpose |
|---|---|
| [Shared decision/search](shared-decision-search.md) | one CPU Laya service for ServerOS/MarketOps development, Serper/Jev reuse, resource/data boundaries |
| [Daily Work Cycle](daily-work-cycle.md) | low-intervention daily understanding, two-objective focus, native GPT review and bounded execution |
| [Integration readiness](integration-readiness-20260922.md) | historical measured GPT/Quay/browser/Windows/cloud/knowledge results; not an automatically current health snapshot |
| [Context Capsule](context-capsule.md) | intent-first sparse context, provenance, budget and recovery |
| [Capability Discovery](capability-discovery.md) | native-first discovery, typed identity, scoped loading and acquisition |
| [Claude/Codex Parity](claude-codex-parity.md) | shared semantics without invented native feature parity |
| [Safety and Concurrency](safety-and-concurrency.md) | ownership, real effect boundaries, concurrent writers and destructive safety |
| [Consolidation review](architecture/CONSOLIDATION-REVIEW-20260921.md) | inspected evidence, corrected contradictions and implementation limits |

## Technical specifications and existing implementation notes

- [Ability Fabric](ability-fabric.md)
- [Agent Runtime Model](agent-runtime-model.md)
- [Memory Fabric](memory-fabric.md)
- [Human Control](human-control.md)
- [Runtime Resources](runtime-resources.md)
- [Fleet Controller Adapter](fleet-controller-adapter.md)
- [Space Resolver](space-resolver.md)
- [Repository Roles](repository-roles.md)

Read concrete claims at the revision they describe. Existing executable/schema behavior remains the implementation contract until deliberately migrated; a new architecture sentence cannot make an absent CLI/MCP operation real. New fields require genuine producers and consumers.

Where a technical note conflicts with the consolidated product architecture, record the exact compatibility gap and migrate the owning implementation. Do not resurrect fixed business stages, fake Item/Run objects, whole-catalogue startup, automatic meeting-to-Wiki moves or cross-runtime plugin assumptions. Do not bypass correct checks to announce success.

## History and ownership

[Product discovery decisions](architecture/PRODUCT-DISCOVERY-DECISIONS-20260921.md) and [the earlier fabric roadmap](fabric-v1-roadmap.md) remain historical inputs after absorption into ARCHITECTURE.md. Their migrations and unverified proposals are not automatically new work.

Product/cross-fabric semantics live in serveros. Canonical reusable abilities live in ability-management. Work/control state lives in work-engine. Concrete roots/targets and timed instance maps live in hwz-ops. Human semantic records live in hwz-records or their declared cloud authoring authority. A followed source may also be maintained, but only inside the actual object-scoped grant.

This detailed-design change does not regenerate live declarations, install skills, refresh observations, edit Feishu documents or activate schedulers. CODE, INTEGRATION and LIVE require their own evidence. The final execution plan gives CC the next bounded implementation path rather than another request to redesign the product.
