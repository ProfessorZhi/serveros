"""Durable derived Fleet notification outbox in the existing event projection.

Ingestion commits before inbox acknowledgement. Intent bytes and audience freeze
before transport IO. No grant, dispatch, or Work ownership is established here.
Ordinary construction never discovers credentials or starts a thread.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
import threading
import time
from pathlib import Path

SUBSCRIBER = "fleet-owner-notification"
# The real factory timeout is 20s. Leave that entire request bound inside the
# finite deduplication window declared by the existing message transport.
REQUEST_BOUND = 20
BATCH_SIZE = 10


def _runtime_module(name, filename):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _timestamp(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def _render(event):
    notifications = _runtime_module("serveros_runtime_notifications", "notifications.py")
    status = event["summary"]["status"]
    semantic = {"COMPLETED": "turn.completed", "FAILED": "turn.failed",
                "UNKNOWN": "session.waiting_input", "CANCELLED": "run.terminated"}[status]
    details = []
    if event.get("project"):
        details.append("Project: " + event["project"])
    if status == "UNKNOWN":
        details.append("Run outcome UNKNOWN; decision required.")
    if event["kind"] == "run.corrected":
        details.append("Correction after UNKNOWN: " + status)
    return notifications.render(notifications.Event(
        event=semantic, device_ref=event["device_ref"], at=str(event["occurred_at"]),
        correlation_id=event["event_id"], session_ref=event["session_ref"],
        run_ref=event["run_id"], reason=status, detail="\n".join(details) or None))


class FleetEventDelivery:
    def __init__(self, events, sender, binding, *, clock=time.time, cutover=None):
        self.events, self.sender, self.binding, self.clock = events, sender, binding, clock
        if cutover is not None and not _timestamp(cutover):
            raise ValueError("notification cutover must be a finite timestamp")
        self.cutover = cutover
        transport = _runtime_module("serveros_runtime_message_transport", "message_transport.py")
        self._dedupe_seconds = transport.IDEMPOTENCY_WINDOW_SECONDS
        self._stop = threading.Event()
        self._thread = None
        self._tick_lock = threading.Lock()
        self.last_result = {"status": "DISABLED" if cutover is None else "UNBOUND"}
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS fleet_notification_subscription (
                    subscriber TEXT PRIMARY KEY, cutover REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS fleet_notification_queue (
                    event_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    queued_at REAL NOT NULL, batch_key TEXT);
                CREATE TABLE IF NOT EXISTS fleet_notification_outbox (
                    batch_key TEXT PRIMARY KEY, intent TEXT NOT NULL,
                    urgent INTEGER NOT NULL, state TEXT NOT NULL,
                    first_attempt REAL, retry_at REAL NOT NULL DEFAULT 0,
                    remote_id TEXT);
                CREATE TABLE IF NOT EXISTS fleet_notification_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_key TEXT NOT NULL, attempted_at REAL NOT NULL,
                    state TEXT NOT NULL, remote_id TEXT);
                CREATE INDEX IF NOT EXISTS fleet_notification_queue_pending
                    ON fleet_notification_queue(batch_key,queued_at);
                CREATE INDEX IF NOT EXISTS fleet_notification_outbox_pending
                    ON fleet_notification_outbox(state,retry_at);
                CREATE INDEX IF NOT EXISTS fleet_notification_attempt_batch
                    ON fleet_notification_attempts(batch_key,attempt_id);
            """)
            db.execute("BEGIN IMMEDIATE")
            if cutover is not None:
                db.execute("INSERT OR IGNORE INTO fleet_notification_subscription VALUES (?,?)", (SUBSCRIBER, cutover))
                stored = db.execute("SELECT cutover FROM fleet_notification_subscription WHERE subscriber=?", (SUBSCRIBER,)).fetchone()[0]
                if stored != cutover:
                    raise ValueError("notification cutover is immutable across restarts")

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.events.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _recipient(self):
        binding = self.binding() if callable(self.binding) else self.binding
        if not isinstance(binding, dict):
            return None
        owner = binding.get("open_id")
        recipient = binding.get("recipient")
        if (not isinstance(owner, str) or not owner.startswith("ou_") or len(owner) > 200
                or not owner.replace("_", "").isalnum() or not binding.get("app_id")
                or recipient != "user:" + owner
                or getattr(self.sender, "recipient", None) != recipient):
            return None
        return recipient

    def _ingest(self, now):
        page = self.events.read(SUBSCRIBER, since=None, limit=100, visible=lambda _: True)
        with self._transaction() as db:
            for event in page["events"]:
                origin = event.get("dispatch_accepted_at")
                if _timestamp(origin) and self.cutover <= origin <= now:
                    db.execute("INSERT OR IGNORE INTO fleet_notification_queue(event_id,payload,queued_at) VALUES (?,?,?)",
                               (event["event_id"], _dump(event), now))
        # Crash here replays only unique queue rows; delivery settlement is not
        # a precondition for advancing the independent ingestion cursor.
        self.events.ack(SUBSCRIBER, page["next_cursor"])
        return len(page["events"])

    def _freeze(self, now, recipient):
        with self._transaction() as db:
            # A bounded working set; urgent items are selected before completions.
            rows = db.execute("SELECT * FROM fleet_notification_queue WHERE batch_key IS NULL ORDER BY json_extract(payload,'$.level')='decide' DESC,queued_at,rowid LIMIT 100").fetchall()
            groups = {}
            for row in rows:
                event = json.loads(row["payload"])
                urgent = event["level"] == "decide"
                # No project evidence: each completion remains its own group.
                group = ("urgent", row["event_id"]) if urgent else ("completion", event.get("project", row["event_id"]))
                groups.setdefault(group, []).append((row, event))
            for group, entries in list(groups.items())[:10]:
                urgent = group[0] == "urgent"
                if not urgent and now < entries[0][0]["queued_at"] + 300:
                    continue
                entries = entries[:BATCH_SIZE]
                text = "\n\n".join(_render(event) for _, event in entries)
                if len(text.encode("utf-8")) > 24000:
                    raise ValueError("notification intent exceeds byte bound")
                intent = {"event_ids": [row["event_id"] for row, _ in entries],
                          "text": text, "text_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                          "recipient": recipient}
                key = hashlib.sha256(_dump(intent).encode("utf-8")).hexdigest()[:50]
                intent["key"] = key
                db.execute("INSERT INTO fleet_notification_outbox(batch_key,intent,urgent,state) VALUES (?,?,?,'PENDING')",
                           (key, _dump(intent), int(urgent)))
                for row, _ in entries:
                    db.execute("UPDATE fleet_notification_queue SET batch_key=? WHERE event_id=?", (key, row["event_id"]))

    def _claim(self, now, recipient):
        with self._transaction() as db:
            candidates = db.execute("""SELECT * FROM fleet_notification_outbox
                WHERE state!='DELIVERED' AND retry_at<=?
                AND json_extract(intent,'$.recipient')=?
                AND (state!='UNKNOWN' OR first_attempt BETWEEN ? AND ?)
                ORDER BY urgent DESC,rowid LIMIT 100""",
                (now, recipient, now - self._dedupe_seconds + REQUEST_BOUND, now)).fetchall()
            for row in candidates:
                intent = json.loads(row["intent"])
                if intent["recipient"] != recipient:
                    continue  # A binding change never redirects a frozen intent.
                first = row["first_attempt"]
                if row["state"] == "UNKNOWN" and (first is None or not 0 <= now - first <= self._dedupe_seconds - REQUEST_BOUND):
                    continue
                if row["state"] == "NOT_SENT" or first is None:
                    first = now
                db.execute("UPDATE fleet_notification_outbox SET state='UNKNOWN',first_attempt=?,retry_at=? WHERE batch_key=?", (first, now + 30, row["batch_key"]))
                attempt = db.execute("INSERT INTO fleet_notification_attempts(batch_key,attempted_at,state) VALUES (?,?,'UNKNOWN')", (row["batch_key"], now)).lastrowid
                return intent, attempt, row["state"]
        return None

    def tick(self):
        with self._tick_lock:
            result = {"status": "DISABLED", "ingested": 0, "delivered": 0}
            if self.cutover is None:
                self.last_result = result
                return result
            now = self.clock()
            if not _timestamp(now):
                raise ValueError("invalid notification clock")
            result["ingested"] = self._ingest(now)
            try:
                recipient = self._recipient()
            except Exception:
                recipient = None
            if not recipient:
                result["status"] = "UNBOUND"
                self.last_result = result
                return result
            self._freeze(now, recipient)
            for _ in range(8):
                if self._stop.is_set():
                    break
                # Each bounded IO call gets a fresh time check; earlier sends
                # cannot carry a later retry past the deduplication deadline.
                attempt_time = self.clock()
                if not _timestamp(attempt_time):
                    raise ValueError("invalid notification clock")
                claim = self._claim(attempt_time, recipient)
                if claim is None:
                    break
                intent, attempt, prior_state = claim
                try:
                    answer = self.sender.send_text(intent["text"], idempotency_key=intent["key"])
                except Exception:
                    answer = {}  # IO exceptions prove neither delivery nor not-sent.
                if not isinstance(answer, dict):
                    answer = {}
                provider = answer.get("provider")
                remote = provider.get("remote_id") if isinstance(provider, dict) else None
                confirmed = (answer.get("state") == "DELIVERED" and isinstance(remote, str)
                             and 0 < len(remote) <= 512 and remote.strip() == remote
                             and all(ord(c) >= 32 for c in remote))
                state = "DELIVERED" if confirmed else "UNKNOWN"
                not_sent = answer.get("state") == "NOT_SENT" and answer.get("retry_safe") is True
                # A safe refusal of THIS retry cannot disprove an earlier
                # uncertain effect. Only a confirmed receipt settles that.
                if not_sent and prior_state != "UNKNOWN":
                    state = "NOT_SENT"
                with self._transaction() as db:
                    # Multiple owned launchers can overlap during a restart.
                    # Confirmation is sticky; an older timeout/refusal cannot
                    # replace a newer attempt's evidence or reopen delivery.
                    db.execute("""UPDATE fleet_notification_outbox SET state=?,remote_id=?
                        WHERE batch_key=? AND (?='DELIVERED' OR
                        (state!='DELIVERED' AND ?=(SELECT MAX(attempt_id)
                        FROM fleet_notification_attempts WHERE batch_key=?)))""",
                        (state, remote if confirmed else None, intent["key"], state, attempt, intent["key"]))
                    db.execute("UPDATE fleet_notification_attempts SET state=?,remote_id=? WHERE attempt_id=?", ("NOT_SENT" if not_sent else state, remote if confirmed else None, attempt))
                result["delivered"] += int(confirmed)
            with self._transaction() as db:
                result["unknown"] = db.execute("SELECT COUNT(*) FROM fleet_notification_outbox WHERE state='UNKNOWN'").fetchone()[0]
                result["pending"] = db.execute("SELECT COUNT(*) FROM fleet_notification_queue q LEFT JOIN fleet_notification_outbox o ON q.batch_key=o.batch_key WHERE o.state IS NULL OR o.state!='DELIVERED'").fetchone()[0]
            result["status"] = "UNKNOWN" if result["unknown"] else "ACTIVE"
            self.last_result = result
            return result

    def _run(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                self.last_result = {"status": "UNAVAILABLE"}
            self._stop.wait(1)

    def start(self):
        if self.cutover is None or self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fleet-owner-notification", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                raise RuntimeError("notification consumer did not stop within its shutdown bound")
            self._thread = None
