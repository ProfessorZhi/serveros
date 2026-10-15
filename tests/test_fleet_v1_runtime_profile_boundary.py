#!/usr/bin/env python3
"""An unbound runtime is UNREACHABLE, not UNAUTHORIZED — and the answer must not
confirm that a profile exists.

The defect these tests were written against
-------------------------------------------
`_may_send` and `_may_control` both asked the config for a binding and then
refused with `APPROVAL_REQUIRED` when it was absent:

    binding = self.config.provider_for(key.device_ref, key.provider)
    if binding is None or not binding.allow_send:
        raise refuse(APPROVAL_REQUIRED, ...)

Two things are wrong with that, and they are different in kind.

**It conflates unreachable with unauthorized.** The module already says these are
different answers, in `_require_provider`'s own words: "No binding is not 'not
authorized', it is 'not reachable', and the two send an operator to different
configuration." `_may_create` defers to `_require_provider` for exactly that
reason; the two session-keyed verbs did not.

**It confirms that an account exists.** `APPROVAL_REQUIRED` says "there is a
route here and you were not granted it" — which tells a caller probing a tag
that the profile behind it is real and merely withheld. A refusal that has to
say `UNKNOWN_DEVICE` says nothing about whether a profile for that tag was ever
configured; that is the answer for a caller who is not entitled to know.

`list_sessions` already settled this question the other way: an unbound runtime
is a GAP it reports (`devices_unreachable`), never a refusal. These tests make
the session-keyed verbs agree with it.

Where the positive control lives
--------------------------------
A runtime that IS bound and merely lacks `allow_send` must still answer
`APPROVAL_REQUIRED` — that is a real authorization fact about a real route, and
a fix that made both cases unreachable would be a different bug. It is asserted
here so the correction cannot over-reach.
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

from fleet_mcp.config import provider_key                       # noqa: E402
from fleet_mcp.errors import FleetRefusal                        # noqa: E402
from fleet_mcp.fleet import SCOPE_CONTROL, SCOPE_CREATE, SCOPE_SEND  # noqa: E402
from fleet_mcp.identity import SessionKey                        # noqa: E402

# The harness the routing tests already use for "one device, two runtimes".
# Imported rather than re-written: a second fixture free to drift from the first
# is how two suites come to disagree about what an unbound runtime is.
from test_fleet_multi_runtime_routing import (                   # noqa: E402
    DEVICE,
    SECOND,
    SECOND_REF,
    TwoRuntimes,
)

#: Phrases that assert a route EXISTS and was withheld. None of them may appear
#: in the answer for a runtime that was never bound, because together they are a
#: statement that the account is real.
AUTHORIZATION_PHRASES = ("not authorized", "allow_send", "granted", "grant")


class AnUnboundRuntimeIsUnreachable(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_send_to_an_unbound_runtime_is_unreachable_not_unauthorized(self):
        # `second=False` — no binding for `claude-fjd` exists at all.
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")
        self.assertNotEqual(caught.exception.code, "APPROVAL_REQUIRED")

    def test_control_on_an_unbound_runtime_is_unreachable_too(self):
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.interrupt_session(
                h.principal(SCOPE_CONTROL), SessionKey.parse(SECOND_REF))
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")

    def test_the_unreachable_answer_does_not_confirm_the_profile_exists(self):
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        detail = caught.exception.detail.lower()
        for phrase in AUTHORIZATION_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(
                    phrase, detail,
                    "an unbound runtime answered with the vocabulary of a "
                    "withheld grant, which tells the caller the account is real")

    def test_no_provider_was_reached_for_the_unbound_runtime(self):
        """The refusal happens before a route is used — there is no route."""
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal):
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        self.assertEqual(h.first.calls, [], "the bound runtime was not used instead")


class TheAuthorizationAnswerIsStillAvailable(unittest.TestCase):
    """The positive control: bound-but-withheld must not be swept up by the fix."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _bound_without_send(self) -> TwoRuntimes:
        h = TwoRuntimes(self.tmp)
        providers = dict(h.config.providers)
        providers[provider_key(DEVICE, SECOND)] = dataclasses.replace(
            providers[provider_key(DEVICE, SECOND)], allow_send=False)
        h.service.config = dataclasses.replace(h.config, providers=providers)
        h.service.providers[provider_key(DEVICE, SECOND)] = h.second
        return h

    def test_a_bound_runtime_without_allow_send_is_still_approval_required(self):
        h = self._bound_without_send()
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        self.assertEqual(caught.exception.code, "APPROVAL_REQUIRED")

    def test_the_send_refusal_names_send_and_not_control(self):
        """It used to say 'not authorized for control' in `_may_send` — the verb
        of the method it was copied from, not of the method that raised."""
        h = self._bound_without_send()
        with self.assertRaises(FleetRefusal) as caught:
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        detail = caught.exception.detail.lower()
        self.assertIn("send", detail)
        self.assertNotIn("control", detail)


class CreateAlreadyDrawsTheLineCorrectly(unittest.TestCase):
    """Regression guard. `_may_create` defers to `_require_provider`; this pins
    that the two session-keyed verbs now agree with it rather than diverging."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_create_and_send_give_the_same_class_for_the_same_unbound_runtime(self):
        h = TwoRuntimes(self.tmp, second=False)
        with self.assertRaises(FleetRefusal) as created:
            h.service.create_session(h.principal(SCOPE_CREATE), device=DEVICE,
                                     project="p-1", runtime=SECOND)
        with self.assertRaises(FleetRefusal) as sent:
            h.service.send_to_session(h.principal(), SessionKey.parse(SECOND_REF),
                                      message="hello")
        self.assertEqual(created.exception.code, "UNKNOWN_DEVICE")
        self.assertEqual(sent.exception.code, created.exception.code)


if __name__ == "__main__":
    unittest.main()
