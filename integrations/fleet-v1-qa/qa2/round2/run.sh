#!/bin/bash
# QA2 round-2 reproducible runs for R1 #365 and S1 #366.
# Exports the exact PR heads with `git archive`; never enters author
# worktrees; bounds every run. The output rc is evidence, not a product gate.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${QA2_WORKDIR:-/tmp/qa2-round2}"

R1_SHA="d043521c516033f00e2315ace74858b3aa5cef87"
S1_SHA="471e300707cc68a669ca3e0c8ff81aecdc278805"
R1_PULL="pull/365/head"
S1_PULL="pull/366/head"

PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
PROBE_PY="${QA2_PYTHON:-python3}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$WORK/logs/$STAMP"
mkdir -p "$WORK/checkouts" "$LOG" || exit 2

if systemd-run --user --scope -q -- true >/dev/null 2>&1; then
  GUARD=(systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
    nice -n 10 timeout 180)
else
  GUARD=(nice -n 10 timeout 180)
fi

say() { printf '%s\n' "$*"; }
run() {
  local name="$1"; shift
  "$@" >"$LOG/$name.log" 2>&1
  local rc=$?
  printf '%s' "$rc" >"$LOG/$name.rc"
  say "  $name rc=$rc  ($LOG/$name.log)"
  return "$rc"
}

ensure_commit() {
  local sha="$1" pull="$2"
  git -C "$REPO" cat-file -e "$sha^{commit}" 2>/dev/null && return 0
  say "fetch $pull"
  git -C "$REPO" fetch --quiet origin "$pull" || return 1
  [ "$(git -C "$REPO" rev-parse FETCH_HEAD)" = "$sha" ] || return 1
  git -C "$REPO" cat-file -e "$sha^{commit}"
}

archive_one() {
  local sha="$1" dir="$2"
  if [ -f "$dir/.snapshot-sha" ] && [ "$(cat "$dir/.snapshot-sha")" = "$sha" ]; then
    say "reuse $dir"; return 0
  fi
  if [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    say "refusing to overwrite non-QA snapshot dir: $dir" >&2; return 1
  fi
  mkdir -p "$dir" || return 1
  git -C "$REPO" archive "$sha" | tar -x -C "$dir" || return 1
  printf '%s' "$sha" >"$dir/.snapshot-sha"
  say "archived $sha -> $dir"
}

export_snapshots() {
  ensure_commit "$R1_SHA" "$R1_PULL" || return 1
  ensure_commit "$S1_SHA" "$S1_PULL" || return 1
  archive_one "$R1_SHA" "$WORK/checkouts/r1-$R1_SHA" || return 1
  archive_one "$S1_SHA" "$WORK/checkouts/s1-$S1_SHA" || return 1
}

R1_DIR="$WORK/checkouts/r1-$R1_SHA"
S1_DIR="$WORK/checkouts/s1-$S1_SHA"

r1() {
  export_snapshots || return 1
  say "== R1 #365 @ $R1_SHA"
  ( cd "$R1_DIR" && run r1-author-102 \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PY" -m pytest -q \
      -p no:cacheprovider \
      tests/test_fleet_v1_runtime_observation_contract.py \
      tests/test_fleet_v1_runtime_reconcile_source.py ) || true
  run r1-v2-25 env PYTHONDONTWRITEBYTECODE=1 QA_TREE="$R1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/qa_probe_r17_r1_361.py" || true
  run r1-controls env PYTHONDONTWRITEBYTECODE=1 R1_SNAPSHOT="$R1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/r1_365_observation_probe.py" \
    --mode controls || true
  run r1-findings env PYTHONDONTWRITEBYTECODE=1 R1_SNAPSHOT="$R1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/r1_365_observation_probe.py" \
    --mode findings || true
}

s1() {
  export_snapshots || return 1
  say "== S1 #366 @ $S1_SHA"
  ( cd "$S1_DIR" && run s1-366-contract \
      env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$S1_DIR" "${GUARD[@]}" "$PY" \
      -m pytest -q -rs -p no:cacheprovider \
      tests/test_owner_atomic_commit_contract.py ) || true
  ( cd "$S1_DIR" && run s1-366-364-suites \
      env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$S1_DIR" "${GUARD[@]}" "$PY" \
      -m pytest -q -rs -p no:cacheprovider \
      tests/test_owner_commit_fence.py tests/test_owner_policy_binding.py \
      tests/test_v1_grant_epoch_authority.py \
      tests/test_v1_grant_os_principal_schema.py ) || true
  ( cd "$S1_DIR" && run s1-366-combined \
      env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$S1_DIR" "${GUARD[@]}" "$PY" \
      -m pytest -q -rs -p no:cacheprovider \
      tests/test_owner_atomic_commit_contract.py \
      tests/test_owner_commit_fence.py tests/test_owner_policy_binding.py \
      tests/test_v1_grant_epoch_authority.py \
      tests/test_v1_grant_os_principal_schema.py ) || true
  ( cd "$S1_DIR" && run s1-366-probe-controls \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PROBE_PY" \
      tests/probes/s1_owner_atomic_commit_probe.py --mode controls ) || true
  ( cd "$S1_DIR" && run s1-366-probe-positive \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PROBE_PY" \
      tests/probes/s1_owner_atomic_commit_probe.py --mode positive ) || true
  ( cd "$S1_DIR" && run s1-366-probe-all \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PROBE_PY" \
      tests/probes/s1_owner_atomic_commit_probe.py --mode all ) || true
  for i in 1 2 3; do
    run "s1-366-seam-probe-$i" env PYTHONDONTWRITEBYTECODE=1 QA_TREE="$S1_DIR" \
      "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/qa_probe_r19_seam_364.py" || true
  done
  run s1-366-revoked-ttl env PYTHONDONTWRITEBYTECODE=1 S1_SNAPSHOT="$S1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/s1_366_revoked_ttl_probe.py" || true
}

summary() {
  say ""
  say "== evidence ($LOG)"
  local f
  for f in "$LOG"/*.rc; do
    [ -e "$f" ] || continue
    printf '%-24s rc=%s\n' "$(basename "$f" .rc)" "$(cat "$f")"
  done
  say ""
  say "Pinned expectations: author/controls/seam/revoked rc=0;"
  say "#365 findings rc=1; #366 positive/all rc=2. A different rc is"
  say "evidence of a changed snapshot or a fixed/regressed probe, not silently a pass."
}

case "${1:-all}" in
  export) export_snapshots ;;
  r1) r1; r=$?; summary; exit "$r" ;;
  s1) s1; r=$?; summary; exit "$r" ;;
  all)
    export_snapshots || exit 1
    r1; rc1=$?
    s1; rc2=$?
    summary
    [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] || exit 1
    ;;
  *) say "unknown selector: $1" >&2; exit 2 ;;
esac
