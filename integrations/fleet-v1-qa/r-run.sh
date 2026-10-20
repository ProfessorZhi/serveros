#!/bin/bash
# Q1 snapshot R — independent security QA probes.
# Usage: r-run.sh <isolated-worktree> [red|s1|272|we|all]
# No product edits. Resource guard is applied here; probes use temp state only.
set +e
TREE="${1:?isolated worktree required}"
WHAT="${2:-all}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
HERE="$(cd "$(dirname "$0")" && pwd)"
run() {
  local name="$1" ref="$2" probe="$3"
  git -C "$TREE" checkout --detach "$ref" || return 2
  echo "== $name @ $(git -C "$TREE" rev-parse HEAD)"
  QA_TREE="$TREE" PYTHONDONTWRITEBYTECODE=1 timeout 180 \
    systemd-run --user --scope --quiet \
      -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=64 \
      nice -n 10 "$PY" "$HERE/probe/$probe"
  echo "exit=$?"
}
case "$WHAT" in
  red) run red 771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8 qa_probe_r1_red.py ;;
  s1)  run s1  429a717bedf1f6c28057a5e355970ff351100571 qa_probe_r1_s1.py ;;
  272) run 272 fc93590030c218934252920a53aa67277f144844 qa_probe_r1_272.py ;;
  we)  run we  e1f783fd93d77b0f1a3fddc183f740285d442b72 qa_probe_r1_we.py ;;
  all)
    run red 771c81c06f6c0b6a5fac65fdf36e51cd52a0f4b8 qa_probe_r1_red.py
    run s1  429a717bedf1f6c28057a5e355970ff351100571 qa_probe_r1_s1.py
    run 272 fc93590030c218934252920a53aa67277f144844 qa_probe_r1_272.py
    run we  e1f783fd93d77b0f1a3fddc183f740285d442b72 qa_probe_r1_we.py
    ;;
  *) echo "unknown selector: $WHAT" >&2; exit 2 ;;
esac
