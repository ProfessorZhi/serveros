Stacked on #377 exact head `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` (`feat/s1-grant-epoch-writer-fix-20261021`). #372/#375/#376/#377 are unchanged. One commit `069f10cce084b95ef7e60ecd2c32784856002d54`; **PROTOTYPE_ONLY / NOT_V1_SIGNED**.

## RED (QA2 same-epoch context rebind)

Real temp SQLite:

```text
acquire → lease token 1, proto_commit:
  context_ref=ctx:proto, grant_ref=grant:proto, grant_epoch=1
insert grant:proto-2 (same policy, same grant_epoch)
UPDATE proto_context SET grant_ref='grant:proto-2' WHERE context_ref='ctx:proto'
check_writer_fenced(resource, owner, token)
→ DID NOT RAISE (PROTOTYPE_FENCED_CHECK authorized:true)
```

The check fetched the grant by the commit's own `grant_ref`, so
`commit.grant_ref == grant.grant_ref` was a tautology. Legacy
`ServerOSStore.check_writer` also returned authorized; that remains
`PRODUCTION_WIRING_REQUIRED`.

## Fix (prototype scope)

- `proto_commit` now persists `context_ref` and `context_actor` snapshots at
  acquire, inside the same `BEGIN IMMEDIATE`.
- `check_writer_fenced` re-reads the CURRENT `proto_context` row in the same
  transaction and refuses:
  - missing context row → `BLOCKED_STALE_CONTEXT`
  - `context.grant_ref != commit.grant_ref` (rebind) → `BLOCKED_STALE_CONTEXT`
  - `context.actor_ref != commit.context_actor` (actor remap) →
    `BLOCKED_STALE_CONTEXT`
  - context/grant/policy actor disagreement → `BLOCKED_STALE_CONTEXT`
- Lineage then checks `grant.policy_ref == commit.policy_ref`, policy
  revocation, policy/authority/grant epochs (`BLOCKED_STALE_EPOCH`) and
  windows.
- Priority is explicit: holder/expiry → committed context snapshot → current
  context binding → policy/grant rows → policy revoke → epoch → window. A
  rebind therefore wins over a simultaneous revoke/epoch bump and returns
  `BLOCKED_STALE_CONTEXT`.

No production WorkEngine/Daemon/owner key/service/migration change; no
`verified` and no production `COMMIT_FENCED`.

## GREEN / no regression

```text
same-epoch context rebind                 BLOCKED_STALE_CONTEXT
context deleted / actor remapped          BLOCKED_STALE_CONTEXT
rebind + simultaneous revoke or epoch     BLOCKED_STALE_CONTEXT (priority)
unchanged binding                         PROTOTYPE_FENCED_CHECK authorized:true,
                                          historical:false, effect_eligible:false
grant epoch 1→2                           BLOCKED_STALE_EPOCH (retained)
policy revoke / TTL rollback / replay     retained
mproc / crash / WAL                       retained
tests/test_s1_atomic_prototype.py         54 passed (51 previous + 3)
#364 production fail-closed control       26 passed, 1 skipped
static scope (RC-v9 base)                 changed=4 allowed=4 forbidden=0
diff vs #377 base                         3 allowed paths only
```

The legacy `ServerOSStore.acquire_lease`/`check_writer` bypasses and the
`work revoke` JSON/database divergence remain explicit negative tests /
`PRODUCTION_WIRING_REQUIRED`; they are not fixed here.

## NOT_PROVEN / NOT_RUN

- A real context rebind or grant rewrite needs an atomic owner reissue that
  bumps the grant epoch (or a durable context generation) in the same
  authority transaction. No such owner interface exists; the prototype cannot
  claim it.
- A same-epoch in-place grant/policy content mutation beyond actor/ref/epoch
  is not fully detectable; that is an owner-protocol requirement.
- Real owner key/signature, kernel `SO_PEERCRED`, two real uids, production
  daemon/Fleet wiring, migration, power loss, real Windows: NOT_RUN.
- No merge, deploy, service restart, production DB/owner/OS grant, credential/
  OAuth/provider send, force-push/reset/clean/prune or full pytest. No hosted
  CI check is attached.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

