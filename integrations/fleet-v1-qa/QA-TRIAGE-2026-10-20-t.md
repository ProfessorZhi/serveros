# QA-TRIAGE-2026-10-20-t — Q1 final-gate independent audit

**Status: `QA_PARTIAL` / `NOT_RELEASE`. No QA signature.**

Lane: `Q1-independent-acceptance`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes/logs under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T06:55Z (UTC)**. Every head below was read from GitHub
immediately before its run; the S1 branch moved during this round (#315 was
tested at `7d35535`, then #318 appeared at `2640bce` and was tested as the real
pushed HEAD). No uncommitted S1 work was used for a verdict.

---

## 1. Pinned inputs

| Candidate | Head SHA | Role |
|---|---|---|
| #315 handoff adapter | `7d35535b53f791513a10b28554b4bd79e943e77a` | RED baseline |
| #318 actor+atomicity+expiry | `2640bcee18bc4bcc58b25c548e8cfb19542f1df9` | GREEN candidate (Draft, stacked on #315) |
| #317 Node Core attempt-before-effect | `cb38ec6fe039c7ba85c8e73f1f7c3e351edaf235` | independent spot-check |
| I1 review RC | `3f0240d5ffb2f66edf184cc6b87e04c02c0598ca` | comparison |
| #303 RC docs | `c0fd51285ee213266bb241baa2976ee7438cfe03` | reference |
| Previous QA (Snapshot S) | #316 `7369e2a` | stacked base |

`#318` is a descendant of `#315`; `#315` is stacked on `#307`, which contains
`#272`/`#241`. `#317` is stacked on `#314` (a different native-core line).

---

## 2. Harness and probes

The same author-test-independent graph as Snapshot S: own fake provider
service, own requests and context; real `DaemonBindings` + `DaemonContexts` +
`LocalLoopbackTransport` + `FleetAgentControlRouter` + real `Daemon` over temp
state/SQLite. No author test module imported. Level: **LOCAL /
REAL_COMPONENTS / IN_PROCESS_DAEMON**. No AF_UNIX, no provider, no network.

New `qa_probe_r3_*.py` probes:

- `qa_probe_r3_context_authority.py` — fresh non-FACT contexts and claim/close;
- `qa_probe_r3_preclaim_failure.py` — preclaim then target/provider/close
  failure; state, unique writer, honest report;
- `qa_probe_r3_expiry_timezone.py` — mixed-timezone `expires_at` at the close
  flag and at admission (`writer_axis`);
- `qa_probe_r3_idempotency_restart.py` — repeat close/claim/handoff + restart;
- the Snapshot S `qa_probe_r2_handoff.py` (25 named checks) is re-run.

---

## 3. RED on #315 `7d35535`

### 3.1 Context authority (P1)

`probe-r3-context-authority-red-315.log` — 16 checks, **10 failed**:

| Context (all fresh) | claim | close(successor) |
|---|---|---|
| VERIFIED/FACT (control) | PASS (claimed) | PASS (closed) |
| BLOCKED/INFERRED | **FAIL (claimed=True)** | **FAIL (closed=True)** |
| UNSUPPORTED/INFERRED | **FAIL** | **FAIL** |
| BLOCKED/UNKNOWN | **FAIL** | **FAIL** |
| UNSUPPORTED/UNKNOWN | **FAIL** | **FAIL** |
| VERIFIED/UNKNOWN | **FAIL** | **FAIL** |
| stale | PASS (UNTRUSTED_ACTOR) | PASS |
| missing | PASS | PASS |

A context that does not carry `provenance=FACT` and `provider_status=VERIFIED`
still minted/transferred write authority. This is the Snapshot S P1, now
including `provenance=UNKNOWN`.

### 3.2 Preclaim then failure (P1)

`probe-r3-preclaim-failure-red-315.log` — 12 checks, 3 failed. In all three
scenarios the target claim (`agent:B/2`) became `current_writer` while the
source stayed `OPEN` (`agent:A/1`) and its send was refused. The handoff
reported `source_preserved=true`, which was **false** (the source was not the
current writer):

| Scenario | handoff | source_preserved | current writer |
|---|---|---|---|
| A target resume returns `{}` | `INVALID_CONTEXT` | `true` (**wrong**) | target `agent:B/2` |
| B target resume raises | **RuntimeError escapes** | — | target `agent:B/2` |
| C close disconnects | `NOT_CURRENT_WRITER` | `true` (**wrong**) | target `agent:B/2` |

No double write: exactly one current writer in each. The defect is the false
report and the escaping exception.

### 3.3 Mixed-timezone expiry

`probe-r3-expiry-timezone-red-315.log` — 10 checks, 3 failed:

- `source_claim_expired` for a past `+08:00` claim: expected `true`, actual
  `false` (text comparison).
- `writer_axis` at admission with the fixed-width `Z` clock: a past `+08:00`
  claim read `CURRENT`, expected `EXPIRED`.
- `source_claim_expired` for a `Z` claim at the same second as an
  `_iso` clock with `+00:00`: expected `true`, actual `false`.

### 3.4 Idempotency / restart

`probe-r3-idem-restart-315.log` — **12/12 PASS**: repeat close is a non-transfer,
repeat claim is refused, repeated handoff does not report success and keeps one
writer, restart preserves the CLOSED source and target claim, stale source is
fenced, target is admitted. Cross-device: **NOT_RUN**.

### 3.5 Main handoff

`qa_probe_r2_handoff.py` on #315 — **25/25 PASS** (the Snapshot S P0 remains
resolved for the successful path).

---

## 4. GREEN on #318 `2640bce` (real pushed HEAD)

### 4.1 Context authority — fixed

`qa_probe_r3_context_authority-green-318.log` — **16/16 PASS**: all five
non-FACT contexts (BLOCKED/UNSUPPORTED × INFERRED/UNKNOWN, and VERIFIED/UNKNOWN)
are refused `UNTRUSTED_ACTOR` for both claim and close (successor); the
VERIFIED/FACT control still claims/closes; stale/missing refused. The
`_trusted_actor_for_mutation` helper uses `actor.authorizes`.

### 4.2 Handoff success path — still GREEN

`probe-r3-handoff-green-318.log` — **25/25 PASS**, `lease_backed=true`: source
CLOSED, stale source `THREAD_CLOSED_FOR_NEW_WORK`, target claim from the live
lease, target current and able to send, T2 holds, caller-chosen writer refused.

### 4.3 Preclaim then failure — honest outcome, one exception gap

`probe-r3-preclaim-failure-green-318.log` — 18 checks, **1 failed**:

- A (target resume `{}`): `BLOCKED_ATOMICITY`, `source_preserved=false`,
  `detail.scope_stalled=true`, unique current writer = target. **PASS.**
- C (close disconnects): `BLOCKED_ATOMICITY`, `source_preserved=false`,
  `detail.scope_stalled=true`, unique current writer = target. **PASS.**
- **B (target resume raises): `RuntimeError` still escapes the handoff.** No
  `BLOCKED_ATOMICITY`, no `scope_stalled` report. The state is the same stalled
  split (target current, source OPEN/not current), but the requirement to
  "output the state" is not met. **FAIL (P1/P2 residual).**

No double write in any scenario. #318 deliberately does not roll the target
claim back (the live lease names the target); the stalled scope is reported for
owner repair.

### 4.4 Mixed-timezone expiry — close flag fixed, admission residual

`qa_probe_r3_expiry_timezone-green-318.log` — 9/10 PASS:

- The close-route `source_claim_expired` now compares aware instants: past
  `+08:00`, past `Z`, same-second `Z`, future `+08:00`, future `Z` all match
  expected. **Fixed.**
- **Residual**: `runtime/coordination.py:366` (`writer_axis`) still uses
  `str(expires) <= str(now)`. A past `+08:00` claim reads `CURRENT` at
  admission, expected `EXPIRED`. The same text comparison also exists at
  `:557` for the Work-lease expiry. The close flag is fixed; the admission
  reader is not.

### 4.5 Idempotency / restart — GREEN

`qa_probe_r3_idempotency_restart-green-318.log` — **12/12 PASS**; cross-device
**NOT_RUN**.

### 4.6 Author tests (context, not QA evidence)

`tests/test_handoff_daemon_bindings.py tests/test_v1_write_fence.py` →
**55 passed** (`r3-318-handoff-fence-tests.log`). The PR body's larger counts
were not re-run.

---

## 5. #317 Node Core — independent spot-check

Built at `cb38ec6` with the real cmake binary, targeted targets only
(`test_sup_action_gate`, `test_sup_action_journal`), under the 1G/no-swap/25%
guard. **LOCAL PASS: 2/2 tests** (`r3-317-ctest.log`, build logs alongside).

Source review:

| Property | Result | Evidence |
|---|---|---|
| Prewrite attempt is durable before the provider call | PASS | `execute_effect` calls `prewrite_attempt` then provider; the fake provider asserts `durable_attempt_seen` |
| Prewrite failure issues no effect, exit 2 | PASS | unwritable dir test; `prewrite_failed`, `provider_called=false`; `main` sets `state_write_failed` and returns 2 |
| Crash after prewrite/effect keeps the attempt | PASS | test drops the in-memory ledger; reload has one attempt, `BUDGET_EXHAUSTED` |
| Outcome write failure keeps the attempt, no blind retry | PASS | test + `main` logs "outcome was not persisted", exits 2 |
| Intermediate symlink and `..` refused | PASS | `check_state_path` lstat-walks ancestors; tests `link/sub/state.json`, `real/../state.json` |
| **dirfd-anchored resolution / TOCTOU** | **NOT_CLOSED (blocker)** | `atomic_replace` uses path-based `mkstemp`/`rename`; the dirfd at `action_journal.cpp:78` is opened only for `fsync`; `check_state_path` is lexical + `lstat`. A directory can be swapped to a symlink between the check and the write. No `openat`/`renameat`/`RESOLVE_BENEATH`. The PR body states this blocker itself. |

`O_NOFOLLOW` is used on the **final** path for an existing file, so a final
symlink is refused; the intermediate/ancestor swap remains. The functional
prewrite/outcome properties are a real improvement and pass locally; the
TOCTOU is not "彻底完善".

---

## 6. I1 RC comparison — `3f0240d`

The RC is **`RC_BLOCKED` / `NOT_RELEASE`**, `signature=null`, and explicitly
excludes #307, the #295–#304 WE stack, and #305/#300/#255. Its composition pins
#275, #312, #277, #309, **#314**, #311, #301, #303.

Tree check at `3f0240d`:

- `mcp/agent_mcp.py` has **no** `DaemonBindings.close`/`claim`;
- `runtime/daemon.py` has the **old** `agent.binding.close` and **no**
  `agent.binding.claim`;
- `native/node-core/supervisor/src/action_journal.cpp` does **not exist**.

So the RC predates and does not contain:
**#315/#318 (T1 handoff fix) or #317 (Node Core crash-consistency)**. Its own
doc says "T1 handoff fix: no PR exists" and marks T1 `BLOCKED_P0`. Per the
round rule, the RC **cannot be QA_SIGNED**; it is not a composition of the
later security patches. A future RC must re-pin and recompose #307/#315/#318/
#317 (and the WE line) and rerun the independent probes.

---

## 7. Gate matrix (final-gate round)

| Gate | #315 | #318 (pushed) | Evidence level |
|---|---|---|---|
| T1 stale handoff, successful path | LOCAL GREEN (25/25) | LOCAL GREEN (25/25) | LOCAL/REAL_COMPONENTS |
| T1 post-preclaim failure | false `source_preserved`, exception escapes | `BLOCKED_ATOMICITY`/`scope_stalled`, unique writer; **provider exception still escapes** | LOCAL |
| T2 one writer | LOCAL PASS | LOCAL PASS | LOCAL |
| T3 expired/revoked | lease-backed admission; expiry residual | same; close flag instant-fixed | LOCAL |
| T4 unknown effect | #312 gate; production `BLOCKED_PROVIDER_SOURCE` | unchanged | LOCAL/SOURCE |
| T5 key+payload | NOT DONE (S2 #252) | unchanged | — |
| T6 expired row | CODE/LOCAL PASS | unchanged | LOCAL |
| T7 cross-user/grant | non-FACT context could claim/close | **fixed** (16/16); admission expiry residual | LOCAL |
| T10 child PASS vs tests | GREEN (#304) | unchanged | LOCAL |
| Race / concurrent claim | single-threaded in-process; cross-process **NOT_RUN** | same | — |
| Idempotency / restart | 12/12 PASS | 12/12 PASS | LOCAL |
| Cross-device | **NOT_RUN** (no second device/provider) | **NOT_RUN** | — |
| #317 TOCTOU | **NOT_CLOSED** | n/a (different line) | LOCAL/SOURCE |

No `REAL_DEVICE`, no `CLIENT_E2E`. Lease/T10 positive paths remain
`BLOCKED_TRUST_SOURCE` in production.

---

## 8. Findings

### F1 — P1/P2 — #318: a provider exception after the preclaim escapes the handoff

`qa_probe_r3_preclaim_failure.py` scenario B: `service.resume` raises
`RuntimeError`; `router.handoff` propagates it. The target claim is already
current, the source is `OPEN`/not current, and the caller gets no
`BLOCKED_ATOMICITY`/`scope_stalled` answer. The state is still a single writer,
so this is not a double write; it is an unreported stalled scope and an
unhandled exception at a procedure whose contract is "no is an answer".

Suggested: wrap the post-preclaim target route in the same exception-to-
`BLOCKED_ATOMICITY` path (detail: exception type, `source_preserved=false`,
`scope_stalled=true`) and add the test.

### F2 — P2 — #318: admission `writer_axis` still compares expiry as text

`runtime/coordination.py:366` uses `str(expires) <= str(now)`. A past `+08:00`
claim reads `CURRENT` at admission (expected `EXPIRED`). The same text compare
exists at `:557` for the Work-lease expiry. The close route's
`source_claim_expired` was fixed to instant comparison; the admission reader
was not. Use the `_instant` parser (as #307 did for the grant read) or pin the
claim/lease expiry to the fixed-width `Z` form at write time. This is P2
because the new lease-backed claims carry no `expires_at` (the live lease is
the primary authority), but legacy/explicit claims are affected.

### F3 — Blocker — #317: intermediate-symlink/dirfd TOCTOU remains

`check_state_path` is lexical + `lstat`; `atomic_replace` uses path-based
`mkstemp`/`rename`. A directory component can be replaced by a symlink between
the check and the write. `O_NOFOLLOW` protects only the final existing file;
the `dirfd` in `action_journal.cpp` is for `fsync`, not for anchored
resolution. Functional prewrite/crash/outcome tests are LOCAL PASS; the TOCTOU
is **NOT_CLOSED** and is the remaining blocker for "彻底完善". The PR body
states it; this audit confirms it from source.

### F4 — Blocker — the I1 RC `3f0240d` does not contain the later security patches

The RC is `RC_BLOCKED`/`NOT_RELEASE` and predates #315/#318/#317 (tree check in
§6). No QA_SIGNED; a new RC must re-pin and recompose.

### F5 — Note — the preclaim orphan is a deliberate no-rollback design

#318 reports `BLOCKED_ATOMICITY`/`scope_stalled=true` and leaves the target as
the unique current writer while the source is OPEN but stale. The live Work
lease names the target, so deleting the claim would not restore the source;
repair is an owner action. This is honest under #318's contract (no double
write, no false success), but the target may still attempt a send while its
provider resume failed. Record it as an operational residual, not a QA PASS.

---

## 9. Receipts

| Run | Result | Log |
|---|---|---|
| Context authority RED #315 | 16 checks, 10 failed | `probe-r3-context-authority-red-315.log` |
| Context authority GREEN #318 | **16/16** | `qa_probe_r3_context_authority-green-318.log` |
| Preclaim failure RED #315 | 12 checks, 3 failed | `probe-r3-preclaim-failure-red-315.log` |
| Preclaim failure GREEN #318 | 18 checks, **1 failed** (F1) | `probe-r3-preclaim-failure-green-318.log` |
| Expiry RED #315 | 10 checks, 3 failed | `probe-r3-expiry-timezone-red-315.log` |
| Expiry GREEN #318 | 10 checks, **1 failed** (F2) | `qa_probe_r3_expiry_timezone-green-318.log` |
| Idempotency/restart #315 | 12/12 | `probe-r3-idem-restart-315.log` |
| Idempotency/restart #318 | 12/12 | `qa_probe_r3_idempotency_restart-green-318.log` |
| Handoff #318 | 25/25 | `probe-r3-handoff-green-318.log` |
| #318 author tests | 55 passed | `r3-318-handoff-fence-tests.log` |
| #317 C++ tests | **2/2 PASS** | `r3-317-ctest.log` + build logs |

All Python runs used explicit files, `timeout`, and the `systemd-run`
1G/no-swap/25% CPU/nice-10 guard where the run was more than a probe. No large
suite, C++ full build, provider/network/model call, serverosd start or
production send was performed. Cross-device and cross-process race are
**NOT_RUN**.

---

## 10. Handback

1. **#318**: F1 (provider exception after preclaim) — convert to
   `BLOCKED_ATOMICITY`; F2 — use instant comparison in `writer_axis`/lease
   expiry. The context-authority P1 is fixed and the successful transfer is
   GREEN at 25/25.
2. **#317**: functional prewrite/outcome LOCAL PASS; dirfd TOCTOU is the
   remaining blocker; do not call the path "complete".
3. **I1 RC**: `3f0240d` is stale and `RC_BLOCKED`; no QA_SIGNED until a new RC
   includes #307/#315/#318/#317 and the independent probes are rerun.
4. **#256**: `QA_PARTIAL` stands; no `QA_SIGNED`, no `V1_COMPLETE`, no release.

## 11. What this does not claim

- No REAL_DEVICE or CLIENT_E2E evidence; no cross-device transfer, no
  cross-process race.
- No provider, network, credential, Feishu or model request; no production
  service start/reload.
- The author-reported regression counts are not converted to QA evidence; only
  the targeted files above were re-run.
- The successful same-store handoff is LOCAL/REAL_COMPONENTS, not a production
  lease producer.
