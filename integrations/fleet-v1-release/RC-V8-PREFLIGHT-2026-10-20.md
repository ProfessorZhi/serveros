# I1 RC-v8 preflight — 2026-10-20

**Status: `RC_BLOCKED` / `NOT_RELEASE`.** This preflight is superseded by the
isolated review candidate `rc/i1-v8-review-20261020` (see
`RC-V8-REVIEW-2026-10-20.md`). The candidate is **not** a release and has no QA
signature. No production resolver, provider, daemon, Fleet, Connector, OAuth,
credential or account is touched.

| Field | Value |
| --- | --- |
| Authoritative main | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` |
| Published RC-v7 | `rc/i1-v7-review-20261020` @ `85c79b20c62da8db2f4d5c98284a34728b6e8f81` (Draft #357) |
| S1 product candidate | #358 `b183aab0b8913ed36e5de539c50223d3eaf508f1` (base RC-v6 `8620a90`) |
| R1 product candidate | #356 `b4020b73e6b7ec2b1f6301471a9872f8864c1643` (base #311 `34b215c`) |
| R1 downstream fix | #361 `6a27204300753f129719103d0aee811ac675622a` (base #356) |
| QA2 audit | #360 `2888d8bda6828bd2c87c68a770440933fdb4ae23` (`QA_PARTIAL`) |
| QA AG re-run | #362 `dac5f8ae5ff160aeed1c4a268346686f78029021` (S1/R1 RED at originals; GREEN at fix heads) |
| QA AF RC-v7 gate | #359 `467f290ec6e9db4ddaf826ef00e8ddaea3751a88` (`QA_PARTIAL`) |
| S1 downstream fix | #363 `8840f416169b22122450fb61a85f2b88c73715a0` (QA2 6/6 GREEN; consumer seam RED at #363) |
| S1 commit-fence fix | #364 `7fe1cccd6b7f103e58140ef7d512d69e436704ec` (QA AG seam probe 10/10 GREEN; no production `COMMIT_FENCED` producer) |
| S1 test-only follow-up | #367 `3cb39ae30e1be6811cb474b3b6b83e49db44ab52` (X2 win32 import probe; test/doc only) |
| RC-v8 review candidate | `rc/i1-v8-review-20261020` @ tested tip `74bae6d41d8e2f5fe23697e9376c42a7f97d0dae` + docs commit |

## 1. Dependency DAG and ancestry

```text
main@771c81c
└─ RC-v7 85c79b2  (RC-v6 8620a90 + #353 560f715 + docs)
   ├─ #358 b183aab  (base RC-v6 8620a90; sibling of the #353 commit)
   └─ #361 6a27204  (base #356 b4020b7; #356 base #311 34b215c)
                    └─ #356 b4020b7 (base #311; #311/#308/#257 already in RC-v7)
```

Verified facts:

| Check | Result |
| --- | --- |
| `merge-base(#358, RC-v7)` | `8620a90` — #358 adds exactly one commit `b183aab` |
| `merge-base(#356, RC-v7)` | `34b215c` (#311) — #356 adds exactly one commit `b4020b7` |
| `merge-base(#361, RC-v7)` | `34b215c`; #361 adds `b4020b7` + `6a27204` |
| `#311 -> #356`, `#308 -> #356`, `#257 -> #356` | true; no duplicate dependency reintroduction |
| `merge-tree(#358, RC-v7)` | **CLEAN** |
| `merge-tree(#356, RC-v7)` | **CLEAN** |
| `merge-tree(#361, RC-v7)` | **CLEAN** |
| `merge-tree(#358, #361)` | **CLEAN** |

**#358 is not based on RC-v7.** It is a sibling of the #353 test-only commit on
RC-v6. Its merge into a future RC-v8 must include the RC-v7 guard patch first;
the merge is textually clean but the S1 regression suite must run on the
composition.

**#356/#361 do not duplicate #257/#308/#311.** Those ancestors are already in
RC-v7; only the R1 gate/fix commits are new.

## 2. QA2 findings (authoritative, not signed)

QA2 #360 and QA AG #362 both keep S1 #358 and R1 #356 at the **original heads**
`b183aab` / `b4020b7`:

| ID | Exact issue |
| --- | --- |
| S1-P1-1 | `runtime/owner_policy.py:118` — `policy.revoked is True` only; `revoked=1` / `"yes"` verifies as unrevoked |
| S1-P1-2 | `runtime/daemon.py:3047-3094` — verify→revoke→return TOCTOU; a revoke during `resolver.resolve` is invisible and `verified: true` is returned; atomicity is **BLOCKED** |
| S1-P2-1 | `runtime/owner_policy.py:129-133` — `issued_at` not ordered against `now`; a policy issued in 2999 authorizes |
| S1-P2-2 | `runtime/agent_control_store.py:2059-2090` — `issue_grant` does not apply `OWNER_WRITTEN_GRANT_FIELDS` to `approved` |
| S1-P2-3 | `runtime/agent_control_store.py:2042-2047` — idempotent issue readback returns before the owner resolver; a revoked policy still yields `issued: true` |
| R1-P1-1 | `reconcile_source_gate.py:218-219,337-346` — synthetic HMAC produced production fields `owner_signed=true`, `s2_verdict=SETTLE_APPLIED`, `settle_allowed=true` |
| R1-P1-2 | `reconcile_source_gate.py:327-335` — empty/absent `observation_id` skips replay; a signed APPLIED replays |
| R1-P2-1 | signed `source` not compared with the capability (`forged:source` accepted) |
| R1-P2-2 | signed `observed_at` not parsed/freshness-checked (`not-a-time` accepted) |
| R1-P2-3 | source gate trusts `present`/`capability`; `thread_known=false` or a CLI adapter declaring `CLIENT_TURN_ID` passes |
| R1-P2-4 | signed payload has no writer fence; epoch 7→8 can settle without the applied set |

Both PRs are **fail-closed in production only while the resolver/gate is
unbound**: no owner resolver → `BLOCKED_OWNER_POLICY`, nothing stored;
`source=None` → `BLOCKED_PROVIDER_SOURCE`, `settle_allowed=false`. QA2/AG is
`QA_PARTIAL`, no signature. Synthetic HMAC is not owner authority.

**Fix status at this snapshot:**

- **R1:** #361 `6a27204` is published on top of #356. Its own test file is **61
  passed** in an isolated checkout (LOCAL). It separates all shared production
  fields to refusal (`settle_allowed=false`, `owner_signed=false`,
  `s2_verdict=null`), moves synthetic settle to `synthetic_settle_allowed`,
  requires a non-empty `observation_id`, binds signed `source` to the capability
  read path, parses `observed_at`, cross-checks `(adapter, verb,
  thread_known)`, and binds the writer epoch/token. Production remains
  `BLOCKED_PROVIDER_SOURCE` / `BLOCKED_OWNER_SIGNATURE`. **Not independently
  QA'd at #361 yet.**
- **S1:** #363 `8840f41` is published and closes the QA2 6/6 findings at its
  exact head, but the **cross-module consumer seam is RED** (§7): the daemon
  returns `verified:true` with `atomicity=BLOCKED_OWNER_COMMIT_SEAM`, and the
  WorkEngine adapter writes one lease after a revocation. A new atomic-seam
  fix PR is **not published**; #358/#363 must not be treated as positive
  authority.

## 3. Minimal safe composition plan for a future RC-v8

### Stage 0 — freeze and gate

1. Freeze RC-v7 `85c79b2` as the base. Do not rebase or rewrite author branches.
2. Re-read the S1/R1 fix heads from GitHub; a moved head invalidates this plan.
3. RC-v8 does not exist until both the S1 fix and the R1 fix have exact heads
   that passed their own tests and a QA2 re-run.

### Stage 1 — R1 first (additive, no production wiring)

Merge **#361 `6a27204`** (it contains #356) onto RC-v7. Why first: it is
integration-only (`integrations/fleet-v1-runtime/**` + one test file), does not
bind a production source, and cannot mint a grant or settle in production.
Textual merge is clean; the return gate is the QA2 R1 findings re-run:

- synthetic fields must not populate production fields;
- empty/absent `observation_id` refused;
- signed `source` == capability read path;
- `observed_at` parsed and freshness-checked;
- `thread_known=false` / CLI adapter mismatch refused;
- writer epoch/token fence enforced;
- production `settle_allowed=false`, `owner_signed=false`,
  `production_decision=BLOCKED_PROVIDER_SOURCE`.

### Stage 2 — S1 after its own fix PR (authority is the higher-risk merge)

Merge the S1 fix tip (not `b183aab` if the fix head differs). Required before
merge:

- `revoked=1` / `"yes"` / non-bool truthy refused; exact revocation semantics;
- verify→revoke atomicity: either an atomic commit/verify seam or a re-read
  after resolve that returns a refusal when the policy changed; the atomicity
  claim must be **BLOCKED** rather than asserted if no seam exists;
- `issued_at` ordered against `now`; future-issued policy refused;
- `OWNER_WRITTEN_GRANT_FIELDS` applied to `approved`;
- idempotent issue readback runs the owner resolver, so a revoked policy cannot
  replay `issued: true`;
- no resolver → `BLOCKED_OWNER_POLICY`, no grant, no uid/context fallback.

The S1 merge touches `runtime/agent_control_store.py` and `runtime/daemon.py`,
already modified by RC-v7's #275/#318 baseline. `merge-tree` is clean, but the
S1 grant/handoff tests and RC-v7's 9-file group must run on the composition.

### Stage 3 — RC-v8 regression and independent QA

Run, on the composed exact SHA:

- QA2's S1/R1 findings harness (`qa2-run.sh all`) with the same 1 GiB/swap0/25%
  bounds; RED must become GREEN, controls must stay 36/36 and 22/22;
- RC-v7 regression: 9-file same-process 138, X2 101/0 skip, light core/S1
  366/213, Fleet Next 286/133, Native 89/0 + 88/0, REAL_TEMP PASS/REFUSAL;
- an independent QA signature at the exact RC-v8 SHA. A unit-test green does
  not waive a QA2 RED.

### Return / stop gates

Any of the following returns the candidate to `RC-v8_READINESS_PENDING` and
blocks the RC:

- S1 fix PR absent or its head differs from the reviewed head;
- any QA2 S1/R1 finding still RED;
- production fields populated by synthetic data;
- a resolver/uid/context fallback or fabricated grant;
- a second owner registry, second reconcile ledger or second T10 producer;
- any RC-v7 regression (the 9-file/X2/REAL_TEMP groups) breaking.

## 4. Single-repo authority and rollback

**Authority owners (one each):**

| Fact | Authority | Not a second authority |
| --- | --- | --- |
| grant/epoch/principal | `runtime/agent_control_store.py` | `runtime/owner_policy.py` is a contract/read; it must not become a registry |
| owner→uid→actor→scope | external owner resolver, passed to the daemon | no uid/context inference; no fallback policy |
| dispatch/reconcile ledger | existing `dispatch_attempts` / Agent Control store | #361 is a gate, not a second ledger |
| T10 finalization | Work Engine finalize/receipt path | no second producer/signature |
| repo | `ProfessorZhi/serveros` | no external work-engine repo |

**Rollback:** RC-v8 would be merge/cherry-pick commits on RC-v7 only. Rollback
is to return to RC-v7 `85c79b2` or `main@771c81c`; there is no production
resolver, provider source, migration or service, so no production state is
created. REAL_TEMP is ephemeral and removes its 0700 tree.

## 5. Lightweight negative test receipts (RC-v7, LOCAL/REAL_TEMP)

These are RC-v7’s fail-closed controls, not RC-v8 evidence.

| Gate | Command/file | Result | Level |
| --- | --- | --- | --- |
| No provider source | `tests/test_dispatch_reconcile_gate.py -k "no_source or response_id_only or unknown_capability or missing_or_unresolved_context"` | **4 passed, 3 subtests** | LOCAL |
| Grant negatives | `tests/test_v1_grant_os_principal_schema.py -k "different_uid or no_os_principal or revoked_and_old_epoch or wrong_scope"` | **4 passed** | LOCAL |
| Representative 9-file T10/Finalize | 9 files, `-k "legacy or signed or without_authority or core_ability or tamper or cross_run"` | **63 passed, 75 deselected** | LOCAL |
| REAL_TEMP owner-source absence | temp daemon `agent.owner.policy.resolve` | **UNKNOWN_METHOD** (no owner route) | REAL_TEMP |
| REAL_TEMP grant/no-store | unsigned `agent.grant.issue` | **DELEGATION_EVIDENCE_INVALID**, no `store.json` | REAL_TEMP |
| REAL_TEMP no-context reconcile | `agent.dispatch.reconcile` without context | **UNTRUSTED_ACTOR**, `source_called=false` | REAL_TEMP |
| REAL_TEMP lifecycle | serverosd start/status/stop/temp cleanup | **PASS/REFUSAL**, `temp_removed=true` | REAL_TEMP |
| R1 fix own tests | `tests/test_fleet_v1_runtime_reconcile_source.py` at #361 | **61 passed** | LOCAL |

The REAL_TEMP run used a fresh `0700` temp registry/state/socket, no
`--enable-reconcile`, no default socket, no production config; only the
recorded PID was signalled. No send, settle or provider call occurred.

## 6. Readiness verdict

**`RC_BLOCKED` / `NOT_RELEASE`.**

- The S1 chain now includes #364 `7fe1ccc`. Its commit-fence behavior is
  independently GREEN (10/10, zero leases for the positive and race cases) and
  the daemon no longer returns a positive owner-backed grant.
- R1 #361 is independently 25/25 on the adapted QA AG probe; production fields
  remain `BLOCKED_PROVIDER_SOURCE`.
- The composed review branch has LOCAL/REAL_TEMP evidence; the X2 synthetic
  Windows group is green at **102 passed/0 skipped** after test-only #367. The
  raw QA2 R1 harness still reports decision-code drift (stronger
  `BLOCKED_PROVIDER_SOURCE`, no settle), and no QA signature exists.
- No signed V1, no `RELEASE`, no main merge, no production deployment. QA2
  #360, QA AG #362 and QA AF #359 are `QA_PARTIAL`; none is a signature.

## 7. Post-QA AG update — S1 consumer seam is RED (RC-v8 stays PENDING)

QA snapshot AG #362 (head `dac5f8ae`) independently re-ran the QA2 harness at
the exact fix heads:

| Lane | Fix head | QA AG result |
| --- | --- | --- |
| S1 | #363 `8840f416` | author 94 passed / 1 skipped; controls 36/36; signed 10/10; QA2 findings **6/6 GREEN** |
| R1 | #361 `6a272043` | author 61 passed; independent v2 probe **25/25** |

**But the cross-module consumer seam is still RED.** An independent I1 run of
QA AG's `qa_probe_r18_seam_363.py` at the exact S1 head `8840f41` (real daemon
AF_UNIX + `ServerOSAuthority`/`UnixSocketVerifyClient` + `OwnerVerifiedLease`
+ temp WorkEngine store) produced:

```text
PASS positive/verified
PASS positive/lease-written
PASS race/daemon-said-point-in-time
FAIL race/no-new-lease leases=1 result={'resource': 'work-write:SRV-0001',
     'owner': 'human:A', 'token': 1, ...} atomicity=BLOCKED_OWNER_COMMIT_SEAM
PASS noresolver/refused (BLOCKED_OWNER_POLICY)
PASS noresolver/no-lease
PASS noauthority/refused-blocked-trust-source (BLOCKED_TRUST_SOURCE)
PASS noauthority/no-lease
TOTAL 8 FAIL 1
RED_CONFIRMED
```

The daemon returns `verified: true` with `atomicity:
"BLOCKED_OWNER_COMMIT_SEAM"`; `workengine/serveros_authority.py` checks only
`verified`, constructs the `LeaseGrant`, and `OwnerVerifiedLease.acquire`
writes **one lease** after the last owner-policy read. QA AG reproduced the
same result. This is a future-activation / V1 release blocker; production
still binds no resolver and remains `BLOCKED_OWNER_POLICY`, so it is not a
current production exploit.

**Consequences:**

- The isolated candidate `rc/i1-v8-review-20261020` composes #356→#361 and
  #358→#363→#364 on RC-v7. It is a **review branch only**, with status
  `RC_BLOCKED` / `NOT_RELEASE`.
- #364 makes the daemon answer `verified: false` with
  `BLOCKED_OWNER_COMMIT_SEAM` and no grant; the adapter refuses owner markers
  unless `atomicity == "COMMIT_FENCED"`. The controlled
  revoke-before-`OwnerVerifiedLease.acquire` case is zero-lease in both the
  author test and the independent QA AG r19 probe.
- R1 #361 remains synthetic-only; production is `BLOCKED_PROVIDER_SOURCE` and
  `settle_allowed=false`.
- The fixture owner policy / synthetic HMAC is **not** production authority.
  No positive production trust may be manufactured.
- Remaining blockers are recorded in `RC-V8-REVIEW-2026-10-20.md` §5: the QA2
  raw-harness decision-code drift, the intended 34/36 S1 controls, the absent
  production `COMMIT_FENCED` producer, and the absent QA signature. The X2
  win32 import group is closed by test-only #367 (102 passed/0 skipped).

QA AG is `QA_PARTIAL`, no signature. Main, the canonical checkout, production
daemons/services and credentials were untouched.
