#!/bin/bash
# Q1 snapshot Y — X1 context-age / RC-v4 readiness (no product edits).
# Usage: y-run.sh <isolated-worktree> [red|green|targeted|rcv4|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE=e3dc9c7ca0aeca9dd97e981de85da3a727ed9d8a
G338=96e0fcf2cd464e5e15fa88ef7032f06139c8a06c

guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }
checkout() { git -C "$TREE" checkout --detach "$1" || return 2; echo "== @ $(git -C "$TREE" rev-parse HEAD)"; }

case "$WHAT" in
  red) checkout "$BASE" || exit 2
    QA_TREE="$TREE" QA_MODE=red PYTHONDONTWRITEBYTECODE=1 timeout 120 guard "$PY" "$HERE/probe/qa_probe_r8_context_freshness.py" ;;
  green) checkout "$G338" || exit 2
    QA_TREE="$TREE" QA_MODE=green PYTHONDONTWRITEBYTECODE=1 timeout 120 guard "$PY" "$HERE/probe/qa_probe_r8_context_freshness.py" ;;
  targeted) checkout "$G338" || exit 2
    guard timeout 300 "$PY" -m pytest tests/test_agent_mcp.py -q -rs -p no:cacheprovider ;;
  rcv4)
    # Read-only GitHub check. Never touch the I1 worktree.
    git ls-remote --heads origin 'refs/heads/rc/*' | sort
    if git ls-remote --heads origin 'refs/heads/rc/*' | grep -qi 'v4'; then
      echo "RC-v4 ref exists; pin its exact SHA and run the full matrix."
    else
      echo "RC-v4 PENDING (no GitHub review ref)."
    fi ;;
  all) "$0" "$TREE" red; "$0" "$TREE" green; "$0" "$TREE" targeted; "$0" "$TREE" rcv4 ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
