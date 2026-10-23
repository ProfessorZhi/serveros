#!/usr/bin/env python3
"""Username/password + TOTP at the consent step, and what must stay closed.

The password is the only outer door on a deployment that sits on the public
internet, so these tests are written the way an attacker would read the surface:
"the token still works", "I can replay the code", "I can post the form from
another page", "I can guess until it opens". Each is kept false, and the things
that must never be written down — the password, the code, the secret — are
checked in the audit log rather than assumed.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import human_auth  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ServerBinding,
)
from fleet_mcp.fleet import FleetService  # noqa: E402
from fleet_mcp.http_transport import HttpSurface, build_oauth, serve  # noqa: E402

CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
USERNAME = "owner"
PASSWORD = "correct horse battery staple"
#: Fixed so the test can mint the code the server will expect. RFC 6238's own
#: secret for the SHA-1 vectors, reused here because it is the one every
#: implementation is checked against.
SECRET = b"12345678901234567890"
OWNER_TOKEN = "owner-pat-value"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return the 302 itself. Following it would hide the code under test."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Client:
    def __init__(self, base: str) -> None:
        self.base = base
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, path: str, *, body: bytes | None = None,
                headers: dict[str, str] | None = None, form=None):
        headers = dict(headers or {})
        if form is not None:
            body = urllib.parse.urlencode(form, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers=headers)
        try:
            with self._opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


def _write_private(path: Path, text: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(path, 0o600)


# --------------------------------------------------------------------------
# The primitives, without a server in the way
# --------------------------------------------------------------------------

class Totp(unittest.TestCase):
    def test_the_rfc_6238_vector_verifies(self):
        """Appendix B, SHA-1, six digits, at T=59 -> 287082."""
        code, step = human_auth.totp_at(SECRET, at=59)
        self.assertEqual(code, "287082")
        self.assertEqual(step, 1)
        self.assertEqual(human_auth.verify_totp(SECRET, "287082", at=59), (True, 1))

    def test_one_step_of_skew_either_way_is_accepted(self):
        for skew in (-30, 0, 30):
            with self.subTest(skew=skew):
                code, _ = human_auth.totp_at(SECRET, at=119 + skew)
                ok, _ = human_auth.verify_totp(SECRET, code, at=119)
                self.assertTrue(ok)

    def test_two_steps_away_is_not_accepted(self):
        code, _ = human_auth.totp_at(SECRET, at=119 + 60)
        ok, _ = human_auth.verify_totp(SECRET, code, at=119)
        self.assertFalse(ok)

    def test_a_code_is_spent_once(self):
        code, step = human_auth.totp_at(SECRET, at=59)
        ok, used = human_auth.verify_totp(SECRET, code, at=59)
        self.assertTrue(ok)
        again, _ = human_auth.verify_totp(SECRET, code, last_step=used, at=59)
        self.assertFalse(again)

    def test_a_code_from_the_wrong_secret_is_refused(self):
        code, _ = human_auth.totp_at(b"a-different-secret!!", at=59)
        self.assertFalse(human_auth.verify_totp(SECRET, code, at=59)[0])


class Anchors(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_group_readable_anchor_is_refused(self):
        """The hash is not the password, but it is the offline attack."""
        path = self.tmp / "pass"
        path.write_text(human_auth._hash_line(USERNAME, PASSWORD) + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        with self.assertRaises(human_auth.HumanAuthError):
            human_auth.load_passwords(path)

    def test_a_malformed_line_is_refused_rather_than_skipped(self):
        """Skipping it would leave the file looking like a one-user anchor."""
        path = self.tmp / "pass"
        _write_private(path, "this is not a hash line\n")
        with self.assertRaises(human_auth.HumanAuthError):
            human_auth.load_passwords(path)

    def test_a_symlink_is_refused(self):
        if not hasattr(os, "symlink"):
            self.skipTest("no symlinks on this platform")
        real = self.tmp / "real"
        _write_private(real, human_auth._hash_line(USERNAME, PASSWORD) + "\n")
        link = self.tmp / "link"
        link.symlink_to(real)
        with self.assertRaises(human_auth.HumanAuthError):
            human_auth.load_passwords(link)

    def test_the_right_password_verifies_and_the_wrong_one_does_not(self):
        path = self.tmp / "pass"
        _write_private(path, human_auth._hash_line(USERNAME, PASSWORD) + "\n")
        credential = human_auth.load_passwords(path)[USERNAME]
        self.assertTrue(human_auth.verify_password(credential, PASSWORD))
        self.assertFalse(human_auth.verify_password(credential, PASSWORD + "!"))
        self.assertFalse(human_auth.verify_password(credential, ""))


class Sessions(unittest.TestCase):
    KEY = b"k" * 32

    def test_a_session_round_trips_its_user(self):
        value = human_auth.issue_session(self.KEY, USERNAME, ttl=60)
        self.assertEqual(human_auth.verify_session(self.KEY, value), USERNAME)

    def test_a_tampered_session_is_nobody(self):
        value = human_auth.issue_session(self.KEY, USERNAME, ttl=60)
        self.assertIsNone(human_auth.verify_session(self.KEY, value[:-1] + ("A" if value[-1] != "A" else "B")))
        self.assertIsNone(human_auth.verify_session(b"x" * 32, value))

    def test_an_expired_session_is_nobody(self):
        value = human_auth.issue_session(self.KEY, USERNAME, ttl=60, now=1000)
        self.assertIsNone(human_auth.verify_session(self.KEY, value, now=1061))

    def test_a_session_and_a_csrf_token_are_not_interchangeable(self):
        value = human_auth.issue_session(self.KEY, USERNAME, ttl=60)
        self.assertIsNone(human_auth.verify_session(self.KEY, human_auth.csrf_for(self.KEY, value)))
        self.assertIsNone(human_auth.verify_session(self.KEY, human_auth.csrf_for(self.KEY, value) + "0"))

    def test_csrf_is_bound_to_this_cookie(self):
        one = human_auth.issue_session(self.KEY, USERNAME, ttl=60)
        other = human_auth.issue_session(self.KEY, USERNAME, ttl=60)
        self.assertNotEqual(human_auth.csrf_for(self.KEY, one), human_auth.csrf_for(self.KEY, other))


# --------------------------------------------------------------------------
# The consent page, over a real socket
# --------------------------------------------------------------------------

class PasswordConsentFlow(unittest.TestCase):
    """One server per test: the throttle and the replay guard are state."""

    VERIFIER = "v" * 64
    TOTP_ANCHOR = f"{USERNAME}:{human_auth.encode_base32(SECRET)}"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.pass_anchor = self.tmp / "owner.pass"
        self.totp_anchor = self.tmp / "owner.totp"
        _write_private(self.pass_anchor, human_auth._hash_line(USERNAME, PASSWORD) + "\n")
        _write_private(self.totp_anchor, self.TOTP_ANCHOR + "\n")

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.config = FleetConfig(
            source=None,
            local_device_ref="windows-shared-dev-01",
            providers={},
            auth=AuthBinding(
                pat_anchor=None,
                password_anchor=self.pass_anchor,
                totp_anchor=self.totp_anchor,
                pat_enabled=False,
                session_ttl=600,
                access_token_ttl=3600,
                refresh_token_ttl=3600,
            ),
            server=ServerBinding(bind="127.0.0.1", port=self.port,
                                 public_base_url=self.base),
            policy=PolicyBinding(),
            audit=AuditBinding(path=self.tmp / "audit.jsonl", rate_limit_per_minute=10_000),
        )
        self.service = FleetService(self.config, providers={})
        self.oauth = build_oauth(self.config, base_url=self.base,
                                 store_root=self.tmp / "oauth")
        self.surface = HttpSurface(service=self.service, config=self.config,
                                   oauth=self.oauth, audit=self.service.audit)
        self.server = serve(self.surface, bind="127.0.0.1", port=self.port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = _Client(self.base)
        self.client_id = self._register()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    # -- helpers -----------------------------------------------------------

    def _register(self) -> str:
        status, _, body = self.client.request(
            "POST", "/oauth/register",
            body=json.dumps({"client_name": "ChatGPT",
                             "redirect_uris": [CHATGPT_REDIRECT]}).encode(),
        )
        self.assertEqual(status, 201, body)
        return json.loads(body)["client_id"]

    def _params(self) -> dict[str, str]:
        return {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": CHATGPT_REDIRECT,
            "scope": "fleet:read fleet:send",
            "state": "state-xyz",
            "code_challenge": _b64(hashlib.sha256(self.VERIFIER.encode()).digest()),
            "code_challenge_method": "S256",
        }

    def _code_now(self) -> str:
        return human_auth.totp_at(SECRET)[0]

    def _get(self, cookie: str | None = None):
        headers = {"Cookie": f"{human_auth.COOKIE_NAME}={cookie}"} if cookie else None
        return self.client.request("GET", "/oauth/authorize?" +
                                   urllib.parse.urlencode(self._params()), headers=headers)

    def _post(self, cookie: str | None = None, **overrides):
        form = dict(self._params())
        form.update({"scope": ["fleet:read", "fleet:send"]})
        form.update(overrides)
        headers = {"Cookie": f"{human_auth.COOKIE_NAME}={cookie}"} if cookie else None
        return self.client.request("POST", "/oauth/authorize", form=form, headers=headers)

    def _login(self, *, password: str = PASSWORD, code: str | None = None):
        return self._post(username=USERNAME, password=password,
                          totp=self._code_now() if code is None else code)

    @staticmethod
    def _cookie_from(headers: dict[str, str]) -> str:
        raw = headers.get("Set-Cookie", "")
        first = raw.split(";", 1)[0]
        name, _, value = first.partition("=")
        assert name == human_auth.COOKIE_NAME, raw
        return value

    @staticmethod
    def _csrf_from(markup: str) -> str:
        marker = 'name="csrf" value="'
        start = markup.index(marker) + len(marker)
        return markup[start:markup.index('"', start)]

    # -- the form ----------------------------------------------------------

    def test_the_page_asks_for_a_username_password_and_code(self):
        status, _, body = self._get()
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn('name="username"', text)
        self.assertIn('name="password"', text)
        self.assertIn('name="totp"', text)
        self.assertNotIn('name="owner_token"', text)
        self.assertNotIn("owner access token", text.lower())

    def test_a_signed_in_page_asks_to_continue_and_carries_a_csrf_token(self):
        _, headers, _ = self._login()
        status, _, body = self._get(self._cookie_from(headers))
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn(f"Signed in as <b>{USERNAME}</b>", text)
        self.assertIn('name="continue"', text)
        self.assertIn('name="csrf"', text)
        self.assertNotIn('name="password"', text)

    def test_the_cookie_is_scoped_and_not_readable_by_script(self):
        _, headers, _ = self._login()
        raw = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", raw)
        self.assertIn("SameSite=Lax", raw)
        self.assertIn("Path=/oauth", raw)
        self.assertIn("Max-Age=600", raw)
        # Plain http on loopback: a Secure cookie would be dropped by the
        # browser and the login would loop with no error anywhere.
        self.assertNotIn("Secure", raw)

    def test_a_good_login_issues_a_code_and_no_token_yet(self):
        status, headers, _ = self._login()
        self.assertEqual(status, 302)
        location = headers["Location"]
        self.assertTrue(location.startswith(CHATGPT_REDIRECT), location)
        self.assertIn("code=", location)
        self.assertIn("state=state-xyz", location)
        self.assertNotIn("access_token", location)

    def test_a_wrong_password_is_401_with_no_cookie(self):
        status, headers, body = self._login(password=PASSWORD + "!")
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)
        self.assertIn("was not accepted", body.decode())

    def test_an_unknown_user_reads_the_same_as_a_wrong_password(self):
        """Byte-identical pages: a difference here is a directory of accounts."""
        a = self._post(username=USERNAME, password=PASSWORD + "!", totp=self._code_now())
        b = self._post(username="nobody", password=PASSWORD, totp=self._code_now())
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[2], b[2])
        self.assertNotIn("no such user", b[2].decode().lower())

    def test_a_missing_code_is_refused(self):
        status, headers, _ = self._post(username=USERNAME, password=PASSWORD)
        self.assertEqual(status, 401)
        self.assertNotIn("Set-Cookie", headers)

    def test_a_code_from_yesterday_is_refused(self):
        old = human_auth.totp_at(SECRET, at=time.time() - 3600)[0]
        status, _, _ = self._login(code=old)
        self.assertEqual(status, 401)

    def test_the_same_code_cannot_open_a_second_session(self):
        first, _, _ = self._login()
        self.assertEqual(first, 302)
        second, headers, _ = self._login()
        self.assertEqual(second, 401)
        self.assertNotIn("Set-Cookie", headers)

    # -- the cookie --------------------------------------------------------

    def test_the_cookie_authorizes_without_the_password(self):
        _, headers, _ = self._login()
        cookie = self._cookie_from(headers)
        _, _, page = self._get(cookie)
        csrf = self._csrf_from(page.decode())
        status, out_headers, _ = self._post(cookie=cookie, csrf=csrf)
        self.assertEqual(status, 302)
        self.assertIn("code=", out_headers["Location"])
        # No second cookie: the session was already established.
        self.assertNotIn("Set-Cookie", out_headers)

    def test_the_cookie_without_its_csrf_token_is_refused(self):
        """Otherwise any page on the internet could approve a client."""
        _, headers, _ = self._login()
        cookie = self._cookie_from(headers)
        status, out, body = self._post(cookie=cookie)
        self.assertEqual(status, 403)
        self.assertNotIn("code=", out.get("Location", ""))
        self.assertIn("not submitted from", body.decode())

    def test_a_csrf_token_from_another_session_is_refused(self):
        _, first, _ = self._login()
        # The adjacent step is inside the skew window, so this is a second
        # legitimate sign-in rather than a replay of the first.
        later = human_auth.totp_at(SECRET, at=time.time() + 30)[0]
        _, second, _ = self._login(code=later)
        _, _, page = self._get(self._cookie_from(second))
        csrf = self._csrf_from(page.decode())
        status, _, _ = self._post(cookie=self._cookie_from(first), csrf=csrf)
        self.assertEqual(status, 403)

    def test_logout_expires_the_cookie(self):
        status, headers, _ = self.client.request("GET", "/oauth/authorize/logout")
        self.assertEqual(status, 302)
        raw = headers.get("Set-Cookie", "")
        self.assertIn("Max-Age=0", raw)
        self.assertIn("Path=/oauth", raw)

    # -- brute force -------------------------------------------------------

    def test_five_failures_close_the_door_for_a_while(self):
        for _ in range(5):
            status, _, _ = self._login(password="wrong")
            self.assertEqual(status, 401)
        status, _, body = self._login()
        self.assertEqual(status, 401)
        self.assertIn("Too many failed attempts", body.decode())

    def test_a_good_login_clears_the_failure_count(self):
        for _ in range(4):
            self._login(password="wrong")
        self.assertEqual(self._login(code=self._code_now())[0], 302)

    # -- what is written down ---------------------------------------------

    def test_neither_the_password_nor_the_code_reaches_the_audit(self):
        code = self._code_now()
        self._post(username=USERNAME, password=PASSWORD + "!", totp=code)  # refused
        self._login()                                                     # accepted
        text = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(code, text)
        self.assertIn('"event": "oauth.login.refused"', text)
        self.assertIn('"event": "oauth.login"', text)
        self.assertIn('"login_result": "bad_password"', text)
        self.assertIn(f'"username": "{USERNAME}"', text)

    def test_an_unknown_username_is_not_recorded(self):
        """The form is unauthenticated: a stranger chooses this value, so a
        token pasted into it must not land in the log."""
        self._post(username="Bearer-abcdef0123456789", password="x",
                   totp=self._code_now())
        text = (self.tmp / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("abcdef0123456789", text)
        self.assertIn('"login_result": "unknown_user"', text)

    # -- the old door ------------------------------------------------------

    def test_the_owner_token_no_longer_authenticates(self):
        """`pat_enabled: false` in the binding, and the anchor is not even set."""
        status, _, _ = self.client.request(
            "POST", "/mcp",
            body=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
            headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
