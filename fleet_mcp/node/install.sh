#!/usr/bin/env bash
#
# Install the Fleet node connector on a POSIX device, as the account that will
# run it. No sudo, no system-wide file, no privileged port, no service manager
# required.
#
# The Windows installer this mirrors is install.ps1, and what it writes are the
# same three kinds of thing in the same three roles:
#
#   binding   $XDG_CONFIG_HOME/serveros-fleet-mcp/fleet-node.local.yaml
#   secret    $XDG_STATE_HOME/serveros-fleet-node/secret
#   logs      $XDG_STATE_HOME/serveros-fleet-node/logs/
#
# plus, only with --systemd, two user unit files under
# $XDG_CONFIG_HOME/systemd/user/. Every path comes from lib_paths.sh, which is
# checked against the Python resolver the connector itself uses.
#
# What this script does NOT do, and why each one is stated rather than assumed
# ---------------------------------------------------------------------------
#   * It does not generate an SSH key, and it does not add a host key to
#     known_hosts. The tunnel authenticates with a key provisioned for this
#     device whose public half is already authorized on the Fleet host. Minting
#     one here would create a trust relationship nobody agreed to, and the
#     failure would surface at the far end as a refused key -- which reads as
#     "the Fleet host is broken" rather than "this script invented a
#     credential". Same for the host key: this script will not trust a host on
#     first sight. --preflight says what is missing instead.
#   * It does not create or copy the CloudCLI credential. The binding names it
#     by anchor, so that there is exactly one of it to revoke.
#   * It does not call systemctl. It writes unit files and prints the commands
#     that enable them, for a person to run. Enabling a service is a change to
#     the account, and `loginctl enable-linger` is a larger version of the same
#     act -- it is printed with that warning attached and never run.
#   * It does not add anything to a crontab. A service that survives logout is a
#     decision about this account, not a side effect of installing a connector.
#   * It does not print the secret, does not accept one as an argument, and puts
#     none in a unit file. What it prints is a fingerprint.
#   * It does not overwrite an existing binding or secret. Overwriting a binding
#     would silently re-point this device at a different Fleet host, and
#     overwriting a secret would invalidate the value the Fleet host holds while
#     leaving no record of which value to re-provision.
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
# shellcheck source=lib_paths.sh
. "$SCRIPT_DIR/lib_paths.sh"

DEVICE_REF=""
CLOUDCLI_ENDPOINT=""
CLOUDCLI_CREDENTIAL=""
LOCAL_PORT=3402
REMOTE_PORT=""
SSH_HOST=""
IDENTITY=""
KNOWN_HOSTS=""
SECRET_ANCHOR_OVERRIDE=""
USE_SYSTEMD=0
PREFLIGHT_ONLY=0
DRY_RUN=0

usage() {
    cat <<'EOF'
usage: install.sh --device-ref <device:id> --cloudcli-endpoint <url> [options]

Required. There is no default for either, and that is deliberate: a default
device ref would enroll this machine under an identity nobody chose, and a
default endpoint would point the connector at whatever happened to be listening.

  --device-ref REF           this machine's typed fleet ref, e.g. device:llm-lab-dell.
                             The ref is authored in fleet/registry.yaml on the
                             Fleet host. An id that is not in that registry is a
                             binding this connector will serve and the Fleet host
                             will not route. Enrollment is a human decision, and
                             this script does not make it.
  --cloudcli-endpoint URL    the CloudCLI MCP endpoint on THIS machine's loopback,
                             e.g. http://127.0.0.1:3001/mcp. Must be a loopback
                             address: this process is reached from off-host only
                             through a tunnel this machine opens, so an endpoint
                             on a routable address would make the tunnel pointless.

Optional:

  --cloudcli-credential PATH  CloudCLI PAT file (default ~/.cloudcli/mcp-pat-claude-code.txt)
  --local-port N              loopback port the connector serves on (default 3402)
  --secret-anchor PATH        where the secret is written
                              (default $XDG_STATE_HOME/serveros-fleet-node/secret)
  --ssh-host HOST             the Fleet host this tunnel logs in to
  --remote-port N             the port assigned to THIS device on the Fleet host.
                              Distinct per device; required by --systemd.
  --identity PATH             the dedicated private key for the tunnel
                              (default $XDG_CONFIG_HOME/serveros-fleet-node/ssh/id_ed25519)
  --known-hosts PATH          the pinned host keys (default: known_hosts beside the key)
  --systemd                   also write systemd user unit files
  --preflight                 check only; write nothing, exit non-zero if a check fails
  --dry-run                   print what would be written; write nothing
  -h, --help                  this text

--preflight reports four things this script will not fix for you: a missing
CloudCLI credential, a missing SSH identity, a missing known_hosts, and a taken
local port. A missing identity or known_hosts is fatal for --systemd, because
the tunnel unit would start and fail forever.
EOF
}

die() { printf '%s\n' "error: $*" >&2; exit 2; }
say() { printf '%s\n' "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --device-ref) DEVICE_REF="${2:?--device-ref needs a value}"; shift 2 ;;
        --cloudcli-endpoint) CLOUDCLI_ENDPOINT="${2:?}"; shift 2 ;;
        --cloudcli-credential) CLOUDCLI_CREDENTIAL="${2:?}"; shift 2 ;;
        --local-port) LOCAL_PORT="${2:?}"; shift 2 ;;
        --remote-port) REMOTE_PORT="${2:?}"; shift 2 ;;
        --ssh-host) SSH_HOST="${2:?}"; shift 2 ;;
        --identity) IDENTITY="${2:?}"; shift 2 ;;
        --known-hosts) KNOWN_HOSTS="${2:?}"; shift 2 ;;
        --secret-anchor) SECRET_ANCHOR_OVERRIDE="${2:?}"; shift 2 ;;
        --systemd) USE_SYSTEMD=1; shift ;;
        --preflight) PREFLIGHT_ONLY=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

fleet_require_home || exit 3

# ---------------------------------------------------------------------------
# Resolve the paths once, from lib_paths.sh, so that every branch below and the
# connector itself are talking about the same files.
# ---------------------------------------------------------------------------
BINDING_FILE=$(fleet_binding_file) || exit 3
STATE_ROOT=$(fleet_state_root) || exit 3
SECRET_PATH="${SECRET_ANCHOR_OVERRIDE:-$(fleet_secret_file)}"
LOG_DIR=$(fleet_log_dir) || exit 3
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TUNNEL_PID_FILE=$(fleet_tunnel_pid_file) || exit 3
DEFAULT_IDENTITY=$(fleet_ssh_identity_default) || exit 3
DEFAULT_KNOWN_HOSTS=$(fleet_known_hosts_default) || exit 3
[ -n "$IDENTITY" ] || IDENTITY="$DEFAULT_IDENTITY"
[ -n "$KNOWN_HOSTS" ] || KNOWN_HOSTS="$DEFAULT_KNOWN_HOSTS"
[ -n "$CLOUDCLI_CREDENTIAL" ] || CLOUDCLI_CREDENTIAL="$HOME/.cloudcli/mcp-pat-claude-code.txt"

for target in "$BINDING_FILE" "$SECRET_PATH"; do
    fleet_refuse_if_closed_root "$target" || exit 3
    fleet_refuse_if_in_repository "$target" || exit 3
done

# ---------------------------------------------------------------------------
# Checks. Each prints one line and records whether it failed, because the whole
# point of --preflight is to answer "what is missing" in one pass rather than
# one refusal at a time.
# ---------------------------------------------------------------------------
FAILED=0
ok() { say "  [ ok ] $*"; }
warn() { say "  [warn] $*"; }
bad() { say "  [ !! ] $*"; FAILED=$((FAILED + 1)); }

[ -n "$DEVICE_REF" ] || die "--device-ref is required (see --help); this script will not guess an identity"
[ -n "$CLOUDCLI_ENDPOINT" ] || die "--cloudcli-endpoint is required (see --help); this script will not guess an endpoint"

# The pattern is fleet/transport-contract.yaml#identity.ref_pattern, not a rule
# invented here. A hostname would give two machines the same identity the moment
# one was renamed, which is why the ref is a typed id and not a locator.
if printf '%s' "$DEVICE_REF" | command grep -Eq '^device:[a-z0-9][a-z0-9-]{1,62}$'; then
    ok "device ref is well formed: $DEVICE_REF"
else
    die "--device-ref '$DEVICE_REF' is not of the form device:<id> (lowercase letters, digits and dashes; see fleet/transport-contract.yaml#identity.ref_pattern)"
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
check_port "--local-port" "$LOCAL_PORT"
[ -z "$REMOTE_PORT" ] || check_port "--remote-port" "$REMOTE_PORT"

# The endpoint has to be loopback. This is the same statement the binding makes
# by writing 127.0.0.1 in its bind field: the connector is reached from off-host
# only through a tunnel this machine owns, and an endpoint on a routable address
# would put a CloudCLI credential one hop from the network.
case "$CLOUDCLI_ENDPOINT" in
    http://127.0.0.1:*|http://127.0.0.1/*|http://localhost:*|http://localhost/*|http://\[::1\]:*|http://\[::1\]/*)
        ok "CloudCLI endpoint is on loopback: $CLOUDCLI_ENDPOINT" ;;
    *)
        bad "CloudCLI endpoint is not a loopback address: $CLOUDCLI_ENDPOINT" ;;
esac

if [ -f "$CLOUDCLI_CREDENTIAL" ]; then
    ok "CloudCLI credential anchor exists: $CLOUDCLI_CREDENTIAL"
else
    warn "no CloudCLI credential at $CLOUDCLI_CREDENTIAL -- the connector will refuse to start and say so in its log. This script will not create it."
fi

if [ -f "$REPO_ROOT/fleet_mcp/node/connector.py" ]; then
    ok "connector module present: $REPO_ROOT/fleet_mcp/node/connector.py"
else
    bad "no connector module at $REPO_ROOT/fleet_mcp/node/connector.py (is this a full checkout?)"
fi

PYTHON_BIN="${SERVEROS_FLEET_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
    PYTHON_BIN=$(command -v python3 || true)
fi
if [ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ]; then
    ok "interpreter: $PYTHON_BIN"
    if PYTHONPATH="$REPO_ROOT" PYTHONIOENCODING=utf-8 "$PYTHON_BIN" -c 'import fleet_mcp.node.connector' >/dev/null 2>&1; then
        ok "fleet_mcp.node.connector imports with this interpreter"
    else
        warn "$PYTHON_BIN cannot import fleet_mcp.node.connector (PyYAML is the usual cause). Install the dependencies for this interpreter, or set SERVEROS_FLEET_PYTHON."
    fi
else
    bad "no python3 on PATH and SERVEROS_FLEET_PYTHON is not set"
fi

# A taken local port is worth saying out loud but is not fatal: the usual cause
# is that this connector is already installed and running, which is exactly the
# state a re-run of this script is normally in.
if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | command grep -qE "[[:space:]]127\.0\.0\.1:${LOCAL_PORT}[[:space:]]"; then
        warn "something is already listening on 127.0.0.1:${LOCAL_PORT} -- if it is this connector, that is expected"
    else
        ok "127.0.0.1:${LOCAL_PORT} is free"
    fi
fi

# The SSH material. Fatal only when --systemd would install a tunnel unit that
# could never come up; otherwise it is reported, because the operator needs to
# know it before they wonder why nothing connects.
SSH_FATAL=0
[ "$USE_SYSTEMD" -eq 1 ] && SSH_FATAL=1

if [ -f "$IDENTITY" ]; then
    ident_mode=$(stat -c '%a' "$IDENTITY" 2>/dev/null || printf '?')
    case "$ident_mode" in
        600|400) ok "SSH identity exists: $IDENTITY (mode $ident_mode)" ;;
        *) warn "SSH identity $IDENTITY has mode $ident_mode; ssh refuses a key that is group- or world-readable" ;;
    esac
else
    if [ "$SSH_FATAL" -eq 1 ]; then
        bad "no SSH identity at $IDENTITY. Provision a key for this device and authorize its public half on the Fleet host; this script will not generate one."
    else
        warn "no SSH identity at $IDENTITY -- the tunnel refuses to start until one is provisioned. This script will not generate one."
    fi
fi

if [ -s "$KNOWN_HOSTS" ]; then
    ok "pinned known_hosts exists: $KNOWN_HOSTS"
else
    if [ "$SSH_FATAL" -eq 1 ]; then
        bad "no pinned host keys at $KNOWN_HOSTS. Add the Fleet host's public host key, verified out of band; this script will not trust a host on first sight."
    else
        warn "no pinned host keys at $KNOWN_HOSTS -- the tunnel needs the Fleet host's key here, verified out of band."
    fi
fi

[ -z "$SSH_HOST" ] || ok "Fleet host: $SSH_HOST"
if [ "$USE_SYSTEMD" -eq 1 ]; then
    if [ -z "$SSH_HOST" ]; then
        bad "--systemd needs --ssh-host: the tunnel unit cannot name a host it was not given"
    fi
    if [ -z "$REMOTE_PORT" ]; then
        bad "--systemd needs --remote-port: the Fleet host assigns a distinct port per device, and no default would be right for this one"
    fi
    case "$SSH_HOST" in
        *[[:space:]]*|*'"'*|*'&'*|*'|'*) bad "--ssh-host '$SSH_HOST' contains characters that cannot be safely written into a unit file" ;;
    esac
fi

say ""
if [ "$FAILED" -gt 0 ]; then
    say "$FAILED check(s) failed."
else
    say "All checks passed."
fi

if [ "$PREFLIGHT_ONLY" -eq 1 ]; then
    [ "$FAILED" -eq 0 ] || exit 1
    exit 0
fi
[ "$FAILED" -eq 0 ] || die "refusing to install while a check above is failing; fix it or run with --preflight to see the whole list"

# ---------------------------------------------------------------------------
# The secret. 32 random bytes, hex, no trailing newline -- byte for byte what
# install.ps1 writes, because the two ends' fingerprints are computed over the
# same bytes and a stray newline would make the same secret report two values.
# ---------------------------------------------------------------------------
new_secret=0
if [ -e "$SECRET_PATH" ]; then
    say "kept existing node secret at $SECRET_PATH (not overwritten)"
else
    new_secret=1
fi

if [ "$DRY_RUN" -eq 1 ]; then
    say "dry run: would write the binding, the secret (32 random bytes) and the logs directory"
    [ "$USE_SYSTEMD" -eq 0 ] || say "dry run: would write unit files under $UNIT_DIR"
    exit 0
fi

# A directory that already exists is left alone if it is ours and is only
# reported if it is loose: `chmod` on a directory somebody else chose, possibly
# one we cannot chmod, is not this script's call.
ensure_dir_700() {
    local dir="$1"
    if [ -d "$dir" ]; then
        local mode
        mode=$(stat -c '%a' "$dir" 2>/dev/null || printf '?')
        case "$mode" in
            700) ;;
            *) warn "$dir exists with mode $mode; the secret it holds is as readable as that mode allows (0700 is what this expects)" ;;
        esac
        return 0
    fi
    mkdir -p -- "$dir"
    chmod 700 -- "$dir"
}

ensure_dir_700 "$STATE_ROOT"
ensure_dir_700 "$LOG_DIR"
ensure_dir_700 "$(dirname -- "$SECRET_PATH")"

if [ "$new_secret" -eq 1 ]; then
    # `openssl rand -hex 32` ends in a newline, which is stripped rather than
    # written: the file has to be 64 bytes for the fingerprint to match the one
    # the Windows installer produces for the same value.
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32 | tr -d '\n' > "$SECRET_PATH"
    else
        # 32 random *bytes*, read as bytes: `head -c 32 /dev/urandom | base64`
        # would be 32 random characters, which is a different and weaker claim.
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$SECRET_PATH"
    fi
    chmod 600 -- "$SECRET_PATH"
fi

# The secret is read below only to check its shape and to hash it. It is never
# printed, never passed as an argument, and never written anywhere but its file.
SECRET_LEN=$(wc -c < "$SECRET_PATH" | tr -d ' ')
if [ "$SECRET_LEN" -ne 64 ] || ! command grep -Eq '^[0-9a-f]{64}$' "$SECRET_PATH"; then
    printf '%s\n' "error: $SECRET_PATH is not 64 hex characters (it is $SECRET_LEN bytes)." \
        "If something else wrote it, remove it and re-run; re-running will not overwrite it." >&2
    exit 3
fi
FINGERPRINT=$(sha256sum "$SECRET_PATH" | cut -c1-12)

# ---------------------------------------------------------------------------
# The binding. Written only if absent -- see the header.
# ---------------------------------------------------------------------------
if [ -e "$BINDING_FILE" ]; then
    say "kept existing binding at $BINDING_FILE (not overwritten)"
else
    ensure_dir_700 "$(dirname -- "$BINDING_FILE")"
    cat > "$BINDING_FILE" <<EOF
# The Fleet node connector's binding for this device.
#
# Written by fleet_mcp/node/install.sh and read by fleet_mcp.node.connector.
# Not in a repository, and not derived from this machine's hostname: two
# machines that present identical ability scans are still two machines, and a
# hostname is a route rather than an identity.
#
# The secret is named here as an anchor and read at its point of use, so that
# there is exactly one copy of it to revoke.
schema: serveros/fleet-node.local/v1
device_ref: $DEVICE_REF
secret_anchor: $SECRET_PATH
cloudcli_endpoint: $CLOUDCLI_ENDPOINT
cloudcli_credential_anchor: $CLOUDCLI_CREDENTIAL
bind: 127.0.0.1
port: $LOCAL_PORT
connect_timeout: 5.0
read_timeout: 45.0
# Empty means every send is refused, including from the Fleet owner. The Fleet
# surface decides whether a caller may drive this device; this file decides
# whether anything may drive the sessions on it. Opening it is an edit here, on
# this machine, naming the sessions it applies to.
allow_send_to: []
max_body_bytes: 1000000
notes: installed $(date '+%Y-%m-%dT%H:%M:%S%z') by fleet_mcp/node/install.sh
EOF
    chmod 600 -- "$BINDING_FILE"
    say "wrote binding $BINDING_FILE"
fi

# ---------------------------------------------------------------------------
# Unit files, only on request. They name the binding and the key by absolute
# path and carry no secret; no unit Description carries one either, because a
# Description is copied into the unit's scope and from there into anything that
# lists units, which is how a credential ends up in a session transcript.
# ---------------------------------------------------------------------------
if [ "$USE_SYSTEMD" -eq 1 ]; then
    # Not ensure_dir_700: ~/.config/systemd/user is conventionally 0755, holds
    # nothing secret, and systemd itself reads it.
    mkdir -p -- "$UNIT_DIR"
    render_unit() {
        local template="$1" target="$2"
        if [ -e "$target" ]; then
            say "kept existing unit $target (not overwritten)"
            return 0
        fi
        command sed \
            -e "s|@REPO_ROOT@|$REPO_ROOT|g" \
            -e "s|@BINDING@|$BINDING_FILE|g" \
            -e "s|@SECRET_ANCHOR@|$SECRET_PATH|g" \
            -e "s|@LOG_DIR@|$LOG_DIR|g" \
            -e "s|@STATE_DIR@|$STATE_ROOT|g" \
            -e "s|@SSH_HOST@|$SSH_HOST|g" \
            -e "s|@REMOTE_PORT@|$REMOTE_PORT|g" \
            -e "s|@LOCAL_PORT@|$LOCAL_PORT|g" \
            -e "s|@IDENTITY@|$IDENTITY|g" \
            -e "s|@KNOWN_HOSTS@|$KNOWN_HOSTS|g" \
            "$template" > "$target"
        chmod 600 -- "$target"
        say "wrote unit $target"
    }
    render_unit "$SCRIPT_DIR/systemd/user/serveros-fleet-node-connector.service.in" \
                "$UNIT_DIR/serveros-fleet-node-connector.service"
    render_unit "$SCRIPT_DIR/systemd/user/serveros-fleet-node-tunnel.service.in" \
                "$UNIT_DIR/serveros-fleet-node-tunnel.service"
fi

say ""
say "node secret fingerprint: $FINGERPRINT"
say "  That is the first 12 hex characters of the secret's SHA-256. Compare it with"
say "  the value the Fleet host holds for $DEVICE_REF: that comparison is the only"
say "  way to confirm the two ends agree, because neither end prints the secret."
say ""
say "written:"
say "  binding        $BINDING_FILE"
say "  secret         $SECRET_PATH (mode 600)"
say "  logs           $LOG_DIR"
say "  tunnel pid     $TUNNEL_PID_FILE (written while the tunnel runs)"
[ "$USE_SYSTEMD" -eq 0 ] || say "  unit files     $UNIT_DIR"
say ""
say "not done by this script, because each needs a person:"
say "  * $DEVICE_REF is not enrolled on the Fleet host unless somebody has added it"
say "    to fleet/registry.yaml. Until then this connector answers requests the Fleet"
say "    host will not route. Enrollment is a human decision."
say "  * the tunnel needs an SSH key authorized on the Fleet host, and the Fleet"
say "    host's public key in $KNOWN_HOSTS."
say ""
if [ "$USE_SYSTEMD" -eq 1 ]; then
    say "to run it under systemd, as this account:"
    say "  systemctl --user daemon-reload"
    say "  systemctl --user enable --now serveros-fleet-node-connector.service"
    say "  systemctl --user enable --now serveros-fleet-node-tunnel.service"
    say ""
    say "  Those units stop at logout unless this account may keep user services alive"
    say "  across sessions. That is a change to the account, so this script does not make"
    say "  it. Run it yourself, and only if you mean this device to stay reachable while"
    say "  nobody is logged in:"
    say "    loginctl enable-linger $USER"
else
    say "to run it now, without systemd:"
    say "  $SCRIPT_DIR/start-tunnel.sh --ssh-host <host> --remote-port <assigned-port> &"
    say "  $SCRIPT_DIR/start-connector.sh"
    say ""
    say "  Neither is installed as a persistent service, and nothing was added to a"
    say "  crontab. Keeping it running across logout is an operator decision."
fi
say ""
say "to stop it:   $SCRIPT_DIR/stop.sh"
say "to remove it: $SCRIPT_DIR/uninstall.sh    (leaves the binding and the secret)"
