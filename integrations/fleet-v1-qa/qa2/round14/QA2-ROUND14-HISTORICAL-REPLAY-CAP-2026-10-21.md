# QA2 Round 14 — #382 historical replay no-capability: P1 RED→GREEN

**Pinned head:** `b7dedd116425d7e645a034e8559fa103562b88eb` (Draft PR #382)
**Base:** `043e08b916b2b511b56b30a419d21ad4450451c2` (PR #381)
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

The previously open **historical-replay capability P1** is **GREEN at #382**:
the replay receipt has no capability, no-capability `check_use`/`commit_effect`
return `BLOCKED_CAPABILITY` and write no marker. The original capability is
**not revoked** by a replay; it remains valid only while the lease/epoch fence
is live and is refused after revoke. P0 production writer/legacy, P2
same-process/token-type/test-harness items and C OS isolation remain open;
`EFFECT_COMMIT_NOT_PROVEN` remains.

## 1. Pinned identity and scope

| Check | Result |
|---|---|
| PR #382 | OPEN, **Draft**, MERGEABLE |
| `refs/pull/382/head` | `b7dedd116425d7e645a034e8559fa103562b88eb` |
| remote branch `feat/s1-historical-replay-cap-20261021` | same exact SHA |
| base #381 | `043e08b916b2b511b56b30a419d21ad4450451c2` (no drift) |
| #380 | `cbbfb63a76072871edfa6aa639355f5b63cc5853` (unchanged) |
| local snapshot | identical to `git archive b7dedd11…`; only `.snapshot-sha` |
| diff | exactly 3 allowed paths, `+111 / −6` |
| paths | README, `single_write_entry.py`, `tests/test_s1_atomic_prototype_single_write.py` |
| `check_scope.py` vs #381 | `changed=3 allowed=3 forbidden=0`, `SCOPE_OK` |
| author tests | old **62P**, new **24P**, combined **86P** |
| old #379 code | round10 controls **43/43**, round11 API **18/18** |

Logs: `logs/a-prime-382-metadata.log`, `logs/a-prime-382-check-scope.log`,
`logs/a-prime-382-author-*.log`, `logs/a-prime-382-local-equals-archive.log`.

## 2. Matrix

| Gate | rc | Result |
|---|---|---|
| author old / new / combined | 0 | **62 / 24 / 86 passed** |
| round10 controls at #382 | 0 | **43/43** |
| round11 API controls at #382 | 0 | **18/18** |
| independent `api` | 0 | **12/12 PASS** |
| independent `binding` | 0 | **6 PASS + 2 P2 observations** |
| independent `raw` | 0 | **5/5 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** |
| independent `osuid` | 0 | **2/2 NOT_RUN** |
| #381 replay baseline (same probe) | 1 | **B3/B3b/B3c RED** (capability present) |

`raw` rc 0 means the predicted bypass reproduced, not product PASS.

## 3. P1 historical replay: #381 RED → #382 GREEN

Same independent probe on the two exact heads:

| Observation | #381 `043e08b9` | #382 `b7dedd11` |
|---|---|---|
| replay `prototype_status` | `PROTOTYPE_HISTORICAL_REPLAY` | same |
| replay `authorized/historical/replayed` | `false/true/true` | same |
| replay `capability_scope` | **`API_ONLY`** | **`HISTORICAL_AUDIT_ONLY`** |
| replay `capability` field | **present** | **absent** |
| `check_use(req, token, replay capability-or-None)` | **`ACCEPTED authorized:true`** | **`BLOCKED_CAPABILITY`** |
| `commit_effect(...)` | **`ACCEPTED`, effect_count=1** | **`BLOCKED_CAPABILITY`, effect_count=0** |

At #381 the replay had a usable capability and the old probe wrote an effect
marker. At #382 the replay has no capability and no-capability calls are
refused with no marker.

Exact #382 evidence:

```text
first = acquire(req:1)
  -> fresh: authorized:true, historical:false, replayed:false,
     capability_scope:"API_ONLY", capability present
replay = acquire(req:1)
  -> authorized:false, historical:true, replayed:true,
     capability_scope:"HISTORICAL_AUDIT_ONLY", capability ABSENT
check_use(replay.request_id, replay.token, None)      -> BLOCKED_CAPABILITY
commit_effect(..., None)                              -> BLOCKED_CAPABILITY
effect_count = 0
```

## 4. Original capability is not revoked (explicit boundary)

The fix does not claim to revoke the original capability. Independent B3b:

```text
first = acquire(req:1)             # cap present
replay = acquire(req:1)            # no cap
check_use(req:1, token1, cap1)     -> ACCEPTED authorized:true
commit_effect(req:1, token1, cap1) -> ACCEPTED, effect_count=1
```

After revoke (B3c):

```text
revoke_policy(...)
replay = acquire(req:1)            # historical, no capability
check_use(replay, None)            -> BLOCKED_CAPABILITY
check_use(req:1, token1, cap1)     -> POLICY_DENIED
commit_effect(req:1, token1, cap1) -> POLICY_DENIED
effect_count = 0
```

So the replay itself grants no current authority, while the original live
capability keeps its original semantics until the fence moves.

## 5. Same request, different facts

`acquire(req:1)` then `acquire(req:1, ttl_s=31.0)` →
**`IDEMPOTENCY_CONFLICT`**, effect count 0. The replay path does not turn a
different-facts request into an audit-only success.

## 6. Regression from #381/#380 (sampled independently)

| Case | Result |
|---|---|
| cross-token `check_use(req1, token2, cap1)` / `commit_effect` | `STALE_TOKEN`, effect 0 (B1) |
| cross-resource capability | `BLOCKED_CAPABILITY`, no B authority (B2) |
| post-hold TTL crossing | `LEASE_EXPIRED`, marker rolled back, ledger unchanged (A4) |
| post-hold policy crossing | `POLICY_EXPIRED_AT_COMMIT`, marker rolled back (A5) |
| post-hold grant crossing | `GRANT_EXPIRED_AT_COMMIT`, marker rolled back (A6) |
| grant epoch 1→2 | `BLOCKED_STALE_EPOCH`, ledger unchanged (A7) |
| context A→B→A | `BLOCKED_STALE_CONTEXT_EPOCH`, ledger unchanged (A8) |
| effect replay / conflict / same-request different ref | idempotent `authorized:false`; `IDEMPOTENCY_CONFLICT`; `EFFECT_ALREADY_COMMITTED` (A3) |
| revoke-first / effect-first hold / crash-before-COMMIT | `POLICY_DENIED` effect 0; revoke blocked `OperationalError`; `os._exit(17)` no marker (RACE1–3) |
| fresh acquire/use/effect | authorized, marker, no production marker (A1) |

No `verified` / `COMMIT_FENCED`; classification `API_ONLY_CORRECTNESS`.

## 7. Still open (retained, not silently ignored)

### P0 — production Writer authorization / legacy entry closure

- Raw same-UID SQLite can insert `proto_effect` after revoke, rewrite
  context/commit and use the readable HMAC key to make `check_use` accept;
  `chmod 0600` DB is still same-UID writable; a per-connection authorizer is
  bypassed by a second connection.
- Legacy `ServerOSStore.acquire_lease`/`check_writer` still bypass
  (`commit_count=1`, legacy authorized, fenced `POLICY_DENIED`); JSON revoke,
  finalize/effect and migration remain outside the fence.
- These are disclosed `RAW_DB_WRITE_BYPASS_EXPECTED` /
  `PRODUCTION_WIRING_REQUIRED`, not fixed.

### P2 — prototype caveats / test harness

- Same-process owner methods, readable `_capability_key`,
  `_writer.state_dir`/`db_path`, and constructor-selected actor/uid/key remain
  reachable (B4); README now discloses this. It is not a single-writer
  process/IPC/OS boundary.
- `True` and `1.0` are accepted as token 1 (`int()` comparison; `"1"` is
  `STALE_TOKEN`); no authority beyond the same integer token, but strict
  `isinstance(token, int) and not bool` is missing (B5).
- Token values are per-resource integers; a same-valued token from another
  resource is accepted for the capability's own resource A (not B). P2
  token-scope ambiguity (B2).
- `check_scope.py` still uses the loosened
  `tests/test_s1_atomic_prototype*` prefix introduced in #380.
- The 24P suite now covers replay no-capability, original-cap-live,
  replay-after-revoke and different-facts conflict, but not bool/float token
  type or payload-digest semantics.
- `payload_digest` is a caller-supplied string; the marker has no
  `context_ref`/`grant_ref`.
- The PR's `#364 production fail-closed control` (26P/1S) was not
  independently rerun in this round.
- Synthetic owner/clock/peer labels remain fixtures.

### NOT_RUN / NOT_PROVEN

Real OS isolation, owner key, two real UIDs, kernel `SO_PEERCRED`, production
daemon/Fleet/WorkEngine wiring, real Windows, Provider/OAuth, power-loss/fsync,
real `finalize_run_verified`/effect. `EFFECT_COMMIT_NOT_PROVEN`.

## 8. Classification and final status

- **P1 API path:** the historical-replay capability P1 is **closed** in the
  tested matrix; no new P1 reproduced. Cross-token and post-hold are also
  GREEN.
- **P0:** raw DB writer and legacy entry closure remain open and disclosed.
- **P2:** same-process caveat, token type/scope, payload digest, check_scope
  loosening, #364 not rerun, synthetic evidence.
- **C:** OS isolation NOT_RUN.
- Closing the P1 does **not** make this V1-signed: there is no real
  owner/writer isolation, no production finalize/effect, and legacy/raw
  writers still bypass the fence.
- Final: **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**, no `QA_SIGNED`, no
  merge, no V1.

## 9. Minimal remaining work

1. **P0:** a different-UID broker/owner-key signer that owns the DB and the
   fenced writer; route legacy `acquire_lease`/`check_writer`, JSON revoke,
   finalize/effect and migration through it.
2. **P2:** strict token type; bind capability to resource or take an explicit
   resource; do not expose `_writer`/`state_dir`/key/owner methods to the
   caller; persist `context_ref`/`grant_ref` and a server-computed payload
   digest in the effect marker; restore an exact test allowlist.
3. **NOT_PROVEN:** real finalize/effect same transaction, real OS isolation and
   provider evidence.

## 10. Reproduction

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-382-b7dedd1
cd "$SNAP"
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode api       # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode binding   # rc 0 (P2 observations)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode raw       # rc 0 (EXPECTED)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode race      # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode osuid     # rc 0 (NOT_RUN)

# #381 RED baseline for the same binding probe
SNAP=$ROOT/checkouts/a-prime-381-043e08b
cd "$SNAP"
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round14>/probes/a_prime_382_replay.py --mode binding   # rc 1 (B3/B3b/B3c RED)
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400–600 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push.
