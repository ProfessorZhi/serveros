"""One place the Fleet MCP surface's own version is written down."""

SERVER_NAME = "serveros-fleet-mcp"
SERVER_VERSION = "0.1.0"

# The revision of the Fleet tool surface. Bumped when a tool's contract changes
# shape, not when an implementation detail does — a caller caches the tool list
# and the annotations it read, so a change to either is a new surface revision.
# `fleet-mcp-3`: the surface grew from nine hand-written tools to those nine
# PLUS the 22 inherited CloudCLI verbs — a change a client that cached
# `tools/list` must be told about, so the revision moved with it.
TOOL_SURFACE_REVISION = "fleet-mcp-3"

# RFC 9728 protected-resource metadata, relative to the origin. Advertised in
# `initialize` so a connector can find the authorization server without being
# configured with it, and served by the HTTP transport at this path.
PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_PATH = "/.well-known/oauth-authorization-server"

# Unauthenticated liveness. It says that this process is up and that everything
# else needs a credential — never anything about the fleet. It is named here
# because a second reader exists: the installer waits on this exact path to
# decide whether the service it just started is running, and
# `fleet/fleet-mcp-contract.yaml` declares it under `deployment`. A path written
# out in three places is three answers to one question.
HEALTH_PATH = "/health"
