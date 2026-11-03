"""recover_after_disconnect: a commander that lost its connection picks the run
up again by re-sending under the same idempotency key — and nothing is sent twice,
even across a restart of this surface, when the durable claim store is bound."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp.config import DispatchBinding, load_config  # noqa: E402
from fleet_mcp.fleet import FleetService  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.runtime import durable_attempts  # noqa: E402
from test_fleet_mcp_surface import TOKYO, Harness  # noqa: E402


@unittest.skipIf(os.name == "nt", "the durable owner uses a POSIX file lock")
class RecoverAcrossARestart(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.key = SessionKey.compose(TOKYO, "claude", "owned-session")
        self.h = Harness(self.root, send_allow=(self.key.ref,), allow_tokyo_send=True)
        object.__setattr__(self.h.config, "dispatch",
                           DispatchBinding(store_path=self.root / "state" / "dispatch.json"))

    def tearDown(self):
        self._td.cleanup()

    def service(self):
        """A fresh surface over the same binding: what a restart looks like."""
        return FleetService(self.h.config, providers={TOKYO: self.h.tokyo},
                            attempts=durable_attempts(self.h.config))

    def test_the_same_key_after_a_restart_returns_the_first_receipt(self):
        first = self.service().send_to_session(self.h.principal(), self.key,
                                               message="run the suite", idempotency_key="task-42")
        again = self.service().send_to_session(self.h.principal(), self.key,
                                               message="run the suite", idempotency_key="task-42")
        self.assertEqual(len(self.h.tokyo.sent), 1)
        self.assertEqual(again.get("run_id"), first.get("run_id"))
        self.assertEqual(first.get("idempotency_durability"), "AGENT_CONTROL_STORE")

    def test_without_the_store_a_restart_forgets(self):
        object.__setattr__(self.h.config, "dispatch", None)
        self.assertIsNone(durable_attempts(self.h.config))


class TheBinding(unittest.TestCase):
    def test_dispatch_store_path(self):
        with tempfile.TemporaryDirectory() as td:
            binding = Path(td) / "fleet.yaml"
            binding.write_text("schema: serveros/fleet-mcp.local/v1\n"
                               f"device_ref: {TOKYO}\n"
                               f"dispatch:\n  store_path: {td}/dispatch.json\n", encoding="utf-8")
            self.assertEqual(load_config(binding).dispatch.store_path, Path(td) / "dispatch.json")


if __name__ == "__main__":
    unittest.main()
