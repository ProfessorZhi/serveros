# Fleet Next release evidence: do not equate code, tools, or login with readiness

This is an **offline audit tool**, not an OAuth issuer, a remote shell, a
production deployer, or a substitute for owner consent.

Run from the repository root:

    python -m fleet_mcp.release_evidence --evidence ./reviewed-next-evidence.json

The input is a **non-secret**, separately collected, reviewed JSON document
with schema `serveros/fleet-next-release-evidence/v1`.

The checker intentionally refuses a manifest until it contains:
- independent V1 and Next Plugin IDs, release/backend identities and distinct
  HTTPS MCP endpoints; no accidental overwrite during side-by-side operation;
- actual MCP tool discovery of 33 distinct tool names and exactly the 6 planned
  OAuth scopes. A registered tool name is **not** a usable verb on a device;
- independently referenced **LIVE** receipts for full regression, cgroup
  limits, health/rollback, HTTPS/OAuth discovery, Feishu SSO, token
  refresh/revocation and permission denials, tool exercise, logo/Skills ZIP
  installation, same-Plugin-ID update and backend-only update;
- a nonempty **dynamic** list of the Runtime instances the owner requires.
  Each must match an actual live observation identified by
  `device × runtime_id × family`, with owner consent, credential isolation,
  effective model/permissionMode and all declared lifecycle operations
  (list/read/create/send/interrupt/reconfigure/approval/close/resume/recover).
  Missing/unavailable Codex on Dell, or a second Claude account on Tokyo, is
  not turned into PASS by another account's successful connection.

The present candidate is 9 Fleet primitives + 22 CloudCLI mirrored verbs +
2 discovery tools. Six OAuth scopes do not entitle a caller to every device
or another user's Linux account. Every local owner grant and the Node floor
still applies. Runtime count is **not** hardcoded; registered instances remain
distinct for multiple CC accounts under one OS UID, and separate Dell vince/fjd
accounts. Avoid registering Codex with Claude's Anthropic-compatible model ID
or directly setting Claude's bypassPermissions on Codex.

The `REVIEW_CANDIDATE_ONLY` decision is **not** `PRODUCTION_READY`.
This tool cannot verify the authenticity of claimed receipts or the identity
of the person signing off. An independent human reviewer must inspect real
ChatGPT/Feishu/CloudCLI readbacks, cgroup limits, owner grants and failover
traces. A mock claiming `provenance: LIVE` is **not** authenticated evidence;
never release automatically based on this manifest.

Monitoring requirements must report service liveness and responsiveness,
project-aware resource limits, freshness/latency, provider-usable models,
Runtime health and new-session admission separately. In particular a large
SwapUsed inventory must not be conflated with active PSI/swap-out, and active
thrashing must not be papered over with a service template's MemoryMax.

Stable update policy: register the independent private Next Plugin ID and
HTTPS issuer **once**, keep them stable for backend/runtime updates, and use a
guarded **same Plugin ID** ZIP Release update for Skills/support resources.
Tool schema changes may require tool refresh and additional scope consent;
none of that makes new plugin installation a routine deployment step.

This quality branch is separate from #192/#198 and must not modify V1 or
merge the main branch without review. All tests run offline, no Feishu/Tailscale
tokens or other OS accounts are consulted.
