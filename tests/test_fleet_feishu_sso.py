#!/usr/bin/env python3
"""The Feishu identity broker: a second way to prove a human is present.

The consent gate in `oauth.py` proves "this is the owner" by requiring the
owner's personal access token. This module is the other proof — a whitelisted
human signs in at Feishu, the *server* exchanges the code for an identity, and
the identity is what the token's subject becomes.

The tests are written as the sentences an attacker would try, and each one is
kept false:

    "I can replay the callback."          -> a transaction is single-use.
    "I can forge the state."              -> an unknown state has no trusted
                                             redirect and is refused.
    "I can post my own open_id."          -> the browser never carries one; the
                                             identity only ever comes from the
                                             server-side exchange.
    "My open_id is whitelisted, so any
     tenant will do."                     -> tenant and app scope are matched.
    "I am whitelisted, so I may drive
     the fleet."                          -> login grants no device verb; the
                                             identity is a principal with no
                                             entry in `operator_grants`.

And the preservation half, which is the point of shipping it default-off: with
no `auth.feishu` block, every one of these routes is a 404 and the existing
OAuth flow is byte-for-byte what it was.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.config import (  # noqa: E402
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ServerBinding,
    load_config,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.http_transport import HttpSurface, build_oauth  # noqa: E402
from fleet_mcp.oauth import OAuthError, TokenStore  # noqa: E402

APP = "cli_aa0e8c3d6f789cc6"
TENANT = "tenant-company"
OPEN_COMPANY = "ou_company_vince"
OPEN_PERSONAL = "ou_personal_me"
CHATGPT_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
BASE = "https://fleet.example"


# --------------------------------------------------------------------------
# A fake Feishu, so no test ever opens a socket or learns a real secret.
# --------------------------------------------------------------------------


class FakeFeishu:
    """Stands in for `open.feishu.cn`. Records what it was asked, answers what
    the test tells it to. Nothing here is a real credential.
    """

    def __init__(self, *, reply=None):
        self.calls: list[tuple[str, dict]] = []
        self._reply = reply if reply is not None else {}

    def post_json(self, url, payload, *, timeout):
        self.calls.append((url, dict(payload)))
        return self._reply.get("token", {"access_token": "uat-fake", "code": 0})

    def get_json(self, url, *, headers, timeout):
        self.calls.append((url, dict(headers)))
        return self._reply.get(
            "userinfo",
            {"code": 0, "data": {"open_id": OPEN_COMPANY, "union_id": "un-x",
                                 "tenant_key": TENANT}},
        )


def _broker(*, enabled=True, whitelist=(), reply=None, secret="s3cr3t-not-real"):
    from fleet_mcp.feishu_sso import FeishuBinding, FeishuBroker  # noqa: PLC0415

    binding = FeishuBinding(
        enabled=enabled,
        app_scope=APP,
        app_id=APP,
        secret_anchor=Path("/nowhere/feishu-app2-secret"),
        redirect_uri=f"{BASE}/oauth/feishu/callback",
        whitelist=tuple(whitelist),
    )
    return FeishuBroker(
        binding=binding,
        http=FakeFeishu(reply=reply),
        secret_reader=lambda: secret,
    )


def _entry(user_id=OPEN_COMPANY, tenant=TENANT, label="company-vince", app_scope=APP):
    from fleet_mcp.feishu_sso import FeishuWhitelistEntry  # noqa: PLC0415

    return FeishuWhitelistEntry(
        app_scope=app_scope, tenant_key=tenant, user_id=user_id, label=label
    )


# --------------------------------------------------------------------------
# The identity key: provider + app/tenant scope + a stable id, and nothing else.
# --------------------------------------------------------------------------


class IdentityKey(unittest.TestCase):
    def test_key_names_provider_app_tenant_and_stable_user_id(self):
        from fleet_mcp.feishu_sso import FeishuIdentity  # noqa: PLC0415

        ident = FeishuIdentity(app_scope=APP, tenant_key=TENANT, user_id=OPEN_COMPANY)
        self.assertEqual(ident.key, f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}")
        self.assertEqual(ident.principal_name, ident.key)

    def test_identity_carries_no_display_name_email_or_phone(self):
        """A name is a claim the API makes, not an identity. It is not carried."""
        from fleet_mcp.feishu_sso import FeishuIdentity  # noqa: PLC0415

        ident = FeishuIdentity(app_scope=APP, tenant_key=TENANT, user_id=OPEN_COMPANY,
                               union_id="un-x")
        row = ident.as_dict()
        for forbidden in ("name", "email", "mobile", "phone", "avatar"):
            self.assertNotIn(forbidden, row)
        self.assertEqual(row["user_id"], OPEN_COMPANY)

    def test_a_key_is_refused_when_a_part_is_empty(self):
        from fleet_mcp.feishu_sso import FeishuIdentity  # noqa: PLC0415

        for kwargs in ({"app_scope": "", "tenant_key": TENANT, "user_id": OPEN_COMPANY},
                       {"app_scope": APP, "tenant_key": "", "user_id": OPEN_COMPANY},
                       {"app_scope": APP, "tenant_key": TENANT, "user_id": ""}):
            with self.assertRaises(ValueError):
                FeishuIdentity(**kwargs)

    def test_a_part_may_not_contain_the_key_separator(self):
        """A colon in a part would let one identity forge another's key."""
        from fleet_mcp.feishu_sso import FeishuIdentity  # noqa: PLC0415

        with self.assertRaises(ValueError):
            FeishuIdentity(app_scope=APP, tenant_key=TENANT, user_id="ou:a")


# --------------------------------------------------------------------------
# The whitelist: exactly the identities an operator wrote down.
# --------------------------------------------------------------------------


class Whitelist(unittest.TestCase):
    def test_only_a_configured_identity_is_admitted(self):
        broker = _broker(whitelist=(_entry(),))
        self.assertTrue(broker.admits(_entry_identity()))

    def test_an_unknown_identity_is_refused(self):
        broker = _broker(whitelist=(_entry(),))
        verdict = broker.verdict(_entry_identity(user_id="ou_someone_else"))
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "unknown_identity")

    def test_a_different_tenant_is_refused_even_with_the_same_open_id(self):
        """An open_id is unique within a tenant, not across tenants."""
        broker = _broker(whitelist=(_entry(),))
        verdict = broker.verdict(_entry_identity(tenant="tenant-other"))
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "wrong_tenant")

    def test_another_app_scope_is_refused(self):
        broker = _broker(whitelist=(_entry(),))
        verdict = broker.verdict(_entry_identity(app_scope="cli_someone_elses"))
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "wrong_app")

    def test_no_identity_is_admitted_when_the_whitelist_is_empty(self):
        """The shipped state: two slots, neither written. Nobody gets in."""
        broker = _broker(whitelist=())
        self.assertFalse(broker.verdict(_entry_identity()).allowed)

    def test_two_independent_identities_are_both_admitted(self):
        broker = _broker(whitelist=(_entry(), _entry(user_id=OPEN_PERSONAL,
                                                     label="personal-me")))
        self.assertTrue(broker.admits(_entry_identity()))
        self.assertTrue(broker.admits(_entry_identity(user_id=OPEN_PERSONAL)))


def _entry_identity(user_id=OPEN_COMPANY, tenant=TENANT, app_scope=APP):
    from fleet_mcp.feishu_sso import FeishuIdentity  # noqa: PLC0415

    return FeishuIdentity(app_scope=app_scope, tenant_key=tenant, user_id=user_id)


# --------------------------------------------------------------------------
# The transaction: one use, one lifetime, bound to the request it came from.
# --------------------------------------------------------------------------


class Transactions(unittest.TestCase):
    def _store(self, ttl=300.0, now=None):
        from fleet_mcp.feishu_sso import TransactionStore  # noqa: PLC0415

        return TransactionStore(ttl=ttl, now=now or time.time)

    def _begin(self, store):
        return store.begin(
            client_id="fleet-abc",
            redirect_uri=CHATGPT_REDIRECT,
            code_challenge="chal",
            scopes=("fleet:read", "fleet:send"),
            resource=f"{BASE}/mcp",
            client_state="client-state-123",
        )

    def test_a_transaction_is_single_use(self):
        store = self._store()
        txn = self._begin(store)
        store.consume(txn.feishu_state)
        with self.assertRaises(Exception) as caught:
            store.consume(txn.feishu_state)
        self.assertIn("state", str(caught.exception).lower())

    def test_a_transaction_expires(self):
        clock = {"t": 1000.0}
        store = self._store(ttl=30.0, now=lambda: clock["t"])
        txn = self._begin(store)
        clock["t"] += 31.0
        with self.assertRaises(Exception):
            store.consume(txn.feishu_state)

    def test_an_unknown_state_is_refused(self):
        store = self._store()
        with self.assertRaises(Exception):
            store.consume("never-issued")

    def test_a_transaction_carries_the_original_request_forward(self):
        store = self._store()
        txn = self._begin(store)
        record = store.consume(txn.feishu_state)
        self.assertEqual(record.client_id, "fleet-abc")
        self.assertEqual(record.redirect_uri, CHATGPT_REDIRECT)
        self.assertEqual(record.code_challenge, "chal")
        self.assertEqual(record.scopes, ("fleet:read", "fleet:send"))
        self.assertEqual(record.client_state, "client-state-123")

    def test_two_transactions_get_different_states(self):
        store = self._store()
        self.assertNotEqual(self._begin(store).feishu_state,
                            self._begin(store).feishu_state)


# --------------------------------------------------------------------------
# The broker: the only place a code becomes an identity, and it is server-side.
# --------------------------------------------------------------------------


class BrokerExchange(unittest.TestCase):
    def test_the_authorize_url_carries_app_redirect_and_state(self):
        url = _broker(whitelist=(_entry(),)).authorize_url(state="st-1")
        self.assertIn(f"app_id={APP}", url)
        self.assertIn("state=st-1", url)
        self.assertIn("response_type=code", url)
        self.assertIn("redirect_uri=", url)

    def test_the_exchange_returns_an_identity_from_the_server_side_payload(self):
        broker = _broker()
        ident = broker.exchange_code("oauth-code-from-feishu")
        # Compared on the key, which is the identity: `union_id` is carried but
        # is not part of what the whitelist matches on.
        self.assertEqual(ident.key, _entry_identity().key)

    def test_the_code_is_never_treated_as_an_identity(self):
        """A code that is not in the payload cannot become an identity."""
        broker = _broker(reply={"userinfo": {"code": 0, "data": {"open_id": "",
                                                                "tenant_key": TENANT}}})
        with self.assertRaises(Exception):
            broker.exchange_code("whatever-the-browser-sent")

    def test_a_failed_exchange_is_refused(self):
        broker = _broker(reply={"token": {"code": 99999, "msg": "boom"}})
        with self.assertRaises(Exception):
            broker.exchange_code("a-code")

    def test_a_userinfo_error_is_refused(self):
        broker = _broker(reply={"userinfo": {"code": 99991, "msg": "no such user"}})
        with self.assertRaises(Exception):
            broker.exchange_code("a-code")

    def test_the_broker_reads_the_secret_only_through_the_reader(self):
        """The config holds a path; the broker asks a reader for the value."""
        seen = {"read": 0}

        def reader():
            seen["read"] += 1
            return "s3cr3t-not-real"

        from fleet_mcp.feishu_sso import FeishuBroker  # noqa: PLC0415
        from fleet_mcp.feishu_sso import FeishuBinding  # noqa: PLC0415

        broker = FeishuBroker(
            binding=FeishuBinding(enabled=True, app_scope=APP, app_id=APP,
                                  secret_anchor=Path("/nowhere"), redirect_uri="https://x/cb"),
            http=FakeFeishu(), secret_reader=reader,
        )
        broker.exchange_code("c")
        self.assertEqual(seen["read"], 1)

    def test_a_missing_secret_refuses_rather_than_calling_out(self):
        fake = FakeFeishu()
        from fleet_mcp.feishu_sso import FeishuBroker, FeishuBinding  # noqa: PLC0415

        broker = FeishuBroker(
            binding=FeishuBinding(enabled=True, app_scope=APP, app_id=APP,
                                  secret_anchor=Path("/nowhere"), redirect_uri="https://x/cb"),
            http=fake, secret_reader=lambda: None,
        )
        with self.assertRaises(Exception):
            broker.exchange_code("c")
        self.assertEqual(fake.calls, [])

    def test_a_disabled_broker_is_absent(self):
        self.assertIsNone(_broker(enabled=False).authorize_url(state="s"))


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FeishuHTTPTransport(unittest.TestCase):
    """The real transport, driven through its opener seam — no socket opens."""

    def _http(self, *, body: bytes, capture: list):
        from fleet_mcp.feishu_sso import FeishuHTTP  # noqa: PLC0415

        def opener(request, timeout):
            capture.append(request)
            return _FakeResponse(body)

        return FeishuHTTP(opener=opener)

    def test_post_sends_json_and_parses_the_reply(self):
        capture: list = []
        http = self._http(body=b'{"code":0,"access_token":"uat"}', capture=capture)
        reply = http.post_json("https://open.feishu.cn/token", {"a": 1}, timeout=5.0)
        self.assertEqual(reply["access_token"], "uat")
        self.assertEqual(capture[0].get_method(), "POST")
        self.assertEqual(capture[0].data, b'{"a": 1}')

    def test_get_sends_the_bearer_header(self):
        capture: list = []
        http = self._http(body=b'{"code":0}', capture=capture)
        http.get_json("https://open.feishu.cn/userinfo",
                      headers={"Authorization": "Bearer uat"}, timeout=5.0)
        self.assertEqual(capture[0].get_header("Authorization"), "Bearer uat")

    def test_a_non_json_reply_is_refused(self):
        http = self._http(body=b"<html>not json</html>", capture=[])
        with self.assertRaises(Exception):
            http.post_json("https://open.feishu.cn/token", {}, timeout=5.0)

    def test_an_unreachable_endpoint_is_refused(self):
        from fleet_mcp.feishu_sso import FeishuHTTP  # noqa: PLC0415

        def opener(request, timeout):
            raise OSError("connection refused")

        with self.assertRaises(Exception):
            FeishuHTTP(opener=opener).get_json("https://x/y", headers={}, timeout=1.0)


# --------------------------------------------------------------------------
# Config: absent means absent. Enabled means fully configured, or refused.
# --------------------------------------------------------------------------


def _cfg_text(auth_block: str) -> str:
    return (
        "schema: serveros/fleet-mcp.local/v1\n"
        "device_ref: device:tokyo-shared-dev-01\n"
        "providers: {}\n"
        f"{auth_block}"
    )


class ConfigBinding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="feishu-cfg-"))

    def _load(self, auth_block: str):
        path = self.tmp / "fleet-mcp.local.yaml"
        path.write_text(_cfg_text(auth_block), encoding="utf-8")
        return load_config(path)

    def test_no_feishu_block_leaves_the_config_as_it_was(self):
        config = self._load("auth:\n  oauth_enabled: true\n")
        self.assertIsNone(config.auth.feishu)

    def test_a_disabled_feishu_block_still_parses(self):
        config = self._load(
            "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: false\n"
            f"    app_scope: {APP}\n"
        )
        self.assertIsNotNone(config.auth.feishu)
        self.assertFalse(config.auth.feishu.enabled)

    def test_enabling_without_an_identity_is_refused(self):
        from fleet_mcp.errors import FleetRefusal  # noqa: PLC0415

        with self.assertRaises(FleetRefusal):
            self._load(
                "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: true\n"
                f"    app_scope: {APP}\n    app_id: {APP}\n"
                "    secret_anchor: /nowhere/secret\n"
                f"    redirect_uri: {BASE}/oauth/feishu/callback\n"
                "    whitelist: []\n"
            )

    def test_enabling_without_an_app_id_is_refused(self):
        from fleet_mcp.errors import FleetRefusal  # noqa: PLC0415

        with self.assertRaises(FleetRefusal):
            self._load(
                "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: true\n"
                f"    app_scope: {APP}\n"
                "    secret_anchor: /nowhere/secret\n"
                f"    redirect_uri: {BASE}/oauth/feishu/callback\n"
                "    whitelist:\n"
                f"      - tenant_key: {TENANT}\n        user_id: {OPEN_COMPANY}\n"
            )

    def test_a_configured_identity_round_trips_as_a_path_not_a_value(self):
        config = self._load(
            "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: true\n"
            f"    app_scope: {APP}\n    app_id: {APP}\n"
            "    secret_anchor: /nowhere/feishu-app2-secret\n"
            f"    redirect_uri: {BASE}/oauth/feishu/callback\n"
            "    whitelist:\n"
            f"      - tenant_key: {TENANT}\n        user_id: {OPEN_COMPANY}\n"
            "        label: company-vince\n"
        )
        feishu = config.auth.feishu
        self.assertTrue(feishu.enabled)
        self.assertEqual(feishu.secret_anchor, Path("/nowhere/feishu-app2-secret"))
        self.assertEqual(len(feishu.whitelist), 1)
        self.assertEqual(feishu.whitelist[0].user_id, OPEN_COMPANY)

    def test_describe_reports_feishu_without_an_anchor_path_or_a_secret(self):
        import json  # noqa: PLC0415

        config = self._load(
            "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: true\n"
            f"    app_scope: {APP}\n    app_id: {APP}\n"
            "    secret_anchor: /nowhere/feishu-app2-secret\n"
            f"    redirect_uri: {BASE}/oauth/feishu/callback\n"
            "    whitelist:\n"
            f"      - tenant_key: {TENANT}\n        user_id: {OPEN_COMPANY}\n"
        )
        row = config.describe()["feishu"]
        self.assertTrue(row["enabled"])
        self.assertEqual(row["app_scope"], APP)
        self.assertEqual(row["identities"], 1)
        # The summary says *whether* a secret is configured, never where it is:
        # the anchor path itself must not appear.
        self.assertIn("secret_anchor_present", row)
        rendered = json.dumps(row)
        self.assertNotIn(str(config.auth.feishu.secret_anchor), rendered)
        self.assertNotIn("/nowhere", rendered)

    def test_describe_reports_no_feishu_when_there_is_none(self):
        config = self._load("auth:\n  oauth_enabled: true\n")
        self.assertIsNone(config.describe()["feishu"])

    def test_the_secret_anchor_is_never_read_at_load(self):
        """Pointing the anchor at a real file must not open it."""
        secret_file = self.tmp / "secret"
        secret_file.write_text("THIS-MUST-NEVER-BE-READ", encoding="utf-8")
        path = self.tmp / "fleet-mcp.local.yaml"
        path.write_text(_cfg_text(
            "auth:\n  oauth_enabled: true\n  feishu:\n    enabled: true\n"
            f"    app_scope: {APP}\n    app_id: {APP}\n"
            f"    secret_anchor: {secret_file}\n"
            f"    redirect_uri: {BASE}/oauth/feishu/callback\n"
            "    whitelist:\n"
            f"      - tenant_key: {TENANT}\n        user_id: {OPEN_COMPANY}\n"
        ), encoding="utf-8")
        config = load_config(path)
        self.assertEqual(config.auth.feishu.secret_anchor, secret_file)
        # The config object holds a path. Whether the file exists is not its
        # business, and nothing in this module reads bytes from it.
        self.assertFalse(hasattr(config.auth.feishu, "secret"))


# --------------------------------------------------------------------------
# The OAuth side: an identity approval mints a code, without a PAT.
# --------------------------------------------------------------------------


class OAuthIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="feishu-oauth-"))
        self.auth = AuthBinding(pat_anchor=None,
                                grantable_scopes=("fleet:read", "fleet:send"))
        self.store = TokenStore(self.tmp / "oauth", access_ttl=3600, refresh_ttl=3600)
        from fleet_mcp.oauth import AuthorizationServer  # noqa: PLC0415

        self.server = AuthorizationServer(store=self.store, auth=self.auth, base_url=BASE,
                                          pat_reader=lambda: None)
        self.client = self.store.register_client(
            client_name="chatgpt", redirect_uris=[CHATGPT_REDIRECT]
        )
        self.params = {
            "response_type": "code", "client_id": self.client.client_id,
            "redirect_uri": CHATGPT_REDIRECT, "state": "client-state",
            "code_challenge": _challenge("verifier"), "code_challenge_method": "S256",
            "scope": "fleet:read fleet:send",
        }

    def test_an_identity_approval_mints_a_code_without_a_pat(self):
        code, client = self.server.approve_identity(
            params=self.params, identity_key=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
        )
        self.assertTrue(code.startswith("fac-"))
        self.assertEqual(client.client_id, self.client.client_id)

    def test_the_identity_token_carries_the_subject_and_the_idp(self):
        code, _ = self.server.approve_identity(
            params=self.params, identity_key=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
        )
        issued = self.server.token({"grant_type": "authorization_code", "code": code,
                                    "client_id": self.client.client_id,
                                    "redirect_uri": CHATGPT_REDIRECT,
                                    "code_verifier": "verifier"})
        claims = self.store.signed_claims(issued["access_token"])
        self.assertEqual(claims["sub"], f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}")
        self.assertEqual(claims["idp"], "feishu")
        self.assertEqual(claims["scope"], "fleet:read fleet:send")

    def test_the_default_subject_is_operator_and_carries_no_idp_claim(self):
        server = _server_with_pat(self)
        code, _ = server.approve(params=self.params, owner_token="pat-value",
                                 granted=["fleet:read"])
        issued = server.token({"grant_type": "authorization_code", "code": code,
                               "client_id": self.client.client_id,
                               "redirect_uri": CHATGPT_REDIRECT,
                               "code_verifier": "verifier"})
        claims = self.store.signed_claims(issued["access_token"])
        self.assertEqual(claims["sub"], "operator")
        self.assertNotIn("idp", claims)

    def test_the_identity_grant_is_bounded_by_what_the_deployment_grants(self):
        """A scope this deployment does not grant is refused on the identity
        path exactly as it is on the PAT path."""
        with self.assertRaises(OAuthError):
            self.server.approve_identity(
                params={**self.params, "scope": "fleet:read fleet:create"},
                identity_key=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
            )

    def test_the_identity_grant_is_narrowed_by_what_was_kept(self):
        code, _ = self.server.approve_identity(
            params=self.params, identity_key=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
            granted=["fleet:read"],
        )
        issued = self.server.token({"grant_type": "authorization_code", "code": code,
                                    "client_id": self.client.client_id,
                                    "redirect_uri": CHATGPT_REDIRECT,
                                    "code_verifier": "verifier"})
        self.assertEqual(issued["scope"], "fleet:read")

    def test_a_refresh_keeps_the_identity(self):
        code, _ = self.server.approve_identity(
            params=self.params, identity_key=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
        )
        first = self.server.token({"grant_type": "authorization_code", "code": code,
                                   "client_id": self.client.client_id,
                                   "redirect_uri": CHATGPT_REDIRECT,
                                   "code_verifier": "verifier"})
        second = self.server.token({"grant_type": "refresh_token",
                                    "refresh_token": first["refresh_token"],
                                    "client_id": self.client.client_id})
        claims = self.store.signed_claims(second["access_token"])
        self.assertEqual(claims["sub"], f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}")
        self.assertEqual(claims["idp"], "feishu")


def _challenge(verifier):
    """The S256 challenge a client would send for this verifier."""
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _server_with_pat(case):
    from fleet_mcp.oauth import AuthorizationServer  # noqa: PLC0415

    # Rebuild the same store/server but with a readable PAT.
    store = case.store
    return AuthorizationServer(store=store, auth=case.auth, base_url=BASE,
                               pat_reader=lambda: "pat-value")


# --------------------------------------------------------------------------
# The HTTP flow, end to end, with a fake Feishu and no socket.
# --------------------------------------------------------------------------


def _surface(*, feishu_broker, pat="owner-token-value"):
    tmp = Path(tempfile.mkdtemp(prefix="feishu-http-"))
    pat_path = tmp / "pat"
    if pat:
        pat_path.write_text(pat, encoding="utf-8")
    config = FleetConfig(
        source=None, local_device_ref="device:tokyo-shared-dev-01",
        providers={},
        auth=AuthBinding(pat_anchor=pat_path if pat else None,
                         access_token_ttl=3600, refresh_token_ttl=3600,
                         grantable_scopes=("fleet:read", "fleet:send")),
        server=ServerBinding(bind="127.0.0.1", port=3401, public_base_url=BASE),
        policy=PolicyBinding(),
    )
    service = FleetService(config, providers={})
    oauth = build_oauth(config, base_url=BASE, store_root=tmp / "oauth")
    surface = HttpSurface(service=service, config=config, oauth=oauth,
                          audit=service.audit, feishu=feishu_broker)
    return surface, oauth


def _register(surface, oauth):
    status, _headers, body = surface.handle(
        "POST", "/oauth/register", "", {}, json_body({
            "client_name": "chatgpt", "redirect_uris": [CHATGPT_REDIRECT],
        }))
    return json_load(body)


def json_body(payload):
    import json  # noqa: PLC0415

    return json.dumps(payload).encode()


def json_load(body: bytes):
    import json  # noqa: PLC0415

    return json.loads(body.decode())


class HttpFlow(unittest.TestCase):
    def test_the_feishu_routes_are_404_when_sso_is_disabled(self):
        surface, _ = _surface(feishu_broker=None)
        for path in ("/oauth/feishu/start", "/oauth/feishu/callback"):
            status, _h, body = surface.handle("GET", path, "", {}, b"")
            self.assertEqual(status, 404, path)
            self.assertEqual(json_load(body)["error"], "not_found")

    def test_the_consent_form_offers_the_feishu_entry_only_when_enabled(self):
        on, oauth_on = _surface(feishu_broker=_broker(whitelist=(_entry(),)))
        client_on = _register(on, oauth_on)
        _s, _h, body_on = on.handle("GET", "/oauth/authorize",
                                    _authorize_query(client_on["client_id"]), {}, b"")
        self.assertIn("/oauth/feishu/start", body_on.decode())

        off, oauth_off = _surface(feishu_broker=None)
        client_off = _register(off, oauth_off)
        _s2, _h2, body_off = off.handle("GET", "/oauth/authorize",
                                        _authorize_query(client_off["client_id"]), {}, b"")
        self.assertNotIn("/oauth/feishu/start", body_off.decode())

    def test_start_redirects_to_feishu_with_a_fresh_state(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        query = _authorize_query(client["client_id"])
        status, headers, _body = surface.handle("GET", "/oauth/feishu/start", query, {}, b"")
        self.assertEqual(status, 302)
        self.assertIn("open.feishu.cn", headers["Location"])
        self.assertIn("state=", headers["Location"])

    def test_start_refuses_a_bad_redirect_uri_before_redirecting(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        query = _authorize_query(client["client_id"],
                                 redirect_uri="https://evil.example/cb")
        status, _headers, body = surface.handle("GET", "/oauth/feishu/start", query, {}, b"")
        self.assertNotIn(status, (301, 302, 303, 307, 308))
        self.assertIn("error", json_load(body))

    def test_the_callback_completes_the_flow_and_carries_iss_and_state(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        status, headers, _body = surface.handle(
            "GET", "/oauth/feishu/callback", f"code=feishu-code&state={state}", {}, b"")
        self.assertEqual(status, 302)
        location = headers["Location"]
        self.assertTrue(location.startswith(CHATGPT_REDIRECT))
        self.assertIn("code=fac-", location)
        self.assertIn("iss=", location)
        self.assertIn("state=client-state", location)

    def test_a_replayed_callback_is_refused(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        first = surface.handle("GET", "/oauth/feishu/callback",
                               f"code=c&state={state}", {}, b"")
        self.assertEqual(first[0], 302)
        second = surface.handle("GET", "/oauth/feishu/callback",
                                f"code=c&state={state}", {}, b"")
        self.assertNotEqual(second[0], 302)

    def test_a_user_who_declines_gets_an_error_not_a_token(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        status, headers, _body = surface.handle(
            "GET", "/oauth/feishu/callback",
            f"error=access_denied&state={state}", {}, b"")
        self.assertEqual(status, 302)
        self.assertIn("error=access_denied", headers["Location"])
        self.assertNotIn("code=fac-", headers["Location"])

    def test_an_unwhitelisted_identity_is_refused_at_the_callback(self):
        broker = _broker(whitelist=(_entry(),),
                         reply={"userinfo": {"code": 0, "data": {
                             "open_id": "ou_stranger", "tenant_key": TENANT}}})
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        status, headers, _body = surface.handle(
            "GET", "/oauth/feishu/callback", f"code=c&state={state}", {}, b"")
        self.assertEqual(status, 302)
        self.assertIn("error=access_denied", headers["Location"])
        self.assertNotIn("code=fac-", headers["Location"])

    def test_a_wrong_tenant_is_refused_at_the_callback(self):
        broker = _broker(whitelist=(_entry(),),
                         reply={"userinfo": {"code": 0, "data": {
                             "open_id": OPEN_COMPANY, "tenant_key": "tenant-other"}}})
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        _status, headers, _body = surface.handle(
            "GET", "/oauth/feishu/callback", f"code=c&state={state}", {}, b"")
        self.assertIn("error=access_denied", headers["Location"])

    def test_an_identity_api_failure_is_an_error_not_a_token(self):
        broker = _broker(whitelist=(_entry(),),
                         reply={"token": {"code": 99999, "msg": "down"}})
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        _status, headers, _body = surface.handle(
            "GET", "/oauth/feishu/callback", f"code=c&state={state}", {}, b"")
        self.assertIn("error=", headers["Location"])
        self.assertNotIn("code=fac-", headers["Location"])

    def test_pkce_is_still_required_at_the_token_exchange(self):
        broker = _broker(whitelist=(_entry(),))
        surface, oauth = _surface(feishu_broker=broker)
        client = _register(surface, oauth)
        state = _start(surface, client["client_id"])
        _s, headers, _b = surface.handle(
            "GET", "/oauth/feishu/callback", f"code=c&state={state}", {}, b"")
        code = _query(headers["Location"], "code")
        status, _h, body = surface.handle("POST", "/oauth/token", "", {},
                                          f"grant_type=authorization_code&code={code}"
                                          f"&client_id={client['client_id']}"
                                          f"&redirect_uri={CHATGPT_REDIRECT}"
                                          f"&code_verifier=WRONG".encode())
        self.assertEqual(status, 400)
        self.assertEqual(json_load(body)["error"], "invalid_grant")


def _authorize_query(client_id, *, redirect_uri=CHATGPT_REDIRECT, scope="fleet:read fleet:send"):
    from urllib.parse import urlencode  # noqa: PLC0415

    return urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "state": "client-state", "code_challenge": "chal",
        "code_challenge_method": "S256", "scope": scope,
    })


def _start(surface, client_id):
    _status, headers, _body = surface.handle("GET", "/oauth/feishu/start",
                                             _authorize_query(client_id), {}, b"")
    return _query(headers["Location"], "state")


def _query(url, name):
    from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

    return parse_qs(urlparse(url).query)[name][0]


# --------------------------------------------------------------------------
# The principal: login is not authorization.
# --------------------------------------------------------------------------


class PrincipalAndGrant(unittest.TestCase):
    def test_a_feishu_token_yields_a_feishu_principal_named_by_its_identity(self):
        tmp = Path(tempfile.mkdtemp(prefix="feishu-principal-"))
        pat = tmp / "pat"
        pat.write_text("owner-pat-value", encoding="utf-8")
        config = FleetConfig(
            source=None, local_device_ref="device:tokyo-shared-dev-01", providers={},
            auth=AuthBinding(pat_anchor=pat, access_token_ttl=3600, refresh_token_ttl=3600),
            server=ServerBinding(bind="127.0.0.1", port=3401, public_base_url=BASE),
            policy=PolicyBinding(),
        )
        service = FleetService(config, providers={})
        oauth = build_oauth(config, base_url=BASE, store_root=tmp / "oauth")
        surface = HttpSurface(service=service, config=config, oauth=oauth,
                              audit=service.audit, feishu=None)
        token, _ttl = oauth.store.mint_access_token(
            client_id="fleet-x", subject=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
            scopes=("fleet:read",), audience=oauth.resource_url, issuer=BASE,
            idp="feishu")
        principal = surface.principal_for_token(token)
        self.assertEqual(principal.credential_kind, "feishu")
        self.assertEqual(principal.name, f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}")

    def test_an_oauth_token_without_an_idp_is_still_named_by_its_client(self):
        tmp = Path(tempfile.mkdtemp(prefix="feishu-principal2-"))
        pat = tmp / "pat"
        pat.write_text("owner-pat-value", encoding="utf-8")
        config = FleetConfig(
            source=None, local_device_ref="device:tokyo-shared-dev-01", providers={},
            auth=AuthBinding(pat_anchor=pat, access_token_ttl=3600, refresh_token_ttl=3600),
            server=ServerBinding(bind="127.0.0.1", port=3401, public_base_url=BASE),
            policy=PolicyBinding(),
        )
        service = FleetService(config, providers={})
        oauth = build_oauth(config, base_url=BASE, store_root=tmp / "oauth")
        surface = HttpSurface(service=service, config=config, oauth=oauth,
                              audit=service.audit, feishu=None)
        token, _ttl = oauth.store.mint_access_token(
            client_id="fleet-y", subject="operator", scopes=("fleet:read",),
            audience=oauth.resource_url, issuer=BASE)
        principal = surface.principal_for_token(token)
        self.assertEqual(principal.credential_kind, "oauth")
        self.assertEqual(principal.name, "oauth:fleet-y")

    def test_a_whitelisted_identity_holds_no_device_verb_until_granted(self):
        """PENDING_GRANT: the identity exists, and may do nothing yet."""
        service = _grant_service()
        identity_key = f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}"
        principal = Principal(name=identity_key, scopes=frozenset({"fleet:read"}),
                              credential_kind="feishu")
        # `_operator_grant_verbs` is the device x runtime x verb axis. The device
        # is company-owned and reachable, so the only reason it returns nothing
        # is that this principal has no grant — which is the whole of
        # PENDING_GRANT.
        self.assertEqual(
            service._operator_grant_verbs(principal, TOKYO, "claude"),
            (),
        )

    def test_the_grant_is_device_runtime_and_verb_specific(self):
        service = _grant_service(grants={
            f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}": {
                TOKYO: ("send",),
                f"{TOKYO}|claude-code": ("control", "send"),
            },
        })
        principal = Principal(name=f"feishu:{APP}:{TENANT}:{OPEN_COMPANY}",
                              scopes=frozenset({"fleet:send", "fleet:control"}),
                              credential_kind="feishu")
        # The base runtime carries only `send`.
        self.assertEqual(service._operator_grant_verbs(principal, TOKYO, "claude"),
                         ("send",))
        # A second runtime needs its own entry, and does not inherit.
        self.assertEqual(
            set(service._operator_grant_verbs(principal, TOKYO, "claude-code")),
            {"control", "send"},
        )


TOKYO = "device:tokyo-shared-dev-01"


def _grant_service(grants=None):
    """A service over a registry where TOKYO is company-owned.

    Needed because the device axis is checked against `ownership == "company"`:
    over an empty registry every grant would be inert, and the test would pass
    for the wrong reason.
    """
    from fleet_mcp.config import ProviderBinding  # noqa: PLC0415
    from fleet_mcp.registry import load_registry  # noqa: PLC0415

    tmp = Path(tempfile.mkdtemp(prefix="feishu-grant-"))
    anchor = tmp / "pat"
    anchor.write_text("owner-token-value", encoding="utf-8")
    reg = tmp / "registry.yaml"
    reg.write_text(
        "devices:\n"
        f"  - device_ref: {TOKYO}\n"
        f"    display_name: tokyo-shared-dev-01\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        "    ownership: company\n",
        encoding="utf-8",
    )
    config = FleetConfig(
        source=None, local_device_ref=TOKYO,
        providers={TOKYO: ProviderBinding(
            device_ref=TOKYO, kind="cloudcli-mcp-http", endpoint="http://tokyo/mcp",
            credential_anchor=anchor, allow_send=True,
        )},
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(operator_grants=dict(grants or {})),
    )
    return FleetService(config, registry=load_registry(reg), providers={})


if __name__ == "__main__":
    unittest.main()
