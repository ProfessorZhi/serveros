"""Every top-level directory is registered in one layer, and the layer rules hold.

`docs/architecture/layers.yaml` says which of the Fleet layers (L0–L4), the
ServerOS product, or the shared tooling each top-level directory belongs to.
A new directory that nobody registered is how a repository's structure drifts
without anyone deciding it should; this test makes that a decision.
See docs/architecture/FLEET-FOUR-LAYERS.md.
"""
from __future__ import annotations

import re
import stat
import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAYERS_FILE = ROOT / "docs" / "architecture" / "layers.yaml"
LAYERS = yaml.safe_load(LAYERS_FILE.read_text(encoding="utf-8"))


def tracked_top_level_dirs() -> set[str]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return {path.split("/", 1)[0] for path in out if "/" in path}


def layer_names(entry) -> set[str]:
    if isinstance(entry, str):
        return {entry}
    return {name.split("-", 1)[0] for name in entry["parts"]}


HUB_REACHES_IN = re.compile(
    r"^\s*(?:from|import)\s+(?:runtime|mcp)\b"         # a package import
    r"|/\s*[\"'](?:runtime|mcp)[\"']"                   # ROOT / "runtime"
    r"|[\"'](?:runtime|mcp)/[\w.-]+\.py[\"']",          # "runtime/x.py"
    re.M)
LOADS_THE_HUB = re.compile(
    r"^\s*(?:from|import)\s+fleet_mcp\b|[\"']fleet_mcp[\"/']", re.M)
CODE_SUFFIXES = {".py", ".sh", ".js", ".mjs", ".ts", ".ps1", ".cpp", ".exe"}


class EveryDirectoryHasALayer(unittest.TestCase):
    def test_every_tracked_top_level_directory_is_registered(self) -> None:
        missing = sorted(tracked_top_level_dirs() - set(LAYERS["directories"]))
        self.assertEqual(missing, [], f"register these in {LAYERS_FILE.name}")

    def test_every_registered_directory_exists(self) -> None:
        gone = sorted(d for d in LAYERS["directories"] if not (ROOT / d).is_dir())
        self.assertEqual(gone, [], f"remove these from {LAYERS_FILE.name}")

    def test_every_layer_named_is_a_declared_layer(self) -> None:
        declared = set(LAYERS["layers"])
        for directory, entry in LAYERS["directories"].items():
            with self.subTest(directory=directory):
                self.assertLessEqual(layer_names(entry), declared)

    def test_a_split_directory_says_what_each_part_is(self) -> None:
        for directory, entry in LAYERS["directories"].items():
            if isinstance(entry, dict):
                with self.subTest(directory=directory):
                    self.assertTrue(entry.get("split"))
                    self.assertGreaterEqual(len(entry["parts"]), 2)

    def test_every_plugin_is_registered_with_its_side(self) -> None:
        present = {p.name for p in (ROOT / "plugins").iterdir() if p.is_dir()}
        self.assertEqual(present, set(LAYERS["plugins"]))
        for name, entry in LAYERS["plugins"].items():
            with self.subTest(plugin=name):
                self.assertIn(entry["side"], {"commander", "device", "lab"})
                if "source" in entry:
                    self.assertTrue((ROOT / entry["source"]).is_dir())


class TheLayerRulesHold(unittest.TestCase):
    def test_the_rules_tested_here_are_the_rules_declared(self) -> None:
        self.assertEqual({r["id"] for r in LAYERS["rules"]}, {
            "hub-does-not-reach-into-runtime-or-mcp",
            "commander-plugins-carry-no-code",
            "identity-does-not-depend-on-the-hub",
        })

    def test_the_hub_does_not_reach_into_runtime_or_mcp(self) -> None:
        offenders = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "fleet_mcp").rglob("*.py")
                           if HUB_REACHES_IN.search(p.read_text(encoding="utf-8")))
        self.assertEqual(offenders, [])

    def test_the_pattern_would_catch_a_hub_that_did(self) -> None:
        for text in ("from runtime import daemon\n",
                     'spec = ROOT / "runtime" / "daemon.py"\n',
                     'load("mcp/agent_mcp.py")\n'):
            with self.subTest(text=text):
                self.assertTrue(HUB_REACHES_IN.search(text))
        self.assertIsNone(HUB_REACHES_IN.search('url = "https://host/mcp"\n'))

    def test_commander_plugins_carry_no_code(self) -> None:
        offenders = []
        for path in (ROOT / "plugins").rglob("*"):
            if path.is_file() and (path.suffix in CODE_SUFFIXES
                                   or path.stat().st_mode & stat.S_IXUSR):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(offenders), [])

    def test_the_identity_layer_does_not_load_the_hub(self) -> None:
        offenders = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "fleet").glob("*.py")
                           if LOADS_THE_HUB.search(p.read_text(encoding="utf-8")))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
