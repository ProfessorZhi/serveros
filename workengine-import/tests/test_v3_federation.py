import tempfile
import unittest
from pathlib import Path
from workengine.federation import check_inventory, inventory


class FederationV3Tests(unittest.TestCase):
    def test_missing_parent_is_placement_drift_and_auth_is_not_topology(self):
        rows = [{"domain":"feishu","object_id":"doc-1","parent_id":"missing","kind":"doc"}]
        drift = check_inventory(rows, [{"domain":"feishu","object_id":"root","kind":"folder"}])
        self.assertEqual(drift[0]["class"], "PLACEMENT_DRIFT")
        self.assertEqual(check_inventory([{"domain":"feishu","error":"AUTH_EXPIRED"}], [])[0]["class"], "AUTH_EXPIRED")

    def test_inventory_preserves_previous_projection_on_error(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'bad.jsonl'; dst=Path(td)/'current.jsonl'; dst.write_text('old\n')
            src.write_text('{"error":"AUTH_EXPIRED"}\n')
            with self.assertRaises(ValueError): inventory('feishu',src,dst)
            self.assertEqual(dst.read_text(),'old\n')
