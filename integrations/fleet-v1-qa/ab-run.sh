#!/bin/bash
# Q1 snapshot AB — RC-v5 fixed-SHA checks (no product edits).
# Usage: ab-run.sh <rc-v5-checkout> [light|nine|native|realtemp|guard|all]
set +e
TREE="${1:?RC-v5 checkout required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

light() {
  for p in qa_probe_r2_handoff qa_probe_r4_lease_admission \
           qa_probe_r3_expiry_timezone qa_probe_r3_preclaim_failure \
           qa_probe_r3_context_authority qa_probe_r3_idempotency_restart \
           qa_probe_r5_core_reconcile qa_probe_r2_reconcile_312 \
           qa_probe_r6_fleet_next_gate; do
    echo "== $p"
    QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
      guard "$PY" "$PROBE/$p.py" 2>&1 | tail -1
  done
}

nine() {
  echo "== 9-file T10+Finalize+Core under private-path guard"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r11-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 420 "$PY" -m pytest \
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
  echo "== X2 boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$TREE/tests/v1_portability/test_portability_boundary.py"
  echo "== Fleet Next offline"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider \
    "$TREE/tests/test_fleet_next_install.py" \
    "$TREE/tests/test_fleet_next_dual_stack_preflight.py" \
    "$TREE/tests/test_fleet_next_canary_start.py" \
    "$TREE/tests/test_fleet_v1_next_refresh_stage.py" \
    "$TREE/tests/test_fleet_v1_next_stage_unit_receipt.py" \
    "$TREE/tests/test_fleet_v1_next_tailscale_gate.py"
}

native() {
  rm -rf /tmp/q1-sup-rcv5-build
  guard timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup-rcv5-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup-rcv5-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup-rcv5-build && $CT -R 'sup_action_(journal|gate)' -V --output-on-failure"
  g++ -std=c++20 -Wall -Wextra -DQ1_HAS_HOOKS -DQ1_HAS_GROUP_CODE \
    -I "$TREE/native/node-core/supervisor/include" \
    -I "$TREE/native/node-core/include" \
    "$PROBE/q1_state_dir_probe.cpp" \
    /tmp/q1-sup-rcv5-build/supervisor/libnodecore_supervisor.a \
    /tmp/q1-sup-rcv5-build/libnodecore.a -o /tmp/q1-state-probe-rcv5 || return 2
  guard timeout 60 /tmp/q1-state-probe-rcv5
}

case "$WHAT" in
  light) light ;;
  nine) nine ;;
  native) native ;;
  realtemp) guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  guard) QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
           guard "$PY" "$PROBE/qa_probe_r10_pr342.py" 2>&1 | grep -E "FAIL|TOTAL" ;;
  all) light; nine; native; guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE"; ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
