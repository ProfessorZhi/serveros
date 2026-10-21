# QA2 Round 12 — #380 single-write entry

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

Pinned #380 `cbbfb63a76072871edfa6aa639355f5b63cc5853` (Draft, base #379
`7245fc6b…`); 5 allowed paths, `SCOPE_OK`; old #379 code intact
(62P / 75P / 43 controls / 18 API controls).

## Matrix

| Gate | rc | Result |
|---|---|---|
| author old | 0 | 62 passed |
| author new | 0 | 13 passed |
| author combined | 0 | 75 passed |
| independent `api` | 0 | **15/15 PASS** |
| independent `capability` | 1 | **6/6 P1 RED** |
| independent `raw` | 0 | **6/6 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** |
| independent `osuid` | 0 | **2/2 NOT_RUN** |

## P1 API-path findings

- **C1 cross-token:** capability for request 1 (token 1, epoch 1) is accepted
  with token 2 (epoch 2); `commit_effect` writes a marker with
  `request_id=req:1`, `token=2`, `context_epoch=2`.
- **C6 replay capability:** a `PROTOTYPE_HISTORICAL_REPLAY` receipt
  (`authorized:false`, `historical:true`) still carries a capability;
  `check_use`/`commit_effect` accept it and write an effect marker.
- **C2–C5 same-process object:** caller can call owner-side `issue` with chosen
  epochs, read `_capability_key`, reach `_writer.state_dir`/`db_path`, and
  choose `actor_ref`/`synthetic_peer_uid` in the constructor. No socket/IPC/UID
  separation; this is prototype capability correctness.

## B (expected) and C (not run)

- Raw same-UID SQL inserts an effect marker, rewrites context+commit, chmod
  0600 still writable by same UID, per-connection authorizer bypassed, legacy
  `ServerOSStore.acquire_lease`/`check_writer` bypass retained. Labeled
  `RAW_DB_WRITE_BYPASS_EXPECTED` / `BLOCKED_DB_WRITER_ISOLATION`.
- `osuid`: 2/2 NOT_RUN (no sudo/setuid/chown; no SO_PEERCRED).
- `EFFECT_COMMIT_NOT_PROVEN`; no `verified`/`COMMIT_FENCED`.

## Layout / run

```text
QA2-ROUND12-SINGLE-WRITE-ENTRY-2026-10-21.md   full report
probes/a_prime_380_single_writer.py            A/B/C + capability + race witness
logs/                                          exact logs + rc + metadata
```

```sh
cd <cbbfb63a snapshot>
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_380_single_writer.py --mode api
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_380_single_writer.py --mode capability
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_380_single_writer.py --mode raw
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_380_single_writer.py --mode race
PYTHONPATH=$PWD:$PWD/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_380_single_writer.py --mode osuid
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push. Real owner key, two real
uids, kernel peer, production wiring, Windows and Provider/OAuth are NOT_RUN.
