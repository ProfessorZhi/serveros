# Bounded recovery — admission, the lease, and what was already there

`serveros/native-core-admission.v1` — the closed refusal codes and the
single-writer lease that the resident Core applies between a plan and the verbs
it would run.

## 1. What this adds, and what it deliberately does not

The planner (`plan.cpp`) already decides *what the manifest wishes*, and it does
that well: owner and allowlist, `manual`/`disabled`, an unknown is not a stopped,
stale evidence is not evidence, boot versus tick, dependency order, and the
recovery budget asked last. The recovery module (`recovery.cpp`) already owns the
budget, the backoff ladder, the breaker, and — the property that matters most —
**persistence across a Core restart**.

This document covers the two things that were missing, and neither is a second
planner:

| added | why the planner could not do it |
|---|---|
| `admission.cpp` | the planner is pure over the manifest, the observations and the instant. It cannot see whether the unit is **installed**, whether the host has a **link**, or whether an agent is **using** the service — and each of those turns a correct plan into a wrong action |
| `lease.cpp` | two writers each believe they own the budget, and **both receipts look correct**. This is the one failure that makes every other safeguard moot, and it cannot be a planner rule because it is not a fact about a service |

## 2. The refusal codes, in the order they bind

Checked in this order, and the order is asserted rather than incidental: when
several facts are wrong at once, which refusal comes back decides what a reader
is told to fix.

| # | code | the fact | the failure it prevents |
|---|---|---|---|
| 1 | `NO_LEASE` | another writer holds the pen | two Cores, two budgets, two correct-looking receipts |
| 2 | `MASKED` | the unit is a mask | undoing, with a program, a decision a person made |
| 3 | `INSTALL_STATE_UNKNOWN` | the install state was not read | guessing in either direction: spending an attempt on a unit that is not there, or refusing one that is running |
| 4 | `NOT_INSTALLED` | `LoadState` says there is no unit | **the case this exists for** — systemd answers `ActiveState=inactive` for a name it cannot find, so a boot trigger with `start_on_boot` would start nothing and blame the host |
| 5 | `NEEDS_LOGIN` | the **service**'s own runtime says it needs a person | a restart that cannot work, spending the budget and hiding an authorisation only a person can renew |
| 6 | `LINK_DOWN` | **when the service is `link_dependent`** — no working link, or not established | a restart spent on a network fault, which outlives the fault |
| 7 | `AGENT_STATE_UNKNOWN` | nobody established whether an agent is using it | the same as 6, in the other direction |
| 8 | `AGENT_BUSY` | an agent is mid-task | interrupting work that was going to finish. A busy thing is not a broken thing |

`MASKED` is checked **before** the install tri-state on purpose:
`unit_is_installed` answers `false` for a mask, so checking the tri-state first
would report every mask as `NOT_INSTALLED` and lose the one fact that changes the
repair — a mask was switched off by a person, and the fix is to ask them, not to
deploy anything.

`LINK_DOWN` names its own checks rather than assuming either way. A caller that
cannot establish the link is refused, because assuming "up" is exactly how a
restart gets spent on a network fault.

Two facts were added to this gate by
[`service-readiness.v1.md`](./service-readiness.v1.md), and each narrows it:

- **`NEEDS_LOGIN`** — read from the *service*'s own runtime, not the unit's. A
  tunnel can be `active` and report `NeedsLogin`; a restart brings it back
  needing login again, so the repair is a person. It is the one fact whose
  absence is **not** a refusal, because the plan's own readiness probe already
  decides whether the service is serving.
- **The link gate is per-service.** `LINK_DOWN` applies only to a service whose
  recovery depends on the link. A crashed LOCAL tunnel is recovered by starting
  it, which is a local act, and refusing it would leave exactly the broken thing
  this Core exists to fix unrestarted, waiting on the network that is the one
  thing down. A manifest opts a specific service out with
  `recovery.offline_recoverable: true` — per service, defaulting **off**.

## 3. Readiness: a process is not a service

`readiness_of(Observed)` returns `READY` only for `Active::Active`, which is
systemd's own judgement rather than this component's. Everything else —
`activating`, `deactivating`, `failed`, `inactive`, and an `ActiveState` this
version does not know — is `NOT_READY`, and an unreadable observation is
`UNMEASURED` rather than assumed either way. A supervisor that has just run a
verb is the one most likely to assume it worked.

`readiness_of` is now the rule for the **default** probe, `systemd-is-active`,
and no longer the whole story: a manifest may declare a service-level probe
(`unix-socket-connect`, `tcp-connect`) that asks the SERVICE rather than the
unit, and `readiness_from_probe` then decides. See
[`service-readiness.v1.md`](./service-readiness.v1.md) §2–§3. A unit reading that
says the unit *cannot* be running still outranks a probe that connects, because
a stale listener can outlive its service.

## 4. The lease

`flock(2)`, non-blocking, taken on a path the caller names and released when the
file description closes — which is what happens when the process exits, dies or
is killed.

**A pid file was rejected on purpose.** Every rule for "the holder is gone" is a
guess: the pid may be reused, the file may be left from another boot, the process
may be stopped rather than dead. `flock` needs no such rule because the kernel
releases the lock on exit, so this module never decides that a lock is stale.

It also never unlinks the lock file, for the same reason `status_socket.hpp`
refuses to unlink a socket: removing the path does not release the lock, so a
live writer would be left holding a lock nobody can find while the next writer
takes a meaningless one on a new inode.

`peek_holder` reports who last wrote the file. It is **diagnostics only** — the
lock is the authority, and a caller that treats a readable holder as a free lease
has inverted the whole design.

## 5. Acting requires all three guarantees

`--enable-actions` is refused unless `--lease`, `--link-state` and
`--agent-state` are all given. Each has a safe default of *unknown*, and *unknown*
refuses. A caller that did not think about these is exactly the caller that
should be told.

The gate runs **before the plan is copied into the receipt**. A receipt that
lists a verb the Core did not run is worse than no receipt: it is a false record
of an effect.

## 6. What is not here

- No second registry, no second daemon, no new schema for services.
- No self-healing beyond the verbs the manifest already declares
  (`allowed_verbs`), and no verb the allowlist does not permit.
- No link or agent provider: the facts are supplied by the caller today
  (`--link-state`, `--agent-state`, and now `--service-runtime`). Reading them
  from the host is a separate change, and inventing a default for any of them
  would reintroduce the permissive-by-omission failure this document exists to
  remove.
