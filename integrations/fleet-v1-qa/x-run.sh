#!/bin/bash
# Q1 snapshot X — pre-RC4 candidate audit (no product edits).
# Usage: x-run.sh <isolated-worktree> [red332|green332|335|334|hosted334|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RCV2=ec43f714c855e07055f32707d4afaebcc5313580
G332=a60ae107423703b200e5f5915ebab1001f4d65f4
G335=85b953963e008f5777b9eb969fd9b1ffe09e4dbf
G334=c984f4a52b159eb6c358a5c573e39dc3c54dd704
SEQ=(tests/test_fleet_v1_t10_authority_bridge.py
     tests/test_fleet_v1_t10_continuation_provenance.py
     tests/test_fleet_v1_t10_legacy_compatibility.py
     tests/test_fleet_v1_t10_producer_binding.py
     tests/test_fleet_v1_t10_signed_finalization.py
     tests/test_finalize_consistency.py
     tests/test_runner_receipt.py
     tests/test_finalize_entry.py)

guard() { systemd-run --user --scope --quiet \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 nice -n 10 "$@"; }
checkout() { git -C "$TREE" checkout --detach "$1" || return 2; echo "== @ $(git -C "$TREE" rev-parse HEAD)"; }

case "$WHAT" in
  red332) checkout "$RCV2" || exit 2; guard timeout 420 "$PY" -m pytest "${SEQ[@]}" -q -p no:cacheprovider ;;
  green332) checkout "$G332" || exit 2
    guard timeout 420 "$PY" -m pytest "${SEQ[@]}" -q -p no:cacheprovider
    guard timeout 120 "$PY" -m pytest tests/test_v1_import_isolation.py -q -p no:cacheprovider ;;
  335) checkout "$G335" || exit 2
    guard timeout 300 "$PY" -m pytest tests/test_fleet_v1_t10_legacy_compatibility.py \
      tests/test_v1_grant_os_principal_schema.py \
      tests/v1_portability/test_portability_boundary.py -q -rs -p no:cacheprovider
    guard timeout 300 "$PY" -m pytest tests/test_fleet_v1_t10_legacy_compatibility.py \
      tests/test_v1_grant_os_principal_schema.py \
      tests/v1_portability/test_portability_boundary.py \
      tests/test_finalize_entry.py tests/test_finalize_consistency.py -q -rs -p no:cacheprovider
    guard timeout 300 env SERVEROS_PORTABILITY_GUARD=1 \
      SERVEROS_PRIVATE_LEGACY_ROOTS=/data/home/vince/workspace/src/work-engine \
      PYTHONPATH="$TREE/tests/v1_portability:$TREE" "$PY" -m pytest -p private_path_guard \
      tests/test_fleet_v1_t10_legacy_compatibility.py \
      tests/test_v1_grant_os_principal_schema.py \
      tests/v1_portability/test_portability_boundary.py \
      tests/test_finalize_entry.py tests/test_finalize_consistency.py -q -rs -p no:cacheprovider ;;
  334) checkout "$G334" || exit 2
    rm -rf /tmp/q1-win-core-linux
    guard timeout 540 bash -lc "$CM -S $TREE/native/node-core/windows-core -B /tmp/q1-win-core-linux -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-win-core-linux -j2 >/dev/null 2>&1 && cd /tmp/q1-win-core-linux && $CT -V" ;;
  hosted334) gh run view 39390576739 --json headSha,conclusion,jobs --jq '{sha:.headSha,conclusion:.conclusion,jobs:[.jobs[]|{name,conclusion}]}' ;;
  all) "$0" "$TREE" red332; "$0" "$TREE" green332; "$0" "$TREE" 335; "$0" "$TREE" 334; "$0" "$TREE" hosted334 ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
