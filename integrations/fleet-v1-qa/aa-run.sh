#!/bin/bash
# Q1 snapshot AA — independent #342 / #343 checks (no product edits).
# Usage: aa-run.sh <pr342-checkout> <pr343-checkout> [probes|tests|poll|all]
set +e
T342="${1:?PR342 checkout required}"
T343="${2:?PR343 checkout required}"
WHAT="${3:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

probes() {
  echo "== #342 fixture canonicalization + path guard (expect P2 alias FAILs)"
  QA_TREE="$T342" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r10_pr342.py" 2>&1 | grep -E "FAIL|TOTAL|fixture_"
  echo "== #343 in-tree identity + ReceiptError"
  QA_TREE="$T343" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r10_pr343.py" 2>&1 | tail -3
}

tests() {
  echo "== #342 X2 three files (unguarded)"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 300 "$PY" -m pytest -q -rs -p no:cacheprovider \
    "$T342/tests/v1_portability/test_portability_boundary.py" \
    "$T342/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$T342/tests/test_v1_grant_os_principal_schema.py"
  echo "== #342 X2 three files (private-path guard)"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r10-private-root}" \
    PYTHONPATH="$T342:$T342/tests/v1_portability" timeout 300 "$PY" -m pytest -q -rs \
    -p private_path_guard -p no:cacheprovider \
    "$T342/tests/v1_portability/test_portability_boundary.py" \
    "$T342/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$T342/tests/test_v1_grant_os_principal_schema.py"
  echo "== #343 nine files, one process, guarded"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r10-private-root}" \
    PYTHONPATH="$T343:$T343/tests/v1_portability" timeout 420 "$PY" -m pytest -q -rs \
    -p private_path_guard -p no:cacheprovider \
    "$T343/tests/test_fleet_v1_t10_authority_bridge.py" \
    "$T343/tests/test_fleet_v1_t10_continuation_provenance.py" \
    "$T343/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$T343/tests/test_fleet_v1_t10_producer_binding.py" \
    "$T343/tests/test_fleet_v1_t10_signed_finalization.py" \
    "$T343/tests/test_finalize_consistency.py" \
    "$T343/tests/test_runner_receipt.py" \
    "$T343/tests/test_finalize_entry.py" \
    "$T343/tests/test_core_ability_boundary.py"
  echo "== #343 explicit T10 refusals"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 180 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$T343/tests/test_fleet_v1_t10_authority_bridge.py" \
    "$T343/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    -k "caller_json_fake_label or binding_mismatch or receipt_exit_disagreement or trusted_parent_label or real_exit_one_with_pass_text"
}

poll() {
  for i in 1 2 3 4; do
    echo "== poll $i $(date -u +%FT%TZ)"
    git ls-remote origin 'refs/heads/rc/*' | grep -i v5 || echo NO_V5_BRANCH
    [ "$i" -lt 4 ] && sleep 90
  done
}

case "$WHAT" in
  probes) probes ;;
  tests) tests ;;
  poll) poll ;;
  all) probes; tests; poll ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
