Stacked on #378 exact head `069f10cce084b95ef7e60ecd2c32784856002d54` (`feat/s1-context-rebind-fence-20261021`). #372/#375/#376/#377/#378 are unchanged. One commit `7245fc6b3711172251e20450d4356abff8af91ee`; **PROTOTYPE_ONLY / NOT_V1_SIGNED**.

## RED at #378 (QA2 witnesses)

Real temp SQLite:

```text
acquire token 1 (context ctx:proto, grant:proto, grant_epoch 1)
insert grant:proto-2 (same policy, same epoch)
raw rebind ctx:proto -> grant:proto-2 -> grant:proto   # A -> B -> A, same epoch
check_writer_fenced(...) -> ABA_RESURRECTED authorized:true

raw UPDATE proto_grant SET projects_json='["other"]'   # same grant epoch
check_writer_fenced(...) -> CONTENT_MUTATION_ACCEPTED authorized:true
```

A value comparison (`grant_ref`/`actor_ref`) is not a generation. A
same-epoch content rewrite of the grant/policy row was also invisible.

## Fix (prototype temp DB only)

- `proto_context.context_epoch` — strict positive integer, initial 1.
- Synthetic test-only `rebind_context(context_ref, expected_context_epoch=...,
  grant_ref=..., actor_ref=...)` uses CAS and increments `context_epoch` in one
  `BEGIN IMMEDIATE`. A→B→A therefore commits epochs 1→2→3.
- SQLite triggers:
  - `proto_context` UPDATE must increase `context_epoch`;
  - `proto_context` DELETE is refused;
  - `proto_commit` UPDATE and DELETE are refused (immutable commit).
- `proto_commit` persists `context_epoch`, `policy_digest`, `grant_digest`
  (canonical JSON SHA-256) at acquire in the same transaction.
- `check_writer_fenced` order: holder/expiry → committed context snapshot →
  current context epoch (`BLOCKED_STALE_CONTEXT_EPOCH`) → current
  grant/actor binding (`BLOCKED_STALE_CONTEXT`) → policy/grant rows →
  policy revoke → policy/authority/grant epoch (`BLOCKED_STALE_EPOCH`) →
  same-epoch content digests (`BLOCKED_STALE_CONTENT`) → windows.
- Normal unchanged binding still returns `PROTOTYPE_FENCED_CHECK
  authorized:true`, `historical:false`, `effect_eligible:false`.

No production WorkEngine/Daemon/owner key/service/migration change; no
`verified` key and no production `COMMIT_FENCED`.

## GREEN / no regression

```text
A->B->A grant rebind                       BLOCKED_STALE_CONTEXT_EPOCH
A->B->A actor rebind                       BLOCKED_STALE_CONTEXT_EPOCH
immediate same-epoch grant rebind          BLOCKED_STALE_CONTEXT_EPOCH
raw SQL rebind without epoch bump          sqlite3.IntegrityError (trigger)
context DELETE                             sqlite3.IntegrityError (trigger)
proto_commit UPDATE / DELETE               sqlite3.IntegrityError (immutable)
missing context row                        BLOCKED_STALE_CONTEXT
same-epoch grant/policy content mutation   BLOCKED_STALE_CONTENT
cross-context rebind of ctx:other          old ctx:proto token remains valid
grant epoch 1->2                           BLOCKED_STALE_EPOCH (retained)
policy revoke / TTL rollback / replay      retained
two-process acquire/revoke, WAL/crash       retained
legacy ServerOSStore acquire/check_writer  still bypass (PRODUCTION_WIRING_REQUIRED)
tests/test_s1_atomic_prototype.py          62 passed (54 previous + 8 net)
#364 production fail-closed control        26 passed, 1 skipped
static scope (RC-v9 base)                  changed=4 allowed=4 forbidden=0
diff vs #378 base                          3 allowed paths only
```

All runs used `MemoryMax=1G`, `SwapMax=0`, `CPUQuota=25%`, `nice -n 10`,
timeouts and temp SQLite/fork only; no full pytest.

## NOT_PROVEN / NOT_RUN

- **PRODUCTION_DEPENDENCY:** a real context rebind needs an atomic
  owner-issued context generation/reissue; `rebind_context` is a synthetic
  harness API, not a real owner interface.
- The SQLite triggers are experimental. A DB owner who can drop them can
  rewrite rows; production needs schema/privilege control and an immutable
  commit store. Delete/recreate ABA with dropped triggers is NOT_PROVEN.
- The canonical digests detect policy/grant content changes in this prototype
  DB; they are not a real owner-signed content proof. A same-epoch content
  rewrite is detected here, but a production owner must still bump epochs.
- Legacy `ServerOSStore.acquire_lease`/`check_writer`, JSON `work revoke`,
  finalize/effect same transaction, real owner key/`SO_PEERCRED`, two real
  uids, production wiring/migration, power loss and real Windows remain
  NOT_RUN / PRODUCTION_WIRING_REQUIRED.
- No merge, deploy, service restart, production DB/owner/OS grant,
  credential/OAuth/provider send, force-push/reset/clean/prune. No RC roll.
  No hosted CI check is attached.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

