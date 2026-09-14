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
| GEN-01 | **PARTIAL** | **A gate — and it exists for one class and not the other three.** `tests/test_declared_commands.py` walks the real argparse parser and fails when any scanned document tells a reader to run a verb that does not exist. Nothing comparable holds a contract to its own `producers:` entry, its `required_fields:`, or a rule with no enforcer. `abilities/pack-contract.yaml`'s `closure.rule` greps only itself; `notification-contract.yaml:producers` names six emitters that do not exist. | The systemic one, observed in **seven instances inside a single module** during the resource-observation round with the test suite green through all seven — and in an **eighth during this round**, when that one gate caught this page's own author citing `serverosctl anomaly explain` in a form the parser does not accept. |

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
| RUN-01 | **DECLARED_ABSENT** | A per-device runtime **binding** — version, features, installation. `grep DeviceRuntimeBinding\|runtime_binding` returns nothing. `fleet/registry.yaml` gives each device a bare `agents: [claude-code, codex]`, which is **membership**. The contract at `:37` and `:193` asserts installations are *"already modelled — one per device, in the registry, with an owner"*, and there are no installation records. | A device reported as able to do something and the command failing. Without a binding, every answer about a device's runtime is answered from the family's global record, which is the union error the eight-axis intersection exists to prevent. |
| RUN-02 | ABSENT | A runtime **feature** vocabulary. `features:` has zero hits across all three repositories; `mcp.client`, `skill.native`, `gui`, `app.server`, `native.extension`, `hook.pre` have zero hits. The nearest precedent is transport's nine closed `capability_levels` — reachability, not consumption. | The CLI/desktop difference, which is real and currently has nowhere to live except a new runtime identity — which is refused. |
| RUN-03 | **COMPUTED_UNREAD** | Any reader for `version_observed_by` or `capability_probe`. Both are in `required_fields` (`:29`); `capability_probe` appears nowhere else in the tree; `version_observed_by` appears three times, all in the same file. The contract's rule *"a version with no `observed_at` is refused"* (`:147`) has no enforcer. | A remembered version being read as a measured one — the exact distinction `:137-142` reasons about correctly and nobody executes. |
| RUN-04 | **COMPUTED_UNREAD** | `skill_discovery` and `session_lifecycle` are declared, are **global single values rather than per-runtime maps**, and are read by nothing. The parity checker says so itself (`runtime_parity.py:41-45`) and explains that the comparison is therefore vacuous. | None yet — and the note in the code is an honest description of a real thing, not a defect. It becomes one the moment a reader assumes these are per-runtime. |
| RUN-05 | ABSENT | An adapter `requires: {runtime, features}`. The adapter record is a **materialization address** — where a runtime reads a provider (`provider-contract.yaml:190-202`) — not a statement of what the runtime must have. | No observed failure. Requirements are checked provider-side today and that path works; this is the third question (*"does this runtime have what this surface needs"*) that nothing answers. |
| RUN-06 | **DEAD** | `fleet/agent_control_router.py:179-181` reads `getattr(provider, "runtime_adapters", None)`. No provider in the tree defines that attribute; the grep hits only the `getattr` itself. A branch that can never be taken and therefore cannot fail. | Same family as `NOT_YET_WRITTEN` and `COMPUTED_ON_DEMAND` from the previous round: an output field that is always empty and never complains. |
| ABL-01 | **EXISTS** (§10) | **`ability#operation`** — resolution at the operation, not the ability. What it was: `resolver.py` filtered on the whole ability and `provider-contract.yaml` had no `operations:` key. What it is now: an optional `operations:` on the provider record, the reference parsed **once** at `resolver.py#parse_ability_ref`, and the three-valued asymmetry copied verbatim from `target.py#_support_state` rather than reinvented — a verb absent from a published table is `UNSUPPORTED` → `INCOMPATIBLE`/`ACTION_UNSUPPORTED`, and no table at all is `UNKNOWN`, which stays on the **candidate** and does not reach `record.reason` (`target-contract.yaml`'s ruling for the same axis, applied not re-decided). `ACTION_UNSUPPORTED` is reused from `target-contract.yaml#reasons`, not coined — and deliberately **not** added to `provider-contract.yaml#status.reasons`, whose closed list is asserted equal to `provider_instance.REASONS` and is about whether a provider is *there*, not what it does. The rule that makes this a widening rather than a reversal: **a bare `ability:x` never consults the table** — otherwise every provider declaring no `operations:` would answer `UNKNOWN` to an ordinary `#read`, withdrawing capabilities that already worked. | `ability:team-document-management#read` works everywhere and `#restore` needs a credential one device lacks. Reporting the ability `UNAVAILABLE` removes a working read; reporting it `AVAILABLE` makes a restore that will fail look available. |
| ABL-02 | PARTIAL | **Device-relative pack resolution.** `packs.py:222` takes `(contract, catalogue, catalogue_revision, catalogue_repo)` and no device at all, while `pack-contract.yaml:37` declares a member that *"cannot be resolved on the target device"*. So `PARTIAL` means *"absent from the pinned catalogue"* and is documented to mean *"this device cannot do it"* — one word apart in the contract, unrelated in the code. | A person reading `PARTIAL` as a statement about their machine. The build-time closure check is real and stays; what is missing is the device-relative half, and no code joins a pack to `resolver.py` or `target.py` at all. |
| ABL-03 | PARTIAL | The five concepts. `provider-contract.yaml:23-30` names five (Ability, Provider, **Profile**, **Binding**, Adapter); the `objects:` block in the same file declares three. Profile and Binding live in a comment, pointing at `fleet/profiles.yaml` and `fleet/registry.yaml` — neither of which uses either word. | A reader who follows the comment to a file that does not use the vocabulary, and concludes the concept is implemented. Resolvable by documentation alone, and it is a real risk to the next author only. |
| ABL-04 | **WRONG** | `abilities/pack-contract.yaml:127` cites `ability-management@7bc7687:ability-packs.yaml`. At that revision the file does not exist; it lands at `0091d8d`. The location is right and the revision is not. | Provenance that cannot be re-checked, in the one field whose entire job is to be re-checkable. Pure documentation error, safe to correct, **and not corrected here** — this round does not touch contracts. |
| ABL-05 | PARTIAL | The eighth input, `task_context`, is inert — `resolver.py:294-300` says so. The axis exists and never narrows anything. | No observed failure. Listed because the `EffectiveAbility` formula is quoted as an eight-way intersection, and one of the eight is decorative. |
| ABL-06 | ABSENT — **decided, not overlooked** | An MCP surface for ability resolution. The CLI exposes `ability resolve`; no MCP tool does — which is why an Agent cannot ask the question natively. **Why it stays absent**, established rather than assumed: the node MCP surface is a thin adapter over `serverosd`, and §23 forbids the core from loading the plane it governs, so `ability.resolve` and `pack.inspect` would have forced a second resolver — the failure the W1 boundary gate exists to catch. `fleet/mcp-contract.yaml`'s `does_not_own` now names both, and `ability.inspect` answers `resolved_by: "serverosctl ability resolve"` instead of a verdict. §10 adds a third name to that list: `operations_declared` is `null` on this surface, never `false`, because operation declarations live on provider records the daemon holds none of. | An Agent that cannot ask, and therefore guesses. `capability-discovery.md` holds the requirement. |

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

**Every entry here is ABSENT.** This is not a deferred plane — it is a plane
that has never been started. A full-workspace grep including `ops/docs/proposals/`
and `ops/.agents/review/` returns **zero hits** for `session_role`,
`bound_item_ref`, `THREAD_ITEM_MISMATCH`, `THREAD_CLOSED_FOR_NEW_WORK`,
`READ_ONLY_CLOSURE` and `work.item.discovered`. No code, no contract, and no
proposal has ever written any of them.

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| COORD-01 | ABSENT | `session_role: coordinator \| execution`. The only `role` in the session contract is `serveros_role` (`agent-session-contract.yaml:231`), which is ServerOS's role relative to the provider — a different subject entirely. | No observed failure on this machine yet. The failure it prevents is the one every multi-agent system finds eventually: two sessions claiming the same Item, or a session that cannot tell whether it is allowed to start work. |
| COORD-02 | ABSENT | `bound_item_ref`. `work_item` is an **optional, unvalidated passthrough string** — `agent-session-contract.yaml:184` puts it in `optional:`, not `required:` — carried through `agent_mcp.py:499` and `agent_control_router.py:248`, and **the execution layer never receives it**: `mcp/agent_session_service.py:333`'s `create()` signature has no `work_item` parameter at all. Nothing binds, validates, freezes or compares it. | A session that believes it is working on an Item, and a system with no record that it is. The value is transported and discarded one hop short of the thing that would act on it. |
| COORD-03 | ABSENT | A cross-thread guard. The **structure already exists two planes over and is worth copying rather than reinventing**: `agent_mcp.py`'s `_device_for()` raises `INVALID_CONTEXT` (*"a session runs on one device and this call will not pick which"*), and `agent_session_service.send()` raises `ActionNotSupported` (*"a session that has reached CLOSED declares `session.send` UNSUPPORTED"*). Note both are **device- and state-scoped, never Item-scoped** — the Item has never been a comparison operand. | The whole point of COORD-01/02. Without a guard, binding is a comment. |
| COORD-04 | ABSENT | `work.item.discovered`, and any way for a discovery to reach a coordinator. work-engine's `INBOX → AVAILABLE → … → CLOSED` has no `DISCOVERED`; the emitted set is `created · transitioned · reopened · outcome_recorded · deliverable_added · run_terminated`. **And no priority, severity or P0 field exists anywhere in the system** — not even in the item-card schema's `required`. The two repositories have no runtime coupling at all; the boundary is one declared path string (`fleet/mcp-contract.yaml:263-266`). | A discovery that lands as a row and waits to be pulled. There is nothing to escalate *to* and no word for *urgent*. |

**The asymmetry, stated plainly, because the ordering below depends on it.**
Coordination is the least mature plane in the product and the one a design
document is most tempted to put first, because it is the most interesting. It
should go **last**. It is the only entry in this ledger with nothing partial to
build on and no precedent to copy at the Item level — and a guard that is wrong
does not merely fail, it blocks work that used to succeed. Everything it needs
(the guard shape, the refusal vocabulary, the `INVALID_CONTEXT` precedent)
already exists and is waiting.

---

## Human Control, and the notification path

| id | status | what is missing | the failure it answers |
|---|---|---|---|
| HUM-01 | **DECLARED_ABSENT** | **Every producer.** `runtime/notifications.py` is complete, tested, and has **no importer outside `tests/`**; `runtime/daemon.py` never names it. The contract's `producers:` block names six events that no code emits. | **Unless a person runs a command, nobody is told anything.** The module that would tell them is finished and unreachable. |
| HUM-02 | ABSENT | An anomaly → event bridge. `runtime/anomaly.py` grades its codes with severity, and the only consumers are **pull** surfaces (`serverosctl anomaly list`, `serverosctl anomaly explain`, `anomaly.list`, `resource.anomaly.list`). | A node that knows something is wrong and reports it only to whoever asks. This is the piece that converts a working detector into a notification. |
| HUM-03 | ABSENT | A notification policy. Zero hits workspace-wide: nothing decides which events reach which conversation, at what volume, or when to stay quiet. | Alert fatigue as the eventual failure of HUM-01/02 done naively — every graded code becomes a message, and the messages stop being read. |
| HUM-04 | **UNSUPPORTED** | An approval transport. `human.approval.transport` is `UNSUPPORTED` and `request_approval` **raises** `HumanControlUnsupported` rather than returning a soft value. | Anything irreversible that would be gated on an approval this transport cannot carry. The capability table is measured on this device and every entry carries `measured_by`. |
| HUM-05 | **UNKNOWN** | The answer to whether `human.identity.verify` works **inside** a BotMux turn. It is `UNKNOWN` rather than `UNSUPPORTED` on purpose — the surface exists and BotMux correctly declined to verify *this* process, because this process is not in a turn. | An `UNKNOWN` flattened into a `no` produces a system that stops asking. Closing it is an experiment, not an implementation. |

HUM-01 is **the best effort-to-value entry in this ledger.** The expensive half
— a closed nine-event vocabulary, severity fixed per event rather than per call
site, ServerOS-authored templates, and a `deliver()` that consults
`human.notification.transport` before claiming a send it did not make — is done
and correct. What is missing is the boring half: something to call it. HUM-01,
HUM-02 and HUM-03 belong in one pass, in that order, and none of them can act
on the world. **They can only tell a person**, which is why they go before
anything in the Ability or Coordination columns.

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
| OBS-01 | ABSENT | **An alert state machine.** `ACKNOWLEDGED`, `SNOOZED`, `RESOLVED`, `alert_ref`, `first_seen`, `hysteresis` and `cooldown` have **zero hits** in `ops/scripts` and in this repository. What exists: `evaluate()`'s transient `{level, key, msg}` list recomputed from scratch every run (so **an alert has no identity across runs**), `run.sh`'s hour-scoped idempotency key, and `history.jsonl` (a metric ledger, not an alert ledger). | A condition that persists six hours becomes **six identical cards**, and a metric oscillating around its threshold produces an unbounded number of them — which trains people to ignore all of them, and the failure after that is a real alert nobody reads. |
| OBS-02 | PARTIAL | `RESTARTING` and `NO_HEALTHCHECK` have **no representation**. `runtime/containers.py:295` carries Docker's raw value (`healthy\|unhealthy\|starting\|none`) through verbatim as evidence; `runtime/probes.py:465` maps it onto four axes; the derived states are `UNAVAILABLE · UNKNOWN · STOPPED · NOT_RUNNING · NOT_READY · FUNCTIONALLY_FAILED · DEGRADED · HEALTHY`. So mid-restart and no-healthcheck both read `DEGRADED`. | One word for a **transient condition** and a **permanent property** — the two a person most needs to tell apart. The precedent to copy is `botmux_watchdog.py`, which models exactly this and adds the transition nobody else has: an explicit recovery, announced. |
| OBS-03 | ABSENT | The three display regions — `HOST` / `MANAGED-ATTRIBUTED` / `UNATTRIBUTED`. Today scope is mixed and a reader has to work out whose a number is. | The ownership split not being visible where it is decided. The monitor's own five measurement 口径 are the same discipline applied to numbers rather than to regions, and one of them is already left **deliberately blank** because the value cannot be computed — that blank is invariant 11, on a card, in production. |
| OBS-04 | ABSENT | A latency axis in ServerOS, and `NOT_CONFIGURED`. Both are **deliberately** absent rather than missing: the only latency measurements in the ecosystem are the ops monitor's `probe_proxy.ms` and `history.jsonl.m_egress_ms`, and a tunnel declared nowhere is not a degraded tunnel — it is `PROVIDER_NOT_INSTALLED`. Recorded so that a future reader finds a decision rather than a hole. | A reader who finds the gap and fills it with an invention. |
| OBS-05 | — | Same gap as **HUM-01/02/03**, which is where it is tracked. Listed here only so a reader who arrives from the monitoring side finds it. | — |

The **review-candidate versus `CLEANUP_CANDIDATE`** distinction belongs in the
same pass and is already correct in the code: an anomaly code is a heuristic
flag that confers nothing, while `CLEANUP_CANDIDATE`
(`runtime/runtime_hygiene.py`) requires **a minimum of two independent facts**
from different sources, deliberately excludes `age_seconds`, and is
`LABEL_ONLY` — every other class in its nine-word ladder is in
`NEVER_ACTIONABLE`. Nothing consumes it as a queue. **That property is the one
to protect**, and it is the reason `REVIEW_CANDIDATE` should stay a *severity*
on an anomaly rather than becoming a second class in that ladder.

---

## The order

The ordering rule is not "biggest gap first". It is **reversibility**, then
value per unit of risk. Everything that only *tells* comes before anything that
*does*, and anything whose wrong answer blocks existing work goes last.

```text
Phase 0  Reading the ledger honestly                          no authorization
  GEN-01  a gate that fails a contract naming what does not exist
  the COMPUTED_UNREAD set:  MEM-06 · RUN-03 · RUN-04
  the DEAD and WRONG set:   RUN-06 · ABL-04
  the PARTIAL doc drifts:   ABL-03

Phase 1  Telling a person — nothing here can act               no authorization
  HUM-01  producers for the nine events
  HUM-02  the anomaly → event bridge
  HUM-03  a notification policy
  OBS-01  the alert state machine, and OBS-02's two states
  OBS-03  the three regions

Phase 2  Closing the declared-and-absent on the read path       authorization: policy
  MEM-01  DONE in v1.2 — the recall firewall, and MEM-03's scope half with it
  MEM-02  the write pipeline, then MEM-03's remaining half, then MEM-05
  RUN-01  the per-device binding, then RUN-02's feature vocabulary
  ABL-01  operation-level resolution, then ABL-02 device-relative packs
  ABL-06  resolution as an MCP surface

Phase 3  Coordination — greenfield, and last                    authorization: design
  COORD-01 · COORD-02 · COORD-03, reusing _device_for() and ActionNotSupported
  COORD-04 only after a discovery has somewhere to go
```

Three things about this order are worth saying out loud.

**Phase 0 looks like tidying and is not.** Every entry in it is a place where
the code is *already correct* and the declaration is wrong. Fixing them costs
almost nothing and removes the class of error that produced the last two
rounds' most expensive findings.

**Phase 1 is where the product gets a voice**, and it is the only phase whose
output a person experiences directly this month. It is deliberately first among
the things that do anything, because it can only ever add information.

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
a new Item-card field                   priority / severity do not exist anywhere
approval transport (HUM-04)             a surface that has to exist, possibly upstream
identity verification inside a turn     an experiment, not an implementation
what leaves a catalogue                 a pack may not shrink to one device
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
