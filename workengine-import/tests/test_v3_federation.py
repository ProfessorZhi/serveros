import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workengine import Activity, WorkItem
from workengine.store import ServerOSStore
from workengine.federation import check_inventory, inventory, proposal, publish_inventory


class FederationV3Tests(unittest.TestCase):
    def run_cli(self, state, artifacts, *args):
        env = os.environ.copy()
        env["SERVEROS_STATE_DIR"] = str(state)
        env["SERVEROS_ARTIFACTS_ROOT"] = str(artifacts)
        return subprocess.run(
            [sys.executable, "-m", "workengine", "--json", *args],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def create_run(self, root):
        store = ServerOSStore(root / "state")
        store.register_project("serveros", "SRV")
        item = store.create_item(WorkItem("SRV-0001", "serveros", "inventory", "ops", "/card"))
        store.add_activity(Activity(item.id, "inventory", "inspect"))
        run = store.begin_run(item.id, "inventory", "codex", "inventory-1", root / "artifacts")
        return store, item, run

    def test_missing_parent_is_placement_drift_and_auth_is_not_topology(self):
        rows = [{"domain":"feishu","object_id":"doc-1","parent":"missing","kind":"doc"}]
        drift = check_inventory(rows, [{"domain":"feishu","object_id":"root","kind":"folder"}])
        self.assertEqual(drift[0]["class"], "UNEXPECTED_OBJECT")
        self.assertEqual(check_inventory([{"domain":"feishu","error":"AUTH_EXPIRED"}], [])[0]["class"], "AUTH_EXPIRED")

    def test_schema_parent_match_has_no_placement_drift(self):
        d=check_inventory([{"domain":"feishu","object_id":"doc","parent":"root"}], [{"domain":"feishu","object_id":"doc","parent":"root"}])
        self.assertEqual(d, [])

    def test_schema_parent_mismatch_is_placement_drift(self):
        d=check_inventory([{"domain":"feishu","object_id":"doc","parent":"wrong"}], [{"domain":"feishu","object_id":"doc","parent":"right"}])
        self.assertEqual([x['class'] for x in d],['PLACEMENT_DRIFT'])

    def test_inventory_preserves_previous_projection_on_error(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'bad.jsonl'; dst=Path(td)/'current.jsonl'; dst.write_text('old\n')
            src.write_text('{"error":"AUTH_EXPIRED"}\n')
            with self.assertRaises(ValueError): inventory('feishu',src,dst)
            self.assertEqual(dst.read_text(),'old\n')

    def test_inventory_preserves_previous_projection_on_rate_limit(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'bad.jsonl'; dst=Path(td)/'current.jsonl'; dst.write_text('old\n')
            src.write_text('{"error":"RATE_LIMIT_TRANSIENT"}\n')
            with self.assertRaises(ValueError): inventory('github',src,dst)
            self.assertEqual(dst.read_text(),'old\n')

    def test_inventory_cli_records_traceable_current_projection_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, run = self.create_run(root)
            source = root / "evidence.jsonl"
            target = root / "federation" / "feishu" / "current.jsonl"
            source.write_text(json.dumps({
                "object_id": "doc-1", "kind": "doc", "locator": "feishu://doc/doc-1",
                "observed_at": "2026-09-09T10:00:00+00:00",
            }) + "\n")

            published = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "feishu", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/{run.id}",
            )

            self.assertEqual(published.returncode, 0, published.stderr)
            payload = json.loads(published.stdout)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertEqual(payload["sha256"], digest)
            self.assertEqual(payload["evidence_run"], f"{item.id}/{run.id}")
            self.assertEqual(payload["receipt"]["domain"], "feishu")
            self.assertEqual(payload["receipt"]["locator"], str(target))
            self.assertEqual(payload["receipt"]["sha256"], digest)
            self.assertEqual(payload["receipt"]["evidence_run"], f"{item.id}/{run.id}")
            repeated = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "feishu", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/{run.id}",
            )
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["receipt"]["receipt_id"], payload["receipt"]["receipt_id"])
            with store.connection() as conn:
                receipt = conn.execute("SELECT * FROM publication_receipts").fetchone()
                receipt_count = conn.execute("SELECT count(*) FROM publication_receipts").fetchone()[0]
            self.assertEqual(receipt["item_id"], item.id)
            self.assertEqual(receipt["run_id"], run.id)
            self.assertEqual(receipt_count, 1)

    def test_inventory_cli_rejects_unknown_run_before_publish_or_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, _ = self.create_run(root)
            source = root / "evidence.jsonl"
            target = root / "current.jsonl"
            source.write_text(json.dumps({"object_id": "repo-1", "kind": "repository"}) + "\n")
            target.write_text("previous\n")

            failed = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/RUN-9999",
            )

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(target.read_text(), "previous\n")
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM publication_receipts").fetchone()[0], 0)

    def test_inventory_cli_failure_does_not_record_publication_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, run = self.create_run(root)
            source = root / "failed.jsonl"
            target = root / "current.jsonl"
            source.write_text(json.dumps({"error": "RATE_LIMIT_TRANSIENT"}) + "\n")
            target.write_text("previous\n")

            failed = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/{run.id}",
            )

            self.assertEqual(failed.returncode, 2)
            self.assertEqual(target.read_text(), "previous\n")
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM publication_receipts").fetchone()[0], 0)

    def test_receipt_failure_restores_previous_current_without_fake_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, run = self.create_run(root)
            source = root / "evidence.jsonl"
            target = root / "current.jsonl"
            source.write_text(json.dumps({
                "object_id": "repo-1", "kind": "repository", "locator": "github://repo-1",
                "observed_at": "2026-09-09T10:00:00+00:00",
            }) + "\n")
            target.write_text("previous\n")
            with store.connection() as conn:
                conn.execute(
                    """CREATE TRIGGER fail_publication_receipt
                       BEFORE INSERT ON publication_receipts
                       BEGIN SELECT RAISE(ABORT, 'injected receipt failure'); END"""
                )

            failed = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/{run.id}",
            )

            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(target.read_text(), "previous\n")
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM publication_receipts").fetchone()[0], 0)

    def test_same_run_cannot_replace_current_with_different_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, run = self.create_run(root)
            source = root / "evidence.jsonl"
            target = root / "current.jsonl"
            evidence_run = f"{item.id}/{run.id}"

            def write_source(object_id):
                source.write_text(json.dumps({
                    "object_id": object_id, "kind": "repository", "locator": f"github://{object_id}",
                    "observed_at": "2026-09-09T10:00:00+00:00",
                }) + "\n")

            write_source("repo-1")
            first = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", evidence_run,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_bytes = target.read_bytes()

            write_source("repo-2")
            conflicting = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", evidence_run,
            )

            self.assertEqual(conflicting.returncode, 2, conflicting.stderr)
            self.assertEqual(target.read_bytes(), first_bytes)
            with store.connection() as conn:
                receipts = conn.execute("SELECT sha256 FROM publication_receipts").fetchall()
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())

    def test_schema_invalid_inventory_does_not_replace_current_or_record_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store, item, run = self.create_run(root)
            source = root / "invalid.jsonl"
            target = root / "current.jsonl"
            source.write_text(json.dumps({"object_id": "doc-1", "kind": "doc"}) + "\n")
            target.write_text("previous\n")

            failed = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "feishu", "inventory",
                "--source", str(source), "--target", str(target),
                "--evidence-run", f"{item.id}/{run.id}",
            )

            self.assertEqual(failed.returncode, 2, failed.stderr)
            self.assertEqual(target.read_text(), "previous\n")
            with store.connection() as conn:
                self.assertEqual(conn.execute("SELECT count(*) FROM publication_receipts").fetchone()[0], 0)

    def test_retry_completes_interrupted_publication_with_same_hash(self):
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "evidence.jsonl"
            target = root / "current.jsonl"
            source.write_text(json.dumps({
                "object_id": "repo-1", "kind": "repository", "locator": "github://repo-1",
                "observed_at": "2026-09-09T10:00:00+00:00",
            }) + "\n")
            target.write_text("previous\n")

            def crash_after_publish(*_):
                raise SimulatedCrash()

            with self.assertRaises(SimulatedCrash):
                publish_inventory(
                    "github", source, target, "SRV-0003/RUN-0006", crash_after_publish,
                )

            published_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            self.assertTrue(target.with_name(f".{target.name}.publish.json").exists())
            self.assertEqual(len(list(root.glob(f".{target.name}.backup.*"))), 1)
            receipt = {
                "domain": "github", "locator": str(target), "sha256": published_hash,
                "evidence_run": "SRV-0003/RUN-0006",
            }
            completed = publish_inventory(
                "github", source, target, "SRV-0003/RUN-0006", lambda *_: receipt,
            )

            self.assertEqual(completed["receipt"], receipt)
            self.assertEqual(completed["sha256"], published_hash)
            self.assertFalse(target.with_name(f".{target.name}.publish.json").exists())
            self.assertEqual(list(root.glob(f".{target.name}.backup.*")), [])

    def test_drift_envelopes_match_cross_repo_schema_contract(self):
        evidence_run = "SRV-0003/RUN-0006"
        drifts = check_inventory(
            [
                {"domain": "feishu", "object_id": "moved", "parent": "wrong"},
                {"domain": "feishu", "object_id": "extra", "parent": "root"},
                {"domain": "feishu", "object_id": "auth", "error": "AUTH_EXPIRED"},
            ],
            [
                {"domain": "feishu", "object_id": "moved", "parent": "right"},
                {"domain": "feishu", "object_id": "missing", "parent": "root"},
            ],
            evidence_run=evidence_run,
        )
        required = {
            "domain", "object_id", "observed", "desired", "class", "risk",
            "proposed_action", "approval", "evidence_run",
        }
        self.assertEqual({drift["class"] for drift in drifts}, {
            "PLACEMENT_DRIFT", "UNEXPECTED_OBJECT", "AUTH_EXPIRED", "MISSING_OBJECT",
        })
        for drift in drifts:
            self.assertEqual(set(drift), required)
            self.assertIsInstance(drift["domain"], str)
            self.assertIsInstance(drift["object_id"], str)
            self.assertIsInstance(drift["observed"], dict)
            self.assertIsInstance(drift["desired"], dict)
            self.assertIn(drift["risk"], {"low", "medium", "high", "critical"})
            self.assertIn(drift["approval"], {"automatic", "proposal-only", "explicit"})
            self.assertEqual(drift["evidence_run"], evidence_run)
        topology = [drift for drift in drifts if drift["class"] != "AUTH_EXPIRED"]
        self.assertTrue(all(drift["approval"] == "proposal-only" for drift in topology))
        proposed = proposal(drifts)
        self.assertTrue(all(set(row) == required for row in proposed))

    def test_proposal_cli_propagates_evidence_run_without_extra_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, item, run = self.create_run(root)
            observed = root / "observed.jsonl"
            desired = root / "desired.jsonl"
            observed.write_text(json.dumps({"domain": "github", "object_id": "repo", "parent": "old"}) + "\n")
            desired.write_text(json.dumps({"domain": "github", "object_id": "repo", "parent": "new"}) + "\n")
            evidence_run = f"{item.id}/{run.id}"

            result = self.run_cli(
                root / "state", root / "artifacts",
                "federation", "github", "proposal",
                "--observed", str(observed), "--desired", str(desired),
                "--evidence-run", evidence_run,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload[0]["evidence_run"], evidence_run)
            self.assertEqual(payload[0]["approval"], "proposal-only")
            self.assertNotIn("action", payload[0])

    def test_proposal_order_is_stable(self):
        rows=[{'risk':'medium','proposed_action':'z','object_id':'2'},{'risk':'high','proposed_action':'b','object_id':'3'},{'risk':'high','proposed_action':'a','object_id':'1'}]
        self.assertEqual([x['object_id'] for x in proposal(rows)],['1','3','2'])
