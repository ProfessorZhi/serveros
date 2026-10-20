# QA2 Round 9 — #378 immediate rebind GREEN, ABA revival RED

**Pinned head:** `069f10cce084b95ef7e60ecd2c32784856002d54` (Draft PR #378)
**Base:** `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` (PR #377)
**Earlier bases:** #376 `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f`, RC-v9 #372 `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd`
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

#378 closes the *persistent* context→grant rebind, actor remap and context
deletion that round 8 reported: all now return `BLOCKED_STALE_CONTEXT` with no
side effect. It does **not** close the context lineage, because the fence has
no monotonic context generation: an ABA rebind `A→B→A` (grant or actor) makes
the **old token authorized:true again** after the state is restored. That is a
new **P1 prototype revival** (F1–F3). Same-generation in-place grant/policy
scope mutation remains accepted as a disclosed **P2 owner-protocol**
dependency (F4–F6). The legacy production bypasses remain **P0
PRODUCTION_DEPENDENCY** and are correctly disclosed. Therefore #378 must not be
called fully closed and no `QA_SIGNED` is possible.

## 1. Pinned identity and scope

| Check | Result |
|---|---|
| PR #378 state | `OPEN`, **Draft**, `MERGEABLE` |
| `refs/pull/378/head` | `069f10cce084b95ef7e60ecd2c32784856002d54` |
| remote branch `feat/s1-context-rebind-fence-20261021` | same exact SHA |
| local exported snapshot | same exact SHA |
| base branch/head | `feat/s1-grant-epoch-writer-fix-20261021` @ `8b42de47…` |
| diff vs #377 | exactly 3 files, `+196 / −39` |
| diff paths | `integrations/fleet-v1-atomic-prototype/README.md`, `…/atomic_fence.py`, `tests/test_s1_atomic_prototype.py` |
| `check_scope.py 8b42de47` | `changed=3 allowed=3 forbidden=0`, `SCOPE_OK` |
| author suite at #378 | **54 passed**, rc 0 |
| production imports in prototype | stdlib + `workengine.store` (read-only); no daemon/Fleet/WorkEngine mutation wiring |
| receipt | `PROTOTYPE_FENCED_COMMIT` / `PROTOTYPE_FENCED_CHECK`, `simulated_owner:true`, `authorized:true`, `historical:false`, `effect_eligible:false`; no `verified` key |
| production `COMMIT_FENCED` | absent |

Logs: `logs/a-prime-378-metadata.log`, `logs/a-prime-378-diff.log`,
`logs/a-prime-378-check-scope.log`, `logs/a-prime-378-author-54p.log`.

The scope receipt is the existing `check_scope.py` output for this exact pair;
its `SCOPE_OK` line is printed only on return 0. No author source was modified.

## 2. Evidence levels and RED/GREEN/NOT_PROVEN definitions

Evidence levels used here:

- `LOCAL_REAL_SQLITE` — real WorkEngine schema/tables, `BEGIN IMMEDIATE`, WAL,
  `busy_timeout=30000`, real token allocator, real temp SQLite DB.
- `SYNTHETIC_OWNER` / `SYNTHETIC_CONTEXT` / `SYNTHETIC_CLOCK` /
  `SYNTHETIC_PEER` — the owner policy, grant, context, clock and peer uid are
  fixtures, not a real owner key, real trusted context source, real store
  clock or kernel `SO_PEERCRED`.
- `REAL_TEMP` — temp DB and temp process only.
- `KERNEL_PEER_NOT_RUN`, `REAL_WINDOWS_NOT_RUN`, `PRODUCTION_DEPENDENCY`.

Definitions applied to every row:

- **RED** — at the pinned head, the witness observes the opposite of the stated
  safety property: the old token is `ACCEPTED`/`authorized:true` after a
  lineage change that a generation fence must make permanent, or an expected
  refusal code is absent, or a refusal produced a side effect.
- **GREEN** — the expected refusal code is observed **and** the lease row,
  `proto_commit` count and `id_sequences` token are unchanged; or, for a
  point-in-time positive, `authorized:true` with `effect_eligible:false` and no
  `verified` key.
- **NOT_PROVEN** — the property needs a real owner/key/reissue producer, a real
  trusted context source, two real OS uids, kernel `SO_PEERCRED`, production
  daemon/WorkEngine wiring, real Windows, provider/OAuth, power-loss/fsync or a
  composed finalize/effect. It is counted as neither RED nor GREEN.

## 3. Results matrix

| Witness | #377 `8b42de47` | #378 `069f10cc` |
|---|---|---|
| round-8 controls (grant removed / policy_ref mismatch / grant_epoch 1→2) | 3/3 rc0 | 3/3 rc0 |
| round-8 findings (rebind / actor / delete / same-grant context / commit persistence) | **5/5 RED rc1 (P1)** | **5/5 GREEN rc0** |
| ABA + content controls (C1–C6) | not run (not the fix head) | **6/6 rc0** |
| ABA + content findings (F1–F7) | not run | **7/7 RED rc1 (P1×3, P2×4)** |
| round-6 controls (TTL/WAL/crash/contention/replay/etc.) | 29/29 rc0 (round 6) | **29/29 rc0** |
| round-6 production findings | 3 RED (P0×2, P2) | 3 RED (P0×2, P2) |
| author tests | 51 passed | **54 passed** |

Logs: `logs/a-prime-378-r8-377-*`, `logs/a-prime-378-r8-378-*`,
`logs/a-prime-378-aba-*`, `logs/a-prime-378-r6-*`, with `.rc` files.

Note: the round-8 probe keeps its historical severity label (`P1`) in its
summary even when the case is now GREEN; the `ok:true` rows are the result.

## 4. A/B — persistent rebind is GREEN

After a fenced acquire (`req:1`, `ctx:proto`, `grant:proto`, token 1,
`grant_epoch=1`, `policy_epoch=1`, `authority_epoch=1`, owner `human:A`), on the
same temp DB:

| Mutation | Observed | Side effect |
|---|---|---|
| `proto_context.grant_ref → grant:proto-2` (second legal grant, same policy/`grant_epoch`) | `BLOCKED_STALE_CONTEXT` “the context was rebound to a different grant” | commit_count 1→1, token_next 2→2, lease unchanged |
| `proto_context.actor_ref → human:B` | `BLOCKED_STALE_CONTEXT` “the context actor changed after the commit” | none |
| `DELETE FROM proto_context` | `BLOCKED_STALE_CONTEXT` “the committed context_ref no longer exists” | none |
| same-`grant_ref` context actor mutation | `BLOCKED_STALE_CONTEXT` “the context actor changed after the commit” | none |
| grant removed | `BLOCKED_OWNER_POLICY` | none |
| `grant.policy_ref` mismatch | `BLOCKED_OWNER_POLICY` | none |
| `grant.grant_epoch 1→2` | `BLOCKED_STALE_EPOCH` | none |
| unchanged binding, point-in-time check | `PROTOTYPE_FENCED_CHECK`, `authorized:true`, `historical:false`, `effect_eligible:false`, no `verified` | — |

`proto_commit` at #378 now persists `context_ref` and `context_actor`; the
fenced check reads the current `proto_context` in the same `BEGIN IMMEDIATE`
and compares `context.grant_ref` with `commit.grant_ref`, `context.actor_ref`
with `commit.context_actor`, and the context/grant/policy actors with one
another. A/B are GREEN.

## 5. C — ABA revives the old token (new P1)

The context has **no generation/epoch/digest**, and the commit stores no
context generation. `check_writer_fenced` samples the *current* context. A
transient rewrite that is reverted to the exact committed snapshot is therefore
indistinguishable from “never changed”.

F1 — context grant A→B→A:

```text
before : check → ACCEPTED authorized:true (token 1, commit grant:proto)
middle : UPDATE proto_context SET grant_ref='grant:proto-2' …
         check → BLOCKED_STALE_CONTEXT
after  : UPDATE proto_context SET grant_ref='grant:proto' …
         check → ACCEPTED authorized:true, context_ref=ctx:proto,
                 context_actor=human:A, grant_ref=grant:proto,
                 grant_epoch=1, effect_eligible:false
lease/token/commit: unchanged (commit_count 1, token_next 2)
```

F2 — context actor A→B→A: middle `BLOCKED_STALE_CONTEXT` (“the context actor
changed after the commit”); after restore **ACCEPTED authorized:true**.
F3 — grant actor A→B→A (same `grant_ref`/`grant_epoch`): middle
`BLOCKED_STALE_CONTEXT` (“the context, grant and policy actors do not agree”);
after restore **ACCEPTED authorized:true**.

This is not the round-8 defect returning: #378 correctly refuses the middle
state. The defect is that the refusal is **non-monotonic**. An old token that a
rebind should have made permanently historical becomes current authority again
when the state is restored, without any new acquire and without any epoch
change. In the prototype this is a **P1** fence revival. The deeper requirement
— a legitimate rebind/rewrite must be an owner reissue that bumps a monotonic
context/grant generation — is **PRODUCTION_DEPENDENCY** and has no producer
here.

## 6. D — same-generation content mutation (P2, disclosed owner protocol)

With the same `grant_ref` and `grant_epoch`, and no epoch bump:

| In-place change | Observed |
|---|---|
| `proto_grant.items_json → ["OTHER-ITEM"]`, `actions_json → ["finalize"]` | **ACCEPTED authorized:true** |
| `proto_owner_policy.projects/items/actions` narrowed | **ACCEPTED authorized:true** |
| `proto_grant.expires_at` extended to 3999 | **ACCEPTED authorized:true** |
| `proto_grant.actor_ref → human:B` (permanent) | `BLOCKED_STALE_CONTEXT` (control) |
| `proto_owner_policy.actor_ref → human:B` (permanent) | `BLOCKED_STALE_CONTEXT` (control) |
| `proto_grant.owner_policy_epoch 1→2` | `BLOCKED_STALE_EPOCH` (control) |
| grant window narrowed into the past | `GRANT_EXPIRED_AT_COMMIT` (control) |

`check_writer_fenced` reads grant revocation/epoch/window/actor but **not**
grant/policy `projects_json`, `items_json` or `actions_json`, and `proto_commit`
stores no scope/window digest. The commit therefore cannot tell whether the
scope it committed is still the scope now in force.

Two causes must be separated:

1. **Prototype validation gap** — the commit could persist a
   `grant_scope_digest`/`policy_scope_digest`/window digest and the check could
   compare it, refusing any same-generation rewrite. #378 does not do this.
2. **Owner-protocol gap / PRODUCTION_DEPENDENCY** — a legitimate scope or
   window rewrite requires an atomic owner reissue that bumps
   `grant_epoch`/`policy_epoch`; the prototype has no owner reissue interface,
   so it cannot manufacture that generation itself.

The README (lines 116–120) explicitly discloses: “a same-epoch in-place
grant/policy content mutation beyond actor/ref/epoch is not fully detectable
and remains an owner-protocol requirement.” D is therefore classified **P2**
(disclosed, prototype-local detection gap) plus **PRODUCTION_DEPENDENCY**, not
a new P0 or an undisclosed P1. The ABA revival in §5 is distinct because it
recovers `authorized:true` after a refused transition and is not named in that
disclosure.

## 7. E — sampling of the earlier controls at #378

`round6_probes/a_prime_376_probe.py --mode controls` at #378: **29/29 rc0**.
Covered (all `LOCAL_REAL_SQLITE` unless noted):

- commit-phase TTL expiry, policy expiry, grant expiry, backward clock before
  the window, within-window positive;
- historical replay `authorized:false/historical:true/effect_eligible:false`,
  different authority/grant idempotency pins → `IDEMPOTENCY_CONFLICT`, replay
  after revoke;
- blocked revoker (`database is locked`), multiprocess contention one token,
  crash before commit rollback, commit → `os._exit` → reopen;
- fenced check valid / after revoke / after grant expiry;
- per-call `now`/`peer_uid` rejected, missing/bool peer `UNTRUSTED_ACTOR`,
  request-supplied uid/actor/verified/authority/`COMMIT_FENCED`/now rejected;
- README flags, no production positive, no production import, finalize
  `NOT_PROVEN`.

Legacy bypass at #378 (`round6_probes --mode findings`, rc1):

- **P0** `ServerOSStore.acquire_lease` still writes a new lease after a
  prototype policy revoke, with `proto_commits=0`; the fence never sees it.
- **P0** `ServerOSStore.check_writer` still returns `authorized:true` for a
  pre-revoke token while `check_writer_fenced` returns `POLICY_DENIED`.
- **P2** the fenced check still takes `owner`/`token` as caller strings and is
  not bound to the synthetic peer; production must bind `SO_PEERCRED` and hold
  the fence across the effect.

The old probe's `f4` row hard-codes `context_read:false`; that field is stale
for #378 and is **not** reported as a current finding. The round-8 witness
shows #378 does read `proto_context`. The peer-binding half of that row remains
a P2/PRODUCTION_DEPENDENCY.

Disclosure status: the PR body (“The legacy `ServerOSStore.acquire_lease`/
`check_writer` bypasses … remain `PRODUCTION_WIRING_REQUIRED`; they are not
fixed here”) and README lines 128–146 (`PRODUCTION_WIRING_REQUIRED`, two
negative tests) correctly disclose these barriers. They are production
wiring blockers, not prototype regressions; they still prevent any V1
authorization claim.

## 8. Findings classification

| ID | Severity | Finding | Status |
|---|---|---|---|
| F1–F3 | **P1** | ABA grant/actor/grant-actor `A→B→A` revives the old token to `authorized:true`; no monotonic context generation | **OPEN** |
| F4–F6 | **P2** | Same-generation in-place grant/policy scope and window mutation accepted; no scope/window digest | OPEN, disclosed owner protocol |
| F7 | **P2** | `proto_commit`/`proto_context` have no context epoch/digest and no scope/window digest | mechanism behind F1–F6 |
| — | **P0** | Legacy `ServerOSStore.acquire_lease` writes outside the fence after revoke | OPEN, `PRODUCTION_WIRING_REQUIRED`, disclosed |
| — | **P0** | Legacy `ServerOSStore.check_writer` accepts a pre-revoke token | OPEN, disclosed |
| — | **P2** | Fenced check not peer-bound; synthetic clock/peer; no real owner key | `PRODUCTION_DEPENDENCY` |
| — | **PRODUCTION_DEPENDENCY** | Real owner reissue/revocation/context source, two real uids, kernel `SO_PEERCRED`, daemon/Fleet/WorkEngine wiring/migration, real Windows, Provider/OAuth, power-loss/fsync, composed finalize/effect | **NOT_RUN / NOT_PROVEN** |

No P0 is a prototype-local regression: both P0 rows are the already-known
production-wiring barriers, and both are disclosed. The new #378-local findings
are P1 (ABA revival) and P2 (same-generation content).

## 9. Minimal counterexamples

All are on a temp DB after one fenced acquire; `token=1`, `req:1`.

```sql
-- C (P1): ABA grant, no context_epoch, no grant_epoch bump
INSERT INTO proto_grant(grant_ref,policy_ref,grant_epoch,owner_policy_epoch,
  os_principal,actor_ref,projects_json,items_json,actions_json,valid_from,
  expires_at,revoked) VALUES ('grant:proto-2','policy:proto',1,1,'uid:4242',
  'human:A','["serveros"]','["SRV-0001"]','["lease.acquire"]',
  '2000-01-01T00:00:00Z','2999-01-01T00:00:00Z',0);
UPDATE proto_context SET grant_ref='grant:proto-2' WHERE context_ref='ctx:proto';
--   check_writer_fenced(...) -> BLOCKED_STALE_CONTEXT
UPDATE proto_context SET grant_ref='grant:proto'   WHERE context_ref='ctx:proto';
--   check_writer_fenced(...) -> ACCEPTED authorized:true   <-- F1
```

```sql
-- C (P1): ABA context actor
UPDATE proto_context SET actor_ref='human:B' WHERE context_ref='ctx:proto';
--   BLOCKED_STALE_CONTEXT
UPDATE proto_context SET actor_ref='human:A' WHERE context_ref='ctx:proto';
--   ACCEPTED authorized:true                                <-- F2
```

```sql
-- D (P2): same grant_ref/grant_epoch, committed scope removed in place
UPDATE proto_grant SET items_json='["OTHER-ITEM"]',
                       actions_json='["finalize"]'
 WHERE grant_ref='grant:proto';
--   ACCEPTED authorized:true                                <-- F4
```

## 10. Minimal correct invariant (recommendation)

1. `proto_context` gains a monotonic `context_epoch` (or a content digest
   chained to the previous value). Every actor/grant bind or rebind increments
   it; a revert to an earlier content must not recreate an earlier epoch.
2. `proto_commit` persists `context_ref`, `context_actor`, `context_epoch`
   (or digest), plus `grant_scope_digest`, `policy_scope_digest` and a window
   digest, all read inside the acquire `BEGIN IMMEDIATE`.
3. `check_writer_fenced` re-reads the current context/grant/policy in the same
   `BEGIN IMMEDIATE` and requires equality of every committed digest/epoch;
   any mismatch is `BLOCKED_STALE_CONTEXT`/`BLOCKED_STALE_EPOCH`. Equality is
   not sufficient for ABA if the epoch is not monotonic.
4. Any legitimate rebind/scope rewrite is an atomic owner reissue that bumps
   `grant_epoch`/`policy_epoch`/`context_epoch`; an old token must never become
   current again. Keep `effect_eligible:false`; do not add `verified`.
5. Production must route every lease mutation, revoke and effect through this
   one fenced domain; the legacy `acquire_lease`/`check_writer`, JSON `work
   revoke` and `finalize` remain outside it.

## 11. Reproduction

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-378-069f10c

# round-8 witness, adapted to the #378 schema
cd "$SNAP"
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round9>/probes/a_prime_377_context_rebind.py --mode controls
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round9>/probes/a_prime_377_context_rebind.py --mode findings

# ABA + same-generation content witness
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round9>/probes/a_prime_378_aba.py --mode controls
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round9>/probes/a_prime_378_aba.py --mode findings

# author suite
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  /data/home/vince/toolchains/serveros-test/bin/python -m pytest -q \
  -p no:cacheprovider tests/test_s1_atomic_prototype.py    # 54 passed
```

All probe runs were bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25%`,
`nice 10`, `timeout 120–300 s`. No production SQLite/Daemon/Service,
permission, OAuth, provider, key or merge/force-push/delete was touched.
Synthetic owner/context/clock/peer are fixtures, not a real owner or real OS
peer.

## 12. Conclusion

- #378 is **GREEN** for the persistent context→grant rebind, actor remap,
  deletion and commit persistence reported in round 8 (5/5), and for the
  grant-epoch/policy-ref controls (3/3); no side effect was observed.
- #378 is **RED** for ABA revival (F1–F3, P1) and **RED/disclosed** for
  same-generation content mutation (F4–F6, P2). The missing context generation
  and scope/window digests (F7) are the mechanism.
- #378 therefore **cannot be called fully closed**. Final status:
  **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**; no `QA_SIGNED`.
- Evidence is `LOCAL_REAL_SQLITE` + `SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER` +
  `REAL_TEMP`; real owner/key/reissue, two real uids, kernel `SO_PEERCRED`,
  production wiring/migration, real Windows, Provider/OAuth and power-loss are
  `NOT_PROVEN`.
