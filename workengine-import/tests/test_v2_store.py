import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from workengine.model import ItemState, WorkItem
from workengine.store import ServerOSStore


class ServerOSStoreTest(unittest.TestCase):
    def test_state_root_must_be_absolute(self):
        with self.assertRaises(ValueError):
            ServerOSStore("relative/state")

    def test_project_prefix_is_unique(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            store.register_project("litellm", "LIT")
            with self.assertRaises(ValueError):
                store.register_project("another", "LIT")

    def test_transactional_item_allocation_is_unique(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            store.register_project("serveros", "SRV")

            def allocate(_):
                return ServerOSStore(td).allocate_item_id("serveros")

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                ids = list(pool.map(allocate, range(40)))
            self.assertEqual(len(set(ids)), 40)
            self.assertEqual(min(ids), "SRV-0001")
            self.assertEqual(max(ids), "SRV-0040")

    def test_item_round_trip_and_append_only_events(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            store.register_project("serveros", "SRV")
            item = WorkItem(
                id="SRV-0001",
                project="serveros",
                title="converge workflows",
                kind="ops",
                card_locator="records/projects/serveros/items/SRV-0001/card.yaml",
            )
            store.create_item(item)
            store.transition_item("SRV-0001", ItemState.AVAILABLE, note="triaged")
            reopened = ServerOSStore(td).get_item("SRV-0001")
            self.assertEqual(reopened.state, ItemState.AVAILABLE)
            events = store.item_events("SRV-0001")
            self.assertEqual([event["event_type"] for event in events], ["created", "transitioned"])
            self.assertEqual(events[1]["payload"]["note"], "triaged")

    def test_database_enables_wal_and_foreign_keys(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            self.assertEqual(store.connection().execute("PRAGMA journal_mode").fetchone()[0].lower(), "wal")
            self.assertEqual(store.connection().execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(Path(store.db_path), Path(td) / "serveros.db")


if __name__ == "__main__":
    unittest.main()
