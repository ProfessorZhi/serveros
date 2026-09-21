# Claude / Codex Semantic Parity

Product authority: [ARCHITECTURE.md](../ARCHITECTURE.md), sections 6, 19 and 29.

**One work world; runtime-specific, version-tested adapters.**

## Shared semantics

Both runtimes use the same Project/Item identity, accepted intent, ownership/policy,
source and evidence references, placement semantics and continuity model. A new
Session can recover useful work without replaying the entire old transcript.

## Legitimate differences

Native instruction and auto-memory loading, Skill packaging, plugin distribution,
hooks, hot MCP attachment, native thread APIs, background execution and subagent
support may differ. These are real adapter capability differences, not automatically
bugs in canonical content.

Do not assume a .codex-plugin directory is a supported installation format solely
because .claude-plugin exists. The selected Codex runtime's documented/tested
extension interfaces decide its packaging. Preserve existing artifact identities and
migrate older packaging deliberately.

Quay's Claude-based execution may be requested by Codex through a verified delegated
CC runtime. This is not the same as claiming the Quay Claude plugin runs natively in
Codex. Native support, delegated support and unsupported operation remain distinct.

## Adapter evidence matrix

For each supported runtime/version, record evidence for the operations actually
provided:

| Area | What to establish |
|---|---|
| bootstrap | default thin ServerOS entry; native instructions preserved |
| discovery | Skills/tools remain discoverable; no duplicate forced catalogue |
| context | prompt resolves correct work; sparse/unbound contexts are legal |
| start/resume | actual native Session/thread behavior, not assumed equivalence |
| send/result | controller ownership, deduplicated input and observed completion |
| stop | requested vs observed stop and resource release |
| checkpoint | graceful events supported; abrupt-failure recovery is honest |
| attachment | actual install/connect/load boundaries and fallback |
| handoff | same durable work and exact revisions, destination-local paths |
| safety | declared enforcement points and uncovered cooperative paths |

Do not turn missing support into a fabricated success or silently use a different
identity, working directory or permission scope.

## Dual-runtime conformance

Run relevant adapter scenarios at important release boundaries, not after every
business task. Check:

1. Both resolve the same declared work from the same accepted inputs.
2. Neither invents an Item from an ordinary repository cwd alone.
3. Native Skill discovery remains available even when the ServerOS hint is wrong.
4. A memory/optional-provider outage leaves safe direct work possible.
5. A checkpoint produced in one runtime can continue in another without losing
   intent, revisions, evidence or protected-resource boundaries.
6. Same-device takeover preserves the resolved worktree when appropriate;
   cross-device handoff resolves new local paths instead of copying absolute paths.
7. Unsupported runtime operations are visibly unsupported.

A successful design or generated manifest is not installation or live-session proof.
