# Proposal — a resident Core that recovers, without inventing its facts

- Item: `SRV-0011` · parent run `RUN-0046`
- Base: `69a18bc79e194bbfda63182bf13ce9c5d9dbd5b0`
- Author: Dell / `fjd` (uid 1000), native Linux lane
- Status: **implementation candidate, NOT deployed.** No production unit, no
  service restart, no grant widened by this document.

## 1. What is refused today, and why the refusal is correct

`serveros-native-core --serve --enable-actions` exits 2:

> *"--serve and --enable-actions together are refused in this stage: the resident
> Core observes. A resident process that acts is a change of authority and needs
> its own review, not a flag"*

That refusal exists because the one-shot acting path is honest for a different
reason than a loop would be. In one pass, `--link-state` and `--agent-state` are
the caller speaking about **that pass**. The gate refuses when either is
`unknown`, so a caller who has not thought about it is told. A resident process
that reads the same flags at startup and then acts for hours is not stating a
fact — it is hard-coding `LINK_UP` and `AGENT_IDLE` for as long as it runs.

`contract/bounded-recovery.v1.md` §6 names exactly this gap and deliberately
leaves it open:

> *"No link or agent provider: the facts are supplied by the caller today …
> Reading them from the host is a separate change, and inventing a default for
> any of them would reintroduce the permissive-by-omission failure this document
> exists to remove."*

So this proposal is not "delete the refusal". It is the separate change §6 named,
made small enough to review.

## 2. What is already there (and is NOT rebuilt)

Everything below is reused unchanged:

| piece | what it already guarantees |
|---|---|
| `recovery.cpp` | persisted budget (v2: boot id + `{monotonic, wall_epoch}` anchor), 3/hour, 60/300/900 s ladder, circuit breaker |
| `lease.cpp` | one writer, `flock`, released by the kernel when the holder exits |
| `action_gate.cpp` | the acting flags are complete and the budget is persistable |
| `admission.cpp` | installed / masked / `NEEDS_LOGIN` / per-service link / agent-busy, with a documented precedence |
| `action_journal.cpp` | prewrite before the effect, outcome as a separate durable write |
| `state_directory.cpp` | the journal is anchored to an inode, not a path string |
| `serve.cpp` | monotonic ticks, no catch-up, no self-restart, clean SIGTERM |
| `probe.cpp` | a verb that exited 0 is not a service that answers — readiness is re-probed after the effect |

This proposal adds **one** module and **one** flag.

## 3. The one new fact source

`--facts FILE`, re-read **on every tick**, parsed by `facts.cpp`.

The document (`serveros/native-core-facts.v1`) carries the facts the planner
cannot see, each of which already turns a correct plan into a wrong action when
it is unknown or stale:

```json
{
  "schema": "serveros/native-core-facts.v1",
  "observed_at": "2026-10-22T00:03:00+08:00",
  "observed_epoch": 1792598580,
  "link":  {"known": true, "up": true},
  "agent": {"known": true, "busy": false, "holder": "cc-session"},
  "services": [
    {"id": "toy", "runtime": "serving"},
    {"id": "other", "runtime": "needs-login"},
    {"id": "third", "runtime": "unknown", "maintenance": true,
     "maintenance_reason": "window"}
  ]
}
```

Why both `observed_at` and `observed_epoch`: the epoch is the **load-bearing**
fact, because it is the only one this Core can compare against its own clock, and
comparing is exactly what decides staleness. The string is what a human reads in
the receipt. They are not redundant, and the epoch is not derived from the string
by a hand-rolled date parser.

### The failure that matters

A stopped writer is the interesting case, not a malformed file. Every failure —
absent, unreadable, wrong schema, unknown field, malformed, stale, from the
future, contradictory, duplicate service — produces the **same** degradation:
`link.known == false` and `agent.known == false`, with the reason named in a
closed code (`FACTS_ABSENT`, `FACTS_STALE`, …). The existing admission gate then
refuses on unknown, as it always did.

There is deliberately no partial trust: a document is believed whole or not at
all, because a fresh link fact beside a stale agent fact is a fact set nobody
should act on.

## 4. What the resident loop does differently from the one-shot path

1. **Facts are re-read per tick**, before the plan is built.
2. **A per-service maintenance flag is a refusal**, not a hint: a person putting
   a service into maintenance is the same statement as `MASKED`, made by the
   owner rather than by systemd.
3. **Startup is gated on the same document.** `--serve --enable-actions` without
   `--facts` is refused, and with an unusable document at startup the whole run
   is refused rather than started-and-idle. `--facts` without
   `--serve --enable-actions` is also refused, so it can never be silently
   ignored.
4. **`--link-state` / `--agent-state` are no longer consulted in this mode.**
   Asking for the same fact twice, once at startup, would reintroduce the
   one-shot claim the flag exists to remove.

## 5. What it still refuses to do

- No new recovery scheduler, no second database, no new daemon.
- No verb outside the manifest's `allowed_verbs` for that exact unit.
- No widening of any grant; the manifest stays the owner's statement.
- No assumption that `active` means healthy: the post-effect readiness probe
  still decides, and `UNMEASURED` is still neither success nor failure.
- No default for link, agent, or maintenance. Unknown refuses.

## 6. Residual gaps this proposal does not close

- **Who writes the facts document** is out of scope here. This lane reads it; the
  writer (a host facts collector, or `serverosd`) is a separate deliverable. Until
  something writes it, the resident acting path has nothing to act on — which is
  the correct failure.
- **`serverosd` coordination**: both the C++ Core and the Python daemon can act
  on units. The lease serialises two Cores; it does not partition ownership
  between the two programs. A verifiable single-owner split is still required
  before this is enabled in production, and is not claimed here.
