"""Deterministic, offline acceptance for the L1 monitor-only runtime.

WHAT THIS IS
------------
A judgement, not a runner. Every subprocess lives in the test that calls this
module, and this module decides whether what came back is acceptable. That split
is deliberate and it is the one the repository's actuator gate enforces: an
`integrations/` module that ran commands would be a second place a command can be
issued from, and the release path already has one.

WHAT IT ACCEPTS
---------------
One machine's worth of *already recorded* facts:

  * a status document read back from the socket,
  * the argv lines a fake service manager recorded while the monitor ran,
  * the exit code, and whether the process stopped when it was asked to.

From those it answers the question the owner's boundary asks: **did this
resident, read-only profile observe and stop there?** It does not ask whether the
host is healthy, whether any service is installed, or whether anything should be
restarted — those are someone else's facts.

WHAT IT REFUSES TO CLAIM
------------------------
`REAL_DEVICE` is not reached by this module and cannot be. Everything here runs
against a temporary directory and a fake service manager. A green acceptance says
the *binary and package* behave; it says nothing about an installed device, and
the receipt says so in the same sentence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "serveros/fleet-v1-monitor-acceptance.v1"

#: The profile this acceptance is about. A run under any other profile is not a
#: failure of the binary — it is a different acceptance, and this one says so
#: rather than judging it.
PROFILE = "monitor-only"

#: Every switch that authorises, feeds or records a mutation. `--monitor-only`
#: must refuse each by name; the list is duplicated from the C++ usage text on
#: purpose, so that adding a switch there without adding it here fails a test.
ACTION_FLAGS = (
    "--enable-actions", "--lease", "--state", "--facts", "--link-state",
    "--agent-state", "--service-runtime", "--receipt", "--owner-map",
)

#: Words a service manager is asked with, as opposed to `show`, which asks it
#: nothing. A monitor may only observe. The hyphenated forms are separate
#: entries because `try-restart` and `reload-or-restart` are mutating verbs whose
#: names are not `restart`; a scanner that only knew the short spellings would
#: call a restart a non-event.
VERBS = frozenset({
    "start", "stop", "restart", "reload", "try-restart", "reload-or-restart",
    "reload-or-try-restart", "enable", "disable", "mask", "unmask", "isolate",
    "set-property", "reset-failed", "kill", "reboot", "poweroff", "halt",
    "kexec", "emergency", "rescue", "default", "suspend", "hibernate",
})

#: Bounds for the resident profile, in the units the status document reports.
#: Generous on purpose: they catch a monitor that forks per tick or holds a
#: buffer, not a monitor that ran on a slower machine.
DEFAULT_BOUNDS = {"rss_kb": 64 * 1024, "cpu_ms_total": 2000, "tick_ms": 1000}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    label: str
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks) and bool(self.checks)

    @property
    def failures(self) -> list[str]:
        return [f"{check.name}: {check.detail}" for check in self.checks if not check.ok]

    def as_dict(self) -> dict:
        return {"label": self.label, "ok": self.ok, "checks": len(self.checks),
                "failures": self.failures}


# --------------------------------------------------------------------------- #
# one resident run
# --------------------------------------------------------------------------- #
def _verb_of(argv_line: str) -> str:
    """The systemctl verb in one recorded argv line, or "" when there is none.

    Only the words BEFORE `--` are considered. `systemctl [options] VERB [--]
    [unit…]`, so everything after the separator is an operand — which is what
    keeps a unit named `stop.service` from being read as a verb. Within the head,
    an exact match against the closed vocabulary; no substring guessing, because
    a guess that is too clever in this direction turns unit names into verbs.
    """
    head = argv_line.split(" -- ", 1)[0].split()
    for word in head:
        if word in VERBS:
            return word
    return ""


def evaluate_monitor_run(status: dict, calls: list[str], *, exit_code: int,
                         stopped: bool, ticks_expected: int,
                         observations_expected: int | None = None,
                         expect_planned_actions: int | None = None,
                         bounds: dict | None = None) -> Report:
    """One resident monitor run, judged from what it left behind."""
    bounds = {**DEFAULT_BOUNDS, **(bounds or {})}
    report = Report("monitor-run")

    report.add("the process exited 0", exit_code == 0, f"exit {exit_code}")
    report.add("it stopped when asked", stopped,
               "the process did not exit within the stop deadline")

    if not isinstance(status, dict):
        report.add("a status document was read back", False, f"got {type(status).__name__}")
        return report
    report.add("a status document was read back", True)

    report.add("the profile is monitor-only", status.get("profile") == PROFILE,
               f"profile is {status.get('profile')!r}")
    report.add("the mode is observe-only", status.get("mode") == "observe-only",
               f"mode is {status.get('mode')!r}")

    ticks = status.get("ticks")
    report.add("it ticked", isinstance(ticks, int) and ticks >= ticks_expected,
               f"ticks={ticks!r}, expected at least {ticks_expected}")

    monitor = status.get("monitor") or {}
    for key, ceiling in bounds.items():
        value = monitor.get(key)
        measured = isinstance(value, int) and value >= 0
        report.add(f"{key} was measured", measured, f"{key}={value!r}")
        if measured:
            report.add(f"{key} within {ceiling}", value <= ceiling, f"{key}={value}")

    # The planner may SEE work; the run must not DO it. BOTH halves are checked,
    # and the first one has to be a floor rather than a type test: "planned 0,
    # executed 0" is also what a planner that saw nothing produces, so a type
    # check would accept a broken binary and call the shape proven.
    plan = status.get("plan") or {}
    actions = plan.get("actions")
    report.add("the plan reports a count", isinstance(actions, int),
               f"plan.actions={actions!r}")
    if expect_planned_actions is not None:
        report.add(f"the plan saw at least {expect_planned_actions} action(s)",
                   isinstance(actions, int) and actions >= expect_planned_actions,
                   f"plan.actions={actions!r}")

    verbs = [_verb_of(line) for line in calls]
    offending = [line for line, verb in zip(calls, verbs) if verb]
    report.add("the service manager was asked something", bool(calls),
               "no observation happened at all, so 'no verbs' proves nothing")
    report.add("no verb was issued", not offending,
               f"the monitor issued {offending[:3]}")
    # The document was read WHILE the process was resident, so it reports the
    # ticks that had finished at that instant — one, not the five the run later
    # completed. The observation count is the separate, checkable half of "five
    # ticks": one `show` per tick, so a run that ticked once cannot pass a
    # five-tick claim by exiting with a large number in its own summary.
    wanted = observations_expected if observations_expected is not None else ticks_expected
    report.add(f"it observed at least {wanted} time(s)", len(calls) >= wanted,
               f"{len(calls)} call(s) recorded, expected at least {wanted}")

    services = status.get("services") or []
    failed = [row for row in services if row.get("active") == "failed"]
    report.add("the failed unit was observed as failed", bool(failed),
               f"services={[(r.get('unit'), r.get('active')) for r in services]}")
    return report


# --------------------------------------------------------------------------- #
# the refusal: an action switch in the monitor profile
# --------------------------------------------------------------------------- #
def evaluate_refusal(flag: str, exit_code: int, stderr: str) -> Report:
    report = Report(f"refuse {flag}")
    report.add("it refused", exit_code == 2, f"exit {exit_code}")
    report.add("the refusal is the profile's", "--monitor-only refuses" in stderr,
               stderr.strip()[:160])
    report.add("the refusal names the flag", flag in stderr, stderr.strip()[:160])
    return report


# --------------------------------------------------------------------------- #
# the package
# --------------------------------------------------------------------------- #
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_package(package: Path, manifest: dict) -> Report:
    """The unpacked payload is the package it says it is, and has ONE actuator."""
    report = Report("package")
    report.add("the manifest names the package", manifest.get("name") == "serverosd",
               repr(manifest.get("name")))

    members = manifest.get("members") or []
    actuators = [m for m in members if m.get("role") == "actuator"]
    report.add("there is exactly one actuator", len(actuators) == 1,
               f"{[m.get('path') for m in actuators]}")
    if len(actuators) == 1:
        report.add("the actuator is the native core",
                   actuators[0].get("path") == "bin/serveros-native-core",
                   repr(actuators[0].get("path")))

    for member in members:
        path = package / str(member.get("path"))
        if not report.add(f"{member.get('path')} is present", path.is_file()):
            continue
        report.add(f"{member.get('path')} matches its digest",
                   sha256(path) == member.get("sha256"),
                   f"{sha256(path)[:12]}… vs {str(member.get('sha256'))[:12]}…")
    return report


# --------------------------------------------------------------------------- #
# the rollback
# --------------------------------------------------------------------------- #
def evaluate_rollback(previous: dict, current: dict) -> Report:
    """Two revisions, distinguishable by digest, so a rollback is observable.

    A rollback that cannot be told apart from the thing it replaced is a symlink
    swap nobody can verify. This asks only the question a temporary directory can
    answer: are these two different builds, each with its own identity.
    """
    report = Report("rollback")
    report.add("the current revision has a manifest", bool(current.get("members")),
               "no members recorded")
    report.add("the previous revision has a manifest", bool(previous.get("members")),
               "no members recorded")
    cur_digest = next((m.get("sha256") for m in current.get("members") or []
                       if m.get("path") == "bin/serveros-native-core"), None)
    prev_digest = next((m.get("sha256") for m in previous.get("members") or []
                        if m.get("path") == "bin/serveros-native-core"), None)
    report.add("both revisions name the actuator", bool(cur_digest and prev_digest),
               f"{prev_digest!r} / {cur_digest!r}")
    report.add("the two revisions are different builds", cur_digest != prev_digest,
               "the rollback target is byte-identical, so the swap proves nothing")
    return report


# --------------------------------------------------------------------------- #
# one receipt
# --------------------------------------------------------------------------- #
def receipt(reports: list[Report], *, provenance: dict) -> dict:
    """One verdict over several reports.

    `ok` uses `Report.ok`, not `not report.failures`: a report that ran no checks
    has no failures and is not a pass, and the two guards disagreeing is how a
    receipt says PASS over an empty judgement.
    """
    verdict = bool(reports) and all(report.ok for report in reports)
    failures = [failure for report in reports for failure in report.failures]
    return {
        "schema": SCHEMA,
        "ok": verdict and not failures,
        "provenance": provenance,
        # THE LAYERS, said out loud. `code` and `unit` are both computed from this
        # one offline suite: nothing here installs a systemd unit, so "UNIT" means
        # "the suite's assertions about the run", not "a unit was exercised".
        # `real_device` and `production` are hardcoded NOT_RUN because this
        # acceptance never touches a device and must not be read as if it did.
        "layers": {
            "code": "PASS" if verdict else "FAIL",
            "unit": "PASS" if verdict else "FAIL",
            "hosted_ci": provenance.get("hosted_ci", "NOT_RUN"),
            "real_device": "NOT_RUN",
            "production": "NOT_RUN",
        },
        "layers_note": ("code and unit are this offline suite; no systemd unit is "
                        "installed, started or stopped, so real_device and "
                        "production stay NOT_RUN"),
        "reports": [report.as_dict() for report in reports],
        "failures": failures,
    }
