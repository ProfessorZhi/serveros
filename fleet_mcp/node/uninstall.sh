#!/usr/bin/env bash
#
# Remove the Fleet node connector from this machine.
#
# This is the rollback, and it is deliberately the cheap direction. Anything a
# script can install has to be removable by a script, on the machine that
# installed it, without asking anyone -- otherwise the honest answer to "what if
# we want this to stop" is a shrug.
#
# What it removes: the two systemd user units it would have written, and any
# connector or tunnel process they started.
#
# What it leaves, on purpose:
#
#   the binding
#   the node secret
#   the logs
#
# The units are what makes this machine reachable. Without them nothing starts
# at login, the tunnel never opens, and the connector is a program sitting on a
# disk. Deleting the secret as well would make the removal irreversible without
# changing what is reachable, and it would destroy the only record of which
# value to re-provision -- so it takes --purge-secret to mean that.
#
# Run it from the same account that ran install.sh. Another account's units are
# not this script's to touch, and it says so rather than reporting success.
#
# The processes are stopped by calling stop.sh rather than by a second copy of
# the kill logic: two implementations of "which process is ours" is two chances
# to get it wrong, and the wrong answer here ends somebody else's tunnel.
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib_paths.sh
. "$SCRIPT_DIR/lib_paths.sh"

PURGE_SECRET=0
PURGE_BINDING=0
DRY_RUN=0
CONFIG=""

usage() {
    cat <<'EOF'
usage: uninstall.sh [--purge-secret] [--purge-binding] [--dry-run]

  --purge-secret    also remove the node secret. The Fleet host keeps the
                    matching value, so remove it there too, or the two ends
                    disagree silently.
  --purge-binding   also remove the binding file.
  --dry-run         print what would be removed and remove nothing.
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --purge-secret) PURGE_SECRET=1; shift ;;
        --purge-binding) PURGE_BINDING=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) warn "error: unknown argument: $1 (try --help)"; exit 2 ;;
    esac
done

fleet_require_home || exit 3

BINDING_FILE=$(fleet_binding_file) || exit 3
SECRET_PATH=$(fleet_secret_file) || exit 3
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Only these two names, ever. An uninstaller that swept the directory would be
# reaching outside what it installed.
UNITS=("serveros-fleet-node-connector.service" "serveros-fleet-node-tunnel.service")

# ---------------------------------------------------------------------------
# 1. The units: stop, disable, remove, reload.
# ---------------------------------------------------------------------------
if command -v systemctl >/dev/null 2>&1; then
    for unit in "${UNITS[@]}"; do
        if [ "$DRY_RUN" -eq 1 ]; then
            say "dry run: would stop and disable systemd user unit $unit"
        else
            # is-active rather than list-units: list-units renders every unit's
            # Description, and a Description can carry a credential.
            if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
                systemctl --user stop "$unit" && say "stopped $unit" || warn "could not stop $unit"
            fi
            if systemctl --user is-enabled --quiet "$unit" 2>/dev/null; then
                systemctl --user disable "$unit" && say "disabled $unit" || warn "could not disable $unit"
            fi
        fi
    done
else
    say "no systemctl on PATH; no user units to remove"
fi

for unit in "${UNITS[@]}"; do
    target="$UNIT_DIR/$unit"
    if [ -e "$target" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            say "dry run: would remove unit file $target"
        else
            rm -f -- "$target"
            say "removed unit file $target"
        fi
    else
        say "no unit file at $target (already removed, or installed by another account)"
    fi
done

if command -v systemctl >/dev/null 2>&1; then
    if [ "$DRY_RUN" -eq 1 ]; then
        say "dry run: would run systemctl --user daemon-reload"
    else
        systemctl --user daemon-reload 2>/dev/null || warn "systemctl --user daemon-reload failed; the removed units will linger until the next reload"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Anything still running, including a tunnel somebody started by hand. The
#    units are gone, so nothing restarts these.
# ---------------------------------------------------------------------------
if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run: would call stop.sh to stop the connector and the tunnel"
else
    say ""
    if [ -z "$CONFIG" ]; then
        CONFIG="$BINDING_FILE"
    fi
    "$SCRIPT_DIR/stop.sh" --config "$CONFIG"
fi

# ---------------------------------------------------------------------------
# 3. The values, only on request.
# ---------------------------------------------------------------------------
say ""
if [ "$PURGE_SECRET" -eq 1 ]; then
    if [ -e "$SECRET_PATH" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            say "dry run: would remove the node secret $SECRET_PATH"
        else
            rm -f -- "$SECRET_PATH"
            say "removed node secret $SECRET_PATH"
            say "the Fleet host still holds the matching value; remove it there too"
        fi
    else
        say "no node secret at $SECRET_PATH"
    fi
else
    say "kept node secret at $SECRET_PATH (pass --purge-secret to remove it)"
fi

if [ "$PURGE_BINDING" -eq 1 ]; then
    if [ -e "$BINDING_FILE" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            say "dry run: would remove the binding $BINDING_FILE"
        else
            rm -f -- "$BINDING_FILE"
            say "removed binding $BINDING_FILE"
        fi
    else
        say "no binding at $BINDING_FILE"
    fi
else
    say "kept binding at $BINDING_FILE (pass --purge-binding to remove it)"
fi

say ""
say "This machine no longer opens the tunnel or starts the connector."
say "The Fleet host will report the device as having no route, which is the"
say "correct answer rather than an error."
