# QA2 Round 14 — #382 historical replay no-capability

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

Pinned #382 `b7dedd116425d7e645a034e8559fa103562b88eb` (Draft, base #381
`043e08b9…`); 3 allowed paths `SCOPE_OK`; author 62P/24P/**86P**; old #379
code intact (round10 43/43, round11 18/18). #381/#380 heads unchanged.

## P1 replay: #381 RED → #382 GREEN

| Observation | #381 | #382 |
|---|---|---|
| replay `capability_scope` | `API_ONLY` | `HISTORICAL_AUDIT_ONLY` |
| replay capability | present | **absent** |
| `check_use` with replay cap / None | **ACCEPTED authorized:true** | **BLOCKED_CAPABILITY** |
| `commit_effect` | **ACCEPTED, marker 1** | **BLOCKED_CAPABILITY, marker 0** |

The original capability is **not revoked**: while the lease/epoch fence is
live it still authorizes (B3b); after revoke it is `POLICY_DENIED` (B3c).
Same request with different facts → `IDEMPOTENCY_CONFLICT` (B3d).

## Results

| Gate | rc | Result |
|---|---|---|
| author old / new / combined | 0 | 62 / 24 / 86 passed |
| independent `api` | 0 | **12/12 PASS** |
| independent `binding` | 0 | **6 PASS + 2 P2 observations** |
| independent `raw` | 0 | **5/5 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** |
| independent `osuid` | 0 | **2/2 NOT_RUN** |
| #381 same binding probe | 1 | B3/B3b/B3c RED (capability present) |

## Retained open items

- **P0:** raw same-UID DB marker/context+commit rewrite (with readable key),
  chmod 0600 same-UID, per-connection authorizer, legacy
  `ServerOSStore.acquire_lease`/`check_writer` bypass — disclosed
  `RAW_DB_WRITE_BYPASS_EXPECTED` / `PRODUCTION_WIRING_REQUIRED`.
- **P2:** same-process owner issue/key/`_writer`/`db_path` (disclosed);
  bool/float accepted as token 1; per-resource token-value ambiguity;
  `check_scope` loosened prefix; `payload_digest` caller string; `#364`
  control not independently rerun.
- **NOT_RUN:** OS isolation, real owner key/two uids/`SO_PEERCRED`, production
  wiring, provider, real finalize/effect; `EFFECT_COMMIT_NOT_PROVEN`.
- No `verified`/`COMMIT_FENCED`.

## Layout / run

```text
QA2-ROUND14-HISTORICAL-REPLAY-CAP-2026-10-21.md   full report
probes/a_prime_382_replay.py                      API/binding/raw/race/C witness
logs/                                             exact logs + rc + metadata
```

```sh
cd <b7dedd11 snapshot>
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_382_replay.py --mode api
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_382_replay.py --mode binding
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_382_replay.py --mode raw
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_382_replay.py --mode race
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_382_replay.py --mode osuid
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400–600 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push.
