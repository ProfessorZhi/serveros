"""`fleet/fleet-mcp-contract.yaml` against the code it claims to describe.

A contract that nothing reads is a comment. Every claim below is one the file
makes in prose and this module checks mechanically, so that the file cannot
quietly fall behind the implementation — and, more importantly, so that the one
claim that is a *refusal* (this surface is not a fifth owner of fleet facts, and
is deliberately not declared in `mcp-contract.yaml`) fails loudly if a later
round reopens it by adding a declaration rather than by making a decision.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from fleet_mcp import node_protocol as np
from fleet_mcp import scopes
from fleet_mcp import tools as tools_module
from fleet_mcp import version as version_module
from fleet_mcp.config import load_config
from fleet_mcp.errors import BAD_REQUEST, FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.server import SUPPORTED_PROTOCOL_VERSIONS

REPO = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO / "fleet" / "fleet-mcp-contract.yaml"


def contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def mcp_contract() -> dict:
    return yaml.safe_load((REPO / "fleet" / "mcp-contract.yaml").read_text(encoding="utf-8"))


class TheContractDescribesThisCode(unittest.TestCase):
    def test_the_file_is_well_formed_and_versioned(self):
        document = contract()
        self.assertEqual(document["schema"], "serveros/fleet-mcp.v1")
        self.assertTrue(document["implementation"])
        self.assertTrue(document["rules"], "a contract with no rules is a description")

    def test_the_named_implementation_exists_and_is_the_entry_point(self):
        document = contract()
        root = REPO / document["implementation"]
        self.assertTrue(root.is_dir(), document["implementation"])
        self.assertTrue((root / "__init__.py").exists())
        module, _, attribute = document["implementation_entry"].partition(":")
        self.assertEqual(module, "fleet_mcp.runtime")
        self.assertEqual(attribute, "main")
        self.assertEqual(document["server_name"], version_module.SERVER_NAME)
        self.assertEqual(document["tool_surface_revision"], version_module.TOOL_SURFACE_REVISION)

    def test_the_declared_tools_are_the_served_tools_in_order(self):
        """Order included: a client caches the list, and a reordering is a
        change to what it read even when the set is identical."""
        declared = list(contract()["tools"])
        self.assertEqual(declared, tools_module.tool_names())
        self.assertEqual(len(declared), 8)

    def test_each_declared_verb_matches_the_annotation_the_tool_carries(self):
        declared = contract()["tools"]
        for tool in tools_module.TOOLS:
            with self.subTest(tool=tool.name):
                verb = declared[tool.name]["verb"]
                self.assertIn(verb, ("read", "write"))
                self.assertEqual(
                    verb == "read", tool.annotations["readOnlyHint"],
                    f"{tool.name} is declared {verb} and annotated otherwise",
                )

    def test_the_write_tools_are_exactly_the_two_named_ones_in_order(self):
        """The set is asserted by name, not counted.

        "Which verbs here can change a device" is the question the annotations
        exist to answer, so it is answered against a literal list rather than
        against a length — a count would stay green through a swap.
        """
        writes = [t.name for t in tools_module.TOOLS if not t.annotations["readOnlyHint"]]
        self.assertEqual(writes, ["fleet_send_to_session", "fleet_create_session"])
        for name in writes:
            with self.subTest(tool=name):
                self.assertEqual(contract()["tools"][name]["verb"], "write")

    def test_the_new_tool_is_appended_rather_than_inserted(self):
        """A cached tool list is a promise about order too.

        `fleet_create_session` is the eighth tool and must sit after the seven
        that `fleet-mcp-1` served, so a client that cached that list sees its own
        seven unchanged and one appended, rather than a renumbering.
        """
        names = tools_module.tool_names()
        self.assertEqual(names[0], "fleet_list_devices")
        self.assertEqual(names[-1], "fleet_create_session")
        self.assertNotIn("fleet_create_session", names[:-1])
        self.assertEqual(list(contract()["tools"])[-1], "fleet_create_session")

    def test_every_tool_names_the_owner_that_answers_it(self):
        """The file's central promise: this surface owns no fact.

        A tool whose entry does not say who answers it is a tool whose
        duplication with another surface would go unnoticed.
        """
        for name, block in contract()["tools"].items():
            with self.subTest(tool=name):
                self.assertTrue(block.get("answers"), f"{name} does not say what it answers")
                self.assertTrue(
                    block.get("answered_by"),
                    f"{name} does not name the owner that answers it",
                )


class TheNodeTransportIsTheWireModule(unittest.TestCase):
    def test_the_declared_routes_are_the_enforced_routes(self):
        declared = tuple(contract()["node_transport"]["routes"])
        self.assertEqual(declared, np.ROUTES)
        self.assertEqual(contract()["node_transport"]["route_set"], "closed")
        self.assertTrue(contract()["node_transport"]["declared_in"].endswith("ROUTES"))

    def test_the_declared_protocol_and_scheme_constants_match(self):
        node = contract()["node_transport"]
        self.assertEqual(node["protocol"], np.PROTOCOL)
        self.assertEqual(
            node["authentication"]["replay_window_seconds"], np.WINDOW_SECONDS
        )
        # The base string is written out in both places, so a drift between the
        # file and the module would be a signature that verifies on one side
        # only. The check is structural rather than textual: the declared form
        # must name six components, and each of the six inputs to `base_string`
        # must actually change it — otherwise a component is declared that the
        # module ignores, or signed that the file forgot.
        declared = [part.strip() for part in
                    node["authentication"]["base"].replace("\\n", "\n").split("\n")]
        self.assertEqual(declared,
                         ["METHOD", "PATH", "sha256hex(body)", "device_ref",
                          "timestamp", "nonce"])

        baseline = np.base_string("GET", "/v1/health", b"", "device:x", "1", "n")
        perturbations = {
            "METHOD": np.base_string("POST", "/v1/health", b"", "device:x", "1", "n"),
            "PATH": np.base_string("GET", "/v1/other", b"", "device:x", "1", "n"),
            "sha256hex(body)": np.base_string("GET", "/v1/health", b"{}", "device:x", "1", "n"),
            "device_ref": np.base_string("GET", "/v1/health", b"", "device:y", "1", "n"),
            "timestamp": np.base_string("GET", "/v1/health", b"", "device:x", "2", "n"),
            "nonce": np.base_string("GET", "/v1/health", b"", "device:x", "1", "m"),
        }
        self.assertEqual(sorted(perturbations), sorted(declared))
        for component, value in perturbations.items():
            with self.subTest(component=component):
                self.assertNotEqual(baseline, value,
                                    f"{component} is declared but does not reach the signature")
        self.assertIs(node["authentication"]["query_signed"], False)

    def test_the_query_string_really_is_outside_the_signature(self):
        """The contract's claim, exercised rather than restated."""
        path, query = np.split_path_and_query("/v1/sessions?limit=5&state=idle")
        self.assertEqual(path, "/v1/sessions")
        self.assertEqual(query, "limit=5&state=idle")
        headers = np.sign("s", method="GET", path=path, body=b"", device_ref="device:x")
        replay = np.ReplayCache()
        self.assertEqual(
            np.verify("s", method="GET", path=path, body=b"", headers=headers,
                      expected_device_ref="device:x", replay=replay),
            "device:x",
        )

    def test_the_declared_implementations_exist(self):
        node = contract()["node_transport"]
        for key in ("implementation_client", "implementation_connector", "shared_wire_module"):
            with self.subTest(key=key):
                self.assertTrue((REPO / node[key]).exists(), node[key])

    def test_the_control_floor_defaults_to_refusing_everything(self):
        floor = contract()["node_transport"]["control_floor"]
        self.assertEqual(floor["default"], [])
        self.assertIn("refused", floor["default_means"])

    def test_the_tunnel_is_reconciled_with_the_existing_prohibition(self):
        """The one place this design touches a standing prohibition.

        `controller-contract.yaml` forbids reaching a device over SSH because
        the controller is not exposed. If this surface ever stopped naming that
        prohibition, the next reader would find an SSH tunnel and no answer.
        """
        reconciliation = contract()["node_transport"]["boundary_reconciliation"]
        self.assertIn("silent_transport_substitution", reconciliation)
        controller = yaml.safe_load(
            (REPO / "fleet" / "controller-contract.yaml").read_text(encoding="utf-8")
        )
        forbidden = {entry["id"] for entry in controller["boundary"]["forbidden"]}
        self.assertIn("silent_transport_substitution", forbidden)
        # And the controller connector that file declines to implement is still
        # declined — this surface is not it.
        self.assertEqual(controller["connector"]["status"], "NOT_IMPLEMENTED")


class TheIngressIsTheCodeThatServesIt(unittest.TestCase):
    def test_the_declared_protocol_versions_are_the_supported_ones(self):
        declared = list(contract()["ingress"]["protocol_versions"])
        self.assertEqual(declared, list(SUPPORTED_PROTOCOL_VERSIONS))

    def test_the_declared_discovery_path_is_the_served_one(self):
        auth = contract()["ingress"]["auth"]
        self.assertEqual(auth["discovery"], version_module.PROTECTED_RESOURCE_PATH)
        self.assertEqual(auth["authorization_server"], version_module.AUTHORIZATION_SERVER_PATH)

    def test_the_declared_scopes_are_the_implemented_ones(self):
        """The file's scope list and `fleet_mcp/scopes.py` are one list.

        They were two, and drifted; `scopes.py` exists so that the vocabulary a
        credential is minted from and the vocabulary a tool checks are the same
        tuple rather than two copies that agree today.
        """
        declared = tuple(contract()["ingress"]["auth"]["scopes"])
        self.assertEqual(declared, scopes.ALLOWED_SCOPES)
        self.assertEqual(declared, scopes.IMPLEMENTED_SCOPES)

    def test_the_planned_scopes_are_declared_and_cannot_be_minted(self):
        """Designed-but-unwritten is a state, and it is checked rather than
        described: a planned scope that leaked into `ALLOWED_SCOPES` would be
        grantable without any tool being written for it."""
        planned = tuple(contract()["ingress"]["auth"]["scopes_designed_not_implemented"])
        self.assertEqual(planned, scopes.PLANNED_SCOPES)
        self.assertFalse(set(planned) & set(scopes.ALLOWED_SCOPES), "planned scopes are grantable")

    def test_the_default_grantable_set_is_still_only_read_and_send(self):
        """Default-off is the whole point of the new scope.

        Adding `fleet:create` to the vocabulary must not add it to what a
        deployment issues when it says nothing: an operator opts in by editing a
        local binding. This is exercised through `load_config` rather than
        against the constant, because the loader is what actually decides — a
        constant that is correct and unused would pass a weaker test.
        """
        self.assertEqual(scopes.DEFAULT_GRANTABLE_SCOPES,
                         (scopes.SCOPE_READ, scopes.SCOPE_SEND))
        self.assertNotIn(scopes.SCOPE_CREATE, scopes.DEFAULT_GRANTABLE_SCOPES)

        tmp = Path(tempfile.mkdtemp(prefix="fleet-mcp-scope-default-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        binding = tmp / "binding.yaml"
        binding.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: device:test-host\n"
            "auth:\n"
            f"  pat_anchor: {anchor}\n",
            encoding="utf-8",
        )
        loaded = load_config(binding)
        self.assertEqual(loaded.auth.grantable_scopes, scopes.DEFAULT_GRANTABLE_SCOPES)
        self.assertNotIn(scopes.SCOPE_CREATE, loaded.auth.grantable_scopes)

    def test_a_binding_cannot_grant_a_scope_this_surface_does_not_implement(self):
        """Asking for an unwritten scope fails at load, not at the consent form.

        A deployment that could name `fleet:control` here would offer the
        operator a box to tick that the authorization server then refuses —
        a configuration fault that presents as a client fault.
        """
        tmp = Path(tempfile.mkdtemp(prefix="fleet-mcp-scope-refused-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        binding = tmp / "binding.yaml"
        binding.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: device:test-host\n"
            "auth:\n"
            f"  pat_anchor: {anchor}\n"
            "  grantable_scopes: [fleet:read, fleet:control]\n",
            encoding="utf-8",
        )
        with self.assertRaises(FleetRefusal) as caught:
            load_config(binding)
        self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertIn("fleet:control", caught.exception.detail)
        # And the refusal names what the surface *does* implement, so an
        # operator reads the alternative rather than guessing it.
        for scope in scopes.ALLOWED_SCOPES:
            with self.subTest(scope=scope):
                self.assertIn(scope, caught.exception.detail)

    def test_the_declared_scopes_partition_the_tools(self):
        """Every scope in the vocabulary is a thing some tool does.

        A scope naming no tool is a credential with no verb behind it — it would
        read as an authorization to a caller and authorize nothing.
        """
        raw = contract()["ingress"]["auth"]["scopes"]
        for name, description in raw.items():
            with self.subTest(scope=name):
                self.assertTrue(description, f"{name} says nothing about what it grants")
        # `fleet:read` is the one scope whose tools are not named in the file —
        # they are all of them, so it is checked against the annotations rather
        # than against a list that would have to be maintained.
        read_tools = [t.name for t in tools_module.TOOLS if t.annotations["readOnlyHint"]]
        self.assertEqual(len(read_tools), 6)
        self.assertEqual(
            sorted(set(tools_module.tool_names()) - set(read_tools)),
            ["fleet_create_session", "fleet_send_to_session"],
        )

    def test_the_ingress_declines_to_fetch_a_caller_supplied_target(self):
        self.assertIs(contract()["ingress"]["request_shaped_fetch"], False)
        forbidden = ("url", "uri", "host", "endpoint", "command", "shell", "path",
                     "callback", "webhook", "target")
        for tool in tools_module.TOOLS:
            for name in (tool.input_schema.get("properties") or {}):
                with self.subTest(tool=tool.name, parameter=name):
                    self.assertNotIn(name.lower(), forbidden)

    def test_the_consent_step_cannot_be_completed_by_an_agent(self):
        self.assertIs(contract()["ingress"]["auth"]["consent_requires_owner_token"], True)
        self.assertIs(contract()["ingress"]["auth"]["requires_human_consent"], True)

    def test_off_host_reach_is_an_operators_decision_and_is_reported_outstanding(self):
        blockers = {b["id"]: b for b in contract()["blocked_on"]}
        self.assertIn("public_exposure_decision", blockers)
        self.assertEqual(blockers["public_exposure_decision"]["owner"], "the human operator")
        self.assertIn("chatgpt_oauth_consent", blockers)


class Addressability(unittest.TestCase):
    def test_the_declared_session_ref_form_is_what_the_identity_module_builds(self):
        key = SessionKey.compose("device:work-fj", "claude", "abc")
        self.assertEqual(key.ref, "device:work-fj|claude|abc")
        separator = contract()["session_ref"]["separator"]
        self.assertEqual(separator, "|")
        self.assertEqual(key.ref.split(separator), ["device:work-fj", "claude", "abc"])
        self.assertEqual(contract()["session_ref"]["form"].replace("<id>", "work-fj")
                         .replace("<provider>", "claude").replace("<native session id>", "abc"),
                         key.ref)

    def test_a_bare_native_id_is_not_an_address(self):
        from fleet_mcp.errors import FleetRefusal

        with self.assertRaises(FleetRefusal):
            SessionKey.parse("b2cb3f10-0000-0000-0000-000000000000")


class WhereThisSurfaceKeepsItsFiles(unittest.TestCase):
    def test_the_declared_roots_are_the_code_that_computes_them(self):
        from fleet_mcp import local_roots as lr

        declared = contract()["local_roots"]
        self.assertEqual(declared["implementation"], "fleet_mcp/local_roots.py")
        self.assertTrue((REPO / declared["implementation"]).exists())

        # Asserted against the module rather than restated as a string, so a
        # change to the platform default cannot leave this file describing a
        # directory nothing uses. Only the trailing segment is compared
        # literally: the leading ones are expanded from the environment
        # (`%LOCALAPPDATA%`, `$XDG_CONFIG_HOME`) and differ per host, while the
        # last segment is the one that names this surface.
        platform_key = "windows" if sys.platform == "win32" else "posix"
        computed = {"binding": lr.binding_root(), "state": lr.state_root()}
        for name, root in computed.items():
            with self.subTest(root=name):
                declared_name = Path(declared[name][platform_key].replace("\\", "/")).name
                self.assertEqual(root.name, declared_name, f"{name} root is {root}")

        # Two roots, because a binding is not state. Neither may contain the
        # other, or the re-derivable layer would sit inside the one that
        # accumulates runtime state — which is what makes a binding
        # re-derivable in the first place.
        binding, state = computed["binding"].resolve(), computed["state"].resolve()
        self.assertNotEqual(binding, state)
        self.assertNotIn(binding, state.parents)
        self.assertNotIn(state, binding.parents)

        # And neither is the root the repository closes, for either platform —
        # a host is one platform, so the claim is checked against the one that
        # is running.
        closed = Path(lr.CLOSED_ROOT).expanduser().resolve()
        for name, root in computed.items():
            with self.subTest(root=name):
                self.assertFalse(str(root.resolve()).startswith(str(closed) + os.sep))
                self.assertNotEqual(root.resolve(), closed)
        self.assertIs(declared["credentials_in_either_root"], False)

    def test_the_contract_says_why_it_is_not_the_serveros_root(self):
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("node-root-contract.yaml", text)
        self.assertIn("move it rather than widen this list", text)

    def test_the_declared_override_is_the_one_the_module_honours(self):
        self.assertIn("FLEET_MCP_HOME", contract()["local_roots"]["override"])


class TheBoundaryWithTheOtherSurfaces(unittest.TestCase):
    def test_this_surface_is_deliberately_not_declared_as_an_mcp_surface(self):
        """The decision, encoded so it has to be unmade on purpose.

        `mcp-contract.yaml` withdrew a surface by this name because it would
        have been a fourth answer to `device.list`. This round built an ingress
        and did not reinstate the surface: the declaration lives in its own
        file, where it says what it owns, what it does not, and which existing
        owner answers each of its tools. Adding a `serveros-fleet-mcp` entry to
        `mcp-contract.yaml` would silently reverse that, so this test fails if
        one appears.
        """
        surfaces = mcp_contract()["surfaces"]
        self.assertNotIn(version_module.SERVER_NAME, surfaces)
        # ...and the withdrawal it would reverse is still on the record.
        agent = surfaces["serveros-agent-mcp"]
        self.assertEqual(agent["supersedes"]["surface"], version_module.SERVER_NAME)

    def test_the_overlap_is_named_against_an_owner_that_exists(self):
        """An overlap recorded against a surface nobody declared is a note to
        nobody. This checks the named counterpart is real."""
        surfaces = mcp_contract()["surfaces"]
        overlaps = contract()["overlap_with"]
        self.assertTrue(overlaps, "the overlap is the reason this file exists; name it")
        for entry in overlaps:
            with self.subTest(against=entry["surface"]):
                self.assertIn(entry["surface"], surfaces)
                self.assertTrue(entry["shares"])
                self.assertTrue(entry["not_shared"])
                self.assertTrue(entry["the_alternative_not_taken"])

    def test_the_tool_names_do_not_collide_with_the_local_surfaces(self):
        """Distinct names, so an assistant composed with both cannot be handed
        two answers to one question under one name."""
        theirs = set(mcp_contract()["surfaces"]["serveros-agent-mcp"]["tools"])
        theirs |= set(mcp_contract()["surfaces"]["serveros-node-mcp"]["tools"])
        ours = set(tools_module.tool_names())
        self.assertFalse(theirs & ours, sorted(theirs & ours))

    def test_the_contract_does_not_claim_what_the_other_surface_owns(self):
        ours = " ".join(contract()["owns"]).lower()
        for claim in ("device identity", "session truth", "agent runtime", "read model"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, ours)


class WhatIsNotBuilt(unittest.TestCase):
    def test_the_unbuilt_capabilities_are_named_with_a_reason(self):
        entries = contract()["not_implemented"]
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(entry=entry.get("id")):
                self.assertTrue(entry.get("id"))
                self.assertTrue(entry.get("why"), "a gap without a reason reads as an oversight")

    def test_the_unbuilt_capabilities_are_actually_unreachable(self):
        """The list is a claim about the code, so it is checked against it.

        `session_create` was on this list through `fleet-mcp-1` and is not any
        more — it has a tool, an admission rule and a scope. The four the list
        still names are the ones with no tool, and each is checked to have no
        tool by *name* rather than by absence from a hand-kept list, so adding
        one to the surface without removing it here fails.
        """
        built = {name.removeprefix("fleet_") for name in tools_module.tool_names()}
        self.assertIn("create_session", built, "session_create is implemented now")
        for absent in ("session_start", "session_control", "session_approve",
                       "session_navigate"):
            with self.subTest(capability=absent):
                self.assertNotIn(absent, built)
        declared_ids = {entry["id"] for entry in contract()["not_implemented"]}
        self.assertNotIn("session_create", declared_ids)
        for absent in ("session_start", "session_control", "session_approve",
                       "session_navigate"):
            with self.subTest(declared=absent):
                self.assertIn(absent, declared_ids)
        self.assertNotIn("POST /v1/sessions", np.ROUTES)
        self.assertNotIn("POST /v1/exec", np.ROUTES)

    def test_no_unwritten_verb_has_a_scope_it_could_be_reached_through(self):
        """The two halves of "designed but unwritten" must both hold.

        Naming a verb in the design while its scope is mintable would be the
        dangerous half: a credential could carry `fleet:control` and there would
        be nothing behind it to check the call against. The scope tuple is where
        that is prevented, so this asserts the tuple rather than the prose.
        """
        for planned in scopes.PLANNED_SCOPES:
            with self.subTest(scope=planned):
                self.assertNotIn(planned, scopes.ALLOWED_SCOPES)
                self.assertFalse(scopes.requires_implemented_scope(planned))
        # And nothing in the surface references one. Checked as a name scan over
        # the modules that gate calls, not as a string match on a call form: the
        # constants are what a caller would use, and `SCOPE_CONTROL` appearing
        # anywhere in `fleet.py` would mean a verb gated on an unmintable scope
        # — a tool nobody can ever be given.
        for module in ("fleet.py", "tools.py"):
            source = (REPO / "fleet_mcp" / module).read_text(encoding="utf-8")
            for planned in scopes.PLANNED_SCOPES:
                constant = "SCOPE_" + planned.split(":", 1)[1].upper()
                with self.subTest(module=module, constant=constant):
                    self.assertNotIn(constant, source)

    def test_no_route_can_execute_a_command(self):
        """Checked against the route table, not against the prose.

        The prose says "no shell" in several places, deliberately; a scan for
        the word would be testing the documentation of the refusal rather than
        the refusal. The route table is the thing that decides.
        """
        self.assertFalse(any("exec" in route or "shell" in route for route in np.ROUTES))
        # And no tool takes one as an argument, under any spelling.
        for tool in tools_module.TOOLS:
            properties = set(tool.input_schema.get("properties") or {})
            with self.subTest(tool=tool.name):
                self.assertFalse(properties & {"command", "shell", "exec", "argv", "script"})


class Deployment(unittest.TestCase):
    """Where this runs is a claim about two other files, so it is checked."""

    def test_the_declared_installer_and_unit_template_are_there(self):
        declared = contract()["deployment"]
        for key in ("installer", "unit_template"):
            with self.subTest(key=key):
                path = REPO / declared[key]
                self.assertTrue(path.exists(), declared[key])
                self.assertIn("fleet_mcp/", declared[key])

    def test_the_declared_names_are_the_installers_own(self):
        from fleet_mcp.deploy import install as deploy

        declared = contract()["deployment"]
        self.assertEqual(declared["unit"], deploy.UNIT_NAME)
        self.assertEqual(declared["service_name"], deploy.SERVICE_NAME)
        self.assertEqual(declared["artifact"]["schema"], deploy.ARTIFACT_SCHEMA)
        self.assertEqual(declared["artifact"]["entry"], deploy.ENTRY)
        self.assertEqual(tuple(declared["artifact"]["payload"]), tuple(deploy.PAYLOAD))
        self.assertEqual(declared["installer"], f"fleet_mcp/deploy/{Path(deploy.__file__).name}")

    def test_the_declared_health_path_is_the_served_one(self):
        self.assertEqual(contract()["deployment"]["health"], version_module.HEALTH_PATH)

    def test_the_unit_template_names_both_placeholders_it_is_rendered_with(self):
        from fleet_mcp.deploy import install as deploy

        template = (REPO / contract()["deployment"]["unit_template"]).read_text(encoding="utf-8")
        for placeholder in (deploy.RUNTIME_PLACEHOLDER, deploy.CONFIG_PLACEHOLDER):
            self.assertIn(placeholder, template, placeholder)
        # And it starts the entry point the artifact declares, at the path the
        # template is rendered against — not at a checkout.
        self.assertIn(f"{deploy.RUNTIME_PLACEHOLDER}/{deploy.ENTRY}", template)

    def test_the_launchers_are_executable_as_committed(self):
        """The recorded modes, not the working tree's.

        Which mode each file needs is a fact about how it is started, and the
        repository already answers it two ways: `bin/serverosctl` and
        `mcp/bin/*` are 100755 because a unit or a PATH entry names them, while
        `scripts/release.py` is 100644 because it is run as `python3
        scripts/release.py`. This artifact has one of each, so it is held to
        both — and Windows has no exec bit to lose, so a mistake here is one
        only the index can catch.
        """
        import subprocess

        declared = contract()["deployment"]
        modes = {}
        for relative in (declared["installer"], declared["artifact"]["entry"],
                         "scripts/release.py"):
            result = subprocess.run(["git", "ls-files", "-s", "--", relative],
                                    cwd=REPO, capture_output=True, text=True)
            if result.returncode != 0 or not result.stdout.strip():
                self.skipTest(f"{REPO} is not a git checkout of {relative}")
            modes[relative] = result.stdout.split()[0]

        self.assertEqual(modes[declared["artifact"]["entry"]], "100755",
                         f"{declared['artifact']['entry']} is started by a unit, "
                         f"so the unit execs it directly")
        self.assertEqual(modes[declared["installer"]], modes["scripts/release.py"],
                         "this installer is operator-run, like scripts/release.py; "
                         "the two are the same kind of program and carry the same mode")


if __name__ == "__main__":
    unittest.main()
