# One writer — the consumer matrix, and when the rename is done

> **Status: `SPEC_PROPOSED` / `NOT_INSTALLED`.** This document describes who can
> change this host and what gates each program. It is not a claim that any device
> runs the C++ Core; §6 records what is actually installed, read-only, on
> 2026-10-28.
>
> The machine-checkable half is [`../consumers.v1.json`](../consumers.v1.json),
> held to this repository by `serverosd/tests/package_contract.py`
> (`ConsumerMatrixTest`). This file holds the reasoning and the dated evidence;
> the JSON holds only claims a test can re-verify.

## 1. Why a matrix at all

Two programs can ask `systemctl --user` for a verb on this machine: the Python
`serverosd` and the C++ `serveros-native-core`. The rule "one writer per unit" is
written in the shared owner map and enforced in both programs. What was never
written down is **the set**: which programs exist, which of them can mutate, what
each one is gated by, and which documents they share.

Without that, three things are invisible:

- a third program that can mutate and is gated by nothing;
- a gate that reads a document the other program does not write the same way;
- a claim like "they contend on one file by construction" that is true of one
  program and false of the other.

The third is not hypothetical — see §4.

## 2. The matrix

| program | lang | can mutate? | gate | lease |
|---|---|---|---|---|
| `serveros-native-core` | C++ | **yes**, for units the map gives `native-core` | owner map (`manifest_owner_refusals`) → `ActionGate` → `WriterLease` | **required, no default** |
| `serverosd` | Python | **yes**, for units the map gives `serverosd` | owner map (`runtime/daemon.py:1316`) → `SafeLease` | **required, defaulted** |
| `serveros-node-resource-snapshot` | C++ | no | — (one-shot read) | — |
| `core-lite-status` | C++ | no | — (reads `systemctl show`) | — |
| `core-lite-profile-check` | C++ | no | — (validates a document) | — |
| `serverosctl` | Python | no (operator surface) | — | — |

Exactly **two** writers, which is why the owner map's vocabulary is closed to two
owners. Adding a third writer is a change to that vocabulary, not to this table.

Shared documents, and who reads them:

| document | readers |
|---|---|
| `serveros/native-core-owner-map.v1` | C++ `facts.cpp`, Python `native_core_facts.py` |
| `serveros/native-core-facts.v1` | C++ `facts.cpp` (written by Python) |
| `serveros/native-core-capability.v1` | none yet — emitted one-shot by the C++ Core, for inventory and comparison |

## 2a. The gate is now uniform — and it was not, until review

The first version of this document claimed the capability document and the acting
path "cannot disagree". An independent review found that they could, because the
owner map was only consulted on the **resident** path:

- `--owner-map` was *required* only when `serve && act`;
- it was *refused* outside `serve && act` — so the one-shot form
  (`--manifest M --enable-actions …`) could not be given a map at all;
- the check itself ran only under `serve && act`.

So a single-pass Core could restart a unit the shared map assigns to `serverosd`,
while `--capability` on the same manifest printed `NOT_MINE`. Pre-existing
behaviour, but these documents asserted it away.

**Fixed in this PR**: the map is required and consulted whenever `act` is set, in
both modes; the toy harness supplies a map; and `tests/test_native_owner_map_parity.py`
now drives the real binary against a fake `systemctl` to assert the three cases —
no map refuses, `serverosd`-owned refuses with nothing sent to the service
manager, `native-core`-owned proceeds (through the fake). The gate is a property
of acting, not of being resident.

## 3. The one-writer mechanism, stated accurately

Both writers use `flock(LOCK_EX | LOCK_NB)` on one file. The kernel releases it
when the holder exits, which is why neither side has to decide whether a lock is
stale (`lease.hpp` and `SafeLease`'s docstrings say this at length, and both are
right).

**That is the whole mechanism.** It serialises two programs *only if they open the
same path*. Nothing in either program checks that they do.

## 4. The gap this document recorded, and how it closed

`runtime/daemon.py` says, in its own words:

> `$XDG_RUNTIME_DIR/serveros` is where the Core's own status socket lives, so a
> Core started with the default `--lease` and this daemon contend on one file
> **by construction**.

When this document was written, the second half was **false of the C++ binary**:
`--lease FILE` was required, had no default, and was assigned only from `argv`.
The two programs shared a pen only when an operator passed the daemon's path by
hand, and nothing checked that they did.

**Closed 2026-10-28, without editing `runtime/daemon.py`.** The C++ Core now
derives the same path when `--lease` is absent:

- `$XDG_RUNTIME_DIR/serveros/native-core.lease` — the path the daemon derives
  from `XDG_RUNTIME_DIR`. **Not byte-identical in one branch**: when
  `XDG_RUNTIME_DIR` is unset the daemon falls back to its state root, and this
  Core has no equivalent root, so it refuses rather than invent one. The
  invariant survives; the string does not, and saying "byte for byte" without
  that qualifier would be a claim the code does not keep;
- **and it refuses to default at all** when `XDG_RUNTIME_DIR` is unset or not
  absolute. A default invented from nothing is a lease at a path no other program
  knows, which is precisely the split-lock the lease exists to prevent;
- **and it refuses when `$XDG_RUNTIME_DIR/serveros` does not exist.** The Core
  never creates that directory — `WriterLease` opens relative to an existing
  parent, while the Python daemon's `SafeLease` does create it for its status
  socket. Left to fail inside the action gate, a fresh runtime directory produced
  "every unit skipped for NO_LEASE" and a one-shot that still **exited 0**;
  indistinguishable from success at a terminal. It is refused up front now, with
  both remedies named.

An explicit `--lease` still wins. The daemon's sentence is now true as written, so
**#407 has nothing to change** — the handoff on that PR can be closed as "the C++
half was enough".

`tests/test_native_lease_default.py` holds the two sources together: it compares
the paths they name, and drives the binary to prove it (refusal without an
`XDG_RUNTIME_DIR`; the lease landing at the canonical path with one; an explicit
`--lease` leaving the canonical path free).

## 5. Provenance: `serveros/native-core-capability.v1`

`serveros-native-core --manifest M --owner-map O --capability` prints one document
and exits. It takes no lease, touches no service and writes nothing.

It answers the question this matrix otherwise leaves to a person: **which program
said that, about which map?** It carries the program identity, its version, the
mode it is in, the manifest digest, the owner map's own verdict and contents, and
one row per manifest unit: `OWNED` / `NOT_MINE` / `UNDECLARED`.

Two of those documents compared by `doubly_claimed_units()` answer "do these two
programs both believe `x.service` is theirs?" as a value. It carries **no paths,
no argv and no credential** — the same discipline `status.hpp` states, for the
same reason.

It is a report, not an authority: the acting path still refuses through
`manifest_owner_refusals`, and the document is derived from that function so the
two cannot disagree — a claim that only became true once §2a's gate was made
uniform, and one that a test now drives end to end rather than asserting in prose.

## 6. What is actually installed — 2026-10-28, read-only

Collected on Dell/fjd with `systemctl --user show`, `ps` and `ls`. **Nothing was
started, stopped, enabled, disabled or reloaded; no file was written.**

> Reading this required one correction worth recording: in this session `$HOME`
> is remapped, so `~/.config/systemd/user` is the *wrong* directory. The real
> user unit directory is `/home/fjd/.config/systemd/user`. A matrix built from the
> wrong `$HOME` reports "nothing installed" for a device that is running two
> services.

| device | L1 `serverosd` | L2 connector / tunnel | notes |
|---|---|---|---|
| **Dell/fjd** | **`NOT_INSTALLED`** — no `serverosd.service`, no `serveros-native-core`, no `~/.local/bin/serveros*`, no state root, no `runtime-assets` | connector **enabled + running**; tunnel **enabled + running** | see below |
| Tokyo | `NOT_OBSERVED` | `NOT_OBSERVED` | not reachable from this session; do not infer from Dell |
| FJPCOS Windows | `NOT_OBSERVED` | `NOT_OBSERVED` | not reachable from this session |

**The two running services are L2, and neither runs a released artifact:**

- `serveros-fleet-node-connector.service` — `ExecStart` under
  `/home/fjd/projects/fleet-node-badc531/`, a git checkout at
  `d5ffdbbf297cf98d86cf73fa5dd2ae8d59dca8ef` (“deploy: node connector route
  POST /v1/projects/delete”). **That commit does not exist on GitHub** — the API
  answers `No commit found for SHA` (HTTP 422). It is a local, unpushed commit.
- `serveros-fleet-node-tunnel.service` — `ExecStart` under
  `/home/fjd/projects/fleet-cutover/`, which is **not a git checkout at all**.
  Its command line carries the Tokyo host and an SSH identity path; those are
  deliberately not reproduced here.

So on this device the three statements have three different answers, which is
exactly why the campaign separates them:

- **source**: both services run from source checkouts, not from a release
  revision;
- **installed**: no artifact was installed — the units point at working
  directories;
- **running**: two processes, one of them from a commit that exists on no remote.

**L1 is not installed here at all**, so this round cannot claim a C++ takeover of
a live service: there is no live L1 service on this device to take over. The
Python `serverosd` pilot referred to in `runtime/serverosd.service` is not running
on Dell.

## 7. When may the `serverosd` rename be called complete?

A directory rename is not the question. These are:

1. **One binary does bounded recovery.** Today `bin/serverosd` (Python) and
   `bin/serveros-native-core` (C++) both act, and the owner map names both. The
   rename is done when one program performs recovery and the other is gone or is
   a thin caller of it — not when a folder is spelled differently.
2. **The owner map's vocabulary shrinks with it.** Two owners exist because two
   programs act. Retiring one is a change to `OWNERS`, the readers' closed
   vocabulary and the parity corpus, in the same PR.
3. **The native payload is a first-class artifact.** Done for the release path
   (`native-runtime` kind, #419); **not** done for the build face, which still
   produces the node-runtime composition and nothing named `serverosd`.
4. **The Python recovery path is gone or delegated**, with the tests that prove
   it — including the ones that today assert the daemon's own retry budget.
5. **`docs/architecture/layers.yaml` registers the directory once**, without
   `split: true`. That file is owned by PR #414 and is present on `main`, but this
   branch's base predates its merge — so this condition is checked against `main`,
   not from this worktree. It is deliberately not a fact `ConsumerMatrixTest`
   asserts, because a test that reads a file absent from its own base would be red
   for a reason that has nothing to do with this change.

None of the five is true today. That is the honest status of D8/D9.

## 8. Residual Python responsibilities (not moved, not claimed)

| responsibility | file | why it stays |
|---|---|---|
| bounded recovery / retry budget | `runtime/daemon.py` | **#407 owns this file.** Writing it here would be a cross-lane edit; a handoff is requested in the PR instead. |
| owner-map writing | `runtime/native_core_facts.py` | it is the only writer; the readers enforce bounds it does not (see the parity corpus `too-many-units` / `long-unit`) |
| facts publication | `runtime/native_core_facts.py` | the C++ Core only reads; nothing has replaced the publisher |
| lease default | `runtime/daemon.py` | the C++ side has none — §4 |
| Feishu / cards / notifications | `runtime/direct_lark_notify.py`, `runtime/message_transport.py` | product surface, out of L1 |

## 9. Next PR candidates

1. **Lease path, made one thing** (§4): give `--lease` the canonical default and
   refuse a mismatch, or add a shared declaration both programs read. Needs the
   Python side, so it needs #407 co-ordination if `daemon.py` is touched.
2. **Facts parity**: the C++ reader's bounds vs the Python *writer*, which
   currently enforces neither. Python-side change; `native_core_facts.py` is not
   #407-owned.
3. **`--capability` in the inventory**: have the read-only device collector pick
   up the capability document so §6 stops being a hand-written table.
4. **Retire one owner**: only after (1) and (2), and only with the parity corpus
   updated in the same PR.

## 10. Rollback

Everything this round adds is additive and reversible in one commit:

- `--capability` is a new one-shot flag; removing it restores the previous
  behaviour exactly, and no other path calls it.
- The parity corpus is fixtures plus two suites; deleting them removes two test
  entries and no product behaviour.
- `serverosd/consumers.v1.json` is data read only by a test.

Nothing installs, starts, enables or reloads anything, so a device has nothing to
roll back.
