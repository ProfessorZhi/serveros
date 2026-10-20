# S1 · Owner policy/epoch → lease commit atomicity contract

**Status: `BLOCKED_OWNER_ATOMIC_AUTHORITY`.**

#364 (`7fe1ccc`) closed the observable race by refusing owner-backed mutation
eligibility: a live owner policy yields `verified: false` /
`BLOCKED_OWNER_COMMIT_SEAM` and no grant. That is correct fail-closed behavior,
but it is not a positive authorization path. This document records what a real
positive would require, what the current node actually provides, and the exact
dependency that is missing.

No production resolver is bound, no owner-issued policy/epoch store exists, and
no production code emits `COMMIT_FENCED`. The probe in
`tests/probes/s1_owner_atomic_commit_probe.py` is the runnable receipt.

## 1. Trust sources: uid → actor → scope

| Fact | Source | Durability / commit | Revocation and TTL |
|---|---|---|---|
| Kernel uid | `SO_PEERCRED` on the AF_UNIX connection; never a request field | kernel; per connection | connection close |
| Actor | context record built by the provider-verified transport (`provider_status=VERIFIED`, `provenance=FACT`, freshness check) | `AgentControlStore.contexts` (JSON) | context age; #318 strong-verdict gate |
| Delegated scope | provider-signed delegation, verified by `verify_delegation`; `projects`/`items`/`actions` copied into the grant | `AgentControlStore.grants`; store-minted `epoch`, `os_principal`, `owner_policy_*` | `revoked`/`revoked_at`; `valid_from`/`expires_at` |
| Owner policy | external **owner-issued** fact read through `OwnerPolicyResolver.resolve` (`runtime/owner_policy.py:58`). Production binds no resolver. | **none.** It is a read; there is no policy document, epoch store, issuance log or revocation commit | policy `revoked`, policy epoch, `valid_from`/`expires_at`, `issued_at` |
| Lease | `ServerOSStore.leases` (SQLite, `workengine/store.py`) | `BEGIN IMMEDIATE` transaction; token + `expires_at`; `lease_guard` holds the write transaction across an effect | token fencing; TTL re-read at the store clock |

The owner-policy fact is the only row with no durable, commit-able source. That
is the root of the blocking dependency, not a missing check.

## 2. What “atomic” must mean

Let **L** be the lease commit (the insert/update of the lease row, or the
protected effect under `lease_guard`) and **R** the commit of an owner-policy
revocation.

The required linearization is:

* if **R commits before L**, L must refuse (`POLICY_DENIED` /
  `BLOCKED_OWNER_COMMIT_SEAM`) and write no lease;
* if **L commits before R**, the lease is a pre-revocation acquisition; R must
  not retroactively rewrite that fact, and the next renew/acquire must see R;
* R and L must be ordered by a single commit domain. Two reads with a gap
  between them are not an ordering, however many times they are repeated.

A read-only resolver plus a later SQLite write cannot provide this ordering.
`COMMIT_FENCED` as a JSON string is a **claim**, not a transaction: any client
or parent can put the string in an answer, and a string cannot serialize a
revoke against a lease insert.

## 3. The required interface contract

The following is the minimum contract for any future positive. It is a design
contract; it is not implemented here because the authority and the mutating
store are not bindable in this environment.

```python
class OwnerCommitFence(Protocol):
    def snapshot(self, *, policy_ref, grant_ref, context, peer, now
                 ) -> OwnerCommitSnapshot: ...
    def commit_lease(self, *, snapshot, resource, owner, ttl_s, write
                     ) -> LeaseCommitReceipt: ...
    def revoke_policy(self, *, policy_ref, expected_epoch, now) -> int: ...
```

`OwnerCommitSnapshot` must bind at least:

* `authority_epoch` — monotonic, advanced by every policy commit/revoke;
* `policy_ref`, `policy_epoch`, `revoked`;
* `grant_ref`, grant `epoch`, `os_principal`, `actor_ref`;
* `projects`, `items`, `actions`, the policy window and `committed_at`.

Invariants:

1. **Identity is measured, not passed.** uid comes from the kernel peer; actor
   from the trusted context. Neither is a caller field.
2. **The snapshot is immutable and fully bound.** A snapshot for one
   policy/grant/uid/actor/scope cannot authorize another.
3. **`commit_lease` and `revoke_policy` are serialized in one commit domain.**
   Either the lease row and the authority epoch are updated in the same
   serializable transaction, or a durable two-phase decision record is written
   before the lease row and recovery is idempotent.
4. **The epoch is re-read inside the lease commit.** A snapshot taken before
   the transaction is not enough; `commit_lease` must compare the committed
   epoch/revocation/window under the same transaction that writes the lease.
5. **No caller-supplied time, owner, policy or token.** The authority store's
   clock is the only clock at the commit boundary.
6. **Revocation is monotonic and first-wins.** A second revoke does not move
   the epoch back or rewrite “when authority ended”.
7. **Crash recovery is defined.** A committed decision with no lease row is
   recoverable/idempotent; an uncommitted decision expires and refuses. An
   orphan must never authorize a later write under a stale epoch.
8. **Timeout/unknown is refusal.** No fallback to the last read, no default
   owner, no permissive policy.
9. **`COMMIT_FENCED` is emitted only by the fence, with a commit receipt.**
   A synthetic HMAC, a legacy #272 readback, or a bare marker is not a fence.
10. **No resolver / no fence is a refusal.** `BLOCKED_OWNER_POLICY` for an
    unbound owner source; `BLOCKED_OWNER_COMMIT_SEAM` for an owner-backed read
    with no commit fence. The default remains closed.

## 4. Current topology and the gap

| Boundary | Implementation | Commit domain |
|---|---|---|
| Context / grants / owner binding | `AgentControlStore` JSON document; `_mutation()` takes `_exclusive(lock_path)` (`runtime/agent_control_store.py:185`, `:922`) | sidecar `flock` + `os.replace` of the JSON document |
| Grant revocation | `AgentControlStore.revoke_grant` (`:2273`); `serverosctl work revoke` calls it under the same lock | same JSON/flock domain |
| Owner policy | `OwnerPolicyResolver.resolve` (`runtime/owner_policy.py:58`); a read. Production `serverosd` constructs `Daemon` without a resolver (`runtime/daemon.py:5272`) | **none** |
| Verify route | `runtime/daemon.py:3036`; after #364 a live owner policy returns `BLOCKED_OWNER_COMMIT_SEAM` with a point-in-time audit and no grant | daemon read, no lease write |
| Lease store | `ServerOSStore.acquire_lease` (`workengine/store.py:492`), `lease_guard` (`:629`) | SQLite `BEGIN IMMEDIATE` |
| Mutating consumer | `OwnerVerifiedLease.acquire` (`workengine/authority.py:278`) calls `_verified` (`:177`) and then `store.acquire_lease` in a **separate** transaction | no authority fence |

There are two real locks on this node: the daemon JSON sidecar `flock` and
SQLite's write lock. They are not the same lock, and no code nests them across
the verify → lease-write boundary. A shared `flock` over the daemon store would
serialize a committed `work revoke` against a lease write only if the Work
Engine write path held it; today `OwnerVerifiedLease.acquire` does not. It also
would not fence an owner-policy change written directly to an external policy
file, because that writer does not take the daemon lock. It is therefore a
possible building block for grant revocation, not an owner-policy/epoch fence.

`COMMIT_FENCED` has no producer: `workengine/serveros_authority.py:190` accepts
the marker, but no daemon route or adapter emits it.

## 5. Race matrix

| Event | #364 behavior | Required with a real fence |
|---|---|---|
| no resolver | `BLOCKED_OWNER_POLICY`, no grant, no lease | same |
| owner policy revoked/expired before verify | `POLICY_DENIED`, no lease | same |
| policy/grant changes during resolve | `BLOCKED_ATOMICITY`, no lease | refuse; no lease |
| owner live, no commit fence | `BLOCKED_OWNER_COMMIT_SEAM`, no grant, no lease | only a fence snapshot may proceed |
| revoke commits after snapshot, before lease commit | refused because no positive path exists | `commit_lease` sees the new epoch and refuses |
| revoke commits after lease commit | no positive path exists | lease is pre-revocation; next renew/acquire refuses |
| grant revoked / expired / wrong epoch | `POLICY_DENIED` / `INVALID_CONTEXT`, no lease | same |
| actor/uid cross-principal | `UNTRUSTED_ACTOR`, no grant/lease | same |
| lease TTL overrun during an effect | `lease_guard` rolls back | same |
| crash between decision and lease row | not reachable (no positive) | idempotent recovery; no authority from an orphan |
| two real UIDs | `BLOCKED_MULTIPRINCIPAL_NOT_RUN` | same test requirement, not faked |

## 6. Exact blocking dependencies

**D1 — Owner-issued policy authority.** A durable, authenticated
owner-issued policy/epoch document with issuance and revocation commits. The
current `OwnerPolicyResolver` is only a read
(`runtime/owner_policy.py:58`); production binds none (`runtime/daemon.py:5272`).
This cannot be replaced by a synthetic HMAC, a local test fixture or an old
#272 receipt.

**D2 — An atomic lease commit seam.** The authority epoch check must live in
the same serializable transaction as the lease row, or in a two-phase commit
with a durable decision record and idempotent recovery. The current
`OwnerVerifiedLease.acquire` (`workengine/authority.py:278`) writes after
`_verified` returns, in a separate SQLite transaction; this file is outside
S1's allowed edit set and needs an owner-approved Work Engine change.

**D3 — A producer contract for `COMMIT_FENCED`.** The marker must come with a
fence commit receipt bound to the policy/grant/uid/actor/scope and the
authority epoch. `workengine/serveros_authority.py:190` checks the marker; no
producer exists.

**D4 — Two real UIDs.** The test process has one uid and no passwordless
sudo/setuid; a second real OS principal cannot be attested. This remains
`BLOCKED_MULTIPRINCIPAL_NOT_RUN`, never faked.

**D5 — Owner approval to bind a production resolver.** Even with D1–D3,
`serverosd` must not bind a resolver or a fence by default; that is an owner
wiring decision, not a code default.

## 7. Runnable probe

`tests/probes/s1_owner_atomic_commit_probe.py` builds a temp daemon, a real
temp AF_UNIX socket (kernel `SO_PEERCRED`), an owner-policy fixture, the real
`ServerOSLeaseAuthority` and `OwnerVerifiedLease`, and a temp
`ServerOSStore`.

```text
python tests/probes/s1_owner_atomic_commit_probe.py --mode controls
  rc=0  if every fail-closed control holds and no lease is written
python tests/probes/s1_owner_atomic_commit_probe.py --mode positive
  rc=2  BLOCKED_OWNER_ATOMIC_AUTHORITY, with the missing dependencies
python tests/probes/s1_owner_atomic_commit_probe.py --mode all
```

Evidence level: `REAL_TEMP` socket/peer and production daemon/store/adapter
code; owner policy/context/grant are test fixtures; no production owner, no
real provider, no second uid. No `HOSTED` or `REAL_DEVICE` claim.

## 8. Not claimed

This document does not authorize a positive lease, does not invent an owner,
does not emit `COMMIT_FENCED`, and does not change the production default. No
merge, deployment, provider send, OAuth, credential or live grant mutation;
no `QA_SIGNED` / `V1_RELEASE`.
