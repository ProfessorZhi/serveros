# ServerOS architecture consolidation review — 2026-09-21

**Review record, not a second architecture.** The resulting product contract is
[ARCHITECTURE.md](../../ARCHITECTURE.md). This document explains the inspected defects,
chosen boundaries and what this documentation change does not establish.

## Scope and inspected baseline

The starting ServerOS main was
[db11aa6d481e995de6a6c7787cd891433fc5e6b2](https://github.com/ProfessorZhi/serveros/tree/db11aa6d481e995de6a6c7787cd891433fc5e6b2).
The starting ARCHITECTURE.md blob was
5ddd778a3e9f99dfe3ab48b55ad458bc89c26597 (116,605 bytes).

The review read the canonical architecture, both product entries, Context Capsule,
Capability Discovery, Claude/Codex parity, Safety and Concurrency, the docs tree,
ability-management README and management Skill, and work-engine README. The latest
user-confirmed product decisions in this design conversation supplied the product
requirements. Older repository/readme contracts were not assumed to describe the
new design or current runtime accurately.

The upstream Quay dist-plugin README was inspected through GitHub: it describes
0.11.0, a separately published distribution branch and selective adoption tiers.
This establishes the documented packaging direction, not the version/readiness of
any user's running provider process.

This review operated through GitHub documentation writes. It did not use remote
terminal control to repair runtime, alter networking, migrate Feishu, or touch
MarketOps/Support Bot working trees.

## Corrected defects and missing boundaries

| ID | Defect or ambiguity in prior material | Consolidated resolution |
|---|---|---|
| A01 | Large incremental edits left repeated section headings and a stray Memory heading fragment | Rebuilt one coherent architecture with one numbered section per concern; old version remains in Git history |
| A02 | Chinese entry still led with abstractions and retired Beijing-era paths | Both entries now lead with daily work and point to one canonical target |
| A03 | Startup disclosure required Item/Run identities before intent existed | L0 stays unbound; current prompt resolves the work |
| A04 | Capsule example treated development/test/delivery fields as universal | Sparse contexts, multi-repo references and non-Item work are valid |
| A05 | Every disclosure level appeared to require explicit requesting, contrary to automatic relevant context | Bounded readonly expansion after intent is automatic; no human wizard |
| A06 | No-allowlist language conflated native discovery with security authorization | Ranking never hijacks discovery; actual effects still obey policy |
| A07 | Capability WHAT shorthand contradicted typed registry identities | Capability/Operation is the need; typed Ability artifact identity is preserved |
| A08 | Global catalogue updates could invalidate unrelated selected packs | Target is exact selected dependency closure; legacy whole-manifest rule needs explicit implementation migration |
| A09 | Cross-device parity assumed equivalent plugin files and native features | Version-tested adapter capability matrix; legitimate differences remain visible |
| A10 | Memory/context diagrams risked full bidirectional replication | Federated retrieval with explicit authority and source-linked invalidation |
| A11 | Native auto-memory startup and shutdown hooks were treated as fully controlled by ServerOS | ServerOS-added budget only; checkpoints best-effort at graceful boundaries plus durable recovery |
| A12 | Immutable Run wording conflicted with progressing execution state | Historical events immutable; live projection progresses and corrections supersede |
| A13 | Evidence publication could be blocked by missing Item-bound Run | Legitimate routine/observation parent contract, never fabricated business work |
| A14 | Activity remained visually mandatory despite flexible work | Optional mutable planning; simple Item-to-Run is allowed |
| A15 | Meetings still automatically promoted to a Project Wiki area | MyDocs/Understanding interpretation, Drive assets, selective knowledge promotion |
| A16 | Garbage/nearby speech could be mistaken for work | Source/context relevance filter; ambiguous speech is review, not a new demand or deletion grant |
| A17 | Final report could appear to prove successful completion/adoption | Human summary, acceptance verdict and business outcome remain separate |
| A18 | Formal report generation, publication and sending were conflated | Independent effects with source revisions, audience and recipient policy |
| A19 | Copies or relocation could silently choose canonical documentation | One declared authoring authority; identity/equivalence/derivation relations |
| A20 | Explicit collaborators were sufficient proof of preserved sharing | Check inherited and explicit access plus audience and old navigation links |
| A21 | Empty child lists were treated as empty documents | Inspect own body, attachments, descendants and references before removal |
| A22 | Preserved provenance polluted human pages with migration scratch | Ops evidence retains journals; human views retain useful content |
| A23 | Every Quay use required another user approval | Autonomous bounded delegation within the accepted work envelope |
| A24 | Quay and ServerOS could both own dispatch/worktrees/status | One execution owner per slice; mapping/result boundary, no lifecycle mirror |
| A25 | Plugin-cache latest and task-list equality were used as deep compatibility proxies | Pin tested CLI/provider/driver composition and reconcile in-flight upgrades |
| A26 | Node online or HTTP 200 was equated to verified private service access | Endpoint/application/auth/stream path verification from the intended client |
| A27 | No TUN was treated as a blanket ingress verdict; 0.0.0.0 as proof of Internet exposure | Inspect actual publication, routing and security path; avoid either inference |
| A28 | A named netshadow-style tool became a hard dependency without an identified implementation | Provider-neutral route/network conflict-check contract |
| A29 | An approved button or USER_EXECUTED label could become Agent production permission | Execution actor is distinct from authorization; production stays user-operated |
| A30 | A method named prepare/read was treated as low-risk by name alone | Evaluate actual side effects, data exposure and load |
| A31 | Prompt-only rules were described too much like hard enforcement | Declare real enforcement points and uncovered cooperative paths |
| A32 | Timeouts/retries/cancellation lacked complete effect semantics | Stable action IDs, intent/receipt, unknown-effect reconciliation and observed stop |
| A33 | Long-lived work depended on immortal Sessions and duplicated schedules | Durable checkpoints, one trigger owner, finite workers and coalesced missed runs |
| A34 | Git worktrees were treated as all required concurrency isolation | Account for accounts, browser profiles, ports, data and integration writers |
| A35 | Source-empty/partial reads could masquerade as completed fresh inventory | Honest coverage and cursors, last-good projection, error-envelope checks |
| A36 | Release rollback could be confused with pointer change or rewind of work state | Compatible artifact activation separate from persistent-state recovery |

These decisions are contract corrections, not 36 new components. The resulting
architecture uses the existing owners and intentionally avoids a workflow engine,
new message broker, separate registry daemon, extra database authority or compulsory
review ceremony.

## Where the previous 34 detail topics now live

| Topic group | Canonical sections |
|---|---|
| Session identity/control/handoff, native history | 6, 19, 29 |
| OpenViking, native memory, bounded assembly and invalidation | 1, 7 |
| Claims, conflict, authority and reconciliation | 3, 4 |
| Feishu capture/Follow/Minutes and information flow | 2, 5 |
| Document stewardship, human views, reports and directory responsibilities | 5, 13, 14, 24 |
| Project/person/service/environment and Bridge relationships | 15, 25 |
| GitHub CC/GPT work packets and accepted revisions | 9, 10 |
| Global registry, typed identity, device projections, acquisition and experience | 16 |
| Dynamic environment, dependencies and resource retirement | 17, 21 |
| Tailscale, Windows placement and future A2A adapter | 6, 18, 29 |
| Quay delegation and native execution boundaries | 11 |
| E2E scenarios, identity, environment discovery and evidence | 12 |
| Shared/production authority and enforcement | 2, 20, 25 |
| Async effects, concurrent writes, background jobs and routines | 23, 26, 27 |
| Small-core composition and both ability families | 0, 22, 28 |
| Anti-rigidity, validation economy and product conformance | 1, 8, 30, 31 |

The former open-ended list is no longer an invitation to keep widening the product.
Installation details and discovered provider limitations are concrete implementation
facts to verify against these chosen contracts.

## External primary-source checks

Provider documentation was checked on 2026-09-21. These sources support adapter
choices; they do not prove capabilities of an installed version.

- [Claude Code memory](https://code.claude.com/docs/en/memory): native instructions
  and auto memory are complementary; contextual instructions are not enforced
  configuration. This supports federation and truthful enforcement boundaries.
- [Claude Code Skills](https://code.claude.com/docs/en/skills) and
  [MCP integration](https://code.claude.com/docs/en/mcp): use native discovery and
  supported extension/loading behavior rather than an independent universal router.
- [Claude Code hooks](https://code.claude.com/docs/en/hooks): lifecycle integration
  is adapter/version-specific; a hook is not an infrastructure monitoring bus or a
  guarantee of a final checkpoint after abrupt failure.
- [Codex App Server](https://developers.openai.com/codex/app-server) and
  [Skills](https://developers.openai.com/codex/skills): bind to supported thread and
  extension interfaces rather than infer parity from Claude packaging filenames.
- [Tailscale userspace networking](https://tailscale.com/docs/concepts/userspace-networking)
  and [Serve](https://tailscale.com/docs/reference/tailscale-cli/serve): distinguish
  network implementation, explicit service publication and persistent Serve settings
  from daemon process supervision. Verify the actual client-to-service path.
- [OpenViking](https://github.com/volcengine/OpenViking): hierarchical context and
  scoped retrieval support summary-first expansion. Its broader storage features do
  not require ServerOS to surrender work or registry authority to it.
- [MCP tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  tool annotations from untrusted servers are not security authority.
- [Quay distribution documentation](https://github.com/yaleh/quay/blob/dist-plugin/README.md):
  selective adoption and a distinct mutable distribution branch justify explicit
  revision capture and avoiding blanket full-methodology installation.

No benchmark improvement percentage or universal provider speed ranking is claimed.
The memory/context budgets in the architecture are design targets pending local
measurement, not product-performance measurements.

## Implementation boundaries deliberately not falsified

This change does not install or upgrade Tailscale, Quay, OpenViking, native runtimes
or CloudCLI; it does not change a source archive, live Feishu page, credential,
production gateway, SQLite schema or selected provider.

Known compatibility work must remain explicit rather than greenwashed:

- Older work-engine entrypoints may still require Activity/Item-bound Run evidence.
- Existing catalogue-wide pack pins may still reject unrelated manifest changes.
- Native adapter hot-load/control/checkpoint behavior must be tested per installed version.
- Concrete enforcement coverage, shared-gateway publication and endpoint reachability
  require runtime/policy evidence, not this document.
- Outstanding migration data-loss or uncertain business-result findings are not
  resolved merely by improving architecture wording.
- Local unpushed Space commits and concurrent working trees are preserved; GitHub
  documentation writes are not a local merge/rebase or production promotion.

Implementation tests and LIVE verification were **not run by this documentation
review**. Documentation readback and changed-path inspection establish only the scope
and durability of these GitHub edits. Release acceptance must use the product
scenarios in section 31 with actual code/environment evidence.

## Reading contract

Use [docs/README.md](../README.md) to route older detailed specifications. Existing
versioned interface details remain useful at their cited revision, but may not
silently override the consolidated product. Conversely, architecture changes do not
permit agents to bypass current executable guards and claim future behavior exists.

The review closes the inspected design contradictions for the stated product scope.
It is not a claim that an evolving system has no undiscovered architectural risk.
Further design changes should be driven by concrete work evidence, not another
unbounded list of speculative subsystems.
