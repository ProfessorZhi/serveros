# ServerOS Fleet V1 — client distribution

**One Fleet Core, one shared source, one adapter per client, one version.**

| | where |
|---|---|
| Fleet Core (the MCP server) | `fleet_mcp/`, served at `https://fleet-v1.professorzhi.fun/mcp` (Tokyo :3412, Cloudflare Tunnel) |
| Shared source | `integrations/fleet-v1-first-install/plugin/` — 13 Skills, `assets/composerIcon.png` + `assets/logo.png` (pinned in `brand-assets.lock.json`), the portable manifest (its `version` is the release version), README, PRIVACY |
| Release manifest | `distribution/channels.yaml` — server URL, the V1 identity, one entry per client |
| Client trees (generated, committed) | `claude-code/`, `codex/`, `openai/` |
| Build / check / package / install | `python3 distribution/build.py build|check|zip|install` |

No client tree is edited by hand. Change a Skill, an asset or the version in the
shared source, run `build`, commit both. CI runs `check` and fails if any tree is
stale. Every client gets the same Skills byte for byte, the same icons, the same
MCP server (`serveros-fleet-v1` → the URL above) and the same version.

The V1 identity is `serveros-fleet-v1` everywhere. It never reuses the Legacy
plugin's name, so V1 is always installed **beside** Legacy, never over it.

## Claude Code

| | |
|---|---|
| install / upgrade | `python3 distribution/build.py install --client claude-code` (replaces only `serveros-fleet-v1@serveros-v1`) |
| verify | `claude plugin list` shows `serveros-fleet-v1@serveros-v1` at the version above; a new session lists the `serveros-fleet-v1` tools and 13 Skills |
| uninstall | `claude plugin uninstall serveros-fleet-v1@serveros-v1 && claude plugin marketplace remove serveros-v1` |
| rollback | `git checkout <previous tag> -- distribution/claude-code && python3 distribution/build.py install --client claude-code` |

## Codex

The tree is a local Codex marketplace (`.agents/plugins/marketplace.json`) with
one plugin (`.codex-plugin/plugin.json`, `.codex-mcp.json`, Skills, assets).

| | |
|---|---|
| install / upgrade | `codex plugin marketplace add <repo>/distribution/codex`, then add `serveros-fleet-v1` from the `serveros-v1` marketplace |
| verify | the plugin is listed at the version above; `/mcp` shows `serveros-fleet-v1`; OAuth sign-in opens on first use |
| uninstall | remove the plugin, then the `serveros-v1` marketplace |

Codex discovers OAuth from the server; the binding carries only the URL.

## ChatGPT

The tree is the package source for ChatGPT's plugin upload: `plugin.json`
(agent-plugins schema, `com.openai.interface` with display name **ServerOS Fleet
V1** and both icons), `mcp.json` (streamable-HTTP, OAuth with dynamic client
registration — no client id, no secret), Skills, assets, README, PRIVACY.

| | |
|---|---|
| package | `python3 distribution/build.py zip --client openai --out dist/` → `serveros-fleet-v1-openai-v<version>.zip` (reproducible; the sha256 is printed) |
| install | upload the zip as a **new** plugin. Do not replace, uninstall or edit **ServerOS Fleet Legacy 1.0.0**; it stays as it is |
| after first upload | record the plugin id Plugin Creator assigns in `channels.yaml` → `clients.openai.plugin_id`; later versions update that same id |
| verify | the connector signs in to the URL above and lists the Fleet V1 tools; Legacy still works |
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
| 4 commander | `serveros-fleet-v1@serveros-v1` | ✓ | — | — |

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
