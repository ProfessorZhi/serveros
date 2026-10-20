# QA2 Round 11 — #379 threat-boundary matrix

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

Pinned #379 `7245fc6b3711172251e20450d4356abff8af91ee` (base #378
`069f10cc…`). Two threat classes are measured separately:

| Mode | Result | Meaning |
|---|---|---|
| A `api` (no direct SQL) | **18/18 PASS** | CAS/epoch/rollback/commit-time refusal/missing-fence fail-closed |
| B `db` (same-UID raw SQLite) | **10/10 EXPECTED_FAIL_UNTRUSTED_DB_WRITE** | tampering reproduced; `rc=0` is not product PASS |
| C `osuid` (different OS UID) | **2/2 NOT_RUN** | no sudo/setuid/chown; kernel peer not exercised |

Same-UID `0600` file/socket, SQLite triggers, unkeyed SHA-256 and a
per-connection `sqlite3` authorizer are **not** a trust boundary against a
same-UID DB writer.

## B-mode reproduced failures

- B1 `INSERT OR REPLACE` context: API epoch 3 → 1; old token `authorized:true`.
- B2 plain `INSERT` forged commit: `commit_count 1→2`; `rowid DESC` selects it.
- B3 `DROP TRIGGER` + same-epoch A→B→A: old token authorized.
- B4 drop delete+epoch triggers, DELETE/reinsert at epoch 1: old token authorized.
- B5 change content + reforge `grant_digest`: changed content authorized.
- B6 `PRAGMA recursive_triggers=ON` + plain INSERT: still authorized.
- B7 authorizer on conn1 denied; conn2 without it writes.
- B8 `chmod 0600` DB: same UID opens `r+b`.
- B9 `chmod 0600` Unix socket: same UID connects.
- B10 digest recomputed from row: unkeyed and equal.

## Layout / run

```text
QA2-ROUND11-THREAT-BOUNDARY-MATRIX-2026-10-21.md   full report
probes/a_prime_379_threat_boundary.py              A/B/C executable witness
logs/                                              exact logs + rc + metadata
```

```sh
cd <7245fc6b snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_379_threat_boundary.py --mode api
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_379_threat_boundary.py --mode db
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_379_threat_boundary.py --mode osuid
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 300 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push. Real owner key, two real
UIDs, kernel `SO_PEERCRED`, production wiring, Windows and Provider/OAuth are
`NOT_PROVEN`. Legacy `acquire_lease`/`check_writer` remain P0
`PRODUCTION_WIRING_REQUIRED`.
