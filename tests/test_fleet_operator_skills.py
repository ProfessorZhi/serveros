"""The Operator Skills contract, and the one source both adapters read.

These are source-text tests, not model-behaviour tests. They pin what the
Skills say an operator must do; they cannot prove that a model follows them.
The distinction matters here because two of the behaviours are about *not*
acting: do not poll a run until it settles, and do not resend an unknown
delivery under a fresh key.

No network, no model call, no plugin install.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import tools as fleet_tools  # noqa: E402

SHARED = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
CC_DEV = ROOT / "integrations" / "fleet-cc-dev-plugin"
FIRST_INSTALL = ROOT / "integrations" / "fleet-v1-first-install"

EXPECTED_SKILLS = {
    "fleet-async-dispatch",
    "fleet-commander-orchestration",
    "fleet-create-with-grants",
    "fleet-device-and-session-discovery",
    "fleet-evidence-review",
    "fleet-plugin-update",
    "fleet-reuse-idle-session",
    "fleet-safety-boundaries",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load("fleet_operator_cc_builder", CC_DEV / "build_cc_plugin.py")
first_install = _load(
    "fleet_operator_first_install_builder", FIRST_INSTALL / "build_first_install.py")


def body(name: str) -> str:
    return (SHARED / name / "SKILL.md").read_text(encoding="utf-8")


def front_matter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    found: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            found[key.strip()] = value.strip()
    return found


class TheSourceIsOneAndShallow(unittest.TestCase):
    def test_the_shared_source_is_the_full_operator_set(self) -> None:
        self.assertEqual({p.name for p in SHARED.iterdir() if p.is_dir()},
                         EXPECTED_SKILLS)

    def test_every_skill_is_an_immediate_child_with_front_matter(self) -> None:
        for path in sorted(SHARED.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                meta = front_matter(text)
                self.assertEqual(meta.get("name"), path.parent.name)
                self.assertTrue(meta.get("description"))
                self.assertGreater(len(text), 800, "a placeholder is not a Skill")
                nested = [p for p in path.parent.rglob("SKILL.md") if p.parent != path.parent]
                self.assertEqual(nested, [])

    def test_the_cc_adapter_carries_the_shared_bytes(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="operator-cc-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "plugin"
        record = cc.generate(skills_source=SHARED, out=out,
                             next_mcp_url="https://fleet-next.example.ts.net/mcp")
        self.assertEqual(record["skills"], sorted(EXPECTED_SKILLS))
        for name in EXPECTED_SKILLS:
            self.assertEqual((out / "skills" / name / "SKILL.md").read_bytes(),
                             (SHARED / name / "SKILL.md").read_bytes())

    def test_the_portable_package_carries_the_same_skill_list(self) -> None:
        self.assertEqual(first_install.check_skills(FIRST_INSTALL / "plugin"),
                         sorted(EXPECTED_SKILLS))


class TheCommanderCoordinatesRatherThanPolls(unittest.TestCase):
    def setUp(self) -> None:
        self.text = body("fleet-commander-orchestration")
        self.lower = self.text.lower()

    def test_it_builds_a_dependency_graph(self) -> None:
        self.assertIn("dependency graph", self.lower)
        self.assertIn("execution reference", self.lower)

    def test_it_dispatches_complete_long_work_or_batched_short_work(self) -> None:
        for phrase in ("complete", "long", "independent", "batch", "short"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.lower)
        self.assertIn("do not repeatedly dispatch one short task", self.lower)

    def test_fleet_is_preferred_and_rdc_is_a_gui_fallback(self) -> None:
        self.assertIn("prefer fleet", self.lower)
        self.assertIn("rdc", self.lower)
        self.assertIn("gui", self.lower)
        self.assertIn("fallback", self.lower)

    def test_targeted_queries_come_before_global_status(self) -> None:
        self.assertIn("targeted", self.lower)
        self.assertIn("global status", self.lower)
        self.assertIn("slow node", self.lower)

    def test_a_dependency_needs_an_actual_continuing_agent(self) -> None:
        self.assertIn("continuing agent", self.lower)
        self.assertIn("verified mechanism", self.lower)
        self.assertIn("not a scheduler", self.lower)


class TheAsyncContractPreservesTheAck(unittest.TestCase):
    def setUp(self) -> None:
        self.text = body("fleet-async-dispatch")
        self.lower = self.text.lower()

    def test_all_five_execution_coordinates_are_named(self) -> None:
        for field in ("device_ref", "runtime", "session_ref", "run_id",
                      "idempotency_key"):
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", self.text)

    def test_ack_is_delivery_not_completion(self) -> None:
        self.assertIn("ack", self.lower)
        self.assertIn("delivery", self.lower)
        self.assertIn("completion", self.lower)
        self.assertIn("not a result", self.lower)

    def test_it_returns_promptly_and_does_not_poll(self) -> None:
        self.assertIn("return promptly", self.lower)
        self.assertIn("do not poll", self.lower)

    def test_timeout_and_unknown_never_authorise_a_fresh_resend(self) -> None:
        self.assertIn("timeout", self.lower)
        self.assertIn("effect_unknown", self.lower)
        self.assertIn("resend", self.lower)
        self.assertIn("fresh key", self.lower)

    def test_resume_uses_existing_execution_references(self) -> None:
        self.assertIn("resume", self.lower)
        self.assertIn("existing execution reference", self.lower)
        self.assertIn("not a persistent scheduler", self.lower)


class TheUpdateSkillSeparatesTheTwoOwners(unittest.TestCase):
    def setUp(self) -> None:
        self.text = body("fleet-plugin-update")
        self.lower = self.text.lower()

    def test_same_plugin_id_and_version_advance_are_required(self) -> None:
        self.assertIn("same plugin id", self.lower)
        self.assertIn("version", self.lower)
        self.assertIn("advance", self.lower)

    def test_readback_and_rollback_are_explicit(self) -> None:
        self.assertIn("readback", self.lower)
        self.assertIn("rollback", self.lower)
        self.assertIn("release", self.lower)

    def test_avatar_and_display_name_belong_to_the_app(self) -> None:
        self.assertIn("avatar", self.lower)
        self.assertIn("display", self.lower)
        self.assertIn("app", self.lower)
        self.assertIn("unverified", self.lower)

    def test_it_does_not_claim_a_creator_operation(self) -> None:
        self.assertIn("never uploads", self.lower)
        self.assertIn("never installs", self.lower)


class TheSkillsNameOnlyServedTools(unittest.TestCase):
    def test_every_fleet_backtick_name_is_served(self) -> None:
        served = set(fleet_tools.SERVED_BY_NAME)
        referenced: dict[str, set[str]] = {}
        for path in sorted(SHARED.glob("*/SKILL.md")):
            for match in re.finditer(r"`(fleet_[a-z_]+)`",
                                     path.read_text(encoding="utf-8")):
                referenced.setdefault(match.group(1), set()).add(path.parent.name)
        self.assertTrue(referenced)
        self.assertEqual({name: sorted(where) for name, where in referenced.items()
                          if name not in served}, {})

    def test_no_skill_invents_a_batch_or_scheduler_tool(self) -> None:
        all_text = "\n".join(p.read_text(encoding="utf-8")
                             for p in SHARED.glob("*/SKILL.md"))
        for invented in ("`fleet_batch", "`fleet_schedule", "`fleet_dispatch"):
            self.assertNotIn(invented, all_text)


if __name__ == "__main__":
    unittest.main()
