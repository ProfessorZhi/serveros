# QA-TRIAGE-2026-10-21-ai — RC-v8 exact-HEAD Q1 gate

**Status: `QA_PARTIAL` / `NOT_RELEASE`; no QA_SIGNED. The tested LOCAL /
REAL_TEMP / hosted-contract gates pass at the published RC-v8 head, but the
production owner-lease authority remains `BLOCKED_OWNER_COMMIT_SEAM` (no
`COMMIT_FENCED` producer), R1 remains synthetic-only, and real Windows/Dell,
live OAuth/provider and full T1–T12 are NOT_RUN.**

Lane: independent Q1 QA (#244/#250, register #256). Own detached checkout at
the actual GitHub PR head; no I1/author worktree, product source, production
service, OAuth or credential was edited.

---

## 1. Exact refs and docs-only proof

| Item | Value |
|---|---|
| RC-v8 Draft PR | **#369**, `rc/i1-v8-review-20261020` |
| Published PR head / local HEAD | **`a6663075a886093b5ea2e28cd6baf8ca63365f2f`** (local = remote) |
| Tested code tip | `74bae6d41d8e2f5fe23697e9376c42a7f97d0dae` |
| `74bae6d..a666307` | **3 docs files only**: I1-RC-DAG, RC-V8-PREFLIGHT, RC-V8-REVIEW; **no code/test difference** |
| Base / main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` (unchanged) |
| QA checkout | `q1-rcv8-qa-20261021`, detached at `a666307`, clean |

### Composition / cherry-pick check

RC-v7 `85c79b2` is an ancestor. The RC-v8 commits are:

`7fb3646` (#356), `9d5dd68` (#361), `97b84ce` (#358), `fce762f` (#363),
`bf785d5` (#364), `74bae6d` (#367), then docs `28d45a0`/`a666307`.

Patch SHA-256 equality against the original PR heads is exact for #356, #361,
#358, #363 and #364, and for #353 `21962267` ↔ RC-v7 `560f715`. #367's
cherry-pick has different diff *context* because it lands on the RC-v7 boundary
file, but the **added lines are byte-identical** to `3cb39ae`
(`sys.path` prepend + the negative test); no omission or duplicate. No
duplicate commit titles. #353's symlink-WinError1314, drive-relative and 8.3
tests are present in the composed boundary (59 tests).

---

## 2. Independent matrix at `a666307`

All runs sequential, `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
TasksMax=64 / nice 10`.

| Gate | rc | Result | Level |
|---|---|---|---|
| 9-file T10+Finalize+Core, one process, private-path guard | 0 | **138 passed / 0 skipped** (78.29 s) | LOCAL |
| Core/Fabric boundary | 0 | **17 passed** | LOCAL |
| S1 handoff (source revoke/target writer/lease+epoch) | 0 | **25/25** | LOCAL |
| Lease admission (`+08:00`/`Z`, malformed, naive; no provider on refusal) | 0 | **24/24** | LOCAL |
| Expiry / timezone / `writer_axis` | 0 | **10/10** | LOCAL |
| Preclaim provider/close failure | 0 | **21/21** | LOCAL |
| Actor VERIFIED+FACT / context authority | 0 | **16/16** | LOCAL |
| Idempotency/restart | 0 | **12/12**; cross-device **NOT_RUN** | LOCAL |
| Durable reconcile `source=None`/UNKNOWN → BLOCKED, no write/no send, single ledger | 0 | **14/14 + daemon 29/29** | LOCAL |
| QA2 S1 original 6 findings at RC-v8 | 0 | **6/6 pass** (revoked non-bool, future issued_at, verify/revoke, owner fields, readback) | LOCAL SYNTHETIC |
| QA2 S1 controls | 1 | **34/36**; the two old route positives are re-attributed to `BLOCKED_OWNER_COMMIT_SEAM` (not 36/36, not deleted) | LOCAL SYNTHETIC |
| Owner lease seam (`qa_probe_r19_seam_364`) | 0 | **10/10 GREEN**: positive + race **0 leases**, `BLOCKED_OWNER_COMMIT_SEAM`; adapter refuses unfenced owner answer; explicit `COMMIT_FENCED` control accepted; no resolver `BLOCKED_OWNER_POLICY` 0; no authority `BLOCKED_TRUST_SOURCE` 0 | LOCAL / REAL_TEMP socket |
| R1 QA2 v1 findings | 1 | 4 shape mismatches, all actual `BLOCKED_PROVIDER_SOURCE` / `settle_allowed=false`; **no production pass claimed** | LOCAL synthetic |
| R1 QA2 v1 controls | 1 | `KeyError: 'decision'` — v1 probe incompatible with v2 shape | — |
| R1 independent v2 attacks | 0 | **25/25**: empty/absent id, forged source, invalid/future/stale time, thread/adapter, old epoch, replay, no source all refused | LOCAL synthetic |
| X2 composed group (boundary 59 + grant 13 + legacy 19 + Windows CLI 10 + import isolation 1) | 0 | **102 passed / 0 skipped**; targeted symlink/drive/8.3/CRLF/win32 tests **14/14** | LOCAL/synthetic |
| Pre-#367 synthetic composition (reverse #367 test patch in own temp tree) | 1 | **100 passed / 1 failed**; failure is the original `ModuleNotFoundError: owner_policy_fixtures`, reproducing the pre-fix 100P/1F | LOCAL synthetic |
| Fleet Next offline | 0 | **286 passed / 133 subtests** | LOCAL |
| Native gate/journal | 0 | **89/0 + 88/0; CTest 2/2** | LOCAL |
| Native dirfd probe | 0 | **10/10** | LOCAL |
| REAL_TEMP (temp 0700 daemon/CLI) | 0 | **PASS/REFUSAL** — socket 0600, status 0, verify/reconcile no-context `UNTRUSTED_ACTOR`, grant issue `BLOCKED_OWNER_POLICY`, no `store.json`, stop/remove | REAL_TEMP |
| Hosted Windows contract `39439556186` | success | `serveros-native-windows-contract` at `a666307` — **HOSTED_CI/UNIT, not REAL_DEVICE** | HOSTED |
| Hosted Fleet Next `39439556171` | success | `Fleet Next isolated canary contracts` at `a666307` — **HOSTED_CI, not device** | HOSTED |

### R1 shape difference (preserved, not greenwashed)

The QA2 v1 probe now sees `BLOCKED_PROVIDER_SOURCE` for the empty-id/replay
cases and its controls crash on the removed top-level `decision`; that is a
schema-shape difference, not a security pass. The independent v2 probe drives
the same attacks semantically and is 25/25; production fields are always
`BLOCKED_PROVIDER_SOURCE`, `owner_signed=false`, `settle_allowed=false`,
`s2_verdict=null`.

### S1 control re-attribution

The two old route positives (`valid-policy-10-field-dto`,
`caller-actor-ref-ignored`) now return `verified:false` with
`BLOCKED_OWNER_COMMIT_SEAM` and no grant. #364 moved the #272 DTO/epoch/uid
coverage to `verify_store` (production `verify_live_grant`) and renamed the
route test to assert the fence refusal. This is the expected semantic change,
not a deleted test or 36/36. Positive owner-backed lease authority remains
unavailable.

---

## 3. Product boundary and gaps

The RC-v7→RC-v8 product paths are the expected S1/R1/W-E changes:
`runtime/daemon.py`, `runtime/agent_control_store.py`,
`runtime/owner_policy.py`, `workengine/serveros_authority.py` and the R1
`integrations/fleet-v1-runtime/reconcile_source_gate.py` (plus docs/fixtures).
No `reconcile_source_gate` import in runtime/fleet/workengine and no
`serveros_authority` production import; the R1 gate is not wired. `COMMIT_FENCED`
appears only in the adapter/docs; **no production producer exists**.

Remaining blockers:

1. **`BLOCKED_OWNER_COMMIT_SEAM`**: owner-backed positive is intentionally
   refused; no trusted `COMMIT_FENCED` producer. This is NOT_V1_SIGNED.
2. R1 is `SYNTHETIC_FIXTURE` only; production `BLOCKED_PROVIDER_SOURCE`, no
   real provider echo/owner signature.
3. Real Windows/Dell, real SCM, live OAuth, CLIENT_E2E and full T1–T12
   (T8/T9/T11/T12, cross-device) are **NOT_RUN**.
4. Hosted success is a contract/CI result, not a QA/device signature.
5. S1 controls are 34/36 by re-attribution; R1 v1 probe shape drift is
   documented, not converted to green.

---

## 4. Judgment

All tested LOCAL / REAL_TEMP / hosted-contract gates pass at the exact
published head; the composition is the intended RC-v7 + S1(358→363→364) +
R1(356→361) + X2(367) set with exact patch equality (except #367's expected
context-only cherry-pick). RC-v8 remains **`QA_PARTIAL` / `NOT_RELEASE`**:
positive owner-lease authority is blocked by the absent commit fence, R1 is
synthetic-only, and real Windows/device/OAuth/full T1–T12 gates are absent.

**No QA_SIGNED, no V1 signature, no merge.**

## 5. Receipts

| Run | Result | Log |
|---|---|---|
| 9-file T10/Finalize/Core | 138 passed | `r22-rcv8-nine.log` |
| Core boundary | 17 passed | `r22-rcv8-core-boundary.log` |
| Handoff/admission/expiry/preclaim/context/idempotency | 25/24/10/21/16/12 | `r22-rcv8-qa_probe_*` |
| Core/daemon reconcile | 14 + 29 | `r22-rcv8-qa_probe_r5*`, `r2*` |
| S1 QA2 findings/controls | 6/6; 34/36 | `r22-rcv8-s1-findings.log`, `-controls.log` |
| Owner seam | 10/10 | `r22-rcv8-seam.log` |
| R1 v1 findings/controls | 4 shape mismatch; KeyError | `r22-rcv8-r1-v1-*.log` |
| R1 v2 attacks | 25/25 | `r22-rcv8-r1-v2.log` |
| X2 102 | 102 passed / 0 skip | `r22-rcv8-x2-102.log` |
| X2 targeted | 14 passed | `r22-rcv8-x2-targeted.log` |
| Pre-#367 100P/1F | 1 failed / 100 passed | `r22-rcv8-pre367-101.log` |
| Fleet Next | 286/133 | `r22-rcv8-fleet-next.log` |
| Native CTest/dirfd | 89/0+88/0; 10/10 | `r22-rcv8-native-ctest.log`, `-dirfd.log` |
| REAL_TEMP | PASS/REFUSAL | `r22-rcv8-realtemp.log` |
