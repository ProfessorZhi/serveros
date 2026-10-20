#!/bin/bash
# Q1 snapshot AC — independent #347 / #349 / #345 checks (no product edits).
# Usage: ac-run.sh <347-tree> <349-tree> <345-tree> [347|349|345|poll|all]
set +e
T347="${1:?347 checkout required}"
T349="${2:?349 checkout required}"
T345="${3:?345 checkout required}"
WHAT="${4:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

run347() {
  echo "== #347 boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$T347/tests/v1_portability/test_portability_boundary.py"
  echo "== #347 alias probe"
  QA_TREE="$T347" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r10_pr342.py" 2>&1 | tail -2
}

run349() {
  echo "== #349 boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$T349/tests/v1_portability/test_portability_boundary.py"
  echo "== #349 X2 three files under guard"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r12-private-root}" \
    PYTHONPATH="$T349:$T349/tests/v1_portability" timeout 300 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$T349/tests/v1_portability/test_portability_boundary.py" \
    "$T349/tests/test_v1_grant_os_principal_schema.py" \
    "$T349/tests/test_fleet_v1_t10_legacy_compatibility.py"
  echo "== #349 alias/8.3/drive-relative probe"
  QA_TREE="$T349" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r12_guard_349.py" 2>&1 | grep -E "FAIL|TOTAL"
}

run345() {
  echo "== #345 tests"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 180 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$T345/tests/test_windows_cli_readonly.py"
  echo "== #345 CLI boundary probe"
  QA_TREE="$T345" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r12_cli_345.py" 2>&1 | grep -E "FAIL|TOTAL"
}

poll() {
  for i in 1 2 3 4; do
    echo "== poll $i $(date -u +%FT%TZ)"
    git ls-remote origin 'refs/heads/rc/*' | grep -i v6 || echo NO_V6_BRANCH
    [ "$i" -lt 4 ] && sleep 90
  done
}

case "$WHAT" in
  347) run347 ;;
  349) run349 ;;
  345) run345 ;;
  poll) poll ;;
  all) run347; run349; run345; poll ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
