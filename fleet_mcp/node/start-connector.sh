#!/usr/bin/env bash
#
# Start the Fleet node connector on this machine.
#
# This process serves a closed, signed API on 127.0.0.1 only. It does not listen
# on a routable address, and the Fleet host reaches it through a reverse tunnel
# this machine opens outward (start-tunnel.sh). The connector and the tunnel are
# two processes on purpose: the connector cannot be made to dial out, and the
# tunnel cannot be made to answer a request.
#
# The three things install.ps1's sibling makes explicit, and why
# --------------------------------------------------------------
# PATH. Under systemd, and under any launcher that does not read a login shell's
# profile, python is not on PATH by accident. The interpreter is resolved up
# front, and a missing one is a message with an exit code rather than a unit
# that started and did nothing.
#
# The binding. It is named on the command line, defaulting to the one path the
# Python resolver would find anyway. "Which device is this" should be a fact
# about an argument rather than about a working directory.
#
# The working directory. The connector is started FROM the repository root
# rather than from wherever the caller happened to be. `python -m` puts the
# current directory first on sys.path, so a launcher started inside a different
# checkout would import that checkout's `fleet_mcp` -- a second copy of the code
# running while the log says the first one's revision. Pinning the directory and
# PYTHONPATH to the same tree removes the question.
#
# This script does not return until the connector exits, so whoever started it
# -- systemd, most of the time -- has a lifetime equal to the process's and a
# real exit code. A launcher that fires a process and returns turns "the
# connector is down" into "the unit succeeded a week ago".
set -euo pipefail

umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd -P)
# shellcheck source=lib_paths.sh
. "$SCRIPT_DIR/lib_paths.sh"

PYTHON_HINT=""
CONFIG=""
LOG_DIR=""

usage() {
    cat <<'EOF'
usage: start-connector.sh [--config PATH] [--python PATH] [--log-dir DIR]

  --config PATH   the binding to read (default: the one install.sh writes)
  --python PATH   the interpreter to use. Falls back to $SERVEROS_FLEET_PYTHON,
                  then to python3 on PATH. It must be an interpreter that can
                  import fleet_mcp.node.connector, which means PyYAML is
                  installed for it.
  --log-dir DIR   append stdout and stderr to separate files in DIR, instead of
                  writing to wherever this was started from. Omit it under
                  systemd: the journal already keeps both streams, and a second
                  copy of a log is a copy that disagrees.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG="${2:?--config needs a value}"; shift 2 ;;
        --python) PYTHON_HINT="${2:?--python needs a value}"; shift 2 ;;
        --log-dir) LOG_DIR="${2:?--log-dir needs a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf '%s\n' "error: unknown argument: $1 (try --help)" >&2; exit 2 ;;
    esac
done

if [ -z "$CONFIG" ]; then
    CONFIG=$(fleet_binding_file) || exit 3
fi

if [ ! -f "$CONFIG" ]; then
    printf '%s\n' \
        "No connector binding at $CONFIG. Run install.sh first: it writes a" \
        "template whose send allow list is empty, which means every send is" \
        "refused until somebody at this keyboard says otherwise." >&2
    exit 3
fi

PYTHON="${PYTHON_HINT:-${SERVEROS_FLEET_PYTHON:-}}"
if [ -z "$PYTHON" ]; then
    PYTHON=$(command -v python3 || true)
fi
if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
    printf '%s\n' \
        "No python3 found on PATH and none given with --python." \
        "Set SERVEROS_FLEET_PYTHON or pass --python explicitly: a unit that" \
        "cannot find its interpreter should say so rather than exit quietly." >&2
    exit 3
fi

# PYTHONPATH is exported and the directory is changed to the same tree, so the
# import path does not depend on where this was started from.
export PYTHONPATH="$REPO_ROOT"
export PYTHONIOENCODING=utf-8
cd -- "$REPO_ROOT"

# -X utf8 because a session title can contain anything and the locale of a
# launcher is not something this can rely on.
ARGS=(-X utf8 -m fleet_mcp.node.connector --config "$CONFIG")

if [ -n "$LOG_DIR" ]; then
    mkdir -p -- "$LOG_DIR"
    # Two files, not one: a launcher that points stdout and stderr at the same
    # path is ambiguous about which stream an interleaved line came from.
    exec "$PYTHON" "${ARGS[@]}" \
        >> "$LOG_DIR/fleet-node-connector.out.log" \
        2>> "$LOG_DIR/fleet-node-connector.log"
fi

exec "$PYTHON" "${ARGS[@]}"
