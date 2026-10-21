#!/usr/bin/env bash
#
# Where the Fleet node keeps its files on a POSIX device.
#
# This file exists because the installer and the connector have to agree on one
# absolute path. If they disagree, the installer still succeeds: it writes a
# binding at a path it invented itself and prints a fingerprint, and then the
# connector starts, reads nothing there, and exits 3 saying it found no binding.
# That is a failure wearing the shape of a success, and it is the reason the
# path derivation lives in one sourced file instead of in each script.
#
# The binding root here is therefore not a convention this file chose. It
# mirrors `fleet_mcp/local_roots.py#binding_root`, which is the implementation
# that `fleet/fleet-mcp-contract.yaml` declares under `local_roots.binding`
# (`posix: $XDG_CONFIG_HOME/serveros-fleet-mcp`). tests/test_fleet_node_linux_bootstrap.py
# runs the Python resolver and these functions side by side under the same
# environment and fails if the two ever disagree.
#
# The node's own state -- secret, pid file, logs -- is a different kind of
# thing: no Python implements it, because nothing on the Fleet host reads it.
# It is this directory's convention, and it mirrors the Windows layout, where
# %LOCALAPPDATA%\ServerOS\state\fleet-node sits beside
# %LOCALAPPDATA%\ServerOS\state\fleet-mcp. The POSIX sibling of
# $XDG_STATE_HOME/serveros-fleet-mcp is $XDG_STATE_HOME/serveros-fleet-node.
# Note that it is deliberately NOT serveros-fleet-mcp/... : a binding and
# mutable runtime state do not share a root, which is the same distinction
# local_roots.py makes for the same reason.
#
# Logs go under that state root rather than beside it. On Windows they sit in a
# flat %LOCALAPPDATA%\ServerOS\logs next to both roots, which has no POSIX
# equivalent -- XDG's own rule is that logs are state. This is one of the places
# the Linux installation is shaped differently from the Windows one, and it is
# listed as such in README.linux.md.

if [ -n "${_FLEET_NODE_LIB_PATHS:-}" ]; then
    return 0
fi
_FLEET_NODE_LIB_PATHS=1

# Every function below needs a home to default against, and a silent fallback
# to the empty string would produce "/serveros-fleet-mcp" -- a path that looks
# like an absolute path and is nobody's.
fleet_require_home() {
    if [ -z "${HOME:-}" ]; then
        printf '%s\n' "error: HOME is not set; the node's paths are all derived from it" >&2
        return 1
    fi
}

# FLEET_MCP_HOME relocates the binding and state roots together, in the Python
# resolver and here. It is the same switch, spelled the same way, because an
# operator who sets it for a test expects both halves to move.
fleet_binding_root() {
    fleet_require_home || return 1
    if [ -n "${FLEET_MCP_HOME:-}" ]; then
        printf '%s/bindings\n' "${FLEET_MCP_HOME%/}"
        return 0
    fi
    printf '%s/serveros-fleet-mcp\n' "${XDG_CONFIG_HOME:-$HOME/.config}"
}

fleet_binding_file() {
    local root
    root=$(fleet_binding_root) || return 1
    printf '%s/fleet-node.local.yaml\n' "$root"
}

# The device's own state: the node secret, the tunnel pid file, the logs.
fleet_state_root() {
    fleet_require_home || return 1
    printf '%s/serveros-fleet-node\n' "${XDG_STATE_HOME:-$HOME/.local/state}"
}

fleet_secret_file() {
    local root
    root=$(fleet_state_root) || return 1
    printf '%s/secret\n' "$root"
}

fleet_log_dir() {
    local root
    root=$(fleet_state_root) || return 1
    printf '%s/logs\n' "$root"
}

# The pid file is a safety property, not bookkeeping: stop.sh and uninstall.sh
# kill only the pid written here. "Kill the ssh processes that look like ours"
# is a heuristic over shared option names, and this host runs other tunnels.
fleet_tunnel_pid_file() {
    local root
    root=$(fleet_state_root) || return 1
    printf '%s/tunnel.pid\n' "$root"
}

# What that pid was asked to forward. Written beside the pid because the guard
# in stop.sh needs something to confirm the pid against: a pid alone is not
# ours, it is a number that was ours once. See the recycled-pid guard there.
fleet_tunnel_forward_file() {
    local root
    root=$(fleet_state_root) || return 1
    printf '%s/tunnel.forward\n' "$root"
}

# A dedicated identity, under this deployment's own config directory rather than
# in the shared ~/.ssh. Two reasons: ~/.ssh/config may already carry a
# Host stanza for the Fleet host that would apply to this tunnel and change what
# it connects to, and a key that exists only for this purpose is a key that can
# be revoked without asking who else was using it. FLEET_NODE_SSH_DIR names the
# directory directly, for an operator who would rather keep it in ~/.ssh.
fleet_ssh_dir_default() {
    fleet_require_home || return 1
    if [ -n "${FLEET_NODE_SSH_DIR:-}" ]; then
        printf '%s\n' "${FLEET_NODE_SSH_DIR%/}"
        return 0
    fi
    printf '%s/serveros-fleet-node/ssh\n' "${XDG_CONFIG_HOME:-$HOME/.config}"
}

# The identity this tunnel authenticates with, and the host keys it will accept.
# Both are named here so that install.sh can say whether they exist yet and
# start-tunnel.sh can refuse without them.
fleet_ssh_identity_default() {
    local dir
    dir=$(fleet_ssh_dir_default) || return 1
    printf '%s/id_ed25519\n' "$dir"
}

fleet_known_hosts_default() {
    local dir
    dir=$(fleet_ssh_dir_default) || return 1
    printf '%s/known_hosts\n' "$dir"
}

# `~/.serveros` is closed to five named files by fleet/node-root-contract.yaml,
# and runtime/paths.py#admit enforces it. Nothing this directory writes belongs
# there, so a target under it is refused before anything is created. The point
# of checking is the one the contract already records: a printed remedy that the
# product refuses is worse than a missing file.
FLEET_CLOSED_ROOT_ALLOWED="node.yaml targets.yaml controller.yaml business-calendar.yaml service-bindings.yaml"

fleet_refuse_if_closed_root() {
    local target="$1" closed rel
    fleet_require_home || return 1
    closed="$HOME/.serveros"
    case "$target" in
        "$closed"|"$closed"/*) ;;
        *) return 0 ;;
    esac
    rel="${target#"$closed"/}"
    case " $FLEET_CLOSED_ROOT_ALLOWED " in
        *" $rel "*) return 0 ;;
    esac
    printf '%s\n' \
        "error: refusing to write $target -- $closed is closed by" \
        "fleet/node-root-contract.yaml to: $FLEET_CLOSED_ROOT_ALLOWED" \
        "Point XDG_CONFIG_HOME / XDG_STATE_HOME somewhere else, or unset them." >&2
    return 1
}

# A binding inside a checkout is a binding that gets committed. local_roots.py
# already refuses to *read* a binding out of the working directory; this refuses
# to write one into a repository in the first place. `.git` is looked for as a
# plain directory entry rather than through `git rev-parse`, so the answer does
# not depend on git being installed or on the caller's git configuration.
fleet_refuse_if_in_repository() {
    local target="$1" dir
    dir=$(dirname -- "$target")
    while :; do
        if [ -e "$dir/.git" ]; then
            printf '%s\n' \
                "error: refusing to write $target -- it is inside the repository at $dir." \
                "A binding is this device's identity as its runtime reads it; a copy in a" \
                "checkout is a copy that gets pushed. Move the root instead." >&2
            return 1
        fi
        [ "$dir" = "/" ] && break
        dir=$(dirname -- "$dir")
    done
    return 0
}
