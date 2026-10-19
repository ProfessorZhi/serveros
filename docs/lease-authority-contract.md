# Trusted-authority contract: Work lease and runner receipt

**Status (this milestone):**

| Fact | State |
|---|---|
| Work Engine lease producer | `BLOCKED_TRUST_SOURCE` — no owner-approved production parent is bound; a source adapter for serveros #272 exists (`docs/serveros-lease-authority.md`) |
| Runner-receipt producer / T10 finalization | `BLOCKED_ORIGINAL_OWNER` — serveros owns the real `invoke_once` receipt producer |
| Work Engine consumption | `SEAM_ONLY` — the T10-required finalize route exists and refuses without a trusted parent; no lease CLI/MCP verb is exposed |
| Code | `workengine/authority.py`, `ServerOSStore.finalize_run_verified` |
| Tests | `tests/test_lease_authority.py`, `tests/test_runner_receipt.py` |

This document is the exact interface the original owner must provide. It is a
contract, not an installation: nothing here grants a principal, mints a token,
or claims that a production path is wired.

See `docs/t10-finalize-entry.md` for the T10-required finalize entry route
(`finalize_run(..., t10_required=True, ...)`) and the exact out-of-process
parent caller it needs.

See `docs/serveros-lease-authority.md` for the cross-repo adapter that consumes
serveros #272's `agent.lease.authority.verify` read. It is source-proven against
the actual #272 source in a hermetic temp socket; the production parent remains
`BLOCKED_TRUST_SOURCE` until an owner-approved caller binds it.

## 1. Why this exists

Independent QA #256 Snapshot H found two gaps at the original authority:

1. serveros #261/#262/#264 now check the Work Engine lease A/1 at admission,
   but `ServerOSStore.acquire_lease(resource, owner=...)` takes the owner from
   its caller. Any caller can write any owner into `leases`. There is no
   legitimate OS-principal + grant verified CLI/MCP producer, so production
   `create -> lease -> send` refuses.
2. serveros #263 produces a real bounded-runner receipt and explicitly states
   that Work Engine finalization does not consume it. `finalize_run` accepted a
   caller's `exit_code`/`verdict` and concluded `SUCCEEDED` from those words.

The store primitives remain correct for what they are. The **producer** is the
missing half, and it cannot be fabricated inside Work Engine.

## 2. What this milestone implements

### 2.1 `OwnerVerifiedLease` (`workengine/authority.py`)

* `acquire` / `renew` / `release` / `check_writer` take **no `owner` argument**.
  The owner written into the lease is the principal returned by the trusted
  authority.
* The resource is derived, never supplied: `work-write:<item_ref>`.
* Every verb re-verifies the grant at the operation instant:
  exact `project`, exact `item_ref` membership, exact action name,
  `revoked == False`, `valid_from <= now < expires_at`, a positive integer
  `epoch`, and an `os_principal` equal to `uid:<os.getuid()>`.
* With no authority bound, every verb refuses `BLOCKED_TRUST_SOURCE`.
* No CLI verb and no MCP tool is registered. `test_lease_authority.py` asserts
  the absence, so an unguarded producer cannot be added by accident.
* Store refusals are preserved as `AuthorityError.reason_code`
  (`LEASE_HELD_BY_OTHER`, `STALE_TOKEN`, `LEASE_EXPIRED`, `NOT_HOLDER`).

### 2.2 `ServerOSStore.finalize_run_verified`

* Requires an opaque `receipt_ref` and a `RunnerReceiptAuthority`. With none
  bound it refuses `BLOCKED_TRUST_SOURCE`; with no ref it refuses
  `RUNNER_RECEIPT_REQUIRED`.
* Re-checks that the verified receipt is about **this** Item/Run, that
  `input_digest` and `receipt_sha256` are 64-hex, that `commit_sha` is a full
  40-hex SHA, that `process_exit_code` equals the caller's `exit_code`, and that
  the receipt's verdict equals the caller's verdict.
* A `PASS` with a nonzero `process_exit_code` is refused
  (`RECEIPT_INCONSISTENT`), never promoted.
* The receipt binding (`action_key`, `input_digest`, `receipt_sha256`,
  `process_exit_code`, `runner_identity`, `commit_sha`) is written into the
  existing `finalize.json` evidence file. The evidence row's `sha256` anchors
  the whole file. No new table, no second ledger, no schema change.
* `SUCCEEDED` requires `verdict == PASS`, `exit_code == 0`, **and** the
  verified receipt's `process_exit_code == 0`. Everything else is `FAILED`.
* Child stdout/stderr, a caller-supplied JSON hash, a bare `exit_code`, and an
  unverified file path are not accepted as authority.

## 3. Exact required original authority interface

### 3.1 Lease authority

```python
class LeaseAuthority(Protocol):
    def verify(self, context_ref: str, *, action: str, project: str,
               item_ref: str, now: float) -> LeaseGrant: ...
```

`LeaseGrant` fields: `principal`, `grant_ref`, `project`, `items` (exact Item
names, no wildcards), `actions` (exact action names), `valid_from`,
`expires_at`, `revoked`, `epoch` (positive int), `os_principal` (`uid:<n>`).

The parent **must**:

1. Resolve `context_ref` out of band — never from a caller argument or a
   caller-supplied payload. The trusted sources on this host are
   `serverosd agent.context.resolve` (a `context_ref` minted by ServerOS) and
   `SO_PEERCRED` (the kernel's answer, not the caller's).
2. Read the live grant record, including `revoked`, `revoked_at`,
   `valid_from`, `expires_at`, `projects`, `items`, `actions`, and the current
   `epoch`. A missing or unreadable grant is a refusal.
3. Bind `os_principal` to the kernel-attested identity of the calling process.
   A bearer `context_ref` alone is not OS-principal verification.
4. Return a `LeaseGrant`; raising is a refusal. The parent must never fall back
   to a self-reported owner.

The Work Engine adapter checks the returned grant against the local process
(`uid:<os.getuid()>`) and against the exact Item/project/action. It does not
and cannot mint the grant, an epoch, or a role.

### 3.2 Runner-receipt authority

```python
class RunnerReceiptAuthority(Protocol):
    def verify(self, receipt_ref: str, *, item_ref: str, run_ref: str,
               now: float) -> VerifiedRunReceipt: ...
```

`VerifiedRunReceipt` fields: `item_ref`, `run_ref`, `action_key`,
`input_digest` (64-hex), `receipt_sha256` (64-hex), `process_exit_code`,
`verdict`, `runner_identity`, `commit_sha` (40-hex).

The parent **must**:

1. Resolve `receipt_ref` to the real `runtime/bounded_invocation.invoke_once`
   record for this action and this exact Item/Run.
2. Recompute/verify the `receipt_sha256` anchor over the receipt and its
   stdin/stdout/stderr, and the `input_digest` over the action input.
3. Return the verified fields. It must not infer a verdict from child text; it
   hashes text, it does not believe it.
4. Refuse when the receipt is absent, replayed with different input, or
   contradicts the requested verdict/exit code.

The store then performs the same `item_ref`/`run_ref`/exit-code/verdict checks
again, so a parent bug cannot silently conclude a different Run.

## 4. Refusal matrix (must stay true before wiring)

| Case | Reason code |
|---|---|
| no authority bound | `BLOCKED_TRUST_SOURCE` |
| empty `context_ref` | `UNTRUSTED_ACTOR` |
| authority raises / returns nothing | `UNTRUSTED_ACTOR` / `UNTRUSTED_RECEIPT` |
| wrong project | `PROJECT_MISMATCH` |
| Item not named by the grant | `ITEM_MISMATCH` |
| action not granted | `ACTION_NOT_GRANTED` |
| revoked grant | `GRANT_REVOKED` |
| expired / not-yet-valid grant | `GRANT_EXPIRED` / `GRANT_NOT_YET_VALID` |
| stale observed epoch | `EPOCH_STALE` |
| OS principal mismatch | `OS_PRINCIPAL_MISMATCH` |
| second live holder | `LEASE_HELD_BY_OTHER` |
| old token after promotion | `STALE_TOKEN` |
| expired lease at commit | `LEASE_EXPIRED` |
| duplicate acquire | `LEASE_HELD_BY_OTHER` (fence unchanged) |
| missing receipt ref / JSON hash | `RUNNER_RECEIPT_REQUIRED` |
| child text or unverified path | `UNTRUSTED_RECEIPT` |
| receipt for another Item/Run | `RECEIPT_ITEM_MISMATCH` / `RECEIPT_RUN_MISMATCH` |
| receipt disagrees on exit/verdict | `EXIT_CODE_DISAGREEMENT` / `RECEIPT_VERDICT_DISAGREEMENT` |
| PASS with nonzero exit | `RECEIPT_INCONSISTENT` |

## 5. What this milestone does not claim

* It does not make the Work Engine CLI or MCP an authenticated producer. The
  adapter has no external entrypoint; that is deliberate.
* It does not close #237 B3 or T10 in production. The lease producer is
  `BLOCKED_TRUST_SOURCE`; the T10 producer is `BLOCKED_ORIGINAL_OWNER`.
* It does not change `finalize_run`. Existing callers keep the old record
  semantics, which are **not** T10-authoritative. Migrating those callers to
  `finalize_run_verified` is the owner patch, not this seam.
* It does not create a second lease store, a grant store, a token, or a
  privilege. It consumes the existing `leases` table and `id_sequences` fence.

## 6. Owner patch required to unblock

1. **Lease producer.** Bind a `LeaseAuthority` inside a trusted out-of-process
   parent (serverosd or an equivalent OS-principal-aware daemon). Expose the
   acquire/renew/release action only through that parent. Never accept a
   verifier, `owner`, or `context_ref` from argv/environment in a way that lets
   the caller choose its own authority.
2. **T10 producer.** Bind a `RunnerReceiptAuthority` to the real
   `invoke_once` receipt and call `finalize_run_verified` from the finalize
   path once the receipt is available. Keep the existing
   `work_engine_consumption=NOT_WIRED` status until this is real.
3. **Acceptance.** Re-run the two test files plus the existing
   `tests/test_lease.py` and `tests/test_v3_finalize.py`; then add the
   cross-repo E2E that proves `create -> lease -> send` with a real principal
   and a real grant, and a T10 child that self-reports PASS while exiting
   nonzero.
