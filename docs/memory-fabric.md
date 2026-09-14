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
serverosctl memory     audit · project · status · subject
```

Until these existed, none of the rules below was a rule a program checked. A
contract with no reader is not a harmless placeholder — it is read by the next
author, who believes it. That pathology has its own record in the ledger:
`mem:a-declaration-with-no-reader-never-fails`.

**This list is the enforcement, and it is not the whole document.** Three rules
stated here are *not* in it, and the sections that state them say so where they
are stated rather than only here: the retrieval firewall, the write pipeline,
and the `claim` kind. Reading a rule in this document does not mean a program
checks it; reading it in the list above does.

## Three layers, one authority

```text
Layer A   Local runtime state     state/serveros/                this node, right now
Layer B   Durable work memory     Git: records/ + artifacts/     the authority
Layer C   Derived active memory   a retrieval index              rebuildable, never authority
```

**Layer B is the only authority.** The other two are indexed readings of it.

```text
Layer A  is machine-local, high-frequency, and disposable.
         Losing it costs a re-probe, never information.
Layer B  is versioned, shared, and the thing a new device syncs from.
         Losing it loses work.
Layer C  is a cache. It may be deleted at any time and rebuilt from B.
         If deleting it loses information, it was not a cache.
```

Layer C being "never authority" is not a limitation to work around. It is the
property that lets it be a search index, an embedding store, or a graph — any
of which would be a bad authority, because none of them can be diffed, reviewed
or reverted. The moment a retrieval result outranks the record it retrieved,
the record stops being the truth and starts being a suggestion.

### The rule that keeps the layering honest

> **If a fact exists only in Layer C, it is lost. Design as though the index is
> already empty.**

That is the test for every new memory feature. A summary that cannot be
regenerated from B is a Layer-B artifact that was filed in the wrong place.

## The five semantics

The three layers answer *where*. They do not answer *what kind of thing is
this*, and a memory system that cannot tell the two apart will file a procedure
next to an anecdote and then weigh them equally.

```text
Working       the live state of the thing being worked on right now
              → the Item's own record. Not a store; if it must survive, write it down.

Episodic      what happened, in order: this Run, this incident, this handoff
              → Layer B ledger records with a timeline

Semantic      what is true: a decision, a constraint, an owner, a boundary
              → Layer B, and the only kind whose VALUE outweighs its TIMELINE

Procedural    how to do it — a repeatable method that improved a result
              → NOT the Memory Fabric. See below.

Retrieval     an index over the above, for finding it again
              → Layer C. Never authority, always rebuildable
```

### Why procedural memory is not memory

Procedural memory is the one entry in that list that lives in a **different
plane**, and the reason is the authority question rather than tidiness:

```text
Memory Fabric      what the work remembers        a record, reviewed by people
Ability Fabric     what this machine can do       a procedure, executed by machines
```

A procedure is a thing that **runs**. It has a version, a compatibility story,
a provider, a place in a composition, and a device on which it either resolves
or does not. Every one of those is Ability Fabric machinery, and the Memory
Fabric has none of them. Filing a procedure as a memory record would give it a
ledger row and no executor — and, worse, would let a Skill be changed by
appending to a ledger instead of by a revision in `ability-management`.

So the flow out of memory and into an ability is explicit, and each arrow
crosses a boundary that has a gate:

```text
a repeated observation            Layer B record, kind `observation`
  ↓ promoted by a person
a procedure candidate             still Layer B — a claim about method
  ↓ distilled, written as a Skill, tested, reviewed
an ability revision               ability-management, pinned, versioned
  ↓ composed
a device resolves it              the Ability Fabric answer
```

Nothing in that chain happens automatically, and the middle step is a person's,
not a summary's. A memory system that promoted its own habits into abilities
would be editing the catalogue from the audit trail.

### Nothing here is built yet

The five semantics above are a **vocabulary**, not four new record kinds. Only
Episodic and Semantic have a home today, and they share it — the eight closed
record kinds in `memory/record-contract.yaml` are not partitioned by semantics,
and no field says which semantic a record belongs to. That is a gap, not a
design: the roadmap records it. What the vocabulary buys immediately is that
"procedural memory" can be *declined* by name instead of quietly accumulating
in the ledger.

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

### The firewall, and why it is the real gap

This is the important one, and it is not a missing convenience.

```text
today   current_value() and heads() accept any subject string.
        They will answer a question about ANY Item in the ledger.
        `firewall` and `bound_item` have zero hits in the tree.
```

There is no boundary stopping an execution thread working on one Item from
recalling another Item's memory. The only thing in the way is prose —
`context/disclosure-contract.yaml:184` says *"Memory is queried, never
preloaded"* and the contract declares `forbidden_at_l0` — and **prose is not a
gate**. Nothing fails when it is violated, which is the definition of the defect
this whole document keeps finding.

The failure case is not hypothetical and it is not small. A production incident
on one workstream produced findings that reached an unrelated workstream's
context, and once that happens there is no record of it, because reading memory
leaves no trace: the ledger records what was **written**, never what was
**read**. An unlogged read across a boundary is invisible to `audit()` by
construction.

The intended rule is a single sentence:

> **Cross-Item recall is refused by default. A fact that should be visible to
> more than one Item must be promoted to a project-level object first — the
> promotion is the consent, and it is recorded.**

Enforcing it needs the thing that is missing everywhere else in this fabric: a
**bound Item on the read path**, carried by the caller and checked by the
reader, so that "which Item is asking" is a parameter rather than an assumption.
That is what `memory/handoff.py` already does correctly for its own narrow case
— `pending(records, device_ref)` takes the addressee and answers only for it.
The inbox is the one place in this fabric where the caller's identity is an
argument, and it is the precedent to generalize rather than a special case.

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

## Layer C, and OpenViking

Layer C has a candidate implementation: OpenViking, as a retrieval and
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

> **OpenViking may not become the reason this round does not finish, and it may
> not become a dependency of the node runtime.**

Concretely: if adopting it pulls in a large dependency set, requires a service
this machine does not already run, or would put a network call on the path of
`serverosd`, then the correct outcome is `NEEDS_HUMAN` with the finding —
contract, adapter and fixture still delivered, the deployment deferred. A
deferred provider that has a contract is worth more than a deployed one that has
none, because the contract is the part that took the thought.

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
