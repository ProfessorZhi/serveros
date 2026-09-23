# ServerOS documentation map

Start with [the product entry](../README.zh.md) and [ARCHITECTURE.md](../ARCHITECTURE.md). ARCHITECTURE remains the single normative product boundary; focused contracts specify named responsibilities without creating another state authority or mandatory workflow.

## Current product design and implementation entry

| Document | Purpose |
|---|---|
| [Node observability and serverosd health](NODE-OBSERVABILITY.md) | hardware/resource semantics, dynamic essential services, Tailscale/tunnel reachability, independent Core monitoring, daemon-failure delivery and fault-domain limits; expands architecture §17/20/21/26 |
| [Readable broadcast and alert cards](BROADCAST-ALERT-DESIGN.md) | 2026-09-23 screenshot-driven default-visible metrics, essential-service strip, accurate health/coverage, readable actionable alerts |
| [Card copy rules](../CARDS-STYLE.md) | concise human presentation, meaningful summaries rather than lines[0], impact uncertainty, no status-card alert disclaimer |
| [Monitoring implementation and acceptance](MONITORING-ACCEPTANCE.md) | targeted M1–M6 detail for existing P8/P9/P11; actual source/model/render/consumer and isolated failure/visual acceptance |
| [Information and document lifecycle](INFORMATION-DOCUMENT-LIFECYCLE.md) | shallow IM default, exclusion/deep materialization, meeting notes, dual-role maintained inputs, cloud placement and feedback |
| [CC final execution plan](CC-EXECUTION-PLAN.md) | P0–P12 cross-repository work packages, migration, real acceptance, rollback and activation; read the focused monitoring acceptance with P8/P9/P11 |
| [Ability canonical blueprint](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md) | 21 semantic responsibilities and chosen artifact shapes, not 21 plugins |
| [Ability product-experience contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-EXPERIENCE-CONTRACT.md) | each responsibility's user-visible outcome, normal/empty/error/partial/deferred behavior and concrete acceptance |
| [Source/Document contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/SOURCE-DOCUMENT-CONTRACT.md) | shared roles, authority, versions, archive depth and actual material coverage |
| [Earlier read-only evidence review](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-DESIGN-EVIDENCE-20260922.md) | historical code/records/chat inspection and unverified live permissions |

The latest monitoring feedback changes the product expectation, not the running system automatically. Do not keep UID/CPU model as the only visible resource summary to satisfy an older test. Likewise do not convert optional/stopped services or all stale historical records into critical alarms. Correct code, readable UI, actual delivery and live failure detection are separate proofs.

## Existing focused contracts

| Document | Purpose |
|---|---|
| [Shared decision/search](shared-decision-search.md) | one CPU Laya service for ServerOS/MarketOps development, Serper/Jev reuse, resource/data boundaries |
| [Daily Work Cycle](daily-work-cycle.md) | low-intervention daily understanding, two-objective focus, native GPT review and bounded execution |
| [Integration readiness](integration-readiness-20260922.md) | dated measured GPT/Quay/browser/Windows/cloud/knowledge results, not current health |
| [Context Capsule](context-capsule.md) | intent-first sparse context, provenance, budget and recovery |
| [Capability Discovery](capability-discovery.md) | native-first discovery, typed identity, scoped loading and acquisition |
| [Claude/Codex Parity](claude-codex-parity.md) | shared semantics without invented feature parity |
| [Safety and Concurrency](safety-and-concurrency.md) | ownership, actual effects, concurrent writers and destructive safety |
| [Consolidation review](architecture/CONSOLIDATION-REVIEW-20260921.md) | earlier inspected evidence, contradictions and implementation limits |

## Technical specifications

- [Ability Fabric](ability-fabric.md)
- [Agent Runtime Model](agent-runtime-model.md)
- [Memory Fabric](memory-fabric.md)
- [Human Control](human-control.md)
- [Runtime Resources](runtime-resources.md)
- [Fleet Controller Adapter](fleet-controller-adapter.md)
- [Space Resolver](space-resolver.md)
- [Repository Roles](repository-roles.md)

Concrete implementation claims apply to the revision/time they describe. Read the actual installed CLI/MCP and provider schema. A new design field or named operation needs a real producer and consumer; no documentation edit turns it into a working interface. Historical freeze/load boundaries still apply: Core monitoring does not add another resident Core process or model/network work to serverosd startup.

## Ownership and history

[Product discovery decisions](architecture/PRODUCT-DISCOVERY-DECISIONS-20260921.md) and [the earlier roadmap](fabric-v1-roadmap.md) remain historical inputs. Do not rewrite their observations into current successful deployment.

serveros owns product composition, Core and human presentation semantics. ability-management owns reusable source/distribution. work-engine owns work/control state. hwz-ops binds concrete devices/targets/limits/recipients/schedules and timed maps. Native providers own their actual states; records/cloud documents retain their declared authoring authority. Independent health checks may observe a failed Core but do not seize its store or imply production control permission.

The 2026-09-23 monitoring revision changes documentation only: no runtime code, policy threshold, registry, installation, cloud card, Tailscale/network or service state is changed. CODE/INTEGRATION/LIVE each require evidence. Implementation follows the existing execution plan plus focused acceptance, not another round of product taxonomy debate.
