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
The scopes are `fleet:read`, `fleet:send` and `fleet:create`, and they are
granted one at a time by the operator on the consent form. Neither write scope
is a superset of the other: a credential issued to message sessions is not
thereby one that may mint them, and the form asks for the two separately for
exactly that reason. Both are only ever issued against a local binding that
already authorizes the act — `fleet:send` for sessions in `send_allow`,
`fleet:create` for devices with `allow_create` — so the token's scope is
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
# The vocabulary lives in `scopes.py`. Imported rather than re-spelled: this
# module used to hold its own literal copy of the three names, and a copy is how
# a scope nothing implements ends up advertised on the wire — nothing forces two
# lists to agree, so eventually they do not.
from .scopes import ALLOWED_SCOPES  # noqa: F401  (re-exported; see `__all__`)
from .version import AUTHORIZATION_SERVER_PATH, PROTECTED_RESOURCE_PATH, SERVER_NAME

# Access-token lifetime. Short on purpose: the refresh path exists precisely so
# this can be short, and a leaked access token that dies in an hour is a much
# smaller problem than one that dies in a month.
DEFAULT_ACCESS_TTL = 3600
AUTH_CODE_TTL = 120
# Refresh tokens are opaque and carry this prefix, which is how the revocation
# endpoint tells the two token kinds apart without a lookup that would make it
# answer differently for a token it never issued.
REFRESH_PREFIX = "frt-"


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
    # The protected resource this grant was approved for. Carried across the
    # chain so a refreshed access token keeps the audience the human consented
    # to, rather than quietly acquiring one from wherever the refresh happened.
    resource: str | None = None
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


def _revocations_from(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """The revocation lists, in the two buckets they mean different things in.

    `clients` — the operator revoked a client outright. No new authorization, no
    refresh, and none of its access tokens work again until somebody removes the
    entry by hand.

    `grants`  — the tokens minted under one approval were retired: an operator
    revoked that refresh token, or a rotated refresh token was replayed, which is
    the signature of a stolen chain. Every token already issued dies, and the
    client is free to authorize again — which is the recovery the reuse-detection
    path is supposed to leave open, and which folding the two buckets together
    would close.
    """
    stored = raw.get("revocations") or {}
    out: dict[str, dict[str, float]] = {"clients": {}, "grants": {}, "tokens": {}}
    for bucket in ("clients", "grants", "tokens"):
        for key, value in (stored.get(bucket) or {}).items():
            try:
                out[bucket][str(key)] = float(value)
            except (TypeError, ValueError):
                continue
    return out


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
        self._clients, self._refresh, self._revocations = self._load_state()
        self._revocation_stamp: tuple[int, int] | None = None

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

    def _load_state(self) -> tuple[dict[str, RegisteredClient], dict[str, RefreshRecord],
                                   dict[str, dict[str, float]]]:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}, {}, _revocations_from({})
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
                resource=row.get("resource"),
                rotated_to=row.get("rotated_to"),
                revoked=bool(row.get("revoked")),
            )
            for token, row in (raw.get("refresh") or {}).items()
            if isinstance(row, dict)
        }
        return clients, refresh, _revocations_from(raw)

    def _save_state(self) -> None:
        # Revoked-token entries are dropped once the token's own `exp` has
        # passed: past expiry the signature check rejects it anyway, and keeping
        # the list forever turns a revocation list into a slowly growing file.
        now = time.time()
        self._revocations["tokens"] = {
            jti: exp for jti, exp in self._revocations["tokens"].items() if exp > now
        }
        payload = {
            "clients": [c.as_dict() for c in self._clients.values()],
            "refresh": {
                token: {
                    "client_id": rec.client_id,
                    "subject": rec.subject,
                    "scopes": list(rec.scopes),
                    "expires_at": rec.expires_at,
                    "resource": rec.resource,
                    "rotated_to": rec.rotated_to,
                    "revoked": rec.revoked,
                }
                for token, rec in self._refresh.items()
            },
            "revocations": self._revocations,
        }
        raw = json.dumps(payload, indent=2).encode("utf-8")
        # Written beside and renamed over: two processes touch this file — the
        # running surface, and `--revoke-client` run by an operator — and a
        # reader that catches a half-written file would silently fall back to
        # "nothing has been revoked", which is the one wrong answer.
        temporary = self._state_path.with_name(self._state_path.name + ".tmp")
        self._write_private(temporary, raw)
        os.replace(temporary, self._state_path)
        self._revocation_stamp = None

    def _reload_revocations_if_changed(self) -> None:
        """Pick up a revocation made by another process.

        The access token is a signed blob with no server-side row, so the only
        thing that can retire it is a check against this list — and the list is
        also written by `serveros-fleet-mcp --revoke-client`, which is a
        different process from the one serving requests. Re-reading on a change
        of (mtime, size) costs one stat per call and is what makes an operator's
        revocation take effect on the running surface without a restart.
        """
        try:
            stat = self._state_path.stat()
        except OSError:
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._revocation_stamp:
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            # Mid-rename, or not written yet. Keep what we have and try again on
            # the next call rather than treating an unreadable file as amnesty.
            return
        self._revocations = _revocations_from(raw)
        self._revocation_stamp = stamp

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
        self._reload_revocations_if_changed()
        client = self._clients.get(client_id)
        if client is None:
            raise OAuthError("invalid_client", "unknown client_id", status=401)
        if client_id in self._revocations["clients"]:
            # A revoked client may not start a new authorization. Saying so here,
            # at the first endpoint that names it, is what makes revocation mean
            # "this client is done" rather than "its old tokens are done".
            raise OAuthError("invalid_client", "this client has been revoked", status=401)
        return client

    def clients(self) -> list[dict[str, Any]]:
        """Every registered client, with its revocation state. Operator-facing."""
        self._reload_revocations_if_changed()
        now = time.time()
        rows = []
        for client in sorted(self._clients.values(), key=lambda c: c.created_at):
            revoked_at = self._revocations["clients"].get(client.client_id)
            live_refresh = sum(
                1 for rec in self._refresh.values()
                if rec.client_id == client.client_id and not rec.revoked and rec.expires_at > now
            )
            rows.append({
                **client.as_dict(),
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(client.created_at)),
                "revoked": revoked_at is not None,
                "revoked_at": (
                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(revoked_at))
                    if revoked_at is not None else None
                ),
                "live_refresh_tokens": live_refresh,
            })
        return rows

    # -- access tokens -----------------------------------------------------

    def mint_access_token(self, *, client_id: str, subject: str, scopes: tuple[str, ...],
                          audience: str, issuer: str | None = None) -> tuple[str, int]:
        now = int(time.time())
        payload = {
            # `iss` is the authorization server and `aud` is the protected
            # resource. They are different URLs whenever the resource is mounted
            # below the issuer's root, and collapsing them — which the earlier
            # revision did by passing one value to both — is what stopped a
            # client from proving which resource it was handed the token for.
            "iss": issuer or audience,
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

    def signed_claims(self, token: str) -> dict[str, Any] | None:
        """The payload of a token whose signature verifies, or None.

        Deliberately says nothing about `aud`, `exp` or revocation: it exists for
        the one caller that has to read a token's `jti` in order to revoke it
        (RFC 7009 accepts a token that has already expired), and for that caller
        the only question is whether this server minted the blob.
        """
        parts = token.split(".")
        if len(parts) != 3:
            return None
        signing_input = f"{parts[0]}.{parts[1]}"
        expected = hmac.new(self._key, signing_input.encode("ascii"), hashlib.sha256).digest()
        try:
            presented = _unb64(parts[2])
        except Exception:
            return None
        # Constant-time, so a caller cannot learn the signature byte by byte.
        if not hmac.compare_digest(expected, presented):
            return None
        try:
            payload = json.loads(_unb64(parts[1]))
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def verify_access_token(self, token: str, *, audience: str) -> dict[str, Any]:
        payload = self.signed_claims(token)
        if payload is None:
            raise OAuthError("invalid_token", "signature does not verify", status=401)
        if payload.get("aud") != audience:
            raise OAuthError(
                "invalid_token",
                "this token was issued for a different audience",
                status=401,
            )
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise OAuthError("invalid_token", "token expired", status=401)
        self._reload_revocations_if_changed()
        self._refuse_if_revoked(payload)
        return payload

    def _refuse_if_revoked(self, payload: dict[str, Any]) -> None:
        """The declared revocation semantics: a revoked client's tokens die now.

        Three things can be revoked, and every one is checked on each call rather
        than being folded into the token at mint time — an access token is a
        signed blob with no server-side row, so a token that carried its own
        revocation state would stay valid until it expired, which is exactly the
        behaviour the earlier revision had and the defect is.

        *   **A client**: every access token it holds stops working. Compared on
            `iat <= revoked_at`, so a token issued in the same second as the
            revocation is treated as revoked — erring towards refusing.
        *   **A grant** (one approval, all the tokens minted under it): every
            access token from that approval dies, which is what revoking a
            refresh token has to mean for a stateless access token.
        *   **One access token, by `jti`**: the single blob is dead.
        """
        client_id = str(payload.get("cid"))
        issued_at = int(payload.get("iat", 0))
        for bucket, message in (
            ("clients", "this client was revoked; its tokens no longer work"),
            ("grants", "this client's tokens were revoked; authorize again"),
        ):
            revoked_at = self._revocations[bucket].get(client_id)
            if revoked_at is not None and issued_at <= int(revoked_at):
                raise OAuthError("invalid_token", message, status=401)
        jti = payload.get("jti")
        if jti and str(jti) in self._revocations["tokens"]:
            raise OAuthError("invalid_token", "this token was revoked", status=401)

    # -- revocation (RFC 7009) ---------------------------------------------

    def revoke_client(self, client_id: str) -> dict[str, Any]:
        """Revoke a client: its tokens die and its refresh chains die with it."""
        with self._lock:
            client = self._clients.get(client_id)
            if client is None:
                raise OAuthError("invalid_client", "unknown client_id", status=404)
            now = time.time()
            self._revocations["clients"][client_id] = now
            killed = 0
            for record in self._refresh.values():
                if record.client_id == client_id and not record.revoked:
                    record.revoked = True
                    killed += 1
            self._save_state()
        return {
            "client_id": client_id,
            "client_name": client.client_name,
            "revoked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "refresh_tokens_revoked": killed,
            "access_tokens": "every token issued to this client before the revocation is refused from now on",
        }

    def revoke_presented_token(self, token: str, *, client_id: str | None = None) -> dict[str, Any]:
        """Revoke whatever this token is, based on what the server can verify.

        The response is the same for a token that was revoked and one this server
        never issued. That is RFC 7009's instruction and it is also the only way
        to keep the endpoint from answering "did this client ever get a token",
        which is a question an unauthenticated caller has no business asking.
        """
        token = token.strip()
        if not token:
            raise OAuthError("invalid_request", "token is required")
        if token.startswith(REFRESH_PREFIX):
            self._reload_revocations_if_changed()
            record = self._refresh.get(token)
            if record is not None and (client_id is None or record.client_id == client_id):
                with self._lock:
                    self._revoke_chain_locked(token)
                    # The chain is only half of it. Every access token already
                    # minted from this grant is a signed blob this server will
                    # honour until it expires, so revoking the grant has to say
                    # so somewhere the validator looks — that is this timestamp.
                    self._revocations["grants"][record.client_id] = time.time()
                    self._save_state()
                return {"revoked": "refresh_token", "client_id": record.client_id}
            return {"revoked": "none"}
        claims = self.signed_claims(token)
        if claims is None:
            return {"revoked": "none"}
        jti = claims.get("jti")
        exp = float(claims.get("exp", 0))
        if not jti or exp <= time.time():
            return {"revoked": "none"}
        with self._lock:
            self._revocations["tokens"][str(jti)] = exp
            self._save_state()
        return {"revoked": "access_token", "client_id": claims.get("cid")}

    # -- refresh -----------------------------------------------------------

    def mint_refresh_token(self, *, client_id: str, subject: str,
                           scopes: tuple[str, ...], resource: str | None = None) -> str:
        token = REFRESH_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            self._refresh[token] = RefreshRecord(
                client_id=client_id,
                subject=subject,
                scopes=scopes,
                expires_at=time.time() + self.refresh_ttl,
                resource=resource,
            )
            self._save_state()
        return token

    def consume_refresh_token(self, token: str, *, client_id: str) -> RefreshRecord:
        self._reload_revocations_if_changed()
        with self._lock:
            if client_id in self._revocations["clients"]:
                raise OAuthError(
                    "invalid_grant",
                    "this client was revoked and may not refresh; authorize again",
                    status=400,
                )
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
                # Deliberately not extended to this client's outstanding access
                # tokens. A replayed rotated token has one benign cause — a
                # client with two refreshes in flight — and retiring every live
                # access token on that signal would make a race look like a
                # breach. The chain dies, which is the recovery this path was
                # built for; an operator who reads the replay as a real leak has
                # `--revoke-client`, which does reach the access tokens.
                self._save_state()
                raise OAuthError(
                    "invalid_grant",
                    "refresh token was already used; the session has been revoked "
                    "and the client must authorize again",
                    status=400,
                )
            return record

    def rotate_refresh_token(self, old_token: str, record: RefreshRecord) -> str:
        new_token = REFRESH_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            current = self._refresh.get(old_token)
            if current is not None:
                current.rotated_to = new_token
            self._refresh[new_token] = RefreshRecord(
                client_id=record.client_id,
                subject=record.subject,
                scopes=record.scopes,
                expires_at=time.time() + self.refresh_ttl,
                resource=record.resource,
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
                 authorize_path: str = "/oauth/authorize",
                 granted: list[str] | None = None) -> str:
    """The form the operator completes. Plain HTML, no script, no external refs.

    `granted` names the boxes to render ticked. It is None on the first render,
    when the client's own request is what to pre-check, and it is the operator's
    actual ticks when the form is re-shown after a failed submit — because a
    re-render that silently re-ticked a box the operator had cleared would ask
    for consent to something it had just been told not to do.
    """
    rows = []
    for scope in ALLOWED_SCOPES:
        requested = scope in (params.get("scope") or "").split()
        ticked = scope in granted if granted is not None else requested
        checked = "checked" if ticked else ""
        disabled = "" if requested else "disabled"
        label = {
            "fleet:read": "Read this fleet — list devices and sessions, and read session history",
            "fleet:send": "Send messages to sessions the operator has already authorized",
            "fleet:create": "Create new sessions on devices the operator has authorized for creation",
        }.get(scope, scope)
        rows.append(
            f'<label class="scope"><input type="checkbox" name="scope" value="{html.escape(scope)}" '
            f'{checked} {disabled}> <b>{html.escape(scope)}</b><br><span>{html.escape(label)}</span></label>'
        )
    # The requested scope is carried in its own field, `requested_scope`, and
    # never under the name `scope`. That separation is the whole of the fix for
    # the obvious bug in the earlier revision: the checkboxes are `name="scope"`
    # and arrive once per scope the operator actually ticked, so if the request
    # also round-tripped as a hidden field named `scope`, a client could ask for
    # `fleet:send`, the operator could leave the box unticked, and the POST would
    # still carry the scope the user never granted. Two fields, two meanings:
    # `requested_scope` is what the client asked for, `scope` is what the human
    # approved.
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params.items()
        if k in ("response_type", "client_id", "redirect_uri", "state", "code_challenge",
                 "code_challenge_method", "resource")
    )
    hidden += (
        f'<input type="hidden" name="requested_scope" '
        f'value="{html.escape(params.get("scope") or "")}">'
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
enrolled in this ServerOS fleet. Approving issues tokens to <b>this client</b>.
Only the boxes you tick below are granted. You can revoke this client later at any
time: <code>serveros-fleet-mcp --list-clients</code> then
<code>--revoke-client &lt;id&gt;</code>, or POST a token to <code>/oauth/revoke</code>.
Revoking makes the client's existing access tokens stop working immediately.</p>
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
                 pat_reader, mcp_path: str = "/mcp") -> None:
        self.store = store
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.mcp_path = mcp_path if mcp_path.startswith("/") else "/" + mcp_path
        self._pat_reader = pat_reader  # () -> str | None
        self._codes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- discovery ---------------------------------------------------------

    @property
    def resource_url(self) -> str:
        """The one protected resource this issuer speaks for.

        RFC 8707 calls it the resource indicator, RFC 9728 publishes it as
        `resource`, and it is the token's `aud`. It is derived from the base URL
        that is actually advertised — so when `server.public_base_url` names a
        public HTTPS address, discovery, the token audience and the audience the
        validator demands all move together instead of the validator keeping a
        private opinion about a loopback address.
        """
        return f"{self.base_url}{self.mcp_path}"

    def protected_resource_metadata(self, mcp_path: str | None = None) -> dict[str, Any]:
        resource = f"{self.base_url}{mcp_path}" if mcp_path else self.resource_url
        return {
            "resource": resource,
            "authorization_servers": [self.base_url],
            "bearer_methods_supported": ["header"],
            "scopes_supported": list(ALLOWED_SCOPES),
            # What `scopes_supported` does not say: which of those this
            # *deployment* will actually grant. A client that asks for
            # `fleet:create` where the binding leaves `grantable_scopes` at its
            # default is refused at the authorize step, and a refusal the client
            # could have read from discovery is a worse experience than one it
            # never triggers. Additive, so a client that reads only the standard
            # key is unaffected.
            "scopes_grantable": list(self.auth.grantable_scopes),
            "resource_documentation": f"{self.base_url}/docs",
        }

    def authorization_server_metadata(self) -> dict[str, Any]:
        return {
            "issuer": self.base_url,
            "authorization_endpoint": f"{self.base_url}/oauth/authorize",
            "token_endpoint": f"{self.base_url}/oauth/token",
            "registration_endpoint": f"{self.base_url}/oauth/register",
            "revocation_endpoint": f"{self.base_url}/oauth/revoke",
            "scopes_supported": list(ALLOWED_SCOPES),
            "scopes_grantable": list(self.auth.grantable_scopes),
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"],
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
        # RFC 8707: a client may name the resource it wants a token for, and the
        # authorization server must refuse a resource it does not serve rather
        # than quietly issuing a token for something else. Absent or equal is
        # fine; anything else is `invalid_target`.
        resource = (params.get("resource") or "").strip()
        if resource and resource.rstrip("/") != self.resource_url.rstrip("/"):
            raise OAuthError(
                "invalid_target",
                f"this authorization server issues tokens for {self.resource_url!r} only, "
                f"not {resource!r}",
            )
        return client

    def revoke(self, form: dict[str, str]) -> dict[str, Any]:
        """RFC 7009. Always answers the same way for a token it does not know."""
        token = (form.get("token") or "").strip()
        client_id = (form.get("client_id") or "").strip() or None
        if not token:
            raise OAuthError("invalid_request", "token is required")
        if not self.store.signed_claims(token) and not token.startswith(REFRESH_PREFIX):
            # Not a token this server could have issued. RFC 7009 says this is
            # not an error, and the reason is worth writing down: a 4xx here
            # would turn an unauthenticated endpoint into an oracle that answers
            # "is this string one of your tokens".
            return {"revoked": "none"}
        result = self.store.revoke_presented_token(token, client_id=client_id)
        return result

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
        issued = self._issue(client_id=client_id, scopes=record["scopes"], subject="operator",
                             resource=record.get("resource"))
        # A code exchange issues a refresh chain, which is what makes the short
        # access-token lifetime workable: the caller renews without a second
        # trip through the consent form.
        issued["refresh_token"] = self.store.mint_refresh_token(
            client_id=client_id, subject="operator", scopes=record["scopes"],
            resource=record.get("resource"),
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
        issued = self._issue(client_id=client_id, scopes=scopes, subject=record.subject,
                             resource=record.resource)
        issued["refresh_token"] = new_refresh
        issued["refresh_token_expires_in"] = self.store.refresh_ttl
        return issued

    def _issue(self, *, client_id: str, scopes: tuple[str, ...], subject: str,
               resource: str | None = None) -> dict[str, Any]:
        # The audience is always this issuer's one protected resource, never a
        # value the caller supplied. `resource` only carries forward what the
        # approved grant was for; validate_authorize has already refused anything
        # that is not this resource, so the two cannot disagree.
        audience = resource or self.resource_url
        access, ttl = self.store.mint_access_token(
            client_id=client_id, subject=subject, scopes=scopes,
            audience=audience, issuer=self.base_url,
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
    "REFRESH_PREFIX",
    "TokenStore",
    "consent_page",
    "metadata_paths",
    "redirect_with_code",
    "redirect_with_error",
    "store_root_for",
    "_validate_redirect_uri",
]
