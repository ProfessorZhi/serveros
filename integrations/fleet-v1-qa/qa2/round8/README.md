# QA2 Round 8 — Context→Grant rebinding RED witness

**Verdict: `PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** New **P1 context fence**
gap and **P2 context generation** gap; PRODUCTION_DEPENDENCY (real owner/peer/
wiring) NOT_RUN. No `QA_SIGNED`. This is a first-round RED report; no S1 source
was modified.

## Pinned snapshot

| Item | Value |
|---|---|
| #377 exact head | `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` |
| base #376 | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| RC-v9 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |

## Results

| Gate | rc | Result |
|---|---|---|
| context probe controls | 0 | 3/3: grant removed / policy_ref mismatch / grant_epoch bump still refuse |
| context probe findings | 1 | **5/5 P1**: context grant rebind, actor change, context delete, same-grant-ref mutation, commit does not persist context |
| #377 author 51 tests (round7 same SHA) | 0 | 51 passed |
| round7 independent controls @#377 | 0 | 29/29 |
| #377 `check_scope` vs #376 | 0 | changed=3 allowed=3 forbidden=0 |

## RED evidence

- `proto_commit` columns have no `context_ref` and no `actor_ref`; `owner` is
  the policy actor, not the trusted context actor. `proto_context` has no
  epoch/generation/digest.
- `check_writer_fenced` reads lease/commit/policy/grant, **not**
  `proto_context`; `commit.grant_ref == grant.grant_ref` is tautological
  (grant is selected by the commit key).
- After a fenced acquire:
  * `UPDATE proto_context SET grant_ref='grant:proto-2'` (same policy/epoch):
    old token `authorized:true`.
  * `UPDATE proto_context SET actor_ref='human:B'`: old token
    `authorized:true`; lease/commit owner remains `human:A`.
  * `DELETE FROM proto_context`: old token `authorized:true`.
  * same-grant-ref context actor mutation: old token `authorized:true`.
- Existing controls still refuse: grant removed / policy_ref mismatch →
  `BLOCKED_OWNER_POLICY`; grant_epoch bump → `BLOCKED_STALE_EPOCH`.

## Minimal fence invariant (recommendation)

1. Add `context_ref`, `actor_ref`, and `context_epoch` (or `context_digest`)
   to `proto_commit`, written in the acquire transaction from the trusted
   `proto_context` row.
2. Add `context_epoch` (or a content digest) to `proto_context`; every actor/
   grant rebind must bump it, or context must be immutable for the grant
   generation.
3. `check_writer_fenced` reads `proto_context` in the same `BEGIN IMMEDIATE`
   and requires context existence, `context.context_ref == commit.context_ref`,
   `context.actor_ref == commit.actor_ref == lease.owner == policy.actor_ref`,
   `context.grant_ref == commit.grant_ref`, and
   `context.context_epoch == commit.context_epoch`; otherwise
   `BLOCKED_STALE_CONTEXT` / `BLOCKED_OWNER_POLICY`.
4. Context/grant rebinding must require a new acquire (new context/grant
   generation); it must not inherit the old token.

## Layout / run

```text
QA2-ROUND8-CONTEXT-GRANT-REBIND-RED-2026-10-21.md   full RED report
probes/a_prime_377_context_rebind.py                independent context witness
logs/                                               exact logs + rc
```

```sh
cd <8b42de47 snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_context_rebind.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_context_rebind.py --mode findings
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`; no production SQLite/Daemon/Service, permission, OAuth, provider or
merge/force-push/delete. Synthetic owner/context/uid are fixtures, not a real
owner or real OS peer.
