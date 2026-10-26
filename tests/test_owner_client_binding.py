"""Portable client refuses before constructing a schema-writing Store."""
import sqlite3
import pytest
from workengine.store import ServerOSStore
from workengine.model import WorkItem


def test_missing_owner_state_is_never_created(tmp_path):
    from workengine.owner_client import OwnerRouter
    path = tmp_path / "missing"
    with pytest.raises(ValueError, match="WORK_STORE_MISMATCH"):
        OwnerRouter(state_dir=path, owner_client=object()).mutate("SRV-0001", "item.transition", target="AVAILABLE")
    assert not path.exists()


def test_partial_envelope_refuses_without_changing_existing_database(tmp_path):
    from workengine.owner_client import OwnerRouter
    store = ServerOSStore(tmp_path / "work")
    store.register_project("serveros", "SRV")
    store.create_item(WorkItem("SRV-0001", "serveros", "fixture", "feature", "fixture"))
    before = store.db_path.read_bytes()
    with pytest.raises(ValueError, match="OWNER_CONTEXT_REQUIRED"):
        OwnerRouter(state_dir=store.state_dir, owner_client=object(), envelope={"context_ref":"context:partial"}).mutate("SRV-0001", "item.transition", target="AVAILABLE")
    assert store.db_path.read_bytes() == before


def test_file_identity_rejects_replacement_at_same_path(tmp_path):
    store = ServerOSStore(tmp_path / "work")
    before = store.owner_target_info()
    replacement = tmp_path / "replacement.db"
    with store.connection() as conn:
        with sqlite3.connect(replacement) as target:
            conn.backup(target)
        target.close()
    conn.close()
    replacement.replace(store.db_path)
    with pytest.raises(ValueError, match="WORK_STORE_MISMATCH"):
        store.owner_target_info()
    assert before["file_identity"]["path"] == str(store.db_path.resolve())


def test_getter_releases_file_handle_for_explicit_stopped_owner_rebind(tmp_path):
    store = ServerOSStore(tmp_path / "work")
    store.register_project("serveros", "SRV")
    store.create_item(WorkItem("SRV-0001", "serveros", "fixture", "feature", "fixture"))
    assert store.get_item("SRV-0001") is not None
    store.db_path.rename(tmp_path / "stopped-owner.db")
