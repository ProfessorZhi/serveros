#!/usr/bin/env bash
# ServerOS Fleet — one-command install / update of the commander plugin.
#
#   curl -fsSL https://raw.githubusercontent.com/ProfessorZhi/serveros/main/distribution/install/install.sh | bash
#   bash install.sh --dry-run               print what would happen, change nothing
#   bash install.sh --version 1.3.2         pin a released version (git tag fleet-v1.3.2)
#   bash install.sh --local                 install from this checkout instead of GitHub
#
# Claude Code and Codex CLI install from GitHub: the repository root is the
# `serveros-fleet` marketplace for both, so after this one run an update is
#   claude plugin marketplace update serveros-fleet && claude plugin update serveros-fleet@serveros-fleet
#   codex plugin marketplace upgrade serveros-fleet && codex plugin add serveros-fleet@serveros-fleet
# (or this script again). It first removes the plugins this one replaces
# (`serveros-fleet-v1@serveros-v1`, `serveros-fleet-ingress@serveros`): one
# Fleet plugin per client. ChatGPT, Claude web/desktop and other agents cannot
# be installed from a shell; it prints the exact step.
#
# The repository is public: nothing to sign in to for the download. No
# credential is downloaded, written or asked for: each client signs in once,
# on the Fleet consent page (account + password + one-time code).
set -euo pipefail

REPO="ProfessorZhi/serveros"
REF="main"
DRY=0
LOCAL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --version) REF="fleet-v${2:?--version needs a value}"; shift 2 ;;
    --local) LOCAL=1; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

run() { echo "+ $*"; [ "$DRY" = 1 ] || "$@"; }
try() { echo "+ $* (may already be done)"; [ "$DRY" = 1 ] || "$@" >/dev/null 2>&1 || true; }

SOURCE="$REPO"
if [ "$LOCAL" = 1 ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo)"
  [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/../channels.yaml" ] \
    || { echo "--local needs this script inside a serveros checkout" >&2; exit 2; }
  SOURCE="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi

echo "ServerOS Fleet commander plugin — $SOURCE$([ "$LOCAL" = 1 ] || echo " @ $REF")"
found=0
if command -v claude >/dev/null; then
  found=1; echo; echo "== Claude Code"
  try claude plugin uninstall serveros-fleet-v1@serveros-v1
  try claude plugin marketplace remove serveros-v1
  try claude plugin uninstall serveros-fleet-ingress@serveros
  try claude plugin marketplace remove serveros
  try claude plugin uninstall serveros-fleet@serveros-fleet
  try claude plugin marketplace remove serveros-fleet
  if [ "$LOCAL" = 1 ]; then
    run claude plugin marketplace add "$SOURCE"
  else
    run claude plugin marketplace add "https://github.com/$SOURCE.git#$REF" --sparse .claude-plugin distribution/claude-code
  fi
  run claude plugin install serveros-fleet@serveros-fleet
fi
if command -v codex >/dev/null; then
  found=1; echo; echo "== Codex CLI"
  try codex plugin remove serveros-fleet@serveros-fleet
  try codex plugin marketplace remove serveros-fleet
  if [ "$LOCAL" = 1 ]; then
    run codex plugin marketplace add "$SOURCE"
  else
    run codex plugin marketplace add "$SOURCE" --ref "$REF" --sparse .agents --sparse distribution/codex
  fi
  run codex plugin add serveros-fleet@serveros-fleet
fi
[ "$found" = 1 ] || echo "No Claude Code or Codex CLI on this machine."

cat <<'NEXT'

== Update later (or run this script again)
  claude plugin marketplace update serveros-fleet && claude plugin update serveros-fleet@serveros-fleet
  codex plugin marketplace upgrade serveros-fleet && codex plugin add serveros-fleet@serveros-fleet

== Clients installed by hand (once; a later upload replaces the same entry)
  ChatGPT (web) + Codex (desktop): upload
    https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-openai.zip
    on https://chatgpt.com/plugins/plugin_asdk_app_6aed2ec2e0e48191b438082e444c3210
  Claude (web / desktop / Cowork):
    Settings → Connectors → add custom connector  https://fleet-v1.professorzhi.fun/mcp
  Any other MCP agent (DeepSeek Harness, …):
    https://github.com/ProfessorZhi/serveros/releases/download/fleet-latest/serveros-fleet-mcp.zip
    — mcp.json + skills/, see its README

Each client signs in once on the Fleet consent page: account, password, one-time code.
NEXT
