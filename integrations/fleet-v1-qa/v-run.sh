#!/bin/bash
# Q1 snapshot V — convergence probes (no product edits).
# Usage: v-run.sh <isolated-worktree> [boundary-red|boundary-green|core|327|326|rcv2|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
R312=2a3e1352b442b0a307deba50e293a10c8ef34d61
G328=cf32af82c03eb52a4f34e2907e670f23b04a9b57
G327=5b7ed524fce096fcda6b85a2636cd18d87912343
P326=3d30c98ed5e0dc7750c8b5a7b9e194c5659d9e3e
RCV2=ec43f714c855e07055f32707d4afaebcc5313580

guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }

checkout() { git -C "$TREE" checkout --detach "$1" || return 2; echo "== @ $(git -C "$TREE" rev-parse HEAD)"; }

pytest_at() {
  local ref="$1"; shift
  checkout "$ref" || return 2
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 300 guard "$PY" -m pytest "$@" -q -rs -p no:cacheprovider
}

probe_at() {
  local ref="$1" script="$2"
  checkout "$ref" || return 2
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 guard "$PY" "$HERE/probe/$script"
}

cpp327() {
  checkout "$G327" || return 2
  rm -rf /tmp/q1-sup327-build
  guard timeout 540 bash -lc "$CM -S $TREE/native/node-core -B /tmp/q1-sup327-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup327-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup327-build && $CT -R 'sup_action_(journal|gate)' --output-on-failure"
  echo "ctest_exit=$?"
  g++ -std=c++20 -Wall -Wextra -DQ1_HAS_HOOKS -DQ1_HAS_GROUP_CODE \
    -I "$TREE/native/node-core/supervisor/include" -I "$TREE/native/node-core/include" \
    "$HERE/probe/q1_state_dir_probe.cpp" \
    /tmp/q1-sup327-build/supervisor/libnodecore_supervisor.a \
    /tmp/q1-sup327-build/libnodecore.a -o /tmp/q1-state-probe-327 || return 2
  guard timeout 60 /tmp/q1-state-probe-327
}

case "$WHAT" in
  boundary-red) pytest_at "$R312" tests/test_core_ability_boundary.py ;;
  boundary-green) pytest_at "$G328" tests/test_core_ability_boundary.py ;;
  core) probe_at "$G328" qa_probe_r5_core_reconcile.py ;;
  327) cpp327 ;;
  326) pytest_at "$P326" tests/test_fleet_v1_t10_legacy_compatibility.py tests/test_v1_grant_os_principal_schema.py tests/v1_portability/test_portability_boundary.py ;;
  rcv2)
    pytest_at "$RCV2" tests/test_core_ability_boundary.py
    probe_at "$RCV2" qa_probe_r4_lease_admission.py
    ;;
  all)
    pytest_at "$R312" tests/test_core_ability_boundary.py
    pytest_at "$G328" tests/test_core_ability_boundary.py
    probe_at "$G328" qa_probe_r5_core_reconcile.py
    cpp327
    pytest_at "$P326" tests/test_fleet_v1_t10_legacy_compatibility.py tests/test_v1_grant_os_principal_schema.py tests/v1_portability/test_portability_boundary.py
    ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
