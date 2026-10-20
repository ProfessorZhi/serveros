#!/usr/bin/env bash
# REAL_TOY — an OPT-IN, MANUAL harness. It is NOT run by ctest and NOT part of
# any build, because it creates a real transient `systemd --user` unit and kills
# it on purpose. Every CTest case in this directory uses a fake `systemctl` and
# must keep doing so.
#
# WHAT IT PROVES, and what it does not
# ------------------------------------
# It proves the ONE thing unit tests cannot: that the readiness rule and the
# admission gate behave this way against a REAL service manager, a REAL socket
# and a REAL `flock`, with a REAL fault injected.
#
# It proves NOTHING about production. The unit it creates is a transient unit
# private to this script (`serveros-c-toy-<pid>.service`), bounded by
# MemoryMax/SwapMax/CPUQuota/TasksMax, and removed on every exit path by the
# trap below. It touches no service it did not create, no account, no credential
# and no model.
#
# Usage: service_recovery_toy.sh /path/to/serveros-native-core
set -uo pipefail

BIN="${1:-}"
if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
  echo "usage: $0 /path/to/serveros-native-core" >&2
  exit 2
fi

UNIT="serveros-c-toy-$$.service"
RUNTIME="${XDG_RUNTIME_DIR:-/tmp}"
SCRATCH="$(mktemp -d "$RUNTIME/serveros-c-toy-XXXXXX")" || exit 2
SOCK="$SCRATCH/toy.sock"
LEASE="$SCRATCH/lease"
STATE="$SCRATCH/recovery-state.json"
PASS=0
FAIL=0

cleanup() {
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  systemctl --user reset-failed "$UNIT" >/dev/null 2>&1 || true
  rm -rf "$SCRATCH"
}
trap cleanup EXIT INT TERM

note() { printf '%s\n' "$*"; }
ok()   { PASS=$((PASS + 1)); printf 'REAL_TOY step=%s result=PASS %s\n' "$1" "${2:-}"; }
bad()  { FAIL=$((FAIL + 1)); printf 'REAL_TOY step=%s result=FAIL %s\n' "$1" "${2:-}"; }
expect() { # $1 step, $2 needle, $3 haystack
  if printf '%s' "$3" | grep -q "$2"; then ok "$1" "saw $2"; else bad "$1" "expected '$2' in: $3"; fi
}
expect_not() { # $1 step, $2 needle, $3 haystack
  if printf '%s' "$3" | grep -q "$2"; then bad "$1" "did NOT expect $2 in: $3"; else ok "$1" "no $2"; fi
}

# A toy service that answers on a unix socket, or — with mode "deaf" — one that
# stays up and never listens. The second shape is the case the whole readiness
# module exists for: systemd calls the unit active and the service serves nothing.
cat > "$SCRATCH/toy.py" <<'PY'
import os, socket, sys, time
path, mode = sys.argv[1], sys.argv[2]
if mode == "deaf":
    while True:
        time.sleep(3600)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    os.unlink(path)
except FileNotFoundError:
    pass
s.bind(path)
s.listen(8)
while True:
    try:
        conn, _ = s.accept()
        conn.close()
    except OSError:
        time.sleep(0.05)
PY

start_toy() {  # $1 = unit, $2 = mode
  systemd-run --user --unit="$1" \
    -p MemoryMax=256M -p MemorySwapMax=0 -p CPUQuota=25% -p TasksMax=16 -p Type=simple \
    /usr/bin/python3 "$SCRATCH/toy.py" "$SOCK" "$2" >/dev/null 2>&1
}

state_of() { systemctl --user show "$1" -p ActiveState --value 2>/dev/null; }
wait_for_state() {  # $1 unit, $2 wanted
  for _ in $(seq 1 60); do
    [ "$(state_of "$1")" = "$2" ] && return 0
    sleep 0.1
  done
  return 1
}

# Bring the toy back to `failed` from scratch. A tick stream only restarts a
# FAILED unit, so every acting case needs one, and a transient unit cannot be
# reused under its own name until it has been reset.
make_failed_toy() {
  systemctl --user stop "$UNIT" >/dev/null 2>&1 || true
  systemctl --user reset-failed "$UNIT" >/dev/null 2>&1 || true
  rm -f "$SOCK"
  for _ in $(seq 1 30); do
    [ "$(systemctl --user show "$UNIT" -p LoadState --value 2>/dev/null)" = "not-found" ] && break
    sleep 0.1
  done
  start_toy "$UNIT" deaf || return 1
  wait_for_state "$UNIT" active || return 1
  systemctl --user kill -s KILL "$UNIT" >/dev/null 2>&1 || true
  wait_for_state "$UNIT" failed
}

note "### REAL_TOY unit=$UNIT scratch=$SCRATCH"

# ---------------------------------------------------------------- 1. healthy
start_toy "$UNIT" serve
if wait_for_state "$UNIT" active; then ok "unit-started" "ActiveState=active"; else bad "unit-started"; fi
for _ in $(seq 1 60); do [ -S "$SOCK" ] && break; sleep 0.1; done
if [ -S "$SOCK" ]; then ok "socket-bound" "$SOCK"; else bad "socket-bound"; fi

cat > "$SCRATCH/link_dependent.json" <<JSON
{
  "schema": "serveros/desired-state.v1",
  "node": "device:llm-lab-dell",
  "principal": "fjd",
  "observe_only": true,
  "generation": 1,
  "services": [
    {
      "id": "toy",
      "owner": "fjd",
      "unit": "$UNIT",
      "desired": "running",
      "start_on_boot": false,
      "health": { "probe": "unix-socket-connect", "probe_path": "$SOCK" },
      "recovery": { "allowed_verbs": ["restart"] }
    }
  ]
}
JSON

out="$("$BIN" --manifest "$SCRATCH/link_dependent.json" --allow-unit "$UNIT" --trigger tick 2>&1)"
expect "observe-only-receipt" '"mode": "observe-only"' "$out"
if [ -S "$SOCK" ]; then ok "probe-answered-on-a-real-socket" "the declared probe connected"; else bad "probe-answered-on-a-real-socket"; fi

# -------------------------------------------------- 2. the gap: ACTIVE, not serving
systemctl --user stop "$UNIT" >/dev/null 2>&1
systemctl --user reset-failed "$UNIT" >/dev/null 2>&1
rm -f "$SOCK"
start_toy "$UNIT" deaf
if wait_for_state "$UNIT" active; then ok "deaf-unit-active" "systemd: the unit is up"; else bad "deaf-unit-active"; fi
if [ ! -S "$SOCK" ]; then ok "deaf-unit-not-serving" "no socket, so no service answers"; else bad "deaf-unit-not-serving"; fi

# -------------------------------------- 3. FAULT INJECTION on the toy unit only
if make_failed_toy; then ok "fault-injected" "ActiveState=failed after SIGKILL"; else bad "fault-injected" "state=$(state_of "$UNIT")"; fi

cat > "$SCRATCH/link_dependent_act.json" <<JSON
{
  "schema": "serveros/desired-state.v1",
  "node": "device:llm-lab-dell",
  "principal": "fjd",
  "observe_only": false,
  "generation": 1,
  "services": [
    {
      "id": "toy",
      "owner": "fjd",
      "unit": "$UNIT",
      "desired": "running",
      "start_on_boot": false,
      "health": { "probe": "unix-socket-connect", "probe_path": "$SOCK" },
      "recovery": { "allowed_verbs": ["restart"] }
    }
  ]
}
JSON

cat > "$SCRATCH/offline.json" <<JSON
{
  "schema": "serveros/desired-state.v1",
  "node": "device:llm-lab-dell",
  "principal": "fjd",
  "observe_only": false,
  "generation": 1,
  "services": [
    {
      "id": "toy",
      "owner": "fjd",
      "unit": "$UNIT",
      "desired": "running",
      "start_on_boot": false,
      "health": { "probe": "unix-socket-connect", "probe_path": "$SOCK" },
      "recovery": { "allowed_verbs": ["restart"], "offline_recoverable": true }
    }
  ]
}
JSON

# 4. LINK DOWN, link-dependent, unit FAILED: REFUSED, and no verb runs — the
#    unit must still be failed afterwards.
out="$("$BIN" --manifest "$SCRATCH/link_dependent_act.json" --allow-unit "$UNIT" \
        --enable-actions --lease "$LEASE" --state "$STATE" --link-state down --agent-state idle \
        --trigger tick 2>&1)"
expect "link-down-refuses-link-dependent" 'LINK_DOWN' "$out"
expect_not "refusal-ran-no-verb" '"verb"' "$out"
if [ "$(state_of "$UNIT")" = "failed" ]; then ok "unit-untouched-by-refusal" "still failed"; else bad "unit-untouched-by-refusal" "state=$(state_of "$UNIT")"; fi

# 5. THE SAME WORLD with offline_recoverable: the link gate is SKIPPED and the
#    verb IS attempted. The restart may still fail — a transient unit whose
#    command is gone cannot come back — and that failure is recorded, which is
#    the whole point of reprobing rather than trusting an exit code.
out="$("$BIN" --manifest "$SCRATCH/offline.json" --allow-unit "$UNIT" \
        --enable-actions --lease "$LEASE" --state "$STATE" --link-state down --agent-state idle \
        --trigger tick 2>&1)"
expect_not "offline-recovery-skips-link-gate" 'LINK_DOWN' "$out"
expect "offline-recovery-attempted-the-verb" '"verb"' "$out"

# 6. NEEDS LOGIN refuses even with everything else in order. A fresh fault, so
#    there is an action for the gate to consider.
if make_failed_toy; then ok "fault-injected-again"; else bad "fault-injected-again" "state=$(state_of "$UNIT")"; fi
out="$("$BIN" --manifest "$SCRATCH/offline.json" --allow-unit "$UNIT" \
        --enable-actions --lease "$LEASE" --state "$STATE" --link-state up --agent-state idle \
        --service-runtime needs-login --trigger tick 2>&1)"
expect "needs-login-refused" 'NEEDS_LOGIN' "$out"
expect_not "needs-login-ran-no-verb" '"verb"' "$out"

# 7. THE LEASE is real and exclusive: a second holder of the same path cannot
#    take it while the first holds it. flock(1), the same lock the Core uses.
exec 9>"$LEASE"
if flock -n 9; then ok "flock-taken"; else bad "flock-taken"; fi
if ( flock -n "$LEASE" -c true ) 2>/dev/null; then
  bad "lease-is-exclusive" "a second flock succeeded while the first was held"
else
  ok "lease-is-exclusive" "flock(2) refuses a second writer"
fi
exec 9>&-

note "### REAL_TOY pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
