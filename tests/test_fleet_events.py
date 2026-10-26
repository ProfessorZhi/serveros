"""Durable projection boundaries: loss, forged acknowledgement and secret retention."""
import pytest


def event(event_id="event-1", **updates):
    return {"event_id": event_id, "kind": "run.completed", "level": "notify",
            "device_ref": "device:work-fj", "runtime": "claude",
            "session_ref": "device:work-fj|claude|session-1", "run_id": "run-1",
            "dispatcher": '["pat","owner"]', "occurred_at": 100.0,
            "summary": {"status": "COMPLETED"}, **updates}


def store(path):
    from fleet_mcp.events import EventStore
    return EventStore(path)


def test_duplicate_event_and_ack_survive_reconstruction(tmp_path):
    path = tmp_path / "events.sqlite3"
    first = store(path)
    assert first.append(event()) == 1
    assert first.append(event()) == 1
    page = first.read("A", since=None, limit=10, visible=lambda _: True)
    assert page["events"][0]["event_id"] == "event-1"
    assert page["next_cursor"] == 1
    assert first.ack("A", 1) == 1
    rebuilt = store(path)
    assert rebuilt.read("A", since=None, limit=10, visible=lambda _: True)["events"] == []
    assert rebuilt.read("B", since=None, limit=10, visible=lambda _: True)["events"][0]["seq"] == 1
    with pytest.raises(ValueError):
        rebuilt.append(event(summary={"status": "FAILED"}))


@pytest.mark.parametrize("cursor", [True, -1, 1.5, "1"])
def test_invalid_cursors_refuse(tmp_path, cursor):
    s = store(tmp_path / "events.db")
    with pytest.raises(ValueError):
        s.read("A", since=cursor, limit=1, visible=lambda _: True)
    with pytest.raises(ValueError):
        s.ack("A", cursor)


@pytest.mark.parametrize("limit", [True, 0, -1, 201, 1.5])
def test_invalid_page_bounds_refuse(tmp_path, limit):
    with pytest.raises(ValueError):
        store(tmp_path / "events.db").read("A", since=None, limit=limit, visible=lambda _: True)


def test_ack_is_bound_to_each_subscribers_actual_scanned_range(tmp_path):
    s = store(tmp_path / "events.db")
    for i in range(3):
        s.append(event(f"e-{i}"))
    s.read("A", since=None, limit=1, visible=lambda _: True)
    for subscriber, cursor in [("A", 3), ("B", 1)]:
        with pytest.raises(ValueError):
            s.ack(subscriber, cursor)
    # A caller chosen start cannot acknowledge the gap it skipped.
    s.read("C", since=2, limit=1, visible=lambda _: True)
    with pytest.raises(ValueError):
        s.ack("C", 3)
    assert s.ack("A", 1) == 1


def test_hidden_pages_advance_without_disclosing_values(tmp_path):
    s = store(tmp_path / "events.db")
    s.append(event("hidden-secret"))
    s.append(event("visible"))
    page = s.read("A", since=None, limit=1, visible=lambda e: e["event_id"] == "visible")
    assert page["events"] == []
    assert page["next_cursor"] == 1
    assert "hidden-secret" not in str(page)
    assert page["has_more"] is True
    page = s.read("A", since=1, limit=1, visible=lambda _: True)
    assert page["events"][0]["event_id"] == "visible"
    assert s.ack("A", 2) == 2


@pytest.mark.parametrize("updates", [
    {"prompt": "secret"}, {"summary": {"text": "credential"}},
    {"summary": {"status": "Bearer abc"}}, {"event_id": "x" * 513},
    {"occurred_at": float("nan")}, {"summary": "raw transcript"},
])
def test_event_payload_is_closed_and_bounded(tmp_path, updates):
    with pytest.raises(ValueError):
        store(tmp_path / "events.db").append(event(**updates))

def test_observation_transaction_rolls_back_event_if_schedule_update_fails(tmp_path, monkeypatch):
    s = store(tmp_path / "events.db")
    s.discover_watch("watch", event(), first_seen=0, now=0)
    real_append = s._append
    def fail_after_insert(db, value):
        real_append(db, value)
        raise RuntimeError("fixture crash after event insertion")
    monkeypatch.setattr(s, "_append", fail_after_insert)
    with pytest.raises(RuntimeError):
        s.record_observation("watch", now=10, delay=600, event=event(), terminal=True)
    assert s.read("A", since=None, limit=10, visible=lambda _: True)["events"] == []
    assert len(s.due_watches(10)) == 1
    monkeypatch.setattr(s, "_append", real_append)
    s.record_observation("watch", now=10, delay=600, event=event(), terminal=True)
    assert len(s.read("A", since=None, limit=10, visible=lambda _: True)["events"]) == 1
    assert s.due_watches(10) == []


@pytest.mark.parametrize("updates", [{"summary": {"status": []}}, {"session_ref": "invalid"}])
def test_malformed_structures_refuse_as_value_errors(tmp_path, updates):
    with pytest.raises(ValueError):
        store(tmp_path / "events.db").append(event(**updates))
