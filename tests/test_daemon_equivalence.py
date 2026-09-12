#!/usr/bin/env python3
"""§7 — direct observation and the live daemon must not disagree.

THE FAILURE THIS EXISTS TO CATCH
--------------------------------
A systemd sandbox put the daemon in a private user namespace. From inside it,
`pgrep` ran, exited 0, and matched nothing. `botmux-host` therefore reported
NOT_RUNNING against a process with two days of uptime, and the daemon could not
tell "I cannot see it" from "it is not running".

That is the systematic version of the bug, and it is the one that matters: the
daemon was not *wrong* about a sample, it was blind to a class of facts, and
every answer it gave was self-consistent. A count that agrees is not evidence
when both sides share the blindness.

So there are two tests here, and they fail differently:

  * the pass-through test is hermetic. It asserts the daemon adds no logic to a
    read — that its answer IS `view.py`'s answer, not a parallel computation
    that could drift from it. This is the structural half, and it holds whether
    or not a daemon happens to be running.

  * the live test compares a running daemon against the CLI on this machine. It
    skips when no daemon is up, because a test that silently passes when the
    thing under test is absent is the defect this whole project keeps finding.

Counts must agree EXACTLY. The process *set* is allowed to differ, because the
two samples are taken at different moments and `top` is a ranking — but the
overlap must be non-trivial, which is what distinguishes "sampled twice" from
"one side is blind".
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_mod = _load("serveros_runtime_registry", "registry.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
view_mod = _load("serveros_runtime_view", "view.py")


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(a_registry(), state_dir=tmp_path,
                             socket_path=tmp_path / "s.sock")


# Every read method, and the `view.py` call it must make: some arguments are
# positional, and encoding which is which is the point — a forwarder that
# silently passed `ref` as a keyword, or dropped it, would still "call view".
FORWARDERS = [
    ("runtime.groups", {}, "runtime_groups", (), {}),
    ("runtime.group", {"ref": "runtime-group:x"}, "runtime_group", ("runtime-group:x",), {}),
    ("runtime.tree", {"ref": "runtime-group:x"}, "runtime_tree", ("runtime-group:x",), {}),
    ("process.top", {}, "process_top", (), {"limit": 10, "by": "cpu"}),
    ("process.top", {"limit": 3, "by": "memory"}, "process_top", (), {"limit": 3, "by": "memory"}),
    ("process.trace", {"pid": 1234}, "process_trace", (1234,), {}),
    ("anomaly.list", {}, "anomaly_list", (), {}),
    ("anomaly.explain", {"subject": "s"}, "anomaly_explain", ("s",), {}),
    ("container.attribution", {}, "container_attribution", (), {}),
]


@pytest.mark.parametrize("method,params,view_name,args,kwargs", FORWARDERS)
def test_each_read_method_is_a_pass_through_to_view(
        daemon, monkeypatch, method, params, view_name, args, kwargs):
    """The daemon's answer must BE `view.py`'s answer, not a second computation.

    `daemon.py` states the rule: "a daemon that answers a question differently
    from the CLI is a daemon whose answer cannot be checked by running the
    CLI". This asserts the mechanism that makes that true rather than trusting
    the comment — if somebody reimplements a read inside the daemon, the
    return value stops being the sentinel and this fails.
    """
    sentinel = object()
    seen = {}

    def recorder(*a, **kw):
        seen["args"] = a
        seen["kwargs"] = kw
        return sentinel

    monkeypatch.setattr(view_mod, view_name, recorder)
    response = daemon.handle({"method": method, "params": params})

    assert response["ok"] is True
    # The value is returned unchanged: no post-processing, no wrapping.
    assert response["result"] is sentinel
    assert seen["args"] == args, f"{method}: positional args"
    # And it is always labelled with who measured it, so a projection can never
    # be mistaken for a direct measurement.
    assert seen["kwargs"].get("answered_by") == daemon_mod.ANSWERED_BY
    for key, value in kwargs.items():
        assert seen["kwargs"].get(key) == value, f"{method}: {key}"


def test_the_budget_read_is_answered_from_the_daemons_own_state(daemon, monkeypatch):
    """`runtime.budget` is the one read that carries live state, not just a ref.

    It must ask about the retry budget `this` daemon writes, or an operator
    asking "how many restarts are left" gets an answer about a different
    process's budget — a number that is real and about the wrong thing.
    """
    sentinel = object()
    seen = {}

    def recorder(*a, **kw):
        seen["args"], seen["kwargs"] = a, kw
        return sentinel

    monkeypatch.setattr(view_mod, "retry_budget", recorder)
    response = daemon.handle({"method": "runtime.budget", "params": {}})

    assert response["result"] is sentinel
    assert seen["kwargs"]["state_dir"] == daemon.state_dir
    assert seen["kwargs"]["registry"] is daemon.registry
    assert seen["kwargs"]["answered_by"] == daemon_mod.ANSWERED_BY


def test_an_unknown_method_is_refused_by_name(daemon):
    """`UNKNOWN_METHOD` names what is served, so a caller can correct itself."""
    response = daemon.handle({"method": "runtime.nonexistent", "params": {}})
    assert response["ok"] is False
    assert response["error"]["code"] == "UNKNOWN_METHOD"
    assert "runtime.groups" in response["error"]["message"]


# ---------------------------------------------------------------------------
# the live half
# ---------------------------------------------------------------------------


def _socket_path() -> Path:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / "serveros/serveros.sock"


def _ipc(method: str, params=None, timeout: float = 60.0):
    path = _socket_path()
    if not path.exists():
        pytest.skip(f"no live daemon: {path} does not exist")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(path))
        connection.sendall((json.dumps({"method": method,
                                        "params": params or {}}) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = connection.recv(1 << 20)
            if not chunk:
                break
            buffer += chunk
    finally:
        connection.close()
    return json.loads(buffer.split(b"\n")[0])


def _cli(*args):
    result = subprocess.run([sys.executable, str(REPO / "bin/serverosctl"), *args, "--json"],
                            capture_output=True, text=True, timeout=300, cwd=REPO)
    assert result.returncode == 0, result.stderr[-500:]
    return json.loads(result.stdout)


def _rows(payload, *keys):
    for key in keys:
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []


@pytest.mark.parametrize("method,cli_args,keys", [
    ("runtime.list", ("runtime", "list"), ("resources",)),
    ("runtime.groups", ("runtime", "groups"), ("groups",)),
    ("container.attribution", ("container", "attribution"), ("containers",)),
    ("resource.summary", ("resource", "summary"), ("disk", "roles")),
])
def test_the_daemon_and_the_cli_count_the_same_things(method, cli_args, keys):
    """Exact agreement on counts.

    Not a tolerance. A daemon that sees a subset of the host reports a
    *smaller* number and looks healthy; the whole point of the private-userns
    defect was that nothing in its own output was internally inconsistent.
    """
    live = _ipc(method)["result"]
    direct = _cli(*cli_args)
    assert len(_rows(live, *keys)) == len(_rows(direct, *keys)), (
        f"{method}: daemon and CLI disagree on count")


def test_the_daemon_is_not_blind_to_a_class_of_processes():
    """The process set may churn; it may not collapse.

    `top` is a ranking, so the two samples legitimately differ by a few rows —
    this machine is shared and CPU changes between them. What must NOT happen
    is the daemon seeing a tiny fraction of what the CLI sees, which is what
    the namespace defect produced: 4 visible where 63 existed.
    """
    live = _rows(_ipc("process.top", {"limit": 50})["result"], "processes", "rows")
    direct = _rows(_cli("process", "top", "--limit", "50"), "processes", "rows")
    if not direct:
        pytest.skip("no processes visible to compare")
    daemon_pids = {row.get("pid") for row in live}
    direct_pids = {row.get("pid") for row in direct}
    overlap = len(daemon_pids & direct_pids)
    # Half is a deliberately loose floor: it is far below the churn two
    # adjacent samples produce, and far above the near-zero the blindness bug
    # produced.
    assert overlap >= max(1, len(direct_pids) // 2), (
        f"daemon and CLI process sets barely overlap: "
        f"daemon={len(daemon_pids)} direct={len(direct_pids)} overlap={overlap}")
