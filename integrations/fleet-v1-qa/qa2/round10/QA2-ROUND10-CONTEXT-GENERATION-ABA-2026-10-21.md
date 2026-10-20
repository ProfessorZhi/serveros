# QA2 Round 10 — #379 context generation: partial, ABA/immutability bypassed

**Pinned head:** `7245fc6b3711172251e20450d4356abff8af91ee` (Draft PR #379)
**Base:** `069f10cce084b95ef7e60ecd2c32784856002d54` (PR #378)
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`.

#379 makes the *API* context generation and the *permanent* same-epoch content
mutation GREEN. It does **not** make context generation or commit immutability
real against SQLite writes: `INSERT OR REPLACE` bypasses the BEFORE DELETE
trigger (recursive_triggers defaults to 0), a plain `INSERT` can append a forged
`proto_commit` row, and grant/policy content `A→B→A` re-matches the digest and
revives the old token. The old token therefore still becomes `authorized:true`
in several paths. This is a **P1 prototype-local** result, with
`PRODUCTION_DEPENDENCY / NO_OWNER_TRUST` for a real owner key, trusted peer,
schema/privilege control and same-transaction effect. No QA signature.

## 1. Pinned identity and scope

| Check | Result |
|---|---|
| PR #379 | `OPEN`, **Draft**, `MERGEABLE` |
| `refs/pull/379/head` | `7245fc6b3711172251e20450d4356abff8af91ee` |
| remote branch `feat/s1-context-generation-20261021` | same exact SHA |
| base branch/head | `feat/s1-context-rebind-fence-20261021` @ `069f10cc…` |
| local exported snapshot | identical to `git archive 7245fc6b…`; only `.snapshot-sha` marker |
| diff vs #378 | exactly 3 files, `+324 / −48` |
| diff paths | `integrations/fleet-v1-atomic-prototype/README.md`, `…/atomic_fence.py`, `tests/test_s1_atomic_prototype.py` |
| `check_scope.py` vs #378 archive | `changed=3 allowed=3 forbidden=0`, `SCOPE_OK` |
| author suite | **62 passed rc0** |
| production imports | stdlib + `workengine.store` (read-only); no daemon/Fleet/WorkEngine mutation wiring |
| `verified` / production `COMMIT_FENCED` | absent |
| receipt | `PROTOTYPE_FENCED_COMMIT` / `PROTOTYPE_FENCED_CHECK`, `simulated_owner:true`, `effect_eligible:false`, no `verified` |

Logs: `logs/a-prime-379-metadata*.log`, `logs/a-prime-379-check-scope.log`,
`logs/a-prime-379-author62.log`, `logs/a-prime-379-local-equals-archive.log`.
No author source was modified.

## 2. Evidence levels and RED/GREEN/NOT_PROVEN definitions

- `LOCAL_REAL_SQLITE` — real WorkEngine schema, `BEGIN IMMEDIATE`, WAL,
  `busy_timeout=30000`, real token allocator, temp SQLite DB.
- `SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER` — policy/grant/context, clock and peer
  uid are fixtures; no owner key, trusted context source, store clock or kernel
  `SO_PEERCRED`.
- `REAL_TEMP`; `KERNEL_PEER_NOT_RUN`, `REAL_WINDOWS_NOT_RUN`,
  `PRODUCTION_DEPENDENCY`.

**RED** — at the pinned head the witness gets the opposite of the stated
property: an old token is `ACCEPTED`/`authorized:true` after a lineage/content
change that must invalidate it, an expected `sqlite3.IntegrityError` is not
raised, or a refusal changes the lease/commit/token.

**GREEN** — the expected refusal code/`IntegrityError` is observed and the
lease, `proto_commit` count and token are unchanged; or a point-in-time
positive returns `authorized:true`, `historical:false`,
`effect_eligible:false`, no `verified`.

**NOT_PROVEN** — needs a real owner key/reissue, trusted peer, two real uids,
production wiring/migration, real Windows, Provider/OAuth, power-loss/fsync or
a composed finalize/effect; counted as neither RED nor GREEN.

## 3. Results

| Gate | rc | Result |
|---|---|---|
| author `tests/test_s1_atomic_prototype.py` | 0 | **62 passed** |
| round10 controls | 0 | **43/43** |
| round10 findings | 1 | **10/10 RED** (P1×8, P2×2) |
| round-6 controls at #379 | 0 | **29/29** (TTL/WAL/crash/contention/replay/etc.) |
| round-6 production findings | 1 | 3 RED: legacy P0×2, peer P2 |
| `check_scope.py` | 0 | changed=3 allowed=3 forbidden=0 |

## 4. What #379 genuinely fixes (GREEN)

- API ABA: `rebind_context` CAS advances `context_epoch` 1→2→3 for grant and
  actor; the old token returns `BLOCKED_STALE_CONTEXT_EPOCH` (C11, C12).
- Persistent single rebind: epoch 1→2; old token
  `BLOCKED_STALE_CONTEXT_EPOCH` (C20, C21).
- Raw `UPDATE` of `grant_ref` or `actor_ref` **without** epoch increase:
  `sqlite3.IntegrityError: proto_context update must increase context_epoch`
  (C1, C2); same-value update and epoch 0/backward likewise (C3–C5).
- Context `DELETE`: `sqlite3.IntegrityError: proto_context rows are
  generation-fenced; rebind instead` (C6).
- `proto_commit` `UPDATE`/`DELETE`:
  `sqlite3.IntegrityError: proto_commit rows are immutable` (C7, C8).
- CAS stale expected: `BLOCKED_STALE_CONTEXT_EPOCH` (C9); no-op rebind is
  idempotent and keeps the token valid (C10).
- Permanent same-generation content mutation of 18 columns across grant/policy
  (`projects/items/actions/os_principal/issued_by/issued_at/valid_from/
  expires_at/actor_ref/revoked`) is refused with `BLOCKED_STALE_CONTENT` or a
  stronger code (`BLOCKED_STALE_CONTEXT`, `POLICY_DENIED`), with **no side
  effect** on lease/commit/token (C18).
- Digest is canonical over the persisted columns: commit digest equals a
  recomputation, and every content column changes it (C17).
- Grant epoch 1→2: `BLOCKED_STALE_EPOCH` (C14). Unchanged binding:
  `authorized:true`, `effect_eligible:false` (C15). Historical replay:
  `authorized:false`, `historical:true`, `effect_eligible:false` (C16).
- Missing fenced commit row: `BLOCKED_OWNER_POLICY` (C22). Invalid raw SQL:
  `OperationalError` (C23). Forged commit naming a missing context:
  `BLOCKED_STALE_CONTEXT` (C26).
- Two-process CAS contention: exactly one `OK` (epoch 2), one
  `BLOCKED_STALE_CONTEXT_EPOCH` (C24). WAL + `integrity_check=ok` (C25).

The round-9 findings are therefore GREEN **for the API/permanent path**:
content mutation → `BLOCKED_STALE_CONTENT`; context ABA → epoch stale.

## 5. New RED — the generation is bypassable by ordinary SQLite writes

### 5.1 `INSERT OR REPLACE` bypasses the DELETE trigger; old token revives (P1)

`PRAGMA recursive_triggers` is 0 by default (recorded in F1). SQLite fires a
BEFORE DELETE trigger for `INSERT OR REPLACE` only when recursive triggers are
enabled, and #379 has no BEFORE INSERT guard. Minimal counterexample:

```sql
-- acquire token 1 at context_epoch=1
SELECT rebind_context(ctx:proto, expected=1, grant=grant:proto-2);  -- epoch 2
SELECT rebind_context(ctx:proto, expected=2, grant=grant:proto);    -- epoch 3
-- check_writer_fenced(token 1) -> BLOCKED_STALE_CONTEXT_EPOCH (GREEN)

INSERT OR REPLACE INTO proto_context(context_ref,actor_ref,grant_ref,
                                     context_epoch)
VALUES('ctx:proto','human:A','grant:proto',1);   -- succeeds
-- check_writer_fenced(token 1) -> ACCEPTED authorized:true  (RED)
```

F1/F2 reproduce this for the grant and actor ABA. `INSERT OR REPLACE` is not
an UPDATE, so `proto_context_epoch_must_increase` does not run; the DELETE
trigger does not run under the default pragma. The context generation is rolled
back to 1 and the old token becomes current authority again. The same path
rewrites a `proto_commit` row:

```sql
-- legal rebind to grant:proto-2 (epoch 2), old token is stale
INSERT OR REPLACE INTO proto_commit(<all columns>)
VALUES(..., context_epoch=2, grant_ref='grant:proto-2',
       grant_digest=<digest of grant:proto-2>, token=1, ...);
-- check_writer_fenced(token 1) -> ACCEPTED authorized:true  (F3, RED)
```

The UPDATE/DELETE immutability triggers do not see REPLACE. F3 shows the commit
row can be aligned with the new context and the old token re-authorized.

### 5.2 A plain `INSERT` can append a forged commit (P1)

There is no BEFORE INSERT trigger and no uniqueness on `(resource, token)`.
`check_writer_fenced` selects `ORDER BY rowid DESC LIMIT 1`, so a second row
wins:

```sql
-- after a legal rebind to epoch 2 (old token stale)
INSERT INTO proto_commit(<all columns>)
VALUES('req:forged', ..., context_epoch=2, grant_ref='grant:proto-2',
       grant_digest=<digest of grant:proto-2>, token=1, ...);
-- commit_count=2; latest row = req:forged
-- check_writer_fenced(token 1) -> ACCEPTED authorized:true  (F8, RED)
```

This does not even need REPLACE and is independent of `recursive_triggers`.
The same mechanism can attach a forged commit to a legacy lease that has no
fence row; the fenced check then has a commit to read.

### 5.3 Content ABA still revives the old token (P1)

The digests are equality snapshots, not generations. Changing content is
refused, but changing it back re-matches the committed digest:

```sql
UPDATE proto_grant SET items_json='["OTHER-ITEM"]' WHERE grant_ref='grant:proto';
-- check -> BLOCKED_STALE_CONTENT
UPDATE proto_grant SET items_json='["SRV-0001"]'    WHERE grant_ref='grant:proto';
-- check -> ACCEPTED authorized:true  (F4, RED)
```

F4/F5 (grant items, policy actions) and F9/F10 (grant actor, policy actor)
reproduce the same revival. `proto_commit` stores `policy_digest`/`grant_digest`
but no `grant_content_epoch`/`policy_content_epoch` and no immutable
policy/grant history; A→B→A is indistinguishable from “never changed”.

### 5.4 The digest is an unsigned hash over a mutable row (P2 / NO_OWNER_TRUST)

`_content_digest` is SHA-256 over the row's canonical JSON, with no key and no
owner signature. A writer who changes `proto_grant.items_json` and recomputes
`grant_digest` can `INSERT OR REPLACE` the commit row with the new digest and
the old token is accepted (F6). This is exactly `PROTOTYPE_ONLY /
NO_OWNER_TRUST`: it detects a non-writer's edit, not an owner-signed content
proof, and not a DB owner who can edit both the row and the commit.

### 5.5 Raw SQL can jump the context epoch (P2)

The trigger enforces only `NEW.context_epoch > OLD.context_epoch`, not +1 and
not an owner-authenticated producer. `UPDATE proto_context SET
context_epoch=999` succeeds (F7). The old token is safely refused
(`BLOCKED_STALE_CONTEXT_EPOCH`), but the synthetic CAS API is poisoned:
`rebind_context(expected=2)` returns `BLOCKED_STALE_CONTEXT_EPOCH (current
999)`. This is denial/poisoning, not authorization revival.

## 6. Round-9 mapping (adapted to the #379 schema/API)

| round-9 case | #379 independent result |
|---|---|
| A→B→A context grant (raw same-epoch) | now `rebind_context` CAS 1→2→3 → `BLOCKED_STALE_CONTEXT_EPOCH` (C11) |
| A→B→A context actor | 1→2→3 → `BLOCKED_STALE_CONTEXT_EPOCH` (C12) |
| same `grant_ref` context mutation | raw no-epoch UPDATE → `IntegrityError` (C1/C2); API rebind → epoch stale (C20/C21) |
| context deletion | `IntegrityError` (C6); **REPLACE reinsert revives** (F1/F2) |
| commit persistence | `context_epoch` + `policy_digest` + `grant_digest` present; immutable for UPDATE/DELETE (C7/C8) |
| grant items/actions scope mutation | `BLOCKED_STALE_CONTENT` (C18), no side effect; **A→B→A revives** (F4/F5) |
| policy scope mutation | `BLOCKED_STALE_CONTENT` (C18); **A→B→A revives** (F5/F10) |
| grant/policy actor mutation | `BLOCKED_STALE_CONTEXT` (C18); **A→B→A revives** (F9/F10) |
| window extension | digest sensitivity + `BLOCKED_STALE_CONTENT` (C17/C18) |
| grant epoch 1→2 | `BLOCKED_STALE_EPOCH` (C14) |
| unchanged binding | `authorized:true`, `effect_eligible:false` (C15) |
| historical replay | `authorized:false`, `historical:true` (C16) |

## 7. Sampling: TTL/WAL/crash/contention and legacy P0

`round6_probes/a_prime_376_probe.py --mode controls` at #379: **29/29 rc0**,
covering commit-phase TTL/policy/grant expiry, backward window, historical
replay and idempotency pins, blocked revoker, multiprocess one-token
contention, crash before commit, commit → `os._exit` → reopen, WAL and
`integrity_check`, fenced valid/revoked/expired, per-call clock/peer
rejection, request authority-field injection, README flags, no production
import/positive, finalize `NOT_PROVEN`.

Legacy production bypasses at #379 (`--mode findings`, rc1):

- **P0** `ServerOSStore.acquire_lease` still writes a lease after a prototype
  revoke with `proto_commits=0`; the fence never sees it.
- **P0** `ServerOSStore.check_writer` still returns `authorized:true` for a
  pre-revoke token while `check_writer_fenced` returns `POLICY_DENIED`.
- **P2** the fenced check still takes `owner`/`token` from the caller and is
  not bound to a real peer. The old probe's hard-coded `context_read:false` is
  stale for #379 and is not reported; the peer half remains valid.

PR body and README correctly disclose the legacy paths as
`PRODUCTION_WIRING_REQUIRED` and “still bypass”; they are not fixed here. JSON
`work revoke`, finalize/effect and migration remain outside the fence.

## 8. Findings classification

| ID | Severity | Finding | Status |
|---|---|---|---|
| F1, F2 | **P1** | `INSERT OR REPLACE` on `proto_context` bypasses the DELETE trigger and rolls the generation back to epoch 1; old token `authorized:true` | OPEN |
| F3 | **P1** | `INSERT OR REPLACE` on `proto_commit` bypasses UPDATE/DELETE immutability; commit aligned to the new epoch, old token authorized | OPEN |
| F8 | **P1** | plain `INSERT` appends a forged `proto_commit` for the same `(resource, token)`; `rowid DESC` selects it, old token authorized | OPEN |
| F4, F5, F9, F10 | **P1** | grant/policy content `A→B→A` re-matches the digest; old token authorized | OPEN |
| F6 | **P2** | digest is an unkeyed hash; commit rewrite + digest reforge authorizes changed content (`NO_OWNER_TRUST`) | OPEN |
| F7 | **P2** | raw SQL can jump `context_epoch` to a future value; old token refused but CAS poisoned | OPEN |
| — | **P0** | legacy `ServerOSStore.acquire_lease` writes outside the fence after revoke | OPEN, disclosed |
| — | **P0** | legacy `ServerOSStore.check_writer` accepts a pre-revoke token | OPEN, disclosed |
| — | **P2** | fenced check not peer-bound; synthetic clock/peer; no real owner key | `PRODUCTION_DEPENDENCY` |
| — | **PRODUCTION_DEPENDENCY** | real owner reissue/context generation, two uids, `SO_PEERCRED`, daemon/Fleet/WorkEngine wiring/migration, real Windows, Provider/OAuth, power-loss/fsync, composed finalize/effect | **NOT_RUN** |

P1 here is **prototype-local**: it requires a SQLite writer, and the PR already
discloses that a DB owner who drops triggers can rewrite the temp DB. The new
point is that the disclosed “drop triggers” caveat is not the only path:
REPLACE and plain INSERT bypass the claims **without** dropping a trigger.

## 9. Minimal counterexamples (copy/paste)

```sql
-- F1: REPLACE context after API ABA
INSERT OR REPLACE INTO proto_context(context_ref,actor_ref,grant_ref,
                                     context_epoch)
VALUES('ctx:proto','human:A','grant:proto',1);

-- F8: append a second commit for the same lease
INSERT INTO proto_commit(request_id,request_digest,grant_ref,policy_ref,
  context_ref,context_actor,context_epoch,policy_digest,grant_digest,
  grant_epoch,policy_epoch,authority_epoch,resource,owner,token,expires_at,
  committed_at)
VALUES('req:forged',<same digest>,'grant:proto-2','policy:proto','ctx:proto',
  'human:A',2,<policy digest>,<grant2 digest>,1,1,1,
  'work-write:SRV-0001','human:A',1,<expires_at>,<committed_at>);
```

## 10. Minimal correct invariant / recommendation

1. **Close the REPLACE/INSERT hole.** Add `BEFORE INSERT` triggers:
   - `proto_context`: abort if `context_ref` already exists (blocks the insert
     half of REPLACE);
   - `proto_commit`: abort if `request_id` or `(resource, token)` already
     exists; add a `UNIQUE(resource, token)` index.
   `PRAGMA recursive_triggers=ON` in the prototype's own connections is
   defense-in-depth but is **per connection**; it does not constrain another
   writer, so it is not the fix on its own.
2. **Make context generation monotonic at the schema, not only the Python API.**
   The trigger should require exactly `OLD.context_epoch + 1` (or an
   owner-authenticated producer), reject future jumps and bound the value; a
   raw writer must not be able to poison the CAS.
3. **Give grant/policy content a generation or immutable history.** A digest
   equality check alone cannot distinguish “never changed” from A→B→A. Add
   `grant_content_epoch`/`policy_content_epoch` (or append-only rows) and
   persist it in `proto_commit`; a same-generation content rewrite is refused
   even if the bytes are restored.
4. **Do not call the digest an owner proof.** SHA-256 over a mutable row is
   `NO_OWNER_TRUST`. Production needs an owner signature over the commit
   (context_ref/epoch, policy/grant digests, resource/token) and a
   schema/privilege boundary that prevents arbitrary writes to the authority
   tables.
5. **Route every lease mutation, revoke and effect through one fenced
   transaction domain.** The legacy `acquire_lease`/`check_writer`, JSON
   `work revoke`, `finalize`/effect and migration remain
   `PRODUCTION_WIRING_REQUIRED`; no positive production authorization exists.
6. Keep `effect_eligible:false`; do not add `verified` or a production
   `COMMIT_FENCED` claim.

## 11. Reproduction

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-379-7245fc6
cd "$SNAP"
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round10>/probes/a_prime_379_generation.py --mode controls
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round10>/probes/a_prime_379_generation.py --mode findings
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  /data/home/vince/toolchains/serveros-test/bin/python -m pytest -q \
  -p no:cacheprovider tests/test_s1_atomic_prototype.py    # 62 passed
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25%`, `nice 10`,
`timeout 300–400 s`; temp SQLite/fork only. No full pytest, production
SQLite/Daemon/Service, permission, OAuth, provider, key or
merge/force-push/delete. Synthetic owner/context/clock/peer are fixtures.

## 12. Conclusion

- #379 is GREEN for the API-level context generation (A→B→A epoch stale),
  permanent same-epoch content mutations, trigger-level raw no-epoch updates,
  context DELETE, commit UPDATE/DELETE, epoch/revoke/replay/TTL/WAL/crash and
  two-process CAS contention.
- #379 is RED for: REPLACE-based context/commit rewrite (F1–F3), plain-INSERT
  forged commit (F8), content A→B→A revival (F4/F5/F9/F10), unsigned digest
  (F6/P2) and future-epoch poisoning (F7/P2).
- The context generation and commit immutability are therefore **not fully
  closed**; the old token can still return `authorized:true`. Verdict:
  **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**, no `QA_SIGNED`.
- Evidence is `LOCAL_REAL_SQLITE` + `SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER` +
  `REAL_TEMP`; real owner/key/reissue, two real uids, kernel `SO_PEERCRED`,
  production wiring/migration, real Windows, Provider/OAuth and power-loss are
  `NOT_PROVEN`.
