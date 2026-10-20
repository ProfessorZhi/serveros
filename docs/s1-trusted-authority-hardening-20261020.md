# S1 trusted-authority hardening · 2026-10-20

**Status:** source + local tests. Not a production acceptance receipt.

| Fact | State |
|---|---|
| Work Engine lease producer | `BLOCKED_TRUST_SOURCE` (unchanged; see `docs/lease-authority-contract.md`) |
| T10 runner-receipt producer | `BLOCKED_ORIGINAL_OWNER` (unchanged) |
| This change | lease TTL/clock refusals, atomic commit fence, finalize evidence anchor |
| Evidence level | `CODE / LOCAL / HERMETIC` only |
| REAL_TEMP / REAL_DEVICE / CLIENT_E2E | `NOT_RUN` |

## 1. Findings fixed in this slice

### S1-H1 · Illegal TTLs were accepted or raised raw exceptions

`acquire_lease(ttl_s=...)` and `renew_lease(ttl_s=...)` did not validate the
TTL. `ttl_s=0` or a negative value was written as an already-expired row and the
call returned normally; `NaN`/`Infinity` escaped as raw `ValueError`/
`OverflowError` from `math.ceil`. There was no upper bound either, so one
acquire could write an expiry far enough ahead to deny the scope indefinitely.

Now a TTL must be a positive, finite, bounded number (`LEASE_TTL_INVALID`), and
the computed expiry is checked to be after `now` at the pinned second precision.
A refused acquire writes nothing.

### S1-H2 · A non-finite clock could take over a LIVE lease

`expires_at > now` is false for `now = NaN`, so `acquire_lease(now=NaN)` took
over a live lease. `+Infinity` and `-Infinity` are equally unusable as a fence.
All non-finite clocks are now `LEASE_CLOCK_INVALID` before the transaction. A
malformed `expires_at` row is `LEASE_ROW_INVALID` rather than a raw
`ValueError`; it is never treated as "cannot be aged, therefore expired".

The store also accepts an injected `clock=` at construction. Every lease verb
uses it when no explicit `now` is supplied, so the adapter and the store age a
grant and a lease on the same clock.

### S1-H3 · `check_writer` was a point check, not a commit boundary

`check_writer` read the lease in one transaction and returned; the caller's
effect landed in another. Between them the lease could expire and a successor
could acquire, and the old writer's effect still committed. That is the
TOCTOU #237 B1/T3 names.

`ServerOSStore.lease_guard()` now opens `BEGIN IMMEDIATE`, validates the holder
and expiry inside it, yields the connection the effect must use, and re-reads
the fence at the trusted store clock immediately before the commit. A takeover
cannot interleave; an effect that overran its TTL rolls back.
`OwnerVerifiedLease.commit_guard()` adds the adapter half: it re-verifies the
grant before the effect and again after it, still inside the same transaction.
`check_writer` remains for point checks and is documented as non-atomic.

### S1-H4 · The finalize file and its evidence row could disagree

`_write_finalize` committed the evidence row first and renamed
`finalize.json.tmp` into place afterwards. A crash in that window left a
committed row whose locator did not exist. On a later finalize, a missing file
was reconstructed from the row as an `UNSIGNED` success — inventing a record and
dropping the receipt the row's anchor covered. The reader also took the latest
evidence row regardless of kind, and treated any `runner_receipt` object as
`T10_VERIFIED`.

Now:

* the file is written, fsynced and promoted **before** the database commit; a
  failed promotion rolls the row back and leaves the Run `FINALIZING`;
* a missing file is `EVIDENCE_MISSING`, never synthesized;
* a crash-left `.tmp` is promoted only when its bytes hash to the row's anchor;
* a tampered file is `EVIDENCE_HASH_MISMATCH`;
* the reader selects the `kind='finalize'` row, requires the file hash to equal
  the row's `sha256`, and requires `authorization` and `runner_receipt` to agree
  (`EVIDENCE_TORN` otherwise). A pre-label record with no receipt is read as
  `UNSIGNED`, not upgraded.

## 2. Dependency DAG (measured, not inferred)

```text
main @ 771c81c
├── #241  feat/v1-session-write-fence-20261016 @ 429a717   (writer gate, B1)
└── #272  feat/v1-s1-s2-grant-epoch-authority @ fc93590    (grant epoch + verify read, B3)
        └── #298 adapter in the Work Engine chain pins fc93590

#295 @ 99e08f0  (lease + fencing, base main 771c81c)
  └── #296 @ 1284522  (authority + runner-receipt seam)
        └── #297 @ 8d42916  (T10-required finalize entry)
              └── #298 @ e1f783f  (serverosd #272 verified-read adapter)
                    └── this branch (hardening)
```

`git merge-tree --write-tree` results:

| Pair | Result |
|---|---|
| `main@771c81c` + `#241` | clean |
| `main@771c81c` + `#272` | clean |
| `#241` + `#272` | clean |
| `#241` + `#295`/`#298` chain | **one conflict: `context/resolver.py`** |
| `#272` + `#295` chain | **one conflict: `context/resolver.py`** |

The conflict is a duplicate fix for the same T6 reader: #241 and #295 both
rewrite `held_leases`. #295's SQL filter (`expires_at > strftime(...)`) compares
against a floor-truncated now and is exact for the pinned second-precision
format; #241's Python `ceil(now)` can expire a live lease up to one second
early. The merge resolution should keep one implementation (prefer #295's
filter, or a hybrid that retains #241's injectable `now` and floors it), not
both.

Minimal landing order: `#241` and `#272` first (they commute textually), then
`#295 → #296 → #297 → #298` as a stack, with the resolver conflict resolved
once. #298 must not land before #272; its test materializes `fc93590` and skips
with a precise reason when the object is absent, so a CI run without #272 is
not a pass.

## 3. Verification

From this branch (temp SQLite, injected clock, hermetic):

```text
python -m pytest \
  tests/test_lease_hardening.py tests/test_finalize_consistency.py \
  tests/test_lease.py tests/test_lease_authority.py \
  tests/test_runner_receipt.py tests/test_finalize_entry.py \
  tests/test_serveros_lease_authority.py tests/test_lease_store.py \
  tests/test_workengine_v3_finalize.py tests/test_resolver.py -q
```

Result: `236 passed, 29 subtests passed` (the cross-repo #272 adapter class ran;
it did not skip). The two new files contribute 26 tests, including a two-store
concurrency case and a promotion-failure rollback.

## 4. Residuals / still blocked

* **Production producers remain blocked.** No authenticated lease producer and
  no `invoke_once` receipt producer are bound; `BLOCKED_TRUST_SOURCE` and
  `BLOCKED_ORIGINAL_OWNER` are unchanged.
* `check_writer(now=...)`, `acquire_lease(now=...)` and the adapter verbs still
  accept an explicit `now` as a test seam. The production commit boundary is
  `lease_guard`/`commit_guard`, which do not. Removing the seam from the older
  primitives would require migrating their existing tests in the stacked PRs.
* `verify_live_grant` (#272) compares grant windows as strings. Mixed
  `Z`/`+00:00` spellings at the same second can extend a grant by less than one
  second; it is a boundary issue, not a bypass, and is not changed here.
* #298's test pins `fc93590`. If #272 is rebased, the pin and the adapter's
  expectation must be re-verified.
