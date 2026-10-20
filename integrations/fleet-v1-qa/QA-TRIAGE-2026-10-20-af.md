# QA-TRIAGE-2026-10-20-af — RC-v7 fixed-SHA gate

**Status: `QA_PARTIAL` at LOCAL / REAL_TEMP for the tested Linux gates;
`RC_BLOCKED` / `NOT_RELEASE` overall. No QA/V1 signature.** RC-v7 is RC-v6 plus
the #353 test-only guard slice; the real-Windows read-only receipt is
56P + 2 skips + 13/19 NOT-APPLICABLE, not a Windows/Full-V1 pass.

Lane: independent Q1 QA (#244/#250, register #256). Own detached checkout at
the published GitHub head; no product source, I1/author worktree, production
Fleet/Daemon/Connector, OAuth or credential was edited.

---

## 1. Exact refs and the requested `a1b78d1f`

| Field | Value |
|---|---|
| RC-v7 Draft PR | **#357**, `rc/i1-v7-review-20261020` |
| Published GitHub head / local HEAD | `85c79b20c62da8db2f4d5c98284a34728b6e8f81` (local = remote) |
| Requested `a1b78d1f6efb2b75d74503cecd98f62ef79fba0f` | **ancestor**, not the PR head |
| `a1b78d1f..85c79b2` | docs-only: `RC-V7-REVIEW-2026-10-20.md`, +20/−3; **no code/test difference** |
| RC-v6 baseline | `8620a90e58cf92ba261560b1e4eb5f0797beee09` |
| Main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` (unchanged) |
| QA checkout | `q1-rcv7-qa-20261020`, detached `85c79b2`, **clean** |
| RC-v6→RC-v7 diff | 4 files: `tests/v1_portability/private_path_guard.py`, `tests/v1_portability/test_portability_boundary.py`, `integrations/fleet-v1-portability/README.md`, `RC-V7-REVIEW-2026-10-20.md`; **no product runtime/Grant/Effect**, no conflict markers |

The #353 cherry-pick `560f715` has the **same patch SHA-256** as PR #353
`21962267` for the three guard/test/README files
(`cb9628dea4c839f562debf05a2a0b276eee6addd26372d6531fb4313d72f63e2`); no
authority-widening change was composed. I pinned the actual published head
rather than treating the docs ancestor as the tip.

All runs sequential, `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
TasksMax=64 / nice 10`, hard timeouts.

---

## 2. Independent results at `85c79b2`

| Gate | rc | Result | Level |
|---|---|---|---|
| 9-file T10+Finalize+Core, one process, private-path guard | 0 | **138 passed / 0 skipped / 0 xfailed** (79.60 s) | LOCAL |
| S1 handoff: source revoke, target writer, lease/epoch | 0 | **25/25** | LOCAL |
| Lease admission: `+08:00`/`Z`, malformed, naive; refused send no provider | 0 | **24/24** | LOCAL |
| Expired grant/lease / timezone / `writer_axis` | 0 | **10/10** | LOCAL |
| Preclaim provider/close failure | 0 | **21/21** | LOCAL |
| Actor VERIFIED+FACT / context authority | 0 | **16/16** | LOCAL |
| Idempotency / restart | 0 | **12/12**; cross-device **NOT_RUN** | LOCAL |
| Core/Fabric boundary | 0 | **17 passed** | LOCAL |
| Durable reconcile `source=None`/UNKNOWN → BLOCKED, no write/no send, single ledger | 0 | **14/14 + daemon 29/29** | LOCAL |
| X2 boundary (LF/CRLF/tamper + platform skip) | 0 | **58 passed / 0 skipped** | LOCAL |
| X2 three files under guard | 0 | **90 passed / 0 skipped** | LOCAL |
| #353 guard probe (WinError/8.3/drive-relative/fixtures) | 0 | **36/36 PASS** | LOCAL/synthetic |
| #345 CLI tests | 0 | **10 passed** | LOCAL |
| #345 CLI probe | 1 | **20/21**, 1 P3 | LOCAL |
| Fleet Next no-start/owner/isolation | 0 | **32/32** | LOCAL |
| Fleet Next offline | 0 | **286 passed / 133 subtests** | LOCAL |
| Native gate/journal | 0 | **89/0 + 88/0; CTest 2/2** | LOCAL |
| Independent native dirfd probe | 0 | **10/10** | LOCAL |
| REAL_TEMP (temp 0700, real daemon/CLI) | 0 | **PASS/REFUSAL** | REAL_TEMP |

### Guard specifics

- WinError 1314 skips **only** on `os.name == "nt"`; `posix`, `winerror=5`,
  and no `winerror` re-raise. Only the two live symlink call sites use the
  helper; Linux runs both (0 skips).
- `C:\PROGRA~1\…`, UNC `PRIVAT~1`, trailing-space/dot and `D:relative\path`
  (two CWDs) are refused conservatively; absolute/sibling/inside and normal
  POSIX paths classify correctly.
- Fixture LF/CRLF pins match; same-length changed field, BOM, mixed EOL and
  lone CR are rejected.
- **Residuals explicit:** `REAL_NTFS_8.3_MAPPING=NOT_RUN`; TOCTOU not closed;
  guard is a check-time test aid, not a filesystem sandbox.

### REAL_TEMP

Fresh `0700` temp; socket **0600**; `status --json` exit 0; verify without
context → `UNTRUSTED_ACTOR`; reconcile without context → `UNTRUSTED_ACTOR`,
`source_called=false`; unsigned grant → `DELEGATION_EVIDENCE_INVALID`; no
`store.json`; SIGTERM stop; temp removed. No production provider/send.

---

## 3. Real Windows / remaining gates

- **Real Windows #353 (inherited unchanged):** FJPCOS reports boundary
  **56 passed + 2 skipped** (`HOST_PRIVILEGE_NO_SYMLINK`, no symlink
  privilege), grant **13 skipped / 0 errors**, legacy **19 skipped / 0 errors**.
  This is `REAL_WINDOWS_READONLY`, **not rerun from Tokyo** and **not a Full
  V1/Windows pass**; the 32 producer-boundary skips are NOT-APPLICABLE.
- **P3 #345:** human stderr omits the literal `UNSUPPORTED_PLATFORM` (JSON has
  it); exit 2 and refusal correct.
- Trusted Producer/OS Grant (`BLOCKED_PRODUCER_BOUNDARY`/
  `BLOCKED_TRUST_SOURCE`), T10 finalize/continuation wiring
  (`BLOCKED_WIRING`), live CC OAuth (`BLOCKED_OWNER_OAUTH`), Windows real
  SCM/REAL_DEVICE, CLIENT_E2E, full T1–T12 and rollback remain open. Hosted CI
  is not used as a device signature.

---

## 4. Judgment

The tested Linux LOCAL/REAL_TEMP gates pass at the actual RC-v7 head; the only
code/test change from RC-v6 is the #353 test-only guard patch, and there is no
product runtime/Grant/Effect change. RC-v7 is still **`QA_PARTIAL` /
`NOT_RELEASE`**:

1. real-Windows symlink privilege remains a host gate (2 skips), not a pass;
2. real NTFS 8.3/trailing mapping and TOCTOU are NOT_RUN;
3. Dell RC-v7 not rerun;
4. trusted producer/OS grant, T10 wiring, live OAuth, real SCM/REAL_DEVICE,
   CLIENT_E2E and full T1–T12 are absent.

**No QA_SIGNED, no merge, no V1_RELEASE.** Author/hosted receipts are not
substituted for this matrix.

## 5. Receipts

| Run | Result | Log |
|---|---|---|
| 9-file T10/Finalize/Core | 138 passed | `r15-rcv7-nine.log` |
| Handoff | 25/25 | `r15-rcv7-qa_probe_r2_handoff.log` |
| Lease admission | 24/24 | `r15-rcv7-qa_probe_r4_lease_admission.log` |
| Expiry | 10/10 | `r15-rcv7-qa_probe_r3_expiry_timezone.log` |
| Preclaim | 21/21 | `r15-rcv7-qa_probe_r3_preclaim_failure.log` |
| Context authority | 16/16 | `r15-rcv7-qa_probe_r3_context_authority.log` |
| Idempotency/restart | 12/12 | `r15-rcv7-qa_probe_r3_idempotency_restart.log` |
| Core boundary | 17 passed | `r15-rcv7-core-boundary.log` |
| Core reconcile | 14/14 | `r15-rcv7-qa_probe_r5_core_reconcile.log` |
| Daemon reconcile | 29/29 | `r15-rcv7-qa_probe_r2_reconcile_312.log` |
| X2 boundary | 58 passed | `r15-rcv7-x2-boundary.log` |
| X2 three files | 90 passed | `r15-rcv7-x2-three.log` |
| Guard probe | 36/36 | `r15-rcv7-guard-probe.log` |
| CLI tests / probe | 10 / 20-21 | `r15-rcv7-cli-tests.log`, `r15-rcv7-cli-probe.log` |
| Fleet Next probe / offline | 32/32; 286/133 | `r15-rcv7-qa_probe_r6_fleet_next_gate.log`, `r15-rcv7-fleet-next.log` |
| Native CTest / dirfd | 89/0+88/0; 10/10 | `r15-rcv7-native-ctest.log`, `r15-rcv7-native-dirfd.log` |
| REAL_TEMP | PASS | `r15-rcv7-realtemp.log` |
