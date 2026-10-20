# V1 effect reconciliation — the driver, and the provider capability it needs

Slice owner: S2 durable-effects line (`#245`, `#237` T4/T5/T9/T12).
Base: `feat/v1-durable-dispatch-attempts-20261017` (`#252`, Draft).

## What this directory is

`effect_reconciliation.py` is the caller that was missing between two existing
owners:

| Concern | Owner (unchanged) |
| --- | --- |
| What a retry may do; what an owner observation authorizes | `runtime/dispatch_attempts.py` |
| The durable attempt record | `runtime/agent_control_store.py#dispatch_attempts` |
| Routing and dispatch | `fleet/agent_control_router.py` |
| **Ask the owner after a timeout/reconnect** | **this directory** |

The driver holds no state, opens no file, and has no `send` method. Every
durable read/write goes through the existing Agent Control store. A second
attempt ledger would be the `second-source-of-truth` / `new-lease-system` shape
`integration/components.yaml` refuses by name.

## What the driver enforces

- It only offers `EFFECT_UNKNOWN` attempts to an observation source. A
  `SETTLED`, `ACCEPTED` or `SUPERSEDED` attempt is never overwritten by a late
  reply.
- A source must declare `CLIENT_TURN_ID`, the id we **still hold** after the
  send response was lost. `RESPONSE_ID_ONLY` and `NO_SOURCE` are
  `BLOCKED_PROVIDER_SOURCE`: the source is not called, and the effect stays
  `EFFECT_UNKNOWN`.
- Lookup misses (`TURN_NOT_FOUND`, `ABSENT`, `MISSING`, `NOT_FOUND`,
  `LOOKUP_MISS`) are normalised to `UNKNOWN`. A miss is not proof of absence,
  so it cannot move the attempt to `CALL_NOT_SENT` (which would make the next
  retry `PROCEED`).
- `NOT_APPLIED` still requires provenance (`source`) and an intent match
  (digest or provider locator) at the kernel; this driver does not relax it.
- A Work-scoped attempt needs the recorded principal **and** the current writer
  (owner/epoch/token). A coordinator attempt with no `work_item` has no write
  scope to gate.
- A source exception is `SOURCE_UNAVAILABLE`; `None` is `NO_OBSERVATION`.
  Neither is an outcome.

## Provider capability matrix — measured from the current adapters

| Adapter | Read path | Key it needs | Capability after a lost send response |
| --- | --- | --- | --- |
| BotMux (`mcp/botmux_provider.py`) | `GET /api/sessions/{id}/trigger-result?triggerId=...` | `trigger_id` returned by the send response | `RESPONSE_ID_ONLY` → `BLOCKED_PROVIDER_SOURCE` |
| Codex app-server (`mcp/codex_app_server_provider.py`) | `thread/read` turn list; `result()` filters by `turn_id` | `turn.id` returned by `turn/start` | `RESPONSE_ID_ONLY` → `BLOCKED_PROVIDER_SOURCE` |

Both adapters forward a client key (`turnIdempotencyKey` / `clientUserMessageId`),
but neither has a read path keyed by it today. `clientUserMessageId` **is**
carried on the turn object in the app-server protocol and in the test
double, so a Codex-only source is plausible; it is not implemented here because
`mcp/` is R1's write scope, not this line's.

`BLOCKED_PROVIDER_SOURCE` is therefore the production answer for both adapters
until R1 exposes a read keyed by the client turn id (or a provider-native
durable journal). No test double in this slice is offered as a production
source.

## Wiring proposal — S1 files are NOT edited by this slice

These are the exact changes that would put the driver on the deployed path.
They touch S1-owned files, so they are a proposal for the S1 owner to apply or
schedule, not a concurrent edit.

### 1. `runtime/daemon.py` — a reconcile method (read/modify S1 file)

Add `agent.dispatch.reconcile` beside `agent.dispatch.resolve` /
`agent.dispatch.record`:

```text
input:  context_ref (required), key, current_writer (owner/epoch/token)
resolve the actor from the trusted context_ref, as record does
build reconciler principal + writer from that context
ask the registered observation source (below)
call EffectReconciler(...).reconcile(key, principal=..., writer=...,
                                     current_writer=...)
return the driver result; never dispatch
```

Refusal rules to preserve:

- no resolvable fresh context → `UNTRUSTED_ACTOR`, no store write;
- source capability ≠ `CLIENT_TURN_ID` → `BLOCKED_PROVIDER_SOURCE`, no settle;
- any kernel refusal (`REFUSE_PRINCIPAL`, `REFUSE_STALE_WRITER`,
  `REFUSE_UNKNOWN`) → no store write.

### 2. `runtime/daemon.py` — reconnect sweep (read/modify S1 file)

At startup and after a transport reconnection, call
`EffectReconciler.reconcile_unknown(...)` for the attempts visible in the
existing store. It never re-dispatches: an attempt whose owner cannot answer
stays `EFFECT_UNKNOWN`. Bound the batch (e.g. the first N keys in sorted order)
so a reconnect cannot become an unbounded provider fan-out.

### 3. `mcp/*_provider.py` — a `CLIENT_TURN_ID` read (R1 ownership)

- BotMux: a read accepting `turnIdempotencyKey`, or a provider journal keyed by
  it. If the endpoint cannot accept that key, the capability stays
  `RESPONSE_ID_ONLY` and reconciliation stays blocked.
- Codex: filter `thread/read` turns by `clientUserMessageId` and expose the
  match with `source="codex:thread/read"`. A miss (`no turn with that client
  id`) must be returned as `UNKNOWN`, never `NOT_APPLIED`.

### 4. Observation source registration

The daemon should hold a source object with `capability` and
`observe(attempt, *, key)`. Until an adapter provides `CLIENT_TURN_ID`, the
registered source must declare `RESPONSE_ID_ONLY` — not a test double, and not
an empty `observe` that returns `UNKNOWN` while claiming the stronger
capability.

## Evidence (LOCAL, bounded)

Run from the Slice worktree with the project venv, explicit files, `timeout`
and a memory cap; no real provider, no paid model send, no daemon restart.

```text
RED   23 tests failed: integrations/v1-effect-reconciliation/
      effect_reconciliation.py did not exist (the driver had no caller).

GREEN 23 passed  tests/test_effect_reconciliation.py
      89 passed  tests/test_agent_control_store.py
                 tests/test_dispatch_attempts.py
                 tests/test_effect_reconciliation.py
      93 passed, 7 subtests passed  tests/test_agent_control_router.py
```

Covered by name: response-id-only source blocked, lookup miss stays unknown,
`NOT_APPLIED` without provenance refused, restart cannot turn unknown into a
retry, late observation cannot reopen a settled attempt, same key + modified
payload conflict, source timeout / `UNKNOWN` leaves the effect unknown,
authoritative `APPLIED` settles and replays, authoritative `NOT_APPLIED` frees
the scope, reconnect sweep touches only `EFFECT_UNKNOWN`, cross-principal and
stale-writer refusals, no second state artifact, no send path.

## Honest level

- `CODE` / `LOCAL`: this driver and its bounded tests.
- `REAL_TEMP`, `REAL_DEVICE`, `CLIENT_E2E`: **not run**.
- `durable_effects`: still **partial**. The observation source is
  `BLOCKED_PROVIDER_SOURCE`; an unknown outcome stays `EFFECT_UNKNOWN` and
  exactly-once is never claimed. `#237` T1–T12 are not closed by this slice.
