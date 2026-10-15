#!/usr/bin/env python3
"""`fleet_create_session` and the runtime axis: which account did it mint in?

The bug this file exists for is not a crash. `SessionKey`'s middle component is
the RUNTIME tag — `list_sessions` says so in as many words, and every read,
send and control path resolves it that way — but `create_session` composed the
ref from the CloudCLI AGENT FAMILY instead. On a device whose second account was
bound as `claude-fjd`, a session created there came back addressed as
`device:<id>|claude|<native id>`, which is the BASE account's address. So the
very next `read_session` on the ref a caller had just been handed resolved to
the account beside the one that created it: same machine, different credential,
and nothing in the ref to say so.

Everything below is therefore about one question — *does the address a create
returns reach the account that created it* — asked from several directions, plus
the negatives that keep the answer from being "yes because everything falls
back to base".

The other three paths of the same defect are asserted too: `allow_create` is read
from the chosen runtime's binding rather than the device's first one, the
idempotency scope names the runtime so one key cannot replay across accounts, and
an unbound runtime is refused rather than redirected.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# The harness itself, imported the same way `test_core_freeze` imports
# `loader_graph`: a sibling test module is a source file, not a package member.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    CreateGrant,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
    provider_key,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (  # noqa: E402
    SCOPE_CREATE,
    SCOPE_READ,
    FleetService,
    Principal,
)
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402
from fleet_mcp.runtime_identity import BASE_RUNTIME  # noqa: E402

# The harness is the one the routing tests already use for "one device, two
# runtimes", imported rather than copied: a second fixture free to drift from
# the first is how two tests come to disagree about what a runtime is.
from test_fleet_multi_runtime_routing import (  # noqa: E402
    DEVICE,
    SECOND,
    UUID,
    _Provider,
    _registry,
)

PROJECT = "proj-a"
OTHER_PROJECT = "proj-b"
OWNER_ID = "native-owner-0001"
SECOND_ID = "native-second-0001"


class _CreatingProvider(_Provider):
    """A runtime that can also mint, and names what it minted per runtime."""

    def __init__(self, ref: str, runtime: str, *, native_id: str, **kw):
        super().__init__(ref, runtime, **kw)
        self._create_id = native_id

    def create(self, *, project, message=None, provider=None, model=None,
               lifecycle_mode=None, permission_mode=None):
        self.calls.append(("create", {"project": project, "provider": provider,
                                      "runtime": self.runtime}))
        return {"sessionId": self._create_id, "projectId": project}


class TwoAccounts:
    """One physical machine, two credential instances, each with its own binding.

    `allow_create` is per BINDING, which is the point: a machine that authorized
    creation in one account has not authorized it in the account beside it, and
    a fixture that set one flag for both could not tell those apart.
    """

    def __init__(self, tmp: Path, *, first_create: bool = True,
                 second_create: bool = True, second: bool = True):
        self.tmp = tmp
        first_anchor = tmp / "vince.node-secret"
        second_anchor = tmp / "fjd.node-secret"
        first_anchor.write_text("vince-secret", encoding="utf-8")
        second_anchor.write_text("fjd-secret", encoding="utf-8")
        self.registry_path = _registry(tmp, "company")

        providers = {
            provider_key(DEVICE, BASE_RUNTIME): ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7401", credential_anchor=first_anchor,
                runtime=BASE_RUNTIME, allow_send=True, allow_create=first_create),
        }
        if second:
            providers[provider_key(DEVICE, SECOND)] = ProviderBinding(
                device_ref=DEVICE, kind="fleet-node",
                endpoint="http://127.0.0.1:7402", credential_anchor=second_anchor,
                runtime=SECOND, allow_send=True, allow_create=second_create)

        self.config = FleetConfig(
            source=None, local_device_ref=DEVICE, providers=providers,
            auth=AuthBinding(pat_anchor=first_anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                create_allow={PROJECT: CreateGrant(), OTHER_PROJECT: CreateGrant()}),
            audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )

        # The SAME native session id from both runtimes, on purpose: that is the
        # case the runtime exists to disambiguate, and a fixture with distinct
        # ids would pass even if the address ignored the runtime entirely.
        self.owner_provider = _CreatingProvider(
            DEVICE, BASE_RUNTIME, native_id=OWNER_ID)
        self.second_provider = _CreatingProvider(
            DEVICE, SECOND, native_id=OWNER_ID) if second else None

        instances = {provider_key(DEVICE, BASE_RUNTIME): self.owner_provider}
        if second:
            instances[provider_key(DEVICE, SECOND)] = self.second_provider
        self.service = FleetService(
            self.config, registry=load_registry(self.registry_path), providers=instances)

    def principal(self) -> Principal:
        return Principal(name="oauth:test",
                         scopes=frozenset((SCOPE_READ, SCOPE_CREATE)),
                         credential_kind="oauth")

    def create(self, project: str = PROJECT, **kw):
        return self.service.create_session(self.principal(), device=DEVICE,
                                           project=project, **kw)


class TheAddressReachesTheAccountThatCreatedIt(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_default_runtime_is_byte_identical_to_before(self):
        """A caller written before a device could serve two must not move."""
        h = TwoAccounts(self.tmp)
        out = h.create()
        self.assertEqual(out["session_ref"], f"{DEVICE}|{BASE_RUNTIME}|{OWNER_ID}")
        self.assertEqual(SessionKey.parse(out["session_ref"]).provider, BASE_RUNTIME)
        self.assertEqual([c[0] for c in h.owner_provider.calls], ["create"])
        self.assertEqual(h.second_provider.calls, [])

    def test_the_second_account_is_addressed_by_its_own_tag(self):
        h = TwoAccounts(self.tmp)
        out = h.create(runtime=SECOND)
        self.assertEqual(out["session_ref"], f"{DEVICE}|{SECOND}|{OWNER_ID}")
        self.assertEqual([c[0] for c in h.second_provider.calls], ["create"])
        self.assertEqual(h.owner_provider.calls, [],
                         "the base account must not be asked for a session the "
                         "caller asked the second account to mint")

    def test_the_returned_ref_resolves_back_to_the_runtime_that_minted_it(self):
        """The end-to-end form of the defect, and the one that used to fail.

        Before the fix the ref came back tagged `claude`, so this read resolved
        to the base binding and was served by the base account.
        """
        h = TwoAccounts(self.tmp)
        ref = h.create(runtime=SECOND)["session_ref"]
        served = h.service.get_session(h.principal(), SessionKey.parse(ref))
        self.assertEqual(served["served_by"], SECOND)

    def test_two_configurations_in_one_account_are_two_runtimes(self):
        """Same device, same OS user, two credential instances — two addresses."""
        h = TwoAccounts(self.tmp)
        one = h.create()["session_ref"]
        two = h.create(runtime=SECOND)["session_ref"]
        self.assertNotEqual(one, two)
        self.assertEqual(SessionKey.parse(one).native_session_id,
                         SessionKey.parse(two).native_session_id,
                         "the fixture shares one native id, so only the runtime "
                         "can be what tells these apart")
        self.assertEqual(h.service.get_session(h.principal(), SessionKey.parse(one))["served_by"],
                         BASE_RUNTIME)
        self.assertEqual(h.service.get_session(h.principal(), SessionKey.parse(two))["served_by"],
                         SECOND)

    def test_the_agent_family_does_not_move_the_address(self):
        """`provider` is the family; it must not leak into the ref's runtime slot."""
        h = TwoAccounts(self.tmp)
        out = h.create(provider="codex")
        self.assertEqual(SessionKey.parse(out["session_ref"]).provider, BASE_RUNTIME)
        # …and the family still reaches the provider, where it means something.
        self.assertEqual(h.owner_provider.calls[0][1]["provider"], "codex")

    def test_the_family_travels_under_the_selected_runtime(self):
        h = TwoAccounts(self.tmp)
        out = h.create(runtime=SECOND, provider="codex")
        self.assertEqual(SessionKey.parse(out["session_ref"]).provider, SECOND)
        self.assertEqual(h.second_provider.calls[0][1]["provider"], "codex")
        self.assertEqual(h.owner_provider.calls, [])


class OneKeyCannotReplayAcrossAccounts(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_same_key_on_two_runtimes_mints_two_sessions(self):
        h = TwoAccounts(self.tmp)
        first = h.create(runtime=BASE_RUNTIME, idempotency_key="k-1")
        second = h.create(runtime=SECOND, idempotency_key="k-1")
        self.assertFalse(second["idempotent_replay"],
                         "the key was spent in another account; replaying it "
                         "here would hand back a session this account never made")
        self.assertEqual(first["session_ref"], f"{DEVICE}|{BASE_RUNTIME}|{OWNER_ID}")
        self.assertEqual(second["session_ref"], f"{DEVICE}|{SECOND}|{OWNER_ID}")
        self.assertEqual([c[0] for c in h.owner_provider.calls], ["create"])
        self.assertEqual([c[0] for c in h.second_provider.calls], ["create"])

    def test_the_same_key_within_one_runtime_still_replays(self):
        """The control. Naming the runtime must not break idempotency itself."""
        h = TwoAccounts(self.tmp)
        first = h.create(runtime=SECOND, idempotency_key="k-2")
        again = h.create(runtime=SECOND, idempotency_key="k-2")
        self.assertTrue(again["idempotent_replay"])
        self.assertEqual(again["session_ref"], first["session_ref"])
        self.assertEqual([c[0] for c in h.second_provider.calls], ["create"],
                         "a replay must not call the device a second time")

    def test_the_same_key_on_two_projects_still_mints_two_sessions(self):
        """The pre-existing axis, kept honest by the change to the scope."""
        h = TwoAccounts(self.tmp)
        h.create(project=PROJECT, idempotency_key="k-3")
        other = h.service.create_session(h.principal(), device=DEVICE,
                                         project=OTHER_PROJECT, idempotency_key="k-3")
        self.assertFalse(other["idempotent_replay"])


class AnUnknownOrUnauthorizedRuntimeIsRefused(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_an_unbound_runtime_is_refused_and_reaches_no_provider(self):
        h = TwoAccounts(self.tmp)
        with self.assertRaises(FleetRefusal) as caught:
            h.create(runtime="claude-not-bound")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")
        self.assertEqual(h.owner_provider.calls, [])
        self.assertEqual(h.second_provider.calls, [],
                         "an unbound runtime must not fall back to a bound one")

    def test_a_runtime_without_allow_create_is_refused_while_the_other_works(self):
        h = TwoAccounts(self.tmp, second_create=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.create(runtime=SECOND)
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")
        self.assertEqual(h.second_provider.calls, [])
        # The account that DID authorize creation is unaffected: the flag is
        # read per binding, not once for the device.
        self.assertEqual(h.create()["session_ref"], f"{DEVICE}|{BASE_RUNTIME}|{OWNER_ID}")

    def test_the_base_account_can_be_refused_while_the_second_works(self):
        """The same reading, from the other side — so neither is the default."""
        h = TwoAccounts(self.tmp, first_create=False)
        with self.assertRaises(FleetRefusal):
            h.create()
        self.assertEqual(h.second_provider.calls, [])
        self.assertEqual(h.create(runtime=SECOND)["session_ref"],
                         f"{DEVICE}|{SECOND}|{OWNER_ID}")

    def test_an_unlisted_project_is_refused_on_either_runtime(self):
        h = TwoAccounts(self.tmp)
        for runtime in (BASE_RUNTIME, SECOND):
            with self.subTest(runtime=runtime):
                with self.assertRaises(FleetRefusal):
                    h.create(runtime=runtime, project="proj-not-allowed")


if __name__ == "__main__":
    unittest.main()
