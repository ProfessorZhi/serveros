"""One physical device, two OS accounts, two runtimes — the naming rule's edges.

The case this file is written against: a Dell A6000 where `vince` and `fjd` each
run their own CloudCLI under their own account. The failure to prevent is not a
crash. It is one account's session being addressed, read, stopped or approved
through the other account's runtime — and, on this surface, the only thing that
separates them is a provider tag inside a reference. So the tags are the thing
under test.

Nothing here touches a device, a credential or a running surface: this is the
naming rule, exercised as a pure function.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import node_protocol, runtime_identity          # noqa: E402
from fleet_mcp.errors import FleetRefusal                       # noqa: E402
from fleet_mcp.identity import SessionKey                       # noqa: E402

DEVICE = "device:llm-lab-dell"
OWNER = "vince"
SECOND = "fjd"
UUID = "b2cb3f10-1111-2222-3333-444455556666"


class TheOwnersRuntimeKeepsItsOldNameTest(unittest.TestCase):
    """Adding a second runtime must not re-address the first.

    `device:llm-lab-dell|claude|<uuid>` is what every existing session ref says.
    If the owner's tag had been re-qualified to `claude-vince`, every stored ref
    would have become a session that could no longer be found, and the window
    while that was being fixed is a window in which a live session's address is
    ambiguous. The unqualified tag is what makes this additive.
    """

    def test_the_owner_gets_the_unqualified_tag(self):
        self.assertEqual(runtime_identity.runtime_tag(OWNER, device_owner=OWNER),
                         runtime_identity.BASE_RUNTIME)

    def test_a_pre_existing_reference_is_unchanged(self):
        legacy = f"{DEVICE}|claude|{UUID}"
        self.assertEqual(
            runtime_identity.runtime_for(DEVICE, OWNER, device_owner=OWNER)
                           .session_ref(UUID),
            legacy)
        # ...and it still parses to the same three parts.
        key = SessionKey.parse(legacy)
        self.assertEqual((key.device_ref, key.provider, key.native_session_id),
                         (DEVICE, "claude", UUID))
        self.assertEqual(key.ref, legacy)

    def test_the_owner_is_recognised_whatever_the_whitespace(self):
        self.assertEqual(
            runtime_identity.runtime_for(DEVICE, "  vince ", device_owner="vince")
                           .tag,
            runtime_identity.BASE_RUNTIME)


class TwoRuntimesAreTwoNamesTest(unittest.TestCase):
    """The same native UUID under two runtimes is two different sessions."""

    def test_the_two_accounts_get_different_tags(self):
        first = runtime_identity.runtime_tag(OWNER, device_owner=OWNER)
        second = runtime_identity.runtime_tag(SECOND, device_owner=OWNER)
        self.assertEqual(first, "claude")
        self.assertEqual(second, "claude-fjd")
        self.assertNotEqual(first, second)

    def test_the_same_uuid_is_two_distinct_references(self):
        one = runtime_identity.runtime_for(DEVICE, OWNER, device_owner=OWNER)
        two = runtime_identity.runtime_for(DEVICE, SECOND, device_owner=OWNER)
        self.assertNotEqual(one.session_ref(UUID), two.session_ref(UUID))
        self.assertNotEqual(SessionKey.parse(one.session_ref(UUID)),
                            SessionKey.parse(two.session_ref(UUID)))

    def test_one_tag_cannot_be_read_back_as_the_other(self):
        """The property the whole scheme rests on: a reference names the runtime
        it was written for, and parsing it does not silently yield the other."""
        second_ref = runtime_identity.runtime_for(
            DEVICE, SECOND, device_owner=OWNER).session_ref(UUID)
        self.assertEqual(SessionKey.parse(second_ref).provider, "claude-fjd")
        self.assertIs(runtime_identity.is_the_base_runtime(
            SessionKey.parse(second_ref).provider), False)
        self.assertIs(runtime_identity.is_the_base_runtime("claude"), True)

    def test_the_tag_is_part_of_the_identity_not_a_label_on_it(self):
        """Two keys that differ only by tag are not equal and do not hash alike,
        so nothing downstream can collapse them by ignoring the provider."""
        a = SessionKey(DEVICE, "claude", UUID)
        b = SessionKey(DEVICE, "claude-fjd", UUID)
        self.assertNotEqual(a, b)
        self.assertEqual(len({a, b}), 2)

    def test_distinct_accounts_get_distinct_tags(self):
        tags = {runtime_identity.runtime_tag(u, device_owner=OWNER)
                for u in (OWNER, "fjd", "kai", "lab2")}
        self.assertEqual(len(tags), 4)


class TheTagCannotBeSmuggledTest(unittest.TestCase):
    """A tag is written by the host from an account name, never by a caller."""

    def test_a_user_that_reduces_to_nothing_is_refused(self):
        for bad in ("---", "...", "-.-", "   "):
            with self.subTest(user=bad):
                with self.assertRaises(ValueError):
                    runtime_identity.runtime_tag(bad, device_owner=OWNER)

    def test_a_user_cannot_carry_the_reference_separator(self):
        """`fjd|claude|mine` must not become a tag that reads as three parts."""
        tag = runtime_identity.runtime_tag("fjd|claude|mine", device_owner=OWNER)
        self.assertNotIn(node_protocol.__class__ and "|", tag)
        # and the reference it produces still has exactly three parts
        ref = runtime_identity.runtime_for(DEVICE, "fjd|claude|mine",
                                           device_owner=OWNER).session_ref(UUID)
        self.assertEqual(len(SessionKey.parse(ref).native_session_id.split("|")), 1)

    def test_a_user_cannot_carry_uppercase_or_shell_metacharacters(self):
        tag = runtime_identity.runtime_tag("FJD; rm -rf /", device_owner=OWNER)
        self.assertEqual(tag, tag.lower())
        for ch in "; /$`&'\"\\\n":
            self.assertNotIn(ch, tag)

    def test_an_unnamed_account_is_refused(self):
        for bad in ("", "   ", None, 7):
            with self.subTest(user=bad):
                with self.assertRaises(ValueError):
                    runtime_identity.runtime_tag(bad, device_owner=OWNER)

    def test_the_device_owner_must_be_named_not_assumed(self):
        for bad in ("", "   ", None):
            with self.subTest(owner=bad):
                with self.assertRaises(ValueError):
                    runtime_identity.runtime_tag(SECOND, device_owner=bad)

    def test_a_second_device_identity_is_refused(self):
        """One physical machine is ONE authenticated device. Two runtimes on it
        are told apart by their tag; inventing a second device_ref for the same
        hardware would give one machine two identities to keep in step."""
        with self.assertRaises(ValueError) as caught:
            runtime_identity.runtime_for("llm-lab-dell", SECOND, device_owner=OWNER)
        self.assertIn("typed device ref", str(caught.exception))

    def test_the_tag_grammar_comes_from_the_authority_not_a_copy(self):
        """A tag this module produces is one `SessionKey` accepts, and an
        account whose tag would not be is REFUSED rather than truncated: a
        truncated name is a tag that reads as a different account."""
        tag = runtime_identity.runtime_tag("fjd", device_owner=OWNER)
        SessionKey(DEVICE, tag, UUID)              # accepted by the authority
        with self.assertRaises(ValueError):
            runtime_identity.runtime_tag("a" * 40, device_owner=OWNER)


class NamingIsIndependentOfAnythingBeingAliveTest(unittest.TestCase):
    """The rule is pure: it neither reads nor requires a route, a registry or a
    process. One runtime being offline cannot re-address the other's sessions,
    which is the failure a registry lookup at naming time would introduce."""

    def test_naming_reads_no_binding_and_no_registry(self):
        import inspect
        source = inspect.getsource(runtime_identity)
        for forbidden in ("load_registry", "config", "socket", "subprocess",
                          "providers", "requests", "urllib"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_ref_is_the_same_whether_or_not_anything_is_reachable(self):
        one = runtime_identity.runtime_for(DEVICE, SECOND, device_owner=OWNER)
        two = runtime_identity.runtime_for(DEVICE, SECOND, device_owner=OWNER)
        self.assertEqual(one.session_ref(UUID), two.session_ref(UUID))

    def test_naming_is_a_function_of_its_arguments_only(self):
        first = runtime_identity.runtime_for(DEVICE, SECOND, device_owner=OWNER)
        second = runtime_identity.runtime_for(DEVICE, SECOND, device_owner=OWNER)
        self.assertEqual(first, second)


class TheIsolationContractIsOnTheDeviceTest(unittest.TestCase):
    """The host must not claim an isolation it cannot check.

    Three of the four facts that make two runtimes isolated are properties of the
    machine, not of this surface: this host cannot see another account's home,
    credential, or state root, and it must not report them as verified. What is
    asserted here is that the contract says so, and that the host's own share is
    named as the host's.
    """

    def test_every_fact_is_named_with_an_owner(self):
        facts = {f["fact"]: f for f in runtime_identity.ISOLATION_CONTRACT}
        self.assertEqual(set(facts), {"os_account", "cloudcli_credential",
                                      "state_root", "node_secret"})
        for name, row in facts.items():
            with self.subTest(fact=name):
                self.assertTrue(row["required"].strip())
                self.assertTrue(row["owner"].strip())

    def test_the_host_is_not_named_as_the_owner_of_what_it_cannot_see(self):
        for row in runtime_identity.ISOLATION_CONTRACT:
            if row["fact"] in ("os_account", "cloudcli_credential", "state_root"):
                self.assertEqual(row["owner"], "device",
                                 f"{row['fact']} is not this host's to verify")

    def test_the_signature_does_not_yet_carry_the_runtime(self):
        """A CHARACTERIZATION of the gap, pinned so that closing it is a
        deliberate edit to this assertion and to the design note.

        `node_protocol.base_string` takes a `device_ref` and no runtime, so two
        runtimes on one device sign and verify identically: a holder of the
        device's node secret can present a request for either runtime, and the
        device side decides which one it was. With one runtime per device that
        was the same statement; with two accounts on one machine it is the
        isolation the `node_secret` row above cannot yet deliver. See
        `docs/fleet-multi-runtime-design.md`, gap G1.
        """
        args = dict(method="POST", path="/v1/native/call", body=b"{}",
                    timestamp="1700000000", nonce="a" * 32)
        self.assertEqual(node_protocol.base_string(device_ref=DEVICE, **args),
                         node_protocol.base_string(device_ref=DEVICE, **args))
        import inspect
        self.assertEqual(
            list(inspect.signature(node_protocol.base_string).parameters),
            ["method", "path", "body", "device_ref", "timestamp", "nonce"])


class TheReferenceRoundTripsTest(unittest.TestCase):
    def test_a_second_runtime_reference_round_trips(self):
        ref = runtime_identity.runtime_for(DEVICE, SECOND,
                                           device_owner=OWNER).session_ref(UUID)
        self.assertEqual(SessionKey.parse(ref).ref, ref)
        self.assertEqual(runtime_identity.tag_of(ref), "claude-fjd")

    def test_a_malformed_reference_is_still_refused_by_the_authority(self):
        for bad in ("", "device:x|claude", "device:x|claude|a|b", "x|claude|a"):
            with self.subTest(ref=bad):
                with self.assertRaises((FleetRefusal, ValueError)):
                    SessionKey.parse(bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
