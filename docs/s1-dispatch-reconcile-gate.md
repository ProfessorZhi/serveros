# S1 · Native `agent.dispatch.reconcile` gate (2026-10-20)

**Status: source + hermetic local tests. No production source is wired.**

| Fact | State |
|---|---|
| Native route | `runtime/daemon.py#_r_agent_dispatch_reconcile` |
| Ledger | the existing `runtime/agent_control_store.py#dispatch_attempts` (no new store) |
| Driver | `integrations/v1-effect-reconciliation/effect_reconciliation.py` (#299), loaded by path |
| Observation source | daemon-held; production passes none |
| Grant verifier | daemon-held; production passes none |
| Production answer | `BLOCKED_PROVIDER_SOURCE` / `BLOCKED_GRANT_UNKNOWN` |
| Evidence level | `CODE / LOCAL`; `REAL_TEMP`, `REAL_DEVICE`, `CLIENT_E2E`: `NOT_RUN` |

## 1. What the route is

`agent.dispatch.reconcile` is the missing caller between two existing owners:

| Concern | Owner |
|---|---|
| What an observation authorizes | `runtime/dispatch_attempts.py` (#252) |
| The durable attempt | `runtime/agent_control_store.py#dispatch_attempts` (#252) |
| Asking the owner after a timeout / reconnect | `integrations/v1-effect-reconciliation/` (#299) |
| The native entry and its authority resolution | **this route** |

The request carries only:

```text
context_ref   the trusted context the caller presents
key           the stable client-side dispatch key recorded before the send
```

Everything else is resolved:

* **principal** — from the resolved, FRESH context (`actor.resolve` +
  `actor.validate` + `actor.freshness`), never from the payload;
* **writer** — the claim on the attempt's own `session_ref` binding, never a
  request field;
* **current writer** — `coordination.current_writer(store.bindings(), item)`,
  the same derivation the admission gate reads;
* **observation source** and **grant verifier** — objects held by the `Daemon`
  from construction, never registered or named by a request.

A request that carries `principal`, `writer`, `current_writer`, `source`,
`capability`, `observation`, `reconciler`, `grant_ref`, `attempt`, `phase`,
`result` or `success` is refused with `INVALID_CONTEXT`. Silently ignoring the
field would let a caller believe it supplied an authority.

## 2. Why production refuses (`BLOCKED_PROVIDER_SOURCE`)

After a send response is lost, this node still holds the **client** turn id it
sent under. It does not hold the provider-native id that arrived in the lost
response. Reconciliation is meaningful only if the provider can be asked by the
id we still hold:

| Adapter | Read path | Key it needs | Capability after a lost reply |
|---|---|---|---|
| BotMux (`mcp/botmux_provider.py`) | `GET /api/sessions/{id}/trigger-result?triggerId=…` | `trigger_id` from the send response | `RESPONSE_ID_ONLY` |
| Codex app-server (`mcp/codex_app_server_provider.py`) | `thread/read` turn list; `result()` filters by `turn_id` | `turn.id` from `turn/start` | `RESPONSE_ID_ONLY` |

R1 #308 measures the same boundary from the authorization side: BotMux
publishes no instance-level `actions()` table, and the Codex runtime is not
declared on the Tokyo registry, so the routed reads are refused before support.
Neither adapter currently exposes a read keyed by the client turn id.

`dispatch_observation_source=None` (the production construction) therefore
yields `BLOCKED_PROVIDER_SOURCE`; the source is not called and the attempt stays
`EFFECT_UNKNOWN`. A lookup miss (`TURN_NOT_FOUND`, `ABSENT`, `MISSING`, …) is
normalised to `UNKNOWN` by the driver and settles nothing. Exactly-once is never
claimed.

The future wiring is an adapter that can answer by the client id, registered at
daemon construction. No test double is offered as a production source.

## 3. Grant authority is a seam, not a second verifier

An attempt may carry `grant_ref` (a reference, not a copy of the grant). Before
the source is asked:

* no `grant_ref` → the writer gate above is the authority;
* `grant_ref` present and no verifier bound → `BLOCKED_GRANT_UNKNOWN`;
* verifier raises or does not return `{"verified": True}` → its reason (for
  example `GRANT_REVOKED`) and no store write.

The production verifier is the #272 `agent.lease.authority.verify` adapter,
which re-reads revocation, window and the monotonic grant epoch against a
trusted context and the kernel peer. It is **not** bound here; this route does
not duplicate that read and does not accept a caller-supplied grant.

## 4. Refusal matrix

| Case | Result | Source called | Ledger write |
|---|---|---|---|
| no observation source | `BLOCKED_PROVIDER_SOURCE` | no | no |
| `RESPONSE_ID_ONLY` / unknown capability | `BLOCKED_PROVIDER_SOURCE` | no | no |
| missing / unresolved / stale context | `UNTRUSTED_ACTOR` | no | no |
| no attempt under the key | `NO_ATTEMPT` | no | no |
| different principal | `REFUSE_PRINCIPAL` | no | no |
| attempt has no current writer / cross-Item binding | `REFUSE_UNKNOWN` | yes | no |
| stale writer claim | `REFUSE_STALE_WRITER` | yes | no |
| `grant_ref` without a verifier | `BLOCKED_GRANT_UNKNOWN` | no | no |
| stale / revoked grant | verifier's reason | no | no |
| source raises | `SOURCE_UNAVAILABLE` | yes | no |
| observation says `UNKNOWN` | `REFUSE_UNKNOWN` | yes | no |
| attempt is not `EFFECT_UNKNOWN` | `NOT_UNKNOWN` | no | no |
| authoritative `APPLIED` | `SETTLE_APPLIED` | yes | one phase change |
| authoritative `NOT_APPLIED` | `SETTLE_NOT_APPLIED` | yes | one phase change |
| request carries authority fields | `INVALID_CONTEXT` | no | no |

`dispatching: False` is present on every answer. There is no send method on the
route or the driver.

## 5. Verification

```text
python -m pytest tests/test_dispatch_reconcile_gate.py \
                 tests/test_effect_reconciliation.py \
                 tests/test_dispatch_attempts.py \
                 tests/test_agent_control_store.py \
                 tests/test_agent_control_router.py \
                 tests/test_coordination.py tests/test_v1_write_fence.py \
                 tests/test_daemon.py tests/test_daemon_equivalence.py \
                 tests/test_control_store_path.py -q
```

The new file has 19 tests (15 parameterised subtests) and covers: no source,
`RESPONSE_ID_ONLY`, unknown capability, missing/unresolved/stale context,
cross-principal, cross-Item, stale writer, missing/stale grant, source failure,
`UNKNOWN`, terminal attempt, same-ledger settle, coordinator scope,
lost-reply → restart → retry, forbidden authority fields, and same key with a
different payload through `agent.dispatch.record`.

Measured, all exit 0: `19 passed` (new gate file); `88 passed` (effect
reconciliation + dispatch attempts + gate); `20 passed` (Agent Control store);
`93 passed, 7 subtests` (Agent Control router); `95 passed` (coordination +
write fence); `17 passed` (daemon method coverage); `33 passed` (daemon
equivalence + control-store path).

All of it is hermetic: temp daemon state, a temp socket path that is never
bound, no provider request, no model call, no paid send.

## 6. Not claimed

* No production observation source is wired; the production answer stays
  `BLOCKED_PROVIDER_SOURCE`.
* No automatic reconnect sweep is installed. A sweep would be a bounded,
  owner-scheduled follow-up; doing it at startup without a `CLIENT_TURN_ID`
  source would only fan out refusals.
* The grant verifier is a seam; #272's read is not bound here.
* `durable_effects` remains **partial**. `#237` T4/T5/T9/T12 are not closed by
  this slice.
* No merge, deployment, service restart, permission change or real provider
  call was performed.
