# QA2 Round 11 — #379 threat-boundary matrix: API-only GREEN vs same-UID DB-write EXPECTED_FAIL

**Pinned head:** `7245fc6b3711172251e20450d4356abff8af91ee` (Draft PR #379)
**Base:** `069f10cce084b95ef7e60ecd2c32784856002d54` (PR #378)
**Date:** 2026-10-21
**Verdict:** **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`.** No `QA_SIGNED`, no merge, no V1.

The two threat classes must not be conflated:

- **A — API-only caller (no direct SQL): GREEN.** 18/18 properties hold:
  CAS/epoch, rollback, commit-time refusal and missing-fence fail-closed.
- **B — same-UID raw SQLite writer: 10/10
  `EXPECTED_FAIL_UNTRUSTED_DB_WRITE`.** The fence is tamperable and the old
  token is re-authorized. `rc=0` in this mode means *the predicted failure was
  reproduced*, **not** a product PASS and **not** “the feature is complete”.
- **C — different OS UID / real file permission: NOT_RUN.** 2/2 `NOT_RUN`;
  no sudo/setuid/chown/privilege change was used.

`0600` file mode, `0600` Unix socket, SQLite triggers, an unkeyed SHA-256
digest and a per-connection `sqlite3` authorizer are **not a trust boundary
against a same-UID writer**. A production writer authorization needs a
different OS UID (or an owner-key broker) that the agent cannot impersonate,
not an in-database control.

## 1. Pinned identity

| Check | Result |
|---|---|
| PR #379 | OPEN, **Draft**, MERGEABLE |
| `refs/pull/379/head` | `7245fc6b3711172251e20450d4356abff8af91ee` |
| base | `069f10cce084b95ef7e60ecd2c32784856002d54` |
| local snapshot | identical to `git archive 7245fc6b…` (only `.snapshot-sha`) |
| diff | 3 allowed files, `+324/−48`, `SCOPE_OK` |
| author suite | **62 passed rc0** |
| later isolated PR after #379 | none found at audit time (open PR list) |

Logs: `logs/a-prime-379-metadata*.log`, `logs/a-prime-379-check-scope.log`,
`logs/a-prime-379-author62.log`.

## 2. Matrix and exit semantics

| Mode | Command | rc | Meaning |
|---|---|---|---|
| A `api` | `a_prime_379_threat_boundary.py --mode api` | 0 | **18/18 PASS**; every API-only property holds |
| B `db` | `... --mode db` | 0 | **10/10 EXPECTED_FAIL_UNTRUSTED_DB_WRITE**; tampering reproduced |
| C `osuid` | `... --mode osuid` | 0 | **2/2 NOT_RUN**; different-UID and kernel-peer evidence not obtained |

Exact logs/rc: `logs/a-prime-379-boundary-api.{log,rc}`,
`logs/a-prime-379-boundary-db.{log,rc}`,
`logs/a-prime-379-boundary-osuid.{log,rc}`.

## 3. A — API-only caller: GREEN (18/18)

| Case | Observation |
|---|---|
| A1 point-in-time | `PROTOTYPE_FENCED_CHECK`, `authorized:true`, `effect_eligible:false`, no `verified` |
| A2 stale CAS | `BLOCKED_STALE_CONTEXT_EPOCH`; context epoch stays 1; old token still valid; no side effect |
| A3 CAS zero / negative | `INVALID_REQUEST`; context epoch 1 |
| A4 legal rebind | epoch 2; old token `BLOCKED_STALE_CONTEXT_EPOCH` |
| A5 no-op rebind | `replayed:true`; token still valid |
| A6 commit-phase TTL | `LEASE_EXPIRED_AT_COMMIT`; lease/commit/token all rolled back |
| A7 commit-phase policy expiry | `POLICY_EXPIRED_AT_COMMIT`; all rows rolled back |
| A8 lease without fence row | `BLOCKED_OWNER_POLICY` (fail-closed) |
| A9 request injection (7 fields: `context_epoch`, `policy_digest`, `grant_digest`, `verified`, `now`, `peer_uid`, `COMMIT_FENCED`) | every one `INVALID_REQUEST`; no lease/commit |
| A10 missing context | `UNTRUSTED_ACTOR` |
| A11 historical replay | `authorized:false`, `historical:true`, `effect_eligible:false` |

The API-only path has **no P1 grant/use timing defect** in this matrix.

## 4. B — same-UID raw SQLite writer: 10/10 EXPECTED_FAIL

Each row is a **predicted failure of the boundary**, not a product feature
PASS. `ok:true` means the tamper succeeded.

| Case | Exact data change | Result |
|---|---|---|
| B1 REPLACE context | API A→B→A leaves epoch 3 and old token stale; `INSERT OR REPLACE` sets `context_epoch=1` | context epoch **3→1**; old token **ACCEPTED `authorized:true`** |
| B2 plain INSERT commit | after legal rebind to epoch 2, append `request_id='req:forged'`, `context_epoch=2`, `grant_ref=grant:proto-2`, recomputed digest | `commit_count 1→2`; `ORDER BY rowid DESC` picks the forgery; old token **ACCEPTED** |
| B3 DROP epoch trigger | `DROP TRIGGER proto_context_epoch_must_increase` (OK); same-epoch `UPDATE grant_ref=grant:proto-2` (OK); middle check `BLOCKED_STALE_CONTEXT`; `UPDATE` back | trigger gone, epoch stays 1; old token **ACCEPTED** |
| B4 DROP delete+epoch triggers | drop both triggers (OK); `DELETE` context (OK); `INSERT` same ref/actor/grant at epoch 1 (OK) | delete/reinsert path revives; old token **ACCEPTED** |
| B5 reforge unsigned digest | change `grant.items_json` to `["OTHER-ITEM"]` (check `BLOCKED_STALE_CONTENT`); recompute `grant_digest`; `INSERT OR REPLACE` commit | changed content **ACCEPTED** |
| B6 `recursive_triggers=ON` | same plain INSERT with `PRAGMA recursive_triggers=ON` | insert OK; `commit_count=2`; old token **ACCEPTED** |
| B7 authorizer per-connection | conn1 `set_authorizer` denies INSERT/UPDATE/DELETE → `DatabaseError`; conn2 without authorizer runs the same UPDATE | conn2 write **OK**; `grant.items_json` changed; authorizer is not schema-wide |
| B8 `chmod 0600` DB | `os.chmod(db, 0o600)`; same uid 1002 opens `r+b` | mode **0o600**; same-UID rw open **true** |
| B9 `chmod 0600` Unix socket | bind AF_UNIX socket, `chmod 0600`, same-UID client connect | mode **0o600**; same-UID connect **true** |
| B10 unkeyed digest | recompute `_content_digest(grant)` from the row | equals stored `grant_digest`; **no key material** |

Reproduced commands:

```sh
ROOT=/data/home/vince/artifacts/skills/qa2-independent-security/2026-10-21
SNAP=$ROOT/checkouts/a-prime-379-7245fc6
cd "$SNAP"
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round11>/probes/a_prime_379_threat_boundary.py --mode api    # rc 0
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round11>/probes/a_prime_379_threat_boundary.py --mode db     # rc 0 (EXPECTED_FAIL)
PYTHONPATH=$SNAP A_PRIME_SNAPSHOT=$SNAP \
  python3 <round11>/probes/a_prime_379_threat_boundary.py --mode osuid  # rc 0 (NOT_RUN)
```

All runs `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout 300 s`; temp SQLite/fork only.

## 5. Why 0600 / triggers / digest / authorizer are not a same-UID boundary

| Control | What it actually gates | Why it fails here |
|---|---|---|
| file mode `0600` | other OS UIDs | the same UID opens `r+b` (B8) |
| Unix socket `0600` | other OS UIDs | the same UID connects (B9); `SO_PEERCRED` is not even used in this prototype |
| SQLite `BEFORE UPDATE/DELETE` trigger | writers who keep the trigger | same UID can `DROP TRIGGER` (B3/B4); `INSERT OR REPLACE` does not fire DELETE when `recursive_triggers=0` (B1); plain `INSERT` has no guard (B2/B6) |
| unkeyed SHA-256 digest | edits that do not also rewrite the commit | the same writer recomputes it (B5/B10) |
| `sqlite3` authorizer | only the connection that installed it | a new connection has no authorizer (B7) |
| `PRAGMA recursive_triggers=ON` | only the connection that sets it | plain `INSERT` still wins (B6) |

The only credible boundary is **process/privilege separation**: a broker in a
different OS UID (or holding an owner key the agent does not have) owns the DB
and the fenced writer; the agent talks to it through a socket, and
`SO_PEERCRED`/owner signature binds the caller. In-DB triggers and hashes are
defense-in-depth against accidental edits, not against a same-UID adversary.

## 6. C — different OS UID: NOT_RUN (not PASS)

| Case | Status | Reason |
|---|---|---|
| C1 different-OS-UID write under `0600` | **NOT_RUN** | no sudo/setuid/chown/privilege change permitted; cannot attest a second real UID |
| C2 real `SO_PEERCRED` peer | **NOT_RUN** | synthetic peer uid 4242; kernel peer not exercised |

`uid=euid=1002` in this run. These rows are excluded from both PASS and FAIL;
they are not evidence of a working different-UID boundary.

## 7. Classification under this round's taxonomy

- **P0 — production Writer authorization / legacy entry closure.**
  The same-UID DB-write cases B1–B7/B10 show that a writer authorization built
  only on in-DB triggers/digests is not a boundary; production must move the
  authoritative write to a different-UID broker/owner-key path. The legacy
  `ServerOSStore.acquire_lease` / `check_writer`, JSON `work revoke`,
  `finalize`/effect and migration remain outside the fence
  (`PRODUCTION_WIRING_REQUIRED`, disclosed; not fixed).
- **P1 — prototype API path grant/use timing.**
  A-mode is 18/18 GREEN: CAS, epoch, rollback, commit-time TTL/policy refusal,
  missing-fence fail-closed and request-injection rejection all hold. No P1
  was reproduced in the API-only path this round.
- **P2 — evidence labels / test harness.**
  * `db` mode `rc=0` and `ok:true` mean `EXPECTED_FAIL_UNTRUSTED_DB_WRITE`,
    not product PASS; quoting it as GREEN is a labeling error.
  * the round-6 probe's hard-coded `context_read:false` is stale for #379.
  * the author's 62-test suite does not exercise `INSERT OR REPLACE`, plain
    `INSERT`, `DROP TRIGGER`, a second connection with/without an authorizer,
    or 0600 file/socket semantics; a green suite is not a boundary proof.
  * synthetic owner/context/clock/peer labels are fixtures, not real OS
    evidence.

## 8. Legacy P0 at the pinned head

- `ServerOSStore.acquire_lease` writes a lease after a prototype policy revoke,
  with `proto_commits=0`; the fence never sees it.
- legacy `check_writer` returns `authorized:true` for a pre-revoke token while
  `check_writer_fenced` returns `POLICY_DENIED`.
- README/PR body mark these `PRODUCTION_WIRING_REQUIRED`; they are correctly
  disclosed and remain P0 production Writer/legacy-entry blockers.

## 9. Requirements for a real production boundary (recommendation)

1. **Separate the writer identity.** The agent UID must not open the
   authoritative DB; a broker in a different UID owns it, with `0600` and
   `SO_PEERCRED`, and all acquire/rebind/revoke/finalize/effect calls go
   through that broker.
2. **Owner-signed commit.** The broker (or owner key) signs the commit
   (`context_ref`, `context_epoch`, policy/grant digests, resource/token);
   the unkeyed SHA-256 digest is not an owner proof (`NO_OWNER_TRUST`).
3. **Append-only authority.** Do not expose `proto_context`/`proto_commit` to
   arbitrary same-UID SQL; enforce `BEFORE INSERT` guards and
   `UNIQUE(resource, token)` as defense-in-depth, and keep the authoritative
   history append-only.
4. **Content generation.** Give grant/policy content a monotonic generation or
   immutable rows; a digest equality check cannot distinguish “never changed”
   from A→B→A.
5. **Close legacy entries.** Route `ServerOSStore.acquire_lease`/`check_writer`,
   JSON `work revoke`, `finalize`/effect and migration through the same broker;
   until then no positive production authorization exists.

## 10. Conclusion

- **API-only** #379 is GREEN for the tested transaction properties (18/18).
- **Same-UID DB-write** #379 is **EXPECTED_FAIL_UNTRUSTED_DB_WRITE** (10/10):
  the fence can be forged/reverted and the old token re-authorized. This is a
  failure of the boundary, not a completed feature.
- **Different OS UID** is **NOT_RUN** (2/2); no privilege change was attempted.
- Final: **`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`**, no `QA_SIGNED`, no merge.
  Evidence is `LOCAL_REAL_SQLITE` + `SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER` +
  `REAL_TEMP`; real owner key, two real UIDs, kernel peer, production wiring,
  Windows, Provider/OAuth and power-loss are `NOT_PROVEN`.
