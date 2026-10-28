#!/usr/bin/env python3
"""Mechanical contract for the GPU operator Skill.

WHY THIS EXISTS
---------------
`integrations/fleet-v1-gpu-operator/SKILL.md` is a *reference*: it tells an
operator where the real GPU/lease surfaces are. Its failure mode is therefore not
"the agent ignores it" (two pressure baselines showed the agent already refuses
the dangerous asks) but **the reference being wrong**: naming a subcommand that
does not exist, repeating a claim the code does not make, or implying a serving
stack is installed when it is not.

Prose cannot prevent that, and prose is what a reader would have to trust. So
every claim the skill makes about this repository is checked here against the
repository, and every check is paired with a deliberately broken copy of the
skill that must be rejected — a check that only ever sees good input cannot be
told apart from one that is unreachable.

No network, no GPU query, no build. This reads two files and the CLI module.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "integrations" / "fleet-v1-gpu-operator" / "SKILL.md"
CLI = ROOT / "runtime" / "cli.py"
CORE_BRIDGE = ROOT / "runtime" / "fleet_core_bridge.py"
INVENTORY = ROOT / "runtime" / "inventory.py"

#: `description` longer than this stops being a trigger and becomes a summary.
DESCRIPTION_LIMIT = 500
#: Whole frontmatter budget, from the skill spec.
FRONTMATTER_LIMIT = 1024
#: A reference that costs more than this is a document, not a trigger.
BODY_LIMIT = 14_000
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Every `serverosctl <group> <verb>` the skill may name. Parsed from the skill,
#: then checked against the CLI's own registrations.
CITED_RE = re.compile(r"serverosctl\s+([a-z][a-z-]+)\s+([a-z][a-z-]+)")


def flat(text: str) -> str:
    """Whitespace-collapsed, lowercased text for phrase assertions.

    A phrase in a Markdown document is broken by line wrapping and emphasis, so
    asserting on the raw text tests the author's line length, not the content.
    """
    return re.sub(r"[\s*`]+", " ", text.lower())


def front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    block = text[4:end]
    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    return fields, text[end + 4:]


def cli_registers(group: str, verb: str) -> bool:
    """Whether `serverosctl <group> <verb>` is actually registered."""
    if not CLI.is_file():
        return False
    text = CLI.read_text(encoding="utf-8")
    group_ok = f'add_parser("{group}"' in text or f'_parser("{group}"' in text or f'add_parser(\n        "{group}"' in text
    verb_ok = f'"{verb}"' in text
    return group_ok and verb_ok


def problems_with_skill(text: str) -> list[str]:
    """Every way the Skill stops being a correct reference."""
    problems: list[str] = []
    meta, body = front_matter(text)

    name = meta.get("name", "")
    if not NAME_RE.match(name):
        problems.append(f"name {name!r} is not lowercase letters, digits and hyphens")

    description = meta.get("description", "")
    if not description:
        problems.append("description is missing")
    else:
        if not description.startswith("Use when"):
            # The spec's discovery rule: a description that summarises the
            # workflow is followed *instead of* the skill being read.
            problems.append("description must start with 'Use when', not summarise the workflow")
        if len(description) > DESCRIPTION_LIMIT:
            problems.append(f"description is {len(description)} chars, over {DESCRIPTION_LIMIT}")
        if len(meta) and sum(len(k) + len(v) + 4 for k, v in meta.items()) > FRONTMATTER_LIMIT:
            problems.append("frontmatter exceeds the 1024-character budget")

    if len(body) > BODY_LIMIT:
        problems.append(f"body is {len(body)} bytes, over the {BODY_LIMIT} reference budget")

    # Every cited serverosctl verb must exist. This is the anti-invention check:
    # a reference that names a subcommand nobody implemented is worse than none,
    # because the operator pastes it and gets an argparse error at 2am.
    for group, verb in sorted(set(CITED_RE.findall(text))):
        if not cli_registers(group, verb):
            problems.append(f"cites `serverosctl {group} {verb}` which the CLI does not register")

    # Claims about the code must be claims the code makes.
    bridge = CORE_BRIDGE.read_text(encoding="utf-8") if CORE_BRIDGE.is_file() else ""
    if "gpu.readonly" not in bridge:
        problems.append("the skill's gpu.readonly discussion has no code behind it")
    if "unavailable" not in bridge:
        problems.append("the skill claims an unavailable reading is possible; the bridge disagrees")

    # A serving stack that is not installed must not be presented as available.
    #
    # Sentence-level, not line-level: a first version checked lines, and a mutant
    # that appended "Start it with `vllm serve <model>`." to a line already
    # saying "the serving stack is not installed" passed — the qualifier was on
    # the same line as the command.
    for stack in ("vllm", "ollama"):
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if stack not in sentence.lower():
                continue
            if not re.search(r"(?i)\bno\b|\bnot\b|absent|never", sentence):
                problems.append(
                    f"mentions `{stack}` in a sentence with no negation: "
                    f"{sentence.strip()[:70]}")
                break

    # No second scheduler: the skill must not tell anyone to build a lockfile.
    for forbidden in ("flock ", "lockfile", "mkdir -p /var/lock", "gpu-manager"):
        if forbidden in text.lower():
            problems.append(f"instructs a second coordination mechanism: {forbidden!r}")

    return problems


class TheGpuOperatorSkillIsACorrectReference(unittest.TestCase):
    def setUp(self) -> None:
        if not SKILL.is_file():
            self.skipTest("the GPU operator skill is not in this tree")
        self.text = SKILL.read_text(encoding="utf-8")

    def test_the_committed_skill_satisfies_the_contract(self) -> None:
        self.assertEqual(problems_with_skill(self.text), [])

    def test_an_invented_subcommand_is_refused(self) -> None:
        """The failure this file exists for: a plausible, nonexistent verb."""
        broken = self.text.replace(
            "serverosctl lease inspect <resource>",
            "serverosctl gpu reserve --card 0")
        self.assertTrue(any("does not register" in p for p in problems_with_skill(broken)),
                        "a reference may not name a subcommand that was never implemented")

    def test_a_workflow_summary_description_is_refused(self) -> None:
        """The spec's discovery rule, made mechanical."""
        broken = self.text.replace(
            "description: Use when",
            "description: Preserve the exact capacity readings and then",
            1)
        self.assertTrue(any("must start with 'Use when'" in p
                            for p in problems_with_skill(broken)),
                        "a description that summarises the workflow is followed "
                        "instead of the skill being read")

    def test_claiming_an_uninstalled_stack_is_refused(self) -> None:
        broken = self.text.replace(
            "This host has no `vllm`\nand no `ollama` on `PATH`; a command that assumes them is a command nobody ran.",
            "Start it with `vllm serve <model>`.")
        if broken == self.text:
            self.skipTest("the anchor text moved; check the vllm line by hand")
        self.assertTrue(any("no negation" in p
                            for p in problems_with_skill(broken)),
                        "this host has neither stack; a reference may not assume one")

    def test_a_second_scheduler_is_refused(self) -> None:
        broken = self.text + "\n\nTake `flock /var/lock/gpu.lock` before starting.\n"
        self.assertTrue(any("second coordination mechanism" in p
                            for p in problems_with_skill(broken)),
                        "the lease is the coordinator; a lockfile beside it is a "
                        "second source of truth")

    def test_the_skill_does_not_claim_a_capability_operation_exists(self) -> None:
        """#39 records this as a semantic gap, not an invitation."""
        text = flat(self.text)
        self.assertIn("no gpu or model-deployment operation", text)
        self.assertIn("not authorisation to invent", text)

    def test_the_two_baseline_behaviours_are_kept(self) -> None:
        """The pressure baselines already refused; the reference must not undo it."""
        text = flat(self.text)
        self.assertIn("may not kill another lane's process", text)
        self.assertIn("not_measured", text)
        self.assertIn("ownership: company", text)


if __name__ == "__main__":
    unittest.main()
