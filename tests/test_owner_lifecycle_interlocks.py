"""Portable real Store pre-effect guards (synthetic enrollment only)."""
import pytest
from workengine.engine import ServerOS
from workengine.model import Activity
from workengine.migration import MigrationManager
from test_owner_work_store_fence import _enroll_fixture


def world(tmp_path):
    engine = ServerOS(state_dir=tmp_path / "state", artifacts_root=tmp_path / "artifacts")
    engine.register_project("demo", "DEM")
    item = engine.create_item("demo", "fixture", "feature")
    engine.add_activity(item.id, "build", "develop")
    run = engine.begin_run(item.id, "build", "worker", "key")
    return engine.store, item, run


def test_managed_finalize_has_zero_filesystem_effect(tmp_path):
    store, item, run = world(tmp_path)
    for state in ("PREPARING", "RUNNING", "FINALIZING"):
        store.advance_run(item.id, run.id, state)
    _enroll_fixture(store, item.id)
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if "serveros.db" not in p.name)
    with pytest.raises(Exception, match="OWNER_COMMIT_FENCE_REQUIRED"):
        store.finalize_run(item.id, run.id, verdict="PASS", exit_code=0, command="fixture", cases=[], revision="abc", dirty=False)
    after = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if "serveros.db" not in p.name)
    assert after == before


def test_managed_compensation_refuses_before_first_move(tmp_path):
    store, item, run = world(tmp_path)
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_text("owned content")
    manager = MigrationManager(store)
    manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
    manager.apply("demo")
    _enroll_fixture(store, item.id)
    with pytest.raises(Exception, match="OWNER_COMMIT_FENCE_REQUIRED"):
        manager.compensate("demo")
    assert not source.exists()
    assert target.read_text() == "owned content"
    assert manager.inspect("demo")["status"] == "COMPLETED"


def test_lifecycle_uses_one_owner_transaction_and_existing_terminal_rules(tmp_path):
    store, item, run = world(tmp_path)
    lease = store.acquire_lease("work-write:" + item.id, owner="human:A", ttl_s=120)
    _enroll_fixture(store, item.id)
    def act(conn, verb, **arguments):
        return store._apply_owner_operation(conn, verb=verb, item_ref=item.id,
            principal="human:A", arguments={"token":lease["token"], **arguments})
    with store.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda value: int(value == item.id))
        with store._owner_scope(conn, item.id):
            act(conn, "activity.add", activity_ref="review", kind="inspect", needs=["build"])
            act(conn, "activity.transition", activity_ref="review", target="READY")
            act(conn, "item.transition", target="AVAILABLE", note="ready")
            act(conn, "item.outcome", outcome="SUCCESS")
            for state in ("PREPARING", "RUNNING", "FINALIZING"):
                act(conn, "run.advance", run_ref=run.id, target=state)
            with pytest.raises(ValueError, match="cannot conclude"):
                act(conn, "run.advance", run_ref=run.id, target="SUCCEEDED")
            plan = act(conn, "run.terminate", run_ref=run.id, reason="done", dry_run=True)
            assert plan["dry_run"]
            assert conn.execute("SELECT state FROM runs WHERE run_id=?", (run.id,)).fetchone()[0] == "FINALIZING"
            act(conn, "run.terminate", run_ref=run.id, reason="done")
    assert store.require_run(item.id, run.id).state.value == "CANCELLED"
    assert store.get_item(item.id).state.value == "AVAILABLE"


@pytest.mark.parametrize("status", ["APPLYING", "COMPENSATING", "UNKNOWN"])
def test_active_migration_journal_prevents_first_enrollment(tmp_path, status):
    store, item, run = world(tmp_path)
    with store.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO migration_intents VALUES (?,?,?,?,?)", ("demo", status, "{}", 0, "now"))
    with pytest.raises(Exception, match="OWNER_MIGRATION_ACTIVE"):
        _enroll_fixture(store, item.id)


def test_legacy_begin_replay_cannot_recreate_managed_directory(tmp_path):
    from pathlib import Path
    store, item, run = world(tmp_path)
    _enroll_fixture(store, item.id)
    Path(run.artifact_dir).rmdir()
    with pytest.raises(ValueError, match="OWNER_COMMIT_FENCE_REQUIRED"):
        store.begin_run(item.id, "build", "worker", "key", tmp_path / "artifacts")
    assert not Path(run.artifact_dir).exists()


def test_owner_run_allocation_retry_adopts_exact_orphan_only(tmp_path):
    from pathlib import Path
    store, item, run = world(tmp_path)
    allocation = {"operation_id":"allocate:1", "digest":"same-intent"}
    with pytest.raises(RuntimeError, match="crash before commit"):
        with store.transaction(immediate=True) as conn:
            allocated = store._begin_run_tx(conn, item.id, "build", "worker", "second-key", tmp_path / "artifacts", allocation=allocation)
            raise RuntimeError("crash before commit")
    assert store.get_run(item.id, allocated.id) is None
    assert (Path(allocated.artifact_dir) / ".owner-allocation.json").is_file()
    with pytest.raises(ValueError, match="OWNER_ARTIFACT_ORPHAN"):
        with store.transaction(immediate=True) as conn:
            store._begin_run_tx(conn, item.id, "build", "other", "third-key", tmp_path / "artifacts", allocation={"operation_id":"allocate:2", "digest":"different"})
    with store.transaction(immediate=True) as conn:
        resumed = store._begin_run_tx(conn, item.id, "build", "worker", "second-key", tmp_path / "artifacts", allocation=allocation)
    assert resumed.id == allocated.id


def test_compensation_failure_keeps_durable_fence_for_enrollment(tmp_path, monkeypatch):
    import workengine.migration as migration
    store, item, run = world(tmp_path)
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_text("content")
    manager = MigrationManager(store)
    manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
    manager.apply("demo")
    def interrupted(*args): raise RuntimeError("simulated process failure")
    monkeypatch.setattr(migration, "_move_verified", interrupted)
    with pytest.raises(RuntimeError): manager.compensate("demo")
    assert manager.inspect("demo")["status"] == "COMPENSATING"
    with pytest.raises(Exception, match="OWNER_MIGRATION_ACTIVE"):
        _enroll_fixture(store, item.id)


def test_compensation_cannot_release_fence_while_apply_is_active(tmp_path, monkeypatch):
    import threading
    import workengine.migration as migration
    store, item, run = world(tmp_path)
    from workengine.model import WorkItem
    store.register_project("parallel", "PAR")
    store.create_item(WorkItem("PAR-0001", "parallel", "unrelated", "feature", "fixture"))
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_text("content")
    manager = MigrationManager(store)
    manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
    entered, proceed, compensated = threading.Event(), threading.Event(), threading.Event()
    original = migration._move_verified
    errors = []
    def paused(*args):
        entered.set()
        assert proceed.wait(10)
        return original(*args)
    monkeypatch.setattr(migration, "_move_verified", paused)
    def apply():
        try: manager.apply("demo")
        except Exception as exc: errors.append(exc)
    def compensate():
        try: manager.compensate("demo")
        except Exception as exc: errors.append(exc)
        finally: compensated.set()
    actor = threading.Thread(target=apply); actor.start()
    assert entered.wait(5)
    recovery = threading.Thread(target=compensate); recovery.start()
    try:
        assert compensated.wait(5), "busy actuator must return a bounded refusal"
        assert len(errors) == 1 and "MIGRATION_BUSY" in str(errors[0])
        errors.clear()
        with pytest.raises(Exception, match="OWNER_MIGRATION_ACTIVE"):
            _enroll_fixture(store, item.id)
        with pytest.raises(ValueError, match="MIGRATION_BUSY"):
            manager.plan({"project":"other", "operations":[]})
        _enroll_fixture(store, "PAR-0001")
    finally:
        proceed.set(); actor.join(10); recovery.join(10)
    assert not errors
    manager.compensate("demo")
    assert manager.inspect("demo")["status"] == "COMPENSATED"
    assert source.read_text() == "content" and not target.exists()
    _enroll_fixture(store, item.id)
    assert manager.plan({"project":"other", "operations":[]})["status"] == "PLANNED"


def test_compensation_reconciles_actual_move_before_cursor_commit(tmp_path, monkeypatch):
    import workengine.migration as migration
    store, item, run = world(tmp_path)
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_text("content")
    manager = MigrationManager(store)
    manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
    original = migration._move_verified
    def crash(*args):
        original(*args)
        raise RuntimeError("actual move happened before cursor commit")
    monkeypatch.setattr(migration, "_move_verified", crash)
    with pytest.raises(RuntimeError): manager.apply("demo")
    monkeypatch.setattr(migration, "_move_verified", original)
    assert manager.inspect("demo")["cursor"] == 0
    assert target.is_file() and not source.exists()
    manager.compensate("demo")
    assert source.read_text() == "content" and not target.exists()
    _enroll_fixture(store, item.id)


@pytest.mark.parametrize("actor", [None, 1, [], {}])
def test_unmanaged_termination_requires_string_actor_at_store_boundary(tmp_path, actor):
    store, item, run = world(tmp_path)
    with pytest.raises(ValueError, match="actor"):
        store.cancel_run(item.id, run.id, reason="fixture", actor=actor)
    assert store.require_run(item.id, run.id).state.value == "ALLOCATED"
    assert not store.run_events(item.id, run.id)


@pytest.mark.parametrize("revision", [None, "", "  ", 5])
def test_unmanaged_context_requires_string_revision_at_store_boundary(tmp_path, revision):
    from workengine.model import ExecutionContext, ContextMode
    store, item, run = world(tmp_path)
    with pytest.raises(ValueError, match="revision"):
        store.attach_context(ExecutionContext(item.id, "build", "demo", ContextMode.DEVELOP, revision))
    assert not store.execution_contexts(item.id, "build")


def test_migration_actuator_is_cross_process_and_releases_after_process_death(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    store, item, run = world(tmp_path)
    before = (store.state_dir / ".migration-actuator.lock").stat().st_ino
    code = """import sys
from workengine.store import ServerOSStore
from workengine.migration_lock import actuator
store=ServerOSStore(sys.argv[1])
with actuator(store):
    print('HELD', flush=True)
    sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-c", code, str(store.state_dir)], cwd=Path(__file__).resolve().parents[1],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "HELD"
        with pytest.raises(ValueError, match="MIGRATION_BUSY"):
            MigrationManager(store).plan({"project":"other", "operations":[]})
    finally:
        child.kill(); child.communicate(timeout=5)
    assert MigrationManager(store).plan({"project":"other", "operations":[]})["status"] == "PLANNED"
    assert (store.state_dir / ".migration-actuator.lock").stat().st_ino == before


def test_migration_never_moves_reserved_control_files(tmp_path):
    store, item, run = world(tmp_path)
    source = tmp_path / "source"
    source.write_text("content")
    manager = MigrationManager(store)
    for origin, target in ((store.state_dir, tmp_path / "moved"), (source, store.state_dir / "new"),
                           (store.state_dir / ".migration-actuator.lock", tmp_path / "lock-moved")):
        with pytest.raises(ValueError, match="reserved control"):
            manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(origin), "target":str(target)}]})
    assert store.db_path.is_file()
    assert (store.state_dir / ".migration-actuator.lock").is_file()


@pytest.mark.parametrize("operation", ["apply", "compensate"])
def test_partial_copy_keeps_compensation_fenced_until_reconciled(tmp_path, operation):
    import hashlib
    store, item, run = world(tmp_path)
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"content")
    manager = MigrationManager(store)
    manager.plan({"project":"demo", "operations":[{"type":"move", "source":str(source), "target":str(target)}]})
    digest = hashlib.sha256(b"content").hexdigest()
    partial = target.with_name(f".{target.name}.serveros-migrate-{digest[:12]}.tmp")
    partial.write_bytes(b"partial")
    if operation == "apply": target.write_bytes(b"content")
    with pytest.raises(ValueError, match="ambiguous partial copy"):
        getattr(manager, operation)("demo")
    assert manager.inspect("demo")["status"] == ("APPLYING" if operation == "apply" else "COMPENSATING")
    with pytest.raises(Exception, match="OWNER_MIGRATION_ACTIVE"):
        _enroll_fixture(store, item.id)
    # Explicit fixture reconciliation establishes the exact completed copy.
    partial.write_bytes(b"content")
    manager.compensate("demo")
    assert not partial.exists()
    _enroll_fixture(store, item.id)
