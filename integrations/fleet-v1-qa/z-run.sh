#!/bin/bash
# Q1 RC-v4 gate — reproducible independent checks (no product edits).
# Usage: z-run.sh <rc-v4-qa-checkout> [light|tests|native|realtemp|all]
set +e
TREE="${1:?RC-v4 QA checkout required}"
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
      guard "$PY" "$PROBE/$p.py" 2>&1 | tail -2
  done
}

tests() {
  echo "== core boundary"
  guard timeout 240 "$PY" -m pytest "$TREE/tests/test_core_ability_boundary.py" -q -p no:cacheprovider
  echo "== T10 + Finalize + core (134)"
  guard timeout 420 "$PY" -m pytest \
    "$TREE/tests/test_fleet_v1_t10_authority_bridge.py" \
    "$TREE/tests/test_fleet_v1_t10_continuation_provenance.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_fleet_v1_t10_producer_binding.py" \
    "$TREE/tests/test_fleet_v1_t10_signed_finalization.py" \
    "$TREE/tests/test_finalize_consistency.py" \
    "$TREE/tests/test_runner_receipt.py" \
    "$TREE/tests/test_finalize_entry.py" \
    "$TREE/tests/test_core_ability_boundary.py" -q -p no:cacheprovider
  echo "== X2 + import isolation (42)"
  guard timeout 240 "$PY" -m pytest \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_import_isolation.py" -q -rs -p no:cacheprovider
  echo "== Fleet Next offline (286)"
  guard timeout 420 "$PY" -m pytest \
    "$TREE/tests/test_fleet_next_install.py" \
    "$TREE/tests/test_fleet_next_dual_stack_preflight.py" \
    "$TREE/tests/test_fleet_next_canary_start.py" \
    "$TREE/tests/test_fleet_v1_next_refresh_stage.py" \
    "$TREE/tests/test_fleet_v1_next_stage_unit_receipt.py" \
    "$TREE/tests/test_fleet_v1_next_tailscale_gate.py" -q -rs -p no:cacheprovider
  echo "== agent_mcp (72/177)"
  guard timeout 300 "$PY" -m pytest "$TREE/tests/test_agent_mcp.py" -q -rs -p no:cacheprovider
}

native() {
  rm -rf /tmp/q1-sup-rcv4-build
  guard timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup-rcv4-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup-rcv4-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup-rcv4-build && $CT -R 'sup_action_(journal|gate)' -V --output-on-failure"
  g++ -std=c++20 -Wall -Wextra -DQ1_HAS_HOOKS -DQ1_HAS_GROUP_CODE \
    -I "$TREE/native/node-core/supervisor/include" \
    -I "$TREE/native/node-core/include" \
    "$PROBE/q1_state_dir_probe.cpp" \
    /tmp/q1-sup-rcv4-build/supervisor/libnodecore_supervisor.a \
    /tmp/q1-sup-rcv4-build/libnodecore.a -o /tmp/q1-state-probe-rcv4 || return 2
  guard timeout 60 /tmp/q1-state-probe-rcv4
}

case "$WHAT" in
  light) light ;;
  tests) tests ;;
  native) native ;;
  realtemp) guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  all) light; tests; native; guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
