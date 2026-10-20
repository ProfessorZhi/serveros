# QA-TRIAGE-2026-10-20-u — Q1 final acceptance review

**Status: `QA_PARTIAL` / `NOT_RELEASE`. No QA signature. No V1 sign-off.**

Lane: `Q1-independent-acceptance`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T07:2xZ (UTC)**. All heads were read from GitHub before
each run. The S1 line moved again this round (#318 → #320); both the RED
baseline and the GREEN candidate were tested at their exact pushed SHAs. No
uncommitted work was used for a verdict.

---

## 1. Pinned inputs

| Candidate | Head SHA | Role |
|---|---|---|
| #318 (RED baseline) | `2640bcee18bc4bcc58b25c548e8cfb19542f1df9` | prior GREEN; now RED |
| #320 (GREEN) | `d559e17442176e126bb4cf3cef6a074f85190245` | admission instants + typed exceptions |
| #321 (Node Core) | `ab24bcf896a7b35c9661b82ab9c17507bc520ba6` | dirfd-bound state |
| I1 RC-v1 | `3f0240d5ffb2f66edf184cc6b87e04c02c0598ca` | comparison |
| I1 RC-v2 | **not pushed** (no `rc/*` head newer than 3f0240d) | **PENDING** |
| Previous QA | #319 `c04b0e1` | stacked base |

`#320` is a descendant of `#318`; `#321` is stacked on `#317` (native line).

---

## 2. Harness

Same author-test-independent graph: own fake provider service, own requests and
context; real `DaemonBindings` + `DaemonContexts` + `LocalLoopbackTransport` +
`FleetAgentControlRouter` + real `Daemon` over temp state/SQLite. No author
test module imported. Level: **LOCAL / REAL_COMPONENTS / IN_PROCESS_DAEMON**;
no AF_UNIX, no provider, no network.

New probe `qa_probe_r4_lease_admission.py`:

- **A** `coordination.writer_axis` with a fixed `Z` clock and a claim expiry:
  past `+08:00`, past `Z`, future `+08:00`, future `Z`, malformed, and a naive
  timestamp whose **text** sorts later than the clock.
- **B** the real `agent.session.send` admission: a temp `leases` row with the
  same spellings; the binding claim is `agent:A/1`; the probe asserts whether
  the send is refused/admitted and whether the provider's `send` was called.

---

## 3. RED on #318 `2640bce`

### 3.1 Lease/claim expiry at admission — 9 failures

`probe-r4-lease-admission-red318.log` — 24 checks, **9 failed**:

| Case | `writer_axis` | real send | provider.send |
|---|---|---|---|
| past `+08:00` | **CURRENT** (expected EXPIRED) | **admitted** | **called once** |
| past `Z` | EXPIRED | refused | not called |
| future `+08:00` | CURRENT | admitted | called once |
| future `Z` | CURRENT | admitted | called once |
| malformed `not-a-time` | **CURRENT** | **admitted** | **called once** |
| naive later text | **CURRENT** | **admitted** | **called once** |

This is a real effect-boundary fail-open: an expired/malformed/unreadable
lease whose **text** sorts after UTC now is admitted and reaches the provider.
The `+08:00` case is the mixed-timezone bug; the malformed/naive cases are
fail-open parses.

### 3.2 Expiry report and preclaim exception

- `qa_probe_r3_expiry_timezone.py` — 9/10; `admission past_plus8` failed.
- `qa_probe_r3_preclaim_failure.py` — 17/18; a provider `resume` exception
  still escaped `RuntimeError`.
- Context authority 16/16; handoff 25/25; idempotency/restart 12/12.

---

## 4. GREEN on #320 `d559e174`

All probes pass at the exact pushed SHA:

| Probe | Result |
|---|---|
| `qa_probe_r4_lease_admission.py` | **24/24** — writer_axis and the real send admission are instant-correct for past/future `+08:00`/`Z`, malformed and naive; refused sends do **not** call `provider.send` |
| `qa_probe_r3_expiry_timezone.py` | **10/10** |
| `qa_probe_r3_preclaim_failure.py` | **21/21** — target `resume` raise, `resume {}`, and close exception all return `BLOCKED_ATOMICITY`, `source_preserved=false`, `detail.scope_stalled=true`, one current writer (the target); the raised case now carries `detail.error`/`provider_reason` |
| `qa_probe_r3_context_authority.py` | **16/16** |
| `qa_probe_r3_idempotency_restart.py` | **12/12**; cross-device NOT_RUN |
| `qa_probe_r2_handoff.py` | **25/25** |

No duplicate writer in any scenario; no provider `send` on a refused admission.
Author tests `test_handoff_daemon_bindings.py tests/test_v1_write_fence.py` →
60 passed, 2 subtests (`r4-320-author-tests.log`; context only).

**Residual (design, not a fail-open):** after a preclaim the target remains the
unique current writer while the source is `OPEN` but stale; `BLOCKED_ATOMICITY`
and `scope_stalled=true` are reported and repair is an owner action. The target
may still attempt a send while its provider resume failed. This is the
documented no-rollback contract, not a double write or a success report.

---

## 5. #321 Node Core — dirfd-bound state

Built at `ab24bcf` with the real cmake binary, only the affected targets
(`test_sup_action_gate`, `test_sup_action_journal`), under
`systemd-run` 1G/no-swap/25%/TasksMax64. **LOCAL PASS: 2/2**
(`r4-321-ctest.log`, build logs alongside).

Source review:

| Property | Result |
|---|---|
| walk from `/` with `openat(O_DIRECTORY|O_NOFOLLOW)`, `fstatat`+`fstat` each inode | PASS |
| refuses `.`/`..`, symlink at any level, non-directory, missing component | PASS |
| refuses world-writable ancestor without sticky; immediate parent must be euid-owned and not group/other-writable | PASS |
| state **read** via `openat(dirfd, basename, O_RDONLY|O_NOFOLLOW)` | PASS (`test_the_read_is_bound_to_the_opened_directory`: the renamed-in evil empty state is not read) |
| write: `openat(dirfd, temp, O_CREAT|O_EXCL|O_NOFOLLOW, 0600)`, write, fsync, `renameat(dirfd,…,dirfd,…)`, `fsync(dirfd)` | PASS |
| post-write stability `fstat(dirfd)` vs `lstat(parent_path)`; mismatch fails closed | PASS (`test_the_write_is_bound_to_the_opened_directory`: the write lands in the moved inode, not the symlink target, and the journal reports failure) |
| **group-writable ancestor above the immediate parent** | **RESIDUAL**: ancestors are only checked for `S_IWOTH` without sticky (`state_directory.cpp:99,232,271`); `S_IWGRP` is not checked above the parent. A group member can rename an intermediate component between the `fstatat` and `openat`; the code does not compare `entry.st_dev/st_ino` with `opened.st_dev/st_ino`, so a non-symlink directory swap is not detected. The fd-bound write/read and post-write stability still protect the **post-open** swap, and the final parent must be euid-owned 0700, which narrows the impact; but the walk itself is not identity-bound. |
| Windows / SCM | **NOT_RUN** — this is Linux `openat`/`renameat`; the Windows handle-relative path is not implemented. No REAL_DEVICE claim. |

The original path-string TOCTOU is substantially closed for post-open swaps;
the group-writable-ancestor walk residual remains and should be stated, not
hidden.

---

## 6. RC-v2 status and SHA comparison

- Remote `rc/*` has only `rc/i1-review-20261020` @ `3f0240d` (**RC-v1**).
  **No RC-v2 is pushed**; the I1 agent's RC-v2 is **PENDING**.
- RC-v1 tree check: no `DaemonBindings.close/claim`, no `agent.binding.claim`,
  no `action_journal.cpp`/`state_directory.cpp`. It predates and does not
  contain **#315/#318/#320** (S1) or **#317/#321** (Node Core). Its own doc
  says "T1 handoff fix: no PR exists".
- Therefore **no QA_SIGNED**. A new RC must pin `#320@d559e174` and
  `#321@ab24bcf` (or later exact SHAs), include the remaining security slices,
  and rerun these probes. A future RC that omits them is not a candidate.

---

## 7. Acceptance levels

| Scope | Level | Notes |
|---|---|---|
| S1 handoff / admission / atomicity probes | **LOCAL_ACCEPTANCE (probe)** | real components, in-process daemon; 25/25 + 24/24 + 21/21 + 16/16 + 12/12 |
| Node Core #321 focused tests | **LOCAL PASS** | 2/2 CTest; Linux only |
| REAL_DEVICE (Tokyo→Dell) | **NOT_RUN** | no trusted transport |
| CLIENT_E2E (Claude Code plugin) | **NOT_RUN** | no client run |
| Windows / SCM | **NOT_RUN** | no Windows machine; Linux dirfd only |
| Cross-process race | **NOT_RUN** | single-threaded daemon; synthetic post-open race only |
| Production parent / lease producer | **BLOCKED_TRUST_SOURCE** | unchanged |

`LOCAL_ACCEPTANCE` is not `V1_COMPLETE`; it is a probe-level result on the
named SHAs.

---

## 8. Gate matrix (final)

| Gate | Status | Level |
|---|---|---|
| T1 successful handoff | GREEN on #320 (25/25) | LOCAL |
| T1 post-preclaim failure | `BLOCKED_ATOMICITY`/`scope_stalled`, unique writer; typed exception | LOCAL |
| T2 one writer | GREEN | LOCAL |
| T3 expired/revoked | aware-instant admission; refused sends do not reach provider | LOCAL |
| T4 unknown effect | #312 gate; production `BLOCKED_PROVIDER_SOURCE` | LOCAL/SOURCE |
| T5 key+payload | NOT DONE (S2 #252) | — |
| T6 expired row | CODE/LOCAL PASS | LOCAL |
| T7 cross-user/grant | context authority GREEN; peer-uid assertion from #272 still not an authorization | LOCAL/PARTIAL |
| T10 child PASS vs tests | GREEN (#304) | LOCAL |
| Race / idempotency / restart | idem/restart GREEN; cross-process race NOT_RUN | LOCAL/NOT_RUN |
| Cross-device | NOT_RUN | — |
| Node Core #321 | focused LOCAL PASS; group-writable ancestor residual | LOCAL |

No REAL_DEVICE or CLIENT_E2E evidence exists.

---

## 9. Findings / blockers to carry

1. **P2 residual (#321)**: group-writable ancestors are not refused; the
   `fstatat`→`openat` pair does not compare inode identity. The post-open
   TOCTOU is closed; the walk identity gap remains. Suggest refusing
   `S_IWGRP` ancestors (or requiring sticky) and comparing
   `entry.st_dev/st_ino` with `opened.st_dev/st_ino` (or `openat2` with
   `RESOLVE_NO_SYMLINKS`). No Windows/SCM claim.
2. **P2 residual (#320)**: preclaim no-rollback is deliberate; the target can
   still send while its provider resume failed and the scope is stalled. The
   handoff reports it; an owner-approved repair path is still needed.
3. **Blocker (RC)**: no RC-v2 is pushed; RC-v1 lacks #320/#321 (and the whole
   S1/native security chain). No QA_SIGNED.
4. **Carried from Snapshot R**: `agent.lease.authority.verify` still asserts
   `SO_PEERCRED` without authorizing it against the grant/principal. This was
   not touched by #320/#321 and remains open.
5. **Carried**: T5 payload digest (S2 #252), T4 production source, T8/T9/T11/
   T12, REAL_DEVICE, CLIENT_E2E — all still NOT_RUN/NOT_DONE.

---

## 10. Receipts

| Run | Result | Log |
|---|---|---|
| Lease admission RED #318 | 24 checks, 9 failed | `probe-r4-lease-admission-red318.log` |
| Lease admission GREEN #320 | **24/24** | `qa_probe_r4_lease_admission-green320.log` |
| Expiry RED #318 | 9/10 | `qa_probe_r3_expiry_timezone-red318-round4.log` |
| Expiry GREEN #320 | **10/10** | `qa_probe_r3_expiry_timezone-green320.log` |
| Preclaim RED #318 | 17/18 | `qa_probe_r3_preclaim_failure-red318-round4.log` |
| Preclaim GREEN #320 | **21/21** | `qa_probe_r3_preclaim_failure-green320.log` |
| Context authority #320 | **16/16** | `qa_probe_r3_context_authority-green320.log` |
| Idempotency/restart #320 | **12/12** | `qa_probe_r3_idempotency_restart-green320.log` |
| Handoff #320 | **25/25** | `qa_probe_r2_handoff-green320.log` |
| #320 author tests | 60 passed, 2 subtests | `r4-320-author-tests.log` |
| #321 CTest | **2/2 PASS** | `r4-321-ctest.log` + build logs |

All runs used explicit files, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard. No large suite, full C++ build, provider/
network/model call, serverosd start or production send was performed.

---

## 11. Handback

1. **#320**: the two Snapshot T residuals are independently GREEN. Carry the
   preclaim no-rollback operational note to the owner.
2. **#321**: Linux focused LOCAL PASS; group-writable ancestor walk residual
   remains; no Windows/SCM claim.
3. **I1**: RC-v2 PENDING; RC-v1 is `RC_BLOCKED` and omits these slices. No
   QA_SIGNED until a new RC pins #320/#321 (and the remaining security chain)
   and the probes rerun.
4. **#256**: `QA_PARTIAL` stands; no `QA_SIGNED`, no `V1_COMPLETE`, no release.

## 12. What this does not claim

- No REAL_DEVICE, CLIENT_E2E, Windows/SCM, cross-device or cross-process race.
- No production provider/lease parent; the positive paths remain
  `BLOCKED_TRUST_SOURCE` where noted.
- Author-reported counts are not QA evidence; only the targeted files above
  were re-run.
