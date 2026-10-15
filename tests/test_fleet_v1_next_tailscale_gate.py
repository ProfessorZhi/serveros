"""Lane E — offline gate tests for the Next ingress's own Tailscale node.

No real systemd, tailscaled, Funnel, device, OAuth, Feishu console, ChatGPT
plugin or SSH connection is accessed. The gate is pure: every case here is a
plan dict in and a decision out, and every case asserts that nothing was
written and nothing was executed.

The four groups mirror the four ways this can go wrong: reusing the old node,
publishing an origin whose paths disagree with themselves, registering without
the owner's own login, and letting a lower stage claim a higher one.
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
            "tailnet_host": "fleet-next-abc.ts.net",
            "funnel_target_port": 3411,
            "local_listen": {"host": "127.0.0.1", "port": 3411},
            "issuer": "https://fleet-next-abc.ts.net",
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
            "https_live": False, "mcp_live": False, "feishu_authenticated": False,
            "chatgpt_installed": False,
        },
    }


def at(stage_flag: str) -> dict:
    """A plan whose state flags are set for every stage up to and including one."""
    order = ["staged", "node_registered", "owner_login_attested", "https_live",
             "mcp_live", "feishu_authenticated", "chatgpt_installed"]
    item = plan()
    if stage_flag:
        for flag in order[:order.index(stage_flag) + 1]:
            item["state"][flag] = True
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
        # The example uses placeholder host/account names; those are the only
        # things it is allowed to stand in for.
        loaded["next"]["tailnet_host"] = "fleet-next-abc.ts.net"
        loaded["next"]["issuer"] = "https://fleet-next-abc.ts.net"
        got = gate.evaluate(loaded)
        self.assertEqual(got["decision"], "PLAN_READY", got["blockers"])


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
        blockers = self._blocked(
            lambda p: p["next"].update(issuer="http://fleet-next-abc.ts.net"))
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
                        gate.ROUTE_FIELDS, gate.TOP_FIELDS):
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
                          "HTTPS_LIVE", "NEXT_MCP_LIVE", "FEISHU_AUTHENTICATED",
                          "CHATGPT_INSTALLED"])

    def test_a_fresh_plan_stands_at_source(self):
        got = gate.evaluate(plan())
        self.assertEqual(got["stage"], "SOURCE")
        self.assertEqual(got["next_stage"], "STAGED_NO_START")

    def test_each_stage_is_reached_only_with_its_own_evidence(self):
        for flag, name in (("staged", "STAGED_NO_START"),
                           ("https_live", "HTTPS_LIVE"),
                           ("mcp_live", "NEXT_MCP_LIVE"),
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

    def test_an_open_port_may_not_be_reported_as_a_usable_route(self):
        """The exact overclaim the ladder exists for: the listener is up, so the
        plan says ChatGPT is installed."""
        item = at("https_live")
        item["state"]["chatgpt_installed"] = True
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertTrue(any("open port is not a usable MCP route" in b
                            for b in got["blockers"]))

    def test_a_gap_in_the_middle_blocks_everything_above_it(self):
        item = at("chatgpt_installed")
        item["state"]["mcp_live"] = False
        got = gate.evaluate(item)
        self.assertEqual(got["decision"], "BLOCKED")
        self.assertNotEqual(got["stage"], "CHATGPT_INSTALLED")


class NextOnlyRollbackAndLimitsTest(unittest.TestCase):
    """This gate inherits the Next unit's bounded canary and the Next-only
    rollback; it does not invent a second installer or a second budget."""

    def test_the_next_unit_is_the_one_the_other_gate_names(self):
        """The unit's FILE NAME is the unit name. The template mentions V1 only
        to forbid installing under it — that mention must not become the name."""
        from fleet_mcp.deploy import next_dual_stack_preflight as preflight
        path = ROOT / "fleet_mcp" / "deploy" / "serveros-fleet-example-mcp.service"
        self.assertEqual(path.name, preflight.NEXT_UNIT)
        self.assertNotEqual(preflight.NEXT_UNIT, preflight.V1_UNIT)
        unit = path.read_text(encoding="utf-8")
        self.assertIn("NEVER install under", unit)
        self.assertIn(preflight.V1_UNIT, unit)
        # The name it actually installs under is the Next one, not V1's.
        self.assertIn("SyslogIdentifier=serveros-fleet-example-mcp", unit)

    def test_the_unit_template_still_carries_the_bounded_caps(self):
        unit = (ROOT / "fleet_mcp" / "deploy"
                / "serveros-fleet-example-mcp.service").read_text(encoding="utf-8")
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
        item = at("https_live")
        first, second = gate.evaluate(item), gate.evaluate(copy.deepcopy(item))
        self.assertEqual(first, second)

    def test_evaluating_does_not_mutate_the_plan(self):
        item = at("staged")
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
        for flag in ("staged", "owner_login_attested", "https_live", "mcp_live",
                     "feishu_authenticated"):
            with self.subTest(stage=flag):
                got = gate.run_sheet(at(flag))
                text = " ".join(got["owner_commands"]).lower()
                for banned in ("sudo", "systemctl restart", "/etc/", "authkey",
                               "tskey", "--advertise-exit-node"):
                    self.assertNotIn(banned, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
