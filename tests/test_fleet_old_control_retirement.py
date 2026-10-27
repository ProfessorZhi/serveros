#!/usr/bin/env python3
"""D5/D6 (#429) — the old control plane stays, and the reasons are executable.

WHY THIS FILE EXISTS
--------------------
D6's plan is: list the consumers, route what `fleet_mcp/` can cover, delete the
rest and declare that L2 has one control plane. The consumer census says step 2
has nothing to stand on yet: `fleet_mcp/` is the Tokyo hub's remote
device/session plane (11 `fleet_*` tools over a connector/OAuth path), while
`mcp/agent_mcp.py` + `fleet/agent_control_router.py` are the device's local
admission plane over `serverosd`. They are not two spellings of one capability.

So this change does not delete anything. It makes the **retention** reviewable
and hard to drift:

* `fleet/old-control-retirement.yaml` is the inventory: every path, its live
  consumers, the Fleet MCP tool that would replace it (none), and the exact
  condition that would unblock retirement.
* This file re-derives the live consumers from the tree and fails when they
  differ from the inventory — a new import of old control is a review, not a
  quiet widening.
* It pins the authority semantics that must survive any future adapter: an
  unknown action is malformed, a server-side action is refused with an explicit
  `handoff()` migration, a mutation without a trusted context is refused, and
  the controller adapter refuses instead of inventing a device list.
* It pins that the plugin bundle exposes no second control plane: two unique
  servers, disjoint tool names, each control action once, and the hub's
  `fleet_*` tools are not declared in the plugin at all.
* It guards the layer claim: while these files exist, `layers.yaml` must still
  describe `fleet/` as split with them in L2 — D5 cannot be claimed before D6.
  It never edits `layers.yaml` (owned by #414) and never edits the L0 identity
  files.
* It carries positive controls proving each guard can go red, so "the inventory
  matches" cannot pass because the comparison was empty.

No network, no provider call, no credential, no service. The router/controller
calls below stop at the refusal branch before any transport is touched.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "fleet" / "old-control-retirement.yaml"
LAYERS = REPO / "docs" / "architecture" / "layers.yaml"
INVENTORY_DOC = REPO / "docs" / "fleet-old-control-retirement.md"
BUNDLE = REPO / "plugin" / "bundle.yaml"

D6_PATHS = {
    "mcp/agent_mcp.py",
    "fleet/controller.py",
    "fleet/agent_control_router.py",
    "fleet/node.py",
    "fleet/interfaces.py",
    "fleet/remote_desktop_commander.py",
}
RETAIN = {"RETAIN_ACTIVE", "RETAIN_ADAPTER"}


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


router = _load("d5_router", REPO / "fleet" / "agent_control_router.py")
controller = _load("d5_controller", REPO / "fleet" / "controller.py")
rdc = _load("d5_rdc", REPO / "fleet" / "remote_desktop_commander.py")
agent_mcp = _load("d5_agent_mcp", REPO / "mcp" / "agent_mcp.py")
node_mcp = _load("d5_node_mcp", REPO / "mcp" / "node_mcp.py")

sys.path.insert(0, str(REPO))
import fleet_mcp.tools as fleet_tools  # noqa: E402  (read-only surface inventory)


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def entries() -> dict[str, dict]:
    return {entry["path"]: entry for entry in manifest().get("entries") or []}


# ---------------------------------------------------------------------------
# the same live-consumer rule the inventory was built with
# ---------------------------------------------------------------------------
_CALL = re.compile(r"\b\w*load\w*\(")
_BLOB = re.compile(r"\b\w*blob\w*\(")
GUARD_FILE = "tests/test_fleet_old_control_retirement.py"
GUARD_LOADS = {"mcp/agent_mcp.py", "fleet/controller.py",
               "fleet/agent_control_router.py",
               "fleet/remote_desktop_commander.py"}


def _call_args(text: str, open_paren: int) -> str:
    """The argument text of one call, with parentheses balanced.

    A lazy `(.*?)` stops at the first `)` and misses
    `_load("x", Path(__file__).parent / "fleet" / "node.py")`. Counting depth
    (and skipping quoted strings) is enough for the call shapes here.
    """
    depth, quote, index = 0, None, open_paren
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:index]
        index += 1
    return text[open_paren + 1:open_paren + 2000]


def _file_loads(rel: str, text: str) -> bool:
    """Whether this text LOADS/IMPORTS `rel`, rather than merely naming it.

    The token rule is deliberately narrow per location: a fleet module's loader
    must mention `fleet` (a path segment or the `serveros_fleet_*` module name),
    and the local agent MCP must mention `agent_mcp`/`MCP`. Requiring only
    "some path" would count a loader of a *different* `controller.py` in `mcp/`.
    """
    stem, base = Path(rel).stem, Path(rel).name
    is_mcp = rel.startswith("mcp/")
    for pattern in (_CALL, _BLOB):
        for match in pattern.finditer(text):
            args = _call_args(text, match.end() - 1)
            if base not in args:
                continue
            # Every loader in this tree takes (name, path) — `_load`, `load`,
            # `blob`. A ONE-argument `load("mcp/agent_mcp.py")` is a sample
            # string in a regex test, not a load; counting it would put a
            # non-consumer in the inventory.
            if "," not in args:
                continue
            if is_mcp:
                located = any(token in args for token in
                              ("agent_mcp", "MCP", '"mcp"', "'mcp'"))
            else:
                located = "fleet" in args
            if located:
                return True
    if re.search(rf"^\s*(?:from|import)\s+fleet\.{re.escape(stem)}\b", text, re.M):
        return True
    if stem in ("agent_mcp", "node_mcp") and re.search(
            rf"^\s*import\s+{re.escape(stem)}\b", text, re.M):
        return True
    return False


def tracked_all_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True).stdout
    files = out.splitlines()
    # Before this file's first commit it is not in the index yet, but it IS a
    # loader of four entries below. Count it so the inventory is true in a
    # fresh checkout as well as in CI.
    if (REPO / GUARD_FILE).is_file() and GUARD_FILE not in files:
        files.append(GUARD_FILE)
    return files


def tracked_code_files() -> list[str]:
    return [f for f in tracked_all_files()
            if f.endswith(".py") or "/bin/" in f]


def live_consumers(rel: str, files: list[str] | None = None) -> list[str]:
    """Files that LOAD or IMPORT `rel`, as opposed to merely naming its path.

    A contract that says "the reason vocabulary is owned by
    fleet/agent_control_router.py" is provenance, not a consumer. A `_load`
    of the file — or an `import fleet.<module>` — is a consumer, because
    deleting the file breaks it.
    """
    found = []
    for candidate in files if files is not None else tracked_code_files():
        if candidate == rel:
            continue
        # This file contains matcher SAMPLE strings for paths it does not load.
        # Count it only for the four modules its imports actually load.
        if candidate == GUARD_FILE and rel not in GUARD_LOADS:
            continue
        text = (REPO / candidate).read_text(encoding="utf-8", errors="replace")
        if _file_loads(rel, text):
            found.append(candidate)
    return sorted(found)


def consumer_diff(actual: list[str], listed: list[str]) -> tuple[list[str], list[str]]:
    return sorted(set(actual) - set(listed)), sorted(set(listed) - set(actual))


def entry_errors(entry: dict, *, fleet_tool_names: set[str],
                 repo: Path = REPO) -> list[str]:
    """The retention contract, as one function so it can be positively controlled."""
    errors: list[str] = []
    disposition = entry.get("disposition")
    if disposition not in RETAIN | {"RETIRED"}:
        errors.append(f"unknown disposition {disposition!r}")
        return errors
    replacement = entry.get("replacement") or {}
    if disposition == "RETIRED":
        if replacement.get("kind") != "fleet_mcp":
            errors.append("RETIRED needs replacement.kind=fleet_mcp")
        tools = tuple(replacement.get("fleet_mcp_tools") or ())
        if not tools:
            errors.append("RETIRED names no Fleet MCP tool")
        for name in tools:
            if name not in fleet_tool_names:
                errors.append(f"RETIRED names {name!r}, which fleet_mcp does not serve")
        evidence = tuple(entry.get("evidence_tests") or ())
        if not evidence:
            errors.append("RETIRED has no evidence test")
        for rel in evidence:
            if not (repo / rel).is_file():
                errors.append(f"RETIRED evidence test {rel} is absent")
        if (entry.get("consumers") or {}).get("code"):
            errors.append("RETIRED still has live code consumers")
    else:
        if replacement.get("kind") != "none":
            errors.append("a retained path must not claim a replacement")
        if not entry.get("why_not_retired"):
            errors.append("a retained path must say why it is retained")
        if not entry.get("unblock"):
            errors.append("a retained path must name what would unblock retirement")
    return errors


def duplicate_names(names) -> list[str]:
    seen, dupes = set(), []
    for name in names:
        if name in seen:
            dupes.append(name)
        seen.add(name)
    return dupes


class TheInventoryIsComplete(unittest.TestCase):
    def test_it_is_the_d6_set_and_nothing_else(self):
        doc = manifest()
        self.assertEqual(doc["schema"], "serveros/fleet-old-control-retirement.v1")
        self.assertEqual(doc["decision"], "BLOCKED_KEEP_AND_GUARD")
        self.assertEqual(set(entries()), D6_PATHS)
        for rel in D6_PATHS:
            self.assertTrue((REPO / rel).is_file(), f"{rel} is listed but absent")

    def test_identity_authority_is_not_retirable(self):
        doc = manifest()
        identity = set(doc["identity_authority"]["paths"])
        self.assertEqual(identity, {"fleet/registry.yaml", "fleet/profiles.yaml"})
        self.assertFalse(identity & set(entries()))
        self.assertFalse(identity & set(D6_PATHS))
        for rel in identity:
            self.assertTrue((REPO / rel).is_file())

    def test_every_entry_obeys_the_retention_contract(self):
        tools = set(fleet_tools.tool_names())
        for rel, entry in entries().items():
            with self.subTest(path=rel):
                self.assertEqual(entry_errors(entry, fleet_tool_names=tools), [])
                self.assertEqual(entry["disposition"], "RETAIN_ACTIVE"
                                 if rel not in ("fleet/controller.py",
                                                "fleet/remote_desktop_commander.py")
                                 else "RETAIN_ADAPTER")

    def test_the_l0_view_is_not_swept_into_the_retirement(self):
        l0 = {row["path"]: row for row in manifest()["l0_dependencies"]}
        self.assertIn("fleet/view.py", l0)
        self.assertEqual(l0["fleet/view.py"]["disposition"], "RETAIN_L0")
        self.assertNotIn("fleet/view.py", entries())


class ConsumersAreReDerived(unittest.TestCase):
    def test_every_entry_matches_the_tree(self):
        for rel, entry in entries().items():
            with self.subTest(path=rel):
                actual = live_consumers(rel)
                listed = sorted((entry.get("consumers") or {}).get("code") or ())
                self.assertTrue(actual, f"the matcher found no consumer of {rel}")
                self.assertTrue(listed, f"the inventory lists no consumer of {rel}")
                missing, stale = consumer_diff(actual, listed)
                self.assertEqual(
                    missing, [],
                    f"new live consumer(s) of {rel} not in the inventory: {missing}")
                self.assertEqual(
                    stale, [],
                    f"the inventory lists consumer(s) that no longer load {rel}: {stale}")

    def test_the_production_closure_is_recorded(self):
        """Deleting a D6 file breaks the loader-closure gate, not only an import."""
        sys.path.insert(0, str(REPO / "tests"))
        import loader_graph
        closure = {str(path) for path in loader_graph.production_closure()}
        declared = set(manifest()["production_closure"]["members"])
        self.assertEqual({p for p in D6_PATHS if p in closure}, declared)
        self.assertIn("mcp/agent_mcp.py", loader_graph.PRODUCTION_ENTRIES)
        self.assertNotIn("fleet/remote_desktop_commander.py", closure)

    def test_the_matcher_sees_nested_and_blob_loads(self):
        """Positive controls for the scanner, including its refusals."""
        samples = [
            ('_load("x", Path(__file__).parent / "fleet" / "node.py")',
             "fleet/node.py", True),
            ('router_mod = load("r", TREE / "fleet" / "agent_control_router.py")',
             "fleet/agent_control_router.py", True),
            ('src = blob(S2, "fleet/agent_control_router.py")',
             "fleet/agent_control_router.py", True),
            ('m = _load("x", MCP / "agent_mcp.py")', "mcp/agent_mcp.py", True),
            ("# see fleet/node.py for the binding rule", "fleet/node.py", False),
            ('_load("x", MCP / "controller.py")', "fleet/controller.py", False),
        ]
        for text, rel, expected in samples:
            with self.subTest(text=text):
                self.assertEqual(_file_loads(rel, text), expected)

    def test_the_l0_view_inventory_matches_too(self):
        l0 = {row["path"]: row for row in manifest()["l0_dependencies"]}
        listed = sorted(l0["fleet/view.py"]["consumers"]["code"])
        missing, stale = consumer_diff(live_consumers("fleet/view.py"), listed)
        self.assertEqual((missing, stale), ([], []))

    def test_the_plugin_consumer_chain_is_the_one_declared(self):
        bundle = yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))
        servers = bundle["mcp"]["servers"]
        names = [server["name"] for server in servers]
        self.assertEqual(duplicate_names(names), [])
        self.assertEqual(set(names), {"serveros-node", "serveros-agent"})
        agent = next(s for s in servers if s["name"] == "serveros-agent")
        self.assertEqual(agent["entry"], "bin/serveros-agent-mcp")
        self.assertTrue((REPO / "mcp" / agent["entry"]).is_file())
        self.assertEqual(bundle["mcp"]["path"], "mcp")
        shim = (REPO / "mcp" / agent["entry"]).read_text(encoding="utf-8")
        self.assertIn("import agent_mcp", shim)
        listed = set(entries()["mcp/agent_mcp.py"]["consumers"]["plugin"])
        self.assertEqual(listed, {"plugin/bundle.yaml", "mcp/bin/serveros-agent-mcp"})

    def test_the_cross_layer_dependency_is_still_real(self):
        declared = manifest()["cross_layer_dependencies"]
        self.assertEqual(len(declared), 1)
        row = declared[0]
        self.assertEqual((row["from"], row["to"], row["owner"]),
                         ("fleet_mcp/registry.py", "fleet/transport.py", "#407"))
        source = (REPO / row["from"]).read_text(encoding="utf-8")
        self.assertIn("from fleet.transport import", source,
                      "the hub registry no longer imports the L0 transport; update the inventory")


class AuthorityAndRefusalSurvive(unittest.TestCase):
    def setUp(self):
        self.router = router.FleetAgentControlRouter(local_device_ref="device:test")

    def request(self, action: str, **extra):
        return router.AgentControlRequest(
            action=action, device_ref="device:test",
            agent_runtime="agent-runtime:claude", **extra)

    def test_an_unknown_action_is_malformed(self):
        with self.assertRaises(router.Rejected):
            self.router.route(self.request("agent.session.nope"))

    def test_a_server_side_action_names_its_migration(self):
        answer = self.router.route(self.request("agent.session.handoff"))
        self.assertFalse(answer.ok)
        self.assertEqual(answer.reason, "ACTION_UNSUPPORTED")
        self.assertIn("handoff()", answer.detail)

    def test_an_unauthorized_send_is_refused_before_any_transport(self):
        answer = self.router.route(self.request("agent.session.send"))
        self.assertFalse(answer.ok)
        self.assertEqual(answer.reason, "INVALID_CONTEXT")
        self.assertFalse(answer.dispatched)

    def test_each_control_tool_schema_requires_a_trusted_context(self):
        """The schema is the first refusal: a send with no context is unusable."""
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(action=name):
                schema = agent_mcp.tool(name)["inputSchema"]
                self.assertIn("context_ref", schema["required"])

    def test_no_service_or_deploy_entrypoint_references_old_control(self):
        """CLI/unit entrypoints are part of the census; today there are none."""
        needles = ("agent_mcp", "agent_control_router", "fleet/controller.py",
                   "fleet/node.py", "fleet/interfaces.py",
                   "remote_desktop_commander")
        offenders = []
        for rel in tracked_all_files():
            if not (rel.endswith((".service", ".timer", ".in", ".ps1", ".sh"))
                    or rel.startswith("deploy/")):
                continue
            text = (REPO / rel).read_text(encoding="utf-8", errors="replace")
            if any(needle in text for needle in needles):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_every_local_control_action_is_still_in_the_router_vocabulary(self):
        action_names = set(router.control_actions())
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(action=name):
                self.assertIn(name, action_names)
        self.assertIn("agent.action.admit",
                      (REPO / "mcp" / "agent_mcp.py").read_text(encoding="utf-8"),
                      "control actions must still go through the daemon's admission")

    def test_the_controller_boundary_refuses_rather_than_inventing_a_fleet(self):
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "controller.yaml"
            self.assertIsNone(controller.declared_controller(absent))
            self.assertTrue(controller.authorization_outstanding(None))
            transport = rdc.RemoteDesktopCommanderTransport(declaration_path=absent)
            self.assertFalse(transport.controller_available())
            with self.assertRaises(rdc.ControllerNotExposed):
                transport.list_devices()
        self.assertTrue(controller.boundary_rules())

    def test_the_hub_does_not_import_the_local_control_plane(self):
        offenders = []
        for path in sorted((REPO / "fleet_mcp").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"agent_mcp|agent_control_router", text):
                offenders.append(str(path.relative_to(REPO)))
        self.assertEqual(offenders, [])
        local = (REPO / "mcp" / "agent_mcp.py").read_text(encoding="utf-8")
        self.assertNotIn("import fleet_mcp", local)
        self.assertNotIn("from fleet_mcp", local)


class ThePluginHasNoSecondControlPlane(unittest.TestCase):
    def test_tool_names_are_unique_and_disjoint_across_servers(self):
        agent = tuple(agent_mcp.tool_names())
        node = tuple(node_mcp.tool_names())
        self.assertEqual(duplicate_names(agent), [])
        self.assertEqual(duplicate_names(node), [])
        self.assertEqual(set(agent) & set(node), set())
        first_class = tuple(fleet_tools.tool_names())
        served = {tool.name for tool in fleet_tools.SERVED_TOOLS}
        self.assertLessEqual(set(first_class), served)
        self.assertGreater(len(served), len(first_class),
                           "the native/mirror definitions should be counted too")
        self.assertTrue(all(name.startswith("fleet_") for name in served))
        plugin = set(agent) | set(node)
        self.assertEqual(served & plugin, set())

    def test_each_control_action_is_declared_exactly_once(self):
        agent = tuple(agent_mcp.tool_names())
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(action=name):
                self.assertEqual(agent.count(name), 1)

    def test_the_plugin_does_not_declare_hub_tools(self):
        plugin_names = set(agent_mcp.tool_names()) | set(node_mcp.tool_names())
        self.assertEqual({n for n in plugin_names if n.startswith("fleet_")}, set())
        # And the hub really does serve the remote plane, so the map above is
        # comparing two live surfaces rather than one surface to an empty set.
        self.assertIn("fleet_list_devices", fleet_tools.tool_names())
        self.assertIn("fleet_send_to_session", fleet_tools.tool_names())


class TheLayerClaimIsNotAheadOfTheTree(unittest.TestCase):
    def test_fleet_is_still_split_while_these_files_exist(self):
        doc = yaml.safe_load(LAYERS.read_text(encoding="utf-8"))
        fleet = doc["directories"]["fleet"]
        self.assertTrue(fleet["split"])
        declared = set(manifest()["layers"]["fleet_l2_members"])
        # Parse the prose into file names: `assertIn(member, prose)` would also
        # pass for a sentence like "controller.py removed".
        l2 = set(re.findall(r"[A-Za-z_]+\.py", fleet["parts"]["L2"]))
        self.assertLessEqual(declared, l2)
        l0 = set(re.findall(r"[A-Za-z_]+\.(?:py|yaml)", fleet["parts"]["L0"]))
        self.assertLessEqual({"registry.yaml", "profiles.yaml"}, l0)
        self.assertEqual(doc["directories"]["mcp"], "L2")

    def test_d5_is_not_claimed_while_d6_is_blocked(self):
        doc = manifest()
        blocked = {row["id"] for row in doc["blocked"]}
        self.assertIn("D5_REMOVE_FLEET_SPLIT", blocked)
        for rel in D6_PATHS:
            self.assertTrue((REPO / rel).is_file(),
                            f"{rel} vanished without an inventory update")


class TheInventoryIsWrittenDown(unittest.TestCase):
    def test_the_doc_names_every_entry(self):
        self.assertTrue(INVENTORY_DOC.is_file())
        text = INVENTORY_DOC.read_text(encoding="utf-8")
        for rel in D6_PATHS | {"fleet/registry.yaml", "fleet/profiles.yaml"}:
            with self.subTest(path=rel):
                self.assertIn(rel, text)

    def test_every_equivalence_row_is_explicit_and_not_a_claim(self):
        rows = manifest()["equivalence"]
        self.assertGreaterEqual(len(rows), 5)
        for row in rows:
            with self.subTest(old=row["old"]):
                self.assertIn("equivalent", row)
                self.assertIsInstance(row["equivalent"], bool)
                self.assertTrue(row["why"])


class TheGuardsCanGoRed(unittest.TestCase):
    """Positive controls: a guard that cannot fail is a comment."""

    def test_a_retired_entry_without_a_replacement_is_rejected(self):
        bad = {"path": "x.py", "disposition": "RETIRED",
               "replacement": {"kind": "none"}, "consumers": {"code": []}}
        self.assertTrue(entry_errors(bad, fleet_tool_names={"fleet_list_devices"}))

    def test_a_retired_entry_naming_a_missing_tool_is_rejected(self):
        bad = {"path": "x.py", "disposition": "RETIRED",
               "replacement": {"kind": "fleet_mcp", "fleet_mcp_tools": ["not_a_tool"]},
               "evidence_tests": ["tests/test_fleet_old_control_retirement.py"],
               "consumers": {"code": []}}
        errors = entry_errors(bad, fleet_tool_names={"fleet_list_devices"})
        self.assertTrue(any("does not serve" in e for e in errors))

    def test_a_retired_entry_with_a_live_consumer_is_rejected(self):
        bad = {"path": "x.py", "disposition": "RETIRED",
               "replacement": {"kind": "fleet_mcp",
                               "fleet_mcp_tools": ["fleet_list_devices"]},
               "evidence_tests": ["tests/test_fleet_old_control_retirement.py"],
               "consumers": {"code": ["mcp/agent_mcp.py"]}}
        self.assertTrue(entry_errors(bad, fleet_tool_names={"fleet_list_devices"}))

    def test_a_retained_entry_claiming_a_replacement_is_rejected(self):
        bad = {"path": "x.py", "disposition": "RETAIN_ACTIVE",
               "replacement": {"kind": "fleet_mcp",
                               "fleet_mcp_tools": ["fleet_list_devices"]},
               "why_not_retired": "x", "unblock": "y"}
        self.assertTrue(entry_errors(bad, fleet_tool_names={"fleet_list_devices"}))

    def test_a_new_live_consumer_is_visible(self):
        missing, stale = consumer_diff(
            ["mcp/agent_mcp.py", "mcp/new_consumer.py"], ["mcp/agent_mcp.py"])
        self.assertEqual(missing, ["mcp/new_consumer.py"])
        self.assertEqual(stale, [])

    def test_a_duplicate_tool_name_is_visible(self):
        self.assertEqual(duplicate_names(["a", "b", "a"]), ["a"])
        self.assertEqual(duplicate_names(["a", "b"]), [])

    def test_the_retirement_validator_accepts_a_correct_retirement(self):
        """So the failures above are not passing because the rule is always-red."""
        good = {"path": "x.py", "disposition": "RETIRED",
                "replacement": {"kind": "fleet_mcp",
                                "fleet_mcp_tools": ["fleet_list_devices"]},
                "evidence_tests": ["tests/test_fleet_old_control_retirement.py"],
                "consumers": {"code": []}}
        self.assertEqual(entry_errors(good, fleet_tool_names={"fleet_list_devices"}), [])


if __name__ == "__main__":
    unittest.main()
