"""One place the Fleet MCP surface's own version is written down."""

SERVER_NAME = "serveros-fleet-mcp"
SERVER_VERSION = "0.2.0"

# The revision of the Fleet tool surface. Bumped when a tool's contract changes
# shape, not when an implementation detail does — a caller caches the tool list
# and the annotations it read, so a change to either is a new surface revision.
# `fleet-mcp-3`: the surface grew from nine hand-written tools to those nine
# PLUS the 22 inherited CloudCLI verbs — a change a client that cached
# `tools/list` must be told about, so the revision moved with it.
# `fleet-mcp-4`: `fleet_list_devices` and `fleet_status` each carry a new
# OPTIONAL `core_lite` block — the machine's Core reading, or why there is none.
# It is a change to a tool's output contract, so it is a new revision even though
# the tool COUNT is unchanged: a client that cached `tools/list` at `-3` and one
# that reads `-4` see the same 33 tools and different result shapes, and a
# revision that only moved when the count moved would not be telling them that.
# Every field that existed at `-3` is still there, unchanged.
# `fleet-mcp-5`: explicit fresh project creation, ten authored tools (34 total),
# owner-max admission and existing-owner delivery durability/readback.
# `fleet-mcp-6`: the surface follows CloudCLI 1.40 — the mirror grows from 22 to
# 31 verbs (the nine 1.40 additions), and `fleet_delete_project` is the eleventh
# authored tool, the surface's only destructive one. 44 tools total.
# `fleet-mcp-7`: `fleet_cloudcli_session_reconfigure` gains an OPTIONAL outer
# `project`. A model or permission-mode change has always been refused unless the
# caller named the session's real project, and that key had nowhere to live: the
# native `session_reconfigure` schema has none to inherit, and `arguments`
# refuses unknown keys — so every such reconfigure was refused for naming a
# project the surface would not accept. No tool was added and no result shape
# changed, but the schema of one tool did: a client that cached `tools/list` at
# `-6` would call it the same way and keep failing.
# `fleet-mcp-8`: `fleet_inbox`, the twelfth authored tool (45 total) — the
# hub's event inbox (ARCHITECTURE §5.1): run and node facts recorded once, read
# by sequence number, with a per-caller cursor that only moves forward.
TOOL_SURFACE_REVISION = "fleet-mcp-8"

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
