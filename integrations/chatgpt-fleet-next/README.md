# ChatGPT Fleet Next — Cloud/Native cooperation source (draft)

**Status: NOT BUILT, NOT INSTALLED, NOT CONNECTED.** This directory is a GitHub-side candidate source for Fleet Next's cloud-facing Skills and related integration contracts. It is separate from the **existing native ServerOS** `plugin/bundle.yaml` composition and must not replace it.

## First two thin Skills
- `skills/fleet-agent-collaboration/SKILL.md`: collaborate with authorized Fleet Sessions, native Parent Agent, review and accept outcomes. Covers 9 currently visible V1 tools; detects rather than presumes newer MCP tools.
- `skills/fleet-ability-routing/SKILL.md`: locate current Ability/operation/Provider on the target machine; ask the local Agent for environment-specific HOW. Does not copy the native Skill manuals or produce OS shell commands.

Future packaging may add `fleet-multiagent-review`, `fleet-permissions` and `fleet-troubleshooting`; add them only after their workflows have real providers and acceptance tests.

## Source ownership
- Canonical reusable Skills, 21 semantic responsibilities and Ability revisions: `ProfessorZhi/ability-management`.
- Fleet MCP tool surface and per-device bindings: `ProfessorZhi/serveros/fleet_mcp` and `abilities`.
- Native ServerOS plugin payload, installation and OS supervision: existing `plugin/bundle.yaml` and runtime.
- This directory: only ChatGPT product onboarding/routing Skills and eventual Fleet Next release packaging, not machine-specific commands, secrets or duplicate source trees.

## Build and rollout conditions
1. Keep V1 installed until Fleet RC #182, SSO #184 and full tool discovery have been independently validated.
2. Verify the exact supported packaging mode (App-backed binding vs portable MCP config) and fixed plugin ID *before* creating the production manifest. A ZIP itself does not host MCP, OAuth or the models.
3. Produce a reproducible release ZIP only after the manifest, icon, Skills and source revisions are pinned and validated; same plugin ID and versioned releases for subsequent updates where supported.
4. MCP tool changes go through controlled Tokyo release and ChatGPT tool refresh; Skills/icon changes require plugin release/update. Do not claim GitHub push automatically installs a private plugin.
5. Test one authorized cloud-to-native handoff and one multi-agent bounded task. No new production service, remote tunnel, Feishu scope, daemon or OpenAgents Workspace pairing as part of packaging.

## Related work
- [Federated agent design](../../docs/plans/fleet-next-agent-federation-v0.md)
- ServerOS issue #186 — Cloud Skills
- ServerOS issue #187 — OpenAgents SDK pilot
- Ability Management issue #29 — Ability Family source of truth
- ServerOS PR #182 — Fleet 33-tool release candidate
- ServerOS PR #184 — Feishu SSO candidate
