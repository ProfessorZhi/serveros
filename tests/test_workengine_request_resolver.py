import tempfile
import unittest
from pathlib import Path

from workengine.engine import ServerOS
from workengine.request import WorkRequest
from workengine.resolver import AbilityResolver
from workengine.store import ServerOSStore


class AbilityResolverTest(unittest.TestCase):
    def test_resolver_starts_empty_and_accepts_external_bindings(self):
        resolver = AbilityResolver()
        self.assertIsNone(resolver.resolve_capability("workspace.converge"))
        resolver.register("workspace.converge", "provider-id", version="2")
        binding = resolver.resolve_capability("workspace.converge")
        self.assertEqual(binding.resolver, "provider-id")
        self.assertEqual(resolver.capabilities(), ["workspace.converge"])


class WorkRequestTest(unittest.TestCase):
    def test_request_creates_generic_item_without_pr_or_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            server = ServerOS(store=ServerOSStore(td), artifacts_root=Path(td) / "artifacts")
            server.register_project("serveros", "SRV")
            item = WorkRequest(server).submit(
                project="serveros",
                title="converge directories",
                kind="ops",
            )
            self.assertEqual(item.id, "SRV-0001")
            self.assertEqual(item.kind, "ops")
            self.assertFalse(hasattr(item, "requirement"))


if __name__ == "__main__":
    unittest.main()
