#!/bin/bash
# QA2 round-3 reproducible runs for S1 Windows collection fix #371.
# Exports #369 and #371 exact heads with `git archive`; never enters author
# worktrees; bounds every run. REAL Windows is NOT_RUN; this script only
# produces LOCAL + SYNTHETIC_WIN32 evidence.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${QA2_WORKDIR:-/tmp/qa2-round3}"
BASE_SHA="a6663075a886093b5ea2e28cd6baf8ca63365f2f"
FIX_SHA="dbb1f9f1f7ca5fb38a1591b4171504b21478e71a"
BASE_PULL="pull/369/head"
FIX_PULL="pull/371/head"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$WORK/logs/$STAMP"
mkdir -p "$WORK/checkouts" "$LOG" "$WORK/tmp" || exit 2

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
  say "  $name rc=$rc"
  return "$rc"
}

ensure_commit() {
  local sha="$1" pull="$2"
  git -C "$REPO" cat-file -e "$sha^{commit}" 2>/dev/null && return 0
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
}

export_snapshots() {
  ensure_commit "$BASE_SHA" "$BASE_PULL" || return 1
  ensure_commit "$FIX_SHA" "$FIX_PULL" || return 1
  archive_one "$BASE_SHA" "$WORK/checkouts/base-$BASE_SHA" || return 1
  archive_one "$FIX_SHA" "$WORK/checkouts/fix-$FIX_SHA" || return 1
}

BASE="$WORK/checkouts/base-$BASE_SHA"
FIX="$WORK/checkouts/fix-$FIX_SHA"

pytest_set() {
  local snap="$1" name="$2"; shift 2
  ( cd "$snap" && run "$name" env PYTHONDONTWRITEBYTECODE=1 \
      PYTHONPATH="$snap" "${GUARD[@]}" "$PY" -m pytest -q -rs \
      -p no:cacheprovider "$@" ) || true
}

probe_one() {
  local name="$1" root="$2" file="$3"
  run "$name" env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PY" \
    "$HERE/probes/s1_win32_collection_probe.py" "$root" "$file" || true
}

base_run() {
  export_snapshots || return 1
  say "== #369 base $BASE_SHA"
  pytest_set "$BASE" base-three \
    tests/test_owner_policy_binding.py tests/test_owner_commit_fence.py \
    tests/test_owner_policy_reduced_gate.py
  pytest_set "$BASE" base-broad \
    tests/test_owner_policy_binding.py tests/test_owner_commit_fence.py \
    tests/test_owner_policy_reduced_gate.py \
    tests/test_v1_grant_epoch_authority.py \
    tests/test_v1_grant_os_principal_schema.py \
    tests/test_serveros_lease_authority.py tests/test_fleet_owner_grant.py \
    tests/test_fleet_owner_grant_template.py
  probe_one base-red-policy "$BASE" tests/test_owner_policy_binding.py
  probe_one base-red-fence "$BASE" tests/test_owner_commit_fence.py
  probe_one base-red-reduced "$BASE" tests/test_owner_policy_reduced_gate.py
}

fix_run() {
  export_snapshots || return 1
  say "== #371 fix $FIX_SHA"
  pytest_set "$FIX" fix-three \
    tests/test_owner_policy_binding.py tests/test_owner_commit_fence.py \
    tests/test_owner_policy_reduced_gate.py
  pytest_set "$FIX" fix-adapter tests/test_owner_commit_fence_adapter.py
  pytest_set "$FIX" fix-broad \
    tests/test_owner_policy_binding.py tests/test_owner_commit_fence.py \
    tests/test_owner_policy_reduced_gate.py \
    tests/test_owner_commit_fence_adapter.py \
    tests/test_v1_grant_epoch_authority.py \
    tests/test_v1_grant_os_principal_schema.py \
    tests/test_serveros_lease_authority.py tests/test_fleet_owner_grant.py \
    tests/test_fleet_owner_grant_template.py
  probe_one fix-green-policy "$FIX" tests/test_owner_policy_binding.py
  probe_one fix-green-fence "$FIX" tests/test_owner_commit_fence.py
  probe_one fix-green-reduced "$FIX" tests/test_owner_policy_reduced_gate.py
  probe_one fix-green-adapter "$FIX" tests/test_owner_commit_fence_adapter.py
}

masking_run() {
  local tmp="$WORK/tmp"
  mkdir -p "$tmp"
  cat >"$tmp/qa2_skip_module.py" <<'PY'
import pytest
pytest.skip("QA2 expected module skip", allow_module_level=True)
PY
  cat >"$tmp/qa2_runtime_error_module.py" <<'PY'
raise RuntimeError("QA2 non-skip failure")
PY
  cat >"$tmp/qa2_import_error_module.py" <<'PY'
import qa2_definitely_missing_module
PY
  probe_one probe-masking-skip "$tmp" "$tmp/qa2_skip_module.py"
  probe_one probe-masking-runtime-error "$tmp" "$tmp/qa2_runtime_error_module.py"
  probe_one probe-masking-import-error "$tmp" "$tmp/qa2_import_error_module.py"
}

summary() {
  say ""
  say "== evidence ($LOG)"
  local f
  for f in "$LOG"/*.rc; do
    [ -e "$f" ] || continue
    printf '%-32s rc=%s\n' "$(basename "$f" .rc)" "$(cat "$f")"
  done
  say ""
  say "Expected: base/fix three+adapter+broad 0; base-red-* 1;"
  say "fix-green-* 0; masking skip 0; masking RuntimeError/ImportError 1."
  say "REAL_WINDOWS_NOT_RUN: no result here is a real Windows pass."
}

check() {
  local bad=0 f want got
  while read -r f want; do
    [ -e "$LOG/$f.rc" ] || continue
    got="$(cat "$LOG/$f.rc")"
    if [ "$got" != "$want" ]; then
      say "MISMATCH $f rc=$got expected=$want"; bad=1
    fi
  done <<EOF
base-three 0
base-broad 0
base-red-policy 1
base-red-fence 1
base-red-reduced 1
fix-three 0
fix-adapter 0
fix-broad 0
fix-green-policy 0
fix-green-fence 0
fix-green-reduced 0
fix-green-adapter 0
probe-masking-skip 0
probe-masking-runtime-error 1
probe-masking-import-error 1
EOF
  return "$bad"
}

case "${1:-all}" in
  export) export_snapshots ;;
  base) base_run; summary ;;
  fix) fix_run; summary ;;
  masking) masking_run; summary ;;
  all)
    export_snapshots || exit 1
    base_run; fix_run; masking_run
    summary
    check || exit 1
    ;;
  *) say "unknown selector: $1" >&2; exit 2 ;;
esac
