#!/bin/bash
# Q1 snapshot AF — RC-v7 fixed-SHA checks (no product edits).
# Usage: af-run.sh <rc-v7-checkout> [light|nine|x2|cli|native|realtemp|all]
set +e
TREE="${1:?RC-v7 checkout required}"
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
  echo "== 9-file T10+Finalize+Core under guard"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r15-private-root}" \
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
  echo "== Core boundary"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 180 "$PY" -m pytest -q \
    -p no:cacheprovider "$TREE/tests/test_core_ability_boundary.py"
}

x2() {
  echo "== X2 boundary + three files"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 240 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$TREE/tests/v1_portability/test_portability_boundary.py"
  guard env PYTHONDONTWRITEBYTECODE=1 SERVEROS_PORTABILITY_GUARD=1 \
    SERVEROS_PRIVATE_LEGACY_ROOTS="${QA_PRIVATE_ROOT:-/tmp/q1-r15-private-root}" \
    PYTHONPATH="$TREE:$TREE/tests/v1_portability" timeout 300 "$PY" -m pytest \
    -q -rs -p private_path_guard -p no:cacheprovider \
    "$TREE/tests/v1_portability/test_portability_boundary.py" \
    "$TREE/tests/test_v1_grant_os_principal_schema.py" \
    "$TREE/tests/test_fleet_v1_t10_legacy_compatibility.py"
  echo "== guard probe"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r14_353.py" 2>&1 | grep -E "FAIL|TOTAL|REAL_NTFS"
}

cli() {
  echo "== CLI tests + boundary probe"
  guard env PYTHONDONTWRITEBYTECODE=1 timeout 180 "$PY" -m pytest -q -rs \
    -p no:cacheprovider "$TREE/tests/test_windows_cli_readonly.py"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 120 \
    guard "$PY" "$PROBE/qa_probe_r12_cli_345.py" 2>&1 | grep -E "FAIL|TOTAL"
}

native() {
  rm -rf /tmp/q1-sup-rcv7-build
  guard timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup-rcv7-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup-rcv7-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup-rcv7-build && $CT -R 'sup_action_(journal|gate)' -V --output-on-failure"
  g++ -std=c++20 -Wall -Wextra -DQ1_HAS_HOOKS -DQ1_HAS_GROUP_CODE \
    -I "$TREE/native/node-core/supervisor/include" \
    -I "$TREE/native/node-core/include" \
    "$PROBE/q1_state_dir_probe.cpp" \
    /tmp/q1-sup-rcv7-build/supervisor/libnodecore_supervisor.a \
    /tmp/q1-sup-rcv7-build/libnodecore.a -o /tmp/q1-state-probe-rcv7 || return 2
  guard timeout 60 /tmp/q1-state-probe-rcv7
}

case "$WHAT" in
  light) light ;;
  nine) nine ;;
  x2) x2 ;;
  cli) cli ;;
  native) native ;;
  realtemp) guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  all) light; nine; x2; cli; native; guard timeout 120 bash "$PROBE/r9_rcv4_realtemp.sh" "$TREE" ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
