# QA-TRIAGE-2026-10-20-ad — RC-v6 fixed-SHA gate

**Status: `QA_PARTIAL` at LOCAL / REAL_TEMP for the tested Linux gates;
`RC_BLOCKED` / `NOT_RELEASE` overall. No QA/V1 signature.** RC-v6 inherits the
known #349 8.3/drive-relative guard P2 and the unresolved real-Windows symlink
privilege failures; neither is a pass.

Lane: independent Q1 QA (#244/#250, register #256). Own detached checkout at
the published GitHub head. No product source, I1/author worktree, production
Fleet/Daemon/Strata/Connector, OAuth or credential was edited.

---

## 1. Exact refs and the `b237c117` / `8620a90` distinction

| Field | Value |
|---|---|
| RC-v6 Draft PR | **#352**, `rc/i1-v6-review-20261020` |
| GitHub PR head / local HEAD | `8620a90e58cf92ba261560b1e4eb5f0797beee09` (local = remote) |
| Requested commit `b237c1178da5ef858ab0df6df191ae2d1082c19c` | **an ancestor**, not the published head |
| `b237c117..8620a90` | docs-only: `integrations/fleet-v1-release/RC-V6-REVIEW-2026-10-20.md`, +26/−2; **no code or test difference** |
| RC-v5 baseline | `c553f11bf1d13036b72a34d4f0c108ff9de75131` |
| Main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` (unchanged) |
| QA checkout | `q1-rcv6-qa-20261020`, detached at `8620a90`, **clean** |
| RC-v5→RC-v6 | 7 files, +971/−88; product path only `runtime/cli.py` (#345), rest tests/docs; no conflict markers |

`gh pr view 352`, `git ls-remote refs/heads/rc/i1-v6-review-20261020` and the
local checkout all report `8620a90`. `b237c117` exists as a docs ancestor
("point RC-v6 pack at the recorded hosted runs") and is byte-identical in code
and tests to the head; I pinned the actual published head rather than silently
substituting or treating an ancestor as the PR tip.

All runs: explicit files, sequential, `systemd-run --user --scope
-p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10`,
hard timeouts.

---

## 2. Independent results at `8620a90`

| Gate | rc | Result | Level |
|---|---|---|---|
| 9-file T10+Finalize+Core, one process, private-path guard | 0 | **138 passed, 0 skipped, 0 xfailed** (84.22 s) | LOCAL |
| S1 handoff: source revoke, target writer, stale fenced, no duplicate | 0 | **25/25** | LOCAL |
| Lease admission: past/future `+08:00`/`Z`, malformed, naive; refused send no provider | 0 | **24/24** | LOCAL |
| Expired grant/lease / `writer_axis` | 0 | **10/10** | LOCAL |
| Preclaim provider/close failure | 0 | **21/21** | LOCAL |
| Actor VERIFIED+FACT / context authority / identity | 0 | **16/16** | LOCAL |
| Idempotency / restart | 0 | **12/12**; cross-device **NOT_RUN** | LOCAL |
| Core/Fabric boundary | 0 | **17 passed** | LOCAL |
| Durable reconcile `source=None` → BLOCKED, no write/no send, single ledger | 0 | **14/14** + daemon **29/29** | LOCAL |
| X2 boundary (LF/CRLF/tamper + synthetic platform skip) | 0 | **51 passed** | LOCAL |
| X2 three files under guard (boundary + grant + legacy) | 0 | **83 passed, 0 skipped** | LOCAL |
| #345 CLI read-only tests | 0 | **10 passed** | LOCAL |
| Fleet Next no-start/owner/isolation probe | 0 | **32/32** | LOCAL |
| Fleet Next offline | 0 | **286 passed, 133 subtests** | LOCAL |
| Native gate/journal | 0 | **89/0 + 88/0; CTest 2/2** | LOCAL |
| Independent native dirfd probe | 0 | **10/10** | LOCAL |
| REAL_TEMP (temp 0700, real daemon/CLI, observe-only) | 0 | **PASS/REFUSAL** | REAL_TEMP |
| #349 guard probe (8.3/drive-relative) | 1 | **28/30, 2 FAIL** (known P2) | LOCAL |
| #345 CLI probe | 1 | **20/21, 1 P3** | LOCAL |

### REAL_TEMP

Fresh `0700` temp registry/state/socket; explicit `--state-dir`/`--socket`;
`--interval 3600`; no `--enable-reconcile`, no default/production socket.
Socket **0600**; `serverosctl status --json` **exit 0**; verify without context
→ `UNTRUSTED_ACTOR`; reconcile without context → `UNTRUSTED_ACTOR`,
`source_called=false`; unsigned grant → `DELEGATION_EVIDENCE_INVALID`; no
`store.json`; SIGTERM stop PASS; temp removed. No production provider/send.

---

## 3. Known issues carried by RC-v6

### P2 — #349 guard: 8.3 / drive-relative ambiguity (test-only)

Independent probe at `8620a90`: **30 checks, 2 FAIL**.

- `C:\PROGRA~1\private\evil` against root `C:\Program Files\private` →
  `_inside=False`, `ambiguous=False`. On an NTFS volume with 8.3 short names
  this is the same file; neither expansion nor conservative refusal.
- `C:..\private\evil` against `C:\work\private` with `windows=True` → no form,
  `ambiguous=False` on a POSIX host. A real Windows host may resolve it via
  per-drive CWD, but this synthetic mode cannot; the guard should refuse when
  it cannot resolve, or document the limit.

These are test-guard limits, not product authority. The README's
"not a filesystem sandbox" scope does not name 8.3/drive-relative.

### Real Windows #349 — NOT a pass

FJPCOS reports at exact #349 `558c324`: boundary **49 passed / 2 failed**
(both unconditional `Path.symlink_to` raising **WinError 1314**, no
`SeCreateSymbolicLinkPrivilege`/Developer Mode); grant **13 skipped / 0 errors**
(NOT APPLICABLE); legacy **19 skipped / 0 errors** (NOT APPLICABLE); #345
read-only tests **10 passed**, `status --json` controlled
`NOT_RUNNING/UNSUPPORTED_PLATFORM` exit 2. RC-v6 inherits #349 unchanged, so
the real-Windows symlink gate remains **unresolved**. This is
`REAL_WINDOWS_READ_ONLY` evidence reported by the device agent, **not rerun
from Tokyo**; no Linux simulation is used as a Windows pass.

### P3 — #345 human output

Human-mode stderr does not contain the literal `UNSUPPORTED_PLATFORM` token;
JSON does. Exit 2 and refusal are correct. Wording only; no authority issue.

### Release blockers unchanged

Positive trusted Producer/OS Grant (`BLOCKED_PRODUCER_BOUNDARY` /
`BLOCKED_TRUST_SOURCE`), T10 finalize/continuation wiring (`BLOCKED_WIRING`),
live CC OAuth (`BLOCKED_OWNER_OAUTH`), Windows Native SCM/REAL_DEVICE,
CLIENT_E2E, full T1–T12 (T8/T9/T11/T12, cross-device) and rollback remain
open. Hosted CI is not used as a real-device signature.

---

## 4. Judgment

The tested Linux LOCAL/REAL_TEMP gates pass at the actual RC-v6 head, and the
only product-path change from RC-v5 is the read-only `runtime/cli.py` status
boundary. RC-v6 is nevertheless **`QA_PARTIAL` / `NOT_RELEASE`**:

1. #349 guard P2 (8.3/drive-relative) is in-tree.
2. Real Windows #349 has 2 failing symlink tests and 32 NOT-APPLICABLE skips;
   RC-v6 has not rerun Windows.
3. Dell RC-v6 not rerun.
4. Trusted producer/OS grant, T10 wiring, live OAuth, real SCM/REAL_DEVICE,
   CLIENT_E2E and full T1–T12 are absent.

**No QA_SIGNED, no merge, no V1_RELEASE.** The author/hosted receipts are not
substituted for this matrix.

## 5. Receipts

| Run | Result | Log |
|---|---|---|
| 9-file T10/Finalize/Core | 138 passed | `r13-rcv6-nine.log` |
| Handoff | 25/25 | `r13-rcv6-qa_probe_r2_handoff.log` |
| Lease admission | 24/24 | `r13-rcv6-qa_probe_r4_lease_admission.log` |
| Expiry | 10/10 | `r13-rcv6-qa_probe_r3_expiry_timezone.log` |
| Preclaim | 21/21 | `r13-rcv6-qa_probe_r3_preclaim_failure.log` |
| Context authority | 16/16 | `r13-rcv6-qa_probe_r3_context_authority.log` |
| Idempotency/restart | 12/12 | `r13-rcv6-qa_probe_r3_idempotency_restart.log` |
| Core boundary | 17 passed | `r13-rcv6-core-boundary.log` |
| Core reconcile | 14/14 | `r13-rcv6-qa_probe_r5_core_reconcile.log` |
| Daemon reconcile | 29/29 | `r13-rcv6-qa_probe_r2_reconcile_312.log` |
| X2 boundary | 51 passed | `r13-rcv6-x2-boundary.log` |
| X2 three files | 83 passed | `r13-rcv6-x2-three.log` |
| Guard probe | 28/30 (2 P2) | `r13-rcv6-guard-probe.log` |
| CLI tests | 10 passed | `r13-rcv6-cli-tests.log` |
| CLI probe | 20/21 (1 P3) | `r13-rcv6-cli-probe.log` |
| Fleet Next probe | 32/32 | `r13-rcv6-qa_probe_r6_fleet_next_gate.log` |
| Fleet Next offline | 286/133 | `r13-rcv6-fleet-next.log` |
| Native CTest | 89/0 + 88/0 | `r13-rcv6-native-ctest.log` |
| Native dirfd | 10/10 | `r13-rcv6-native-dirfd.log` |
| REAL_TEMP | PASS | `r13-rcv6-realtemp.log` |
