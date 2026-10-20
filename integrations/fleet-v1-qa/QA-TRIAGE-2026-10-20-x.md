# QA-TRIAGE-2026-10-20-x — Q1 pre-RC4 candidate audit

**Status: candidate-level `LOCAL` PASS for #332/#335 and
`HOSTED_WINDOWS_BUILD/UNIT` + `LOCAL` PASS for #334. No RC-v4 and no V1
signature. RC-v4 is PENDING.**

Lane: `Q1-independent-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T08:4xZ (UTC)**.

---

## 1. Pinned inputs

| Candidate | Exact head SHA | Base |
|---|---|---|
| #332 import isolation | `a60ae107423703b200e5f5915ebab1001f4d65f4` | RC-v2 `ec43f714` |
| #335 public authority | `85b953963e008f5777b9eb969fd9b1ffe09e4dbf` | RC-v2 `ec43f714` |
| #334 Windows hosted build | `c984f4a52b159eb6c358a5c573e39dc3c54dd704` | #309 `c97eb410` |
| RC-v3 (latest pushed) | `e3dc9c7ca0aeca9dd97e981de85da3a727ed9d8a` | — |
| RC-v4 | **not pushed** | **PENDING** |

RC-v3 tree check: `tests/v1_import_isolation.py`, the #335 public-producer
test, and the Windows workflow are **absent** from `e3dc9c7`. These three
candidates are post-RC-v3; RC-v4 must include them.

---

## 2. #332 — restore the in-tree `workengine` after a T10 archive load

**Test-only**: `git diff --stat ec43f71 a60ae10` shows 5 test files, +203
lines; no product file. No `xfail`/`skip` in the added lines. The helper
snapshots the module objects at collection time and restores them in
`setUpModule`/`tearDownModule`; a synthetic regression test asserts identity and
foreign-path removal.

Exact sequence (5 T10 + 3 consumers), single process:

```text
python -m pytest \
  tests/test_fleet_v1_t10_authority_bridge.py \
  tests/test_fleet_v1_t10_continuation_provenance.py \
  tests/test_fleet_v1_t10_legacy_compatibility.py \
  tests/test_fleet_v1_t10_producer_binding.py \
  tests/test_fleet_v1_t10_signed_finalization.py \
  tests/test_finalize_consistency.py tests/test_runner_receipt.py \
  tests/test_finalize_entry.py -q -p no:cacheprovider
```

| Tree | Result |
|---|---|
| RC-v2 base `ec43f71` | **53 failed, 58 passed** (67.09s) |
| #332 `a60ae10` | **111 passed**, exit 0 (46.69s) |
| `tests/test_v1_import_isolation.py` | **1 passed** |

No product code or security gate changed; the consumer hooks do not skip or
xfail anything. This closes the same-process pollution blocker without changing
what the consumers assert.

---

## 3. #335 — public authority positive conformance on RC-v2

**Test-only + docs/fixtures**: 9 files, +1224/−272; no product file. The public
tree at RC-v2 ships `workengine/authority.py`; the two former
`PRIVATE_LEGACY_SOURCE_REQUIRED` xfails are replaced by real public assertions.
No `xfail`/`skip` remains in either X2 suite.

| Run | Result |
|---|---|
| X2 three files (`test_fleet_v1_t10_legacy_compatibility.py`, `test_v1_grant_os_principal_schema.py`, `tests/v1_portability/test_portability_boundary.py`) | **41 passed, 0 xfailed** |
| Same process + `test_finalize_entry.py` + `test_finalize_consistency.py` | **74 passed** |
| Same five under `SERVEROS_PORTABILITY_GUARD=1`, roots `/data/home/vince/workspace/src/work-engine` | **74 passed**, no private access |

Boundary assertions read from the tests:

- The local producer is labelled **`PUBLIC_REIMPLEMENTATION_LOCAL`**; it writes
  `T10_VERIFIED`, but the consumer returns `ELIGIBLE_FOR_QA` with
  `v1_acceptable=False`, `parent_accepted=False`, `qa_signature=False`,
  `production_status=BLOCKED_TRUST_SOURCE`, `trusted_parent=TEST_DOUBLE`.
- `finalize_run(t10_required=True, receipt_authority=None)` →
  `BLOCKED_TRUST_SOURCE`; the Run stays `FINALIZING`.
- A locally forged `trusted-parent:` string → `BLOCKED_WIRING` /
  `V1_ACCEPTANCE_NOT_WIRED`; `trusted_parent=UNVERIFIED`; no acceptance.
- Commit/actor/run binding mismatches and receipt exit disagreement are refused.
- The real public `workengine.authority.LeaseGrant` accepts the daemon's
  verified wire; the module exposes the exact public seams.

No production trust parent exists; the local producer is not a production
authority and does not become one through a label.

---

## 4. #334 — hosted Windows contract build (`HOSTED_WINDOWS_BUILD/UNIT`)

**Not a product-code claim for live Windows.** The workflow is PR-only, path
filtered to `native/node-core/windows-core/**` and itself, `permissions:
contents: read`, uses `windows-latest`, pins `actions/checkout` by full SHA,
uses the runner's VS/CMake (no `-G`), configures the **standalone**
`windows-core`, builds, checks three `.exe`s, and runs `ctest -V`. It never
calls the runner's SCM, installs nothing, starts no service, reads no
credential and contacts no network endpoint.

Hosted evidence at the exact head:

| Run | Head | Result |
|---|---|---|
| 39390576739 | `c984f4a` | **success**, 1 job, 9/9 steps |
| toolchain | — | CMake 4.4.4; Visual Studio 18 2026; MSVC 19.51.36260.0 |
| binaries | — | `test_windows_scm_status.exe`, `test_windows_core_source_contract.exe`, `test_windows_status_document.exe` |
| suites | — | 74 + 55 + 139 checks, 0 failures; **100% tests passed out of 3** |

Local standalone Linux build at the same SHA (CMake standalone path):

| Run | Result |
|---|---|
| configure/build/ctest | **3/3 passed** |
| checks | 74 + **59** + 139 = **272 checks, 0 failures** |

The 55 vs 59 difference is the platform branch, not a skip: on `_WIN32` the
suite adds the `win32_scm_available()` check and does not run the four
`UNSUPPORTED_PLATFORM` observations (which describe a build without the
`_WIN32` half). The `\\remote\x` negative runs on both and is rejected by name
validation before any API.

Source review:

- `win32_scm.cpp`: only `WIN32_LEAN_AND_MEAN` / `NOMINMAX`; the `_WIN32` gate
  and read-only query semantics are unchanged.
- The standalone CMake branch defines portable `nodecore` from `../src/json.cpp`
  and a header-only `nodecore_supervisor` interface; the Linux supervisor .cpp
  set is not compiled on Windows.
- The `_WIN32` test asserts the compiled branch is present; it does **not**
  query the SCM. The named pipe/DACL/token are **NOT IMPLEMENTED**; the live
  Windows reading is **NOT_RUN** (issue #246). Non-ASCII service-name handling
  is compiled but not exercised; the PR documents this rather than hiding it.
- Label: **`HOSTED_WINDOWS_BUILD/UNIT`**, not `REAL_DEVICE`, not `SCM`, not
  `CLIENT_E2E`.

---

## 5. RC-v4

Not pushed (`rc/*` = `3f0240d`, `ec43f71`, `e3dc9c7`). **PENDING.** When it is
published, QA must use a fresh isolated checkout pinned to its exact SHA and
rerun T1/expiry/actor/core-boundary/T10/finalize-identity plus a test-only
REAL_TEMP daemon/socket no-effect refusal. This receipt does not sign RC-v4 or
the full T1–T12 matrix.

---

## 6. Findings / blockers

1. **#332**: no blocker; test-only, 53F→111P, helper 1P, no skips.
2. **#335**: no blocker; 41P/0x, 74P same process, 74P under the private-path
   guard, production remains `BLOCKED_TRUST_SOURCE`, forged label refused.
3. **#334**: no blocker for the **`HOSTED_WINDOWS_BUILD/UNIT`** claim. It is
   not REAL_DEVICE/SCM/CLIENT_E2E; the SCM is never called; the named pipe,
   DACL and token check are not implemented; the Windows suite's lower check
   count is a platform branch, not a skip.
4. **RC-v4 PENDING**: no RC sign-off; RC-v3 does not include these three
   candidates.
5. Carried: RC-v2's T1 admission fail-open/Core boundary remain outside this
   round; the earlier REAL_DEVICE/CLIENT_E2E gaps are unchanged.

---

## 7. Receipts

| Run | Result | Log |
|---|---|---|
| #332 sequence RED RC-v2 | 53 failed, 58 passed | `r7-332-sequence-red-rcv2.log` |
| #332 sequence GREEN | 111 passed | `r7-332-sequence-green.log` |
| #332 isolation helper | 1 passed | `r7-332-isolation-helper.log` |
| #335 X2 three | 41 passed, 0 xfailed | `r7-335-x2-three.log` |
| #335 five same-process | 74 passed | `r7-335-five.log` |
| #335 guarded five | 74 passed | `r7-335-guarded-five.log` |
| #334 hosted run | success at `c984f4a`; 9/9 steps; 268 checks | GitHub 39390576739 |
| #334 Linux standalone | 3/3 passed; 272 checks | `r7-334-linux-standalone-v.log` |

All local runs used explicit files, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard. No production daemon/provider/network/model
send, no secrets/OAuth, no real systemctl or Windows SCM.

---

## 8. Handback

1. **#332/#335**: candidate-level LOCAL PASS; keep Draft, no product change
   needed from QA.
2. **#334**: HOSTED_WINDOWS_BUILD/UNIT + LOCAL PASS; do not promote to
   REAL_DEVICE/SCM.
3. **I1**: RC-v4 PENDING; a new RC must include these candidates and rerun the
   full matrix + REAL_TEMP before any QA_SIGNED.
4. **#256/#244**: `QA_PARTIAL` stands; no V1 signature.

## 9. What this does not claim

- No RC-v4 sign-off, no full T1–T12 acceptance.
- No REAL_DEVICE, CLIENT_E2E, SCM, live Windows reading, real OAuth/token.
- The local T10 producer is not production authority; the hosted Windows build
  is not a user-device measurement.
