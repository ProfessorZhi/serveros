"""OAuth 2.1 for the Fleet MCP endpoint — authorization code + PKCE + DCR.

Why this is here at all
-----------------------
ChatGPT custom connectors do not accept a long-lived bearer token pasted into
a form. They require an MCP server that speaks OAuth: discovery metadata, dynamic
client registration, an authorization-code flow with PKCE, and refresh. A
surface that answers 401 without metadata is not connectable no matter how
correct its tools are.

The consent gate, and what "human grant" means here
---------------------------------------------------
The authorization endpoint does not hand out tokens to whoever asks. It serves a
form that only the operator can complete, because the one thing required to
approve a client is **possession of the owner's personal access token** — a
secret that lives in a file on this host (`auth.pat_anchor`) and is never in the
browser, never in this repository, and never sent anywhere except back to this
process over TLS.

That is deliberate. This code cannot manufacture a human's consent: there is no
default-approved path, no bootstrap token, no environment variable that skips
the form. If `pat_anchor` is unset, OAuth is refused outright rather than
falling back to anonymous issuance — an authorization server with no way to
authenticate the human approving a client is a public token dispenser.

What is granted
---------------
The scopes are `fleet:read` and `fleet:send`, and they are granted one at a
time by the operator on the consent form. `fleet:send` is only ever issued for
sessions already allow-listed in the local binding: the token's scope is
necessary and never sufficient.

Token format
------------
Access tokens are HMAC-SHA256 signed compact blobs, verified by signature, so
they survive a restart without a session table. Refresh tokens are opaque and
stored, so they can be rotated with reuse detection — the property that makes a
stolen refresh token recoverable rather than permanent. The signing key is
generated on first use at 0600 and never leaves the host.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from .config import AuthBinding
from .version import AUTHORIZATION_SERVER_PATH, PROTECTED_RESOURCE_PATH, SERVER_NAME

# Access-token lifetime. Short on purpose: the refresh path exists precisely so
# this can be short, and a leaked access token that dies in an hour is a much
# smaller problem than one that dies in a month.
DEFAULT_ACCESS_TTL = 3600
AUTH_CODE_TTL = 120
ALLOWED_SCOPES = ("fleet:read", "fleet:send")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass
class RegisteredClient:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]
    created_at: float = field(default_factory=time.time)
    token_endpoint_auth_method: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "client_name": self.client_name,
            "redirect_uris": list(self.redirect_uris),
            "token_endpoint_auth_method": self.token_endpoint_auth_method,
        }


@dataclass
class RefreshRecord:
    client_id: str
    subject: str
    scopes: tuple[str, ...]
    expires_at: float
    # Set when this token has been rotated. A second use of a rotated token is
    # the signature of a stolen one, and is treated as a breach of the whole
    # chain rather than of one token.
    rotated_to: str | None = None
    revoked: bool = False


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.status = status

    def as_payload(self) -> dict[str, str]:
        return {"error": self.error, "error_description": self.description}


class TokenStore:
    """Signing key, clients and refresh chains. Lives at 0600, outside git."""

    def __init__(self, root: Path, *, access_ttl: int, refresh_ttl: int) -> None:
        self.root = root
        self.access_ttl = max(60, int(access_ttl))
        self.refresh_ttl = max(600, int(refresh_ttl))
        self._lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)
        self._key_path = root / "signing.key"
        self._state_path = root / "state.json"
        self._key = self._load_or_create_key()
        self._clients, self._refresh = self._load_state()

    # -- persistence -------------------------------------------------------

    def _load_or_create_key(self) -> bytes:
        try:
            existing = self._key_path.read_bytes()
            if len(existing) >= 32:
                return existing
        except FileNotFoundError:
            pass
        key = secrets.token_bytes(32)
        self._write_private(self._key_path, key)
        return key

    @staticmethod
    def _write_private(path: Path, data: bytes) -> None:
        """Write owner-only.

        On POSIX the mode is set at creation. On Windows `os.chmod` only toggles
        the read-only bit, so the mode call is best-effort — the honest
        statement is that file mode is not the protection boundary on Windows,
        the ACL on the user profile directory is.
        """
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _load_state(self) -> tuple[dict[str, RegisteredClient], dict[str, RefreshRecord]]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}, {}
        clients = {
            row["client_id"]: RegisteredClient(
                client_id=row["client_id"],
                client_name=row.get("client_name", ""),
                redirect_uris=tuple(row.get("redirect_uris") or ()),
                created_at=row.get("created_at", time.time()),
                token_endpoint_auth_method=row.get("token_endpoint_auth_method", "none"),
            )
            for row in raw.get("clients") or []
            if isinstance(row, dict) and row.get("client_id")
        }
        refresh = {
            token: RefreshRecord(
                client_id=row["client_id"],
                subject=row.get("subject", "operator"),
                scopes=tuple(row.get("scopes") or ()),
                expires_at=row.get("expires_at", 0.0),
                rotated_to=row.get("rotated_to"),
                revoked=bool(row.get("revoked")),
            )
            for token, row in (raw.get("refresh") or {}).items()
            if isinstance(row, dict)
        }
        return clients, refresh

    def _save_state(self) -> None:
        payload = {
            "clients": [c.as_dict() for c in self._clients.values()],
            "refresh": {
                token: {
                    "client_id": rec.client_id,
                    "subject": rec.subject,
                    "scopes": list(rec.scopes),
                    "expires_at": rec.expires_at,
                    "rotated_to": rec.rotated_to,
                    "revoked": rec.revoked,
                }
                for token, rec in self._refresh.items()
            },
        }
        self._write_private(self._state_path, json.dumps(payload, indent=2).encode("utf-8"))

    # -- clients -----------------------------------------------------------

    def register_client(self, *, client_name: str, redirect_uris: list[str]) -> RegisteredClient:
        if not redirect_uris:
            raise OAuthError("invalid_redirect_uri", "at least one redirect_uri is required")
        for uri in redirect_uris:
            _validate_redirect_uri(uri)
        client = RegisteredClient(
            client_id="fleet-" + secrets.token_urlsafe(18),
            client_name=client_name or "unnamed client",
            redirect_uris=tuple(redirect_uris),
        )
        with self._lock:
            self._clients[client.client_id] = client
            self._save_state()
        return client

    def client(self, client_id: str) -> RegisteredClient:
        client = self._clients.get(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id", status=401)
        return client

    # -- access tokens -----------------------------------------------------

    def mint_access_token(self, *, client_id: str, subject: str, scopes: tuple[str, ...],
                          audience: str) -> tuple[str, int]:
        now = int(time.time())
        payload = {
            "iss": audience,
            "sub": subject,
            "aud": audience,
            "cid": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": now + self.access_ttl,
            "jti": secrets.token_urlsafe(12),
        }
        header = {"alg": "HS256", "typ": "at+jwt"}
        signing_input = (
            _b64(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + _b64(json.dumps(payload, separators=(",", ":")).encode())
        )
        signature = hmac.new(self._key, signing_input.encode("ascii"), hashlib.sha256).digest()
        return f"{signing_input}.{_b64(signature)}", self.access_ttl

    def verify_access_token(self, token: str, *, audience: str) -> dict[str, Any]:
        parts = token.split(".")
        if len(parts) != 3:
            raise OAuthError("invalid_token", "malformed token", status=401)
        signing_input = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(self._key, signing_input.encode("ascii"), hashlib.sha256).digest()
        try:
            presented = _unb64(parts[2])
        except Exception:
            raise OAuthError("invalid_token", "malformed signature", status=401) from None
        # Constant-time, so a caller cannot learn the signature byte by byte.
        if not hmac.compare_digest(expected, presented):
            raise OAuthError("invalid_token", "signature does not verify", status=401)
        try:
            payload = json.loads(_unb64(parts[1]))
        except Exception:
            raise OAuthError("invalid_token", "malformed payload", status=401) from None
        if payload.get("aud") != audience:
            raise OAuthError(
                "invalid_token",
                "this token was issued for a different audience",
                status=401,
            )
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise OAuthError("invalid_token", "token expired", status=401)
        return payload

    # -- refresh -----------------------------------------------------------

    def mint_refresh_token(self, *, client_id: str, subject: str,
                           scopes: tuple[str, ...]) -> str:
        token = "frt-" + secrets.token_urlsafe(32)
        with self._lock:
            self._refresh[token] = RefreshRecord(
                client_id=client_id,
                subject=subject,
                scopes=scopes,
                expires_at=time.time() + self.refresh_ttl,
            )
            self._save_state()
        return token

    def consume_refresh_token(self, token: str, *, client_id: str) -> RefreshRecord:
        with self._lock:
            record = self._refresh.get(token)
            if record is None:
                raise OAuthError("invalid_grant", "unknown refresh token", status=400)
            if record.revoked:
                raise OAuthError("invalid_grant", "refresh token revoked", status=400)
            if record.client_id != client_id:
                raise OAuthError("invalid_grant", "refresh token belongs to another client", status=400)
            if record.expires_at <= time.time():
                raise OAuthError("invalid_grant", "refresh token expired", status=400)
            if record.rotated_to:
                # Reuse of a rotated token. The only safe reading is that the
                # chain is compromised, so the whole chain dies — the caller has
                # to re-authorize, which is the correct cost.
                self._revoke_chain_locked(token)
                self._save_state()
                raise OAuthError(
                    "invalid_grant",
                    "refresh token was already used; the session has been revoked "
                    "and the client must authorize again",
                    status=400,
                )
            return record

    def rotate_refresh_token(self, old_token: str, record: RefreshRecord) -> str:
        new_token = "frt-" + secrets.token_urlsafe(32)
        with self._lock:
            current = self._refresh.get(old_token)
            if current is not None:
                current.rotated_to = new_token
            self._refresh[new_token] = RefreshRecord(
                client_id=record.client_id,
                subject=record.subject,
                scopes=record.scopes,
                expires_at=time.time() + self.refresh_ttl,
            )
            self._save_state()
        return new_token

    def _revoke_chain_locked(self, token: str) -> None:
        """Follow `rotated_to` forward and revoke everything downstream."""
        seen: set[str] = set()
        cursor: str | None = token
        while cursor and cursor not in seen:
            seen.add(cursor)
            record = self._refresh.get(cursor)
            if record is None:
                break
            record.revoked = True
            cursor = record.rotated_to


# --------------------------------------------------------------------------
# Authorization endpoint: the consent gate
# --------------------------------------------------------------------------


def _validate_redirect_uri(uri: str) -> None:
    """Only https, or http to a loopback address.

    Without this the authorization endpoint is an open redirect that can be
    pointed at any host, and a code — which becomes a token — would be handed to
    it. Loopback http is allowed because that is how a local client receives a
    code without a certificate.
    """
    parsed = urlparse(uri)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost"):
        return
    raise OAuthError(
        "invalid_redirect_uri",
        f"redirect_uri {uri!r} must be https, or http on loopback",
    )


def consent_page(*, client: RegisteredClient, params: dict[str, str], error: str | None = None,
                 authorize_path: str = "/oauth/authorize") -> str:
    """The form the operator completes. Plain HTML, no script, no external refs."""
    rows = []
    for scope in ALLOWED_SCOPES:
        requested = scope in (params.get("scope") or "").split()
        checked = "checked" if requested else ""
        disabled = "" if requested else "disabled"
        label = {
            "fleet:read": "Read this fleet — list devices and sessions, and read session history",
            "fleet:send": "Send messages to sessions the operator has already authorized",
        }.get(scope, scope)
        rows.append(
            f'<label class="scope"><input type="checkbox" name="scope" value="{html.escape(scope)}" '
            f'{checked} {disabled}> <b>{html.escape(scope)}</b><br><span>{html.escape(label)}</span></label>'
        )
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params.items()
        if k in ("response_type", "client_id", "redirect_uri", "state", "code_challenge",
                 "code_challenge_method", "resource", "scope")
    )
    requested_note = params.get("scope") or "(no scope requested)"
    error_html = (
        f'<p class="error">{html.escape(error)}</p>' if error else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Authorize {html.escape(client.client_name)} · {html.escape(SERVER_NAME)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1.25rem;color:#1b1b1f}}
 h1{{font-size:1.25rem}} .muted{{color:#5b5b66}} .error{{color:#a3232b;font-weight:600}}
 .scope{{display:block;border:1px solid #d8d8de;border-radius:8px;padding:.75rem 1rem;margin:.5rem 0}}
 input[type=password]{{width:100%;padding:.6rem;border:1px solid #c8c8d0;border-radius:6px;font:inherit}}
 button{{margin-top:1.25rem;padding:.7rem 1.4rem;border:0;border-radius:8px;background:#1b1b1f;color:#fff;font:inherit;cursor:pointer}}
 dl{{display:grid;grid-template-columns:auto 1fr;gap:.25rem 1rem;font-size:.9rem}}
 dt{{color:#5b5b66}} dd{{margin:0;word-break:break-all}}
</style></head><body>
<h1>Authorize <b>{html.escape(client.client_name)}</b> to use {html.escape(SERVER_NAME)}</h1>
{error_html}
<p class="muted">This client is asking for access to the Claude Code / Codex sessions
enrolled in this ServerOS fleet. Approving issues tokens to <b>this client</b>;
you can revoke them by removing <code>state.json</code> and restarting.</p>
<dl>
 <dt>Requested</dt><dd>{html.escape(requested_note)}</dd>
 <dt>Redirects to</dt><dd>{html.escape(', '.join(client.redirect_uris))}</dd>
</dl>
<form method="post" action="{html.escape(authorize_path)}">
 {hidden}
 {''.join(rows)}
 <p><label for="pat"><b>Owner access token</b></label><br>
 <span class="muted">Required. This is the token at the configured
 <code>pat_anchor</code> on the machine running this server. It is checked
 here and never stored, logged or forwarded.</span></p>
 <input type="password" id="pat" name="owner_token" autocomplete="off" required>
 <button type="submit">Approve</button>
</form>
</body></html>
"""


# --------------------------------------------------------------------------
# Authorization server behaviour
# --------------------------------------------------------------------------


class AuthorizationServer:
    def __init__(self, *, store: TokenStore, auth: AuthBinding, base_url: str,
                 pat_reader) -> None:
        self.store = store
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self._pat_reader = pat_reader  # () -> str | None
        self._codes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- discovery ---------------------------------------------------------

    def protected_resource_metadata(self, mcp_path: str) -> dict[str, Any]:
        return {
            "resource": f"{self.base_url}{mcp_path}",
            "authorization_servers": [self.base_url],
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(ALLOWED_SCOPES),
            "resource_documentation": f"{self.base_url}/docs",
        }

    def authorization_server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.base_url,
            "authorization_endpoint": f"{self.base_url}/oauth/authorize",
            "token_endpoint": f"{self.base_url}/oauth/token",
            "registration_endpoint": f"{self.base_url}/oauth/register",
            "scopes_supported": list(ALLOWED_SCOPES),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "service_documentation": f"{self.base_url}/docs",
        }

    # -- dynamic client registration --------------------------------------

    def register(self, body: dict[str, Any]) -> dict[str, Any]:
        redirect_uris = body.get("redirect_uris")
        if not isinstance(redirect_uris, list):
            raise OAuthError("invalid_redirect_uri", "redirect_uris must be an array")
        method = body.get("token_endpoint_auth_method") or "none"
        if method not in ("none",):
            raise OAuthError(
                "invalid_client_metadata",
                "only public clients (token_endpoint_auth_method: none) are accepted; "
                "a confidential client on a self-hosted server would need a secret "
                "this server has no way to keep from the browser it hands it to",
            )
        client = self.store.register_client(
            client_name=str(body.get("client_name") or "unnamed client"),
            redirect_uris=[str(u) for u in redirect_uris],
        )
        return {
            **client.as_dict(),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(ALLOWED_SCOPES),
        }

    # -- authorize ---------------------------------------------------------

    def validate_authorize(self, params: dict[str, str]) -> RegisteredClient:
        if params.get("response_type") != "code":
            raise OAuthError("unsupported_response_type", "only response_type=code is supported")
        client = self.store.client(params.get("client_id") or "")
        redirect_uri = params.get("redirect_uri") or ""
        if redirect_uri not in client.redirect_uris:
            # Never redirect on a mismatch — that is the open-redirect bug. The
            # error is shown here, to the browser, instead.
            raise OAuthError(
                "invalid_request",
                "redirect_uri does not match the one registered for this client",
            )
        method = params.get("code_challenge_method")
        if method != "S256" or not params.get("code_challenge"):
            raise OAuthError(
                "invalid_request",
                "PKCE with code_challenge_method=S256 is required",
            )
        requested = set((params.get("scope") or "").split())
        if not requested:
            raise OAuthError("invalid_scope", "at least one scope must be requested")
        unknown = requested - set(ALLOWED_SCOPES)
        if unknown:
            raise OAuthError("invalid_scope", f"unsupported scope(s): {sorted(unknown)}")
        ungrantable = requested - set(self.auth.grantable_scopes)
        if ungrantable:
            raise OAuthError(
                "invalid_scope",
                f"this deployment does not grant {sorted(ungrantable)}",
            )
        return client

    def approve(self, *, params: dict[str, str], owner_token: str,
                granted: list[str]) -> tuple[str, RegisteredClient]:
        """The human step. Returns (code, client) or raises."""
        client = self.validate_authorize(params)
        if not self._check_owner(owner_token):
            raise OAuthError("access_denied", "owner token did not verify", status=401)
        requested = set((params.get("scope") or "").split())
        scopes = tuple(sorted(requested & set(granted) & set(self.auth.grantable_scopes)))
        if not scopes:
            raise OAuthError(
                "access_denied",
                "no scope was granted; a token with no scope would let the client "
                "connect and then fail every call, which is worse than a refusal",
                status=400,
            )
        code = "fac-" + secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = {
                "client_id": client.client_id,
                "redirect_uri": params.get("redirect_uri"),
                "scopes": scopes,
                "code_challenge": params.get("code_challenge"),
                "resource": params.get("resource"),
                "expires_at": time.time() + AUTH_CODE_TTL,
            }
        return code, client

    def _check_owner(self, presented: str) -> bool:
        expected = self._pat_reader()
        if not expected or not presented:
            return False
        return hmac.compare_digest(expected.strip(), presented.strip())

    # -- token -------------------------------------------------------------

    def token(self, form: dict[str, str]) -> dict[str, Any]:
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            return self._token_from_code(form)
        if grant_type == "refresh_token":
            return self._token_from_refresh(form)
        raise OAuthError("unsupported_grant_type", f"grant_type {grant_type!r} is not supported")

    def _token_from_code(self, form: dict[str, str]) -> dict[str, Any]:
        code = form.get("code") or ""
        verifier = form.get("code_verifier") or ""
        client_id = form.get("client_id") or ""
        with self._lock:
            record = self._codes.pop(code, None)
        if record is None:
            raise OAuthError("invalid_grant", "unknown or already-used authorization code")
        if record["expires_at"] <= time.time():
            raise OAuthError("invalid_grant", "authorization code expired")
        if record["client_id"] != client_id:
            raise OAuthError("invalid_grant", "authorization code was issued to another client")
        if form.get("redirect_uri") and form["redirect_uri"] != record["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
        if not _pkce_matches(verifier, record["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed")
        issued = self._issue(client_id=client_id, scopes=record["scopes"], subject="operator")
        # A code exchange issues a refresh chain, which is what makes the short
        # access-token lifetime workable: the caller renews without a second
        # trip through the consent form.
        issued["refresh_token"] = self.store.mint_refresh_token(
            client_id=client_id, subject="operator", scopes=record["scopes"]
        )
        issued["refresh_token_expires_in"] = self.store.refresh_ttl
        return issued

    def _token_from_refresh(self, form: dict[str, str]) -> dict[str, Any]:
        token = form.get("refresh_token") or ""
        client_id = form.get("client_id") or ""
        record = self.store.consume_refresh_token(token, client_id=client_id)
        requested = set((form.get("scope") or "").split())
        scopes = record.scopes
        if requested:
            # A refresh may narrow scope and may never widen it. Widening on
            # refresh would let a client that was granted read obtain send
            # without anyone approving it a second time.
            if not requested <= set(record.scopes):
                raise OAuthError(
                    "invalid_scope",
                    "a refresh cannot widen the granted scope; re-authorize instead",
                )
            scopes = tuple(sorted(requested))
        new_refresh = self.store.rotate_refresh_token(token, record)
        issued = self._issue(client_id=client_id, scopes=scopes, subject=record.subject)
        issued["refresh_token"] = new_refresh
        issued["refresh_token_expires_in"] = self.store.refresh_ttl
        return issued

    def _issue(self, *, client_id: str, scopes: tuple[str, ...], subject: str) -> dict[str, Any]:
        access, ttl = self.store.mint_access_token(
            client_id=client_id, subject=subject, scopes=scopes, audience=self.base_url
        )
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": ttl,
            "scope": " ".join(scopes),
        }


def _pkce_matches(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return hmac.compare_digest(_b64(digest), challenge)


def redirect_with_code(redirect_uri: str, code: str, state: str | None) -> str:
    params = {"code": code}
    if state:
        params["state"] = state
    joiner = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{joiner}{urlencode(params)}"


def redirect_with_error(redirect_uri: str, error: str, description: str,
                        state: str | None) -> str:
    params = {"error": error, "error_description": description}
    if state:
        params["state"] = state
    joiner = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{joiner}{urlencode(params)}"


def metadata_paths() -> tuple[str, str]:
    return PROTECTED_RESOURCE_PATH, AUTHORIZATION_SERVER_PATH


def store_root_for(anchor: Path | None, fallback: Path) -> Path:
    """Where OAuth state lives: next to the binding, never in the repository."""
    if anchor is not None:
        return anchor.parent / "fleet-mcp-oauth"
    return fallback


__all__ = [
    "ALLOWED_SCOPES",
    "AUTHORIZATION_SERVER_PATH",
    "AUTH_CODE_TTL",
    "AuthorizationServer",
    "DEFAULT_ACCESS_TTL",
    "OAuthError",
    "PROTECTED_RESOURCE_PATH",
    "TokenStore",
    "consent_page",
    "metadata_paths",
    "redirect_with_code",
    "redirect_with_error",
    "store_root_for",
    "_validate_redirect_uri",
]
