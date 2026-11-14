"""D-5: the OAuth issuer leaves the two signals the watchdog reads.

2026-11-04 the signing key was created anew while clients were registered;
every client token stopped verifying and nobody was told.
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fleet_mcp.oauth import OAuthError, TokenStore  # noqa: E402


def store(root):
    return TokenStore(root, access_ttl=3600, refresh_ttl=86400)


class TheKeyIsRegenerated(unittest.TestCase):
    def test_a_new_key_over_registered_clients_leaves_a_marker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "state.json").write_text(json.dumps({
                "clients": [{"client_id": "c1", "redirect_uris": ["http://x"],
                             "client_name": "Claude Code"}]}))
            s = store(root)
            self.assertEqual(len(s._clients), 1, "the fixture is in the real state schema")
            marker = json.loads((root / "key-regenerated.json").read_text())
            self.assertEqual(marker["registered_clients"], 1)
            self.assertNotIn("key", json.dumps(marker).lower().replace("registered", ""))

    def test_a_first_install_leaves_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            store(Path(d))
            self.assertFalse((Path(d) / "key-regenerated.json").exists())

    def test_an_existing_key_leaves_no_marker(self):
        with tempfile.TemporaryDirectory() as d:
            store(Path(d))
            store(Path(d))
            self.assertFalse((Path(d) / "key-regenerated.json").exists())


class RefusedTokensAreCounted(unittest.TestCase):
    def test_refusals_are_counted_by_reason_without_the_token(self):
        with tempfile.TemporaryDirectory() as d:
            s = store(Path(d))
            secret_looking = "aaaa.bbbb.cccc-not-a-real-token"
            for _ in range(3):
                with self.assertRaises(OAuthError):
                    s.verify_access_token(secret_looking, audience="x")
            s._auth_flushed = 0.0                    # force the throttled flush
            with self.assertRaises(OAuthError):
                s.verify_access_token(secret_looking, audience="x")
            text = (Path(d) / "auth-failures.json").read_text()
            doc = json.loads(text)
            self.assertEqual(doc["counts"]["signature does not verify"]["count"], 4)
            self.assertNotIn(secret_looking, text)
            self.assertNotIn("aaaa", text)


if __name__ == "__main__":
    unittest.main()
