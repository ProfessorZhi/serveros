# A′ atomic authority epoch + lease commit prototype

**PROTOTYPE_ONLY / V1_NOT_RELEASE.**

| Flag | Value |
|---|---|
| `PROD_DEFAULT_BLOCKED` | production #364 still returns `BLOCKED_OWNER_COMMIT_SEAM` |
| `NO_OWNER_TRUST` | the only owner is a synthetic fixture; no owner key exists |
| `NO_DAEMON_WIRING` | nothing here is imported by `serverosd`, Fleet or Work Engine |
| `NO_MIGRATION` | `proto_*` tables are created in a temp DB only |
| `NOT_V1_SIGNED` | this is not a production authorization |
| `REAL_FINALIZE_SAME_TXN` | **NOT_PROVEN** |
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

**Simulated:** the owner policy, grant, context, uid and the non-empty
"owner attestation" string are fixtures. The attestation is not a signature,
not a key and not a cryptographic proof. The peer uid and context are supplied
out of band by the harness; request fields cannot choose them.

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
blocked. The next step is a Work Engine entrypoint that performs the
fence-check and lease insert in one transaction, with a real owner issuance
interface. Neither exists here.

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
| D crash | `os._exit` after lease/commit writes but before COMMIT leaves no lease, no commit row and no token advance; committed state survives restart |
| E replay | same request id + same facts → same token, `replayed: true`, no new commit/token; different facts → `IDEMPOTENCY_CONFLICT`; stale writer epoch → `STALE_AUTHORITY_EPOCH` |
| F finalize/effect | **NOT_PROVEN**; `finalize_run_verified` has its own transaction, so lease + finalize are not composed here |

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
