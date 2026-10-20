#!/bin/bash
# Q1 snapshot U — final acceptance probes (no product edits).
# Usage: u-run.sh <isolated-worktree> [red318|green320|321|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RED=2640bcee18bc4bcc58b25c548e8cfb19542f1df9
GREEN="${S1_HEAD:-d559e17442176e126bb4cf3cef6a074f85190245}"
N321="${N321_HEAD:-ab24bcf896a7b35c9661b82ab9c17507bc520ba6}"

probe() {
  local name="$1" ref="$2" script="$3"
  git -C "$TREE" checkout --detach "$ref" || return 2
  echo "== $name @ $(git -C "$TREE" rev-parse HEAD)"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
    systemd-run --user --scope --quiet \
      -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 \
      nice -n 10 "$PY" "$HERE/probe/$script"
  echo "exit=$?"
}

suite() {
  local ref="$1"
  probe lease-admission "$ref" qa_probe_r4_lease_admission.py
  probe expiry "$ref" qa_probe_r3_expiry_timezone.py
  probe preclaim "$ref" qa_probe_r3_preclaim_failure.py
  probe context "$ref" qa_probe_r3_context_authority.py
  probe idem "$ref" qa_probe_r3_idempotency_restart.py
  probe handoff "$ref" qa_probe_r2_handoff.py
}

cpp321() {
  git -C "$TREE" checkout --detach "$N321" || return 2
  rm -rf /tmp/q1-sup321-build
  systemd-run --user --scope --quiet \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 \
    nice -n 10 timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup321-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup321-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup321-build && $CT -R 'sup_action_(journal|gate)' --output-on-failure"
}

case "$WHAT" in
  red318) suite "$RED" ;;
  green320) suite "$GREEN" ;;
  321) cpp321 ;;
  all) suite "$RED"; suite "$GREEN"; cpp321 ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
