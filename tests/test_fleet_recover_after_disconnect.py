"""recover_after_disconnect: a commander that lost its connection picks the run
up again by re-sending under the same idempotency key, and nothing is sent twice
— even across a restart of this surface when it runs over the ServerOS owner
ledger (`bin/serveros-fleet-owned`, durability AGENT_CONTROL_STORE)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp.fleet import FleetService  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from test_fleet_dispatch_claims import owner_store  # noqa: E402
from test_fleet_mcp_surface import TOKYO, Harness  # noqa: E402


@unittest.skipIf(os.name == "nt", "the owner ledger uses a POSIX file lock")
class RecoverAcrossARestart(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.key = SessionKey.compose(TOKYO, "claude", "owned-session")
        self.h = Harness(self.root, send_allow=(self.key.ref,), allow_tokyo_send=True)
        self.store_path = self.root / "agent-control" / "store.json"
        self.store_path.parent.mkdir()

    def tearDown(self):
        self._td.cleanup()

    def service(self):
        """A fresh surface over the same owner ledger: what a restart looks like."""
        return FleetService(self.h.config, providers={TOKYO: self.h.tokyo},
                            attempts=owner_store(self.store_path))

    def test_the_same_key_after_a_restart_returns_the_first_receipt(self):
        first = self.service().send_to_session(self.h.principal(), self.key,
                                               message="run the suite", idempotency_key="task-42")
        again = self.service().send_to_session(self.h.principal(), self.key,
                                               message="run the suite", idempotency_key="task-42")
        self.assertEqual(len(self.h.tokyo.sent), 1)
        self.assertEqual(again.get("run_id"), first.get("run_id"))
        self.assertEqual(first.get("idempotency_durability"), "AGENT_CONTROL_STORE")

    def test_a_process_local_surface_forgets_on_restart(self):
        plain = FleetService(self.h.config, providers={TOKYO: self.h.tokyo})
        plain.send_to_session(self.h.principal(), self.key, message="x", idempotency_key="k")
        FleetService(self.h.config, providers={TOKYO: self.h.tokyo}).send_to_session(
            self.h.principal(), self.key, message="x", idempotency_key="k")
        self.assertEqual(len(self.h.tokyo.sent), 2)


if __name__ == "__main__":
    unittest.main()
