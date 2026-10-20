# QA-TRIAGE-2026-10-20-z — RC-v4 independent QA gate

**Status: `PARTIAL PASS` at LOCAL for the gates actually run; `RC_BLOCKED`
for release; **no QA_SIGNED**, no V1 release signature.**

Lane: `Q1-independent-qa`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T09:3xZ (UTC)**.

---

## 1. Exact refs and checkout

| Item | Exact ref |
|---|---|
| RC-v4 Draft PR | #340, branch `rc/i1-v4-review-20261020` |
| Branch head (local = remote = PR head) | `40c4b1c46e5d3f3da883045bfa2152acc41da01a` |
| Tested composition head | `a0b8795e52b52b5d286d265cc8b1b6f327a9d692` |
| `a0b8795` → `40c4b1c` | **docs-only**: `RC-V4-REVIEW-2026-10-20.md`, +162 lines; code identical |
| Base RC-v3 | `e3dc9c7ca0aeca9dd97e981de85da3a727ed9d8a` |
| Authoritative main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` |
| QA checkout | `worktrees/serveros/q1-rcv4-qa-20261020`, detached at `40c4b1c`, **status clean** |
| I1 worktree | **not accessed or modified** |

Composition pins read from #340: #330 `bf1b514`, #332 `a60ae10`, #335 `85b9539`,
#334 `c984f4a` (post-#309 commits only), #338 `96e0fcf`; #325 excluded by the
author due to an add/add conflict. All six cherry-picks are reported clean.

---

## 2. Independent test matrix at `40c4b1c`

All local runs used `systemd-run --user --scope -p MemoryMax=1G
-p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10` and explicit
files/timeouts. No production daemon, provider/network/model send, secrets,
OAuth, service/plugin change, or main merge.

| Gate | Independent result | Level |
|---|---|---|
| T1 handoff: source writer revoked, target lease-backed writer, stale source fenced, no duplicate | **25/25** | LOCAL / REAL_COMPONENTS |
| Lease/claim expiry: past/future `+08:00`/`Z`, malformed, naive; real send admission; refused sends do not call provider | **24/24** | LOCAL |
| Expiry close flag + `writer_axis` | **10/10** | LOCAL |
| Preclaim provider/close error → `BLOCKED_ATOMICITY`, `source_preserved=false`, `scope_stalled=true`, one current writer | **21/21** | LOCAL |
| Context authority / identity: UNVERIFIED/different actor revoked; VERIFIED/FACT control | **16/16** | LOCAL |
| Idempotency / restart | **12/12**; cross-device **NOT_RUN** | LOCAL |
| Core boundary #328 | **17 passed** | LOCAL |
| Durable reconcile: `source=None` → `BLOCKED_PROVIDER_SOURCE`, no write, no send; single `store.json`; synthetic settle | **14/14** + daemon **29/29** | LOCAL |
| T10 + Finalize + core boundary, one pytest process | **134 passed**, 0 failed (84.10s) | LOCAL |
| X2 public authority + import isolation | **42 passed, 0 xfailed** | LOCAL |
| Fleet Next offline (install, dual-stack, canary, refresh, unit receipt, tailscale) | **286 passed, 133 subtests** | LOCAL |
| Fleet Next isolation probe (names/limits/owner/no-start/workflow) | **32/32** | LOCAL |
| `test_agent_mcp.py` (#338 composition) | **72 passed, 177 subtests** | LOCAL |
| Native action gate/journal | **89 + 88 checks, 0 failures**, CTest 2/2 | LOCAL |
| Native dirfd independent probe (group-writable/inode identity/sticky/post-open write) | **10/10** | LOCAL |
| REAL_TEMP: temp 0700 registry/state/socket, observe-only daemon | **PASS** (details below) | REAL_TEMP |

### REAL_TEMP details

`mktemp -d` 0700; explicit registry/state/socket under the temp dir; real
`bin/serverosd --interval 3600`, **no `--enable-reconcile`**, no default
socket; `XDG_RUNTIME_DIR` temp. Observed:

- socket mode **0600**; `serverosctl --json status` **exit 0** (device_ref
  `device:q1-rcv4-temp`);
- `agent.lease.authority.verify` without `context_ref` → `verified=false`,
  `UNTRUSTED_ACTOR`;
- `agent.dispatch.reconcile` without context → `UNTRUSTED_ACTOR`,
  `source_called=false`, `dispatching=false`;
- unsigned `agent.grant.issue` → `DELEGATION_EVIDENCE_INVALID`;
- **no `state/agent-control/store.json`**; SIGTERM stop PASS; temp removed.

No production daemon/service/socket/state was touched.

---

## 3. What was not independently re-run

- The author's **829 passed / 7 xfailed / 287 subtests** core feature batch
  (394s) was not re-run; the targeted matrix above was used instead. That
  count remains author-reported.
- F1 conformance **29 PASS** (FAKE_DEVICE) was not re-run; author-reported.
- Hosted CI runs `39389009969` (Fleet Next, #330) and `39390576739` (Windows,
  #334) are GitHub results at the pinned SHAs; I read the run metadata/logs,
  not a local execution. The Windows result is **HOSTED_WINDOWS_BUILD/UNIT**,
  not `REAL_DEVICE`.
- Dell/Windows real device work and live CC OAuth remain **NOT_RUN**.

---

## 4. Remaining blockers (no release signature)

1. **Positive trusted provider / OS grant**: production lease and T10 positive
   paths remain `BLOCKED_TRUST_SOURCE`; `acquire_lease` remains
   `BLOCKED_PRODUCER_BOUNDARY`. The public/local producers are not production
   authority.
2. **Live CC OAuth / Next ingress**: `BLOCKED_OWNER_OAUTH` / NOT_RUN from the
   prior P1 lane; no live client E2E.
3. **Windows Native SCM / real device**: NOT_RUN; #334 is a hosted MSVC
   contract build and does not call the SCM.
4. **Full T1–T12**: T8/T9/T11/T12 and cross-device are NOT_RUN; only the
   tested gates above are `LOCAL`/`REAL_TEMP` evidence.
5. **Author-only counts**: the 829 core batch and F1 29 are not converted to
   QA evidence.

**Judgment:** the tested RC-v4 gates are a **PARTIAL PASS at LOCAL**; RC-v4
remains `RC_BLOCKED` for release, and no QA/V1 signature is given.

---

## 5. Receipts

| Run | Result | Log |
|---|---|---|
| T1 handoff | 25/25 | `r9-rcv4-qa_probe_r2_handoff.log` |
| Lease admission | 24/24 | `r9-rcv4-qa_probe_r4_lease_admission.log` |
| Expiry | 10/10 | `r9-rcv4-qa_probe_r3_expiry_timezone.log` |
| Preclaim failure | 21/21 | `r9-rcv4-qa_probe_r3_preclaim_failure.log` |
| Context authority | 16/16 | `r9-rcv4-qa_probe_r3_context_authority.log` |
| Idempotency/restart | 12/12 | `r9-rcv4-qa_probe_r3_idempotency_restart.log` |
| Core reconcile | 14/14 | `r9-rcv4-qa_probe_r5_core_reconcile.log` |
| Daemon reconcile | 29/29 | `r9-rcv4-qa_probe_r2_reconcile_312.log` |
| Fleet Next gate | 32/32 | `r9-rcv4-qa_probe_r6_fleet_next_gate.log` |
| Core boundary | 17 passed | `r9-rcv4-core-boundary.log` |
| T10+Finalize+core | 134 passed | `r9-rcv4-t10-finalize-core.log` |
| X2+isolation | 42 passed | `r9-rcv4-x2-isolation.log` |
| Fleet Next offline | 286 passed/133 subtests | `r9-rcv4-fleet-next.log` |
| agent_mcp | 72 passed/177 subtests | `r9-rcv4-agent-mcp.log` |
| Native CTest | 89+88 checks, 0 failures | `r9-rcv4-native-ctest.log` |
| Native dirfd probe | 10/10 | `r9-rcv4-native-dirfd-probe.log` |
| REAL_TEMP | PASS | `r9-rcv4-realtemp.log` |

---

## 6. Handback

1. **#340**: the tested local gates are independently `PARTIAL PASS`; no
   blocker found in the composed code for those gates. Keep Draft; no merge.
2. **#256/#244**: record the exact SHA, the independent receipts and the
   remaining trust/device/OAuth/T1–T12 blockers; no QA/V1 signature.
3. **I1**: do not treat the author's 829 core batch or F1 29 as QA evidence;
   they were not re-run. A future release still requires the positive trust
   producer, live client/real-device E2E, Windows Native SCM and the full
   T1–T12 matrix.

## 7. What this does not claim

- No V1 release, no `QA_SIGNED`, no merge.
- No REAL_DEVICE, CLIENT_E2E, live OAuth or production provider.
- The tested gates are LOCAL/REAL_TEMP only; the hosted Windows result is
  HOSTED_WINDOWS_BUILD/UNIT, not a device measurement.
