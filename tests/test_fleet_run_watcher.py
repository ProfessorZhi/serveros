"""Observer tests use the real durable projection and a directed provider boundary."""
import pytest
from test_fleet_events import event, store


class Attempts:
    def __init__(self, **receipt):
        self.records = {"attempt-1": {"key": "attempt-1", "phase": "ACCEPTED",
            "verb": "session.send", "device_ref": "device:work-fj", "runtime": "claude",
            "principal": '["pat","owner"]', "recorded_at": "1970-01-01T00:00:00+00:00",
            "result": {"accepted": True, "session_ref": "device:work-fj|claude|session-1",
                       "run_id": "run-1", "queued_message_uuid": None, **receipt}}}
    def dispatch_attempts(self):
        return self.records


class Provider:
    def __init__(self):
        self.status = "running"
        self.error = None
        self.calls = []
    def get_run(self, key, run_id, *, wait=False, wait_seconds=0):
        assert key.ref == "device:work-fj|claude|session-1"
        assert run_id == "run-1"
        assert wait is False
        self.calls.append(run_id)
        if self.error:
            raise self.error
        return {"runId": run_id, "status": self.status, "output": "must never persist"}


class Service:
    def __init__(self):
        self.provider = Provider()
    def _session_provider(self, key):
        return self.provider
    def _verified_family_detail(self, key, provider):
        return None


def watcher(service, events, attempts, clock):
    from fleet_mcp.run_watcher import RunWatcher
    return RunWatcher(service, events, attempts, clock=clock)


def rows(events):
    return events.read("test", since=0, limit=100, visible=lambda _: True)["events"]


def test_ack_recovery_emits_terminal_once_across_restart(tmp_path):
    path = tmp_path / "events.db"
    s, a, now = Service(), Attempts(), [0]
    s.provider.status = "completed"
    w = watcher(s, store(path), a, lambda: now[0])
    assert s.provider.calls == []  # construction has no background work
    w.tick()
    w = watcher(s, store(path), a, lambda: now[0])
    w.tick()
    assert len(s.provider.calls) == 1
    result = rows(store(path))
    assert len(result) == 1
    assert result[0]["kind"] == "run.completed"
    assert result[0]["summary"] == {"status": "COMPLETED"}
    assert "must never persist" not in path.read_bytes().decode("latin1")


def test_provider_errors_back_off_and_unknown_can_later_be_corrected(tmp_path):
    e, s, a, now = store(tmp_path / "events.db"), Service(), Attempts(), [0]
    s.provider.error = RuntimeError("token must not be retained")
    w = watcher(s, e, a, lambda: now[0])
    for at in [0, 10, 40, 160, 760]:
        now[0] = at
        w.tick()
        assert len(s.provider.calls) == [0, 10, 40, 160, 760].index(at) + 1
        now[0] = at + 1
        w.tick()
    assert rows(e) == []
    now[0] = 86400
    w.tick()
    assert [r["kind"] for r in rows(e)] == ["run.unknown"]
    s.provider.error = None
    s.provider.status = "failed"
    now[0] += 600
    w = watcher(s, e, a, lambda: now[0])
    w.tick()
    assert [r["kind"] for r in rows(e)] == ["run.unknown", "run.corrected"]
    assert rows(e)[1]["summary"] == {"status": "FAILED", "previous_status": "UNKNOWN"}


def test_absent_run_stays_unresolved_and_malformed_receipts_are_reported(tmp_path):
    e, s = store(tmp_path / "events.db"), Service()
    a = Attempts(run_id=None, queued_message_uuid="queued-1")
    result = watcher(s, e, a, lambda: 0).tick()
    assert result["unresolved"] == 1
    assert s.provider.calls == []
    assert rows(e) == []
    a.records["attempt-1"]["result"]["session_ref"] = "wrong"
    result = watcher(s, e, a, lambda: 0).tick()
    assert result["malformed"] == 1


def test_wrong_run_identity_never_becomes_terminal(tmp_path):
    e, s = store(tmp_path / "events.db"), Service()
    s.provider.get_run = lambda *args, **kwargs: {"runId": "other", "status": "completed"}
    result = watcher(s, e, Attempts(), lambda: 0).tick()
    assert result["errors"] == 1
    assert rows(e) == []

def test_recovery_and_provider_observations_are_bounded_per_tick(tmp_path):
    import copy
    e, s, a = store(tmp_path / "events.db"), Service(), Attempts()
    original = a.records["attempt-1"]
    a.records = {f"attempt-{i:04}": copy.deepcopy(original) for i in range(201)}
    s.provider.status = "completed"
    w = watcher(s, e, a, lambda: 0)
    assert w.tick()["observed"] == 100
    assert w.tick()["observed"] == 100
    assert w.tick()["observed"] == 1
    assert len(s.provider.calls) == 201


def test_start_close_runs_owned_observer_and_stops_promptly(tmp_path):
    import threading
    e, s, a = store(tmp_path / "events.db"), Service(), Attempts()
    observed = threading.Event()
    original = s.provider.get_run
    def directed_read(*args, **kwargs):
        observed.set()
        return original(*args, **kwargs)
    s.provider.get_run = directed_read
    s.provider.status = "completed"
    w = watcher(s, e, a, lambda: 0)
    try:
        w.start()
        w.start()
        assert observed.wait(5)
    finally:
        w.close()
    assert rows(e)[0]["kind"] == "run.completed"
    assert w._thread is None


def test_multiple_observers_do_not_duplicate_terminal_facts(tmp_path):
    path = tmp_path / "events.db"
    e = store(path)
    e.discover_watch("watch", event(), first_seen=0, now=0)
    # Two processes may have read the same due row before either committed.
    e.record_observation("watch", now=1, delay=600, event=event(occurred_at=1), terminal=True)
    rebuilt = store(path)
    rebuilt.record_observation("watch", now=2, delay=600, event=event(occurred_at=2), terminal=True)
    assert len(rows(rebuilt)) == 1

def test_invalid_queue_identifiers_report_malformed_without_storage(tmp_path):
    e, s = store(tmp_path / "events.db"), Service()
    a = Attempts(run_id=None, queued_message_uuid={"prompt": "sensitive"})
    result = watcher(s, e, a, lambda: 0).tick()
    assert result["malformed"] == 1
    assert result["unresolved"] == 0
    assert rows(e) == []

@pytest.mark.parametrize("negative", [
    {"success": False}, {"exitCode": 2}, {"exit_code": -1},
    {"error": {"message": "sensitive provider detail"}},
    {"result": {"is_error": True}}, {"result": {"isError": True}},
    {"result": {"success": False}}, {"result": {"exitCode": 1}},
    {"result": {"error": "credential must not persist"}},
])
def test_completed_execution_with_failure_evidence_never_reports_success(tmp_path, negative):
    e, s = store(tmp_path / "events.db"), Service()
    s.provider.get_run = lambda *args, **kwargs: {"id": "run-1", "state": "completed", **negative}
    watcher(s, e, Attempts(), lambda: 0).tick()
    assert rows(e)[0]["kind"] == "run.failed"
    assert rows(e)[0]["summary"] == {"status": "FAILED"}
    assert "sensitive provider detail" not in str(rows(e))
    assert "credential must not persist" not in str(rows(e))


def test_missing_run_remains_unknown_and_never_resends(tmp_path):
    from fleet_mcp.errors import FleetRefusal
    e, s, now = store(tmp_path / "events.db"), Service(), [0]
    s.provider.error = FleetRefusal("RUN_NOT_FOUND", "the device lost its run index")
    w = watcher(s, e, Attempts(), lambda: now[0])
    assert w.tick()["errors"] == 1
    assert rows(e) == []
    now[0] = 86400
    w.tick()
    assert [r["kind"] for r in rows(e)] == ["run.unknown"]


def test_conflicting_terminal_states_are_reported_and_remain_nonterminal(tmp_path):
    e, s = store(tmp_path / "events.db"), Service()
    s.provider.get_run = lambda *args, **kwargs: {"state": "completed", "status": "failed"}
    assert watcher(s, e, Attempts(), lambda: 0).tick()["errors"] == 1
    assert rows(e) == []
