# Onboarding the Next node to the tailnet — where the browser block is, and why

The Next tailnet daemon is PREAUTH (`BackendState = NeedsLogin`). Clearing that
needs one browser action by the tailnet owner. This note records why no agent on
this host may take that action, and what to do instead.

## What was measured, not assumed

| thing | state |
|---|---|
| `opencli` | installed, v1.8.7, user-local (`~/.local/bin/opencli`) |
| `opencli doctor` | `[OK] Daemon` (port 19825) · `[MISSING] Extension: not connected` · `[FAIL] Connectivity` |
| `opencli profile list` | no Browser Bridge profiles connected |
| the GUI profile actually in use | `tokyo-webgui/state/chrome-profile`, reached over Xvfb `:192` → x11vnc → noVNC on `127.0.0.1:6082` |
| Browser Bridge extension in that profile | **absent** (0 extension manifests) |
| bundled in the npm package | **none** — the extension ships only via GitHub Releases |

So the extension route is unavailable without a manual `chrome://extensions` →
*Load unpacked*, and the account the sign-in would need is the owner's, not one
this host holds.

## The CDP route exists — and must not be used here

OpenCLI documents a Chrome DevTools Protocol alternative
(`OPENCLI_CDP_ENDPOINT`) aimed exactly at "a remote headless server environment
without a UI". It works. It is still the wrong tool **on this host**:

- Chrome binds the debugging port to `127.0.0.1`, and on Linux that is reachable
  by **every local user**. This is a shared multi-user machine.
- CDP has **no authentication**. Any local process could drive the attached
  browser.
- The session being created holds a **one-time Tailscale authorisation URL** and
  then the owner's tailnet login. Attaching it to an unauthenticated local port
  would expose the authorisation link itself, not merely the ability to click.

A localhost debugging port that is safe on a single-user laptop is a credential
exposure on a shared host. The distinction is the host, not the tool.

## What to do instead

1. On a **single-user device** the owner controls, ensure Chrome is signed in to
   the tailnet account that should own the new node.
2. Install the Bridge extension: download `opencli-extension-v*.zip` from the
   OpenCLI GitHub Releases, `chrome://extensions` → Developer mode → *Load
   unpacked*.
3. Open the one-time authorisation URL **there** and complete the browser MFA.
   Never paste it into a shared host's browser, and never into a log, an issue,
   or a commit.
4. Come back to Tokyo and read the result from the **Next-only** socket:

   ```bash
   ~/.local/opt/fleet-next-tailscale/1.104.1/tailscale_1.104.1_amd64/tailscale \
     --socket="$HOME/.local/state/fleet-next-tailscale/tailscaled.sock" status
   ```

   `BackendState` moves from `NeedsLogin` to `Running`, and `TailscaleIPs`
   becomes non-empty. Both are read-only; neither starts anything.

## What still may not happen here

No `tailscale serve` / `funnel`, no `systemctl enable` of the Next unit, no
starting the Next service, and no change to V1's Funnel or the four other
`tailscaled` instances. Those remain separate owner-approved steps, gated by
`fleet_mcp/deploy/next_tailscale_gate.py`.
