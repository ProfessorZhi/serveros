"""A second consent proof: a whitelisted Feishu identity, instead of a PAT.

Why this exists
---------------
The consent gate in `oauth.py` proves "the human approving this client is the
owner" by requiring the owner's personal access token. That is a good proof and
a bad experience: it asks a person to copy a bearer secret out of a file and
paste it into a form. This module is the other proof, for the people the owner
has written down: a Feishu sign-in, verified server-side, whose result is an
*identity* rather than a shared secret.

Two things this deliberately does not do
----------------------------------------
**It does not turn a login into authority.** A whitelisted identity holds no
device verb at all. It becomes a principal — `feishu:<app>:<tenant>:<user_id>`
— and the principal is subject to the same `policy.operator_grants` lookup every
other principal is. With no entry there, the identity is `PENDING_GRANT`: it can
connect, and every operation is refused by the operator grant. This is the whole
difference between "signed in" and "authorized", and it is why the identity's
scopes are not the thing that matters here.

**It does not trust the browser.** The browser carries a `code` and a `state`
and nothing else. No `open_id`, no name, no phone number, no tenant ever arrives
from the client. The identity is read from the Feishu API by this process, over
a connection that never involved the client, and an exchange that does not
return a stable user id is a refusal rather than a guess.

The identity key
----------------
`feishu:<app_scope>:<tenant_key>:<open_id>`, and every part matters:

*   **app_scope** — which Feishu application this is. Two applications are two
    namespaces, and an `open_id` from one means nothing in the other.
*   **tenant_key** — an `open_id` is unique *within a tenant*, not across
    tenants. Matching on the id alone would let the same string from another
    organization through, so the tenant is matched and a mismatch is a named
    refusal.
*   **open_id** — the stable user id. A display name is a claim the API makes
    and can change; it is never part of the key and never carried.

`union_id` is recorded when the API returns one but is never used to match
across applications or tenants — it is documented as stable per developer, not
per organization, and building a cross-tenant match on an assumption nobody has
verified is exactly the failure this module exists to avoid.

Credentials
-----------
The binding holds a **path** to the app secret, never the value. This module
opens that file only at the moment of an exchange, only through a reader the
caller supplies, and never logs, returns or copies what it read.
"""
from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

# The provider name, and the first part of every identity key this module makes.
PROVIDER = "feishu"
# The key separator. A colon cannot appear in any of the three parts (each is
# validated below), so a key cannot be forged by smuggling one in.
SEPARATOR = ":"

# Where Feishu's endpoints are *by default*. These are defaults, not facts about
# a deployment: which authorize/token/userinfo paths a given application may use
# is a Feishu-console question this repository has not verified, so every one is
# overridable in the binding and nothing here is load-bearing. The issue that
# introduced this module records them as UNVERIFIED.
DEFAULT_BASE_URL = "https://open.feishu.cn"
DEFAULT_AUTHORIZE_PATH = "/open-apis/authen/v1/authorize"
DEFAULT_TOKEN_PATH = "/open-apis/authen/v2/oauth/token"
DEFAULT_USERINFO_PATH = "/open-apis/authen/v1/user_info"

DEFAULT_TRANSACTION_TTL = 300.0
DEFAULT_REQUEST_TIMEOUT = 10.0


class SSOError(Exception):
    """A refusal on the Feishu leg. Never carries a secret or a raw payload."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description


# --------------------------------------------------------------------------
# The identity
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeishuIdentity:
    """A stable identity, and only what is stable about it.

    Constructed in exactly one place — `FeishuBroker.exchange_code` — from the
    payload the Feishu API returned to this process. There is no constructor
    path from a request body, which is the property that makes "the browser
    cannot supply an identity" true rather than aspirational.
    """

    app_scope: str
    tenant_key: str
    user_id: str
    union_id: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("app_scope", self.app_scope), ("tenant_key", self.tenant_key),
                            ("user_id", self.user_id)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if SEPARATOR in value:
                raise ValueError(
                    f"{name} contains the key separator {SEPARATOR!r}; a part that "
                    f"can contain it can forge another identity's key"
                )

    @property
    def key(self) -> str:
        return SEPARATOR.join((PROVIDER, self.app_scope, self.tenant_key, self.user_id))

    @property
    def principal_name(self) -> str:
        """The name this identity presents to `policy.operator_grants`."""
        return self.key

    def as_dict(self) -> dict[str, Any]:
        # No display name, email, phone or avatar: those are claims, not
        # identity, and carrying them is how they end up in a log or a decision.
        return {
            "identity_key": self.key,
            "provider": PROVIDER,
            "app_scope": self.app_scope,
            "tenant_key": self.tenant_key,
            "user_id": self.user_id,
            "union_id": self.union_id,
        }


@dataclass(frozen=True)
class FeishuWhitelistEntry:
    """One identity an operator has written down, and the tenant it must be in."""

    tenant_key: str
    user_id: str
    app_scope: str = ""
    label: str = ""


@dataclass(frozen=True)
class Verdict:
    """Whether an identity is admitted, and if not, which question said no.

    The reason is kept distinct from the answer because the three refusals send
    an operator to three different places: an unknown user is a missing entry, a
    wrong tenant is an entry pointing at the wrong organization, and a wrong app
    is the wrong application entirely.
    """

    allowed: bool
    reason: str
    entry: FeishuWhitelistEntry | None = None


# --------------------------------------------------------------------------
# The binding
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FeishuBinding:
    """Everything the broker needs, and no secret.

    `secret_anchor` is a path. Nothing in this module — or in `config.py`, which
    builds this object — reads a byte from it. The value reaches the exchange
    through a `secret_reader` the caller supplies, at the moment of use.
    """

    enabled: bool = False
    app_scope: str = ""
    app_id: str | None = None
    secret_anchor: Path | None = None
    redirect_uri: str | None = None
    base_url: str = DEFAULT_BASE_URL
    authorize_path: str = DEFAULT_AUTHORIZE_PATH
    token_path: str = DEFAULT_TOKEN_PATH
    userinfo_path: str = DEFAULT_USERINFO_PATH
    scope: str | None = None
    whitelist: tuple[FeishuWhitelistEntry, ...] = ()
    transaction_ttl: float = DEFAULT_TRANSACTION_TTL
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT

    def authorize_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.authorize_path.lstrip("/")

    def token_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.token_path.lstrip("/")

    def userinfo_endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/" + self.userinfo_path.lstrip("/")


# --------------------------------------------------------------------------
# The one place this module touches the network — and it is injectable.
# --------------------------------------------------------------------------


def _urlopen(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (https only)


class FeishuHTTP:
    """POST/GET JSON. The default opens a socket; a test supplies an opener.

    Everything the broker knows about the transport lives here, so a test that
    passes a fake opener exercises the whole exchange path with no socket and no
    credential.
    """

    def __init__(self, *, opener: Any | None = None) -> None:
        self._open = opener if opener is not None else _urlopen

    def post_json(self, url: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._read(request, timeout)

    def get_json(self, url: str, *, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        return self._read(request, timeout)

    def _read(self, request: urllib.request.Request, timeout: float) -> dict[str, Any]:
        # `self._open`, not the module function: the injected opener has to be
        # the one that runs, or a test that supplied a fake would quietly reach
        # the network instead — which is the failure this seam exists to make
        # impossible.
        try:
            with self._open(request, timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:  # a 4xx/5xx with a body
            try:
                body = exc.read()
            except Exception:  # noqa: BLE001
                body = b""
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SSOError("network_error", f"the Feishu endpoint could not be reached: {type(exc).__name__}") from None
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise SSOError("bad_response", "the Feishu endpoint did not answer with JSON") from None
        if not isinstance(parsed, dict):
            raise SSOError("bad_response", "the Feishu endpoint answered with a non-object")
        return parsed


# --------------------------------------------------------------------------
# The one-time transaction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingAuthorization:
    """A ChatGPT authorization request, held server-side between two redirects."""

    feishu_state: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    scopes: tuple[str, ...]
    resource: str | None
    client_state: str | None
    created_at: float
    expires_at: float

    def authorize_params(self) -> dict[str, str]:
        """The original request, reconstructed for `approve_identity`.

        Rebuilt from the server's own record, never from the callback's query —
        which is what makes the scope, the client and the redirect the ones the
        client actually asked for rather than the ones the last hop supplied.
        """
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code_challenge": self.code_challenge,
            "code_challenge_method": self.code_challenge_method,
            "scope": " ".join(self.scopes),
        }
        if self.client_state:
            params["state"] = self.client_state
        if self.resource:
            params["resource"] = self.resource
        return params


class TransactionStore:
    """Server-side, single-use, time-bounded. In memory, like the auth codes.

    The store is what binds the two halves of the flow together: the ChatGPT
    request that started it and the Feishu callback that finishes it. Because
    the binding lives here and never travels through the browser, a callback
    cannot substitute a different client, redirect, scope or PKCE challenge —
    it can only present the opaque `state` this server issued, once.
    """

    def __init__(self, *, ttl: float = DEFAULT_TRANSACTION_TTL,
                 now: Callable[[], float] = time.time) -> None:
        self.ttl = max(30.0, float(ttl))
        self._now = now
        self._pending: dict[str, PendingAuthorization] = {}

    def begin(self, *, client_id: str, redirect_uri: str, code_challenge: str,
              scopes: tuple[str, ...], resource: str | None = None,
              client_state: str | None = None,
              code_challenge_method: str = "S256") -> PendingAuthorization:
        state = "fst-" + secrets.token_urlsafe(24)
        now = self._now()
        txn = PendingAuthorization(
            feishu_state=state, client_id=client_id, redirect_uri=redirect_uri,
            code_challenge=code_challenge, code_challenge_method=code_challenge_method,
            scopes=tuple(scopes), resource=resource, client_state=client_state,
            created_at=now, expires_at=now + self.ttl,
        )
        self._discard_expired(now)
        self._pending[state] = txn
        return txn

    def consume(self, feishu_state: str) -> PendingAuthorization:
        """Take the transaction, once. A second call with the same state fails."""
        now = self._now()
        self._discard_expired(now)
        txn = self._pending.pop(str(feishu_state or ""), None)
        if txn is None:
            raise SSOError(
                "invalid_state",
                "this sign-in state is unknown or has already been used; the "
                "sign-in must be started again",
            )
        if txn.expires_at <= now:
            raise SSOError("expired_state", "this sign-in state has expired")
        return txn

    def discard(self, feishu_state: str) -> None:
        self._pending.pop(str(feishu_state or ""), None)

    def __len__(self) -> int:
        return len(self._pending)

    def _discard_expired(self, now: float) -> None:
        stale = [s for s, t in self._pending.items() if t.expires_at <= now]
        for state in stale:
            self._pending.pop(state, None)


# --------------------------------------------------------------------------
# The broker
# --------------------------------------------------------------------------


class FeishuBroker:
    """Turns a Feishu authorization code into an identity, or refuses."""

    def __init__(self, *, binding: FeishuBinding, http: FeishuHTTP,
                 secret_reader: Callable[[], str | None]) -> None:
        self.binding = binding
        self.http = http
        self._secret_reader = secret_reader
        self.transactions = TransactionStore(ttl=binding.transaction_ttl)

    @property
    def enabled(self) -> bool:
        return bool(self.binding.enabled)

    # -- the redirect to Feishu -------------------------------------------

    def authorize_url(self, *, state: str) -> str | None:
        """Where to send the browser. `None` when the broker is disabled.

        `None` rather than an exception because a disabled broker must be
        indistinguishable from no broker at all: the caller turns it into the
        same 404 an unknown route gets.
        """
        if not self.enabled:
            return None
        params = {
            "app_id": self.binding.app_id or "",
            "redirect_uri": self.binding.redirect_uri or "",
            "response_type": "code",
            "state": state,
        }
        if self.binding.scope:
            params["scope"] = self.binding.scope
        return f"{self.binding.authorize_endpoint()}?{urlencode(params)}"

    # -- the code, exchanged server-side ----------------------------------

    def exchange_code(self, code: str) -> FeishuIdentity:
        """`code` -> identity. The only path to a `FeishuIdentity`.

        The secret is read here and now, held in a local, and never stored on
        the broker. A refusal is raised before the network is touched whenever
        the configuration cannot carry the request out.
        """
        if not self.enabled:
            raise SSOError("not_enabled", "Feishu sign-in is not enabled on this deployment")
        if not code:
            raise SSOError("invalid_request", "the callback carried no authorization code")
        secret = self._secret_reader()
        if not secret:
            # Refused *before* calling out: with no secret there is nothing to
            # authenticate the exchange with, and a request to Feishu that could
            # only fail is a request whose failure would be read as Feishu's.
            raise SSOError("not_configured", "no Feishu app secret is configured at the anchor")
        token = self.http.post_json(
            self.binding.token_endpoint(),
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.binding.app_id or "",
                "client_secret": secret,
                "redirect_uri": self.binding.redirect_uri or "",
            },
            timeout=self.binding.request_timeout,
        )
        user_token = _access_token_from(token)
        if not user_token:
            raise SSOError("exchange_failed", "Feishu did not return a user access token")
        info = self.http.get_json(
            self.binding.userinfo_endpoint(),
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=self.binding.request_timeout,
        )
        return self._identity_from(info)

    def _identity_from(self, info: dict[str, Any]) -> FeishuIdentity:
        payload = info.get("data") if isinstance(info.get("data"), dict) else info
        code = payload.get("code", info.get("code", 0))
        if code not in (0, None):
            raise SSOError("identity_failed", "Feishu refused the user-info request")
        user_id = str(payload.get("open_id") or payload.get("sub") or "").strip()
        if not user_id:
            raise SSOError(
                "identity_failed",
                "Feishu returned no stable user id; a code is not an identity and "
                "a display name is not either",
            )
        tenant_key = str(payload.get("tenant_key") or payload.get("tenant") or "").strip()
        if not tenant_key:
            # Fail closed. Without a tenant there is nothing to match an identity
            # against, and admitting on an unverified tenant is the cross-tenant
            # assumption this broker exists to refuse.
            raise SSOError(
                "identity_failed",
                "Feishu returned no tenant for this identity, so it cannot be "
                "matched against the whitelist",
            )
        union_id = str(payload.get("union_id") or "").strip() or None
        return FeishuIdentity(
            app_scope=self.binding.app_scope,
            tenant_key=tenant_key,
            user_id=user_id,
            union_id=union_id,
        )

    # -- the whitelist -----------------------------------------------------

    def verdict(self, identity: FeishuIdentity) -> Verdict:
        """Whether this identity is one the operator wrote down.

        Three questions, three answers, in the order that makes the refusal
        actionable: is this application ours, is this person written down, and
        is this person in the tenant we wrote them down for.
        """
        entries = self.binding.whitelist
        if not entries:
            return Verdict(False, "not_configured")
        for entry in entries:
            if entry.app_scope != identity.app_scope or entry.user_id != identity.user_id:
                continue
            if entry.tenant_key == identity.tenant_key:
                return Verdict(True, "allowed", entry)
            return Verdict(False, "wrong_tenant", entry)
        if not any(entry.app_scope == identity.app_scope for entry in entries):
            return Verdict(False, "wrong_app")
        return Verdict(False, "unknown_identity")

    def admits(self, identity: FeishuIdentity) -> bool:
        return self.verdict(identity).allowed


def _access_token_from(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("code") not in (0, None):
        return ""
    direct = payload.get("access_token")
    if isinstance(direct, str) and direct:
        return direct
    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("access_token")
        if isinstance(nested, str) and nested:
            return nested
    return ""


# --------------------------------------------------------------------------
# Construction
# --------------------------------------------------------------------------


def build_feishu(config: Any, *, http: FeishuHTTP | None = None,
                 secret_reader: Callable[[], str | None] | None = None) -> FeishuBroker | None:
    """The broker for this deployment, or `None` when Feishu sign-in is off.

    Absent configuration is `None`, which every caller treats as "no such
    route". That is what makes the default deployment — no `auth.feishu` block —
    behave exactly as it did before this module existed.
    """
    binding = getattr(getattr(config, "auth", None), "feishu", None)
    if binding is None or not binding.enabled:
        return None

    def _read_from_anchor() -> str | None:
        anchor = binding.secret_anchor
        if anchor is None:
            return None
        try:
            return anchor.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    return FeishuBroker(
        binding=binding,
        http=http if http is not None else FeishuHTTP(),
        secret_reader=secret_reader if secret_reader is not None else _read_from_anchor,
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_TRANSACTION_TTL",
    "FeishuBinding",
    "FeishuBroker",
    "FeishuHTTP",
    "FeishuIdentity",
    "FeishuWhitelistEntry",
    "PendingAuthorization",
    "PROVIDER",
    "SSOError",
    "TransactionStore",
    "Verdict",
    "build_feishu",
]
