"""Delivery identity is independent of queue and Run identity (AC-386)."""
import copy
import os

import pytest

from fleet_mcp.dispatch import DispatchGuard, delivery_receipt
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from test_fleet_run_watcher import Attempts, Service, rows, store, watcher
from test_fleet_mcp_cloudcli_provider import FakeClient, provider

KEY = SessionKey.parse("device:work-fj|claude|session-1")


def delivery(**changes):
    return {"idKind": "delivery", "deliveryId": "delivery-1", "sessionId": "session-1",
            "runId": None, "status": "running", "bootId": "boot-1", **changes}


def test_receipt_preserves_delivery_separately_and_reconstructs_without_resend():
    from fleet_mcp.fleet import Principal
    p = Principal("owner", frozenset(), "pat")
    g = DispatchGuard()
    args = dict(device_ref=KEY.device_ref, runtime=KEY.provider, session_ref=KEY.ref, params={})
    c = g.begin(p, "send", KEY.ref, "key", **args)
    record = {"display_name": KEY.as_dict(), "run_id": None,
              "delivery_id": "delivery-1", "queued_message_uuid": "queue-2"}
    g.complete(c, record)
    rebuilt = DispatchGuard()
    rebuilt._records = copy.deepcopy(g._records)
    replay = rebuilt.begin(p, "send", KEY.ref, "key", **args).replay
    assert replay["delivery_id"] == "delivery-1"
    assert replay["run_id"] is None
    assert replay["queued_message_uuid"] == "queue-2"
    assert delivery_receipt(record)["delivery_id"] == "delivery-1"


def test_old_receipt_does_not_acquire_delivery_field():
    assert delivery_receipt({"session": KEY.as_dict(), "run_id": "old"}) == {
        "session_ref": KEY.ref, "run_id": "old", "queued_message_uuid": None, "accepted": True}


@pytest.mark.parametrize("field", ["delivery_id", "run_id", "queued_message_uuid"])
@pytest.mark.parametrize("value", ["", "x\n", "x" * 513, {}])
def test_receipt_rejects_invalid_execution_identifiers(field, value):
    with pytest.raises(ValueError):
        delivery_receipt({"session": KEY.as_dict(), field: value})


def test_cloudcli_checks_actual_delivery_capability_before_calling():
    client = FakeClient(responses={"run_get": delivery()})
    with pytest.raises(FleetRefusal) as error:
        provider(client).get_delivery(KEY, "delivery-1")
    assert error.value.code == "CAPABILITY_UNSUPPORTED"
    assert client.calls == []


def delivery_client(payload=None):
    client = FakeClient(responses={"run_get": delivery() if payload is None else payload})
    client.tools_list = lambda: [{"name": "run_get", "inputSchema": {
        "type": "object", "properties": {"runId": {"type": "string", "minLength": 1},
        "deliveryId": {"type": "string", "minLength": 1}, "session": {"type": "string"},
        "waitSeconds": {"type": "number"}}, "additionalProperties": False,
        "oneOf": [{"required": ["runId"]}, {"required": ["deliveryId"]}]}}]
    return client


def test_cloudcli_delivery_uses_the_native_namespace_and_session():
    client = delivery_client()
    assert provider(client).get_delivery(KEY, "delivery-1")["deliveryId"] == "delivery-1"
    assert client.calls == [("run_get", {"deliveryId": "delivery-1", "session": "session-1"})]


def test_cloudcli_delivery_refuses_another_device_before_contacting_provider():
    client = delivery_client()
    with pytest.raises(FleetRefusal):
        provider(client).get_delivery(SessionKey.compose("device:tokyo-relay", "claude", "session-1"), "delivery-1")
    assert client.calls == []


def test_native_delivery_arm_checks_response_identity_and_requires_session():
    p = provider(delivery_client(delivery(sessionId="other")))
    with pytest.raises(FleetRefusal):
        p.native_call("run_get", {"deliveryId": "delivery-1", "session": "session-1"})
    client = delivery_client()
    with pytest.raises(FleetRefusal):
        provider(client).native_call("run_get", {"deliveryId": "delivery-1"})
    assert client.calls == []


@pytest.mark.parametrize("changes", [{"sessionId": "other"}, {"deliveryId": "other"},
                                       {"idKind": "run"}, {"sessionId": None}])
def test_cloudcli_refuses_misbound_delivery_response(changes):
    with pytest.raises(FleetRefusal):
        provider(delivery_client(delivery(**changes))).get_delivery(KEY, "delivery-1")


def test_delivery_profile_accepts_native_family_but_refuses_a_different_family():
    from dataclasses import replace
    from fleet_mcp import cloudcli_native as native
    key = SessionKey.compose(KEY.device_ref, "codex-owner", "session-1")
    p = provider(delivery_client(delivery(provider="codex")))
    p.binding = replace(p.binding, runtime="codex-owner", family="codex")
    assert p.get_delivery(key, "delivery-1")["provider"] == "codex"
    p._client = lambda: delivery_client(delivery(provider="claude"))
    with pytest.raises(FleetRefusal):
        p.get_delivery(key, "delivery-1")


def test_delivery_refuses_conflicting_run_aliases():
    with pytest.raises(FleetRefusal):
        provider(delivery_client(delivery(runId="bound", run_id="other"))).get_delivery(KEY, "delivery-1")


@pytest.mark.parametrize("args", [{}, {"run_id": "r", "delivery_id": "d"},
                                  {"delivery_id": ""}, {"run_id": "r", "delivery_id": None}])
def test_authored_lookup_requires_exactly_one_nonempty_identifier(args):
    from fleet_mcp.tools import SERVED_BY_NAME, ToolContext
    with pytest.raises(FleetRefusal) as error:
        SERVED_BY_NAME["fleet_get_run"].handler(ToolContext(None, None), args)
    assert error.value.code == "BAD_REQUEST"


def delivery_service(response):
    s = Service()
    s.provider.delivery = response
    s.provider.get_delivery = lambda key, value: copy.deepcopy(s.provider.delivery)
    s.provider.get_run = lambda key, value, **kw: {
        "idKind": "run", "runId": value, "sessionId": "session-1",
        "bootId": "boot-1", "status": "running"}
    return s


def test_delivery_completion_waits_for_its_bound_run_result_across_restart(tmp_path):
    path, now = tmp_path / "events.db", [0]
    s = delivery_service(delivery(runId="bound-run", status="completed"))
    a = Attempts(run_id=None, delivery_id="delivery-1", queued_message_uuid="different-queue")
    w = watcher(s, store(path), a, lambda: now[0])
    assert w.tick()["observed"] == 1
    assert rows(store(path)) == []
    # A conflicting later delivery cannot silently replace its persisted binding.
    now[0] = 10
    s.provider.delivery["runId"] = "unrelated-current-run"
    s.provider.get_run = lambda *args, **kw: {"status": "completed"}
    w = watcher(s, store(path), a, lambda: now[0])
    assert w.tick()["errors"] == 1
    assert rows(store(path)) == []
    s.provider.delivery["runId"] = "bound-run"
    s.provider.get_run = lambda *args, **kw: {"idKind": "run", "runId": "bound-run",
        "sessionId": "session-1", "bootId": "boot-1", "status": "completed"}
    now[0] = 40
    w.tick()
    w.tick()
    assert [(r["kind"], r["run_id"], r["delivery_id"]) for r in rows(store(path))] == [
        ("run.completed", "bound-run", "delivery-1")]


@pytest.mark.parametrize("response", [delivery(status="completed"),
    delivery(runId="other", status="completed"), delivery(sessionId="other"),
    delivery(deliveryId="queue-1"), delivery(idKind="run")])
def test_missing_or_misbound_delivery_stays_unknown_and_never_resends(tmp_path, response):
    e, s, now = store(tmp_path / "events.db"), delivery_service(response), [0]
    a = Attempts(run_id="expected-run", delivery_id="delivery-1")
    w = watcher(s, e, a, lambda: now[0])
    w.tick()
    assert rows(e) == []
    now[0] = 86400
    w.tick()
    assert [r["kind"] for r in rows(e)] == ["run.unknown"]


def test_two_deliveries_in_same_run_emit_distinct_events_and_cancel_needs_no_run(tmp_path):
    e, s, a = store(tmp_path / "events.db"), delivery_service(delivery()), Attempts()
    a.records["attempt-1"]["result"].update(run_id=None, delivery_id="delivery-1")
    a.records["attempt-2"] = copy.deepcopy(a.records["attempt-1"])
    a.records["attempt-2"]["result"]["delivery_id"] = "delivery-2"
    s.provider.get_delivery = lambda key, value: delivery(deliveryId=value, status="cancelled")
    w = watcher(s, e, a, lambda: 0)
    w.tick()
    w.tick()
    assert sorted((r["delivery_id"], r["kind"], r["run_id"]) for r in rows(e)) == [
        ("delivery-1", "run.cancelled", None), ("delivery-2", "run.cancelled", None)]


def test_two_completed_deliveries_in_one_run_do_not_collapse_events(tmp_path):
    e, s, a = store(tmp_path / "events.db"), delivery_service(delivery()), Attempts()
    a.records["attempt-1"]["result"].update(run_id="shared", delivery_id="delivery-1")
    a.records["attempt-2"] = copy.deepcopy(a.records["attempt-1"])
    a.records["attempt-2"]["result"]["delivery_id"] = "delivery-2"
    s.provider.get_delivery = lambda key, value: delivery(deliveryId=value, runId="shared", status="completed")
    s.provider.get_run = lambda *args, **kw: {"idKind": "run", "runId": "shared", "sessionId": "session-1",
                                           "bootId": "boot-1", "status": "completed"}
    w = watcher(s, e, a, lambda: 0)
    w.tick()
    w.tick()
    assert sorted((r["delivery_id"], r["run_id"], r["kind"]) for r in rows(e)) == [
        ("delivery-1", "shared", "run.completed"), ("delivery-2", "shared", "run.completed")]


@pytest.mark.parametrize("run", [{"status": "completed"},
    {"idKind": "delivery", "runId": "bound", "sessionId": "session-1", "status": "completed"},
    {"idKind": "run", "runId": "bound", "sessionId": "other", "status": "completed"},
    {"idKind": "run", "runId": "bound", "sessionId": "session-1", "bootId": "restarted",
     "status": "completed"}])
def test_delivery_requires_trustworthy_bound_run_not_session_current_run(tmp_path, run):
    e, s = store(tmp_path / "events.db"), delivery_service(delivery(runId="bound", status="completed"))
    s.provider.get_run = lambda *args, **kw: run
    assert watcher(s, e, Attempts(run_id=None, delivery_id="delivery-1"), lambda: 0).tick()["errors"] == 1
    assert rows(e) == []


def test_send_exposes_and_replays_delivery_only_ack(tmp_path):
    from test_fleet_mcp_surface import Harness, TOKYO
    key = SessionKey.compose(TOKYO, "claude", "owned-session")
    h = Harness(tmp_path, send_allow=(key.ref,), allow_tokyo_send=True)
    calls = []
    def send(*args, **kwargs):
        calls.append(True)
        return {"deliveryId": "delivery-only", "runId": None, "queuedMessageUuid": None}
    h.tokyo.send = send
    first = h.service.send_to_session(h.principal(), key, message="go", idempotency_key="id")
    second = h.service.send_to_session(h.principal(), key, message="go", idempotency_key="id")
    assert first["delivery_id"] == second["delivery_id"] == "delivery-only"
    assert first["run_id"] is None
    assert calls == [True]


def test_create_exposes_delivery_ack_and_replays(tmp_path):
    from test_fleet_create_session import Harness, TOKYO
    h = Harness(tmp_path)
    h.providers[TOKYO].create = lambda **kw: {"sessionId": "created", "deliveryId": "delivery-new"}
    for _ in range(2):
        result = h.service.create_session(h.principal(), device=TOKYO, project="p-1",
                                          message="go", idempotency_key="create")
        assert result["delivery_id"] == "delivery-new"


@pytest.mark.skipif(os.name == "nt", reason="existing owner uses POSIX file locks")
def test_delivery_only_ack_survives_real_owner_reconstruction(tmp_path):
    from test_fleet_dispatch_claims import owner_store
    from fleet_mcp.fleet import Principal
    path = tmp_path / "owner.json"
    args = dict(device_ref=KEY.device_ref, runtime=KEY.provider, session_ref=KEY.ref, params={})
    p = Principal("owner", frozenset(), "pat")
    guard = DispatchGuard(owner_store(path))
    c = guard.begin(p, "send", KEY.ref, "id", **args)
    guard.complete(c, {"display_name": KEY.as_dict(), "run_id": None, "delivery_id": "d"})
    replay = DispatchGuard(owner_store(path)).begin(p, "send", KEY.ref, "id", **args).replay
    assert replay["delivery_id"] == "d"


def test_native_run_schema_and_projection_enforce_exactly_one_id():
    from fleet_mcp import cloudcli_native as native
    schemas = [native.input_schema("run_get"),
        native.mirrored_schema(native.NATIVE_BY_NAME["run_get"])["properties"]["arguments"]]
    for schema in schemas:
        assert native.validate_arguments({"deliveryId": "d", "session": "s"}, schema) == []
        assert native.validate_arguments({}, schema)
        assert native.validate_arguments({"runId": "r", "deliveryId": "d"}, schema)


def test_service_delivery_lookup_reuses_current_read_and_family_gates(tmp_path):
    from test_fleet_mcp_surface import Harness, TOKYO
    from fleet_mcp.fleet import Principal
    from fleet_mcp.tools import ToolContext, call_tool
    h = Harness(tmp_path)
    key = SessionKey.compose(TOKYO, "claude", "owned-session")
    calls = []
    def get_delivery(key, value):
        calls.append((key.ref, value))
        return delivery(sessionId=key.native_session_id)
    h.tokyo.get_delivery = get_delivery
    args = {"session_ref": key.ref, "delivery_id": "delivery-1"}
    denied = call_tool(ToolContext(h.service, Principal("reader", frozenset(), "pat")),
                       "fleet_get_run", args)
    assert denied["structuredContent"]["code"] == "SCOPE_DENIED"
    assert calls == []
    answer = call_tool(ToolContext(h.service, h.principal()), "fleet_get_run", args)
    assert not answer.get("isError")
    assert answer["structuredContent"]["delivery_id"] == "delivery-1"
    assert calls == [(key.ref, "delivery-1")]


def test_existing_sqlite_schedule_events_and_cursor_survive_binding_migration(tmp_path):
    import json
    import sqlite3
    from test_fleet_events import event
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE watches(watch_id TEXT PRIMARY KEY,payload TEXT NOT NULL,first_seen REAL NOT NULL,"
               "next_poll REAL NOT NULL,polls INTEGER NOT NULL DEFAULT 0,unknown_emitted INTEGER NOT NULL DEFAULT 0,"
               "terminal INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO watches VALUES (?,?,?,?,?,?,?)", ("old", json.dumps(event()), 0, 123, 4, 1, 0))
    db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE,payload TEXT NOT NULL)")
    db.execute("INSERT INTO events VALUES (?,?,?)", (1, "event-1", json.dumps(event())))
    db.execute("CREATE TABLE subscribers(subscriber TEXT PRIMARY KEY,acknowledged INTEGER NOT NULL DEFAULT 0)")
    db.execute("INSERT INTO subscribers VALUES (?,?)", ("existing", 1))
    db.commit()
    db.close()
    e = store(path)
    e.bind_delivery("old", "bound", "boot")
    rebuilt = store(path)
    row = rebuilt.due_watches(123)[0]
    assert (row["polls"], row["unknown_emitted"], row["next_poll"]) == (4, 1, 123)
    assert row["bound_run_id"] == "bound"
    assert json.loads(row["payload"])["run_id"] == "run-1"
    assert rebuilt.read("existing", since=None, limit=10, visible=lambda _: True)["events"] == []
    with pytest.raises(ValueError):
        rebuilt.bind_delivery("old", "other", "boot")


@pytest.mark.parametrize("kind,status,level", [("run.completed", "COMPLETED", "notify"),
                                               ("run.failed", "FAILED", "decide")])
def test_unbound_delivery_cannot_be_a_work_result_event(tmp_path, kind, status, level):
    from test_fleet_events import event
    with pytest.raises(ValueError):
        store(tmp_path / "events.db").append(event(run_id=None, delivery_id="delivery-1",
            kind=kind, summary={"status": status}, level=level))


@pytest.mark.parametrize("namespace", ["claude", "codex"])
def test_node_delivery_uses_existing_signed_native_route(tmp_path, namespace):
    import threading
    from fleet_mcp.config import ProviderBinding
    from fleet_mcp.node.connector import Connector, NodeConfig, serve
    from fleet_mcp.providers.fleet_node import FleetNodeProvider
    secret, token = tmp_path / "node.secret", tmp_path / "cloudcli.token"
    secret.write_text("fixture-secret")
    token.write_text("fixture-token")
    key = SessionKey.compose(KEY.device_ref, namespace, "session-1")
    config = NodeConfig(device_ref=KEY.device_ref, runtime=namespace, secret_anchor=secret,
        cloudcli_endpoint="http://127.0.0.1:1/mcp", cloudcli_credential_anchor=token, port=0)
    connector = Connector(config)
    connector.provider = provider(delivery_client(delivery(provider=namespace)))
    server = serve(config, connector=connector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        p = FleetNodeProvider(ProviderBinding(device_ref=KEY.device_ref, runtime=namespace, kind="fleet-node",
            endpoint=f"http://127.0.0.1:{server.server_address[1]}", credential_anchor=secret))
        assert p.get_delivery(key, "delivery-1")["deliveryId"] == "delivery-1"
        connector.provider = provider(FakeClient())
        with pytest.raises(FleetRefusal) as error:
            p.get_delivery(key, "delivery-1")
        assert error.value.code == "CAPABILITY_UNSUPPORTED"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
