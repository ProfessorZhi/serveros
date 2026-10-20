# QA-TRIAGE-2026-10-20-ag — QA2 re-run: S1 #358 / R1 #356 RED confirmed

**Status: `QA_PARTIAL` / `NOT_RELEASE`. The QA2 findings are independently
reproduced at the exact PR heads (S1 6/6, R1 8/8; 2 P1 findings each, no P0),
and both downstream fix PRs now pass the same attack set at their exact SHAs:
S1 #363 `8840f41` and R1 #361 `6a27204`. **A separate critical consumer seam is
RED at #363: `verified:true` with `atomicity:BLOCKED_OWNER_COMMIT_SEAM` is still
consumed by `OwnerVerifiedLease.acquire`, which writes a new lease after a
post-read policy revoke. This is `BLOCKED_OWNER_COMMIT_SEAM` / `NOT_V1_SIGNED`;
a new S1 fix PR is pending.** The fixes are stacked Drafts, not in RC-v7 and
not an RC-v8 signature. No QA/V1/RC signature.**

Lane: independent Q1 QA (#244/#250, register #256). QA2's Draft #360 was used
read-only as the harness; the QA2 branch was not modified. All work was done
from the exact exported snapshots in isolated checkouts, never in the S1/R1
author worktrees. No product source, production owner policy resolver,
provider, OAuth, credential, service or device was touched.

---

## 1. Exact refs

| Item | SHA / ref |
|---|---|
| QA2 harness | #360 `2888d8bda6828bd2c87c68a770440933fdb4ae23`, base RC-v7 `85c79b2` |
| S1 owner policy (RED) | #358 `b183aab0b8913ed36e5de539c50223d3eaf508f1`, base RC-v6 `8620a90` |
| R1 reconcile source (RED) | #356 `b4020b73e6b7ec2b1f6301471a9872f8864c1643`, base #311 `34b215c` |
| S1 fix (GREEN candidate) | #363 `8840f416169b22122450fb61a85f2b88c73715a0`, base #358 |
| R1 fix (GREEN candidate) | #361 `6a27204300753f129719103d0aee811ac675622a`, base #356 |
| Main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` |
| RC-v7 | `85c79b20c62da8db2f4d5c98284a34728b6e8f81` (neither #358 nor #356 is in it) |

The harness archives `b183aab…` and `b4020b7…` and verifies `.snapshot-sha`;
both snapshots match the PR heads above. The QA2 PR itself was not edited.

---

## 2. Reproduction

Run from the QA2 checkout, with a private work directory and the harness's own
bounds (`MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 120`):

```text
QA2_WORKDIR=/tmp/q1-qa2-rerun-20261020 ./qa2-run.sh all
```

Result: `s1-author=0`, `s1-controls=0`, `s1-signed=0`, `s1-findings=1`;
`r1-author=0`, `r1-controls=0`, `r1-findings=1`. A findings rc of 1 is the
expected RED: the adversarial probes confirmed the gaps. Logs are preserved
under `artifacts/skills/automated-testing/q1-security-qa-20261020/qa2-rerun-20261020T104115Z/`
(not copied into the public repo).

| Gate | rc | Independent result |
|---|---|---|
| S1 author tests | 0 | **86 passed, 1 skipped, 23 subtests** |
| S1 controls | 0 | **36/36** |
| S1 signed (local Ed25519) | 0 | **10/10** |
| S1 findings | 1 | **6/6 confirmed** (2 P1, 3 P2; int/str are one finding) |
| R1 author test | 0 | **35 passed** |
| R1 controls | 0 | **22/22** |
| R1 findings | 1 | **8/10 confirmed** (2 P1, 4 P2; id and thread cases each have two variants) |

The single S1 skip is honest and not a security pass:
`BLOCKED_MULTIPRINCIPAL_NOT_RUN` — the process is uid 1002 with no
passwordless setuid/sudo, so a second real uid cannot be attested. Synthetic
peer metadata is LOCAL only.

Fail-closed controls held: with no production owner-policy resolver, issue and
verify return **`BLOCKED_OWNER_POLICY`** and store nothing; with `source=None`,
R1 returns **`BLOCKED_PROVIDER_SOURCE`** with `settle_allowed=false`.
Production-mode signed input is still `BLOCKED_OWNER_SIGNATURE`.

---

## 3. S1 #358 — confirmed RED

| Case | Severity | Observed vs expected | Source seam |
|---|---|---|---|
| `f1-revoked-int`, `f1-revoked-str` | **P1** | `revoked=1` / `"yes"` → `verified=true`; expected `POLICY_DENIED` | `runtime/owner_policy.py:118` uses `policy.revoked is True` only |
| `f3-revoke-after-verify-before-return` | **P1** | a revoke after the first grant read still returns `verified=true`; expected refusal / atomic commit | verify reads, then returns; no verify+commit seam |
| `f2-policy-issued-in-future` | P2 | future `issued_at` authorizes; expected `BLOCKED_OWNER_POLICY` | `issued_at` is shape-parsed only; no `now` ordering |
| `f4-issue-without-resolver-stores-approved-owner-fields` | P2 | issue stores owner fields from `approved`; real verifier allowlist blocks today | `issue_grant` does not apply `OWNER_WRITTEN_GRANT_FIELDS` to `approved` |
| `f5-idempotent-issue-readback-skips-revoked-policy` | P2 | replay of identical evidence returns `issued=true` after policy revoke | existing-evidence readback returns before `verify_delegation`/resolver in `agent_control_store.py` |

Risk: the two P1s are a type-confusion fail-open and a TOCTOU. With no
production resolver bound, the route is still `BLOCKED_OWNER_POLICY`; that is
the only reason this is not a production P0. **Do not bind an owner-policy
resolver until P1-1/P1-2 are fixed or P1-2 is explicitly represented as an
atomic-commit `BLOCKED_OWNER_POLICY` seam.**

Required author cases: `revoked=1` and `revoked="yes"` → `POLICY_DENIED`;
future `issued_at` → `BLOCKED_OWNER_POLICY`; revoke between resolve and return
→ no `verified=true`; replay of revoked-policy evidence → refused; an
`approved` document carrying owner-written fields → refused by `issue_grant`
itself (defence in depth).

---

## 4. R1 #356 — confirmed RED

| Case | Severity | Observed vs expected | Source seam |
|---|---|---|---|
| `f1-replay-with-empty-observation-id`, `f1-replay-with-absent-observation-id` | **P1** | accepted APPLIED with `settle_allowed=true`; expected `REFUSED_REPLAY` | replay check only runs `if observation_id` (`reconcile_source_gate.py:327`) |
| `f4-synthetic-result-production-field-names` | **P1** | synthetic HMAC returns `owner_signed=true`, `s2_verdict/s2_status=SETTLE_APPLIED`, `settle_allowed=true` | `owner_signed = True` at line 218; `S2_SETTLE_APPLIED` at line 341 |
| `f2-signed-source-not-bound` | P2 | signed `source="forged:source"` accepted with `capability=CLIENT_TURN_ID` | signed source never compared to capability |
| `f3-observed-at-not-validated` | P2 | `observed_at="not-a-time"` accepted | no parse, clock or max-age check |
| `f5-writer-epoch-not-signed` | P2 | epoch 7→8 old observation still validates | writer fence absent from signed bytes |
| `f6-source-declaration-not-cross-checked-unknown-thread`, `…cli-adapter-declares-turn-id` | P2 | `thread_known=false` and adapter mismatch still accepted | only `present`/`capability` read; matrix distinctions ignored |

Risk: P1-1 is a contract hazard — a test HMAC must never be labelled
`owner_signed` or carry the kernel's real `SETTLE_APPLIED` token. P1-2 is a
replay bypass. Production remains blocked today because there is no production
import/wiring and the synthetic gate is not an owner authority; that does not
make the field names safe. **Do not wire the gate or treat a synthetic result
as owner-signed until these are fixed.**

Required author cases: empty/absent `observation_id` replay →
`REFUSED_REPLAY`; synthetic results must use a synthetic verdict/`owner_signed`
name (or `settle_allowed=false`) and never the production `SETTLE_APPLIED`;
signed source must equal the declared capability; `observed_at` must parse and
be fresh; the signed payload must bind writer owner/epoch/token; and
`thread_known=false`/adapter/verb mismatches must be `BLOCKED_PROVIDER_SOURCE`.

---

## 5. Fix PRs — GREEN re-test at exact SHAs

Both fix PRs are now published and were exported separately; the author
worktrees were not entered.

### S1 #363 `8840f416169b22122450fb61a85f2b88c73715a0`

Same QA2 S1 probe, `S1_SNAPSHOT` set to the exported #363 tree:

| Gate | rc | Result |
|---|---|---|
| Author six files | 0 | **94 passed, 1 skipped, 23 subtests** (same honest `BLOCKED_MULTIPRINCIPAL_NOT_RUN`) |
| Controls | 0 | **36/36** |
| Signed local Ed25519 | 0 | **10/10** |
| Findings | 0 | **6/6 now pass** |

Observed after the fix: `revoked=1` / `"yes"` → `BLOCKED_OWNER_POLICY`,
`verified=false`; future `issued_at` → `BLOCKED_OWNER_POLICY`; the
verify→revoke race → **`BLOCKED_ATOMICITY`**, `verified=false`, no positive
verdict; owner-written fields are stripped from `approved` and the stored
binding is null; idempotent readback re-checks the live policy and returns
`issued=false` with `BLOCKED_OWNER_POLICY` after revocation.

Residual, explicit in the fix: a normal positive verify is point-in-time and
declares **`atomicity=BLOCKED_OWNER_COMMIT_SEAM`**; there is still no atomic
verify+commit authority. With no production resolver, issue/verify remain
**`BLOCKED_OWNER_POLICY`** and store nothing.

### R1 #361 `6a27204300753f129719103d0aee811ac675622a`

The v1 QA2 probe is intentionally **not compatible** with the v2 result shape
(the old controls hit `KeyError: 'decision'`, and its findings expectations
name v1 fields). I therefore used an independent probe over the public v2
fixture, asserting the same security properties rather than old field names:

- Author `test_fleet_v1_runtime_reconcile_source.py`: **61 passed**.
- Independent v2 probe: **25/25 PASS**.
- Shared production fields are always a refusal: `decision` /
  `production_decision = BLOCKED_PROVIDER_SOURCE`, `owner_signed=false`,
  `settle_allowed=false`, `s2_verdict=null`, `s2_status=BLOCKED_PROVIDER_SOURCE`;
  a valid synthetic HMAC proves APPLIED only under `synthetic_*`.
- Empty/absent observation id → `REFUSED_MISSING_OBSERVATION_ID`; forged
  signed source → `REFUSED_SOURCE_MISMATCH`; `not-a-time` / future / stale →
  `REFUSED_OBSERVED_AT_INVALID` / `_FUTURE` / `_STALE`; unknown thread, CLI
  adapter and create verb → `BLOCKED_PROVIDER_SOURCE`; old writer epoch →
  `REFUSED_STALE_WRITER`; signed writer-field mutation → invalid signature;
  non-empty replay → `REFUSED_REPLAY`; no source → `BLOCKED_PROVIDER_SOURCE`.

Residual: this remains `SYNTHETIC_FIXTURE`; the synthetic HMAC is not owner
authority. Production is still `BLOCKED_PROVIDER_SOURCE` with
`BLOCKED_OWNER_SIGNATURE`; no real provider, owner signature or OAuth was used.

**RC-v8:** no `rc/i1-v8*` ref is published; no RC-v8 gate or signature is
claimed. Both fixes are stacked Drafts (#363 on #358, #361 on #356), not in
RC-v7 `85c79b2`.

Evidence levels: S1 is `LOCAL_SYNTHETIC_PEER` / `LOCAL_SYNTHETIC_POLICY` /
`LOCAL_SYNTHETIC_TRUST` (locally generated Ed25519 in a temp HOME); R1 is
`SYNTHETIC_FIXTURE`. No `REAL_OWNER_2UID`, real provider, live OAuth,
REAL_DEVICE or CLIENT_E2E claim.

## 5b. Critical S1 consumer seam — RED at #363

Independent real-process probe (`qa_probe_r18_seam_363.py`) at exact
`8840f416169b22122450fb61a85f2b88c73715a0`:

- real `Daemon` on a temp AF_UNIX socket with a synthetic owner-policy
  resolver; one bound grant/context;
- real `UnixSocketVerifyClient` → `ServerOSLeaseAuthority` →
  `OwnerVerifiedLease.acquire` with a temp WorkEngine `ServerOSStore`.

Positive control: a live policy acquires exactly one lease (token 1).

Race: revoke the policy after the daemon's last read/response. The daemon
answer is `verified:true`, `atomicity:BLOCKED_OWNER_COMMIT_SEAM`; the client
checks only `verified is True`, builds the `LeaseGrant`, and `acquire` writes
**one new lease** (`token=1`, `owner=human:A`). That is a **FAIL** of the
required fail-closed boundary — a post-read revoke produced a lease.

Controls: no owner resolver → `BLOCKED_OWNER_POLICY`, zero leases; no trusted
authority → `BLOCKED_TRUST_SOURCE`, zero leases.

Required fix: either the daemon returns `verified:false,
reason:BLOCKED_OWNER_COMMIT_SEAM` until a real atomic commit fence exists, or
the mutating Work/lease boundary enforces an authoritative fence the adapter
validates. Renaming the reason or adding another read is not a fence. **No new
S1 fix PR is published yet; GREEN is NOT_RUN.** This is a future activation /
V1 release blocker (`BLOCKED_OWNER_COMMIT_SEAM / NOT_V1_SIGNED`), not evidence
of a current production exploit because the installed daemon binds no
resolver.

## 6. Judgment

**QA_PARTIAL / NOT_RELEASE; no QA/V1/RC-v7/RC-v8 signature.** The original
QA2 RED is reproduced at #358/#356 and is now GREEN at the fix heads
#363/#361 for the same attack set. S1 still has no atomic verify+commit
authority (`BLOCKED_OWNER_COMMIT_SEAM`) and remains `BLOCKED_OWNER_POLICY`
without a production resolver; R1 remains `SYNTHETIC_FIXTURE` with production
`BLOCKED_PROVIDER_SOURCE`/`BLOCKED_OWNER_SIGNATURE`. Unit-test green is not a
production trust signature. Real owner resolver and two real UIDs, a real
provider/owner signature, live OAuth, Windows real SCM/REAL_DEVICE,
CLIENT_E2E and full T1–T12 remain BLOCKED; RC-v8 is not published. The
consumer seam at #363 is independently RED (`BLOCKED_OWNER_COMMIT_SEAM /
NOT_V1_SIGNED`): `OwnerVerifiedLease.acquire` writes a new lease from a
point-in-time `verified:true` answer after a post-read revoke. Until a new S1
fix is published and re-tested, no V1 Work Engine lease-authority signature.

## 7. Receipts

| Run | rc | Log |
|---|---|---|
| S1 author | 0 | `qa2-rerun-20261020T104115Z/s1-author.log` |
| S1 controls | 0 | `s1-controls.log` |
| S1 signed | 0 | `s1-signed.log` |
| S1 findings | 1 | `s1-findings.log` |
| R1 author | 0 | `r1-author.log` |
| R1 controls | 0 | `r1-controls.log` |
| R1 findings | 1 | `r1-findings.log` |
| S1 #363 author | 0 | `qa2-fix-20261020/…` (94P/1S) |
| S1 #363 controls | 0 | `qa2-fix-20261020/q1-s1-363-controls.log` |
| S1 #363 signed | 0 | `qa2-fix-20261020/q1-s1-363-signed.log` |
| S1 #363 findings | 0 | `qa2-fix-20261020/q1-s1-363-findings.log` (6/6 pass) |
| R1 #361 author | 0 | 61 passed |
| R1 #361 independent v2 probe | 0 | `qa2-fix-20261020/r17-r1-361-probe.log` (25/25) |
| R1 #361 v1 probe | — | incompatible with v2 result shape (documented above) |
| Fix-PR poll | — | `r16-s1r1-fix-poll.log` |
