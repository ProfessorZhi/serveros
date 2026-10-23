---
name: fleet-agent-collaboration
description: Coordinate ChatGPT with authorized ServerOS Fleet Sessions, native Parent agents, and optionally multi-agent workers. Use for delegating bounded work, reviewing evidence, and tracking actual acceptance; not for direct shell, production changes, or bypassing owner grants.
---

# Fleet Native Agent Collaboration (Cloud-side thin Skill)

This is **an uninstalled design candidate** for Fleet Next. The available tools are only those actually exposed by the connected Fleet MCP app; do not assume the full 34-tool candidate is installed.

## Core decision
- First decide whether the user really needs a remote agent. Plain advice and drafts stay in chat; repository-only work can use GitHub directly.
- For local execution, tests, host-specific knowledge, container lifecycle, network diagnosis, or deployment: ask the user's authorization for the target device/scope before using a remote Fleet tool if that scope is not already granted in this conversation.
- Do not carry out hidden side effects through a Skill. A Skill is routing guidance, not an entitlement.

## How to cooperate
1. Capture objective, success check, exclusions and allowed effects. If the current conversation already contains them, do not ask again.
2. If remote action is justified, get live device/readiness and correct runtime; registered device != reachable; a reachable gateway != authorized tool use.
3. Discover the relevant Session and current run. Prefer an existing authorized *idle* Session with matching project ownership; never interrupt a running Agent to make space.
4. Confirm existing Work Parent, writer lease, active worktree, ability/provider readiness, grant and bounded resource budget. Native knowledge lives on target: GPT should not guess Docker flags, project directories, ports, cgroup settings, or credentials.
5. Send a **bounded request**: task, AC, current revision, desired artifacts, allowed effects, refusal conditions, time/resource bounds and exact worktree/owner isolation. Use an idempotency key when the tool supports it.
6. Distinguish delivery accepted, actual run started, run finished, outputs available, reviewed, and accepted. Follow `run_id`; a timeout/UNSETTLED is not failure and must not trigger duplicate dispatch.
7. Ask the native Parent for raw artifact refs and independent validation. Cross-device or multi-agent fan-out must keep exactly one active writer per work item epoch.
8. Report using accurate labels: PROPOSED / WAITING_AUTHORIZATION / DISPATCHED / RUNNING / RESULT_READY / ACCEPTED / BLOCKED / FAILED. When evidence is missing, explain the specific missing observation.

## Existing V1 tool surface
Use `fleet_list_devices`, `fleet_list_sessions`, `fleet_get_session`, `fleet_read_session`, `fleet_status` for read-only orientation when relevant and authorized. Use `fleet_send_to_session`, `fleet_create_session`, `fleet_get_run`, `fleet_interrupt_session` only within specific permissions; creation has a project allowlist, and interrupt ends a *run*, not a session. Do not assume additional 34-tool mirrored CloudCLI names or per-UID runtime controls unless actually discovered.

## Native Parent and worker responsibilities
- Native Parent: owns work decomposition, workspace, receipt consumption and AC acceptance; may choose Codex, Claude Code, or a valid runtime/provider.
- Worker: implements only its task/lease; cannot make scope decisions, promote releases, use another account's secret, or self-certify ACCEPTED.
- Reviewer: independent context, raw evidence, validates the source revision and safety checks; outputs findings to Parent.
- GPT: cross-device intent/status/comparison and presentation. It cannot grant production rights by telling a worker to proceed.
- OpenAgents/Quay: optional A6 providers. Shared conversation does not authorize effects or create a new Work Engine.

## Examples
**User asks to start a test container on Dell.** State device/scope and get real local provider/Ability readiness; send the bounded instruction to a native Agent to check container configuration and safety. If allowed, execute through local provider and verify health. Without owner authorization, return WAITING_AUTHORIZATION. Never manufacture a `docker run` command.

**User asks Codex to direct DeepSeek.** Create an accepted bounded Parent goal; the Parent chooses worker task and isolated branch/worktree, gets an independently running worker receipt, then commissions a different reviewer. GPT reports the Parent acceptance and any unresolved limits, not merely the workers' chat claims.

## Sources, not copies
Canonical responsibility tree and specialist Skill source: `ProfessorZhi/ability-management`. ServerOS Ability Fabric + Fleet binding: `ProfessorZhi/serveros`. This Skill contains no credentials, absolute machine paths, internal prompts, or duplicate local command manuals.
