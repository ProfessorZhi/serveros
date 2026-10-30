"""M3 — deciding which card to send or patch, and keeping the receipt.

Inputs: the hub inbox (read with its own cursor, `hub-notify`) and the
resource views of the current tick. Output: calls on a `Transport` and one
ledger row per card.

When a person is interrupted (a NEW message) and when a card only changes in
place (a PATCH), following CARDS-STYLE R14/R17/R20:

| situation                                     | action                         |
|-----------------------------------------------|--------------------------------|
| first tick of a workday window (CN 08:00–20:00)| send today's 资源播报卡        |
| a later :00/:30 slot, card content changed     | patch it                       |
| the fleet turns 报警, card content changed     | patch it now (not at the slot) |
| a reading or run opens an episode             | send a 报警卡 (yellow or red)  |
| yellow episode becomes red                    | send a new red card, patch the |
|                                               | old one to grey 已升级         |
| red episode falls back to yellow              | patch, no new message          |
| episode ends                                  | patch to green 已恢复          |
| runs complete                                 | one green 任务完成卡 per 5 min |

Feishu limits (open.feishu.cn, message create / patch): a card may be patched
only within 14 days of sending and only if it declared `update_multi: true`;
5 QPS per message, and 5 QPS per user or chat for sends; 30 KB per card; a send
`uuid` deduplicates for one hour. Accordingly: every card declares
`update_multi`; an episode older than 13 days is continued on a fresh card; sends
are spaced and capped per tick; each send carries a stable idempotency key so a
retry inside the hour cannot produce a second message.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from . import cards
from .levels import ALERT, OK, RANK, WARN, private_file

SUBSCRIBER = "hub-notify"
WINDOW_START, WINDOW_END = 8, 20          # CN local hours, workdays
SLOT_MINUTES = 30
BATCH_SECONDS = 300
PATCH_WINDOW = timedelta(days=13)          # Feishu allows 14; leave a day of slack
MAX_SENDS_PER_TICK = 20
SPACING_SECONDS = 0.25                      # under 5 QPS per recipient

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_key   TEXT PRIMARY KEY,
    message_id TEXT,
    sent_at    TEXT NOT NULL,
    patched_at TEXT,
    version    TEXT NOT NULL,
    slot       TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    episode    TEXT PRIMARY KEY,
    card_key   TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL,
    body       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_completions (
    run_key    TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    first_at   TEXT NOT NULL
);
"""


class Transport(Protocol):
    def send(self, card: dict, *, idempotency_key: str) -> str | None: ...
    def patch(self, message_id: str, card: dict, *, sent_at: str) -> bool: ...


class JournalTransport:
    """Writes what would be sent to a JSONL file. The default: nothing leaves."""

    def __init__(self, path: str | Path) -> None:
        self.path = private_file(path)

    def _write(self, row: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def send(self, card: dict, *, idempotency_key: str) -> str | None:
        message_id = "journal-" + idempotency_key[:24]
        self._write({"op": "send", "message_id": message_id, "key": idempotency_key, "card": card})
        return message_id

    def patch(self, message_id: str, card: dict, *, sent_at: str) -> bool:
        self._write({"op": "patch", "message_id": message_id, "card": card})
        return True


class LarkTransport:
    """The real thing: `runtime/message_transport.LarkMessageSender` (lark-cli).

    Built only when the operator names a recipient. It refuses oversize cards
    and patches outside the 14-day window itself; a refusal is returned as
    "not sent" and retried on a later tick, never turned into a second card.
    """

    def __init__(self, recipient: str) -> None:
        import importlib.util
        import sys
        name = "serveros_message_transport"
        if name not in sys.modules:
            path = Path(__file__).resolve().parents[3] / "runtime" / "message_transport.py"
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        self._sender = sys.modules[name].sender(recipient)

    def send(self, card: dict, *, idempotency_key: str) -> str | None:
        answer = self._sender.send_card(card, idempotency_key=idempotency_key)
        if answer.get("state") != "DELIVERED":
            return None
        return (answer.get("provider") or {}).get("remote_id")

    def patch(self, message_id: str, card: dict, *, sent_at: str) -> bool:
        answer = self._sender.update({"message_id": message_id, "sent_at": sent_at, "card": card})
        return answer.get("state") == "DELIVERED"


def _version(card: dict) -> str:
    """The card's content without its timestamp line, so a re-render of the same
    facts is the same version and does not cause a patch."""
    body = json.dumps(card["body"], ensure_ascii=False, sort_keys=True)
    head = json.dumps({k: v for k, v in card["header"].items() if k != "subtitle"},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256((head + body).encode()).hexdigest()[:16]


def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:40]


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Notifier:
    def __init__(self, inbox: Any, ledger_path: str | Path, transport: Transport, *,
                 device_names: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.inbox = inbox
        self.path = private_file(ledger_path)
        self.transport = transport
        self.device_names = device_names or {}
        self.sleep = sleep
        self._sent_this_tick = 0
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- transport with pacing ----------------------------------------------

    def _send(self, card_key: str, card: dict, now: datetime, *, slot: str | None = None) -> bool:
        if self._sent_this_tick >= MAX_SENDS_PER_TICK:
            return False
        if self._sent_this_tick:
            self.sleep(SPACING_SECONDS)
        message_id = self.transport.send(card, idempotency_key=_key(card_key, _version(card)))
        self._sent_this_tick += 1
        if not message_id:
            return False
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO cards(card_key, message_id, sent_at, version, slot)"
                         " VALUES (?,?,?,?,?)",
                         (card_key, message_id, _iso(now), _version(card), slot))
        return True

    def _patch(self, card_key: str, card: dict, now: datetime, *, slot: str | None = None) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM cards WHERE card_key=?", (card_key,)).fetchone()
        if row is None:
            return False
        if row["version"] == _version(card):
            return True
        if self._sent_this_tick:
            self.sleep(SPACING_SECONDS)
        ok = self.transport.patch(row["message_id"], card, sent_at=row["sent_at"])
        self._sent_this_tick += 1
        if ok:
            with self._connect() as conn:
                conn.execute("UPDATE cards SET version=?, patched_at=?, slot=COALESCE(?, slot) "
                             "WHERE card_key=?", (_version(card), _iso(now), slot, card_key))
        return ok

    def _card(self, card_key: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM cards WHERE card_key=?", (card_key,)).fetchone()

    # -- 资源播报卡 -----------------------------------------------------------

    @staticmethod
    def in_window(now: datetime) -> bool:
        local = now.astimezone(cards.CN)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        return WINDOW_START * 60 <= minutes <= WINDOW_END * 60

    @staticmethod
    def slot(now: datetime) -> str:
        local = now.astimezone(cards.CN)
        return f"{local:%Y-%m-%d} {local.hour:02d}:{(local.minute // SLOT_MINUTES) * SLOT_MINUTES:02d}"

    def status(self, views: list[dict], now: datetime) -> str:
        """Send or patch today's 资源播报卡. Returns what happened."""
        if not views or not self.in_window(now):
            return "OUTSIDE_WINDOW" if views else "NO_VIEWS"
        card_key = f"status:{now.astimezone(cards.CN):%Y-%m-%d}"
        card = cards.resource_card(views, now=now)
        slot = self.slot(now)
        existing = self._card(card_key)
        if existing is None:
            return "SENT" if self._send(card_key, card, now, slot=slot) else "SEND_FAILED"
        if existing["version"] == _version(card):
            return "UNCHANGED"
        urgent = cards.overall(views) == ALERT
        if existing["slot"] == slot and not urgent:
            return "WAITING_FOR_SLOT"
        return "PATCHED" if self._patch(card_key, card, now, slot=slot) else "PATCH_FAILED"

    # -- 报警卡 ---------------------------------------------------------------

    def _episode(self, episode: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM episodes WHERE episode=?", (episode,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "body": json.loads(row["body"])}

    def _save_episode(self, episode: str, card_key: str, generation: int, state: str,
                      body: dict) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO episodes(episode, card_key, generation, state, body)"
                         " VALUES (?,?,?,?,?)",
                         (episode, card_key, generation, state, json.dumps(body, ensure_ascii=False)))

    def _open(self, body: dict, now: datetime) -> None:
        """Open, raise, lower or refresh an episode."""
        episode = body["episode"]
        current = self._episode(episode)
        if current is None or current["state"] != "OPEN":
            generation = (current["generation"] + 1) if current else 0
            card_key = f"episode:{episode}#{generation}"
            body = {**body, "first_seen": body.get("first_seen") or _iso(now), "state": "OPEN"}
            if self._send(card_key, cards.alert_card(body, now=now), now):
                self._save_episode(episode, card_key, generation, "OPEN", body)
            return
        previous = current["body"]
        body = {**previous, **body, "first_seen": previous.get("first_seen"), "state": "OPEN"}
        sent = self._card(current["card_key"])
        expired = sent is not None and now - _parse(sent["sent_at"]) > PATCH_WINDOW
        escalated = RANK[body["level"]] > RANK[previous["level"]]
        if escalated or expired:
            # A person is interrupted for a new red, and a card past Feishu's
            # patch window cannot change: both continue on a fresh card.
            if escalated:
                self._patch(current["card_key"],
                            cards.alert_card({**previous, "state": "ESCALATED"}, now=now), now)
            generation = current["generation"] + 1
            card_key = f"episode:{episode}#{generation}"
            if self._send(card_key, cards.alert_card(body, now=now), now):
                self._save_episode(episode, card_key, generation, "OPEN", body)
            return
        if self._patch(current["card_key"], cards.alert_card(body, now=now), now):
            self._save_episode(episode, current["card_key"], current["generation"], "OPEN", body)

    def _close(self, episode: str, now: datetime, *, text: str | None = None) -> None:
        current = self._episode(episode)
        if current is None or current["state"] != "OPEN":
            return
        body = {**current["body"], "state": "RECOVERED", "recovered_at": _iso(now)}
        if text:
            body["text"] = text
        if self._patch(current["card_key"], cards.alert_card(body, now=now), now):
            self._save_episode(episode, current["card_key"], current["generation"], "RECOVERED", body)

    # -- inbox ----------------------------------------------------------------

    def _name(self, device_ref: str | None) -> str:
        return self.device_names.get(device_ref or "", device_ref or "—")

    def consume(self, now: datetime) -> int:
        """Turn new inbox events into episode and completion work."""
        page = self.inbox.read(SUBSCRIBER, limit=200)
        handled_to = page["cursor"]
        for event in page["events"]:
            if self._sent_this_tick >= MAX_SENDS_PER_TICK:
                break
            self._handle(event, now)
            handled_to = event["seq"]
        if handled_to > page["cursor"]:
            # Only as far as what was handled: an event left over by the per-tick
            # send cap is read again next tick, not skipped.
            self.inbox.ack(SUBSCRIBER, handled_to)
        return len(page["events"])

    def _handle(self, event: dict, now: datetime) -> None:
        kind = event["kind"]
        name = self._name(event.get("device_ref"))
        if kind.startswith("node."):
            key, _, level = (event.get("evidence") or "|").partition("|")
            episode = f"{event['device_ref']}|{key}"
            if kind == "node.recovered":
                self._close(episode, now, text=event["summary"])
                return
            self._open({"episode": episode, "key": key, "kind": kind,
                        "device_ref": event["device_ref"],
                        "subject": event["summary"].split("：")[0],
                        "text": event["summary"].split("：", 1)[-1],
                        "level": level if level in (WARN, ALERT) else WARN,
                        "last_seen": event["occurred_at"], "first_seen": event["occurred_at"],
                        "evidence": event.get("evidence")}, now)
            return
        if not kind.startswith("run."):
            return
        run_episode = f"run|{event.get('session_ref')}|{event.get('run_id')}"
        project = event.get("project") or "未分配项目"
        if kind == "run.completed":
            self._close(run_episode, now, text="任务已完成，读到了实际输出")
            with self._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO pending_completions(run_key, body, first_at)"
                             " VALUES (?,?,?)",
                             (run_episode, json.dumps({"run_id": event["run_id"], "project": project,
                                                       "device_ref": event.get("device_ref"),
                                                       "device_name": name}, ensure_ascii=False),
                              _iso(now)))
            return
        level, what, detail = {
            "run.failed": (ALERT, "任务失败", "执行失败"),
            "run.unknown": (WARN, "任务结果无法确认", "没能确认它完成了，也没能确认它失败了"),
            "run.stalled": (WARN, "任务运行时间过长", "仍在运行，已超过 2 小时"),
        }.get(kind, (WARN, kind, kind))
        reason = re.search(r"\(([A-Z0-9_]+)\)\s*$", event["summary"])
        if reason and kind != "run.stalled":
            detail += f"（{reason.group(1)}）"
        self._open({"episode": run_episode, "key": f"run:{event.get('run_id')}", "kind": kind,
                    "device_ref": event.get("device_ref"),
                    "subject": f"{what} · {project}",
                    "text": f"{event['run_id']}（{name}）{detail}",
                    "level": level, "first_seen": event["occurred_at"],
                    "last_seen": event["occurred_at"], "evidence": event.get("session_ref")}, now)

    def flush_completions(self, now: datetime, *, force: bool = False) -> int:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM pending_completions ORDER BY first_at").fetchall()
        if not rows:
            return 0
        oldest = _parse(rows[0]["first_at"])
        if not force and (now - oldest).total_seconds() < BATCH_SECONDS:
            return 0
        runs = [json.loads(r["body"]) for r in rows]
        card_key = f"done:{_key(*[r['run_key'] for r in rows])}"
        if not self._send(card_key, cards.completion_card(runs, now=now), now):
            return 0
        with self._connect() as conn:
            conn.executemany("DELETE FROM pending_completions WHERE run_key=?",
                             [(r["run_key"],) for r in rows])
        return len(runs)

    # -- one pass -------------------------------------------------------------

    def tick(self, views: list[dict], now: datetime) -> dict[str, Any]:
        self._sent_this_tick = 0
        consumed = self.consume(now)
        completions = self.flush_completions(now)
        status = self.status(views, now)
        return {"consumed": consumed, "completions_sent": completions,
                "status": status, "messages": self._sent_this_tick}
