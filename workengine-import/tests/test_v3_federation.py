import tempfile
import unittest
from pathlib import Path
from workengine.federation import check_inventory


class FederationV3Tests(unittest.TestCase):
    def test_missing_parent_is_placement_drift_and_auth_is_not_topology(self):
        rows = [{"domain":"feishu","object_id":"doc-1","parent_id":"missing","kind":"doc"}]
        drift = check_inventory(rows, [{"domain":"feishu","object_id":"root","kind":"folder"}])
        self.assertEqual(drift[0]["class"], "PLACEMENT_DRIFT")
        self.assertEqual(check_inventory([{"domain":"feishu","error":"AUTH_EXPIRED"}], [])[0]["class"], "AUTH_EXPIRED")

