# ServerOS documentation map

Start with [the product entry](../README.zh.md) and [ARCHITECTURE.md](../ARCHITECTURE.md). ARCHITECTURE remains the single normative product boundary; focused contracts specify existing responsibilities without adding another authority, resident Core or compulsory workflow.

## Real-work collaboration and execution

| Document | Purpose |
|---|---|
| [Collaboration contract](COLLABORATION-CONTRACT.md) | native Parent continuation, accepted user deliverables, minimal producer/receiver handoffs, multi-authority admission, budgets and recovery; details architecture §1/3/4/8/10/11/27 |
| [Work scenario review](WORK-SCENARIO-REVIEW-20260923.md) | actual pinned records, archived Feishu messages, SOP, code and primary research; separates static risks, historical evidence and unverified runtime |
| [Daily Work Cycle](daily-work-cycle.md) | corrected capture/understanding watermarks, bounded daily focus, real GPT/Quay work, parent acceptance and delivery continuation |
| [Scenario acceptance](SCENARIO-ACCEPTANCE.md) | J01–J10 real user journeys, expected outcomes, negative and recovery checks; not proof the tests were executed |
| [CC final execution plan](CC-EXECUTION-PLAN.md) | existing P0–P12 implementation order and safety/activation boundaries |
| [Collaboration implementation detail](COLLABORATION-IMPLEMENTATION.md) | current producer/consumer gaps mapped onto P0–P12; no second task plan or completion database |
| [Ability canonical blueprint](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-CANONICAL-BLUEPRINT.md) | fixed 21 semantic responsibilities, smallest useful artifacts and exact scenario links |
| [Per-ability scenario contracts](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-SCENARIO-CONTRACTS.md) | all 21 responsibilities reverse-checked against actual work: inputs, decisions, receiver, scope, recovery and acceptance |
| [Product coverage versus sufficiency](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-PRODUCT-COMPLETENESS-20260923.md) | corrects the prior all-checkmarks interpretation; topic coverage is not proof that collaboration or LIVE behavior works |

A child task finishing, a report being generated, a JSON schema passing or an installed Skill loading does not by itself complete the user's accepted goal. The current native Parent must check its required outcomes and hand unfinished work to an actual receiver/continuation path. These obligations use existing Work Engine/Agent Control/provider receipts, not another orchestration platform.

## Product surfaces and source lifecycle

| Document | Purpose |
|---|---|
| [Node observability and serverosd health](NODE-OBSERVABILITY.md) | resource semantics, dynamic necessary services, Tailscale/tunnel reachability, independent Core checks and realistic fault domains |
| [Readable broadcast and alert cards](BROADCAST-ALERT-DESIGN.md) | screenshot-driven visible resource metrics and service health, truthful coverage, actionable low-noise alerts |
| [Card copy rules](../CARDS-STYLE.md) | actual summaries rather than UID/model lines, precise uncertainty and normal-versus-alert presentation |
| [Monitoring acceptance](MONITORING-ACCEPTANCE.md) | M1–M6/V01–V18 for P8/P9/P11, producer/model/native visual tests and isolated failure recovery; reused by J08 |
| [Priority-contact deep archive](DEEP-ARCHIVE-POLICY.md) | project counterpart p2p/project-group DEEP_REQUIRED policy, forwarded-file material closure, completeness and migration |\n| [Information and document lifecycle](INFORMATION-DOCUMENT-LIFECYCLE.md) | default shallow IM, selected materialization, meetings, documents that are both followed and maintained, placement and feedback |
| [Source/Document contract](https://github.com/ProfessorZhi/ability-management/blob/main/design/SOURCE-DOCUMENT-CONTRACT.md) | stable identities, roles, authority, versions, material coverage and two watermarks |
| [Ability product experience](https://github.com/ProfessorZhi/ability-management/blob/main/design/PRODUCT-EXPERIENCE-CONTRACT.md) | user-visible outcomes, empty/unknown/partial/failure/waiting behavior and acceptance for every responsibility |

Do not treat every state label in product examples as a new runtime enum or compulsory sequence. Availability, health, authorization, execution, business outcome and delivery are different dimensions. Keep existing supported schemas until deliberately migrated with real producers and consumers.

## Existing focused contracts

| Document | Purpose |
|---|---|
| [Shared decision/search](shared-decision-search.md) | one Laya host service for ServerOS/MarketOps development, direct Serper/Jev adapters and resource/data boundaries |
| [Historical integration readiness](integration-readiness-20260922.md) | dated component measurements, not current end-to-end readiness |
| [Context Capsule](context-capsule.md) | sparse intent-first context, provenance and recovery |
| [Capability Discovery](capability-discovery.md) | native-first discovery, identity, scoped loading and adoption |
| [Claude/Codex Parity](claude-codex-parity.md) | shared contracts without invented runtime equivalence |
| [Safety and Concurrency](safety-and-concurrency.md) | ownership, actual effect boundaries and concurrent writers |
| [Earlier consolidation review](architecture/CONSOLIDATION-REVIEW-20260921.md) | prior evidence, contradictions and implementation gaps |

## Technical specifications and owners

- [Ability Fabric](ability-fabric.md)
- [Agent Runtime Model](agent-runtime-model.md)
- [Memory Fabric](memory-fabric.md)
- [Human Control](human-control.md)
- [Runtime Resources](runtime-resources.md)
- [Fleet Controller Adapter](fleet-controller-adapter.md)
- [Space Resolver](space-resolver.md)
- [Repository Roles](repository-roles.md)

ServerOS owns product composition, Core and human presentation semantics. ability-management owns reusable source/distribution. Work Engine owns current work/control state; Agent Control owns trusted grants. hwz-ops binds concrete targets, limits, recipients and schedules. records and native documents keep their authoring authority. A readable Card is not a second grant/worker database, and an independent observer cannot seize a failed daemon's store.

## History and scope of this design update

[Discovery decisions](architecture/PRODUCT-DISCOVERY-DECISIONS-20260921.md), [earlier roadmap](fabric-v1-roadmap.md) and [prior Ability evidence review](https://github.com/ProfessorZhi/ability-management/blob/main/design/ABILITY-DESIGN-EVIDENCE-20260922.md) remain historical evidence. New source/versions must be inspected rather than backfilled as if older runs had observed them.

The collaboration review changes documentation only. It does not alter ARCHITECTURE's frozen resident surface, runtime code, permissions, enum definitions, credential bindings, installations, actual source cursors, Feishu documents, cards or active services. CODE/INTEGRATION/LIVE require their own evidence. Read only the sections relevant to the current native Agent task; do not load the full design corpus into every Session.
