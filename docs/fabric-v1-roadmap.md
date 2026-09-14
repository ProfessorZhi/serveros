# Fabric V1 Roadmap

[`ARCHITECTURE.md`](../ARCHITECTURE.md) freezes the principles. This page is what
it points at: **the ledger of every place the architecture is declared and not
yet built**, with the real failure each gap answers and the order they have to
be closed in.

The rule this page is written under is the same one the architecture's own
freeze clause uses:

> **A gap written down is a question. The same gap written as architecture is a
> lie with a maintainer.**

So nothing below is a plan, a promise or a schedule. Each row is a statement
about the code as it stands, and the only claim made about anything not yet
built is *what it would have to do and why anyone would care*.

## Status vocabulary

```text
EXISTS            built, wired, reachable, tested
PARTIAL           built for one case and not the general one
COMPUTED_UNREAD   computed correctly, then discarded — no reader anywhere
DECLARED_ABSENT   written in a contract, implemented nowhere
ABSENT            no vocabulary, no code. Not deferred — never started.
```

`DECLARED_ABSENT` is the one that costs the most, because a reader of a
contract is *supposed* to trust it. `COMPUTED_UNREAD` is the cheapest to fix
and the most embarrassing, because the work is already done and being thrown
away.

The **failure** column names the observed failure the gap answers. Where the
entry comes from a specification rather than from something that went wrong on
this machine, it says so — *"no observed failure"* is a legitimate answer and
an important one, because a mechanism that cannot name its failure is exactly
what this product keeps building by accident.

---

## General

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| GEN-01 | **EXISTS — the one gate is now four** | **The gate, for all four classes.** `tests/test_declared_commands.py` walks the real argparse parser and fails when any scanned document tells a reader to run a verb that does not exist. Joining it, `tests/test_declared_implementations.py` holds a contract to its own declarations in three classes: `DeclaredEventsHaveProducers` (a `producers:` entry must appear as a literal in code that is not a contract and not a test), `DeclaredFieldsHaveReaders` (a declared field must be read by some non-test module — **read by AST, not by text**, so a mention in a docstring is not a reader) and `EnforcerClaimsResolve` (an `enforcement:` / `enforced_by:` / `decided_by:` naming a file or symbol that does not exist). Each class carries an exemption table shaped like `test_declared_commands.py#KNOWN_ABSENT`, and every table only empties: **leaving is the only exit an exemption has**, so a field that gains a reader must lose its row or the gate goes red. What it found the day it opened: **six events with no producer**, **one declared field with no reader**, and **no unresolved enforcer claims** — the last held to a positive control fed a synthetic contract naming a file and a symbol that do not exist, because a gate whose findings list is empty on the day it opens is indistinguishable from a scanner that stopped matching. The fourth is `tests/test_core_ability_boundary.py`: the import direction, and whether the Core starts with no provider at all. | The systemic one, observed in **seven instances inside a single module** during the resource-observation round with the test suite green through all seven — and in an **eighth**, when that one gate caught this page's own author citing `serverosctl anomaly explain` in a form the parser does not accept. |

GEN-01 is the reason every entry below exists, and the one gate that is already
built is the model to generalize rather than a thing to admire. It is worth
reading in full (`tests/test_declared_commands.py`) because two of its
properties are the ones a second gate would have to reproduce:

```text
it walks the PARSER, not a hand-kept list   so the real answer cannot drift
                                           from the thing it describes
its KNOWN_ABSENT entries cannot outlive     every exemption must carry a
their declarations                          `because` and a `disposition`,
                                           and must still be declared
                                           somewhere in the repo
```

That second property is the whole design. The three findings the gate opened
with are not failures — they are **recorded, reasoned, and self-retiring**,
which is why the gate can be green on the day it ships and still catch the
next one. A gate that is red when written is a gate that gets turned off.

The cheap version of the remaining three classes is available immediately:
**fail on names that appear in a contract and nowhere else, when the
contract's own text claims the name is implemented.** The expensive version —
proving a declared behaviour is reachable — is what `runtime/conformance.py`
already attempts for one contract.

---

## Memory-Context Fabric

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| MEM-01 | **EXISTS** (§18) | **The recall firewall** — built as `memory/scope.py`, closed in v1.2. What it was: `firewall` and `bound_item` had zero hits in the tree, `current_value()` and `heads()` took a free-form subject and would answer a question about **any** Item, and the only statement of the rule was prose. What it is now: a `scope` field on the record (`item` · `project` · `user`), a required keyword-only `reader` on both read entry points, and three refusals that are raised rather than returned as an empty answer. | **The strongest entry in this ledger.** It is the Memory-layer form of the exact defect a live incident already produced one layer up: work from one Item surfacing inside another. And it is invisible by construction — reads are never logged, so `audit()` cannot see a cross-boundary read even after the fact. That limit is unchanged and now stated: a read RETURNS a reading, which reaches the reader and not an auditor. |
| MEM-02 | ABSENT | The write pipeline — candidate → scope → sensitivity → dedup → quality → policy → commit. Today there are two gates and no pipeline: `record.validate()` (one record) and `record.audit()` (whole-ledger duplicate and cycle detection). `record.py:383-398` states outright that the ledger *cannot* detect a conflict. | Nothing has failed yet. The missing rung is the one between *"an Agent inferred this"* and *"this is durable truth"*; without it every write is trusted equally, which is a decision made by omission. |
| MEM-03 | **PARTIAL** | The `scope: user · project · item` half is built (§18, `record-contract.yaml#scope`) and is what MEM-01 needed. The `subject/predicate/value/valid_from` half is still absent: `subject` is a **location**, not a subject — `record.py` permits `device:<id>` or exactly three slash-separated parts, and there is no `user:`. | The promotion ladder from MEM-01: half of it was the missing rung, and the other half is a different claim about what a record IS. Recorded as one PARTIAL row rather than split in two, because a reader who finds one half built should not have to discover that by reading the other half's status. |
| MEM-04 | ABSENT | The durable `claim` kind. Kinds are **closed at eight** (`decision · observation · revision · handoff · finding · question · checkpoint · incident`), enforced at `record.py:209` and asserted against the contract at `tests/test_memory_ledger.py:100`. | No observed failure. This is a specification item, and it is listed separately from MEM-03 because the *cost of adding a kind* is a different question from the cost of adding a field — the enforcement above means every new kind is a deliberate, tested act, which is the property worth keeping. |
| MEM-05 | ABSENT | `ContextBundle`. Zero hits. What exists is `current_value()` (one record or `None`), `heads()`, `pending()` and `usable_hits()` — no purpose, no per-record provenance envelope, no token budget. | No observed failure. Without it, retrieval quality can only be argued about, never measured. |
| MEM-06 | **COMPUTED_UNREAD** | Three values computed and then dropped: `projection.build()` writes `heads` and `open_questions` into `memory.json` and **nothing reads either**; `confidence` is validated and never weights anything (`heads()`/`current_value()` sort by `at` alone); `by` is documented as affecting weight (*"a fact recorded by a person and one recorded by an Agent do not carry the same weight"*) and is never read. | Silent, and this is the sharpest form of it: the module was written as if these mattered. A reader who trusts `confidence` will believe a low-confidence record was discounted. |
| MEM-07 | PARTIAL | OpenViking as the Layer C provider. Contract, fixture and status evaluator all exist — and **three modules have zero production callers**: `provider_status.evaluate()`, `layering.usable_hits()`, `handoff.pending()` are reached only from tests. | No observed failure. Recorded as PARTIAL rather than ABSENT because the work is real, tested and correct; it simply has no production entry point and no deployment. |

MEM-01's fix was smaller than its severity suggested, and the precedent was
already in the tree: `memory/handoff.py:pending(records, device_ref)` takes the
caller's scope **as an argument**, and refuses to resolve it itself. The read
path now takes `reader` the same way and raises rather than answering on
mismatch.

Two things about it are worth carrying forward, because they were the whole of
the work and neither was the code:

* **The policy is refuse-by-default**, decided by the user rather than chosen by
  the kernel. It is not a safe default discovered by construction; it is a
  ruling, and `record-contract.yaml#scope.reader_rule` records it as one.

* **The widening is a field, not a verb.** The rule said the promotion "is
  recorded", and the smallest thing that satisfies that sentence is a value
  beside `by` and `at` and `revision` — not a promotion object, not a
  permission store. Both alternatives invent a second place the answer lives.

---

## Agent Runtime, and the Ability Fabric

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| RUN-01 | **EXISTS** | **A per-device runtime binding.** What it was: `fleet/registry.yaml` gave each device a bare `agents: [claude-code, codex]` — **membership** — and the contract asserted installations were *"already modelled — one per device, in the registry, with an owner"* while no installation record existed anywhere in the tree. What it is now: `abilities/runtime_binding.py` resolves a binding from `(device_ref, runtime_ref)`, each device row carries `runtime_bindings:`, `fleet/transport.py#Device.runtime_bindings` carries it to the fleet view, and `serverosctl device agents [--probe]` is the one surface that answers it. The split that makes it a measurement rather than a restatement: **`runtime_ref` and `declared_at` are authored; surface, version and features are observed** — a surface written into the registry for a machine nobody has queried would be indistinguishable in the record from one written after checking, so the registry holds none of them. There is no store, no `bindings.json`, no writer and nothing on disk to go stale: `persistence: none`, for the reason `provider_instance` gives. | A device reported as able to do something and the command failing. Without a binding, every answer about a device's runtime is answered from the family's global record, which is the union error the eight-axis intersection exists to prevent. |
| RUN-02 | **EXISTS** | **A runtime feature vocabulary.** What it was: zero hits across all three repositories for `features:`, `mcp.client`, `skill.native`, `gui`, `app.server`. What it is now: `agent-runtime-contract.yaml#runtime_features` — seven closed names, and **four states**, `AVAILABLE · UNAVAILABLE · UNKNOWN · INCOMPATIBLE`. The fourth is what makes it a measurement: *nobody established it* calls for the opposite action to *it is absent*, and `runtime_binding.py#feature()` is the one function that answers one. The shape is `fleet/transport-contract.yaml#capability_levels` copied rather than invented — a per-entity closed vocabulary whose last value keeps "we did not ask" from reading as "no". `native.extension` was considered and is **omitted** rather than marked planned, because there is no such surface anywhere in the tree and declaring it would have produced a feature nothing could ever answer about. | The CLI/desktop difference, which is real and now has somewhere to live that is not a new runtime identity: `surface` is a field of a **binding**, so Codex on a desktop is one family with a different observation. |
| RUN-03 | **EXISTS — one implemented, one deleted** | Any reader for `version_observed_by` or `capability_probe`. Both were in `required_fields` on a record no code wrote and no code read, and the two departures are different findings rather than one tidy-up. `version_observed_by` was **implemented**, and it moved to where its subject is: how a runtime's version is established is a property of the runtime *family*, not of a record about one device, so it is now `version_observation.version_observed_by` and `abilities/runtime_binding.py#version_observation()` refuses a version whose probe is not the one declared for that family — the contract's rule *"a version with no `observed_at` is refused"* finally having an enforcer. `capability_probe` was **deleted**: it named how to probe what a runtime can do, written before there was a vocabulary of things to probe *for*, and a second mechanism describing the same probe in a different place is the second copy this ledger keeps finding. | A remembered version being read as a measured one — the exact distinction the contract reasoned about correctly and nobody executed. |
| RUN-04 | **EXISTS — moved, and the shape is enforced** | `skill_discovery` and `session_lifecycle` were global single values in `capabilities:`, compared by a checker that could only ever report agreement, because two values that are always equal cannot differ. They left that block for `product_invariants:` — statements about the *product*, not about one runtime or one device — and the question they answer moved to the feature axis (`skill.native`, `session.lifecycle`, `AVAILABLE` for every declared runtime with the declaration as its evidence). The parity block now **enforces the shape rather than trusting it**: `PARITY_COMPARES_A_SHARED_VALUE` fails any `compared_fields` entry whose value is not keyed by every declared runtime id, so restoring a global to that list is a red check instead of a quiet regression. One entry remains, `consumable_provider_kinds`, and it is now keyed per runtime. | The vacuous comparison — stated by the checker itself, and an honest description of a real thing rather than a defect. It becomes a defect the moment a reader assumes these are per-runtime, which is what the shape check now makes unrepresentable. |
| RUN-05 | **ABSENT — decided rather than deferred** | An adapter `requires: {runtime, features}`. The adapter record stays a **materialization address** — where one runtime reads a provider (`provider-contract.yaml#adapters.shape`) — and the requirement it looked like it should carry now lives on the provider (`provider-contract.yaml#required_runtime_features`), evaluated **per binding**. So the third question (*"does this installed runtime have what this surface needs"*) now has an answer, and the answer is deliberately **not** on the adapter: an adapter says where and how one runtime reads something and nothing else, and a `requires:` there would be the second copy of the provider's rules that `ARCHITECTURE.md` forbids adapters to become. | No observed failure. Requirements are checked provider-side today and that path works; what was missing was the subject split that keeps three questions from collapsing into one — *does this DEVICE have the capability* / *can this runtime FAMILY consume it* / *does this INSTALLED runtime have it* — and only the third can come back `UNKNOWN` without anything being wrong. |
| RUN-06 | **DEAD** | `fleet/agent_control_router.py#provider_rows()` reads `getattr(provider, "runtime_adapters", None)`. No provider in the tree defines that attribute; the grep hits only the `getattr` itself. A branch that can never be taken and therefore cannot fail. | Same family as `NOT_YET_WRITTEN` and `COMPUTED_ON_DEMAND` from the previous round: an output field that is always empty and never complains. |
| RUN-07 | **PARTIAL** | **The codex app-server observation is nondeterministic between two true descriptions of one event.** `mcp/codex_app_server_provider.py#observe()` races *writing its handshake* against *reading the child's exit status*. When the child dies immediately — which is exactly what a probe is for — whichever side wins decides what gets recorded: the exit code, or `BrokenPipeError`. Both are accurate observations of the same dead server, and `tests/test_codex_app_server_provider.py::test_a_dead_server_is_a_stopped_provider_and_not_a_closed_session` pins one of them, so it fails under load (observed once at load 4.7–5.4 on 8 cores, clean in isolation at 0.50s and clean on re-run). | A test that fails for a reason unrelated to the code it tests, which is how a real regression gets waved through as *"that one is flaky"*. The evidence recorded for a dead server should not depend on a race in the reporter; the fix is to prefer the exit status — which is the more specific fact — when both are available. Not fixed in W7: the file is outside that round's scope. |
| ABL-01 | **EXISTS** (§10) | **`ability#operation`** — resolution at the operation, not the ability. What it was: `resolver.py` filtered on the whole ability and `provider-contract.yaml` had no `operations:` key. What it is now: an optional `operations:` on the provider record, the reference parsed **once** at `resolver.py#parse_ability_ref`, and the three-valued asymmetry copied verbatim from `target.py#_support_state` rather than reinvented — a verb absent from a published table is `UNSUPPORTED` → `INCOMPATIBLE`/`ACTION_UNSUPPORTED`, and no table at all is `UNKNOWN`, which stays on the **candidate** and does not reach `record.reason` (`target-contract.yaml`'s ruling for the same axis, applied not re-decided). `ACTION_UNSUPPORTED` is reused from `target-contract.yaml#reasons`, not coined — and deliberately **not** added to `provider-contract.yaml#status.reasons`, whose closed list is asserted equal to `provider_instance.REASONS` and is about whether a provider is *there*, not what it does. The rule that makes this a widening rather than a reversal: **a bare `ability:x` never consults the table** — otherwise every provider declaring no `operations:` would answer `UNKNOWN` to an ordinary `#read`, withdrawing capabilities that already worked. | `ability:team-document-management#read` works everywhere and `#restore` needs a credential one device lacks. Reporting the ability `UNAVAILABLE` removes a working read; reporting it `AVAILABLE` makes a restore that will fail look available. |
| ABL-02 | PARTIAL | **Device-relative pack resolution.** `packs.py:222` takes `(contract, catalogue, catalogue_revision, catalogue_repo)` and no device at all, while `pack-contract.yaml:37` declares a member that *"cannot be resolved on the target device"*. So `PARTIAL` means *"absent from the pinned catalogue"* and is documented to mean *"this device cannot do it"* — one word apart in the contract, unrelated in the code. | A person reading `PARTIAL` as a statement about their machine. The build-time closure check is real and stays; what is missing is the device-relative half, and no code joins a pack to `resolver.py` or `target.py` at all. |
| ABL-03 | PARTIAL | The five concepts. `provider-contract.yaml:23-30` names five (Ability, Provider, **Profile**, **Binding**, Adapter); the `objects:` block in the same file declares three. Profile and Binding live in a comment, pointing at `fleet/profiles.yaml` and `fleet/registry.yaml` — neither of which uses either word. | A reader who follows the comment to a file that does not use the vocabulary, and concludes the concept is implemented. Resolvable by documentation alone, and it is a real risk to the next author only. |
| ABL-04 | **WRONG** | `abilities/pack-contract.yaml:127` cites `ability-management@7bc7687:ability-packs.yaml`. At that revision the file does not exist; it lands at `0091d8d`. The location is right and the revision is not. | Provenance that cannot be re-checked, in the one field whose entire job is to be re-checkable. Pure documentation error, safe to correct, **and not corrected here** — this round does not touch contracts. |
| ABL-05 | PARTIAL | The eighth input, `task_context`, is inert — `resolver.py:294-300` says so. The axis exists and never narrows anything. | No observed failure. Listed because the `EffectiveAbility` formula is quoted as an eight-way intersection, and one of the eight is decorative. |
| ABL-06 | ABSENT — **decided, not overlooked** | An MCP surface for ability resolution. The CLI exposes `ability resolve`; no MCP tool does — which is why an Agent cannot ask the question natively. **Why it stays absent**, established rather than assumed: the node MCP surface is a thin adapter over `serverosd`, and §23 forbids the core from loading the plane it governs, so `ability.resolve` and `pack.inspect` would have forced a second resolver — the failure the W1 boundary gate exists to catch. `fleet/mcp-contract.yaml`'s `does_not_own` now names both, and `ability.inspect` answers `resolved_by: "serverosctl ability resolve"` instead of a verdict. §10 adds a third name to that list: `operations_declared` is `null` on this surface, never `false`, because operation declarations live on provider records the daemon holds none of. | An Agent that cannot ask, and therefore guesses. `capability-discovery.md` holds the requirement. |

RUN-01 through RUN-04 are the four entries this page listed as *"the contract says
so and nothing executes it"*, and all four are closed — **but not by one
mechanism**, and the difference is what makes the closure worth reading.

Two were **built** (RUN-01's binding, RUN-02's vocabulary), one was **implemented
on the half that had a subject and deleted on the half that did not** (RUN-03),
and one was **moved to the plane whose statement it always was** (RUN-04). None
of the four was closed by writing a name into this ledger — which is not how a
row here is allowed to change. Each is now a mechanism with a producer, a reader
and a test that fails when either stops. RUN-03 is the one to keep in mind —
`capability_probe` was as real as `version_observed_by` on the day both were
believed, and the honest disposition for a declaration with nothing behind it is
sometimes deletion rather than construction.

What is **not** closed is stated in the same breath, and per item rather than as
a mood:

```text
`agents:` is still authored on all three device rows. It is a projection of
`runtime_bindings` and the mismatch check is live, but the migration is not
finished, and finishing it means removing a key that three device rows and the
fleet view read. Until then BINDING_MEMBERSHIP_MISMATCH is doing real work
rather than gating an empty surface.

Two of the three accepted version probes — `package-manifest` and
`service-handshake` — are accepted and unimplemented, and the contract says so
per probe rather than leaving three accepted probes reading as three implemented
ones. `cli-version` is the only one with a producer.

device:work-fj and device:personal-dell have bindings and no observation. That is
not a column to fill in from the Beijing row: nobody has reached either machine
from this node, and the record is shaped so the unfillable case comes back
`observation: UNKNOWN` with a reason rather than a guess.
```

ABL-01 and ABL-02 are the same shape of error — the code answered a **coarser**
question than the contract claimed — and ABL-01 is now closed by copying rather
than inventing. The verb axis already ran for Agent Sessions with eleven closed
verbs and a three-valued support state, and that is what was copied; the
neighbouring plane's ruling on where the coarse word may appear was copied too,
which is why `ACTION_UNSUPPORTED` stays on the candidate and `record.reason`
stays null when the verdict was merely *nobody asked*.

ABL-02 remains open, and the two were never one piece of work: closing the verb
axis needed a key on a provider record, while the device-relative pack half
needs a device argument threaded into `packs.py` and a join to `resolver.py`
that does not exist. Its row still reads PARTIAL, and nothing about ABL-01
changes that.

---

## Coordination Fabric

**This section was written when the whole plane was ABSENT, and three of its
four entries have since closed.** It is rewritten rather than deleted, because
the prediction it made about *where* this work belonged was wrong in a way
worth keeping: it argued that Coordination should go **last**, and it went
third of nine.

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| COORD-01 | **EXISTS** | **`session_role: coordinator \| execution`.** What it was: the only `role` in the session contract was `serveros_role` (`agent-session-contract.yaml:274`), which is ServerOS's role relative to the *provider* — a different subject entirely, and the reason a grep for `session_role` returned zero hits without that being surprising. What it is now: declared in a `coordination:` block of `agent-session-contract.yaml` and **required at create with no default**, because a default would be a guess about somebody's intent recorded as a fact, and the record would then be evidence for the guess. It is deliberately **not** a field in `session_fields.optional`: a role is what a thread is *for*, not one more thing a session has. `fleet/agent-control-contract.yaml:127` carries the ruling to the control plane. | The failure this plane exists for is on this machine and in `runtime/coordination.py`'s own header: **LIT-0101 reached DELIVERED / SUCCESS, and the same execution conversation went on to prepare LIT-0205.** Two real Items, one finished, a thread that did not close with it, and nothing in the tree that could tell it to. |
| COORD-02 | **EXISTS** | **`bound_item_ref`.** What it was: `work_item` was an **optional, unvalidated passthrough string** carried through `agent_mcp.py:499` and `agent_control_router.py:248`, and the execution layer never received it, because `agent_session_service.create()` had no such parameter. Nothing bound, validated, froze or compared it — and `work_item` is *still* in `session_fields.optional` (`agent-session-contract.yaml:184`) **without a second meaning**: the contract now says which word is the binding, and `work_item` stays as the provider-reported field it always was. Leaving the two words side by side with no note would have been the near-collision this file's own §17 comment forbids, so the note is in the contract rather than here. What it is now: **required for `execution`, forbidden for `coordinator`, frozen at create and immutable after** — the one role that is legitimately unbound is the coordinator, which coordinates *across* Items and therefore has no single one. It is held by `runtime/agent_control_store.py#put_binding`, beside `contexts` and `approvals`, in the same atomic write and under the same key discipline (`by ref, never by actor`) rather than in a second store. **The provider service still does not take it, and that is the design rather than a remaining gap**: the binding is ServerOS-side state about a thread, not a parameter of a provider's `create`, and threading it in would give the provider a reason to hold an opinion about which Item is legitimate. | A session that believes it is working on an Item, and a system with no record that it is — the value transported and discarded one hop short of the thing that would act on it. |
| COORD-03 | **EXISTS** | **The cross-thread guard, and it was copied rather than invented** — exactly as this row predicted. `runtime/coordination.py` is a pure module: binding arrives as a dict, the Item's state arrives as a string, no clock and no store, and the reader that goes and gets an Item's state lives in `runtime/daemon.py` so the decisions can be tested without a state root. Four refusals, and the vocabulary is the part that took the care: **`INVALID_CONTEXT` reused** (no binding at all — the same shape as `_device_for`'s refusal for a request that contradicts the ref it was addressed to), and **three new words**, each because the alternative was to report it as one of the others. `THREAD_ITEM_MISMATCH` (the caller named one Item, the thread is bound to another — repair is in the caller's payload); `READ_ONLY_CLOSURE` (the bound Item is DELIVERED or CLOSED — repair is a new thread, not a new Item name, which is why calling it a mismatch would send the caller to fix the wrong field); `THREAD_CLOSED_FOR_NEW_WORK` (the thread was closed while its provider is still alive and accepting — calling it `ACTION_UNSUPPORTED` would say something false about the provider and stop the caller ever retrying). The three landed in `abilities/target-contract.yaml#reasons`, the single owner, and `tests/test_target_resolver.py:172` is the gate that makes the closed list a decision rather than a habit. | The whole point of COORD-01/02: without a guard, binding is a comment. |
| COORD-04 | ABSENT — **and still the only entry in this plane that is** | `work.item.discovered`, and any way for a discovery to reach a coordinator. work-engine's `INBOX → AVAILABLE → … → CLOSED` has no `DISCOVERED`; the emitted set is `created · transitioned · reopened · outcome_recorded · deliverable_added · run_terminated`, and `runtime/notification-contract.yaml`'s closed event set is eleven names — *discovered* is not among them, so this was **not** quietly half-done alongside the other three. **And no priority, severity or P0 field exists anywhere in the system** — not even in the item-card schema's `required`, which is why §S's board renders five empty buckets and marks the axis `UNPRODUCED` rather than filling it from a proxy. | A discovery that lands as a row and waits to be pulled. There is nothing to escalate *to* and no word for *urgent*. |

**What the ordering argument got wrong, since this ledger is supposed to be
read against itself.** The paragraph that used to close this section said
Coordination should go last — it was greenfield, it had no partial to build on,
and a guard that is wrong blocks work that used to succeed. The second half was
right and is what made the work cheap: every piece it needed (`_device_for`'s
`INVALID_CONTEXT`, `ActionNotSupported` for a closed session, the store's
atomic write) already existed, so three entries closed in one round without
inventing a mechanism. The first half was wrong, and the reason is worth
keeping: **greenfield was being read as "unbounded", when what it actually
meant was "nothing to reconcile"** — there was no legacy binding to migrate and
no existing caller to break, which is why it cost less than the entries this
page had ranked ahead of it. The plane that genuinely has to go last is
COORD-04, and for a reason the original paragraph did not name: it needs the
**priority axis**, and that axis does not exist in either repository.

---

## Requirement Intake, and the board a person reads

§S asks for three things that are easy to write as one and wrong that way:
**Requirement Source**, **Work Item**, and **Requirement Board**. Two of the
three already existed and only the third was missing, which is why this plane
is one table row and not a project.

The intake half is **not built here, on purpose**. A requirement's origin is
already held by the authority — `item_candidates.(domain, external_id,
external_revision)` on the work-engine side and `source_refs` / `aliases` on
the Item card — and a store in this repository would be a second place a
requirement's origin lives, needing reconciliation with the first. What was
missing was a projection: one view over every project, for a person.

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| REQ-01 | **EXISTS** | The three nouns, kept apart, in `intake/board-contract.yaml`: `requirement-source` (held by the authority), `work-item` (truth owner work-engine, read through one declared MCP path), `requirement-board` (a projection with **no authority and no reader**). The board reads the per-project documents the control plane already rendered and **never the store** — a second reader of the store would be a second answer to "what state is this Item in". | A board that quietly becomes the answer to a question the authority already answers, and then disagrees with it. |
| REQ-02 | **EXISTS** | The Global Board itself: `intake/global_board.py`, reached as `serverosctl requirement board` (and `--write` to persist), state at `state/requirement-board/current.json` + `current.md` — both generated together so they cannot describe different generations. Measured live on 2026-09-14: **17 Items over 3 projects, 10 active, 7 completed, 17 of 17 cards joined**. §S's default display is rendered in §S's order: the five levels, then 未分级, then 已完成. | Every project having its own board and nobody having the one a person asks for. |
| REQ-03 | **EXISTS**, by derivation | The completion semantics. `已完成` is `CLOSED` and `CANCELLED` — **taken from `state_machine.reopen()`**, which accepts exactly those two and refuses everything else, not chosen here. `DELIVERED` is deliberately **not** in the set: it sits on the mainline with something still outstanding, so it renders in the active area with its state visible. `~~title~~` is drawn from the Item's state, and reopen needs **no board write at all** because the board stores no completion flag to clear. It cannot delete an Item it does not store. | A board that keeps its own copy of "finished" and a reopen that has to be applied in two places, one of which is a projection nobody remembers to regenerate. |
| REQ-04 | **DECLARED, UNPRODUCED** | The **priority axis**. §S's `P0 P1 P2 P3 P4` are declared and rendered **empty**, and the axis says `UNPRODUCED` in a named state. No priority, severity or P-level field exists anywhere in this fabric — not in work-engine's `items_index`, not in the item-card schema's `required`, not here. The board reports the one real signal it has — `titles_naming_a_level`, every title containing a literal `P0`–`P4` (**1 live: `litellm/LIT-0103`**, over all 17 rows including completed ones) — and that field **never fills a bucket**. Bucketing by `kind` or by `state` and printing the result under P0–P4 would invent a taxonomy and present it in a column a reader has been told means priority. | A priority column filled by a proxy, which is worse than an empty one because it is believed. |
| REQ-05 | **PARTIAL** | Of the six declared source kinds, **one is exercised**: every `source_refs` value in the live board is a local document path. `feishu-doc`, `feishu-wiki`, `im-thread` and `github-issue` are declared in the contract and **no Item carries one yet**; `candidate_mappings` is empty on all 17 rows. The execution-session column is joined from the §17 binding store and is **`-` on every row**, because no session has ever been created through that path on this node — the join is exercised by test, not by live data. | A five-kind intake surface that reads as complete. It is not: what exists is the shape, and four of its five external kinds have never carried an Item. |

**The board has no reader, and that is the design rather than a gap.** Nothing
routes, admits or resolves on it, which is why `serverosctl requirement board`
prints and writes **nothing** unless `--write` is given, and why `--output-dir`
is refused inside the boards root it reads — the first draft wrote there and the
next run would have read its own output back as a project board and refused the
whole composition over a schema mismatch it had created itself. That failure
appears on the **second** run, which is the worst place for one to appear.

REQ-04 is the row that needs a person, and it is already on the list below:
introducing a priority field is a decision about where it lives, not a task.

---

## Human Control, and the notification path

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| HUM-01 | **EXISTS — one producer of seven** | The **producer**, for the one event that had a detector behind it. `runtime/alerting.py#notify_for` raises `alert.opened` and `alert.resolved`; both are `IMPLEMENTED` with a `raised_by` in `runtime/notification-contract.yaml#producers`, and the gate checks the file and the symbol resolve *and* that the event name is a literal in it. The other six are `DECLARED_ABSENT` **with the reason and a disposition written on each**, not parked in an exemption table. | **Unless a person runs a command, nobody is told anything.** The module that would tell them was finished and unreachable. It is now reachable from the one place where a detector was already running and already graded. |
| HUM-02 | **EXISTS** | An anomaly → event bridge. `runtime/daemon.py#alert_pass` evaluates both detectors every tick (`runtime/anomaly.py` and `runtime/resource_anomaly.py`) through the daemon's **own** observers, and `runtime/alerting.py#evaluate` turns graded codes into alerts with an identity. Only graded codes: a code in `codes_not_evaluated`, or a severity of `unknown`, **does not notify** — "nothing was evaluated" is not "the node is fine". | A node that knows something is wrong and reports it only to whoever asks. This is the piece that converts a working detector into a notification. |
| HUM-03 | **EXISTS** | A notification policy, in `runtime/notification-contract.yaml#alerts`. Identity is `code + device_ref + subject_ref`, and **`identity_is_not: [pid, container_name, timestamp]`** is part of it. Cooldown is 900s / 1h / 6h by severity with the clock on `last_attempt_at` and not `last_notified_at` — otherwise a transport that is down silences the alert for as long as it stays down. Hysteresis closes at `threshold - margin`, with the margins left in `attribution-contract.yaml` where the thresholds they modify already live. | Alert fatigue as the eventual failure of HUM-01/02 done naively — every graded code becomes a message, and the messages stop being read. |
| HUM-04 | **UNSUPPORTED** | An approval transport. `human.approval.transport` is `UNSUPPORTED` and `request_approval` **raises** `HumanControlUnsupported` rather than returning a soft value. This is now also the reason `alert.ack` and `alert.snooze` are `transport: LOCAL_ONLY, surfaces: [serverosctl]` — a Feishu message cannot carry a decision back, so a design that let a person "reply ACK" would produce alerts nobody could close. | Anything irreversible that would be gated on an approval this transport cannot carry. The capability table is measured on this device and every entry carries `measured_by`. |
| HUM-05 | **UNKNOWN** | The answer to whether `human.identity.verify` works **inside** a BotMux turn. It is `UNKNOWN` rather than `UNSUPPORTED` on purpose — the surface exists and BotMux correctly declined to verify *this* process, because this process is not in a turn. | An `UNKNOWN` flattened into a `no` produces a system that stops asking. Closing it is an experiment, not an implementation. |

HUM-01, HUM-02 and HUM-03 were done in one pass, in that order, as the ledger
said to. **The split that made it work is the one to keep**: `alert_pass()`
decides the facts and writes an intent, holding no provider; `serverosctl alert
sync` is the only surface that holds a transport and the only one that can write
`last_delivery ∈ DELIVERED · FAILED · UNSUPPORTED · UNKNOWN`, and **none of the
four resolves an alert**. A daemon that can send is a daemon that cannot tell
you the transport is broken, which is exactly when its answer is wanted.

None of the three can act on the world. **They can only tell a person**, which
is why they went before anything in the Ability or Coordination columns — and
the recommendation an alert carries is always `REVIEW` / `MONITOR` /
`GOVERNANCE_REQUIRED`, with the sentence stating that nothing was stopped.
**An alert is not an authorization.**

HUM-04 and HUM-05 are the two entries in this page that **a person has to
close rather than a change**. HUM-04 needs a surface that does not exist and
may have to come from upstream; HUM-05 needs someone to run a probe from inside
a BotMux turn. No amount of code here substitutes for either.

---

## Observation, ownership, and what a person is shown

Last round froze this plane, so most of it is `EXISTS` and the gaps are
specific. What is missing is almost entirely the **last hop** — the part
between a correct measurement and a person who can act on it.

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| OBS-01 | **EXISTS** | **An alert state machine**, in `runtime/alerting.py`, state at `state/serveros/alerts/state.json` through the same `_atomic_write` the control store uses. Four states — `OPEN · ACKNOWLEDGED · SNOOZED · RESOLVED` — over an identity of `code + device_ref + subject_ref`, with dedupe, cooldown, hysteresis and an announced recovery. An `ACKNOWLEDGED` alert **never becomes RESOLVED on its own**; a `SNOOZED` one keeps observing and speaks again when its window ends if it is still firing. §12's rule is enforced across the delivery seam: an intent is not a delivery, only `record_delivery` moves `last_notified_at`, and a delivery that did not arrive leaves the alert exactly as open as it was. Surfaces: `serverosctl alert list · inspect · ack · snooze · sync`, and `alert.list` / `alert.inspect` on the node MCP — the **write** half is deliberately not on MCP, which holds no state and changes nothing. | A condition that persists six hours becomes **six identical cards**, and a metric oscillating around its threshold produces an unbounded number of them — which trains people to ignore all of them, and the failure after that is a real alert nobody reads. |
| OBS-02 | PARTIAL | `RESTARTING` and `NO_HEALTHCHECK` have **no representation**. `runtime/containers.py:295` carries Docker's raw value (`healthy\|unhealthy\|starting\|none`) through verbatim as evidence; `runtime/probes.py:465` maps it onto four axes; the derived states are `UNAVAILABLE · UNKNOWN · STOPPED · NOT_RUNNING · NOT_READY · FUNCTIONALLY_FAILED · DEGRADED · HEALTHY`. So mid-restart and no-healthcheck both read `DEGRADED`. | One word for a **transient condition** and a **permanent property** — the two a person most needs to tell apart. The precedent to copy is `botmux_watchdog.py`, which models exactly this and adds the transition nobody else has: an explicit recovery, announced. |
| OBS-03 | ABSENT | The three display regions — `HOST` / `MANAGED-ATTRIBUTED` / `UNATTRIBUTED`. Today scope is mixed and a reader has to work out whose a number is. | The ownership split not being visible where it is decided. The monitor's own five measurement 口径 are the same discipline applied to numbers rather than to regions, and one of them is already left **deliberately blank** because the value cannot be computed — that blank is invariant 11, on a card, in production. |
| OBS-04 | ABSENT | A latency axis in ServerOS, and `NOT_CONFIGURED`. Both are **deliberately** absent rather than missing: the only latency measurements in the ecosystem are the ops monitor's `probe_proxy.ms` and `history.jsonl.m_egress_ms`, and a tunnel declared nowhere is not a degraded tunnel — it is `PROVIDER_NOT_INSTALLED`. Recorded so that a future reader finds a decision rather than a hole. | A reader who finds the gap and fills it with an invention. |
| OBS-05 | **EXISTS — the last hop, and where the numbers a person reads come from** | Tracked as **HUM-01/02/03** and **OBS-01**, now closed. The one thing this round added *for* the monitoring side is the shape of a finding: a card that said `codex-app-server 5 processes total 279.5%` read **top contributor (highest cpu): pid 79288 … 279.5% of one core**, because a group total is arithmetic and not a thing anybody can act on. A PID is still not an identity — the alert keys on the canonical `runtime-group:` ref, so a process killed and relaunched by its supervisor is **one** alert and not one per restart. | A reader who arrives from the monitoring side and finds a group name where they need a process, or a hole where the last hop should be. |

| OBS-06 | **EXISTS — the family has a canonical home, and it is not a new name** | The `Runtime & Resources` family resolves through `runtime-management`, whose `domain.primary` is `serveros.operations.runtime` and whose `taxonomy_refs` name `serveros.operations`. Deriving the tree from the catalogue's own rows gives 15 nodes with that one occupied. The four other names the family was proposed under — `resource-monitoring`, `container-inspection`, `resource-governance`, tunnel/port inspection — are **absent from every ref** and are not created: §4 forbids manufacturing an ability so a tree looks complete, and the observation logic they would duplicate already lives in the Node Runtime semantic service. | A reader who expects a family to mean a set of new ability names, and builds four of them beside a tree that already answers the question. |
| OBS-07 | **EXISTS — and it was the ServerOS side that was wrong** | `inventory.pressure()` asserted *"Real memory pressure"* from the swap **watermark** alone. Measured on this host 2026-09-15: 99.2% of an 8 GiB swap file used, while memory PSI read `some`/`full` avg10/avg60/avg300 all `0.00`, `pswpout` read 3208181 at two moments forty minutes apart, and 15.7 GiB of 31.0 GiB was available. Nothing was paging. The note now requires the watermark **and** a PSI reading, reports the PSI numbers and both thresholds it compared against, and **states that it did not establish pressure** when `/proc/pressure` cannot be read — the same refusal `pressure_stall` already made one method above. | A watermark reported as current pressure, on a machine that is idle. `vm.swappiness=10` means pages return from swap only when touched again, so a swap file that filled once stays full indefinitely and the number describes the machine's past. |
| OBS-08 | ABSENT — **and blocked on a repository this round does not own** | §V's card requirement is unmet on both of the monitor's rendering surfaces: `monitor.py:295` and `monitor.py:808` print `{count} 个进程 合计占 {c}% 单核`, the exact shape §V forbids, and neither reads `checks.py:610`'s `pids` — the per-process list it already collected by the same two-point `/proc/<pid>/stat` differential the rest of the module uses. The repair is a rendering change over data that exists. It is not made here because `workspace/ops` is read-only to this round **and its entire `scripts/monitor/` tree is untracked** (`?? scripts/monitor/`, `git ls-files` empty at `b08d7ca`): the production alerting path has no revision to review against and none to revert to. | A card whose only CPU figure is an arithmetic total, on the surface a person reads every thirty minutes — and a finding that cannot be repaired because the file it lives in was never committed. |

The **swap threshold pair has two producers.** The failure above was found and
fixed **twice, one day apart, in two repositories**: `ops/scripts/monitor/
rules.yaml` carries `swap_used_max_gb: 6.0` beside
`swap_pressure_psi_some_avg60_min: 5.0` with the identical reasoning written
out, dated 2026-09-14, and its card says *"不告警：水位是历史峰值，页早已在内存里"*.
ServerOS's pair is 90.0% and 1.0%, and the two fail-loud directions are
opposite — the monitor alerts anyway when PSI is unreadable, ServerOS refuses
to claim either way — which is correct in both cases and worth keeping
different: a notification should speak when it cannot measure, a note attached
to a measurement should say what it did not establish. **What is not settled is
that 1.0% and 5.0% are both called "memory pressure".** Between them sits a
host one producer would call pressured and the other normal. Aligning them is a
threshold change in one of two repositories, so it is recorded here and left
for a person.

The **top-contributor shape is right in ServerOS and ranked by the metric the
monitor rejects.** `alerting.detail_for` renders *"top contributor (highest
cpu): pid … % of one core"*, which is what §V asks for, but `graph.contributors()`
orders by summed **lifetime average** CPU and says so (`cpu_metric:
"lifetime_average_percent_of_one_core"`) — the same unit `checks.py:574`
documents refusing, because a process that burns hard for one second and exits
reads as ~100%. Both are deliberate: `cpu_percent_high`'s contract entry says
*"sustained is the claim"*, and for a staleness question a lifetime average is
the honest unit. §V's card is asking a different question — what is burning
**now** — and ServerOS already computes that differential in
`inventory.cpu_usage()`, just not per process.

The **review-candidate versus `CLEANUP_CANDIDATE`** distinction belongs in the
same pass and is already correct in the code: an anomaly code is a heuristic
flag that confers nothing, while `CLEANUP_CANDIDATE`
(`runtime/runtime_hygiene.py`) requires **a minimum of two independent facts**
from different sources, deliberately excludes `age_seconds`, and is
`LABEL_ONLY` — every other class in its nine-word ladder is in
`NEVER_ACTIONABLE`. Nothing consumes it as a queue. **That property is the one
to protect**, and it is the reason `REVIEW_CANDIDATE` should stay a *severity*
on an anomaly rather than becoming a second class in that ladder.

The rename this round did make is the opposite kind and does not touch that
ladder: `STOPPED_RECLAIM_CANDIDATE` became **`STOPPED_REVIEW_CANDIDATE`** in
`hygiene.py` and `anomaly.py`, because *"reclaim"* named an action while both
producers refuse to recommend one, and because one name was covering **two
mutually exclusive owner facts** — `hygiene` emits it when a typed owner *is*
declared ("Nameable, not abandoned"), `anomaly` when no owner *can* be
established. Neither is actionable and both remain where they were. The rename
cost nothing because no alert state file existed to carry the old identity, and
`tests/test_alert_authorization.py` now asserts the property that was prose
until this round: the alert vocabularies and the actuator's operation list are
disjoint, and `Actuator.perform` refuses every alert code, advice word and
hygiene class **by name** — with a positive control, so the loop cannot pass
against an actuator that refuses everything.

---

## Feishu Knowledge Work — frozen for v1.3, not started in v1.2

**This is a scope decision, not a gap, and the difference matters for how it is
read.** Nothing below is a promise or a schedule; it is the list of names that a
reader might reasonably expect to find implemented in v1.2 and will not, with
the measurement that says so.

The sequencing rule it follows was stated by the person who made the call:

> v1.2 proves that **any** Ability Family can be correctly discovered, expanded,
> resolved, constrained and executed. v1.3 proves that **Feishu**, as the first
> complex consumer, can run completely on that Fabric.

Doing both in one round would mean validating an unstable family against an
unstable generic layer, which is the dependency the ordering exists to avoid.
So the family is measured, named, and stopped.

### The measurement

Taken 2026-09-15 against `abilities-manifest.yaml` and `skills/` **across all
37 refs** in the ability-management repository, not against the working tree —
which is why the answer is per-branch rather than a single yes or no:

| member | state | where it actually is |
|---|---|---|
| `feishu-space-management` | **canonical** | on `origin/main` and on every other ref. `domain.primary: serveros.corporate.feishu`, `taxonomy_refs: [serveros.corporate]`, `artifact_mode: vendored`, two revisions, `installations: []` — the catalogue does not claim it is installed anywhere, and this node agrees: `serverosctl ability status feishu-space-management` answers *"not in the local projection"* |
| `document-stewardship` | **unmerged** | on four refs and no others: `serveros/ability-registry-v3-layout-fleet`, `serveros/convergence-20260911`, `serveros/srv-0003-docstew`, `serveros/system-convergence-closure-20260911`. Its `payload/` is **byte-identical across all four** (`SKILL.md` 393c6422, both references, `maintain.py` 4e143388, tests 158318b7); what differs is packaging — one variant carries `ability.yaml` + `revisions.yaml` and a different `ABILITY.md`, the other three carry neither |
| `personal-knowledge-management` | **absent** | zero hits, every ref |
| `team-document-governance` | **absent** | zero hits, every ref |
| `content-archiving` | **absent** | zero hits, every ref |
| `pack:feishu-knowledge-work` | **absent** | zero hits. The five declared packs — `autonomous-quality-loop`, `litellm-development`, `cross-device-collaboration`, `agent-orchestration`, `quality-delivery` — all resolve `CLOSED` against the catalogue, and none mentions the family |

**One thing is already true and is worth more than the negative list.** The tree
node this family would live in **already exists and is populated**:
`serverosctl ability tree` derives `serveros.corporate.feishu` from a real
installed ability, `feishu-report`, which is an optional member of the
`quality-delivery` pack. So v1.3 extends a node that resolves today rather than
creating an axis — and `feishu-report` cross-referencing `serveros.corporate` is
the mechanism §4's `taxonomy_refs` exists for, working on real data.

### The frozen names

```text
abilities       document-stewardship · personal-knowledge-management
                team-document-governance · content-archiving
                feishu-space-management (canonical, not installed here)
pack            pack:feishu-knowledge-work
profiles        DocumentGovernanceProfile · StyleProfile
mutation rule   team high-risk mutation → pre-change backup
archive policy  DENY | REFERENCE_ONLY | EXPAND
```

**And the boundary that must travel with the list.** A live work document on
this machine does keep a completed item's title with its strikethrough, moves it
to a *"已完成"* section, and can bring it back on reopen. That behaviour is
**one profile**, `DocumentGovernanceProfile: litellm-living-work-doc`, and it is
**not** a property of `team-document-governance`. A v1.3 implementation that
read the observed behaviour as the general rule would apply one document's
convention to a team's records — which is the kind of generalization this
repository has paid for before.

### What v1.2 proves instead

The generic claim is the deliverable, and the measurement above is also its
test case: a family that is **partly materialized** is exactly what a fabric
that cannot tell *absent* from *invented* gets wrong. The v1.2 claim is that it
does not — `ability status` answers *"not in the local projection"* and adds
that absence from a projection is not proof of absence on the authority;
`ability tree` places what is classified and lists the rest as unplaced rather
than defaulting them into a node; `pack inspect` refuses without a catalogue
rather than resolving against the local projection and reporting every pack
`UNAVAILABLE`. Those three answers were taken against this family and are
recorded in `SRV-0009/RUN-0001/05-FEISHU-KNOWLEDGE-WORK-FAMILY.md`.

---

## The order

The ordering rule is not "biggest gap first". It is **reversibility**, then
value per unit of risk. Everything that only *tells* comes before anything that
*does*, and anything whose wrong answer blocks existing work goes last.

```text
Phase 0  Reading the ledger honestly                          no authorization
  GEN-01  DONE — four gates: commands, producers, readers, enforcer claims
  the COMPUTED_UNREAD set:  MEM-06 (open) · RUN-03 · RUN-04 — the last two DONE
  the DEAD and WRONG set:   RUN-06 · ABL-04 — both open
  the PARTIAL doc drifts:   ABL-03 — open

Phase 1  Telling a person — nothing here can act               no authorization
  HUM-01  DONE in v1.2 — one producer of seven, the other six named
  HUM-02  DONE in v1.2 — the anomaly → event bridge, graded codes only
  HUM-03  DONE in v1.2 — identity, cooldown, hysteresis, delivery record
  OBS-01  DONE in v1.2 — the alert state machine
  OBS-02  the two container states — remains
  OBS-03  the three regions — remains

Phase 2  Closing the declared-and-absent on the read path       authorization: policy
  MEM-01  DONE in v1.2 — the recall firewall, and MEM-03's scope half with it
  MEM-02  the write pipeline, then MEM-03's remaining half, then MEM-05
  RUN-01  DONE — the per-device binding, and RUN-02's feature vocabulary with it
  RUN-05  the same pass placed the requirement on the provider, not the adapter
          — its row stays ABSENT, which is now the decision rather than the gap
  ABL-01  DONE (§10) — operation-level resolution; ABL-02 remains
  ABL-06  resolution as an MCP surface — remains, and is now a decision

Phase 3  Coordination — greenfield, and last                    authorization: design
  COORD-01 · COORD-02 · COORD-03  DONE in v1.2 — the same pass, and it cost less
          than the entries Phase 0 ranked ahead of it, because greenfield
          turned out to mean "nothing to reconcile" rather than "unbounded"
  COORD-04  the only entry left in this plane, and it waits on the priority axis

Phase 4  Feishu Knowledge Work — frozen, and deliberately not started
  the family, the pack, the two profiles and the archive policy
  see the section above: this is a scope decision, not a gap
```

Three things about this order are worth saying out loud.

**Phase 0 looks like tidying and is not.** Every entry in it is a place where
the code is *already correct* and the declaration is wrong. Fixing them costs
almost nothing and removes the class of error that produced the last two
rounds' most expensive findings.

**Phase 1 is where the product gets a voice, and it has one from v1.2.** It was
deliberately first among the things that do anything, because it can only ever
add information — and the sentence a person now receives says so: when a
recommendation is present, it states that nothing was stopped. What took the
work was not the sending. It was the four properties that make a voice worth
listening to: an identity that survives a restart, a cooldown so one condition
is one message, a hysteresis margin so a wobbling number cannot machine-gun a
person, and an announced recovery — because silence after an alert is
indistinguishable from a dead timer.

**MEM-01 was the highest-severity entry, sat in Phase 2 anyway, and is done.**
It was listed ahead of the rest of its phase because it was small, and it was
small for the reason the whole ledger keeps rediscovering: the prose that
specified it had already named the mechanism. `handoff.pending(records,
device_ref)` took the caller's scope as an **argument** and refused to resolve
it itself; the read path now takes `reader` the same way and raises instead of
answering. What it needed from a person was the policy — refuse by default —
and that ruling is now in `record-contract.yaml#scope.reader_rule` rather than
in a decision nobody wrote down.

## What has to be decided by a person

```text
a push to another repository            not implied by any approval to date
a live promotion of this product        rebuild, promote, restart, rematerialize
a new Item-card field                   priority / severity do not exist anywhere.
                                        §S's board renders P0-P4 empty and marks the
                                        axis UNPRODUCED rather than filling it from a
                                        proxy; run `serverosctl requirement board` to
                                        see the five empty buckets and the one real
                                        signal (titles containing a P-level)
approval transport (HUM-04)             a surface that has to exist, possibly upstream
identity verification inside a turn     an experiment, not an implementation
what leaves a catalogue                 a pack may not shrink to one device
workload scratch containment            belongs to the provider's launch config,
                                        not to the observer — see
                                        `runtime-resources.md` §9.1
```

Every one of these is a decision rather than a task, and none of them is
unblocked by writing more code.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the principles this ledger is the gap of
- [`ability-fabric.md`](ability-fabric.md) — ABL entries in context
- [`memory-fabric.md`](memory-fabric.md) — MEM entries in context
- [`agent-runtime-model.md`](agent-runtime-model.md) — RUN-01 and RUN-02
- [`human-control.md`](human-control.md) — HUM entries, and the six measured capabilities
- [`runtime-resources.md`](runtime-resources.md) — OBS entries, and the notification path
- [`repository-roles.md`](repository-roles.md) — which repository owns which entry
