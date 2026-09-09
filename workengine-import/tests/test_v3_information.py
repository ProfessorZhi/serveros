import sqlite3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
import io
from pathlib import Path

from workengine.model import WorkItem
from workengine.store import ServerOSStore
from workengine.migrations import V3_SQL
from workengine.board import snapshot
from workengine.cli import main


class InformationV3Tests(unittest.TestCase):
    def _create_candidate_fixture(self, state):
        store = ServerOSStore(state)
        store.register_project("demo", "DEM")
        source = store.register_source("github", "issue-1", revision="r1", locator="https://x")
        observation = store.record_observation(
            source["source_id"], "obs-1", semantic_type="bug", content_hash="a" * 64
        )
        store.record_claim("claim-1", "/claim", [observation["observation_id"]])
        candidate = store.create_candidate(
            "candidate-1",
            "demo",
            "candidate",
            "bug",
            dedupe_key="candidate-dedupe-1",
            claim_ids=["claim-1"],
            domain="github",
            external_id="issue-1",
            external_revision="r1",
        )
        return store, candidate

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
                self.assertEqual(c.execute("select version from schema_migrations order by version desc limit 1").fetchone()[0], 6)
                self.assertEqual(c.execute("select count(*) from items_index").fetchone()[0], 1)
                for table in ("source_objects", "observations", "claims", "item_candidates", "evidence", "feedback", "publication_receipts"):
                    self.assertIsNotNone(c.execute("select name from sqlite_master where name=?", (table,)).fetchone())
                receipt_columns = {row[1] for row in c.execute("pragma table_info(publication_receipts)")}
                self.assertTrue({"sha256", "evidence_run", "item_id", "run_id"} <= receipt_columns)
                feedback_columns = {row[1] for row in c.execute("pragma table_info(feedback)")}
                self.assertIn("observation_id", feedback_columns)

    def test_v3_fixture_upgrades_through_v6_without_losing_candidate_rows(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/'state'; state.mkdir(); db=state/'serveros.db'; c=sqlite3.connect(db)
            c.executescript((Path(__file__).parents[1]/'workengine/schema.sql').read_text()); c.executescript(V3_SQL.replace("domain TEXT NOT NULL DEFAULT '', ",""))
            c.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"); c.execute("INSERT INTO schema_migrations VALUES(3,'t')")
            c.execute("INSERT INTO projects VALUES('p','PFX')"); c.execute("INSERT INTO item_candidates(candidate_id,project,title,kind,state,dedupe_key,external_id,external_revision,approved_item_id,created_at) VALUES('x','p','t','bug','NEW','d','e','r',NULL,'t')")
            c.execute("INSERT INTO items_index VALUES('PFX-0001','p','old','ops','/card','INBOX',NULL,'t','t')")
            c.execute("INSERT INTO feedback(item_id,locator,kind,created_at) VALUES('PFX-0001','/legacy','review','t')")
            c.execute("INSERT INTO source_objects VALUES('s','github','e','r','https://x','t')"); c.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)",('o','s','bug','online','github','curated','a'*64,'t'))
            c.execute("INSERT INTO claims VALUES('cl','/claim','ACTIVE','t')"); c.execute("INSERT INTO claim_observations VALUES('cl','o')"); c.execute("INSERT INTO candidate_claims VALUES('x','cl')"); c.commit(); c.close()
            s=ServerOSStore(state)
            with s.connection() as dbconn:
                self.assertEqual(dbconn.execute('select max(version) from schema_migrations').fetchone()[0],6); self.assertEqual(dbconn.execute("select external_id from item_candidates where candidate_id='x'").fetchone()[0],'e')
                self.assertEqual(dbconn.execute('select count(*) from candidate_claims where candidate_id="x" and claim_id="cl"').fetchone()[0],1); self.assertEqual(list(dbconn.execute('pragma foreign_key_check')),[])
                legacy_feedback = dbconn.execute("select locator,observation_id from feedback").fetchone()
                self.assertEqual(tuple(legacy_feedback), ('/legacy', None))

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

    def test_feedback_links_to_observation_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(Path(td) / "state")
            store.register_project("demo", "DEM")
            item = store.create_item(WorkItem("DEM-0001", "demo", "feedback", "test", "/card"))
            source = store.register_source("file", "report", revision="r1", locator="/report")
            observation = store.record_observation(
                source["source_id"], "obs-feedback", semantic_type="feedback", content_hash="b" * 64,
            )

            first = store.record_feedback(
                item.id, "/feedback/codex.json", "agent-verification", observation["observation_id"],
            )
            repeated = store.record_feedback(
                item.id, "/feedback/codex.json", "agent-verification", observation["observation_id"],
            )

            self.assertEqual(repeated, first)
            self.assertEqual(first["observation_id"], observation["observation_id"])
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM feedback").fetchone()[0], 1)
            with self.assertRaises(KeyError):
                store.record_feedback(item.id, "/feedback/missing.json", "agent-verification", "missing")

    def test_feedback_record_cli_links_observation_and_retries_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store = ServerOSStore(state)
            store.register_project("demo", "DEM")
            item = store.create_item(WorkItem("DEM-0001", "demo", "feedback", "test", "/card"))
            source = store.register_source("file", "claude-run", revision="r1", locator="/run")
            observation = store.record_observation(
                source["source_id"], "obs-claude", semantic_type="feedback", content_hash="c" * 64,
            )
            argv = [
                "--state-dir", str(state), "feedback", "record", item.id,
                "--observation", observation["observation_id"],
                "--locator", "/feedback/claude.json", "--kind", "agent-verification",
            ]

            env = os.environ.copy()
            outputs = []
            for _ in range(2):
                result = subprocess.run(
                    [sys.executable, "-m", "workengine", "--json", *argv],
                    text=True, capture_output=True, env=env, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(json.loads(result.stdout))

            self.assertEqual(outputs[0], outputs[1])
            self.assertEqual(outputs[0]["observation_id"], observation["observation_id"])
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM feedback").fetchone()[0], 1)

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
            s.create_candidate('matched','demo','two','bug',dedupe_key='d2',claim_ids=['c']); s.match_candidate('matched',item2.id)
            self.assertEqual(snapshot(s,'demo')['items'][1]['candidate_mappings'],[])

    def test_candidate_cli_create_and_explicit_new_promotion_are_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/'state'; s=ServerOSStore(state); s.register_project('demo','DEM'); src=s.register_source('github','i',revision='r',locator='https://x'); o=s.record_observation(src['source_id'],'o',semantic_type='bug',content_hash='a'*64); s.record_claim('cl','/claim',['o'])
            def run(*args):
                self.assertEqual(main(['--state-dir',str(state),*args]),0)
            run('candidate','create','cand','--project','demo','--title','x','--kind','bug','--dedupe-key','d','--claim','cl','--domain','github','--external-id','i','--external-revision','r')
            run('candidate','promote','cand','--create-new','--project','demo','--prefix','DEM','--title','x','--kind','bug'); first=s.list_items('demo')[0].id
            run('candidate','promote','cand','--create-new','--project','demo','--prefix','DEM','--title','x','--kind','bug'); self.assertEqual(first,s.list_items('demo')[0].id); self.assertEqual(len(s.list_items('demo')),1)

    def test_candidate_create_new_missing_candidate_leaves_no_orphan_item(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store = ServerOSStore(state)

            with redirect_stderr(io.StringIO()):
                exit_code = main([
                    "--state-dir", str(state),
                    "candidate", "promote", "missing",
                    "--create-new",
                    "--project", "demo",
                    "--prefix", "DEM",
                    "--title", "orphan",
                    "--kind", "bug",
                ])

            self.assertEqual(exit_code, 2)
            self.assertEqual(store.list_items("demo"), [])
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM projects").fetchone()[0], 0)
                self.assertEqual(conn.execute("SELECT count(*) FROM id_sequences").fetchone()[0], 0)

    def test_candidate_create_new_rolls_back_item_when_promotion_fails(self):
        with tempfile.TemporaryDirectory() as td:
            store, _ = self._create_candidate_fixture(Path(td) / "state")
            with store.connection() as conn:
                conn.execute(
                    """CREATE TRIGGER fail_candidate_promotion
                       BEFORE UPDATE OF state ON item_candidates
                       WHEN NEW.state = 'PROMOTED'
                       BEGIN
                         SELECT RAISE(ABORT, 'injected promotion failure');
                       END"""
                )

            with self.assertRaises(sqlite3.IntegrityError):
                store.promote_candidate_to_new_item(
                    "candidate-1",
                    project="demo",
                    prefix="DEM",
                    title="new item",
                    kind="bug",
                )

            self.assertEqual(store.list_items("demo"), [])
            with store.connection() as conn:
                candidate = conn.execute(
                    "SELECT state,approved_item_id FROM item_candidates WHERE candidate_id='candidate-1'"
                ).fetchone()
                sequence = conn.execute(
                    "SELECT next_value FROM id_sequences WHERE scope='demo' AND kind='item'"
                ).fetchone()[0]
            self.assertEqual(tuple(candidate), ("NEW", None))
            self.assertEqual(sequence, 1)

    def test_candidate_create_retry_returns_one_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store, first = self._create_candidate_fixture(state)

            second = store.create_candidate(
                "candidate-1",
                "demo",
                "candidate",
                "bug",
                dedupe_key="candidate-dedupe-1",
                claim_ids=["claim-1"],
                domain="github",
                external_id="issue-1",
                external_revision="r1",
            )

            self.assertEqual(second, first)
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM item_candidates").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT count(*) FROM candidate_claims").fetchone()[0], 1)

    def test_candidate_create_cli_retry_returns_same_object_without_duplicate_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store = ServerOSStore(state)
            store.register_project("demo", "DEM")
            source = store.register_source(
                "github", "issue-cli", revision="r1", locator="https://x"
            )
            observation = store.record_observation(
                source["source_id"],
                "obs-cli",
                semantic_type="bug",
                content_hash="b" * 64,
            )
            store.record_claim("claim-cli", "/claim-cli", [observation["observation_id"]])
            command = [
                sys.executable,
                "-m",
                "workengine.cli",
                "--state-dir",
                str(state),
                "candidate",
                "create",
                "candidate-cli",
                "--project",
                "demo",
                "--title",
                "candidate from CLI",
                "--kind",
                "bug",
                "--dedupe-key",
                "candidate-cli-dedupe",
                "--claim",
                "claim-cli",
                "--domain",
                "github",
                "--external-id",
                "issue-cli",
                "--external-revision",
                "r1",
            ]

            first = subprocess.run(command, check=False, capture_output=True, text=True)
            second = subprocess.run(command, check=False, capture_output=True, text=True)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(second_payload, first_payload)
            self.assertEqual(
                {key: first_payload[key] for key in ("candidate_id", "state", "approved_item_id")},
                {
                    "candidate_id": "candidate-cli",
                    "state": "NEW",
                    "approved_item_id": None,
                },
            )
            with store.connection() as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT count(*) FROM item_candidates WHERE candidate_id='candidate-cli'"
                    ).fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT count(*) FROM candidate_claims WHERE candidate_id='candidate-cli'"
                    ).fetchone()[0],
                    1,
                )

    def test_candidate_match_retry_returns_same_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            store, _ = self._create_candidate_fixture(Path(td) / "state")
            item = store.create_item(WorkItem("DEM-0001", "demo", "existing", "bug", "/card"))

            first = store.match_candidate("candidate-1", item.id)
            second = store.match_candidate("candidate-1", item.id)

            self.assertEqual(second, first)
            self.assertEqual(second["state"], "MATCHED")
            self.assertEqual(second["approved_item_id"], item.id)

    def test_candidate_match_cli_retry_returns_same_mapping_without_duplicate_state(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store, _ = self._create_candidate_fixture(state)
            item = store.create_item(WorkItem("DEM-0001", "demo", "existing", "bug", "/card"))
            command = [
                sys.executable,
                "-m",
                "workengine.cli",
                "--state-dir",
                str(state),
                "candidate",
                "match",
                "candidate-1",
                "--item",
                item.id,
            ]

            first = subprocess.run(command, check=False, capture_output=True, text=True)
            second = subprocess.run(command, check=False, capture_output=True, text=True)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertEqual(second_payload, first_payload)
            self.assertEqual(
                {key: first_payload[key] for key in ("candidate_id", "state", "approved_item_id")},
                {
                    "candidate_id": "candidate-1",
                    "state": "MATCHED",
                    "approved_item_id": "DEM-0001",
                },
            )
            with store.connection() as conn:
                persisted = conn.execute(
                    """SELECT candidate_id,state,approved_item_id,count(*)
                       FROM item_candidates
                       WHERE candidate_id='candidate-1'
                       GROUP BY candidate_id,state,approved_item_id"""
                ).fetchone()
            self.assertEqual(tuple(persisted), ("candidate-1", "MATCHED", "DEM-0001", 1))

    def test_candidate_match_cli_returns_persisted_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store, _ = self._create_candidate_fixture(state)
            item = store.create_item(WorkItem("DEM-0001", "demo", "existing", "bug", "/card"))
            result = subprocess.run(
                [
                    sys.executable, "-m", "workengine.cli",
                    "--state-dir", str(state),
                    "candidate", "match", "candidate-1",
                    "--item", item.id,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout) | {"created_at": "ignored"},
                {
                    "candidate_id": "candidate-1",
                    "project": "demo",
                    "title": "candidate",
                    "kind": "bug",
                    "state": "MATCHED",
                    "dedupe_key": "candidate-dedupe-1",
                    "domain": "github",
                    "external_id": "issue-1",
                    "external_revision": "r1",
                    "approved_item_id": item.id,
                    "created_at": "ignored",
                },
            )

    def test_candidate_match_cli_rejects_missing_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            store = ServerOSStore(state)
            store.register_project("demo", "DEM")
            item = store.create_item(WorkItem("DEM-0001", "demo", "existing", "bug", "/card"))
            result = subprocess.run(
                [
                    sys.executable, "-m", "workengine.cli",
                    "--state-dir", str(state),
                    "candidate", "match", "missing",
                    "--item", item.id,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "INVALID_INPUT")
