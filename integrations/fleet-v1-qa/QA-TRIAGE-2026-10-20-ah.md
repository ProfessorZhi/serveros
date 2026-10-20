# QA-TRIAGE-2026-10-20-ah — X2 #367 synthetic import-probe fix

**Status: #367 `PASS` at LOCAL for the synthetic import-probe fix; no product,
S1 fixture/grant, private-path guard or skip change. `QA_PARTIAL` /
`NOT_RELEASE`; no QA/V1 signature. Real Windows is **NOT_RUN**. The
symlink-WinError1314 / drive-relative / 8.3 guard tests are **NOT_PRESENT** on
the #367 branch (ancestry gap, see §5); they remain RC-v7 tests and must be
re-run in the eventual RC-v8 composition. RC-v8 is not published.**

Lane: independent Q1 QA (#244/#250, register #256). Separate clean checkouts
at the exact #367 head and its #364 base; no author/RC/I1 worktree, product
source, production service, OAuth or credential touched.

---

## 1. Exact refs

| Item | Value |
|---|---|
| #367 Draft PR | `test/x2-win-import-probe-20261020` |
| Head (local = remote = PR) | `3cb39ae30e1be6811cb474b3b6b83e49db44ab52` |
| Base | S1 #364 `7fe1cccd6b7f103e58140ef7d512d69e436704ec` |
| QA checkout | `q1-pr367-qa-20261020`, detached, clean |
| RED base checkout | `q1-pr367-red-20261020` at #364 `7fe1ccc`, clean |
| Diff | **2 files, +124/−0**: `tests/v1_portability/test_portability_boundary.py` (+45), `tests/v1_portability/PROBE-IMPORT-CONTRACT.md` (+79); **no product/runtime path, no S1 fixture/grant, no private-path guard, no skip/xfail** |

`git show --numstat` is `79 0` and `45 0`; the only `sys.path.insert` is inside
the `python -c` subprocess string (the probe mirroring pytest prepend), never
in the test process or product code.

---

## 2. RED at #364 `7fe1ccc`

Boundary suite at the exact base (before the #367 fix):

```text
1 failed, 50 passed in 1.41s   (51 collected)
```

Failure: the `spec_from_file_location` probe subprocess did not put `tests/`
on `sys.path`, so S1's module-level `import owner_policy_fixtures` raised:

```text
ModuleNotFoundError: No module named 'owner_policy_fixtures'
tests/test_v1_grant_os_principal_schema.py:43
```

This is the reported 100P/1F failure, independently reproduced. It is an
import-environment defect in the probe, not a product/S1 authorization
failure.

---

## 3. GREEN at #367 `3cb39ae`

| Run | rc | Result |
|---|---|---|
| Boundary suite | 0 | **52 passed, 0 skipped** |
| X2 three files under private-path guard (boundary + grant + legacy) | 0 | **84 passed, 0 skipped** |
| Boundary + grant + legacy + `test_finalize_entry` + `test_finalize_consistency` | 0 | **119 passed, 0 skipped** |

The new negative test `test_win32_probe_needs_the_suite_directory_on_sys_path`
passes and asserts the exact `ModuleNotFoundError.name ==
"owner_policy_fixtures"`; the existing probe still prints `WIN32_IMPORT_OK 13`
with `tests/` added. CRLF/canonical/tamper tests remain in the 52; no test is
skipped or xfailed.

---

## 4. Scope check

The diff adds only the probe's subprocess `sys.path` prepend and a documentation
contract. It does **not**:

- change any product/runtime `sys.path` or code;
- change the S1 owner-policy fixture/grant or its module-level import;
- change `tests/v1_portability/private_path_guard.py`;
- add `pytest.skip`/`xfail` or remove a test;
- change the CRLF/canonical fixture or the win32 probe's `workengine`/`fcntl`
  blocker.

The probe still sets `sys.platform="win32"` and blocks `workengine`/`fcntl`
before import; any other import error still propagates.

---

## 5. Ancestry limitation: Windows guard tests are NOT on #367

`#353` (`21962267`) is **not an ancestor** of #367 or its base #364;
`#349` is not either. The boundary suite on #367 has **51 tests**, not the 58
from RC-v7: the symlink `HOST_PRIVILEGE_NO_SYMLINK`, drive-relative and 8.3
tests live in RC-v7 #357, not in this S1 branch. Therefore:

- “the symlink/drive-relative/8.3 negatives are maintained” is **NOT_VERIFIED
  on #367**; on this exact branch they are **NOT_PRESENT / NOT_RUN**.
- The #367 diff is additive and does not remove them; the actual RC-v8
  composition must re-run the RC-v7 boundary (58 + the new probe test) together
  with S1 #364 and #367.

A **local test-only composition** of RC-v7 `85c79b2` + the #367 boundary patch
(not RC-v8, not a #367-exact claim) collected 59 tests and gave **1 failed / 58
passed**: the 9 symlink/drive/8.3/CRLF-oriented tests passed, while the new
negative test failed because RC-v7 alone has no S1 module-level
`owner_policy_fixtures` import — expected for that incomplete composition. It
does not establish RC-v8; RC-v8 must be tested at its exact published SHA.

Real Windows remains **NOT_RUN**; synthetic win32 is not a real-device pass.

---

## 6. Judgment

| Item | Verdict | Level |
|---|---|---|
| #367 import-probe RED reproduction | **PASS** (1 failed/50 passed at #364) | LOCAL |
| #367 GREEN boundary / X2 three / combo | **PASS** (52 / 84 / 119, 0 skip) | LOCAL/SYNTHETIC |
| #367 product/guard/skip scope | **PASS** (test/doc only, no removal) | CODE/LOCAL |
| WinError1314 / drive-relative / 8.3 maintenance on #367 | **NOT_PRESENT / NOT_RUN** | ancestry gap |
| Real Windows | **NOT_RUN** | — |
| RC-v8 | **PENDING** (no `rc/i1-v8*` ref) | — |

**No QA/V1 signature.** The probe fix is correct for its purpose and the
requested small suites are green, but RC-v8, real Windows, trusted owner
`COMMIT_FENCED`, OS grant/provider, live OAuth, Windows SCM/REAL_DEVICE,
CLIENT_E2E and full T1–T12 remain blocking. **QA_PARTIAL / NOT_RELEASE.**

## 7. Receipts

| Run | Result | Log |
|---|---|---|
| RED boundary at #364 | 1 failed / 50 passed | `r21-367-red-boundary.log` |
| GREEN boundary #367 | 52 passed / 0 skip | `r21-367-green-boundary.log` |
| X2 three files #367 | 84 passed / 0 skip | `r21-367-x2-three.log` |
| Combo (5 files) #367 | 119 passed / 0 skip | `r21-367-combo119.log` |
| Local RC-v7 + #367 test patch (not RC-v8) | 1 failed / 58 passed; 9 guard tests pass | `r21-367-rcv7-comp-boundary.log`, `r21-367-rcv7-comp-targeted.log` |
