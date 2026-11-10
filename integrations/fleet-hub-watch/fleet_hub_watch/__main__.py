"""`python -m fleet_hub_watch` — the hub's resident watcher.

    python -m fleet_hub_watch --config <fleet binding>
        --notify lark:<user:open_id|chat:chat_id> | journal | off
        [--interval 30] [--once] [--card-spec card-spec.yaml]
    python -m fleet_hub_watch --check-spec card-spec.yaml

The broadcast spec decides what the cards say and when they go out (titles,
headings, advice, the workday window, the 日报 time and sections). It is read at
start and again whenever the file changes; a spec that fails its check is
refused by name and the last good one stays in use, so an edit can never stop
the watch. `--check-spec` proves a spec without starting anything.

Each tick: discover and classify runs (M2), read every company device's
Core-Lite reading and record level changes (M4), then send or patch Feishu
cards (M3). `--notify` has no default: `lark:user:<open_id>` and
`lark:chat:<chat_id>` send for real, `journal` writes the cards it would send to
`notify.journal.jsonl` and sends nothing, `off` runs with no notifier. Those
three are the whole set, and none of them is reached by forgetting a flag.

The binding must name `inbox.path`: the watcher writes the same inbox the Fleet
surface reads through `fleet_inbox`, and a watcher writing somewhere the surface
does not read would be a monitor nobody can see. Its watch list and lock sit in
the inbox's directory.

One watcher per inbox: a second process exits 3 instead of double-recording.
Every tick writes a heartbeat file, so the hub's own service-health reading can
tell a watcher that stopped from one that has nothing to say.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

from fleet_mcp.config import load_config
from fleet_mcp.fleet import FleetService, Principal
from fleet_mcp.inbox import EventInbox
from fleet_mcp.monitor_control import MonitorControl

from datetime import datetime, timezone

from . import resources
from . import spec as broadcast_spec
from . import cards, feishu
from .notify import JournalTransport, LarkTransport, Notifier, resolve_recipient, run_excerpt
from .watch import Watcher, WatchStore

DEFAULT_CONTRACT = Path(__file__).resolve().parents[3] / "runtime" / "attribution-contract.yaml"
DEFAULT_SPEC_FILE = Path(__file__).resolve().parents[1] / "card-spec.yaml"


class SpecWatcher:
    """The current broadcast spec, reloaded when its file changes."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.mtime: float | None = None
        self.spec = broadcast_spec.load(None)
        self.state = "DEFAULT"
        self.refresh()

    def refresh(self) -> bool:
        """True when a new spec took effect."""
        if self.path is None or not self.path.exists():
            return False
        mtime = self.path.stat().st_mtime
        if mtime == self.mtime:
            return False
        self.mtime = mtime
        try:
            self.spec = broadcast_spec.load(self.path)
        except broadcast_spec.SpecError as exc:
            self.state = f"REFUSED: {exc}"
            print(f"card spec refused, keeping the previous one: {exc}", file=sys.stderr)
            return False
        self.state = f"LOADED {self.path.name}"
        return True

EXIT_ANOTHER_WATCHER = 3
EXIT_NO_INBOX = 2


def _heartbeat(path: Path, document: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False)
    os.replace(temporary, path)


HEALTH_EVERY_SECONDS = 1800


def _ready(state) -> bool:
    return str(state or "").lower() in ("ready", "ok", "valid", "needs_refresh")


def act_on_button(value: dict, control: MonitorControl, inbox) -> str:
    """The one action an owner's click on a hub card may cause."""
    by = "feishu:owner-button"
    if value["fleet"] == "mute":
        control.queue("mute", {"subject": str(value.get("subject")),
                               "minutes": int(value.get("minutes") or 60),
                               "reason": "飞书卡片按钮"}, by=by)
        return "MUTE_QUEUED"
    if value["fleet"] == "pause_broadcast":
        control.queue("pause_broadcast", {"minutes": int(value.get("minutes") or 120),
                                          "reason": "飞书卡片按钮"}, by=by)
        return "PAUSE_QUEUED"
    if value["fleet"] == "revert_tune":
        from fleet import intelligence
        device, runtime = str(value.get("device_ref")), str(value.get("runtime"))
        last = next((r for r in reversed(intelligence.tuning_journal(200))
                     if r["device_ref"] == device and r["runtime"] == runtime), None)
        if last is None:
            return "NOTHING_TO_REVERT"
        record = intelligence.set_default_model(device, runtime, last["before"], by=by,
                                                reason="飞书卡片上撤销")
        inbox.record({"kind": "tuning.changed", "level": "notify", "device_ref": device,
                      "dispatched_by": by, "evidence": f"{device}|tune|{runtime}",
                      "summary": f"{device} {runtime} 默认模型：{record['before'] or '设备默认'} → "
                                 f"{record['after'] or '设备默认'}。原因：你在飞书卡片上撤销"})
        return "REVERTED"
    return "IGNORED"


def apply_commands(control: MonitorControl, notifier, specs: SpecWatcher) -> int:
    """Apply what Fleet queued (fleet_monitor_control) since the last tick."""
    def daily() -> str:
        if notifier is None:
            return "NOTIFY_OFF"
        return notifier.daily(datetime.now(timezone.utc), force=True)

    def reload_spec() -> str:
        specs.mtime = None
        if specs.refresh() and notifier is not None:
            notifier.spec = specs.spec
        return specs.state

    commands = control.pending()
    for command in commands:
        control.apply(command, daily=daily, reload_spec=reload_spec)
    return len(commands)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet_hub_watch", description=__doc__)
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between ticks")
    parser.add_argument("--instance-id", default="tokyo-hub")
    parser.add_argument("--once", action="store_true", help="one tick, then exit")
    parser.add_argument("--batch-excerpt-chars", type=int, default=200,
                        help="characters of each task's final reply on a 批次完成 card "
                             "(0: counts only); long tokens in it are masked")
    parser.add_argument("--notify", default=None,
                        help="required: lark:user:<open_id> | lark:chat:<chat_id> sends for "
                             "real, journal writes the cards and sends nothing, off runs "
                             "without a notifier")
    parser.add_argument("--alerts-to", default=None,
                        help="optional second destination for alert cards only, e.g. "
                             "lark:chat-name:<group name>; the rest stay on --notify")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                        help="attribution contract holding the resource thresholds")
    parser.add_argument("--card-spec", default=str(DEFAULT_SPEC_FILE),
                        help="broadcast spec (what the cards say and when)")
    parser.add_argument("--check-spec", metavar="SPEC",
                        help="prove a broadcast spec and exit (0 good, 2 refused)")
    parser.add_argument("--config", help="the Fleet binding file")
    args = parser.parse_args(argv)

    if args.check_spec:
        try:
            broadcast_spec.load(args.check_spec)
        except broadcast_spec.SpecError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_NO_INBOX
        print("OK: every card renders and passes the CardKit check")
        return 0
    if not args.config:
        parser.error("--config is required")
    if args.notify is None:
        # `journal` used to be the default, so a unit that simply forgot the
        # flag installed a watcher that found every run, recorded every outcome
        # and wrote every card into a file nobody reads — a monitor that reports
        # nothing while looking healthy. A destination is a decision, and a
        # decision is not made by omitting an argument.
        parser.error(
            "--notify is required: name where the cards go. "
            "`lark:owner` sends them to whoever lark-cli here is logged in as; "
            "`lark:user:<open_id>` or `lark:chat:<chat_id>` sends them for real; "
            "`journal` writes the ones it would send and sends nothing; "
            "`off` runs the watcher with no notifier")

    config = load_config(args.config)
    if config.inbox is None:
        print("the binding names no inbox.path; refusing to watch into nowhere", file=sys.stderr)
        return EXIT_NO_INBOX
    directory = config.inbox.path.parent
    lock = open(directory / "watch.lock", "a")  # noqa: SIM115 - held for the process lifetime
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another watcher holds this inbox", file=sys.stderr)
        return EXIT_ANOTHER_WATCHER

    service = FleetService(config)
    principal = Principal(name="hub-watch", scopes=frozenset({"fleet:read"}),
                          credential_kind="local-service")
    watcher = Watcher(service, principal, EventInbox(config.inbox.path),
                      WatchStore(directory / "watch.sqlite"), instance_id=args.instance_id)
    thresholds = resources.load_thresholds(args.contract)
    levels = resources.LevelStore(directory / "resources.sqlite")
    specs = SpecWatcher(Path(args.card_spec) if args.card_spec else None)
    control = MonitorControl(directory)
    names = {e.device_ref.ref: (e.display_name or e.device_ref.ref) for e in service.registry.all()}
    notifier = None
    if args.notify == "journal":
        notifier = Notifier(watcher.inbox, directory / "notify.sqlite",
                            JournalTransport(directory / "notify.journal.jsonl"), device_names=names,
                            spec=specs.spec, control=control,
                            excerpt=run_excerpt(service, principal, args.batch_excerpt_chars))
    elif args.notify.startswith("lark:"):
        try:
            recipient = resolve_recipient(args.notify[len("lark:"):])
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_NO_INBOX
        alert_transport = None
        if args.alerts_to:
            try:
                alerts = resolve_recipient(args.alerts_to[len("lark:"):]
                                           if args.alerts_to.startswith("lark:") else args.alerts_to)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return EXIT_NO_INBOX
            alert_transport = LarkTransport(alerts)
            print(f"alert cards go to {alerts}", file=sys.stderr)
        print(f"cards go to {recipient}", file=sys.stderr)
        notifier = Notifier(watcher.inbox, directory / "notify.sqlite",
                            LarkTransport(recipient), device_names=names,
                            spec=specs.spec, control=control, alert_transport=alert_transport,
                            excerpt=run_excerpt(service, principal, args.batch_excerpt_chars))
    elif args.notify != "off":
        print(f"unknown --notify {args.notify!r}", file=sys.stderr)
        return EXIT_NO_INBOX
    beat = directory / "watch.heartbeat.json"
    lark_state: dict = {}
    buttons = None
    if notifier is not None and args.notify.startswith("lark:"):
        lark_state = feishu.health()
        buttons = feishu.ButtonListener(
            feishu.owner_open_id(),
            lambda value: act_on_button(value, control, watcher.inbox))
        buttons.start()
    answerer = messages = None
    if notifier is not None and args.notify.startswith("lark:"):
        from .commands import Answerer, MessageListener
        answerer = Answerer(service=service, principal=principal, inbox=watcher.inbox,
                            notifier=notifier, names=names, watching=watcher.store.all)

        def reply(message_id: str, command: str, arg: str) -> str:
            now = datetime.now(timezone.utc)
            try:
                card = answerer.answer(command, arg, now)
            except Exception as exc:  # noqa: BLE001 - say so rather than stay silent
                from .commands import text_card
                card = text_card("没能回答", [f"{command}：{type(exc).__name__}"], now=now,
                                 template="red")
            sent = True
            for i, one in enumerate(card if isinstance(card, list) else [card]):
                key = f"ask:{message_id}:{command}" + (f":{i}" if i else "")
                ok = notifier.transport.send(one, idempotency_key=key)
                if not ok and '"chart"' in json.dumps(one):
                    # A client that refuses the chart still gets the card.
                    from .cards import without_charts
                    ok = notifier.transport.send(without_charts(one), idempotency_key=key + ":plain")
                    watcher.inbox.trace("notify", "chart refused; sent without it",
                                        ref=f"ask:{message_id}")
                sent = bool(ok) and sent
            watcher.inbox.trace("notify", f"answered 「{command}」" if sent else
                                f"answer to 「{command}」 FAILED", ref=f"ask:{message_id}")
            return "SENT" if sent else "FAILED"
        messages = MessageListener(feishu.owner_open_id(), reply)
        messages.start()
    last_health = time.time()
    rich_cache: dict = {}
    while True:
        started = time.time()
        try:
            applied = apply_commands(control, notifier, specs)
            if specs.refresh() and notifier is not None:
                notifier.spec = specs.spec
            result = watcher.tick()
            now = datetime.now(timezone.utc)
            views = resources.sweep(service, principal, watcher.inbox, levels, thresholds,
                                    now_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            rich = None
            if notifier is not None:
                slot = notifier.slot(now)
                if rich_cache.get("slot") != slot or time.time() - rich_cache.get("at", 0) > 600:
                    # Each device's own 资源播报 card, fetched at most every ten
                    # minutes; the hub is the one sender for all of them.
                    try:
                        rich_cache.update(slot=slot, at=time.time(),
                                          cards=service.status_cards(principal))
                    except Exception as exc:  # noqa: BLE001 - plain readings still go out
                        print(f"status cards unreadable: {type(exc).__name__}", file=sys.stderr)
                        rich_cache.update(slot=slot, at=time.time(), cards={})
                rich = rich_cache.get("cards")
                if answerer is not None:
                    answerer.views, answerer.rich = views, rich or {}
            sent = notifier.tick(views, now, rich) if notifier else None
            if buttons is not None and time.time() - last_health > HEALTH_EVERY_SECONDS:
                lark_state, last_health = feishu.health(), time.time()
            if buttons is not None and not _ready(lark_state.get("user")):
                notifier.notify_owner(f"lark-login:{now:%Y-%m-%d}",
                                      cards.lark_card(lark_state, now=now), now)
            _heartbeat(beat, {"last_tick_at": started, "ok": True,
                              "watching": result["watching"],
                              "recorded": result["observe"]["recorded"],
                              "unreachable": result["discover"]["unreachable"],
                              "devices": {v["device_ref"]: v["level"] for v in views},
                              "notify": sent, "card_spec": specs.state,
                              "commands_applied": applied,
                              "feishu": ({**lark_state, "buttons": buttons.state,
                                          "clicks_handled": buttons.handled,
                                          "messages": messages.state if messages else None,
                                          "asks_answered": messages.handled if messages else 0}
                                         if buttons is not None else None)})
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
            _heartbeat(beat, {"last_tick_at": started, "ok": False,
                              "error": type(exc).__name__})
            print(f"tick failed: {type(exc).__name__}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(max(0.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
