"""ServerOS Fleet MCP — one plugin that sees and coordinates enrolled sessions.

The surface is a composition layer over identities that already exist:
`fleet/transport.py` owns what a device is, the CloudCLI MCP gateway owns what a
session is, and this package owns the one thing neither of them does — the
mapping from a caller's credential to an authorized set of devices and sessions,
and a single place that answers across all of them.

    fleet_mcp.identity   device_ref + provider + native_session_id, and nothing else
    fleet_mcp.policy     who may do what, decided before the provider is asked
    fleet_mcp.providers  one adapter per way to reach a device
    fleet_mcp.fleet      the read model composed from the two above
    fleet_mcp.tools      the eight tools, with annotations and typed refusals
    fleet_mcp.scopes     the scope vocabulary, written down once
    fleet_mcp.server     MCP protocol, transport-independent
    fleet_mcp.http_transport  Streamable HTTP + the OAuth endpoints
    fleet_mcp.oauth      authorization code + PKCE + DCR, with a real consent gate
    fleet_mcp.node       the outbound connector a Windows host runs
"""

from .version import SERVER_NAME, SERVER_VERSION, TOOL_SURFACE_REVISION

__all__ = ["SERVER_NAME", "SERVER_VERSION", "TOOL_SURFACE_REVISION"]
