# Fleet Next Agent Monitor — Legacy source adapter handoff

Status: **source adapter implemented and unit-tested offline. Integration to a
concrete Agent Monitor entry is `BLOCKED_UPSTREAM`.**

Owner decision (#478): Legacy Fleet 0.1.0 remains the live control plane for
ChatGPT; Fleet Next develops in parallel. This adapter **reads** Legacy facts
only. It never creates, sends, deletes, stops or restarts anything.

## 1. Where it lives

```
integrations/fleet-next-monitor-source-adapter/
    fleet_next_monitor_source_adapter/{contract,adapter,serializer,cli}.py
tests/test_fleet_next_monitor_source_adapter.py
docs/integration/FLEET-NEXT-SOURCE-ADAPTER.md   (this file)
```

No file outside these paths is modified. The Dell-owned
`integrations/fleet-next-agent-monitor/**` and the activation wrapper are not
touched.

## 2. The handoff contract (for the Agent Monitor side)

The monitor injects a client; it does not import Legacy, and it does not accept a
module spec from data.

```python
class LegacyReadOnlyClient(Protocol):
    fleet_instance_id: str
    route_id: str
    boot_id: str | None
    def read_session(*, device_ref, session_ref) -> Mapping: ...
    def read_run(*, device_ref, session_ref, run_id=None, delivery_id=None) -> Mapping: ...
    def read_ci(*, source_sha) -> Mapping: ...
```

The client may raise:

| exception | adapter answer |
|---|---|
| `RunNotFound(reason=..., retryable=...)` | `UNKNOWN` with the native reason/retryable |
| `RouteOffline` | `OFFLINE` |
| `RemoteRefused` | `UNKNOWN` / `REMOTE_REFUSED` (no message is carried) |
| any other exception | `UNKNOWN` / `REMOTE_<ClassName>` (the message is never read) |

The adapter is constructed as:

```python
LegacyReadOnlySourceAdapter(client, grant, max_readings=500)
```

`grant` is a `CallerGrant` naming `devices`, `projects`, `sessions`, `views`
(`session`/`run`/`ci`) explicitly. `*` is refused; an empty `sessions` set means
no session.

## 3. Observation schema

```json
{
  "schema": "serveros/fleet-next-monitor-observation.v1",
  "view": "run",
  "status": "UNKNOWN",
  "reason": "NO_NATIVE_ACTIVITY",
  "retryable": null,
  "identity": {
    "instanceId": "...", "routeId": "...", "deviceRef": "...",
    "sessionRef": "...", "runId": "...", "deliveryId": null,
    "bootId": "...", "provider": "...", "sourceSHA": "...",
    "observedAt": "..."
  },
  "native": { "instanceId": "...", "runId": "...", "deliveryId": "...", "...": "..." },
  "readings": [],
  "readingsUsed": 1
}
```

`identity` is what was asked; `native` is what the answer carried (scalars only).
They are never joined. A mismatch is reported, not resolved.

## 4. Status rules the monitor must preserve

| native fact | adapter status |
|---|---|
| `completed` + transcript or verified artifact bound to the expected `sourceSHA` | `TERMINAL_VERIFIED` |
| `completed` with neither | `UNKNOWN` / `NO_NATIVE_ACTIVITY` |
| `RUN_NOT_FOUND` | `UNKNOWN` (never `DONE`) |
| route offline | `OFFLINE` (never `FAILED`) |
| `bootId` differs from the client's | `UNKNOWN` / `BOOT_MISMATCH` |
| answer names another `instanceId` | `UNKNOWN` / `INSTANCE_MISMATCH` |
| CI draft | `UNKNOWN` / `DRAFT_CI` |
| CI `success` but unverified | `UNKNOWN` / `UNVERIFIED_CI` |
| CI verified, non-draft, exact SHA | `TERMINAL_VERIFIED` |

There is no `DONE` status in the vocabulary.

## 5. Why the integration is BLOCKED_UPSTREAM

The Agent Monitor's runnable entry has no published SHA on this base:

* `integrations/fleet-next-agent-monitor/` does not exist in this tree;
* the activation wrapper on `feat/fleet-next-agent-monitor-activation-20261028`
  injects `--monitor-entry module:callable` and states that the upstream one-shot
  CLI does not exist yet (`BLOCKED_UPSTREAM_ENTRY`, exit 3, zero ticks);
* #481's Batch CLI is a scheduler entry for the milestone-batch lane, not the
  Agent Monitor's monitor entry.

So this PR defines the adapter and its contract, proves them offline, and stops
there. It does **not** claim a connected Agent Monitor, and it does not invent an
interface the other lane has not published. When that entry lands, the handoff is
one constructor call plus the client above.

## 6. Acceptance layers

| layer | status |
|---|---|
| **CODE / UNIT / CI** | **done** — RED (package absent → `ModuleNotFoundError`) → GREEN `35 passed, 11 subtests` |
| **REAL_DEVICE / CLIENT_E2E** | **not done** — no Legacy service, no CloudCLI, no credential |
| **PRODUCTION** | **not done** — no deploy, no scheduler, no service, no main merge |
| **AGENT MONITOR INTEGRATION** | **BLOCKED_UPSTREAM** (see §5) |

## 7. Safety properties asserted by tests

* no write verb exists on the adapter or the client protocol;
* a denied grant costs zero client calls (authorization before the call);
* two instances with one `runId` do not cross, and the foreign native identifiers
  are not forwarded;
* another caller's session is refused;
* remote exception messages, secret-shaped payload fields and `RemoteRefused`
  text never reach an observation;
* a claimed terminal without evidence, a `RUN_NOT_FOUND`, an offline route and a
  boot mismatch are never terminal;
* no model list can be produced (containers are not forwarded);
* 500 reads are the cap; the 501st is `STALE` without a client call;
* the serializer refuses import-shaped keys and unknown fields; the package
  imports no `socket`/`urllib`/`requests`/`http`/`subprocess`/`importlib` and
  calls no `eval`/`exec`/`__import__`;
* the CLI reads one bounded document and exits.

Related: #478, #424, #417, #394, #481, #434, #425.
