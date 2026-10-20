#!/bin/bash
# Q1 snapshot AJ — RC-v9 (#371 test-only) minimal checks (no product edits).
# Usage: aj-run.sh <rc-v9-checkout> [owner|collector|nine|x2|fleet|realtemp|all]
set +e
TREE="${1:?RC-v9 checkout required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

owner() {
  echo "== owner suite (3 POSIX + adapter)"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider \
    "$TREE/tests/test_owner_commit_fence.py" \
    "$TREE/tests/test_owner_commit_fence_adapter.py" \
    "$TREE/tests/test_owner_policy_binding.py" \
    "$TREE/tests/test_owner_policy_reduced_gate.py"
}

collector() {
  echo "== synthetic Win32 collector (3 skipped / 0 error + adapter collected)"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 120 "$PY" \
    "$TREE/tests/probes/s1_win32_collection_probe.py" "$TREE" \
    tests/test_owner_commit_fence.py \
    tests/test_owner_policy_binding.py \
    tests/test_owner_policy_reduced_gate.py \
    tests/test_owner_commit_fence_adapter.py
}

seam() {
  echo "== S1 owner seam (zero lease)"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
    guard "$PY" "$PROBE/qa_probe_r19_seam_364.py" 2>&1 | tail -2
}

nine() {
  mkdir -p "${QA_PRIVATE_ROOT:-/tmp/q1-r24-private-root}"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r24-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 500 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/test_fleet_v1_t10_authority_bridge.py" \
    "$TREE/tests/test_fleet_v1_t10_continuation_provenance.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_fleet_v1_t10_producer_binding.py" \
    "$TREE/tests/test_fleet_v1_t10_signed_finalization.py" \
    "$TREE/tests/test_finalize_consistency.py" \
    "$TREE/tests/test_runner_receipt.py" \
    "$TREE/tests/test_finalize_entry.py" \
    "$TREE/tests/test_core_ability_boundary.py"
}

x2() {
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r24-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 360 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_windows_cli_readonly.py" \
    "$TREE/tests/test_v1_import_isolation.py"
}

fleet() {
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider \
    "$TREE/tests/test_fleet_next_install.py" \
    "$TREE/tests/test_fleet_next_dual_stack_preflight.py" \
    "$TREE/tests/test_fleet_next_canary_start.py" \
    "$TREE/tests/test_fleet_v1_next_refresh_stage.py" \
    "$TREE/tests/test_fleet_v1_next_stage_unit_receipt.py" \
    "$TREE/tests/test_fleet_v1_next_tailscale_gate.py"
}

case "$WHAT" in
  owner) owner ;;
  collector) collector ;;
  seam) seam ;;
  nine) nine ;;
  x2) x2 ;;
  fleet) fleet ;;
  realtemp) guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  all) owner; collector; seam; nine; x2; fleet; guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
