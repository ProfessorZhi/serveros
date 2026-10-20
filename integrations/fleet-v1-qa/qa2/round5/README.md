# QA2 Round 5 — A′ atomic fence prototype #375

**Verdict: `LOCAL_PROTOTYPE_QA` / `NOT_V1_SIGNED`.** The transaction-shape
controls pass at LOCAL + REAL_TEMP SQLite, but P0 production-wiring barriers and
P1/P2 gaps remain. No `QA_SIGNED` / `V1_RELEASE`.

## Pinned refs

| Item | Value |
|---|---|
| Requested exact head | `0c081f7786b23e9c2757440a41fcfd184bab815d` |
| Current remote PR head | `d8edfe8d7989674775bc94f373008368e3284e3e` |
| Base RC-v9 / #372 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| base..0c081f7 | 4 files, +1447 |
| 0c081f7..d8edfe8 | only `tests/test_s1_atomic_prototype.py` +9 (`FORK_ONLY` markers); prototype bytes identical |

The requested SHA and the current remote head differ; both were checked. The
author's PR body names the latest head `d8edfe8`; do not present it as
`0c081f7`.

## Results

| Gate | rc | Result |
|---|---|---|
| Author 36 tests @0c081f7 | 0 | 36 passed |
| Author 36 tests @d8edfe8 | 0 | 36 passed (Linux fork available) |
| `check_scope.py` @0c081f7 | 0 | `changed=4 allowed=4 forbidden=0`, `SCOPE_OK` |
| Independent controls (3 runs) | 0 | **39/39** each |
| Independent findings | 1 | **5/5** confirmed (P0/P1/P2) |

Key controls: revoke-first → no lease; acquire-first → historical token and new
request refused; barrier race with no sleep → serialized; two processes contend
→ one token/one commit, WAL, busy_timeout 30000, integrity ok; crash before
COMMIT → no lease/commit/token; restart replay → same token; invalid/caller-
injected matrix → no lease/commit; no production import; success receipt has no
`verified`.

## Findings

| ID | Severity | Finding |
|---|---|---|
| F1 | **P0** | legacy `ServerOSStore.acquire_lease` writes a new lease after policy revoke outside the fence. README discloses this production-wiring barrier (lines 54–71). |
| F2 | **P1** | a pre-revoke lease token is still accepted by `ServerOSStore.active_lease`/`check_writer` after revoke; the existing writer guard does not consult the proto authority epoch. Partially disclosed. |
| F3 | **P1** | `expected_authority_epoch`/`expected_grant_epoch` are not in the idempotency digest; same request id with a different pin replays instead of `IDEMPOTENCY_CONFLICT`. |
| F4 | P2 | `now`/`peer_uid` are caller arguments; a caller-chosen past `now` acquires while the policy is expired at the real clock. Production must supply a trusted clock and `SO_PEERCRED`; would be P0 if a route accepts caller-controlled `now`. |
| F5 | P2 | README lacks explicit `NO_TRUSTED_CLOCK`/`NO_TRUSTED_PEER`/`SO_PEERCRED` flags, though it correctly flags `NO_OWNER_TRUST`/`NO_DAEMON_WIRING`/two-real-uids. |

Trusted reads: `acquire` reads context/grant/policy inside one `BEGIN IMMEDIATE`
and writes lease + `proto_commit` in the same transaction; `revoke_policy` uses
the same DB/transaction. The P0/P1 gaps are outside the fence: the legacy store
writes the same `leases` table without `proto_*`, and `check_writer` reads the
lease row without the commit epoch. No JSON policy source is read.

## Evidence levels / NOT_RUN

- `LOCAL` + `REAL_TEMP` SQLite (temp DB, real WorkEngine schema, WAL,
  `id_sequences`, real lease helpers); `SYNTHETIC` owner policy/grant/context/
  uid/attestation; Linux fork.
- **NOT_RUN**: real owner key/issuance/revocation, two real OS uids/trusted
  peer, production daemon/Fleet/WorkEngine wiring, DB migration, real Windows,
  provider/OAuth/send. `finalize_run_verified` + lease same transaction is
  **NOT_PROVEN**.
- No production source, Grant, owner policy, WorkEngine, daemon, credential or
  service was modified.

## Layout / run

```text
QA2-ROUND5-A-PRIME-375-PROTOTYPE-2026-10-21.md   full report
probes/a_prime_independent_probe.py              independent controls/findings
logs/                                            exact logs + rc
```

```sh
cd <0c081f7 snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_independent_probe.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_independent_probe.py --mode findings
pytest -q -p no:cacheprovider tests/test_s1_atomic_prototype.py
python integrations/fleet-v1-atomic-prototype/check_scope.py \
  31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd
```

All runs bounded with `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
nice 10 / timeout`; no full pytest, provider, credential or production service.
