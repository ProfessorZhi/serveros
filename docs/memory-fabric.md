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

Every rule above is a rule a program checks, and until these existed none of
them was. A contract with no reader is not a harmless placeholder — it is read
by the next author, who believes it. That pathology has its own record in the
ledger: `mem:a-declaration-with-no-reader-never-fails`.

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
