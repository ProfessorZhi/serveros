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
      python3 -m fleet_hub_watch --config <fleet binding> \
        --notify <lark:user:open_id | lark:chat:chat_id | journal | off> [--interval 30] [--once]

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
| 报警卡 | 🔴 报警 (a device reading that held 5 min); 🟡/🔴 for a run | an episode opens; yellow → red (old card turns grey 已升级) | red → yellow; 已恢复 (green) |
| 任务完成卡 | 🟢 | completions gathered for 5 minutes | — |

Feishu rules applied (open.feishu.cn message create/patch): `update_multi: true`
on every card; patch only within 14 days (continued on a fresh card after 13);
5 QPS per recipient (sends spaced, capped at 20 per tick, the rest next tick);
30 KB per card (validated); a stable send key so a retry within the hour cannot
duplicate. `--notify` has no default and is required: `lark:<user:…|chat:…>`
sends for real through `runtime/message_transport.LarkMessageSender`;
`journal` writes the cards it would send to `notify.journal.jsonl` and sends
nothing; `off` runs with no notifier at all. Which one an installation passes
*is* its broadcast policy — a monitor that records completions and sends no
card is a deliberate `journal`, never a forgotten flag.

`samples/` holds one rendered card per kind and level, marked 示例; paste one
into Feishu's card builder to preview it, or draw them all on one page:
`python3 samples/preview.py --out cards.html` (an approximation of Feishu's
rendering — good for judging what is said first, what is coloured and what
repeats).

### How a card is written

Owner, 2026-11-10 («可读性很差»). Every card follows the same four rules, and
`tests/test_fleet_hub_broadcast.py` holds the samples to them:

1. **One plain sentence first.** A reader who reads nothing else knows whether
   anything is wrong, where, and whether it is theirs to do.
2. **Only a problem is coloured.** A healthy number is plain text; a healthy
   device is one grey line.
3. **The state is said once** — the header (its colour, its tag, or the batch
   title), never again in a body tag or a second sentence.
4. **The same thing appears once.** A task every device was given is written
   above the rows; the 日报 has one 「要看的」 list (what is open now and what
   went wrong, each once with ×N), and its count is the sum of that list.

Words a person reads: 交换区 (not Swap 水位), 内存吃紧 (not 内存压力 停顿),
系统盘 / 数据盘 (not ROOT_FS / DATA), 结果不明 (not 没读到结果). No error code
and no count of zero on a card's face.

In the 日报 the `runs` section is the opening sentence, and `open_now` and
`problems` are one list placed at whichever of the two comes first in
`sections`; `resources` is the fold of what recovered.

## What the cards say: `card-spec.yaml`

Facts (levels, readings, run outcomes, thresholds) are code. Presentation is
`card-spec.yaml`: titles, headings, advice lines, the workday window, the
update slot, the completion batch, and the 日报 — its time (default 18:00 on
workdays), its sections (`runs`, `problems`, `resources`, `open_now`) and their
order. The `fleet-broadcast` skill (`skill/fleet-broadcast/SKILL.md`) is how a
change is made. `python -m fleet_hub_watch --check-spec card-spec.yaml` proves a
spec; the running watcher reloads the file when it changes, refuses a bad one by
name, keeps the last good one, and records `card_spec` in its heartbeat.

## Control from Fleet

The monitor answers to the Fleet command centre through two tools (introduced in `fleet-mcp-10`):

- `fleet_monitor_status` (fleet:read): heartbeat age and state, device levels, the
  card spec in use, open alert episodes, mutes, broadcast pause, recent commands.
- `fleet_monitor_control` (fleet:control): `mute` / `unmute` a device or an
  episode, `pause_broadcast` / `resume_broadcast`, `send_daily_now`,
  `reload_spec`. Commands are queued in `monitor-control.sqlite` beside the inbox
  (0600) and applied on the next tick; the result is recorded and shown by
  `fleet_monitor_status`. A mute or pause stops messages, never observation or
  counting; nothing acts on a device.

## Feishu through lark-cli: destinations, self-check, buttons

The watcher uses the lark-cli already logged in on the hub, so nobody looks an
id up (`fleet_hub_watch/feishu.py`).

| `--notify` / `--alerts-to` | goes to |
|---|---|
| `lark:owner` (kit default) | whoever lark-cli is logged in as (`contact +get-user --as user`) |
| `lark:chat-name:<name>` | the group with exactly that name the bot can see (`im +chat-search`) |
| `lark:user:<open_id>` / `lark:chat:<chat_id>` | that person or group |

`--alerts-to` is optional: alert cards go there, everything else stays on
`--notify` (e.g. alerts to a group, the 日报 and completions to the owner).
Unresolvable destinations stop the watcher with the reason; there is no silent
`journal` fallback.

**Self-check.** `lark-cli auth status` at start and every 30 minutes, written to
the heartbeat under `feishu` (`fleet_monitor_status` shows it). A lapsed user
login is carded to the owner once a day; the bot keeps sending.

**Buttons.** Open alert cards carry 静音 1 小时 / 暂停播报 2 小时; tuning cards
carry 撤销这次调整. Clicks arrive over `lark-cli event consume
card.action.trigger --as bot` and are acted on only when the operator is the
owner, only for those three actions (queued as monitor-control commands, or the
tuning journal reverted). One-time console step: **App → Events & Callbacks →
Callback configuration → long connection**, subscribe `card.action.trigger`.
Without it the buttons render and nothing comes back; the heartbeat's
`feishu.buttons` says `LISTENING` either way, so check a click once.

## Resource readings

For the hub's own device, bind `core_lite: {snapshot_path: ...}` in the Fleet
binding; for a node-connector device, set the connector's `core_snapshot_path`.
Both read the owner-only file that
`native/node-core/core-lite/deploy/publish-status.sh` writes every 30 s. Until a
file is published the readings are `NOT_CONFIGURED`, never zero.

## 日报

One card per workday at the spec's report time, built from what the notifier
counted that day: runs completed / failed / unconfirmed / stalled, the runs that
need a look, the resource episodes (with 已恢复 when they ended), and what is
still open. The header colour is what is still open at report time, not the worst
thing that happened. `samples/daily.json` is an example.

A device reading at 注意 never sends a card of its own: it is a line on the
资源播报卡. A device 报警 is held for `schedule.alert_debounce_seconds` (300 s in
`card-spec.yaml`) before its card goes out; one that recovers or drops back to
注意 inside that window sends nothing and is still counted in the 日报. Memory
pressure `full` has its own recovery margin
(`psi_memory_full_avg60_percent_recovery_margin`, 0.25), so a host hovering at
the 0.5 line does not flip a card red and green.

## Not done here

- `serveros-fleet-watch.service.template` is a rendering input, not a unit:
  `distribution/device_kit.py` refuses to render it without `--notify`, because
  the destination cannot be left to the template's author's guess.
- Discovery cannot see a run that starts and ends between two passes.
- The native `run_get` field naming the run's state is not yet observed on a
  live CloudCLI; `status` and `state` are both read. Confirm at M5.
- `decision.required` from pending approvals is not raised yet.
- Workdays are Monday–Friday; CN public holidays are not known to it.
- No card has been checked in a real Feishu client yet (R11): the samples are
  JSON that passes the CardKit validator, not a screenshot.
