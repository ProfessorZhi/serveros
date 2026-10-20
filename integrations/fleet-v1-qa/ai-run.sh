#!/bin/bash
# Q1 snapshot AI — RC-v8 exact-HEAD checks (no product edits).
# Usage: ai-run.sh <rc-v8-checkout> [probes|nine|x2|native|realtemp|all]
set +e
TREE="${1:?RC-v8 checkout required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="$HERE/probe"
guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

probes() {
  for p in qa_probe_r2_handoff qa_probe_r4_lease_admission \
           qa_probe_r3_expiry_timezone qa_probe_r3_preclaim_failure \
           qa_probe_r3_context_authority qa_probe_r3_idempotency_restart \
           qa_probe_r5_core_reconcile qa_probe_r2_reconcile_312 \
           qa_probe_r6_fleet_next_gate; do
    echo "== $p"
    QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
      guard "$PY" "$PROBE/$p.py" 2>&1 | tail -1
  done
  echo "== owner seam"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
    guard "$PY" "$PROBE/qa_probe_r19_seam_364.py" 2>&1 | tail -2
}

nine() {
  mkdir -p "${QA_PRIVATE_ROOT:-/tmp/q1-r22-private-root}"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r22-private-root}" \
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
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r22-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 360 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py" \
    "$TREE/tests/test_windows_cli_readonly.py" \
    "$TREE/tests/test_v1_import_isolation.py"
}

native() {
  rm -rf /tmp/q1-sup-rcv8-build
  guard timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup-rcv8-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup-rcv8-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup-rcv8-build && $CT -R 'sup_action_(journal|gate)' -V --output-on-failure"
  g++ -std=c++20 -Wall -Wextra -DQ1_HAS_HOOKS -DQ1_HAS_GROUP_CODE \
    -I "$TREE/native/node-core/supervisor/include" \
    -I "$TREE/native/node-core/include" \
    "$PROBE/q1_state_dir_probe.cpp" \
    /tmp/q1-sup-rcv8-build/supervisor/libnodecore_supervisor.a \
    /tmp/q1-sup-rcv8-build/libnodecore.a -o /tmp/q1-state-probe-rcv8 || return 2
  guard timeout 60 /tmp/q1-state-probe-rcv8
}

case "$WHAT" in
  probes) probes ;;
  nine) nine ;;
  x2) x2 ;;
  native) native ;;
  realtemp) guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  all) probes; nine; x2; native; guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
