#!/bin/bash
# Q1 snapshot T — final-gate independent probes (no product edits).
# Usage: t-run.sh <isolated-worktree> [red|green|317|all]
# RED=#315 7d35535, GREEN=#318 2640bce, 317=cb38ec6.
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
CM="${CMAKE_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/cmake}"
CT="${CTEST_BIN:-/data/home/vince/toolchains/cmake-native/cmake/data/bin/ctest}"
HERE="$(cd "$(dirname "$0")" && pwd)"
RED=7d35535b53f791513a10b28554b4bd79e943e77a
GREEN="${S1_HEAD:-2640bcee18bc4bcc58b25c548e8cfb19542f1df9}"
N317=cb38ec6fe039c7ba85c8e73f1f7c3e351edaf235

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

cpp317() {
  git -C "$TREE" checkout --detach "$N317" || return 2
  rm -rf /tmp/q1-sup-build
  systemd-run --user --scope --quiet \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 \
    nice -n 10 timeout 540 bash -lc \
    "$CM -S $TREE/native/node-core -B /tmp/q1-sup-build -DCMAKE_BUILD_TYPE=Release >/dev/null 2>&1 && $CM --build /tmp/q1-sup-build --target test_sup_action_journal test_sup_action_gate -j2 >/dev/null 2>&1 && cd /tmp/q1-sup-build && $CT -R 'sup_action_(journal|gate)' --output-on-failure"
}

case "$WHAT" in
  red)
    probe context-red "$RED" qa_probe_r3_context_authority.py
    probe preclaim-red "$RED" qa_probe_r3_preclaim_failure.py
    probe expiry-red "$RED" qa_probe_r3_expiry_timezone.py
    probe idem-red "$RED" qa_probe_r3_idempotency_restart.py
    probe handoff-red "$RED" qa_probe_r2_handoff.py
    ;;
  green)
    probe context-green "$GREEN" qa_probe_r3_context_authority.py
    probe preclaim-green "$GREEN" qa_probe_r3_preclaim_failure.py
    probe expiry-green "$GREEN" qa_probe_r3_expiry_timezone.py
    probe idem-green "$GREEN" qa_probe_r3_idempotency_restart.py
    probe handoff-green "$GREEN" qa_probe_r2_handoff.py
    ;;
  317) cpp317 ;;
  all) "$0" "$TREE" red; "$0" "$TREE" green; cpp317 ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
