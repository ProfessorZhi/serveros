# QA2 Round 9 — #378 rebind GREEN, ABA revival RED

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.
#378 closes the persistent context rebind/actor/deletion gap (round 8), but an
ABA `A→B→A` revives the old token to `authorized:true`; same-generation scope
mutation remains a disclosed owner-protocol P2.

## Pinned snapshot

| Item | Value |
|---|---|
| #378 exact head | `069f10cce084b95ef7e60ecd2c32784856002d54` |
| base #377 | `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` |
| base #376 | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| RC-v9 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff vs #377 | 3 files, +196/−39, `SCOPE_OK` |
| author suite | 54 passed rc0 |

## Results

| Gate | #377 | #378 |
|---|---|---|
| round-8 controls | 3/3 rc0 | 3/3 rc0 |
| round-8 findings | **5/5 RED rc1 (P1)** | **5/5 GREEN rc0** |
| ABA/content controls | — | 6/6 rc0 |
| ABA/content findings | — | **7/7 RED rc1 (P1×3, P2×4)** |
| round-6 controls | 29/29 (round 6) | **29/29 rc0** |
| round-6 production findings | P0×2 + P2 | P0×2 + P2 |

## New findings

- **P1 ABA revival (F1–F3):** after a fenced acquire, a transient context grant
  `A→B→A`, context actor `A→B→A`, or grant actor `A→B→A` is refused in the
  middle leg (`BLOCKED_STALE_CONTEXT`) but the **old token becomes
  `authorized:true` again** after the original state is restored. No
  `context_epoch`/digest and no monotonic generation exists.
- **P2 same-generation content (F4–F6):** narrowing `grant.items/actions` or
  `policy.projects/items/actions`, or extending the grant window in place
  without a `grant_epoch` bump, still returns `authorized:true`. The commit
  stores no scope/window digest. README discloses the owner-protocol
  requirement; the prototype could still add digests.
- **P2 mechanism (F7):** `proto_commit` has `context_ref`/`context_actor` but
  no `context_epoch`/digest and no grant/policy scope or window digest;
  `proto_context` has only `context_ref, actor_ref, grant_ref`.
- **P0 production barriers (disclosed):** legacy `ServerOSStore.acquire_lease`
  writes after revoke outside the fence; legacy `check_writer` accepts a
  pre-revoke token. README/PR mark them `PRODUCTION_WIRING_REQUIRED`; not fixed.

## Layout / run

```text
QA2-ROUND9-ABA-CONTENT-MUTATION-2026-10-21.md   full report
probes/a_prime_378_aba.py                       ABA + content witness
probes/a_prime_377_context_rebind.py            round-8 witness, #378 schema
logs/                                           exact logs + rc + metadata
```

```sh
cd <069f10cc snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_378_aba.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_378_aba.py --mode findings
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_context_rebind.py --mode findings
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`. No production SQLite/Daemon/Service, permission, OAuth, provider,
key or merge/force-push/delete. Synthetic owner/context/clock/peer are
fixtures, not a real owner or real OS peer. Real owner/key/reissue, two real
uids, kernel `SO_PEERCRED`, production wiring, Windows and provider/OAuth are
`NOT_PROVEN`.
