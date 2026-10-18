import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from workengine.model import ItemState, Outcome, WorkItem
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

    def test_outcome_and_deliverables_are_independent_from_item_state(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            store.register_project("serveros", "SRV")
            store.create_item(WorkItem("SRV-0001", "serveros", "converge", "ops", "/records/card.yaml"))

            item = store.set_outcome("SRV-0001", Outcome.PARTIAL)
            deliverable = store.add_deliverable("SRV-0001", "report", "/artifacts/report.md")

            self.assertEqual(item.state, ItemState.INBOX)
            self.assertEqual(item.outcome, Outcome.PARTIAL)
            self.assertEqual(deliverable["kind"], "report")
            self.assertEqual(store.list_deliverables("SRV-0001"), [deliverable])
            self.assertEqual(store.item_events("SRV-0001")[-1]["event_type"], "deliverable_added")


if __name__ == "__main__":
    unittest.main()
