# QA2 Round 13 — #381 exact commit/token binding

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

Pinned #381 `043e08b916b2b511b56b30a419d21ad4450451c2` (Draft, base #380
`cbbfb63a…`); 4 allowed paths `SCOPE_OK`; author 62P/20P/82P; old #379 code
intact (round10 43/43, round11 18/18).

## Results

| Gate | rc | Result |
|---|---|---|
| author old / new / combined | 0 | 62 / 20 / 82 passed |
| independent `api` | 0 | **12/12 PASS** |
| independent `binding` | 1 | **B1/B2 PASS, B3 P1 RED, B4/B5 P2 observed** |
| independent `raw` | 0 | **5/5 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** |
| independent `osuid` | 0 | **2/2 NOT_RUN** |

## #380 RED → #381 GREEN

- Cross-token `check_use(req1, token2, cap1)` and `commit_effect(...)`:
  #380 `ACCEPTED` + mixed marker → #381 **`STALE_TOKEN`, effect 0**.
- Post-hold TTL/policy/grant crossing: `LEASE_EXPIRED` /
  `POLICY_EXPIRED_AT_COMMIT` / `GRANT_EXPIRED_AT_COMMIT`, marker rolled back,
  lease/token/commit/effect ledger unchanged.
- Cross-resource capability mixing: `BLOCKED_CAPABILITY`; same-valued token
  from another resource authorizes only the capability's own resource (P2
  token-scope ambiguity).

## Still open

- **P1 historical replay capability:** `acquire` still attaches a capability
  to a `PROTOTYPE_HISTORICAL_REPLAY` receipt (`authorized:false`,
  `historical:true`); `check_use`/`commit_effect` accept it and write a marker.
  This is the #380 C6 gap unchanged; cross-token is not all P1s.
- **P2 same-process caveat (now disclosed):** owner `issue`, readable
  `_capability_key`, reachable `_writer`/`db_path`, constructor identity.
- **P2 token type:** `True` and `1.0` accepted as token 1; no escalation.
- **P0 raw DB/legacy:** expected `RAW_DB_WRITE_BYPASS_EXPECTED`; legacy
  `acquire_lease`/`check_writer` still bypass; disclosed, not fixed.
- **NOT_RUN:** OS isolation, real owner/uid/peer, provider, production
  finalize/effect; `EFFECT_COMMIT_NOT_PROVEN`.

## Layout / run

```text
QA2-ROUND13-COMMIT-TOKEN-BINDING-2026-10-21.md   full report
probes/a_prime_381_binding.py                    A/binding/raw/race/C witness
logs/                                            exact logs + rc + metadata
```

```sh
cd <043e08b9 snapshot>
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_381_binding.py --mode api
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_381_binding.py --mode binding
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_381_binding.py --mode raw
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_381_binding.py --mode race
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_381_binding.py --mode osuid
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400–600 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push. Real owner key, two real
uids, kernel peer, production wiring, Windows and Provider/OAuth are NOT_RUN.
