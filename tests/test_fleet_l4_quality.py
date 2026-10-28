#!/usr/bin/env python3
"""L4 operator-Skill and plugin/MCP quality gates.

These are source-text and packaging checks. They do not claim model behaviour:
a Skill description is a routing hint, and a green static check is not a
pressure-scenario pass. The model-behaviour layers that were not run are
recorded in the audit report as NOT_RUN.

The word budgets are deliberate token-cost contracts, not style preferences:
the shared Skills are loaded into every operator session, and the measured
pre-change sizes in the audit are 1,969 / 1,800 words for safety/create.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import version as fleet_version  # noqa: E402
from fleet_mcp import tools as fleet_tools  # noqa: E402

SHARED = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
PLUGIN = ROOT / "plugins" / "serveros-fleet-ingress"
PLUGIN_SKILLS = PLUGIN / "skills"

#: Skills whose descriptions currently summarize a workflow instead of naming
#: the triggering situation. `fleet-async-dispatch` is #410-owned and excluded
#: from this branch's edit; its own owner can apply the same rule.
TRIGGER_ONLY = (
    "fleet-commander-orchestration",
    "fleet-create-with-grants",
    "fleet-device-and-session-discovery",
    "fleet-evidence-review",
    "fleet-plugin-update",
    "fleet-reuse-idle-session",
    "fleet-safety-boundaries",
)
BUDGETED = {
    "fleet-create-with-grants": 1500,
    "fleet-commander-orchestration": 1300,
    "fleet-safety-boundaries": 1500,
}


def body(name: str) -> str:
    return (SHARED / name / "SKILL.md").read_text(encoding="utf-8")


def description(name: str) -> str:
    text = body(name)
    end = text.find("\n---", 4)
    if end < 0:
        raise AssertionError(f"{name}: unterminated frontmatter")
    front = text[4:end]
    match = re.search(r"(?m)^description:\s*(.+)$", front)
    if not match:
        raise AssertionError(f"{name}: no description")
    return match.group(1).strip()


def words(text: str) -> int:
    return len(text.split())


class TriggerDescriptionsNameTheSituation(unittest.TestCase):
    def test_command_skill_descriptions_start_with_use_when(self) -> None:
        for name in TRIGGER_ONLY:
            with self.subTest(skill=name):
                self.assertTrue(
                    description(name).lower().startswith("use when"),
                    f"{name} description starts with a workflow summary; a "
                    f"trigger-only description is the routing contract")

    def test_descriptions_do_not_summarize_a_workflow(self) -> None:
        # A description that teaches the process becomes a shortcut the model
        # follows instead of reading the Skill body.
        for name in TRIGGER_ONLY:
            with self.subTest(skill=name):
                self.assertNotRegex(
                    description(name),
                    r"(?i)\b(coordinate|create a fleet session|update the app-backed)\b",
                    f"{name} description still summarizes the workflow")


class OwnerMaxIsExplainedOnce(unittest.TestCase):
    def test_universal_bypass_sentence_has_one_owner(self) -> None:
        owners = [name for name in (p.parent.name for p in SHARED.glob("*/SKILL.md"))
                  if "not a universal bypass" in body(name)]
        self.assertEqual(owners, ["fleet-safety-boundaries"],
                         "owner-max scope is explained in one place; other "
                         "Skills should link to it, not restate it")


class TokenBudget(unittest.TestCase):
    def test_budgeted_skills_stay_within_their_word_contract(self) -> None:
        for name, limit in sorted(BUDGETED.items()):
            with self.subTest(skill=name):
                actual = words(body(name))
                self.assertLessEqual(
                    actual, limit,
                    f"{name} is {actual} words; the L4 budget is {limit}")


class DispatchSemantics(unittest.TestCase):
    def test_no_skill_turns_unknown_into_success(self) -> None:
        pattern = re.compile(
            r"(?is)\bunknown\b[^.]{0,100}\b(?:success|succeeded|complete|done)\b")
        for path in sorted(SHARED.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                for sentence in re.split(r"(?<=[.!?])\s+", path.read_text(encoding="utf-8")):
                    if pattern.search(sentence) and not re.search(
                            r"(?i)\b(?:not|never|no)\b", sentence):
                        self.fail(f"{path.parent.name}: {sentence[:160]}")

    def test_no_skill_instructs_a_positive_wait_loop(self) -> None:
        pattern = re.compile(
            r"(?is)\b(?:wait|poll|read[^.]{0,40}back)\b[^.]{0,50}"
            r"\b(?:until|till)\b[^.]{0,30}\b(?:settle|complete|finish)\b")
        for path in sorted(SHARED.glob("*/SKILL.md")):
            for sentence in re.split(r"(?<=[.!?])\s+", path.read_text(encoding="utf-8")):
                if pattern.search(sentence) and not re.search(
                        r"(?i)\b(?:do not|never|not)\b", sentence):
                    self.fail(f"{path.parent.name}: {sentence[:160]}")

    def test_no_skill_hardcodes_a_tool_count(self) -> None:
        for path in sorted(SHARED.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"),
                                    r"\b(?:22|33|34|44)\b")


class PluginAndMcpContract(unittest.TestCase):
    def test_source_and_plugin_skills_are_byte_identical(self) -> None:
        shared = sorted(p.parent.name for p in SHARED.glob("*/SKILL.md"))
        packaged = sorted(p.parent.name for p in PLUGIN_SKILLS.glob("*/SKILL.md"))
        self.assertEqual(shared, packaged)
        for name in shared:
            with self.subTest(skill=name):
                left = hashlib.sha256((SHARED / name / "SKILL.md").read_bytes()).hexdigest()
                right = hashlib.sha256((PLUGIN_SKILLS / name / "SKILL.md").read_bytes()).hexdigest()
                self.assertEqual(left, right, f"{name} is a second copy")

    def test_manifest_identity_is_shared(self) -> None:
        portable = json.loads(
            (ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "plugin.json")
            .read_text(encoding="utf-8"))
        claude = json.loads(
            (PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        for field in ("name", "version"):
            with self.subTest(field=field):
                self.assertEqual(portable[field], claude[field])
        self.assertEqual(portable["name"], "serveros-fleet-ingress")

    def test_served_surface_is_composed_once_and_matches_its_revision(self) -> None:
        self.assertEqual(len(fleet_tools.SERVED_TOOLS),
                         len(fleet_tools.SERVED_BY_NAME),
                         "a composed tool name is duplicated; one is unreachable")
        self.assertEqual(fleet_version.TOOL_SURFACE_REVISION, "fleet-mcp-7")
        self.assertEqual(len(fleet_tools.NATIVE_MIRROR_TOOLS), 31)
        self.assertEqual(len(fleet_tools.SERVED_TOOLS), 44)

class FixtureDoesNotFreezeAToolCount(unittest.TestCase):
    def test_fixture_docstring_reads_the_surface_instead_of_hardcoding(self) -> None:
        text = (ROOT / "integrations" / "fleet-cc-dev-plugin" /
                "fixture_stdio_server.py").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?i)all\s+34\s+tools")


class CrossRuntimeDiscoveryIsExplicit(unittest.TestCase):
    def test_discovery_names_both_runtimes_and_the_codex_alias(self) -> None:
        text = body("fleet-device-and-session-discovery")
        for token in ("Claude", "Codex", "alias", "runtime"):
            with self.subTest(token=token):
                self.assertIn(token, text)
        self.assertIn("do not infer it from the base runtime",
                      " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
