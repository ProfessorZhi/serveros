# X2 — public-safe V1 test portability and private-legacy boundary

Base: `rc/i1-review-20261020` @ `3f0240d`
Lane: X2 (independent test portability / private-legacy boundary)
Evidence level: **LOCAL / HERMETIC**
Scope: tests and documentation only. No product source, no Q1 trusted seam, no
running daemon, no live service, no credential.

## 1. The defect

At the RC, two suites hardcoded an **owner-private** Work Engine checkout and
extracted it with `git archive` / `git show`:

* `tests/test_fleet_v1_t10_legacy_compatibility.py`
* `tests/test_v1_grant_os_principal_schema.py`

The Dell/fjd 11-file pytest set therefore reported **294 passed, 7 xfailed,
1 failed, 12 setup errors**. The 12 setup errors were the private-checkout
fixture; the failure was the `git show` of the private `LeaseGrant` class.

The same shape is reproducible on Tokyo without deleting or reading that
checkout: a pytest audit-hook guard refuses any `open` / `os.*` /
`subprocess.*` event that touches an operator-supplied private root. Measured
on the RC files under the guard:

```text
1 failed, 11 passed, 12 errors
```

## 2. What the suites read now

| Source | Use |
| --- | --- |
| in-tree `workengine/` (sanitized public tree) | real legacy `ServerOSStore.finalize_run`, Run/evidence rows |
| in-tree `runtime/bounded_invocation.py` | real offline runner receipt (exit 1) |
| in-tree `integrations/fleet-v1-verdict-evidence/` | the consumer gate under test |
| `tests/v1_portability/fixtures/*.json` | pinned public contract fixtures |

The suites do **not** read, copy, print or commit the private archive, its tree,
paths, commit contents or credentials. The private root is supplied by the
operator at run time through `SERVEROS_PRIVATE_LEGACY_ROOTS`; it is not stored
in this repository.

## 3. Classification

| Evidence | Status | Where |
| --- | --- | --- |
| Legacy/pre-#15 compatibility is `v1_acceptable=False` and `v1_required=True` refuses | **PASS / EXECUTED** | `test_legacy_pre15_compat_cannot_affect_v1` |
| The real in-tree sanitized `finalize_run` writes no authorization marker and is denied by V1 | **PASS / EXECUTED** | `test_in_tree_legacy_finalize_is_unmarked_and_not_v1` |
| Explicit legacy `UNSIGNED` label is denied | **PASS / PINNED FIXTURE** | `test_pinned_unsigned_label_is_denied_by_v1` |
| Missing trusted parent is `BLOCKED_WIRING` | **PASS** | `test_v1_missing_trusted_parent_is_blocked_wiring` |
| A labelled test-double parent is `ELIGIBLE_FOR_QA` only, never V1 acceptable | **PASS** | `test_v1_test_double_grant_is_eligible_for_qa_only` |
| Caller JSON fake `T10_VERIFIED` is denied | **PASS** | `test_v1_caller_json_fake_label_is_denied` |
| Caller JSON grant is denied | **PASS** | `test_v1_caller_json_grant_is_denied` |
| Stale / unknown / revoked / epoch-less grant is not accepted | **PASS** | `test_v1_stale_or_unknown_grant_is_not_accepted` |
| Cross-grant / actor / Run / commit is denied | **PASS** | `test_v1_cross_grant_actor_or_run_is_denied` |
| Tampered durable evidence is denied | **PASS / EXECUTED** | `test_v1_tampered_bytes_is_denied` |
| Child stdout says PASS but the real receipt exits 1 is denied | **PASS / EXECUTED** | `test_v1_child_text_pass_real_exit_one_is_denied` |
| Grant answer is the full pinned public `LeaseGrant` wire shape | **PASS / PINNED FIXTURE** | `test_the_positive_result_matches_the_pinned_public_contract` |
| WE #14's own `LeaseGrant` dataclass accepts the answer | **PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN** | `test_the_real_work_engine_lease_grant_dataclass_is_private_legacy_source_required` |
| WE #15's real signed T10 verifier produces the durable finalize | **PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN** | `test_real_signed_t10_verifier_is_private_legacy_source_required` |

`PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN` is an explicit `xfail` with the
reason in the test and in the private-safe ledger. It is **not** a blanket skip
and does not manufacture green. If the public tree ever ships
`workengine/authority.py` with `LeaseGrant` / `T10Binding` /
`VerifiedRunReceipt`, those tests run the original assertions and the
classification must be removed deliberately.

## 4. Run

Operator supplies the private root only to *forbid* it (never to read it):

```bash
cd <this worktree>
SERVEROS_PORTABILITY_GUARD=1 \
SERVEROS_PRIVATE_LEGACY_ROOTS="$OWNER_PRIVATE_LEGACY_ROOT" \
PYTHONPATH=tests/v1_portability \
python -m pytest -p private_path_guard -q \
  tests/v1_portability/test_portability_boundary.py \
  tests/test_fleet_v1_t10_legacy_compatibility.py \
  tests/test_v1_grant_os_principal_schema.py
```

Without the guard, the same two suites run normally:

```bash
python -m pytest -q \
  tests/test_fleet_v1_t10_legacy_compatibility.py \
  tests/test_v1_grant_os_principal_schema.py
```

Bounded execution used on Tokyo (shared host):

```text
systemd-run --user --scope --collect \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 -- \
  timeout 300 <python> -m pytest ...
```

## 5. Measured results — Tokyo, 2026-10-20

| Run | Result |
| --- | --- |
| The two suites, no guard, bounded | **25 passed, 2 xfailed** |
| The two suites + boundary, guarded, bounded | **32 passed, 2 xfailed** |
| Boundary suite alone | **7 passed** |
| RC reproduction under the guard (before the rewrite) | **1 failed, 11 passed, 12 errors** |

The two `xfailed` are the private-legacy producer proofs above. There are no
product-source changes and no changes to Q1's exact-SHA trusted seams.

## 6. Dell/fjd retest

Status: **PENDING owner-coordinated retest**. The same three paths, the same
guard variables and the same bounded command above are the retest. Expected on
a host without the private checkout: the guarded and unguarded counts must be
identical, because the suites never reach for it.

## 7. Non-claims

This is LOCAL/HERMETIC consumer-gate evidence. It is not `REAL_DEVICE`, not a
production acceptance, not a QA signature, and not a release. The private
verifier's positive output remains `NOT_RUN` until a public, trusted producer
exists.
