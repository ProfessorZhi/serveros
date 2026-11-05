# ServerOS Fleet — client distribution

**One Fleet Core, one shared source, one adapter per client, one version.**

## How each client installs and updates

| client | install once | update |
|---|---|---|
| Claude Code | `install.sh` / `install.ps1` (or `claude plugin marketplace add ProfessorZhi/serveros` + `claude plugin install serveros-fleet@serveros-fleet`) | `claude plugin marketplace update serveros-fleet && claude plugin update serveros-fleet@serveros-fleet`; or turn on auto-update for the marketplace in `/plugin` |
| Codex CLI | `install.sh` / `install.ps1` (or `codex plugin marketplace add ProfessorZhi/serveros` + `codex plugin add serveros-fleet@serveros-fleet`) | `codex plugin marketplace upgrade serveros-fleet && codex plugin add serveros-fleet@serveros-fleet` |
| ChatGPT web + Codex desktop (one zip) | upload [`serveros-fleet-openai.zip`](https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-openai.zip) on the plugin page | upload the same link again; it always holds the newest version |
| Claude web / desktop / Cowork | Settings → Connectors → custom connector `https://fleet-v1.professorzhi.fun/mcp` | nothing to update: tools come from the server |
| any other MCP agent | [`serveros-fleet-mcp.zip`](https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-mcp.zip): `mcp.json` + `skills/` | download again for new Skills |

The repository root **is** the `serveros-fleet` marketplace for both CLIs
(`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`, written
by `build.py build` and pointing into `distribution/claude-code/` and
`distribution/codex/`), so merging to main is what releases to them. The
installers add it with a sparse checkout of just those directories. A release
tag pins a version: `install.sh --version 1.3.2` (`#fleet-v1.3.2`).

Fleet *tools* live on the hub: deploying the hub changes them for every client
at once, with no plugin update. A plugin update is only needed for Skills and
manifest text.

| | where |
|---|---|
| Fleet Core (the MCP server) | `fleet_mcp/`, served at `https://fleet-v1.professorzhi.fun/mcp` (Tokyo :3412, Cloudflare Tunnel) |
| Shared source | `integrations/fleet-v1-first-install/plugin/` — 13 Skills, `assets/composerIcon.png` + `assets/logo.png` (pinned in `brand-assets.lock.json`), the portable manifest (its `version` is the release version), README, PRIVACY |
| Release manifest | `distribution/channels.yaml` — server URL, the Fleet identity, one entry per client |
| Client trees (generated, committed) | `claude-code/`, `codex/`, `openai/` |
| Build / check / package / install | `python3 distribution/build.py build|check|zip|install` |

No client tree is edited by hand. Change a Skill, an asset or the version in the
shared source, run `build`, commit both. CI runs `check` and fails if any tree is
stale. Every client gets the same Skills byte for byte, the same icons, the same
MCP server (`serveros-fleet` → the URL above) and the same version.

The identity is `serveros-fleet` everywhere and the client shows it as
**ServerOS Fleet**, with the version from the manifest beside it; no name carries
a version. It replaces the earlier `serveros-fleet@serveros-fleet` in place:
`build.py install` removes that one first.

## Claude Code

| | |
|---|---|
| install / upgrade | `install.sh` / `install.ps1` from GitHub (above); from a checkout, `install.sh --local` or `python3 distribution/build.py install --client claude-code` |
| verify | `claude plugin list` shows `serveros-fleet@serveros-fleet` at the version above; a new session lists the `serveros-fleet` tools and 13 Skills |
| uninstall | `claude plugin uninstall serveros-fleet@serveros-fleet && claude plugin marketplace remove serveros-fleet` |
| rollback | `git checkout <previous tag> -- distribution/claude-code && python3 distribution/build.py install --client claude-code` |

## Codex

The tree is a local Codex marketplace (`.agents/plugins/marketplace.json`) with
one plugin (`.codex-plugin/plugin.json`, `.codex-mcp.json`, Skills, assets).

| | |
|---|---|
| install / upgrade | `install.sh` / `install.ps1` from GitHub (above); from a checkout, `install.sh --local` |
| verify | the plugin is listed at the version above; `/mcp` shows `serveros-fleet`; OAuth sign-in opens on first use |
| uninstall | remove the plugin, then the `serveros-fleet` marketplace |

Codex discovers OAuth from the server; the binding carries only the URL.

## ChatGPT (web) and Codex (desktop)

ChatGPT runs a plugin on the web only when its tools come from an **App
registered in the account**; a package that declares its own MCP server is
treated as desktop-only. And an update of an App-backed plugin must leave the
**App's own fields** exactly as they are (author, description, category,
developer name, display name, descriptions, icons): ChatGPT refuses an upload
that changes them (1.3.0 was refused for this).

So the package is:

- the App's fields, copied verbatim from `distribution/openai-app.json` (a
  snapshot of the plugin as it stands in the account);
- what the plugin owns: the plugin name and App id from `channels.yaml`
  (`.app.json`), the version, and the 13 Skills.

To change the name, description or **icon** that ChatGPT shows, edit the App
in ChatGPT's App settings, then update `openai-app.json` to match. Never
change them through the package. Uploading a new version replaces the same
plugin.

| | |
|---|---|
| package | `python3 distribution/build.py zip --client openai --out dist/`, or `serveros-fleet-openai-v<version>.zip` from the release |
| install / update | upload the zip on [the ServerOS Fleet plugin page](https://chatgpt.com/plugins/plugin_asdk_app_6aec2811173881918325a3dc82742d8d) |
| verify | the plugin shows **ServerOS Fleet** with its icon and the new version; Fleet tools answer on the web and in Codex |
| rollback | upload the previous version's zip from `fleet-v<previous>` |
| other accounts | the App id is this account's. Another account registers its own App for `https://fleet-v1.professorzhi.fun/mcp`, and its id goes into its own build |

## Any other MCP agent

`distribution/mcp`, released as `serveros-fleet-mcp-v<version>.zip`. It holds
`mcp.json` (streamable HTTP, OAuth by dynamic registration) and the Skills as
plain `SKILL.md` files. Wire it in whatever form the agent loads MCP servers
and instructions (DeepSeek Harness, custom agents). The agent signs in on the
Fleet consent page like every other client.

## One command on a machine

`distribution/install/install.sh` (Linux, macOS) and `install.ps1` (Windows)
install or update every client they find:

- Claude Code: `serveros-fleet@serveros-fleet` from the GitHub marketplace, with
  the older `serveros-fleet-v1@serveros-v1` and `serveros-fleet-ingress@serveros`
  removed first;
- Codex CLI: the `serveros-fleet` marketplace from GitHub and its plugin.

The repository is public, so nothing needs signing in to download. They print
the one-time steps for ChatGPT, Claude web and other agents, and the update
commands. `--dry-run` / `-DryRun` prints the steps and changes nothing;
`--version` / `-Version` pins a release tag; `--local` / `-Local` uses this checkout.

## Releasing a new version

1. Change Skills / assets / text in the shared source; bump `version` in
   `integrations/fleet-v1-first-install/plugin/plugin.json`.
2. `python3 distribution/build.py build`; also `python3 scripts/fleet_plugin.py build`
   (the Legacy-named Claude Code tree under `plugins/`).
3. Commit; CI checks every tree. Merge: the release workflow publishes `fleet-v<version>` and `fleet-latest`.
4. Claude Code / Codex CLI pick it up on their next `marketplace update` /
   `upgrade` (commands above). ChatGPT: upload `serveros-fleet-openai.zip` from
   `fleet-latest` to the same plugin id.

MCP tool changes ship with the server (Tokyo Fleet V1), not with these packages;
a client sees them after its next tool refresh.

# Device kits — one per device

The commander plugin above is only the hub's fourth layer. Every device gets
the same four layers, declared once in `distribution/devices.yaml` and generated
into `distribution/devices/<device>/` (`DEVICE.json` + `README.md`, committed,
checked in CI by `device_kit.py check`):

| layer | what | tokyo | dell | work-fj (Windows) |
|---|---|---|---|---|
| 1 core plugin | serveros core (`plugin/profiles/linux-work.yaml` → `hwz-server-os-linux-work`) for Claude Code + Codex; the same payload is the node runtime (serverosd) | ✓ | ✓ | standalone skills via the family |
| 2 ability family | ability-management profile, rolled out at one pinned revision and read back (discovery of the rest: `control_plane.py catalog search/install`) | `tokyo-development-primary` | `dell-gpu-compute` | `work-fj-windows` |
| 3 monitor · basic | serverosd | ✓ | ✓ | — |
| 3 monitor · native | core-lite C++ collector → status file (Linux: systemd timer, 30 s; Windows: scheduled task, 1 min) | ✓ | ✓ | ✓ Windows collector (Win32) |
| 3 monitor · link | node connector → hub | — | ✓ | ✓ |
| 3 monitor · hub | agent monitor (`fleet-hub-watch`): Fleet run outcomes, completion / alert / daily cards to Feishu | ✓ | — | — |
| 4 commander | `serveros-fleet@serveros-fleet` | ✓ | — | — |

```bash
REV=$(git -C ../ability-management rev-parse origin/main)
python3 distribution/device_kit.py plan    --device dell --ability-rev $REV --data-root $DATA_ROOT
python3 distribution/device_kit.py install --device dell --ability-rev $REV --data-root $DATA_ROOT --apply
python3 distribution/device_kit.py install --device dell --ability-rev $REV --data-root $DATA_ROOT --apply --enable
```

`install` without `--apply` only prints; with `--apply` it runs the steps in
order and stops at the first failure, writing a receipt to
`~/.local/state/serveros/device-kit/<device>.receipt.json`. Unit files are
written but nothing is enabled or started without `--enable`. The node
connector step is a preflight: its real install needs the device's own
credential anchor and tunnel key, which only the owner supplies. On the hub,
`--notify lark:user:<open_id>` switches the agent monitor from the local
journal to Feishu. A Windows kit is plan-only here; its README holds the
PowerShell to run on the device.

Add a device: one entry in `devices.yaml` (and a core-lite profile in
`native/node-core/core-lite/profiles/` whose `node` is its device ref), then
`device_kit.py build` and commit.

# Downloads: `fleet-v<version>` and `fleet-latest`

Every merge to main that touches the plugins, the device kits or the native
collector publishes **`fleet-v<version>`** once (it is never replaced, so it
is what a rollback downloads) and rebuilds the rolling **`fleet-latest`**
(`.github/workflows/fleet-release.yml`). On a pull request the same packages
are built as workflow artifacts, so a broken package fails before release.

| asset | use |
|---|---|
| `serveros-fleet-openai-v<ver>.zip` | ChatGPT web + Codex desktop: upload on the plugin page (replaces it); on `fleet-latest` also as `serveros-fleet-openai.zip`, a link that never changes |
| `serveros-fleet-claude-code-v<ver>.zip` | Claude Code offline copy (installs normally come from GitHub) |
| `serveros-fleet-codex-v<ver>.zip` | Codex CLI offline copy (installs normally come from GitHub) |
| `serveros-fleet-mcp-v<ver>.zip` | any other MCP agent: `mcp.json` + `skills/`; on `fleet-latest` also as `serveros-fleet-mcp.zip` |
| `install.sh` / `install.ps1` | one-command install / update on a machine |
| `serveros-core-lite-windows-x64.zip` | Windows devices: the collector + status builder, no build tools needed |

The release notes carry the source commit and each file's SHA-256.

# Deploying the hub (Tokyo)

Installing a client plugin does not update the Fleet server. The hub runs from
its own checkout and is updated separately:

```bash
cd ~/src/serveros-v1 && git fetch origin && git checkout --detach <release commit>
git rev-parse HEAD                                   # must equal the release's commit
systemctl --user restart serveros-fleet-v1 serveros-fleet-watch
curl -fsS https://fleet-v1.professorzhi.fun/health   # ok
```

The release notes name the commit each version was built from.

# Updating a device in place

| device | command (run on the device, from its serveros checkout) |
|---|---|
| Linux (tokyo, dell) | `python3 distribution/device_kit.py update --device <name> --data-root $DATA_ROOT --apply` |
| Windows (work-fj) | `powershell -ExecutionPolicy Bypass -File distribution\windows\update.ps1 -Device work-fj -FromRelease -Apply` |

Both pull main, re-apply every layer the device declares (core plugin and node
runtime, ability family at one pinned revision, the collector, the publish
timer or task), restart only what was already running, and write what the device
now runs to `device-versions.json` (`~/.local/state/serveros/` or
`%LOCALAPPDATA%\ServerOS\state\`). Without `--apply` / `-Apply` both only print.
They are meant to be run by whoever can reach the device, a person or an agent
session on it; the hub never pushes an install.

The hub reads every device's record with the `fleet_device_versions` tool: a
device on the node channel answers the signed `GET /v1/device/versions` route,
the hub's own device is read locally. More than one entry in
`distinct_serveros_commits` means a device is behind; `NOT_RECORDED` means no
update has run there yet.
