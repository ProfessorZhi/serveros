# QA2 Round 6 — A′ prototype hardening #376

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED` /
`V1_RELEASE`.

## Pinned refs

| Item | Value |
|---|---|
| Requested exact head | `02688cecf7df37dfde6e81c5a632595783148a8d` |
| Current remote PR head | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| Base #375 head | `d8edfe8d7989674775bc94f373008368e3284e3e` |
| Base RC-v9 / #372 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| d8edfe8..02688cec | 4 files, +639/−131 |
| 02688cec..01e8a9c | README +12 only; prototype/test bytes identical |

HEAD DRIFT: the requested `02688cec` is the hardening commit; the current
remote head is `01e8a9c` (README-only delta). Both run **50P** on Linux.

## Results

| Gate | rc | Result |
|---|---|---|
| Author 50 tests @02688cec | 0 | 50 passed |
| Author 50 tests @01e8a9c | 0 | 50 passed |
| Independent controls | 0 | **29/29** |
| Independent findings | 1 | **4/4** (P0×2, P1×1, P2×1) |
| `check_scope.py` @02688cec | 0 | changed=4 allowed=4 forbidden=0, SCOPE_OK |
| #364 three-file control | 0 | 27 passed, 1 skipped |

## Closed in prototype scope

- **A commit-phase re-check**: FixedClock jump after `hold` past TTL / policy /
  grant / backward-before-window → `LEASE_EXPIRED_AT_COMMIT` /
  `POLICY_EXPIRED_AT_COMMIT` / `GRANT_EXPIRED_AT_COMMIT` / `POLICY_DENIED`;
  no lease, no `proto_commit`, no token advance. Within-window commit succeeds.
- **B historical replay**: same request id → `PROTOTYPE_HISTORICAL_REPLAY`
  `authorized:false historical:true effect_eligible:false`, same token;
  different authority/grant epoch pin → `IDEMPOTENCY_CONFLICT`; replay after
  revoke historical; new request `POLICY_DENIED`.
- **C processes**: blocked revoker gets exact `database is locked` while the
  acquirer holds `BEGIN IMMEDIATE` (no sleep); double acquire has one winner/
  one `LEASE_HELD_BY_OTHER`; crash-before-commit rollback; commit-then-`os._exit`
  reopen; WAL + `integrity_check=ok`. Power-loss/fsync NOT_PROVEN.
- **D fenced check**: valid token `PROTOTYPE_FENCED_CHECK authorized:true
  effect_eligible:false`; after policy revoke / grant expiry it refuses.
  Legacy `ServerOSStore.check_writer` still authorizes (production gap).
- **E constructor clock/peer**: `acquire` rejects per-call `now`/`peer_uid`
  with `TypeError`; missing/bool synthetic peer → `UNTRUSTED_ACTOR`; request
  self-asserted `uid`/`actor_ref`/`verified`/`authority`/`COMMIT_FENCED`/`now`
  → `INVALID_REQUEST`; no lease/commit. Constructor clock/peer are synthetic;
  kernel peer NOT_RUN.
- **F**: `REAL_FINALIZE_SAME_TXN` NOT_PROVEN; no production import; success
  receipt has no `verified` and `effect_eligible:false`.

## Findings

| ID | Severity | Finding |
|---|---|---|
| F1 | **P1** | `check_writer_fenced` compares commit `policy_epoch`/`authority_epoch` to the current policy, but **not `commit.grant_epoch` to `grant.grant_epoch`**. After `proto_grant.grant_epoch` 1→2 (same policy/authority), `check_writer_fenced` still returns `PROTOTYPE_FENCED_CHECK authorized:true`; expected `BLOCKED_STALE_EPOCH`. Static: `atomic_fence.py:739-768`; no grant-epoch comparison. Fix: add the comparison + regression test. |
| F2 | **P0** | legacy `ServerOSStore.acquire_lease` writes a new lease after revoke outside the fence (`proto_commit=0`, policy revoked). README marks `PRODUCTION_WIRING_REQUIRED`; PR says not fixed. |
| F3 | **P0** | legacy `ServerOSStore.check_writer` accepts a pre-revoke token after revoke; `check_writer_fenced` refuses it. README marks `PRODUCTION_WIRING_REQUIRED`; production must use the fenced check across the effect. |
| F4 | P2 | `check_writer_fenced` takes caller `owner`/`token` strings and does not read the constructor synthetic peer or current context; it is a token/authority check, not caller authentication. README flags `NO_TRUSTED_PEER`/`SO_PEERCRED_NOT_BOUND`; production must bind the kernel peer and hold the fence. |

P0 findings are production-wiring barriers, **not** #376 prototype
regressions; they are correctly disclosed and not claimed fixed. F1 is a
prototype-scope P1 in the new fenced check and should be fixed before the
fenced check is called complete.

## Evidence levels / NOT_RUN

`LOCAL_REAL_SQLITE` (temp DB, real WorkEngine schema/WAL/`id_sequences`/lease
helpers); `SYNTHETIC_OWNER/CLOCK/PEER`; `REAL_TEMP` for the temp DB.
`KERNEL_PEER_NOT_RUN`, `REAL_WINDOWS_NOT_RUN`, real owner key/issuance/revocation
NOT_RUN, production wiring/migration NOT_RUN, Provider/OAuth/send NOT_RUN,
power-loss/fsync NOT_PROVEN, `REAL_FINALIZE_SAME_TXN` NOT_PROVEN.

## Layout / run

```text
QA2-ROUND6-A-PRIME-376-HARDENING-2026-10-21.md   full report
probes/a_prime_376_probe.py                      independent A/B/C/D/E/F witnesses
logs/                                            exact logs + rc
```

```sh
cd <02688cec snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_376_probe.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_376_probe.py --mode findings
pytest -q -p no:cacheprovider tests/test_s1_atomic_prototype.py
python integrations/fleet-v1-atomic-prototype/check_scope.py \
  31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`; no full pytest, production SQLite/Daemon/Service, permission,
OAuth, provider or merge/force-push/delete.
