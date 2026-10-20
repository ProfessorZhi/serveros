#!/bin/bash
# QA2 reproducible runs for S1 #358 and R1 #356.
#
# It exports the exact PR heads with `git archive`, never enters the authors'
# worktrees, and bounds every run. The probes are read-only against the
# exported snapshots; their logs/evidence stay under QA2_WORKDIR.
#
# Usage:
#   ./qa2-run.sh export
#   ./qa2-run.sh s1
#   ./qa2-run.sh r1
#   ./qa2-run.sh all
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(git -C "$HERE" rev-parse --show-toplevel)"
WORK="${QA2_WORKDIR:-/tmp/qa2-independent-security}"

S1_SHA="b183aab0b8913ed36e5de539c50223d3eaf508f1"
R1_SHA="b4020b73e6b7ec2b1f6301471a9872f8864c1643"
S1_PULL="pull/358/head"
R1_PULL="pull/356/head"

PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
PROBE_PY="${QA2_PYTHON:-python3}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$WORK/logs/$STAMP"
mkdir -p "$WORK/checkouts" "$LOG" || exit 2

if systemd-run --user --scope -q -- true >/dev/null 2>&1; then
  GUARD=(systemd-run --user --scope -q \
    -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
    nice -n 10 timeout 120)
else
  GUARD=(nice -n 10 timeout 120)
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
  if git -C "$REPO" cat-file -e "$sha^{commit}" 2>/dev/null; then
    return 0
  fi
  say "fetching $pull for $sha"
  git -C "$REPO" fetch --quiet origin "$pull" || return 1
  local got
  got="$(git -C "$REPO" rev-parse FETCH_HEAD)"
  if [ "$got" != "$sha" ]; then
    say "SHA MISMATCH: $pull -> $got, expected $sha" >&2
    return 1
  fi
  git -C "$REPO" cat-file -e "$sha^{commit}"
}

archive_one() {
  local sha="$1" dir="$2"
  if [ -f "$dir/.snapshot-sha" ] && \
     [ "$(cat "$dir/.snapshot-sha")" = "$sha" ]; then
    say "reuse $dir"
    return 0
  fi
  if [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    say "refusing to overwrite non-QA snapshot dir: $dir" >&2
    return 1
  fi
  mkdir -p "$dir" || return 1
  git -C "$REPO" archive "$sha" | tar -x -C "$dir" || return 1
  printf '%s' "$sha" >"$dir/.snapshot-sha"
  say "archived $sha -> $dir"
}

export_snapshots() {
  ensure_commit "$S1_SHA" "$S1_PULL" || return 1
  ensure_commit "$R1_SHA" "$R1_PULL" || return 1
  archive_one "$S1_SHA" "$WORK/checkouts/s1-$S1_SHA" || return 1
  archive_one "$R1_SHA" "$WORK/checkouts/r1-$R1_SHA" || return 1
}

S1_DIR="$WORK/checkouts/s1-$S1_SHA"
R1_DIR="$WORK/checkouts/r1-$R1_SHA"

s1() {
  export_snapshots || return 1
  say "== S1 #358 @ $S1_SHA"
  local rc_author=0 rc_controls=0 rc_signed=0 rc_findings=0
  ( cd "$S1_DIR" && run s1-author \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PY" -m pytest -q \
      -p no:cacheprovider \
      tests/test_owner_policy_binding.py \
      tests/test_owner_policy_reduced_gate.py \
      tests/test_v1_grant_epoch_authority.py \
      tests/test_v1_grant_os_principal_schema.py \
      tests/test_fleet_owner_grant.py \
      tests/test_fleet_owner_grant_template.py ) || rc_author=$?

  run s1-controls env PYTHONDONTWRITEBYTECODE=1 S1_SNAPSHOT="$S1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/s1_probe.py" --mode controls \
    || rc_controls=$?

  if "$PROBE_PY" -c 'import cryptography' >/dev/null 2>&1; then
    run s1-signed env PYTHONDONTWRITEBYTECODE=1 S1_SNAPSHOT="$S1_DIR" \
      "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/s1_signed_probe.py" \
      || rc_signed=$?
  else
    say "  s1-signed BLOCKED_CRYPTOGRAPHY (no cryptography in $PROBE_PY)"
    rc_signed=2
  fi

  run s1-findings env PYTHONDONTWRITEBYTECODE=1 S1_SNAPSHOT="$S1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/s1_probe.py" --mode findings \
    || rc_findings=$?

  [ "$rc_author" -eq 0 ] && [ "$rc_controls" -eq 0 ] && [ "$rc_signed" -eq 0 ] \
    && return 0
  return 1
}

r1() {
  export_snapshots || return 1
  say "== R1 #356 @ $R1_SHA"
  local rc_author=0 rc_controls=0 rc_findings=0
  ( cd "$R1_DIR" && run r1-author \
      env PYTHONDONTWRITEBYTECODE=1 "${GUARD[@]}" "$PY" -m pytest -q \
      -p no:cacheprovider \
      tests/test_fleet_v1_runtime_reconcile_source.py ) || rc_author=$?

  run r1-controls env PYTHONDONTWRITEBYTECODE=1 R1_SNAPSHOT="$R1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/r1_probe.py" --mode controls \
    || rc_controls=$?

  run r1-findings env PYTHONDONTWRITEBYTECODE=1 R1_SNAPSHOT="$R1_DIR" \
    "${GUARD[@]}" "$PROBE_PY" "$HERE/probes/r1_probe.py" --mode findings \
    || rc_findings=$?

  [ "$rc_author" -eq 0 ] && [ "$rc_controls" -eq 0 ] && return 0
  return 1
}

summary() {
  say ""
  say "== evidence ($LOG)"
  local f
  for f in "$LOG"/*.rc; do
    [ -e "$f" ] || continue
    printf '%-14s rc=%s\n' "$(basename "$f" .rc)" "$(cat "$f")"
  done
  say ""
  say "A findings rc of 1 means the adversarial probe confirmed the"
  say "documented gaps. A controls rc of 0 means the fail-closed controls held."
}

case "${1:-all}" in
  export) export_snapshots ;;
  s1) s1; r=$?; summary; exit "$r" ;;
  r1) r1; r=$?; summary; exit "$r" ;;
  all)
    export_snapshots || exit 1
    s1; rc1=$?
    r1; rc2=$?
    summary
    [ "$rc1" -eq 0 ] && [ "$rc2" -eq 0 ] || exit 1
    ;;
  *) say "unknown selector: $1" >&2; exit 2 ;;
esac
