"""Durable owner notifications: the transport alone is a fake; SQLite is real."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_fleet_events import event, store

ROOT = Path(__file__).resolve().parents[1]
BINDING = {"app_id": "cli_fixture", "open_id": "ou_owner", "recipient": "user:ou_owner"}


def delivery_module():
    name = "serveros_runtime_fleet_event_delivery"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime/fleet_event_delivery.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class Sender:
    recipient = "user:ou_owner"
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent = []
        self.inspect = None
    def send_text(self, text, *, idempotency_key):
        if self.inspect:
            self.inspect(text, idempotency_key)
        self.sent.append((text, idempotency_key))
        answer = self.answers.pop(0) if self.answers else {
            "state": "DELIVERED", "provider": {"remote_id": "om_fixture"}}
        if isinstance(answer, Exception):
            raise answer
        return answer


def consumer(events, sender, now, **kwargs):
    return delivery_module().FleetEventDelivery(
        events, sender, BINDING, clock=lambda: now[0], cutover=100, **kwargs)


def append(events, ident, status="COMPLETED", **fields):
    events.append(event(ident, kind="run." + status.lower(),
        level="decide" if status in {"FAILED", "UNKNOWN"} else "notify",
        summary={"status": status}, dispatch_accepted_at=100, project="p-1", **fields))


def receipts(events):
    with sqlite3.connect(events.path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM fleet_notification_outbox ORDER BY rowid")]


def test_completion_batches_for_five_minutes_and_urgent_event_passes_it(tmp_path):
    # A coupled ingestion/settlement cursor would hide failure behind completion.
    e, sender, now = store(tmp_path / "events.db"), Sender(), [110]
    c = consumer(e, sender, now)
    append(e, "one")
    c.tick()
    now[0] = 200
    append(e, "two")
    append(e, "urgent", "FAILED")
    assert c.tick()["delivered"] == 1
    assert "failed" in sender.sent[0][0].lower()
    now[0] = 409
    assert c.tick()["delivered"] == 0
    now[0] = 410
    assert c.tick()["delivered"] == 1
    assert sender.sent[1][0].count("Turn completed") == 2
    assert e.read("fleet-owner-notification", since=None, limit=100, visible=lambda _: True)["events"] == []
    assert c.tick()["delivered"] == 0


def test_confirmed_receipt_and_cursor_survive_restart(tmp_path):
    e, sender, now = store(tmp_path / "events.db"), Sender(), [110]
    append(e, "failed", "FAILED")
    assert consumer(e, sender, now).tick()["delivered"] == 1
    rebuilt = consumer(store(e.path), sender, now)
    rebuilt.tick()
    assert len(sender.sent) == 1
    row = receipts(e)[0]
    assert row["state"] == "DELIVERED"
    assert row["remote_id"] == "om_fixture"


def test_intent_is_durable_before_io_and_unknown_retry_keeps_frozen_content(tmp_path):
    e, now = store(tmp_path / "events.db"), [110]
    sender = Sender(TimeoutError("uncertain"))
    c = consumer(e, sender, now)
    append(e, "one")
    c.tick()
    now[0] = 410
    def inspect(text, key):
        row = receipts(e)[0]
        assert row["state"] == "UNKNOWN"
        assert row["first_attempt"] == 410
        intent = json.loads(row["intent"])
        assert intent["event_ids"] == ["one"]
        assert intent["text"] == text
        assert intent["key"] == key
        assert intent["recipient"] == "user:ou_owner"
        assert len(intent["text_digest"]) == 64
    sender.inspect = inspect
    c.tick()
    sender.inspect = None
    assert receipts(e)[0]["state"] == "UNKNOWN"
    append(e, "two")
    now[0] = 440
    consumer(store(e.path), sender, now).tick()
    assert sender.sent[0] == sender.sent[1]
    assert json.loads(receipts(e)[0]["intent"])["event_ids"] == ["one"]
    now[0] = 740
    c.tick()
    assert len(sender.sent) == 3
    assert sender.sent[2][1] != sender.sent[0][1]


@pytest.mark.parametrize("answer", [TimeoutError("unknown"), {"state": "DELIVERED", "provider": {}}, {"state": "FAILED"}])
def test_uncertain_outcome_cannot_be_blindly_retried_after_dedupe_window(tmp_path, answer):
    e, sender, now = store(tmp_path / "events.db"), Sender(answer), [110]
    append(e, "urgent", "UNKNOWN")
    c = consumer(e, sender, now)
    c.tick()
    assert receipts(e)[0]["state"] == "UNKNOWN"
    now[0] = 3711
    consumer(store(e.path), sender, now).tick()
    assert len(sender.sent) == 1
    assert receipts(e)[0]["state"] == "UNKNOWN"


def test_explicit_not_sent_can_retry_after_the_window(tmp_path):
    e, sender, now = store(tmp_path / "events.db"), Sender({"state": "NOT_SENT", "retry_safe": True}), [110]
    append(e, "urgent", "FAILED")
    c = consumer(e, sender, now)
    c.tick()
    assert receipts(e)[0]["state"] == "NOT_SENT"
    now[0] = 4000
    c.tick()
    assert receipts(e)[0]["state"] == "DELIVERED"
    assert sender.sent[0] == sender.sent[1]


def test_cutover_uses_dispatch_acceptance_not_observation_time(tmp_path):
    e, sender, now = store(tmp_path / "events.db"), Sender(), [200]
    e.append(event("old", kind="run.failed", level="decide", summary={"status": "FAILED"}, dispatch_accepted_at=99, occurred_at=200))
    e.append(event("no-origin", kind="run.failed", level="decide", summary={"status": "FAILED"}, occurred_at=200))
    append(e, "boundary", "FAILED")
    consumer(e, sender, now).tick()
    assert len(sender.sent) == 1
    assert len(e.read("human", since=0, limit=100, visible=lambda _: True)["events"]) == 3
    with pytest.raises(ValueError, match="cutover"):
        delivery_module().FleetEventDelivery(e, sender, BINDING, cutover=99)


@pytest.mark.parametrize("binding,recipient", [
    ({**BINDING, "recipient": "chat:oc_group"}, "chat:oc_group"),
    ({**BINDING, "open_id": "ou_other"}, "user:ou_owner"),
    (BINDING, "user:ou_other"), ({}, "user:ou_owner")])
def test_wrong_audience_is_never_called(tmp_path, binding, recipient):
    e, sender = store(tmp_path / "events.db"), Sender()
    sender.recipient = recipient
    append(e, "urgent", "FAILED")
    c = delivery_module().FleetEventDelivery(e, sender, binding, cutover=100, clock=lambda: 110)
    assert c.tick()["status"] == "UNBOUND"
    assert sender.sent == []


def test_missing_cutover_is_disabled_and_does_not_consume(tmp_path):
    e, sender = store(tmp_path / "events.db"), Sender()
    append(e, "urgent", "FAILED")
    c = delivery_module().FleetEventDelivery(e, sender, BINDING)
    assert c.tick()["status"] == "DISABLED"
    assert sender.sent == []


def test_watcher_preserves_trusted_origin_separate_from_observation(tmp_path):
    from test_fleet_run_watcher import Service, Attempts, watcher, rows
    e, s, a = store(tmp_path / "events.db"), Service(), Attempts()
    a.records["attempt-1"]["accepted_at"] = "1970-01-01T00:01:40+00:00"
    s.provider.status = "failed"
    watcher(s, e, a, lambda: 200).tick()
    row = rows(e)[0]
    assert row["dispatch_accepted_at"] == 100
    assert row["occurred_at"] == 200


@pytest.mark.skipif(sys.platform == "win32", reason="existing owner uses POSIX fcntl; Linux CI covers actual owner")
def test_owned_ack_timestamp_is_written_by_owner(tmp_path):
    from test_fleet_owned_runtime import existing_store
    from fleet_mcp.dispatch import DispatchGuard
    from fleet_mcp.fleet import Principal
    owner = existing_store(tmp_path / "store.json")
    guard = DispatchGuard(owner)
    claim = guard.begin(Principal("owner", frozenset(), "pat"), "send", "target", "key", device_ref="device:work-fj", runtime="claude", session_ref="device:work-fj|claude|session-1", params={"message": "private"})
    guard.complete(claim, {"accepted": True, "session": {"session_ref": "device:work-fj|claude|session-1"}, "run_id": "run-1"})
    record = list(owner.dispatch_attempts().values())[0]
    assert record["accepted_at"].endswith("+00:00")
    assert "private" not in json.dumps(record)
    replay = guard.begin(Principal("owner", frozenset(), "pat"), "send", "target", "key",
        device_ref="device:work-fj", runtime="claude",
        session_ref="device:work-fj|claude|session-1", params={"message": "private"})
    assert replay.replay["run_id"] == "run-1"
    assert list(owner.dispatch_attempts().values())[0]["accepted_at"] == record["accepted_at"]

def test_ingestion_crash_before_ack_replays_queue_without_duplicate_delivery(tmp_path, monkeypatch):
    e, sender, now = store(tmp_path / "events.db"), Sender(), [110]
    append(e, "urgent", "FAILED")
    c = consumer(e, sender, now)
    monkeypatch.setattr(e, "ack", lambda *args: (_ for _ in ()).throw(RuntimeError("crash before ack")))
    with pytest.raises(RuntimeError, match="crash"):
        c.tick()
    consumer(store(e.path), sender, now).tick()
    assert len(sender.sent) == 1
    assert len(receipts(e)) == 1


def test_expired_unknown_receipts_do_not_starve_new_urgent_events(tmp_path):
    e, sender, now = store(tmp_path / "events.db"), Sender(), [110]
    c = consumer(e, sender, now)
    # Real durable expired receipts from previous interrupted calls.
    with sqlite3.connect(e.path) as db:
        for i in range(100):
            intent = {"event_ids": [f"old-{i}"], "recipient": "user:ou_owner", "text": "old", "text_digest": "0" * 64, "key": f"old-{i}"}
            db.execute("INSERT INTO fleet_notification_outbox(batch_key,intent,urgent,state,first_attempt) VALUES (?,?,1,'UNKNOWN',0)", (f"old-{i}", json.dumps(intent)))
    now[0] = 4000
    append(e, "new", "FAILED")
    assert c.tick()["delivered"] == 1
    assert len(sender.sent) == 1


def test_disabled_owned_configuration_does_not_read_binding_or_start_sender(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_fleet_owned_runtime import factory
    module = factory()
    monkeypatch.setattr(module, "_owner_notification_transport", lambda: pytest.fail("disabled cannot read live metadata"), raising=False)
    service = SimpleNamespace(events=store(tmp_path / "events.db"), event_delivery=None)
    module.configure_notifications(service)
    assert service.event_delivery is None


def test_enabled_owned_configuration_uses_verified_owner_and_bot_transport(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_fleet_owned_runtime import factory
    module, sender = factory(), Sender()
    monkeypatch.setattr(module, "_owner_notification_transport", lambda: (BINDING, sender), raising=False)
    service = SimpleNamespace(events=store(tmp_path / "events.db"), event_delivery=None)
    module.configure_notifications(service, enabled=True, cutover=100)
    append(service.events, "urgent", "FAILED")
    service.event_delivery.clock = lambda: 110
    assert service.event_delivery._thread is None
    assert service.event_delivery.tick()["delivered"] == 1


def test_enabled_missing_owner_binding_is_unbound_without_stopping_service(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_fleet_owned_runtime import factory
    module = factory()
    def unavailable():
        raise ValueError("unbound")
    monkeypatch.setattr(module, "_owner_notification_transport", unavailable, raising=False)
    service = SimpleNamespace(events=store(tmp_path / "events.db"), event_delivery=None)
    module.configure_notifications(service, enabled=True, cutover=100)
    assert service.event_delivery.tick()["status"] == "UNBOUND"


def test_stdio_notification_failure_does_not_stop_control_or_observer(tmp_path, monkeypatch, capsys):
    import io
    import fleet_mcp.runtime as runtime
    from test_fleet_create_session import Harness
    h, calls = Harness(tmp_path), []
    class Observer:
        def start(self): calls.append("observer-start")
        def close(self): calls.append("observer-close")
    class Consumer:
        last_result = None
        def start(self):
            calls.append("delivery-start")
            raise RuntimeError("delivery startup failed")
        def close(self): calls.append("delivery-close")
    h.service.run_watcher = Observer()
    h.service.event_delivery = Consumer()
    monkeypatch.setattr(runtime.sys, "stdin", io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))
    assert runtime.run_stdio(h.config, service=h.service) == 0
    assert json.loads(capsys.readouterr().out)["id"] == 1
    assert calls == ["observer-start", "delivery-start", "delivery-close", "observer-close"]
    assert h.service.event_delivery.last_result == {"status": "UNAVAILABLE"}


@pytest.mark.parametrize("origin", [None, "bad", "1970-01-01T00:01:40", "2999-01-01T00:00:00+00:00"])
def test_missing_or_malformed_origin_does_not_starve_observation(tmp_path, origin):
    from test_fleet_run_watcher import Service, Attempts, watcher, rows
    e, s, a = store(tmp_path / "events.db"), Service(), Attempts()
    a.records["attempt-1"]["accepted_at"] = origin
    s.provider.status = "failed"
    watcher(s, e, a, lambda: 200).tick()
    assert rows(e)[0]["occurred_at"] == 200
    assert "dispatch_accepted_at" not in rows(e)[0]


def test_owner_binding_refuses_multiple_apps_owners_or_credentials(tmp_path):
    from test_fleet_owned_runtime import factory
    live = factory()._load("serveros_runtime_live_sender", ROOT / "runtime/live_sender.py")
    config, credentials = tmp_path / "config", tmp_path / "credentials"
    credentials.mkdir()
    (credentials / "cli_aaaaaaaaaaa_ou_aaaaaaaaaaa.enc").touch()
    config.write_text('cli_aaaaaaaaaaa ou_aaaaaaaaaaa ou_bbbbbbbbbbb')
    with pytest.raises(live.LiveSenderError):
        live.owner_binding(config_path=config, credential_dir=credentials)
    config.write_text('cli_aaaaaaaaaaa cli_bbbbbbbbbbb ou_aaaaaaaaaaa')
    with pytest.raises(live.LiveSenderError):
        live.owner_binding(config_path=config, credential_dir=credentials)
    config.write_text('cli_aaaaaaaaaaa ou_aaaaaaaaaaa')
    (credentials / "cli_bbbbbbbbbbb_ou_bbbbbbbbbbb.enc").touch()
    with pytest.raises(live.LiveSenderError):
        live.owner_binding(config_path=config, credential_dir=credentials)


def test_not_sent_on_retry_cannot_erase_an_earlier_unknown_effect(tmp_path):
    e, sender, now = store(tmp_path / "events.db"), Sender(TimeoutError("ambiguous"), {"state": "NOT_SENT", "retry_safe": True}), [110]
    append(e, "urgent", "FAILED")
    c = consumer(e, sender, now)
    c.tick()
    now[0] = 140
    c.tick()
    assert receipts(e)[0]["state"] == "UNKNOWN"
    assert receipts(e)[0]["first_attempt"] == 110
    now[0] = 4000
    c.tick()
    assert len(sender.sent) == 2


def test_live_factory_binds_bot_transport_and_ignores_environment_recipient(monkeypatch):
    from types import SimpleNamespace
    from test_fleet_owned_runtime import factory
    module, calls = factory(), []
    def build_sender(recipient, **kwargs):
        calls.append((recipient, kwargs))
        return Sender()
    def load(name, path):
        if name == "serveros_runtime_live_sender":
            return SimpleNamespace(owner_binding=lambda: BINDING)
        assert name == "serveros_runtime_message_transport"
        return SimpleNamespace(sender=build_sender)
    monkeypatch.setattr(module, "_load", load)
    monkeypatch.setenv("SERVEROS_ALERT_RECIPIENT", "chat:oc_wrong")
    assert module._owner_notification_transport()[0] == BINDING
    assert calls == [("user:ou_owner", {"identity": "bot", "timeout": 20})]

def test_copied_runtime_closure_can_deliver_through_actual_transport_without_source_checkout(tmp_path):
    import shutil
    import subprocess
    for name in ("fleet_mcp_owner.py", "fleet_event_delivery.py", "notifications.py", "notification-contract.yaml", "live_sender.py", "message_transport.py"):
        destination = tmp_path / "runtime" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "runtime" / name, destination)
    (tmp_path / "fleet").mkdir()
    for name in ("transport.py", "transport-contract.yaml"):
        shutil.copyfile(ROOT / "fleet" / name, tmp_path / "fleet" / name)
    shutil.copytree(ROOT / "fleet_mcp", tmp_path / "fleet_mcp", ignore=shutil.ignore_patterns("__pycache__"))
    source = '''
import importlib.util,json,pathlib,sys
root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location("owned",root/"runtime/fleet_mcp_owner.py")
owned=importlib.util.module_from_spec(spec);spec.loader.exec_module(owned)
live=owned._load("serveros_runtime_live_sender",root/"runtime/live_sender.py")
live.owner_binding=lambda:{"app_id":"cli_fixture","open_id":"ou_owner","recipient":"user:ou_owner"}
transport=owned._load("serveros_runtime_message_transport",root/"runtime/message_transport.py")
calls=[]
def run(argv,**kwargs):
 calls.append((argv,kwargs));return {"data":{"message_id":"om_fixture"}}
transport.run=run
from fleet_mcp.events import EventStore
from types import SimpleNamespace
service=SimpleNamespace(events=EventStore(root/"events.db"),event_delivery=None)
owned.configure_notifications(service,enabled=True,cutover=100)
service.events.append({"event_id":"e1","kind":"run.failed","level":"decide","device_ref":"device:work-fj","runtime":"claude","session_ref":"device:work-fj|claude|session-1","run_id":"run-1","dispatcher":"owner","occurred_at":110,"dispatch_accepted_at":100,"summary":{"status":"FAILED"}})
service.event_delivery.clock=lambda:110
assert service.event_delivery.tick()["delivered"]==1
assert calls[0][1]=={"timeout":20}
argv=calls[0][0]
assert argv[argv.index("--as")+1]=="bot"
assert argv[argv.index("--user-id")+1]=="ou_owner"
assert "--idempotency-key" in argv
print("copied closure delivered to fake transport")
'''
    result = subprocess.run([sys.executable, "-I", "-c", source, str(tmp_path)], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "copied closure delivered to fake transport"

def test_late_uncertain_attempt_cannot_overwrite_a_confirmed_receipt(tmp_path):
    e, now = store(tmp_path / "events.db"), [110]
    second = consumer(store(e.path), Sender(), now)
    class SlowSender(Sender):
        def send_text(self, text, *, idempotency_key):
            now[0] = 140
            assert second.tick()["delivered"] == 1
            raise TimeoutError("late uncertain answer")
    first = consumer(e, SlowSender(), now)
    append(e, "urgent", "FAILED")
    first.tick()
    assert receipts(e)[0]["state"] == "DELIVERED"
    assert receipts(e)[0]["remote_id"] == "om_fixture"
