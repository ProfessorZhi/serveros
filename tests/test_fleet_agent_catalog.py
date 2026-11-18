"""D-2: list a session's Skills / slash commands, invoke one, read the reply.

The catalog comes from the session's own device; invoking names an item and
is delivered as an ordinary send, so no unlisted text reaches a session and
every send gate still applies.
"""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from fleet_mcp import cloudcli_models as cm
from fleet_mcp import tools as fleet_tools
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.providers.base import CATALOG_SCHEMA, invocation_text
from fleet_mcp.tools import ToolContext

from test_fleet_mcp_surface import TOKYO, FakeProvider, Harness, _summary

SKILLS = {"success": True, "data": {"provider": "claude", "skills": [
    {"name": "fleet-core", "command": "/fleet-core", "description": "read this first",
     "scope": "plugin", "sourcePath": "/home/u/.claude/plugins/x/SKILL.md", "pluginName": "serveros-fleet"},
    {"name": "evil", "command": "rm -rf /", "description": "not an invocation"},
]}}
COMMANDS = {"builtIn": [{"name": "/config", "description": "settings", "namespace": "builtin"}],
            "custom": [{"name": "/review", "path": "/srv/p/.claude/commands/review.md",
                        "relativePath": "review.md", "description": "Review", "namespace": "project"}],
            "count": 2}


class CatalogProvider(FakeProvider):
    def list_skills(self, key):
        return {**cm.project_skills("claude", SKILLS), "session_ref": key.ref}

    def list_commands(self, key):
        return {**cm.project_commands(COMMANDS), "session_ref": key.ref}


def _harness(tmp: Path) -> tuple[Harness, SessionKey]:
    harness = Harness(tmp, allow_tokyo_send=True, send_allow=(f"{TOKYO}|claude|abc",))
    provider = CatalogProvider(TOKYO, sessions=[_summary(TOKYO, "abc")])
    harness.tokyo = provider
    harness.service.providers[TOKYO] = provider
    return harness, SessionKey.compose(TOKYO, "claude", "abc")


class TheCatalogIsProjected(unittest.TestCase):
    def test_skills_keep_names_and_invocations_and_drop_paths(self):
        out = cm.project_skills("claude", SKILLS)
        self.assertEqual(out["schema"], CATALOG_SCHEMA)
        self.assertEqual([i["invocation"] for i in out["items"]], ["/fleet-core"])
        self.assertNotIn("/home/u", json.dumps(out))

    def test_builtin_commands_are_listed_but_not_invocable(self):
        out = cm.project_commands(COMMANDS)
        by = {i["name"]: i for i in out["items"]}
        self.assertTrue(by["/review"]["invocable"])
        self.assertFalse(by["/config"]["invocable"])
        self.assertNotIn("/srv/p", json.dumps(out))

    def test_the_invocation_comes_from_the_catalog_never_from_the_caller(self):
        catalog = cm.project_commands(COMMANDS)
        self.assertEqual(invocation_text(catalog, "/review", "  the  diff "), "/review the diff")
        for name in ("/config", "/not-listed", "rm -rf /"):
            with self.subTest(name=name), self.assertRaises(FleetRefusal):
                invocation_text(catalog, name)


class ListThenInvoke(unittest.TestCase):
    def test_list_invoke_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness, key = _harness(Path(tmp))
            ctx = ToolContext(service=harness.service, principal=harness.principal())
            listed = fleet_tools.call_tool(ctx, "fleet_agent_catalog",
                                           {"session_ref": key.ref, "kind": "skills"})
            self.assertFalse(listed["isError"], listed)
            listed = listed["structuredContent"]
            self.assertEqual([i["name"] for i in listed["items"]], ["fleet-core"])
            self.assertTrue(listed["untrusted_content"])
            done = fleet_tools.call_tool(ctx, "fleet_agent_invoke",
                                         {"session_ref": key.ref, "name": "fleet-core",
                                          "args": "status of tokyo"})
            self.assertFalse(done["isError"], done)
            done = done["structuredContent"]
            self.assertEqual(done["invoked"]["text"], "/fleet-core status of tokyo")
            self.assertEqual(harness.tokyo.sent[-1][1], "/fleet-core status of tokyo")
            run = fleet_tools.call_tool(ctx, "fleet_get_run",
                                        {"session_ref": key.ref, "run_id": done["run_id"]})
            self.assertFalse(run["isError"], run)

    def test_invoking_passes_the_send_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness, key = _harness(Path(tmp))
            reader = harness.principal("fleet:read")
            with self.assertRaises(FleetRefusal):
                harness.service.invoke(reader, key, kind="skills", name="fleet-core")
            other = SessionKey.compose(TOKYO, "claude", "not-allowed")
            harness.tokyo._sessions.append(_summary(TOKYO, "not-allowed"))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.invoke(harness.principal(), other, kind="commands", name="/review")
            self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
            self.assertEqual(harness.tokyo.sent, [])

    def test_a_provider_without_a_catalog_is_unsupported_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Harness(Path(tmp))
            key = SessionKey.compose(TOKYO, "claude", "abc")
            harness.tokyo._sessions.append(_summary(TOKYO, "abc"))
            with self.assertRaises(FleetRefusal) as caught:
                harness.service.agent_catalog(harness.principal(), key, kind="skills")
            self.assertEqual(caught.exception.code, "CAPABILITY_UNSUPPORTED")


class TheDeviceReadsItsOwnRestRoutes(unittest.TestCase):
    """`LocalLibrary` asks CloudCLI on loopback, with the owner's token file."""

    def _library(self, tmp: Path, answers: dict[str, object], seen: list):
        token = tmp / "token"
        token.write_text("t", encoding="utf-8")
        token.chmod(0o600)

        class Resp(io.BytesIO):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout):
            seen.append((req.get_method(), req.full_url, req.data))
            path = req.full_url.split("3001", 1)[1].split("?", 1)[0]
            return Resp(json.dumps(answers[path]).encode())

        return cm.LocalLibrary("http://127.0.0.1:3001/mcp", token, opener=opener)

    def test_skills_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen: list = []
            lib = self._library(Path(tmp), {"/api/providers/codex/skills": SKILLS,
                                            "/api/commands/list": COMMANDS}, seen)
            skills = lib.skills("codex", "/srv/p")
            commands = lib.commands("/srv/p")
        self.assertEqual(skills["provider"], "codex")
        self.assertIn("workspacePath=%2Fsrv%2Fp", seen[0][1])
        self.assertEqual(json.loads(seen[1][2]), {"projectPath": "/srv/p"})
        self.assertEqual(len(commands["items"]), 2)


class TheConnectorServesTheCatalog(unittest.TestCase):
    def test_catalog_route_takes_only_kind(self):
        from fleet_mcp.node.connector import Connector

        connector = Connector.__new__(Connector)
        connector.config = type("C", (), {"device_ref": TOKYO, "runtime": "claude"})()
        connector.provider = CatalogProvider(TOKYO)
        status, body = connector._dispatch("POST", "/v1/sessions/abc/catalog",
                                           b'{"kind": "commands"}', TOKYO)
        self.assertEqual(status, 200)
        self.assertEqual(body["kind"], "commands")
        with self.assertRaises(FleetRefusal):
            connector._dispatch("POST", "/v1/sessions/abc/catalog",
                                b'{"kind": "skills", "path": "/etc"}', TOKYO)

    def test_the_route_is_on_the_closed_list(self):
        from fleet_mcp.node_protocol import ROUTES
        self.assertIn("POST /v1/sessions/{id}/catalog", ROUTES)


if __name__ == "__main__":
    unittest.main()
