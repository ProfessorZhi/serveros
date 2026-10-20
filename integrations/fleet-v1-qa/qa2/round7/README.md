# QA2 Round 7 — A′ P1 fix #377

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED` /
`V1_RELEASE`.

## Pinned refs

| Item | Value |
|---|---|
| #377 exact head | `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` |
| base #376 exact | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| base RC-v9 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff vs #376 | 3 files, +64/−7 (README/atomic_fence/tests) |
| `check_scope` vs #376 | changed=3 allowed=3 forbidden=0, SCOPE_OK |

## Results

| Gate | rc | Result |
|---|---|---|
| Author 51 tests | 0 | 51 passed |
| #364 three-file control | 0 | 27 passed, 1 skipped |
| round6 independent controls @#377 | 0 | 29/29 |
| extra lineage controls @#377 | 0 | 5/5 |
| extra lineage controls @#376 (RED) | 1 | grant-epoch + policy-ref ACCEPTED |
| round6 findings @#377 | 1 | P0×2 + P2 remain; prior P1 now ok |
| extra findings @#377 | 1 | context P2, legacy P0×2, peer P2 |

## Closed P1

At #376, a lease committed with `commit.grant_epoch=1` still got
`authorized:true` after `proto_grant.grant_epoch` was bumped to 2 with
policy/authority unchanged. At #377, `check_writer_fenced` returns
`BLOCKED_STALE_EPOCH`; a policy_ref lineage mismatch returns
`BLOCKED_OWNER_POLICY`; all refusals leave lease/commit/token unchanged
(`before == after`). Valid token is accepted point-in-time with
`effect_eligible:false`. The fix is at `atomic_fence.py:761-778` and the new
test is at `tests/test_s1_atomic_prototype.py:599-616`.

## Residual P2 / P0

- **P2** context/grant rebind: the new `commit.grant_ref == grant.grant_ref`
  check is tautological (the grant is selected by `commit.grant_ref`), and the
  fenced check does not read the current context. A context rebound to a
  different grant with the same `grant_epoch` still yields
  `authorized:true`. If the owner source allows a reissue without bumping
  `grant_epoch`, escalate to P1; the owner protocol must bump the generation.
- **P0 production-wiring barriers remain, correctly disclosed**:
  legacy `ServerOSStore.acquire_lease` writes a new lease after revoke;
  legacy `ServerOSStore.check_writer` accepts a pre-revoke token. README marks
  `PRODUCTION_WIRING_REQUIRED`; the PR says NOT fixed.
- P2: fenced check takes caller `owner`/`token` strings and does not bind the
  constructor synthetic peer/context; README flags `NO_TRUSTED_PEER`/
  `SO_PEERCRED_NOT_BOUND`.
- NOT_RUN: real owner key/issuance/revocation, two real OS uids, kernel
  `SO_PEERCRED`, production daemon/WorkEngine wiring/migration, real Windows,
  Provider/OAuth/send; power-loss/fsync and finalize same transaction
  NOT_PROVEN.

## Layout / run

```text
QA2-ROUND7-A-PRIME-377-P1-FIX-2026-10-21.md   full report
probes/a_prime_377_extra.py                    independent lineage/no-side-effect witness
logs/                                          exact logs + rc
```

```sh
cd <8b42de47 snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_extra.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_extra.py --mode findings
pytest -q -p no:cacheprovider tests/test_s1_atomic_prototype.py
python integrations/fleet-v1-atomic-prototype/check_scope.py \
  01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`; no full pytest, production SQLite/Daemon/Service, permission,
OAuth, provider or merge/force-push/delete.
