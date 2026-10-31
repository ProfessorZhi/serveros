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

## Resource levels (M4) and Feishu cards (M3)

`resources.py` reads each company device's Core-Lite summary and levels it with
the node's own thresholds (`runtime/attribution-contract.yaml`): disk 85% 注意 /
92% 报警, memory 90%, swap 90% (watermark), memory PSI some/full, a FAILED
service, a device that was seen and is now gone. Recovery needs the reading to
clear the bar by the contract's margin. GPU memory is shown, not judged (no
threshold is declared). Level changes become `node.*` inbox events.

`cards.py` renders three CardKit 2.0 cards; `notify.py` decides send vs patch:

| card | colour | when a message is sent | when it changes in place |
|---|---|---|---|
| 资源播报卡 | worst device level | first tick of a workday, CN 08:00–20:00 | next :00/:30 slot if changed; at once on 报警 |
| 报警卡 | 🟡 注意 / 🔴 报警 | an episode opens; yellow → red (old card turns grey 已升级) | red → yellow; 已恢复 (green) |
| 任务完成卡 | 🟢 | completions gathered for 5 minutes | — |

Feishu rules applied (open.feishu.cn message create/patch): `update_multi: true`
on every card; patch only within 14 days (continued on a fresh card after 13);
5 QPS per recipient (sends spaced, capped at 20 per tick, the rest next tick);
30 KB per card (validated); a stable send key so a retry within the hour cannot
duplicate. `--notify journal` (default) writes cards to `notify.journal.jsonl`
and sends nothing; `--notify lark:<user:…|chat:…>` uses
`runtime/message_transport.LarkMessageSender` and is the owner's decision.

`samples/` holds one rendered card per kind and level, marked 示例; paste one
into Feishu's card builder to preview it.

## What the cards say: `card-spec.yaml`

Facts (levels, readings, run outcomes, thresholds) are code. Presentation is
`card-spec.yaml`: titles, headings, advice lines, the workday window, the
update slot, the completion batch, and the 日报 — its time (default 18:00 on
workdays), its sections (`runs`, `problems`, `resources`, `open_now`) and their
order. The `fleet-broadcast` skill (`skill/fleet-broadcast/SKILL.md`) is how a
change is made. `python -m fleet_hub_watch --check-spec card-spec.yaml` proves a
spec; the running watcher reloads the file when it changes, refuses a bad one by
name, keeps the last good one, and records `card_spec` in its heartbeat.

## 日报

One card per workday at the spec's report time, built from what the notifier
counted that day: runs completed / failed / unconfirmed / stalled, the runs that
need a look, the resource episodes (with 已恢复 when they ended), and what is
still open. The header colour is what is still open at report time, not the worst
thing that happened. `samples/daily.json` is an example.

## Not done here

- Not installed anywhere. `serveros-fleet-watch.service.template` is a
  rendering input; enabling it on Tokyo is M5 and an owner decision.
- Discovery cannot see a run that starts and ends between two passes.
- The native `run_get` field naming the run's state is not yet observed on a
  live CloudCLI; `status` and `state` are both read. Confirm at M5.
- `decision.required` from pending approvals is not raised yet.
- Workdays are Monday–Friday; CN public holidays are not known to it.
- No card has been checked in a real Feishu client yet (R11): the samples are
  JSON that passes the CardKit validator, not a screenshot.
