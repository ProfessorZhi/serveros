#!/usr/bin/env python3
"""RC-next integration QA — the packaging claim must be true of the payload.

WHY THIS FILE EXISTS
--------------------
`docs/integration/FLEET-V1-RC-20261028.md` §4 records one blocker standing
between the RC and a releasable artifact:

    `plugin/bundle.yaml` declares `artifacts: [node-runtime, plugin]` while the
    payload it describes carries `fleet_mcp/node/*`, so
    `scripts/build.py#verify_artifact_kinds` refuses to write the manifest.

That record names ONE kind. Re-measured on the composed RC-next tree it is two:
after `l2-node`, the same rule also refuses `l2-hub`, because `fleet_mcp/` ships
`server.py` as well. The refusal is raised on the first leaked kind, which is
why the second one is invisible until the first is declared. `_leaked()` below
is only used to make that enumeration appear in the failure message; the verdict
is the build's own function, not a second copy of its rule.

WHAT THIS FILE GATES
--------------------
1. The two packaging lists agree in both directions (`CORE_MODULES` ↔
   `plugin/bundle.yaml`), which is the fix #450 landed and which must not
   regress. Green today.
2. `fleet_hub` — the directory #447 moved and #450 packaged — is in both lists
   under one path, and the moved module really is inside it. Green today.
3. **The merge gate**: the composition's declared kinds and the bytes it ships
   agree. Red today, by name, with both exits spelled out. Neither exit is mine
   to take: one is `plugin/bundle.yaml`, the other is what the payload ships.
4. The flip: declaring exactly the kinds the payload already backs is enough to
   pass, which is what proves (3) is a declaration question and not a missing
   file. Skips itself if the owner takes the other exit.

WINDOWS / HOST INDEPENDENCE
---------------------------
`build.py` spells its own member list with `str(rel)`, so on Windows every
member is `bin\\serverosd` while `release.py#REQUIRED_MEMBERS` is `bin/serverosd`
— the build refuses a payload it just wrote, and #450's fixture cannot even be
built here (`tests/test_build_fleet_hub_packaging.py`: 11 errors, identical on
the #449 baseline). That is a real portability defect in `scripts/build.py`,
recorded in the RC-next document as CODE/NOT_RUN-on-Windows; it is NOT this
lane's file to change. To keep the question this file answers decidable on any
host, members are collected with the build's own source walk and re-spelled
`as_posix()`. Nothing else is re-implemented.

LAYERS: CODE (reads `scripts/build.py`, `scripts/release.py`,
`plugin/bundle.yaml`) / UNIT (unittest; no build is written, no network, no
service, no credential).
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "plugin" / "bundle.yaml"


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]` and reads None otherwise.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


build = _load("serveros_build_rc_next", REPO / "scripts" / "build.py")


def composition() -> dict:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def declared_kinds() -> list[str]:
    return build.declared_artifact_kinds(composition(), BUNDLE)


def module_blocks() -> dict[str, dict]:
    return {key: value for key, value in composition().items()
            if isinstance(value, dict) and "path" in value}


def payload_members() -> set[str]:
    """The combined build's member list, POSIX-spelled.

    Same source walk the build uses (`local_skill_files`), plus the files the
    combined build generates rather than copies. The generated set is derived
    from the composition, not assumed: `plan()` emits the plugin manifests only
    for a kind that claims them, and the MCP manifests only when the composition
    declares a server.
    """
    members: set[str] = set()
    blocks = module_blocks()
    for module in build.CORE_MODULES:
        rel = (blocks.get(module) or {}).get("path")
        if not rel:
            continue
        for _source, inside in build.local_skill_files(REPO / rel):
            members.add(f"{rel}/{inside.as_posix()}")
    kinds = declared_kinds()
    contract = build.release_contract()
    if contract.PLUGIN in kinds:
        members |= {".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
                    ".codex-plugin/plugin.json"}
    if build._declared_servers(composition()):
        members |= {".mcp.json", ".codex-mcp.json"}
    members |= {"README.md", build.ARTIFACT_MANIFEST_NAME}
    return members


def _leaked(kinds: list[str], members: set[str]) -> dict[str, list[str]]:
    """Every undeclared kind the payload backs — the build raises on the first.

    Diagnostic only. It mirrors the loop in `verify_artifact_kinds` so the
    failure message can name all of them at once; the assertion below still
    calls that function.
    """
    contract = build.release_contract()
    out: dict[str, list[str]] = {}
    for kind in contract.ARTIFACT_KINDS:
        if kind in kinds:
            continue
        present = [rel for rel in contract.REQUIRED_MEMBERS[kind] if rel in members]
        if present:
            out[kind] = present
    return out


class TheTwoPackagingListsAgree(unittest.TestCase):
    """#450's fix: neither list may name something the other does not."""

    def test_every_core_module_is_declared_in_the_composition(self):
        blocks = module_blocks()
        missing = [module for module in build.CORE_MODULES if module not in blocks]
        self.assertEqual(missing, [], (
            "scripts/build.py#CORE_MODULES names directories the composition "
            "does not ship; the build would copy them and the manifest would "
            "not describe them"))

    def test_every_declared_module_is_a_core_module(self):
        orphans = sorted(set(module_blocks()) - set(build.CORE_MODULES))
        self.assertEqual(orphans, [], (
            "plugin/bundle.yaml declares a source path the build never visits, "
            "so the artifact would miss code its own members load"))

    def test_one_path_per_module(self):
        blocks = module_blocks()
        wrong = {module: blocks[module]["path"] for module in build.CORE_MODULES
                 if blocks.get(module, {}).get("path") != module}
        self.assertEqual(wrong, {}, (
            "a module's bundle path must be the module's own directory name; a "
            "renamed path makes the packaging list and the tree disagree"))

    def test_the_packaging_list_is_not_a_layer_claim(self):
        """`CORE_MODULES` packages; `layers.yaml` assigns. Neither implies the other."""
        layers = yaml.safe_load((REPO / "docs" / "architecture" / "layers.yaml")
                                .read_text(encoding="utf-8"))
        registered = layers["directories"]
        self.assertIn("tests", registered,
                      "positive control: a registered top-level directory")
        self.assertNotIn("tests", build.CORE_MODULES,
                         "positive control: ... that the build does not package, so "
                         "the two vocabularies are neither equal nor implied")
        self.assertIn("fleet_hub", build.CORE_MODULES,
                      "positive control: the overlap is real, not avoided by the "
                      "two lists being disjoint")
        self.assertIn("fleet_hub", registered)
        self.assertNotEqual(set(build.CORE_MODULES), set(registered),
                            "the packaging list and the layer table would then be "
                            "one list, and registering a directory would package it")


class TheMovedHubIsPackaged(unittest.TestCase):
    """#447 moved the rule; #450 taught the build to copy it."""

    def test_fleet_hub_is_in_both_lists_under_one_path(self):
        self.assertIn("fleet_hub", build.CORE_MODULES)
        self.assertEqual(module_blocks().get("fleet_hub", {}).get("path"),
                         "fleet_hub")

    def test_the_moved_module_is_inside_the_packaged_directory(self):
        self.assertTrue((REPO / "fleet_hub" / "fleet_audience.py").is_file(),
                        "the hub's module is not where the packaging lists say")
        stale_root = REPO / "runtime" / "fleet_hub"
        stale = sorted(path.name for path in stale_root.rglob("*.py")
                       ) if stale_root.is_dir() else []
        self.assertEqual(stale, [], (
            "stage 2 moved the directory; a leftover source copy would be a "
            "second module object and a second HMAC key. Byte-code cache left by "
            "an earlier checkout is not a source copy and is not counted"))

    def test_the_packaged_payload_reaches_the_hub(self):
        members = payload_members()
        self.assertIn("fleet_hub/fleet_audience.py", members,
                      "the node payload must carry the rule it loads at run time")


class TheDeclaredKindsAreTheShippedBytes(unittest.TestCase):
    """THE MERGE GATE — `verify_artifact_kinds`, on the real composition."""

    def test_the_composition_declares_every_kind_the_payload_carries(self):
        kinds = declared_kinds()
        members = payload_members()
        leaked = _leaked(kinds, members)
        try:
            evidence = build.verify_artifact_kinds(kinds, members, BUNDLE)
        except ValueError as refused:
            self.fail(
                "BLOCKED_OWNER_HANDOFF(plugin/bundle.yaml or the payload it "
                "ships): the composition's declaration and its bytes disagree.\n"
                f"  build's verdict: {refused}\n"
                f"  undeclared kinds the payload backs: {leaked}\n"
                "  Exit A — declare every kind the payload backs in "
                "`plugin/bundle.yaml#artifacts` (owner: the composition).\n"
                "  Exit B — stop shipping those members in this payload (owner: "
                "the packaging fix).\n"
                "  Either way the two faces must not be one payload again.")
        self.assertEqual(set(evidence), set(kinds),
                         "verification returned evidence for a kind not declared")

    def test_declaring_the_kinds_the_payload_backs_is_enough(self):
        """The flip: the bytes are already there, so the red above is the claim.

        If the owner takes Exit B instead, the members disappear and this skips
        — which is the correct reading of a gate that no longer has a claim to
        check.
        """
        kinds = declared_kinds()
        members = payload_members()
        leaked = _leaked(kinds, members)
        if not leaked:
            self.skipTest("the payload no longer backs an undeclared kind; the "
                          "gate was closed by removing the bytes")
        contract = build.release_contract()
        widened = list(kinds) + list(leaked)
        evidence = build.verify_artifact_kinds(widened, members, BUNDLE)
        for kind, present in leaked.items():
            self.assertEqual(sorted(evidence[kind]), sorted(
                rel for rel in contract.REQUIRED_MEMBERS[kind] if rel in members),
                f"{kind}: evidence must be the members that back the claim")
            self.assertTrue(present, f"{kind}: a leaked kind with no member is a bug")

    def test_the_members_it_judges_are_the_builds_own(self):
        """Positive control for the enumeration the two tests above depend on."""
        members = payload_members()
        for required in ("bin/serverosd", "runtime/daemon.py",
                         "workengine/schema.sql", "fleet_mcp/server.py",
                         "fleet_mcp/node/connector.py"):
            self.assertIn(required, members,
                          f"{required} is a release required member; if the walk "
                          f"stopped producing it the gate above would pass for "
                          f"the wrong reason")


if __name__ == "__main__":
    unittest.main()
