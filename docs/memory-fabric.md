# Memory-Context Fabric

The fabric answers: **what does this work remember, where is that written down,
and what happens when a device disagrees with the answer?**

It exists because "the Agent knows" is not a storage location. Today a work
Item's memory is spread across four places that were never designed to agree —
Git history, `records/` cards, `artifacts/` evidence, and whatever a given Agent
session happens to still be holding. The fourth one is the problem: it is the
only one that is not durable and it is the one an Agent consults first.

## Who enforces this

```text
memory/record.py       validate() one record; audit() the whole ledger
memory/projection.py   build() · manifest_for() · staleness()
memory/handoff.py      checkpoint() · handoff() · pending() · answers()
memory/layering.py     the caller-side filter for Layer C hits
memory/scope.py        the recall firewall — who may read a record (§18)
serverosctl memory     audit · project · status · subject
```

Until these existed, none of the rules below was a rule a program checked. A
contract with no reader is not a harmless placeholder — it is read by the next
author, who believes it. That pathology has its own record in the ledger:
`mem:a-declaration-with-no-reader-never-fails`.

**This list is the enforcement, and it is not the whole document.** Two rules
stated here are *not* in it, and the sections that state them say so where they
are stated rather than only here: the write pipeline, and the `claim` kind. The
recall firewall used to be the third and moved into the list in v1.2, when
§18 gave it a module. Reading a rule in this document does not mean a program
checks it; reading it in the list above does.

## Three memory layers, authority stays with the owning system

```text
Layer A   Local runtime state       state/serveros/              this node, right now
Layer B   Durable reference plane   source / records / evidence  reviewable authoritative inputs
Layer C   Derived long-term memory  OpenViking + retrieval       persistent, non-authoritative
```

Memory Fabric does **not** create one product-wide authority. Source objects own source
content/revisions; B2 owns current Claims/Views; B3 owns accepted Requirement semantics;
Work Engine owns current work/control relationships; Agent Control owns trusted delegation;
providers own their external bodies. Layer B means the durable source/record/evidence plane
that memory can cite and recover from, not a universal database that replaces those owners.

Layer C may persist session-derived preferences, episodes and experience so agents can
remember across sessions and devices, but a retrieval hit cannot change the current owner,
deadline, Requirement, permission, work state or deployed truth by itself.

```text
Layer A  is machine-local, high-frequency, and disposable.
         Losing it costs a re-probe, never a business decision.
Layer B  is versioned/reviewable and carries current semantic records and evidence.
         A new device can recover those durable references; live Work Engine/Agent Control
         state is resolved from its own authority rather than reconstructed from Git.
Layer C  is durable derived memory and an index over Layer B / Session history.
         Losing it harms recall and learned convenience, so it is backed up; work must still
         continue safely from Layer B, checkpoints and source evidence.
```

Layer C being "non-authoritative" is the key property, not "must be throwaway bytes".
OpenViking may maintain its own persistent workspace and extracted memories. What it may not
do is make a memory score or generated summary outrank the source/record that governs the
current decision.

### The rule that keeps the layering honest

> **A fact that matters to current business state cannot live only in Layer C.**
> If a retrieved experience becomes a Requirement, decision, owner, deadline or permission,
> promote it through the normal B2/B3 acceptance path into the appropriate authority.

Derived personal preferences and reusable experience may remain Provider-owned memory with
source/session provenance; exact byte-for-byte regeneration is not required for them.
Current work, rights and commitments are required to survive a complete OpenViking outage.

## The five semantics

The three layers answer *where*. They do not answer *what kind of thing is
this*, and a memory system that cannot tell the two apart will file a procedure
next to an anecdote and then weigh them equally.

```text
Working       live state of the thing being worked on now
              → Work Item / checkpoint; not a long-term memory store

Episodic      what happened: this Run, incident, handoff or Session
              → source/history + derived OpenViking memory

Semantic      what is currently true: decision, constraint, owner, boundary
              → B2/current source is authoritative; OpenViking may only retrieve hints

Experience    what tended to work, fail or matter in similar situations
              → OpenViking may preserve it as derived long-term memory

Executable    a repeatable method the system is expected to perform
procedure     → Ability Fabric, not Memory Fabric

Retrieval     finding the relevant subset again
              → OpenViking/MetaCC/native providers; score never means truth
```

### Experience is memory; executable procedure is Ability

OpenViking can legitimately remember trajectories, tool-use experience and recurring
patterns. The important boundary is not whether something sounds "procedural"; it is whether
the system is expected to **execute it as a governed capability**.

```text
Memory Fabric      what the work learned / recalls   derived experience with provenance
Ability Fabric     what this system can execute       versioned procedure with runtime/effects
```

A memory such as "Playwright login usually fails when the profile lock is stale" can guide
future investigation. It does not authorize killing a browser or redefine the testing
Skill. Once a method is intended to run repeatably as system behavior, it needs the Ability
Fabric's revision, compatibility, provider, effect and acceptance controls.

The promotion path is therefore explicit:

```text
repeated observations / session experience
  ↓ relevant and supported
experience memory / method candidate
  ↓ accepted as a reusable system behavior
Skill/script/provider candidate
  ↓ G1 authoring + G1.5/A7 evaluation
ability revision
  ↓ composition / installation / fresh consumer
executable capability
```

OpenViking may generate or consolidate the experience layer automatically; it may not
self-promote that experience into an installed Skill, permission or production behavior.

### Vocabulary versus current implementation

The semantics above are a product vocabulary, not a requirement to add one record kind per
row. Current records already preserve episodes and semantic facts; OpenViking is still
UNBOUND and its official memory model is not yet wired into this product. The existing
`memory/record-contract.yaml` therefore remains an implementation-era contract, not proof
that all target memory semantics are live.

Migration should add the minimum producer/consumer fields needed for provenance, scope and
invalidation rather than mirror OpenViking's internal schema into ServerOS. In particular,
experience memory can live in the Provider while executable procedures continue to enter
G1 only through the explicit promotion path above.

## The ledger

Layer B is a ledger in the ordinary sense: append-mostly, versioned, and
reviewable. Its substrate is Git, and that choice is load-bearing — the ledger
inherits `blame`, `diff`, `revert`, and a history nobody has to be trusted to
maintain.

```text
record kinds            decision · observation · revision · handoff · finding
                        · question · checkpoint · incident
required on every record  id · kind · subject · at · by · source · revision
required for some kinds   observation also requires confidence
                          handoff also requires to_device_ref
never                     a secret, a credential, a token, a raw transcript body
```

This list said five kinds and omitted `question`, while the contract's `kinds`
block declared six and its own retention rule referred to one ("`question`
resolves, `decision` and `revision` do not"). Six is right: a question is the
kind whose whole purpose is to be picked up by the person who can answer it,
and a ledger of this shape with no way to record one is a ledger that loses
its open questions to chat. `memory/record.py` now holds the closed list and a
test asserts it against the contract, so the next disagreement fails rather
than being read past.

Two more kinds arrived with §59, and one of them is the kind the cross-device
fixture is built on. `checkpoint` is **where the work stopped and what to do
first**, written for a device that is not this one. `revision` binds an
artifact and `observation` says what was measured; neither says where to
resume, and a handoff with no checkpoint behind it moves the work without
moving the state. `incident` is separate from `finding` because the two differ
in who did the looking: a finding is a problem found by inspection, an incident
is a problem that found us, and folding Case A into `finding` would lose its
timeline and its cost. The brief's §21 list named seven things; the contract
now resolves all seven, and `brief_vocabulary` in the contract writes down
which of the five pre-existing kinds carries each name.

A record without a `source` is an assertion. A record without a `revision` is a
claim about a repository that may have moved. Both are refused, for the same
reason the Evidence plane refuses an artifact with no Run: a fact whose origin
is not recorded is not more durable than memory, it is just written down.

### The `claim` kind, and why there would be exactly one new one

The ledger records *events* — a decision was made, a thing was observed, a
question was opened. It does not record a durable **assertion with a subject, a
predicate and a value**, and that is the shape semantic memory actually needs:

```text
proposed      claim  subject: item:LIT-0101   predicate: owner   value: <ref>
                     valid_from: …   superseded_by: <claim id>
```

The case for adding it is real: "who owns this" and "which constraint applies"
are the questions a new session asks first, and today the answers are scattered
across decision bodies rather than being queryable fields.

**It does not exist.** `kinds` is a closed list of eight, enforced in
`memory/record.py:209` and asserted against the contract by
`tests/test_memory_ledger.py:100`. There is no `scope` field, no
`predicate`/`value` pair, and no validity window. `subject` is a *location*, not
a thing — `record.py:81` accepts `device:<id>` or exactly three slash-separated
path parts, and `user:` is not in the grammar at all.

If it is added, it should be added **once**. Each new kind is a new closed
vocabulary, a new required-field set, a new retention rule and a new migration
for every reader, and the eight that exist arrived one live failure at a time.
A batch of kinds proposed from a design document — `preference`, `fact`,
`constraint`, `insight`, `profile` — would each be a field nobody has yet
needed to write, which is the `declared-but-absent` pattern with a schema
attached. The test is the one this document already uses: **which real failure
fails without it?**

## Writing to the ledger

There is a pipeline described for this and **there is no pipeline**. What
exists is two gates:

```text
record.validate()   one record, against the contract's required fields
record.audit()      the whole ledger — duplicate ids, and `related` cycles
```

Nothing between them. Specifically, there is no candidate stage, no scope or
sensitivity check, no deduplication against near-identical records, no quality
scoring, and no policy step — and `record.py:383-398` says so plainly: the
ledger **cannot detect a conflict**. Two records can disagree and both are
valid; resolving them is a reader's problem, because the write path has no
opinion.

`memory/handoff.py:37-43` is worth reading as the design principle that
produced this state. It returns records. It does not append them:

> *a handoff that wrote to the ledger would be a second writer of Layer B, and
> the module's whole value is that it can be run without changing anything.*

So the rule for who may write what is currently **implicit and unenforced**,
and the intended rule is this:

```text
Human, deciding explicitly        may write durable, at high confidence
checkpoint · handoff · incident   durable automatically — they are events, and
                                  an event that has to be approved is not a log
Agent inference                   a candidate at best. Never durable on its own.
secret, credential, token         never, at any layer, in any kind
```

The first three lines are the ones needing a mechanism; the last is already
enforced by the contract's field list and is the one that must never relax.

### Three things computed and then never read

Worth recording together, because they are the same defect in one module and
the fix is the same shape:

```text
projection.build()   computes `heads` and `open_questions` and writes them
                     into memory.json. No program reads either field.
confidence           validated (`high|medium|low`) and never weighed.
                     heads() / current_value() sort by `at` alone.
record-contract.yaml says `by` exists so that a human's fact and an Agent's
                     carry different weight. Nothing reads `by` for weight.
```

Each is a correct piece of work with no consumer. None of them is a bug in the
sense of producing a wrong answer — which is exactly why they survive: an
unread field cannot fail. The cost is that a reader finds them, believes they
are load-bearing, and builds on a computation nobody performs.

### Local projection

Layer A keeps a *projection* of the ledger — what this node needs without a
network round trip. It is a projection in the strict sense used everywhere else
in this product: derived, regenerable, and never hand-edited. If the projection
and the ledger disagree, the projection is rebuilt.

```text
ledger      records/projects/<project>/memory/       Git; the authority
projection  state/serveros/projections/memory/       derived; carries projection.yaml
build       serverosctl memory project
staleness   serverosctl memory status   → CURRENT · STALE · MISSING
```

The projection lives under `state/serveros/projections/` because that is the
only projections namespace and `fleet/state-root-contract.yaml` says so — it
carries the manifest, the five invariants and the reader. This document's
contract originally declared `state/serveros/memory/`, a second one; see
`mem:the-projection-location-had-two-answers`.

**It carries pointers, not content.** Every record reaches the projection as
`id · kind · at · by · source · revision`, and never as `body`. A node offline
with its projection can say *that* a decision exists and what it was about; it
cannot say what the decision was. Carrying the text would put a second copy of
the ledger in the one namespace declared to have no authority, which is the
worst available place for it.

The revision a projection names is the ledger's **content**, not its `HEAD`: a
commit sha when the tree is clean, and `sha+content:<hash>` when it is not. A
ledger with one uncommitted record has moved, and a projection stamped with
`HEAD` would call itself current while the thing it summarises had changed —
see `mem:a-revision-must-name-content-not-head`.

## Retrieval, the ContextBundle, and the firewall

Reading memory is where the fabric is weakest, and the weakness has two
separate halves.

### What a reader gets today

```text
current_value(records, subject)   the newest record about a subject, or None
heads(records, …)                 one record per subject
pending(records, device_ref)      handoffs addressed here, unanswered
usable_hits(hits, …)              the Layer C filter — see the OpenViking note
```

Four functions, four shapes, no common envelope. A caller assembles a context
by calling three of them and deciding what to do with the results. That is not
a memory system answering "what should this session know"; it is a ledger
answering four narrow questions very precisely.

### What a `ContextBundle` would be

```text
proposed
  purpose        why this is being retrieved — a session start and a review
                 are different questions with different right answers
  records        each with its own provenance envelope: id · kind · at · by ·
                 source · revision · layer
  budget         a bound on how much may be returned, so that a bundle is a
                 decision rather than whatever happened to match
  unknowns       what was searched for and not found
```

**It does not exist** — `ContextBundle` has zero hits in the tree. The
`unknowns` field is the one that matters most and the one most likely to be
dropped: a bundle that returns matches and no misses cannot distinguish "nothing
is recorded about this" from "the search was wrong", which is invariant 11
applied to retrieval.

### The firewall

This was the important one, and it was not a missing convenience. It is built
now — §18, `memory/scope.py` — and what it replaced is worth keeping, because
the shape of the gap is the argument for the shape of the fix.

```text
then    current_value() and heads() accepted any subject string.
        They would answer a question about ANY Item in the ledger.
        `firewall` and `bound_item` had zero hits in the tree.

now     both take a REQUIRED keyword-only `reader` with no default, so the
        omission is a TypeError at the call site rather than an admitted read.
```

There was no boundary stopping an execution thread working on one Item from
recalling another Item's memory. The only thing in the way was prose —
`context/disclosure-contract.yaml:184` says *"Memory is queried, never
preloaded"* and the contract declares `forbidden_at_l0` — and **prose is not a
gate**. Nothing failed when it was violated, which is the definition of the
defect this whole document keeps finding.

The failure case is not hypothetical and it is not small. A production incident
on one workstream produced findings that reached an unrelated workstream's
context, and once that happens there is no record of it, because reading memory
leaves no trace: the ledger records what was **written**, never what was
**read**. An unlogged read across a boundary is invisible to `audit()` by
construction.

The rule is a single sentence:

> **Cross-Item recall is refused by default. A fact that should be visible to
> more than one Item must be promoted to a project-level object first — the
> promotion is the consent, and it is recorded.**

Enforcing it needed the thing that was missing everywhere else in this fabric:
a **bound Item on the read path**, carried by the caller and checked by the
reader, so that "which Item is asking" is a parameter rather than an
assumption. That is what `memory/handoff.py` already did correctly for its own
narrow case — `pending(records, device_ref)` takes the addressee and answers
only for it. The inbox was the one place in this fabric where the caller's
identity is an argument, and it became the precedent rather than a special
case.

#### What the rule turned out to need

Three scopes, and the second sentence of the rule is why there is no fourth
object: *"the promotion is the consent, and it is **recorded**"*. So the
consent is a field on the record.

```text
scope: item      the default. Recallable from a thread bound to this
                 subject's own Item, and nowhere else.
scope: project   promoted. Recallable from any Item of the same project.
scope: user      promoted further. Recallable from any Item on the node.
```

A record is promoted by saying so, beside `by` and `at` and `revision` like
every other authored claim — which makes a promotion a reviewed line in a diff
rather than a side effect of a `promote()` call that wrote a second copy of the
fact somewhere else. **There is no permission store for `user`**, deliberately:
the permission *is* the promotion, with a name in `by`, and a second store
would be a second authority with no producer and would put the answer to "may
this be recalled" in a file no reviewer of the fact will ever open.

The three refusals are three words because the repairs are three actions:

```text
CROSS_ITEM_RECALL    the fact belongs to another Item and was never promoted.
                     Repair: bind a thread there, or promote the fact.
UNBOUND_READER       the reader names no Item — a coordinator (§17), or a
                     session from before §17. Repair: bind the thread.
UNPROMOTED_SUBJECT   the subject is not on the Item axis at all (a `device:`
                     fact) and was never promoted. Repair: promote it, or
                     address it to a device with a handoff — which is a
                     stronger claim than a scope, because it names its
                     recipient.
```

A refusal is **raised**, never returned as an empty answer. An empty list is a
legal answer — nothing is recorded about this subject — and a refusal arriving
in that shape tells a caller the ledger is empty about an Item it is not
allowed to see. The two call for opposite responses, so they cannot share a
representation. This is the one way in which this firewall is stricter than the
prose that specified it, and the prose was satisfiable by `if False: pass`.

Reads are still not journaled — the ledger records what was written, and
nothing here writes a read into it. What a read does now is **return a
reading**: subject, reader, admitted, refused, reason. That is the trace §18
has, and the honest statement of its limit is that it reaches the reader and
not an auditor: `audit()` still cannot see a read that happened yesterday.

### Query, never preload

One rule outranks the mechanism design, and it is stated in the contract:

> *Memory is queried, never preloaded.*

A session that begins by loading everything the work remembers has no boundary
to enforce, because the read already happened. The firewall is only meaningful
if retrieval is a call with a caller, a purpose and a bound — which is the same
reason the `ContextBundle` and the firewall are one entry on the roadmap and
not two.

## Handing work to another device

Because Layer B is Git, a cross-device handoff needs no new transport. It needs
one question answered, and nothing answered it before `memory/handoff.py`:

> given a ledger and a device, which handoffs are addressed to **that** device
> and have not been answered?

```text
Beijing   checkpoint + handoff      checkpoint() · handoff()
FJ/Dell   reads the handoff         pending(records, device_ref)
Windows   writes the result         result(..., handoff_id=...)
Beijing   retrieves it              answers(records, handoff_id)
```

The sender is not waiting. That is the whole design, and it is why this works
when the two devices are never awake together and why §59's fallback for an
unavailable remote actuator is this rather than a queue.

Two things make it correct rather than merely plausible:

**A handoff names its addressee.** `to_device_ref` is required on `handoff`
and refused on every other kind. A handoff nobody is addressed by is a record
that says the work moved and not where it went: the receiving device cannot
select it, so nothing arrives, and a record nobody read is byte-identical to
one everybody read. This is the same requirement a tunnel has, for the same
reason — a channel that does not name both its ends cannot be checked.

**An answer is a link, not a timestamp.** The result is an ordinary
`checkpoint`; what makes it an answer is `related`, naming the handoff.
Without that link a handoff stays open forever, and with it two devices that
independently pick up the same work both show up as answers rather than one
silently replacing the other.

The inbox is deliberately not `current_value(subject)`. The current value of a
subject is the *newest* record about it — which, once the receiving device has
replied, is the reply. A device that built its inbox that way would be handed
back its own work and would never see the request. `pending()` and
`round_trip()` both take an `at` bound for the same reason `heads()` does: the
receiving device reads its inbox *before* the answer exists, and reading the
ledger as of a moment is the only way to observe that without deleting from it.

## Cross-project / cross-device topology

The target deployment has **one shared OpenViking service on Tokyo**, supervised as an
outer Provider like Laya but with a different job. Laya answers bounded decision questions;
OpenViking stores/retrieves long-term context. Neither lives inside `serverosd`.

```text
Tokyo Claude/Codex ─┐
Windows Claude/Codex├─ authenticated client / official memory plugin
other approved Agent┘
        ↓
OpenViking @ Tokyo
        ├─ user-global memories / stable preferences
        ├─ project-scoped resources and experience
        └─ session-derived episodic/experience memory
```

A device does not get a separate memory universe. Its identity is provenance and an
applicability filter. Query assembly prefers the current Project/Item scope, can add
user-global preferences/experience, and only crosses into another Project when the current
task makes that relevant and data policy permits it.

The Provider may preserve derived memories extracted from Session history; therefore it is
more than a transient vector cache operationally. It is still **non-authoritative** for
current business meaning. Durable current truth remains in Sources/Claims/Views/Requirement
and work/control owners. Provider backup/recovery protects its own persistent workspace;
GitHub is not used to version its internal DB/index.

Claude Code/Codex should reuse the upstream OpenViking integrations where compatible:
auto-recall/capture through lifecycle hooks plus explicit MCP tools. ServerOS disables the
OpenViking Skill catalogue for its profiles so a memory provider cannot become a second
Ability registry. Capture filters/bypass policy exclude secrets, protected raw production
material and scopes that should not become cross-project memory.

GitHub/private records are the interoperability and review plane for consumers that cannot
reach OpenViking. A ChatGPT/Work consumer can read current `hwz-records`/project documents
through its GitHub connector; it does not require ServerOS to expose OpenViking publicly.
If a future authenticated ChatGPT/OpenViking connector has a real consumer, G1 can adopt it
as another A2 realization without changing memory authority.

### What of this exists, as of 2026-09-27

The paragraph above is the target, and the gap between it and the deployment is now small
enough that leaving it unstated would make the target unreadable as a status. On this node:

| Target element | State |
|---|---|
| one shared OpenViking service, supervised as an outer Provider | **deployed** — `openviking.service`, its own unit, cgroup and restart policy; `serverosd` neither starts nor stops it and does not import it |
| authenticated access, not public | **deployed** — `127.0.0.1:1933`; the data plane answers `UNAUTHENTICATED` without a key and 200 with one |
| persistent workspace, protected by backup/recovery, not versioned in Git | **deployed** — `runtime-assets/openviking/data`, with `memory/openviking/backup.py` and an exercised restore; no DB or index in Git |
| non-authoritative for current business meaning | **holds by construction** — no consumer reads the provider yet, so nothing can rank a hit above a current record. This is the one line here that a future consumer must re-earn rather than inherit |
| ServerOS disables the OpenViking Skill catalogue | **deployed** — the provider contributes no ability; `ability-management` is untouched |
| auto-recall/capture through lifecycle hooks | **not configured, deliberately** — capture presumes a VLM, and configuring one would both begin capturing material whose scope policy is not yet written and spend the user's Codex quota. The provider is retrieval-capable and capture-silent |
| a Windows or other device reaching this service | **not deployed** — one node's provider, reachable from that node |

The conditions this deployment was accepted against are in `memory/provider-contract.yaml`
(v2) and each one carries the evidence it was measured with.

## Layer C, and OpenViking

Layer C's selected provider direction is OpenViking, as a retrieval and
embedding provider. It is adopted as a **provider behind a contract**, not as a
component of the runtime, and that distinction is the whole of the deployment
decision.

```text
provider contract   what the fabric asks: index this record · search these terms
adapter             the OpenViking-specific implementation
fixture             a deterministic in-process double, for tests
status              READY_TO_PROMOTE, or NEEDS_HUMAN with the blocker named
```

The rule that governs it:

> **OpenViking may not become a dependency of the node runtime or the reason unrelated
> work stops. It may, however, run as its own explicitly adopted shared Provider service.**

Concretely: dependencies stay in the OpenViking service environment rather than the
ServerOS runtime artifact; no OpenViking call enters `serverosd`'s tick/startup path.
Deployment still requires an explicit Binding, authentication/data policy, persistence,
resource measurement and rollback. The existing `memory/provider-contract.yaml` still
describes OpenViking as a pre-deployment candidate and must be migrated together with the
real adapter/Binding when the provider is actually adopted; this design text does not
claim that deployment has happened.

### Why it must not enter the daemon loop

`serverosd` is a small control loop with a closed responsibility list. Search
and embedding are neither mechanism nor local state:

```text
serverosd      detects that a record changed
provider       indexes it
```

An embedding call inside the tick loop makes the loop's latency a function of a
model and its availability, and makes a search index a startup dependency of a
node that must come up without one. The daemon may *ask* a provider; it may not
*be* one.

## What is not memory

```text
not memory   a live process's state                → Node Runtime Plane
not memory   an observation with no owner          → an observation, discardable
not memory   another system's body                 → that system's authority
not memory   a model's context window              → already gone, by definition
```

The last line is the one worth stating: a context window is not a store, and a
design that treats summarization as persistence will lose exactly the facts that
were expensive to obtain.

## Related

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — four planes, and invariant 9
- [`ability-fabric.md`](ability-fabric.md) — memory providers are providers
- [`runtime-resources.md`](runtime-resources.md) — the daemon's closed loop
- [`fabric-v1-roadmap.md`](fabric-v1-roadmap.md) — the firewall, the pipeline,
  the `claim` kind, and what has to be true first


## 2026-09-24 current-first clarification

Memory Fabric is a retrieval fabric, not the authority for current business meaning. Current B2 Claims/Views and current source objects are consulted before historical Session/MetaCC/OpenViking results. Similarity/relevance scores never become truth scores.

B2 correction/retraction first changes local recall eligibility; provider invalidate/reindex is asynchronous. A provider outage therefore cannot make an older corrected claim current again.

The current structured-understanding view families are `people/projects/topics/responsibilities`. Their prose is a current projection, while full source history lives in raw/source snapshots and semantic receipts. Existing long cards migrate on touch rather than through a one-shot LLM rewrite.

See [UNDERSTANDING-MEMORY-RECONCILIATION](UNDERSTANDING-MEMORY-RECONCILIATION.md) for the system integration and ability-management for the canonical semantic contract.
