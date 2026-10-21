Stacked on #380 exact head `cbbfb63a76072871edfa6aa639355f5b63cc5853` (`feat/s1-single-write-entry-20261021`). #372–#380 are unchanged. One commit `043e08b916b2b511b56b30a419d21ad4450451c2`; **PROTOTYPE_ONLY / NOT_V1_SIGNED**.

## RED at #380 (real temp SQLite, synthetic FixedClock)

```text
acquire req1 → token1, cap1 (commit token1)
advance clock past TTL → acquire req2 → token2 on the same resource
check_use(req1, token2, cap1)
→ CROSS_TOKEN_CHECK_ACCEPTED authorized:true
commit_effect(req1, token2, cap1, effect_ref=effect:cross)
→ marker request_id=req1, token=2, successor epochs (inconsistent)

commit_effect(req1, token1, cap1, hold=clock→NOW+31)
→ TTL_HOLD_EFFECT_ACCEPTED marker persisted
commit_effect(... hold=clock→policy expires+1)
→ WINDOW_HOLD_EFFECT_ACCEPTED marker persisted
```

The fencer re-queried a commit by the caller token instead of binding the
commit loaded from `request_id`; the post-hold path had no second clock read.

## Fix

- `SingleWriteEntry.check_use` / `commit_effect` require
  `commit.token == caller token` and pass that exact commit
  (`request_id`, `resource`, `owner`, `token`, `expires_at`) into
  `_check_writer_fenced`; the fencer never re-queries a successor commit by
  the caller token and checks the commit's own expiry.
- `commit_effect` inserts the marker, runs the TEST-ONLY hold, then reads the
  injected clock again **inside the same `BEGIN IMMEDIATE`** and re-runs the
  exact fence/window check before COMMIT. TTL/policy/grant crossing raises
  `LEASE_EXPIRED` / `POLICY_EXPIRED_AT_COMMIT` / `GRANT_EXPIRED_AT_COMMIT`
  and rolls back `proto_effect` (and leaves the lease/commit unchanged).
- The writer transaction holds the SQLite write lock across check+marker; a
  concurrent revoke is blocked (independent `BEGIN IMMEDIATE` probe with
  `busy_timeout=0`).

## GREEN

```text
cross-token check/effect                    STALE_TOKEN, no marker
cross-resource capability                   BLOCKED_CAPABILITY, no marker
post-hold TTL crossing                      LEASE_EXPIRED, no marker
post-hold policy crossing                   POLICY_EXPIRED_AT_COMMIT, no marker
post-hold grant crossing                    GRANT_EXPIRED_AT_COMMIT, no marker
effect-first then revoke                    marker kept; later effect POLICY_DENIED
concurrent revoke during effect hold        independent BEGIN IMMEDIATE blocked;
                                            marker commits epoch 1, revoke epoch 2
previous 75 tests                           retained
combined                                    82 passed
#364 production fail-closed control          26 passed, 1 skipped
static scope (RC-v9 base)                    changed=6 allowed=6 forbidden=0
diff vs #380 base                            4 allowed paths only
```

## Classification / disclosure

- A / `API_ONLY_CORRECTNESS` (was API_ONLY_GREEN renamed per this round).
- B / `RAW_DB_WRITE_BYPASS_EXPECTED` / `BLOCKED_DB_WRITER_ISOLATION`.
- C / `OS_ISOLATION_NOT_RUN`.
- `EFFECT_COMMIT_NOT_PROVEN`: `proto_effect` remains a prototype marker, not
  Work Engine finalize or a production effect.
- Same-process caveat documented: an object holder can call synthetic owner
  methods, reach `_writer`/`db_path` and read the capability key. The HMAC
  capability binds the commit and catches request/token/capability mixing; it
  is **not** OS isolation or a real owner key.

## NOT_RUN

Real owner key/issuance, kernel `SO_PEERCRED`, two real OS uids, production
daemon/Fleet wiring, DB migration, real Windows, power loss/fsync and
production finalize/effect are NOT_RUN / NOT_PROVEN. Legacy
`ServerOSStore.acquire_lease`/`check_writer`, JSON `work revoke` and raw DB
writers remain outside this fence. No merge, deploy, service restart,
production DB/owner/OS grant, credential/OAuth/provider send,
force-push/reset/clean/prune or full pytest; no RC roll. No hosted CI check.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

