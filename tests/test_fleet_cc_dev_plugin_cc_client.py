#!/usr/bin/env python3
"""The local CC client fixture: what it generates, and what it refuses.

The fixture exists because "the plugin works" had been reported from package
load evidence, which enumerates no tool. This module pins the two halves that
make a real client test meaningful and keeps them apart:

* the **fixture package** — a stdio binding, the five canonical Skills written
  byte for byte, a `TEST_FIXTURE_ONLY` marker, and no release-shaped claim;
* the **protocol surface** — the real `fleet_mcp` dispatcher and the real 34
  tools, driven over stdio by a fixture device, with a `fleet:read` principal.

The negative cases are the point of the second half: discovery advertises all
48 tools, and a write is still refused `SCOPE_DENIED` before the fake provider
is reached. No network, no credential, no model call, and the `claude` CLI is
not invoked here; the real-client run is `run_cc_client_e2e.py`, whose receipt
records `PLUGIN_LOAD_OK` / `MCP_INITIALIZE_OK` / `TOOLS_LIST_OK` / `AUTH_SCOPE`
/ `CLIENT_E2E` separately.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The tests import the real `fleet_mcp` service; that import must not write
# bytecode into the source tree, whose hash is part of an installed artifact's
# identity. This covers the unittest process before its first project import.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_local_fixture as fixture  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    MANAGED_IDLE,
    ProviderHealth,
    SessionSummary,
)
from fleet_mcp.registry import DeviceRegistry  # noqa: E402

DEVICE = "device:fixture-node"
SESSION_REF = "device:fixture-node|claude|fixture-session-0001"


class GeneratedFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-fixture-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"
        self.record = fixture.generate(out=self.out)

    def test_the_manifest_is_a_test_fixture_and_not_the_adapter(self) -> None:
        manifest = json.loads(
            (self.out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], fixture.FIXTURE_NAME)
        self.assertNotEqual(manifest["name"], "serveros-fleet-dev")
        self.assertIn("TEST FIXTURE ONLY", manifest["description"])
        self.assertIn("author", manifest)

    def test_the_binding_is_explicitly_stdio_and_local(self) -> None:
        binding = json.loads((self.out / ".mcp.json").read_text(encoding="utf-8"))
        server = binding["mcpServers"][fixture.SERVER_NAME]
        self.assertEqual(server["type"], "stdio")
        self.assertIn("command", server)
        self.assertIn(str(fixture.DEFAULT_SERVER), server["args"])
        self.assertEqual(server["env"], {"PYTHONDONTWRITEBYTECODE": "1"})
        # The production adapter's test asserts its binding is never a command
        # or an env block. This is the other artefact, and it says so.
        self.assertNotIn("url", server)
        self.assertNotIn("oauth", server)

    def test_the_skills_are_the_canonical_bytes(self) -> None:
        for source in sorted(SHARED_SKILLS.iterdir()):
            written = (self.out / "skills" / source.name / "SKILL.md").read_bytes()
            self.assertEqual(written, (source / "SKILL.md").read_bytes(),
                             f"{source.name} was re-typed rather than read")
        self.assertEqual(sorted(self.record["skills"]),
                         sorted(p.name for p in SHARED_SKILLS.iterdir()))

    def test_the_marker_says_do_not_install(self) -> None:
        body = (self.out / fixture.FIXTURE_MARKER).read_text(encoding="utf-8")
        self.assertIn("TEST FIXTURE ONLY", body)
        self.assertIn("Do not install this", body)
        self.assertIn("not the release", body)

    def test_nothing_installable_or_branded_is_written(self) -> None:
        images = [p for p in self.out.rglob("*") if p.suffix.lower() in
                  (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")]
        archives = [p for p in self.out.rglob("*") if p.suffix.lower() in
                    (".zip", ".mcpb")]
        self.assertEqual(images, [])
        self.assertEqual(archives, [])
        self.assertFalse(self.record["installable"])
        self.assertEqual(self.record["installs"], [])
        self.assertEqual(self.record["uploads"], [])

    def test_a_second_generation_and_a_bad_principal_are_refused(self) -> None:
        with self.assertRaises(fixture.cc.Refused):
            fixture.generate(out=self.out)
        with self.assertRaises(fixture.cc.Refused):
            fixture.generate(out=self.tmp / "other", principal="root")

    def test_an_output_inside_the_skills_source_is_refused(self) -> None:
        inside = SHARED_SKILLS / "fixture-generated"
        with self.assertRaises(fixture.cc.Refused):
            fixture.generate(out=inside)
        self.assertFalse(inside.exists())


class TheProtocolSurface(unittest.TestCase):
    """The real dispatcher and the real tools, over the fixture's stdio."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(prefix="cc-fixture-proto-")
        base = Path(cls.tmp.name)
        cls.log = base / "wire.jsonl"
        cls.state = base / "state"
        cls.proc = subprocess.Popen(
            [sys.executable, str(fixture.DEFAULT_SERVER),
             "--log", str(cls.log), "--state-dir", str(cls.state),
             "--principal", "read-only"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        assert cls.proc.stdin and cls.proc.stdout
        cls.rpc("initialize", {"protocolVersion": "2025-06-18",
                               "capabilities": {},
                               "clientInfo": {"name": "unittest", "version": "0"}})
        cls.rpc("notifications/initialized", expect_response=False)

    @classmethod
    def rpc(cls, method: str, params: dict | None = None, *,
            expect_response: bool = True) -> dict:
        assert cls.proc.stdin and cls.proc.stdout
        cls.proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": method, "method": method,
            "params": params or {},
        }) + "\n")
        cls.proc.stdin.flush()
        if not expect_response:
            return {}
        line = cls.proc.stdout.readline()
        if not line:
            raise AssertionError(
                f"fixture closed stdout after {method}; stderr="
                f"{cls.proc.stderr.read() if cls.proc.stderr else ''}")
        return json.loads(line)

    @classmethod
    def call(cls, name: str, arguments: dict | None = None) -> dict:
        response = cls.rpc("tools/call", {"name": name,
                                          "arguments": arguments or {}})
        return response["result"]

    @classmethod
    def tearDownClass(cls) -> None:
        assert cls.proc.stdin and cls.proc.stderr
        cls.proc.stdin.close()
        try:
            cls.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        stderr = cls.proc.stderr.read()
        rows = [json.loads(line) for line in cls.log.read_text().splitlines()]
        shutdown = next((row for row in rows if row.get("event") == "shutdown"), {})
        cls.tmp.cleanup()
        if stderr.strip():
            raise AssertionError(f"fixture wrote to stderr: {stderr[:500]}")
        # The refused send must not have reached the fake device. This is read
        # after the process exits, because the shutdown record is written then.
        if shutdown.get("provider_sends") != 0:
            raise AssertionError(
                f"the fake provider saw {shutdown.get('provider_sends')} send(s); "
                f"a SCOPE_DENIED must be refused before the device is reached")

    def test_initialize_answers_the_real_server(self) -> None:
        response = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(response["result"]["serverInfo"]["name"],
                         "serveros-fleet-mcp")

    def test_discovery_is_all_44_tools_and_is_not_entitlement(self) -> None:
        tools = self.rpc("tools/list")["result"]["tools"]
        names = [tool["name"] for tool in tools]
        self.assertEqual(len(names), 48)
        self.assertEqual(len(set(names)), 48)
        self.assertIn("fleet_list_devices", names)
        self.assertIn("fleet_send_to_session", names)
        self.assertIn("fleet_cloudcli_native_capability", names)
        writes = [tool["name"] for tool in tools
                  if not tool["annotations"]["readOnlyHint"]]
        self.assertIn("fleet_send_to_session", writes)
        self.assertLess(len(writes), len(tools),
                        "listing everything cannot mean doing everything")

    def test_status_reports_the_read_only_scope(self) -> None:
        result = self.call("fleet_status")
        self.assertFalse(result["isError"])
        body = result["structuredContent"]
        self.assertEqual(body["principal"]["scopes"], ["fleet:read"])
        self.assertEqual(body["identity_layer"]["enrolled_devices"], [DEVICE])

    def test_list_devices_reads_the_fixture_and_says_it_is_a_fixture(self) -> None:
        result = self.call("fleet_list_devices")
        self.assertFalse(result["isError"])
        body = result["structuredContent"]
        self.assertEqual(body["total"], 1)
        row = body["devices"][0]
        self.assertEqual(row["device_ref"], DEVICE)
        self.assertEqual(row["availability"]["local_agent_controller"], "AVAILABLE")
        self.assertIn("TEST_FIXTURE", row["provider"]["detail"])

    def test_list_sessions_reads_one_session_by_its_full_ref(self) -> None:
        result = self.call("fleet_list_sessions")
        self.assertFalse(result["isError"])
        body = result["structuredContent"]
        self.assertEqual(body["total"], 1)
        row = body["sessions"][0]
        self.assertEqual(row["session_ref"], SESSION_REF)
        self.assertEqual(row["classification"], MANAGED_IDLE)
        self.assertFalse(row["control_allowed"]["send_allowed"])

    def test_capability_is_observed_and_two_verbs_are_usable(self) -> None:
        result = self.call("fleet_cloudcli_native_capability",
                           {"device_ref": DEVICE})
        self.assertFalse(result["isError"])
        body = result["structuredContent"]
        self.assertEqual(body["state"], "OBSERVED")
        self.assertEqual(body["observed_count"], 2)
        self.assertEqual(body["tools"]["sessions_list"]["state"], "USABLE")
        self.assertEqual(body["tools"]["session_get"]["state"], "USABLE")
        self.assertEqual(body["tools"]["session_send"]["state"],
                         "UNSUPPORTED_BY_DEVICE")

    def test_a_read_only_principal_is_refused_a_write_before_the_device(self) -> None:
        result = self.call("fleet_send_to_session",
                           {"session_ref": SESSION_REF, "message": "must not send"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")
        # The fake provider records every send it sees. The count is read at
        # shutdown by tearDownClass, because a refusal at the scope must leave
        # it empty; reaching the provider would mean the credential was checked
        # after the device, which is the wrong order.

    def test_a_bare_session_id_is_not_an_address(self) -> None:
        result = self.call("fleet_get_session",
                           {"session_ref": "fixture-session-0001"})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_SESSION_REF")

    def test_an_unknown_tool_is_a_typed_refusal(self) -> None:
        result = self.call("fleet_no_such_tool")
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")

    def test_an_unknown_filter_is_refused_rather_than_ignored(self) -> None:
        """`device_ref` instead of `device` must not widen the question.

        The schema is `additionalProperties: false`; ignoring an unknown filter
        answers "all devices" while the caller believes it asked about one.
        """
        result = self.call("fleet_list_sessions", {"device_ref": DEVICE})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")


class KnownFindingReadOnlyControlState(unittest.TestCase):
    """A product finding, executable, outside this lane's write area.

    `FleetService.list_sessions` attaches `control_allowed` computed by
    `_session_control_state` (`fleet_mcp/fleet.py:918`), whose docstring calls it
    "the same check `_may_send` makes". `send_to_session`
    (`fleet_mcp/fleet.py:1045`) calls `principal.require(SCOPE_SEND)` at
    `fleet_mcp/fleet.py:1053` before `_may_send`; `_session_control_state` does
    not. A principal holding only `fleet:read` is therefore told
    `send_allowed: true` for a session that `fleet_send_to_session` will refuse
    `SCOPE_DENIED`.

    RC-v2: #305 fixed this finding (`send_allowed` now includes the caller's
    `fleet:send`). The former `expectedFailure` is promoted to a real assertion
    here; it remains the regression guard and is not weakened.
    """

    def test_a_read_only_principal_must_not_be_told_send_is_allowed(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="cc-fixture-finding-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        anchor = tmp / "pat"
        anchor.write_text("TEST_FIXTURE\n", encoding="utf-8")
        key = SessionKey.compose(DEVICE, "claude", "fixture-session-0001")

        class Provider:
            kind = "fixture"
            device_ref = DEVICE

            def health(self):
                return ProviderHealth(device_ref=DEVICE, kind="fixture",
                                      reachable=True, authorized=True, detail="x")

            def list_sessions(self, *, limit, project=None, state=None):
                return [SessionSummary(
                    key=key, title="fixture", classification=MANAGED_IDLE,
                    running=False, resident_host=False, host_state=None,
                    lifecycle_mode=None, project_id=None, last_activity=None)]

            def get_session(self, key):
                return {"session_ref": key.ref}

            def read_session(self, key, **kwargs):
                return {}

            def send(self, key, message, **kwargs):
                raise AssertionError("a refused send must not reach the provider")

        config = FleetConfig(
            source=None, local_device_ref=DEVICE,
            providers={DEVICE: ProviderBinding(
                device_ref=DEVICE, kind="cloudcli-mcp-http",
                endpoint="http://127.0.0.1:0/mcp", credential_anchor=anchor,
                allow_send=True)},
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(),
            policy=PolicyBinding(send_allow=(key.ref,)),
            audit=AuditBinding(path=tmp / "audit.jsonl",
                               rate_limit_per_minute=10_000),
        )
        registry = DeviceRegistry({"devices": [{
            "device_ref": DEVICE, "display_name": "fixture", "platform": "linux",
            "roles": [], "environment_profile": "linux-server", "agents": [],
            "ownership": "company",
        }]})
        service = FleetService(config, registry=registry,
                               providers={DEVICE: Provider()})
        read_only = Principal(name="read-only",
                              scopes=frozenset({"fleet:read"}),
                              credential_kind="fixture")
        listing = service.list_sessions(read_only)
        # This is the assertion the product currently fails.
        self.assertFalse(listing["sessions"][0]["control_allowed"]["send_allowed"],
                         "a read-only principal was told it may send")


if __name__ == "__main__":
    unittest.main()
