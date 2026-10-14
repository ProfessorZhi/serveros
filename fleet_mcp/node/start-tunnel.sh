#!/usr/bin/env bash
#
# Keep the reverse tunnel to the Fleet host open.
#
# This machine dials out; the Fleet host listens. That direction is the point: a
# machine that is not reachable from outside should not have to accept an
# inbound connection to become reachable, and opening a port on it to make one
# possible is a worse trade than the convenience it buys.
#
# What this does NOT do, and why it is stated here
# ------------------------------------------------
# The tunnel does not make this machine reachable. It makes ONE port on the
# Fleet host's loopback reach ONE port on this machine's loopback, and that port
# answers nothing except signed requests carrying this device's node secret. A
# stolen secret yields seven named questions and no way to widen them -- see
# fleet/fleet-mcp-contract.yaml.
#
# Trust is not created here
# -------------------------
# Two files have to already exist before this script will dial anything:
#
#   the identity      a private key provisioned for this device, whose public
#                     half is already authorized on the Fleet host.
#   the known_hosts   the Fleet host's public host key, verified out of band.
#
# Neither is generated, and neither is discovered. `StrictHostKeyChecking=no`
# and `accept-new` do not appear in this file at all, and a test asserts that:
# both of them mean "trust whoever answers", which is precisely the property
# that makes a tunnel worth having. A missing file is a message and a non-zero
# exit, before ssh is invoked, so that the failure is "this device has not been
# provisioned" rather than "the Fleet host refused us".
#
# ExitOnForwardFailure is deliberate. Without it ssh stays up while quietly
# running with no forward, which presents to the Fleet host as a tunnel that is
# established and a device that answers nothing -- the "presence is not
# capability" failure this project has already been bitten by once. With it, a
# port already bound is an exit, and the exit is what the retry loop sees.
#
# A bound port is not always a configuration mistake, and calling it one cost
# fifteen hours on the Windows node. When a client goes away without a clean TCP
# teardown, the Fleet host's sshd keeps the remote port bound to the dead
# connection, every retry then exits in two seconds on "remote port forwarding
# failed for listen port N", and the listener belongs to the root monitor of an
# sshd session the host cannot tell apart from a live one. Nothing here can fix
# that. What this file can do is name it, which is what INCIDENT remote-port-stuck
# below is for, together with the command that clears it.
#
# The pid file is not bookkeeping, it is the safety property. "Kill the ssh
# processes that look like this one" is a heuristic over shared option names,
# and this host runs other tunnels: a CloudCLI port forward, and a read-only
# production query broker that is explicitly not this script's to touch. Our own
# child's pid can tell them apart; a filter cannot. The forward spec is written
# beside the pid so that the stopper can confirm the pid is still the process it
# recorded -- see the recycled-pid guard in stop.sh.
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib_paths.sh
. "$SCRIPT_DIR/lib_paths.sh"

SSH_HOST=""
REMOTE_PORT=""
LOCAL_PORT=3402
IDENTITY=""
KNOWN_HOSTS=""
SSH_EXE="ssh"
CONNECT_TIMEOUT=15
RETRY_SECONDS=15
SLOW_RETRY_SECONDS=60
MAX_ATTEMPTS=0

usage() {
    cat <<'EOF'
usage: start-tunnel.sh --ssh-host HOST --remote-port N [options]

Requires the Fleet host and the port it assigned to THIS device. Neither has a
default: a default host would be a guess about which machine holds the Fleet
surface, and the remote port is assigned per device, so a default would be a
collision waiting to happen between two devices.

  --ssh-host HOST        the Fleet host to log in to (user@host if needed)
  --remote-port N        the port assigned to this device on the Fleet host
  --local-port N         the connector's loopback port (default 3402)

  --identity PATH        dedicated private key (default under
                         $XDG_CONFIG_HOME/serveros-fleet-node/ssh/)
  --known-hosts PATH     pinned host keys (default: known_hosts beside the key)
  --ssh-exe PATH         ssh binary to use (default: ssh on PATH)

  --connect-timeout N    ssh ConnectTimeout in seconds (default 15)
  --retry-seconds N      seconds between attempts (default 15)
  --slow-retry-seconds N seconds between attempts after a short-lived exit
                         (default 60; the effective value is the larger of this
                         and 4x --retry-seconds)
  --max-attempts N       stop after N attempts (default 0 = never). This exists
                         so a test can bound the loop; it is not for deployments.
  -h, --help             this text

Before dialling, this checks that the identity, the pinned host keys and the ssh
binary exist. It creates none of them, and it never sets
StrictHostKeyChecking=no or accept-new: a tunnel that trusts whoever answers is
not a tunnel.
EOF
}

die() { printf '%s\n' "error: $*" >&2; exit 3; }

while [ $# -gt 0 ]; do
    case "$1" in
        --ssh-host) SSH_HOST="${2:?--ssh-host needs a value}"; shift 2 ;;
        --remote-port) REMOTE_PORT="${2:?--remote-port needs a value}"; shift 2 ;;
        --local-port) LOCAL_PORT="${2:?}"; shift 2 ;;
        --identity) IDENTITY="${2:?}"; shift 2 ;;
        --known-hosts) KNOWN_HOSTS="${2:?}"; shift 2 ;;
        --ssh-exe) SSH_EXE="${2:?}"; shift 2 ;;
        --connect-timeout) CONNECT_TIMEOUT="${2:?}"; shift 2 ;;
        --retry-seconds) RETRY_SECONDS="${2:?}"; shift 2 ;;
        --slow-retry-seconds) SLOW_RETRY_SECONDS="${2:?}"; shift 2 ;;
        --max-attempts) MAX_ATTEMPTS="${2:?}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf '%s\n' "error: unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

fleet_require_home || exit 3
[ -n "$SSH_HOST" ] || die "--ssh-host is required (see --help)"
[ -n "$REMOTE_PORT" ] || die "--remote-port is required (see --help); the Fleet host assigns a distinct port per device"

# A host is not an argument list. Anything outside this set is refused rather
# than escaped, because quoting rules for a shell, a unit file and ssh are three
# different rule sets and a value that survives all three by luck is a value
# that will not survive the fourth.
case "$SSH_HOST" in
    -*) die "--ssh-host '$SSH_HOST' starts with a dash" ;;
esac
if ! printf '%s' "$SSH_HOST" | command grep -Eq '^[A-Za-z0-9_.@:-]+$'; then
    die "--ssh-host '$SSH_HOST' contains characters outside letters, digits, dot, dash, underscore, colon and @"
fi

check_port() {
    local label="$1" value="$2"
    case "$value" in
        ''|*[!0-9]*) die "$label must be a number, got '$value'" ;;
    esac
    if [ "$value" -lt 1 ] || [ "$value" -gt 65535 ]; then
        die "$label must be between 1 and 65535, got '$value'"
    fi
}
check_port "--remote-port" "$REMOTE_PORT"
check_port "--local-port" "$LOCAL_PORT"

for label in "--connect-timeout:$CONNECT_TIMEOUT" "--retry-seconds:$RETRY_SECONDS" \
             "--slow-retry-seconds:$SLOW_RETRY_SECONDS" "--max-attempts:$MAX_ATTEMPTS"; do
    case "${label#*:}" in
        ''|*[!0-9]*) die "${label%%:*} must be a non-negative number, got '${label#*:}'" ;;
    esac
done

[ -n "$IDENTITY" ] || IDENTITY=$(fleet_ssh_identity_default) || exit 3
[ -n "$KNOWN_HOSTS" ] || KNOWN_HOSTS=$(fleet_known_hosts_default) || exit 3

# ---------------------------------------------------------------------------
# Preflight. Nothing below this line runs ssh, so every one of these is a
# refusal rather than a failed connection.
# ---------------------------------------------------------------------------
if [ ! -f "$IDENTITY" ]; then
    printf '%s\n' \
        "No SSH identity at $IDENTITY." \
        "The tunnel authenticates with a key provisioned for this device whose public" \
        "half is already authorized on the Fleet host. This script will not generate" \
        "one: a key minted here is a trust relationship nobody agreed to, and the" \
        "failure would appear at the far end as a refused key." \
        "Provision the key, authorize it on $SSH_HOST, then run this again." >&2
    exit 3
fi
ident_mode=$(stat -c '%a' "$IDENTITY" 2>/dev/null || printf '?')
case "$ident_mode" in
    600|400) ;;
    *) printf '%s\n' \
        "The SSH identity $IDENTITY has mode $ident_mode; ssh refuses a key that is" \
        "group- or world-readable. Fix it with: chmod 600 $IDENTITY" >&2
       exit 3 ;;
esac

if [ ! -s "$KNOWN_HOSTS" ]; then
    printf '%s\n' \
        "No pinned host keys at $KNOWN_HOSTS." \
        "This tunnel will not trust a host on first sight, and it does not set" \
        "StrictHostKeyChecking=no or accept-new -- a tunnel that accepts whoever" \
        "answers is not a tunnel. Add the Fleet host's public host key, verified out" \
        "of band:" \
        "  ssh-keyscan -H ${SSH_HOST##*@} > $KNOWN_HOSTS   # then verify the fingerprint" \
        "The keyscan output is not authenticated by anything; compare the fingerprint" \
        "with the operator of $SSH_HOST before you trust it, then chmod 600 $KNOWN_HOSTS." >&2
    exit 3
fi

if case "$SSH_EXE" in */*) false ;; *) true ;; esac; then
    if ! command -v "$SSH_EXE" >/dev/null 2>&1; then
        die "no ssh binary called '$SSH_EXE' on PATH"
    fi
else
    [ -x "$SSH_EXE" ] || die "--ssh-exe '$SSH_EXE' is not executable"
fi

STATE_ROOT=$(fleet_state_root) || exit 3
LOG_DIR=$(fleet_log_dir) || exit 3
PID_FILE=$(fleet_tunnel_pid_file) || exit 3
FORWARD_FILE=$(fleet_tunnel_forward_file) || exit 3

if [ -d "$STATE_ROOT" ]; then :; else mkdir -p -- "$STATE_ROOT"; chmod 700 -- "$STATE_ROOT"; fi
if [ -d "$LOG_DIR" ]; then :; else mkdir -p -- "$LOG_DIR"; chmod 700 -- "$LOG_DIR"; fi

LOG_FILE="$LOG_DIR/fleet-node-tunnel.log"
SSH_ERR_FILE="$LOG_DIR/fleet-node-tunnel.ssh.log"

log() {
    local line
    line="$(date '+%Y-%m-%dT%H:%M:%S%z') $*"
    printf '%s\n' "$line" >> "$LOG_FILE"
    printf '%s\n' "$line"
}

# The forward is written as an explicit address pair rather than derived, so
# that the log line names the same thing the Fleet host was configured with.
# Both addresses are loopback on both ends: the Fleet host's sshd binds
# 127.0.0.1 there, not its public interface.
FORWARD="127.0.0.1:${REMOTE_PORT}:127.0.0.1:${LOCAL_PORT}"

# The option list is spelled out rather than assembled from defaults: a tunnel
# whose trust settings come from a user's ssh config is a tunnel whose trust
# settings nobody has read.
SSH_OPTIONS=(
    -N
    -i "$IDENTITY"
    -o "IdentitiesOnly=yes"
    -o "UserKnownHostsFile=$KNOWN_HOSTS"
    -o "StrictHostKeyChecking=yes"
    -o "ExitOnForwardFailure=yes"
    -o "ConnectTimeout=$CONNECT_TIMEOUT"
    -o "ServerAliveInterval=30"
    -o "ServerAliveCountMax=3"
    -o "TCPKeepAlive=yes"
    -o "BatchMode=yes"
)

child=""
cleanup() {
    trap - TERM INT HUP
    if [ -n "$child" ]; then
        kill "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
    fi
    rm -f -- "$PID_FILE" "$FORWARD_FILE"
    exit 0
}
trap cleanup TERM INT HUP

log "tunnel supervisor starting: $FORWARD -> $SSH_HOST (retry ${RETRY_SECONDS}s)"

# Consecutive attempts that failed because the remote port is held. Reset by any
# other outcome, so the count means what it says.
stuck_streak=0
attempt=0

while :; do
    attempt=$((attempt + 1))
    started=$(date +%s)
    log "opening $FORWARD"

    # Each attempt gets a clean stderr file, so what is read back below is this
    # attempt's reason rather than the first one's. The forward spec is written
    # before the process exists and the pid after, so that a stopper can never
    # find a pid it has nothing to confirm it against.
    : > "$SSH_ERR_FILE"
    printf '%s\n' "$FORWARD" > "$FORWARD_FILE"

    ssh_code=0
    "$SSH_EXE" "${SSH_OPTIONS[@]}" -R "$FORWARD" "$SSH_HOST" 2>"$SSH_ERR_FILE" &
    child=$!
    printf '%s\n' "$child" > "$PID_FILE"
    wait "$child" || ssh_code=$?
    child=""
    rm -f -- "$PID_FILE" "$FORWARD_FILE"

    lifetime=$(( $(date +%s) - started ))
    log "tunnel exited after ${lifetime}s with code $ssh_code"

    if [ -s "$SSH_ERR_FILE" ]; then
        while IFS= read -r line; do
            if [ -n "$line" ]; then
                log "ssh: $line"
            fi
        done < "$SSH_ERR_FILE"
    fi

    # The one short-lived exit that is not a configuration problem and does not
    # clear itself. Retrying is still correct -- the port frees the moment
    # somebody ends the stale session -- but a reader of this log has to be told
    # that waiting is not the fix. Reported once and then every thirtieth
    # attempt, so that a fifteen-hour outage is three loud lines rather than
    # nine hundred quiet ones.
    if command grep -qF "remote port forwarding failed for listen port ${REMOTE_PORT}" "$SSH_ERR_FILE" 2>/dev/null; then
        stuck_streak=$((stuck_streak + 1))
        if [ "$stuck_streak" -eq 1 ] || [ $((stuck_streak % 30)) -eq 0 ]; then
            log "INCIDENT remote-port-stuck: 127.0.0.1:${REMOTE_PORT} on $SSH_HOST is held by the sshd session of a tunnel that is gone -- consecutive failed attempts: ${stuck_streak}. Waiting does not fix this."
            log "  clear it on the Fleet host, as the account this tunnel logs in as:"
            log "    ps -u <user> -o pid,etimes,cmd | grep 'sshd: <user>\$'"
            log "    pkill -f '^sshd: <user>\\s*\$'"
            log "  that ends every session-less ssh -N login for that user, including any other tunnel, which reopens on its own schedule."
            log "  the listener belongs to the root monitor of that session, so it cannot be cleared from the account itself -- the session has to end."
        fi
    else
        stuck_streak=0
    fi

    # A tunnel that never stayed up for longer than the retry interval is a
    # configuration problem -- a wrong host, a refused key, a port already bound
    # -- and hammering it produces a log nobody can read. Back off.
    sleep_for="$RETRY_SECONDS"
    if [ "$lifetime" -lt 5 ]; then
        sleep_for=$(( RETRY_SECONDS * 4 ))
        if [ "$SLOW_RETRY_SECONDS" -gt "$sleep_for" ]; then
            sleep_for="$SLOW_RETRY_SECONDS"
        fi
    fi
    log "retrying in ${sleep_for}s"

    if [ "$MAX_ATTEMPTS" -gt 0 ] && [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
        log "not retrying: --max-attempts $MAX_ATTEMPTS reached. This switch exists so a test can bound the loop; a deployment should not pass it."
        exit 0
    fi

    # `sleep` in the background plus `wait` rather than a plain `sleep`, because
    # a plain sleep is not interrupted by a signal: systemd would stop this unit
    # and the process would sit out the remainder of a sixty-second delay before
    # noticing.
    sleep "$sleep_for" &
    sleep_pid=$!
    wait "$sleep_pid" 2>/dev/null || true
done
