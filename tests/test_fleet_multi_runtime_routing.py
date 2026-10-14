"""Two runtimes on one physical device: routing, isolation, and the refusals.

The case: a Dell A6000 where `vince` and `fjd` each run their own CloudCLI under
their own OS account. One `device_ref` (one authenticated machine), two
runtimes, two endpoints, two credentials, two node secrets.

Everything here is a fake transport. No device, no credential value and no
socket is involved: what is under test is WHICH binding a call reaches, and
which calls are refused, given a session reference and a principal.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import node_protocol as np                       # noqa: E402
from fleet_mcp import runtime_identity                           # noqa: E402
from fleet_mcp import tools as fleet_tools                      # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,   # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.errors import (BAD_REQUEST, PROVIDER_UNREACHABLE,   # noqa: E402
                              UNKNOWN_DEVICE, FleetRefusal, refuse)
from fleet_mcp.fleet import (SCOPE_READ, SCOPE_SEND, FleetService,   # noqa: E402
                             Principal)
from fleet_mcp.identity import SessionKey                        # noqa: E402
from fleet_mcp.providers.base import SessionSummary               # noqa: E402

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
    """A device that records what it was asked and which endpoint it is.

    `native_ids` is what this runtime will report, and the SAME id under two
    runtimes is the case the whole scheme exists for — so the default is one
    shared id and the two runtimes therefore look, to a careless listing, like
    two rows that ought to be collapsed. They are not, and that is asserted.
    """

    kind = "fleet-node"

    def __init__(self, ref: str, runtime: str, *, native_ids=(UUID,)):
        self.device_ref = ref
        self.runtime = runtime
        self.native_ids = tuple(native_ids)
        self.answers = True
        self.calls: list = []

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        return {"session_ref": key.ref, "served_by": self.runtime}

    def native_call(self, tool, arguments, *, session_ref=None):
        self.calls.append((tool, arguments))
        return {"ok": True, "tool": tool}

    def list_sessions(self, *, limit, project=None, state=None):
        self.calls.append(("list_sessions", limit, project, state))
        if not self.answers:
            raise refuse(PROVIDER_UNREACHABLE, f"{self.runtime} did not answer")
        return [self._summary(native_id) for native_id in self.native_ids[:limit]]

    def _summary(self, native_id: str) -> SessionSummary:
        return SessionSummary(
            key=SessionKey(self.device_ref, self.runtime, native_id),
            title=f"{self.runtime}:{native_id}",
            classification="MANAGED_IDLE",
            running=False,
            resident_host=False,
            host_state=None,
            lifecycle_mode=None,
            project_id=None,
            last_activity={"iso": "2026-10-14T00:00:00Z"},
        )

    def health(self):
        raise AssertionError("not used here")


class TwoRuntimes:
    """One device, one or two bindings, and the fake providers behind them."""

    def __init__(self, tmp: Path, *, owner: str = "company", second: bool = True,
                 grants=None, enabled_second: bool = True, first: bool = True,
                 first_ids=(UUID,), second_ids=(UUID,)):
        self.tmp = tmp
        first_anchor = tmp / "vince.node-secret"
        first_anchor.write_text("vince-secret", encoding="utf-8")
        second_anchor = tmp / "fjd.node-secret"
        second_anchor.write_text("fjd-secret", encoding="utf-8")
        self.registry = _registry(tmp, owner)

        providers = {}
        if first:
            providers[DEVICE] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7301", credential_anchor=first_anchor,
                runtime="claude", allow_send=True, allow_control=True)
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

        self.first = _Provider(DEVICE, "claude", native_ids=first_ids) if first else None
        self.second = _Provider(DEVICE, SECOND, native_ids=second_ids) if second else None
        instances = {}
        if first:
            instances[provider_key(DEVICE, "claude")] = self.first
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

    def rows(self, response: dict, runtime: str | None = None) -> list[dict]:
        """The rows one runtime's listing contributed, in returned order."""
        return [r for r in response["sessions"] if runtime is None or r["runtime"] == runtime]


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


class ListingWalksTheDeviceAndTheRuntimeTest(unittest.TestCase):
    """`fleet_list_sessions` walks `device × runtime`, not `device`.

    The failure this class exists against: an operator asks for everything on a
    machine that serves two runtimes and gets one of them, with nothing in the
    answer saying so. A listing that is silently one runtime short is worse than
    one that is visibly short, because the second is a fact and the first is a
    fact wearing the clothes of a complete answer.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_default_is_the_devices_own_runtime_and_nothing_else(self):
        """Every caller written before a device could serve two. The default is
        not a new behaviour with a compatibility story — it is the old behaviour
        with a name, and this pins that the second runtime is NOT swept in."""
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal())
        self.assertEqual(got["total"], 1)
        self.assertEqual([r["runtime"] for r in got["sessions"]], ["claude"])
        self.assertEqual(got["runtimes_queried"], [DEVICE])
        self.assertEqual(h.second.calls, [], "the second runtime was not asked")

    def test_base_spelled_out_is_the_same_as_omitting_it(self):
        h = TwoRuntimes(self.tmp)
        omitted = h.service.list_sessions(h.principal())
        spelled = h.service.list_sessions(h.principal(), runtime="base")
        self.assertEqual(omitted, spelled)

    def test_all_lists_every_bound_runtime_and_asks_each_one(self):
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal(), runtime="all")
        self.assertEqual(got["runtimes_queried"], [DEVICE, provider_key(DEVICE, SECOND)])
        self.assertEqual(got["sessions_per_runtime"],
                         {DEVICE: 1, provider_key(DEVICE, SECOND): 1})
        self.assertEqual([c[0] for c in h.first.calls], ["list_sessions"])
        self.assertEqual([c[0] for c in h.second.calls], ["list_sessions"])

    def test_the_two_spellings_of_all_select_the_same_set(self):
        h = TwoRuntimes(self.tmp)
        self.assertEqual(h.service.list_sessions(h.principal(), runtime="all"),
                         h.service.list_sessions(h.principal(), runtime="*"))

    def test_a_precise_tag_lists_exactly_that_runtime(self):
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal(), runtime=SECOND)
        self.assertEqual(got["total"], 1)
        self.assertEqual(got["runtimes_queried"], [provider_key(DEVICE, SECOND)])
        self.assertEqual(h.first.calls, [], "the base runtime was not asked")

    def test_every_row_says_which_runtime_it_came_from(self):
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal(), runtime="all")
        for row in got["sessions"]:
            with self.subTest(ref=row["session_ref"]):
                # `provider` in a session ref IS the runtime tag; `runtime` is the
                # same fact under a name that cannot be mistaken for the CloudCLI
                # agent family that `create_session`'s `provider` means.
                self.assertEqual(row["runtime"], row["provider"])
                self.assertEqual(SessionKey.parse(row["session_ref"]).provider,
                                 row["runtime"])

    def test_the_limit_is_per_runtime_not_per_device(self):
        """A two-runtime machine is two listings, so a `limit` of 1 is not a
        device-wide budget of 1. Reporting the per-runtime counts is what lets a
        caller tell a full page from an empty runtime."""
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal(), runtime="all", limit=5)
        self.assertEqual([c[1] for c in h.first.calls], [5])
        self.assertEqual([c[1] for c in h.second.calls], [5])
        self.assertEqual(got["sessions_per_runtime"],
                         {DEVICE: 1, provider_key(DEVICE, SECOND): 1})

    def test_a_filter_reaches_every_runtime(self):
        h = TwoRuntimes(self.tmp)
        h.service.list_sessions(h.principal(), runtime="all", project="p1", state="idle")
        for provider in (h.first, h.second):
            with self.subTest(runtime=provider.runtime):
                self.assertEqual(provider.calls, [("list_sessions", 20, "p1", "idle")])


class TheSameUuidUnderTwoRuntimesIsNotADuplicateTest(unittest.TestCase):
    """Two rows sharing a native id are two sessions, and must not be collapsed.

    Both fake runtimes report the SAME native id, which is the case the triple
    exists for. A listing that deduplicated on the native id — or on the title,
    or on anything but the triple — would drop a live session and report the
    result as complete.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_both_rows_come_back_and_they_are_different_sessions(self):
        h = TwoRuntimes(self.tmp)
        got = h.service.list_sessions(h.principal(), runtime="all")
        self.assertEqual(got["total"], 2)
        self.assertEqual({r["session_ref"] for r in got["sessions"]}, {OWNER_REF, SECOND_REF})
        self.assertEqual({r["native_session_id"] for r in got["sessions"]}, {UUID})
        self.assertEqual(len({r["session_ref"] for r in got["sessions"]}), 2)

    def test_each_row_is_controlled_against_its_own_runtime(self):
        """Cross-UID, fail-closed. A principal granted `send` on the device's own
        runtime holds nothing on the second: the grant is looked up under the
        second runtime's own key, finds nothing, and the row says so rather than
        inheriting the grant beside it."""
        h = TwoRuntimes(self.tmp, grants={"oauth:test": {DEVICE: ["send"]}})
        got = h.service.list_sessions(h.principal(), runtime="all")
        by_ref = {r["session_ref"]: r for r in got["sessions"]}
        self.assertTrue(by_ref[OWNER_REF]["control_allowed"]["send_allowed"])
        self.assertFalse(by_ref[SECOND_REF]["control_allowed"]["send_allowed"])
        self.assertEqual(by_ref[SECOND_REF]["control_allowed"]["operator_grant_verbs"], [])

    def test_a_grant_on_the_second_runtime_does_not_admit_the_first(self):
        h = TwoRuntimes(self.tmp, grants={
            "oauth:test": {provider_key(DEVICE, SECOND): ["send"]}})
        got = h.service.list_sessions(h.principal(), runtime="all")
        by_ref = {r["session_ref"]: r for r in got["sessions"]}
        self.assertFalse(by_ref[OWNER_REF]["control_allowed"]["send_allowed"])
        self.assertTrue(by_ref[SECOND_REF]["control_allowed"]["send_allowed"])

    def test_a_personal_devices_rows_are_admitted_on_neither_runtime(self):
        h = TwoRuntimes(self.tmp, owner="personal", grants={
            "oauth:test": {DEVICE: ["send"], provider_key(DEVICE, SECOND): ["send"]}})
        got = h.service.list_sessions(h.principal(), runtime="all")
        for row in got["sessions"]:
            with self.subTest(ref=row["session_ref"]):
                self.assertFalse(row["control_allowed"]["send_allowed"])


class OneUnreachableRuntimeIsAGapNotAnAbsenceTest(unittest.TestCase):
    """Partial results, stated as partial.

    A runtime that did not answer and a runtime with nothing to report are
    different facts with different remedies, and a listing that returned `[]` for
    both would make the first look like the second.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_silent_second_runtime_still_returns_the_first_ones_rows(self):
        h = TwoRuntimes(self.tmp)
        h.second.answers = False
        got = h.service.list_sessions(h.principal(), runtime="all")
        self.assertEqual([r["runtime"] for r in got["sessions"]], ["claude"])
        self.assertEqual(got["total"], 1)
        self.assertEqual(len(got["devices_unreachable"]), 1)
        self.assertEqual(got["devices_unreachable"][0]["runtime"], SECOND)
        self.assertEqual(got["devices_unreachable"][0]["code"], PROVIDER_UNREACHABLE)

    def test_the_gap_names_the_runtime_and_not_only_the_device(self):
        """On a two-runtime machine 'the device is unreachable' cannot be true:
        one runtime answered. An entry naming only the device would make the two
        facts indistinguishable, which is the whole failure mode here."""
        h = TwoRuntimes(self.tmp, second=True, enabled_second=False)
        got = h.service.list_sessions(h.principal(), runtime="all")
        gap = got["devices_unreachable"][0]
        self.assertEqual(gap["device_ref"], DEVICE)
        self.assertEqual(gap["runtime"], SECOND)
        self.assertEqual(gap["provider_key"], provider_key(DEVICE, SECOND))
        self.assertEqual(gap["provider_key"], f"{DEVICE}|{SECOND}")
        self.assertIn("bound", gap["detail"])

    def test_a_disabled_second_runtime_does_not_make_the_listing_a_refusal(self):
        h = TwoRuntimes(self.tmp, second=True, enabled_second=False)
        got = h.service.list_sessions(h.principal(), runtime="all")
        self.assertEqual(got["total"], 1)
        self.assertEqual(got["sessions"][0]["runtime"], "claude")

    def test_a_legal_tag_with_nothing_behind_it_is_a_gap_not_a_refusal(self):
        h = TwoRuntimes(self.tmp, second=False)
        got = h.service.list_sessions(h.principal(), runtime=SECOND)
        self.assertEqual(got["sessions"], [])
        self.assertEqual([g["runtime"] for g in got["devices_unreachable"]], [SECOND])
        self.assertEqual(got["runtimes_queried"], [provider_key(DEVICE, SECOND)])

    def test_the_default_does_not_fall_back_to_the_only_runtime_there_is(self):
        """A machine whose only binding is the second runtime. The default asks
        for the device's OWN runtime, finds no route, and says so — it does not
        quietly answer with the runtime that does exist."""
        h = TwoRuntimes(self.tmp, first=False, second=True)
        got = h.service.list_sessions(h.principal())
        self.assertEqual(got["sessions"], [])
        self.assertEqual([g["runtime"] for g in got["devices_unreachable"]], ["claude"])
        self.assertEqual(got["runtimes_queried"], [DEVICE])

    def test_the_audit_record_carries_the_runtime_too(self):
        h = TwoRuntimes(self.tmp, second=True, enabled_second=False)
        h.service.list_sessions(h.principal(), runtime="all")
        records = [json.loads(line) for line in
                   (self.tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
        listing = [r for r in records if r.get("event") == "fleet.list_sessions"][-1]
        self.assertEqual(listing["runtimes"], 2)
        self.assertEqual(listing["unreachable"], [provider_key(DEVICE, SECOND)])


class TheSelectorIsNotATagTest(unittest.TestCase):
    """`all` is a legal provider name, so 'the selectors are not tags' cannot be
    left to nobody having tried it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_the_selectors_are_reserved_in_the_naming_authority(self):
        self.assertEqual(runtime_identity.SELECTORS, ("all", "*", "base"))
        for s in runtime_identity.SELECTORS:
            with self.subTest(selector=s):
                self.assertTrue(runtime_identity.is_a_selector(s))
        for tag in ("claude", "claude-fjd", "claude-all", "all-fjd"):
            with self.subTest(tag=tag):
                self.assertFalse(runtime_identity.is_a_selector(tag))

    def test_a_binding_cannot_be_filed_under_a_selector(self):
        from fleet_mcp.config import load_config
        for selector in ("all", "base"):
            with self.subTest(selector=selector):
                path = self.tmp / f"config-{selector}.yaml"
                path.write_text(
                    "providers:\n"
                    f"  device:llm-lab-dell|{selector}:\n"
                    "    kind: fleet-node\n"
                    "    endpoint: http://127.0.0.1:7302\n"
                    "    credential_anchor: /dev/null\n",
                    encoding="utf-8")
                with self.assertRaises(FleetRefusal) as caught:
                    load_config(path)
                self.assertEqual(caught.exception.code, BAD_REQUEST)

    def test_an_illegal_runtime_name_is_refused_rather_than_searched_for(self):
        h = TwoRuntimes(self.tmp)
        for bad in ("FJD", "claude fjd", "-fjd", "9fjd", ""):
            with self.subTest(runtime=bad):
                with self.assertRaises(FleetRefusal) as caught:
                    h.service.list_sessions(h.principal(), runtime=bad)
                self.assertEqual(caught.exception.code, BAD_REQUEST)
        self.assertEqual(h.first.calls, [])

    def test_a_selector_never_reaches_a_provider(self):
        h = TwoRuntimes(self.tmp)
        h.service.list_sessions(h.principal(), runtime="all")
        for provider in (h.first, h.second):
            for call in provider.calls:
                with self.subTest(runtime=provider.runtime):
                    self.assertNotIn(call[0], runtime_identity.SELECTORS)


class TheToolSurfaceCarriesTheSelectorTest(unittest.TestCase):
    """The parameter has to be reachable from the schema or it does not exist."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _tool(self):
        return next(t for t in fleet_tools.SERVED_TOOLS
                    if t.name == "fleet_list_sessions")

    def test_runtime_is_declared_and_optional(self):
        schema = self._tool().input_schema
        self.assertIn("runtime", schema["properties"])
        self.assertNotIn("runtime", schema.get("required", []))
        self.assertIs(schema["additionalProperties"], False)

    def test_the_schema_does_not_call_the_runtime_a_provider_family(self):
        described = self._tool().input_schema["properties"]["runtime"]["description"]
        self.assertIn("runtime", described)
        self.assertIn("agent family", described)

    def _call(self, h: TwoRuntimes, arguments: dict):
        result = fleet_tools.call_tool(
            fleet_tools.ToolContext(service=h.service, principal=h.principal()),
            "fleet_list_sessions", arguments)
        self.assertIs(result["isError"], False, result.get("structuredContent"))
        return result["structuredContent"]

    def test_the_handler_forwards_it(self):
        both = self._call(TwoRuntimes(self.tmp), {"runtime": "all"})
        self.assertEqual(both["total"], 2)
        self.assertEqual(sorted(both["sessions_per_runtime"]),
                         sorted([DEVICE, provider_key(DEVICE, SECOND)]))

    def test_the_default_ask_still_reaches_only_the_base_runtime(self):
        """Called with no `runtime` at all, which is how every caller that
        predates this parameter calls it."""
        h = TwoRuntimes(self.tmp)
        legacy = self._call(h, {})
        self.assertEqual(legacy["total"], 1)
        self.assertEqual([r["runtime"] for r in legacy["sessions"]], ["claude"])
        self.assertEqual(h.second.calls, [])

    def test_a_bad_selector_comes_back_as_a_typed_refusal_not_a_traceback(self):
        h = TwoRuntimes(self.tmp)
        result = fleet_tools.call_tool(
            fleet_tools.ToolContext(service=h.service, principal=h.principal()),
            "fleet_list_sessions", {"runtime": "FJD"})
        self.assertIs(result["isError"], True)
        self.assertEqual(result["structuredContent"]["code"], BAD_REQUEST)


if __name__ == "__main__":
    unittest.main(verbosity=2)
