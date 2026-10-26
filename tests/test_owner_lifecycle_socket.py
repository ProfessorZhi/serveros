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
import subprocess
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


def test_managed_publication_refuses_before_creating_parent_or_lock(tmp_path):
    from workengine.federation import publish_inventory
    from test_owner_work_store_fence import _enroll_fixture
    world = support.make_world(tmp_path)
    _, work, _, _ = world
    work.add_activity(Activity("SRV-0001", "inventory", "inspect"))
    run = work.begin_run("SRV-0001", "inventory", "worker", "inventory", tmp_path / "artifacts")
    _enroll_fixture(work, "SRV-0001")
    source = tmp_path / "inventory.jsonl"
    source.write_text('{"object_id":"r1","kind":"repository","locator":"fixture","observed_at":"2026-09-09T10:00:00Z"}\n')
    target = tmp_path / "must-not-exist" / "current.jsonl"
    with pytest.raises(Exception, match="OWNER_COMMIT_FENCE_REQUIRED"):
        publish_inventory("github", source, target, "SRV-0001/" + run.id,
                          work.record_publication_receipt, work.get_publication_receipt)
    assert not target.parent.exists()


@pytest.mark.parametrize("fault", [None, "revision", "wrong_repo", "scope", "expired", "ambiguous", "symlink", "fsmonitor"])
def test_context_attach_measures_git_and_uses_original_wake_producer(tmp_path, fault):
    from workengine.owner_context import _runtime
    world = support.make_world(tmp_path)
    daemon, work, context, policy = world
    policy["actions"].append("engineering.execute")
    workspace = tmp_path / "workspace"
    repo = workspace / "worktrees" / "serveros" / "SRV-0001"
    repo.mkdir(parents=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    (repo / "fixture.txt").write_text("fixture")
    git("add", "fixture.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    revision = git("rev-parse", "HEAD")
    monitor_effect = tmp_path / "must-not-run-fsmonitor"
    if fault == "fsmonitor":
        hook = repo / ".git" / "fixture-monitor"
        hook.write_text("#!/bin/sh\ntouch '" + str(monitor_effect) + "'\n")
        hook.chmod(0o700)
        git("config", "core.fsmonitor", str(hook))
    catalog = workspace / "ops/workspace/serveros/projects.yaml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("projects:\n  serveros:\n    worktrees_under: serveros\n    canonical_repos: [serveros]\n")
    work.owner_workspace_root = workspace
    from pathlib import Path
    work.owner_git_executable = Path(shutil.which("git")).resolve()
    work.owner_artifacts_root = tmp_path / "artifacts"
    work.owner_continuation_receipts_root = tmp_path / "wake-receipts"
    work.add_activity(Activity("SRV-0001", "build", "develop"))
    request = support.issue(world)
    control = daemon.control_store()
    with control._mutation():
        grant = control._data["grants"][request["grant_ref"]]
        grant["budget"] = {"wall_seconds": 120, "max_attempts": 2, "exhausted": False}
        grant["engineering"] = {"schema":"serveros/engineering-binding.v1", "worktree":str(repo), "image":"sha256:" + "a" * 64,
                                "memory_bytes": 67108864, "cpus": 1, "pids_limit": 16}
        control._flush()
    wake, continuation = _runtime("wake"), _runtime("continuation")
    now = support.support.iso(daemon)
    checkpoint = continuation.build_checkpoint(work_item="SRV-0001", epoch=1, parent_owner="parent:fixture",
        next_safe_action="inspect the fixture", waiting_on={"kind":"window", "until":support.support.iso(daemon, -1)},
        write_scope=[str(repo)], now=support.support.iso(daemon, -30))
    control.put_checkpoint(checkpoint)
    spend = wake.spending(grant["budget"], item="SRV-0001", grant_ref=request["grant_ref"])
    key = wake.action_key("SRV-0001", 1, 1)
    envelope = wake.capability_envelope(checkpoint, grant, request["grant_ref"], attempt=1, key=key, spend=spend,
                                      now=support.support.iso(daemon, -300) if fault == "expired" else now)
    wake.write_envelope(wake.envelope_path(work.owner_continuation_receipts_root, key), envelope)
    if fault == "ambiguous":
        key2 = wake.action_key("SRV-0001", 1, 2)
        wake.write_envelope(wake.envelope_path(work.owner_continuation_receipts_root, key2),
            wake.capability_envelope(checkpoint, grant, request["grant_ref"], attempt=2, key=key2, spend=spend, now=now))
    if fault == "scope":
        with control._mutation():
            control._data["checkpoints"][checkpoint["checkpoint_ref"]]["write_scope"] = [str(tmp_path / "other")]
            control._flush()
    if fault == "symlink":
        moved = workspace / "worktrees" / "moved"
        repo.rename(moved)
        repo.symlink_to(moved, target_is_directory=True)
    with serving(daemon) as client:
        request = pin(client, request)
        lease = client.mutate(**request)
        assert lease["committed"], lease
        attach = {k:v for k,v in request.items() if k != "ttl_s"}
        attach.update(verb="context.attach", operation_id="context:1", token=lease["result"]["token"], activity_ref="build",
                      repo="other" if fault == "wrong_repo" else "serveros", mode="develop",
                      checkpoint_ref=checkpoint["checkpoint_ref"], expected_revision="f" * 40 if fault == "revision" else revision)
        answer = client.mutate(**attach)
        if fault and fault != "fsmonitor":
            assert answer["committed"] is False, answer
            assert work.execution_contexts("SRV-0001", "build") == []
        else:
            assert answer["committed"], answer
            stored = work.execution_contexts("SRV-0001", "build")[0]
            assert stored.revision == revision and stored.worktree == str(repo) and not stored.dirty
            assert not monitor_effect.exists()


def test_control_receipt_replay_still_requires_current_lease(tmp_path):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    acquired = support.call(daemon, "agent.work.mutate", request)
    operation = {k:v for k,v in request.items() if k not in {"verb", "ttl_s", "expected_store_binding"}}
    operation.update(operation_id="control:1", token=acquired["result"]["token"], intent={"checkpoint_ref":"fixture"})
    owner = daemon.owner_work_operations()
    assert owner.control_commit(operation, peer=support.support.a_peer(), mutation=lambda _: {"bound":True})["committed"]
    release = {k:v for k,v in request.items() if k != "ttl_s"}
    release.update(verb="lease.release", operation_id="release:control", token=acquired["result"]["token"])
    assert support.call(daemon, "agent.work.mutate", release)["committed"]
    with pytest.raises(ValueError):
        owner.control_commit(operation, peer=support.support.a_peer(), mutation=lambda _: pytest.fail("replay effect"))


def test_partial_cli_envelope_and_owner_refusal_never_fall_back(tmp_path, monkeypatch):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    output = []
    monkeypatch.setattr(cli, "emit", lambda value, **_: output.append(value))
    assert cli.main(["--state-dir", str(work.state_dir), "--context-ref", request["context_ref"], "item", "transition", "SRV-0001", "--to", "AVAILABLE"]) == 2
    assert "OWNER_CONTEXT_REQUIRED" in str(output)
    with serving(daemon) as client:
        request = pin(client, request)
        lease = client.mutate(**request)
        common = flags(daemon, work, request, lease["result"]["token"] + 1)
        assert cli.main(common + ["--operation-id", "bad-token", "item", "transition", "SRV-0001", "--to", "AVAILABLE"]) == 2
    assert work.get_item("SRV-0001").state.value == "INBOX"


def test_target_pin_is_rechecked_after_work_lock_acquisition(tmp_path, monkeypatch):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    original = work.transaction
    @contextmanager
    def replacement(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            work.owner_artifacts_root = tmp_path / "changed-after-lock"
            yield conn
    monkeypatch.setattr(work, "transaction", replacement)
    answer = support.call(daemon, "agent.work.mutate", request)
    assert answer["committed"] is False and answer["reason"] == "WORK_STORE_MISMATCH"
    assert work.active_lease("work-write:SRV-0001") is None


def test_historical_expired_acquisition_cannot_authorize_new_effect(tmp_path):
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    acquired = support.call(daemon, "agent.work.mutate", request)
    original_now = work.clock()
    work._clock = lambda: original_now + 121
    replay = support.call(daemon, "agent.work.mutate", request)
    assert replay["replayed"] and replay["historical"]
    mutate = {k:v for k,v in request.items() if k != "ttl_s"}
    mutate.update(verb="item.transition", operation_id="after-expiry", token=acquired["result"]["token"], target="AVAILABLE")
    assert support.call(daemon, "agent.work.mutate", mutate)["committed"] is False


def test_publication_serializes_first_enrollment(tmp_path, monkeypatch):
    import workengine.federation as federation
    world = support.make_world(tmp_path)
    daemon, work, _, _ = world
    request = support.issue(world)
    work.add_activity(Activity("SRV-0001", "inventory", "inspect"))
    run = work.begin_run("SRV-0001", "inventory", "worker", "inventory", tmp_path / "artifacts")
    source = tmp_path / "source.jsonl"
    source.write_text('{"object_id":"r1","kind":"repository","locator":"fixture","observed_at":"2026-09-09T10:00:00Z"}\n')
    entered, proceed, enrolled = threading.Event(), threading.Event(), threading.Event()
    original = federation._write_transaction_file
    def held(*args):
        entered.set()
        assert proceed.wait(5)
        return original(*args)
    monkeypatch.setattr(federation, "_write_transaction_file", held)
    results = {}
    publisher = threading.Thread(target=lambda: results.update(publication=federation.publish_inventory("github", source, tmp_path / "current.jsonl", "SRV-0001/" + run.id, work.record_publication_receipt, work.get_publication_receipt)))
    publisher.start(); assert entered.wait(5)
    def acquire():
        results["acquire"] = support.call(daemon, "agent.work.mutate", request)
        enrolled.set()
    writer = threading.Thread(target=acquire); writer.start()
    assert not enrolled.wait(.05)
    proceed.set(); publisher.join(5); writer.join(5)
    assert results["acquire"]["committed"]
    assert results["publication"]["receipt"]["item_id"] == "SRV-0001"


@pytest.mark.parametrize("pending_target_matches", [True, False])
def test_publication_recovery_of_managed_pending_item_has_zero_file_effects(tmp_path, monkeypatch, pending_target_matches):
    import workengine.federation as federation
    from workengine.model import WorkItem
    from test_owner_work_store_fence import _enroll_fixture
    _, work, _, _ = support.make_world(tmp_path)
    work.create_item(WorkItem("SRV-0002", "serveros", "unmanaged", "feature", "fixture"))
    for item in ("SRV-0001", "SRV-0002"):
        work.add_activity(Activity(item, "inventory", "inspect"))
        work.begin_run(item, "inventory", "worker", "key", tmp_path / "artifacts")
    source = tmp_path / "objects.jsonl"
    source.write_text('{"object_id":"r1","kind":"repository","locator":"fixture","observed_at":"2026-09-09T10:00:00Z"}\n')
    target = tmp_path / "current.jsonl"
    target.write_text("old\n")
    original = work._record_publication_receipt_tx
    class ProcessDeath(BaseException): pass
    def crash(*args): raise ProcessDeath()
    monkeypatch.setattr(work, "_record_publication_receipt_tx", crash)
    with pytest.raises(ProcessDeath):
        federation.publish_inventory("github", source, target, "SRV-0001/RUN-0001", work.record_publication_receipt, work.get_publication_receipt)
    monkeypatch.setattr(work, "_record_publication_receipt_tx", original)
    _enroll_fixture(work, "SRV-0001")
    if not pending_target_matches: target.write_text("old\n")
    def files():
        return {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    before = files()
    with pytest.raises(ValueError, match="OWNER_COMMIT_FENCE_REQUIRED"):
        federation.publish_inventory("github", source, target, "SRV-0002/RUN-0001", work.record_publication_receipt, work.get_publication_receipt)
    assert files() == before


def test_parser_requires_unmanaged_actor_and_revision_but_not_owner_facts():
    parser = cli.build_parser()
    for args in (["run", "abandon", "SRV-0001", "RUN-0001", "--reason", "fixture"],
                 ["context", "attach", "SRV-0001", "build", "--repo", "serveros", "--mode", "develop"]):
        with pytest.raises(SystemExit): parser.parse_args(args)
        parsed = parser.parse_args(["--owner-socket", "/explicit/owner.sock", *args])
        assert parsed.owner_socket == "/explicit/owner.sock"
