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
