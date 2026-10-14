---
name: fleet-connectivity-check
description: Check the non-sensitive health and connectivity of a registered ServerOS Fleet MCP endpoint without changing services.
---

# ServerOS Fleet connectivity (packaging test)

This is a packaging experiment, not a live Fleet connector. Do not call a real Fleet MCP endpoint or claim its network status from this skill alone.

When diagnosing a remote MCP connection, distinguish DNS resolution, TLS certificate, HTTPS reachability, MCP initialize/tools/list, OAuth client registration, token exchange, and plugin tool metadata refresh. Observe read-only endpoints first. Never print bearer tokens or OAuth authorization codes. Do not restart services without the operator's explicit approval.
