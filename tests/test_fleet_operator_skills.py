"""Static packaging and consistency checks for the shared Operator Skills.

These are source-text and package-byte checks, not model-behaviour evidence.
The consistency checks are deliberately small: each one names a contradiction
that was observed in the old text and carries both a positive control (the old
snippet must be caught) and a negative control (the current Skill must not be).

No network, no model call, no plugin install.
"""
from __future__ import annotations

import importlib.util
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

# The exact old instructions this revision had to remove. They are kept as
# positive controls so a future edit cannot quietly reintroduce them.
OLD_POSITIVE_WAIT = (
    "Keep the run id and read it back (`fleet_get_run`) until it settles.")
OLD_FORCED_PROBE = (
    "Call, in this order:\n"
    "1. `fleet_status`\n"
    "2. `fleet_list_devices`\n"
    "3. `fleet_list_sessions`")
OLD_PER_DEVICE_REGISTRY = (
    "the agent on that device uses its own ability family, with its own "
    "registry and its own responsibilities.")
OLD_HARDCODED_MODEL = (
    "on this fleet the admin runtime's default is "
    "`deepseek-v4-flash-vision-anthropic`.")
OLD_SINGLE_INTENT = (
    "One message, one intent. A message that asks for three things and reports "
    "partially back is a message whose result cannot be accepted or refused.")


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


# -- the small static consistency lints -----------------------------------

POSITIVE_WAIT = re.compile(
    r"(?is)\b(?:keep the run id|read (?:it|the run|the result) back)\b"
    r".{0,120}\buntil it settles\b")


def has_positive_wait(text: str) -> bool:
    """True when the text tells the caller to keep reading until settlement."""
    return bool(POSITIVE_WAIT.search(text))


def has_forced_global_probe(text: str) -> bool:
    """True when a global status/list probe is a mandatory first step."""
    lowered = text.lower()
    if "call, in this order" in lowered:
        return True
    status = lowered.find("fleet_status")
    devices = lowered.find("fleet_list_devices")
    return 0 <= status < devices and "not a prerequisite" not in lowered


def has_per_device_registry(text: str) -> bool:
    return bool(re.search(
        r"own ability family, with its own registry|per-device ability "
        r"registry|its own registry", text, re.I))


def has_hardcoded_fleet_model(text: str) -> bool:
    return "deepseek-v4-flash-vision-anthropic" in text


def has_single_intent_restriction(text: str) -> bool:
    lowered = text.lower()
    return "one message, one intent" in lowered or "three things" in lowered


PER_CALL_APPROVAL = re.compile(
    r"(?is)(?:ask|approval|approve|approval_required).{0,100}"
    r"(?:before|each|per).{0,60}(?:create|send|dispatch)")


def requires_per_call_approval(text: str) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if PER_CALL_APPROVAL.search(sentence) and not re.search(
                r"(?i)\b(?:do not|does not|never|not)\b", sentence):
            return True
    return False


def hardcodes_tool_count(text: str) -> bool:
    return bool(re.search(r"\b33\b", text)) or "thirty-three" in text.lower()


def names_invented_project_verb(text: str) -> bool:
    # `fleet_create_project` is served now, so naming it is no longer an
    # invention; `test_every_fleet_backtick_name_is_served` keeps that honest.
    return any(name in text for name in
               ("fleet_project_create", "fleet_new_project"))


def has_capability_discovery(text: str) -> bool:
    lowered = text.lower()
    return (("tools/list" in lowered or "tool list" in lowered)
            and "capability" in lowered)


class TheSourceIsOneAndPackaged(unittest.TestCase):
    def test_the_shared_source_has_the_expected_members(self) -> None:
        self.assertEqual({p.name for p in SHARED.iterdir() if p.is_dir()},
                         EXPECTED_SKILLS)

    def test_every_skill_has_legal_front_matter_and_a_body(self) -> None:
        for path in sorted(SHARED.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                meta = front_matter(text)
                self.assertEqual(meta.get("name"), path.parent.name)
                self.assertTrue(meta.get("description"))
                self.assertTrue(text.split("\n---", 1)[1].strip())
                nested = [p for p in path.parent.rglob("SKILL.md")
                          if p.parent != path.parent]
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

    def test_the_portable_package_lists_the_same_skills(self) -> None:
        self.assertEqual(first_install.check_skills(FIRST_INSTALL / "plugin"),
                         sorted(EXPECTED_SKILLS))


class TheOldContradictionsAreGone(unittest.TestCase):
    """Each test has an old positive and a current negative control."""

    def test_reuse_does_not_wait_until_settled(self) -> None:
        current = body("fleet-reuse-idle-session")
        self.assertTrue(has_positive_wait(OLD_POSITIVE_WAIT))
        self.assertFalse(has_positive_wait(current))
        self.assertIn("queued_message_uuid", current)
        self.assertIn("RUN_NOT_FOUND", current)

    def test_discovery_does_not_mandate_a_global_probe_first(self) -> None:
        current = body("fleet-device-and-session-discovery")
        self.assertTrue(has_forced_global_probe(OLD_FORCED_PROBE))
        self.assertFalse(has_forced_global_probe(current))
        self.assertIn("fleet_get_session", current)
        self.assertIn("not a prerequisite", current.lower())

    def test_ability_authority_is_shared_not_per_device(self) -> None:
        discovery = body("fleet-device-and-session-discovery")
        safety = body("fleet-safety-boundaries")
        self.assertTrue(has_per_device_registry(OLD_PER_DEVICE_REGISTRY))
        self.assertFalse(has_per_device_registry(discovery))
        self.assertFalse(has_per_device_registry(safety))
        self.assertIn("ability-management", discovery)
        for word in ("installed", "ready", "granted"):
            self.assertIn(word, discovery.lower())

    def test_create_has_no_hardcoded_fleet_model(self) -> None:
        current = body("fleet-create-with-grants")
        self.assertTrue(has_hardcoded_fleet_model(OLD_HARDCODED_MODEL))
        self.assertFalse(has_hardcoded_fleet_model(current))
        self.assertIn("effective", current.lower())
        self.assertIn("owner", current.lower())

    def test_reuse_allows_complete_long_work_and_independent_batches(self) -> None:
        current = body("fleet-reuse-idle-session")
        self.assertTrue(has_single_intent_restriction(OLD_SINGLE_INTENT))
        self.assertFalse(has_single_intent_restriction(current))
        self.assertIn("complete long", current.lower())
        self.assertIn("batch", current.lower())
        self.assertIn("independent", current.lower())


class TheUnknownRetryContractIsStated(unittest.TestCase):
    def test_async_states_the_durability_enum_and_reconciles_first(self) -> None:
        text = body("fleet-async-dispatch")
        for value in ("PROCESS_LOCAL", "AGENT_CONTROL_STORE", "NOT_REQUESTED",
                      "UNKNOWN"):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertIn("not a", text.lower())
        self.assertIn("cross-restart guarantee", text.lower())
        self.assertIn("RUN_NOT_FOUND", text)
        self.assertIn("never_issued", text)
        self.assertIn("queued_message_uuid", text)
        self.assertIn("reconciliation", text.lower())

    def test_create_states_the_same_key_is_not_a_restart_guarantee(self) -> None:
        text = body("fleet-create-with-grants")
        for value in ("PROCESS_LOCAL", "AGENT_CONTROL_STORE", "NOT_REQUESTED"):
            with self.subTest(value=value):
                self.assertIn(value, text)
        self.assertIn("RUN_NOT_FOUND", text)
        self.assertIn("queued_message_uuid", text)
        self.assertIn("not a failure", text.lower())


class TheRoutingAndAppBoundariesRemain(unittest.TestCase):
    def test_commander_prefers_fleet_and_keeps_rdc_a_gui_fallback(self) -> None:
        text = body("fleet-commander-orchestration").lower()
        self.assertIn("prefer fleet", text)
        self.assertIn("rdc", text)
        self.assertIn("gui", text)
        self.assertIn("fallback", text)
        self.assertIn("do not repeatedly dispatch one short task", text)

    def test_update_keeps_app_presentation_unverified(self) -> None:
        text = body("fleet-plugin-update").lower()
        for word in ("avatar", "display", "unverified"):
            self.assertIn(word, text)
        self.assertIn("app", text)


class TheOwnerMaxManagedProfileIsStated(unittest.TestCase):
    """Owner-max is a one-time managed profile, not OS root or per-call approval."""

    def test_owner_max_is_named_in_the_command_and_safety_skills(self) -> None:
        for name in ("fleet-safety-boundaries", "fleet-create-with-grants",
                     "fleet-commander-orchestration"):
            with self.subTest(skill=name):
                self.assertIn("owner-max", body(name).lower())

    def test_routine_actions_are_not_written_as_per_call_approval(self) -> None:
        old = ("Ask the owner for approval before each fleet_create_session "
               "call; every send needs a fresh approval.")
        self.assertTrue(requires_per_call_approval(old))
        for name in ("fleet-safety-boundaries", "fleet-create-with-grants",
                     "fleet-reuse-idle-session",
                     "fleet-commander-orchestration"):
            with self.subTest(skill=name):
                self.assertFalse(requires_per_call_approval(body(name)))

    def test_no_sudo_or_os_root_claim(self) -> None:
        all_text = "\n".join(p.read_text(encoding="utf-8")
                              for p in SHARED.glob("*/SKILL.md")).lower()
        self.assertNotIn("sudo", all_text)
        self.assertNotIn("root access", all_text)

    def test_tool_count_is_discovered_not_hardcoded(self) -> None:
        old = "# Thirty-three tools are not thirty-three permissions"
        self.assertTrue(hardcodes_tool_count(old))
        all_text = "\n".join(p.read_text(encoding="utf-8")
                              for p in SHARED.glob("*/SKILL.md"))
        self.assertFalse(hardcodes_tool_count(all_text))

    def test_project_creation_uses_capability_discovery(self) -> None:
        invented = "Call `fleet_project_create` to make a new project."
        self.assertTrue(names_invented_project_verb(invented))
        for name in ("fleet-create-with-grants",
                     "fleet-device-and-session-discovery",
                     "fleet-commander-orchestration"):
            with self.subTest(skill=name):
                text = body(name)
                self.assertTrue(has_capability_discovery(text))
                self.assertFalse(names_invented_project_verb(text))
                self.assertIn("fleet_create_project", text)
                self.assertIn("fleet_status.owner_access", text)


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

    def test_login_is_password_and_one_time_code_not_chat(self) -> None:
        text = body("fleet-safety-boundaries")
        self.assertIn("one-time code", text)
        self.assertIn("Never ask for the password or the one-time code in chat", text)
        self.assertIn("auth.pat_enabled", text)

    def test_project_removal_is_documented_with_its_confirmation(self) -> None:
        text = body("fleet-create-with-grants")
        for word in ("fleet_delete_project", "confirm_path", "fleet:control",
                     "PROJECT_NOT_FOUND"):
            with self.subTest(word=word):
                self.assertIn(word, text)

    def test_no_unserved_batch_or_scheduler_name_is_written(self) -> None:
        all_text = "\n".join(p.read_text(encoding="utf-8")
                             for p in SHARED.glob("*/SKILL.md"))
        for invented in ("fleet_batch", "fleet_schedule", "fleet_dispatch"):
            self.assertNotIn(invented, all_text)


if __name__ == "__main__":
    unittest.main()
