#!/bin/bash
# Q1 snapshot W — Fleet Next unit-name P0 / boundary probes (no product edits).
# Usage: w-run.sh <isolated-worktree> [red-base|green-330|probe|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BASE=ec43f714c855e07055f32707d4afaebcc5313580
G330=bf1b514f9bcb1386f14f3a866dfda8162ee5cfc4

guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

checkout() { git -C "$TREE" checkout --detach "$1" || return 2; echo "== @ $(git -C "$TREE" rev-parse HEAD)"; }

suite() {
  checkout "$1" || return 2
  for p in test_fleet_next_dual_stack_preflight test_fleet_next_install \
           test_fleet_runtime_create_policy test_fleet_create_session_runtime; do
    echo "== $p"
    guard timeout 300 "$PY" -m unittest discover -s "$TREE/tests" -p "$p.py" 2>&1 | tail -3
    echo "exit=${PIPESTATUS[0]}"
  done
}

case "$WHAT" in
  red-base)
    checkout "$BASE" || exit 2
    guard timeout 300 "$PY" -m unittest discover -s "$TREE/tests" -p test_fleet_next_install.py 2>&1 | tail -8
    ;;
  green-330)
    suite "$G330"
    ;;
  probe)
    checkout "$G330" || exit 2
    QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 120 guard "$PY" "$HERE/probe/qa_probe_r6_fleet_next_gate.py"
    ;;
  all) "$0" "$TREE" red-base; "$0" "$TREE" green-330; "$0" "$TREE" probe ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
