"""One place the Fleet MCP surface's own version is written down."""

SERVER_NAME = "serveros-fleet-mcp"
SERVER_VERSION = "0.1.0"

# The revision of the Fleet tool surface. Bumped when a tool's contract changes
# shape, not when an implementation detail does — a caller caches the tool list
# and the annotations it read, so a change to either is a new surface revision.
TOOL_SURFACE_REVISION = "fleet-mcp-1"

# RFC 9728 protected-resource metadata, relative to the origin. Advertised in
# `initialize` so a connector can find the authorization server without being
# configured with it, and served by the HTTP transport at this path.
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_PATH = "/.well-known/oauth-authorization-server"
