# Proposal — a real facts writer, and one owner per service

Follow-on to `proposal-native-resident-recovery.md` (PR #390). That change let a
resident Core act on facts it re-reads, and deliberately shipped with **no
writer**: the production path stayed `NOT_WIRED`. This is the writer, and the
answer to the coordination question that outlived it.

## 1. The two gaps

1. **Nothing produces the facts document.** A reader with no writer means the
   resident acting path can never legitimately start.
2. **The lease only serialises two Cores.** It does not stop the C++ Core and the
   Python `serverosd` from both acting on the same unit, because they are two
   different programs and each believes it is the writer. Two writers with two
   budgets is exactly the failure `lease.hpp` names, arriving through a door the
   lease cannot see.

## 2. What is reused, not rebuilt

`probes.host.systemd_user_unit` (the existing `systemctl --user show` reader),
`registry`/`health` for resource state, `agent_sessions` for session refs, and the
Core's own `flock` lease contract. No WorkEngine, no RunDB, no second manager.

## 3. Facts are collected, never assumed

`runtime/native_core_facts.py` writes `serveros/native-core-facts.v1` atomically
(tmp + rename, 0600) on a bounded cadence.

| fact | source | when unavailable |
|---|---|---|
| `services[].runtime` | per-service declared signal (a `needs-login` marker or probe) | `unknown` |
| `services[].maintenance` | explicit per-unit marker file | `false` |
| `link` | configured TCP probe (host, port, timeout) | `known: false` |
| `agent` | injected session-busy resolver | `known: false` |
| `observed_epoch` | the wall clock at collection | — |

A fact with no configured source is **UNKNOWN**, and unknown refuses downstream.
There is no code path that invents `link: up` or `agent: idle`.

**Honest gap:** systemd exposes no reliable "a person stopped this" signal —
`inactive` after `systemctl stop` and `inactive` after a clean exit are the same
observation. This proposal therefore uses an explicit `maintenance` marker for
"do not bring it back", and states that a bare manual stop is **not** reliably
distinguishable. It is recorded rather than guessed.

## 4. One owner per unit, enforced by one lease

- Every managed unit gets an owner in the owner map: `native-core` or
  `serverosd`.
- **Both programs take the same `flock` file** (the Core's existing `--lease`
  path) for as long as they might *act*. If the Core holds it, the Python
  recovery actor refuses; if Python holds it, the Core reports
  "another writer holds the lease".
- **The facts writer deliberately does not take that lease.** Observation is a
  rename of its own file, not an effect, and a writer that held the act lease
  would deadlock the design: the Core could never acquire the pen it needs in
  order to act on the facts being published. The lease guards *action*, and both
  acting programs take it.
- Within a process, the owner map restricts action to units it owns; a unit owned
  by the other program is refused by name rather than skipped.
- The Core's manifest lists only `native-core` units. That is a review rule, and
  the owner map is the machine-checkable half.

## 5. What this does not do

No production unit is installed or started; the templates stay `NOT INSTALLED`.
No tunnel, GPU, fleet control or global configuration is touched. `fleet_mcp/**`
and the root-lane `runtime/` files (`agent_control_store.py`,
`control_dispatch.py`, `fleet_mcp_owner.py`) are not modified — `git diff` against
the base is empty for all of them.
