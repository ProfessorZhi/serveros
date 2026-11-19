"""Commanders beyond ChatGPT/Claude/coding agents: Grok, Gemini, Muse.

They speak MCP but cannot load the Skills, may send the 2025-11-25 protocol
header, and may name themselves by a client-metadata URL instead of registering.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import guide  # noqa: E402
from fleet_mcp.oauth import OAuthError, TokenStore, fetch_client_metadata  # noqa: E402
from fleet_mcp.server import PREFERRED_PROTOCOL_VERSION, SUPPORTED_PROTOCOL_VERSIONS  # noqa: E402

URL = "https://grok.example/.well-known/oauth-client.json"


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Protocol(unittest.TestCase):
    def test_2025_11_25_is_accepted_and_the_fallback_is_unchanged(self):
        self.assertIn("2025-11-25", SUPPORTED_PROTOCOL_VERSIONS)
        self.assertEqual(PREFERRED_PROTOCOL_VERSION, "2025-06-18")


class MetadataClients(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TokenStore(Path(self.tmp.name), access_ttl=3600, refresh_ttl=86400)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_url_client_is_fetched_once_and_kept(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return {"client_id": url, "client_name": "Grok",
                    "redirect_uris": ["https://grok.example/callback"]}
        self.store.metadata_fetcher = fetch
        client = self.store.client(URL)
        self.assertEqual((client.client_name, client.redirect_uris),
                         ("Grok", ("https://grok.example/callback",)))
        self.store.client(URL)
        self.assertEqual(calls, [URL])

    def test_a_document_that_names_another_id_or_a_bad_redirect_is_refused(self):
        for doc in ({"client_id": "https://other/", "redirect_uris": ["https://x/cb"]},
                    {"client_id": URL, "redirect_uris": ["http://evil.example/cb"]},
                    {"client_id": URL, "redirect_uris": []},
                    {"client_id": URL, "redirect_uris": ["https://x/cb"],
                     "token_endpoint_auth_method": "client_secret_basic"}):
            self.store.metadata_fetcher = lambda url, doc=doc: doc
            with self.assertRaises(OAuthError):
                self.store.client(URL)

    def test_an_unknown_plain_id_is_still_unknown(self):
        with self.assertRaises(OAuthError):
            self.store.client("fleet-nope")


class TheFetchIsGuarded(unittest.TestCase):
    def ok(self, request, timeout):
        return Answer(json.dumps({"client_id": URL}).encode())

    def test_only_public_https_and_small_json(self):
        public = lambda host: {"93.184.216.34"}
        self.assertEqual(fetch_client_metadata(URL, resolve=public, opener=self.ok)["client_id"], URL)
        for url, resolve in (("http://grok.example/x", public),
                             ("https://user:pw@grok.example/x", public),
                             (URL, lambda h: {"127.0.0.1"}),
                             (URL, lambda h: {"10.0.0.5"}),
                             (URL, lambda h: {"169.254.169.254"}),
                             (URL, lambda h: set())):
            with self.assertRaises(OAuthError):
                fetch_client_metadata(url, resolve=resolve, opener=self.ok)
        big = lambda request, timeout: Answer(b"{" + b" " * 6000 + b"}")
        with self.assertRaises(OAuthError):
            fetch_client_metadata(URL, resolve=public, opener=big)

    def test_the_server_says_it_supports_it(self):
        import inspect
        from fleet_mcp.oauth import AuthorizationServer
        source = inspect.getsource(AuthorizationServer.authorization_server_metadata)
        self.assertIn('"client_id_metadata_document_supported": True', source)


class TheGuide(unittest.TestCase):
    def test_every_shipped_skill_is_a_topic_and_core_reads(self):
        topics = [t["topic"] for t in guide.index()]
        self.assertIn("fleet-core", topics)
        self.assertEqual(len(topics), len(list(guide.DEFAULT_DIR.glob("*/SKILL.md"))))
        core = guide.read("fleet-core")
        self.assertIn("owner", core["text"])
        self.assertFalse(core["text"].startswith("---"))
        self.assertIsNone(guide.read("../etc/passwd"))
        self.assertIsNone(guide.read("no-such-topic"))

    def test_the_instructions_point_at_it(self):
        from fleet_mcp import server
        self.assertIn("fleet_guide", server._INSTRUCTIONS)
        self.assertLess(len(server._INSTRUCTIONS.encode()), 4096)



class TwoProcessesOneStateFile(unittest.TestCase):
    def test_a_client_minted_elsewhere_survives_the_running_surfaces_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            running = TokenStore(Path(tmp), access_ttl=3600, refresh_ttl=86400)
            operator = TokenStore(Path(tmp), access_ttl=3600, refresh_ttl=86400)
            minted = operator.register_token_client(client_name="Muse")
            self.assertEqual(running.client(minted.client_id).client_name, "Muse")
            running.register_client(client_name="Grok", redirect_uris=["https://g.example/cb"])
            again = TokenStore(Path(tmp), access_ttl=3600, refresh_ttl=86400)
            names = sorted(c["client_name"] for c in again.clients())
        self.assertEqual(names, ["Grok", "Muse"])


class RefusalsSayWhy(unittest.TestCase):
    """11-19, Muse: 8 approvals, no exchange, 400s and 401s with no reason in
    the audit. Each refusal now carries a closed word."""

    def test_words(self):
        from fleet_mcp.oauth import refusal_word
        cases = {
            "unknown or already-used authorization code": "code_unknown_or_used",
            "authorization code expired": "expired",
            "authorization code was issued to another client": "client_mismatch",
            "redirect_uri does not match the one registered for this client": "redirect_mismatch",
            "PKCE with code_challenge_method=S256 is required": "pkce",
            "unknown client_id": "unknown_client",
            "this client has been revoked": "revoked",
            "no Authorization header": "no_header",
            "token expired": "expired",
            "something new": "other",
        }
        for text, word in cases.items():
            self.assertEqual(refusal_word(text), word, text)

    def test_the_audit_keeps_the_word(self):
        from fleet_mcp import disclosure
        projected, _ = disclosure.project_record({"event": "oauth.authorize.refused",
                                           "refusal": "redirect_mismatch", "error": "invalid_request",
                                           "detail": "redirect_uri https://evil/x"})
        self.assertEqual(projected["refusal"], "redirect_mismatch")
        self.assertNotIn("evil", str(projected))

    def test_registration_time_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TokenStore(Path(tmp), access_ttl=3600, refresh_ttl=86400)
            store.register_client(client_name="Grok", redirect_uris=["https://g.example/cb"])
            row = TokenStore(Path(tmp), access_ttl=3600, refresh_ttl=86400).clients()[0]
        self.assertNotIn("unknown", row["created_at"])


if __name__ == "__main__":
    unittest.main()
