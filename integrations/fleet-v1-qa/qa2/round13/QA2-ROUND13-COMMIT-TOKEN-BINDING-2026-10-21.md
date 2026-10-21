# QA2 Round 13 — #381 exact commit/token binding: cross-token GREEN, replay still P1

**Pinned head:** `043e08b916b2b511b56b30a419d21ad4450451c2` (Draft PR #381)
**Base:** `cbbfb63a76072871edfa6aa639355f5b63cc5853` (PR #380)
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

#381 fixes the #380 **cross-token** P1 and the missing post-hold clock/fence
re-check: cross-token check/effect now `STALE_TOKEN` with no marker; TTL/policy/
grant crossings after `hold` roll the marker back and leave the lease/commit/
token unchanged. It does **not** fix the separate **historical-replay
capability** P1: a `PROTOTYPE_HISTORICAL_REPLAY` receipt still carries a usable
capability, and `check_use`/`commit_effect` turn its `authorized:false` into
`authorized:true` plus an effect marker. Therefore the P1 set is **not fully
closed** and #381 cannot be QA-signed.

## 1. Pinned identity and scope

| Check | Result |
|---|---|
| PR #381 | OPEN, **Draft**, MERGEABLE |
| `refs/pull/381/head` | `043e08b916b2b511b56b30a419d21ad4450451c2` |
| remote branch `feat/s1-commit-token-binding-20261021` | same exact SHA |
| base | `cbbfb63a76072871edfa6aa639355f5b63cc5853` |
| local snapshot | identical to `git archive 043e08b9…`; only `.snapshot-sha` |
| diff | exactly 4 allowed paths, `+361 / −26` |
| paths | README, `atomic_fence.py`, `single_write_entry.py`, `tests/test_s1_atomic_prototype_single_write.py` |
| `check_scope.py` vs #380 | `changed=4 allowed=4 forbidden=0`, `SCOPE_OK` |
| old #379 code | old 62P rc0; round10 controls **43/43**; round11 API **18/18** |
| author new file | **20 passed** (previous 13 + 7) |
| author combined | **82 passed** |
| production marker | no `verified`, no `COMMIT_FENCED`; A/`API_ONLY_CORRECTNESS` |

Logs: `logs/a-prime-381-metadata.log`, `logs/a-prime-381-check-scope.log`,
`logs/a-prime-381-author-*.log`, `logs/a-prime-381-local-equals-archive.log`.

## 2. Matrix

| Gate | rc | Result |
|---|---|---|
| author old `test_s1_atomic_prototype.py` | 0 | **62 passed** |
| author new `test_s1_atomic_prototype_single_write.py` | 0 | **20 passed** |
| author combined | 0 | **82 passed** |
| round10 controls at #381 | 0 | **43/43** |
| round11 API controls at #381 | 0 | **18/18** |
| independent `api` | 0 | **12/12 PASS** |
| independent `binding` | 1 | **B1/B2 PASS, B3 P1 RED, B4/B5 P2 observed** |
| independent `raw` | 0 | **5/5 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** |
| independent `osuid` | 0 | **2/2 NOT_RUN** |

`raw` rc 0 means the predicted bypass reproduced, not product PASS.

## 3. #380 RED → #381 GREEN: cross-token

#380 round12 C1 (saved as `logs/a-prime-380-round12-capability-red.log`):

```text
first = acquire(req:1)                       # token 1, epoch 1
rebind ctx -> grant:proto-2                  # epoch 2
advance clock past TTL; second = acquire(req:2)  # token 2
check_use("req:1", token2, cap1)             -> ACCEPTED authorized:true
commit_effect("req:1", token2, cap1)         -> marker request_id=req:1,
                                                token=2, context_epoch=2
```

#381 B1 on the same scenario:

```text
check_use("req:1", token2, cap1)   -> STALE_TOKEN
commit_effect(...)                 -> STALE_TOKEN
effect_count = 0
```

The commitment row's token is now checked before the capability and before the
fence; the exact commit row is passed to `_check_writer_fenced`, which verifies
request_id/resource/owner/token and the commit's own expiry. Cross-token is
**GREEN**.

## 4. Post-hold clock/fence re-check: GREEN

The effect transaction inserts the marker, runs the TEST-ONLY `hold`, then
reads the injected `FixedClock` again inside the same `BEGIN IMMEDIATE` and
re-runs the exact commit fence:

| Case | Code | Marker | Ledger before == after |
|---|---|---|---|
| hold clock → NOW+31 (ttl 30) | `LEASE_EXPIRED` | absent | yes |
| policy expires NOW+10, hold → NOW+11 | `POLICY_EXPIRED_AT_COMMIT` | absent | yes |
| grant expires NOW+10, hold → NOW+11 | `GRANT_EXPIRED_AT_COMMIT` | absent | yes |

Ledger comparison includes the lease row, `id_sequences` token, `proto_commit`
count and `proto_effect` count. No sleep was used.

Also GREEN:

- effect replay same facts → `replayed:true`, `authorized:false`, one marker;
  same `effect_ref` different payload → `IDEMPOTENCY_CONFLICT`; same
  `request_id` different `effect_ref` → `EFFECT_ALREADY_COMMITTED`.
- grant epoch 1→2 → `BLOCKED_STALE_EPOCH` for use and effect; context A→B→A →
  `BLOCKED_STALE_CONTEXT_EPOCH`; missing grant / missing policy →
  `BLOCKED_OWNER_POLICY`; ledger unchanged in each case.
- revoke-first → `POLICY_DENIED`, effect 0.
- no `verified` / `COMMIT_FENCED`; `classification()` returns
  `API_ONLY_CORRECTNESS`, B/C labels, `EFFECT_COMMIT_NOT_PROVEN`,
  `NOT_V1_SIGNED`.

## 5. Cross-resource and token type

**Cross-resource capability mixing is refused.** With request A on
`work-write:SRV-0001` and request B on `work-write:SRV-0002` (both token value
1):

```text
check_use("req:b", token_b, cap_a)     -> BLOCKED_CAPABILITY
commit_effect("req:b", token_b, cap_a) -> BLOCKED_CAPABILITY
```

**P2 token-scope ambiguity:** `check_use("req:a", token_b_value=1, cap_a)`
returns `ACCEPTED` for **resource A**, because token values are per-resource
integers and the API does not take a resource argument. It does not authorize
resource B; the fence uses capability A's commit/resource. This is an ambiguity
in token scoping, not a cross-resource grant.

**P2 token type:** `check_use(req, True, cap)` and `check_use(req, 1.0, cap)`
are `ACCEPTED` as token 1 (`int()` comparison; the old atomic check rejected
bool). No authority beyond the same integer token; `"1"` is refused
`STALE_TOKEN`. Strict `isinstance(token, int) and not bool` validation is
missing.

## 6. Historical replay capability: P1 still open

#380 round12 C6 was:

```text
replay = acquire(req:1, same facts)
  -> authorized:false, historical:true, replayed:true, token 1,
     capability present
```

#381 B3 reproduces the same gap unchanged:

```text
check_use("req:1", token, replay.capability)      -> ACCEPTED authorized:true
commit_effect("req:1", token, replay.capability)  -> ACCEPTED authorized:true
                                                      effect_count = 1
```

The replay receipt says no current authorization, but its capability is
accepted as current authority and writes an effect marker. The cross-token fix
does not address this: request, token and capability all match; the defect is
that `acquire` attaches a usable capability to a historical/replayed receipt.
#381 must not be described as closing all P1s.

## 7. Same-process caveat and raw DB

`SingleWriteEntry` remains a same-process Python object. The README now
explicitly discloses that a holder can call synthetic owner `issue` with chosen
epochs, reach `_writer.state_dir`/`db_path`, and read `_capability_key`; B4
reproduces all of it (`self_acquire ACCEPTED`, forged capability accepted,
direct DB open, `socket_api:false`). This is **P2 disclosed prototype
capability correctness**, not a production boundary.

Raw same-UID SQLite (`raw`, 5/5 EXPECTED_RAW_DB_BYPASS):

- direct `proto_effect` insert after revoke is visible to `effect()`;
- raw `REPLACE` of context+commit (epoch 2/grant2) plus the reachable HMAC key
  makes `check_use` **ACCEPTED** for token 1;
- `chmod 0600` DB still same-UID `r+b`;
- a per-connection SQLite authorizer is bypassed by a second connection;
- legacy `ServerOSStore.acquire_lease`/`check_writer` still bypass
  (`commit_count=1`, legacy authorized, fenced `POLICY_DENIED`).

These are expected and disclosed (`RAW_DB_WRITE_BYPASS_EXPECTED` /
`BLOCKED_DB_WRITER_ISOLATION`), but remain **P0 production Writer/legacy
entry closure**, not fixed.

## 8. Two-process isolation / crash

- **RACE1 revoke-first:** child revoke commits; parent use/effect
  `POLICY_DENIED`, effect 0.
- **RACE2 effect-first with hold:** effect transaction holds the write lock;
  parent `BEGIN IMMEDIATE` with `busy_timeout=0` gets `OperationalError`; after
  release the marker commits, then the revoke commits, and later use is
  `POLICY_DENIED`. A revoke cannot interleave inside the effect transaction.
- **RACE3 crash-before-COMMIT:** child `os._exit(17)` in `hold`; reopen shows
  no marker, `effect_count=0`.

## 9. Classification

- **P0 — production Writer authorization / legacy entry closure:** raw DB
  writer can insert effect markers/rewrite context+commit and use the readable
  key; legacy `acquire_lease`/`check_writer`, JSON revoke, finalize/effect and
  migration remain outside the fence. Disclosed `PRODUCTION_WIRING_REQUIRED`.
- **P1 — prototype API path:** historical replay capability re-authorizes
  (`authorized:false` receipt → `check_use`/`commit_effect` authorized:true and
  marker). Cross-token is fixed; this replay gap remains open.
- **P2 — evidence labels / test harness / prototype caveats:**
  same-process owner/key/db_path caveat (now disclosed); token type
  bool/float acceptance; token-scope ambiguity across resources with equal
  token integers; `check_scope` still uses the loosened
  `tests/test_s1_atomic_prototype*` prefix from #380; the new 20P suite has no
  replay-capability test; `payload_digest` is caller-supplied and the marker
  has no context_ref/grant_ref; `#364` production control not independently
  rerun; synthetic labels.
- **NOT_RUN:** OS isolation, real owner key, two real uids, kernel
  `SO_PEERCRED`, production daemon/Fleet/WorkEngine wiring, real Windows,
  Provider/OAuth, power-loss/fsync, real `finalize_run_verified`/effect.
  `EFFECT_COMMIT_NOT_PROVEN`.

## 10. Minimal fixes

1. **Do not mint capabilities on replay.** In `acquire`, if the receipt is
   `historical`/`replayed`, omit `capability`, or attach
   `capability_scope="HISTORICAL"` and reject it in `check_use`/`commit_effect`.
2. **Strict token type** at the API boundary:
   `isinstance(token, int) and not isinstance(token, bool)`.
3. **Bind capability to resource** and/or take an explicit resource; token
   integers are per-resource, so a same-valued token from another resource is
   indistinguishable (P2).
4. **Owner/caller separation** and a different-UID broker/owner key; do not
   expose `_writer`, `state_dir`, `db_path` or `_capability_key`; move owner
   `issue`/`revoke`/`rebind` behind a separate principal.
5. **P0 legacy closure:** route `acquire_lease`/`check_writer`, JSON revoke,
   finalize/effect and migration through the fenced broker; real finalize
   remains `EFFECT_COMMIT_NOT_PROVEN`.

## 11. Reproduction

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-381-043e08b
cd "$SNAP"
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round13>/probes/a_prime_381_binding.py --mode api       # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round13>/probes/a_prime_381_binding.py --mode binding   # rc 1 (B3 P1)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round13>/probes/a_prime_381_binding.py --mode raw       # rc 0 (EXPECTED)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round13>/probes/a_prime_381_binding.py --mode race      # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round13>/probes/a_prime_381_binding.py --mode osuid     # rc 0 (NOT_RUN)
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400–600 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push.

## 12. Conclusion

- #381 is **GREEN** for the #380 cross-token P1 and for post-hold TTL/policy/
  grant rollback; 82P author, 43/43 round10, 18/18 round11, 4/4 scope.
- It is **RED** for the historical-replay capability P1; the #380 C6 gap is
  unchanged. Same-process owner/key/db_path, token type and token-scope are P2.
- Raw DB/legacy bypass remains P0 `PRODUCTION_WIRING_REQUIRED`; OS isolation,
  real provider and real finalize/effect are NOT_RUN / `EFFECT_COMMIT_NOT_PROVEN`.
- Final: **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**, no `QA_SIGNED`.
