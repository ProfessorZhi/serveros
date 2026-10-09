"""MCP Streamable HTTP, plus the OAuth endpoints a connector needs to reach it.

Everything here is the standard library. The surface has to run on a shared host
where installing a web framework is a change to somebody else's environment, and
the protocol is small enough that a framework would be more code to audit than
to write.

Authentication
--------------
Two credentials are accepted, and both are checked before any tool runs:

*   **The owner's personal access token** — the value at `auth.pat_anchor`,
    compared in constant time. This is the operator's own credential, used by
    local clients and as the consent proof at the authorization endpoint. It
    carries both scopes, because it *is* the owner.

*   **An OAuth access token** — a signed blob minted by `oauth.py`. Its scopes
    come from what the operator approved on the consent form and from nothing
    else.

An unauthenticated request gets 401 with `WWW-Authenticate` pointing at the
protected-resource metadata. That header is not decoration: it is what makes a
connector start the OAuth flow instead of reporting the server as broken.

Two things this transport deliberately does not do
--------------------------------------------------
*   **It never fetches a client-supplied URL.** There is no callback, no
    webhook, no "check this endpoint". The only redirection is a `redirect_uri`
    validated against the registered client's exact list. Nothing a caller sends
    can make this process issue a request to an address of their choosing.
*   **It does not enable permissive CORS.** A wildcard
    `Access-Control-Allow-Origin` on an endpoint that accepts bearer tokens lets
    any page in any browser spend the operator's credential. Connectors call
    server-to-server; a browser client can be given a narrow origin explicitly
    if one is ever needed.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .audit import AuditLog
from .errors import FleetRefusal
from .fleet import Principal, SCOPE_READ, SCOPE_SEND
from .oauth import (
    ALLOWED_SCOPES,
    AuthorizationServer,
    OAuthError,
    TokenStore,
    consent_page,
    redirect_with_code,
    redirect_with_error,
)
from .server import McpServer
from .tools import ToolContext
from .version import (
    AUTHORIZATION_SERVER_PATH,
    HEALTH_PATH,
    PROTECTED_RESOURCE_PATH,
    SERVER_NAME,
    SERVER_VERSION,
)

MAX_BODY_BYTES_DEFAULT = 1_000_000


class HttpSurface:
    """Routing for one Fleet MCP endpoint, independent of the socket layer."""

    def __init__(
        self,
        *,
        service,
        config,
        authorize: Callable[[str | None], Principal] | None = None,
        oauth: AuthorizationServer | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.oauth = oauth
        self.audit = audit
        self.max_body_bytes = int(getattr(config.server, "max_body_bytes", MAX_BODY_BYTES_DEFAULT))
        self.mcp_path = getattr(config.server, "mcp_path", "/mcp")
        self._authenticate = authorize or self._default_authenticate

    # -- authentication ----------------------------------------------------

    def _default_authenticate(self, header: str | None) -> Principal:
        """Resolve a bearer credential, or raise `HttpAuthError`."""
        if not header:
            raise HttpAuthError("no Authorization header")
        parts = header.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HttpAuthError("Authorization must be `Bearer <token>`")
        return self.principal_for_token(parts[1].strip())

    def principal_for_token(self, token: str) -> Principal:
        import hmac

        pat = self.config.auth.pat_anchor.read_text(encoding="utf-8").strip() \
            if self.config.auth.pat_anchor and self.config.auth.pat_anchor.exists() else None
        if pat and hmac.compare_digest(pat, token):
            return Principal(
                name="owner-pat",
                scopes=frozenset((SCOPE_READ, SCOPE_SEND)),
                credential_kind="pat",
            )
        if self.oauth is not None:
            try:
                claims = self.oauth.store.verify_access_token(token, audience=self.oauth.base_url)
            except OAuthError as exc:
                raise HttpAuthError(exc.description) from None
            scopes = frozenset((claims.get("scope") or "").split())
            return Principal(
                name=f"oauth:{claims.get('cid', 'unknown')}",
                scopes=scopes,
                credential_kind="oauth",
            )
        raise HttpAuthError("token is not the owner token and no OAuth issuer is configured")

    # -- routing -----------------------------------------------------------

    def handle(self, method: str, path: str, query: str, headers: dict[str, str],
               body: bytes) -> tuple[int, dict[str, str], bytes]:
        """Return (status, headers, body). Never raises for a client-caused error."""
        route = urlparse(path).path.rstrip("/") or "/"

        try:
            if route == HEALTH_PATH:
                return self._health()
            if route == PROTECTED_RESOURCE_PATH or route.startswith(PROTECTED_RESOURCE_PATH + "/"):
                return self._protected_resource()
            if route == AUTHORIZATION_SERVER_PATH or route.startswith(AUTHORIZATION_SERVER_PATH + "/"):
                return self._authorization_server()
            if route == "/oauth/register":
                return self._oauth_register(method, body)
            if route == "/oauth/authorize":
                return self._oauth_authorize(method, query, body)
            if route == "/oauth/token":
                return self._oauth_token(method, body)
            if route == self.mcp_path:
                return self._mcp(method, headers, body)
            if route in ("/", "/docs"):
                return self._docs()
        except OAuthError as exc:
            return _json(exc.status, exc.as_payload())
        except FleetRefusal as exc:
            return _json(400, exc.as_payload())
        except HttpAuthError as exc:
            return self._unauthorized(str(exc))
        except Exception:  # noqa: BLE001
            # A defect in this process. The traceback goes to the unit's stderr,
            # not to the caller: a stack trace in a response body is an
            # information leak dressed as helpfulness.
            traceback.print_exc(file=sys.stderr)
            return _json(500, {"error": "internal_error",
                               "error_description": "the Fleet surface failed unexpectedly; "
                                                    "see the service log"})
        return _json(404, {"error": "not_found", "error_description": f"no route for {route!r}"})

    # -- endpoints ---------------------------------------------------------

    def _health(self) -> tuple[int, dict[str, str], bytes]:
        """Liveness only — deliberately not a fleet report.

        This path is unauthenticated so a supervisor or a tunnel can probe it.
        Anything about devices, sessions or credentials belongs behind the
        token, and putting a summary here would make the tunnel's health check
        an unauthenticated inventory endpoint.
        """
        return _json(200, {
            "status": "ok",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "auth_required": True,
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    def _protected_resource(self) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        return _json(200, self.oauth.protected_resource_metadata(self.mcp_path))

    def _authorization_server(self) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        return _json(200, self.oauth.authorization_server_metadata())

    def _oauth_register(self, method: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        if method != "POST":
            return _json(405, {"error": "invalid_request",
                               "error_description": "registration requires POST"})
        payload = _json_body(body)
        registered = self.oauth.register(payload)
        if self.audit:
            self.audit.record(
                "oauth.register",
                client_id=registered.get("client_id"),
                client_name=registered.get("client_name"),
                redirect_uris=registered.get("redirect_uris"),
            )
        return _json(201, registered)

    def _oauth_authorize(self, method: str, query: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        params = {k: v[0] for k, v in parse_qs(query).items()}
        authorize_path = urlparse(self.oauth.base_url).path or ""

        if method == "GET":
            # Stage one: response_type, client and redirect_uri must be sound
            # *before* anything is rendered, because a bad redirect_uri must
            # never be redirected to.
            try:
                client = self.oauth.validate_authorize(params)
            except OAuthError as exc:
                return _html(exc.status, consent_page(
                    client=_placeholder_client(params), params=params,
                    error=f"{exc.error}: {exc.description}",
                    authorize_path=authorize_path or "/oauth/authorize",
                ))
            return _html(200, consent_page(client=client, params=params,
                                           authorize_path=authorize_path or "/oauth/authorize"))

        if method != "POST":
            return _json(405, {"error": "invalid_request",
                               "error_description": "authorization requires GET or POST"})

        # `scope` arrives repeated, once per checkbox the operator ticked. It is
        # folded back into one space-separated string for the parameter checks
        # and kept as a list for the grant — reading only the first occurrence
        # would silently drop every scope after the first.
        parsed = parse_qs(body.decode("utf-8", "replace"))
        form = {k: (" ".join(v) if k == "scope" else v[0]) for k, v in parsed.items()}
        granted = parsed.get("scope") or []

        # Stage one again: the redirect_uri must be proven sound before any
        # error is allowed to travel through it.
        try:
            self.oauth.validate_authorize(form)
        except OAuthError as exc:
            return _html(exc.status, consent_page(
                client=_placeholder_client(form), params=form,
                error=f"{exc.error}: {exc.description}",
                authorize_path=authorize_path or "/oauth/authorize",
            ))

        try:
            code, client = self.oauth.approve(
                params=form, owner_token=form.get("owner_token", ""), granted=list(granted)
            )
        except OAuthError as exc:
            if exc.error == "access_denied" and "owner token" in exc.description:
                # The operator mistyped their token. Re-present the form rather
                # than bouncing them through a redirect that loses their place.
                return _html(401, consent_page(
                    client=_placeholder_client(form), params=form,
                    error=f"{exc.error}: {exc.description}",
                    authorize_path=authorize_path or "/oauth/authorize",
                ))
            # Any other failure is the client's to hear about, and the
            # redirect_uri is now known good.
            return _redirect(redirect_with_error(
                form.get("redirect_uri", ""), exc.error, exc.description, form.get("state")
            ))
        if self.audit:
            self.audit.record("oauth.approve", client_id=client.client_id,
                              redirect_uri=form.get("redirect_uri"))
        return _redirect(redirect_with_code(form["redirect_uri"], code, form.get("state")))

    def _oauth_token(self, method: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        if method != "POST":
            return _json(405, {"error": "invalid_request", "error_description": "token requires POST"})
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        issued = self.oauth.token(form)
        if self.audit:
            self.audit.record("oauth.token", grant_type=form.get("grant_type"),
                              client_id=form.get("client_id"), scope=issued.get("scope"))
        return _json(200, issued)

    def _mcp(self, method: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], bytes]:
        if method == "GET":
            # Server-initiated streams are not offered: this surface has no
            # out-of-band notifications to push, and holding a socket open for
            # events that never come makes every client's reconnect logic worse.
            return _json(405, {"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32000,
                                         "message": "this endpoint does not offer a server-to-client stream; "
                                                    "POST JSON-RPC messages to it"}})
        if method == "DELETE":
            # Stateless: there is no session to terminate. Answering 204 is the
            # truthful response to a client tidying up.
            return 204, {}, b""
        if method != "POST":
            return _json(405, {"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32000, "message": f"{method} is not supported here"}})

        version = headers.get("mcp-protocol-version")
        if version and version not in ("2025-06-18", "2025-03-26", "2024-11-05"):
            return _json(400, {"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32000,
                                         "message": f"unsupported MCP-Protocol-Version {version!r}"}})

        try:
            principal = self._authenticate(headers.get("authorization"))
        except HttpAuthError as exc:
            return self._unauthorized(str(exc))

        if self.audit:
            self.audit.record("mcp.request", principal=principal.name, bytes=len(body))

        try:
            message = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return _json(400, {"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": f"parse error: {exc}"}})

        # The principal is captured in the closure rather than read inside the
        # dispatcher, so no field of the request can influence it: a tool call
        # naming another credential has no path to one.
        mcp = McpServer(lambda: ToolContext(service=self.service, principal=principal))

        if isinstance(message, list):
            responses = [r for r in (mcp.handle(m) for m in message) if r is not None]
            if not responses:
                return 202, {}, b""
            return _json(200, responses)

        response = mcp.handle(message)
        if response is None:
            # A notification: 202 with no body, per the streamable-HTTP rules.
            return 202, {}, b""
        return _json(200, response)

    def _unauthorized(self, detail: str) -> tuple[int, dict[str, str], bytes]:
        """401 with the metadata pointer a connector follows to start OAuth."""
        resource_metadata = (
            f"{self.oauth.base_url}{PROTECTED_RESOURCE_PATH}"
            if self.oauth is not None
            else f"http://{self.config.server.bind}:{self.config.server.port}{PROTECTED_RESOURCE_PATH}"
        )
        challenge = f'Bearer resource_metadata="{resource_metadata}"'
        if self.audit:
            self.audit.record("mcp.unauthorized", detail=detail)
        return _json(
            401,
            {"error": "invalid_token", "error_description": detail},
            {"WWW-Authenticate": challenge},
        )

    def _docs(self) -> tuple[int, dict[str, str], bytes]:
        body = {
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocol": "MCP Streamable HTTP (JSON-RPC 2.0)",
            "endpoints": {
                self.mcp_path: "POST JSON-RPC. Requires `Authorization: Bearer <token>`.",
                HEALTH_PATH: "Unauthenticated liveness. No fleet information.",
                PROTECTED_RESOURCE_PATH: "RFC 9728 protected-resource metadata.",
                AUTHORIZATION_SERVER_PATH: "RFC 8414 authorization-server metadata.",
                "/oauth/register": "RFC 7591 dynamic client registration (public clients, PKCE).",
                "/oauth/authorize": "Authorization endpoint. Requires the owner token to approve.",
                "/oauth/token": "Token endpoint. authorization_code and refresh_token.",
            },
        }
        return _json(200, body)


class HttpAuthError(Exception):
    """The presented credential is missing, malformed or not accepted."""


def _json_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise OAuthError("invalid_client_metadata", "body must be a JSON object") from None
    if not isinstance(payload, dict):
        raise OAuthError("invalid_client_metadata", "body must be a JSON object")
    return payload


def _placeholder_client(params: dict[str, str]):
    from .oauth import RegisteredClient

    return RegisteredClient(
        client_id=params.get("client_id") or "(unknown client)",
        client_name=params.get("client_id") or "unknown client",
        redirect_uris=(params.get("redirect_uri"),) if params.get("redirect_uri") else (),
    )


def _json(status: int, payload: Any, extra_headers: dict[str, str] | None = None):
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(raw)),
        "Cache-Control": "no-store",
        # A response that names a device must never be stored by a proxy.
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
    headers.update(extra_headers or {})
    return status, headers, raw


def _html(status: int, markup: str):
    raw = markup.encode("utf-8")
    return status, {
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(raw)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        # The consent form carries an owner token. Nothing on the page may load
        # from, or post to, anywhere but here.
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    }, raw


def _redirect(location: str):
    return 302, {"Location": location, "Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer"}, b""


class _Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"
    surface: HttpSurface  # injected on the server class

    def _respond(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        if status not in (204, 304) and "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 0:
            length = 0
        if length > self.surface.max_body_bytes:
            # Read and discard a bounded amount so the connection is not left
            # mid-request, then refuse. The alternative — reading it all — is
            # how an unauthenticated POST becomes a memory problem.
            remaining = min(length, 8 * 1024 * 1024)
            try:
                self.rfile.read(remaining)
            except OSError:
                pass
            raise _BodyTooLarge(length)
        return self.rfile.read(length) if length else b""

    def _dispatch(self) -> None:
        try:
            body = self._read_body()
        except _BodyTooLarge as exc:
            self._respond(*_json(413, {
                "error": "request_too_large",
                "error_description": f"body of {exc.length} bytes exceeds the "
                                     f"{self.surface.max_body_bytes} byte limit",
            }))
            self.close_connection = True
            return
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            status, out_headers, out_body = self.surface.handle(
                self.command, self.path, urlparse(self.path).query, headers, body
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            status, out_headers, out_body = _json(500, {"error": "internal_error"})
        self._respond(status, out_headers, out_body)

    do_GET = do_POST = do_DELETE = do_HEAD = _dispatch

    def log_message(self, fmt: str, *args: Any) -> None:
        # Access logging goes to stderr without the query string: an
        # authorization code and a `state` travel in a query, and a log line is
        # a copy of a credential that outlives it.
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


class _BodyTooLarge(Exception):
    def __init__(self, length: int) -> None:
        super().__init__(str(length))
        self.length = length


def serve(surface: HttpSurface, *, bind: str, port: int) -> ThreadingHTTPServer:
    handler = type("FleetHandler", (_Handler,), {"surface": surface})
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    # A probe from a tunnel or a supervisor must not be able to hold a worker
    # forever on a half-open connection.
    server.timeout = 30
    return server


def build_oauth(config, *, base_url: str, store_root) -> AuthorizationServer:
    """Wire the OAuth issuer. Refuses when there is no owner credential.

    An authorization server that cannot authenticate the human approving a
    client is a public token dispenser, so this raises rather than degrading to
    an unauthenticated consent form.
    """
    if not config.auth.oauth_enabled:
        raise FleetRefusal(
            "BAD_REQUEST",
            "oauth_enabled is false in the local binding; a ChatGPT connector "
            "cannot authenticate without it",
        )
    if config.auth.pat_anchor is None or not config.auth.pat_anchor.exists():
        raise FleetRefusal(
            "BAD_REQUEST",
            "auth.pat_anchor is not set to a readable file. The consent step "
            "proves the human approving a client is the owner by requiring that "
            "token; with no owner credential configured there is nothing to "
            "prove it with, and this surface will not issue tokens to anonymous "
            "clients.",
        )

    def read_pat() -> str | None:
        try:
            return config.auth.pat_anchor.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    store = TokenStore(store_root, access_ttl=config.auth.access_token_ttl,
                       refresh_ttl=config.auth.refresh_token_ttl)
    return AuthorizationServer(store=store, auth=config.auth, base_url=base_url, pat_reader=read_pat)


def local_base_url(*, bind: str, port: int, public_base_url: str | None) -> str:
    if public_base_url:
        return public_base_url.rstrip("/")
    host = bind if bind not in ("0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{port}"


def probe_port_free(bind: str, port: int) -> bool:
    """True when nothing is listening on `bind:port`."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((bind, port)) != 0


__all__ = [
    "ALLOWED_SCOPES",
    "HttpAuthError",
    "HttpSurface",
    "build_oauth",
    "local_base_url",
    "probe_port_free",
    "serve",
]
