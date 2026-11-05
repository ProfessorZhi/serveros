# ServerOS Fleet — client distribution

**One Fleet Core, one shared source, one adapter per client, one version.**

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
| install / upgrade | `python3 distribution/build.py install --client claude-code` (replaces only `serveros-fleet@serveros-fleet`) |
| verify | `claude plugin list` shows `serveros-fleet@serveros-fleet` at the version above; a new session lists the `serveros-fleet` tools and 13 Skills |
| uninstall | `claude plugin uninstall serveros-fleet@serveros-fleet && claude plugin marketplace remove serveros-fleet` |
| rollback | `git checkout <previous tag> -- distribution/claude-code && python3 distribution/build.py install --client claude-code` |

## Codex

The tree is a local Codex marketplace (`.agents/plugins/marketplace.json`) with
one plugin (`.codex-plugin/plugin.json`, `.codex-mcp.json`, Skills, assets).

| | |
|---|---|
| install / upgrade | `codex plugin marketplace add <repo>/distribution/codex`, then add `serveros-fleet` from the `serveros-fleet` marketplace |
| verify | the plugin is listed at the version above; `/mcp` shows `serveros-fleet`; OAuth sign-in opens on first use |
| uninstall | remove the plugin, then the `serveros-fleet` marketplace |

Codex discovers OAuth from the server; the binding carries only the URL.

## ChatGPT

The tree is the package source for ChatGPT's plugin upload: `plugin.json`
(agent-plugins schema, `com.openai.interface` with display name **ServerOS Fleet**,
the manifest version and both icons), `mcp.json` (streamable-HTTP, OAuth with dynamic client
registration — no client id, no secret), Skills, assets, README, PRIVACY.

| | |
|---|---|
| package | `python3 distribution/build.py zip --client openai --out dist/` → `serveros-fleet-openai-v<version>.zip` (reproducible; the sha256 is printed) |
| install | upload the zip as the plugin; once Plugin Creator assigns an id, record it as `clients.openai.plugin_id` so later uploads replace it. The retired **ServerOS Fleet Legacy 1.0.0** plugin can be removed after this one signs in |
| after first upload | record the plugin id Plugin Creator assigns in `channels.yaml` → `clients.openai.plugin_id`; later versions update that same id |
| verify | the plugin shows as **ServerOS Fleet** with its icon and version; it signs in to the URL above and lists the Fleet tools |
| rollback | upload the previous version's zip to the same plugin id |

## Releasing a new version

1. Change Skills / assets / text in the shared source; bump `version` in
   `integrations/fleet-v1-first-install/plugin/plugin.json`.
2. `python3 distribution/build.py build`; also `python3 scripts/fleet_plugin.py build`
   (the Legacy-named Claude Code tree under `plugins/`).
3. Commit; CI checks every tree. Merge, tag `fleet-v<version>`.
4. Claude Code: `install`. Codex: re-add the marketplace. ChatGPT: upload the new zip
   to the same plugin id.

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

# Downloads: the `fleet-latest` Release

Every merge to main that touches the plugins, the device kits or the native
collector rebuilds one rolling GitHub Release, **`fleet-latest`**
(`.github/workflows/fleet-release.yml`). On a pull request the same packages
are built as workflow artifacts, so a broken package fails before release.

| asset | use |
|---|---|
| `serveros-fleet-openai-v<ver>.zip` | ChatGPT: upload this file as the plugin |
| `serveros-fleet-codex-v<ver>.zip` | Codex: unzip, `codex plugin marketplace add <dir>` |
| `serveros-fleet-claude-code-v<ver>.zip` | Claude Code (or `build.py install --client claude-code`) |
| `serveros-core-lite-windows-x64.zip` | Windows devices: the collector + status builder, no build tools needed |

The release notes carry the source commit and each file's SHA-256.

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
