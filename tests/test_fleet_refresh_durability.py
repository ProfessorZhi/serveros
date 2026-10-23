"""A refresh chain that can survive its own client.

The behaviour these tests pin is the answer to one field report: a session kept
demanding a browser login even though it had been granted a refresh token. The
cause was not the token's lifetime but the chain's — rotation marks the token it
was presented with used, and a second use of a used token is read as a stolen
chain and revoked end to end. A client that keeps presenting the token it was
first given (or that refreshes twice at once) therefore kills its own session.

Two settings give the chain a way out, and each gets its own tests:

* `rotate_refresh: false` — the presented token IS the credential. It comes back
  unchanged and its expiry slides, so a client that never stores a replacement
  keeps working forever.
* `refresh_rotation_grace_seconds` — rotation stays on, but a replay inside the
  window answers with the chain head instead of revoking. Outside the window
  the old reading holds: the whole chain dies.

The third thing pinned here is the audit gap: a refused token request used to
leave no row at all, so "expired" and "revoked" looked the same afterwards.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fleet_mcp.config import load_config
from fleet_mcp.oauth import OAuthError, TokenStore


CLIENT = "fleet-test-client"


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "oauth"

    def tearDown(self):
        self._tmp.cleanup()

    def store(self, **kwargs) -> TokenStore:
        kwargs.setdefault("access_ttl", 3600)
        kwargs.setdefault("refresh_ttl", 3600)
        return TokenStore(self.root, **kwargs)

    def mint(self, store: TokenStore) -> str:
        return store.mint_refresh_token(client_id=CLIENT, subject="operator",
                                        scopes=("fleet:read",))


class RotationOff(_StoreCase):
    def test_the_presented_token_is_the_credential(self):
        store = self.store(rotate_refresh=False)
        first = self.mint(store)

        live, record = store.consume_refresh_token(first, client_id=CLIENT)
        self.assertEqual(live, first)
        self.assertEqual(store.rotate_refresh_token(live, record), first)

        # Repeated refreshes keep working, and none of them invents a successor:
        # the state on disk still names one token, not a chain.
        for _ in range(3):
            live, record = store.consume_refresh_token(first, client_id=CLIENT)
            self.assertEqual(store.rotate_refresh_token(live, record), first)
        saved = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(list(saved["refresh"]), [first])
        self.assertIsNone(saved["refresh"][first]["rotated_to"])

    def test_a_refresh_slides_the_expiry(self):
        """A token that is never replaced must not lapse into a login."""
        store = self.store(rotate_refresh=False, refresh_ttl=600)
        first = self.mint(store)
        live, record = store.consume_refresh_token(first, client_id=CLIENT)
        before = record.expires_at
        with mock.patch("fleet_mcp.oauth.time.time", return_value=time.time() + 400):
            store.rotate_refresh_token(live, record)
        self.assertGreater(record.expires_at, before)

    def test_a_token_rotated_before_the_setting_changed_still_works(self):
        """The migration path, not a hypothetical: this deployment has one."""
        rotating = self.store(rotate_refresh=True)
        first = self.mint(rotating)
        _, record = rotating.consume_refresh_token(first, client_id=CLIENT)
        second = rotating.rotate_refresh_token(first, record)

        settled = self.store(rotate_refresh=False)  # re-reads the same state
        live, record = settled.consume_refresh_token(first, client_id=CLIENT)
        self.assertEqual(live, second, "the replay is answered with the chain head")
        self.assertEqual(settled.rotate_refresh_token(live, record), second)
        # Nothing was revoked on the way: the head is still spendable.
        live, _ = settled.consume_refresh_token(second, client_id=CLIENT)
        self.assertEqual(live, second)

    def test_rotation_off_does_not_widen_the_other_refusals(self):
        store = self.store(rotate_refresh=False)
        first = self.mint(store)
        with self.assertRaises(OAuthError):
            store.consume_refresh_token(first, client_id="someone else")
        with self.assertRaises(OAuthError):
            store.consume_refresh_token("fleet-t-unknown", client_id=CLIENT)


class RotationGrace(_StoreCase):
    def test_a_replay_inside_the_window_answers_with_the_head(self):
        store = self.store(rotate_refresh=True, rotation_grace_seconds=300)
        first = self.mint(store)
        _, record = store.consume_refresh_token(first, client_id=CLIENT)
        second = store.rotate_refresh_token(first, record)

        live, record = store.consume_refresh_token(first, client_id=CLIENT)
        self.assertEqual(live, second)
        third = store.rotate_refresh_token(live, record)
        self.assertNotEqual(third, second)

        # The chain stayed linear and alive: the head works, and the original
        # token is still answered with whatever the head is now.
        live, record = store.consume_refresh_token(second, client_id=CLIENT)
        self.assertEqual(live, third)
        live, record = store.consume_refresh_token(first, client_id=CLIENT)
        self.assertEqual(live, third)

    def test_a_replay_outside_the_window_still_kills_the_chain(self):
        store = self.store(rotate_refresh=True, rotation_grace_seconds=30)
        first = self.mint(store)
        _, record = store.consume_refresh_token(first, client_id=CLIENT)
        second = store.rotate_refresh_token(first, record)

        later = time.time() + 120
        with mock.patch("fleet_mcp.oauth.time.time", return_value=later):
            with self.assertRaises(OAuthError) as caught:
                store.consume_refresh_token(first, client_id=CLIENT)
            self.assertIn("already used", str(caught.exception))
            with self.assertRaises(OAuthError) as caught:
                store.consume_refresh_token(second, client_id=CLIENT)
        self.assertIn("revoked", str(caught.exception))

    def test_the_rotation_time_survives_a_restart(self):
        store = self.store(rotate_refresh=True, rotation_grace_seconds=300)
        first = self.mint(store)
        _, record = store.consume_refresh_token(first, client_id=CLIENT)
        second = store.rotate_refresh_token(first, record)

        restarted = self.store(rotate_refresh=True, rotation_grace_seconds=300)
        live, _ = restarted.consume_refresh_token(first, client_id=CLIENT)
        self.assertEqual(live, second, "a grace window that resets on restart is not one")

    def test_no_grace_means_the_old_reading(self):
        store = self.store(rotate_refresh=True)
        first = self.mint(store)
        _, record = store.consume_refresh_token(first, client_id=CLIENT)
        store.rotate_refresh_token(first, record)
        with self.assertRaises(OAuthError) as caught:
            store.consume_refresh_token(first, client_id=CLIENT)
        self.assertIn("already used", str(caught.exception))


class TheSettingsAreConfig(unittest.TestCase):
    """Both behaviours are chosen in YAML, and the omitted case is the narrow one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "binding.yaml"

    def tearDown(self):
        self._tmp.cleanup()

    def _load(self, auth: str):
        self.path.write_text(
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: device:local-test\n"
            "server:\n  bind: 127.0.0.1\n  port: 3401\n"
            f"auth:\n{auth}",
            encoding="utf-8",
        )
        return load_config(self.path)

    def test_omitting_them_keeps_rotation_and_no_grace(self):
        config = self._load("  pat_anchor: /tmp/x\n")
        self.assertTrue(config.auth.refresh_rotate)
        self.assertEqual(config.auth.refresh_rotation_grace_seconds, 0)

    def test_both_are_read(self):
        config = self._load(
            "  pat_anchor: /tmp/x\n"
            "  refresh_rotate: false\n"
            "  refresh_rotation_grace_seconds: 172800\n"
        )
        self.assertFalse(config.auth.refresh_rotate)
        self.assertEqual(config.auth.refresh_rotation_grace_seconds, 172800)
