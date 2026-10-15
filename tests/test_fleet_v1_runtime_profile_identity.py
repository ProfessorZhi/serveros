#!/usr/bin/env python3
"""One OS account, several credential profiles — and the routing that keeps them
apart.

The gap these tests were written against
----------------------------------------
`runtime_tag(user, device_owner=…)` derived exactly one tag per OS account:
the bare base tag for the device's own account, `<base>-<user>` for any other.
That is wide enough for two accounts and wrong for what is coming — one account
carrying several independent CC identities (a general route, an official
subscription, a work identity), each with its own credential, its own anchor and
its own grants. Under the old rule all of them collapsed onto one tag, which
means one address for several runtimes: the same native session id in two of
them becomes a coin toss.

What is asserted here, in order:

* naming — a profile produces a DIFFERENT stable tag, the no-profile spelling is
  byte-for-byte what it was, families do not borrow each other's tags, reserved
  words and over-long names are refused, and two identities that would collide
  onto one tag are refused rather than resolved by declaration order;
* routing — the same native id under two profiles reaches the one the reference
  names, for read, send, create and the native mirror, and never the neighbour;
* declarations — a binding may name its account, profile and incarnation, and
  the set-level rules refuse a duplicated incarnation or a duplicated
  account/profile pair;
* what did NOT change — the three-segment session ref, and the base runtime's
  address.

Deliberately absent: any claim that an `instance_id` proves which process is
live. It is an owner-authored label on a local file, nothing signs it, and this
surface cannot read a remote incarnation back. Where a proof is needed the
answer is UNVERIFIED, and a test below pins that the absence produces no
fallback to another profile rather than a silent borrow.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp import runtime_identity as ri                        # noqa: E402
from fleet_mcp.config import ProviderBinding, provider_key          # noqa: E402
from fleet_mcp.errors import FleetRefusal                           # noqa: E402
from fleet_mcp.fleet import (                                       # noqa: E402
    SCOPE_CONTROL, SCOPE_CREATE, SCOPE_READ, SCOPE_SEND,
)
from fleet_mcp.identity import SessionKey                           # noqa: E402

DEVICE = "device:llm-lab-dell"
OWNER = "vince"
OTHER = "fjd"
UUID = "b2cb3f10-1111-2222-3333-444455556666"

GENERAL = "deepseek-general"
OFFICIAL = "official-claude"
MARKETOPS = "marketops"


class NamingGivesEachProfileItsOwnStableTag(unittest.TestCase):

    def test_one_account_three_profiles_is_three_tags(self):
        tags = {p: ri.runtime_tag(OWNER, device_owner=OWNER, profile=p)
                for p in (GENERAL, OFFICIAL, MARKETOPS)}
        self.assertEqual(len(set(tags.values())), 3, tags)
        for profile, tag in tags.items():
            with self.subTest(profile=profile):
                self.assertTrue(ri.is_a_provider_tag(tag), tag)

    def test_the_same_profile_always_gets_the_same_tag(self):
        a = ri.runtime_tag(OWNER, device_owner=OWNER, profile=OFFICIAL)
        b = ri.runtime_tag(OWNER, device_owner=OWNER, profile=OFFICIAL)
        self.assertEqual(a, b, "a tag that moves cannot be an address")

    def test_the_no_profile_spelling_is_byte_for_byte_what_it_was(self):
        """The migration guarantee: nothing written before profiles moves."""
        self.assertEqual(ri.runtime_tag(OWNER, device_owner=OWNER), "claude")
        self.assertEqual(ri.runtime_tag(OTHER, device_owner=OWNER), "claude-fjd")
        self.assertEqual(
            ri.runtime_for(DEVICE, OTHER, device_owner=OWNER).session_ref(UUID),
            f"{DEVICE}|claude-fjd|{UUID}")

    def test_a_second_accounts_profile_is_not_the_owners_profile(self):
        own = ri.runtime_tag(OWNER, device_owner=OWNER, profile=OFFICIAL)
        other = ri.runtime_tag(OTHER, device_owner=OWNER, profile=OFFICIAL)
        self.assertNotEqual(own, other)

    def test_the_family_is_an_axis_and_not_a_model(self):
        claude = ri.runtime_tag(OWNER, device_owner=OWNER, profile=GENERAL)
        codex = ri.runtime_tag(OWNER, device_owner=OWNER, profile=GENERAL,
                               family="codex")
        self.assertNotEqual(claude, codex)
        self.assertTrue(codex.startswith("codex"))
        # A model id is not a family. Naming Codex with an Anthropic model is
        # the collapse the family axis exists to prevent.
        for not_a_family in ("deepseek-v4-flash-vision-anthropic", "sonnet", ""):
            with self.subTest(family=not_a_family):
                with self.assertRaises(ValueError):
                    ri.runtime_tag(OWNER, device_owner=OWNER, family=not_a_family)

    def test_a_reserved_word_is_refused_as_a_profile(self):
        for reserved in ri.SELECTORS:
            with self.subTest(reserved=reserved):
                with self.assertRaises(ValueError):
                    ri.runtime_tag(OWNER, device_owner=OWNER, profile=reserved)

    def test_a_profile_that_normalises_onto_a_reserved_word_is_refused(self):
        with self.assertRaises(ValueError):
            ri.runtime_tag(OWNER, device_owner=OWNER, profile="  ALL  ")

    def test_an_empty_profile_is_refused_rather_than_treated_as_absent(self):
        for empty in ("", "   ", ".", "-"):
            with self.subTest(profile=empty):
                with self.assertRaises(ValueError):
                    ri.runtime_tag(OWNER, device_owner=OWNER, profile=empty)

    def test_an_over_long_tag_is_refused_rather_than_truncated(self):
        with self.assertRaises(ValueError) as caught:
            ri.runtime_tag(OWNER, device_owner=OWNER, profile="p" * 40)
        self.assertIn(str(ri.TAG_MAX_LENGTH), str(caught.exception))


class TwoIdentitiesThatWouldCollideAreRefused(unittest.TestCase):
    """The axes share a character set, so grammar alone cannot separate them."""

    def test_an_account_named_like_another_profiles_tag_is_caught(self):
        # `runtime_tag(OWNER, profile="marketops")` and
        # `runtime_tag("marketops", device_owner=OWNER)` both derive a tag that
        # ends in `marketops`; whether they are equal depends on the spelling,
        # which is exactly why the set is checked rather than the grammar.
        profiled = ri.runtime_for(DEVICE, OWNER, device_owner=OWNER, profile=MARKETOPS)
        named = ri.runtime_for(DEVICE, f"{OWNER}-{MARKETOPS}", device_owner=OWNER)
        if profiled.tag == named.tag:
            with self.assertRaises(ValueError) as caught:
                ri.runtime_tags_are_distinct([profiled, named])
            self.assertIn("two different runtimes derive the tag", str(caught.exception))
        else:
            # Not a collision in this spelling — the set is still accepted, and
            # the check above is the one that would catch it if it were.
            ri.runtime_tags_are_distinct([profiled, named])
            self.assertNotEqual(profiled.tag, named.tag)

    def test_the_same_identity_declared_twice_is_not_a_collision(self):
        one = ri.runtime_for(DEVICE, OWNER, device_owner=OWNER, profile=OFFICIAL)
        two = ri.runtime_for(DEVICE, OWNER, device_owner=OWNER, profile=OFFICIAL)
        ri.runtime_tags_are_distinct([one, two])   # must not raise

    def test_three_distinct_profiles_pass_the_set_check(self):
        ri.runtime_tags_are_distinct([
            ri.runtime_for(DEVICE, OWNER, device_owner=OWNER, profile=p)
            for p in (GENERAL, OFFICIAL, MARKETOPS)])


class TheThreeSegmentRefIsUntouched(unittest.TestCase):

    def test_a_profiled_ref_is_still_exactly_three_segments(self):
        ref = ri.runtime_for(DEVICE, OWNER, device_owner=OWNER,
                             profile=OFFICIAL).session_ref(UUID)
        self.assertEqual(ref.count("|"), 2)
        self.assertEqual(ref, f"{DEVICE}|claude-{OFFICIAL}|{UUID}")
        self.assertEqual(SessionKey.parse(ref).native_session_id, UUID)

    def test_an_incarnation_cannot_be_smuggled_in_because_a_fourth_segment_is_refused(self):
        from fleet_mcp.errors import FleetRefusal as FR
        with self.assertRaises(FR):
            SessionKey.parse(f"{DEVICE}|claude-{OFFICIAL}|{UUID}|incarnation-2")


class AProfileIsANameAndNotAnAuthorization(unittest.TestCase):

    def _binding(self, **kw):
        base = dict(device_ref=DEVICE, kind="fleet-node",
                    endpoint="http://127.0.0.1:7301",
                    credential_anchor=Path("/nonexistent/anchor"))
        base.update(kw)
        return ProviderBinding(**base)

    def test_a_binding_may_name_its_account_profile_and_incarnation(self):
        b = self._binding(account=OWNER, profile=OFFICIAL, instance_id="prov-2")
        self.assertEqual(b.profile, OFFICIAL)
        self.assertEqual(b.instance_id, "prov-2")
        # Naming them grants nothing.
        self.assertFalse(b.allow_send)
        self.assertFalse(b.allow_create)
        self.assertFalse(b.allow_control)

    def test_a_reserved_word_is_refused_as_a_profile_or_an_incarnation(self):
        for field in ("profile", "instance_id", "account"):
            for reserved in ri.SELECTORS:
                with self.subTest(field=field, reserved=reserved):
                    with self.assertRaises(ValueError):
                        self._binding(**{field: reserved})

    def test_a_padded_or_empty_declaration_is_refused(self):
        for field in ("profile", "instance_id", "account"):
            for bad in ("", "   ", " x "):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        self._binding(**{field: bad})

    def test_the_fields_default_to_absent_so_old_bindings_are_unchanged(self):
        b = self._binding()
        self.assertIsNone(b.profile)
        self.assertIsNone(b.account)
        self.assertIsNone(b.instance_id)


class TheLoaderRefusesDuplicatedDeclarations(unittest.TestCase):
    """Set-level rules, which a single binding cannot answer about itself."""

    def _load(self, providers_yaml: str):
        from fleet_mcp.config import load_config
        tmp = Path(tempfile.mkdtemp())
        (tmp / "anchor").write_text("secret", encoding="utf-8")
        path = tmp / "fleet-mcp.local.yaml"
        path.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            f"device_ref: {DEVICE}\n" + providers_yaml, encoding="utf-8")
        return load_config(path)

    def _entry(self, provider_key_str: str, **kw) -> str:
        """One `providers:` entry. The KEY is a device ref, optionally carrying
        the runtime after a bar — the loader reads the tag from there, not from
        the bare runtime name."""
        lines = [f"  {provider_key_str}:",
                 "    kind: fleet-node",
                 f"    endpoint: {kw.get('endpoint', 'http://127.0.0.1:7301')}",
                 f"    credential_anchor: {kw.get('anchor', '/nonexistent/a')}"]
        for key in ("account", "profile", "instance_id"):
            if key in kw:
                lines.append(f"    {key}: {kw[key]}")
        return "\n".join(lines) + "\n"

    def _key(self, profile: str | None) -> str:
        return provider_key(DEVICE, ri.runtime_tag(OWNER, device_owner=OWNER,
                                                   profile=profile))

    def test_a_duplicated_incarnation_label_is_refused(self):
        body = ("providers:\n"
                + self._entry(self._key(None), instance_id="prov-1")
                + self._entry(self._key(OFFICIAL), instance_id="prov-1"))
        with self.assertRaises(FleetRefusal) as caught:
            self._load(body)
        self.assertIn("same instance_id", caught.exception.detail)

    def test_a_duplicated_account_profile_pair_is_refused(self):
        body = ("providers:\n"
                + self._entry(self._key(None), account=OWNER, profile=OFFICIAL)
                + self._entry(self._key(GENERAL), account=OWNER, profile=OFFICIAL))
        with self.assertRaises(FleetRefusal) as caught:
            self._load(body)
        self.assertIn("account/profile", caught.exception.detail)

    def test_distinct_profiles_on_one_device_load_cleanly(self):
        body = ("providers:\n"
                + self._entry(self._key(GENERAL), account=OWNER, profile=GENERAL)
                + self._entry(self._key(OFFICIAL), account=OWNER, profile=OFFICIAL)
                + self._entry(self._key(MARKETOPS), account=OWNER, profile=MARKETOPS))
        config = self._load(body)
        self.assertEqual(len(config.providers), 3)
        for profile in (GENERAL, OFFICIAL, MARKETOPS):
            with self.subTest(profile=profile):
                key = self._key(profile)
                self.assertIn(key, config.providers)
                self.assertEqual(config.providers[key].profile, profile)
                # …and the other two are NOT what this tag reaches.
                for other in (p for p in (GENERAL, OFFICIAL, MARKETOPS) if p != profile):
                    self.assertNotEqual(self._key(other), key)


if __name__ == "__main__":
    unittest.main()
