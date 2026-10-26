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
