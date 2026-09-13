# Memory-Context Fabric

The fabric answers: **what does this work remember, where is that written down,
and what happens when a device disagrees with the answer?**

It exists because "the Agent knows" is not a storage location. Today a work
Item's memory is spread across four places that were never designed to agree —
Git history, `records/` cards, `artifacts/` evidence, and whatever a given Agent
session happens to still be holding. The fourth one is the problem: it is the
only one that is not durable and it is the one an Agent consults first.

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
required on every record  id · kind · subject · at · by · source · revision
never                     a secret, a credential, a token, a raw transcript body
```

A record without a `source` is an assertion. A record without a `revision` is a
claim about a repository that may have moved. Both are refused, for the same
reason the Evidence plane refuses an artifact with no Run: a fact whose origin
is not recorded is not more durable than memory, it is just written down.

### Local projection

Layer A keeps a *projection* of the ledger — what this node needs without a
network round trip. It is a projection in the strict sense used everywhere else
in this product: derived, regenerable, and never hand-edited. If the projection
and the ledger disagree, the projection is rebuilt.

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
