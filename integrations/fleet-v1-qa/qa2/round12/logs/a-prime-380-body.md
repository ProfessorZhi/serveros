Stacked on #379 exact head `7245fc6b3711172251e20450d4356abff8af91ee` (`feat/s1-context-generation-20261021`). #372–#379 are unchanged. One commit `cbbfb63a76072871edfa6aa639355f5b63cc5853`; **PROTOTYPE_ONLY / NOT_V1_SIGNED**.

## A / API_ONLY_GREEN

`integrations/fleet-v1-atomic-prototype/single_write_entry.py` adds one
restricted writer object:

- constructor-bound synthetic actor, peer uid and clock; caller methods accept
  no uid/actor/clock/policy/epoch/SQL, and extra keywords raise `TypeError`;
- owner-side `issue` writes policy + grant + context in **one** `BEGIN
  IMMEDIATE`; `issue_grant`, `revoke_policy`, `rebind_context` are the owner
  fixture operations;
- caller-side `acquire` goes through the fenced prototype and returns an
  in-process HMAC capability bound to the committed authority;
- `check_use` and `commit_effect` re-run the fence in one `BEGIN IMMEDIATE`;
  `commit_effect` inserts the `proto_effect` marker in that same transaction;
- tampered capability, revoke, context A→B→A, stale token, expired TTL,
  replay/conflicting payload, and raw authority fields are refused;
- `proto_effect` is a prototype marker, **not** Work Engine finalize or a
  production effect.

## B / RAW_DB_WRITE_BYPASS_EXPECTED

The tests intentionally demonstrate that a same-file SQL writer can insert an
effect marker outside the API (and the existing legacy `ServerOSStore` write
bypass is retained). Classification:

`BLOCKED_DB_WRITER_ISOLATION` — this experiment does not claim that a raw DB
writer is excluded. Triggers/digests are prototype detection, not isolation.

## C / OS_ISOLATION_NOT_RUN

The capability key is an in-process test secret. No real OS uid/ACL/socket/
owner key, no chmod/chown, no production daemon or service is used.

`classification()` returns exact A/B/C names plus `EFFECT_COMMIT_NOT_PROVEN`
and `NOT_V1_SIGNED`.

## Evidence

```text
tests/test_s1_atomic_prototype.py               62 passed
tests/test_s1_atomic_prototype_single_write.py  13 passed
combined                                        75 passed
#364 production fail-closed control              26 passed, 1 skipped
static scope (RC-v9 base)                        changed=6 allowed=6 forbidden=0
diff vs #379 base                                5 allowed paths only
```

Tests cover API-only issue/acquire/use-check/effect, capability tamper,
revoke, context ABA, expired/superseded token, replay/conflict, rollback on
fault and `os._exit` before COMMIT, two-process one-winner acquire, raw-DB and
legacy-store bypass, and explicit OS-isolation classification.

## NOT_RUN / limitations

- Real owner key/issuance, kernel `SO_PEERCRED`, two real OS uids, production
  daemon/Fleet wiring, DB migration, real Windows and power loss: NOT_RUN.
- `EFFECT_COMMIT_NOT_PROVEN`: a real WorkEngine `finalize_run_verified`/effect
  in the same transaction needs an owner-approved WorkEngine change; this PR
  only writes a prototype marker.
- Legacy `ServerOSStore.acquire_lease`/`check_writer`, JSON `work revoke` and
  all production permissions remain outside this fence.
- No merge, deploy, service restart, production DB/owner/OS grant,
  credential/OAuth/provider send, force-push/reset/clean/prune or full pytest.
  No RC roll. No hosted CI check is attached.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

