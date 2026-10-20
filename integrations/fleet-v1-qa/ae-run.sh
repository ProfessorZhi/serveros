#!/bin/bash
# Q1 snapshot AE — #353 host-privilege/alias guard checks (no product edits).
# Usage: ae-run.sh <353-checkout> [tests|probe|poll|all]
set +e
TREE="${1:?353 checkout required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

tests() {
  echo "== #353 boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$TREE/tests/v1_portability/test_portability_boundary.py"
  echo "== #353 X2 three files under guard"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r14-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 300 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py"
}

probes() {
  echo "== #353 host-privilege + alias refusal probe"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r14_353.py" 2>&1 | grep -E "FAIL|TOTAL|REAL_NTFS"
}

poll() {
  for i in 1 2 3 4; do
    echo "== poll $i $(date -u +%FT%TZ)"
    git ls-remote origin 'refs/heads/rc/*' | grep -i v7 || echo NO_V7_BRANCH
    [ "$i" -lt 4 ] && sleep 90
  done
}

case "$WHAT" in
  tests) tests ;;
  probe) probes ;;
  poll) poll ;;
  all) tests; probes; poll ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
