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

import datetime
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
# §3. The card receipt ledger, loaded under the same name `runtime/daemon.py`
# loads it under — so what these tests read back is the ledger the daemon
# actually writes to, and not a second instance of the same class.
card_delivery_mod = _load("serveros_runtime_card_delivery", "card_delivery.py")
# §27. Loaded so the unavailable-provider tests can drive the REAL inventory
# over a host whose reads all fail, rather than a fake that models the failure
# shapes — and therefore tests the fake.
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")

# `release.py` is the one module under test that lives outside `runtime/`, so it
# does not fit `_load`. It is loaded here because the unit-rendering it owns is
# the thing an installed unit has to be a rendering OF — asserting that
# relationship needs the same function the installer used, not a second
# implementation of the substitution in this file.
_release_spec = importlib.util.spec_from_file_location(
    "serveros_release", REPO / "scripts" / "release.py")
release = importlib.util.module_from_spec(_release_spec)
sys.modules["serveros_release"] = release
_release_spec.loader.exec_module(release)

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


class FakeClock:
    """A clock and a sleep that advance each other.

    The daemon's re-probe waits for a restarted resource to settle (see
    `Daemon._reprobe`), which is a real wait. A test that exercises a real
    reconcile therefore either sleeps for the whole settle window or does not
    test it at all. Passing this as both `now` and `sleep` compresses the
    window to nothing while keeping the loop's *logic* — including the fact
    that it terminates — under test.
    """

    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


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

    # The three segments `resource.summary` gained when the card stopped
    # counting host numbers as a person's. Answered as named absences rather
    # than given placeholder numbers: this double has no `/proc` to read, and a
    # fake that invented "0.1 core" would be a fixture asserting a measurement.
    # The real paths are exercised in `tests/test_user_observation.py`, which
    # drives the real `Inventory` through a host that serves `/proc`.
    def cpu_usage(self):
        return {"state": inventory_mod.UNAVAILABLE,
                "reason": "the fake host does not sample /proc/stat"}

    def user_summary(self):
        return {"state": inventory_mod.UNAVAILABLE,
                "reason": "the fake host has no processes to attribute"}

    def user_quota(self):
        return {"state": inventory_mod.UNAVAILABLE,
                "reason": "the fake host has no filesystem quota"}


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
              runner=None, clock=None, sleep=None, registry=None):
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
            sleep=sleep,
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

    The template carries placeholders now, so the comparison renders it first.
    That is a stronger check than the byte comparison it replaces, not a weaker
    one: it still fails on any edit to the reviewed template, and it fails
    additionally when the installed unit names a runtime or a state directory
    that the reviewed template would not have produced from those values.

    The values are read back out of the installed unit rather than supplied by
    the test. Hardcoding this machine's paths would make the test pass on the
    node it was written on and mean nothing anywhere else, and it would stop
    noticing the one thing worth noticing — that the unit is a consistent
    rendering of the template rather than a hand-edited file that resembles one.
    """
    installed = Path.home() / ".config/systemd/user/serverosd.service"
    if not installed.exists():
        pytest.skip("the serverosd pilot is not installed on this host")

    text = installed.read_text()
    exec_line = next((l for l in text.splitlines() if l.startswith("ExecStart=")), None)
    assert exec_line is not None, "the installed unit has no ExecStart"
    runtime_dir = exec_line[len("ExecStart="):].removesuffix("/bin/serverosd")
    state_line = next(
        (l for l in text.splitlines() if l.startswith("Environment=SERVEROS_STATE_DIR=")), None)
    assert state_line is not None, "the installed unit declares no SERVEROS_STATE_DIR"
    state_dir = state_line[len("Environment=SERVEROS_STATE_DIR="):]

    expected = release.render_unit((RUNTIME / "serverosd.service").read_text(),
                                   Path(runtime_dir), Path(state_dir))
    assert text == expected, (
        "the installed unit has drifted from runtime/serverosd.service; reinstall it "
        "with `scripts/release.py promote ...`, which renders the reviewed template"
    )


def test_the_installed_unit_does_not_name_a_source_checkout():
    """A supervisor must not be pointed at a worktree.

    Stated as its own test because this is the defect the release path was built
    to remove, and it is invisible in a byte comparison: a unit pointing at a
    worktree is byte-identical to the reviewed template that still contains that
    path. The worktree gets retired, the service stops starting, and nothing
    that ran before it did had a reason to object.
    """
    installed = Path.home() / ".config/systemd/user/serverosd.service"
    if not installed.exists():
        pytest.skip("the serverosd pilot is not installed on this host")
    for line in installed.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith(("#", ";")):
            continue
        assert "/workspace/worktrees/" not in line, (
            f"the installed unit runs out of a per-Item worktree, which may be retired: "
            f"{line.strip()}"
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
    clock = FakeClock()
    runner = FakeRunner()
    daemon = daemon_factory(
        an_automatable_resource("down"),
        containers={"down": {"State": {"Status": "exited"}}},
        runner=runner,
        clock=clock,
        sleep=clock.sleep,
        enable_reconcile=True,
    )
    report = daemon.tick()
    decision = next(d for d in report["decisions"] if d["resource"] == "down")
    assert decision["performed"] is True
    assert runner.commands == [["docker", "restart", "down"]]
    # And it re-probes, because "did the restart work" is a question only a
    # probe answers. Assuming yes is how a restart loop reports success.
    reprobe = decision["reprobe"]
    assert reprobe["state"] in ("NOT_RUNNING", "FUNCTIONALLY_FAILED", "DEGRADED")
    # This container stays down no matter how long the settle window is, so the
    # window is the thing being measured here: it must expire, report the last
    # real observation as unsettled, and stop.
    assert reprobe["settled"] is False
    assert reprobe["waited_seconds"] >= daemon_mod.Daemon.SETTLE_SECONDS
    assert reprobe["samples"] == int(
        daemon_mod.Daemon.SETTLE_SECONDS / daemon_mod.Daemon.SETTLE_INTERVAL) + 1


def test_a_resource_that_answers_after_the_operation_is_reported_as_settled(daemon_factory):
    """The measured §53 race, as a test.

    A restart returns before the restarted service is listening. Sampling once
    reports the restart's latency as its effect, and does it in the direction
    that looks like a failure. The re-probe waits for the answer instead —
    bounded, so a resource that never answers is still reported promptly.
    """
    def declaring_healthcheck(info, status):
        info["Config"] = {"Healthcheck": {"Test": ["CMD", "true"]}}
        info["State"]["Health"] = {"Status": status, "Log": [{"Output": status}]}
        return info

    clock = FakeClock()
    containers = {"slow": declaring_healthcheck(
        {"State": {"Status": "exited"}}, "unhealthy")}
    returned_at = {"t": None}

    class SlowRunner:
        """A restart that returns before the service is listening.

        The one-second lag is modelled against the clock rather than a call
        count, because that is what the real thing is: time passing between the
        supervisor's exit 0 and the socket being bound.
        """

        def __init__(self):
            self.commands = []

        def run(self, args, timeout):
            self.commands.append(list(args))
            returned_at["t"] = clock.t
            return {"exit_code": 0, "stdout": "", "stderr": ""}

    real_observe = daemon_mod.probes_mod.observe

    def observe(resource, host):
        if returned_at["t"] is not None and clock.t - returned_at["t"] >= 1.0:
            containers["slow"] = declaring_healthcheck(
                running_container("slow")["slow"], "healthy")
        return real_observe(resource, host)

    daemon = daemon_factory(
        an_automatable_resource("slow"),
        containers=containers,
        runner=SlowRunner(),
        clock=clock,
        sleep=clock.sleep,
        enable_reconcile=True,
    )
    daemon_mod.probes_mod.observe = observe
    try:
        report = daemon.tick()
    finally:
        daemon_mod.probes_mod.observe = real_observe

    decision = next(d for d in report["decisions"] if d["resource"] == "slow")
    assert decision["performed"] is True
    assert decision["reprobe"]["settled"] is True, decision["reprobe"]
    assert decision["reprobe"]["state"] == "HEALTHY"
    # And it waited for the answer rather than getting lucky: a single sample
    # taken at the moment the restart returned would have said NOT_RUNNING.
    assert decision["reprobe"]["samples"] > 1
    assert decision["reprobe"]["waited_seconds"] >= 1.0


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
    # §54 names the outcome, and it is not "RECONCILE, once more". A decision
    # record that keeps naming the action it has already decided not to take
    # is the restart loop written down rather than executed.
    assert decision["action"] == "NEEDS_HUMAN"
    assert decision["budget_exhausted"] is True
    assert "max_attempts=3" in decision["because"]
    # The observation says the same thing. It is built before the budget is
    # consulted, so left alone it reports the health model's RECONCILE while
    # the decision beside it reports NEEDS_HUMAN.
    observation = report["observations"]["flappy"]
    assert observation["action"]["action"] == "NEEDS_HUMAN"
    assert observation["action"]["budget_exhausted"] is True
    # And the health model's own verdict is preserved under it rather than
    # overwritten — the escalation is recorded, not the state relabelled.
    assert observation["state"] == "NOT_RUNNING"


def test_no_restart_loop_on_a_resource_that_cannot_be_fixed(daemon_factory):
    """§54's acceptance, as a test: retry budget exhausted → NEEDS_HUMAN.

    The distinction §54 is really about is between a resource that is restarted
    and recovers, and one that is restarted and does not. Only the second can
    loop, and what stops it is the budget — so this drives the whole sequence
    through `tick()` rather than asserting on the budget alone: restart once,
    fail the re-probe, and then check that every subsequent tick is inert.
    """
    class NeverRecovers:
        """Stands in for a service that exits immediately, every time.

        The §54 fixture uses a systemd unit with `Restart=no` running
        `--mode broken`; the shape being modelled is that no number of restarts
        changes the answer.
        """

        def __init__(self):
            self.commands = []

        def run(self, args, timeout):
            self.commands.append(list(args))
            return {"exit_code": 0, "stdout": "", "stderr": ""}

    clock = FakeClock()
    runner = NeverRecovers()
    daemon = daemon_factory(
        an_automatable_resource("permanent"),
        containers={"permanent": {"State": {"Status": "exited"}}},
        runner=runner,
        clock=clock,
        sleep=clock.sleep,
        enable_reconcile=True,
    )

    def tick():
        return next(d for d in daemon.tick()["decisions"]
                    if d["resource"] == "permanent")

    first = tick()
    assert first["performed"] is True
    assert first["recovered"] is False, first["reprobe"]

    # Eight ticks at 100s: past the 60s backoff, never past the 3600s window,
    # so what is being measured is the budget rather than the window's pruning.
    observed = []
    for _ in range(8):
        clock.t += 100
        observed.append(tick())

    # Three restarts, which is max_attempts — not eight, and not one per tick.
    assert len(runner.commands) == daemon_mod.MAX_ATTEMPTS, runner.commands
    # And once the budget is spent, every later tick says so in the field a
    # reader filters on, having done nothing.
    inert = [d for d in observed if d["action"] == "NEEDS_HUMAN"]
    assert inert, [d["action"] for d in observed]
    for decision in inert:
        assert decision["performed"] is False
        assert decision["budget_exhausted"] is True


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


# ---------------------------------------------------------------------------
# §4/§2 · the discovery tree and the single-ability read
# ---------------------------------------------------------------------------
#
# These two methods exist so that §11's `ability.inspect` names something richer
# than `ability.status`, and so that §4's tree is reachable by an agent at all.
# The failure mode both guard against is the one this file's neighbours already
# guard: an empty answer that reads as a confident one.


def _state_root(tmp_path, monkeypatch, materials):
    """A state root holding one projection, so the derivations have a subject."""
    projection = _load("serveros_ability_projection", "ability_projection.py")
    state = tmp_path / "state"
    (state / "abilities").mkdir(parents=True)
    projection.write_atomic(state / "abilities" / "effective.json", {
        "schema": "serveros/ability-projection.v1",
        "authority": "ability-management",
        "this_is": "a local observed projection, not the authority",
        "registry_status": "FRESH",
        "generated_at": "2027-01-15T07:00:00+00:00",
        "source_revision": "deadbeef",
        "abilities": materials,
    })

    class Paths:
        def root(self, name):
            assert name == "state"
            return state

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    return state


def test_the_tree_is_derived_from_the_projection_and_carries_its_provenance(
        daemon_factory, tmp_path, monkeypatch):
    """A tree without the projection's staleness beside it is a placement
    presented as a fact about the authority, which §27 forbids the node to
    claim."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "shared-memory", "id": "ProfessorZhi/shared-memory",
         "path": "skills/shared-memory", "domain": {"primary": "serveros.memory"},
         "taxonomy_refs": ["serveros.memory"]},
        {"name": "test-management", "id": "ProfessorZhi/test-management",
         "path": "skills/test-management",
         "domain": {"primary": "serveros.operations.test"},
         "taxonomy_refs": ["serveros.engineering.quality", "serveros.operations"]},
        {"name": "design-and-contract", "id": "ProfessorZhi/design-and-contract",
         "path": "skills/design-and-contract"},
    ])
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.tree"})["result"]

    assert result["authority"] == "ability-management"
    assert result["registry_status"] == "FRESH"
    assert result["source_revision"] == "deadbeef"
    assert result["basis"] == "domain.primary"
    # Every prefix is a node, including the root — a tree whose top level
    # vanished would put every ability one level too high.
    assert result["nodes"] == ["serveros", "serveros.memory",
                               "serveros.operations", "serveros.operations.test"]
    assert result["placements"] == {"shared-memory": "serveros.memory",
                                    "test-management": "serveros.operations.test"}
    # `serveros.engineering.quality` is referenced and is NOT a node: no
    # ability's primary path runs through it, and inventing one would be a node
    # no ability is in.
    assert result["cross_refs"] == {
        "test-management": ["serveros.engineering.quality", "serveros.operations"]}
    assert "serveros.engineering.quality" not in result["nodes"]
    assert result["unplaced"] == ["design-and-contract"]
    assert (result["ability_count"], result["classified_count"],
            result["unplaced_count"]) == (3, 2, 1)


def test_the_tree_is_null_and_not_empty_when_nothing_was_materialized(
        daemon_factory, tmp_path, monkeypatch):
    """The distinction this whole surface turns on, applied one level up.

    `nodes: []` says "the catalogue has no nodes in it". That is a claim about
    ability-management, made by a node that has not read it — and the empty
    list is exactly how the claim gets made by accident, because `[]` is also
    what a correct derivation from an unread catalogue would produce.
    """
    empty = tmp_path / "state"
    empty.mkdir()

    class Paths:
        def root(self, name):
            assert name == "state"
            return empty

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.tree"})["result"]

    assert result["registry_status"] == "NOT_MATERIALIZED"
    assert result["nodes"] is None and result["placements"] is None
    assert result["unplaced"] is None and result["cross_refs"] is None
    assert "NOT an empty tree" in result["note"]


def test_ability_inspect_places_one_ability_and_refuses_to_resolve_it(
        daemon_factory, tmp_path, monkeypatch):
    """§11's name, and the boundary it must not cross.

    The tempting implementation answers "can this node run it" from "is it in
    the projection", which is a union where the truth is an intersection: the
    ability is materialized AND a provider that can execute it is present. A
    projection is only the first half, so `resolution` is `not attempted` and
    the answer names what does attempt it.
    """
    _state_root(tmp_path, monkeypatch, [
        {"name": "test-management", "id": "ProfessorZhi/test-management",
         "path": "skills/test-management",
         "domain": {"primary": "serveros.operations.test"},
         "taxonomy_refs": ["serveros.engineering.quality"]},
    ])
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.inspect", "params": {"name": "test-management"}})["result"]

    assert result["installed"] is True
    assert result["source"] == "local projection"
    assert result["resolution"] == "not attempted"
    assert result["resolved_by"] == "serverosctl ability resolve"
    assert result["registry_status"] == "FRESH"
    assert result["tree"] == {"placement": "serveros.operations.test",
                              "cross_refs": ["serveros.engineering.quality"],
                              "unplaced": False}
    # No FIELD in this answer may read as a verdict on executability. Asserted
    # over the keys rather than over the prose, because the prose is where an
    # explanation belongs and the keys are where a caller looks first.
    assert set(result) & {"runnable", "supported", "available", "ready"} == set()


def test_ability_inspect_of_an_unknown_name_does_not_claim_absence(
        daemon_factory, tmp_path, monkeypatch):
    """Absence from a projection is not absence on the authority (§27)."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "shared-memory", "id": "x", "path": "skills/shared-memory"},
    ])
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.inspect", "params": {"name": "nothing-like-this"}})["result"]

    assert result["installed"] is None
    assert result["ability"] is None
    assert result["tree"] is None
    assert "not proof of absence" in result["note"]
    # The projection itself is still fresh and is still reported as such: the
    # ability is missing, not the catalogue.
    assert result["registry_status"] == "FRESH"
    assert result["source_revision"] == "deadbeef"


def test_operations_declared_is_null_and_never_false(daemon_factory, tmp_path, monkeypatch):
    """The §10 field this surface was asked for, and the one value it may hold.

    A boolean here would be a claim about a PROVIDER record, and this process
    holds none: providers reach the node over a transport, and §23 forbids the
    core from loading the plane that governs it. A `false` would be
    indistinguishable from a provider somebody actually read — the same defect
    as an empty list standing in for "could not ask".

    Asserted on the value rather than trusted to the comment, because a future
    edit that "makes the field useful" by filling in `False` is exactly the edit
    this exists to fail on.
    """
    _state_root(tmp_path, monkeypatch, [
        {"name": "test-management", "id": "x", "path": "skills/test-management"},
    ])
    for name in ("test-management", "nothing-like-this"):
        result = daemon_factory(a_resource("thing-one")).handle(
            {"method": "ability.inspect", "params": {"name": name}})["result"]
        assert result["operations_declared"] is None, name
        assert not isinstance(result["operations_declared"], bool), name
        assert result["operations_declared_by"].startswith("serverosctl ability resolve")


# ---------------------------------------------------------------------------
# the Agent fast path · search, resolve, and the Item a caller is standing in
# ---------------------------------------------------------------------------
#
# The unit of progress these three methods are measured against is not "is the
# catalogue complete" but "can a real Agent finish a real piece of work without
# spending its context reading the catalogue". So the assertions below are about
# COST and about UNKNOWNS, not about coverage: how many rows a search returns,
# what a search hit does not carry, and which of the resolver's eight inputs the
# node refuses to invent.


def test_a_search_returns_rows_and_the_count_that_makes_them_readable(
        daemon_factory, tmp_path, monkeypatch):
    """`matched` beside `matches`, because a truncated list is indistinguishable
    from a complete one until somebody says which it is."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "test-management", "id": "ProfessorZhi/test-management",
         "path": "skills/test-management", "type": "skill", "version": "1.2.0",
         "domain": {"primary": "serveros.operations.test"},
         "taxonomy_refs": ["serveros.engineering.quality"],
         "agents": ["claude-code"], "fingerprint": "sha256:" + "a" * 64,
         "revisions_installed": 3},
        {"name": "runtime-management", "id": "ProfessorZhi/runtime-management",
         "path": "skills/runtime-management", "type": "skill",
         "domain": {"primary": "serveros.operations.runtime"}},
    ])
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.search", "params": {"query": "management"}})["result"]

    assert result["matched"] == 2
    assert result["returned"] == len(result["matches"]) == 2
    assert result["truncated"] is False
    assert result["catalogue_size"] == 2
    # §27/§28 still hold on this surface: it searches a local projection and
    # says so, so a hit is never read as the authority's confirmation.
    assert result["authority"] == "ability-management"
    assert result["registry_status"] == "FRESH"

    # A hit is a pointer, not a record. The fingerprint, the revision count and
    # the installation count are what `ability.inspect` is for — one ability, to
    # a caller that has already decided which one it is asking about — and
    # carrying them in eight rows is the cost the search exists to remove.
    hit = result["matches"][0]
    assert set(hit) == {"name", "id", "type", "path", "domain",
                        "taxonomy_refs", "agents"}
    assert "fingerprint" not in json.dumps(result["matches"])


def test_a_search_that_matches_nothing_says_zero_and_not_null(
        daemon_factory, tmp_path, monkeypatch):
    """"I looked and there is none" is a finding; "nobody looked" is not, and
    the two must not render the same. This is the 0 the neighbouring tests keep
    apart from None — asserted here on the side where 0 is the true answer, so
    that a future edit removing the distinction fails in both directions."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "shared-memory", "id": "x", "path": "skills/shared-memory"},
    ])
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.search", "params": {"query": "nothing-like-this"}})["result"]
    assert result["matched"] == 0
    assert result["matches"] == []
    assert result["catalogue_size"] == 1


def test_an_empty_query_is_refused_rather_than_answered_with_the_catalogue(
        daemon_factory, tmp_path, monkeypatch):
    """The tempting implementation returns everything for `""`, which is the
    catalogue read the tool exists to replace — and the caller cannot tell it
    asked for nothing."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "shared-memory", "id": "x", "path": "skills/shared-memory"},
    ])
    daemon = daemon_factory(a_resource("thing-one"))
    for query in ("", "   ", None):
        params = {} if query is None else {"query": query}
        result = daemon.handle({"method": "ability.search", "params": params})["result"]
        assert result["matched"] is None, query
        assert result["matches"] is None, query
        assert result["refused"] == "EMPTY_QUERY", query
        # Not None: the catalogue IS materialized, so its size is known and is
        # what tells the caller how much it just declined to read.
        assert result["catalogue_size"] == 1, query


def test_a_search_with_no_projection_is_null_and_not_an_empty_catalogue(
        daemon_factory, tmp_path, monkeypatch):
    """The reasoning `ability.tree` and `ability.list` already carry, applied
    where it is easiest to lose: a search with nothing to search FROM would
    otherwise return the same `[]` as a real search that found nothing."""
    empty = tmp_path / "state"
    empty.mkdir()

    class Paths:
        def root(self, name):
            assert name == "state"
            return empty

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.search", "params": {"query": "anything"}})["result"]
    assert result["matched"] is None and result["matches"] is None
    assert result["catalogue_size"] is None
    assert "no projection is not an empty catalogue" in result["note"]
    assert result["registry_status"] == "NOT_MATERIALIZED"


def test_a_search_limit_is_clamped_and_the_value_used_is_reported(
        daemon_factory, tmp_path, monkeypatch):
    """A malformed limit is a reason not to honour the request, not a reason to
    refuse a search that could have been answered — and the caller has to be
    able to see which of the two happened."""
    _state_root(tmp_path, monkeypatch, [
        {"name": f"ability-{n:02d}", "id": f"x/{n}", "path": f"skills/a{n}"}
        for n in range(20)
    ])
    daemon = daemon_factory(a_resource("thing-one"))
    ask = lambda limit: daemon.handle(  # noqa: E731
        {"method": "ability.search",
         "params": {"query": "ability", **({} if limit is None else {"limit": limit})}})["result"]

    assert ask(None)["limit"] == daemon_mod.DEFAULT_ABILITY_SEARCH_LIMIT
    assert ask("many")["limit"] == daemon_mod.DEFAULT_ABILITY_SEARCH_LIMIT
    assert ask(3)["returned"] == 3 and ask(3)["matched"] == 20
    assert ask(3)["truncated"] is True
    assert ask(999)["limit"] == daemon_mod.MAX_ABILITY_SEARCH_LIMIT
    # Not 0 and not a refusal: a limit of zero rows is not a limit, it is a
    # request to be told nothing, and the answer says what was actually done.
    assert ask(0)["returned"] == 1
    assert daemon_mod.MAX_ABILITY_SEARCH_LIMIT < 51, (
        "the ceiling exists so that one search cannot become the catalogue read "
        "it replaces; a ceiling at or above the catalogue size does not do that"
    )


def test_a_search_matches_the_repository_name_as_well_as_the_ability_name(
        daemon_factory, tmp_path, monkeypatch):
    """A caller usually knows the repository it saw and not the ability's own
    name — and a query is not a digest, so the fingerprint is deliberately not
    searched."""
    _state_root(tmp_path, monkeypatch, [
        {"name": "convergence", "id": "ProfessorZhi/marketops",
         "path": "worktrees/marketops/repository-convergence",
         "fingerprint": "sha256:deadbeefcafe"},
    ])
    daemon = daemon_factory(a_resource("thing-one"))
    for query in ("MARKETOPS", "repository-convergence", "Convergence"):
        result = daemon.handle({"method": "ability.search",
                                "params": {"query": query}})["result"]
        assert result["matched"] == 1, query
    # The fingerprint is in the row the search reads and not in what it matches:
    # a hit returned for a reason the caller cannot see in the result.
    assert daemon.handle({"method": "ability.search",
                          "params": {"query": "deadbeef"}})["result"]["matched"] == 0


def test_the_four_fast_path_methods_are_the_ones_a_bootstrap_needs(daemon_factory):
    """The surface itself, asserted rather than documented.

    A bootstrap that has to enumerate the catalogue before it can call anything
    is not a bootstrap. These four are what an Agent needs to locate itself, find
    the few abilities that could own its question, look at one of them, and ask
    whether this node can do it now — in that order, none of them requiring the
    others' full output.
    """
    daemon = daemon_factory(a_resource("thing-one"))
    names = daemon.handler_map()
    assert {"context.current", "ability.search", "ability.inspect",
            "ability.resolve"} <= set(names)
    assert set(daemon._methods()) == set(names)


def test_ability_resolve_refuses_to_invent_the_runtime_it_is_asked_about(
        daemon_factory):
    """There is no default, and the reason is a fact about this process rather
    than a preference: the host profile states the managed-runtime registry is
    empty, so there is no declaration of installed runtimes to default FROM. A
    default here would print a guess about the caller as the caller's own
    answer."""
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": "ability.resolve",
                              "params": {"ability": "runtime-management"}})
    assert response["ok"] is False
    assert response["error"]["code"] == "ValueError"
    assert "no declaration of which Agent runtimes are installed" in response["error"]["message"]

    empty = daemon.handle({"method": "ability.resolve",
                           "params": {"ability": "", "runtime": "claude-code"}})
    assert empty["ok"] is False
    assert "missing required parameter 'ability'" in empty["error"]["message"]


def test_ability_resolve_supplies_three_inputs_and_names_the_other_five(
        daemon_factory):
    """The whole point of putting this on the node surface is that it answers
    WITHOUT the rest — and says which ones it did not have. `inputs_seen` is
    empty, and that is not a defect: the eight are documents (a catalogue, a
    device's capabilities, a policy), and the three required scalars are not
    among them. So the honest answer is UNKNOWN naming five questions, which is
    still useful: it says what to go and find."""
    daemon = daemon_factory(a_resource("thing-one"))
    result = daemon.handle({"method": "ability.resolve",
                            "params": {"ability": "runtime-management",
                                       "runtime": "claude-code"}})["result"]

    assert result["status"] == "UNKNOWN"
    assert result["inputs_seen"] == []
    assert result["inputs_missing"] == [
        "ability", "device_capability", "device_profile", "device_binding",
        "agent_runtime", "runtime_health", "policy", "task_context"]
    # Provenance for the three scalars is kept OUT of the kernel's two lists —
    # a field whose job is to say how much of the intersection was evaluated
    # must not start carrying a second kind of thing.
    assert result["asked_as"]["runtime_from"] == "caller"
    assert result["asked_as"]["runtime"] == "claude-code"
    assert result["asked_as"]["device_ref"] == daemon.registry.device_ref
    assert result["asked_as"]["device_ref_from"] == "serverosd"
    assert result["device_ref"] == daemon.registry.device_ref


def test_ability_resolve_keeps_the_verb_out_of_the_catalogue_key(daemon_factory):
    """`<id>#<verb>` splits once, here as everywhere: the ability is the identity
    that must not change when a caller asks about an operation."""
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "ability.resolve",
         "params": {"ability": "runtime-management#restart",
                    "runtime": "claude-code",
                    "device": "device:elsewhere-01"}})["result"]
    assert result["ability"] == "runtime-management"
    assert result["operation"] == "restart"
    # A device the caller named is the caller's claim, and is labelled as such:
    # this surface does not adopt it as its own observation.
    assert result["device_ref"] == "device:elsewhere-01"
    assert result["asked_as"]["device_ref_from"] == "caller"


def test_a_malformed_resolve_request_is_an_error_and_a_no_is_an_answer(
        daemon_factory):
    """`resolver.py`'s own distinction, kept at the layer above it. A "no" is
    returned; only a form it cannot parse raises — and the node surface does not
    launder either one into the other: a malformed request is an ERROR, and a
    resolved-to-UNKNOWN request is a RESULT the caller can read."""
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": "ability.resolve",
                              "params": {"ability": "x#a#b",
                                         "runtime": "claude-code"}})
    assert response["ok"] is False
    assert response["error"]["code"] == "ValueError"
    assert "more than one operation" in response["error"]["message"]

    # …and a request the kernel merely cannot answer is the other thing: OK,
    # with UNKNOWN and the reason inside it. A caller that got an error for a
    # "no" would go looking for a bug in its own syntax.
    answered = daemon.handle({"method": "ability.resolve",
                              "params": {"ability": "x", "runtime": "claude-code"}})
    assert answered["ok"] is True
    assert answered["result"]["status"] == "UNKNOWN"


def test_context_current_will_not_infer_the_item_from_its_own_directory(
        daemon_factory, tmp_path, monkeypatch):
    """The caller's cwd is a parameter with no default, on purpose.

    `serverosd` runs from wherever its unit started it. Inferring the Item from
    that directory would answer "which Item am I in" with a directory the caller
    has never been in — and a context guessed from a cwd is how an artifact
    lands in the wrong Item."""
    empty = tmp_path / "state"
    empty.mkdir()

    class Paths:
        names = ("state", "workspace")

        def root(self, name):
            return empty if name == "state" else tmp_path / "workspace"

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "context.current"})["result"]

    assert result["cwd"] is None
    assert result["inferred_item"] is None
    assert "will not infer one from the daemon's own directory" in result["inference_note"]
    # The roots are still answered: they come from the contract and never from
    # cwd, which is why half the question can be answered and the other half
    # refused.
    assert result["roots"] == {"state": str(empty), "workspace": str(tmp_path / "workspace")}
    assert set(result["state_runtime"]) == set(daemon_mod.paths_mod.STATE_SERVEROS_RUNTIME)


def test_context_current_infers_the_item_from_the_cwd_the_caller_stated(
        daemon_factory, tmp_path, monkeypatch):
    """A cwd under the worktree layout infers its Item; a cwd outside every
    layout is UNKNOWN with the reason, rather than a plausible-looking Item."""
    # Named with a `root_` prefix so the class bodies below can read them: a
    # class body that assigns `worktrees` cannot also READ a `worktrees` from
    # the enclosing function, because the assignment makes it a name the body
    # is itself defining.
    root_worktrees = tmp_path / "workspace" / "worktrees"
    inside = root_worktrees / "serveros" / "SRV-0011"
    (inside / "runtime").mkdir(parents=True)
    elsewhere = tmp_path / "scratch"
    elsewhere.mkdir()

    real_infer = daemon_mod.resolver_mod.infer_item

    class Paths:
        names = ("state",)

        def root(self, name):
            return tmp_path / "state"

    class Roots:
        worktrees = root_worktrees
        test_envs = tmp_path / "workspace" / "test-envs"
        artifacts_root = tmp_path / "artifacts"

        @classmethod
        def from_env(cls):
            return cls

    # Attributes assigned AFTER the class body on purpose: inside it, `Roots`
    # is a name the body is itself defining, so `Roots = Roots` is a NameError
    # rather than a reference to the one above. The real `infer_item` is the
    # point of this test — the daemon is being checked for asking the resolver
    # the caller's cwd, not for re-implementing the layout rules.
    class Shim:
        pass

    Shim.Roots = Roots
    Shim.infer_item = staticmethod(real_infer)

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    monkeypatch.setattr(daemon_mod, "resolver_mod", Shim)
    daemon = daemon_factory(a_resource("thing-one"))

    # Deeper paths still resolve: the check is on position, not on the path
    # ending there — the Item is inferred from a directory INSIDE a Run.
    result = daemon.handle({"method": "context.current",
                            "params": {"cwd": str(inside / "runtime")}})["result"]
    assert result["inferred_item"] == "SRV-0011"
    assert result["cwd"] == str(inside / "runtime")
    assert result["inference_note"] == "SRV-0011"

    # A cwd outside every layout is UNKNOWN with the reason. The resolver
    # RAISES here rather than returning None — "cannot infer an Item from
    # <path>" names the path the caller gave, which is what makes it actionable
    # — and the answer reports it: `inferred_item` is null and the reason is in
    # `inference_note`, so a caller can branch on the fact instead of parsing a
    # string.
    outside = daemon.handle({"method": "context.current",
                             "params": {"cwd": str(elsewhere)}})["result"]
    assert outside["inferred_item"] is None
    assert outside["inference_note"].startswith("UNKNOWN (ValueError:")
    assert "cannot infer an Item from" in outside["inference_note"]
    assert str(elsewhere) in outside["inference_note"]


def test_context_current_reports_an_inference_failure_instead_of_raising(
        daemon_factory, tmp_path, monkeypatch):
    """An unreadable cwd is a fact about the question, not a crash: the caller
    still needs the roots, and the failure is named rather than swallowed."""
    class Paths:
        names = ("state",)

        def root(self, name):
            return tmp_path / "state"

    class Boom(Exception):
        pass

    class Roots:
        @classmethod
        def from_env(cls):
            raise Boom("no root contract")

    class Shim:
        @staticmethod
        def infer_item(roots, cwd):
            raise AssertionError("unreachable: from_env raised first")

    Shim.Roots = Roots  # see the note on the same pattern above

    monkeypatch.setattr(daemon_mod.paths_mod, "Paths", Paths)
    monkeypatch.setattr(daemon_mod, "resolver_mod", Shim)
    result = daemon_factory(a_resource("thing-one")).handle(
        {"method": "context.current",
         "params": {"cwd": str(tmp_path)}})["result"]

    assert result["inferred_item"] is None
    assert result["inference_note"] == "UNKNOWN (Boom: no root contract)"
    assert result["roots"] == {"state": str(tmp_path / "state")}


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


def test_no_memory_provider_import_reaches_the_daemon():
    """§61, and the enforcement `memory/provider-contract.yaml` names.

    THAT FILE SAYS:

        enforcement: "the daemon's imports are checked by test; a provider
                      import in `runtime/daemon.py` fails the suite."

    and nothing performed that check. The test above searches the source for
    three substrings — `subprocess.run(["git"`, `feishu`, `shutil.rmtree` —
    which is a check on text, not on imports. `import openviking` in
    `runtime/daemon.py` would satisfy it, and so would any provider, because
    none of their names appear in the list.

    So the enforcement is written as what it was declared to be: parse the
    module, look at what it actually imports, and refuse a provider.

    Parsing rather than matching text matters in both directions here. A
    substring search reports a false positive on the word "feishu" inside a
    docstring — the module discusses Feishu in prose — and a false negative on
    any import written in a form the list did not anticipate, such as
    `from x import y` or `importlib.import_module`.
    """
    import ast

    tree = ast.parse((RUNTIME / "daemon.py").read_text())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr in ("import_module", "__import__")):
            # A dynamic import is exactly how this rule would be evaded, by
            # accident as easily as on purpose.
            imported.add("<dynamic import>")

    # The provider namespaces. A provider is a thing the daemon may ASK ABOUT
    # over IPC, never a thing it may import: an embedding call inside the tick
    # loop makes loop latency a function of a model and its availability.
    forbidden = {"openviking", "memory", "numpy", "torch", "sentence_transformers"}
    reached = sorted(imported & forbidden)
    assert not reached, (
        f"`runtime/daemon.py` imports {reached}. `memory/provider-contract.yaml` "
        f"requires the daemon to reach a provider over IPC and never to import "
        f"one.")

    # And the positive control. This assert would pass on a parser that found
    # nothing at all, which is how a purity test quietly stops testing.
    assert imported, "the import scan found no imports in a module that has them"
    assert "json" in imported or "argparse" in imported, sorted(imported)


# ---------------------------------------------------------------------------
# §14 · a missing parameter is refused by name, not by KeyError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,param", [
    ("runtime.status", "id"),
    ("runtime.explain", "id"),
    ("path.resolve", "ref"),
    ("path.admit", "path"),
    ("ability.resolve", "ability"),
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
    ("ability.resolve", "ability"),
])
def test_an_empty_string_parameter_is_refused_the_same_way(daemon_factory, method, param):
    """`""` is not an id. A falsy value that reaches the registry produces the
    same bare error one layer down, so it is refused at the boundary."""
    daemon = daemon_factory(a_resource("thing-one"))
    response = daemon.handle({"method": method, "params": {param: ""}})
    assert response["ok"] is False
    assert "missing required parameter" in response["error"]["message"]


# ---------------------------------------------------------------------------
# §18/§27 · the resource anomaly surface, and what "nothing was measured" says
# ---------------------------------------------------------------------------


class DeadHost:
    """A host whose every read fails, driven through the REAL inventory.

    The point of using `Inventory` over a dead host rather than a hand-built
    fake document is that the failure SHAPES under test are the producer's own.
    A fake that returns `{}` tests the fake: it found a `KeyError` in
    `hygiene.containers()` on the first attempt, when the real `docker_list`
    returns `{"state": "UNAVAILABLE", "reason": ...}` and the guard expects
    exactly that. §27 is about what the real producer does when the provider is
    down, so the real producer is what this drives.
    """

    def read(self, args, timeout):
        return -1, "", f"`{' '.join(args[:2])}` is not reachable in this test"

    def read_text(self, path):
        return None

    def statvfs(self, path):
        return None

    def exists(self, path):
        return False


def _dead_inventory():
    return inventory_mod.Inventory(DeadHost())


def test_an_unmeasured_node_does_not_answer_that_it_has_no_resource_anomalies(
        daemon_factory):
    """§27, at the surface: unavailable is not zero.

    The failure this forbids has no error in it. A node that cannot read Docker
    or `/proc` and answers "0 anomalies, 15 codes clean" is wrong in the one
    direction nobody investigates, and this round's §27 exists because the
    resource layer was one `None` comparison away from doing exactly that.

    BOTH halves of the host are dead here, and that is the whole point of the
    fixture: `_dead_inventory()` kills the Docker, disk and memory reads, and
    `containers={}` kills the probe host §15's comparison goes through. An
    inventory-only dead host still has a working probe host, so exactly one
    code — `DESIRED_ACTUAL_MISMATCH` — graded, correctly, because a comparison
    really did run and really did find the registration in its desired state.
    That version of this test asserted the wrong thing and would have passed
    for the wrong reason had the comparison been skippable.
    """
    daemon = daemon_factory(a_resource("thing-one"), containers={},
                            inventory=_dead_inventory())
    response = daemon.handle({"method": "resource.anomaly.list", "params": {}})
    assert response["ok"] is True
    result = response["result"]

    assert result["coverage"]["graded"] == 0, result["codes_graded"]
    assert result["codes_graded"] == []
    assert result["codes_emitted"] == []
    assert len(result["codes_not_evaluated"]) == result["coverage"]["of"] == 15
    # Every ungraded code names why, and the reasons are about a provider, not
    # about a threshold: an empty `why` would be the declaration-layer shape of
    # "we did not look" reported as "we looked and found nothing".
    assert all(row["why"] for row in result["codes_not_evaluated"])
    assert all(row["needs"] for row in result["codes_not_evaluated"])


def test_a_comparison_that_really_ran_is_still_graded(daemon_factory):
    """The other half of the rule above, and why it is not "grade nothing".

    Same dead inventory, but the probe host answers this time: one container,
    registered `desired: running`, found running. `DESIRED_ACTUAL_MISMATCH` is
    then graded with zero signals — which is the correct reading and the one
    §27 is not asking anybody to stop reporting. Without this test the rule
    above would be satisfied by a detector that never grades this code at all,
    and "never graded" is the same silence as "graded clean" with a different
    label on it.
    """
    daemon = daemon_factory(a_resource("thing-one"), inventory=_dead_inventory())
    result = daemon.handle({"method": "resource.anomaly.list", "params": {}})["result"]
    assert result["codes_graded"] == ["DESIRED_ACTUAL_MISMATCH"], result["codes_graded"]
    assert result["codes_emitted"] == []
    assert all(row["code"] != "DESIRED_ACTUAL_MISMATCH"
               for row in result["codes_not_evaluated"])


def test_the_two_runtime_codes_are_ungraded_rather_than_clean_when_docker_is_down(
        daemon_factory):
    """The narrower half, and the one that was a live defect in this round.

    `RUNTIME_UNOWNED` and `RUNTIME_ORPHANED` are graded from the container
    hygiene report. When Docker is unreachable that report classifies nothing,
    and the first version of this read "classified nothing" as "nothing is
    unowned" — the two codes came back in `codes_graded` on a call where
    nothing had looked at a container.
    """
    daemon = daemon_factory(a_resource("thing-one"), inventory=_dead_inventory())
    result = daemon.handle({"method": "resource.anomaly.list", "params": {}})["result"]
    ungraded = {row["code"]: row["why"] for row in result["codes_not_evaluated"]}
    for code in ("RUNTIME_UNOWNED", "RUNTIME_ORPHANED"):
        assert code in ungraded, code
        assert "unreachable" in ungraded[code] or "could not" in ungraded[code], ungraded[code]
        assert code not in result["codes_graded"]


# ---------------------------------------------------------------------------
# §Q2 · the digest handlers the daemon advertises are reachable from the object
#        the request actually arrives at
# ---------------------------------------------------------------------------


def test_no_private_attribute_shadows_a_method_on_the_daemon(daemon_factory):
    """A `self._name = None` cache in `__init__` over a method called `_name`
    turns every call site into `TypeError: 'NoneType' object is not callable`.

    Not hypothetical. §Q2's delivery cursor was written that way, the full
    suite stayed green because nothing called the new handlers, and it surfaced
    on a live node — as a refusal from a daemon that had already been promoted.
    A gate written only for this one name would have been worth one defect; this
    one is general, so the next cache lands on it at the moment it is written
    rather than at the moment it is installed.
    """
    daemon = daemon_factory()
    kind = type(daemon)
    methods = {name for name in dir(kind)
               if not name.startswith("__")
               and callable(getattr(kind, name, None))}
    shadowed = sorted(set(vars(daemon)) & methods)
    assert shadowed == [], (
        f"{shadowed} are instance attributes AND methods of the same name; "
        f"`self.{shadowed[0]}(...)` calls the attribute, not the method")


def test_the_digest_handlers_answer_through_the_real_dispatch(daemon_factory):
    """The three methods §Q2 advertises, called the way the socket calls them.

    Through `handle` and not through `_r_*`, because the map in `_methods()` is
    itself part of the declared surface: a handler that works when called
    directly and is unreachable over the wire is not a wired handler.
    """
    daemon = daemon_factory()

    taken = daemon.handle(
        {"method": "alert.digest-baseline", "params": {}})["result"]
    assert taken["taken"] is True
    assert taken["baseline_keys"] == 0
    again = daemon.handle(
        {"method": "alert.digest-baseline", "params": {}})["result"]
    assert again["taken"] is False
    assert again["baseline_at"] == taken["baseline_at"]

    document = daemon.handle({"method": "alert.digest", "params": {}})["result"]
    assert document["baseline_at"] == taken["baseline_at"]
    assert document["counts"] == {"NEW": 0, "ESCALATED": 0,
                                  "RECOVERED": 0, "REOPENED": 0}
    assert document["decision"]["send"] is False

    recorded = daemon.handle(
        {"method": "alert.digest-record",
         "params": {"generation": document["decision"]["generation"],
                    "state": "DELIVERED", "shown": []}})["result"]
    assert recorded["delivered"] is True


def test_a_digest_composed_on_an_empty_register_is_quieter_not_louder(daemon_factory):
    """Two ways to be loud by accident, both closed.

    An unreadable register must not read as "everything resolved" and produce a
    card full of recoveries, and a missing baseline must not read as "everything
    is new". An empty register here produces neither: no rows, no send.
    """
    daemon = daemon_factory()
    daemon.handle({"method": "alert.digest-baseline", "params": {}})
    document = daemon.handle({"method": "alert.digest", "params": {}})["result"]
    assert document["rows"] == []
    assert document["shown"] == []
    assert document["more"] == 0
    assert document["silent_resolutions"] == 0
    assert document["decision"]["send"] is False
    assert "changed" in document["decision"]["why"]


# ---------------------------------------------------------------------------
# §3/V28 · the receipt keeps the message the send produced
# ---------------------------------------------------------------------------


def _a_status_card():
    """One rendered card, small enough to read. Every value is about nobody."""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"template": "grey",
                   "title": {"tag": "plain_text", "content": "本机 · 资源播报"}},
        "body": {"elements": [{"tag": "markdown", "content": "CPU 3.2%"}]},
    }


def _frozen_status(card):
    return card_delivery_mod.freeze_card(
        "status", card, at="2026-09-12T00:00:00+00:00")


def test_a_delivery_keeps_the_message_the_sender_reported(daemon_factory):
    """The message id does not exist until the card has been sent, so the
    receipt is the only place it can be kept — and the daemon is the only
    writer of the ledger.

    A daemon that dropped this parameter would leave every receipt looking
    complete while making «update that card in place» impossible to carry out,
    which is the failure mode this whole slice exists to close.
    """
    daemon = daemon_factory()
    card = _a_status_card()
    answer = daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "status", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_status(card),
                   "remote_id": "om_from_the_transport"}})
    assert answer["ok"] is True, answer
    assert answer["result"]["entry"]["remote_id"] == "om_from_the_transport"
    # Read back through the other RPC, because a value that is written into the
    # answer without reaching the document is the same defect one hop later.
    read_back = daemon.handle({"method": "card.deliveries",
                               "params": {"kind": "status"}})["result"]
    assert read_back["last_delivered"]["remote_id"] == "om_from_the_transport"


def test_a_send_the_transport_could_not_name_keeps_the_absence(daemon_factory):
    """The transport reports "the answer carried no id" rather than refusing the
    send, so the daemon has to be able to file that. It is stored as `None` and
    not as `''`: the key is present, so a reader can tell «this send has no
    message» from «nobody wrote this field», and the two need different
    answers from whoever looks next.
    """
    daemon = daemon_factory()
    card = _a_status_card()
    for absent in (None, "", "   "):
        answer = daemon.handle({
            "method": "card.record-delivery",
            "params": {"kind": "status", "state": "DELIVERED", "card": card,
                       "frozen": _frozen_status(card), "remote_id": absent}})
        assert answer["ok"] is True, answer
        assert answer["result"]["entry"]["remote_id"] is None
    ledger = json.loads(
        (Path(daemon.state_dir) / "cards" / "delivery.json").read_text(
            encoding="utf-8"))
    assert all("remote_id" in row for row in ledger["entries"])


def test_a_card_that_does_not_hash_to_the_version_is_refused(daemon_factory):
    """A receipt's whole value is that its version and the payload that went out
    are the same thing. The pair is checked here rather than trusted, because
    the caller is a sender that composed both — and a receipt that accepted a
    mismatched pair would certify a card that was never sent."""
    daemon = daemon_factory()
    edited = _a_status_card()
    edited["header"]["template"] = "red"
    answer = daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "status", "state": "DELIVERED", "card": edited,
                   "frozen": _frozen_status(_a_status_card()),
                   "remote_id": "om_x"}})
    assert answer["ok"] is False, answer
    assert "two different payloads" in json.dumps(answer)
    assert daemon.card_ledger().entries() == []


def test_a_receipt_against_something_that_is_not_a_message_is_refused(
        daemon_factory):
    """`oc_` names a conversation, and a card filed against one would make every
    later patch aim at a chat. The refusal must not repeat the identifier, and
    it must not have written anything on its way to refusing."""
    daemon = daemon_factory()
    card = _a_status_card()
    answer = daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "status", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_status(card),
                   "remote_id": "oc_a_conversation"}})
    assert answer["ok"] is False, answer
    assert "a_conversation" not in json.dumps(answer)
    assert daemon.card_ledger().entries() == []


#: One instant, held still, so that the fourteen-day window is a fact about
#: these tests rather than about when they ran: the receipt is filed at it and
#: the verdict is asked at an instant derived from it.
EPOCH = 1789000000.0
FILED_AT = datetime.datetime.fromtimestamp(EPOCH, datetime.timezone.utc)


def _later(**over):
    return (FILED_AT + datetime.timedelta(**over)).isoformat()


def _frozen_alert(card):
    """The same card, sent as an incident. The kind is in the version, so a
    receipt for this one cannot be confused with a status card's."""
    return card_delivery_mod.freeze_card("alert", card, at=_later())


def test_a_receipt_keeps_the_episode_it_was_filed_under(daemon_factory):
    """An incident receipt without its episode is a receipt for «some card».

    The next hour — and the next day — look the same incident up by that word,
    so a daemon that dropped it would leave every receipt looking complete while
    making «same episode, one card» unanswerable, which leaves a second card as
    the only way forward.
    """
    daemon = daemon_factory(clock=lambda: EPOCH)
    card = _a_status_card()
    answer = daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "alert", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_alert(card),
                   "episode": "abc123def456", "remote_id": "om_incident"}})
    assert answer["ok"] is True, answer
    assert answer["result"]["entry"]["episode"] == "abc123def456"
    # Read back through the other RPC: a value written into the answer without
    # reaching the document is the same defect one hop later.
    read_back = daemon.handle({"method": "card.deliveries",
                               "params": {"kind": "alert"}})["result"]
    assert read_back["last_delivered"]["episode"] == "abc123def456"


def test_an_incident_receipt_that_names_no_episode_is_refused(daemon_factory):
    """The rule lives on the ledger's side of this call — `record` is the only
    door a row goes through — so the daemon passes on what the sender said and
    writes nothing when the answer is «no episode»."""
    daemon = daemon_factory(clock=lambda: EPOCH)
    card = _a_status_card()
    answer = daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "alert", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_alert(card), "remote_id": "om_incident"}})
    assert answer["ok"] is False, answer
    assert daemon.card_ledger().entries() == []


def test_the_incident_rpc_answers_out_of_the_ledger(daemon_factory):
    """V29, over the socket: an episode that already has a message is patched
    and not sent again — and the answer comes from what was SENT, which is the
    only document that survives the hour and the day boundary."""
    daemon = daemon_factory(clock=lambda: EPOCH)
    card = _a_status_card()
    daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "alert", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_alert(card), "episode": "abc123def456",
                   "remote_id": "om_incident"}})
    answer = daemon.handle({
        "method": "card.incident",
        "params": {"kind": "alert", "episode": "abc123def456",
                   "at": _later(hours=6)}})["result"]
    assert answer["action"] == "PATCH_MESSAGE", answer
    assert answer["reason"] == "IN_HAND", answer
    assert answer["remote_id"] == "om_incident", answer

    # …and an episode nobody has sent for is a first send, asked the same way.
    other = daemon.handle({
        "method": "card.incident",
        "params": {"kind": "alert", "episode": "bbb222ccc333",
                   "at": _later(hours=6)}})["result"]
    assert other["action"] == "NEW_MESSAGE", other
    assert other["reason"] == "NEVER_SENT", other


def test_the_incident_rpc_is_a_read(daemon_factory):
    """A verdict is not a send, a patch or a note-to-self: asking it twice has
    to leave the ledger exactly as it was, or the second ask would be answering
    a question the first one changed."""
    daemon = daemon_factory(clock=lambda: EPOCH)
    card = _a_status_card()
    daemon.handle({
        "method": "card.record-delivery",
        "params": {"kind": "alert", "state": "DELIVERED", "card": card,
                   "frozen": _frozen_alert(card), "episode": "abc123def456",
                   "remote_id": "om_incident"}})
    before = json.dumps(daemon.card_ledger().entries(), sort_keys=True)
    for _ in range(2):
        daemon.handle({"method": "card.incident",
                       "params": {"kind": "alert", "episode": "abc123def456",
                                  "at": _later(hours=6)}})
    assert json.dumps(daemon.card_ledger().entries(), sort_keys=True) == before


def test_the_incident_rpc_refuses_what_it_cannot_answer(daemon_factory):
    """Two refusals, and both are refusals rather than guesses: a card family
    that is about a workday has no episode to look up, and a verdict asked
    about «the episode» names none — a call that answered either by defaulting
    would aim a patch at whichever incident it found first."""
    daemon = daemon_factory(clock=lambda: EPOCH)
    for params in ({"kind": "status", "episode": "abc123def456"},
                   {"kind": "alert", "episode": ""},
                   {"kind": "alert"}):
        answer = daemon.handle({"method": "card.incident", "params": params})
        assert answer["ok"] is False, answer
    assert daemon.card_ledger().entries() == []
