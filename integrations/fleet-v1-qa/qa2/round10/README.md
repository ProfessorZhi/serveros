# QA2 Round 10 — #379 context generation: partial

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.
#379 closes the API-level context ABA and permanent content mutation, but
`INSERT OR REPLACE`/plain `INSERT` bypass the triggers, commit immutability and
the generation, and content `A→B→A` revives the old token.

## Pinned snapshot

| Item | Value |
|---|---|
| #379 exact head | `7245fc6b3711172251e20450d4356abff8af91ee` |
| base #378 | `069f10cce084b95ef7e60ecd2c32784856002d54` |
| diff vs #378 | 3 files, +324/−48, `SCOPE_OK` |
| author suite | **62 passed rc0** |

## Results

| Gate | rc | Result |
|---|---|---|
| round10 controls | 0 | **43/43** |
| round10 findings | 1 | **10/10 RED** (P1×8, P2×2) |
| round-6 controls at #379 | 0 | **29/29** |
| round-6 production findings | 1 | legacy P0×2 + peer P2 |
| `check_scope.py` | 0 | changed=3 allowed=3 forbidden=0 |

## GREEN

- `rebind_context` CAS: A→B→A advances `context_epoch` 1→2→3; old token
  `BLOCKED_STALE_CONTEXT_EPOCH`.
- Raw context `UPDATE` without epoch increase / same value / 0 / backward:
  `sqlite3.IntegrityError`.
- Context DELETE: `IntegrityError`. Commit UPDATE/DELETE: `IntegrityError`.
- 18 permanent content columns: `BLOCKED_STALE_CONTENT` or stronger, no side
  effect.
- Digest is canonical and sensitive to every column.
- Grant epoch bump, unchanged positive, historical replay, missing fence row,
  invalid SQL, two-process CAS contention, WAL/integrity: GREEN.

## New RED

- **P1 F1/F2:** `INSERT OR REPLACE` on `proto_context` does not fire the DELETE
  trigger (`recursive_triggers=0`); it rolls the epoch back to 1 and the old
  token returns `authorized:true` after an API A→B→A.
- **P1 F3:** `INSERT OR REPLACE` rewrites a `proto_commit` row (epoch/grant/
  digest); old token authorized.
- **P1 F8:** plain `INSERT` appends a forged commit for the same
  `(resource, token)`; `rowid DESC` selects it; old token authorized.
- **P1 F4/F5/F9/F10:** grant/policy content `A→B→A` re-matches the digest and
  revives the old token. Digests are equality snapshots, not generations.
- **P2 F6:** the digest is an unkeyed SHA-256 over a mutable row; a writer can
  reforge the commit digest — `PROTOTYPE_ONLY / NO_OWNER_TRUST`.
- **P2 F7:** raw SQL can jump `context_epoch` to 999; old token refused but the
  CAS API is poisoned.

## Layout / run

```text
QA2-ROUND10-CONTEXT-GENERATION-ABA-2026-10-21.md   full report
probes/a_prime_379_generation.py                   generation/ABA witness
logs/                                              exact logs + rc + metadata
```

```sh
cd <7245fc6b snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_379_generation.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_379_generation.py --mode findings
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`; temp SQLite/fork only. No production SQLite/Daemon/Service,
permission, OAuth, provider, key or merge/force-push/delete. Real owner/key/
reissue, two real uids, kernel `SO_PEERCRED`, production wiring, Windows and
Provider/OAuth are `NOT_PROVEN`. Legacy `acquire_lease`/`check_writer` remain
P0 `PRODUCTION_WIRING_REQUIRED`, correctly disclosed and not fixed.
