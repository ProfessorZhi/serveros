# X2 — public-safe V1 test portability and public-authority conformance

Base: `rc/i1-v4-review-20261020` @ `40c4b1c` (X2 #335 @ `85b9539` is composed in)
Lane: X2 (independent test portability / private-legacy boundary)
Evidence level: **LOCAL / HERMETIC**
Scope: tests and documentation only. No product source, no Q1 trusted seam, no
running daemon, no live service, no credential.

## 1. The defect

At RC-v1, two suites hardcoded an **owner-private** Work Engine checkout and
extracted it with `git archive` / `git show`:

* `tests/test_fleet_v1_t10_legacy_compatibility.py`
* `tests/test_v1_grant_os_principal_schema.py`

The Dell/fjd 11-file pytest set therefore reported **294 passed, 7 xfailed,
1 failed, 12 setup errors**. The 12 setup errors were the private-checkout
fixture; the failure was the `git show` of the private `LeaseGrant` class.

A pytest audit-hook guard reproduces the same shape on Tokyo without deleting or
reading that checkout. Measured on the RC-v1 files under the guard:

```text
1 failed, 11 passed, 12 errors
```

## 2. What the suites read now

| Source | Use |
| --- | --- |
| in-tree `workengine/` (public, sanitized) | real `ServerOSStore` legacy `UNSIGNED` and public `finalize_run(t10_required=True)` |
| in-tree `workengine/authority.py` (#304, RC-v2) | real public `LeaseGrant`, `ReceiptError`, `T10Binding`, `VerifiedRunReceipt`, `OwnerVerifiedLease` |
| in-tree `runtime/bounded_invocation.py` | real offline runner receipt |
| in-tree `integrations/fleet-v1-verdict-evidence/` | the consumer gate under test |
| `tests/v1_portability/fixtures/*.json` | pinned public contract fixtures |

The suites do **not** read, copy, print or commit the private archive, its tree,
paths, commit contents or credentials. The private root is supplied by the
operator at run time through `SERVEROS_PRIVATE_LEGACY_ROOTS`; it is not stored
in this repository.

## 3. Evidence labels

| Label | Meaning |
| --- | --- |
| **PUBLIC_REIMPLEMENTATION_LOCAL** | The public in-tree `workengine.authority` dataclasses plus a LOCALLY CONTROLLED `RunnerReceiptAuthority`. This proves the public API and its linkage to the consumer gate; it is **not** a production trusted parent. |
| **HISTORICAL_PRIVATE_SOURCE_UNVERIFIED** | The old owner-private Work Engine #15 producer. It is not read, extracted or copied, and no claim is made that it produced the same record. |
| **PINNED_PUBLIC_FIXTURE** | The durable `T10_VERIFIED` / explicit `UNSIGNED` consumer shape under `fixtures/`, used where the producer is not the point. |
| **EXECUTED** | The real in-tree store and bounded runner produced the record. |

## 4. Classification

| Evidence | Status | Where |
| --- | --- | --- |
| Legacy/pre-#15 compatibility is `v1_acceptable=False`; `v1_required=True` refuses | **PASS / EXECUTED** | `test_legacy_pre15_compat_cannot_affect_v1` |
| The real in-tree legacy `finalize_run` writes durable `UNSIGNED` and is denied by V1 | **PASS / EXECUTED** | `test_in_tree_legacy_finalize_is_unsigned_and_not_v1` |
| Explicit legacy `UNSIGNED` label is denied | **PASS / PINNED_PUBLIC_FIXTURE** | `test_pinned_unsigned_label_is_denied_by_v1` |
| Missing trusted parent is `BLOCKED_WIRING` | **PASS** | `test_v1_missing_trusted_parent_is_blocked_wiring` |
| A labelled test-double parent is `ELIGIBLE_FOR_QA` only | **PASS** | `test_v1_test_double_grant_is_eligible_for_qa_only` |
| Caller JSON fake `T10_VERIFIED` is denied | **PASS** | `test_v1_caller_json_fake_label_is_denied` |
| Caller JSON grant is denied | **PASS** | `test_v1_caller_json_grant_is_denied` |
| Stale / unknown / revoked / epoch-less grant is not accepted | **PASS** | `test_v1_stale_or_unknown_grant_is_not_accepted` |
| Cross-grant / actor / Run / commit is denied | **PASS** | `test_v1_cross_grant_actor_or_run_is_denied` |
| Tampered durable evidence is denied | **PASS / EXECUTED** | `test_v1_tampered_bytes_is_denied` |
| Child stdout says PASS but the real receipt exits 1 is denied | **PASS / EXECUTED** | `test_v1_child_text_pass_real_exit_one_is_denied` |
| Grant answer is the full pinned public `LeaseGrant` wire shape | **PASS / PINNED_PUBLIC_FIXTURE** | `test_the_positive_result_matches_the_pinned_public_contract` |
| The REAL public `LeaseGrant` class accepts the daemon's verified wire | **PASS / EXECUTED** | `test_the_real_public_work_engine_lease_grant_accepts_the_verified_wire` |
| The public authority module exposes `LeaseGrant`, `ReceiptError`, `T10Binding`, `VerifiedRunReceipt`, `OwnerVerifiedLease` | **PASS** | `test_the_public_authority_module_exposes_the_exact_seams` |
| The REAL public `T10Binding` / `VerifiedRunReceipt` + `finalize_run(t10_required=True)` write a durable `T10_VERIFIED` | **PASS / PUBLIC_REIMPLEMENTATION_LOCAL** | `test_public_t10_producer_writes_an_exact_t10_verified_finalize` |
| No trusted receipt parent means `BLOCKED_TRUST_SOURCE`, Run stays `FINALIZING` | **PASS / EXECUTED** | `test_public_t10_producer_without_a_parent_is_blocked` |
| A T10 binding for another commit / actor / Run is refused | **PASS / EXECUTED** | `test_public_t10_binding_mismatch_is_refused` |
| A receipt exit 1 disagreeing with the caller's 0 is refused | **PASS / EXECUTED** | `test_public_t10_receipt_exit_disagreement_is_refused` |
| A locally forged `trusted-parent:` label cannot accept | **PASS** | `test_public_t10_trusted_parent_label_is_not_acceptance` |
| The old private #15 producer produced the same record | **HISTORICAL_PRIVATE_SOURCE_UNVERIFIED** | not run; no claim |

There are **no `pytest.xfail` and no `pytest.skip`** calls in the two suites;
`test_suite_has_no_blanket_skip_or_xfail` enforces that statically.

## 5. Run

Operator supplies the private root only to *forbid* it (never to read it):

```bash
cd <this worktree>
SERVEROS_PORTABILITY_GUARD=1 \
SERVEROS_PRIVATE_LEGACY_ROOTS="$OWNER_PRIVATE_LEGACY_ROOT" \
PYTHONPATH="$PWD:$PWD/tests/v1_portability" \
python -m pytest -p private_path_guard -q \
  tests/v1_portability/test_portability_boundary.py \
  tests/test_fleet_v1_t10_legacy_compatibility.py \
  tests/test_v1_grant_os_principal_schema.py
```

Same-process T10 + Finalize run (X1's public finalize tests are included
read-only; they are not edited here):

```bash
PYTHONPATH="$PWD" python -m pytest -q \
  tests/v1_portability/test_portability_boundary.py \
  tests/test_fleet_v1_t10_legacy_compatibility.py \
  tests/test_v1_grant_os_principal_schema.py \
  tests/test_finalize_entry.py \
  tests/test_finalize_consistency.py
```

Bounded execution used on Tokyo (shared host):

```text
systemd-run --user --scope --collect \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 -- \
  timeout 420 <python> -m pytest ...
```

## 6. Measured results — Tokyo, 2026-10-20

| Run | Result |
| --- | --- |
| RC-v2 pre-upgrade canary (public authority appears) | **2 failed, 25 passed** (intended hard fail) |
| X2 boundary + two suites, bounded | **41 passed, 0 xfailed** |
| Same process + X1 `test_finalize_entry.py` + `test_finalize_consistency.py` | **74 passed** |
| Same process under the private-path guard | **74 passed** |
| RC-v1 reproduction under the guard (historical) | **1 failed, 11 passed, 12 errors** |

No product source is changed and no Q1 exact-SHA trusted seam is touched.

## 7. Dell/fjd retest

Status: **PENDING owner-coordinated retest**. The same three X2 paths and the
same bounded command are the retest. Expected on a host without the private
checkout: **41 passed, 0 xfailed, 0 errors**; the guard and unguarded counts
must be identical because the suites never reach for it.

## 8. Non-claims

This is LOCAL/HERMETIC public-API conformance and consumer-gate evidence. The
locally controlled producer is `PUBLIC_REIMPLEMENTATION_LOCAL`, not a production
trusted parent; the V1 decision remains `ELIGIBLE_FOR_QA` only with
`v1_acceptable=False`, `production_status="BLOCKED_TRUST_SOURCE"`. It is not
`REAL_DEVICE`, not a production acceptance, not a QA signature, and not a
release. The historical private producer remains unverified.

## 9. Windows FJPCOS portability repair (RC-v4 @ 40c4b1c)

The real Windows agent reported **4 failed + 19 skipped** on this exact RC-v4
head. None of the four was a product/trust failure; all were test-portability
defects. The 19 skips are the legacy suite's explicit producer boundary, not
silence.

### 9.1 Fixture pinning under `core.autocrlf`

`finalize_t10_verified.v1.json` is **613 LF bytes / sha256 `81f51a...`** in Git;
a Windows `core.autocrlf` working tree had **633 bytes / CR=20 / sha256
`7a8e0c...`**. Three `test_public_fixture_is_pinned` cases compared the raw
working-tree bytes and failed.

Repair:

* `tests/v1_portability/fixtures/.gitattributes` sets `*.json text eol=lf`, so
  a fresh checkout is canonical on every platform.
* `canonical_json_bytes()` accepts a PURE CRLF transcription and normalizes it
  to LF. It rejects a BOM, a lone CR, mixed endings, non-UTF-8 and invalid JSON.
* The pin is over the CANONICAL bytes, so a changed field or an extra byte is
  still a hard failure; CRLF cannot launder a semantic change.
* Negative tests: pure CRLF passes; changed field fails; UTF-8/UTF-16 BOM
  fails; lone CR fails; mixed EOL fails.
* Measured on Linux with all pinned fixtures converted to pure CRLF (the exact
  633-byte / `7a8e0c...` form): the boundary suite is **34 passed**.

Operator re-checkout on Windows (not run by this lane):

```text
git add --renormalize tests/v1_portability/fixtures
git checkout -- tests/v1_portability/fixtures
```

### 9.2 Guard root normalization

`_inside` normalized only the candidate and compared it with the raw root. On
Windows the normalized candidate (`d:\...`, `normcase`) never matched the raw
`D:\...` root; on Linux a synthetic backslash root also escaped because
`os.sep` is `/`.

Repair: BOTH candidate and root are canonicalized into the same path family
(`win`, `unc`, `drive-relative`, `device`, `posix`, `win-native`), with `/`
separators and case-folding for Windows semantics. The long-path `\\?\` prefix
names the same file; `\\.\` and drive-relative paths are different families and
never match. Sibling prefixes (`root2`) and traversal (`root\..\other`) are
refused. The env-vs-argv rule is unchanged: environment values are not scanned.

Synthetic Windows cases on Linux cover drive case, both separators, the
long-path alias, drive-relative, UNC case, sibling share and traversal; POSIX
case-sensitivity is preserved.

### 9.3 The 19 skips

`tests/v1_portability/fixtures/windows-skip-manifest.v1.json` lists all **19**
legacy cases (and the 13 grant cases) with
`reason_code: PRODUCER_BOUNDARY_POSIX_ONLY`. The module-level reason string in
both suites names that code and the manifest. Windows FJPCOS has no verified X2
producer; the POSIX bounded runner / in-tree finalize / uid grant read are not
run against a fake Windows producer. No skip is removed to manufacture green.
The portable boundary, fixture-pin and guard tests remain enabled on Windows.

### 9.4 Measured after the repair (Tokyo Linux, bounded 1 GiB / swap 0 / 25% CPU)

| Run | Result |
| --- | --- |
| X2 three files | **66 passed, 0 skipped** |
| Same process + X1 `test_finalize_entry.py` + `test_finalize_consistency.py` | **99 passed** |
| Same process under the private-path guard | **99 passed** |
| Boundary suite with all pinned fixtures converted to pure CRLF | **34 passed** |
| Windows pre-fix (reported at RC-v4) | **4 failed, 19 skipped** |

The remote Windows retest is requested through #250/#244; this lane does not
touch or install anything on the user's Windows worktrees.
