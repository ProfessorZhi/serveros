"""D-2: the connection layer is an interface; CloudCLI is one implementation.

The interface verbs in docs/architecture/agent-access.md are the ones the code
has, and the modules that still name CloudCLI above the interface can only
become fewer.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs/architecture/agent-access.md"
CLOUDCLI_MODULES = ("cloudcli_native", "cloudcli_models", "providers.cloudcli", "providers import cloudcli")

#: Which CloudCLI modules each module may import, as of 2026-11-18. Remove an
#: entry when its use moves behind the interface; never add one.
ALLOWED = {
    "fleet_mcp/cloudcli_native.py": set(),
    "fleet_mcp/cloudcli_models.py": set(),
    "fleet_mcp/providers/__init__.py": {"providers.cloudcli"},
    "fleet_mcp/providers/cloudcli.py": {"cloudcli_native", "cloudcli_models"},
    "fleet_mcp/providers/fleet_node.py": {"cloudcli_native"},
    "fleet_mcp/node/connector.py": {"providers.cloudcli", "cloudcli_native"},
    "fleet_mcp/node/model_login.py": {"cloudcli_models"},
    "fleet_mcp/deploy/release_plan.py": {"cloudcli_native"},
    "fleet_mcp/fleet.py": {"cloudcli_native"},
    "fleet_mcp/tools.py": {"cloudcli_native"},
}


def _named(line: str) -> set[str]:
    found = set()
    if "cloudcli_native" in line:
        found.add("cloudcli_native")
    if "cloudcli_models" in line:
        found.add("cloudcli_models")
    if re.search(r"providers\.cloudcli\b|from \.cloudcli import|providers import cloudcli", line):
        found.add("providers.cloudcli")
    return found


class TheInterfaceIsWrittenDown(unittest.TestCase):
    def test_every_verb_in_code_is_in_the_document(self):
        from fleet_mcp.providers.base import DeviceProvider
        verbs = {n for n in vars(DeviceProvider) if not n.startswith("_") and callable(getattr(DeviceProvider, n))}
        doc = DOC.read_text(encoding="utf-8")
        for verb in sorted(verbs):
            with self.subTest(verb=verb):
                self.assertIn(f"`{verb}(", doc)

    def test_the_document_says_cloudcli_is_an_implementation(self):
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("CloudCLI is its first implementation, not the interface", doc)
        for verb in ("list_skills", "invoke_skill", "list_commands", "invoke_command",
                     "reconfigure", "approve", "list_models", "upsert_model"):
            self.assertIn(f"`{verb}(", doc)
        self.assertNotIn("**to add**", doc)


class CloudCliIsNamedOnlyWhereItAlreadyWas(unittest.TestCase):
    def test_no_module_imports_a_cloudcli_module_it_is_not_listed_for(self):
        found: dict[str, set[str]] = {}
        for path in sorted((ROOT / "fleet_mcp").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if re.match(r"\s*(from|import)\s", line):
                    names = _named(line)
                    if names:
                        found.setdefault(rel, set()).update(names)
        extra = {rel: sorted(names - ALLOWED.get(rel, set()))
                 for rel, names in found.items() if names - ALLOWED.get(rel, set())}
        self.assertEqual(extra, {}, "new CloudCLI imports")

    def test_fleet_reads_the_model_library_only_through_the_interface(self):
        text = (ROOT / "fleet_mcp/fleet.py").read_text(encoding="utf-8")
        self.assertNotIn("cloudcli_models", text)


if __name__ == "__main__":
    unittest.main()
