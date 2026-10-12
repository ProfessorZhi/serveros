#!/usr/bin/env bash
#
# Stop the Fleet node connector and its tunnel, without removing anything.
#
# This is the verb between "running" and "uninstalled". The Windows side folds
# it into uninstall.ps1, which is fine there because the tasks are the only
# thing that starts the processes; here the same is true of the units, but
# `systemctl --user stop` and "remove the unit file" are different acts with
# different reversibility, and an operator who wants the device unreachable for
# an afternoon should not have to unregister anything to get it.
#
# Order matters: the units are stopped FIRST, because a unit with Restart=always
# would otherwise resurrect what the second half of this script kills.
#
# How a process is identified, and why it is not a filter
# ------------------------------------------------------
#   the tunnel       by the pid start-tunnel.sh wrote down, confirmed against
#                    the forward spec recorded beside it. A pid is a number that
#                    was ours once: after a reboot or enough churn, that number
#                    belongs to somebody else's process, and killing it would be
#                    this script reaching outside its own installation. The
#                    recorded forward spec -- which carries the port the Fleet
#                    host assigned to this device -- is the confirmation.
#   the connector    by scanning /proc for a process this account owns whose
#                    command line carries BOTH the connector module and this
#                    binding path. Either one alone is too wide: the module name
#                    matches a second checkout, and the binding path alone
#                    matches a `grep`. A broad "kill python processes that look
#                    like ours" is exactly what uninstall.ps1's comment records
#                    nearly ending two unrelated tunnels.
#
# This host runs other tunnels -- a CloudCLI port forward and a read-only
# production query broker -- so a heuristic over shared ssh option names is not
# an acceptable way to find ours.
#
# systemctl is used through `is-active` and `stop` only. `systemctl --user
# list-units` is deliberately absent from this file and every other script here:
# it renders every unit's Description, a scope unit's Description can carry a
# Bearer token, and that output lands in whatever transcript is watching.
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib_paths.sh
. "$SCRIPT_DIR/lib_paths.sh"

CONFIG=""
DRY_RUN=0
UNITS=("serveros-fleet-node-connector.service" "serveros-fleet-node-tunnel.service")

usage() {
    cat <<'EOF'
usage: stop.sh [--config PATH] [--dry-run]

  --config PATH   the binding whose connector to stop (default: the one
                  install.sh writes). Only a process whose command line names
                  this exact path is stopped.
  --dry-run       print what would be stopped and stop nothing.
EOF
}

say() { printf '%s\n' "$*"; }
warn() { printf '%s\n' "$*" >&2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG="${2:?--config needs a value}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) warn "error: unknown argument: $1 (try --help)"; exit 2 ;;
    esac
done

fleet_require_home || exit 3

if [ -z "$CONFIG" ]; then
    CONFIG=$(fleet_binding_file) || exit 3
fi

PID_FILE=$(fleet_tunnel_pid_file) || exit 3
FORWARD_FILE=$(fleet_tunnel_forward_file) || exit 3

# ---------------------------------------------------------------------------
# 1. The systemd user units, if this account has them and the manager knows them.
# ---------------------------------------------------------------------------
unit_is_active() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user is-active --quiet "$1" 2>/dev/null
}

if command -v systemctl >/dev/null 2>&1; then
    for unit in "${UNITS[@]}"; do
        if unit_is_active "$unit"; then
            if [ "$DRY_RUN" -eq 1 ]; then
                say "dry run: would stop systemd user unit $unit"
            elif systemctl --user stop "$unit"; then
                say "stopped systemd user unit $unit"
            else
                warn "systemctl could not stop $unit; continuing, because the processes"
                warn "below are stopped by pid rather than by unit"
            fi
        else
            say "systemd user unit $unit is not active (not installed, or not running)"
        fi
    done
else
    say "no systemctl on PATH; nothing was started by a unit"
fi

# ---------------------------------------------------------------------------
# 2. The tunnel, by the recorded pid.
# ---------------------------------------------------------------------------
cmdline_of() {
    # A missing or unreadable /proc entry yields the empty string rather than an
    # error, so that "the process is gone" and "the process is not ours to look
    # at" both end up handled by the same guard instead of by a stack trace.
    tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null || true
}

stop_tunnel() {
    if [ ! -f "$PID_FILE" ]; then
        say "no tunnel pid on record (the tunnel was already down, or was never started here)"
        return 0
    fi
    local pid forward
    pid=$(head -n 1 "$PID_FILE" 2>/dev/null | tr -d '[:space:]' || true)
    case "$pid" in
        ''|*[!0-9]*)
            warn "recorded tunnel pid file $PID_FILE does not hold a pid; removing it"
            [ "$DRY_RUN" -eq 1 ] || rm -f -- "$PID_FILE"
            return 0 ;;
    esac

    if [ ! -f "$FORWARD_FILE" ]; then
        warn "pid $pid is on record but the forward spec it belongs to is not."
        warn "Not killing it: without that spec there is nothing to confirm the pid"
        warn "against, and a pid alone is a number that was ours once."
        return 0
    fi
    forward=$(head -n 1 "$FORWARD_FILE" 2>/dev/null | tr -d '[:space:]' || true)
    if [ -z "$forward" ]; then
        warn "the recorded forward spec at $FORWARD_FILE is empty; not killing pid $pid"
        return 0
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        say "recorded tunnel pid $pid is not running"
        [ "$DRY_RUN" -eq 1 ] || rm -f -- "$PID_FILE" "$FORWARD_FILE"
        return 0
    fi

    local cmdline
    cmdline=$(cmdline_of "$pid")
    case "$cmdline" in
        *"$forward"*) ;;
        *)
            warn "pid $pid is alive but its command line does not carry the recorded"
            warn "forward ($forward). That is a recycled pid, not this tunnel, and it is"
            warn "not this script's to kill. Removing the stale records instead."
            [ "$DRY_RUN" -eq 1 ] || rm -f -- "$PID_FILE" "$FORWARD_FILE"
            return 0 ;;
    esac

    if [ "$DRY_RUN" -eq 1 ]; then
        say "dry run: would stop tunnel pid $pid (forward $forward)"
        return 0
    fi

    say "stopping tunnel pid $pid (forward $forward)"
    kill "$pid" 2>/dev/null || true
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
        warn "pid $pid did not exit on SIGTERM; sending SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f -- "$PID_FILE" "$FORWARD_FILE"
    return 0
}
stop_tunnel

# ---------------------------------------------------------------------------
# 3. The connector, by module and binding together.
# ---------------------------------------------------------------------------
stop_connectors() {
    local self_uid dir pid cmdline found
    self_uid=$(id -u)
    found=0
    for dir in /proc/[0-9]*; do
        pid="${dir#/proc/}"
        [ -r "$dir/cmdline" ] || continue
        [ "$(stat -c '%u' "$dir" 2>/dev/null || printf 'x')" = "$self_uid" ] || continue
        cmdline=$(cmdline_of "$pid")
        [ -n "$cmdline" ] || continue
        case "$cmdline" in
            *fleet_mcp.node.connector*) ;;
            *) continue ;;
        esac
        case "$cmdline" in
            *"$CONFIG"*) ;;
            *) continue ;;
        esac
        found=1
        if [ "$DRY_RUN" -eq 1 ]; then
            say "dry run: would stop connector pid $pid"
            continue
        fi
        say "stopping connector pid $pid"
        kill "$pid" 2>/dev/null || true
    done
    if [ "$found" -eq 0 ]; then
        say "no connector process is running with binding $CONFIG"
    fi
    return 0
}
stop_connectors

say ""
if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run: nothing was stopped."
else
    say "The connector and the tunnel are stopped. The binding, the secret and the"
    say "logs are untouched. The Fleet host will report this device as having no"
    say "route, which is the correct answer rather than an error."
fi
