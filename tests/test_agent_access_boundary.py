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

#: Modules allowed to import a CloudCLI module, as of 2026-11-18. Remove a line
#: when its use moves behind the interface; never add one.
ALLOWED = {
    "fleet_mcp/cloudcli_native.py",
    "fleet_mcp/cloudcli_models.py",
    "fleet_mcp/providers/__init__.py",
    "fleet_mcp/providers/cloudcli.py",
    "fleet_mcp/providers/fleet_node.py",
    "fleet_mcp/node/connector.py",
    "fleet_mcp/node/model_login.py",
    "fleet_mcp/deploy/release_plan.py",
    "fleet_mcp/fleet.py",
    "fleet_mcp/tools.py",
}


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
        for missing in ("list_skills", "invoke_skill", "list_commands", "invoke_command"):
            self.assertIn(missing, doc)


class CloudCliIsNamedOnlyWhereItAlreadyWas(unittest.TestCase):
    def test_no_new_module_imports_a_cloudcli_module(self):
        found = set()
        for path in sorted((ROOT / "fleet_mcp").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if re.match(r"\s*(from|import)\s", line) and any(m in line for m in CLOUDCLI_MODULES):
                    found.add(path.relative_to(ROOT).as_posix())
                    break
        self.assertLessEqual(found, ALLOWED, f"new CloudCLI imports: {sorted(found - ALLOWED)}")


if __name__ == "__main__":
    unittest.main()
