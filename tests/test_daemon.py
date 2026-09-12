#!/usr/bin/env python3
"""Tests for serverosd — the semantic controller.

Two things are under test here and they are different in kind.

The first is behaviour: what the daemon observes, what it persists, what it
refuses to act on. Those tests are ordinary.

The second is the *shape of the gate*. This is the only component in the tree
with a code path that changes something on the machine, so several tests below
assert a negative: that the actuator is unreachable from the object every probe
holds, that the unit file does not turn mutation on at boot, that a budget
exhausts instead of looping. A gate is only worth as much as the test that
fails when somebody opens it, and every one of these exists because of a
specific way it could be opened by accident.

Hermetic: no docker, no systemd, no socket on the real machine, no network.
"""
from __future__ import annotations

import importlib.util
import json
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
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")

Registry = registry_mod.Registry
Daemon = daemon_mod.Daemon
Budget = daemon_mod.Budget
Actuator = daemon_mod.Actuator
SubprocessRunner = daemon_mod.SubprocessRunner


# ---------------------------------------------------------------------------
# Fixtures — a registry, a probe host, an inventory, a runner. All fakes.
# ---------------------------------------------------------------------------


def a_resource(resource_id="thing", **overrides):
    record = {
        "id": resource_id,
        "kind": "container",
        "owner": {"type": "project", "ref": "project:litellm"},
        "scope": "user",
        "lifecycle": {"class": "task-scoped"},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "probes": [{"id": "state", "kind": "container-state", "axis": "liveness",
                    "container": resource_id}],
    }
    record.update(overrides)
    return record


def an_automatable_resource(resource_id="down", **overrides):
    """A resource that satisfies all seven of §10's conjuncts.

    `a_resource`'s defaults deliberately do not: they are task-scoped, have no
    functional probe, declare no reconcile policy and carry no budget. The old
    definition of `is_automatable` (protected? scoped? owned? desired?) called
    such a thing automatable; §10 does not, and it now takes four more
    declarations to reach the actuator.

    That is the change, and it is why this helper exists rather than the
    defaults being widened: a test that wants to reach an actuator has to say
    what the resource is for, how its recovery would be verified and what
    bounds it. Widening `a_resource` would have kept the old tests passing
    while quietly meaning something else.
    """
    overrides.setdefault("lifecycle", {"class": "persistent"})
    overrides.setdefault("probes", [
        {"id": "state", "kind": "container-state", "axis": "liveness",
         "container": resource_id},
        {"id": "health", "kind": "container-healthcheck", "axis": "functional",
         "container": resource_id},
    ])
    overrides.setdefault("recovery", {"model": "restart"})
    overrides.setdefault("reconcile", {"mode": "restart", "max_attempts": 3,
                                       "backoff": [60, 300, 900]})
    return a_resource(resource_id, **overrides)


def a_registry(*resources, **kw):
    return Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": list(resources),
        **kw,
    })


class FakeProbeHost:
    """A host that answers only what a test configured.

    Anything not configured raises, so a daemon test cannot pass because the
    fake happened to return something plausible — the failure mode that let
    the compose-label bug through a live run.
    """

    name = "fake-probe-host"

    def __init__(self, containers=None):
        self.containers = containers or {}

    def now(self):
        return "2026-09-12T00:00:00+00:00"

    def docker_inspect(self, name):
        if name not in self.containers:
            raise probes_mod.ProbeUnavailable(f"no such container: {name}")
        return self.containers[name]

    def systemd_user_unit(self, unit):
        raise probes_mod.ProbeUnavailable(f"no such unit: {unit}")

    def process_pids(self, pattern):
        return []


def running_container(name="thing"):
    return {name: {"State": {"Status": "running", "StartedAt": "2026-09-12T00:00:00Z"},
                   "RestartCount": 0, "State.OOMKilled": False}}


class FakeInventory:
    def __init__(self, containers=None):
        self._containers = containers or []

    def docker_list(self, all_=True):
        return {"count": len(self._containers), "containers": self._containers}

    def docker_volumes(self):
        return {"count": 0, "volumes": []}

    def disk_status(self):
        return {"roles": [{"role": "ROOT_FS", "used_percent": 44.4}], "others": []}

    def pressure(self):
        return {"advisory_only": True, "notes": []}

    def processes_top(self, limit=15, by="cpu"):
        return {"sorted_by": by, "rows": [], "scope_note": "fake"}

    def host_summary(self):
        return {"cpu": {"model": "fake"}, "memory": {"total_gib": 31.0}}


class FakeRunner:
    """Records the commands it is asked to run and returns a configured result."""

    def __init__(self, exit_code=0):
        self.commands = []
        self.exit_code = exit_code

    def run(self, args, timeout):
        self.commands.append(list(args))
        return {"exit_code": self.exit_code, "stdout": "", "stderr": ""}


@pytest.fixture
def daemon_factory(tmp_path):
    def build(*resources, containers=None, inventory=None, enable_reconcile=False,
              runner=None, clock=None, registry=None):
        if registry is None:
            registry = a_registry(*resources)
        if containers is None:
            # By default every registered container exists and is running, so
            # a test that means "this one is down" has to say so explicitly
            # rather than inheriting a fault from a default.
            containers = {}
            for resource in registry.all():
                if resource.kind == "container":
                    containers.update(running_container(resource.id))
        return Daemon(
            registry,
            state_dir=tmp_path / "state",
            socket_path=tmp_path / "run/serveros.sock",
            host=FakeProbeHost(containers),
            inventory=inventory or FakeInventory(),
            enable_reconcile=enable_reconcile,
            runner=runner,
            now=clock,
            log=lambda message: None,
        )

    return build


# ---------------------------------------------------------------------------
# §18/§20/§21 · it is a semantic controller, not a supervisor
# ---------------------------------------------------------------------------


def test_the_daemon_does_not_fork_or_daemonize():
    """§20: systemd is the supervisor. A second one is a competing authority.

    Checked as source text because the defect this prevents is a *later edit*
    adding a `daemonize()` for convenience, and a behavioural test would never
    notice until the two supervisors disagreed on a real machine.
    """
    source = (RUNTIME / "daemon.py").read_text()
    for forbidden in ("os.fork(", "setsid(", "daemonize(", "Popen("):
        assert forbidden not in source, forbidden


def test_the_actuator_is_unreachable_from_the_object_probes_hold():
    """The gate inside the gate.

    Every probe is handed a `LiveHost`. If `LiveHost` could run a command, a
    probe could mutate through its own host and "observe only" would stop
    being a property anything can check. So the actuator takes a *different*
    object, and this asserts the separation rather than trusting it.
    """
    assert not hasattr(probes_mod.LiveHost, "run")
    assert not hasattr(probes_mod.LiveHost, "read")
    assert isinstance(Actuator().runner, SubprocessRunner)


def test_only_systemctl_user_and_docker_are_ever_commanded():
    """No escalation surface, and no third tool appearing by accident."""
    runner = FakeRunner()
    actuator = Actuator(runner, dry_run=False)

    service = a_registry(a_resource("svc-one", kind="systemd-user-service",
                                    probes=[{"id": "u", "kind": "systemd-unit-state",
                                             "axis": "liveness", "unit": "x.service"}])).get("svc-one")
    actuator.restart(service)
    container = a_registry(a_resource("ctr-one")).get("ctr-one")
    actuator.restart(container)

    assert runner.commands == [
        ["systemctl", "--user", "restart", "x.service"],
        ["docker", "restart", "ctr-one"],
    ]
    for command in runner.commands:
        assert command[0] in ("systemctl", "docker")
        assert "sudo" not in command


def test_a_kind_with_no_actuator_is_refused_rather_than_guessed():
    """Guessing a restart command for a kind nobody wrote one for is how a
    control plane becomes a footgun."""
    resource = a_registry(a_resource("proc-one", kind="process", probes=[
        {"id": "pid", "kind": "process-exists", "axis": "liveness", "pattern": "x"}
    ])).get("proc-one")
    result = Actuator(FakeRunner(), dry_run=False).restart(resource)
    assert result["performed"] is False
    assert result["reason"] == "NO_ACTUATOR"
    assert "does not implement one" in result["note"]


# ---------------------------------------------------------------------------
# §21 · the unit reaches the gate deliberately, and the deployed copy is the
# reviewed copy
# ---------------------------------------------------------------------------


def test_the_unit_file_stays_promotable_and_says_it_is_installed():
    unit = (RUNTIME / "serverosd.service").read_text()
    assert "[Install]" in unit  # promotable by `systemctl --user enable`
    # This used to read `assert "NOT INSTALLED" in unit`, which was true of a
    # candidate and false of a running service. The replacement asserts the
    # POSITIVE fact rather than banning the old string: the header now quotes
    # the retired wording to explain the change, and a substring ban would
    # fail on the explanation instead of on the thing explained — the same
    # trap the TCP assertion below documents.
    assert "INSTALLED on this node as a PILOT" in unit


def test_an_installed_unit_is_byte_identical_to_the_reviewed_artefact():
    """The deployed copy must be the reviewed copy.

    Skipped when the pilot is not installed on this host — the invariant is
    about the RELATIONSHIP, not about this machine having a service. But when
    a copy IS installed, a difference means somebody's node is running a unit
    that no review covers, which is worth failing over.
    """
    installed = Path.home() / ".config/systemd/user/serverosd.service"
    if not installed.exists():
        pytest.skip("the serverosd pilot is not installed on this host")
    assert installed.read_bytes() == (RUNTIME / "serverosd.service").read_bytes(), (
        "the installed unit has drifted from runtime/serverosd.service; reinstall "
        "with `install -m 0644 runtime/serverosd.service "
        "~/.config/systemd/user/serverosd.service`"
    )


def test_the_unit_does_not_turn_on_reconcile_or_listen_on_tcp():
    """A unit that enables mutation at boot is one nobody reviewed at the
    moment it mattered. A unit that binds TCP is reachable by anything that
    can reach the host."""
    unit = (RUNTIME / "serverosd.service").read_text()
    exec_start = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    assert exec_start and "--enable-reconcile" not in exec_start[0]
    # Directives only: the file discusses TCP in prose to explain why it does
    # not use it, and a substring check over the whole text would fail on the
    # explanation rather than on the thing explained.
    directives = [line for line in unit.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    for line in directives:
        for forbidden in ("ListenStream", "ListenDatagram", "ListenFIFO", "SocketBind"):
            assert forbidden not in line, line
        assert not line.startswith("ExecStartPre="), line
    assert "Restart=on-failure" in unit  # `always` restarts a deliberate stop


def test_the_unit_does_not_isolate_the_observer():
    """The daemon's product is its answer about the host.

    Measured on 2026-09-13, one property per transient unit, reading
    `readlink /proc/self/ns/user` and `pgrep -f 'botmux __supervisor'` from
    inside:

        property                    userns                visible  sees_1833
        (none)                      user:[4026531837]         63      yes
        NoNewPrivileges=yes         user:[4026531837]         66      yes
        UMask=0077                  user:[4026531837]         64      yes
        RuntimeDirectory=x          user:[4026531837]         67      yes
        ProtectProc=invisible       user:[4026531837]         65      yes
        ReadWritePaths=/tmp         user:[4026533473]          4       NO
        PrivateUsers=yes            user:[4026533473]          4       NO

    `ReadWritePaths=` alone put the unit in a private user namespace whose
    uid_map was `1006 1006 1`; from in there `pgrep` ran, exited 0, and matched
    nothing. The probe underneath reported STOPPED — a live process with two
    days of uptime, described as down, by a daemon that had not gone blind but
    had been blinded, and could not tell the difference.

    The first three directives below are the ones measured; the rest are the
    same family (user- and mount-namespace isolation) and fail the same silent
    way, so they are gated rather than re-measured one at a time. This asserts
    the FILE; `test_the_running_daemon_is_not_in_a_private_user_namespace`
    asserts the RUNNING thing, because a file that is right and a service that
    was started before the fix look identical from here.
    """
    directives = [line for line in (RUNTIME / "serverosd.service").read_text().splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    isolating = (
        "ReadWritePaths=", "ReadOnlyPaths=", "InaccessiblePaths=",
        "BindPaths=", "BindReadOnlyPaths=",
        "PrivateUsers=", "PrivateTmp=", "PrivateMounts=", "PrivateDevices=",
        "ProtectSystem=", "ProtectHome=", "ProtectProc=", "ProcSubset=",
        "ProtectKernelTunables=", "ProtectKernelModules=", "ProtectKernelLogs=",
        "ProtectControlGroups=", "ProtectClock=", "ProtectHostname=",
        "RestrictNamespaces=",
    )
    for line in directives:
        for directive in isolating:
            assert not line.startswith(directive), (
                f"`{line}` isolates the observer from the host it exists to "
                f"report on; see the measurement table in this test"
            )


def test_the_running_daemon_is_not_in_a_private_user_namespace():
    """The live half of the gate above.

    A private user namespace always has a narrow uid_map — the one measured
    for `ReadWritePaths=` was `1006 1006 1`, a single uid. The initial
    namespace maps the whole 32-bit range. Asserting on the map rather than on
    `/proc/<pid>/ns/user` keeps this honest when the test itself runs inside a
    namespace: what matters is whether the daemon can see this user's other
    processes, and a one-uid map is exactly the thing that stops it.

    Skipped when the pilot is not running on this host.
    """
    main_pid = _serverosd_main_pid()
    if main_pid is None:
        pytest.skip("the serverosd pilot is not running on this host")
    uid_map = Path(f"/proc/{main_pid}/uid_map").read_text().strip()
    mapped = int(uid_map.split()[2])
    assert mapped > 1, (
        f"serverosd runs in a private user namespace (uid_map {uid_map!r}); its "
        f"process probes cannot see this user's other processes and will report "
        f"live things as STOPPED"
    )


def _serverosd_main_pid() -> int | None:
    """The pilot's main pid, or None when it is not installed or not running.

    `systemctl --user show` is deliberately tolerated to fail: a test that
    errors because systemd is absent from the sandbox is a test that reports
    its environment as a product defect.
    """
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", "serverosd.service", "-p", "MainPID", "--value"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pid = out.stdout.strip()
    if out.returncode != 0 or not pid.isdigit() or pid == "0":
        return None
    return int(pid) if Path(f"/proc/{pid}").exists() else None


def test_the_daemon_re_binds_a_socket_file_that_was_removed_underneath_it(daemon_factory):
    """Measured live on 2026-09-13: a running daemon held an unlinked socket.

    It had logged `listening on ...`, ticked for minutes, and served nobody —
    `fd 3 -> socket:[69123718]` with no file to reach it by. Every client got
    ENOENT and the daemon had no reason ever to find out.
    """
    import threading

    daemon = daemon_factory()
    thread = threading.Thread(
        target=daemon.serve_forever, kwargs={"interval": 0.05, "stop_after": 60},
        daemon=True,
    )
    thread.start()
    try:
        _wait_for(lambda: daemon.socket_path.exists())
        _round_trip(daemon.socket_path)  # reachable before the removal

        daemon.socket_path.unlink()
        assert not daemon.socket_path.exists()

        _wait_for(lambda: daemon.socket_path.exists())
        assert _round_trip(daemon.socket_path)["ok"] is True, (
            "the daemon did not restore its endpoint after the socket file "
            "was removed; it would go on reporting itself healthy and "
            "unreachable"
        )
    finally:
        thread.join(timeout=10)  # `stop_after` ends it; nothing to signal


def test_a_daemon_does_not_unlink_a_socket_that_is_not_its_own(daemon_factory):
    """A restart puts two daemons on one path in sequence.

    The old one's teardown must not delete the new one's socket — that is a
    service that comes up healthy and unreachable at once, which is exactly
    what was observed on this host.
    """
    daemon = daemon_factory()
    # The listener must stay referenced. Dropping it closes the fd, frees the
    # socket inode, and lets the next file at that path be handed the SAME
    # inode number — which silently turns this test into a tautology. That is
    # not a hypothetical: it is how the first version of this test failed.
    server = daemon._open_socket()

    # Somebody else takes the path: the socket file is replaced.
    daemon.socket_path.unlink()
    daemon.socket_path.write_bytes(b"")  # a new file, a new inode
    replacement_ino = daemon.socket_path.stat().st_ino
    assert replacement_ino != daemon._socket_ino, (
        "the filesystem reused the socket's inode number; this test cannot "
        "distinguish the two files and must not be trusted as written"
    )

    daemon._release_socket()

    assert daemon.socket_path.exists(), (
        "teardown removed a socket this daemon no longer owned"
    )
    assert daemon.socket_path.stat().st_ino == replacement_ino


def test_a_daemon_does_release_its_own_socket(daemon_factory):
    """The other half of the guard, so that it cannot pass by doing nothing."""
    daemon = daemon_factory()
    server = daemon._open_socket()  # kept referenced, as in `serve_forever`
    assert daemon.socket_path.exists()
    daemon._release_socket()
    assert not daemon.socket_path.exists()
    server.close()


def _wait_for(predicate, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition never became true")


def _round_trip(path) -> dict:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(str(path))
        client.sendall(json.dumps({"method": "runtime.list", "params": {}}).encode() + b"\n")
        buffer = b""
        while b"\n" not in buffer:
            chunk = client.recv(65536)
            if not chunk:
                break
            buffer += chunk
        return json.loads(buffer.split(b"\n")[0].decode())
    finally:
        client.close()


def test_linger_is_reported_not_changed():
    """§21: enabling linger changes how the machine boots. That is a fact to
    report and a person to ask, not an edit to make from a control loop."""
    source = (RUNTIME / "daemon.py").read_text()
    assert "enable-linger" not in source
    assert "loginctl" not in source


# ---------------------------------------------------------------------------
# §19 · the reconcile gate
# ---------------------------------------------------------------------------


def test_observe_only_by_default_acts_on_nothing(daemon_factory):
    """Even when the health model says RECONCILE, and even when the resource
    is fully automatable."""
    runner = FakeRunner()
    daemon = daemon_factory(
        an_automatable_resource("down"),
        containers={"down": {"State": {"Status": "exited"}}},
        runner=runner,
    )
    report = daemon.tick()

    decision = next(d for d in report["decisions"] if d["resource"] == "down")
    assert decision["action"] == "RECONCILE"
    assert decision["performed"] is False
    assert decision["dry_run"] is True
    assert decision["would_run"] == ["docker", "restart", "down"]
    assert runner.commands == []  # the runner was never reached


def test_enabling_reconcile_actually_reaches_the_actuator(daemon_factory):
    """The gate is real in both directions: the previous test would also pass
    if reconcile were simply broken."""
    runner = FakeRunner()
    daemon = daemon_factory(
        an_automatable_resource("down"),
        containers={"down": {"State": {"Status": "exited"}}},
        runner=runner,
        enable_reconcile=True,
    )
    report = daemon.tick()
    decision = next(d for d in report["decisions"] if d["resource"] == "down")
    assert decision["performed"] is True
    assert runner.commands == [["docker", "restart", "down"]]
    # And it re-probes, because "did the restart work" is a question only a
    # probe answers. Assuming yes is how a restart loop reports success.
    assert decision["reprobe"]["state"] in ("NOT_RUNNING", "FUNCTIONALLY_FAILED", "DEGRADED")


def test_a_protected_resource_that_is_down_is_never_a_reconcile_candidate(daemon_factory):
    """The two authorities answer different questions and the registry wins.

    `action_for` answers "does this need restarting". The registry answers
    "may anyone restart this". A protected resource that is down returns
    NEEDS_HUMAN here — not RECONCILE-then-blocked — because the block is
    applied before the action is named. Getting the order the other way round
    would produce a decision record that says RECONCILE and then does nothing,
    which reads as a bug in the daemon rather than as a policy.
    """
    runner = FakeRunner()
    daemon = daemon_factory(
        a_resource("protected-down", protection={"level": "protected"}),
        containers={"protected-down": {"State": {"Status": "exited"}}},
        runner=runner,
        enable_reconcile=True,
    )
    report = daemon.tick()
    action = report["observations"]["protected-down"]["action"]
    assert action["action"] == "NEEDS_HUMAN"
    assert action["blocked_by"] == ["protection=protected"]
    assert report["decisions"] == []
    assert runner.commands == []


def test_the_daemons_own_gate_holds_even_if_the_health_model_says_reconcile(
        daemon_factory, monkeypatch):
    """Defence in depth, tested as such.

    Today `action_for` cannot return RECONCILE for a protected resource, so
    this branch is unreachable through the real health model. That is exactly
    why it is tested by forcing the input: the daemon's refusal must be a
    property of the daemon, not a property inherited from a collaborator that
    might be edited later by somebody who never read this file.
    """
    runner = FakeRunner()
    daemon = daemon_factory(
        a_resource("protected-down", protection={"level": "protected"}),
        containers={"protected-down": {"State": {"Status": "exited"}}},
        runner=runner,
        enable_reconcile=True,
    )
    real = daemon_mod.health_mod.action_for
    monkeypatch.setattr(
        daemon_mod.health_mod, "action_for",
        lambda resource, health: {"action": "RECONCILE", "because": "forced by the test"},
    )
    try:
        report = daemon.tick()
    finally:
        monkeypatch.setattr(daemon_mod.health_mod, "action_for", real)

    decision = next(d for d in report["decisions"] if d["resource"] == "protected-down")
    assert decision["performed"] is False
    assert decision["blocked_by"] == ["protection=protected"]
    assert "the registry says no" in decision["note"]
    assert runner.commands == []


def test_a_kind_without_an_actuator_is_blocked_before_the_runner(daemon_factory):
    runner = FakeRunner()
    daemon = daemon_factory(
        a_resource("proc", kind="process", probes=[
            {"id": "pid", "kind": "process-exists", "axis": "liveness", "pattern": "nope"}
        ]),
        containers={},
        runner=runner,
        enable_reconcile=True,
    )
    daemon.tick()
    assert runner.commands == []


# ---------------------------------------------------------------------------
# §19 · no infinite restart loop
# ---------------------------------------------------------------------------


def test_the_budget_exhausts_instead_of_looping(tmp_path):
    """The defect this exists for, stated exactly.

    On this host the endpoint was restarted five times in two hours and
    reported healthy each time, because a restart that returns exit 0 was
    treated as a fix. The fourth attempt is not a new attempt at a fix. It is
    the loop, and it has to stop and ask for a person.
    """
    clock = [1000.0]
    budget = Budget(tmp_path / "b.json", now=lambda: clock[0])

    for _ in range(daemon_mod.MAX_ATTEMPTS):
        assert budget.may_act("flappy")["allowed"] is True
        budget.record("flappy", "restart")
        clock[0] += daemon_mod.BACKOFF_SECONDS[-1] + 1

    verdict = budget.may_act("flappy")
    assert verdict["allowed"] is False
    assert verdict["reason"] == "BUDGET_EXHAUSTED"
    assert verdict["attempts_in_window"] == daemon_mod.MAX_ATTEMPTS
    assert verdict["retry_after_seconds"] > 0
    assert "not be a new attempt at a fix" in verdict["note"]


def test_the_budget_survives_a_daemon_restart(tmp_path):
    """`Restart=always` would otherwise grant a fresh budget every crash,
    which is the original bug wearing a different hat."""
    clock = [1000.0]
    path = tmp_path / "b.json"
    first = Budget(path, now=lambda: clock[0])
    for _ in range(daemon_mod.MAX_ATTEMPTS):
        first.record("flappy", "restart")

    second = Budget(path, now=lambda: clock[0])
    assert second.may_act("flappy")["allowed"] is False
    assert second.may_act("flappy")["reason"] == "BUDGET_EXHAUSTED"
    assert second.last_action("flappy") == "restart"


def test_backoff_refuses_a_second_immediate_attempt(tmp_path):
    clock = [1000.0]
    budget = Budget(tmp_path / "b.json", now=lambda: clock[0])
    budget.record("flappy", "restart")

    refused = budget.may_act("flappy")
    assert refused["allowed"] is False
    assert refused["reason"] == "BACKOFF"
    assert refused["retry_after_seconds"] == daemon_mod.BACKOFF_SECONDS[0]

    clock[0] += daemon_mod.BACKOFF_SECONDS[0] + 1
    assert budget.may_act("flappy")["allowed"] is True


def test_attempts_outside_the_window_are_pruned(tmp_path):
    """A resource that failed three times last month is not permanently
    barred; that would make the budget a life sentence."""
    clock = [1000.0]
    budget = Budget(tmp_path / "b.json", now=lambda: clock[0])
    for _ in range(daemon_mod.MAX_ATTEMPTS):
        budget.record("flappy", "restart")
    assert budget.may_act("flappy")["allowed"] is False

    clock[0] += daemon_mod.WINDOW_SECONDS + 1
    assert budget.may_act("flappy")["allowed"] is True
    assert budget.may_act("flappy")["attempts_in_window"] == 0


def test_an_exhausted_budget_escalates_rather_than_acting(daemon_factory):
    runner = FakeRunner()
    daemon = daemon_factory(
        an_automatable_resource("flappy"),
        containers={"flappy": {"State": {"Status": "exited"}}},
        runner=runner,
        enable_reconcile=True,
    )
    for _ in range(daemon_mod.MAX_ATTEMPTS):
        daemon.budget.record("flappy", "restart")

    report = daemon.tick()
    decision = next(d for d in report["decisions"] if d["resource"] == "flappy")
    assert decision["performed"] is False
    assert decision["escalate_to"] == "DEGRADED/NEEDS_HUMAN"
    assert runner.commands == []


# ---------------------------------------------------------------------------
# §11/§43 · the endpoint is registered, reported, and left alone
# ---------------------------------------------------------------------------


def test_the_endpoint_is_never_an_automatable_resource():
    """§43: register and probe it, state NEEDS_HUMAN, do not loop on auth."""
    registry = a_registry(a_resource(
        "remote-device-endpoint", kind="transport-endpoint",
        lifecycle={"class": "persistent"}, protection={"level": "protected"},
        owner={"type": "serveros", "ref": "serveros:node"},
        probes=[{"id": "l", "kind": "process-exists", "axis": "liveness", "pattern": "x"}],
    ))
    resource = registry.get("remote-device-endpoint")
    assert resource.is_automatable is False
    # Asserted as the PERMISSION block rather than as the whole list. The whole
    # list now also carries §10's policy conjuncts (`observe`, no functional
    # probe), which are true of almost every resource on this host and say
    # nothing about the endpoint in particular. What the endpoint's §43 ruling
    # is about is that no permission block permits an action — and a test that
    # pinned the full list would fail every time a conjunct was added, while
    # still not testing this.
    split = resource.automatability()
    assert split["permission"] == ["protection=protected"]
    assert split["recommendation_only"] is False
    assert split["automatable"] is False


def test_the_endpoint_fault_routes_to_needs_human_not_to_a_restart(daemon_factory):
    daemon = daemon_factory(
        a_resource("remote-device-endpoint", kind="transport-endpoint",
                   protection={"level": "protected"}, lifecycle={"class": "persistent"},
                   probes=[{"id": "l", "kind": "process-exists",
                            "axis": "liveness", "pattern": "nothing-runs-this"}]),
        containers={},
        enable_reconcile=True,
    )
    report = daemon.tick()
    observation = report["observations"]["remote-device-endpoint"]
    assert observation["action"]["action"] == "NEEDS_HUMAN"
    assert observation["action"]["blocked_by"] == ["protection=protected"]
    assert report["decisions"] == []


def test_a_consumed_authorization_routes_to_needs_human_and_says_why(
        daemon_factory, monkeypatch):
    """§11's acceptance case, end to end through the daemon.

    This is the incident the architecture was written after: the endpoint was
    up, the socket was connected, and its provider session had expired. Every
    restart issued a new device code that nobody could authorize, so ~5
    restarts in 2 hours each produced exit 0 and each was read as a fix.

    The assertion that matters is the last one. A restart policy supervises a
    process; it cannot renew a session, so `restart` must not appear as the
    proposed action even when the resource is otherwise automatable.
    """
    from dataclasses import replace  # noqa: F401  (documentation of intent)

    def expired_endpoint(resource, host):
        return health_mod.Health(
            liveness=health_mod.Observation(
                axis="liveness", state="RUNNING",
                observed_by={"type": "probe", "id": "process-exists"},
                observed_at="2026-09-12T00:00:00+00:00",
                evidence={"type": "host-measurement"}, reason=None),
            transport=health_mod.Observation(
                axis="transport", state="CONNECTED",
                observed_by={"type": "probe", "id": "relay-socket"},
                observed_at="2026-09-12T00:00:00+00:00",
                evidence={"type": "host-measurement"}, reason=None),
            functional=health_mod.Observation(
                axis="functional", state="FAILED",
                observed_by={"type": "probe", "id": "relay-presence"},
                observed_at="2026-09-12T00:00:00+00:00",
                evidence={"type": "provider-response"},
                reason="AUTH_REAUTH_REQUIRED"),
            readiness=health_mod.Observation(
                axis="readiness", state="UNKNOWN",
                observed_by={"type": "probe", "id": "none"},
                observed_at="2026-09-12T00:00:00+00:00"),
            desired=resource.desired,
            diagnostic={
                "reason": "AUTH_FAILURE_SUSPECTED",
                "observed_by": {"type": "diagnostic", "id": "journal"},
                "observed_at": "2026-09-12T00:00:00+00:00",
                "evidence": {"type": "journal-diagnostic"},
                "authoritative": False,
                "note": "corroboration from the journal, not a measurement.",
            },
        )

    monkeypatch.setattr(daemon_mod.probes_mod, "observe", expired_endpoint)
    runner = FakeRunner()
    daemon = daemon_factory(
        a_resource("remote-device-endpoint", kind="transport-endpoint",
                   lifecycle={"class": "persistent"},
                   owner={"type": "serveros", "ref": "serveros:node"},
                   protection={"level": "normal"},
                   probes=[{"id": "l", "kind": "process-exists",
                            "axis": "liveness", "pattern": "x"}]),
        containers={}, runner=runner, enable_reconcile=True,
    )
    report = daemon.tick()
    observation = report["observations"]["remote-device-endpoint"]
    action = observation["action"]

    assert observation["health"]["state"] == "FUNCTIONALLY_FAILED"
    assert action["action"] == "NEEDS_HUMAN"
    assert action["reason"] == "AUTH_REAUTH_REQUIRED"
    assert action["held_on"] == "reason"
    assert action["authoritative"] is True
    assert "cannot renew a session" in action["because"]
    assert "restarted 5 times" in action["restarting_is_the_wrong_response"]
    # Nothing was restarted, and nothing was even proposed.
    assert report["decisions"] == []
    assert runner.commands == []


def test_a_journal_hypothesis_alone_routes_to_needs_human_but_is_not_authoritative(
        daemon_factory, monkeypatch):
    """§9/§11: a log line may route an action to a person. It may never set a
    state, and a person acting on it is entitled to know it is a hypothesis."""
    # Captured before the patch: `daemon_mod.probes_mod` IS the module being
    # patched, so calling through it inside the replacement recurses forever.
    real_observe = daemon_mod.probes_mod.observe

    def guessed_endpoint(resource, host):
        health = real_observe(resource, host)
        return health_mod.Health(
            liveness=health.liveness, transport=health.transport,
            functional=health.functional, readiness=health.readiness,
            desired=resource.desired,
            diagnostic={"reason": "AUTH_FAILURE_SUSPECTED",
                        "observed_by": {"type": "diagnostic", "id": "journal"},
                        "observed_at": "2026-09-12T00:00:00+00:00",
                        "evidence": {"type": "journal-diagnostic"},
                        "authoritative": False, "note": "a hypothesis"},
        )

    monkeypatch.setattr(daemon_mod.probes_mod, "observe", guessed_endpoint)
    daemon = daemon_factory(
        a_resource("endpoint-two", kind="transport-endpoint",
                   lifecycle={"class": "persistent"},
                   owner={"type": "serveros", "ref": "serveros:node"},
                   probes=[{"id": "l", "kind": "container-state",
                            "axis": "liveness", "container": "gone"}]),
        containers={},
    )
    report = daemon.tick()
    observation = report["observations"]["endpoint-two"]
    assert observation["health"]["diagnostic"]["authoritative"] is False
    # UNAVAILABLE is not a fault state, so the action is DEFER — the journal
    # hypothesis did not promote an unmeasured resource into a diagnosis.
    assert observation["action"]["action"] == "DEFER"


# ---------------------------------------------------------------------------
# §17 · drift is a disagreement about the declaration, not about the world
# ---------------------------------------------------------------------------


def test_a_registration_whose_container_is_gone_is_declaration_drift(daemon_factory):
    """UNAVAILABLE means the probe reached the host and found nothing. The
    fix is to edit the declaration — a control plane that "repairs" this
    recreates things people deliberately removed."""
    daemon = daemon_factory(a_resource("ghost"), containers={})
    report = daemon.tick()
    drift = next(d for d in report["drift"] if d["resource"] == "ghost")
    assert drift["kind"] == "DECLARED_BUT_NOT_OBSERVABLE"
    assert drift["proposed_action"] == "review the registration, not the world"


def test_a_registration_with_no_probe_is_reported_as_unmanaged(daemon_factory):
    """A registration nobody can observe is not management. Reported as a
    registry defect, not as an outage — they send people to different places."""
    daemon = daemon_factory(a_resource("blind-one", probes=[]))
    report = daemon.tick()
    drift = next(d for d in report["drift"] if d["resource"] == "blind-one")
    assert drift["kind"] == "REGISTERED_WITHOUT_A_PROBE"
    assert "not management" in drift["note"]


def test_a_protected_resource_that_is_down_is_reported_and_not_touched(daemon_factory):
    """Protected means ServerOS will not act. It does not mean nobody should
    be told — silence about a protected resource is how the LIT-0103 bucket
    went unnoticed."""
    runner = FakeRunner()
    daemon = daemon_factory(
        a_resource("protected-one", protection={"level": "protected"}),
        containers={"protected-one": {"State": {"Status": "exited"}}},
        runner=runner,
        enable_reconcile=True,
    )
    report = daemon.tick()
    drift = next(d for d in report["drift"] if d["resource"] == "protected-one")
    assert drift["kind"] == "PROTECTED_AND_DOWN"
    assert drift["proposed_action"] == "NEEDS_HUMAN"
    assert runner.commands == []


# ---------------------------------------------------------------------------
# §17 · the loop survives its own failures
# ---------------------------------------------------------------------------


def test_one_broken_probe_does_not_stop_the_other_resources(daemon_factory):
    registry = a_registry(
        a_resource("bad", probes=[{"id": "x", "kind": "container-state",
                                   "axis": "liveness", "container": "missing"}]),
        a_resource("good"),
    )
    daemon = daemon_factory(registry=registry, containers={"good": running_container("good")["good"]})
    report = daemon.tick()
    assert report["observations"]["bad"]["state"] == "UNAVAILABLE"
    assert report["observations"]["good"]["health"]["state"] in ("HEALTHY", "DEGRADED")
    assert "health" in report["observations"]["good"]


def test_a_tick_that_raises_does_not_kill_the_daemon(tmp_path, monkeypatch):
    """A control plane that exits on its first bad measurement is an outage
    generator."""
    daemon = Daemon(
        a_registry(a_resource("thing-one")),
        state_dir=tmp_path / "state",
        socket_path=tmp_path / "run/s.sock",
        host=FakeProbeHost({"thing-one": running_container("thing-one")["thing-one"]}),
        inventory=FakeInventory(),
        log=lambda message: None,
    )
    calls = []
    original = daemon.tick

    def exploding_tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the registry was mid-write")
        return original()

    monkeypatch.setattr(daemon, "tick", exploding_tick)
    daemon.serve_forever(interval=0.0, stop_after=2)
    # Three calls for two successful ticks: the counter only advances on
    # success, so the failed tick did not consume the stop budget and the loop
    # kept going. That is the property — a raised tick is not the end of the
    # daemon and not a tick either.
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# §35 · persistence goes to declared leaves, atomically
# ---------------------------------------------------------------------------


def test_observations_land_in_the_declared_state_leaves(daemon_factory, tmp_path):
    daemon = daemon_factory(a_resource("thing-one"))
    daemon.tick()
    for leaf in ("observed/current.json", "health/current.json", "hygiene/current.json"):
        path = tmp_path / "state" / leaf
        assert path.exists(), leaf
        assert json.loads(path.read_text())["schema"].startswith("serveros/")


def test_the_write_is_atomic_so_a_reader_never_sees_a_half_file(daemon_factory, tmp_path):
    """The MCP adapter reads these files while the daemon writes them, on a
    machine that is already swapping."""
    daemon = daemon_factory(a_resource("thing-one"))
    daemon.tick()
    assert list((tmp_path / "state" / "observed").glob("*.tmp")) == []
    written = json.loads((tmp_path / "state" / "observed/current.json").read_text())
    # DEGRADED, not HEALTHY: this resource declares a liveness probe and no
    # functional one, and not having asked whether it works is not the same as
    # having found that it does. The default is the honest one.
    assert written["observations"]["thing-one"]["health"]["state"] == "DEGRADED"


def test_a_resource_that_was_not_observed_is_not_written_as_healthy(daemon_factory):
    daemon = daemon_factory(a_resource("ghost"), containers={})
    report = daemon.tick()
    assert report["observations"]["ghost"]["health"]["state"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# §23/§24 · the served interface, and what it refuses to answer
# ---------------------------------------------------------------------------


def test_an_unknown_method_is_refused_and_lists_the_known_ones(daemon_factory):
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": "runtime.restart", "params": {"id": "thing-one"}})
    assert response["ok"] is False
    assert response["error"]["code"] == "UNKNOWN_METHOD"
    assert "runtime.status" in response["error"]["message"]


def test_ability_questions_are_answered_but_never_on_the_authoritys_behalf(
        daemon_factory, tmp_path, monkeypatch):
    """§24 asks for `ability.list`; §27/§29 put that namespace under
    ability-management; §28 requires the node to keep working while the
    authority is unreachable.

    The first version of this refused `ability.*` outright. That is
    defensible and wrong: it makes every node surface depend on
    ability-management being reachable, which is exactly the coupling §28
    exists to remove. The reconciliation is that the ANSWER names its own
    status — the authority, and whether this view is fresh, stale, or has
    never been materialized. Then "nothing is installed" and "nobody has
    asked in three days" stop being the same empty list.
    """
    # Hermetic for the same reason as the absent-projection test below: the
    # unpatched path is the live node, and this test's whole subject is which
    # state the node is in.
    empty = tmp_path / "state"
    empty.mkdir()

    class Paths:
        def root(self, name):
            assert name == "state"
            return empty

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    daemon = daemon_factory(a_resource("thing-one"))
    for method in ("ability.list", "ability.status"):
        response = daemon.handle({"method": method, "params": {"name": "serveros"}})
        assert response["ok"] is True
        result = response["result"]
        assert result["authority"] == "ability-management"
        assert result["this_is"] == "a local observed projection, not the authority"
        assert result["registry_status"] == "NOT_MATERIALIZED"
        assert result["stale"] is True

    listing = daemon.handle({"method": "ability.list"})["result"]
    assert "nobody has asked" in listing["note"]

    # A named lookup says something narrower and must not claim the listing's
    # wording: absence from a projection is not absence on the authority.
    named = daemon.handle({"method": "ability.status",
                           "params": {"name": "serveros"}})["result"]
    assert "not proof of absence" in named["note"]
    assert named["installed"] is None


def test_an_absent_projection_is_not_reported_as_no_abilities_installed(
        daemon_factory, tmp_path, monkeypatch):
    """The distinction the whole §28 bootstrap path turns on.

    Hermetic, and it was not. This test asserted NOT_MATERIALIZED while reading
    `Paths().root("state")` unpatched — the live node. It passed for exactly as
    long as no projection had been materialized, and failed the moment one was,
    which is to say it was testing this machine and reporting the answer as if
    it were a property of the code. A directory that is empty by construction
    is the only thing that can mean "absent" here.
    """
    empty = tmp_path / "state"
    empty.mkdir()

    class Paths:
        def root(self, name):
            assert name == "state"
            return empty

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.list"})["result"]
    assert result["registry_status"] == "NOT_MATERIALIZED"
    assert "NOT the same as" in result["note"]


def test_a_fresh_projection_is_reported_as_fresh_and_a_stale_one_as_stale(
        daemon_factory, tmp_path, monkeypatch):
    import json as jsonlib

    abilities = tmp_path / "state" / "abilities"
    abilities.mkdir(parents=True)

    class Paths:
        def root(self, name):
            assert name == "state"
            return tmp_path / "state"

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    now = 1_800_000_000.0
    daemon = daemon_factory(a_resource("thing-one"), clock=lambda: now)

    fresh = {"generated_at": "2027-01-15T07:00:00+00:00", "registry_status": "FRESH",
             "source_revision": "abc123", "abilities": [{"name": "serveros"}]}
    (abilities / "effective.json").write_text(jsonlib.dumps(fresh))
    result = daemon.handle({"method": "ability.list"})["result"]
    assert result["registry_status"] == "FRESH" and result["stale"] is False
    assert result["abilities"] == [{"name": "serveros"}]

    # The file still says FRESH; the clock says otherwise. The clock wins,
    # because `registry_status` is what the writer believed at the time.
    stale = dict(fresh, generated_at="2020-01-01T00:00:00+00:00")
    (abilities / "effective.json").write_text(jsonlib.dumps(stale))
    result = daemon.handle({"method": "ability.list"})["result"]
    assert result["registry_status"] == "STALE"
    assert result["age_seconds"] > daemon_mod.MAX_PROJECTION_AGE_SECONDS


def test_an_undated_projection_is_not_treated_as_fresh(daemon_factory, tmp_path,
                                                       monkeypatch):
    """A missing timestamp defaulting to "just now" is how a three-day-old
    capability list gets read as current."""
    abilities = tmp_path / "state" / "abilities"
    abilities.mkdir(parents=True)
    (abilities / "effective.json").write_text('{"abilities": []}')

    class Paths:
        def root(self, name):
            return tmp_path / "state"

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle({"method": "ability.list"})["result"]
    assert result["age_seconds"] is None
    assert result["stale"] is True


def test_a_corrupt_projection_is_unreadable_not_empty(daemon_factory, tmp_path,
                                                      monkeypatch):
    abilities = tmp_path / "state" / "abilities"
    abilities.mkdir(parents=True)
    (abilities / "effective.json").write_text("{ this is not json")

    class Paths:
        def root(self, name):
            return tmp_path / "state"

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle({"method": "ability.list"})["result"]
    assert result["registry_status"] == "UNREADABLE"
    assert "much more confident claim" in result["note"]


def test_the_daemon_reads_the_projection_the_writer_actually_writes(
        daemon_factory, tmp_path, monkeypatch):
    """The join between the two halves of §28.

    Each side is tested on its own — the reader above, the writer in
    `test_ability_projection.py` — and both can be green while disagreeing on
    the directory, the filename, or a field name. That is the failure a pair of
    isolated unit tests is structurally unable to catch, so it is asserted once,
    end to end, by writing through the real writer and reading through the real
    daemon.

    A deliberately minimal projection: this test is about the contract between
    the two processes, not about how abilities get derived.
    """
    projection = _load("serveros_runtime_ability_projection", "ability_projection.py")
    state = tmp_path / "state"
    (state / "abilities").mkdir(parents=True)
    projection.write_atomic(state / "abilities" / "effective.json", {
        "schema": "serveros/ability-projection.v1",
        "authority": "ability-management",
        "this_is": "a local observed projection, not the authority",
        "registry_status": "FRESH",
        "generated_at": "2027-01-15T07:00:00+00:00",
        "source_revision": "deadbeef",
        "abilities": [{"name": "session-manager"}],
    })

    class Paths:
        def root(self, name):
            assert name == "state"
            return state

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    # One hour after `generated_at`, and well inside the reader's limit — the
    # same instant the neighbouring staleness tests use.
    now = 1_800_000_000.0
    result = daemon_factory(a_resource("thing-one"), clock=lambda: now).handle(
        {"method": "ability.list"})["result"]

    assert result["registry_status"] == "FRESH"
    assert result["stale"] is False
    assert result["source_revision"] == "deadbeef"
    assert result["abilities"] == [{"name": "session-manager"}]
    assert result["generated_at"] == "2027-01-15T07:00:00+00:00"


def test_observation_get_before_a_tick_is_not_an_empty_healthy_answer(daemon_factory):
    """Absence has a name. An empty observation set must never read as a
    clean bill of health."""
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": "observation.get"})
    assert response["ok"] is True
    assert response["result"]["tick"] is None
    assert "no observation, not a healthy node" in response["result"]["note"]


def test_every_served_method_answers_after_a_tick(daemon_factory):
    daemon = daemon_factory(a_resource("thing-one"))
    daemon.tick()
    for method, params in (
        ("device.show", {}), ("context.current", {}),
        ("path.resolve", {"ref": "state/runtime/observed"}),
        ("runtime.list", {}), ("runtime.status", {"id": "thing-one"}),
        ("runtime.explain", {"id": "thing-one"}), ("resource.summary", {}),
        ("resource.hygiene", {}), ("observation.get", {}),
    ):
        response = daemon.handle({"method": method, "params": params})
        assert response["ok"] is True, (method, response)


def test_runtime_explain_shows_the_budget_and_the_reason(daemon_factory):
    daemon = daemon_factory(a_resource("protected-one", protection={"level": "protected"}))
    result = daemon.handle({"method": "runtime.explain", "params": {"id": "protected-one"}})["result"]
    # The permission reason is what makes this a NEEDS_HUMAN rather than a
    # recommendation; it must be present. Asserting membership rather than
    # equality because the list also carries §10's policy conjuncts, and the
    # point of this test is the budget and the last action, not the list.
    assert "protection=protected" in result["why_not_automatable"]
    assert result["budget"]["allowed"] is True
    assert result["last_action"] is None


# ---------------------------------------------------------------------------
# §22 · the socket
# ---------------------------------------------------------------------------


def test_the_default_socket_is_a_unix_socket_in_the_runtime_directory():
    path = daemon_mod.default_socket_path()
    assert path.name == "serveros.sock"
    assert path.parent.name == "serveros"
    assert "XDG_RUNTIME_DIR" in (RUNTIME / "daemon.py").read_text()


def test_a_request_round_trips_over_a_real_socket_pair(daemon_factory):
    """The IPC path itself, not just `handle()`."""
    daemon = daemon_factory(a_resource("thing-one"))
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        right.sendall(json.dumps({"method": "runtime.list"}).encode() + b"\n")
        daemon._serve_connection(left)
        response = json.loads(right.recv(65536).decode())
    finally:
        left.close()
        right.close()
    assert response["ok"] is True
    assert response["result"]["count"] == 1


def test_malformed_input_gets_a_named_error_not_a_dropped_connection(daemon_factory):
    daemon = daemon_factory(a_resource("thing-one"))
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        right.sendall(b"this is not json\n")
        daemon._serve_connection(left)
        response = json.loads(right.recv(65536).decode())
    finally:
        left.close()
        right.close()
    assert response["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# §17 · the daemon does not grow into things that are not its job
# ---------------------------------------------------------------------------


def test_the_daemon_does_not_import_a_git_or_feishu_client(daemon_factory):
    """§18, as a test rather than as a paragraph. Each of these imports is a
    step toward the control plane becoming a dependency of everything."""
    source = (RUNTIME / "daemon.py").read_text()
    for forbidden in ("subprocess.run([\"git\"", "feishu", "shutil.rmtree"):
        assert forbidden not in source, forbidden
    methods = daemon_factory(a_resource("thing-one"))._methods()
    for forbidden in ("git.", "feishu.", "knowledge.", "ability.author"):
        assert not any(m.startswith(forbidden) for m in methods), forbidden


# ---------------------------------------------------------------------------
# §14 · a missing parameter is refused by name, not by KeyError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,param", [
    ("runtime.status", "id"),
    ("runtime.explain", "id"),
    ("path.resolve", "ref"),
    ("path.admit", "path"),
])
def test_a_missing_parameter_is_refused_in_a_sentence(daemon_factory, method, param):
    """Found by calling the live socket by hand.

    `runtime.status` with no `id` answered `KeyError: 'id'` — a code where a
    sentence belongs, and the same shape for `path.resolve` with no `ref`,
    which `_required`'s own docstring names as the case it exists for. Four
    handlers were still indexing `params` directly while the rest went
    through the helper, so the refusal a caller got depended on which method
    they happened to call.
    """
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": method, "params": {}})

    assert response["ok"] is False
    assert response["error"]["code"] == "ValueError", response["error"]
    assert param in response["error"]["message"]
    assert "missing required parameter" in response["error"]["message"]
    # And the refusal says WHY an empty answer would have been worse.
    assert "nothing to report" in response["error"]["message"]


@pytest.mark.parametrize("method,param", [
    ("runtime.status", "id"),
    ("runtime.explain", "id"),
    ("path.resolve", "ref"),
    ("path.admit", "path"),
])
def test_an_empty_string_parameter_is_refused_the_same_way(daemon_factory, method, param):
    """`""` is not an id. A falsy value that reaches the registry produces the
    same bare error one layer down, so it is refused at the boundary."""
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": method, "params": {param: ""}})
    assert response["ok"] is False
    assert "missing required parameter" in response["error"]["message"]
