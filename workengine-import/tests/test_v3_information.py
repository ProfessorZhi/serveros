import sqlite3
import tempfile
import unittest
from pathlib import Path

from workengine.model import WorkItem
from workengine.store import ServerOSStore
from workengine.migrations import V3_SQL
from workengine.board import snapshot


class InformationV3Tests(unittest.TestCase):
    def test_reopen_v2_database_runs_additive_migration(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            state.mkdir()
            db = state / "serveros.db"
            conn = sqlite3.connect(db)
            conn.executescript((Path(__file__).parents[1] / "workengine/schema.sql").read_text())
            conn.execute("insert into projects values ('demo','DEM')")
            conn.execute("insert into id_sequences values ('demo','item',2)")
            conn.execute("insert into items_index values ('DEM-0001','demo','old','ops','/card','INBOX',NULL,'t','t')")
            conn.commit(); conn.close()
            store = ServerOSStore(state)
            with store.connection() as c:
                self.assertEqual(c.execute("select version from schema_migrations order by version desc limit 1").fetchone()[0], 4)
                self.assertEqual(c.execute("select count(*) from items_index").fetchone()[0], 1)
                for table in ("source_objects", "observations", "claims", "item_candidates", "evidence", "feedback", "publication_receipts"):
                    self.assertIsNotNone(c.execute("select name from sqlite_master where name=?", (table,)).fetchone())

    def test_v3_fixture_upgrades_to_v4_without_losing_candidate_rows(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/'state'; state.mkdir(); db=state/'serveros.db'; c=sqlite3.connect(db)
            c.executescript((Path(__file__).parents[1]/'workengine/schema.sql').read_text()); c.executescript(V3_SQL.replace("domain TEXT NOT NULL DEFAULT '', ",""))
            c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"); c.execute("INSERT INTO schema_migrations VALUES(3,'t')")
            c.execute("INSERT INTO item_candidates(candidate_id,project,title,kind,state,dedupe_key,external_id,external_revision,approved_item_id,created_at) VALUES('x','p','t','bug','NEW','d','e','r',NULL,'t')"); c.commit(); c.close()
            s=ServerOSStore(state)
            with s.connection() as dbconn:
                self.assertEqual(dbconn.execute('select max(version) from schema_migrations').fetchone()[0],4); self.assertEqual(dbconn.execute("select external_id from item_candidates where candidate_id='x'").fetchone()[0],'e')

    def test_information_records_require_provenance_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            s = ServerOSStore(Path(td) / "state")
            source = s.register_source("feishu", "chat-1", revision="r1", locator="https://example/chat")
            obs = s.record_observation(source["source_id"], "obs-1", semantic_type="feedback", content_hash="a"*64)
            claim = s.record_claim("claim-1", "records/information/understanding/c1.md", [obs["observation_id"]])
            self.assertEqual(s.record_observation(source["source_id"], "obs-1", semantic_type="feedback", content_hash="a"*64), obs)
            self.assertEqual(claim["claim_id"], "claim-1")
            with self.assertRaises(ValueError):
                s.record_claim("bad", "x", [])

    def test_external_identity_is_unique_across_dedupe_keys(self):
        with tempfile.TemporaryDirectory() as td:
            s=ServerOSStore(Path(td)/"state"); s.register_project("demo","DEM")
            src=s.register_source("github","issue-1",revision="r1",locator="https://x")
            o=s.record_observation(src['source_id'],'o',semantic_type='bug',content_hash='a'*64)
            c=s.record_claim('c','/claim',[o['observation_id']])
            s.create_candidate('c1','demo','one','bug',dedupe_key='d1',claim_ids=['c'],external_id='issue-1',external_revision='r1',domain='github')
            with self.assertRaises((ValueError,sqlite3.IntegrityError)):
                s.create_candidate('c2','demo','two','bug',dedupe_key='d2',claim_ids=['c'],external_id='issue-1',external_revision='r1',domain='github')

    def test_board_maps_candidate_only_to_promoted_item_and_uses_source_revision(self):
        with tempfile.TemporaryDirectory() as td:
            s=ServerOSStore(Path(td)/'state'); s.register_project('demo','DEM')
            item1=s.create_item(WorkItem('DEM-0001','demo','one','bug','/one')); item2=s.create_item(WorkItem('DEM-0002','demo','two','bug','/two'))
            src=s.register_source('github','issue',revision='rev-9',locator='https://x'); o=s.record_observation(src['source_id'],'o',semantic_type='bug',content_hash='a'*64); s.record_claim('c','/claim',['o'])
            s.create_candidate('cand','demo','one','bug',dedupe_key='d',claim_ids=['c'],domain='github',external_id='issue',external_revision='rev-9'); s.match_candidate('cand',item1.id); s.promote_candidate('cand',item1.id)
            data=snapshot(s,'demo'); by={x['id']:x for x in data['items']}
            self.assertEqual(len(by[item1.id]['candidate_mappings']),1); self.assertEqual(by[item2.id]['candidate_mappings'],[]); self.assertEqual(data['generation_source_revision'],'rev-9')
