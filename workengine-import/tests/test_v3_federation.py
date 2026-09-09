import tempfile
import unittest
from pathlib import Path
from workengine.federation import check_inventory, inventory, proposal


class FederationV3Tests(unittest.TestCase):
    def test_missing_parent_is_placement_drift_and_auth_is_not_topology(self):
        rows = [{"domain":"feishu","object_id":"doc-1","parent_id":"missing","kind":"doc"}]
        drift = check_inventory(rows, [{"domain":"feishu","object_id":"root","kind":"folder"}])
        self.assertEqual(drift[0]["class"], "UNEXPECTED_OBJECT")
        self.assertEqual(check_inventory([{"domain":"feishu","error":"AUTH_EXPIRED"}], [])[0]["class"], "AUTH_EXPIRED")

    def test_same_object_wrong_parent_is_placement_drift(self):
        d=check_inventory([{"domain":"feishu","object_id":"doc","parent_id":"wrong"}], [{"domain":"feishu","object_id":"doc","parent_id":"right"}])
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

    def test_proposal_order_is_stable(self):
        rows=[{'risk':'medium','action':'z','object_id':'2'},{'risk':'high','action':'b','object_id':'3'},{'risk':'high','action':'a','object_id':'1'}]
        self.assertEqual([x['object_id'] for x in proposal(rows)],['1','3','2'])
