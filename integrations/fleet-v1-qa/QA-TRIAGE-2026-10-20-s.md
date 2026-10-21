# QA-TRIAGE-2026-10-20-s — Q1 independent acceptance for #250 / #256

**Status: `QA_PARTIAL` / `NOT_RELEASE`. No QA signature.**

Lane: `Q1-independent-acceptance`, campaign #244, parent #250, QA register #256.
Independent Session; no product source edited. Additive QA files only under
`integrations/fleet-v1-qa/**`; probes and logs live outside the repo under
`artifacts/skills/automated-testing/q1-security-qa-20261020/`.

Snapshot: **2026-10-20T06:35Z (UTC)**. All candidate SHAs were read from GitHub
immediately before the run. This round builds an **author-test-independent**
harness (own fake provider service, own request builders, own context record)
and does not import `tests/threads.py`, `tests/test_agent_control_router.py` or
any other author test module.

---

## 1. Pinned inputs

| Candidate | Head SHA | Role |
|---|---|---|
| Old S1 writer fence #241 | `429a717bedf1f6c28057a5e355970ff351100571` | named RED baseline |
| `origin/main` | `771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8` | T10 RED baseline |
| New S1 handoff adapter #315 | `7d35535b53f791513a10b28554b4bd79e943e77a` | GREEN candidate (pushed Draft) |
| S1 lease/finalize hardening #304 | `776cd982689774a5a275c2cc395a2e092d1910be` | T2/T3/T10 hardening |
| S1 grant window #307 | `6abefaa7beadb908054cd7d162c1e34c48a29eef` | grant-window RED→GREEN |
| S2 reconcile gate #312 | `2a3e1352b442b0a307deba50e293a10c8ef34d61` | reconciliation route |
| S2 reconciliation driver #299 | `4452ddfa3cda3404cc4038acf8086772d868c578` | driver (included in #312) |
| R1 client turn id #311 | `34b215c355d93efe9e7b06fcc09f9a803c061f00` | schema-only |

`#272` contains `#241`; the new S1 branch is based on `#307` (`6abefaa`), which
contains `#272`. The three S1 hardening PRs are a linear stack.

---

## 2. The independent handoff harness

`qa_probe_r2_handoff.py` wires the **production** objects:

```text
Daemon(real temp state_dir, real JSON/SQLite stores)
  └─ CallAdapter (in-process call(method, params); no AF_UNIX)
       ├─ mcp.agent_mcp.DaemonBindings      (the real production bindings handle)
       └─ mcp.agent_mcp.DaemonContexts      (the real production context handle)
LocalLoopbackTransport(service=<own fake provider>, bindings=DaemonBindings)
FleetAgentControlRouter(local_device_ref=device:server-beijing)
```

It uses its own `Service`/`Provider` (the two methods the transport reads), its
own context record, and `request_from_payload` for the production request
envelope. No author test module is imported.

Evidence level: **LOCAL / REAL_COMPONENTS / IN_PROCESS_DAEMON**. It is not a
real AF_UNIX socket and not REAL_DEVICE.

Named assertions:

- `WIRE` — the production bindings handle exposes `close`.
- `T1` — after handoff A→B, a send on source A is refused; source binding CLOSED.
- `T1b` — target B has its own writer claim and is `current_writer`; B may send.
- `T2` — while A owns the scope, B is refused `NOT_CURRENT_WRITER`; A is admitted.
- `T7` — the production handle does not forward a caller-chosen writer; a direct
  daemon RPC is observed separately.
- `S2` — a handoff to a **new** target (`session.create`) reaches
  `context_handed_off` and is checked for the same transfer.

### 2.1 RED on #241 `429a717b` (named failures)

`probe-r2-red-241.log` — 24 checks, 13 failed:

```text
FAIL WIRE production DaemonBindings exposes close :: close=False
PASS T2 B refused while A owns the scope :: NOT_CURRENT_WRITER
PASS T2 A admitted while it owns the scope
FAIL handoff A->B (resume target) :: ok=False reason=NOT_CURRENT_WRITER
     completed=('checkpoint',)          # target resume is gated before the transfer
FAIL T1 source A binding is CLOSED      :: state=OPEN
FAIL T1 stale A send is refused         :: ok=True
FAIL T1b target B has a writer claim    :: None
FAIL T1b target B is current writer     :: current=session:q1:1 (the source)
FAIL T1b target B may send              :: NOT_CURRENT_WRITER
PASS T7 production handle does not forward caller-chosen writer
OBSERVE direct agent.binding.record writer = victim/99/999
PASS S2 handoff C->new-target reaches context_handed_off
FAIL S2 source C binding CLOSED         :: state=OPEN
FAIL S2 source C no longer current      :: current=session:q1:3
FAIL S2 stale C send refused            :: ok=True
FAIL S2 target has its own claim        :: None
FAIL S2 target is current writer        :: current=session:q1:3
FAIL S2 target may send                 :: NOT_CURRENT_WRITER
```

Three distinct failures, not one:

1. **P0 (old F1)** — the production `DaemonBindings` has no `close`, so the
   source is never revoked; stale A remains `current_writer` and is admitted.
2. **P1 (old F2)** — the target receives no claim; after the source is closed
   the scope would be unowned, and the target cannot write.
3. **New**: the default `session.resume` target is **refused at
   `resolve_target`** because `_admit` requires the target to be the current
   writer *before* the transfer. A handoff to an existing non-writer session
   cannot start; only a `session.create` target reaches `context_handed_off`.

T2 holds on the old tree: while A owns the scope, B is fenced deterministically.

T7 on the old tree: the production `DaemonBindings.put` forwards only
`session_ref/session_role/bound_item_ref/device_ref`, so the caller-chosen
writer is **not** forwarded; the direct `agent.binding.record` RPC still mints
`victim/99/999` (the known explicit-writer surface, Snapshot R F4).

---

## 3. T10 / finalize evidence: RED on main, GREEN on #304

`qa_probe_r2_t10.py`.

**RED `origin/main@771c81c`** (`probe-r2-t10-red.log`): `finalize_run` has no
`t10_required` route, and a caller `PASS/0` concludes `SUCCEEDED` with **no**
`authorization`/`t10_acceptable` label.

**GREEN `#304@776cd98`** (`probe-r2-t10-green.log`), 12/12:

- `t10_required=True` without a trusted parent → `BLOCKED_TRUST_SOURCE`, Run
  stays `FINALIZING`, zero evidence.
- legacy finalize → `UNSIGNED` / `t10_acceptable=false`, labelled in the durable
  record.
- a forged `finalize.json` (add a runner receipt + `T10_VERIFIED`) is refused
  `EVIDENCE_HASH_MISMATCH`; a tampered genuine T10 file is likewise refused.
- `finalize.json` as a directory: the final rename now happens **inside** the
  transaction; the exception rolls it back, Run stays `FINALIZING`, zero
  evidence.

This closes Snapshot R F5 (forged label) and F6 (post-commit rename) at the
store layer, independently.

---

## 4. Lease hardening: RED on #298, GREEN on #304

`qa_probe_r2_lease_hardening.py`.

**RED `#298@e1f783f`** (14/14 old-bad behaviours observed): `ttl_s=0/-1` writes
an already-expired row and returns; `nan/inf/1e12` raise raw
`ValueError`/`OverflowError`; `check_writer(now=nan)` returns
`authorized=True`; string/float/bool tokens match the minted token; an empty
owner is written; a malformed row raises a raw `ValueError`.

**GREEN `#304@776cd98`** (15/15): all TTLs → `LEASE_TTL_INVALID`; NaN clocks →
`LEASE_CLOCK_INVALID` with the live holder unchanged; NaN commit instant →
`LEASE_CLOCK_INVALID`; coerced tokens → `STALE_TOKEN`; empty owner →
`LEASE_OWNER_REQUIRED`; malformed row → `LEASE_ROW_INVALID`.

Supplementary: `tests/test_lease_hardening.py tests/test_finalize_consistency.py`
→ **26 passed, 22 subtests** (`r2-304-tests.log`).

---

## 5. Grant window: RED on #272, GREEN on #307

`qa_probe_r2_grant_window.py`.

**RED `#272@fc93590`** (`probe-r2-grant-red.log`):

```text
expired grant expires_at = <past>+08:00  -> verified: True
grant expires_at = "not-a-time"          -> verified: True
```

The window was compared as text, so an expired `+08:00` grant sorted above the
UTC instant and authorized; a malformed window sorted above every real instant.

**GREEN `#307@6abefaa`** (`probe-r2-grant-green.log`): both are refused
(`POLICY_DENIED` for the expired window, `INVALID_CONTEXT` for the unreadable
one). This is a real trust-producer fail-open closed, independently.

---

## 6. #312 `agent.dispatch.reconcile` — independent evaluation

`qa_probe_r2_reconcile_312.py` → **29/29** (`probe-r2-reconcile-312.log`).

- **Trusted context only**: the request carries `context_ref` + `key`; the
  principal is `actor.resolve` + `validate` + `freshness == FRESH` from the
  daemon's store. A stale context is `UNTRUSTED_ACTOR`; a different principal is
  `REFUSE_PRINCIPAL`; neither reads the source.
- **Caller authority is refused, not ignored**: `principal`, `writer`,
  `current_writer`, `source`, `source_capability`, `observation`, `reconciler`,
  `verifier`, `grant`, `grant_ref`, `attempt`, `phase`, `result`, `success` are
  each `INVALID_CONTEXT`.
- **Writer/owner read**: the writer is the claim on the attempt's own
  `session_ref` binding; `current_writer` is the kernel's derivation from the
  same store. The grant verifier and observation source are daemon-held
  constructor objects, never request fields.
- **No send/retry effect**: with no source the answer is
  `BLOCKED_PROVIDER_SOURCE`, `source_called=false`, `dispatching=false`, and the
  attempt stays `EFFECT_UNKNOWN`. A `RESPONSE_ID_ONLY` source is refused without
  being called. The driver exposes no `send`/`retry`/`dispatch` API.
- **Settle path is synthetic**: a test-double `CLIENT_TURN_ID` source returning
  `APPLIED` yields `SETTLE_APPLIED`/`SETTLED`, one source read, one ledger write,
  `dispatching=false`. This is a gate/kernel proof, **not** provider evidence.
- Missing key → `INVALID_CONTEXT`; unknown key → `NO_ATTEMPT`; terminal attempt
  → `NOT_UNKNOWN` (source not read); grant without a verifier →
  `BLOCKED_GRANT_UNKNOWN` (source not read).

Production remains `BLOCKED_PROVIDER_SOURCE`: no production observation source
is bound. `tests/test_dispatch_reconcile_gate.py` +
`tests/test_effect_reconciliation.py` → **42 passed, 19 subtests**
(`r2-312-299-tests.log`). #299's driver has no send path and no second ledger.

---

## 7. #311 client turn id — schema-only, not provider evidence

`tests/test_fleet_v1_runtime_client_turn_id.py` → **30 passed**
(`r2-311-tests.log`). The module has no network/send/transport imports.

Independent schema check: `codex-cli 0.160.0` is installed; running
`CODEX_HOME=<empty temp> codex app-server generate-json-schema --out <temp>`
regenerated the v2 schemas, and the sha256 values match the PR document exactly:

```text
TurnStartParams.json          2dfcf68705896fadc344ccfeb2e9fe5a6bcbbb8b9a90cf449ce232b636daf05a
ThreadReadParams.json         dfe040c6ac71d30795b8be3f3ff232e66f362a37f883b491e5d1ea367f470db4
ThreadReadResponse.json       3a0aaf9cff80796d07130b85723ca825f9203796d15c878f452083cf9f9a6f0a
ThreadTurnsListParams.json    2a8b93d7d4437cc25e16a1dde16d8db3c338d39e16c5eece2a56e943a21577d7
ThreadItemsListParams.json    d5cf9784413f1d1abba756e04e48b273a9d12ab2c5675442e91b6a4ee55e19f0
ThreadTurnsListResponse.json  8db83d38553204a223cf349680c59b0cc674ba95a4637e1995bfeabd73c80219
```

Evidence level: **SCHEMA_ONLY + SYNTHETIC_FIXTURE**. The stable schema allows
the fields; it does **not** prove that the provider copies
`clientUserMessageId` into `ThreadItem.userMessage.clientId` at runtime. Live
proof is **NOT_RUN**; production remains `BLOCKED_PROVIDER_SOURCE`. The doc's
`stable/v2/...` path label is the generator's `out/v2/...` on this host (a
label difference, not a hash difference). No create/send/model call was made.

---

## 8. New S1 handoff adapter #315 — P0 resolved, one P1 remains

Pushed Draft **#315** at `7d35535b53f791513a10b28554b4bd79e943e77a`
(`feat/s1-handoff-daemon-bindings-20261020`, stacked on #307).

### GREEN — the old P0 is resolved on the real production graph

The same `qa_probe_r2_handoff.py` that produced the 13 named RED failures on
`#241` now gives **25/25**, `lease_backed=true`
(`probe-r2-handoff-green-315.log`):

- `DaemonBindings.close` exists; handoff A→B completes with
  `write_authority_transferred`.
- source A is `CLOSED`; stale A send is refused `THREAD_CLOSED_FOR_NEW_WORK`.
- target B's claim is minted from the **live Work lease** (`agent:B/2`), B is
  `current_writer`, and B may send.
- while A owns the scope, B is refused `NOT_CURRENT_WRITER`; A is admitted.
- `agent.binding.record` now refuses a caller-chosen writer
  (`UNTRUSTED_ACTOR`) — Snapshot R F4 closed.
- create-target handoff also transfers (`agent:D/2`).

The PR's own `tests/test_handoff_daemon_bindings.py` → 9 passed (author test;
not QA evidence; `r2-315-handoff-tests.log`).

### P1 — `agent.binding.close` / `agent.binding.claim` accept a context that does not AUTHORIZE a mutation

Both routes check `actor.validate` + `freshness`, not `actor.authorizes`:

```text
runtime/daemon.py:3262  actor_mod.validate(trusted)          # claim
runtime/daemon.py:3266  freshness = actor_mod.freshness(...)
runtime/daemon.py:3398  actor_mod.validate(trusted)          # close
runtime/daemon.py:3402  freshness = actor_mod.freshness(...)
```

`validate` is structural and `freshness` is age-only. `actor.authorizes`
additionally requires `provenance == FACT` and `provider_status == VERIFIED`
(the same check the #272 verified read uses). A `BLOCKED`/`UNSUPPORTED` context
with non-FACT provenance is a valid, fresh record and can be minted by
`actor.build` with a non-empty `actor_ref`.

`qa_probe_r2_close_context.py` on this HEAD (8 checks, 4 failures):

```text
VERIFIED/FACT   -> close closed=true, claim claimed=true   (control, expected)
BLOCKED/INFERRED -> close closed=true                      (should refuse)
UNSUPPORTED/INFERRED -> close closed=true                  (should refuse)
BLOCKED/INFERRED -> claim claimed=true                     (should refuse)
UNSUPPORTED/INFERRED -> claim claimed=true                 (should refuse)
stale / missing context -> UNTRUSTED_ACTOR                 (correct)
```

Minimal fix (both routes): keep `validate` for shape, then use
`actor.authorizes(trusted, now)` and refuse when `authorizes` is not true; the
separate freshness check becomes redundant. Add BLOCKED/UNSUPPORTED/stale/missing
tests. The same pattern also exists in `_r_agent_dispatch_record`
(`runtime/daemon.py:3586/3590`, S2 #252) — out of #315's diff, but the same
trusted-context gate should be aligned.

### Scope limitation

`_revoke_source_write_authority` requires the target binding in the **source**
store; a cross-store (Tokyo→Dell) transfer is a fail-closed non-transfer. That
is the safe direction, but T1 `REAL_DEVICE` remains **BLOCKED**, not PASS. The
same-store resume and create paths are the ones this receipt covers.

---

## 9. Gate matrix (this round)

| Gate | Old #241 | #304/#307 | #312 | New S1 #315 |
|---|---|---|---|---|
| T1 stale handoff | **RED** (P0) | — | — | **LOCAL GREEN** (same-store; 25/25); REAL_DEVICE **BLOCKED**; P1 trust gap |
| T2 one writer | LOCAL PASS | lease hardening RED→GREEN | — | LOCAL PASS (lease-backed claim) |
| T3 expired/revoked | LOCAL PARTIAL | TTL/clock/commit hardening GREEN | — | admission checks the live lease; LOCAL |
| T4 unknown effect | ABSENT | — | gate exists; production `BLOCKED_PROVIDER_SOURCE` | — |
| T5 key+payload | NOT DONE (S2 #252) | — | — | — |
| T6 expired row | CODE/LOCAL PASS | unchanged | — | — |
| T7 cross-user/grant | PARTIAL (direct writer RPC) | grant window fail-open closed | principal/writer gates PASS | PARTIAL — record RPC fixed; BLOCKED-context transfer **P1** |
| T10 child PASS vs tests | not in #241 tree | **GREEN** (#304) | — | — |
| T8/T9/T11/T12 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |

No `REAL_DEVICE` or `CLIENT_E2E` evidence. Lease/T10 positive paths remain
`BLOCKED_TRUST_SOURCE` in production. #295–#298 author-reported regressions are
not converted to QA evidence; only the targeted files above were re-run.

---

## 10. Receipts

| Run | Command | Result | Log sha256 |
|---|---|---|---|
| Handoff RED #241 | `qa_probe_r2_handoff.py` | 24 checks, 13 named failures | `72b0fd4c…` |
| Handoff GREEN #315 | `qa_probe_r2_handoff.py` | **25/25** | `probe-r2-handoff-green-315.log` |
| Close/claim context #315 | `qa_probe_r2_close_context.py` | 8 checks, **4 P1 failures** | `probe-r2-close-context-315.log` |
| T10 RED main | `qa_probe_r2_t10.py` | 2/2 absence | `6571d8e0…` |
| T10 GREEN #304 | `qa_probe_r2_t10.py` | 12/12 | `dd67e8b5…` |
| Lease RED #298 | `qa_probe_r2_lease_hardening.py` | 14/14 old-bad | `49aa9189…` |
| Lease GREEN #304 | `qa_probe_r2_lease_hardening.py` | 15/15 | `a43acdd6…` |
| Grant RED #272 | `qa_probe_r2_grant_window.py` | 2/2 fail-open | `a758c944…` |
| Grant GREEN #307 | `qa_probe_r2_grant_window.py` | 2/2 refused | `a050cc3c…` |
| Reconcile #312 | `qa_probe_r2_reconcile_312.py` | 29/29 | `00a9dee2…` |
| #315 author tests | pytest 1 file | 9 passed | `r2-315-handoff-tests.log` |
| #312+#299 tests | pytest 2 files | 42 passed, 19 subtests | `e5d99c73…` |
| #311 tests | pytest 1 file | 30 passed | `dd298c5c…` |
| #304 tests | pytest 2 files | 26 passed, 22 subtests | `397d4f37…` |

All runs used explicit files, `timeout`, and (for pytest) the
`systemd-run` 1G/no-swap/25% CPU/nice-10 guard. No large suite, C++ build,
provider/network/model call, serverosd reload or production send was performed.

---

## 11. Handback

1. **#315 (P0 resolved / P1 open)**: the real MCP handoff P0 is independently
   GREEN at `7d35535` (25/25), including stale-source fencing and the target's
   lease-backed claim. **P1**: `agent.binding.close`/`claim` accept
   BLOCKED/UNSUPPORTED contexts (non-FACT provenance) and transfer authority;
   use `actor.authorizes`, add the four tests. Cross-store (Tokyo→Dell)
   transfer is fail-closed; T1 `REAL_DEVICE` is still BLOCKED.
2. **#304**: independently GREEN for F5/F6 and the lease TTL/clock/token
   hardening. T10 still has no production parent.
3. **#307**: independently GREEN for the grant-window fail-open.
4. **#312/#299**: gate is sound and has no send/retry effect; production source
   remains `BLOCKED_PROVIDER_SOURCE`; the synthetic settle is not provider
   evidence.
5. **#311**: schema-only; hashes independently reproduced; live proof NOT_RUN.
6. **#256**: `QA_PARTIAL` stands; no `QA_SIGNED`, no `V1_COMPLETE`, no release.

## 12. What this does not claim

- No product PASS beyond the named LOCAL checks; no REAL_DEVICE or CLIENT_E2E.
- No provider/network/credential/Feishu/model request; no production service
  start, reload or send.
- No author-reported full regression is converted to QA evidence.
- The new S1 GREEN is not claimed until its Draft PR is pushed and the same
  probe is run against the real GitHub HEAD.
