# QA-TRIAGE-2026-10-21-aj — RC-v9 exact minimal gate (#371 test-only)

**Status: `QA_PARTIAL` / `NOT_RELEASE`; no QA_SIGNED. RC-v9 is RC-v8 code/test
plus #371 test-only changes and docs. The tested Linux LOCAL / REAL_TEMP gates
pass at the final published head, but the production owner-lease authority is
still `BLOCKED_OWNER_COMMIT_SEAM`, R1 is synthetic-only, and real Windows/Dell,
live OAuth/provider and full T1–T12 are NOT_RUN.**

Lane: independent Q1 QA (#244/#250, register #256). Own detached checkout at
the final GitHub head; no I1/author worktree, product source, production
service, OAuth or credential touched.

---

## 1. Exact refs and the requested `bb68`

| Item | Value |
|---|---|
| RC-v9 Draft PR | **#372**, `rc/i1-v9-review-20261021` |
| Requested `bb68fbfa874b710a7be1050bae6d4e46e1441d97` | **docs ancestor**, not the final head |
| Final published head / local HEAD | **`31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd`** (local = remote) |
| Tested code tip | `dbb1f9f1f7ca5fb38a1591b4171504b21478e71a` (#371) |
| `bb68..31bfc28` | docs-only (`I1-RC-DAG` + `RC-V9-REVIEW`); **no code/test difference** |
| RC-v8 baseline | `a6663075a886093b5ea2e28cd6baf8ca63365f2f` |
| Main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` |
| QA checkout | `q1-rcv9-qa-20261021`, detached `31bfc28`, clean |

`bb68` is an ancestor; `bb68..11fe502` and `11fe502..31bfc28` are docs-only.
The actual final head is `31bfc28`; I pinned that, not the docs ancestor.

### Scope proof

`git diff a666307..31bfc28` names only:

- `tests/probes/s1_win32_collection_probe.py` (+93)
- `tests/test_owner_commit_fence.py` (split/skip)
- `tests/test_owner_commit_fence_adapter.py` (+62, cross-platform)
- `tests/test_owner_policy_binding.py` (module-level nt gate)
- `tests/test_owner_policy_reduced_gate.py` (module-level nt gate)
- the two RC-v9 docs

`git diff --stat a666307..31bfc28 -- runtime workengine context fleet
integrations/fleet-v1-runtime native` is **empty**: runtime, WorkEngine,
integrations and native bytes are identical. There is no silent product change.

---

## 2. Independent results at `31bfc28`

| Gate | rc | Result | Level |
|---|---|---|---|
| Owner suite (3 POSIX modules + cross-platform adapter) | 0 | **28 passed, 1 skipped**; skip is `BLOCKED_MULTIPRINCIPAL_NOT_RUN`; no Linux module-level skip | LOCAL |
| Adapter alone (cross-platform) | 0 | **1 passed** | LOCAL |
| Synthetic-Win32 collector: 3 POSIX modules + adapter | 0 | **SKIPPED 3, ERROR 0, COLLECTED 1**; reasons `POSIX_ONLY`; adapter collects | LOCAL/SYNTHETIC |
| Fourth file `test_v1_grant_epoch_authority.py` at RC-v7 | 1 | **ERROR: No module named 'fcntl'** — pre-existing, not caused by #371 | LOCAL/SYNTHETIC |
| S1 owner seam real daemon/store | 0 | **10/10 GREEN**; positive and revoke-after-read race **0 leases**, `BLOCKED_OWNER_COMMIT_SEAM`; no resolver `BLOCKED_OWNER_POLICY` 0; no authority `BLOCKED_TRUST_SOURCE` 0 | LOCAL / REAL_TEMP socket |
| QA2 S1 original 6 findings | 0 | **6/6 pass** | LOCAL SYNTHETIC |
| R1 independent v2 attacks | 0 | **25/25** | LOCAL SYNTHETIC |
| 9-file T10+Finalize+Core, one process, guard | 0 | **138 passed / 0 skipped** (78.79 s) | LOCAL |
| X2 composed group (boundary 59 + grant 13 + legacy 19 + CLI 10 + isolation 1) | 0 | **102 passed / 0 skipped** | LOCAL/synthetic |
| Fleet Next offline | 0 | **286 passed / 133 subtests** | LOCAL |
| REAL_TEMP | 0 | **PASS/REFUSAL**: socket 0600, status 0, verify/reconcile no-context `UNTRUSTED_ACTOR`, grant issue `BLOCKED_OWNER_POLICY`, no `store.json`, clean stop/removal | REAL_TEMP |

### #371-specific verification

- Three POSIX S1 modules now contain `if os.name == "nt": pytest.skip(...,
  allow_module_level=True)` **before** `runtime/daemon.py`/`fcntl` imports.
  On Linux `os.name != "nt"`: all assertions run; only the pre-existing
  multiprincipal test skips.
- The adapter assertion was split into `test_owner_commit_fence_adapter.py`
  (no daemon, AF_UNIX, SO_PEERCRED, uid or fcntl) and is **COLLECTED** by the
  synthetic-Win32 probe; it also passes alone (1P).
- The synthetic-Win32 collector reports **3 SKIPPED / 0 ERROR**: exactly the
  three POSIX modules; the adapter is not skipped. This is LOCAL synthetic
  evidence, not a real Windows run.
- `test_v1_grant_epoch_authority.py` still errors on `fcntl` under the synthetic
  Win32 environment; the same error is reproduced against RC-v7
  `85c79b2`, so it is pre-existing and not a #371 regression. #371 claims only
  the three S1 modules, not this fourth file.

---

## 3. Reused / not re-run (CODE-TREE EQUIVALENT)

Because runtime/WorkEngine/integrations/native are byte-identical to RC-v8,
these RC-v8 records remain valid as CODE-TREE EQUIVALENT at RC-v9:

- Native `test_sup_action_gate` **89/0**, journal **88/0**, dirfd **10/10**.
- R1 v1 shape-drift observation and independent v2 25/25 (the v2 probe was
  re-run at RC-v9 and is 25/25).
- X2 pre-#367 100P/1F synthetic reproduction (X2 tests and #367 patch are
  unchanged; RC-v9 X2 is 102/0).
- Large core feature batch / 595P light account from the author is not
  independently re-run here; the 138-test T10/Finalize/Core and targeted
  probes above are the independent matrix.

No full pytest batch was run.

---

## 4. Real Windows / blockers

- Real FJPCOS Windows has **NOT_RUN** at `bb68`/`31bfc28`. The RC-v8 device
  read-only receipt is boundary **57P + 2 skips**, R1 **61P**, Grant **13S**,
  Legacy **19S**; the three S1 collection errors are fixed in synthetic-Win32,
  but a real FJPCOS rerun at RC-v9 is still required. The fourth
  `fcntl` collection error is pre-existing (RC-v7), not attributed to #371.
- No production **`COMMIT_FENCED`** producer: owner-backed positive remains
  `BLOCKED_OWNER_COMMIT_SEAM / NOT_V1_SIGNED`.
- No trusted owner signer / provider echo; R1 remains synthetic-only.
- Live OAuth, real SCM, REAL_DEVICE, CLIENT_E2E, full T1–T12 remain BLOCKED.
- Hosted CI and unit green are not device/QA signatures.

## 5. Judgment

`#371` is a clean test-only slice: POSIX skip gates are Windows-only, the
adapter remains cross-platform and runs, no Linux test is silently skipped, and
the RC-v9 product tree is byte-identical to RC-v8. The tested LOCAL / REAL_TEMP
gates pass at the final head. RC-v9 is nevertheless **`QA_PARTIAL` /
`NOT_RELEASE`** for the unresolved production owner-commit fence, trusted
signer/provider, real Windows/device and full T1–T12 gates.

**No QA_SIGNED, no V1 signature, no merge.**

## 6. Receipts

| Run | Result | Log |
|---|---|---|
| Owner suite | 28P/1S | `r24-rcv9-owner-suite.log` |
| Adapter alone | 1P | `r23-rcv9-adapter.log` |
| Synthetic Win32 collector | 3S/0E/1 collected | `r24-rcv9-win32-collector.log` |
| RC-v7 fourth file | ERROR fcntl | `r23-rcv9-rcv7-fourth.log` |
| S1 seam | 10/10 | `r24-rcv9-seam.log` |
| S1 QA2 findings | 6/6 | `r24-rcv9-s1-findings.log` |
| R1 v2 | 25/25 | `r24-rcv9-r1-v2.log` |
| 9-file T10/Finalize/Core | 138P | `r24-rcv9-nine.log` |
| X2 102 | 102P/0S | `r24-rcv9-x2-102.log` |
| Fleet Next | 286/133 | `r24-rcv9-fleet-next.log` |
| REAL_TEMP | PASS/REFUSAL | `r24-rcv9-realtemp.log` |
