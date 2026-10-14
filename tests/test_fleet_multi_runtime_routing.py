"""Two runtimes on one physical device: routing, isolation, and the refusals.

The case: a Dell A6000 where `vince` and `fjd` each run their own CloudCLI under
their own OS account. One `device_ref` (one authenticated machine), two
runtimes, two endpoints, two credentials, two node secrets.

Everything here is a fake transport. No device, no credential value and no
socket is involved: what is under test is WHICH binding a call reaches, and
which calls are refused, given a session reference and a principal.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import node_protocol as np                       # noqa: E402
from fleet_mcp import runtime_identity                           # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,   # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.errors import UNKNOWN_DEVICE, FleetRefusal        # noqa: E402
from fleet_mcp.fleet import (SCOPE_READ, SCOPE_SEND, FleetService,   # noqa: E402
                             Principal)
from fleet_mcp.identity import SessionKey                        # noqa: E402

DEVICE = "device:llm-lab-dell"
OWNER = "vince"
SECOND_USER = "fjd"
SECOND = "claude-fjd"
UUID = "b2cb3f10-1111-2222-3333-444455556666"
OWNER_REF = f"{DEVICE}|claude|{UUID}"
SECOND_REF = f"{DEVICE}|{SECOND}|{UUID}"


def _registry(tmp: Path, owner: str = "company") -> Path:
    p = tmp / "registry.yaml"
    p.write_text(
        "devices:\n"
        f"  - device_ref: {DEVICE}\n"
        "    display_name: llm-lab-dell\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        f"    ownership: {owner}\n",
        encoding="utf-8")
    return p


class _Provider:
    """A device that records what it was asked and which endpoint it is."""

    kind = "fleet-node"

    def __init__(self, ref: str, runtime: str):
        self.device_ref = ref
        self.runtime = runtime
        self.calls: list = []

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        return {"session_ref": key.ref, "served_by": self.runtime}

    def native_call(self, tool, arguments, *, session_ref=None):
        self.calls.append((tool, arguments))
        return {"ok": True, "tool": tool}

    def list_sessions(self, *, limit, project=None, state=None):
        self.calls.append(("list_sessions", limit))
        return [{"served_by": self.runtime}]

    def health(self):
        raise AssertionError("not used here")


class TwoRuntimes:
    """One device, one or two bindings, and the fake providers behind them."""

    def __init__(self, tmp: Path, *, owner: str = "company", second: bool = True,
                 grants=None, enabled_second: bool = True):
        self.tmp = tmp
        first_anchor = tmp / "vince.node-secret"
        first_anchor.write_text("vince-secret", encoding="utf-8")
        second_anchor = tmp / "fjd.node-secret"
        second_anchor.write_text("fjd-secret", encoding="utf-8")
        self.registry = _registry(tmp, owner)

        providers = {
            DEVICE: ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7301", credential_anchor=first_anchor,
                runtime="claude", allow_send=True, allow_control=True),
        }
        if second:
            providers[provider_key(DEVICE, SECOND)] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7302", credential_anchor=second_anchor,
                runtime=SECOND, allow_send=True, allow_control=True,
                enabled=enabled_second)

        self.config = FleetConfig(
            source=None, local_device_ref=DEVICE, providers=providers,
            auth=AuthBinding(pat_anchor=first_anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(operator_grants=dict(grants or {})),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )

        self.first = _Provider(DEVICE, "claude")
        self.second = _Provider(DEVICE, SECOND) if second else None
        instances = {provider_key(DEVICE, "claude"): self.first}
        if second and enabled_second:
            # A runtime that is not answering has no route: that is what makes
            # it offline rather than silently served by the binding beside it.
            instances[provider_key(DEVICE, SECOND)] = self.second
        from fleet_mcp.registry import load_registry
        self.service = FleetService(self.config, registry=load_registry(self.registry),
                                    providers=instances)

    def principal(self, *scopes, name="oauth:test") -> Principal:
        return Principal(name=name, scopes=frozenset(scopes or (SCOPE_READ, SCOPE_SEND)),
                         credential_kind="oauth")


class TheSameUuidUnderTwoRuntimesIsTwoSessionsTest(unittest.TestCase):
    """The whole reason the runtime is part of the address."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_reference_reaches_its_own_binding(self):
        h = TwoRuntimes(self.tmp)
        one = h.service.get_session(h.principal(), SessionKey.parse(OWNER_REF))
        two = h.service.get_session(h.principal(), SessionKey.parse(SECOND_REF))
        self.assertEqual(one["served_by"], "claude")
        self.assertEqual(two["served_by"], SECOND)
        self.assertEqual([c[0] for c in h.first.calls], ["get_session"])
        self.assertEqual([c[0] for c in h.second.calls], ["get_session"])

    def test_the_same_native_id_is_a_different_reference_per_runtime(self):
        self.assertNotEqual(OWNER_REF, SECOND_REF)
        self.assertEqual(SessionKey.parse(OWNER_REF).native_session_id,
                         SessionKey.parse(SECOND_REF).native_session_id)
        self.assertNotEqual(SessionKey.parse(OWNER_REF), SessionKey.parse(SECOND_REF))

    def test_a_native_call_follows_the_reference(self):
        h = TwoRuntimes(self.tmp)
        h.service.native_call(h.principal(),
                              device_ref=DEVICE, native_tool="sessions_list",
                              session_ref=SECOND_REF)
        # the native route records the CloudCLI tool name it was handed
        self.assertEqual([c[0] for c in h.second.calls], ["sessions_list"])
        self.assertEqual(h.first.calls, [])


class TheOldReferenceIsUnchangedTest(unittest.TestCase):
    """A machine that never grows a second runtime must behave exactly as before."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_single_runtime_binding_still_resolves(self):
        h = TwoRuntimes(self.tmp, second=False)
        got = h.service.get_session(h.principal(), SessionKey.parse(OWNER_REF))
        self.assertEqual(got["served_by"], "claude")
        self.assertIsNone(h.config.provider_for(DEVICE, SECOND))

    def test_the_bare_key_is_what_the_base_runtime_files_under(self):
        h = TwoRuntimes(self.tmp, second=False)
        self.assertEqual(provider_key(DEVICE), DEVICE)
        self.assertIn(DEVICE, h.config.providers)
        self.assertEqual(h.config.providers[DEVICE].runtime, "claude")

    def test_an_absent_runtime_is_unknown_rather_than_the_base_one(self):
        """The failure mode this prevents: a call for `claude-fjd` silently
        served by the device's own runtime because the lookup fell back."""
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.get_session(h.principal(), SessionKey.parse(SECOND_REF))
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)


class CredentialsDoNotCrossTest(unittest.TestCase):
    """Each runtime signs with its OWN secret, read from its OWN anchor."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_two_bindings_read_different_anchors(self):
        h = TwoRuntimes(self.tmp)
        first = h.config.providers[DEVICE]
        second = h.config.providers[provider_key(DEVICE, SECOND)]
        self.assertNotEqual(first.credential_anchor, second.credential_anchor)
        self.assertNotEqual(first.credential(), second.credential())

    def test_a_signature_for_one_runtime_does_not_verify_for_the_other(self):
        h = TwoRuntimes(self.tmp)
        first = h.config.providers[DEVICE]
        second = h.config.providers[provider_key(DEVICE, SECOND)]
        headers = np.sign(second.credential(), method="GET", path="/v1/health",
                          body=b"", device_ref=DEVICE, runtime=SECOND)
        # It verifies under its own runtime and secret...
        np.verify(second.credential(), method="GET", path="/v1/health", body=b"",
                  headers=headers, expected_device_ref=DEVICE,
                  replay=np.ReplayCache(), expected_runtime=SECOND)
        # ...and against the SAME device, under the other secret, it does not.
        with self.assertRaises(np.SignatureError) as caught:
            np.verify(first.credential(), method="GET", path="/v1/health", body=b"",
                      headers=headers, expected_device_ref=DEVICE,
                      replay=np.ReplayCache(), expected_runtime=SECOND)
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")


class TheNodeSignatureCoversTheRuntimeTest(unittest.TestCase):
    """v2: the runtime is inside the signature, and replay is still refused."""

    SECRET = "shared-device-secret"
    ARGS = dict(method="POST", path="/v1/sessions", body=b"{}", device_ref=DEVICE)

    def _verify(self, headers, **over):
        # `verify` takes `expected_device_ref`; `device_ref` belongs to `sign`.
        request = {k: v for k, v in self.ARGS.items() if k != "device_ref"}
        return np.verify(self.SECRET, headers=headers, expected_device_ref=DEVICE,
                         replay=np.ReplayCache(), **{**request, **over})

    def test_v1_is_unchanged_and_still_verifies(self):
        headers = np.sign(self.SECRET, **self.ARGS)
        self.assertEqual(headers[np.PROTOCOL_HEADER], np.PROTOCOL)
        self.assertNotIn(np.RUNTIME_HEADER, headers)
        self.assertEqual(self._verify(headers, expected_runtime="claude"), DEVICE)

    def test_a_v1_request_is_refused_by_a_connector_serving_a_second_runtime(self):
        headers = np.sign(self.SECRET, **self.ARGS)
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers, expected_runtime=SECOND)
        self.assertEqual(caught.exception.code, "RUNTIME_REQUIRED")

    def test_v2_signs_the_runtime_and_verifies(self):
        headers = np.sign(self.SECRET, runtime=SECOND, **self.ARGS)
        self.assertEqual(headers[np.PROTOCOL_HEADER], np.PROTOCOL_V2)
        self.assertEqual(headers[np.RUNTIME_HEADER], SECOND)
        self.assertEqual(self._verify(headers, expected_runtime=SECOND), DEVICE)

    def test_a_v2_request_for_another_runtime_is_refused(self):
        headers = np.sign(self.SECRET, runtime=SECOND, **self.ARGS)
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers, expected_runtime="claude")
        self.assertEqual(caught.exception.code, "RUNTIME_MISMATCH")

    def test_a_v2_request_that_names_no_runtime_is_refused(self):
        headers = np.sign(self.SECRET, runtime=SECOND, **self.ARGS)
        del headers[np.RUNTIME_HEADER]
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers, expected_runtime=SECOND)
        self.assertEqual(caught.exception.code, "MISSING_RUNTIME")

    def test_a_v1_request_cannot_carry_a_runtime_of_its_own(self):
        """The caller cannot self-report an account: v1 does not sign it."""
        headers = np.sign(self.SECRET, **self.ARGS)
        headers[np.RUNTIME_HEADER] = SECOND
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers, expected_runtime=SECOND)
        self.assertEqual(caught.exception.code, "RUNTIME_NOT_SIGNED")

    def test_a_tampered_runtime_breaks_the_signature(self):
        headers = np.sign(self.SECRET, runtime=SECOND, **self.ARGS)
        headers[np.RUNTIME_HEADER] = "claude"
        with self.assertRaises(np.SignatureError) as caught:
            self._verify(headers, expected_runtime="claude")
        self.assertEqual(caught.exception.code, "BAD_SIGNATURE")

    def test_a_v2_replay_is_refused(self):
        replay = np.ReplayCache()
        headers = np.sign(self.SECRET, runtime=SECOND, **self.ARGS)
        request = {k: v for k, v in self.ARGS.items() if k != "device_ref"}
        np.verify(self.SECRET, headers=headers, expected_device_ref=DEVICE,
                  replay=replay, expected_runtime=SECOND, **request)
        with self.assertRaises(np.SignatureError) as caught:
            np.verify(self.SECRET, headers=headers, expected_device_ref=DEVICE,
                      replay=replay, expected_runtime=SECOND, **request)
        self.assertEqual(caught.exception.code, "REPLAY")

    def test_the_two_versions_do_not_share_a_signature(self):
        one = np.sign(self.SECRET, **self.ARGS)[np.SIGNATURE_HEADER]
        two = np.sign(self.SECRET, runtime="claude", **self.ARGS)[np.SIGNATURE_HEADER]
        self.assertNotEqual(one, two)


class AGrantDoesNotCrossRuntimesTest(unittest.TestCase):
    """Owner consent is per runtime. Adding one does not widen the other."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_base_runtime_grant_does_not_admit_the_second_runtime(self):
        h = TwoRuntimes(self.tmp, grants={"oauth:test": {DEVICE: ["send"]}})
        # the base runtime is admitted
        self.assertIn(SCOPE_SEND, h.principal().scopes)
        h.service.native_call(h.principal(), device_ref=DEVICE,
                              native_tool="session_send",
                              session_ref=OWNER_REF,
                              arguments={"session": UUID, "message": "x"})
        # the second runtime is not: no entry under its own key
        with self.assertRaises(FleetRefusal):
            h.service.native_call(h.principal(), device_ref=DEVICE,
                                  native_tool="session_send",
                                  session_ref=SECOND_REF,
                                  arguments={"session": UUID, "message": "x"})

    def test_the_second_runtime_admits_only_with_its_own_entry(self):
        h = TwoRuntimes(self.tmp, grants={
            "oauth:test": {DEVICE: ["send"], provider_key(DEVICE, SECOND): ["send"]}})
        h.service.native_call(h.principal(), device_ref=DEVICE,
                              native_tool="session_send", session_ref=SECOND_REF,
                              arguments={"session": UUID, "message": "x"})
        self.assertEqual([c[0] for c in h.second.calls], ["session_send"])

    def test_an_unrelated_principal_gets_neither(self):
        h = TwoRuntimes(self.tmp, grants={"oauth:test": {DEVICE: ["send"]}})
        other = h.principal(name="oauth:someone-else")
        with self.assertRaises(FleetRefusal):
            h.service.native_call(other, device_ref=DEVICE, native_tool="session_send",
                                  session_ref=SECOND_REF,
                                  arguments={"session": UUID, "message": "x"})

    def test_a_personal_device_grants_neither_runtime(self):
        h = TwoRuntimes(self.tmp, owner="personal", grants={
            "oauth:test": {DEVICE: ["send"], provider_key(DEVICE, SECOND): ["send"]}})
        for ref in (OWNER_REF, SECOND_REF):
            with self.subTest(ref=ref):
                with self.assertRaises(FleetRefusal):
                    h.service.native_call(h.principal(), device_ref=DEVICE,
                                          native_tool="session_send", session_ref=ref,
                                          arguments={"session": UUID, "message": "x"})


class OneRuntimeOfflineDoesNotAffectTheOtherTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_disabled_second_runtime_leaves_the_base_working(self):
        h = TwoRuntimes(self.tmp, second=True, enabled_second=False)
        # the base runtime is untouched
        self.assertEqual(h.service.get_session(h.principal(),
                                               SessionKey.parse(OWNER_REF))["served_by"],
                         "claude")
        # and asking for the disabled one is refused, not silently redirected
        with self.assertRaises(FleetRefusal) as caught:
            h.service.get_session(h.principal(), SessionKey.parse(SECOND_REF))
        self.assertEqual(caught.exception.code, UNKNOWN_DEVICE)

    def test_a_missing_second_binding_leaves_the_base_working(self):
        h = TwoRuntimes(self.tmp, second=False)
        self.assertEqual(h.service.get_session(h.principal(),
                                               SessionKey.parse(OWNER_REF))["served_by"],
                         "claude")

    def test_a_reference_and_a_runtime_that_disagree_are_refused(self):
        h = TwoRuntimes(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.native_call(h.principal(), device_ref=DEVICE,
                                  native_tool="sessions_list",
                                  session_ref=SECOND_REF, runtime="claude")
        self.assertEqual(caught.exception.code, "BAD_SESSION_REF")
        self.assertEqual(h.second.calls, [])


class TheServedSurfaceIsUnchangedTest(unittest.TestCase):
    """Neither gap closed here changes what a single-runtime device serves."""

    def test_the_tool_surface_is_still_thirty_three_with_the_legacy_nine(self):
        from fleet_mcp import cloudcli_native, tools
        names = [t.name for t in tools.SERVED_TOOLS]
        self.assertEqual(len(names), 33)
        self.assertEqual(len(cloudcli_native.NATIVE_TOOLS), 22)
        for nine in ("fleet_list_devices", "fleet_list_sessions", "fleet_get_session",
                     "fleet_read_session", "fleet_send_to_session", "fleet_get_run",
                     "fleet_status", "fleet_create_session", "fleet_interrupt_session"):
            self.assertIn(nine, names)

    def test_a_single_runtime_config_advertises_one_binding(self):
        with tempfile.TemporaryDirectory() as d:
            h = TwoRuntimes(Path(d), second=False)
            described = h.config.describe()["providers"]
            self.assertEqual(list(described), [DEVICE])
            self.assertEqual(described[DEVICE]["runtime"], "claude")


if __name__ == "__main__":
    unittest.main(verbosity=2)
