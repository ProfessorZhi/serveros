"""Wiring: binding file in, running surface out.

Kept apart from `server.py` and `http_transport.py` so that both of those stay
testable without a configuration file, a socket or a signing key — the protocol
layer should be provable in isolation, and a module that reads `~/.serveros` on
import cannot be.

Two entry points, one service:

*   **stdio** — a local client on the same host, launched by whatever already
    runs the ServerOS MCP surfaces. The process's own user is the principal: it
    could read the binding file and the credential anchors directly, so
    demanding a token for a channel that is already inside the trust boundary
    would be ceremony, not control.
*   **HTTP** — ChatGPT and anything else off-host. Every request carries a
    credential, and the credential decides the scopes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .config import FleetConfig, load_config
from .errors import INTERNAL, DISPATCH_STATE_UNAVAILABLE, FleetRefusal, refuse
from .fleet import FleetService, Principal, SCOPE_CREATE, SCOPE_READ, SCOPE_SEND
from .http_transport import HttpSurface, build_oauth, local_base_url, serve
from .local_roots import binding_root, state_root
from .oauth import TokenStore, store_root_for
from .server import SUPPORTED_PROTOCOL_VERSIONS, McpServer, dumps, loads
from .tools import ToolContext
from .version import PROTECTED_RESOURCE_PATH, SERVER_NAME, SERVER_VERSION
from .scopes import ALLOWED_SCOPES


def owner_principal(config: FleetConfig | None = None) -> Principal:
    """The principal for a channel that is already inside the trust boundary.

    All three scopes, including `fleet:create` — and that is not a loosening.
    This principal exists for a channel that never left the host, so the scope
    check is not what protects creation here; `allow_create` on the target device
    and the project's entry in `policy.create_allow` are, and both default to
    refusal. Withholding the scope from the owner would buy nothing and would
    make the operator's own local tools the one caller that cannot do what the
    deployment is configured to allow.

    Note the asymmetry with `AuthBinding.grantable_scopes`, which defaults to
    read+send: that is the ceiling on what may be *handed to someone else*, and
    it is narrow because a third party should have to be granted creation
    explicitly. This is the owner's own channel, where the binding is the
    authority and the scope merely stops the call before the binding is read.
    """
    return Principal(name="local-owner",
                     scopes=frozenset(ALLOWED_SCOPES if config is not None
                                      and config.policy.owner_max.get("local-owner")
                                      else (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)),
                     credential_kind="pat")


def build_service(config: FleetConfig) -> FleetService:
    if config.policy.owner_max:
        raise refuse(DISPATCH_STATE_UNAVAILABLE,
                     "owner-max uses bin/serveros-fleet-owned and the existing ServerOS ledger")
    return FleetService(config)


def default_store_root(config: FleetConfig) -> Path:
    """Where the OAuth issuer's keys and state live.

    Sibling to the binding in the sense that both are this surface's own, and
    in no ServerOS root: `~/.serveros` admits five named files and refuses the
    rest, and `fleet/node-root-contract.yaml` puts credentials on the
    `never_in_this_root` list outright.
    """
    if config.server.oauth_store_root is not None:
        return config.server.oauth_store_root
    fallback = state_root() / "oauth"
    return store_root_for(config.auth.pat_anchor, fallback)


def build_http_surface(config: FleetConfig, service: FleetService) -> HttpSurface:
    base_url = local_base_url(
        bind=config.server.bind, port=config.server.port,
        public_base_url=config.server.public_base_url,
    )
    oauth = None
    if config.auth.oauth_enabled:
        try:
            oauth = build_oauth(config, base_url=base_url, store_root=default_store_root(config))
        except FleetRefusal as exc:
            # Not fatal: a local-only deployment without an owner token still
            # serves stdio and, if bound to loopback, the debug path. What it
            # cannot do is accept an off-host connector, and it says so.
            sys.stderr.write(
                f"{SERVER_NAME}: OAuth issuer disabled — {exc.detail}\n"
                f"{SERVER_NAME}: the HTTP endpoint will refuse every request that "
                f"is not the owner token.\n"
            )
    return HttpSurface(service=service, config=config, oauth=oauth, audit=service.audit)


def run_http(config: FleetConfig, *, service: FleetService | None = None) -> int:
    service = service or build_service(config)
    surface = build_http_surface(config, service)
    bind, port = config.server.bind, config.server.port
    try:
        server = serve(surface, bind=bind, port=port)
    except OSError as exc:
        raise refuse(
            INTERNAL,
            f"cannot bind {bind}:{port} ({exc}). Another Fleet surface may already "
            f"be running; check the port before changing it.",
        ) from None
    base = local_base_url(bind=bind, port=port, public_base_url=config.server.public_base_url)
    sys.stderr.write(
        f"{SERVER_NAME} {SERVER_VERSION} listening on http://{bind}:{port}\n"
        f"  MCP endpoint        {base}{config.server.mcp_path}\n"
        f"  health              {base}/health\n"
        f"  protected resource  {base}{PROTECTED_RESOURCE_PATH}\n"
        f"  devices with routes {sorted(service.providers)}\n"
        # One line per write class, each filtered on the flag it names. This
        # banner used to print the `allow_send` set under the label "control
        # authorized", which read as a capability the binding had not granted:
        # a startup log is where an operator checks what just came up, so a
        # sentence about a permission has to be the answer to that permission.
        f"  send authorized     {[r for r, b in config.providers.items() if b.allow_send]}\n"
        f"  control authorized  {[r for r, b in config.providers.items() if b.allow_control]}\n"
        f"  create authorized   {[r for r, b in config.providers.items() if b.allow_create]}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write(f"{SERVER_NAME}: interrupted\n")
    finally:
        server.server_close()
    return 0


def run_stdio(config: FleetConfig, *, service: FleetService | None = None) -> int:
    """Line-delimited JSON-RPC over stdin/stdout.

    One request per line, one response per line — the framing every stdio MCP
    client uses. A malformed line produces a JSON-RPC parse error rather than
    killing the loop, because a client that sends one bad message should not
    have to restart the server to recover.
    """
    service = service or build_service(config)
    context = lambda: ToolContext(service=service, principal=owner_principal(config))  # noqa: E731
    mcp = McpServer(context)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = loads(line)
        except json.JSONDecodeError as exc:
            out.write(dumps({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": f"parse error: {exc}"}}) + "\n")
            out.flush()
            continue
        response = mcp.handle(message)
        if response is None:
            continue
        out.write(dumps(response) + "\n")
        out.flush()
    return 0


def clients_command(config: FleetConfig, *, revoke: str | None, as_json: bool) -> int:
    """`--list-clients` / `--revoke-client`. Operator-only, on this host.

    It reads the same state file the running surface reads, so revoking here
    takes effect on the next request the surface serves without a restart —
    and without an HTTP endpoint that would have to be authenticated to be safe
    to expose.

    The refusal when there is no OAuth state is a statement about the binding,
    not a crash: a deployment that never ran an issuer has no clients to show,
    and saying "none" would read as "nobody has access".
    """
    store_root = default_store_root(config)
    state = store_root / "state.json"
    if not state.exists():
        sys.stderr.write(
            f"{SERVER_NAME}: no OAuth state at {state}. Either this binding runs "
            f"no issuer (auth.oauth_enabled) or nothing has been registered yet.\n"
        )
        return 4
    store = TokenStore(store_root, access_ttl=config.auth.access_token_ttl,
                       refresh_ttl=config.auth.refresh_token_ttl,
                       rotate_refresh=config.auth.refresh_rotate,
                       rotation_grace_seconds=config.auth.refresh_rotation_grace_seconds)
    if revoke is None:
        rows = store.clients()
        if as_json:
            sys.stdout.write(json.dumps({"store": str(state), "clients": rows}, indent=2) + "\n")
            return 0
        if not rows:
            sys.stdout.write(f"no clients registered in {state}\n")
            return 0
        sys.stdout.write(f"{len(rows)} client(s) in {state}\n")
        for row in rows:
            state_text = f"REVOKED {row['revoked_at']}" if row["revoked"] else "active"
            sys.stdout.write(
                f"  {row['client_id']}\n"
                f"    name           {row['client_name']}\n"
                f"    state          {state_text}\n"
                f"    registered     {row['created_at']}\n"
                f"    redirects      {', '.join(row['redirect_uris']) or '(none)'}\n"
                f"    live refresh   {row['live_refresh_tokens']}\n"
            )
        return 0
    try:
        result = store.revoke_client(revoke)
    except Exception as exc:  # noqa: BLE001 — an unknown id is an operator typo
        sys.stderr.write(f"{SERVER_NAME}: cannot revoke {revoke!r}: {exc}\n")
        return 5
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


def mint_token_command(config: FleetConfig, *, name: str, scopes: str, days: int,
                       out=None, err=None) -> int:
    """`--mint-token NAME --scopes fleet:read[,fleet:send] [--days 90]`.

    A long-lived, scoped, revocable bearer token for one app that takes an API
    key instead of OAuth (Meta Muse builds its connector from a URL and a key).
    It is NOT the owner token: that one carries every scope and is the consent
    anchor, so it never leaves this host. This one is a client of its own —
    named in the commander log, limited to what the deployment lets OAuth
    clients have, gone at `exp` or at `--revoke-client`.

    The token is printed once, alone, on stdout (paste it into the app's secure
    credential box, never into a chat); everything else goes to stderr.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    wanted = tuple(dict.fromkeys(x.strip() for x in scopes.split(",") if x.strip()))
    allowed = set(config.auth.grantable_scopes)
    if not wanted or not set(wanted) <= allowed:
        err.write(f"{SERVER_NAME}: --scopes must be some of {sorted(allowed)}\n")
        return 2
    if not 1 <= days <= TokenStore.TOKEN_MAX_DAYS:
        err.write(f"{SERVER_NAME}: --days must be 1..{TokenStore.TOKEN_MAX_DAYS}\n")
        return 2
    store = TokenStore(default_store_root(config), access_ttl=config.auth.access_token_ttl,
                       refresh_ttl=config.auth.refresh_token_ttl,
                       rotate_refresh=config.auth.refresh_rotate,
                       rotation_grace_seconds=config.auth.refresh_rotation_grace_seconds)
    base = local_base_url(bind=config.server.bind, port=config.server.port,
                          public_base_url=config.server.public_base_url)
    try:
        client = store.register_token_client(client_name=name)
    except Exception as exc:  # noqa: BLE001 - a bad name is an operator typo
        err.write(f"{SERVER_NAME}: {exc}\n")
        return 2
    token, ttl = store.mint_access_token(
        client_id=client.client_id, subject=f"token:{client.client_name}", scopes=wanted,
        audience=f"{base}{config.server.mcp_path}", issuer=base, ttl=days * 86400)
    err.write(
        f"connector token for {client.client_name!r}\n"
        f"  client_id   {client.client_id}   (revoke: --revoke-client {client.client_id})\n"
        f"  scopes      {' '.join(wanted)}\n"
        f"  expires in  {days} days\n"
        f"  endpoint    {base}{config.server.mcp_path}   (Authorization: Bearer <token>)\n"
        f"the token follows on stdout, once; it is not stored here and cannot be shown again\n")
    out.write(token + "\n")
    return 0


def main(argv: list[str] | None = None, *, service_factory=build_service) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "stdio"
    config_path: str | None = None
    list_clients = False
    revoke_client: str | None = None
    as_json = False
    mint_name: str | None = None
    mint_scopes = "fleet:read"
    mint_days = 90
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--stdio",):
            mode = "stdio"
        elif arg in ("--http", "--serve"):
            mode = "http"
        elif arg in ("--config", "-c"):
            index += 1
            if index >= len(argv):
                sys.stderr.write("fleet-mcp: --config needs a path\n")
                return 2
            config_path = argv[index]
        elif arg == "--list-clients":
            list_clients = True
        elif arg == "--revoke-client":
            index += 1
            if index >= len(argv):
                sys.stderr.write("fleet-mcp: --revoke-client needs a client_id\n")
                return 2
            revoke_client = argv[index]
        elif arg in ("--mint-token", "--scopes", "--days"):
            index += 1
            if index >= len(argv):
                sys.stderr.write(f"fleet-mcp: {arg} needs a value\n")
                return 2
            if arg == "--mint-token":
                mint_name = argv[index]
            elif arg == "--scopes":
                mint_scopes = argv[index]
            else:
                try:
                    mint_days = int(argv[index])
                except ValueError:
                    sys.stderr.write("fleet-mcp: --days needs a whole number\n")
                    return 2
        elif arg == "--json":
            as_json = True
        elif arg in ("--version", "-V"):
            sys.stdout.write(f"{SERVER_NAME} {SERVER_VERSION}\n")
            return 0
        elif arg in ("--help", "-h"):
            sys.stdout.write(_HELP)
            return 0
        else:
            sys.stderr.write(f"fleet-mcp: unknown argument {arg!r}\n{_HELP}")
            return 2
        index += 1

    try:
        config = load_config(config_path)
    except FleetRefusal as exc:
        # Startup refusals are the ones an operator must read, so they go to
        # stderr with the code and a non-zero exit — never a silent empty fleet.
        sys.stderr.write(f"{SERVER_NAME}: {exc.code}: {exc.detail}\n")
        return 3

    if mint_name is not None:
        return mint_token_command(config, name=mint_name, scopes=mint_scopes, days=mint_days)

    if list_clients or revoke_client is not None:
        # These act on state, not on a socket, so they are answered before any
        # listener is created: revoking a client must not depend on the server
        # that issued it still being up.
        return clients_command(config, revoke=revoke_client, as_json=as_json)

    sys.stderr.write(f"{SERVER_NAME} {SERVER_VERSION}: binding from {config.source}\n")
    try:
        service = service_factory(config)
        if mode == "http":
            return run_http(config, service=service)
        return run_stdio(config, service=service)
    except FleetRefusal as exc:
        sys.stderr.write(f"{SERVER_NAME}: {exc.code}: {exc.detail}\n")
        return 3


_HELP = f"""\
{SERVER_NAME} {SERVER_VERSION} — ServerOS Fleet MCP

Usage:
  serveros-fleet-mcp [--stdio] [-c PATH]     speak MCP on stdin/stdout (default)
  serveros-fleet-mcp --http  [-c PATH]       serve MCP Streamable HTTP
  serveros-fleet-mcp --list-clients  [-c PATH] [--json]
                                             who has been given OAuth access here
  serveros-fleet-mcp --revoke-client ID [-c PATH]
                                             stop a client: its tokens die now
  serveros-fleet-mcp --mint-token NAME [--scopes fleet:read[,fleet:send]] [--days 90] [-c PATH]
                                             a scoped, expiring bearer token for one
                                             app that takes an API key (e.g. Muse)
  serveros-fleet-mcp --version | --help

Environment:
  FLEET_MCP_CONFIG   path to the local binding (default {binding_root()}/fleet-mcp.local.yaml)
  FLEET_MCP_HOME     relocate this surface's own binding and state roots

Protocol versions supported: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}
"""
