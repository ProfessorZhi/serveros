# Fleet Next Agent Monitor — the monitor body

Issue **#424** asks for an observer that keeps watching after the ChatGPT turn is
gone. Issue **#478** requires it to run beside Legacy Fleet without touching it.
This package is the **monitor itself** — not the activation wrapper
(`integrations/fleet-next-agent-monitor-activation/`, PR #482), not the Legacy
source adapter (PR #490), and not the activation/CLI wiring (PR #488).

It is the thing those three were waiting for: PR #485 and PR #491 both recorded
`BLOCKED_UPSTREAM_SOURCE` naming exactly this path, because
`integrations/fleet-next-agent-monitor/` did not exist on `main` or on any PR
head.

```
integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/
  contract.py   vocabulary (no DONE), identity, watch keys, exit codes
  state.py      durable cursor, boot epoch, flock single writer, heartbeat
  sources.py    read-only Legacy sampling; authorization before read
  core.py       MonitorCore.run_once() — sample, classify, dedupe, hand over
  notifier.py   the #423 notifier seam (injected) and its closed envelopes
  offline.py    an offline facts client: synthetic JSON, no network, no token
  runtime.py    config, one-shot CLI, and the zero-argument `tick()`
  __main__.py   `python -m fleet_next_agent_monitor`
```

## The one entry #488 needs

PR #482/#488's wrapper resolves `--monitor-entry module:callable` and calls it
with **no arguments**. This package publishes exactly that:

```python
from fleet_next_agent_monitor.runtime import tick   # -> dict
```

`tick()` reads its configuration from the environment (`…_CONFIG` and `…_STATE`
are required; `…_NOTIFIER`, `…_CLIENT`, `…_OFFLINE_FACTS` are optional) and
returns a mapping that carries `backlog` and `unknown_receipts` at top level —
the two names #488 reads off a tick. A missing configuration is a returned
`BLOCKED_INPUT` document, never an exception, so the wrapper reports a refusal
instead of `TICK_FAILED`.

The one-shot CLI is the same code as a process:

```
python -m fleet_next_agent_monitor --config cfg.json --state /abs/state.json \
    --offline-facts facts.json --notifier-entry module:callable
# exit 0 SETTLED/TICKED · 2 REFUSED · 3 BLOCKED_INPUT · 4 UNCONFIRMED
```

## What it refuses to say

`contract.STATUSES` contains no `DONE`, `SUCCESS`, `VERIFIED` or `COMPLETED`.
The only positive terminal word is `TERMINAL_VERIFIED`, and it is produced *only*
when the far end carried native activity:

| far end says | monitor says |
| --- | --- |
| `completed`, non-empty assistant message | `TERMINAL_VERIFIED` |
| `completed`, no message, no verified artifact bound to the expected SHA | `UNKNOWN` / `NO_NATIVE_ACTIVITY` |
| `completed` with a device note saying there was no readable message | `UNKNOWN` / `NO_NATIVE_ACTIVITY` |
| run not issued | `UNKNOWN` / `NEVER_ISSUED` |
| run expired | `UNKNOWN` / `EXPIRED` |
| native `bootId` ≠ the expected boot | `UNKNOWN` / `BOOT_MISMATCH` |
| route down, or the network dropped mid-read | `OFFLINE` / `ROUTE_OFFLINE` |
| CI green, draft pipeline | `UNKNOWN` / `DRAFT_CI` |
| CI green on a *different* commit | `UNKNOWN` / `SHA_MISMATCH` |
| CI completed with an unreadable conclusion | `UNKNOWN` / `UNVERIFIED_CI` |

Only `TERMINAL_VERIFIED` and `FAILED` are ever handed to the notifier
(`contract.DISPATCHABLE`). A watch that is still `UNKNOWN` produces a
de-bounced warning in the tick document and **nothing** outbound.

## The exit code is part of the vocabulary

A supervisor usually reads the number, not the document. So the number says the
same three things the state word does, and no two different facts share one:

| tick | exit | what it means |
| --- | --- | --- |
| `SETTLED` | 0 | every watch has a terminal verdict and every delivery has a receipt |
| `TICKED` | 0 | every outstanding watch is **work in flight** (`ACCEPTED`/`QUEUED`/`STARTED`/`RUNNING`) and nothing abnormal was seen |
| `UNCONFIRMED` | 4 | a hand-over has no receipt, the cursor is stale, **or** a watch has no verdict and no work in flight (`UNKNOWN`/`OFFLINE`/`STALE`/`DENIED`) |
| `BLOCKED_INPUT` | 3 | there is no ledger, no authorized subject, or no run identity — nothing was sent |
| `REFUSED` | 2 | this process may not write (a second writer) or the configuration is unusable |

`LIVE_STATUSES` and the `UNOBSERVED` warning name the split: a run that is
**running** is not a failure — it is reported as `TICKED`, never as `FAILED` —
while "the monitor could not decide" is never handed over as `0`. #495's Wave 16
§4.2 is the measurement of the old behaviour: an expired or unobservable watch
reached the supervisor as `TICKED` exit 0, which is the number that means
"nothing to do here".

## A typed subject is a claim, not an authorization

A CI watch may carry its own typed `device_ref` (#497). Both dimensions of the
grant are checked, and both are checked **before anything is read**:

1.  `sources.observe_ci` authorizes `project` and, when the watch declares a
    device, that `device_ref` against `grant.devices`. An ungranted declared
    device is `DENIED` / `BLOCKED_CI_SUBJECT` with **zero** provider calls, and
    only an authorized device is carried into the observation's identity.
2.  `notifier.delivery_subject` refuses to hand a declared CI subject over
    unless that identity names **the same device**. `device:<anything>` being
    well-typed is not evidence that anybody granted it — Wave 16 §4.1 measured a
    granted project plus `device_ref: "device:victim"` producing a `DELIVERED`
    message whose text and delivery key both named the victim.

A CI watch that declares no device still uses the project container subject
`device:ci:<project>`, which needs no device grant.

A run watch that names only a `delivery_id` is deliverable when the far end
answers with the run's own `runId` — that identity comes from the observation,
never from the delivery id. When nothing names a run, the tick is
`BLOCKED_RUN_IDENTITY` (exit 3, nothing sent): #423's envelope is not handed a
`runId: null` that its ledger rejects *after* a claim has been written.

## Composition, not reimplementation

*   **One notifier, injected.** `--notifier-entry module:callable` must resolve
    to an object exposing `observe_payload(source, payload)` — #423's
    `MilestoneNotifier`. The payloads this package builds are #423's own closed
    envelopes (`cloudcli` and `github_actions`), so #423's `decide()` remains the
    single classifier of green/red/yellow/UNKNOWN. With **no** binding the tick
    still reports its vitals but delivery is `BLOCKED_LEDGER`, counted as
    `UNCONFIRMED` — there is no fallback sender, by design.
*   **One ledger.** This package stores a cursor, a boot epoch and a heartbeat.
    It does not store deliveries, receipts or transport keys: those are #423's.
*   **One writer.** A `flock` sidecar (`<state>.lock`) is taken for the whole
    tick. A concurrent process is refused `SECOND_WRITER` **before it reads**.
    A restart adopts a new `boot_epoch` and records `recovered_from`.

## Handoffs (documented, not dual-written)

1.  **#490 → here.** `sources.LegacyFactsSource` is a thin adapter with the same
    observation shape as PR #490's `LegacyReadOnlySourceAdapter`
    (`view/status/reason/retryable/identity/native/readings/readingsUsed`).
    When #490 lands, its `observe_run` / `observe_ci` can replace this one
    unchanged: `core.observe_watch` only needs those two method names and a
    mapping. Nothing in #490 was edited.
2.  **#423 → here.** When #423 merges, the binding is
    `--notifier-entry <module>:<factory>` where the factory returns a
    `MilestoneNotifier`. Until then that is a **named blocker**, not a stub.
3.  **#488 → here.** The activation wrapper needs no change: point
    `--monitor-entry` at `fleet_next_agent_monitor.runtime:tick`.

## What is NOT claimed

*   No timer is installed, no systemd unit is enabled, nothing is deployed.
*   No Feishu message has been sent; no credential is read or stored.
*   No live Legacy client is contacted: every test uses the offline facts
    client, and the live client is an injection point this package does not own.
*   The cross-process dedupe proven here is at the **hand-over boundary** (the
    same fact reaches `observe_payload` at most once). End-to-end message dedupe
    is #423's ledger claim, not this package's.

## Tests

```
python3 -m unittest discover -s tests -p 'test_fleet_next_agent_monitor_core.py' -v
```

52 tests on this branch, every negative case in its own interpreter; 2 of them
start a subprocess of #498's activation wrapper and skip here (that file is not
on this branch) — they run for real in the composed tree (#497 + #498).

RED on the `3db8ebf1` tree the tests were written against: 11 failures, exit 1
(`notifier`/`sources`/`core` as they were, tests as they are now).
GREEN here: 52 passed, 2 skipped, exit 0.
