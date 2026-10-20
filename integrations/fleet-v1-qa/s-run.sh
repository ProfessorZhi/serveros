#!/bin/bash
# Q1 snapshot S — independent acceptance probes (no product edits).
# Usage: s-run.sh <isolated-worktree> [handoff-red|handoff|t10-red|t10|lease-red|lease|grant-red|grant|312|all]
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
run() {
  local name="$1" ref="$2" mode="$3" probe="$4"
  git -C "$TREE" checkout --detach "$ref" || return 2
  echo "== $name @ $(git -C "$TREE" rev-parse HEAD)"
  QA_TREE="$TREE" QA_MODE="$mode" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
    systemd-run --user --scope --quiet \
      -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 \
      nice -n 10 "$PY" "$HERE/probe/$probe"
  echo "exit=$?"
}
case "$WHAT" in
  handoff-red) run handoff-red 429a717bedf1f6c28057a5e355970ff351100571 red qa_probe_r2_handoff.py ;;
  handoff)     run handoff "${S1_HEAD:-7d35535b53f791513a10b28554b4bd79e943e77a}" green qa_probe_r2_handoff.py ;;
  close-context) run close-context "${S1_HEAD:-7d35535b53f791513a10b28554b4bd79e943e77a}" green qa_probe_r2_close_context.py ;;
  t10-red)     run t10-red 771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8 red qa_probe_r2_t10.py ;;
  t10)         run t10 776cd982689774a5a275c2cc395a2e092d1910be green qa_probe_r2_t10.py ;;
  lease-red)   run lease-red e1f783fd93d77b0f1a3fddc183f740285d442b72 red qa_probe_r2_lease_hardening.py ;;
  lease)       run lease 776cd982689774a5a275c2cc395a2e092d1910be green qa_probe_r2_lease_hardening.py ;;
  grant-red)   run grant-red fc93590030c218934252920a53aa67277f144844 red qa_probe_r2_grant_window.py ;;
  grant)       run grant 6abefaa7beadb908054cd7d162c1e34c48a29eef green qa_probe_r2_grant_window.py ;;
  312)         run 312 2a3e1352b442b0a307deba50e293a10c8ef34d61 green qa_probe_r2_reconcile_312.py ;;
  all)
    run handoff-red 429a717bedf1f6c28057a5e355970ff351100571 red qa_probe_r2_handoff.py
    run handoff "${S1_HEAD:-7d35535b53f791513a10b28554b4bd79e943e77a}" green qa_probe_r2_handoff.py
    run close-context "${S1_HEAD:-7d35535b53f791513a10b28554b4bd79e943e77a}" green qa_probe_r2_close_context.py
    run t10-red 771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8 red qa_probe_r2_t10.py
    run t10 776cd982689774a5a275c2cc395a2e092d1910be green qa_probe_r2_t10.py
    run lease-red e1f783fd93d77b0f1a3fddc183f740285d442b72 red qa_probe_r2_lease_hardening.py
    run lease 776cd982689774a5a275c2cc395a2e092d1910be green qa_probe_r2_lease_hardening.py
    run grant-red fc93590030c218934252920a53aa67277f144844 red qa_probe_r2_grant_window.py
    run grant 6abefaa7beadb908054cd7d162c1e34c48a29eef green qa_probe_r2_grant_window.py
    run 312 2a3e1352b442b0a307deba50e293a10c8ef34d61 green qa_probe_r2_reconcile_312.py
    ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
