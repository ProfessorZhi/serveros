#!/bin/bash
# Q1 snapshot AH — #367 synthetic import-probe checks (no product edits).
# Usage: ah-run.sh <367-checkout> [red-364-checkout] [tests|red|all]
set +e
TREE="${1:?367 checkout required}"
RED="${2:-}"
WHAT="${3:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

red() {
  [ -n "$RED" ] || { echo "red checkout not supplied" >&2; return 2; }
  echo "== RED at #364 base (expected 1 failed)"
  ( cd "$RED" && guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
      -p no:cacheprovider tests/v1_portability/test_portability_boundary.py )
}

tests() {
  echo "== boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$TREE/tests/v1_portability/test_portability_boundary.py"
  echo "== X2 three files under guard"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r21-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 300 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py"
  echo "== combo 119"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 300 "$PY" -m pytest -q -rs \
    -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_finalize_entry.py" \
    "$TREE/tests/test_finalize_consistency.py"
}

case "$WHAT" in
  tests) tests ;;
  red) red ;;
  all) red; tests ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
