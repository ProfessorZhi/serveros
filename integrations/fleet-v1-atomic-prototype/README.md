# A′ atomic authority epoch + lease commit prototype

**PROTOTYPE_ONLY / NOT_V1_SIGNED / V1_NOT_RELEASE.**

Round 2 hardness notes are below; the production default is still blocked.

| Flag | Value |
|---|---|
| `PROD_DEFAULT_BLOCKED` | production #364 still returns `BLOCKED_OWNER_COMMIT_SEAM` |
| `NO_OWNER_TRUST` | the only owner is a synthetic fixture; no owner key exists |
| `NO_DAEMON_WIRING` | nothing here is imported by `serverosd`, Fleet or Work Engine |
| `NO_MIGRATION` | `proto_*` tables are created in a temp DB only |
| `NOT_V1_SIGNED` | this is not a production authorization |
| `NO_TRUSTED_CLOCK` | the clock is a constructor-injected synthetic fixture; production must use the store clock |
| `NO_TRUSTED_PEER` | `synthetic_peer_uid` is a harness fixture; production must use kernel `SO_PEERCRED` |
| `SO_PEERCRED_NOT_BOUND` | no kernel peer credential is read by this prototype |
| `PRODUCTION_WIRING_REQUIRED` | legacy `ServerOSStore.acquire_lease`/`check_writer`, JSON `work revoke`, finalize/effect and owner key are unfenced |
| `REAL_FINALIZE_SAME_TXN` | **NOT_PROVEN** |
| `POWER_LOSS` | **NOT_PROVEN**; `os._exit` is a process kill, not fsync/power-loss evidence |
| `REAL_WINDOWS` | **NOT_RUN** |
| `TWO_REAL_UIDS` | **NOT_RUN** |

## What this proves

The question is whether an owner-policy revocation and a lease acquire can be
ordered by one SQLite commit domain instead of a verify-read followed by a
later write. The prototype puts both in the **same `BEGIN IMMEDIATE`
transaction** on one temp SQLite DB:

1. resolve the trusted context and grant;
2. read the synthetic owner-policy/authority-epoch fence row;
3. check policy/grant revocation, windows, epoch, uid, actor and exact scope;
4. read the existing lease and mint the next `id_sequences` lease token;
5. write the lease row **and** the commit-audit row (`proto_commit`);
6. commit once.

A synthetic owner revoke also uses `BEGIN IMMEDIATE` on the same DB and
advances `policy_epoch` and `authority_epoch` together. SQLite serializes the
two writers, so a revoke that commits before an acquire makes the acquire see
the revoked row / new epoch and refuse; an acquire that commits first is
auditable and a later revoke does not rewrite its token or epoch.

## What is real vs simulated

**Real (imported read-only from Work Engine):**
`workengine.store.ServerOSStore` materializes the real schema (`leases`,
`id_sequences`, `projects`, `items`), the real WAL/busy-timeout connection
settings, and the real `_lease_epoch`/`_lease_ttl`/`_lease_expiry`/
`_lease_stamp` timestamp and TTL rules. `AtomicFencePrototype._next_token`
implements the same `id_sequences(scope,kind='lease')` allocation the store
uses, inside the same transaction.

**Not used:** `ServerOSStore.acquire_lease` opens its own `BEGIN IMMEDIATE`;
calling it could not include the fence row. It is not modified.

**Simulated:** the owner policy, grant, context, uid, clock and the non-empty
"owner attestation" string are fixtures. The attestation is not a signature,
not a key and not a cryptographic proof. `clock` and `synthetic_peer_uid` are
constructor-bound harness inputs; `acquire` accepts no per-call `now` or
`peer_uid`, so a request field cannot choose them. They are still NOT the
production store clock or kernel `SO_PEERCRED` (`NO_TRUSTED_CLOCK`,
`NO_TRUSTED_PEER`, `SO_PEERCRED_NOT_BOUND`).

## Round-2 hardening (closed in this prototype scope)

* **Commit-phase window re-check.** After the lease/commit rows are written
  and after the test `hold`, the prototype reads the injected trusted clock
  **again inside the same `BEGIN IMMEDIATE`** and re-checks TTL, policy window,
  grant window and revocation. `LEASE_EXPIRED_AT_COMMIT`,
  `POLICY_EXPIRED_AT_COMMIT`, `GRANT_EXPIRED_AT_COMMIT` or `POLICY_DENIED`
  rolls the lease row, the `id_sequences` token and `proto_commit` back
  together. Tests use a `FixedClock` jump, never a long sleep.
* **No per-call clock/peer.** `AtomicFencePrototype(clock=...,
  synthetic_peer_uid=...)` binds the synthetic harness facts at construction;
  `acquire(request, hold=...)` has no `now`/`peer_uid` keyword. A caller
  cannot pass a past instant or a chosen uid on the call.
* **Historical replay.** A repeated request id returns
  `prototype_status: PROTOTYPE_HISTORICAL_REPLAY`, `authorized: false`,
  `historical: true`, `effect_eligible: false` even if the policy is still
  live. It is an audit record, not current authority and not effect-eligible.
  The idempotency digest includes the expected authority/grant epoch pins.
* **Fenced writer check.** `check_writer_fenced` re-reads the committed
  `context_ref`/`context_actor` snapshots, the CURRENT context row, the commit
  policy/grant epochs and the current policy/grant in one `BEGIN IMMEDIATE`.
  It checks commit/grant/policy lineage and refuses a pre-revoke token after a
  revoke, an expired policy/grant window, a `grant_epoch` bump (QA2 witness:
  commit at grant epoch 1, grant bumped to 2 → `BLOCKED_STALE_EPOCH`), or a
  same-epoch context rebind/deletion/actor remap (`BLOCKED_STALE_CONTEXT`).
  Priority is: holder/expiry → committed context snapshot → current context
  binding → policy/grant rows → policy revoke → policy/authority/grant epoch →
  windows. A rebind therefore wins over a simultaneous revoke/epoch bump.
  It is still a point-in-time check, not an effect transaction.
* **Barrier evidence.** The acquire-vs-revoke test starts an independent
  SQLite connection with `busy_timeout=0` and proves its `BEGIN IMMEDIATE`
  raises `database is locked` while the acquirer holds the write lock; no
  sleep is used.
* **Commit-after-kill.** A child commits, flushes its queue and `os._exit`s;
  the parent reopens the DB, verifies the lease/commit/token, WAL mode and
  `integrity_check=ok`. This is a process-kill durability test, not a
  power-loss/fsync proof.

## Round-3 context-lineage witness

QA2's witness: acquire at `grant_epoch=1` with `context_ref=ctx:proto` bound to
`grant:proto`; insert `grant:proto-2` with the same policy and same grant
epoch; rebind `proto_context` to `grant:proto-2`; the old token still returned
`PROTOTYPE_FENCED_CHECK authorized:true` because the check fetched the grant
by the commit's own `grant_ref` (a tautology).

The commit row now persists `context_ref` and `context_actor`; the fenced
check reads the current context row in the same transaction and refuses a
missing context, a grant rebind, or an actor remap as
`BLOCKED_STALE_CONTEXT`. The commit/grant/policy lineage and grant epoch are
also checked. `ROUND3_RED` was reproduced before this change; the new tests
are GREEN.

**Production dependency / NOT_PROVEN:** a real context rebind or grant rewrite
needs an atomic owner reissue that bumps the grant epoch (or a durable context
generation) in the same authority transaction. The prototype has no such
owner interface; a same-epoch in-place grant/policy content mutation beyond
actor/ref/epoch is not fully detectable and remains an owner-protocol
requirement.

## Why a pure experimental module is not enough

This module proves the transaction shape, not a production integration. It
cannot, by itself:

* make `OwnerVerifiedLease.acquire` or any other production caller use the
  fence — that requires an owner-approved change at the Work Engine mutating
  entrypoint (`workengine/authority.py` / `store.py`);
* stop a legacy `ServerOSStore.acquire_lease` caller from writing outside the
  fence — production must route every lease mutation through one fenced
  entrypoint;
* bind a real owner key or policy issuer — `NO_OWNER_TRUST`.

Anyone can still call `ServerOSStore.acquire_lease` directly on the same DB;
that is exactly why this is a prototype and why the production default stays
blocked. Two negative tests make this explicit
(`test_legacy_serveros_store_acquire_bypasses_the_fence`,
`test_legacy_check_writer_accepts_a_pre_revoke_token_after_revoke`): the real
store writes a lease after a prototype revoke, and the real `check_writer`
still returns authorized for a pre-revoke token. Both are
`PRODUCTION_WIRING_REQUIRED`, not prototype failures. Also unresolved:
`serverosctl work revoke` writes the JSON ledger rather than this SQLite
fence; `finalize_run_verified` opens its own transaction; no owner signature
or `SO_PEERCRED` binding exists. Production must route every lease mutation,
revoke and effect through one fenced transaction domain before any positive
authorization can be claimed.

## Residual P2 / not claimed

* `proto_commit` is a **lease-acquisition audit**, not an effect/finalize
  commit. There is no FK or trigger tying `leases` to it; a manual or legacy
  insert can create a lease with no commit row (the negative test does exactly
  that). Production migration must add the constraint and one fenced
  entrypoint.
* `check_scope.py` includes committed and working-tree paths, but it is a diff
  receipt, not a runtime control.
* The 50-test result is self-reported in this Draft; no hosted CI check is
  attached.

## Authoritative-source premise

The real source of truth would be a **real owner** writing policy/epoch and
revocation rows into this same SQLite fence, through an owner-authenticated
issuance interface. No such owner/key/interface exists in this repository or
on this node. The prototype only simulates that fact; it cannot obtain
production authorization and must not be wired into `serverosd` or Work
Engine as-is.

## Test matrix (`tests/test_s1_atomic_prototype.py`)

| Area | Evidence |
|---|---|
| A fenced commit | one transaction writes policy epoch + lease + commit row; success word `PROTOTYPE_FENCED_COMMIT` / `SIMULATED_OWNER`, no `verified` |
| A revoke ordering | revoke-first → no lease; acquire-first → token/epoch auditable, later new acquire refused |
| RED baseline | a legacy cross-domain read → revoke → separate write reproduces a lease after revoke (test asserts the gap, not a fix) |
| B fail closed | revoked/expired/future policy, revoked/expired grant, stale policy/authority/grant epoch, cross uid/actor/scope, missing attestation/fence row, non-bool revocation, invalid TTL, caller self-asserted authority fields |
| C multi-process | two independent SQLite connections/processes race for one lease: exactly one success, one `LEASE_HELD_BY_OTHER`, no double lease; acquire transaction precedes a concurrent revoke (commit epoch 1 vs revoke epoch 2) |
| D crash | `os._exit(17)` after writes but before COMMIT leaves no lease/commit/token; a child that commits, flushes and `os._exit(23)` reopens with lease/commit/token intact, WAL and `integrity_check=ok`; power loss **NOT_PROVEN** |
| E replay | same request id + same facts → `PROTOTYPE_HISTORICAL_REPLAY`, `authorized: false`, `historical: true`, same token, no new commit/token; different facts or epoch pins → `IDEMPOTENCY_CONFLICT`; stale epoch → `STALE_*_EPOCH` |
| F finalize/effect | **NOT_PROVEN**; `finalize_run_verified` has its own transaction; `PRODUCTION_WIRING_REQUIRED` |
| Hardening | commit-phase TTL/policy/grant re-check with `FixedClock` jumps; independent-connection `BEGIN IMMEDIATE` blocked proof; legacy acquire/check_writer bypass negative tests; fenced writer check including post-acquire policy/grant expiry, grant-epoch bump, and same-epoch context rebind/deletion/actor remap (54 tests) |

## Run

```text
systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 \
  -p CPUQuota=25% -p TasksMax=64 -- \
  nice -n 10 python -m pytest tests/test_s1_atomic_prototype.py -q \
  -p no:cacheprovider
```

Multi-process tests use Linux `fork`; Windows and a second real OS uid are
**NOT_RUN**. No production daemon/socket, owner key, OS grant, credential,
OAuth, provider send or in-service DB is touched.
