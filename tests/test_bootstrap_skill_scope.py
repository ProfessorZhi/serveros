"""§25 — the `serveros` bootstrap Skill stays a bootstrap.

§25 fixes what the bootstrap Skill is for (discover node, context.current,
path.resolve, runtime status, capability resolve, load specialist,
policy/admission) and what it must not become (a Docker cleanup procedure, a
workspace lifecycle manual, a Feishu procedure, a testing methodology).

The distinction is not "does it mention Docker" — it mentions Docker twice, as a
PROHIBITION, which is policy and is in scope. The distinction is whether the
Skill *instructs a domain's work* or *delegates it*. So the rules below are:

  1. every executable example calls the control plane and nothing else
  2. each of §25's responsibilities has a surface here
  3. a forbidden mutation appears only as a prohibition, never as a step
  4. the file stays small enough to be read before acting

Rule 4 is a ratchet rather than a fact: the budget is not a measurement of what
the file is, it is a decision about what it may become. A bootstrap that grows a
section per domain has stopped being a bootstrap, and the failure is gradual, so
nothing else would catch it.
"""
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "serveros" / "SKILL.md"

# §25's responsibilities, each with a surface that must appear in the Skill.
RESPONSIBILITIES = {
    "discover node": r"device show",
    "context.current": r"context current",
    "path.resolve": r"path resolve",
    "path admission": r"path admit",
    "runtime status": r"runtime status",
    "capability resolve": r"ability[. ]resolve",
    "load specialist": r"runtime-management|workspace-management|test-management",
    "policy": r"protected|protection",
    # §24's Agent fast path, by tool name rather than by prose. A bootstrap that
    # describes capability discovery without naming the four tools an Agent can
    # actually call leaves the reader to find them, which is the context cost
    # the fast path exists to remove — and the tool names are exactly what
    # drifts when the surface changes, so they are what a gate has to read.
    "fast path · context.current": r"`context\.current`",
    "fast path · ability.search": r"`ability\.search`",
    "fast path · ability.inspect": r"`ability\.inspect`",
    "fast path · ability.resolve": r"`ability\.resolve`",
    # The Item is inferred from a cwd the CALLER states and never from the
    # process's own directory. Stated in the Skill because it is the one thing
    # an Agent can get wrong here without any error being raised.
    "fast path · the Item comes from the stated cwd": r"cwd",
}

# Mutating actions that belong to a specialist, not to the bootstrap. Each may
# still APPEAR — as a prohibition. What it may not do is read as an instruction.
FORBIDDEN_MUTATIONS = (
    "docker system prune", "docker volume prune", "docker rmi", "docker rm",
    "docker kill", "docker stop", "git clean", "git reset", "git stash",
    "rm -rf", "pytest",
)

# Lines that mark a statement as prohibitive rather than instructive.
NEGATIONS = ("不 ", "不要", "禁止", "永远不", "不得", "never")

FENCE = re.compile(r"^```(\w*)\s*$")
CONTROL_PLANE = ("serverosctl", "serverosd")


def _lines() -> list[str]:
    return SKILL.read_text(encoding="utf-8").splitlines()


def _fenced_blocks() -> list[tuple[str, list[str]]]:
    blocks, lang, current = [], None, []
    for line in _lines():
        if lang is None:
            match = FENCE.match(line)
            if match:
                lang, current = match.group(1), []
            continue
        if FENCE.match(line):
            blocks.append((lang, current))
            lang = None
            continue
        current.append(line)
    return blocks


class BootstrapSkillScopeTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")
        self.lines = _lines()

    def test_the_skill_exists_and_is_readable(self):
        self.assertTrue(SKILL.is_file(), "skills/serveros/SKILL.md is missing")

    def test_executable_examples_call_the_control_plane_and_nothing_else(self):
        offenders = []
        for lang, block in _fenced_blocks():
            if lang not in ("bash", "sh", "shell", "console"):
                continue
            for line in block:
                command = line.split("#", 1)[0].strip()
                if not command:
                    continue
                if not command.startswith(CONTROL_PLANE):
                    offenders.append(command)
        self.assertEqual(
            offenders,
            [],
            "§25: the bootstrap Skill shows how to call the control plane. A shell "
            "example for anything else is a domain procedure that belongs in a "
            "specialist — and a rule written only in a Skill is a rule no other "
            "consumer honours.\n" + "\n".join(f"  {o}" for o in offenders),
        )

    def test_every_section_25_responsibility_has_a_surface_here(self):
        missing = [
            name for name, pattern in RESPONSIBILITIES.items()
            if not re.search(pattern, self.text, re.IGNORECASE)
        ]
        self.assertEqual(
            missing,
            [],
            f"§25 responsibilities with no surface in the bootstrap Skill: {missing}",
        )

    def test_a_forbidden_mutation_appears_only_as_a_prohibition(self):
        offenders = []
        for number, line in enumerate(self.lines, start=1):
            lowered = line.lower()
            for forbidden in FORBIDDEN_MUTATIONS:
                if forbidden not in lowered:
                    continue
                if not any(marker.lower() in lowered for marker in NEGATIONS):
                    offenders.append((number, forbidden, line.strip()))
        self.assertEqual(
            offenders,
            [],
            "§25: a domain's mutating action reads as an instruction here. It may be "
            "named as a prohibition (policy, in scope) or delegated to its "
            "specialist (routing, in scope); it may not be a step.\n"
            + "\n".join(f"  {n}: {f!r} in {t}" for n, f, t in offenders),
        )

    def test_the_bootstrap_stays_small_enough_to_read_before_acting(self):
        budget = 100
        self.assertLessEqual(
            len(self.lines),
            budget,
            f"§25: the bootstrap Skill is {len(self.lines)} lines, over its {budget}-line "
            "budget. Growth here is drift, not an accident: a bootstrap that grows a "
            "section per domain has become the thing it routes to. Move the section "
            "to its specialist and raise the budget deliberately, or delete it.",
        )

    def test_it_delegates_rather_than_owning_other_domains_data(self):
        """The affirmative half: it must SAY that it delegates."""
        self.assertRegex(
            self.text,
            r"不抢占|owner|权威",
            "§25: the bootstrap must state that the specialists own their data, "
            "not merely avoid mentioning it.",
        )


if __name__ == "__main__":
    unittest.main()
