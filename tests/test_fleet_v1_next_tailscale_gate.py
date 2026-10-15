"""Lane E — offline gate tests for the Next ingress's own Tailscale node.

No real systemd, tailscaled, Funnel, device, OAuth, Feishu console, ChatGPT
plugin or SSH connection is accessed. The gate is pure: every case here is a
plan dict in and a decision out, and every case asserts that nothing was
written and nothing was executed.

The groups mirror the ways this goes wrong: reusing the old node; publishing an
origin whose paths disagree with themselves; registering without the owner's own
login; **demanding an HTTP 200 from a backend that is deliberately not running**;
**accepting a self-reported flag as public liveness**; and letting a lower stage
claim a higher one.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import next_tailscale_gate as gate     # noqa: E402

HOST = "fleet-next-abc.ts.net"
ISSUER = f"https://{HOST}"

#: The ladder's state flags in order. `public_verified` is listed with them
#: because it occupies a rung, but it is NOT a state field — it is derived from
#: `public_readback`, which is the whole point of the case below.
LADDER = ["staged", "node_registered", "owner_login_attested", "route_provisioned",
          "tls_cert_ready", "unit_live", "public_verified", "feishu_authenticated",
          "chatgpt_installed"]


def plan() -> dict:
    """A plan that is isolated, and stands at the bottom of the ladder."""
    return {
        "schema": gate.SCHEMA,
        "existing": {
            "node_name": "fleet-existing",
            "state_dir": "/var/lib/tailscale",
            "socket": "/run/tailscale/tailscaled.sock",
            "service_unit": "tailscaled.service",
            "funnel_target_port": 8401,
        },
        "next": {
            "node_name": "fleet-existing-next",
            "state_dir": "/home/operator/.local/state/fleet-next-tailscale",
            "socket": "/home/operator/.local/state/fleet-next-tailscale/tailscaled.sock",
            "service_unit": "fleet-next-tailscaled.service",
            "tailnet_host": HOST,
            "funnel_target_port": 3411,
            "local_listen": {"host": "127.0.0.1", "port": 3411},
            "issuer": ISSUER,
        },
        "routes": {
            "health": "/health",
            "mcp": "/mcp",
            "oauth_protected_resource": "/.well-known/oauth-protected-resource",
            "oauth_authorization_server": "/.well-known/oauth-authorization-server",
            "feishu_callback": "/oauth/feishu/callback",
        },
        "state": {
            "staged": False, "node_registered": False, "owner_login_attested": False,
            "route_provisioned": False, "tls_cert_ready": False, "unit_live": False,
            "feishu_authenticated": False, "chatgpt_installed": False,
        },
    }


def readback(**over) -> dict:
    """What a real fetch of the public origin would have recorded."""
    base = {
        "health_status": 200,
        "oauth_protected_resource_status": 200,
        "oauth_authorization_server_status": 200,
        "mcp_tools_seen": 33,
        "issuer": ISSUER,
        "audience": f"{ISSUER}/mcp",
    }
    base.update(over)
    return base


def at(stage: str) -> dict:
    """A plan standing at `stage`, with every rung below it satisfied the way
    that rung is actually satisfied — a recorded readback for `public_verified`,
    a flag for the others."""
    item = plan()
    if not stage:
        return item
    for flag in LADDER:
        if flag == "public_verified":
            item["public_readback"] = readback()
        else:
            item["state"][flag] = True
        if flag == stage:
            break
    return item


class EveryDecisionIsSideEffectFreeTest(unittest.TestCase):

    def test_a_ready_plan_declares_no_writes_and_no_execution(self):
        got = gate.evaluate(plan())
        self.assertEqual(got["decision"], "PLAN_READY")
        self.assertEqual(got["writes"], [])
        self.assertEqual(got["executes"], [])
        self.assertFalse(got["old_node_changed"])
        self.assertFalse(got["authkey_required"])

    def test_a_blocked_plan_is_equally_inert(self):
        broken = plan()
        broken["next"]["node_name"] = broken["existing"]["node_name"]
        got = gate.evaluate(broken)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertEqual(got["writes"], [])
        self.assertEqual(got["executes"], [])

    def test_the_example_plan_shipped_with_the_module_is_valid(self):
        path = ROOT / "fleet_mcp" / "deploy" / "next_tailscale_plan.example.json"
        loaded = json.loads(path.read_text(encoding="utf-8"))
        loaded["next"]["tailnet_host"] = HOST
        loaded["next"]["issuer"] = ISSUER
        got = gate.evaluate(loaded)
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
        self.assertEqual(got["stage"], "SOURCE")


class ProvisioningARouteIsNotServingItTest(unittest.TestCase):
    """The circular block this file exists to keep out.

    Publishing a Funnel at a restricted loopback backend that is not running yet
    is a real step — and it is exactly the step at which HTTP cannot answer. A
    run sheet that asked for `/health` 200 before the unit starts would forbid
    its own next stage: the unit starts one rung later.
    """

    def test_the_route_stages_come_before_the_unit_is_live(self):
        names = [name for name, _ in gate.STAGES]
        self.assertLess(names.index("NEXT_ROUTE_PROVISIONED"),
                        names.index("NEXT_UNIT_LIVE"))
        self.assertLess(names.index("TLS_CERT_READY"),
                        names.index("NEXT_UNIT_LIVE"))
        self.assertLess(names.index("NEXT_UNIT_LIVE"),
                        names.index("PUBLIC_MCP_VERIFIED"))

    def test_a_provisioned_route_is_a_reachable_stage_with_no_backend(self):
        got = gate.evaluate(at("route_provisioned"))
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
        self.assertEqual(got["stage"], "NEXT_ROUTE_PROVISIONED")
        self.assertFalse(got["backend_running"])

    def test_a_ready_certificate_is_a_reachable_stage_with_no_backend(self):
        got = gate.evaluate(at("tls_cert_ready"))
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
        self.assertEqual(got["stage"], "TLS_CERT_READY")
        self.assertFalse(got["backend_running"])
        self.assertFalse(got["public_verified"])

    def test_neither_route_sheet_asks_for_an_http_status(self):
        """The negative that pins the review. `run_sheet` emits the sheet for the
        stage AFTER the plan's, so the route sheet comes from a plan standing at
        NEW_NODE_REGISTERED and the cert sheet from one at NEXT_ROUTE_PROVISIONED.

        Neither may ask for `/health`: that fetch belongs one rung higher, after
        the unit is live. A status code may appear only as a prohibition.
        """
        for standing, sheet_stage in (("owner_login_attested", "NEXT_ROUTE_PROVISIONED"),
                                      ("route_provisioned", "TLS_CERT_READY")):
            with self.subTest(sheet=sheet_stage):
                got = gate.run_sheet(at(standing))
                self.assertEqual(got["stage"], sheet_stage)
                joined = " ".join(got["owner_commands"])
                self.assertNotIn("/health", joined)
                if sheet_stage == "NEXT_ROUTE_PROVISIONED":
                    self.assertIn("MUST NOT be started", joined)

    def test_neither_route_stage_demands_health_in_the_decision(self):
        for stage in ("route_provisioned", "tls_cert_ready"):
            with self.subTest(stage=stage):
                got = gate.evaluate(at(stage))
                self.assertFalse(any("health" in b.lower() for b in got["blockers"]))

    def test_the_route_sheet_says_the_backend_must_not_be_started_for_it(self):
        got = gate.run_sheet(at("owner_login_attested"))
        self.assertEqual(got["stage"], "NEXT_ROUTE_PROVISIONED")
        joined = " ".join(got["owner_commands"])
        self.assertIn("MUST NOT be started", joined)
        self.assertIn("no HTTP status", joined)

    def test_the_cert_sheet_asks_for_a_handshake_not_a_response(self):
        got = gate.run_sheet(at("route_provisioned"))
        self.assertEqual(got["stage"], "TLS_CERT_READY")
        joined = " ".join(got["owner_commands"]).lower()
        self.assertIn("handshake", joined)
        self.assertIn("no 200", joined)
        self.assertNotIn("/health", joined)

    def test_the_unit_stage_is_where_caps_are_read_back(self):
        got = gate.run_sheet(at("tls_cert_ready"))
        self.assertEqual(got["stage"], "NEXT_UNIT_LIVE")
        joined = " ".join(got["owner_commands"])
        self.assertIn("systemctl --user show", joined)
        self.assertTrue(got["next_stage_requires"], "the unit stage advances on unit_live")


class PublicVerificationComesFromEvidenceTest(unittest.TestCase):
    """A self-reported boolean is not public liveness."""

    def test_there_is_no_flag_a_plan_can_set_to_claim_it(self):
        item = plan()
        item["state"]["public_verified"] = True
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("unsupported field" in b for b in got["blockers"]))
        self.assertFalse(got["public_verified"])

    def test_no_readback_means_no_public_stage(self):
        """The overclaim the review named: a plan that says the route is up and
        the unit is up, and therefore claims the public surface works."""
        item = at("unit_live")
        item["state"]["chatgpt_installed"] = True
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertFalse(got["public_verified"])
        self.assertNotEqual(got["stage"], "CHATGPT_INSTALLED")

    def test_a_recorded_readback_is_what_reaches_the_stage(self):
        got = gate.evaluate(at("public_verified"))
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
        self.assertEqual(got["stage"], "PUBLIC_MCP_VERIFIED")
        self.assertTrue(got["public_verified"])

    def test_a_502_on_health_does_not_reach_the_stage(self):
        item = at("unit_live")
        item["public_readback"] = readback(health_status=502)
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("health_status" in b for b in got["blockers"]))
        self.assertNotIn(got["stage"], ("PUBLIC_MCP_VERIFIED", "CHATGPT_INSTALLED"))

    def test_a_missing_discovery_document_does_not_reach_the_stage(self):
        for field in ("oauth_protected_resource_status",
                      "oauth_authorization_server_status"):
            with self.subTest(field=field):
                item = at("unit_live")
                item["public_readback"] = readback(**{field: 404})
                got = gate.evaluate(item)
                self.assertEqual(got["decision"], "BLOCKED")
                self.assertTrue(any(field in b for b in got["blockers"]))

    def test_an_empty_tool_face_is_not_a_tool_face(self):
        item = at("unit_live")
        item["public_readback"] = readback(mcp_tools_seen=0)
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("mcp_tools_seen" in b for b in got["blockers"]))

    def test_an_issuer_mismatch_is_refused(self):
        item = at("unit_live")
        item["public_readback"] = readback(issuer="https://somewhere-else.ts.net")
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("public_readback.issuer" in b for b in got["blockers"]))

    def test_an_incomplete_readback_is_refused(self):
        item = at("unit_live")
        item["public_readback"] = {"health_status": 200}
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("incomplete" in b for b in got["blockers"]))

    def test_a_missing_audience_is_refused(self):
        item = at("unit_live")
        item["public_readback"] = readback(audience="")
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("audience" in b for b in got["blockers"]))

    def test_the_unit_stage_does_not_require_any_readback(self):
        """The complement: evidence is demanded only where it can exist."""
        got = gate.evaluate(at("unit_live"))
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
        self.assertEqual(got["stage"], "NEXT_UNIT_LIVE")


class TheOldNodeIsNeverReusedTest(unittest.TestCase):
    """Reusing the existing node is the failure that looks like success: the
    ingress answers and V1 has quietly changed underneath it."""

    def _blocked(self, mutate) -> list:
        item = plan()
        mutate(item)
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        return got["blockers"]

    def test_the_existing_node_name_may_not_be_taken(self):
        blockers = self._blocked(
            lambda p: p["next"].update(node_name=p["existing"]["node_name"]))
        self.assertTrue(any("next.node_name" in b for b in blockers))

    def test_an_unnamed_existing_node_is_not_a_pass(self):
        """The gate cannot prove isolation against a node it was not told about,
        so it refuses to guess one."""
        blockers = self._blocked(lambda p: p["existing"].update(node_name=""))
        self.assertTrue(any("existing.node_name" in b for b in blockers))

    def test_the_system_tailscaled_socket_is_refused_by_name(self):
        blockers = self._blocked(lambda p: p["next"].update(
            socket=gate.DEFAULT_TAILSCALED_SOCKET))
        self.assertTrue(any("system tailscaled socket" in b for b in blockers))

    def test_the_next_state_dir_may_not_nest_inside_the_old_one(self):
        blockers = self._blocked(lambda p: p["next"].update(
            state_dir="/var/lib/tailscale"))
        self.assertTrue(any("next.state_dir" in b for b in blockers))

    def test_the_next_socket_may_not_live_in_the_old_state_dir(self):
        blockers = self._blocked(lambda p: p["next"].update(
            socket="/var/lib/tailscale/next.sock"))
        self.assertTrue(any("next.socket" in b for b in blockers))

    def test_the_existing_unit_name_may_not_be_taken(self):
        blockers = self._blocked(
            lambda p: p["next"].update(service_unit=p["existing"]["service_unit"]))
        self.assertTrue(any("next.service_unit" in b for b in blockers))

    def test_the_existing_published_port_may_not_be_reused(self):
        blockers = self._blocked(lambda p: p["next"].update(
            local_listen={"host": "127.0.0.1", "port": 8401},
            funnel_target_port=8401))
        self.assertTrue(any("already publishes this port" in b for b in blockers))

    def test_the_next_listener_must_be_loopback_only(self):
        blockers = self._blocked(lambda p: p["next"].update(
            local_listen={"host": "0.0.0.0", "port": 3411}))
        self.assertTrue(any("loopback only" in b for b in blockers))

    def test_a_funnel_aimed_anywhere_but_this_ingress_is_refused(self):
        """A Funnel pointing at another port publishes V1, or publishes nothing."""
        blockers = self._blocked(lambda p: p["next"].update(funnel_target_port=8401))
        self.assertTrue(any("funnel_target_port" in b for b in blockers))


class TheOriginAndItsPathsAgreeTest(unittest.TestCase):
    """One issuer, two discovery documents, the MCP route and the Feishu
    callback. A console-registered callback is a URL an operator cannot silently
    fix, so it is pinned exactly."""

    def _blocked(self, mutate) -> list:
        item = plan()
        mutate(item)
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        return got["blockers"]

    def test_a_host_that_is_not_a_tailnet_name_is_refused(self):
        blockers = self._blocked(lambda p: p["next"].update(tailnet_host="example.com"))
        self.assertTrue(any("ts.net" in b for b in blockers))

    def test_an_issuer_that_is_not_the_host_is_refused(self):
        blockers = self._blocked(
            lambda p: p["next"].update(issuer="https://somewhere-else.ts.net"))
        self.assertTrue(any("next.issuer" in b for b in blockers))

    def test_a_plaintext_issuer_is_refused(self):
        blockers = self._blocked(lambda p: p["next"].update(issuer=f"http://{HOST}"))
        self.assertTrue(any("next.issuer" in b for b in blockers))

    def test_the_feishu_callback_path_is_pinned_exactly(self):
        blockers = self._blocked(
            lambda p: p["routes"].update(feishu_callback="/callback"))
        self.assertTrue(any("routes.feishu_callback" in b for b in blockers))

    def test_both_discovery_documents_are_required(self):
        for name in ("oauth_protected_resource", "oauth_authorization_server"):
            with self.subTest(route=name):
                blockers = self._blocked(lambda p, n=name: p["routes"].pop(n))
                self.assertTrue(any(f"routes.{name}" in b for b in blockers))

    def test_the_mcp_path_is_pinned(self):
        blockers = self._blocked(lambda p: p["routes"].update(mcp="/api/mcp"))
        self.assertTrue(any("routes.mcp" in b for b in blockers))


class NoCredentialCanEnterTest(unittest.TestCase):
    """Registration is the owner's interactive browser login. This tooling
    never accepts, stores, copies or logs a key, so the only correct behaviour
    is to refuse the field — and to refuse it without echoing what was in it."""

    def test_an_auth_key_field_is_refused_by_name(self):
        for field in ("authkey", "auth_key", "tailscale_authkey", "TS_AUTHKEY"):
            with self.subTest(field=field):
                item = plan()
                item["next"][field] = "tskey-SENTINEL-VALUE"
                got = gate.evaluate(item)
                self.assertEqual(got["decision"], "BLOCKED")
                joined = " ".join(got["blockers"])
                self.assertIn(field.lower(), joined.lower())
                self.assertNotIn("SENTINEL", joined,
                                 "the refusal echoed the value it refused")

    def test_a_token_or_secret_field_is_refused_and_never_echoed(self):
        for field, value in (("v1_token", "tok-SENTINEL"),
                             ("client_secret", "sec-SENTINEL"),
                             ("feishu_app_secret", "fei-SENTINEL")):
            with self.subTest(field=field):
                item = at("staged")
                item["state"][field] = value
                got = gate.evaluate(item)
                self.assertEqual(got["decision"], "BLOCKED")
                self.assertNotIn("SENTINEL", " ".join(got["blockers"]))

    def test_a_nested_credential_field_is_found_too(self):
        item = plan()
        item["next"]["deep"] = {"inner": {"access_token": "SENTINEL"}}
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        joined = " ".join(got["blockers"])
        self.assertIn("access_token", joined)
        self.assertNotIn("SENTINEL", joined)

    def test_the_declared_plan_schema_has_nowhere_to_put_a_key(self):
        """Structural, not behavioural: the fields are a closed set and none of
        them is credential-shaped, so a compliant plan cannot carry one."""
        for allowed in (gate.NEXT_FIELDS, gate.EXISTING_FIELDS, gate.STATE_FIELDS,
                        gate.ROUTE_FIELDS, gate.TOP_FIELDS,
                        gate.PUBLIC_READBACK_FIELDS):
            for name in allowed:
                with self.subTest(field=name):
                    self.assertFalse(
                        any(hint in name for hint in gate.SECRET_HINTS))

    def test_no_execution_path_is_required_to_register(self):
        """The gate reports that an owner interaction is needed, and that no
        auth key is — the two must not be conflated."""
        got = gate.evaluate(plan())
        self.assertFalse(got["authkey_required"])
        self.assertIn("browser", got["owner_interaction_required"])


class TheLadderIsMonotoneAndFailClosedTest(unittest.TestCase):

    def test_the_ladder_is_the_declared_order(self):
        self.assertEqual([name for name, _ in gate.STAGES],
                         ["SOURCE", "STAGED_NO_START", "NEW_NODE_REGISTERED",
                          "NEXT_ROUTE_PROVISIONED", "TLS_CERT_READY",
                          "NEXT_UNIT_LIVE", "PUBLIC_MCP_VERIFIED",
                          "FEISHU_AUTHENTICATED", "CHATGPT_INSTALLED"])

    def test_a_fresh_plan_stands_at_source(self):
        got = gate.evaluate(plan())
        self.assertEqual(got["stage"], "SOURCE")
        self.assertEqual(got["next_stage"], "STAGED_NO_START")

    def test_each_stage_is_reached_only_with_its_own_evidence(self):
        for flag, name in (("staged", "STAGED_NO_START"),
                           ("route_provisioned", "NEXT_ROUTE_PROVISIONED"),
                           ("tls_cert_ready", "TLS_CERT_READY"),
                           ("unit_live", "NEXT_UNIT_LIVE"),
                           ("public_verified", "PUBLIC_MCP_VERIFIED"),
                           ("feishu_authenticated", "FEISHU_AUTHENTICATED"),
                           ("chatgpt_installed", "CHATGPT_INSTALLED")):
            with self.subTest(stage=name):
                got = gate.evaluate(at(flag))
                self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])
                self.assertEqual(got["stage"], name)

    def test_registration_without_the_owner_login_is_blocked(self):
        item = plan()
        item["state"]["node_registered"] = True
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("owner_login_attested" in b for b in got["blockers"]))

    def test_registration_with_the_owner_login_is_reached(self):
        got = gate.evaluate(at("owner_login_attested"))
        self.assertEqual(got["decision"], "PLAN_READY")
        self.assertEqual(got["stage"], "NEW_NODE_REGISTERED")

    def test_a_gap_in_the_middle_blocks_everything_above_it(self):
        item = at("chatgpt_installed")
        item["state"]["unit_live"] = False
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertNotEqual(got["stage"], "CHATGPT_INSTALLED")

    def test_a_missing_certificate_holds_the_ladder_below_the_public_stage(self):
        """Public evidence does not rescue a gap in the staging record. The plan
        still says the certificate is not ready, so the ladder stops there and
        the stage is reported honestly rather than promoted by the readback."""
        item = at("public_verified")
        item["state"]["tls_cert_ready"] = False
        got = gate.evaluate(item)
        self.assertEqual(got["stage"], "NEXT_ROUTE_PROVISIONED")
        self.assertNotEqual(got["stage"], "PUBLIC_MCP_VERIFIED")
        self.assertTrue(got["public_verified"], "the evidence itself still stands")


class NextOnlyRollbackAndLimitsTest(unittest.TestCase):
    """This gate inherits the Next unit's bounded canary and the Next-only
    rollback; it does not invent a second installer or a second budget."""

    def test_the_next_unit_is_the_one_the_other_gate_names(self):
        """The unit's FILE NAME is the unit name. The template mentions V1 only
        to forbid installing under it — that mention must not become the name."""
        from fleet_mcp.deploy import next_dual_stack_preflight as preflight
        path = ROOT / "fleet_mcp" / "deploy" / "serveros-fleet-next-mcp.service"
        self.assertEqual(path.name, preflight.NEXT_UNIT)
        self.assertNotEqual(preflight.NEXT_UNIT, preflight.V1_UNIT)
        unit = path.read_text(encoding="utf-8")
        self.assertIn("NEVER install under", unit)
        self.assertIn(preflight.V1_UNIT, unit)
        self.assertIn("SyslogIdentifier=serveros-fleet-next-mcp", unit)

    def test_the_unit_template_still_carries_the_bounded_caps(self):
        unit = (ROOT / "fleet_mcp" / "deploy"
                / "serveros-fleet-next-mcp.service").read_text(encoding="utf-8")
        for required in ("MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
                         "TasksMax=96", "NoNewPrivileges=yes"):
            with self.subTest(required=required):
                self.assertIn(required, unit)

    def test_this_gate_does_not_restate_a_budget_of_its_own(self):
        """One budget, in one place. A second copy here is how the two drift."""
        source = (ROOT / "fleet_mcp" / "deploy"
                  / "next_tailscale_gate.py").read_text(encoding="utf-8")
        for invented in ("MemoryMax=", "CPUQuota=", "TasksMax=", "memory_max_mib"):
            with self.subTest(token=invented):
                self.assertNotIn(invented, source)


class TheGateIsDeterministicTest(unittest.TestCase):
    """Re-running the gate on the same observed state is a no-op decision, which
    is the whole of its idempotence: it holds no state to advance."""

    def test_two_evaluations_of_one_plan_agree(self):
        item = at("public_verified")
        first, second = gate.evaluate(item), gate.evaluate(copy.deepcopy(item))
        self.assertEqual(first, second)

    def test_evaluating_does_not_mutate_the_plan(self):
        item = at("tls_cert_ready")
        before = copy.deepcopy(item)
        gate.evaluate(item)
        gate.run_sheet(item)
        self.assertEqual(item, before)

    def test_an_advanced_plan_is_still_deterministic(self):
        item = at("chatgpt_installed")
        self.assertEqual(gate.evaluate(item), gate.evaluate(item))


class TheRunSheetIsASheetNotAnActionTest(unittest.TestCase):

    def test_the_sheet_names_the_next_stage_and_executes_nothing(self):
        got = gate.run_sheet(plan())
        self.assertEqual(got["stage"], "STAGED_NO_START")
        self.assertTrue(got["owner_commands"])
        self.assertEqual(got["writes"], [])
        self.assertEqual(got["executes"], [])

    def test_the_registration_stage_says_owner_browser_and_own_socket(self):
        got = gate.run_sheet(at("staged"))
        self.assertEqual(got["stage"], "NEW_NODE_REGISTERED")
        joined = " ".join(got["owner_commands"]).lower()
        self.assertIn("own", joined)
        self.assertIn("socket", joined)

    def test_a_blocked_plan_yields_no_sheet(self):
        item = plan()
        item["next"]["node_name"] = item["existing"]["node_name"]
        got = gate.run_sheet(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertNotIn("owner_commands", got)

    def test_no_sheet_step_names_a_system_or_privileged_operation(self):
        for flag in ("staged", "owner_login_attested", "route_provisioned",
                     "tls_cert_ready", "unit_live", "feishu_authenticated"):
            with self.subTest(stage=flag):
                got = gate.run_sheet(at(flag))
                text = " ".join(got["owner_commands"]).lower()
                for banned in ("sudo", "systemctl restart", "/etc/", "authkey",
                               "tskey", "--advertise-exit-node"):
                    self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
