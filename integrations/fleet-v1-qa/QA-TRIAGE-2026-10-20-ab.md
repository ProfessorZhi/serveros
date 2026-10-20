# QA-TRIAGE-2026-10-20-ab — RC-v5 fixed-SHA gate

**Status: `QA_PARTIAL` at LOCAL / REAL_TEMP for the tested Linux gates;
`RC_BLOCKED` / `NOT_RELEASE` overall. No QA/V1 signature.** RC-v5 carries the
known-unfixed #342 guard P2 and the measured Windows grant-suite collection
errors; neither is a pass.

Lane: independent Q1 QA (#244/#250, register #256). Independent Session; own
detached worktree at the exact GitHub head. No product source, I1 worktree, X2
author directory, production daemon/service/socket/provider, OAuth or credential
was touched.

---

## 1. Exact refs

| Field | Value |
|---|---|
| RC-v5 Draft PR | **#348**, branch `rc/i1-v5-review-20261020` |
| GitHub head / local HEAD | `c553f11bf1d13036b72a34d4f0c108ff9de75131` (local = remote) |
| Tested code head | `ac4ce82a73196ed83d16f2abc199aad802b60c9a` (`c553f11` is docs-only) |
| RC-v4 baseline | `40c4b1c46e5d3f3da883045bfa2152acc41da01a` |
| Main comparison | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` |
| QA checkout | `worktrees/serveros/q1-rcv5-qa-20261020`, detached, **clean** |
| RC-v5 composition | #342 `c5e320d`, #343 `596011f`, #344 `ac4ce82` on RC-v4 |
| RC-v4→RC-v5 diff | 13 files, +826/−65; **only** `tests/**`, `integrations/fleet-v1-portability/**`, `integrations/fleet-v1-release/**`; no product runtime path; no conflict markers |

All runs: explicit files, sequential, `systemd-run --user --scope
-p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10`,
hard timeouts. Tokyo load was ~42 during the 9-file run, which took 164.68 s
(author: 78.10 s); the count is what matters, not the wall time. No large batch
was run concurrently.

---

## 2. Independent results at `c553f11`

| Gate | Result | Level |
|---|---|---|
| 9-file T10+Finalize+Core, one process, private-path guard | **138 passed, 0 skipped, 0 xfailed** (164.68 s) | LOCAL |
| S1 handoff: source revoke, target lease writer, stale fenced, no duplicate | **25/25** | LOCAL/REAL_COMPONENTS |
| Lease admission/expiry: past/future `+08:00`/`Z`, malformed, naive; refused send never calls provider | **24/24** | LOCAL |
| Expiry `writer_axis` / close flag | **10/10** | LOCAL |
| Preclaim provider/close error → `BLOCKED_ATOMICITY`, one writer | **21/21** | LOCAL |
| Context authority / identity | **16/16** | LOCAL |
| Idempotency / restart | **12/12**; cross-device **NOT_RUN** | LOCAL |
| Durable reconcile `source=None` → BLOCKED, no write/no send; single ledger | **14/14** + daemon **29/29** | LOCAL |
| Fleet Next no-start / owner / isolation probe | **32/32** | LOCAL |
| Fleet Next offline (6 files) | **286 passed, 133 subtests** | LOCAL |
| X2 portability boundary | **37 passed, 0 skipped** | LOCAL |
| Grant negative + import isolation under guard | **14 passed** | LOCAL |
| #344 host-profile tests (`missing`/unreadable → `RootUnbound`) | **2 passed** | LOCAL |
| Explicit T10 refusals (caller JSON, binding, exit disagreement, forged label, exit-1 PASS text) | **5 passed** | LOCAL |
| Native `test_sup_action_gate` / `test_sup_action_journal` | **89 checks / 0 failures + 88 checks / 0 failures; CTest 2/2** | LOCAL |
| Independent native dirfd probe (group-writable, inode identity, post-open write, moved inode, symlink) | **10/10** | LOCAL |
| REAL_TEMP: temp 0700 registry/state/socket, observe-only daemon | **PASS** (below) | REAL_TEMP |

### REAL_TEMP details

Fresh `0700` temp; explicit registry/state/socket; `--interval 3600`; no
`--enable-reconcile`, no default socket. Socket **0600**; `serverosctl --json
status` **exit 0**; `agent.lease.authority.verify` without context →
`UNTRUSTED_ACTOR`; `agent.dispatch.reconcile` without context →
`UNTRUSTED_ACTOR`, `source_called=false`, `dispatching=false`; unsigned
`agent.grant.issue` → `DELEGATION_EVIDENCE_INVALID`; **no `store.json`**;
SIGTERM stop PASS; temp removed. No production socket/service/state.

---

## 3. #342 guard — known-unfixed P2 in RC-v5

RC-v5 contains #342 `c5e320d` but **not** the later #347 `a0053c9` or #349
`558c324`. Re-running the independent guard probe at `c553f11` reproduces the
same **12 failures / 69 checks**:

- extended UNC `\\?\UNC\server\share\private\evil` and `//?/UNC/…` not matched
  against `\\server\share\private`;
- `\\.\D:\owner\private\root\evil` not blocked;
- POSIX `///…` and `////…` classified as device and not blocked — a
  **regression vs RC-v4**, which collapses them and blocks; the real audit hook
  allowed reading a private file through `///…`;
- `//…` bypasses (pre-existing);
- symlink alias to the private root read successfully through the hook (lexical
  design, pre-existing).

This is a test-only guard, not a product authority defect, but it is a **known
P2 carried by RC-v5** and the README §9.2 long-path claim remains too broad.

**Separate check, not RC-v5 evidence:** at #347 `a0053c9836ba9fe42213258830c1a625baf2886d`
my same probe is **69/69 PASS**. #347 explicitly normalizes `\\?\UNC\`,
`\\.\D:`, collapses POSIX slash multiplicity, uses realpath for symlink aliases,
and conservatively refuses ambiguous device namespaces. #349 `558c324` (stacked
on #347) addresses the Windows slice. **Neither is in RC-v5; their PASS cannot
substitute for RC-v5's gate.**

---

## 4. Windows / Dell status

- **Windows at #342 (inherited by RC-v5):** FJPCOS agent reports
  **1 failed / 36 passed**, 19 explicit producer-boundary skips, and **13
  grant-suite setup/collection errors** (`fcntl` imported before the platform
  `skipif`). The remaining failure is the synthetic `/OWNER/PRIVATE/ROOT/evil`
  POSIX case-sensitivity expectation on a Windows host. This was **not rerun
  from Tokyo**; it is `REAL_WINDOWS_READ_ONLY` evidence reported by the device
  agent, not my measurement. #349 fixes the slice but is not in RC-v5.
- **Windows CLI status bug:** Draft #345 `ef8a360` is separate and not in RC-v5.
- **Dell/fjd:** RC-v5 **NOT_RERUN** from Tokyo. RC-v4 was 109 passed / 2 failed
  / 23 errors; #343/#344 target those, but only a Dell retest at the RC-v5 SHA
  can close it.
- **Tokyo Windows execution:** **NOT_RUN** (no Windows device attached to this
  lane). No `REAL_DEVICE` or `CLIENT_E2E` claim.

---

## 5. Judgment and remaining blockers

The tested Linux LOCAL gates and the REAL_TEMP refusal path pass at the exact
RC-v5 SHA; there is no new product-authority change in RC-v5 (only test/doc
commits). However, RC-v5 is **QA_PARTIAL / NOT_RELEASE**, not a signed RC:

1. **Known P2 in-tree:** #342 guard aliases, including the POSIX `///`
   regression; #347/#349 are not in RC-v5.
2. **Windows:** 1 failure + 13 grant collection errors at the #342 slice; RC-v5
   Windows rerun not done; #349 not in RC-v5.
3. **Dell:** RC-v5 not rerun.
4. **Positive trusted producer / OS grant:** `BLOCKED_PRODUCER_BOUNDARY` /
   `BLOCKED_TRUST_SOURCE`; no production lease/T10 positive.
5. **T10 finalize/continuation link:** `BLOCKED_WIRING`.
6. **Live CC OAuth / Next ingress:** `BLOCKED_OWNER_OAUTH` / NOT_RUN.
7. **Real Windows SCM / REAL_DEVICE / CLIENT_E2E:** NOT_RUN.
8. **Full T1–T12:** T8/T9/T11/T12 and cross-device NOT_RUN; 7 V13 monitor
   `xfailed` remain expected gaps.

**No QA_SIGNED, no merge, no release.** The author's 857-pass core batch was not
re-run; this gate used the targeted matrix above.

## 6. Receipts

| Run | Result | Log |
|---|---|---|
| 9-file T10/Finalize/Core | 138 passed | `r11-rcv5-nine.log` |
| Handoff | 25/25 | `r11-rcv5-qa_probe_r2_handoff.log` |
| Lease admission | 24/24 | `r11-rcv5-qa_probe_r4_lease_admission.log` |
| Expiry | 10/10 | `r11-rcv5-qa_probe_r3_expiry_timezone.log` |
| Preclaim | 21/21 | `r11-rcv5-qa_probe_r3_preclaim_failure.log` |
| Context authority | 16/16 | `r11-rcv5-qa_probe_r3_context_authority.log` |
| Idempotency/restart | 12/12 | `r11-rcv5-qa_probe_r3_idempotency_restart.log` |
| Core reconcile | 14/14 | `r11-rcv5-qa_probe_r5_core_reconcile.log` |
| Daemon reconcile | 29/29 | `r11-rcv5-qa_probe_r2_reconcile_312.log` |
| Fleet Next gate | 32/32 | `r11-rcv5-qa_probe_r6_fleet_next_gate.log` |
| X2 boundary | 37 passed | `r11-rcv5-x2-boundary.log` |
| Fleet Next offline | 286/133 | `r11-rcv5-fleet-next.log` |
| Grant + isolation | 14 passed | `r11-rcv5-grant-isolation.log` |
| Host-profile | 2 passed | `r11-rcv5-hostprofile.log` |
| T10 refusals | 5 passed | `r11-rcv5-t10-refusals.log` |
| Native CTest | 89/0 + 88/0 | `r11-rcv5-native-ctest.log` |
| Native dirfd probe | 10/10 | `r11-rcv5-native-dirfd.log` |
| REAL_TEMP | PASS | `r11-rcv5-realtemp.log` |
| #342 guard at RC-v5 | 12 FAIL (known P2) | `r11-rcv5-pr342-guard.log` |
| #347 fix path (separate) | 69/69 PASS, not RC-v5 | `r11-347-guard-fixpath.log` |
