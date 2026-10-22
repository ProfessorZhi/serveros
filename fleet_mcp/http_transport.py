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
import re
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import human_auth
from .audit import AuditLog
from .errors import FleetRefusal
from .feishu_sso import FeishuBroker, SSOError, build_feishu
from .fleet import Principal, SCOPE_CREATE, SCOPE_READ, SCOPE_SEND
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
        feishu: FeishuBroker | None = None,
    ) -> None:
        self.service = service
        self.config = config
        self.oauth = oauth
        self.audit = audit
        # The Feishu sign-in broker, or `None`. Built from the binding when one
        # is not supplied, so the two ways of having no Feishu route — no block,
        # and a disabled block — both arrive here as `None` and both make the
        # two routes 404 exactly as an unknown path does.
        self.feishu = feishu if feishu is not None else build_feishu(config)
        # Password login is a property of the binding, not of this class: a
        # deployment that names no `password_anchor` keeps the owner-token form
        # exactly as it was, and one that names one gets the login form.
        self.human = None
        if oauth is not None and config.auth.password_anchor is not None:
            self.human = human_auth.HumanLogin.from_paths(
                password_anchor=config.auth.password_anchor,
                totp_anchor=config.auth.totp_anchor,
                replay_path=oauth.store.root / "login-replay.json",
                key=oauth.store.signing_key,
                session_ttl=config.auth.session_ttl,
            )
        self.max_body_bytes = int(getattr(config.server, "max_body_bytes", MAX_BODY_BYTES_DEFAULT))
        self.mcp_path = getattr(config.server, "mcp_path", "/mcp")
        # When the advertised base URL carries a path — the shape a deployment
        # gets when it is mounted below a host's root, e.g. behind a proxy that
        # does not strip the prefix — every endpoint is reachable under it as
        # well as without it. Both forms route to the same handler, so a
        # loopback probe keeps working while the public URL also resolves.
        advertised = urlparse(str(getattr(config.server, "public_base_url", "") or "")).path
        self.path_prefix = advertised.rstrip("/") if advertised.rstrip("/") != "/" else ""
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

        # `pat_enabled: false` is a config edit, not a code path: the anchor may
        # still exist and still be valid, and it simply stops being consulted.
        # A deployment that turns the PAT back on needs no redeploy of a
        # different build.
        pat = None
        if self.config.auth.pat_enabled and self.config.auth.pat_anchor \
                and self.config.auth.pat_anchor.exists():
            pat = self.config.auth.pat_anchor.read_text(encoding="utf-8").strip()
        if pat and hmac.compare_digest(pat, token):
            # The owner's own token carries all three scopes. `fleet:create` is
            # not a widening of what this credential can reach: creation is
            # gated per device by `allow_create` *and* per project by
            # `policy.create_allow`, both of which refuse by default, so the
            # operator holds the scope for exactly the projects they authorized
            # and nothing more.
            #
            # This is deliberately not the same question as `grantable_scopes`.
            # That tuple is the ceiling on what this deployment will *hand out*
            # — to an OAuth client, on the consent form — and it defaults to
            # read+send so a connector is never minted with creation authority
            # nobody asked for. Withholding `fleet:create` from the operator's
            # own credential would buy nothing and cost the whole feature: the
            # local tools would be the one caller unable to do what the binding
            # is configured to permit.
            return Principal(
                name="owner-pat",
                scopes=frozenset((SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)),
                credential_kind="pat",
            )
        if self.oauth is not None:
            try:
                # Verified against the protected resource, not the issuer. A
                # token minted for another audience — or one minted before this
                # deployment moved its public URL — is refused here rather than
                # being accepted because it happens to be signed by this key.
                claims = self.oauth.store.verify_access_token(
                    token, audience=self.oauth.resource_url
                )
            except OAuthError as exc:
                raise HttpAuthError(exc.description) from None
            scopes = frozenset((claims.get("scope") or "").split())
            # A token minted through the Feishu broker names the *identity*,
            # not the client. That is the whole point of the second proof: the
            # same client can be used by two different people, and
            # `policy.operator_grants` has to be able to tell them apart.
            if str(claims.get("idp") or "") == "feishu":
                subject = str(claims.get("sub") or "")
                if not subject.startswith("feishu:"):
                    raise HttpAuthError(
                        "a Feishu identity token did not carry a Feishu subject"
                    )
                return Principal(name=subject, scopes=scopes, credential_kind="feishu")
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
        route = urlparse(path).path
        prefix = self.path_prefix
        if prefix and (route == prefix or route.startswith(prefix + "/")):
            route = route[len(prefix):]
        route = route.rstrip("/") or "/"

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
                return self._oauth_authorize(method, query, body, headers)
            if route == "/oauth/authorize/logout":
                return self._oauth_logout()
            if route == "/oauth/feishu/start":
                return self._oauth_feishu_start(method, query)
            if route == "/oauth/feishu/callback":
                return self._oauth_feishu_callback(method, query)
            if route == "/oauth/token":
                return self._oauth_token(method, body)
            if route == "/oauth/revoke":
                return self._oauth_revoke(method, body)
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
        try:
            payload = _json_body(body)
            registered = self.oauth.register(payload)
        except OAuthError as exc:
            # A refusal is answered and forgotten here: the body goes back to the
            # caller and nothing of the request survives in this process, so the
            # audit is the only place an operator can later read *why* a client
            # was turned away. What is written is the error code alone — a closed
            # word from the RFC's own set, and the same field the refusal body
            # already returned, so it tells the party that caused it nothing it
            # did not already hold. `exc.description` is deliberately NOT
            # recorded: it is free text this surface built out of the caller's
            # input (for `invalid_redirect_uri` it quotes the redirect_uri), and
            # no other part of the request is recorded either. The error is
            # re-raised untouched, so the status and the JSON body are exactly
            # what they were before this line existed.
            if self.audit:
                self.audit.record("oauth.register.refused", error=exc.error)
            raise
        if self.audit:
            self.audit.record(
                "oauth.register",
                client_id=registered.get("client_id"),
                client_name=registered.get("client_name"),
                redirect_uris=registered.get("redirect_uris"),
            )
        return _json(201, registered)

    def _oauth_authorize(self, method: str, query: str, body: bytes,
                         headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        params = {k: v[0] for k, v in parse_qs(query).items()}
        authorize_path = urlparse(self.oauth.base_url).path or ""
        cookie = _cookie_value(headers or {}, human_auth.COOKIE_NAME)
        login_user = self.human.user(cookie) if self.human is not None else None
        secure_cookie = self.oauth.base_url.startswith("https://")

        def render(status: int, markup_client, *, form: dict[str, str],
                   error: str | None = None, granted: list[str] | None = None):
            return _html(status, consent_page(
                client=markup_client, params=form, error=error,
                authorize_path=authorize_path or "/oauth/authorize",
                granted=granted,
                login_mode="password" if self.human is not None else "pat",
                login_user=login_user,
                csrf=self.human.csrf(cookie) if (self.human is not None and login_user) else None,
                totp_required=bool(self.human is not None and self.human.totp_required),
                feishu_start_path=(
                    f"{authorize_path}/oauth/feishu/start"
                    if self.feishu is not None and self.feishu.enabled else None
                ),
            ), form_action=_redirect_origin(form.get("redirect_uri", "")))

        if method == "GET":
            # Stage one: response_type, client and redirect_uri must be sound
            # *before* anything is rendered, because a bad redirect_uri must
            # never be redirected to.
            try:
                client = self.oauth.validate_authorize(params)
            except OAuthError as exc:
                return render(exc.status, _placeholder_client(params), form=params,
                              error=f"{exc.error}: {exc.description}")
            return render(200, client, form=params)

        if method != "POST":
            return _json(405, {"error": "invalid_request",
                               "error_description": "authorization requires GET or POST"})

        # Two fields, two meanings, and the separation is the point:
        #
        #   `requested_scope`  what the client asked for — rendered into the form
        #                      by consent_page as a hidden field.
        #   `scope`            what the operator ticked, arriving once per box.
        #
        # The earlier revision round-tripped the requested scope under the name
        # `scope` as well, so unticking a box changed nothing: the hidden field
        # re-supplied it and it was granted. Reading the request from its own
        # field is what stops a grant the human did not make.
        parsed = parse_qs(body.decode("utf-8", "replace"))
        requested = " ".join(parsed.get("requested_scope") or [])
        if not requested:
            # A client that posts the authorization request directly — the test
            # harness does, and so would any non-browser caller — has only one
            # place to put it. Treat its `scope` as the request, which is what it
            # always was for a caller that is not this page.
            requested = " ".join(parsed.get("scope") or [])
        form = {k: v[0] for k, v in parsed.items()}
        form["scope"] = requested
        granted = [s.strip() for s in parsed.get("scope") or [] if s.strip()]

        # Stage one again: the redirect_uri must be proven sound before any
        # error is allowed to travel through it.
        try:
            self.oauth.validate_authorize(form)
        except OAuthError as exc:
            return render(exc.status, _placeholder_client(form), form=form,
                          error=f"{exc.error}: {exc.description}", granted=granted)

        # Stage two: who is authorising. Two shapes, one page:
        #
        #   password mode  a session cookie, or username+password[+TOTP] on
        #                  this submit. The cookie is what makes three clients
        #                  cost one sign-in.
        #   PAT mode       the single owner token, unchanged.
        verified_user: str | None = None
        fresh_cookie: str | None = None
        extra: dict[str, str] = {}

        if self.human is not None:
            if login_user is not None:
                # A cookie was present, so the form carried a CSRF token bound
                # to it and this submit must present the matching one. Without
                # this check any page on the internet could POST this consent
                # form on behalf of a signed-in browser.
                if not self.human.csrf_ok(cookie, form.get("csrf", "")):
                    if self.audit:
                        self.audit.record("oauth.login.refused", username=login_user,
                                          login_result="csrf")
                    return render(403, _placeholder_client(form), form=form,
                                  error="This form was not submitted from the consent page.",
                                  granted=granted)
                verified_user = login_user
            else:
                username = (form.get("username") or "").strip()
                ok, reason = self.human.verify(
                    username, form.get("password") or "", form.get("totp") or "",
                )
                if self.audit:
                    # Two fields, both closed: the result is a word this module
                    # chose, and the name is recorded only when it matched an
                    # entry in the anchor — never the text the caller typed.
                    # The password and the code are not in scope here at all.
                    self.audit.record(
                        "oauth.login" if ok else "oauth.login.refused",
                        username=username if username in self.human.passwords else None,
                        login_result=reason.partition(":")[0],
                    )
                if not ok:
                    return render(401, _placeholder_client(form), form=form,
                                  error=_login_error(reason), granted=granted)
                verified_user = username
                raw = self.human.issue(username)
                attrs = [f"{human_auth.COOKIE_NAME}={raw}", "HttpOnly", "SameSite=Lax",
                         "Path=/oauth", f"Max-Age={int(self.human.session_ttl)}"]
                if secure_cookie:
                    attrs.insert(2, "Secure")
                fresh_cookie = "; ".join(attrs)
                extra["Set-Cookie"] = fresh_cookie

        try:
            code, client = self.oauth.approve(
                params=form, owner_token=form.get("owner_token", ""),
                granted=list(granted), verified_user=verified_user,
            )
        except OAuthError as exc:
            if verified_user is None and exc.error == "access_denied" \
                    and "owner token" in exc.description:
                # The operator mistyped their token. Re-present the form rather
                # than bouncing them through a redirect that loses their place.
                return render(401, _placeholder_client(form), form=form,
                              error=f"{exc.error}: {exc.description}", granted=granted)
            # Any other failure is the client's to hear about, and the
            # redirect_uri is now known good. A cookie minted a moment ago still
            # travels: the sign-in succeeded, only the grant did not.
            return _redirect(redirect_with_error(
                form.get("redirect_uri", ""), exc.error, exc.description, form.get("state"),
                issuer=self.oauth.base_url,
            ), extra_headers=extra)
        if self.audit:
            # `username` stays None in PAT mode rather than being filled with
            # `owner-pat`: the field answers "which person approved this", and a
            # token is not a person.
            self.audit.record("oauth.approve", client_id=client.client_id,
                              redirect_uri=form.get("redirect_uri"),
                              username=verified_user)
        return _redirect(redirect_with_code(
            form["redirect_uri"], code, form.get("state"), issuer=self.oauth.base_url,
        ), extra_headers=extra)

    def _oauth_logout(self) -> tuple[int, dict[str, str], bytes]:
        """Drop the session cookie and send the browser back to the form.

        Expiring it is the whole of the operation: the cookie is a signed
        statement and nothing server-side tracks it, so "signed out" means
        "this browser no longer holds the statement". Nothing else is touched.
        """
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        attrs = [f"{human_auth.COOKIE_NAME}=", "HttpOnly", "SameSite=Lax",
                 "Path=/oauth", "Max-Age=0"]
        if self.oauth.base_url.startswith("https://"):
            attrs.insert(2, "Secure")
        target = (urlparse(self.oauth.base_url).path or "") + "/oauth/authorize"
        if self.audit:
            self.audit.record("oauth.logout")
        return _redirect(target, extra_headers={"Set-Cookie": "; ".join(attrs)})

    def _oauth_feishu_start(self, method: str, query: str) -> tuple[int, dict[str, str], bytes]:
        """Begin the Feishu leg: validate the request, hold it, redirect.

        The request is validated here exactly as the consent form validates it —
        a bad `redirect_uri` must be refused before anything is sent anywhere —
        and then held server-side under an opaque state. The browser carries
        only that state, so the client, the redirect, the scopes and the PKCE
        challenge cannot be swapped out mid-flow by whoever holds the link.
        """
        if self.oauth is None or self.feishu is None:
            return _json(404, {"error": "not_found",
                               "error_description": "no route for '/oauth/feishu/start'"})
        params = {k: v[0] for k, v in parse_qs(query).items()}
        try:
            client = self.oauth.validate_authorize(params)
        except OAuthError as exc:
            return _json(exc.status, exc.as_payload())
        txn = self.feishu.transactions.begin(
            client_id=client.client_id,
            redirect_uri=params.get("redirect_uri") or "",
            code_challenge=params.get("code_challenge") or "",
            code_challenge_method=params.get("code_challenge_method") or "S256",
            scopes=tuple(sorted(set((params.get("scope") or "").split()))),
            resource=(params.get("resource") or "").strip() or None,
            client_state=params.get("state"),
        )
        url = self.feishu.authorize_url(state=txn.feishu_state)
        if url is None:
            return _json(404, {"error": "not_found",
                               "error_description": "Feishu sign-in is not enabled"})
        if self.audit:
            self.audit.record("oauth.feishu.start", client_id=client.client_id)
        return _redirect(url)

    def _oauth_feishu_callback(self, method: str, query: str) -> tuple[int, dict[str, str], bytes]:
        """Finish the Feishu leg: one use of the state, then a code or an error.

        Every path out of here either redirects to the *registered* redirect_uri
        with a Fleet code, or redirects an error there — except when the state
        itself is unknown, in which case there is no trusted redirect to use and
        the refusal is shown rather than sent.
        """
        if self.oauth is None or self.feishu is None:
            return _json(404, {"error": "not_found",
                               "error_description": "no route for '/oauth/feishu/callback'"})
        params = {k: v[0] for k, v in parse_qs(query).items()}
        state = (params.get("state") or "").strip()
        if not state:
            return _json(400, {"error": "invalid_request",
                               "error_description": "the Feishu callback carried no state, "
                                                    "so there is no request to complete"})
        try:
            txn = self.feishu.transactions.consume(state)
        except SSOError as exc:
            # Single use, and a state that is unknown, expired or already spent
            # is the same answer here. No redirect: this server issued no
            # redirect_uri for this state, and inventing one would be the
            # open-redirect this flow is built to avoid.
            return _json(400, {"error": exc.error, "error_description": exc.description})

        def refuse(error: str, description: str) -> tuple[int, dict[str, str], bytes]:
            if self.audit:
                self.audit.record("oauth.feishu.refused", client_id=txn.client_id, error=error)
            return _redirect(redirect_with_error(
                txn.redirect_uri, error, description, txn.client_state,
                issuer=self.oauth.base_url,
            ))

        if params.get("error"):
            # The person declined, or Feishu refused before issuing a code.
            return refuse(params["error"],
                          params.get("error_description") or "the sign-in was not completed")
        code = (params.get("code") or "").strip()
        if not code:
            return refuse("invalid_request",
                          "the Feishu callback carried neither a code nor an error")
        try:
            identity = self.feishu.exchange_code(code)
        except SSOError as exc:
            # The error *name* is safe to send; the description may quote a
            # payload, so it stays here and only the kind travels.
            return refuse("server_error", f"the sign-in could not be completed ({exc.error})")
        verdict = self.feishu.verdict(identity)
        if not verdict.allowed:
            return refuse("access_denied", f"this identity is not admitted ({verdict.reason})")
        try:
            fleet_code, client = self.oauth.approve_identity(
                params=txn.authorize_params(), identity_key=identity.key,
            )
        except OAuthError as exc:
            return refuse(exc.error, exc.description)
        if self.audit:
            # Recorded under `subject` — the same "who" field `fleet.grant`
            # records a principal under, and a `REF` in the disclosure table.
            # A new field name would have to be added to that table or its value
            # would be withheld, which is the guard `test_fleet_mcp_disclosure`
            # enforces.
            self.audit.record("oauth.feishu.approve", client_id=client.client_id,
                              subject=identity.key)
        return _redirect(redirect_with_code(
            txn.redirect_uri, fleet_code, txn.client_state, issuer=self.oauth.base_url,
        ))

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

    def _oauth_revoke(self, method: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
        if self.oauth is None:
            return _json(404, {"error": "not_found",
                               "error_description": "this deployment does not run an OAuth issuer"})
        if method != "POST":
            return _json(405, {"error": "invalid_request", "error_description": "revocation requires POST"})
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        result = self.oauth.revoke(form)
        if self.audit:
            # The token itself is never recorded — only that something of a
            # given kind was revoked, which is what an operator needs to see.
            self.audit.record("oauth.revoke", revoked=result.get("revoked"),
                              client_id=result.get("client_id") or form.get("client_id"))
        # RFC 7009 fixes this response: 200 with an empty body, whether or not
        # the token existed. Any richer answer would be an oracle.
        return 200, {"Cache-Control": "no-store"}, b""

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
                "/oauth/revoke": "RFC 7009 revocation. A revoked client's access tokens "
                                 "are refused immediately, not at their next expiry.",
            },
        }
        return _json(200, body)


class HttpAuthError(Exception):
    """The presented credential is missing, malformed or not accepted."""


# A request target followed by a query string, inside a log line. Matches up to
# the first space or closing quote so the HTTP version and status fields of a
# standard request line survive intact.
_QUERY_IN_LOG = re.compile(r'(\S+?\?)[^\s"]*')


def _without_query(line: str) -> str:
    """Replace every query string in a log line with `[redacted]`.

    A path-only replacement would be enough for today's routes and wrong
    tomorrow: this collapses *any* query in the line, including one in a query
    parameter of a nested URL, without needing to know which endpoints carry
    secrets.
    """
    return _QUERY_IN_LOG.sub(r"\1[redacted]", line)


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


def _redirect_origin(redirect_uri: str) -> str | None:
    """The one origin the consent form may be *redirected to* after a submit.

    `form-action` is enforced against every hop of a submission's redirect
    chain, not only against the URL the form posts to. The page posts to
    `self`, the server answers 302 to the client's registered redirect_uri, and
    the browser then checks that new URL against this same policy: `'self'`
    alone blocks it, the navigation is abandoned, and the operator is left on
    the consent page with no error at all. Allowing just the origin of the
    redirect_uri this request already had to prove (`validate_authorize` ran
    first) leaves the policy's intent intact: the owner token still cannot be
    posted anywhere else.
    """
    if not redirect_uri:
        return None
    parts = urlparse(redirect_uri)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _cookie_value(headers: dict[str, str], name: str) -> str | None:
    """One cookie out of a `Cookie:` header, or None.

    The header name is compared case-insensitively because `http.client` and
    the test harness spell it differently, and a lookup that misses returns
    "not signed in" — which is indistinguishable from a stolen-cookie answer
    and would show up as a silent login loop rather than an error.
    """
    raw = ""
    for key, value in headers.items():
        if key.lower() == "cookie":
            raw = value
            break
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value.strip() or None
    return None


def _login_error(reason: str) -> str:
    """The page's one sentence for every failed sign-in.

    Distinct wording per cause ("no such user" vs "wrong password") would turn
    this form into an account enumerator; the reason itself goes to the audit
    log, where only the operator can read it.
    """
    if reason.startswith("throttled:"):
        wait = reason.split(":", 1)[1]
        return f"Too many failed attempts. Try again in {wait}."
    if reason == "missing_username":
        return "Enter a username and password."
    if reason == "bad_totp":
        return "That verification code was not accepted. Enter the current six-digit code."
    return "Username, password or verification code was not accepted."


def _html(status: int, markup: str, form_action: str | None = None,
          extra_headers: dict[str, str] | None = None):
    raw = markup.encode("utf-8")
    csp = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
    if form_action:
        csp += " " + form_action
    return status, {
        **(extra_headers or {}),
        "Content-Type": "text/html; charset=utf-8",
        "Content-Length": str(len(raw)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        # The consent form carries an owner token. Nothing on the page may load
        # from, or post to, anywhere but here -- plus the single callback origin
        # the server is about to redirect to, which the browser re-checks.
        "Content-Security-Policy": csp,
    }, raw


def _redirect(location: str, extra_headers: dict[str, str] | None = None):
    return 302, {**(extra_headers or {}), "Location": location, "Cache-Control": "no-store",
                 "Referrer-Policy": "no-referrer"}, b""


class _Handler(BaseHTTPRequestHandler):
    server_version = f"{SERVER_NAME}/{SERVER_VERSION}"
    protocol_version = "HTTP/1.1"
    # ~40 ms per call, and only on a REUSED connection. `_respond` writes the
    # headers and the body as two separate sends (`wbufsize` is 0, so the write
    # file is unbuffered and Nagle is on): the small header segment goes out,
    # the rest waits on a delayed ACK that the idle client has no reason to send
    # yet. Measured on 2026-10-10 against this same tree, 10 calls each, same
    # port sequence: control p50 53.1 ms, with this flag p50 12.0 ms. A fresh
    # connection hides it — which is why it read as "warm is slower than cold"
    # in the first A/B, and why it is worth a line of comment to keep.
    disable_nagle_algorithm = True
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
        # Access logging goes to stderr with every query string removed. The
        # default handler logs `self.requestline`, and on this surface that line
        # carries an authorization code, a `state`, a PKCE `code_challenge` and
        # a `resource` — `/oauth/authorize?...` and `/oauth/token?...` are the
        # two most sensitive URLs a request can name. A log line is a copy of a
        # credential that outlives the credential, in a file with a different
        # lifetime and a different audience than the one the credential was
        # scoped to, so the value is stripped at the point of writing rather
        # than being filtered by whoever reads the journal later.
        sys.stderr.write("%s - %s\n" % (self.address_string(), _without_query(fmt % args)))


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


def build_oauth(config, *, base_url: str, store_root,
                mcp_path: str | None = None) -> AuthorizationServer:
    """Wire the OAuth issuer. Refuses when there is no owner credential.

    An authorization server that cannot authenticate the human approving a
    client is a public token dispenser, so this raises rather than degrading to
    an unauthenticated consent form.
    """
    advertised = str(getattr(config.server, "public_base_url", "") or "")
    if advertised:
        # What is advertised becomes the token audience and the endpoint in
        # every discovery document, so an http one would mean codes and tokens
        # travelling in clear to a URL ChatGPT then trusts. Loopback http is the
        # exception, for the same reason it is one for redirect_uri: that is how
        # a local client reaches a server with no certificate.
        parsed = urlparse(advertised)
        loopback_http = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1", "localhost")
        if not ((parsed.scheme == "https" and parsed.netloc) or loopback_http):
            raise FleetRefusal(
                "BAD_REQUEST",
                f"server.public_base_url is {advertised!r}. It is published as this "
                f"issuer's identity and as the audience of every token it issues, so "
                f"it has to be an https URL (or http on loopback for a local test); "
                f"leaving it unset advertises the bind address instead.",
            )
    if not config.auth.oauth_enabled:
        raise FleetRefusal(
            "BAD_REQUEST",
            "oauth_enabled is false in the local binding; a ChatGPT connector "
            "cannot authenticate without it",
        )
    # One verifier is required, and either flavour will do: a password anchor
    # (username + password [+ TOTP]) or the owner PAT. What is refused is
    # neither — an issuer that cannot authenticate the person approving a
    # client is a public token dispenser.
    if config.auth.password_anchor is not None:
        if not config.auth.password_anchor.exists():
            raise FleetRefusal(
                "BAD_REQUEST",
                f"auth.password_anchor points at {config.auth.password_anchor}, which "
                f"does not exist. The consent step proves the human approving a client "
                f"is the owner by asking for a password and a one-time code; with no "
                f"credential file there is nothing to prove it with.",
            )
    elif config.auth.pat_anchor is None or not config.auth.pat_anchor.exists():
        raise FleetRefusal(
            "BAD_REQUEST",
            "neither auth.password_anchor nor auth.pat_anchor is set to a readable "
            "file. The consent step proves the human approving a client is the owner; "
            "with no owner credential configured there is nothing to prove it with, "
            "and this surface will not issue tokens to anonymous clients.",
        )

    def read_pat() -> str | None:
        # `pat_enabled: false` answers None here as well as in
        # `principal_for_token`. Two gates on one fact, because they fail
        # differently: one refuses a bearer at the front door, this one makes
        # sure the consent form cannot fall back to a token the operator has
        # turned off even if a future call site passes `verified_user=None`.
        if not config.auth.pat_enabled or config.auth.pat_anchor is None:
            return None
        try:
            return config.auth.pat_anchor.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    store = TokenStore(store_root, access_ttl=config.auth.access_token_ttl,
                       refresh_ttl=config.auth.refresh_token_ttl)
    return AuthorizationServer(
        store=store, auth=config.auth, base_url=base_url, pat_reader=read_pat,
        mcp_path=mcp_path or str(getattr(config.server, "mcp_path", "/mcp")),
    )


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
