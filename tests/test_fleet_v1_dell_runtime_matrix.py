"""Lane D — the Dell A6000 runtime matrix, as this host is allowed to know it.

One physical device, two OS accounts. What this file pins is the boundary
between the parts of that matrix the Fleet surface OWNS and the parts it may
only DECLARE:

  * the runtime TAG is derived, by the device's own owner rule, and this host
    can prove it;
  * the four ISOLATION facts are the DEVICE's to arrange. This host cannot
    verify a single one of them by looking inwards, and must not pretend to;
  * the credential owner of an account this host does not run under is
    ``UNKNOWN`` — a statement about evidence, not a placeholder for "probably
    fine".

Everything here is offline. No device, no socket, no credential value and no
second OS account is involved, and no test in this file reads any path outside
its own ``tmp_path``.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import cloudcli_native as cn                     # noqa: E402
from fleet_mcp import runtime_identity as ri                    # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.identity import SessionKey                       # noqa: E402

DEVICE = "device:llm-lab-dell"
OWNER_ACCOUNT = "vince"
OTHER_ACCOUNT = "fjd"
OTHER_RUNTIME = "claude-fjd"
UUID = "b2cb3f10-1111-2222-3333-444455556666"


def _binding(tmp: Path, runtime: str, account: str, **over) -> ProviderBinding:
    """One account's binding.

    The anchor is a PATH this host owns; the credential's *value* is never read
    here, and for the second account there is no file at the path at all — which
    is the honest representation of a credential this host cannot see.
    """
    anchor = tmp / f"{account}.node-secret"
    if account == OWNER_ACCOUNT:
        anchor.write_text("owner-secret", encoding="utf-8")
    kwargs = dict(device_ref=DEVICE, kind="fleet-node",
                  endpoint=f"http://127.0.0.1:{7300 + len(runtime)}",
                  credential_anchor=anchor, runtime=runtime)
    kwargs.update(over)
    return ProviderBinding(**kwargs)


def _config(tmp: Path, providers: dict) -> FleetConfig:
    return FleetConfig(
        source=None, local_device_ref=DEVICE, providers=providers,
        auth=AuthBinding(pat_anchor=tmp / "owner.node-secret"),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(operator_grants={}),
        audit=AuditBinding(path=tmp / "audit.jsonl", rate_limit_per_minute=10_000),
    )


class TheTagIsDerivedFromTheDeviceNotTheCallerTest(unittest.TestCase):
    """`claude` for the device's own account, `claude-<user>` for every other."""

    def test_the_two_dell_accounts_get_the_two_expected_tags(self):
        first = ri.runtime_for(DEVICE, OWNER_ACCOUNT, device_owner=OWNER_ACCOUNT)
        second = ri.runtime_for(DEVICE, OTHER_ACCOUNT, device_owner=OWNER_ACCOUNT)
        self.assertEqual(first.tag, "claude")
        self.assertEqual(second.tag, OTHER_RUNTIME)
        self.assertTrue(ri.is_the_base_runtime(first.tag))
        self.assertFalse(ri.is_the_base_runtime(second.tag))

    def test_the_device_owner_is_an_input_not_an_assumption(self):
        """Naming no owner is refused rather than defaulted to the caller: a
        defaulted owner is how the second runtime would silently BECOME the
        first one, and the first one's sessions would be re-addressed."""
        with self.assertRaises(ValueError):
            ri.runtime_tag(OTHER_ACCOUNT, device_owner="")

    def test_the_tag_of_an_existing_reference_parses_back(self):
        ref = ri.runtime_for(DEVICE, OTHER_ACCOUNT,
                             device_owner=OWNER_ACCOUNT).session_ref(UUID)
        self.assertEqual(ref, f"{DEVICE}|{OTHER_RUNTIME}|{UUID}")
        self.assertEqual(ri.tag_of(ref), OTHER_RUNTIME)

    def test_the_layout_matches_the_binding_key_the_config_uses(self):
        """The naming module and the config loader must agree on the key, or a
        runtime is addressable by one and unreachable by the other."""
        tag = ri.runtime_for(DEVICE, OTHER_ACCOUNT, device_owner=OWNER_ACCOUNT).tag
        self.assertEqual(provider_key(DEVICE, tag), f"{DEVICE}|{tag}")
        self.assertEqual(provider_key(DEVICE), DEVICE)


class TheCredentialOwnerIsUnknownWhereThisHostHasNoProofTest(unittest.TestCase):
    """The fact this Lane exists to keep honest.

    A binding for the second account is a DECLARATION by whoever wrote the
    config. It is not evidence that the account holds a credential, that the
    credential works, or that the account exists. The surface has exactly one
    honest way to say that, and it is `UNKNOWN`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_runtime_nobody_read_is_unknown_rather_than_complete(self):
        snap = cn.capability_snapshot(device_ref=DEVICE)
        self.assertEqual(snap["state"], "UNKNOWN")
        self.assertIsNone(snap["observed_count"])
        self.assertEqual(snap["tools"], {})
        # Every mirrored verb is unsupported-by-device until the device says
        # otherwise — NOT assumed present.
        self.assertEqual(snap["unsupported_by_device"], sorted(cn.NATIVE_BY_NAME))

    def test_an_unread_runtime_still_reports_the_count_it_recorded(self):
        """`recorded_count` is what THIS BUILD mirrors, and it is reported even
        when nothing was read — as a fact about the build, never as a claim
        about the device."""
        snap = cn.capability_snapshot(device_ref=DEVICE, reason="no credential here")
        self.assertEqual(snap["recorded_count"], len(cn.NATIVE_TOOLS))
        self.assertEqual(snap["reason"], "no credential here")

    def test_the_two_dell_bindings_name_different_anchors(self):
        providers = {
            DEVICE: _binding(self.tmp, "claude", OWNER_ACCOUNT),
            provider_key(DEVICE, OTHER_RUNTIME): _binding(
                self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT),
        }
        cfg = _config(self.tmp, providers)
        first = cfg.providers[DEVICE]
        second = cfg.providers[provider_key(DEVICE, OTHER_RUNTIME)]
        self.assertNotEqual(first.credential_anchor, second.credential_anchor)
        self.assertTrue(first.credential_anchor.exists())

    def test_a_binding_for_another_account_does_not_invent_a_credential(self):
        """Reading a binding's credential is reading a FILE. For an account
        whose home this host cannot enter that file is simply not there, and the
        answer is `None` — never the credential of the account beside it."""
        providers = {
            DEVICE: _binding(self.tmp, "claude", OWNER_ACCOUNT),
            provider_key(DEVICE, OTHER_RUNTIME): _binding(
                self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT, allow_send=True),
        }
        cfg = _config(self.tmp, providers)
        first = cfg.providers[DEVICE]
        second = cfg.providers[provider_key(DEVICE, OTHER_RUNTIME)]
        self.assertFalse(second.credential_anchor.exists())
        self.assertIsNone(second.credential())
        # ...and, decisively, it is not the owner's.
        self.assertIsNotNone(first.credential())
        self.assertNotEqual(second.credential(), first.credential())

    def test_an_unreadable_anchor_is_none_rather_than_an_error_with_a_value(self):
        """A directory where a credential file should be is an OSError, and the
        surface answers it the same way: no credential. There is no branch in
        which a failed read yields somebody else's secret."""
        (self.tmp / "a-directory").mkdir()
        binding = _binding(self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT)
        binding = ProviderBinding(
            device_ref=DEVICE, kind="fleet-node",
            endpoint="http://127.0.0.1:7399",
            credential_anchor=self.tmp / "a-directory", runtime=OTHER_RUNTIME)
        self.assertIsNone(binding.credential())


class TheIsolationFactsAreTheDevicesToArrangeTest(unittest.TestCase):
    """What this host may claim about isolation, and what it may not."""

    def test_the_contract_names_four_facts_and_an_owner_for_each(self):
        facts = {entry["fact"] for entry in ri.ISOLATION_CONTRACT}
        self.assertEqual(facts, {"os_account", "cloudcli_credential",
                                 "state_root", "node_secret"})
        for entry in ri.ISOLATION_CONTRACT:
            with self.subTest(fact=entry["fact"]):
                self.assertTrue(entry["owner"].startswith("device"))

    def test_the_host_owns_one_obligation_and_it_is_routing(self):
        """Three of the four are the device's. The host's own job is the fourth:
        route by the tag it was given, and refuse a tag it was not given."""
        host_side = [e for e in ri.ISOLATION_CONTRACT if "host" in e["owner"]]
        self.assertEqual([e["fact"] for e in host_side], ["node_secret"])

    def test_a_tag_is_a_name_and_the_module_says_so(self):
        """The guard against over-claiming, asserted against the module's own
        text: a tag authenticates nothing."""
        source = (ROOT / "fleet_mcp" / "runtime_identity.py").read_text(encoding="utf-8")
        self.assertIn("It authenticates nothing", source)
        self.assertIn("a claim, not a proof", source)


class TheRuntimeTagIsNotTheAgentFamilyTest(unittest.TestCase):
    """Two different axes that share the word `claude`.

    `claude-fjd` is a RUNTIME (which account on which machine). `claude` as
    `create_session(provider=...)` is a FAMILY (which agent product). A caller
    that conflated them would ask for the account named "fjd" by asking for an
    agent, and be answered by whichever one the lookup reached first.
    """

    def test_the_family_vocabulary_is_closed_and_does_not_contain_the_tag(self):
        from fleet_mcp.runtime_create_policy import FAMILIES
        self.assertEqual(FAMILIES, frozenset({"claude", "codex"}))
        self.assertNotIn(OTHER_RUNTIME, FAMILIES)

    def test_a_second_accounts_tag_is_a_legal_provider_name(self):
        """It has to be addressable — that is what makes it a runtime."""
        key = SessionKey(DEVICE, OTHER_RUNTIME, UUID)
        self.assertEqual(key.provider, OTHER_RUNTIME)

    def test_the_profile_kinds_keep_the_two_accounts_apart(self):
        """A profile kind is a credential instance, and the same machine may
        carry one of each without either standing in for the other."""
        from fleet_mcp.runtime_create_policy import PROFILE_KINDS
        for kind in ("cloudcli-deepseek", "official-claude", "marketops-claude", "codex"):
            with self.subTest(kind=kind):
                self.assertIn(kind, PROFILE_KINDS)


class AnAccountNameIsNotAPrivilegeClaimTest(unittest.TestCase):
    """A UID is an attribution, not an authority.

    The upstream FJ production host carries an `admin` route label, and an
    account on a shared machine may be named anything at all. Neither fact is
    read by this surface, and neither grants a scope: authority arrives from a
    grant, on a named runtime and project, or it does not arrive.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_binding_field_carries_a_privilege(self):
        """A binding says WHERE an account is reached. If it could also say how
        much that account is worth, the config file would be an authority."""
        binding = _binding(self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT)
        for banned in ("admin", "root", "sudo", "is_admin", "privilege",
                       "superuser", "role"):
            with self.subTest(field=banned):
                self.assertFalse(hasattr(binding, banned))

    def test_an_ownerish_account_name_grants_nothing_the_other_does_not(self):
        for account in (OWNER_ACCOUNT, OTHER_ACCOUNT, "admin", "root"):
            with self.subTest(account=account):
                ident = ri.runtime_for(DEVICE, account, device_owner=OWNER_ACCOUNT)
                self.assertEqual(ident.user, account)
                self.assertTrue(ident.tag.startswith("claude"))

    def test_a_tag_of_admin_is_still_only_a_tag(self):
        tag = ri.runtime_tag("admin", device_owner=OWNER_ACCOUNT)
        self.assertEqual(tag, "claude-admin")
        self.assertFalse(ri.is_a_selector(tag))
        self.assertNotEqual(tag, ri.BASE_RUNTIME)

    def test_the_isolation_contract_requires_no_privileged_account(self):
        """Isolation is arranged by ACCOUNTS, not by privilege. A contract that
        needed root to be true would be a contract this host could not keep —
        and the two accounts on this device are both unprivileged.

        Checked as whole phrases, not substrings: `state root` is a directory
        and the word `root` inside it is not a claim about a superuser.
        """
        text = " ".join(e["required"] for e in ri.ISOLATION_CONTRACT).lower()
        for phrase in ("sudo", "superuser", "root user", "as root",
                       "administrator", "run as root"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)


class TheMatrixCarriesItsGrantsPerRuntimeTest(unittest.TestCase):
    """Registration is Device x account x runtime x family x credential x
    project x (create/send/control) — and the grant dimension is per runtime,
    never per device."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _cfg(self) -> FleetConfig:
        return _config(self.tmp, {
            DEVICE: _binding(self.tmp, "claude", OWNER_ACCOUNT,
                             allow_send=True, allow_create=True, allow_control=True),
            provider_key(DEVICE, OTHER_RUNTIME): _binding(
                self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT,
                allow_send=True, allow_create=False, allow_control=False),
        })

    def test_the_two_runtimes_hold_different_grants(self):
        cfg = self._cfg()
        first = cfg.provider_for(DEVICE, "claude")
        second = cfg.provider_for(DEVICE, OTHER_RUNTIME)
        self.assertTrue(first.allow_create)
        self.assertFalse(second.allow_create)
        self.assertTrue(second.allow_send)

    def test_the_default_for_a_binding_is_deny(self):
        bare = _binding(self.tmp, OTHER_RUNTIME, OTHER_ACCOUNT)
        self.assertFalse(bare.allow_send)
        self.assertFalse(bare.allow_create)
        self.assertFalse(bare.allow_control)

    def test_describe_reports_both_bindings_and_their_runtimes(self):
        described = self._cfg().describe()["providers"]
        self.assertEqual(sorted(described),
                         sorted([DEVICE, f"{DEVICE}|{OTHER_RUNTIME}"]))
        self.assertEqual(described[DEVICE]["runtime"], "claude")
        self.assertEqual(described[f"{DEVICE}|{OTHER_RUNTIME}"]["runtime"],
                         OTHER_RUNTIME)

    def test_the_matrix_has_no_room_for_a_second_device_identity(self):
        """One physical machine is ONE authenticated device. The account
        dimension rides in the provider tag; it must never mint a second
        `device:` ref, or two accounts would become two machines."""
        providers = self._cfg().providers
        self.assertEqual({b.device_ref for b in providers.values()}, {DEVICE})
        for key, binding in providers.items():
            with self.subTest(key=key):
                self.assertTrue(binding.device_ref.startswith("device:"))
                self.assertNotIn("|", binding.device_ref)


if __name__ == "__main__":
    unittest.main(verbosity=2)
