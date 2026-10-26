"""Original-process CLI/MCP -> kernel peer -> real owner -> SQLite.

POSIX only: no fcntl or peer substitutions. Fixture contexts/policies are
issued using the existing owner test harness; every client call uses a socket.
"""
import os
import pytest

if os.name == "nt":
    pytest.skip("POSIX_ONLY: owner ledger and original SO_PEERCRED", allow_module_level=True)

import json
import shutil
import socket
import threading
from contextlib import contextmanager

import test_owner_atomic_operations as support
from workengine import cli, mcp
from workengine.model import Activity
from workengine.serveros_authority import UnixSocketOwnerClient


@contextmanager
def serving(daemon):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(daemon.socket_path))
    listener.listen(8)
    listener.settimeout(.1)
    stopped = threading.Event()
    def serve():
        while not stopped.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            with conn:
                daemon._serve_connection(conn)
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield UnixSocketOwnerClient(daemon.socket_path)
    finally:
        stopped.set()
        thread.join(5)
        listener.close()


def inspect_args(request):
    return {k: request[k] for k in ("context_ref", "grant_ref", "project", "item_ref", "expected_epoch", "verb")}


def pin(client, request):
    answer = client.inspect_target(**inspect_args(request))
    assert answer["verified"], answer
    return {**request, "expected_store_binding": answer["store_binding"]}


def flags(daemon, work, request, token):
    return ["--state-dir", str(work.state_dir), "--owner-socket", str(daemon.socket_path),
            "--context-ref", request["context_ref"], "--grant-ref", request["grant_ref"],
            "--expected-grant-epoch", str(request["expected_epoch"]), "--lease-token", str(token)]


def test_cli_and_mcp_route_real_lifecycle_and_dry_run(tmp_path, monkeypatch):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    work.owner_artifacts_root = tmp_path / "artifacts"
    captured = []
    monkeypatch.setattr(cli, "emit", lambda value, **_: captured.append(value))
    with serving(daemon) as client:
        request = pin(client, request)
        acquired = client.mutate(**request)
        assert acquired["committed"], acquired
        token = acquired["result"]["token"]
        common = flags(daemon, work, request, token)
        assert cli.main(common + ["--operation-id", "activity:1", "activity", "add", "SRV-0001", "build", "--kind", "develop"]) == 0
        assert cli.main(common + ["--operation-id", "run:1", "run", "begin", "SRV-0001", "build", "--worker", "worker:fixture", "--idempotency-key", "run-key:1"]) == 0
        run = work.require_run("SRV-0001", "RUN-0001")
        assert run.worker == "worker:fixture"
        assert os.path.isdir(run.artifact_dir)
        tool = mcp.WorkControlClient(state_dir=work.state_dir, owner_client=client)
        args = {"item": "SRV-0001", "run": run.id, "reason": "finished investigation",
                "context_ref": request["context_ref"], "grant_ref": request["grant_ref"],
                "expected_grant_epoch": request["expected_epoch"], "lease_token": token,
                "operation_id": "terminate:1", "dry_run": True}
        before = len(work.item_events("SRV-0001"))
        answer = tool.call("work.run.abandon", args)
        assert answer["state"] == "PLANNED", answer
        assert len(work.item_events("SRV-0001")) == before
        with work.connection() as conn:
            assert conn.execute("SELECT 1 FROM owner_operation_receipts WHERE operation_id='terminate:1'").fetchone() is None
        answer = tool.call("work.run.abandon", {**args, "dry_run": False})
        assert answer["state"] == "TERMINATED", answer
        assert work.require_run("SRV-0001", run.id).state.value == "CANCELLED"
        assert work.run_events("SRV-0001", run.id)[0]["payload"]["actor"] == "human:A"
        with work.connection() as conn:
            receipts = [json.loads(r[0]) for r in conn.execute("SELECT receipt_json FROM owner_operation_receipts")]
        assert all(r["os_principal"] == f"uid:{os.getuid()}" for r in receipts)


def test_raw_mutation_requires_target_pin_and_copy_is_not_same_store(tmp_path, monkeypatch):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    with serving(daemon) as client:
        assert client.mutate(**{k:v for k,v in request.items() if k != "expected_store_binding"})["committed"] is False
        request = pin(client, request)
        acquired = client.mutate(**request)
        assert acquired["committed"]
        copied = tmp_path / "copied"
        copied.mkdir()
        with work.connection() as conn:
            import sqlite3
            with sqlite3.connect(copied / "serveros.db") as target:
                conn.backup(target)
        outputs = []
        monkeypatch.setattr(cli, "emit", lambda value, **_: outputs.append(value))
        args = flags(daemon, work, request, acquired["result"]["token"])
        args[1] = str(copied)
        assert cli.main(args + ["--operation-id", "wrong-db", "item", "transition", "SRV-0001", "--to", "AVAILABLE"]) == 2
        assert "WORK_STORE_MISMATCH" in str(outputs)
        assert work.get_item("SRV-0001").state.value == "INBOX"


def test_historical_release_receipt_after_release_but_no_current_writer(tmp_path):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    with serving(daemon) as client:
        request = pin(client, support.issue(world))
        acquired = client.mutate(**request)
        release = {k:v for k,v in request.items() if k != "ttl_s"}
        release.update(verb="lease.release", operation_id="release:1", token=acquired["result"]["token"])
        assert client.mutate(**release)["committed"]
        replay = client.mutate(**release)
        assert replay["historical"] and replay["replayed"]
        assert work.active_lease("work-write:SRV-0001") is None
        changed = {**release, "verb":"item.transition", "operation_id":"after-release", "target":"AVAILABLE"}
        assert client.mutate(**changed)["committed"] is False
        assert support.revoke(world)["committed"]
        assert client.mutate(**release)["committed"] is False


def test_changed_assembly_pin_refuses_even_historical_receipt(tmp_path):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    with serving(daemon) as client:
        request = pin(client, support.issue(world))
        assert client.mutate(**request)["committed"]
        work.owner_artifacts_root = tmp_path / "different-artifacts"
        answer = client.mutate(**request)
        assert answer["committed"] is False
        assert answer["reason"] == "WORK_STORE_MISMATCH"


def test_unknown_reply_keeps_original_operation_key(tmp_path):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = tmp_path / "lost.sock"
    listener.bind(str(path)); listener.listen(1)
    seen = []
    def drop():
        conn, _ = listener.accept()
        with conn:
            seen.append(json.loads(conn.recv(65536)))
    thread = threading.Thread(target=drop); thread.start()
    try:
        answer = UnixSocketOwnerClient(path).mutate(operation_id="retained:key")
        assert answer["state"] == "UNKNOWN"
        assert answer["operation_id"] == "retained:key"
        thread.join(5)
        assert seen[0]["params"]["operation_id"] == "retained:key"
    finally:
        listener.close()
