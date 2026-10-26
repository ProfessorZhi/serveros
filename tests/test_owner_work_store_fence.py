"""Portable SQL enforcement tests, with explicitly synthetic fence fixtures."""
import json
import sqlite3

import pytest

from workengine.model import WorkItem
from workengine.store import ServerOSStore


@pytest.fixture
def managed(tmp_path):
    store = ServerOSStore(tmp_path / "work")
    store.register_project("serveros", "SRV")
    for item in ("SRV-0001", "SRV-0002"):
        store.create_item(WorkItem(id=item, project="serveros", title=item,
                                  kind="feature", card_locator=item + ".md"))
    with store.transaction(immediate=True) as conn:
        # Synthetic fixture only. Production enables this connection function
        # only inside its control-ledger authority lock and Work transaction.
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
        conn.execute("INSERT INTO owner_operation_receipts VALUES (?,?,?,?,?)",
                     ("fixture:1", "SRV-0001", "lease.acquire", "fixture-digest", json.dumps({"fixture": True})))
    return store


@pytest.mark.parametrize("sql", [
    "UPDATE items_index SET title='escaped' WHERE item_id='SRV-0001'",
    "UPDATE items_index SET item_id='SRV-0003' WHERE item_id='SRV-0001'",
    "DELETE FROM items_index WHERE item_id='SRV-0001'",
    "INSERT INTO item_events(item_id,event_type,at,payload_json) VALUES ('SRV-0001','bypass','now','{}')",
    "UPDATE item_events SET item_id='SRV-0002' WHERE item_id='SRV-0001'",
    "UPDATE item_events SET item_id='SRV-0001' WHERE item_id='SRV-0002'",
    "DELETE FROM owner_operation_receipts",
    "UPDATE owner_operation_receipts SET item_ref='SRV-0002'",
    "INSERT INTO leases VALUES ('work-write:SRV-0001','forged',1,'2999-01-01T00:00:00Z')",
])
def test_managed_insert_update_delete_and_old_new_identity_are_guarded(managed, sql):
    with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
        with managed.transaction(immediate=True) as conn:
            conn.execute(sql)


def test_unmanaged_items_keep_existing_behavior(managed):
    managed.transition_item("SRV-0002", "AVAILABLE")
    assert managed.get_item("SRV-0002").state.value == "AVAILABLE"


def test_reopening_store_does_not_reset_management_or_connection_guard(managed):
    reopened = ServerOSStore(managed.state_dir)
    with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
        reopened.transition_item("SRV-0001", "AVAILABLE")


def test_only_exact_managed_item_can_be_written_in_fixture_connection(managed):
    with managed.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0002"))
        with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
            conn.execute("UPDATE items_index SET title='wrong scope' WHERE item_id='SRV-0001'")


def test_closed_work_operations_mutate_the_existing_tables_in_caller_transaction(managed):
    with managed.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
        lease = managed._apply_owner_operation(conn, verb="lease.acquire", item_ref="SRV-0001",
                                               principal="human:A", arguments={"ttl_s": 60})
        renewed = managed._apply_owner_operation(conn, verb="lease.renew", item_ref="SRV-0001",
                principal="human:A", arguments={"token": lease["token"], "ttl_s": 120})
        assert renewed["token"] == lease["token"]
        changed = managed._apply_owner_operation(conn, verb="item.transition", item_ref="SRV-0001",
                principal="human:A", arguments={"token": lease["token"], "target": "AVAILABLE", "note": "fixture"})
        assert changed["state"] == "AVAILABLE"
        managed._apply_owner_operation(conn, verb="lease.release", item_ref="SRV-0001",
                principal="human:A", arguments={"token": lease["token"]})
    assert managed.active_lease("work-write:SRV-0001") is None
    assert managed.get_item("SRV-0001").state.value == "AVAILABLE"
