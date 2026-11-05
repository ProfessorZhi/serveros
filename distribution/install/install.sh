#!/usr/bin/env bash
# ServerOS Fleet — one-command install / update of the commander plugin.
#
#   curl -fsSL <this file> | bash          (or)   bash distribution/install/install.sh
#   bash install.sh --dry-run               print what would happen, change nothing
#   bash install.sh --version 1.3.1         a fixed release instead of fleet-latest
#
# What it does, per client found on this machine:
#   Claude Code   install / update `serveros-fleet@serveros-fleet` (removes the
#                 older `serveros-fleet-v1@serveros-v1` first: one Fleet plugin)
#   Codex CLI     add / refresh the `serveros-fleet` marketplace and the plugin
#   ChatGPT, Claude web/desktop, other agents
#                 cannot be installed from a shell; it prints the exact step
#
# Packages come from the GitHub Release (the repository is private, so `gh`
# must be signed in), or from this checkout when run inside one. No credential
# is downloaded, written or asked for: each client signs in once, on the Fleet
# consent page (account + password + one-time code).
set -euo pipefail

REPO="ProfessorZhi/serveros"
TAG="fleet-latest"
DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --version) TAG="fleet-v${2:?--version needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

HOME_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/serveros-fleet"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
run() { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }
try() { echo "+ $* (may already be done)"; [ "$DRY" = 1 ] || "$@" >/dev/null 2>&1 || true; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo)"
CHECKOUT=""
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../channels.yaml" ]; then CHECKOUT="$(cd "$SCRIPT_DIR/../.." && pwd)"; fi

fetch() {  # fetch <client>  ->  prints the directory holding that client's tree
  local client="$1" dest="$HOME_DIR/$1"
  if [ -n "$CHECKOUT" ]; then echo "$CHECKOUT/distribution/$client"; return; fi
  command -v gh >/dev/null || { echo "gh is required to download from the private release (gh auth login)" >&2; exit 2; }
  [ "$DRY" = 1 ] && { echo "$dest"; return; }
  gh release download "$TAG" --repo "$REPO" --pattern "serveros-fleet-$client-v*.zip" --dir "$WORK" --clobber >&2
  rm -rf "$dest"; mkdir -p "$dest"
  unzip -q "$WORK"/serveros-fleet-"$client"-v*.zip -d "$dest"
  echo "$dest"
}

echo "ServerOS Fleet commander plugin — ${TAG}${CHECKOUT:+ (from checkout $CHECKOUT)}"
found=0
if command -v claude >/dev/null; then
  found=1; echo; echo "== Claude Code"
  tree="$(fetch claude-code)"
  try claude plugin uninstall serveros-fleet-v1@serveros-v1
  try claude plugin marketplace remove serveros-v1
  try claude plugin uninstall serveros-fleet@serveros-fleet
  try claude plugin marketplace remove serveros-fleet
  run claude plugin marketplace add "$tree"
  run claude plugin install serveros-fleet@serveros-fleet
fi
if command -v codex >/dev/null; then
  found=1; echo; echo "== Codex CLI"
  tree="$(fetch codex)"
  try codex plugin marketplace add "$tree"
  run codex plugin add serveros-fleet@serveros-fleet
fi
[ "$found" = 1 ] || echo "No Claude Code or Codex CLI on this machine."

cat <<'NEXT'

== Clients installed by hand (once; later versions replace the same entry)
  ChatGPT (web) + Codex (desktop):
    upload serveros-fleet-openai-v<version>.zip from the release on
    https://chatgpt.com/plugins/plugin_asdk_app_6aec2811173881918325a3dc82742d8d
  Claude (web / desktop / Cowork):
    Settings → Connectors → add custom connector  https://fleet-v1.professorzhi.fun/mcp
  Any other MCP agent (DeepSeek Harness, …):
    serveros-fleet-mcp-v<version>.zip — mcp.json + skills/, see its README

Each client signs in once on the Fleet consent page: account, password, one-time code.
NEXT
