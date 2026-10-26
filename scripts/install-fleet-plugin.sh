#!/usr/bin/env bash
# One-click install of the ServerOS Fleet plugin into Claude Code, from this checkout.
# See scripts/fleet_plugin.py for what it does; extra arguments are passed through
# (e.g. --keep-legacy, --next-mcp-url URL).
set -euo pipefail
exec python3 "$(dirname "$0")/fleet_plugin.py" install "$@"
