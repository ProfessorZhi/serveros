# X1 — the last two private Work Engine fixtures, made public-safe

**Verdict:** `CODE / LOCAL`. Test-only. The two T10 files no longer read an
owner-private checkout; they exercise the public in-tree `workengine` package
the tree ships.

**Baseline:** `rc/i1-v4-review-20261020` = `40c4b1c46e5d3f3da883045bfa2152acc41da01a`
(local = origin). Files: `tests/test_fleet_v1_t10_authority_bridge.py` and
`tests/test_fleet_v1_t10_signed_finalization.py`.

**Historical label:** the old #14 `e4c4a2b2…` and #15 `4bd9e8a1…` archive
equality is `HISTORICAL_PRIVATE_SOURCE_UNVERIFIED` / `NOT_RUN`. It is never a
PASS, and the old SHAs are kept only as labels, not as paths read by the suite.

## 1. RED on a host without the private checkout

X2 #335's `tests/v1_portability/private_path_guard.py` reproduces Dell/fjd on
Tokyo without reading the private tree:

```bash
SERVEROS_PORTABILITY_GUARD=1 \
SERVEROS_PRIVATE_LEGACY_ROOTS=/data/home/vince/workspace/src/work-engine \
PYTHONPATH=tests/v1_portability \
python -m pytest <files> -q -p private_path_guard
```

Before this change:

| file | result | cause |
|---|---|---|
| `tests/test_fleet_v1_t10_signed_finalization.py` | **13 errors** | `PRIVATE_LEGACY_SOURCE_ACCESS: subprocess.Popen touched an owner-private legacy checkout` |
| `tests/test_fleet_v1_t10_authority_bridge.py` | **10 errors** | same |

Both fixtures ran `git -C /data/home/vince/workspace/src/work-engine archive
<old SHA>` and then deleted `workengine*` from `sys.modules`. That is the same
failure Dell reported as `109 passed, 2 failed, 23 errors` on the 9-file
one-process suite: 23 = 13 + 10.

## 2. What changed (two test files)

- The `we14` / `we15` fixtures now import the **public in-tree** package
  normally:
  `workengine.WorkItem`, `workengine.authority.{ReceiptError, VerifiedRunReceipt,
  T10Binding}`, `workengine.model.{Activity, ContextMode, ExecutionContext,
  RunState}`, `workengine.store.ServerOSStore`.
- No `git archive`, no `tarfile`, no private `WE_REPO` path, no
  `sys.path.insert` of a private root, and **no deletion of `workengine*` from
  `sys.modules`**. The #332 module-identity property is therefore preserved by
  construction.
- The PREVIEW bridge predates the public receipt's `actor_ref` field. Its
  `receipt_factory` is now a test-local adapter that supplies `actor_ref` from
  the same grant principal the continuation authority already names
  (`CONTEXT`); it is not a second authority, a caller claim, or a new DB.
- The signed-finalization `_V15ReceiptAuthority` test double already supplied
  all ten fields, so it needed no logic change; only its fixture changed.
- Two explicit identity tests were added:
  `test_the_public_fixture_keeps_the_in_tree_module_identity` in each file
  asserts `sys.modules["workengine.authority"] is workengine.authority`,
  `we1x.ReceiptError is workengine.authority.ReceiptError`, and that the module
  file is under the repository.

No product code, no X2 file, no `tests/v1_portability/**` file changed.

## 3. Public API mapping

| old private shape | public in-tree shape | note |
|---|---|---|
| #14 `VerifiedRunReceipt` (9 fields) | `workengine.authority.VerifiedRunReceipt` (10 fields, adds `actor_ref`) | bridge factory supplies `actor_ref` from the grant principal |
| #15 `T10Binding` / `VerifiedRunReceipt` | same names/fields in `workengine.authority` | `_V15ReceiptAuthority` constructs the public class with all ten fields |
| `ServerOSStore.finalize_run_verified` | same method, same keyword contract | receipt is re-checked by `validate_verified_receipt` |
| `ServerOSStore.finalize_run(..., t10_required=True, t10_binding=...)` | same method and contract | `validate_t10_binding` checks exact Item/Run/commit/actor |
| #14/#15 `ReceiptError` | `workengine.authority.ReceiptError(reason_code, detail)` | bridge `error_factory` already matches |
| `RunnerReceiptAuthority` | public Protocol; `_V15ReceiptAuthority` is a clearly labelled test double | not a production verifier |

## 4. GREEN

All runs use `python -m pytest -p no:cacheprovider` under
`systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- nice -n 10 timeout ...`.

| check | result |
|---|---|
| the two files, without the guard | **25 passed**, exit 0 (5.21s) |
| the two files, under the private-path guard | **25 passed**, exit 0 (earlier run: 23 passed before the two identity tests) |
| 9-file same-process suite under the guard: 5 T10 + `finalize_consistency` + `runner_receipt` + `finalize_entry` + `core_ability_boundary` | **136 passed**, exit 0 (74.70s) — 134 pre-existing + 2 identity tests, **0 fail / 0 error** |
| X2's `test_v1_grant_os_principal_schema.py` + #332 identity helper, under the guard | **14 passed**, exit 0 (3.91s) |

The 9-file result is the Dell/fjd reproduction: `109 passed + 2 failed + 23
errors` becomes **136 passed, 0 failed, 0 error** on the same one-process order.

## 5. Coverage preserved

- `authority_bridge`: real exit-1 child with PASS text cannot conclude success;
  real exit-0 receipt concludes `SUCCEEDED`; child-text path, caller JSON/hash,
  missing trusted context, missing parent anchor, stale commit SHA, revoked
  grant, cross-Run, cross-principal, and checksum tampering are all refused.
- `signed_finalization`: legacy `SUCCEEDED`/`UNSIGNED` is denied; exact
  T10-verified zero-exit is `ELIGIBLE` for independent QA only with
  `production_status=BLOCKED_TRUST_SOURCE`; missing `t10_acceptable`, caller
  JSON label, tampered evidence, commit/actor/Run mismatch, non-zero exit,
  weaker authorization, missing receipt anchor/source, and unreadable source
  are denied.
- Authority `VERIFIED` / `FACT` / current epoch / peer OS identity remain
  covered by X2's public `tests/test_v1_grant_os_principal_schema.py` (read-only
  here), which passes under the same guard.
- The positive path is a **local test producer**, not production acceptance:
  `parent_accepted=False`, `qa_signature=False`, `production_status=BLOCKED_TRUST_SOURCE`.

## 6. Boundaries

- Only the two owned test files plus this document.
- No X2 file, no `tests/v1_portability/**`, no X1 prior-PR branch, no I1/QA
  worktree, no product runtime, no `fleet/registry.yaml`, no service,
  credential, OAuth or provider send.
- No merge, reset, clean, prune, force push or deletion.
- `LOCAL` only; no `REAL_TEMP` / `REAL_DEVICE` / `CLIENT_E2E` claim. Dell should
  retest the exact PR SHA locally after publish; Windows device files are not
  touched.
