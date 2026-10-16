#!/usr/bin/env python3
"""The 22 inherited verbs address the runtime the caller named — and no more.

The sentence these tests keep false:

    "one device can serve two runtimes, and the mirror reaches only the first"

Before this change every mirrored verb dropped the `runtime` axis at the tool
edge, so `FleetService.native_call`'s own resolution (`_runtime_for_call`,
`provider_for`) was never asked: a call naming the second runtime silently ran
against the device's own. The hand-written tools took `runtime`; the mirror did
not, which made the mirror a *narrower* door than the tools it inherits from —
and, in the one direction that matters, a *different* one: the same verb reached
a different account than the caller asked for.

Everything here runs against FAKE providers. No socket, no device, no
credential, no model call, no approval answered.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fleet_mcp import scopes as scopes_mod
from fleet_mcp import tools as tools_mod
from fleet_mcp.config import (AuditBinding, AuthBinding, CreateGrant,
                              FleetConfig, PolicyBinding, ProviderBinding,
                              ServerBinding, provider_key)
from fleet_mcp.errors import BAD_REQUEST, BAD_SESSION_REF, UNKNOWN_DEVICE
from fleet_mcp.fleet import SCOPE_CREATE, SCOPE_READ, SCOPE_SEND
from fleet_mcp.fleet import FleetService, Principal, RateLimiter
from fleet_mcp.runtime_identity import BASE_RUNTIME

DEVICE = "device:tokyo-shared-dev-01"
SECOND = "claude-official"
OAUTH = "oauth:test-client"


def _registry(tmp: Path) -> Path:
    p = tmp / "registry.yaml"
    p.write_text(
        "devices:\n"
        f"  - device_ref: {DEVICE}\n"
        "    display_name: tokyo\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        "    ownership: company\n",
        encoding="utf-8",
    )
    return p


class FakeProvider:
    """One runtime's route. It records what it was asked and answers nothing."""

    def __init__(self, device_ref: str, runtime: str):
        self.device_ref = device_ref
        self.runtime = runtime
        self.calls: list = []

    def native_call(self, tool, arguments, *, session_ref=None):
        self.calls.append((tool, arguments, session_ref))
        return {"ok": True, "runtime": self.runtime, "tool": tool}


class TwoRuntimes:
    """One device, two bindings, two providers, and the tool edge above them."""

    def __init__(self, tmp: Path, *, create_allow=None, grants=None,
                 second_enabled: bool = True, second_allow_create: bool = True,
                 send_allow=()):
        self.anchor = tmp / "vince.pat"
        self.anchor.write_text("owner-token-value", encoding="utf-8")
        second_anchor = tmp / "fjd.pat"
        second_anchor.write_text("second-token-value", encoding="utf-8")

        self.config = FleetConfig(
            source=None,
            local_device_ref=DEVICE,
            providers={
                DEVICE: ProviderBinding(
                    device_ref=DEVICE, kind="cloudcli-mcp-http",
                    endpoint="http://127.0.0.1:3011/mcp",
                    credential_anchor=self.anchor,
                    allow_send=True, allow_create=True, allow_control=True),
                provider_key(DEVICE, SECOND): ProviderBinding(
                    device_ref=DEVICE, runtime=SECOND, kind="cloudcli-mcp-http",
                    endpoint="http://127.0.0.1:3013/mcp",
                    credential_anchor=second_anchor,
                    enabled=second_enabled,
                    allow_send=True, allow_create=second_allow_create,
                    allow_control=True),
            },
            auth=AuthBinding(pat_anchor=self.anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                send_allow=tuple(send_allow),
                create_allow=dict(create_allow or {}),
                operator_grants=dict(grants or {}),
            ),
            audit=AuditBinding(path=tmp / "audit.jsonl",
                               rate_limit_per_minute=10_000),
        )
        from fleet_mcp.registry import load_registry
        self.first = FakeProvider(DEVICE, BASE_RUNTIME)
        self.second = FakeProvider(DEVICE, SECOND)
        self.service = FleetService(
            self.config, registry=load_registry(_registry(tmp)),
            providers={DEVICE: self.first,
                       provider_key(DEVICE, SECOND): self.second})

    def ctx(self, *scopes: str) -> tools_mod.ToolContext:
        principal = Principal(name=OAUTH, scopes=frozenset(scopes),
                              credential_kind="oauth")
        return tools_mod.ToolContext(service=self.service, principal=principal)

    def edge(self, name: str, args: dict, *scopes: str):
        """Call through the REAL edge, so schema enforcement is in the path."""
        return tools_mod.call_tool(self.ctx(*scopes), name, args)


def codes(result: dict) -> str:
    import json
    return json.dumps(result, ensure_ascii=False)


def is_error(result: dict) -> bool:
    return bool(result.get("isError"))


# ---------------------------------------------------------------------------


class TheSchemaCarriesTheRuntimeAxis(unittest.TestCase):
    def test_every_mirrored_verb_accepts_a_runtime(self):
        for tool in tools_mod.NATIVE_MIRROR_TOOLS:
            props = tool.input_schema["properties"]
            self.assertIn("runtime", props, tool.name)
            self.assertIn("device_ref", props, tool.name)
            # Nothing became required that was not: the axis is optional and an
            # omitted key must stay absent, not default to something invented.
            self.assertNotIn("runtime", tool.input_schema.get("required", []),
                             tool.name)

    def test_the_closure_is_unchanged(self):
        for tool in tools_mod.SERVED_TOOLS:
            self.assertIs(tool.input_schema.get("additionalProperties"), False,
                          tool.name)

    def test_the_capability_tool_accepts_a_runtime(self):
        tool = tools_mod.SERVED_BY_NAME["fleet_cloudcli_native_capability"]
        self.assertIn("runtime", tool.input_schema["properties"])
        self.assertEqual(tool.input_schema["required"], ["device_ref"])

    def test_the_surface_is_still_thirty_three_tools_and_six_scopes(self):
        self.assertEqual(len(tools_mod.SERVED_TOOLS), 33)
        self.assertEqual(len(tools_mod.NATIVE_MIRROR_TOOLS), 22)
        self.assertEqual(len(scopes_mod.ALLOWED_SCOPES), 6)

    def test_an_unknown_top_level_key_is_still_refused(self):
        """The closure is what keeps a caller from smuggling in a stray axis."""
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            for stray in ("model", "permissionMode", "provider"):
                result = h.edge("fleet_cloudcli_session_get",
                                {"device_ref": DEVICE, stray: "x"},
                                SCOPE_READ)
                self.assertTrue(is_error(result), stray)
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])


class AMirroredReadReachesTheRuntimeItNames(unittest.TestCase):
    def test_session_get_lands_on_the_second_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_get",
                            {"device_ref": DEVICE, "runtime": SECOND,
                             "arguments": {"session": "s-1"}},
                            SCOPE_READ)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.second.calls), 1)
            self.assertEqual(h.first.calls, [])

    def test_session_get_without_a_runtime_keeps_the_old_behaviour(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_get",
                            {"device_ref": DEVICE,
                             "arguments": {"session": "s-1"}},
                            SCOPE_READ)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.first.calls), 1)
            self.assertEqual(h.second.calls, [])

    def test_an_unknown_runtime_is_refused_and_reaches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_get",
                            {"device_ref": DEVICE, "runtime": "not-a-runtime",
                             "arguments": {"session": "s-1"}},
                            SCOPE_READ)
            self.assertTrue(is_error(result))
            self.assertIn(UNKNOWN_DEVICE, codes(result))
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])

    def test_a_disabled_second_binding_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), second_enabled=False)
            result = h.edge("fleet_cloudcli_session_get",
                            {"device_ref": DEVICE, "runtime": SECOND,
                             "arguments": {"session": "s-1"}},
                            SCOPE_READ)
            self.assertTrue(is_error(result))
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])


class AMirroredSessionRefMustAgreeWithTheRuntime(unittest.TestCase):
    def test_a_reference_naming_another_runtime_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_send",
                            {"device_ref": DEVICE, "runtime": SECOND,
                             "session_ref": f"{DEVICE}|{BASE_RUNTIME}|s-1",
                             "arguments": {"session": "s-1", "message": "hi"}},
                            SCOPE_SEND)
            self.assertTrue(is_error(result))
            self.assertIn(BAD_SESSION_REF, codes(result))
            # Refused BEFORE any route: the reference and the runtime disagree,
            # so neither provider may be chosen.
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])

    def test_the_reference_alone_still_selects_the_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp),
                            send_allow=(f"{DEVICE}|{SECOND}|s-1",))
            result = h.edge("fleet_cloudcli_session_send",
                            {"device_ref": DEVICE,
                             "session_ref": f"{DEVICE}|{SECOND}|s-1",
                             "arguments": {"session": "s-1", "message": "hi"}},
                            SCOPE_SEND)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.second.calls), 1)
            self.assertEqual(h.first.calls, [])


class TheRuntimeSelectsTheAuthorizationNotJustTheRoute(unittest.TestCase):
    """device x runtime x verb x project — none of it reachable from the mirror.

    The runtime axis carries its own `allow_create`: it lives on a BINDING, and
    a binding is one account's reach. So the pair of tests here is the whole
    point — the same project, the same device, the same verb, and two answers
    that differ only by which account was named.
    """

    def _create(self, h, runtime):
        return h.edge("fleet_cloudcli_session_create",
                      {"device_ref": DEVICE, "runtime": runtime,
                       "project": "p", "arguments": {"project": "p"}},
                      SCOPE_CREATE)

    def test_creation_is_granted_in_the_runtime_that_authorized_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            grants={OAUTH: {DEVICE: ("create",)}})
            result = self._create(h, BASE_RUNTIME)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.first.calls), 1)
            self.assertEqual(h.second.calls, [])

    def test_the_same_grant_does_not_authorize_the_second_runtime(self):
        """A grant made under one runtime is not a grant under the one beside it."""
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            second_allow_create=False,
                            grants={OAUTH: {DEVICE: ("create",)}})
            result = self._create(h, SECOND)
            self.assertTrue(is_error(result))
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])

    def test_the_second_runtime_needs_its_own_operator_grant(self):
        """An operator grant is keyed by device AND runtime, so it is not inherited.

        `_operator_grant_verbs` reads `provider_key(device_ref, runtime)`: the
        base runtime keeps the bare `device:<id>` key, and a second runtime
        needs its own entry. Granting one runtime must therefore buy nothing in
        the account beside it — which is the whole reason the runtime is an axis
        and not a spelling of the device.
        """
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            grants={OAUTH: {DEVICE: ("create",)}})
            result = self._create(h, SECOND)
            self.assertTrue(is_error(result))
            self.assertIn("WRITE_NOT_GRANTED", codes(result))
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])

    def test_the_second_runtime_authorizes_on_its_own_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            grants={OAUTH: {
                                DEVICE: ("create",),
                                provider_key(DEVICE, SECOND): ("create",)}})
            result = self._create(h, SECOND)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.second.calls), 1)
            self.assertEqual(h.first.calls, [])

    def test_an_unlisted_project_is_refused_in_either_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={},
                            grants={OAUTH: {DEVICE: ("create",)}})
            for runtime in (BASE_RUNTIME, SECOND):
                result = self._create(h, runtime)
                self.assertTrue(is_error(result), runtime)
            self.assertEqual(h.first.calls, [])
            self.assertEqual(h.second.calls, [])

    def test_a_write_without_a_session_ref_is_still_refused(self):
        """The floor is never read out of the native payload."""
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_send",
                            {"device_ref": DEVICE, "runtime": SECOND,
                             "arguments": {"session": "s-1", "message": "hi"}},
                            SCOPE_SEND)
            self.assertTrue(is_error(result))
            self.assertEqual(h.second.calls, [])

    def test_read_scope_does_not_reach_a_write_verb(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_send",
                            {"device_ref": DEVICE, "runtime": SECOND,
                             "session_ref": f"{DEVICE}|{SECOND}|s-1",
                             "arguments": {"session": "s-1", "message": "hi"}},
                            SCOPE_READ)
            self.assertTrue(is_error(result))
            self.assertEqual(h.second.calls, [])


class TheCapabilitySnapshotNamesItsRuntime(unittest.TestCase):
    def test_a_runtime_reaches_the_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            seen: list = []
            original = h.service.native_capability

            def spy(principal, *, device_ref, runtime=BASE_RUNTIME):
                seen.append((device_ref, runtime))
                return {"device_ref": device_ref, "runtime": runtime}

            h.service.native_capability = spy
            try:
                result = h.edge("fleet_cloudcli_native_capability",
                                {"device_ref": DEVICE, "runtime": SECOND},
                                SCOPE_READ)
            finally:
                h.service.native_capability = original
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(seen, [(DEVICE, SECOND)])

    def test_omitting_the_runtime_uses_the_services_own_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            seen: list = []
            original = h.service.native_capability

            def spy(principal, *, device_ref, runtime=BASE_RUNTIME):
                seen.append((device_ref, runtime))
                return {"device_ref": device_ref, "runtime": runtime}

            h.service.native_capability = spy
            try:
                h.edge("fleet_cloudcli_native_capability",
                      {"device_ref": DEVICE}, SCOPE_READ)
            finally:
                h.service.native_capability = original
            # `None` is never forwarded: the signature's own default decides.
            self.assertEqual(seen, [(DEVICE, BASE_RUNTIME)])


if __name__ == "__main__":
    unittest.main()
