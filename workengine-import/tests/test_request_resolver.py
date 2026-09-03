"""Tests for the WorkRequest facade and the Ability Resolver seam."""

from __future__ import annotations

import unittest
from pathlib import Path

from workengine.engine import WorkEngine
from workengine.model import State
from workengine.request import WorkRequest
from workengine.resolver import AbilityResolver, CapabilityBinding


class WorkRequestTest(unittest.TestCase):
    def test_submit_creates_persisted_new_item(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            engine = WorkEngine(state_dir=Path(tmp) / "state")
            req = WorkRequest(engine=engine)
            item = req.submit("Autonomous delivery needs a resumable work engine.")
            self.assertEqual(item.status, State.NEW)
            # Persisted -> a fresh engine over the same dir sees it.
            again = WorkEngine(state_dir=Path(tmp) / "state").get(item.id)
            self.assertEqual(again.requirement, "Autonomous delivery needs a resumable work engine.")

    def test_title_defaults_to_requirement(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            req = WorkRequest(engine=WorkEngine(state_dir=Path(tmp) / "state"))
            item = req.submit("Just a requirement.")
            self.assertEqual(item.title, "Just a requirement.")

    def test_submit_accepts_plan(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            req = WorkRequest(engine=WorkEngine(state_dir=Path(tmp) / "state"))
            item = req.submit("r", plan=["a", "b", "c"])
            self.assertEqual(item.plan, ["a", "b", "c"])


class AbilityResolverTest(unittest.TestCase):
    def test_resolve_capability_stable_interface(self):
        resolver = AbilityResolver()
        resolver.register("produce_report", "internal/handler-report")
        binding = resolver.resolve_capability("produce_report")
        self.assertIsInstance(binding, CapabilityBinding)
        self.assertEqual(binding.capability, "produce_report")

    def test_unknown_capability_returns_none(self):
        resolver = AbilityResolver()
        self.assertIsNone(resolver.resolve_capability("does_not_exist"))

    def test_no_skill_names_hardcoded(self):
        # The resolver registry starts empty and only resolves what is
        # registered from outside. There must be no built-in skill identifiers.
        resolver = AbilityResolver()
        self.assertEqual(resolver.capabilities(), [])
        for name in ("muc-playwright-e2e", "dev-doctor", "feature-development"):
            self.assertIsNone(resolver.resolve_capability(name))

    def test_capabilities_lists_registered(self):
        resolver = AbilityResolver()
        resolver.register("z", "h")
        resolver.register("a", "h")
        self.assertEqual(resolver.capabilities(), ["a", "z"])


if __name__ == "__main__":
    unittest.main()
