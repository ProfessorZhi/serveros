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
| a run fails, or cannot be confirmed, or stalls | send a 报警卡 (yellow or red)  |
| a device reading turns 报警 and HOLDS for       | send a red 报警卡              |
|   `schedule.alert_debounce_seconds` (300)     |                                |
| a device reading turns 注意, or a 报警 ends     | no card: the 资源播报卡 line   |
|   before the debounce                         |   and the 日报 carry it         |
| yellow episode becomes red                    | send a new red card, patch the |
|                                               | old one to grey 已升级         |
| red episode falls back to yellow              | patch, no new message          |
| episode ends                                  | patch to green 已恢复          |
| runs complete                                 | one green 任务完成卡 per 5 min |
| workday, at the spec's report time            | one 日报卡 per day             |

The workday window, slot length, completion batch and report time come from
the broadcast spec (`spec.py`); the defaults are the values above.

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
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

from . import cards
from . import spec as broadcast_spec
from .levels import ALERT, OK, RANK, WARN, private_file

SUBSCRIBER = "hub-notify"

#: What a 批次完成 card masks in a reply excerpt: things shaped like keys.
_SECRETISH = re.compile(r"\b(?:sk|pk|ghp|gho|xox[abps]|AKIA)[-_A-Za-z0-9]{12,}|\b[A-Za-z0-9+/_=-]{32,}\b")
_OUTPUT_FIELDS = ("lastAssistantMessage", "last_assistant_message", "output", "result")


def _as_text(value: Any) -> str | None:
    """A reply as providers carry it: a string, {content|text}, or a list of blocks."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _as_text(value.get("content") if value.get("content") is not None else value.get("text"))
    if isinstance(value, list):
        parts = [t for t in (_as_text(v) for v in value) if t]
        return " ".join(parts) or None
    return None


_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-|^[0-9a-f]{16,}$|^run-", re.I)


#: A device's own card older than this is not shown as current: the device
#: falls back to the plain readings until it writes a fresh one.
RICH_MAX_AGE = timedelta(minutes=20)


def rich_card(document: dict, now: datetime) -> dict | None:
    """The card a device made, ready to send and patch, or None when it is
    missing, failed or stale."""
    if not isinstance(document, dict) or document.get("state") != "OK":
        return None
    card = document.get("card")
    try:
        made = _parse(str(document.get("made_at")))
    except ValueError:
        return None
    if not isinstance(card, dict) or now - made > RICH_MAX_AGE:
        return None
    card = json.loads(json.dumps(card))
    config = card.setdefault("config", {})
    config["update_multi"] = True          # patched in place all day
    return card


def _looks_like_id(text: str) -> bool:
    return bool(_ID_RE.match(text.strip()))


def clip_excerpt(text: Any, chars: int) -> str | None:
    """One line, key-shaped strings masked, at most `chars` characters."""
    text = _as_text(text)
    if not isinstance(text, str) or chars <= 0:
        return None
    flat = " ".join(text.split())
    flat = _SECRETISH.sub("‹已遮蔽›", flat)
    if not flat:
        return None
    return flat if len(flat) <= chars else flat[:chars - 1] + "…"


def recorded_reply(inbox: Any, run_id: str) -> str | None:
    """The reply the monitor kept in the trace when this run finished, or None."""
    reader = getattr(inbox, "read_trace", None)
    if reader is None:
        return None
    try:
        steps = reader(run_id=run_id, limit=5000)
    except Exception:  # noqa: BLE001
        return None
    found = None
    for step in steps:
        if step.get("step") == "result" and step.get("run_id") == run_id:
            found = (step.get("detail") or {}).get("text") or found
    return found


def clean_label(text: Any, limit: int = 28) -> str:
    """A task's name for a card: the instruction's first words, without
    markdown marks (Owner, 2026-11-11: a title showed «**缓存命中率…»)."""
    text = re.sub(r"[*`#>_~]+", "", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip(" ，,。.:：;；")
    return text if len(text) <= limit else text[:limit - 1].rstrip(" ，,") + "…"


def run_excerpt(service: Any, principal: Any, chars: int,
                inbox: Any = None) -> Callable[[str, str], str | None] | None:
    """(session_ref, run_id) -> the start of that run's final reply, read through
    Fleet as the monitor's own read-only principal. None when `chars` is 0.

    A session can move on after its run (Owner, 2026-11-10: a batch card showed
    FJPCOS «answering» with the owner's later conversation in the same
    session), so the reply the monitor kept when THIS run finished comes first;
    the session is read only when nothing was kept."""
    if chars <= 0:
        return None
    from fleet_mcp.identity import SessionKey

    def read(session_ref: str, run_id: str) -> str | None:
        key = SessionKey.parse(session_ref)
        run = service.get_run(principal, key, run_id)
        text = next((run[f] for f in _OUTPUT_FIELDS if run.get(f)), None)
        if not _as_text(text):
            text = recorded_reply(inbox, run_id) if inbox is not None else None
        if not _as_text(text):
            # No run receipt and nothing kept: the session still holds the reply.
            from .watch import read_reply, recorded_prompt
            text = read_reply(service, principal, key,
                              recorded_prompt(inbox, run_id) if inbox is not None else None)
        return clip_excerpt(text, chars)
    return read
PATCH_WINDOW = timedelta(days=13)          # Feishu allows 14; leave a day of slack
MAX_SENDS_PER_TICK = 20
HISTORY_SLOT_MINUTES = 5                   # one point per reading per five minutes
HISTORY_KEEP = timedelta(hours=24)
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
CREATE TABLE IF NOT EXISTS pending_alerts (
    episode    TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    since      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    device_ref TEXT NOT NULL,
    key        TEXT NOT NULL,
    slot       TEXT NOT NULL,
    value      REAL NOT NULL,
    PRIMARY KEY (device_ref, key, slot)
);
CREATE TABLE IF NOT EXISTS daily_events (
    day        TEXT NOT NULL,
    event_key  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    body       TEXT NOT NULL,
    PRIMARY KEY (day, event_key, kind)
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


class ArchivingTransport:
    """A transport that also hands every card it delivered to `archive`
    (the journal), so what was sent can be read later. An archive that fails
    never stops a card."""

    def __init__(self, inner: Transport, archive: Callable[..., Any]) -> None:
        self.inner, self.archive = inner, archive

    def send(self, card: dict, *, idempotency_key: str) -> str | None:
        message_id = self.inner.send(card, idempotency_key=idempotency_key)
        if message_id:
            self._keep(card, message_id, "发送", idempotency_key)
        return message_id

    def patch(self, message_id: str, card: dict, *, sent_at: str) -> bool:
        done = self.inner.patch(message_id, card, sent_at=sent_at)
        if done:
            self._keep(card, message_id, "更新", None)
        return done

    def _keep(self, card: dict, message_id: str, how: str, key: str | None) -> None:
        try:
            self.archive(card, message_id=message_id, how=how, key=key)
        except Exception as exc:  # noqa: BLE001 - a card is worth more than its copy
            print(f"card not archived: {type(exc).__name__}", file=sys.stderr)


def resolve_recipient(spec: str, *, run=None) -> str:
    """A `--notify lark:<spec>` destination, resolved through lark-cli (see feishu.py)."""
    from . import feishu
    return feishu.resolve(spec, run=run)


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
                 sleep: Callable[[float], None] = time.sleep,
                 spec: dict | None = None, control: Any = None,
                 alert_transport: Transport | None = None,
                 excerpt: Callable[[str, str], str | None] | None = None) -> None:
        self.spec = spec or broadcast_spec.load(None)
        #: `fleet_mcp.monitor_control.MonitorControl`, or None: mutes and pauses
        #: that Fleet queued. A pause or a mute stops messages, never counting.
        self.control = control
        self.inbox = inbox
        self.path = private_file(ledger_path)
        self.transport = transport
        #: Where alert-episode cards go when it differs from the default (e.g.
        #: alerts to a group, the 日报 and completions to the owner).
        self.alert_transport = alert_transport
        self.device_names = device_names or {}
        #: (session_ref, run_id) -> the start of the run's final reply, or None.
        #: Used for the batch card only; the inbox itself never holds reply text.
        self.excerpt = excerpt
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

    def _paused(self, now: datetime) -> bool:
        return self.control is not None and self.control.broadcast_state(now)["state"] == "PAUSED"

    def _muted(self, body: dict, now: datetime) -> bool:
        return self.control is not None and self.control.muted(
            [body.get("device_ref"), body.get("episode")], now)

    def _route(self, card_key: str):
        if self.alert_transport is not None and card_key.startswith("episode:"):
            return self.alert_transport
        return self.transport

    def notify_owner(self, card_key: str, card: dict, now: datetime) -> bool:
        """A card from outside the inbox (e.g. the lark-cli self-check)."""
        if self._card(card_key) is not None:
            return False
        return self._send(card_key, card, now)

    def _trace(self, step: str, **fields: Any) -> None:
        tracer = getattr(self.inbox, "trace", None)
        if tracer is not None:
            try:
                tracer("notify", step, **fields)
            except Exception:  # noqa: BLE001 - the trace never stops a card
                pass

    def _send(self, card_key: str, card: dict, now: datetime, *, slot: str | None = None) -> bool:
        if self._sent_this_tick >= MAX_SENDS_PER_TICK or self._paused(now):
            self._trace("card held: broadcast paused" if self._paused(now)
                        else "card held: per-tick cap", ref=card_key)
            return False
        if self._sent_this_tick:
            self.sleep(SPACING_SECONDS)
        message_id = self._route(card_key).send(card, idempotency_key=_key(card_key, _version(card)))
        if not message_id and cards.is_styled(card):
            # Feishu refused the styled card: the same content, plainly, still arrives.
            message_id = self._route(card_key).send(
                cards.plain(card), idempotency_key=_key(card_key, _version(card), "plain"))
            self._trace("styled card refused; sent plain" if message_id
                        else "plain card also refused", ref=card_key)
        self._sent_this_tick += 1
        self._trace("card sent" if message_id else "card send FAILED", ref=card_key,
                    message_id=message_id,
                    title=((card.get("header") or {}).get("title") or {}).get("content"))
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
        if self._paused(now):
            return False
        if self._sent_this_tick:
            self.sleep(SPACING_SECONDS)
        ok = self._route(card_key).patch(row["message_id"], card, sent_at=row["sent_at"])
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

    def workday(self, now: datetime) -> bool:
        return now.astimezone(cards.CN).isoweekday() in self.spec["schedule"]["workdays"]

    def in_window(self, now: datetime) -> bool:
        if not self.workday(now):
            return False
        local = now.astimezone(cards.CN)
        start, _, end = self.spec["schedule"]["window"].partition("-")
        minutes = local.hour * 60 + local.minute
        return broadcast_spec.minutes(start) <= minutes <= broadcast_spec.minutes(end)

    def slot(self, now: datetime) -> str:
        local = now.astimezone(cards.CN)
        size = self.spec["schedule"]["slot_minutes"]
        start = (local.hour * 60 + local.minute) // size * size
        return f"{local:%Y-%m-%d} {start // 60:02d}:{start % 60:02d}"

    def status(self, views: list[dict], now: datetime, rich: dict[str, dict] | None = None) -> str:
        """Send or patch today's 资源播报: the device's own rich card where it
        made a fresh one, the plain readings for the rest. Returns what happened."""
        if not views and not rich:
            return "NO_VIEWS"
        if not self.spec["schedule"].get("resource_push", False):
            return "ON_DEMAND"
        if not self.in_window(now):
            return "OUTSIDE_WINDOW"
        outcomes = []
        covered = set()
        for device_ref, document in sorted((rich or {}).items()):
            card = rich_card(document, now)
            if card is None:
                continue
            covered.add(device_ref)
            outcomes.append(self._daily_card(f"rich:{device_ref}:{now.astimezone(cards.CN):%Y-%m-%d}",
                                             card, now, urgent=card["header"].get("template") == "red"))
        rest = [v for v in views if v.get("device_ref") not in covered]
        if rest:
            outcomes.append(self._plain_status(rest, now))
        return ",".join(outcomes) or "NO_VIEWS"

    def _daily_card(self, card_key: str, card: dict, now: datetime, *, urgent: bool) -> str:
        slot = self.slot(now)
        existing = self._card(card_key)
        if existing is None:
            return "SENT" if self._send(card_key, card, now, slot=slot) else "SEND_FAILED"
        if existing["version"] == _version(card):
            return "UNCHANGED"
        if existing["slot"] == slot and not urgent:
            return "WAITING_FOR_SLOT"
        return "PATCHED" if self._patch(card_key, card, now, slot=slot) else "PATCH_FAILED"

    def _plain_status(self, views: list[dict], now: datetime) -> str:
        card_key = f"status:{now.astimezone(cards.CN):%Y-%m-%d}"
        card = cards.resource_card(views, now=now, spec=self.spec)
        return self._daily_card(card_key, card, now, urgent=cards.overall(views) == ALERT)

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
        if self._muted(body, now):
            return
        episode = body["episode"]
        current = self._episode(episode)
        if current is None or current["state"] != "OPEN":
            generation = (current["generation"] + 1) if current else 0
            card_key = f"episode:{episode}#{generation}"
            body = {**body, "first_seen": body.get("first_seen") or _iso(now), "state": "OPEN"}
            if self._send(card_key, cards.alert_card(body, now=now, spec=self.spec), now):
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
                            cards.alert_card({**previous, "state": "ESCALATED"}, now=now, spec=self.spec), now)
            generation = current["generation"] + 1
            card_key = f"episode:{episode}#{generation}"
            if self._send(card_key, cards.alert_card(body, now=now, spec=self.spec), now):
                self._save_episode(episode, card_key, generation, "OPEN", body)
            return
        if self._patch(current["card_key"], cards.alert_card(body, now=now, spec=self.spec), now):
            self._save_episode(episode, current["card_key"], current["generation"], "OPEN", body)

    def expire_runs(self, now: datetime, *, older_than: timedelta = timedelta(hours=6)) -> int:
        """A task alert still open after six hours is dropped from the open list,
        silently — owner, 2026-11-09: 31 old 无法确认 episodes stayed open
        forever and kept counting as 当前问题. Its card is left as it was."""
        with self._connect() as conn:
            rows = conn.execute("SELECT episode, body FROM episodes WHERE state='OPEN' "
                                "AND episode LIKE 'run|%'").fetchall()
        expired = 0
        for row in rows:
            body = json.loads(row["body"])
            try:
                first = _parse(body.get("first_seen") or "")
            except ValueError:
                first = None
            if first is None or now - first > older_than:
                with self._connect() as conn:
                    conn.execute("UPDATE episodes SET state='EXPIRED' WHERE episode=?",
                                 (row["episode"],))
                expired += 1
        return expired

    def _close(self, episode: str, now: datetime, *, text: str | None = None) -> None:
        current = self._episode(episode)
        if current is None or current["state"] != "OPEN":
            return
        if self._muted(current["body"], now):
            return
        body = {**current["body"], "state": "RECOVERED", "recovered_at": _iso(now)}
        if text:
            body["text"] = text
        if self._patch(current["card_key"], cards.alert_card(body, now=now, spec=self.spec), now):
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

    # -- a card for every dispatched task -----------------------------------------

    #: A dispatched task not claimed by a batch within this long gets its own card.
    ANNOUNCE_AFTER = timedelta(seconds=15)
    #: Dispatches older than this are not announced (a restart does not flood).
    ANNOUNCE_WITHIN = timedelta(hours=2)

    @staticmethod
    def _live_key(session_ref: str, run_id: str) -> str:
        return f"run-live:{session_ref}|{run_id}"

    def _live_card(self, session_ref: str, run_id: str, now: datetime, *,
                   outcome: str | None = None, at: str | None = None) -> dict:
        label = clean_label(getattr(self.inbox, "label_of", lambda *_a: None)(session_ref, run_id)) \
            or "一个任务"
        text = None
        if outcome is not None and self.excerpt is not None:
            try:
                text = self.excerpt(session_ref, run_id)
            except Exception:  # noqa: BLE001
                text = None
        run = {"session_ref": session_ref, "run_id": run_id, "outcome": outcome,
               "excerpt": text, "label": label}
        return cards.batch_card({"summary": f"{label}：1 个任务"}, now=now, runs=[run],
                                opened_at=at or getattr(self.inbox, "dispatched_at",
                                                        lambda *_a: None)(session_ref, run_id),
                                device_names=self.device_names)

    def announce_dispatches(self, now: datetime) -> int:
        """Owner, 2026-11-11: «分派任务时根本没有飞书卡片». A batch has had a card
        from its opening; a task dispatched on its own had none until it ended.
        Now every dispatched task not claimed by a batch within
        `ANNOUNCE_AFTER` gets one card, 进行中, patched in place when it ends."""
        reader = getattr(self.inbox, "recent_dispatches", None)
        if reader is None:
            return 0
        sent = 0
        for row in reader(_iso(now - self.ANNOUNCE_WITHIN)):
            try:
                at = _parse(row["at"])
            except (KeyError, ValueError):
                continue
            if now - at < self.ANNOUNCE_AFTER:
                break                                   # rows are oldest first
            ref, run_id = row["session_ref"], row["run_id"]
            if self._batch_of({"session_ref": ref, "run_id": run_id}) is not None:
                continue
            key = self._live_key(ref, run_id)
            if self._card(key) is not None:
                continue
            if self._send(key, self._live_card(ref, run_id, now, at=row["at"]), now):
                sent += 1
                self._trace("card sent: task dispatched", session_ref=ref, run_id=run_id)
        return sent

    def _finish_live(self, event: dict, outcome: str, now: datetime) -> bool:
        """The task's own card, if it has one, patched to how it ended."""
        ref, run_id = event.get("session_ref"), event.get("run_id")
        if not ref or not run_id or self._card(self._live_key(ref, run_id)) is None:
            return False
        self._patch(self._live_key(ref, run_id),
                    self._live_card(ref, run_id, now, outcome=outcome), now)
        self._trace("its own card updated", session_ref=ref, run_id=run_id, outcome=outcome)
        return True

    def pulse(self, now: datetime) -> int:
        """Read the inbox between ticks.

        A tick is 30 s of discovery and device probing, and an event that lands
        just after one waits the whole interval. A dispatched batch is the case
        that hurts: `batch.opened` and its first members can settle inside the
        same window, so the card is already the finished one when it is first
        sent, and the person who dispatched sees no card while the work runs.
        Reading the inbox on its own short cadence keeps a card close to the
        thing it announces; the tick still does everything else.
        """
        self._sent_this_tick = 0
        handled = self.consume(now)
        try:
            self.announce_dispatches(now)
        except Exception:  # noqa: BLE001 - a missed announcement is not a crash
            pass
        return handled

    def _label(self, event: dict) -> str:
        """What the owner calls this task: the instruction's first line, else the
        project when it is a name (not an id), else 一个任务."""
        finder = getattr(self.inbox, "label_of", None)
        label = finder(event.get("session_ref"), event.get("run_id")) if finder else None
        project = event.get("project")
        if not label and isinstance(project, str) and project and not _looks_like_id(project):
            label = project
        return clean_label(label) or "一个任务"

    def _batch_of(self, event: dict) -> str | None:
        finder = getattr(self.inbox, "batch_of", None)
        return finder(event.get("session_ref"), event.get("run_id")) if finder else None

    def _batch_id_of(self, event: dict) -> str | None:
        """The batch this event belongs to, from its own tag or the member table."""
        ref = str(event.get("work_ref") or "")
        if ref.startswith("batch:"):
            return ref.removeprefix("batch:")
        return self._batch_of(event)

    def _refresh_batch(self, event: dict, now: datetime) -> None:
        """A batch member settled: say so on the batch's own card."""
        batch_id = self._batch_id_of(event)
        if batch_id:
            self._batch_update(batch_id, now)

    def _batch_update(self, batch_id: str, now: datetime) -> bool:
        """The batch's card: sent when it opens, patched while it runs and ends.

        A dispatched batch used to produce exactly one message, at the end, as
        a fresh send. The person who dispatched it saw nothing until every
        agent was done, and a batch that overran its read window came back as
        「无法读取」on a card that had never shown it was running. Now the card
        exists from the moment the batch is opened, says how many tasks are
        still going, and every member that settles patches it in place — the
        same message from start to end, as the owner asked.
        """
        try:
            head = self.inbox.batch(batch_id)
        except Exception:  # noqa: BLE001 - an unknown batch gets no card, not a crash
            return False
        event = {"summary": f"{head['name']}：{head['total']} 个任务",
                 "work_ref": f"batch:{batch_id}"}
        runs, opened_at = self._batch_runs(event)
        key = f"batch:{event['work_ref']}"
        card = cards.batch_card(event, now=now, runs=runs, opened_at=opened_at,
                                device_names=self.device_names)
        if self._card(key) is None:
            return self._send(key, card, now)
        return self._patch(key, card, now)

    def _batch_runs(self, event: dict) -> tuple[list[dict], str | None]:
        batch_id = str(event.get("work_ref") or "").removeprefix("batch:")
        try:
            head = self.inbox.batch(batch_id)
        except Exception:  # noqa: BLE001 - the card still goes out with the counts
            return [], None
        runs = head["runs"]
        out = []
        for run in runs[:20]:                       # a card stays well under 30 KB
            text = None
            # A run still going has no output to fetch, and asking the device
            # for one costs a round trip on the same tunnel the tick is using.
            if self.excerpt is not None and run.get("outcome") is not None:
                try:
                    text = self.excerpt(run["session_ref"], run["run_id"])
                except Exception:  # noqa: BLE001 - one unreadable run is not the card's end
                    text = None
            label = getattr(self.inbox, "label_of", lambda *_a: None)(run["session_ref"], run["run_id"])
            out.append({**run, "excerpt": text, "label": label})
        return out, head.get("opened_at")

    def _note(self, event: dict, body: dict) -> None:
        """Count an event into the day it happened (CN date) for the 日报."""
        occurred = _parse(event.get("occurred_at") or _iso(datetime.now(timezone.utc)))
        day = f"{occurred.astimezone(cards.CN):%Y-%m-%d}"
        key = event.get("event_id") or f"{event.get('seq')}"
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO daily_events(day, event_key, kind, body)"
                         " VALUES (?,?,?,?)",
                         (day, key, event["kind"], json.dumps(body, ensure_ascii=False)))

    def _handle(self, event: dict, now: datetime) -> None:
        kind = event["kind"]
        name = self._name(event.get("device_ref"))
        if kind in ("batch.opened", "batch.completed"):
            batch_id = str(event.get("work_ref") or "").removeprefix("batch:")
            if batch_id:
                self._batch_update(batch_id, now)
            return
        if kind == "tuning.changed":
            self._send(f"tune:{event.get('event_id') or event.get('seq')}",
                       cards.tuning_card(event, now=now), now)
            return
        if kind.startswith("node."):
            # `reach|ALERT` or `reach|ALERT|TIMEOUT`: the third field names the
            # failure when the hub had one to name. Splitting on the first two
            # separators keeps a third field from being read as part of the level.
            parts = (event.get("evidence") or "|").split("|")
            key = parts[0]
            level = parts[1] if len(parts) > 1 else ""
            episode = f"{event['device_ref']}|{key}"
            if kind == "node.recovered":
                self._note(event, {"episode": episode})
                # Recovered before it was ever carded: a blip. It stays in the
                # 日报 through the note above and interrupts nobody.
                self._drop_pending(episode)
                self._close(episode, now, text=event["summary"])
                return
            level = level if level in (WARN, ALERT) else WARN
            self._note(event, {"episode": episode, "subject": event["summary"].split("：")[0],
                               "text": event["summary"].split("：", 1)[-1],
                               "level": level})
            body = {"episode": episode, "key": key, "kind": kind,
                    "device_ref": event["device_ref"],
                    "subject": event["summary"].split("：")[0],
                    "text": event["summary"].split("：", 1)[-1],
                    "level": level,
                    "last_seen": event["occurred_at"], "first_seen": event["occurred_at"],
                    "evidence": event.get("evidence")}
            current = self._episode(episode)
            if current is not None and current["state"] == "OPEN":
                # A card already exists: raising, lowering and refreshing it is
                # the card's job and does not wait.
                self._open(body, now)
                return
            if level != ALERT:
                # 注意 is a line on the 资源播报卡, not a card of its own.
                self._drop_pending(episode)
                return
            self._hold(episode, body, event["occurred_at"])
            return
        if not kind.startswith("run."):
            return
        run_episode = f"run|{event.get('session_ref')}|{event.get('run_id')}"
        label = self._label(event)
        in_batch = (str(event.get("work_ref") or "").startswith("batch:")
                    or self._batch_of(event) is not None)
        dispatched = getattr(self.inbox, "was_dispatched", lambda *_a: True)(
            event.get("session_ref"), event.get("run_id"))
        if kind == "run.completed":
            self._note(event, {"episode": run_episode})
            self._close(run_episode, now, text="任务已完成，读到了实际输出")
            if in_batch:
                # A batch member is told on its batch card; refresh it now so the
                # person who dispatched sees this one finish without waiting for
                # the last of them.
                self._refresh_batch(event, now)
                self._trace("no card: told on the batch card",
                            session_ref=event.get("session_ref"), run_id=event.get("run_id"))
                return
            if not dispatched:
                # A resident session's own turn is not a task anyone dispatched —
                # the 日报 counts it.
                self._trace("no card: a session's own turn",
                            session_ref=event.get("session_ref"), run_id=event.get("run_id"))
                return
            if self._finish_live(event, "completed", now):
                return                           # its own card says so; no second message
            self._trace("queued for the 任务完成 card", session_ref=event.get("session_ref"),
                        run_id=event.get("run_id"))
            with self._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO pending_completions(run_key, body, first_at)"
                             " VALUES (?,?,?)",
                             (run_episode, json.dumps({"run_id": event["run_id"], "label": label,
                                                       "session_ref": event.get("session_ref"),
                                                       "device_ref": event.get("device_ref"),
                                                       "device_name": name}, ensure_ascii=False),
                              _iso(now)))
            return
        level, what, detail = {
            "run.failed": (ALERT, "任务失败", "执行出错，没有完成"),
            "run.unknown": (WARN, "没拿到任务结果", "任务停了，但读不到它的回复"),
            "run.stalled": (WARN, "任务跑了很久", "已经跑了 2 小时以上，还没结束"),
        }.get(kind, (WARN, kind, kind))
        reason = re.search(r"\(([A-Z0-9_]+)\)\s*$", event["summary"])
        self._note(event, {"episode": run_episode, "subject": f"{what} · {label}",
                           "text": f"{name}：{detail}", "level": level})
        if kind in ("run.failed", "run.unknown") and not in_batch \
                and self._finish_live(event, kind.split(".", 1)[1], now):
            return                               # its own card turned red/orange in place
        if in_batch and kind != "run.stalled":
            # Its outcome is a line on the batch card; one message, not two.
            self._refresh_batch(event, now)
            self._trace("no card: told on the batch card", session_ref=event.get("session_ref"),
                        run_id=event.get("run_id"))
            return
        if not dispatched and kind != "run.stalled":
            self._trace("no card: a session's own turn", session_ref=event.get("session_ref"),
                        run_id=event.get("run_id"))
            # A resident session's own turn: nobody dispatched it, so nobody is
            # waiting for a card about it. The 日报 counts it.
            return
        started = getattr(self.inbox, "dispatched_at", lambda *_a: None)(
            event.get("session_ref"), event.get("run_id"))
        self._open({"episode": run_episode, "key": f"run:{event.get('run_id')}", "kind": kind,
                    "device_ref": event.get("device_ref"),
                    "subject": f"{name} · {what}",
                    "task": label,
                    "text": detail,
                    "level": level,
                    "first_seen": started if started and started <= event["occurred_at"]
                    else event["occurred_at"],
                    "last_seen": event["occurred_at"],
                    "evidence": " · ".join(x for x in (event.get("session_ref"),
                                                       f"run {event.get('run_id')}",
                                                       reason.group(1) if reason else None) if x)},
                   now)

    # -- debounce -------------------------------------------------------------

    def _hold(self, episode: str, body: dict, since: str) -> None:
        """Remember a device 报警 until it has lasted `alert_debounce_seconds`."""
        with self._connect() as conn:
            row = conn.execute("SELECT since FROM pending_alerts WHERE episode=?",
                               (episode,)).fetchone()
            first = row["since"] if row else since
            conn.execute("INSERT OR REPLACE INTO pending_alerts(episode, body, since) VALUES (?,?,?)",
                         (episode, json.dumps({**body, "first_seen": first}, ensure_ascii=False),
                          first))

    def _drop_pending(self, episode: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_alerts WHERE episode=?", (episode,))

    def promote_pending(self, now: datetime) -> int:
        """Open a card for each held 报警 that has outlasted the debounce."""
        wait = timedelta(seconds=self.spec["schedule"]["alert_debounce_seconds"])
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM pending_alerts ORDER BY since").fetchall()
        opened = 0
        for row in rows:
            if now - _parse(row["since"]) < wait:
                continue
            if self._sent_this_tick >= MAX_SENDS_PER_TICK:
                break
            self._open(json.loads(row["body"]), now)
            current = self._episode(row["episode"])
            if current is not None and current["state"] == "OPEN":
                self._drop_pending(row["episode"])
                opened += 1
            elif self._muted(json.loads(row["body"]), now):
                self._drop_pending(row["episode"])
        return opened

    def flush_completions(self, now: datetime, *, force: bool = False) -> int:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM pending_completions ORDER BY first_at").fetchall()
        if not rows:
            return 0
        oldest = _parse(rows[0]["first_at"])
        batch = self.spec["schedule"]["completion_batch_seconds"]
        if not force and (now - oldest).total_seconds() < batch:
            return 0
        # A run can join a batch after it was queued (the batch opened after the
        # run finished): its batch card tells it, so it leaves this card.
        late = [r for r in rows if self._batch_of(json.loads(r["body"])) is not None]
        if late:
            with self._connect() as conn:
                conn.executemany("DELETE FROM pending_completions WHERE run_key=?",
                                 [(r["run_key"],) for r in late])
            rows = [r for r in rows if r not in late]
            if not rows:
                return 0
        runs = [json.loads(r["body"]) for r in rows]
        if self.excerpt is not None:
            # The device's own answer under each task, as on the batch card.
            for run in runs:
                if run.get("session_ref") and run.get("run_id") and not run.get("excerpt"):
                    try:
                        run["excerpt"] = self.excerpt(run["session_ref"], run["run_id"])
                    except Exception:  # noqa: BLE001 - the card still goes out
                        run["excerpt"] = None
        card_key = f"done:{_key(*[r['run_key'] for r in rows])}"
        if not self._send(card_key, cards.completion_card(runs, now=now, spec=self.spec), now):
            return 0
        with self._connect() as conn:
            conn.executemany("DELETE FROM pending_completions WHERE run_key=?",
                             [(r["run_key"],) for r in rows])
        return len(runs)

    # -- 日报 -----------------------------------------------------------------

    def day_summary(self, day: str) -> dict:
        """What the 日报 for `day` (CN date, YYYY-MM-DD) says, from the ledger."""
        with self._connect() as conn:
            rows = conn.execute("SELECT kind, body FROM daily_events WHERE day=? ORDER BY rowid",
                                (day,)).fetchall()
            open_rows = conn.execute("SELECT body FROM episodes WHERE state='OPEN'").fetchall()
        runs = {"completed": 0, "failed": 0, "unknown": 0, "stalled": 0}
        problems, resources, recovered = [], {}, set()
        for row in rows:
            body = json.loads(row["body"])
            kind = row["kind"]
            if kind.startswith("run."):
                runs[kind.split(".", 1)[1]] = runs.get(kind.split(".", 1)[1], 0) + 1
                if kind != "run.completed":
                    problems.append({k: body[k] for k in ("subject", "text", "level")})
            elif kind == "node.recovered":
                recovered.add(body["episode"])
            elif kind.startswith("node."):
                resources[body["episode"]] = {k: body[k] for k in ("subject", "text", "level")}
        open_now = [{k: json.loads(r["body"]).get(k, "") for k in ("subject", "text", "level")}
                    for r in open_rows]
        return {"runs": runs, "problems": problems,
                "resources": [{**r, "recovered": ep in recovered} for ep, r in resources.items()],
                "open_now": open_now}

    def daily(self, now: datetime, *, force: bool = False) -> str:
        """Send today's 日报 once, at or after the spec's report time on a workday.

        `force` (Fleet's `send_daily_now`) skips the time and workday checks but
        not the once-a-day rule.
        """
        report = self.spec["daily_report"]
        if not report["enabled"] and not force:
            return "DISABLED"
        if not self.workday(now) and not force:
            return "NOT_A_WORKDAY"
        local = now.astimezone(cards.CN)
        if not force and local.hour * 60 + local.minute < broadcast_spec.minutes(
                self.spec["schedule"]["daily_report_at"]):
            return "NOT_YET"
        card_key = f"daily:{local:%Y-%m-%d}"
        if self._card(card_key) is not None:
            return "ALREADY_SENT"
        card = cards.daily_card(self.day_summary(f"{local:%Y-%m-%d}"), now=now, spec=self.spec)
        return "SENT" if self._send(card_key, card, now) else "SEND_FAILED"

    # -- one pass -------------------------------------------------------------

    # -- 读数历史（画曲线用） ---------------------------------------------------

    def record_history(self, views: list[dict], now: datetime) -> None:
        """Every numeric reading, one per device and key per HISTORY_SLOT; a day kept."""
        slot = now.astimezone(timezone.utc).replace(
            minute=now.minute - now.minute % HISTORY_SLOT_MINUTES, second=0, microsecond=0)
        rows = [(v["device_ref"], r["key"], _iso(slot), float(r["value"]))
                for v in views for r in v.get("readings") or []
                if isinstance(r.get("value"), (int, float)) and not isinstance(r.get("value"), bool)
                and v.get("device_ref")]
        with self._connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO history(device_ref, key, slot, value) "
                             "VALUES (?,?,?,?)", rows)
            conn.execute("DELETE FROM history WHERE slot < ?",
                         (_iso(now - HISTORY_KEEP),))

    def history(self, device_ref: str, since: datetime) -> dict[str, list[tuple[str, float]]]:
        """{key: [(slot, value), ...]} oldest first, for one device."""
        with self._connect() as conn:
            rows = conn.execute("SELECT key, slot, value FROM history WHERE device_ref=? "
                                "AND slot >= ? ORDER BY slot", (device_ref, _iso(since))).fetchall()
        out: dict[str, list[tuple[str, float]]] = {}
        for row in rows:
            out.setdefault(row["key"], []).append((row["slot"], row["value"]))
        return out

    def tick(self, views: list[dict], now: datetime,
             rich: dict[str, dict] | None = None) -> dict[str, Any]:
        self._sent_this_tick = 0
        try:
            self.record_history(views, now)
        except Exception as exc:  # noqa: BLE001 - a curve is not worth a broadcast
            print(f"history not recorded: {type(exc).__name__}", file=sys.stderr)
        consumed = self.consume(now)
        try:
            self.announce_dispatches(now)
        except Exception as exc:  # noqa: BLE001
            print(f"dispatch cards not sent: {type(exc).__name__}", file=sys.stderr)
        promoted = self.promote_pending(now)
        completions = self.flush_completions(now)
        self.expire_runs(now)
        status = self.status(views, now, rich)
        daily = self.daily(now)
        return {"consumed": consumed, "alerts_opened": promoted, "completions_sent": completions,
                "status": status, "daily": daily, "messages": self._sent_this_tick}
