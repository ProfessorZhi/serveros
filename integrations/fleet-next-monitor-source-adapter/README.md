# Fleet Next monitor — read-only Legacy source adapter

Owner decision (#478): **Legacy Fleet 0.1.0 keeps the live control plane; Fleet
Next develops in parallel.** This package therefore only READS facts Legacy
already owns. It has no create, send, delete, stop, restart, interrupt or cancel
path, and it cannot acquire one by configuration.

```
caller grant + injected read-only client
        -> LegacyReadOnlySourceAdapter
        -> one Observation (JSON)
```

## What is here

| file | what it does |
|---|---|
| `contract.py` | identities, statuses, grant, observation. No `DONE` status exists. |
| `adapter.py` | authorization → one bounded read → classification; `FixtureLegacyClient` for offline use |
| `serializer.py` | closed JSON input; refuses import-shaped keys; no `eval`/`exec`/`__import__` |
| `cli.py` | one offline tick: JSON in, JSON out, exit |

## Identity discipline

Eight native facts plus time are preserved **separately**:

```
instanceId · routeId · deviceRef · sessionRef · runId · deliveryId · bootId
provider · sourceSHA · observedAt
```

The observation carries two blocks on purpose:

* `identity` — what the caller asked for (plus the instance/route the client
  declares);
* `native` — the identity fields the answer itself carried, scalar-only.

Keeping them apart is what lets a monitor compare "asked" with "answered". The
same `runId` under two `instanceId`s is two different runs; an answer naming the
wrong instance, device, session, run, delivery or boot is reported as `UNKNOWN`
and its native identifiers are **not** copied into the request's block.

## Status vocabulary

```
ACCEPTED · QUEUED · STARTED · RUNNING · TERMINAL_VERIFIED
FAILED · STALE · UNKNOWN · OFFLINE · DENIED
```

There is no `DONE`.

* native `completed` **without** a non-empty transcript and **without** a
  verified artifact bound to the expected `sourceSHA` → `UNKNOWN` /
  `NO_NATIVE_ACTIVITY`;
* `RUN_NOT_FOUND` → `UNKNOWN` (its native `reason` and `retryable` are carried);
* route offline → `OFFLINE`, never `FAILED`;
* boot mismatch → `UNKNOWN` / `BOOT_MISMATCH`;
* a CI run that is a draft, or `success` but unverified → `UNKNOWN`; only an
  exact-`sourceSHA`, non-draft, verified success is `TERMINAL_VERIFIED`.

## Grant

`CallerGrant` names devices, projects, sessions and views explicitly. An empty
`sessions` set means **no session**, never every session. `*` is refused at
construction. Authorization happens before any client call; a denied read
performs zero provider reads (asserted).

## Bounded

`max_readings` (default 500) is charged per read. The 501st read returns
`STALE` / `READINGS_CAPPED` without touching the client. Input documents are
capped at 1,000,000 bytes.

## CLI

```bash
PYTHONPATH=integrations/fleet-next-monitor-source-adapter \
  python -m fleet_next_monitor_source_adapter.cli --input tick.json
```

`tick.json`:

```json
{
  "request": {"view": "run", "device_ref": "device:x",
              "session_ref": "device:x|claude|s", "run_id": "r1"},
  "grant":   {"principal": "monitor", "devices": ["device:x"],
              "projects": [], "sessions": ["device:x|claude|s"], "views": ["run"]},
  "fixture": {"run": {"instanceId": "instance-a", "status": "completed"}},
  "instance": {"instanceId": "instance-a", "routeId": "route-a", "bootId": "boot-1"}
}
```

Exit codes: `0` observation produced · `2` refused (bad input / denied / import
shape) · `3` blocked upstream (input unreadable).

The input never names a Python module. The only dynamic construction is the
fixture client built from the JSON itself; a real deployment injects a real
read-only client programmatically (see the handoff doc).

## What this is not

* Not the Agent Monitor. It is the **source adapter** the monitor would call.
* Not deployed, not scheduled, not a service.
* It does not touch Legacy Fleet's service, socket, route, database, state or
  credential. It reads no credential at all.
* `BLOCKED_UPSTREAM`: the Agent Monitor's one-shot monitor entry has no published
  SHA yet (see `#478` and the activation doc). The integration point is defined
  here; the consumer is not.

## Evidence

* RED on the base commit: package absent → `ModuleNotFoundError`, collection
  error.
* GREEN: `35 passed, 11 subtests passed` (`tests/test_fleet_next_monitor_source_adapter.py`).
* CLI smoke: a synthetic completed-without-transcript fixture prints
  `"status": "UNKNOWN", "reason": "NO_NATIVE_ACTIVITY"` and exits 0.

**CODE/UNIT/CI only.** No device, no Legacy service, no credential, no model, no
Feishu, no deployment.
