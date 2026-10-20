#!/bin/bash
# Q1 RC-v4 REAL_TEMP smoke: temp 0700 state/socket, observe-only daemon,
# one status read and deny probes. No production state/socket/service.
set +e
TREE="${1:?candidate worktree required}"
PY="${SERVEROS_TEST_PYTHON:-/data/home/vince/toolchains/serveros-test/bin/python3}"
T="$(mktemp -d /tmp/q1-rcv4-realtemp.XXXXXX)" || exit 2
chmod 700 "$T"
mkdir -p "$T/state" "$T/run/serveros"
chmod 700 "$T/state" "$T/run" "$T/run/serveros"
cat > "$T/registry.yaml" <<'YAML'
schema: serveros/managed-runtime-registry.v1
device_ref: device:q1-rcv4-temp
resources: []
YAML
export XDG_RUNTIME_DIR="$T/run"
SOCKET="$T/run/serveros/serveros.sock"
DAEMON_LOG="$T/daemon.log"
"$PY" "$TREE/bin/serverosd" --registry "$T/registry.yaml" \
  --state-dir "$T/state" --socket "$SOCKET" --interval 3600 \
  >"$DAEMON_LOG" 2>&1 &
PID=$!
for _ in $(seq 1 40); do
  [ -S "$SOCKET" ] && break
  sleep 0.25
done
if [ ! -S "$SOCKET" ]; then
  echo "FAIL daemon socket not created"; cat "$DAEMON_LOG"; kill "$PID" 2>/dev/null; rm -rf "$T"; exit 1
fi
MODE=$(stat -c '%a' "$SOCKET")
echo "socket_mode=$MODE pid=$PID"
[ "$MODE" = "600" ] && echo "PASS socket 0600" || echo "FAIL socket mode $MODE"
"$PY" "$TREE/bin/serverosctl" --json --registry "$T/registry.yaml" status >"$T/status.json" 2>"$T/status.err"
RC=$?
echo "status_rc=$RC"
[ "$RC" = "0" ] && echo "PASS status exit 0" || { echo "FAIL status"; cat "$T/status.err"; }
head -c 300 "$T/status.json" 2>/dev/null; echo
"$PY" - "$SOCKET" <<'PY'
import json, socket, sys
sock_path = sys.argv[1]
def call(method, params):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(sock_path)
    s.sendall((json.dumps({"method": method, "params": params}) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.split(b"\n", 1)[0].decode())
verify = call("agent.lease.authority.verify", {})
print("verify=" + json.dumps(verify, sort_keys=True)[:400])
reconcile = call("agent.dispatch.reconcile", {})
print("reconcile=" + json.dumps(reconcile, sort_keys=True)[:400])
grant = call("agent.grant.issue", {"evidence": {}})
print("grant_issue=" + json.dumps(grant, sort_keys=True)[:400])
PY
echo "---store?---"
if [ -e "$T/state/agent-control/store.json" ]; then echo "STORE_PRESENT"; else echo "STORE_ABSENT"; fi
kill -TERM "$PID" 2>/dev/null
for _ in $(seq 1 40); do kill -0 "$PID" 2>/dev/null || break; sleep 0.25; done
if kill -0 "$PID" 2>/dev/null; then echo "FAIL daemon still alive"; kill -KILL "$PID" 2>/dev/null; else echo "PASS daemon stopped"; fi
rm -rf "$T"
[ -e "$T" ] && echo "FAIL temp remains" || echo "PASS temp removed"
exit 0
