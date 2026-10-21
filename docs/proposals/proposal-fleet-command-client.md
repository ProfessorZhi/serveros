# Fleet command-side adaptation and client packaging

## Problem Statement

The Fleet control side now owns a managed account configured once by the owner
at the highest native permissions the owner chose. The operator Skills still
read like a per-call approval workflow: create is described as an exceptional
path, every write seems to need a fresh grant, and the shared text hard-codes a
tool count and a single Claude/DeepSeek default. That conflicts with the
authorized operating model and with independent Codex/Claude model and
permission profiles.

The client side has only a Claude Code development adapter. Codex 0.160.0 uses a
different plugin format (`.codex-plugin/plugin.json`, `.codex-mcp.json`, and a
marketplace manifest), not the Claude `.claude-plugin`/`.mcp.json` format. The
update checker also needs to stop treating a fixed tool count as a permanent
fact and leave project-creation entry points to the core capability surface.

## Goals

- Correct the **same eight shared Skills** so they describe:
  - one owner-configured managed profile, after which routine create/send/
    dispatch inside that profile is pre-authorized and is not re-asked per call;
  - CC and Codex each read back their own effective model and highest native
    permission; no fleet-wide model default and no cross-family model id;
  - no claim of Linux root, `sudo`, or control over another OS account;
  - new-project creation only through an actual capability exposed by the core,
    discovered from the real tool list; never guess a tool name;
  - the existing DAG, complete long-task envelopes, concurrent dispatch/ACK,
    stable references, UNKNOWN no-blind-resend and evidence closure.
- Keep one shared Skills source for the portable package, the Claude Code
  adapter and the new Codex adapter.
- Add an isolated Codex client adapter that loads the shared Skills and a Fleet
  MCP binding without touching the user's global Codex configuration.
- Keep the update package's same plugin identity and App-owned configuration
  protection; produce local, reviewable versioned artifacts, hashes and
  rollback notes without inventing Creator IDs or versions.
- Do not hard-code the tool count as a permanent product fact; read the actual
  `tools/list` result.

## Non-Goals

- No change to `fleet_mcp/**`, `runtime/**`, `native/**`, WorkEngine or Core.
- No new tool name promised by this lane. The project-create entry point is a
  core deliverable; this lane consumes it after the core exposes it.
- No production deployment, plugin upload, Creator call, OAuth consent, global
  install or paid model probe.
- No claim that a Codex plugin ZIP and a Claude plugin directory are the same
  format, or that Skills survive their host.
- No `sudo`, OS-account administration, credential access or universal bypass.

## Design

### Owner-max managed profile

The owner configures one managed account/runtime/project ceiling once. Within
that configured scope, `fleet_create_session`, `fleet_send_to_session` and
dispatch are routine and pre-authorized; the operator does not ask a human to
approve each ordinary call. This is an authorization normalisation, not a new
authority:

- the effective scope remains the owner's configured scope and device/runtime
  bindings, not a universal permission;
- CC and Codex have independent model/provider/permission profiles; the
  effective model and highest native permission are read back after create and
  reported per runtime;
- `bypassPermissions`, if configured by the owner, belongs to that runtime,
  account and project. It is never "the fleet runs with bypass", never a
  requestable widening, and never OS root/sudo;
- a human decision is still required when the target is outside the configured
  profile, the capability is absent, identity is ambiguous or contradictory,
  or an irreversible action would exceed the grant.

### Project creation capability

Creating a **project** is not the same as creating a session. The core owns the
entry point. The client discovers the actual surface (`tools/list` / the
plugin's recorded tool list), and if a project-create capability is present,
uses that exact name and schema. If it is absent, the client reports
`PROJECT_CREATE_CAPABILITY_NOT_EXPOSED` and returns the decision to the core/
owner. It does not invent a name, edit a device file, or fall back to another
account. The tool count is whatever the surface currently serves; it is not a
constant in the Skills or package.

### Shared Skills

The eight existing names stay. The semantic corrections are concentrated in
`safety-boundaries`, `create-with-grants`, `reuse-idle-session`,
`discovery`, `commander-orchestration` and `plugin-update`; `async-dispatch`
keeps its delivery/UNKNOWN contract, and `evidence-review` keeps its
read-the-artifact rule. The source remains one directory and remains shallow.

### Codex client adapter

Codex 0.160.0 is verified locally to consume:

- `.codex-plugin/plugin.json` with `skills: "./skills/"` and
  `mcpServers: "./.codex-mcp.json"`;
- a remote streamable-HTTP MCP entry as `{"mcpServers": {name: {"url": ...}}}`
  or a stdio entry with `command`/`args`/`cwd`;
- a local marketplace `.agents/plugins/marketplace.json` with a `local` source
  path.

The adapter builder reads the same shared Skills source and writes a Codex
plugin directory. It supports the remote HTTP binding and an opt-in
`stdio-owner` binding to the local trusted `bin/serveros-fleet-owned --stdio
--config PATH` entry; the latter carries no OAuth fields and never reads the
config or a credential. A separate local-fixture builder writes a marketplace
and a stdio MCP fixture for a no-model handshake. The real handshake uses an isolated
`CODEX_HOME` and a local marketplace; it never edits `/data/home/vince/.codex`
or any other global config. CC and Codex artifacts have different layouts and
are not described as interchangeable ZIP formats.

### Update package

The existing overlay identity rules remain: same `plugin_id`, name,
displayName, App binding, protected manifest/MCP configuration and non-Skill
member bytes; only version, Skills/supporting files, keywords, onboarding and
local-executor changes are overlay-updatable. The package is local and
versioned with hashes and a rollback target. No Creator backend ID or live
version is invented. The actual tool list is read after the core exposes the
project entry; the count is not frozen.

## Proposal review rounds

### Round 1

- **Warning — owner-max could be read as universal privilege.** Resolution:
  separate one-time owner configuration from OS root/sudo and other OS accounts;
  require effective-scope and effective-model read-back; ask only outside scope.
- **Warning — routine approval conflict.** Resolution: explicitly say routine
  create/send/dispatch inside the managed profile is pre-authorized, while
  preserving the outer-grant/permission refusal cases.
- **Warning — project entry point not yet exposed.** Resolution: capability
  discovery, report missing rather than guess; no dependency on a tool name or
  a fixed count.
- **Warning — Codex format assumption.** Resolution: use the locally verified
  Codex 0.160.0 layout and marketplace; do not reuse the CC ZIP layout.

### Round 2

Re-read against the eight existing Skill names, the package identity constraints
and the actual Codex CLI. No unresolved critical/warning remains. The remaining
risk is that the core's project-create capability is not implemented yet; the
Skills therefore report it as a missing capability, not as an available verb.

## Open Questions / Unverified

- The exact core project-create verb/schema is pending root; this lane does not
  create it.
- Live Creator packaging, OAuth and production updates remain UNVERIFIED.
- Real model behaviour after the Skill rewrite is not measured; only source
  and package/handshake checks are in scope.
