# S1 · Handoff write-authority transfer through the real daemon bindings

**Status: source + hermetic local tests. No production provider, session or
model request.**

| Fact | State |
|---|---|
| P0 | #310 F1/F2: `DaemonBindings` had no `close`, so the deployed handoff skipped revocation; the target never received a writer claim |
| Native path | `mcp/agent_mcp.py::DaemonBindings.close/claim` → `runtime/daemon.py#agent.binding.close/claim` → `AgentControlStore` |
| Transfer | one store mutation closes the source and writes the target's claim |
| Writer authority | the LIVE Work Engine lease (`work-engine:leases`); never a caller string |
| Actor verdict | `actor.authorizes` — FRESH **and** provenance FACT **and** provider VERIFIED |
| Evidence | `CODE / LOCAL`; `REAL_TEMP`, `REAL_DEVICE`, `CLIENT_E2E`: `NOT_RUN` |

## 1. The gap

`fleet/agent_control_router.py::_revoke_source_write_authority` checked
`hasattr(source_bindings, "close")`. The production MCP loopback is built with
`mcp/agent_mcp.py::DaemonBindings`, which had `get`/`put`/`records_for` and no
`close`; the check therefore returned a non-revocation and the handoff reported
success with the source binding still `OPEN`. The green handoff tests used
`tests/threads.py::MemoryBindings`, which does implement `close`, so the test
double hid the deployed gap. Even with a close, the target had no
owner/epoch/token, so closing the source would have left the scope merely
unowned (F2).

## 2. What changed

### 2.1 `DaemonBindings` (real daemon-backed)

* `close(ref, *, at, reason, successor_ref=None, context_ref=None)` calls
  `agent.binding.close`, refuses a `closed=false` answer, and refuses a returned
  record whose state is not `CLOSED`. The daemon's clock stamps `closed_at`;
  `at` is accepted for the existing interface but is not trusted over the
  owner's clock.
* `claim(ref, *, from_ref, context_ref)` calls `agent.binding.claim` and
  refuses an answer whose writer claim is not the returned current writer.
* Neither method accepts a writer, owner, token or epoch from this client.

### 2.2 `agent.binding.close` and `agent.binding.claim`

Both new mutation routes use `_trusted_actor_for_mutation`, which requires the
**strong** `actor.authorizes` verdict (FRESH + provenance FACT + provider
VERIFIED), not merely `validate` + `freshness == FRESH`. A fresh
`BLOCKED`/`UNKNOWN` context built by `actor.build` passes the weaker pair but
is refused here.

`agent.binding.close` keeps the historical plain close. With a
`successor_ref` it becomes a protected transfer:

1. a trusted, fresh, FACT+VERIFIED `context_ref` is required;
2. the source and target bindings must be `OPEN` and bound to the **same Item**;
3. the source must be the current writer (binding derivation);
4. either the target is already the current writer and its claim is backed by
   the live Work lease, or the source is current and the live lease has moved
   to a different owner/token. In the latter case the daemon mints the target's
   claim from that lease and closes both in one store mutation;
5. the returned current writer must be the target's claim.

`agent.binding.claim` is the pre-close half for an already-bound target: it
mints the target's writer claim from the **live Work lease** before the target's
resume/send is admitted. If there is no live lease, or the lease still names
the source, the answer is `NOT_CURRENT_WRITER` and nothing is written. The
claim's owner/token are the lease's; it is never a caller-supplied writer.

### 2.3 Store

`AgentControlStore.close_binding(..., successor=..., actor=...)` validates the
successor (same Item, open, no existing claim, shaped writer) and writes the
source closure and the successor claim under one `_mutation`. `claim_writer`
attaches a claim only to an open binding with none. No new store, file or
ledger is created.

### 2.4 Router

`handoff()` now:

* pre-claims an already-bound target before the target route (a resume target is
  otherwise fenced by admission), and claims a create target after the route;
* captures the revocation result and returns `ok=False` with
  `NOT_CURRENT_WRITER` (or the transport's reason) when the source was not
  closed or the target's claim is not the current writer;
* adds `write_authority_transferred` to the completed steps and to
  `HANDOFF_STEPS` / `fleet/agent-control-contract.yaml#handoff.steps`;
* includes the revocation evidence in the success and failure details.

### 2.5 Admission clock and provider exceptions

* `runtime/coordination.py::writer_axis` and the Work-lease branch of
  `admit_work` compare `expires_at` and `now` as **aware instants**
  (`_instant`). A malformed or naive timestamp fails closed (`EXPIRED` /
  `NOT_CURRENT_WRITER`); it is never CURRENT. The Q1 example
  `2023-11-15T05:13:20+08:00` (21:13:20Z) against `2023-11-14T22:13:20Z` is the
  regression, and a real-daemon test uses an expired `+08:00` lease row and
  asserts the provider's `send` was never called.
* `handoff()` wraps `router.route(target_request)`. A raising provider or
  transport is a typed answer, not an escape:
  * after a pre-claim: `BLOCKED_ATOMICITY`, `source_preserved=false`,
    `scope_stalled=true`, with the exception type/detail and the target claim;
  * with no claim: the transport's reason if it is in the reason vocabulary,
    otherwise `PROVIDER_ERROR`, with `source_preserved=true`.
  No automatic rollback is attempted.

## 3. Refusal matrix

| Case | Result |
|---|---|
| fresh context with `provider_status != VERIFIED` or `provenance != FACT` | `UNTRUSTED_ACTOR`, no mutation |
| malformed or naive writer/lease expiry | `EXPIRED` / `NOT_CURRENT_WRITER` (never CURRENT) |
| target claim minted, target route/provider refuses | `BLOCKED_ATOMICITY`, `source_preserved=false`, `scope_stalled=true` |
| target claim minted, provider/transport route raises | `BLOCKED_ATOMICITY`, `source_preserved=false`, `scope_stalled=true` |
| target route raises before any claim | transport reason or `PROVIDER_ERROR`, source untouched |
| target claim minted, source close transport fails | `BLOCKED_ATOMICITY`, `source_preserved=false`, `scope_stalled=true` |
| target claim written but not confirmed current | `BLOCKED_ATOMICITY`, `scope_stalled=true` |
| source has no writer claim | `NOT_CURRENT_WRITER`, source preserved |
| target is not bound to the source's Item | `NOT_CURRENT_WRITER`, source preserved |
| target already has an unrelated claim | `INVALID_CONTEXT` / `NOT_CURRENT_WRITER` |
| no live Work lease | `NOT_CURRENT_WRITER`, source preserved |
| lease still names the source (no transfer) | `NOT_CURRENT_WRITER`, source preserved |
| target claim not backed by the live lease | `NOT_CURRENT_WRITER`, source preserved |
| daemon unreachable | `NODE_RUNTIME_UNAVAILABLE`, source preserved |
| close returns not-CLOSED | non-transfer, fail closed |
| close returns CLOSED but target not current | non-transfer, `source_preserved=false` |
| source already closed and target current | idempotent success |
| cross-store target binding | not supported; source preserved, fail closed |

The source stays readable after a transfer: `inspect`, `result`, `interrupt`
and `stop` are not gated; only new sends/resumes are refused with
`THREAD_CLOSED_FOR_NEW_WORK`.

## 4. End-to-end evidence

`tests/test_handoff_daemon_bindings.py` (16 tests) uses a real `Daemon`, a real
temp `serveros.db` with a live `leases` row, the real `DaemonBindings`, and the
real router/loopback:

* A writes before the handoff; B without a claim is fenced;
* the lease moves to B, the handoff transfers the claim, A is `CLOSED` and its
  send is `THREAD_CLOSED_FOR_NEW_WORK`, B is admitted;
* B is refused after the lease is deleted (`NOT_CURRENT_WRITER`);
* no lease transfer leaves the source `OPEN` and the handoff fail-closed;
* a rival cannot claim the scope;
* an unreachable daemon fails closed;
* a stale source cannot transfer authority;
* a restart keeps the transferred claim;
* an expired source claim is recovered by the lease-backed transfer, and the
  evidence reports `source_claim_expired` (compared as an aware instant, not
  ISO text; the `+08:00` case is the regression);
* **new**: a fresh `BLOCKED`/`UNKNOWN` context cannot `claim` or
  `close(successor_ref)` (`UNTRUSTED_ACTOR`; no writer mutation), while the
  VERIFIED+FACT context still can;
* **new**: a target provider refusal after the pre-claim and a close transport
  failure after the pre-claim both return `BLOCKED_ATOMICITY` with
  `source_preserved=false` and `scope_stalled=true`; the source is open but no
  longer the current writer, and its send is `NOT_CURRENT_WRITER`;
* **new**: an expired `+08:00` lease is refused at real `session.send`
  admission (`NOT_CURRENT_WRITER`), and the provider's `send` is never called;
* **new**: a provider `resume` that raises after the pre-claim and a source
  `close` that raises a plain `RuntimeError` after the pre-claim both return
  `BLOCKED_ATOMICITY` with `source_preserved=false` and `scope_stalled=true`;
  the target is the only current writer and no `send` reaches the service.

RED at #318 `2640bce`: the kernel offset/naive expiry tests failed, the
real-daemon offset-lease admission test was ADMITTED, and the provider
exception escaped the handoff (`5 failed, 57 passed`; the close-`RuntimeError`
case already returned the typed outcome). GREEN at this branch.

Focused suites, all exit 0:

```text
16 passed               tests/test_handoff_daemon_bindings.py
60 passed, 2 subtests   test_v1_write_fence.py + test_handoff_daemon_bindings.py
93 passed, 7 subtests   test_agent_control_router.py
83 passed, 164 subtests test_agent_control_store.py + test_agent_mcp.py
53 passed               test_coordination.py
33 passed               test_daemon_equivalence.py + test_control_store_path.py
13 passed               daemon method-coverage selection
```

## 5. Not claimed

* **No automatic compensation for a stalled pre-claim.** If the target's claim
  is minted and the target route or the source close then fails, the handoff
  returns `BLOCKED_ATOMICITY` with `source_preserved=false` and
  `scope_stalled=true`. The target's claim is not rolled back: the live Work
  lease (the authority) names the target, so deleting the binding claim would
  not restore the source. Repair is an owner action (re-lease / close / claim).
  The handoff never calls the source preserved when it is not the current
  writer.
* The strong `actor.authorizes` verdict is required at both new mutation routes;
  outer middleware is not relied on.
* Cross-device write-authority transfer is not implemented: the source store
  cannot atomically mint the target's claim in another store. That path fails
  closed and keeps the source's authority.
* The Work Engine lease producer remains `BLOCKED_TRUST_SOURCE`; this slice
  reads the live lease and never mints a grant or lease row.
* No real provider, session, model request, credential or paid send.
* No merge, deployment, service restart or permission change.
