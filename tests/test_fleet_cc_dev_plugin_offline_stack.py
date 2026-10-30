#!/usr/bin/env python3
"""Four reachability claims, kept four -- driven offline, end to end.

A Fleet "it works" can mean any of four different things, and the F line's whole
difficulty is that an earlier revision reported one of them as another. They are:

1. **package load.** Claude Code enumerates the Skills in a plugin directory and
   registers an MCP server *name*. This is what `LOCAL_CC_PLUGIN_LOADED` rests
   on, and it is a statement about a directory on this host. It enumerates no
   tool, and a registered server name is not a tool list.
2. **an authenticated `tools/list`.** The ingress answers 34 distinct tool names
   to a credential it accepted. Unauthenticated it answers 401 -- and a 401 is a
   refusal, not an empty list. `NEXT_TOOLS_DISCOVERED` is this and only this.
3. **an ability resolution.** `abilities/resolver.py` intersects eight inputs for
   one device, one runtime and one moment. `AVAILABLE` is not a grant, and
   `UNKNOWN` is a real answer rather than a pass.
4. **a session lifecycle.** A create, a send, and a run read back. An accepted
   message is not a settled run, and a created session is not a started one.

None of the four substitutes for another, so each class below asserts the
substitution that must fail. The MCP surface is the real
`fleet_mcp.http_transport.HttpSurface` on loopback; the device is a fake provider
shaped like the one the surface's own tests use; the plugin is the real generated
directory. Offline: no network beyond loopback, no secret in the repository, no
model call, and the `claude` CLI is never invoked -- the package-load class pins
the *directory*, which is what a load is a load of.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import scopes as fleet_scopes  # noqa: E402
from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.http_transport import HttpSurface, build_oauth, serve  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402

SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
CC_DEV = ROOT / "integrations" / "fleet-cc-dev-plugin"
DEV_URL = "https://fleet-next.example.ts.net/mcp"
TOKYO = "device:tokyo-shared-dev-01"
SESSION_REF = f"{TOKYO}|claude|abc"
OWNER_TOKEN = "owner-pat-value"

_spec = importlib.util.spec_from_file_location("fleet_cc_dev_plugin_stack", CC_DEV / "build_cc_plugin.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_cc_dev_plugin_stack"] = cc
_spec.loader.exec_module(cc)


# --------------------------------------------------------------------------
# The Skills name tools that exist
# --------------------------------------------------------------------------

class TheSkillsNameRealTools(unittest.TestCase):
    """Every `fleet_*` backtick in a Skill is a tool the surface actually serves.

    The regression this exists against: `fleet-reuse-idle-session` told the
    assistant to call `fleet_send_session`, and the served name is
    `fleet_send_to_session`. A Skill that names a tool nobody serves is worse
    than a vague one -- the assistant calls it, gets a protocol error, and reads
    that as the device being unreachable.
    """

    def test_every_referenced_tool_name_is_served(self) -> None:
        import re
        served = set(fleet_tools.SERVED_BY_NAME)
        referenced: dict[str, set[str]] = {}
        for path in sorted(SHARED_SKILLS.glob("*/SKILL.md")):
            body = path.read_text(encoding="utf-8")
            for match in re.finditer(r"`(fleet_[a-z_]+)`", body):
                referenced.setdefault(match.group(1), set()).add(path.parent.name)
        self.assertTrue(referenced, "no skill names a tool; the scan is broken")
        stray = {name: sorted(where) for name, where in referenced.items() if name not in served}
        self.assertEqual(stray, {}, "a Skill names a tool the surface does not serve")

    def test_the_send_verb_is_named_exactly_once_and_correctly(self) -> None:
        body = (SHARED_SKILLS / "fleet-reuse-idle-session" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`fleet_send_to_session`", body)
        self.assertNotIn("`fleet_send_session`", body)


# --------------------------------------------------------------------------
# 1. Package load
# --------------------------------------------------------------------------

class PackageLoadCarriesNoToolList(unittest.TestCase):
    """`LOCAL_CC_PLUGIN_LOADED` is a statement about a directory, and no more."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-dev-stack-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"
        cc.generate(skills_source=SHARED_SKILLS, out=self.out, next_mcp_url=DEV_URL)

    def test_the_manifest_and_binding_are_the_documented_shapes(self) -> None:
        manifest = json.loads((self.out / ".claude-plugin" / "plugin.json").read_text())
        binding = json.loads((self.out / ".mcp.json").read_text())
        # A load registers a NAME. That name is the whole of what a load
        # contributes to a reachability claim.
        self.assertEqual(manifest["name"], cc.DEV_PLUGIN_NAME)
        self.assertEqual(list(binding["mcpServers"]), ["serveros-fleet"])
        self.assertEqual(binding["mcpServers"]["serveros-fleet"]["type"], "http")

    def test_a_registered_server_name_is_not_a_tool_list(self) -> None:
        """The directory a load reads carries no tool *declaration*.

        The Skills do name tools -- that is what a Skill is for. What no file
        carries is a tool list: the wire shape of one is a declaration with an
        `inputSchema`, and the enumeration of all 34 is what a load is not.
        """
        served = set(fleet_tools.SERVED_BY_NAME)
        for structural in (".claude-plugin/plugin.json", ".mcp.json"):
            text = (self.out / structural).read_text(encoding="utf-8")
            self.assertNotIn("fleet_", text, f"{structural} names a tool")
        for path in self.out.rglob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("inputSchema", text, f"{path} declares a tool")
            found = {name for name in served if name in text}
            self.assertLess(len(found), len(served),
                            f"{path} carries the whole 34-name surface")

    def test_the_generated_plugin_is_byte_identical_to_the_one_skills_source(self) -> None:
        """One canonical copy: the adapter reads the shared source, never retypes."""
        for skill in sorted(SHARED_SKILLS.iterdir()):
            generated = (self.out / "skills" / skill.name / "SKILL.md").read_bytes()
            self.assertEqual(generated, (skill / "SKILL.md").read_bytes())


# --------------------------------------------------------------------------
# 2. An authenticated tools/list
# --------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Client:
    """Returns 4xx/5xx rather than raising, so a refusal is a reading."""

    def __init__(self, base: str) -> None:
        self.base = base
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method, path, *, body=None, headers=None):
        request = urllib.request.Request(
            self.base + path, data=body, method=method,
            headers=dict(headers or {"Content-Type": "application/json"}),
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def rpc(self, payload, *, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, out_headers, body = self.request(
            "POST", "/mcp", body=json.dumps(payload).encode(), headers=headers)
        return status, out_headers, (json.loads(body) if body else None)


class TheIngressRefusesBeforeItEnumerates(unittest.TestCase):
    """The MCP endpoint, driven as a protocol: 401 is a refusal, 200 is a list.

    No providers are bound. These are about the boundary in front of the fleet,
    and a surface with no route still has to open and refuse correctly.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        pat = tmp / "owner-pat"
        pat.write_text(OWNER_TOKEN + "\n", encoding="utf-8")
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.config = FleetConfig(
            source=None,
            local_device_ref=TOKYO,
            providers={},
            auth=AuthBinding(pat_anchor=pat),
            server=ServerBinding(bind="127.0.0.1", port=cls.port, public_base_url=cls.base),
            policy=PolicyBinding(),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        cls.service = FleetService(cls.config, providers={})
        cls.oauth = build_oauth(cls.config, base_url=cls.base, store_root=tmp / "oauth")
        surface = HttpSurface(service=cls.service, config=cls.config, oauth=cls.oauth,
                              audit=cls.service.audit)
        cls.server = serve(surface, bind="127.0.0.1", port=cls.port)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.client = _Client(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._tmp.cleanup()

    def test_an_unauthenticated_list_is_a_refusal_and_not_an_empty_list(self) -> None:
        """A 401 that reads as "no tools" is the substitution this forbids."""
        status, headers, payload = self.client.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "invalid_token")
        self.assertIn("resource_metadata=", headers.get("WWW-Authenticate", ""))
        # Nothing about a tool list: no result, no array, no count.
        self.assertNotIn("result", payload)
        self.assertNotIn("tools", payload)

    def test_an_authenticated_list_returns_the_surface_and_only_then(self) -> None:
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=OWNER_TOKEN)
        self.assertEqual(status, 200)
        names = [tool["name"] for tool in payload["result"]["tools"]]
        self.assertEqual(len(names), 45)
        self.assertEqual(len(set(names)), 45)
        # The verb the Skill used to misname. It is served under its real name,
        # and the name it was called by is not a tool.
        self.assertIn("fleet_send_to_session", names)
        self.assertNotIn("fleet_send_session", names)

    def test_forty_four_tools_is_not_thirty_three_permissions(self) -> None:
        _, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, token=OWNER_TOKEN)
        tools_out = payload["result"]["tools"]
        writes = [t["name"] for t in tools_out if not t["annotations"]["readOnlyHint"]]
        self.assertLess(len(writes), len(tools_out), "listing everything cannot mean doing everything")
        self.assertIn("fleet_create_session", writes)

    def test_six_scopes_are_advertised_and_two_are_granted_by_default(self) -> None:
        """Advertised is not held. This is the fact the Skills used to elide."""
        status, _, body = self.client.request("GET", "/.well-known/oauth-authorization-server")
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertEqual(sorted(document["scopes_supported"]), sorted(fleet_scopes.ALLOWED_SCOPES))
        self.assertEqual(len(document["scopes_supported"]), 6)
        granted_default = fleet_scopes.DEFAULT_GRANTABLE_SCOPES
        self.assertEqual(set(granted_default), {"fleet:read", "fleet:send"})
        self.assertTrue(set(granted_default) < set(fleet_scopes.ALLOWED_SCOPES))
        self.assertEqual(fleet_scopes.PLANNED_SCOPES, ())

    def test_a_missing_route_is_reported_as_an_unreachable_gap_not_as_emptiness(self) -> None:
        """A listing over no route names the gap; it does not answer "no sessions".

        This is the same distinction one layer down: an unreachable device and a
        device with nothing on it produce different answers, and reading the
        first as the second is how "we could not look" becomes "there is
        nothing there". The listing also carries no tool surface -- a list of
        sessions is not a list of tools.
        """
        status, _, payload = self.client.rpc(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "fleet_list_sessions", "arguments": {}}},
            token=OWNER_TOKEN)
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertFalse(result.get("isError"), "a listing answers even with nothing to reach")
        self.assertNotIn("tools", result)
        self.assertNotIn("inputSchema", json.dumps(result))
        listing = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        self.assertEqual(listing["sessions"], [])
        unreachable = listing["devices_unreachable"]
        self.assertTrue(unreachable, "a device with no route left no trace on the listing")
        self.assertTrue(all(row["code"] == "UNKNOWN_DEVICE" for row in unreachable))
        self.assertIn("holds no route", unreachable[0]["detail"])


# --------------------------------------------------------------------------
# 3. An ability resolution
# --------------------------------------------------------------------------

def _resolver_module():
    spec = importlib.util.spec_from_file_location(
        "serveros_ability_resolver_stack", ROOT / "abilities" / "resolver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnAbilityResolutionIsNotAGrant(unittest.TestCase):
    """`abilities/resolver.py` answers "can this machine do this", and nothing else.

    It is the third read-only decision kernel, and the plane it belongs to is
    not the Fleet tool plane. A resolution that came back `AVAILABLE` is not a
    permission, and one that came back `UNKNOWN` is not a pass.
    """

    def test_a_request_with_nothing_supplied_resolves_unknown_and_not_available(self) -> None:
        resolver = _resolver_module()
        record = resolver.resolve({
            "ability": "ability:test-management",
            "device_ref": TOKYO,
            "runtime": "claude-code",
        })
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertNotEqual(record["status"], "AVAILABLE")
        # No finding to name, so no reason is invented.
        self.assertIsNone(record["reason"])
        self.assertIn("the ability catalogue", record["detail"])
        self.assertEqual(record["candidates"], [])
        # Every status is a description of a capability, none is an authority.
        self.assertEqual(
            set(resolver.STATUSES),
            {"AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN"})

    def test_a_resolution_record_carries_no_permission_field(self) -> None:
        resolver = _resolver_module()
        record = resolver.resolve({
            "ability": "ability:fleet-send",
            "device_ref": TOKYO,
            "runtime": "claude-code",
        })
        for forbidden in ("authorized", "granted", "permitted", "allowed", "consented"):
            self.assertNotIn(forbidden, record,
                             "a resolution must not carry an authorization")
        # Role-shaped fields exist and are not grants: unresolved is UNKNOWN.
        self.assertEqual(record["product_role_state"], "UNKNOWN")
        self.assertIsNone(record["realization_role"])

    def test_the_ability_plane_does_not_know_the_tool_plane(self) -> None:
        """Two planes, and a name in one is not a name in the other."""
        resolver = _resolver_module()
        source = Path(resolver.__file__).read_text(encoding="utf-8")
        self.assertNotIn("fleet_mcp", source)
        for tool_name in fleet_tools.SERVED_BY_NAME:
            self.assertNotIn(tool_name, source)

    def test_listed_is_not_resolvable(self) -> None:
        """In the pinned catalogue and implemented by nobody is a definite no.

        The pair to `test_..._resolves_unknown`: a supplied empty provider set
        is a published closed list with nothing in it, so the answer is a
        refusal -- not UNKNOWN, and never AVAILABLE. "A documented ability is
        not an available one" is the resolver's own sentence.
        """
        resolver = _resolver_module()
        record = resolver.resolve({
            "ability": "ability:test-management",
            "device_ref": TOKYO,
            "runtime": "claude-code",
            "abilities": {"test-management": {"documented": True}},
            "providers": [],
        })
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertNotEqual(record["status"], "AVAILABLE")


# --------------------------------------------------------------------------
# 4. A session lifecycle
# --------------------------------------------------------------------------

class _RecordingProvider:
    """A device that answers exactly what the lifecycle test needs it to."""

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref
        self.sent: list[tuple[str, str]] = []

    def send(self, key, message, *, idempotency_key=None):
        self.sent.append((key.ref, message))
        # What a real provider returns: an id for the run it started, and an
        # acceptance. Neither is the run's state.
        return {"runId": f"run-{len(self.sent)}", "queuedMessageUuid": None, "accepted": True}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {"state": "completed", "output": "done"}


class AnAcceptedMessageIsNotASettledRun(unittest.TestCase):
    """Dispatch is the middle of the task. Release happens by reading back."""

    def _service(self, tmp: Path):
        anchor = tmp / "pat"
        anchor.write_text(OWNER_TOKEN + "\n", encoding="utf-8")
        config = FleetConfig(
            source=None,
            local_device_ref=TOKYO,
            providers={TOKYO: ProviderBinding(
                device_ref=TOKYO, kind="cloudcli-mcp-http",
                endpoint="http://127.0.0.1:3011/mcp",
                credential_anchor=anchor, allow_send=True)},
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(send_allow=(SESSION_REF,)),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        provider = _RecordingProvider(TOKYO)
        return FleetService(config, providers={TOKYO: provider}), provider

    def _principal(self, *scopes):
        return Principal(name="tester", scopes=frozenset(scopes or
                         (fleet_scopes.SCOPE_READ, fleet_scopes.SCOPE_SEND)),
                         credential_kind="pat")

    def test_an_accepted_send_reports_no_run_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self._service(Path(tmp))
            key = SessionKey.compose(TOKYO, "claude", "abc")
            result = service.send_to_session(self._principal(), key, message="hello")
        # An id, and an acceptance. Not an outcome.
        self.assertTrue(result["run"]["accepted"])
        self.assertTrue(result["run_id"])
        self.assertNotIn("state", result["run"])
        self.assertNotIn("state", result)

    def test_the_run_state_comes_from_the_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _ = self._service(Path(tmp))
            key = SessionKey.compose(TOKYO, "claude", "abc")
            sent = service.send_to_session(self._principal(), key, message="hello")
            run = service.get_run(self._principal(), key, sent["run_id"])
        self.assertEqual(run["state"], "completed")
        self.assertEqual(run["run_id"], sent["run_id"])
        self.assertEqual(run["session_ref"], SESSION_REF)

    def test_a_read_only_credential_is_refused_before_the_device_speaks(self) -> None:
        """Send is its own scope, and read does not imply it."""
        with tempfile.TemporaryDirectory() as tmp:
            service, provider = self._service(Path(tmp))
            key = SessionKey.compose(TOKYO, "claude", "abc")
            with self.assertRaises(FleetRefusal) as caught:
                service.send_to_session(self._principal(fleet_scopes.SCOPE_READ), key,
                                        message="hello")
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")
        self.assertEqual(provider.sent, [], "the device was reached for a refused call")

    def test_a_create_is_not_a_started_run(self) -> None:
        """The surface refuses to fabricate a run id for a create that started none."""
        self.assertEqual(fleet_tools.SERVED_BY_NAME["fleet_create_session"].name,
                         "fleet_create_session")
        self.assertNotEqual(
            fleet_tools.SERVED_BY_NAME["fleet_create_session"].description,
            fleet_tools.SERVED_BY_NAME["fleet_get_run"].description,
            "creation and run lookup must not share one description")
        self.assertNotIn("run_id", fleet_tools.SERVED_BY_NAME["fleet_create_session"].input_schema
                         .get("required", []))


if __name__ == "__main__":
    unittest.main()
