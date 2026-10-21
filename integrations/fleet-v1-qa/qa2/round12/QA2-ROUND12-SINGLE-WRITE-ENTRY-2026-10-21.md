# QA2 Round 12 — #380 single-write entry: API GREEN, capability binding RED

**Pinned head:** `cbbfb63a76072871edfa6aa639355f5b63cc5853` (Draft PR #380)
**Base:** `7245fc6b3711172251e20450d4356abff8af91ee` (PR #379)
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`, no merge, no V1.

#380 is **API_ONLY_GREEN for the tested path** (15/15) and honestly labels the
raw-DB writer as expected bypass. But the "single writer" is a same-process
Python object, not a process/IPC/UID capability: the caller can reach the
internal `_writer`, its `state_dir`/`db_path`, the HMAC key and the owner-side
`issue` methods. Independently, a capability is **not bound to the token
argument**: a capability for request A authorizes token B of another generation
and writes a mixed effect marker, and a **historical replay receipt carries a
usable capability** that `check_use`/`commit_effect` turn back into
authorization. These are P1 prototype capability/API-path findings.

## 1. Pinned identity and scope

| Check | Result |
|---|---|
| PR #380 | OPEN, **Draft**, MERGEABLE |
| `refs/pull/380/head` | `cbbfb63a76072871edfa6aa639355f5b63cc5853` |
| remote branch `feat/s1-single-write-entry-20261021` | same exact SHA |
| base | `7245fc6b3711172251e20450d4356abff8af91ee` |
| local snapshot | identical to `git archive cbbfb63a…`; only `.snapshot-sha` |
| diff | exactly 5 allowed paths, `+969 / −140` |
| paths | README, `atomic_fence.py`, `check_scope.py`, `single_write_entry.py`, `tests/test_s1_atomic_prototype_single_write.py` |
| `check_scope.py` vs #379 | `changed=5 allowed=5 forbidden=0`, `SCOPE_OK` |
| old #379 code | old 62P rc0; combined 75P rc0; round10 controls 43/43; round11 API 18/18 |
| production import | stdlib + sibling `atomic_fence`; no daemon/Fleet/WorkEngine mutation wiring |
| `verified` / `COMMIT_FENCED` | absent from all receipts and the new module |

Logs: `logs/a-prime-380-metadata.log`, `logs/a-prime-380-check-scope.log`,
`logs/a-prime-380-author-*.log`, `logs/a-prime-380-local-equals-archive.log`.

## 2. Matrix

| Gate | rc | Result |
|---|---|---|
| author old `test_s1_atomic_prototype.py` | 0 | **62 passed** |
| author new `test_s1_atomic_prototype_single_write.py` | 0 | **13 passed** |
| author combined | 0 | **75 passed** |
| round10 controls at #380 (old #379 behavior) | 0 | **43/43** |
| round11 API controls at #380 | 0 | **18/18** |
| independent `api` | 0 | **15/15 PASS** |
| independent `capability` | 1 | **6/6 P1 RED** |
| independent `raw` | 0 | **6/6 EXPECTED_RAW_DB_BYPASS** |
| independent `race` | 0 | **3/3 PASS** (TOCTOU/serialization/rollback) |
| independent `osuid` | 0 | **2/2 NOT_RUN** |

`raw` rc 0 means the predicted bypass reproduced, not product PASS.
`osuid` rc 0 means NOT_RUN was recorded, not PASS.

## 3. A — API-only path: GREEN (15/15)

- issue → acquire → use-check → same-transaction effect marker: authorized,
  `PROTOTYPE_EFFECT_MARKER_COMMITTED`, `effect_marker:true`,
  `production_effect:false`, no `verified`.
- `check_use` writes no marker.
- replay is idempotent: `replayed:true`, `authorized:false`, one marker.
- same `effect_ref` + different payload → `IDEMPOTENCY_CONFLICT`; same
  `request_id` + different `effect_ref` → `EFFECT_ALREADY_COMMITTED`.
- tampered capability → `BLOCKED_CAPABILITY`, no marker.
- revoke-first → `POLICY_DENIED` for use and effect, no marker.
- context A→B→A → `BLOCKED_STALE_CONTEXT_EPOCH`, no marker.
- expired / superseded token → `LEASE_EXPIRED` / `STALE_TOKEN` for both use
  and effect, no marker.
- missing grant → `BLOCKED_OWNER_POLICY`; missing policy likewise.
- caller-side extra kwargs → `TypeError`; request-injected `uid` →
  `INVALID_REQUEST`.
- fault in `hold` after marker insert → `RuntimeError`, no marker row.
- replay after revoke → `POLICY_DENIED`; replay after context ABA →
  `BLOCKED_STALE_CONTEXT_EPOCH` (the fence runs before the idempotency lookup;
  fail-closed, but no historical replay receipt is available once authority
  changed).
- In A5–A7/A9/A9b the refusal leaves the lease row, `id_sequences` token,
  `proto_commit` count and `proto_effect` count byte-for-byte unchanged.
- no `verified` key, no `COMMIT_FENCED` value; `classification()` reports
  A/B/C, `EFFECT_COMMIT_NOT_PROVEN`, `NOT_V1_SIGNED`.

Two-process behavior (`race`, 3/3 PASS):

- **revoke-first:** child commits revoke; parent use/effect → `POLICY_DENIED`,
  effect count 0.
- **effect-first with hold:** the child's effect transaction holds the write
  lock after inserting the marker; a parent `BEGIN IMMEDIATE` with
  `busy_timeout=0` gets `OperationalError`; after release the child commits, the
  marker exists, then the revoke commits, and later use → `POLICY_DENIED`.
  A revoke cannot interleave inside the effect transaction.
- **crash-before-COMMIT:** child `os._exit(17)` in `hold`; reopen shows no
  marker (`effect_count=0`).

## 4. P1 — capability binding and replay (independent RED)

### 4.1 C1: capability for request A authorizes token B of another generation

A capability is HMAC over commit fields, but `check_use`/`commit_effect` fetch
the commit by `request_id` and then pass the caller's `token` to the fence
without requiring `commit.token == token`.

```text
first  = acquire(req:1)                     # token 1, context_epoch 1
issue_grant(grant:proto-2); rebind(ctx -> grant:proto-2)  # epoch 2
advance clock past TTL; second = acquire(req:2)           # token 2, epoch 2
check_use("req:1", token=2, capability_of_req1[token 1])
  -> ACCEPTED authorized:true, context_epoch 2, grant:proto-2
commit_effect("req:1", token=2, capability_of_req1, effect:cross)
  -> ACCEPTED; marker row = {request_id: "req:1", token: 2,
                             context_epoch: 2, grant_epoch: 1}
```

The marker mixes request 1's identity with token 2's generation; the capability
did not bind the token/request pair. Fix: require
`int(commit["token"]) == token` (and resource equality) and use that same
commit for both the capability check and the marker. P1 prototype API path.

### 4.2 C6: a historical replay receipt carries a usable capability

`acquire` unconditionally attaches a capability, including to a
`PROTOTYPE_HISTORICAL_REPLAY` receipt:

```text
replay = acquire(req:1, same facts)
  -> authorized:false, historical:true, replayed:true, token 1, capability present
check_use("req:1", token 1, replay.capability)
  -> ACCEPTED authorized:true
commit_effect("req:1", token 1, replay.capability, effect:replay)
  -> ACCEPTED authorized:true; effect_count 1
```

The replay response says no current authorization, but its capability is
accepted as current authority. Fix: do not attach a capability to a
historical/replayed receipt (or mark it `HISTORICAL` and reject it in
`check_use`/`commit_effect`). P1 prototype API path.

### 4.3 C2–C5: the "single writer" is a same-process Python object

| Case | Evidence |
|---|---|
| C2 caller self-issue | caller calls `issue(policy_ref='policy:self', grant_ref='grant:self', context_ref='ctx:self', policy_epoch=9, authority_epoch=9, grant_epoch=9)` on the same object; `acquire` then succeeds with those epochs |
| C3 readable HMAC key | `entry._capability_key` is reachable (underscore is convention only; len 23 in the test); a forged capability over a commit acquired via `entry._writer.acquire` is accepted by `check_use` |
| C4 public writer/state | `entry._writer.state_dir` / `entry._writer.db_path` are reachable; a direct `sqlite3.connect(db_path)` works |
| C5 constructor identity | the caller constructs with `actor_ref="human:SELF"`, `synthetic_peer_uid=7777`, `capability_key=b"self-key"`; there is no socket/UID/IPC check (`socket_api:false`, same-process Python object) |

These are **prototype capability correctness**, not a remote production
exploit: they require holding the writer object. They show the object is not a
restricted principal boundary, and the owner-side `issue`/`issue_grant`/
`revoke`/`rebind` methods live on the same object the caller uses. A real
single-writer design needs a separate owner-side process/key, not a Python
attribute convention.

## 5. B — same-UID raw DB writer: EXPECTED_RAW_DB_BYPASS (6/6)

| Case | Observation |
|---|---|
| R1 raw effect marker | after revoke, direct SQL inserts `proto_effect`; `effect()` sees it; the API use-check still refuses `POLICY_DENIED` |
| R2 raw context+commit forge | direct `REPLACE` of `proto_context` (epoch 2, grant2) and `proto_commit` (epoch 2, grant2, recomputed digest), plus the readable HMAC key, makes `check_use` **ACCEPTED** for token 1 |
| R3 `chmod 0600` DB | mode `0o600`; same UID opens `r+b` |
| R4 authorizer | conn1 `set_authorizer` denies writes (`DatabaseError`); conn2 without it inserts the marker |
| R5 legacy Store | `ServerOSStore.acquire_lease` writes a lease after revoke; legacy `check_writer` returns `authorized:true`; fenced use is `POLICY_DENIED`; `commit_count=1` |
| R6 labels | `classification()["B"] == RAW_DB_WRITE_BYPASS_EXPECTED` / `BLOCKED_DB_WRITER_ISOLATION` |

The PR/README correctly disclose B as expected and do not claim a fix. R2 adds
that the readable HMAC key plus raw DB write makes even the API use-check
accept a rewritten lineage.

## 6. Static analysis of "single write entry"

- `SingleWriteEntry` is a plain Python class wrapping
  `AtomicFencePrototype`; there is no socket, bind/listen/accept, subprocess,
  fork, `os.getuid`, or `SO_PEERCRED` in `single_write_entry.py`.
- `self._writer` is an `AtomicFencePrototype`; `state_dir`, `db_path`,
  `_transaction`, `_clock`, `_synthetic_peer_uid`, and the module-level
  `atomic`/`ServerOSStore` are reachable. Python has no private attributes.
- `_capability_key` is a caller-injectable bytes field; the HMAC is an
  in-process integrity check, not a bearer secret against a same-UID caller.
- `issue` accepts policy/grant/context/epochs from the caller; it is on the
  same object as `acquire`/`check_use`/`commit_effect`. There is no
  owner/caller principal split.
- The constructor accepts `actor_ref`, `synthetic_peer_uid`, `clock`, and
  `capability_key` from the caller. A production entrypoint must bind these
  from the trusted process/peer, not the object constructor.
- `proto_effect` is an ordinary table; there is no BEFORE INSERT authenticity
  guard. `payload_digest` is a caller-supplied string, not a computed payload
  proof; the marker has no context_ref/grant_ref, only epochs.

## 7. Classification

### P0 — production Writer authorization / legacy entry closure

- Same-UID raw DB write can insert effect markers, rewrite context/commit and
  use the readable key to make `check_use` accept (R1/R2); this is the same
  production writer-isolation gap, correctly labeled
  `BLOCKED_DB_WRITER_ISOLATION`.
- Legacy `ServerOSStore.acquire_lease` / `check_writer` still bypass (R5);
  JSON `work revoke`, finalize/effect and migration remain outside the fence.
- These are disclosed `PRODUCTION_WIRING_REQUIRED`; not fixed by #380.

### P1 — prototype API path grant/use timing and capability correctness

- **C1** capability not bound to `(request_id, token, resource)`; a capability
  for request A authorizes token B and writes a mixed marker.
- **C6** historical replay receipt carries a usable capability; `authorized:
  false` becomes `authorized:true` through `check_use`/`commit_effect`.
- **C2–C5** the "single writer" is a same-process object; caller can self-issue
  authority with chosen epochs, read the HMAC key, reach `state_dir`/`db_path`,
  and choose actor/uid/key in the constructor. This is prototype capability
  correctness, not a production exploit.

### P2 — evidence labels / test harness

- `check_scope.py` was loosened from an exact test file to
  `ALLOWED_TEST_PREFIX = "tests/test_s1_atomic_prototype"`, allowing any
  matching test file; still test-only, but the exact allowlist is weaker.
- The new 13P suite does not test cross-token capability, replay capability,
  readable key, `state_dir`/`db_path`, caller `issue`, or the raw-key+forge
  combination; a green 75P is not a capability proof.
- `payload_digest` is caller-provided; the effect marker is not a payload/effect
  proof and has no context_ref/grant_ref.
- The author's `#364 production fail-closed control` (26P/1S) was not
  independently rerun in this round.
- Synthetic owner/peer/clock labels remain fixtures; `EFFECT_COMMIT_NOT_PROVEN`.

### NOT_RUN

OS isolation, real owner key, two real uids, kernel `SO_PEERCRED`, production
daemon/Fleet/WorkEngine wiring/migration, real Windows, Provider/OAuth,
power-loss/fsync, real `finalize_run_verified`/effect. `EFFECT_COMMIT_NOT_PROVEN`.

## 8. Minimal fixes / recommendations

1. **Bind the capability to the exact token.** In `check_use`/`commit_effect`,
   after fetching the commit by `request_id`, require
   `int(commit["token"]) == token` and `commit["resource"] == resource`; use
   that commit for both the capability check and the effect marker. Never pass
   a caller token to the fence independently of the capability's commit.
2. **Do not mint capabilities on replay.** `acquire` should omit `capability`
   when the receipt is `historical`/`replayed`, or set
   `capability_scope="HISTORICAL"` and have `check_use`/`commit_effect` reject
   it. A replay response must not yield current authorization.
3. **Separate owner and caller.** Move `issue`/`issue_grant`/`revoke_policy`/
   `rebind_context` behind a different principal/process; the caller object
   should not expose them. Do not expose `_writer`, `state_dir`, `db_path` or
   `_capability_key` to the caller.
4. **Bind identity from the trusted entrypoint.** `actor_ref`,
   `synthetic_peer_uid`, clock and key must come from the trusted process/
   `SO_PEERCRED`, not constructor arguments supplied by the caller.
5. **Effect marker integrity.** Store `context_ref`/`grant_ref` and a
   server-computed payload digest (or, better, use the real finalize/effect
   transaction). The marker is not a production effect.
6. **P0 production boundary.** Use a different-UID broker/owner-key signer that
   owns the DB; route legacy `acquire_lease`/`check_writer`, JSON revoke,
   finalize/effect and migration through it. Until then no positive production
   authorization exists.

## 9. Reproduction

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-380-cbbfb63
cd "$SNAP"
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round12>/probes/a_prime_380_single_writer.py --mode api         # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round12>/probes/a_prime_380_single_writer.py --mode capability  # rc 1 (P1)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round12>/probes/a_prime_380_single_writer.py --mode raw         # rc 0 (EXPECTED)
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round12>/probes/a_prime_380_single_writer.py --mode race        # rc 0
PYTHONPATH=$SNAP:$SNAP/integrations/fleet-v1-atomic-prototype \
  A_PRIME_SNAPSHOT=$SNAP \
  python3 <round12>/probes/a_prime_380_single_writer.py --mode osuid       # rc 0 (NOT_RUN)
```

All runs bounded `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 400 s`; temp SQLite/fork only. No production daemon/Provider, no
privilege change, no merge/reset/clean/force-push.

## 10. Conclusion

- #380 **API_ONLY_GREEN** for its tested path (15/15), old #379 behavior intact
  (62P / 75P / 43 controls / 18 API controls).
- It does **not** establish a single writer/process/IPC boundary: it is a
  same-process Python object with a reachable writer/key/state and a caller-
  accessible owner side.
- Two independent P1 API-path defects: cross-token capability authorization
  (C1) and historical-replay capability re-authorization (C6). C2–C5 are
  prototype capability correctness.
- Raw DB writer/legacy bypass remains P0 `PRODUCTION_WIRING_REQUIRED`, openly
  labeled `RAW_DB_WRITE_BYPASS_EXPECTED` / `BLOCKED_DB_WRITER_ISOLATION`.
- OS isolation, real provider and real WorkEngine finalize are NOT_RUN;
  `EFFECT_COMMIT_NOT_PROVEN`.
- Final: **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**, no `QA_SIGNED`.
