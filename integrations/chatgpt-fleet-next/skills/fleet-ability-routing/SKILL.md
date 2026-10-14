---
name: fleet-ability-routing
description: Route a ChatGPT request to the proper ServerOS semantic Ability and effective local Provider, separating capability discovery from permission and actual execution. Use for host-dependent tasks and cross-device selection, not as a replacement for the local native Skill.
---

# Fleet Ability Routing — Cloud-side thin Skill

This draft is not installed. **GPT describes WHAT and asks the target Native Agent HOW.** The actual ability source/version and Provider resolution live in `ProfessorZhi/ability-management` and ServerOS Ability Fabric, not in this Skill.

## The semantic pipeline
`User goal → G1 semantic owner / Ability (+ known operation) → candidate devices → EffectiveAbility resolution → owner/verb grant → native agent → receipt → Parent acceptance`.

Existing stable responsibilities include A1 Session, A3 Workspace, A4 Runtime & Resource, A5 Fleet, A6 Delegation, A7 Testing, A8 Build/Release, A9 Observe and A10 Production Change. A single task may combine several, but none receives permission by being named.

The effective availability is an **intersection** of AbilityDefinition, Provider, DeviceCapabilities, Profile, Binding, AgentRuntimeCapabilities, RuntimeHealth, Policy and TaskContext. Consult the canonical current provider/ability contract and probes. Treat `AVAILABLE`, `DEGRADED`, `UNAVAILABLE`, `INCOMPATIBLE`, `UNKNOWN` as distinct. The provider operation `ability#operation` is valid only when that operation actually exists in the current catalogue; do not invent it.

## Selection rule
1. Use available user constraints to identify the required outcome, data locality, identity, risk, software, OS, GPU and network.
2. Resolve live device/ref and runtime. Do not infer from a friendly machine name, provider family or a screenshot.
3. Consult the pinned Ability catalogue and the current device/provider binding for the **actual** operation. If the connected GPT app cannot query it directly, delegate a read-only local resolution to an authorized Native Agent rather than hallucinate available tools.
4. Prefer native implementation already installed and suitable. If unavailable, name the exact unsupported/missing input; no silent fallback to SSH, RDC or another user's Runtime.
5. Pass a bounded task to the Native Agent. Let it inspect local, authorized Skill/CLI/API documentation, path contract, existing services and resource budgets. Do not ship or reproduce credentials or absolute paths in cloud prompts.
6. If an effect is requested, verify its separate grant; a successful read/inspection does not confer create/restart/deploy rights.
7. Accept only a receipt with version, target, observed actual state, testing/evidence and honest UNKNOWN fields.

## Example: user says “起一个容器”
- Ability family likely A4 Runtime; whether the exact `container-start` operation is registered is **UNKNOWN until resolved**, not a guaranteed Tool.
- Native Agent checks the real container provider, daemon readiness, Compose project, port/volume conflicts, resource policy and owner. For a production deployment it asks for explicit authorization.
- On success, it returns actual container/project identity and a health/readiness receipt. A generated compose file or provider ACK does not count as running service.
- On failure, report the specific missing provider, ungranted effect or operational error, and do not re-run a different command without checking idempotency.

## Review and authority
G1 governs Skill revisions and sources; A4/Node Core reports local OS facts; A5/Fleet controls identity/routing; A6 organizes bounded children; Work Parent is the acceptance owner; B10 is the human decision owner. OpenAgents is a replaceable A6 network adapter only.

The cloud-side plugin Skill is intentionally thin: documentation of **when**, **where**, **who**, and **how to verify**, not a machine-specific operation manual. It should work even if the execution Provider changes from Claude Code to Codex or a DeepSeek-powered native worker.
