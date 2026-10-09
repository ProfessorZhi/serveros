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
from .errors import INTERNAL, FleetRefusal, refuse
from .fleet import FleetService, Principal, SCOPE_READ, SCOPE_SEND
from .http_transport import HttpSurface, build_oauth, local_base_url, serve
from .local_roots import binding_root, state_root
from .oauth import store_root_for
from .server import SUPPORTED_PROTOCOL_VERSIONS, McpServer, dumps, loads
from .tools import ToolContext
from .version import PROTECTED_RESOURCE_PATH, SERVER_NAME, SERVER_VERSION


def owner_principal() -> Principal:
    """The principal for a channel that is already inside the trust boundary."""
    return Principal(name="local-owner", scopes=frozenset((SCOPE_READ, SCOPE_SEND)),
                     credential_kind="pat")


def build_service(config: FleetConfig) -> FleetService:
    return FleetService(config)


def default_store_root(config: FleetConfig) -> Path:
    """Where the OAuth issuer's keys and state live.

    Sibling to the binding in the sense that both are this surface's own, and
    in no ServerOS root: `~/.serveros` admits five named files and refuses the
    rest, and `fleet/node-root-contract.yaml` puts credentials on the
    `never_in_this_root` list outright.
    """
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
        f"  control authorized  {[r for r, b in config.providers.items() if b.allow_send]}\n"
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
    context = lambda: ToolContext(service=service, principal=owner_principal())  # noqa: E731
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = "stdio"
    config_path: str | None = None
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

    sys.stderr.write(f"{SERVER_NAME} {SERVER_VERSION}: binding from {config.source}\n")
    if mode == "http":
        return run_http(config)
    return run_stdio(config)


_HELP = f"""\
{SERVER_NAME} {SERVER_VERSION} — ServerOS Fleet MCP

Usage:
  serveros-fleet-mcp [--stdio] [-c PATH]     speak MCP on stdin/stdout (default)
  serveros-fleet-mcp --http  [-c PATH]       serve MCP Streamable HTTP
  serveros-fleet-mcp --version | --help

Environment:
  FLEET_MCP_CONFIG   path to the local binding (default {binding_root()}/fleet-mcp.local.yaml)
  FLEET_MCP_HOME     relocate this surface's own binding and state roots

Protocol versions supported: {', '.join(SUPPORTED_PROTOCOL_VERSIONS)}
"""
