"""A pytest run placed inside a systemd user cgroup on the host that runs it.

WHY THIS MODULE IS NOT A SHELL WRAPPER
--------------------------------------
The launcher behind the 2026-10-14 incident had two protections, and both were
stories. `timeout` on the local `ssh` bounds the *client*; the remote process is
reparented the moment the session ends, so the timeout elapsed over a process it
no longer owned. `trap 'rm -rf "$SB"' EXIT` runs when the shell exits, and the
shell exited early: it removed a directory and left the process behind. Neither
could have worked, because neither was enforced by anything that outlives the
session.

What does outlive the session is the kernel. A systemd *user* transient service
is a cgroup, and a cgroup's `memory.max`, `memory.high`, `cpu.max` and
`RuntimeMaxUSec` are enforced by the kernel against every process in it,
including the ones nobody knows about. So this module does one thing:

    it asks systemd for a bounded unit, and then it reads the bound back out
    of the kernel and refuses to call the run a success unless it holds.

The last clause is the whole design. A runner that starts a bounded unit and
trusts it is the *same* runner that trusted `timeout`. Here an unreadable
`memory.max` is not "no news" — it is a failure, and a unit whose limit drifted
is a failure even when pytest exited zero. Uncertainty ends the run.

WHAT IT DOES NOT DO
-------------------
It never writes a property onto a slice. `user.slice` and `root.slice` carry
every other account on a shared host, so capping one would answer a single
runaway by making it everyone's problem; `build_launch_argv` is built so that no
property it emits can name one.

It never carries the caller's environment into the unit. systemd starts the
service with the user manager's own environment, so nothing in this process —
`AWS_*`, a token, a cookie — can reach the payload or the receipt. The receipt
records the command line and the limits; it never records an environment.

It never falls back. When the user manager or the memory controller is absent,
the answer is `EXIT_PREFLIGHT` and no launch, because the alternative to a
bounded run is not an unbounded run — it is no run.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "serveros/constrained-test-run.v1"

GIB = 1024 ** 3

#: The exit codes a caller switches on. Non-zero is the norm here: this runner
#: earns a zero, it does not default to one.
EXIT_OK = 0
EXIT_REFUSED = 2        # the request itself is not one this runner may serve
EXIT_PREFLIGHT = 3      # no user systemd, or no memory controller to bound with
EXIT_ENVELOPE = 4       # the kernel does not have the limits we asked for
EXIT_TREE = 5           # a process of this run is outside the unit's cgroup
EXIT_EVIDENCE = 6       # the receipt could not be written
EXIT_UNVERIFIED = 7     # the run finished without ever being observed alive

#: The profiles. The default is the issue's own number; the two large ones are
#: the authorised escalation and require an explicit grant to be resolved.
#:
#: `tasks_max` is not in the issue and is here on purpose: memory bounds how
#: large a runaway gets, and this bounds how many of them there can be. A fork
#: storm that stays under 4 GiB is still a fork storm, and the incident's
#: descendants were exactly the thing the launcher could not see.
PROFILES: dict[str, dict] = {
    "default": {
        "memory_high_bytes": 3 * GIB, "memory_max_bytes": 4 * GIB,
        "cpu_quota_percent": 400, "runtime_max_sec": 600,
        "tasks_max": 2048, "requires_authorization": False,
    },
    "large-8": {
        "memory_high_bytes": 6 * GIB, "memory_max_bytes": 8 * GIB,
        "cpu_quota_percent": 400, "runtime_max_sec": 1800,
        "tasks_max": 4096, "requires_authorization": True,
    },
    "large-16": {
        "memory_high_bytes": 12 * GIB, "memory_max_bytes": 16 * GIB,
        "cpu_quota_percent": 400, "runtime_max_sec": 1800,
        "tasks_max": 8192, "requires_authorization": True,
    },
}

_LIMIT_FIELDS = ("memory_high_bytes", "memory_max_bytes", "cpu_quota_percent",
                 "runtime_max_sec", "tasks_max")

#: A unit name this runner generated. Anchored so that a name that came from
#: anywhere else — a caller, a config file — cannot be passed off as one of ours.
UNIT_NAME_RE = re.compile(r"^serveros-test-[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9]{1,32}\.service$")

#: The cgroup files that carry the envelope, in the kernel's own units. These
#: are read back while the unit is alive; see `verify_envelope`.
_CGROUP_FILES = ("memory.max", "memory.high", "cpu.max", "pids.max")

_CPU_PERIOD_USEC = 100_000

_SHOW_PROPERTIES = (
    "LoadState", "ActiveState", "SubState", "Result", "ExecMainCode",
    "ExecMainStatus", "MainPID", "ControlGroup", "MemoryPeak", "MemoryCurrent",
    "MemorySwapPeak", "RuntimeMaxUSec", "MemoryMax", "MemoryHigh",
    "CPUQuotaPerSecUSec", "KillMode", "TasksMax",
)

_REDACTED = "<redacted>"

#: (pattern, replacement). A named key keeps its name so the receipt still
#: reads as a command line; the value after it does not survive.
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(--?(?:token|passwd|password|secret|api[-_]?key|authorization)"
                r"[=\s]+)(\S+)"), rf"\1{_REDACTED}"),
    (re.compile(r"(?i)\b(bearer)\s+\S+"), rf"\1 {_REDACTED}"),
    (re.compile(r"\b(?:gh[pousr]_|sk-)[A-Za-z0-9_\-]{16,}"), _REDACTED),
)


class RunnerRefused(Exception):
    """A request this runner will not serve. Nothing is launched."""


class LimitsRefused(RunnerRefused):
    """A profile that does not exist, or one that needs a grant it does not have."""


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


def resolve_limits(profile: str, *, authorized: bool = False,
                   profiles: dict | None = None) -> dict:
    """The envelope for `profile`, or a refusal.

    An unknown profile is refused rather than defaulted: "4 GiB" is a safe
    answer, and silently giving it to a caller who asked for something else is
    how a run ends up bounded by a number nobody chose.
    """
    table = PROFILES if profiles is None else profiles
    spec = table.get(profile)
    if not isinstance(spec, dict):
        raise LimitsRefused(
            f"{profile!r} is not a declared profile. Known: {sorted(table)}")
    if spec.get("requires_authorization") and not authorized:
        raise LimitsRefused(
            f"profile {profile!r} raises the envelope beyond the default and "
            f"requires an explicit authorisation")
    return {name: spec[name] for name in _LIMIT_FIELDS}


# ---------------------------------------------------------------------------
# Where pytest may be started, and what it may be pointed at
# ---------------------------------------------------------------------------


def validate_worktree(raw, *, home) -> Path:
    """The directory a run is allowed to start in.

    The incident was a property of the *directory*, not of the arguments:
    `pytest.ini` declares `testpaths = tests`, and that declaration only takes
    effect from a repository root. From `$HOME` there is no root to find, so
    pytest falls back to walking the tree it is standing in. Requiring a git
    worktree root is therefore the guard against the whole class, and it is a
    stronger one than any argument check.
    """
    path = Path(str(raw))
    if not path.is_absolute():
        raise RunnerRefused(f"a worktree must be an absolute path, not {raw!r}")
    if path != path.resolve():
        raise RunnerRefused(
            f"{path} is a symlink or has a non-normalised component; a run "
            f"must happen in the directory it names")
    if not path.is_dir():
        raise RunnerRefused(f"{path} is not a directory")
    if path == path.parent:
        raise RunnerRefused(f"{path} is a filesystem root")
    home = Path(str(home))
    if path == home or home.is_relative_to(path):
        raise RunnerRefused(
            f"{path} is {home} or contains it; pytest started from a home "
            f"directory collects the whole tree below it")
    if not (path / ".git").exists():
        raise RunnerRefused(
            f"{path} is not a git worktree root (no .git), so it cannot be "
            f"the place a suite is collected from")
    return path


def validate_selection(worktree: Path, *, all_tests: bool, paths) -> list[str]:
    """The explicit test paths a run may carry, normalised to worktree-relative.

    `all_tests` is the one way to ask for the whole suite, and it is a flag
    because it is the decision that has to be made on purpose. Asking for the
    suite and for a path at the same time is two answers to one question, and
    is refused.
    """
    if all_tests and paths:
        raise RunnerRefused("--all and explicit test paths name two different runs")
    if all_tests:
        return ["tests"]
    if not paths:
        raise RunnerRefused(
            "a run needs explicit test paths under tests/, or --all; an empty "
            "selection means 'collect everything', which is the incident")
    worktree = worktree.resolve()
    tests_root = worktree / "tests"
    selection: list[str] = []
    for raw in paths:
        if ".." in Path(str(raw)).parts:
            raise RunnerRefused(f"{raw!r} traverses; a test path is stated, not computed")
        candidate = Path(str(raw))
        if candidate.is_absolute():
            if not candidate.is_relative_to(worktree):
                raise RunnerRefused(f"{raw} is outside the worktree {worktree}")
            candidate = candidate.relative_to(worktree)
        if not candidate.parts or candidate.parts[0] != "tests":
            raise RunnerRefused(
                f"{raw!r} is not under tests/. `runtime/` and the data root hold "
                f"modules that pytest would collect as tests")
        target = worktree / candidate
        if not target.exists():
            raise RunnerRefused(f"{raw!r} does not exist")
        if target.resolve() != (tests_root / candidate.relative_to("tests")).resolve() \
                and not target.resolve().is_relative_to(tests_root.resolve()):
            raise RunnerRefused(f"{raw!r} resolves outside tests/")
        if target.is_dir():
            selection.append(str(candidate))
            continue
        if not (target.name.startswith("test_") and target.suffix == ".py"):
            raise RunnerRefused(
                f"{raw!r} is not a test module; only test_*.py inside tests/ is runnable")
        selection.append(str(candidate))
    return selection


# ---------------------------------------------------------------------------
# The launch line
# ---------------------------------------------------------------------------


def unit_name(*, nonce: str, epoch: float) -> str:
    """A unique, systemd-safe unit name that is structurally not a slice."""
    stamp = datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"serveros-test-{stamp}-{nonce}.service"


def build_launch_argv(*, unit: str, limits: dict, worktree: Path, pytest_bin: str,
                      test_args, run_dir: Path, systemd_run_bin: str) -> list[str]:
    """The `systemd-run --user` line that creates the bounded unit.

    One property per bound, and `KillMode=control-group` so that the unit is
    also the *kill* unit: the descendants the incident's launcher could not see
    are exactly what control-group stops. `StandardOutput`/`StandardError` go to
    files in the run directory rather than to the journal, because the receipt
    has to be readable without a logging stack.
    """
    run_dir = Path(run_dir)
    properties = [
        f"--property=MemoryHigh={limits['memory_high_bytes']}",
        f"--property=MemoryMax={limits['memory_max_bytes']}",
        f"--property=CPUQuota={limits['cpu_quota_percent']}%",
        f"--property=RuntimeMaxSec={limits['runtime_max_sec']}",
        f"--property=TasksMax={limits['tasks_max']}",
        "--property=KillMode=control-group",
        # TERM first, then KILL after ten seconds. A run that ignores the
        # deadline is stopped anyway; it is never waited on indefinitely.
        "--property=TimeoutStopSec=10",
        "--property=Type=exec",
        f"--property=WorkingDirectory={worktree}",
        f"--property=StandardOutput=file:{run_dir / 'stdout.log'}",
        f"--property=StandardError=file:{run_dir / 'stderr.log'}",
    ]
    argv = [systemd_run_bin, "--user", f"--unit={unit}", *properties, "--",
            pytest_bin, *[str(a) for a in test_args]]
    return argv


def build_show_argv(unit: str, systemctl_bin: str = "systemctl") -> list[str]:
    argv = [systemctl_bin, "--user", "show", unit]
    for name in _SHOW_PROPERTIES:
        argv += ["-p", name]
    return argv


# ---------------------------------------------------------------------------
# Reading the kernel back
# ---------------------------------------------------------------------------


def parse_show(text: str) -> dict:
    """`Key=Value` lines into a mapping. `[not set]` is kept as itself.

    An unset property and an absent one are different answers, and collapsing
    them is how "the limit was not reported" becomes "the limit was fine".
    """
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value
    return out


def parse_proc_cgroup(text) -> str | None:
    """The unified cgroup path from `/proc/<pid>/cgroup`, or None."""
    if not text:
        return None
    for line in str(text).splitlines():
        _, _, path = line.rpartition(":")
        if path:
            return path
    return None


def expected_cgroup_values(limits: dict) -> dict:
    """The envelope as the kernel writes it, so a comparison is exact.

    `cpu.max` is `<quota> <period>` in microseconds; `CPUQuota=400%` is four
    seconds of CPU per second, which the kernel writes as `400000 100000`.
    Comparing against the kernel's own spelling is the point — a comparison
    against systemd's pretty form (`4s`) would be a comparison against a
    formatter.
    """
    return {
        "memory.max": str(limits["memory_max_bytes"]),
        "memory.high": str(limits["memory_high_bytes"]),
        "cpu.max": f"{limits['cpu_quota_percent'] * (_CPU_PERIOD_USEC // 100)} {_CPU_PERIOD_USEC}",
        "pids.max": str(limits["tasks_max"]),
    }


def verify_envelope(expected: dict, observed: dict) -> list[dict]:
    """Every disagreement between the requested envelope and the kernel's.

    A file that could not be read is `LIMIT_UNREADABLE`, not agreement: the
    only honest reading of "I could not see it" is that it was not proven.
    """
    findings: list[dict] = []
    for name in _CGROUP_FILES:
        if name not in expected:
            continue
        want = expected[name]
        got = observed.get(name)
        if got is None:
            findings.append({"code": "LIMIT_UNREADABLE", "property": name,
                             "requested": want, "observed": None})
        elif str(got).strip() != str(want):
            findings.append({"code": "LIMIT_NOT_APPLIED", "property": name,
                             "requested": want, "observed": str(got).strip()})
    return findings


def verify_tree(unit_cgroup: str, pid: int, proc_cgroup_text) -> list[dict]:
    """That this run's process is inside the unit's own cgroup.

    A descendant sub-cgroup is the normal shape and passes. A *parent* does
    not: `app.slice` contains the unit and is not the unit, and a process there
    is a process the unit's kill and its limits do not reach.
    """
    if not unit_cgroup:
        return [{"code": "UNIT_CGROUP_UNKNOWN", "pid": pid, "unit_cgroup": None}]
    path = parse_proc_cgroup(proc_cgroup_text)
    if path is None:
        return [{"code": "PROCESS_CGROUP_UNREADABLE", "pid": pid,
                 "unit_cgroup": unit_cgroup}]
    if path == unit_cgroup or path.startswith(unit_cgroup.rstrip("/") + "/"):
        return []
    return [{"code": "PROCESS_OUTSIDE_UNIT_CGROUP", "pid": pid,
             "unit_cgroup": unit_cgroup, "process_cgroup": path}]


def classify_result(show: dict) -> tuple[str, int]:
    """The unit's outcome and the exit code a caller should see.

    An unrecognised or absent `Result` is never a zero. The run may well have
    passed; the point is that nothing here can tell, and a runner that guesses
    "success" on an unknown unit is the runner this module exists to replace.
    """
    result = (show or {}).get("Result", "")
    status = (show or {}).get("ExecMainStatus", "0")
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        status_int = 1
    if result == "success":
        return "success", status_int
    if result == "timeout":
        return "timeout", 124
    if result == "oom-kill":
        return "oom-kill", 137
    if result == "signal":
        return "signal", 128 + status_int
    if result == "exit-code":
        return "exit-code", status_int
    return "unknown", EXIT_UNVERIFIED


def scrub(text: str) -> str:
    """A receipt is evidence, and evidence does not carry a credential."""
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# ---------------------------------------------------------------------------
# The real systemd
# ---------------------------------------------------------------------------


class Systemd:
    """`systemd-run` and `systemctl --user`, as this module needs them.

    A thin adapter: every method is one command or one file read, so a fake can
    stand in for it without reimplementing policy.
    """

    def __init__(self, *, systemd_run_bin: str = "systemd-run",
                 systemctl_bin: str = "systemctl"):
        self.systemd_run_bin = systemd_run_bin
        self.systemctl_bin = systemctl_bin

    def available(self) -> tuple[bool, str]:
        """Whether a bounded run is possible here at all.

        Three questions, and each must answer yes: are the tools runnable, is
        there a user manager to own the unit, and is the memory controller
        delegated far enough down for a transient unit to carry `memory.max`? A
        node with systemd but no delegation would accept the unit and silently
        not enforce the memory bound, which is the failure this check exists to
        make impossible.

        The manager is asked, not assumed. `systemd-run --user --version` prints
        a version without ever opening the bus, so a host with the tools
        installed and no reachable manager answered yes here and then failed at
        launch — a check reporting a fact it had not established.
        `systemctl --user show-environment` can only answer with the manager on
        the other end of that socket. It is asked twice, a moment apart, because
        on a freshly started session the socket can appear after the binaries.
        """
        try:
            proc = subprocess.run([self.systemd_run_bin, "--version"],
                                  capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{self.systemd_run_bin} is not runnable: {exc}"
        if proc.returncode != 0:
            return False, (f"{self.systemd_run_bin} is not runnable: "
                           f"{proc.stderr.strip()[:200]}")
        why = "the user manager did not answer"
        for attempt in range(2):
            try:
                proc = subprocess.run(
                    [self.systemctl_bin, "--user", "show-environment"],
                    capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"{self.systemctl_bin} is not runnable: {exc}"
            if proc.returncode == 0:
                break
            why = (proc.stderr.strip() or proc.stdout.strip())[:200]
            if attempt == 0:
                time.sleep(0.25)
        else:
            return False, f"no reachable user manager: {why}"
        controllers = Path("/sys/fs/cgroup/cgroup.controllers")
        try:
            text = controllers.read_text()
        except OSError as exc:
            return False, f"cgroup v2 is not readable: {exc}"
        if "memory" not in text.split():
            return False, "the memory controller is not available on this node"
        delegated = Path(
            f"/sys/fs/cgroup/user.slice/user-{os.getuid()}.slice/"
            f"user@{os.getuid()}.service/cgroup.subtree_control")
        try:
            sub = delegated.read_text()
        except OSError as exc:
            return False, f"the user manager's cgroup is not readable: {exc}"
        if "memory" not in sub.split():
            return False, ("the memory controller is not delegated to the user "
                           "manager, so a transient unit could not carry memory.max")
        return True, "ok"

    def launch(self, argv):
        return subprocess.run([str(a) for a in argv], capture_output=True,
                              text=True, timeout=60)

    def unit_snapshot(self, unit: str) -> dict:
        proc = subprocess.run(build_show_argv(unit, self.systemctl_bin),
                              capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return {"LoadState": "not-found"}
        return parse_show(proc.stdout)

    def cgroup_read(self, control_group: str, filename: str):
        try:
            return Path(f"/sys/fs/cgroup{control_group}/{filename}").read_text().strip()
        except OSError:
            return None

    def proc_cgroup(self, pid: int):
        try:
            return Path(f"/proc/{int(pid)}/cgroup").read_text()
        except (OSError, ValueError):
            return None

    def proc_starttime(self, pid: int):
        """Field 22 of `/proc/<pid>/stat`: the tick the process was created.

        `pid` alone is reused by the kernel, so `(pid, starttime)` is the pair a
        reader can replay against the process table later and get the same
        answer. The command name can contain spaces and parentheses, so the
        fields are counted from after the closing `)`.
        """
        try:
            text = Path(f"/proc/{int(pid)}/stat").read_text()
        except (OSError, ValueError):
            return None
        close = text.rfind(")")
        if close < 0:
            return None
        parts = text[close + 1:].split()
        if len(parts) < 20:
            return None
        try:
            return int(parts[19])
        except ValueError:
            return None

    def stop(self, unit: str) -> None:
        subprocess.run([self.systemctl_bin, "--user", "stop", unit],
                       capture_output=True, text=True, timeout=30)

    def reset_failed(self, unit: str) -> None:
        subprocess.run([self.systemctl_bin, "--user", "reset-failed", unit],
                       capture_output=True, text=True, timeout=15)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class Request:
    worktree: Path
    pytest_bin: str
    receipt_dir: Path
    profile: str = "default"
    all_tests: bool = False
    test_paths: list = field(default_factory=list)
    reason: str = ""
    authorized: bool = False
    systemd_run_bin: str = "systemd-run"
    systemctl_bin: str = "systemctl"
    home: Path | None = None
    device_ref: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _receipt_base(request: Request, run_id: str) -> dict:
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "subject": f"uid:{os.getuid()}",
        "user": getpass.getuser(),
        "device_ref": request.device_ref,
        "profile": request.profile,
        "reason": scrub(request.reason),
        "home": str(request.home) if request.home else None,
    }


def _emit(receipt: dict, request: Request, run_dir: Path | None) -> dict:
    """Write the receipt, and say so. A receipt that cannot be written is
    evidence that does not exist, which is an outcome and not a detail."""
    if run_dir is None:
        receipt["receipt_path"] = None
        receipt["receipt_written"] = False
        return receipt
    path = Path(run_dir) / "receipt.json"
    try:
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        receipt["receipt_path"] = None
        receipt["receipt_written"] = False
        receipt["receipt_error"] = str(exc)
        return receipt
    receipt["receipt_path"] = str(path)
    receipt["receipt_written"] = True
    return receipt


def run(request: Request, *, systemd=None, clock=time.monotonic,
        sleep=time.sleep, poll_interval: float = 1.0,
        deadline_slack_sec: int = 60, run_id: str | None = None) -> dict:
    """Validate, launch inside a bounded unit, read the kernel back, report.

    Order matters and is the safety property: everything that can refuse does
    so *before* anything is launched, and everything that can disagree with the
    request is read back *while* the run is alive, because a unit's cgroup and
    its `MainPID` stop existing the moment it ends.

    The poll is fast only until the envelope has been read once and slow after
    that. Both halves matter: the bound has to be caught while the unit is
    alive, and a ten-minute run must not turn into twelve thousand `systemctl`
    invocations. `MemoryPeak` keeps being sampled afterwards, because it stops
    being readable the moment the unit's cgroup goes away.
    """
    systemd = systemd or Systemd(systemd_run_bin=request.systemd_run_bin,
                                 systemctl_bin=request.systemctl_bin)
    run_id = run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}"
    receipt = _receipt_base(request, run_id)

    ok, why = systemd.available()
    if not ok:
        receipt.update(outcome="preflight-refused", exit_code=EXIT_PREFLIGHT,
                       refusal=why, verification=[], observed={}, requested={},
                       started_at=_now(), finished_at=_now())
        return _emit(receipt, request, None)

    home = request.home if request.home is not None else Path.home()
    try:
        worktree = validate_worktree(request.worktree, home=home)
        selection = validate_selection(worktree, all_tests=request.all_tests,
                                       paths=list(request.test_paths))
        limits = resolve_limits(request.profile, authorized=request.authorized)
    except RunnerRefused as exc:
        receipt.update(outcome="refused", exit_code=EXIT_REFUSED, refusal=str(exc),
                       verification=[], observed={}, requested={},
                       started_at=_now(), finished_at=_now())
        return _emit(receipt, request, None)

    receipt["requested"] = dict(limits)
    receipt["worktree"] = str(worktree)
    receipt["selection"] = list(selection)
    receipt["cap_bytes"] = limits["memory_max_bytes"]

    run_dir = Path(request.receipt_dir) / run_id
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        receipt.update(outcome="evidence-unavailable", exit_code=EXIT_EVIDENCE,
                       refusal=f"the receipt directory is not usable: {exc}",
                       verification=[], observed={}, started_at=_now(),
                       finished_at=_now())
        return _emit(receipt, request, None)

    unit = unit_name(nonce=os.urandom(4).hex(), epoch=time.time())
    receipt["unit"] = unit
    argv = build_launch_argv(
        unit=unit, limits=limits, worktree=worktree, pytest_bin=request.pytest_bin,
        test_args=["-q", "-p", "no:cacheprovider", *selection],
        run_dir=run_dir, systemd_run_bin=request.systemd_run_bin)
    receipt["launch_argv"] = [scrub(a) for a in argv]
    receipt["stdout_path"] = str(run_dir / "stdout.log")
    receipt["stderr_path"] = str(run_dir / "stderr.log")
    receipt["started_at"] = _now()

    proc = systemd.launch(argv)
    launch_rc = getattr(proc, "returncode", 1)
    receipt["launch_returncode"] = launch_rc
    if launch_rc != 0:
        receipt["launch_stderr"] = scrub(getattr(proc, "stderr", "") or "")

    started = clock()
    deadline = started + limits["runtime_max_sec"] + deadline_slack_sec
    grace = started + 5.0

    envelope_findings: list[dict] = []
    tree_findings: list[dict] = []
    observed: dict = {}
    envelope_observed = False
    tree_checked = False
    main_pid = 0
    main_starttime: int | None = None
    unit_cgroup = ""
    peak: int | None = None
    snapshot: dict = {}
    read_starttime = getattr(systemd, "proc_starttime", lambda _pid: None)
    for _ in range(100_000):
        snapshot = systemd.unit_snapshot(unit)
        state = snapshot.get("ActiveState", "")
        cgroup = snapshot.get("ControlGroup", "")
        if state in ("active", "activating") and cgroup:
            expected = expected_cgroup_values(limits)
            observed = {name: systemd.cgroup_read(cgroup, name) for name in expected}
            envelope_findings = verify_envelope(expected, observed)
            envelope_observed = True
            unit_cgroup = cgroup
            try:
                main_pid = int(snapshot.get("MainPID") or 0)
            except (TypeError, ValueError):
                main_pid = 0
            # Read while the unit is alive. `/proc/<pid>/stat` stops existing
            # the moment the process does, so a starttime read after the loop
            # would be `None` on every run — and `None` is also what an
            # unreadable one gives, so the two failures would look alike.
            main_starttime = read_starttime(main_pid)
            # `MainPID=0` is systemd saying "not yet", so it is skipped rather
            # than looked up: `/proc/0/cgroup` fails for a reason that has
            # nothing to do with this run. If no pid is ever named, the absence
            # is turned into a finding after the loop — never into a pass.
            if main_pid > 0:
                tree_findings = verify_tree(cgroup, main_pid, systemd.proc_cgroup(main_pid))
                tree_checked = True
            raw_peak = snapshot.get("MemoryPeak", "")
            if raw_peak and raw_peak != "[not set]":
                try:
                    peak = int(raw_peak)
                except ValueError:
                    peak = None
        if state in ("inactive", "failed"):
            break
        if snapshot.get("LoadState") == "not-found" and clock() > grace:
            break
        if clock() > deadline:
            systemd.stop(unit)
            receipt["forced_stop"] = True
            break
        sleep(min(poll_interval, 0.05) if not envelope_observed else poll_interval)

    if envelope_observed and not tree_checked:
        tree_findings = [{"code": "MAINPID_UNKNOWN", "pid": 0, "unit_cgroup": unit_cgroup}]

    outcome, code = classify_result(snapshot)
    if receipt.get("forced_stop"):
        outcome, code = "timeout", 124
    verification = [*envelope_findings, *tree_findings]
    if verification:
        outcome = "verification-failed"

    receipt.update(
        outcome=outcome,
        observed=observed,
        envelope_observed=envelope_observed,
        verification=verification,
        peak_bytes=peak,
        finished_at=_now(),
        main_process={
            "pid": main_pid,
            "starttime": main_starttime,
            "cgroup": unit_cgroup,
        },
    )

    if envelope_findings:
        receipt["exit_code"] = EXIT_ENVELOPE
    elif tree_findings:
        receipt["exit_code"] = EXIT_TREE
    elif launch_rc != 0 or not envelope_observed:
        receipt["exit_code"] = EXIT_UNVERIFIED
    else:
        receipt["exit_code"] = code

    systemd.reset_failed(unit)

    _append_index(Path(request.receipt_dir), {
        "run_id": run_id, "unit": unit, "subject": receipt["subject"],
        "device_ref": receipt["device_ref"], "outcome": outcome,
        "exit_code": receipt["exit_code"], "cap_bytes": limits["memory_max_bytes"],
        "peak_bytes": peak, "ts": receipt["finished_at"],
    })
    return _emit(receipt, request, run_dir)


def _append_index(receipt_dir: Path, row: dict) -> None:
    """One line per run, for a per-uid producer to correlate against.

    This is a read interface, not a second alerting system: #167 owns the
    per-uid measurement and the notification path, and this file only tells it
    which of a uid's memory came from a run that already carried a declared
    cap. Nothing here decides that anything should be alerted on.
    """
    try:
        receipt_dir.mkdir(parents=True, exist_ok=True)
        with (receipt_dir / "index.jsonl").open("a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        return
