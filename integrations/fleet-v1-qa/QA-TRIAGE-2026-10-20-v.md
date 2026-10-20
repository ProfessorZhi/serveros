# QA-TRIAGE-2026-10-20-v — Q1 convergence review: Core boundary, dirfd policy, RC

**Status: `QA_PARTIAL` / `NOT_RELEASE`. No QA signature. No V1 sign-off.**

Lane: `Q1-independent-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T08:1xZ (UTC)**. Every head below was read from GitHub
before its run; each verdict names the exact SHA. The RC-v3 branch is **not
pushed**; RC-v2 is the latest pushed RC.

---

## 1. Pinned inputs

| Candidate | Head SHA | Role |
|---|---|---|
| #312 reconcile gate (RED) | `2a3e1352b442b0a307deba50e293a10c8ef34d61` | Core boundary violation |
| #328 Core-owned caller (GREEN) | `cf32af82c03eb52a4f34e2907e670f23b04a9b57` | boundary + single-store |
| #321 dirfd state (base) | `ab24bcf896a7b35c9661b82ab9c17507bc520ba6` | prior state walk |
| #327 ancestor/identity policy | `5b7ed524fce096fcda6b85a2636cd18d87912343` | group-writable + inode identity |
| #326 portability suites | `3d30c98ed5e0dc7750c8b5a7b9e194c5659d9e3e` | public/private split |
| RC-v2 | `ec43f714c855e07055f32707d4afaebcc5313580` | latest pushed RC |
| RC-v3 | **not pushed** (`rc/*` has only `3f0240d`, `ec43f71`) | **PENDING** |

---

## 2. #328 — Core/Fabric boundary and the reconcile caller

### 2.1 RED on #312

`pytest tests/test_core_ability_boundary.py` at `2a3e1352`:
**1 failed, 15 passed**. The exact failure is
`CoreDoesNotDependOnTheFabric::test_the_core_process_reaches_only_core_directories`
→ `['integrations/v1-effect-reconciliation/effect_reconciliation.py'] != []`.
The daemon path-loaded the integration facade (`runtime/daemon.py` lines
~3326-3339 at #312). This is a real loader edge, not a comment scan.

### 2.2 GREEN on #328

Same test at `cf32af82`: **17 passed**. The daemon no longer references
`integrations`; the Core caller is
`runtime/dispatch_attempts.py#reconcile_attempt`.

### 2.3 Not a whitelist bypass

- `git diff 2a3e1352 cf32af82 -- tests/test_core_ability_boundary.py
  tests/loader_graph.py` shows **only a new negative test** naming the exact
  edge; `CORE_DIRS` is still `('runtime','context','fleet')` and
  `KNOWN_CORE_CROSSINGS` contains no `integrations` entry.
- `runtime/daemon.py` and `runtime/dispatch_attempts.py` at #328 contain no
  `integrations/` loader.
- The new test asserts both the static refs and the loader closure reach no
  `integrations/` path, so widening `CORE_DIRS` or adding a crossing would be
  visible.

### 2.4 Single store and no send

`qa_probe_r5_core_reconcile.py` — **14/14**:

- `reconcile_attempt` exists; Core/facade expose no `send`/`retry`/`dispatch`.
- The facade's `EffectReconciler.reconcile` delegates to the Core; it holds no
  state and opens no store.
- With `observation_source=None`, an EFFECT_UNKNOWN attempt returns
  `BLOCKED_PROVIDER_SOURCE` and the store is **byte-identical before/after**
  (no write).
- A synthetic `CLIENT_TURN_ID` source returning APPLIED yields
  `SETTLE_APPLIED`, one source read, and the **same** `store.json` records
  `SETTLED`; the only other file is `store.json.lock`. No second ledger.
- The synthetic settle is **not provider evidence**.

`qa_probe_r2_reconcile_312.py` on #328: **29/29** (trusted fresh context,
principal/writer from the store, caller authority/observation refused, no
send/retry, production `BLOCKED_PROVIDER_SOURCE`). The S2 suites
`test_effect_reconciliation.py test_dispatch_reconcile_gate.py
test_dispatch_attempts.py` → **88 passed, 19 subtests**.

---

## 3. #327 — group-writable ancestors and opened-inode identity

### 3.1 Focused CTest

Built at `5b7ed524` with the real cmake binary, only
`test_sup_action_gate` + `test_sup_action_journal`, under
`systemd-run` 1G/no-swap/25%/TasksMax64: **2/2 PASS**
(`r5-327-ctest.log`).

### 3.2 Independent negative probe

`q1_state_dir_probe.cpp` is a separate probe linked against the built
`libnodecore_supervisor`, not the author's test file. With #327's hooks it
gives **10/10 PASS**:

- normal path under `/tmp` (1777 sticky root-owned) with our entry: accepted;
- **group-writable non-sticky ancestor**: `STATE_ANCESTOR_GROUP_WRITABLE`;
- **world-writable non-sticky ancestor**: `STATE_ANCESTOR_WORLD_WRITABLE`;
- group-writable **sticky 01770** owned by us + entry owned by us: accepted;
- **parent inode replacement between fstatat and openat**:
  `STATE_PARENT_IDENTITY_CHANGED`;
- **final-file inode switch**: `STATE_PATH_IDENTITY_CHANGED`;
- post-open rename + symlink: the write reports failure, lands in the moved
  inode, and does not follow the symlink.

RED control on #321 (same probe, no hooks/group code): **1 failed, 7 passed** —
the group-writable non-sticky ancestor is `USABLE` (accepted). This is the
residual reported in Snapshot U; #327 closes it.

### 3.3 Scope

Linux `openat`/`renameat` only. **Windows/SCM is NOT_RUN**; no handle-relative
implementation was tested. The sticky third-party-owner branch is not
constructible without root (the PR says so); the probe covers the owner cases
we can construct. No `REAL_DEVICE` claim.

---

## 4. #326 — public-safe portability and the two xfailed

`pytest tests/test_fleet_v1_t10_legacy_compatibility.py
tests/test_v1_grant_os_principal_schema.py
tests/v1_portability/test_portability_boundary.py` at `3d30c98`:
**32 passed, 2 xfailed** (`r5-326-portability.log`).

The two xfailed are explicitly `PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN`:

1. the real signed T10 verifier (`workengine.authority.T10Binding` /
   `VerifiedRunReceipt`) is not in the public tree; the public consumer gate
   uses pinned `finalize_t10_verified.v1.json` / `finalize_unsigned.v1.json`
   fixtures;
2. WE #14's `LeaseGrant` dataclass is not public; the public grant wire fields
   are checked against a pinned `lease_grant.v1.json`.

Judgment: the xfails are **legitimate producer-side isolation**, not hidden
failures. The public **consumer** gate and the wire contract are covered, and
the boundary test fails if `workengine/authority.py` ever ships publicly
(forcing the classification to be revisited). A public equivalent assertion of
the **private producer** would be pretending; no additional public assertion is
required for the consumer, but the positive producer path remains
`PRIVATE_LEGACY_SOURCE_REQUIRED / NOT_RUN` and must not be claimed PASS. No
private source content was read.

---

## 5. RC status — exact-SHA check

### RC-v2 `ec43f71`

`RC_BLOCKED` / `NOT_RELEASE` in its own document. Independent probes at the
exact SHA:

- `tests/test_core_ability_boundary.py` → **1 failed, 15 passed** — the
  `integrations/v1-effect-reconciliation/...` loader is still present; #328 is
  not composed.
- `qa_probe_r4_lease_admission.py` → **9/24 fail** — a past `+08:00`,
  malformed, or naive lease/claim expiry is admitted and **`provider.send` is
  called**; #320 is not composed. RC-v2's own doc says #320 is "not included by
  design" and records the #318 residual.
- `native/node-core/supervisor/src/state_directory.cpp` is **absent**; #321/
  #327 are not composed. #317 was not rebuilt in the RC.

So RC-v2 contains #318 (and #317 source) but **omits #320, #328, #321, #327**;
its T1/T3 admission and Core boundary are still RED. **No QA_SIGNED.**

### RC-v3

Not pushed at the time of this snapshot (`rc/*` = `3f0240d`, `ec43f71`).
**PENDING.** When it is pushed, the exact SHA must be pinned and the same
probes + a test-only REAL_TEMP run (temp state/socket, no production) must be
executed. A new RC must include #320 (admission/typed exceptions), #328 (Core
boundary), #321/#327 (dirfd state) and the remaining slices; otherwise it stays
`RC_BLOCKED`.

---

## 6. Gate matrix / levels

| Item | Result | Level |
|---|---|---|
| Core boundary #312 → #328 | RED 1F/15P → GREEN 17P; no whitelist widening | LOCAL |
| Reconcile caller single store / no send | 14/14 + 29/29 + 88P/19 subtests | LOCAL (synthetic settle) |
| #327 group-writable / identity / sticky | CTest 2/2; independent probe 10/10; #321 control 1F/7P | LOCAL (Linux) |
| #326 portability | 32P/2 xfailed, classified private-legacy | LOCAL |
| RC-v2 | boundary 1F; lease admission 9/24 F | LOCAL exact-SHA |
| RC-v3 | **PENDING** (not pushed) | — |
| REAL_DEVICE / CLIENT_E2E / Windows / cross-device | **NOT_RUN** | — |

No `REAL_DEVICE`, no `CLIENT_E2E`, no Windows/SCM, no cross-device. No
production provider/lease parent; positive lease/T10 paths remain
`BLOCKED_TRUST_SOURCE` where noted.

---

## 7. Findings / blockers

1. **RC-v2 cannot be signed**: it omits #320 (admission fail-open reaching
   `provider.send`), #328 (Core boundary violation), #321/#327 (dirfd state).
   The exact-SHA probes are RED. Its own doc is `RC_BLOCKED`; that is consistent.
2. **RC-v3 PENDING**: no exact SHA to test. A future RC must pin #320/#328/
   #321/#327 and rerun the probes + REAL_TEMP; only then can the
   `LOCAL_ACCEPTANCE` gates be considered.
3. **#328 GREEN**: the boundary fix is real, the caller is in the Core, the
   single store is used, `source=None` writes nothing and sends nothing.
4. **#327 GREEN at LOCAL**: group-writable non-sticky and fstatat/openat
   identity mismatches are refused; sticky 01770 and `/tmp` are accepted; the
   post-open write is fd-bound and fails closed. Windows/SCM remains NOT_RUN.
5. **#326**: the 2 xfailed are legitimate private-producer isolation; the
   public consumer gate is covered; the producer positive path is NOT_RUN.
6. **Carried**: peer-uid assertion in `agent.lease.authority.verify` (Snapshot
   R), T5 payload digest, T4 production source, T8/T9/T11/T12, REAL_DEVICE,
   CLIENT_E2E.

---

## 8. Receipts

| Run | Result | Log |
|---|---|---|
| Boundary RED #312 | 1 failed, 15 passed | `r5-boundary-red312.log` |
| Boundary GREEN #328 | 17 passed | `r5-boundary-green328.log` |
| Boundary RC-v2 | 1 failed, 15 passed | `r5-rcv2-boundary.log` |
| Core reconcile probe #328 | 14/14 | `probe-r5-core-reconcile-green328.log` |
| Daemon reconcile #328 | 29/29 | `r5-reconcile-328.log` |
| S2 suites #328 | 88 passed, 19 subtests | `r5-328-s2-suites.log` |
| #327 CTest | 2/2 PASS | `r5-327-ctest.log` |
| #327 independent probe | 10/10 PASS | `r5-327-negative-probe.log` |
| #321 control probe | 1 failed, 7 passed | `r5-321-negative-probe-red.log` |
| #326 portability | 32 passed, 2 xfailed | `r5-326-portability.log` |
| RC-v2 lease admission | 9/24 fail | `r5-rcv2-lease-admission.log` |

All Python runs used explicit files, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard; the C++ builds used the same guard and only
the focused targets. No production daemon, provider/network/model call, or
production send was performed.

---

## 9. Handback

1. **#328**: GREEN; no new blocker from this audit.
2. **#327**: LOCAL GREEN; no new Linux blocker; Windows/SCM NOT_RUN.
3. **#326**: xfailed classification accepted as private-producer isolation; do
   not claim the producer positive path.
4. **I1 RC**: RC-v2 `ec43f71` is RED at the exact SHA; RC-v3 PENDING. No
   QA_SIGNED until a new RC pins #320/#328/#321/#327 and the probes + REAL_TEMP
   rerun.
5. **#256**: `QA_PARTIAL` stands; no `QA_SIGNED`, no `V1_COMPLETE`, no release.

## 10. What this does not claim

- No REAL_DEVICE, CLIENT_E2E, Windows/SCM, cross-device or cross-process race.
- No production provider/lease parent.
- Author-reported counts are not QA evidence; only the targeted files above
  were re-run.
- The synthetic CLIENT_TURN_ID settle and the private xfails are not positive
  provider evidence.
