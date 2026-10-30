# fleet-hub-watch — the hub's resident watcher (M2)

Design: [`docs/plans/fleet-v1-resident-monitor-and-tokyo-hub.md`](../../docs/plans/fleet-v1-resident-monitor-and-tokyo-hub.md) §3.

Runs on the hub (Tokyo). Each tick:

1. **discover** — lists sessions on every enrolled **company** device; for each
   session the provider reports as running, reads its detail and takes the
   current run id. A new run joins the watch list.
2. **observe** — classifies each due run with the Agent Monitor's own rule
   (`fleet_next_agent_monitor.sources.LegacyFactsSource`) over a
   FleetService-backed client (`facts.py`). Terminal outcomes become one inbox
   event each; moving runs are re-checked on a 10s → 30s → 2min → 10min backoff.

| classifier says | inbox event | level |
|---|---|---|
| `TERMINAL_VERIFIED` (readable output) | `run.completed` | notify |
| `FAILED` | `run.failed` | notify |
| `UNKNOWN`, not retryable (no output, vanished, identity mismatch) | `run.unknown` | decide |
| still moving after `stall_after` (2h) | `run.stalled`, once | notify |
| still moving after `max_age` (24h) | `run.unknown` | decide |
| route offline | nothing yet; checked again | — |

Inbox events carry identifiers and the verdict only, never transcript text.

## Composition

`fleet_mcp` (service, inbox) and `fleet_next_agent_monitor` (classifier) are
composed here, so neither imports the other.

    PYTHONPATH=<repo>:<repo>/integrations/fleet-next-agent-monitor:<repo>/integrations/fleet-hub-watch \
      python3 -m fleet_hub_watch --config <fleet binding> [--interval 30] [--once]

The binding must declare `inbox.path` (the same file `fleet_inbox` reads).
`watch.sqlite`, `watch.lock` and `watch.heartbeat.json` sit next to it, all 0600.
A second watcher on the same inbox exits 3.

## Not done here

- Not installed anywhere. `serveros-fleet-watch.service.template` is a
  rendering input; enabling it on Tokyo is M5 and an owner decision.
- Discovery cannot see a run that starts and ends between two passes.
- The native `run_get` field naming the run's state is not yet observed on a
  live CloudCLI; `status` and `state` are both read. Confirm at M5.
- Notification delivery (M3), node/resource events (M4), and
  `decision.required` from pending approvals are not part of M2.
