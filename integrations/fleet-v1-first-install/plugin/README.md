# ServerOS Fleet — remote sessions on your own devices

This plugin connects an assistant to one Fleet MCP server: the ingress your
operator gave you a URL for. Through it the assistant can list the enrolled
devices and the Claude Code / Codex sessions on them, read a session, send a
message, interrupt a run, and — where the device and the policy allow it —
create or remove a project and create a session inside it. It also carries
eight Skills that teach the assistant to discover the real device first, reuse
an idle session instead of minting a new one, dispatch long work without
waiting on it, and accept a delegated result only from evidence it actually
read.

## What is inside

| Path | What it is |
|---|---|
| `plugin.json` (agent-plugins.org) or `.claude-plugin/plugin.json` (Claude Code) | the manifest |
| `mcp.json` / `.mcp.json` | the remote `streamable-http` MCP binding |
| `skills/` | eight Skill documents, one `SKILL.md` per directory |
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
   in with your account name, password and the six-digit code from your
   authenticator app, and choose the scopes you consent to. Type these only
   into that page, never into a chat. The server advertises six
   (`fleet:read`, `fleet:send`, `fleet:control`, `fleet:create`, `fleet:approve`,
   `fleet:navigate`); what your token actually carries is the subset you grant.
3. Nothing else is configured here. The device allow-lists, project allow-lists
   and per-session grants live on the Fleet server, not in this package.

## Updating and rolling back

This package is **data**: two manifests, eight Skill documents and two images. An
update replaces those bytes and nothing else. It does not restart the device
runtime, the hub, or any session, and a Skill edit does not change the MCP URL or
the scope string, so it does not ask you to sign in again.

### Updating

1. Advance the package `version` in the source manifest and rebuild. A Skill or
   README edit **without** a version advance is an edit many clients never pick
   up: Claude Code's install/update path keys on the version, so the fix would
   ship and nobody would receive it.
2. Reinstall from the same marketplace. The client fetches the new version into
   its own cache; the previous version stays addressable until it is replaced.

### Rolling back

1. Pin the previous version and reinstall it from the marketplace (or from the
   source revision that produced it — see `../README.md` for the build command).
2. Verify the two version carriers agree again: the plugin manifest and the
   marketplace entry must name the same version, and the Skills must be
   byte-identical to that revision's source.

Rollback is a reinstall, not an undo: the package keeps no state, and the server
keeps the ledger. A milestone that was already reported is not re-reported by a
rollback — that record lives on the Fleet side, not here.

### What neither one does

- No `serverosd` restart, no service reload, no device credential change.
- No scope change. Scopes are fixed at authorization; a package update cannot
  widen them, and rolling back does not narrow them.
- No session is created, interrupted or resumed by installing, updating or
  rolling back this package.

## Privacy

The package contains no executable code and no telemetry. It sends data only to
the MCP URL in its binding, and only what Claude Code sends during a tool call.
Read [PRIVACY.md](PRIVACY.md) for the full statement, including what is stored
locally and what the server operator decides.

## What this package does not claim

- **Rendering is a client behaviour.** The icon paths point at the same bytes
  `brand-assets.lock.json` pins by SHA-256, but whether and where a client draws
  them has not been observed.
- **A tool is not a permission.** The tool surface is wider than any one
  credential: a token carries the scopes granted to it, and every mutating verb
  is checked again against the server's own policy.
- **A Skill is instruction, not authority.** The eight Skills describe how to
  use the surface correctly; they cannot grant a scope the token lacks.

## Reading the source tree

If you are looking at this directory in the repository rather than in a built
package, the binding here is `mcp.template.json` and the build, refusals and
verification receipt are documented one level up in
[`../README.md`](../README.md).
