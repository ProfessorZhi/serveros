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
UUID = "11111111-2222-3333-4444-555555555555"


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

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None):
        # The hand-written create reaches the provider here; a mirrored one
        # reaches `native_call`. Both record, so "which account" is always a
        # fact about the fake rather than an inference from a return value.
        self.calls.append(("create", {"project": project, "provider": provider}))
        return {"sessionId": UUID, "runId": None, "served_by": self.runtime}


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

    def test_the_surface_is_still_thirty_four_tools_and_six_scopes(self):
        self.assertEqual(len(tools_mod.SERVED_TOOLS), 34)
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


class ABlankRuntimeIsNeverAnAccount(unittest.TestCase):
    """An explicitly-named runtime that is not a name is refused, not defaulted.

    `FleetService._runtime_for_call` resolved an unnamed call with
    `runtime or BASE_RUNTIME`, so `runtime=""` — a value the caller DID send —
    became "the base account". That is the one outcome this whole axis exists to
    prevent: a call that names nothing lands on a named account, and the caller
    is told it succeeded. Whitespace fared no better in kind: it was refused as
    `UNKNOWN_DEVICE`, which is the answer for "a legal name with nothing behind
    it" and reads to an operator as a missing binding rather than a bad request.

    The grammar authority is `SessionKey` (`FleetService._validate_runtime_selector`
    already says so); these tests pin that the mirror and the capability snapshot
    go through it, and that they reach NO provider while refusing.

    A key that is ABSENT still means the device's own runtime — that is the
    behaviour every caller had before a device could serve two, and it is
    unchanged. An explicit `null` is the same "unnamed" in this codebase
    (`node_protocol.py`: "`runtime=None` is v1, unchanged"), so it is not
    treated as a name either.
    """

    BLANKS = ("", " ", "   ", "\t", "\n", "  \t ")

    def _mirror_read(self, h, runtime, *, session_ref=None):
        args = {"device_ref": DEVICE, "runtime": runtime,
                "arguments": {"session": "s-1"}}
        if session_ref is not None:
            args["session_ref"] = session_ref
        return h.edge("fleet_cloudcli_session_get", args, SCOPE_READ)

    def test_a_blank_runtime_on_a_mirrored_read_is_a_bad_request(self):
        for blank in self.BLANKS:
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp))
                result = self._mirror_read(h, blank)
                self.assertTrue(is_error(result), repr(blank))
                self.assertIn(BAD_REQUEST, codes(result), repr(blank))
                self.assertEqual(h.first.calls, [], repr(blank))
                self.assertEqual(h.second.calls, [], repr(blank))

    def test_a_blank_runtime_with_a_session_ref_is_refused_without_a_route(self):
        """Neither half may win: the call names no valid runtime at all."""
        for blank in self.BLANKS:
            for runtime_named in (BASE_RUNTIME, SECOND):
                with tempfile.TemporaryDirectory() as tmp:
                    h = TwoRuntimes(Path(tmp))
                    result = self._mirror_read(
                        h, blank,
                        session_ref=f"{DEVICE}|{runtime_named}|s-1")
                    self.assertTrue(is_error(result), repr(blank))
                    self.assertTrue(
                        BAD_REQUEST in codes(result) or BAD_SESSION_REF in codes(result),
                        codes(result))
                    self.assertEqual(h.first.calls, [], repr(blank))
                    self.assertEqual(h.second.calls, [], repr(blank))

    def test_a_blank_runtime_cannot_create_in_the_base_account(self):
        """The sharpest form: this one used to MINT against the base account."""
        for blank in self.BLANKS:
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                                grants={OAUTH: {DEVICE: ("create",)}})
                result = h.edge("fleet_cloudcli_session_create",
                                {"device_ref": DEVICE, "runtime": blank,
                                 "project": "p",
                                 "arguments": {"project": "p"}},
                                SCOPE_CREATE)
                self.assertTrue(is_error(result), repr(blank))
                self.assertIn(BAD_REQUEST, codes(result), repr(blank))
                self.assertEqual(h.first.calls, [], repr(blank))
                self.assertEqual(h.second.calls, [], repr(blank))

    def test_a_blank_runtime_on_the_capability_snapshot_is_a_bad_request(self):
        for blank in self.BLANKS:
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp))
                result = h.edge("fleet_cloudcli_native_capability",
                                {"device_ref": DEVICE, "runtime": blank},
                                SCOPE_READ)
                self.assertTrue(is_error(result), repr(blank))
                self.assertIn(BAD_REQUEST, codes(result), repr(blank))

    def test_the_hand_written_read_keeps_refusing_a_blank_runtime(self):
        """The behaviour that was already right, pinned so it cannot regress."""
        for blank in self.BLANKS:
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp))
                result = h.edge("fleet_list_sessions",
                                {"device": DEVICE, "runtime": blank}, SCOPE_READ)
                self.assertTrue(is_error(result), repr(blank))
                self.assertIn(BAD_REQUEST, codes(result), repr(blank))

    def test_an_absent_runtime_still_means_the_devices_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = self._mirror_read(h, None)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.first.calls), 1)
            self.assertEqual(h.second.calls, [])

    def test_an_explicit_null_is_unnamed_not_a_second_kind_of_missing(self):
        """`node_protocol.py` fixes `runtime=None` as v1 \"unnamed\"; it names
        no account, so it cannot reach one the caller did not name."""
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp))
            result = h.edge("fleet_cloudcli_session_get",
                            {"device_ref": DEVICE, "runtime": None,
                             "arguments": {"session": "s-1"}}, SCOPE_READ)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.first.calls), 1)
            self.assertEqual(h.second.calls, [])

    def test_a_non_string_runtime_is_a_bad_request_not_a_route(self):
        for bogus in (0, 1, True, [], {}, 1.5):
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp))
                result = self._mirror_read(h, bogus)
                self.assertTrue(is_error(result), repr(bogus))
                self.assertIn(BAD_REQUEST, codes(result), repr(bogus))
                self.assertEqual(h.first.calls, [], repr(bogus))
                self.assertEqual(h.second.calls, [], repr(bogus))

    def test_the_hand_written_create_refuses_a_blank_runtime(self):
        """The same hole existed on the tool that predates the mirror.

        `fleet_create_session` resolved its runtime with `args.get("runtime") or
        BASE_RUNTIME`, so a blank never reached the validator: the hand-written
        create MINTED in the base account for a caller who named something else.
        The mirror was where it was noticed; it was not where it started.
        """
        for blank in self.BLANKS:
            with tempfile.TemporaryDirectory() as tmp:
                h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                                grants={OAUTH: {DEVICE: ("create",)}})
                result = h.edge("fleet_create_session",
                                {"device": DEVICE, "project": "p",
                                 "runtime": blank}, SCOPE_CREATE)
                self.assertTrue(is_error(result), repr(blank))
                self.assertIn(BAD_REQUEST, codes(result), repr(blank))
                self.assertEqual(h.first.calls, [], repr(blank))
                self.assertEqual(h.second.calls, [], repr(blank))

    def test_the_hand_written_create_still_works_without_a_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            grants={OAUTH: {DEVICE: ("create",)}})
            result = h.edge("fleet_create_session",
                            {"device": DEVICE, "project": "p"}, SCOPE_CREATE)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.first.calls), 1)
            self.assertEqual(h.second.calls, [])

    def test_the_hand_written_create_names_the_second_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = TwoRuntimes(Path(tmp), create_allow={"p": CreateGrant()},
                            grants={OAUTH: {DEVICE: ("create",),
                                            provider_key(DEVICE, SECOND): ("create",)}})
            result = h.edge("fleet_create_session",
                            {"device": DEVICE, "project": "p",
                             "runtime": SECOND}, SCOPE_CREATE)
            self.assertFalse(is_error(result), codes(result))
            self.assertEqual(len(h.second.calls), 1)
            self.assertEqual(h.first.calls, [])
