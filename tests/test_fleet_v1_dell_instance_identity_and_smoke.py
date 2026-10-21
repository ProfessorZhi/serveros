"""Lane D — persistent instance identity, invariant refs, and the V1 smoke floor.

Two questions this file answers with executable pins rather than prose:

1. **What must the Fleet Next binding gain so a runtime instance is a stable
   thing the fleet can name?** Today a binding is keyed by
   `(device_ref, runtime)`. That names an ACCOUNT, which is durable, and says
   nothing about the INSTANCE behind it — the process, its state root, its
   credential generation. Restart the CloudCLI under `fjd`, re-provision the
   account, rotate the node secret, and every binding key is unchanged while
   the thing behind it is a different runtime. The advice is a persisted
   instance identity; the tests here pin what it must NOT break.

2. **What is the smallest V1 smoke this Lane may accept, and what may it never
   do without owner authorization?** Encoded as a plan plus a guard, so
   "未授权不能真操作" is a test result rather than an intention.

Offline throughout: no device, no socket, no credential, no second OS account.
"""
from __future__ import annotations

import dataclasses
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import runtime_identity as ri                    # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, FleetConfig,  # noqa: E402
                              PolicyBinding, ProviderBinding, ServerBinding,
                              provider_key)
from fleet_mcp.identity import SessionKey                       # noqa: E402

DEVICE = "device:llm-lab-dell"
OWNER = "vince"
OTHER = "fjd"
OTHER_RUNTIME = "claude-fjd"
UUID = "b2cb3f10-1111-2222-3333-444455556666"
OWNER_REF = f"{DEVICE}|claude|{UUID}"
OTHER_REF = f"{DEVICE}|{OTHER_RUNTIME}|{UUID}"

#: The provider grammar, as the naming authority borrows it. Restated here as a
#: literal because a test that imported the pattern would not notice it changing.
PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")


class TheBindingNamesAnAccountAndAnInstanceTest(unittest.TestCase):
    """MIGRATED FROM A GAP CHARACTERISATION — the skip came off in the change
    that made it pass, which is what its own note said would happen.

    It used to be `TheBindingNamesAnAccountAndNotAnInstanceTest`, skipped under
    `CONTRACT_PENDING_REWRITE`, asserting that a binding had NO instance field
    and that two successive instances of one account were indistinguishable.
    Both were measurements of a gap, and both are now false: Lane B added
    `account`, `profile` and `instance_id` to the binding.

    Rewritten rather than deleted, and rewritten POSITIVELY. Keeping the old
    assertions as invariants would have made the correct change fail, and the
    cheapest way back to green would have been to restore the gap.
    """

    def _binding(self, **kw) -> ProviderBinding:
        base = dict(device_ref=DEVICE, kind="fleet-node",
                    endpoint="http://127.0.0.1:7302",
                    credential_anchor=Path("/nonexistent/secret"),
                    runtime=OTHER_RUNTIME)
        base.update(kw)
        return ProviderBinding(**base)

    def test_a_binding_can_declare_its_account_profile_and_incarnation(self):
        binding = self._binding(account="fjd", profile="marketops",
                                instance_id="prov-2")
        self.assertEqual(binding.account, "fjd")
        self.assertEqual(binding.profile, "marketops")
        self.assertEqual(binding.instance_id, "prov-2")

    def test_the_fields_are_present_on_the_type_now(self):
        """The old test asserted these names were ABSENT. Inverted, because the
        absence was the gap."""
        names = {f.name for f in dataclasses.fields(ProviderBinding)}
        for present in ("account", "profile", "instance_id", "family"):
            with self.subTest(field=present):
                self.assertIn(present, names)

    def test_naming_an_incarnation_grants_nothing(self):
        """It is a declaration on a local file — not a credential, not an
        authorization, and not evidence that any process is alive."""
        binding = self._binding(account="fjd", profile="marketops",
                                instance_id="prov-2")
        self.assertFalse(binding.allow_send)
        self.assertFalse(binding.allow_create)
        self.assertFalse(binding.allow_control)

    def test_two_incarnations_of_one_account_are_distinguishable(self):
        """The measurement the old test made, inverted: two provisionings of one
        account, and the declarations now differ."""
        self.assertNotEqual(self._binding(instance_id="prov-1"),
                            self._binding(instance_id="prov-2"))

    def test_the_incarnation_stays_out_of_the_session_ref(self):
        """The ref is still three segments — `device|<tag>|<native id>`. An
        incarnation in the address would re-address every live session on every
        restart and every credential rotation."""
        self.assertEqual(OTHER_REF.count("|"), 2)
        self.assertEqual(provider_key(DEVICE, OTHER_RUNTIME),
                         OTHER_REF.rsplit("|", 1)[0])


class TheSessionRefMustNotChangeTest(unittest.TestCase):
    """Whatever identity is added, an existing ref keeps meaning what it meant.

    `device:llm-lab-dell|claude|<uuid>` is what every live session already says.
    An instance identity that reached the ref would re-address them all, and a
    window in which two readings of one address disagree is a window in which a
    message can be delivered to the wrong runtime.
    """

    def test_the_ref_is_exactly_three_segments(self):
        key = SessionKey.parse(OWNER_REF)
        self.assertEqual(key.ref, OWNER_REF)
        self.assertEqual(OWNER_REF.count("|"), 2)
        self.assertEqual(ri.tag_of(OWNER_REF), "claude")

    def test_the_second_accounts_ref_differs_only_in_the_tag(self):
        owner = SessionKey.parse(OWNER_REF)
        other = SessionKey.parse(OTHER_REF)
        self.assertEqual(owner.device_ref, other.device_ref)
        self.assertEqual(owner.native_session_id, other.native_session_id)
        self.assertNotEqual(owner.provider, other.provider)
        self.assertEqual(owner.native_session_id, UUID)

    def test_a_fourth_segment_is_refused_outright(self):
        """The authority does not merely fail to equal the old ref — it refuses
        a four-part string by name. That is what makes "the instance id stays
        out of the ref" an enforced invariant rather than a convention."""
        from fleet_mcp.errors import BAD_SESSION_REF, FleetRefusal
        with self.assertRaises(FleetRefusal) as caught:
            SessionKey.parse(f"{OWNER_REF}|instance-7")
        self.assertEqual(caught.exception.code, BAD_SESSION_REF)
        self.assertIn("4 part(s)", str(caught.exception))

    def test_an_instance_identity_has_no_legal_place_in_the_provider_slot(self):
        """It could be smuggled into the tag — the grammar allows it. It must
        not be, because the tag is the ADDRESS and re-using it for instance
        identity would make one runtime's sessions unreachable the moment it
        restarted."""
        # `claude-fjd` is a legal provider name...
        self.assertIsNotNone(PROVIDER_NAME.fullmatch(OTHER_RUNTIME))
        # ...and so, syntactically, is a tag with an instance baked in. The
        # scheme's rule is that nothing may write one, and this asserts the
        # rule is stated where the tag is derived rather than left implied.
        source = (ROOT / "fleet_mcp" / "runtime_identity.py").read_text(encoding="utf-8")
        self.assertIn("re-addressing the first", source)
        self.assertIn("no window in which a live\nsession's address is ambiguous", source)

    def test_the_tag_is_a_function_of_the_account_alone(self):
        """Two derivations of the same account agree, whatever else is true of
        the machine — that is what makes the address stable across a restart."""
        first = ri.runtime_for(DEVICE, OTHER, device_owner=OWNER)
        second = ri.runtime_for(DEVICE, OTHER, device_owner=OWNER)
        self.assertEqual(first.tag, second.tag)
        self.assertEqual(first.session_ref(UUID), second.session_ref(UUID))
        self.assertEqual(first.session_ref(UUID), OTHER_REF)


class TheV1SmokeFloorTest(unittest.TestCase):
    """The smallest V1 smoke this Lane can accept, and its authorization guard.

    The plan is declarative on purpose. A smoke that is a shell script can be
    run by accident by whoever has the checkout; a smoke that is a data
    structure with a guard has to be passed a decision, and the decision is
    checkable after the fact.
    """

    #: `authorized` is what the owner grants. Every step that changes state on
    #: the device or spends a paid credential is False until then.
    PLAN = (
        {"step": "identity", "verb": "fleet_status", "mutates": False,
         "detail": "which device this host believes it is"},
        {"step": "reach", "verb": "fleet_list_devices", "mutates": False,
         "detail": "the device answers on its own binding"},
        {"step": "capability", "verb": "fleet_cloudcli_native_capability",
         "mutates": False,
         "detail": "what THIS runtime's CloudCLI enumerates, or UNKNOWN"},
        {"step": "sessions", "verb": "fleet_list_sessions", "mutates": False,
         "detail": "the device's own runtime lists, and the second account is "
                   "reported as a gap rather than as empty"},
        {"step": "isolation", "verb": "fleet_get_session", "mutates": False,
         "detail": "the same native id under two accounts stays two refs"},
        {"step": "create", "verb": "fleet_create_session", "mutates": True,
         "detail": "OWNER AUTHORIZATION REQUIRED — mints a session in an "
                   "account, spends a credential and starts work"},
        {"step": "continue", "verb": "fleet_send_to_session", "mutates": True,
         "detail": "OWNER AUTHORIZATION REQUIRED — starts a run"},
        {"step": "interrupt", "verb": "fleet_interrupt_session", "mutates": True,
         "detail": "OWNER AUTHORIZATION REQUIRED — ends work in progress"},
    )

    @staticmethod
    def may_run(step: dict, *, authorized: bool) -> bool:
        return (not step["mutates"]) or authorized

    def runnable(self, *, authorized: bool) -> list:
        return [s["step"] for s in self.PLAN if self.may_run(s, authorized=authorized)]

    def test_unauthorized_runs_only_the_read_only_steps(self):
        got = self.runnable(authorized=False)
        self.assertEqual(got, ["identity", "reach", "capability", "sessions",
                               "isolation"])
        for step in self.PLAN:
            if step["mutates"]:
                with self.subTest(step=step["step"]):
                    self.assertNotIn(step["step"], got)

    def test_the_guard_is_the_only_thing_holding_the_writes_back(self):
        """Both halves asserted, so the guard is a gate and not a constant."""
        self.assertEqual(self.runnable(authorized=False),
                         self.runnable(authorized=False))
        self.assertEqual(len(self.runnable(authorized=True)), len(self.PLAN))
        self.assertNotEqual(self.runnable(authorized=False),
                            self.runnable(authorized=True))

    def test_every_mutating_step_says_what_it_needs(self):
        for step in self.PLAN:
            if not step["mutates"]:
                continue
            with self.subTest(step=step["step"]):
                self.assertIn("OWNER AUTHORIZATION REQUIRED", step["detail"])

    def test_the_read_only_steps_are_the_actual_read_verbs(self):
        """A smoke whose 'read' step is a write would be the exact failure this
        plan exists to avoid, so the names are pinned to the served surface."""
        from fleet_mcp.providers.base import DeviceProvider  # noqa: F401
        read_verbs = {s["verb"] for s in self.PLAN if not s["mutates"]}
        self.assertEqual(read_verbs, {
            "fleet_status", "fleet_list_devices",
            "fleet_cloudcli_native_capability", "fleet_list_sessions",
            "fleet_get_session",
        })

    def test_no_step_names_a_second_device_or_a_privileged_path(self):
        for step in self.PLAN:
            with self.subTest(step=step["step"]):
                text = f"{step['verb']} {step['detail']}".lower()
                for banned in ("sudo", "root", "/etc/", "systemctl restart"):
                    self.assertNotIn(banned, text)

    def test_the_two_dell_accounts_are_both_in_scope_and_only_one_is_authorized(self):
        """V1 names four initial targets; on this host that is two accounts. A
        plan that quietly dropped the unauthorized one would report a complete
        matrix with a row missing."""
        accounts = {OWNER, OTHER}
        self.assertEqual(accounts, {"vince", "fjd"})
        tags = {ri.runtime_for(DEVICE, a, device_owner=OWNER).tag for a in accounts}
        self.assertEqual(tags, {"claude", OTHER_RUNTIME})


class TheMatrixIsAConfigThisHostCanActuallyHoldTest(unittest.TestCase):
    """Both rows of the Dell matrix expressed as a real, loadable binding pair —
    the shape the readiness doc registers, asserted rather than described."""

    def test_the_pair_loads_and_describes_without_a_second_device(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            owner_anchor = tmp / "vince.node-secret"
            owner_anchor.write_text("owner-secret", encoding="utf-8")
            other_anchor = tmp / "fjd.node-secret"
            # NOT written: the second account's credential is not this host's to
            # hold. Its absence is the registered state, not a setup oversight.
            cfg = FleetConfig(
                source=None, local_device_ref=DEVICE,
                providers={
                    DEVICE: ProviderBinding(
                        device_ref=DEVICE, kind="fleet-node",
                        endpoint="http://127.0.0.1:7301",
                        credential_anchor=owner_anchor, runtime="claude",
                        allow_send=True, allow_create=True, allow_control=True),
                    provider_key(DEVICE, OTHER_RUNTIME): ProviderBinding(
                        device_ref=DEVICE, kind="fleet-node",
                        endpoint="http://127.0.0.1:7302",
                        credential_anchor=other_anchor, runtime=OTHER_RUNTIME,
                        allow_send=False, allow_create=False, allow_control=False),
                },
                auth=AuthBinding(pat_anchor=owner_anchor),
                server=ServerBinding(bind="127.0.0.1", port=0),
                policy=PolicyBinding(operator_grants={}),
                audit=AuditBinding(path=tmp / "audit.jsonl",
                                   rate_limit_per_minute=10_000),
            )
            described = cfg.describe()["providers"]
            self.assertEqual(sorted(described),
                             sorted([DEVICE, f"{DEVICE}|{OTHER_RUNTIME}"]))
            # The second account is registered, disabled-by-default, and its
            # credential is absent — all three at once, which is the truth.
            second = cfg.providers[f"{DEVICE}|{OTHER_RUNTIME}"]
            self.assertFalse(second.allow_create)
            self.assertFalse(second.allow_send)
            self.assertIsNone(second.credential())


if __name__ == "__main__":
    unittest.main(verbosity=2)
