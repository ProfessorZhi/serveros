"""`fleet_hub/` is a registered L2 directory, and not a second L0 authority.

D2 stage 2 promotes the L2 audience rule out of `runtime/` to a top-level
`fleet_hub/`. `tests/test_fleet_hub_boundary.py` owns WHERE the file is and how
the two loaders share one module object; this file owns the other half — the
entry in `docs/architecture/layers.yaml`:

  * IT IS REGISTERED, ONCE, AS L2. `tests/test_repository_layers.py` says that a
    tracked top-level directory must be registered; this says WHICH registration
    is the right one, and that it is the only one. A YAML mapping silently keeps
    the last of two identical keys, so "registered twice" is the one shape the
    owning guard cannot see; the keys are read off the text for that reason.

  * L2 IS NOT AN AUTHORITY. L0 is "设备身份与拓扑、工作状态、能力目录。四层都读它,
    它不依赖四层". A hub reads L0 and is never a source of it, so registering the
    hub must not be a quiet promotion to the layer that owns identity. Three
    things are asserted instead of assumed: the L0 anchors stay in `fleet/` and
    `workengine/`, `fleet_hub/` carries no copy of them, and no L0 file loads the
    new hub. The last one closes a gap that this move opened — the
    `identity-does-not-depend-on-the-hub` rule in layers.yaml names `fleet_mcp`
    only, so before this file `fleet/transport.py` could have started importing
    `fleet_hub` and nothing would have failed.

  * THE MOVE IS COMPLETE, NOT HALF-REGISTERED. `runtime/`'s L2 part no longer
    names `fleet_audience`, and the directories that were `split` before are
    still `split` — a new registration must not be the moment an old claim is
    quietly dropped. `fleet` (L0 registry + L2 retired control plane) and
    `fleet_mcp` (Tokyo hub + node connector) are still cross-layer; a top-level
    `fleet_hub` retires neither of them.

Each matcher carries a positive control, so a guard that has stopped looking is
visible as a failure rather than as a pass.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LAYERS_FILE = ROOT / "docs" / "architecture" / "layers.yaml"

HUB = "fleet_hub"
HUB_LAYER = "L2"

#: L0's anchors in this repository: who a device is, what it declares about
#: itself, and how that is read. A hub holding a copy of any of them would be a
#: second authority whatever the table said about it.
L0_ANCHORS = ("registry.yaml", "profiles.yaml", "transport.py", "view.py")

LOADS_THE_HUB = re.compile(
    r"^\s*(?:from|import)\s+fleet_hub\b"        # a package import
    r"|/\s*[\"']fleet_hub[\"']"                 # ROOT / "fleet_hub" / "x.py"
    r"|[\"']fleet_hub/[\w.-]+\.py[\"']",        # "fleet_hub/x.py"
    re.M)


def layers() -> dict:
    return yaml.safe_load(LAYERS_FILE.read_text(encoding="utf-8"))


def layer_names(entry) -> set[str]:
    """The layer names an entry claims, read the same way the owning guard does."""
    if isinstance(entry, str):
        return {entry}
    return {name.split("-", 1)[0] for name in entry["parts"]}


def is_an_authority(entry) -> bool:
    return "L0" in layer_names(entry)


def directory_keys(text: str) -> list[str]:
    """Keys of the `directories:` mapping in file order, duplicates included.

    `yaml.safe_load` keeps the last of two identical keys, so a table that
    registers one directory twice parses as if it registered it once. Reading the
    keys off the text is what keeps the second entry visible.
    """
    keys: list[str] = []
    inside = False
    for line in text.splitlines():
        if not inside:
            inside = line.startswith("directories:") and line.strip() == "directories:"
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  (\S[^:]*):", line)
        if match:
            keys.append(match.group(1))
    return keys


class TheHubIsRegisteredAsL2(unittest.TestCase):
    def test_the_hub_is_in_the_directory_table(self) -> None:
        self.assertIn(HUB, layers()["directories"])

    def test_the_hub_is_registered_exactly_once(self) -> None:
        keys = directory_keys(LAYERS_FILE.read_text(encoding="utf-8"))
        self.assertIn(HUB, keys, "the parser must see the entry it is counting")
        self.assertEqual(keys.count(HUB), 1)

    def test_the_hub_claims_one_layer_and_it_is_l2(self) -> None:
        entry = layers()["directories"][HUB]
        self.assertIsInstance(
            entry, str,
            "a directory that lives in one layer is a plain name; `split` is for "
            "directories that still straddle two, and the hub does not")
        self.assertEqual(layer_names(entry), {HUB_LAYER})

    def test_the_authority_check_would_catch_a_promotion(self) -> None:
        # Positive control: the same predicate used above, on the two shapes an
        # L0 claim can take. If both of these stopped reading as authority, the
        # L2 assertion above would pass for the wrong reason.
        self.assertTrue(is_an_authority("L0"))
        self.assertTrue(is_an_authority({"split": True, "parts": {"L0": "…", "L2": "…"}}))
        self.assertFalse(is_an_authority(HUB_LAYER))


class TheHubIsNotASecondL0Authority(unittest.TestCase):
    def test_the_identity_layer_still_owns_the_anchors(self) -> None:
        missing = [name for name in L0_ANCHORS if not (ROOT / "fleet" / name).is_file()]
        self.assertEqual(missing, [], "the L0 anchors must stay in fleet/")
        self.assertEqual(layers()["directories"]["workengine"], "L0")
        fleet = layers()["directories"]["fleet"]
        self.assertTrue(fleet.get("split"))
        self.assertIn("L0", layer_names(fleet), "fleet/ still carries the identity half")

    def test_the_hub_carries_no_copy_of_the_identity_layer(self) -> None:
        offenders = sorted(
            str(path.relative_to(ROOT)) for path in (ROOT / HUB).rglob("*")
            if path.is_file() and (path.name in L0_ANCHORS
                                   or path.name.endswith("-contract.yaml")))
        self.assertEqual(offenders, [], "a copy here would be a second source of truth")

    def test_the_identity_layer_and_the_work_engine_do_not_load_the_hub(self) -> None:
        sources = [*(ROOT / "fleet").glob("*.py"), *(ROOT / "workengine").rglob("*.py")]
        self.assertTrue(sources, "no L0 source was found; the scan would certify nothing")
        offenders = sorted(str(path.relative_to(ROOT)) for path in sources
                           if LOADS_THE_HUB.search(path.read_text(encoding="utf-8")))
        self.assertEqual(
            offenders, [],
            "L0 must not depend on the hub; the hub may read L0, never the reverse")

    def test_the_loader_pattern_would_catch_an_l0_that_did(self) -> None:
        # The third shape below is spelled `spec_from_file_location(...)` on
        # purpose, not with the module's own loader helper.
        # `tests/test_fleet_hub_boundary.py` enumerates every tracked file for
        # loader calls naming `fleet_audience.py` and collects the synthetic name
        # from each; a positive control written as a loader call would be read as
        # a THIRD loader site (name "x") and would make that sibling guard red.
        # This string still exercises this file's own `ROOT / "fleet_hub"` shape.
        for text in ("from fleet_hub import fleet_audience\n",
                     "import fleet_hub.fleet_audience\n",
                     'spec_from_file_location("n", ROOT / "fleet_hub" / "fleet_audience.py")\n'):
            with self.subTest(text=text):
                self.assertTrue(LOADS_THE_HUB.search(text))
        # Negative controls: a neighbouring name, and a URL that merely ends in
        # the directory name, must not read as a load.
        self.assertIsNone(LOADS_THE_HUB.search("import fleet_hub_extra\n"))
        self.assertIsNone(LOADS_THE_HUB.search('url = "https://host/fleet_hub/"\n'))


class TheMoveDidNotDropAnOldClaim(unittest.TestCase):
    def test_runtime_no_longer_claims_the_audience_rule(self) -> None:
        parts = layers()["directories"]["runtime"]["parts"]
        claims = {name: text for name, text in parts.items() if "fleet_audience" in text}
        self.assertEqual(
            claims, {},
            "runtime/ no longer contains fleet_audience; claiming it there would "
            "describe a file that is not in the directory")

    def test_the_control_plane_is_still_split(self) -> None:
        for directory in ("fleet", "fleet_mcp", "runtime"):
            entry = layers()["directories"][directory]
            with self.subTest(directory=directory):
                self.assertIsInstance(entry, dict, "a split entry was flattened")
                self.assertTrue(entry.get("split"))
                self.assertGreaterEqual(len(entry["parts"]), 2)
                self.assertIn("L2", layer_names(entry))


if __name__ == "__main__":
    unittest.main()
