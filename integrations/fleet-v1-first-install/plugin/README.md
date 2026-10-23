# ServerOS Fleet — remote sessions on your own devices

This plugin connects an assistant to one Fleet MCP server: the ingress your
operator gave you a URL for. Through it the assistant can list the enrolled
devices and the Claude Code / Codex sessions on them, read a session, send a
message, interrupt a run, and — where the device and the policy allow it —
create a session inside an approved project. It also carries five Skills that
teach the assistant to discover the real device first, reuse an idle session
instead of minting a new one, and accept a delegated result only from evidence
it actually read.

## What is inside

| Path | What it is |
|---|---|
| `plugin.json` (agent-plugins.org) or `.claude-plugin/plugin.json` (Claude Code) | the manifest |
| `mcp.json` / `.mcp.json` | the remote `streamable-http` MCP binding |
| `skills/` | five Skill documents, one `SKILL.md` per directory |
| `assets/` | the composer icon and the larger logo |
| `PRIVACY.md` | what the package does and does not do with data |

## Setup

1. Register the package with your client. In Claude Code, with a marketplace
   that lists it:

   ```
   /plugin install serveros-fleet-ingress@fleet-local
   ```

   The marketplace name is the one that marketplace declares; substitute your
   own. The portable package is registered by the host that supports it.
2. Use a Fleet tool once. The client opens the server's authorization page; sign
   in and choose the scopes you consent to. The server advertises six
   (`fleet:read`, `fleet:send`, `fleet:control`, `fleet:create`, `fleet:approve`,
   `fleet:navigate`); what your token actually carries is the subset you grant.
3. Nothing else is configured here. The device allow-lists, project allow-lists
   and per-session grants live on the Fleet server, not in this package.

## Privacy

The package contains no executable code and no telemetry. It sends data only to
the MCP URL in its binding, and only what Claude Code sends during a tool call.
Read [PRIVACY.md](PRIVACY.md) for the full statement, including what is stored
locally and what the server operator decides.

## What this package does not claim

- **Rendering is a client behaviour.** The icon paths point at the same bytes
  `brand-assets.lock.json` pins by SHA-256, but whether and where a client draws
  them has not been observed.
- **34 tools are not 34 permissions.** The tool surface is wider than any one
  credential: a token carries the scopes granted to it, and every mutating verb
  is checked again against the server's own policy.
- **A Skill is instruction, not authority.** The five Skills describe how to
  use the surface correctly; they cannot grant a scope the token lacks.

## Reading the source tree

If you are looking at this directory in the repository rather than in a built
package, the binding here is `mcp.template.json` and the build, refusals and
verification receipt are documented one level up in
[`../README.md`](../README.md).
