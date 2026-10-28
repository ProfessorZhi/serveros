#!/usr/bin/env python3
"""RC-next integration QA — the packaging claim must be true of the payload.

WHY THIS FILE EXISTS
--------------------
`docs/integration/FLEET-V1-RC-20261028.md` §4 recorded one blocker standing
between the RC and a releasable artifact:

    `plugin/bundle.yaml` declares `artifacts: [node-runtime, plugin]` while the
    payload it describes carries `fleet_mcp/node/*`, so
    `scripts/build.py#verify_artifact_kinds` refuses to write the manifest.

Re-measured on the composed RC-next tree it was two kinds deep: after
`l2-node`, the same rule also refused `l2-hub`, because `fleet_mcp/` ships
`server.py` as well. The refusal was raised on the first leaked kind, which is
why the second one was invisible until the first was declared.

**#462 `a8ec6c30` closed it builder-side**, not by editing `plugin/bundle.yaml`
and not by dropping bytes. The rule is now narrower and better justified:

* a kind this builder does not produce (`l2-node`, `l2-hub`, `native-runtime`)
  is refused **by name** — the claim belongs to the tool that assembles that
  payload (#431/#433's verifier), not to this build;
* the absent-but-present direction is limited to `build_face_kinds()` — the two
  faces this builder actually decides (`node-runtime`, `plugin`) — so the
  `fleet_mcp/` files the combined artifact ships as a Core module no longer
  make the payload "also an l2-node".

This file gates the RESULT, in the shape the owner chose. It does not re-open
the decision. What it does keep is the bite: a build that stopped checking its
own two faces, or that started claiming an L2 identity again, reddens here.

WHAT THIS FILE GATES
--------------------
1. The two packaging lists agree in both directions (`CORE_MODULES` ↔
   `plugin/bundle.yaml`), which is the fix #450 landed and which must not
   regress.
2. `fleet_hub` — the directory #447 moved and #450 packaged — is in both lists
   under one path, and the moved module really is inside it.
3. **The gate**: the real composition verifies clean, and its verdict names
   exactly the kinds it declared.
4. **The guard still bites** (positive controls, because a narrowed rule is
   indistinguishable from a disabled one by passing alone): declaring `plugin`
   while carrying the daemon is refused naming `node-runtime`; declaring
   `node-runtime` while carrying plugin members is refused naming `plugin`;
   and declaring `l2-node`/`l2-hub` is refused as "not a kind this build
   produces".
5. The builder's claim is exactly two faces. If someone widens
   `build_face_kinds()` to include an identity it cannot verify, that is a
   second authority over the same bytes and it reddens here.

RESIDUAL RISK, RECORDED
-----------------------
Under the new rule the combined artifact's manifest no longer states which L2
halves it embeds — `fleet_mcp/node/*` and `fleet_mcp/server.py` are shipped as
Core modules and nothing here claims they are or are not a standalone `l2-node`
/ `l2-hub`. That claim, and its verification, is #431/#433's standalone
verifier over a payload this lane never assembles: **NOT_RUN** here, and #461 is
the work that actually separates the halves. This file cannot close that gap and
does not pretend to.

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
    # A synthetic name is process-wide, so the consult and the write have to be
    # one shape: `sys.modules.get(name)` alone does not stop a second writer
    # from displacing the first (tests/loader_graph.py#unguarded_registrants).
    if name in sys.modules:
        return sys.modules[name]
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


def _leaked_faces(kinds: list[str], members: set[str]) -> dict[str, list[str]]:
    """Every undeclared BUILD FACE the payload backs — the build raises on the first.

    Diagnostic only, and scoped to `build_face_kinds()` on purpose: since #462
    the absent-but-present direction applies to the two faces this builder
    decides, and to nothing else. Iterating `ARTIFACT_KINDS` here would report
    `l2-node`/`l2-hub` as leaks, which is the rule the owner deliberately
    retired — a mirror that is wider than the rule is a false alarm generator.
    """
    contract = build.release_contract()
    out: dict[str, list[str]] = {}
    for kind in build.build_face_kinds():
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
    """THE GATE — `verify_artifact_kinds`, on the real composition."""

    def test_the_composition_verifies_and_returns_its_own_evidence(self):
        kinds = declared_kinds()
        members = payload_members()
        leaked = _leaked_faces(kinds, members)
        try:
            evidence = build.verify_artifact_kinds(kinds, members, BUNDLE)
        except ValueError as refused:
            self.fail(
                "REGRESSION(scripts/build.py#verify_artifact_kinds): #462's "
                "builder-side fix no longer accepts the composition it was "
                "written for.\n"
                f"  build's verdict: {refused}\n"
                f"  declared: {sorted(kinds)}\n"
                f"  undeclared faces the payload backs: {leaked}")
        self.assertEqual(set(evidence), set(kinds),
                         "verification returned evidence for a kind not declared")
        self.assertEqual(leaked, {}, "the real composition backs no undeclared face")

    def test_a_plugin_payload_that_carries_the_daemon_is_still_refused(self):
        """Positive control: the face guard was narrowed, not disabled."""
        members = payload_members()
        with self.assertRaises(ValueError) as caught:
            build.verify_artifact_kinds(["plugin"], members, BUNDLE)
        self.assertIn("node-runtime", str(caught.exception),
                      "a plugin payload that ships the daemon is one payload "
                      "twice; the refusal must name the face it smuggles")

    def test_a_node_payload_that_carries_the_plugin_is_still_refused(self):
        members = payload_members()
        with self.assertRaises(ValueError) as caught:
            build.verify_artifact_kinds(["node-runtime"], members, BUNDLE)
        self.assertIn("plugin", str(caught.exception),
                      "declaring one face does not entitle the payload to ship "
                      "the other's manifests")

    def test_the_build_refuses_to_claim_an_l2_identity(self):
        """The rule that replaced the old demand: not mine to claim, so refused.

        If this stops raising, the build has taken over the L2 release tool's
        verdict (`#431`/`#433`) — a second authority over the same bytes.
        """
        kinds = declared_kinds()
        members = payload_members()
        contract = build.release_contract()
        for l2 in (contract.L2_NODE, contract.L2_HUB):
            with self.assertRaises(ValueError) as caught:
                build.verify_artifact_kinds(list(kinds) + [l2], members, BUNDLE)
            self.assertIn("not a kind this build produces", str(caught.exception),
                          f"{l2} must be refused by name, not by member-by-member "
                          f"coincidence")

    def test_the_two_faces_are_the_builders_whole_claim(self):
        """Pin the narrowing's extent: two faces, and nothing else."""
        contract = build.release_contract()
        self.assertEqual(tuple(build.build_face_kinds()),
                         (contract.NODE_RUNTIME, contract.PLUGIN),
                         "the builder's claim widened; whoever widened it now owns "
                         "the verifier for the new kind, and this file's residual-"
                         "risk note is out of date")

    def test_the_members_it_judges_are_the_builds_own(self):
        """Positive control for the enumeration the tests above depend on."""
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
