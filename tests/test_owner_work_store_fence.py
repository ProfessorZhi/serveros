"""Portable SQL enforcement tests, with explicitly synthetic fence fixtures."""
import json
import sqlite3
import subprocess
import sys

import pytest

from workengine.model import WorkItem
from workengine.store import ServerOSStore


def _enroll_fixture(store, item):
    """Synthetic enrollment only; mutations under test use normal connections."""
    with store.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda value: int(value == item))
        with store._owner_scope(conn, item):
            conn.execute("INSERT INTO owner_operation_receipts VALUES (?,?,?,?,?)",
                         ("fixture:" + item, item, "lease.acquire", "fixture-digest", "{}"))


def _feedback_fixture(tmp_path, second_link=None):
    from workengine.engine import ServerOS
    store = ServerOSStore(tmp_path / "feedback-work")
    engine = ServerOS(store, artifacts_root=tmp_path / "artifacts")
    engine.register_project("serveros", "SRV")
    for title in ("first", "second"):
        engine.create_item("serveros", title, "feature")
    source = store.register_source("fixture", "feedback", revision="1", locator="feedback-source")
    spare = store.register_source("fixture", "spare", revision="1", locator="spare-source")
    for ref, source_id in (("obs:feedback", source["source_id"]), ("obs:spare", spare["source_id"])):
        store.record_observation(source_id, ref, semantic_type="fixture", content_hash="a" * 64)
    store.record_feedback("SRV-0001", "feedback", "review", "obs:feedback")
    if second_link == "feedback":
        store.record_feedback("SRV-0002", "shared-feedback", "review", "obs:feedback")
    elif second_link == "candidate":
        store.record_claim("claim:feedback", "shared-claim", ["obs:feedback"])
        store.create_candidate("candidate:feedback", "serveros", "shared", "feature",
                               dedupe_key="feedback", claim_ids=["claim:feedback"])
        store.match_candidate("candidate:feedback", "SRV-0002")
    _enroll_fixture(store, "SRV-0001")
    if second_link:
        _enroll_fixture(store, "SRV-0002")
    return store


@pytest.fixture
def managed(tmp_path):
    store = ServerOSStore(tmp_path / "work")
    store.register_project("serveros", "SRV")
    for item in ("SRV-0001", "SRV-0002"):
        store.create_item(WorkItem(id=item, project="serveros", title=item,
                                  kind="feature", card_locator=item + ".md"))
    with store.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO source_objects VALUES ('source:1','fixture','id','1','fixture','now')")
        conn.execute("INSERT INTO observations(observation_id,source_id,semantic_type,content_hash,created_at) "
                     "VALUES ('observation:1','source:1','fixture','hash','now')")
        conn.execute("INSERT INTO claims VALUES ('claim:1','fixture','ACTIVE','now')")
        conn.execute("INSERT INTO claim_observations VALUES ('claim:1','observation:1')")
        conn.execute("INSERT INTO item_candidates(candidate_id,project,title,kind,dedupe_key,approved_item_id,created_at) "
                     "VALUES ('candidate:1','serveros','fixture','feature','fixture','SRV-0001','now')")
        conn.execute("INSERT INTO candidate_claims VALUES ('candidate:1','claim:1')")
        conn.execute("INSERT INTO leases VALUES ('work-write:SRV-0001','human:A',1,'2999-01-01T00:00:00Z')")
        # Synthetic fixture only. Production enables this connection function
        # only inside its control-ledger authority lock and Work transaction.
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
        conn.execute("INSERT INTO owner_transaction_scopes(item_ref) VALUES ('SRV-0001')")
        conn.execute("INSERT INTO owner_operation_receipts VALUES (?,?,?,?,?)",
                     ("fixture:1", "SRV-0001", "lease.acquire", "fixture-digest", json.dumps({"fixture": True})))
        conn.execute("DELETE FROM owner_transaction_scopes")
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
    "UPDATE leases SET resource='work-write:SRV-0002' WHERE resource='work-write:SRV-0001'",
    "DELETE FROM leases WHERE resource='work-write:SRV-0001'",
    "UPDATE item_candidates SET approved_item_id='SRV-0002' WHERE candidate_id='candidate:1'",
    "DELETE FROM candidate_claims WHERE candidate_id='candidate:1'",
    "UPDATE claims SET locator='escaped' WHERE claim_id='claim:1'",
    "DELETE FROM claim_observations WHERE claim_id='claim:1'",
    "UPDATE observations SET content_hash='escaped' WHERE observation_id='observation:1'",
    "UPDATE source_objects SET revision='escaped' WHERE source_id='source:1'",
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
        with managed._owner_scope(conn, "SRV-0001"):
            conn.execute("DELETE FROM leases WHERE resource='work-write:SRV-0001'")
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


def test_raw_unmanaged_connection_remains_compatible(managed):
    with sqlite3.connect(managed.db_path) as conn:
        conn.execute("INSERT INTO item_events(item_id,event_type,at,payload_json) VALUES ('SRV-0002','raw-fixture','now','{}')")


def test_scope_marker_cannot_be_committed_even_when_cleanup_is_forgotten(managed):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with managed.transaction(immediate=True) as conn:
            conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
            conn.execute("INSERT INTO owner_transaction_scopes(item_ref) VALUES ('SRV-0001')")
    with managed.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM owner_transaction_scopes").fetchone()[0] == 0


def test_scope_marker_cannot_be_broadened_or_replaced(managed):
    with managed.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
        conn.execute("INSERT INTO owner_transaction_scopes(item_ref) VALUES ('SRV-0001')")
        for sql in ("UPDATE owner_transaction_scopes SET item_ref='SRV-0002'",
                    "INSERT OR REPLACE INTO owner_transaction_scopes(item_ref) VALUES ('SRV-0002')"):
            with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
                conn.execute(sql)
        conn.execute("DELETE FROM owner_transaction_scopes WHERE item_ref='SRV-0001'")


def test_scope_is_uncommitted_other_connection_cannot_write_and_exception_rolls_back(managed):
    with pytest.raises(RuntimeError, match="injected"):
        with managed.transaction(immediate=True) as conn:
            conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
            with managed._owner_scope(conn, "SRV-0001"):
                conn.execute("UPDATE items_index SET title='pending' WHERE item_id='SRV-0001'")
                other = sqlite3.connect(managed.db_path, timeout=0)
                try:
                    assert other.execute("SELECT count(*) FROM owner_transaction_scopes").fetchone()[0] == 0
                    with pytest.raises(sqlite3.OperationalError, match="locked"):
                        other.execute("BEGIN IMMEDIATE")
                finally:
                    other.close()
                raise RuntimeError("injected")
    with managed.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM owner_transaction_scopes").fetchone()[0] == 0
        assert conn.execute("SELECT title FROM items_index WHERE item_id='SRV-0001'").fetchone()[0] == "SRV-0001"


def test_process_kill_inside_scope_rolls_back_marker_and_actual_item_write(managed):
    script = '''import os, sys
from workengine.store import ServerOSStore
store = ServerOSStore(sys.argv[1])
with store.transaction(immediate=True) as conn:
    conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
    with store._owner_scope(conn, "SRV-0001"):
        conn.execute("UPDATE items_index SET title='crashed' WHERE item_id='SRV-0001'")
        os._exit(73)
'''
    completed = subprocess.run([sys.executable, "-c", script, str(managed.state_dir)],
                               capture_output=True, text=True, timeout=15)
    assert completed.returncode == 73, completed.stderr
    reopened = ServerOSStore(managed.state_dir)
    with reopened.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM owner_transaction_scopes").fetchone()[0] == 0
        assert conn.execute("SELECT title FROM items_index WHERE item_id='SRV-0001'").fetchone()[0] == "SRV-0001"


def test_cleanup_failure_and_disabled_foreign_keys_cannot_leave_scope(managed):
    with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
        with managed.transaction(immediate=True) as conn:
            conn.create_function("owner_commit_scope", 1, lambda item: int(item == "SRV-0001"))
            with managed._owner_scope(conn, "SRV-0001"):
                conn.execute("UPDATE items_index SET title='pending' WHERE item_id='SRV-0001'")
                conn.create_function("owner_commit_scope", 1, lambda item: 0)
    conn = managed.connection()
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        conn.create_function("owner_commit_scope", 1, lambda item: 1)
        with pytest.raises(ValueError, match="foreign keys"):
            with managed._owner_scope(conn, "SRV-0001"):
                pytest.fail("scope accepted disabled foreign keys")
        conn.rollback()
    finally:
        conn.close()
    with managed.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM owner_transaction_scopes").fetchone()[0] == 0


@pytest.mark.parametrize("sql", [
    "UPDATE observations SET content_hash='changed' WHERE observation_id='obs:feedback'",
    "UPDATE source_objects SET locator='changed' WHERE source_id='fixture:feedback:1'",
    "UPDATE observations SET observation_id='obs:escaped' WHERE observation_id='obs:feedback'",
    "UPDATE observations SET observation_id='obs:feedback' WHERE observation_id='obs:spare'",
    "UPDATE source_objects SET source_id='source:escaped' WHERE source_id='fixture:feedback:1'",
    "UPDATE source_objects SET source_id='fixture:feedback:1' WHERE source_id='fixture:spare:1'",
    "DELETE FROM observations WHERE observation_id='obs:feedback'",
    "DELETE FROM source_objects WHERE source_id='fixture:feedback:1'",
])
def test_feedback_only_provenance_guards_old_and_new_identity(tmp_path, sql):
    store = _feedback_fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
        with store.transaction(immediate=True) as conn:
            conn.execute(sql)


@pytest.mark.parametrize("second_link", ["feedback", "candidate"])
@pytest.mark.parametrize("scope_item", ["SRV-0001", "SRV-0002"])
@pytest.mark.parametrize("sql", [
    "UPDATE observations SET content_hash='changed' WHERE observation_id='obs:feedback'",
    "UPDATE source_objects SET locator='changed' WHERE source_id='fixture:feedback:1'",
])
def test_shared_feedback_provenance_requires_every_managed_item(tmp_path, second_link, scope_item, sql):
    store = _feedback_fixture(tmp_path, second_link)
    with store.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda item: int(item == scope_item))
        with store._owner_scope(conn, scope_item):
            with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
                conn.execute(sql)


@pytest.mark.parametrize("second_link", ["feedback", "candidate"])
def test_shared_feedback_provenance_accepts_all_exact_fixture_scopes(tmp_path, second_link):
    store = _feedback_fixture(tmp_path, second_link)
    with store.transaction(immediate=True) as conn:
        conn.create_function("owner_commit_scope", 1, lambda item: int(item in {"SRV-0001", "SRV-0002"}))
        with store._owner_scope(conn, "SRV-0001"), store._owner_scope(conn, "SRV-0002"):
            conn.execute("UPDATE observations SET content_hash='scoped' WHERE observation_id='obs:feedback'")
            conn.execute("UPDATE source_objects SET locator='scoped' WHERE source_id='fixture:feedback:1'")


def test_mixed_project_register_then_create_preserves_unmanaged_workflow(tmp_path):
    from workengine.engine import ServerOS
    store = _feedback_fixture(tmp_path)
    with store.transaction() as conn:
        before = tuple(conn.execute("SELECT * FROM projects WHERE project='serveros'").fetchone())
    # The real CLI's _cmd_item(create) performs these exact two facade calls.
    # Exercise them directly so this portable regression does not import the
    # CLI's unrelated POSIX-only federation module on Windows.
    engine = ServerOS(store, artifacts_root=tmp_path / "artifacts")
    engine.register_project("serveros", "SRV")
    created = engine.create_item("serveros", "third", "feature")
    assert created.id == "SRV-0003"
    with store.transaction() as conn:
        assert tuple(conn.execute("SELECT * FROM projects WHERE project='serveros'").fetchone()) == before
        assert conn.execute("SELECT count(*) FROM owner_operation_receipts WHERE item_ref='SRV-0003'").fetchone()[0] == 0


@pytest.mark.parametrize("sql", [
    "UPDATE projects SET prefix='OTHER' WHERE project='serveros'",
    "DELETE FROM projects WHERE project='serveros'",
    "INSERT OR REPLACE INTO projects VALUES ('serveros','OTHER')",
])
def test_mixed_project_real_mutation_still_requires_owner_scope(tmp_path, sql):
    store = _feedback_fixture(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="OWNER_COMMIT_FENCE"):
        with store.transaction(immediate=True) as conn:
            conn.execute(sql)
