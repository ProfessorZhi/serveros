#!/usr/bin/env bash
# Publish one Core-Lite status document for this account — observe only.
#
#   publish-status.sh --bin DIR --profile PROFILE --out FILE [--filesystem ROLE=PATH ...]
#                     [--network-target NAME=HOST:PORT ...]
#
# With --network-target, this device also measures its network paths (TCP
# connect time and failures, `net/probe_network.py`) into network.json next to
# FILE, at most every five minutes and AFTER the status is in place, and folds
# the last fresh measurement into the next document as `network.readonly`.
# Only the path names reach the document; hosts and ports stay here.
#
# Runs the existing collector (two samples) and the Core-Lite status builder,
# then replaces FILE atomically, owner-only (0600). FILE is what the Fleet node
# connector serves at /v1/core/status (`core_snapshot_path`) and what the hub's
# local source reads (`core_lite.snapshot_path`). Nothing here starts, stops or
# changes anything on the machine; a failed run leaves the previous document in
# place and exits non-zero, so a stale reading is visible by its observed_at
# instead of being replaced by a half-written one.
set -euo pipefail
umask 077

bin="" profile="" out=""
fs_args=()
net_targets=()
while [ $# -gt 0 ]; do
  case "$1" in
    --bin) bin="$2"; shift 2 ;;
    --profile) profile="$2"; shift 2 ;;
    --out) out="$2"; shift 2 ;;
    --filesystem) fs_args+=(--filesystem "$2"); shift 2 ;;
    --network-target) net_targets+=(--target "$2"); shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$bin" ] && [ -n "$profile" ] && [ -n "$out" ] || { echo "--bin, --profile and --out are required" >&2; exit 2; }
case "$out" in /*) ;; *) echo "--out must be absolute" >&2; exit 2 ;; esac
[ -x "$bin/serveros-node-resource-snapshot" ] && [ -x "$bin/core-lite-status" ] || {
  echo "collector or status builder not found under $bin" >&2; exit 2; }

dir="$(dirname "$out")"
mkdir -p "$dir"
chmod 700 "$dir"
work="$(mktemp -d "$dir/.publish.XXXXXX")"
trap 'rm -rf "$work"' EXIT

"$bin/serveros-node-resource-snapshot" --compact "${fs_args[@]}" > "$work/snapshot.json"
"$bin/core-lite-status" --profile "$profile" --snapshot "$work/snapshot.json" > "$work/status.json"
python3 - "$work/status.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
if not isinstance(doc, dict):
    sys.exit("status is not a JSON object")
PY
here="$(cd "$(dirname "$0")" && pwd)"
net_dir="$here"
[ -f "$net_dir/probe_network.py" ] || net_dir="$here/../net"
network="$dir/network.json"
if [ ${#net_targets[@]} -gt 0 ] && [ -f "$net_dir/fold_network.py" ]; then
  python3 "$net_dir/fold_network.py" "$work/status.json" "$network" || true
fi
chmod 600 "$work/status.json"
mv -f "$work/status.json" "$out"

# The probe runs after the document is in place, so it never makes one late.
if [ ${#net_targets[@]} -gt 0 ] && [ -f "$net_dir/probe_network.py" ]; then
  if [ ! -f "$network" ] || [ -n "$(find "$network" -mmin +5 2>/dev/null)" ]; then
    python3 "$net_dir/probe_network.py" --out "$network" --samples 3 --timeout 1.5 \
      "${net_targets[@]}" || true
  fi
fi
