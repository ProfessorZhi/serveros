"""Inbox uses authenticated identity, current bindings, and stored delivery bounds."""
from dataclasses import replace
import pytest
from fleet_mcp.fleet import FleetService, Principal
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.tools import ToolContext, call_tool
from test_fleet_create_session import Harness
from test_fleet_events import event, store


def test_inbox_registered_read_and_scope_gate(tmp_path):
    h = Harness(tmp_path)
    service = FleetService(h.config, providers=h.providers, events=store(tmp_path / "events.db"))
    result = call_tool(ToolContext(service, h.principal()), "fleet_inbox", {})
    assert not result.get("isError")
    with pytest.raises(FleetRefusal) as caught:
        service.inbox(Principal("denied", frozenset(), "pat"))
    assert caught.value.code == "SCOPE_DENIED"


def test_unavailable_has_no_process_local_durability_claim(tmp_path):
    h = Harness(tmp_path)
    result = h.service.inbox(h.principal())
    assert result["status"] == "UNAVAILABLE"
    assert result["events"] == []


def test_current_disabled_binding_hides_previously_delivered_events(tmp_path):
    h = Harness(tmp_path)
    e = store(tmp_path / "events.db")
    e.append(event())
    s = FleetService(h.config, providers=h.providers, events=e)
    assert len(s.inbox(h.principal())["events"]) == 1
    config = replace(h.config, providers={k: replace(v, enabled=False) for k,v in h.config.providers.items()})
    s = FleetService(config, providers=h.providers, events=e)
    assert s.inbox(h.principal(), since=0)["events"] == []
    # A separate authenticated credential cannot acknowledge this subscriber's delivery.
    with pytest.raises(ValueError):
        e.ack('["oauth","another"]', 1)


def test_ack_parameter_is_boolean_and_read_ack_are_separate(tmp_path):
    h = Harness(tmp_path)
    e = store(tmp_path / "events.db")
    e.append(event())
    s = FleetService(h.config, providers=h.providers, events=e)
    assert len(s.inbox(h.principal())["events"]) == 1
    assert len(s.inbox(h.principal())["events"]) == 1
    page = s.inbox(h.principal(), ack=True)
    assert page["acknowledged_cursor"] == 1
    assert s.inbox(h.principal())["events"] == []
    with pytest.raises(ValueError):
        s.inbox(h.principal(), ack="yes")

def test_stdio_lifespan_starts_and_closes_injected_observer(tmp_path, monkeypatch):
    import io
    import fleet_mcp.runtime as runtime
    h = Harness(tmp_path)
    lifecycle = []
    class Observer:
        def start(self): lifecycle.append("start")
        def close(self): lifecycle.append("close")
    h.service.run_watcher = Observer()
    monkeypatch.setattr(runtime.sys, "stdin", io.StringIO(""))
    runtime.run_stdio(h.config, service=h.service)
    assert lifecycle == ["start", "close"]


def test_http_lifespan_closes_observer_when_server_fails(tmp_path, monkeypatch):
    import fleet_mcp.runtime as runtime
    h = Harness(tmp_path)
    lifecycle = []
    class Observer:
        def start(self): lifecycle.append("start")
        def close(self): lifecycle.append("close")
    class Server:
        def serve_forever(self):
            lifecycle.append("serve")
            raise RuntimeError("fixture transport failed")
        def server_close(self): lifecycle.append("server-close")
    h.service.run_watcher = Observer()
    monkeypatch.setattr(runtime, "build_http_surface", lambda *args: object())
    monkeypatch.setattr(runtime, "serve", lambda *args, **kwargs: Server())
    with pytest.raises(RuntimeError, match="fixture transport failed"):
        runtime.run_http(h.config, service=h.service)
    assert lifecycle == ["start", "serve", "close", "server-close"]


def test_visibility_seam_fails_closed_for_missing_project_evidence(tmp_path):
    h = Harness(tmp_path)
    e = store(tmp_path / "events.db")
    e.append(event())
    e.append(event("project-present", project="p-1"))
    class ProjectReadService(FleetService):
        def _event_visible(self, principal, event):
            return super()._event_visible(principal, event) and event.get("project") == "p-1"
    s = ProjectReadService(h.config, providers=h.providers, events=e)
    assert [row["event_id"] for row in s.inbox(h.principal())["events"]] == ["project-present"]

def test_owned_factory_injects_durable_projection_without_starting_thread(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_fleet_owned_runtime import factory
    from fleet_mcp.run_watcher import RunWatcher
    module = factory()
    ledger = tmp_path / "existing-owner" / "store.json"
    ledger.parent.mkdir()
    ledger.write_text("owner fixture boundary")
    class Owner:
        def __init__(self, path, *, require_existing):
            assert path == ledger
            assert require_existing is True
        def claim_dispatch_attempt(self, *args, **kwargs):
            raise AssertionError("factory cannot dispatch")
        def complete_dispatch_claim(self, *args, **kwargs):
            raise AssertionError("factory cannot dispatch")
        def dispatch_attempts(self):
            return {}
    monkeypatch.setattr(module, "control_store_path", lambda: ledger)
    monkeypatch.setattr(module, "_load", lambda *args: SimpleNamespace(AgentControlStore=Owner))
    h = Harness(tmp_path)
    service = module.build_service(h.config, providers=h.providers)
    assert isinstance(service.run_watcher, RunWatcher)
    assert service.run_watcher._thread is None
    service.events.append(event())
    rebuilt = module.build_service(h.config, providers=h.providers)
    assert rebuilt.inbox(h.principal())["events"][0]["event_id"] == "event-1"

def test_status_reports_projection_and_observer_health_without_private_data(tmp_path):
    h = Harness(tmp_path)
    assert h.service.status(h.principal())["event_projection"]["status"] == "UNAVAILABLE"
    e = store(tmp_path / "events.db")
    s = FleetService(h.config, providers=h.providers, events=e)
    from types import SimpleNamespace
    s.run_watcher = SimpleNamespace(last_result={"malformed": 1, "recovery_status": "UNSUPPORTED"})
    projection = s.status(h.principal())["event_projection"]
    assert projection["durability"] == "SQLITE"
    assert projection["observer"]["malformed"] == 1
