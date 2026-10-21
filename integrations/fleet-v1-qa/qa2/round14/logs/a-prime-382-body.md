Stacked on #381 exact head `043e08b916b2b511b56b30a419d21ad4450451c2` (`feat/s1-commit-token-binding-20261021`). #372–#381 are unchanged. One commit `b7dedd116425d7e645a034e8559fa103562b88eb`; **PROTOTYPE_ONLY / NOT_V1_SIGNED**.

## RED at #381 (real temp SQLite)

```text
acquire req1 → fresh token1/capability
acquire req1 again → PROTOTYPE_HISTORICAL_REPLAY
  authorized:false, historical:true, replayed:true
  BUT capability_scope:"API_ONLY", capability present
check_use(replay.request_id, replay.token, replay.capability)
  → REPLAY_CAP_CHECK_ACCEPTED authorized:true
commit_effect(... replay.capability ...)
  → REPLAY_CAP_EFFECT_ACCEPTED, proto_effect marker written
```

A replay is an audit record, not current authority; it must not mint a usable
capability.

## Fix

`SingleWriteEntry.acquire` now attaches a capability **only** to a fresh
`PROTOTYPE_FENCED_COMMIT`:

- `prototype_status == PROTOTYPE_FENCED_COMMIT`
- `authorized is True`, `historical is False`, `replayed is False`

A historical replay omits the `capability` field and sets
`capability_scope: "HISTORICAL_AUDIT_ONLY"`. `check_use`/`commit_effect` with
no capability return `BLOCKED_CAPABILITY` and write no marker. Unknown statuses
fail closed (no capability).

**Not claimed:** a replay does not revoke the ORIGINAL capability. If the
original caller still holds it and the lease/epoch fence is live, it keeps its
original semantics; this is documented explicitly. Once lease/policy/grant/
context epoch moves, the fence refuses it.

## GREEN

```text
historical replay                 no capability, HISTORICAL_AUDIT_ONLY
no-capability check/effect        BLOCKED_CAPABILITY, marker 0
original capability while live    still valid (not revoked by replay)
replay after revoke               historical, no capability;
                                  original cap now POLICY_DENIED
same request different facts      IDEMPOTENCY_CONFLICT
previous 82 tests                 retained
combined                          86 passed
#364 production fail-closed control 26 passed, 1 skipped
static scope (RC-v9 base)           changed=6 allowed=6 forbidden=0
diff vs #381 base                   3 allowed paths only
```

Cross-token, post-hold TTL/policy/grant, lock contention, crash, historical
replay and B raw-DB expected bypass tests remain green. All runs used
`MemoryMax=1G`, `SwapMax=0`, `CPUQuota=25%`, `nice -n 10`, timeouts and temp
SQLite/fork only; no full pytest.

## Classification / limitations

- A / `API_ONLY_CORRECTNESS`; B / `RAW_DB_WRITE_BYPASS_EXPECTED` /
  `BLOCKED_DB_WRITER_ISOLATION`; C / `OS_ISOLATION_NOT_RUN`;
  `EFFECT_COMMIT_NOT_PROVEN`.
- Same-process caveat unchanged: an object holder can call synthetic owner
  methods, reach `_writer`/`db_path` and read the in-process capability key;
  HMAC is not OS isolation or a real owner key.
- Real owner key/issuance, kernel `SO_PEERCRED`, two real OS uids, production
  daemon/Fleet wiring, DB migration, real Windows, power loss/fsync and
  production finalize/effect remain NOT_RUN/NOT_PROVEN. Legacy
  `ServerOSStore`/JSON `work revoke`/raw DB writers remain outside this fence.
- No merge, deploy, service restart, production DB/owner/OS grant,
  credential/OAuth/provider send, force-push/reset/clean/prune or RC roll. No
  hosted CI check.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

