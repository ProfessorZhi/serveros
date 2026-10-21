"""Live probe: the real `Daemon.reconcile` effect seam against a real toy unit.

Not part of the unittest suite — it starts and restarts a transient user unit, so
it is run by hand and its output is kept as evidence. It exercises the actual
`Daemon.reconcile` code path (not a reimplementation of it) with the real
`Actuator`, a real `systemctl --user restart`, and the shared lease.

    python3 manual_daemon_lease_probe.py <unit> <workdir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))

import daemon as daemon_mod  # noqa: E402
import native_core_facts as facts  # noqa: E402


class StubResource:
    """The narrow slice of the registry's Resource that `reconcile` touches."""

    def __init__(self, unit: str) -> None:
        self.id = "toy"
        self.record = {"id": "toy", "unit": unit, "kind": "systemd-unit"}
        self.probes = ({"id": "unit", "kind": "systemd-unit-state", "axis": "liveness", "unit": unit},)
        self.kind = "systemd-unit"
        self.is_automatable = True
        self.reconcile = {"mode": "restart", "max_attempts": 5, "backoff": 0}
        self.protection = "normal"
        self.desired = "running"

    def automatability(self) -> dict:
        return {"automatable": True, "recommendation_only": False,
                "permission": [], "policy": []}

    def why_not_automatable(self):
        return None


class StubRegistry:
    def __init__(self, resource) -> None:
        self._resource = resource

    def all(self):
        return [self._resource]

    def automatable(self):
        return [self._resource]


def build(unit: str, workdir: Path, *, lease: Path | None):
    workdir.mkdir(parents=True, exist_ok=True)
    resource = StubResource(unit)
    owner_map = workdir / "owner-map.json"
    facts.OwnerMap({unit: facts.OWNER_SERVEROSD}).write(owner_map)
    return daemon_mod.Daemon(
        StubRegistry(resource),
        state_dir=workdir / "state",
        enable_reconcile=True,
        native_lease_path=lease,
        owner_map_path=owner_map,
    ), resource


def run(daemon, resource) -> dict:
    observations = {resource.id: {"action": {"action": "RECONCILE"}}}
    decisions = daemon.reconcile(observations)
    return decisions[0] if decisions else {}


def main() -> int:
    unit = sys.argv[1]
    workdir = Path(sys.argv[2])
    lease = workdir / "native-core.lease"
    report = {}

    # A: nobody holds the lease -> the real effect runs
    daemon, resource = build(unit, workdir / "a", lease=lease)
    before = _invocation(unit)
    decision = run(daemon, resource)
    report["A_free_lease"] = {"performed": decision.get("performed"),
                              "blocked_by": decision.get("blocked_by"),
                              "action": decision.get("action"),
                              "budget_allowed": (decision.get("budget") or {}).get("allowed"),
                              "invocation_before": before, "invocation_after": _invocation(unit)}
    report["A_lease_released_after"] = not daemon._native_lease.held

    # B: another writer (the Core) holds the lease -> refused, no effect
    holder = facts.SafeLease(lease)
    holder.acquire()
    try:
        daemon, resource = build(unit, workdir / "b", lease=lease)
        before = _invocation(unit)
        decision = run(daemon, resource)
        report["B_lease_held_elsewhere"] = {"performed": decision.get("performed"),
                                            "blocked_by": decision.get("blocked_by"),
                                            "action": decision.get("action"),
                                            "budget_allowed": (decision.get("budget") or {}).get("allowed"),
                                            "invocation_before": before,
                                            "invocation_after": _invocation(unit)}
    finally:
        holder.release()

    # C: the owner map assigns the unit to the Core -> refused by name
    daemon, resource = build(unit, workdir / "c", lease=lease)
    facts.OwnerMap({unit: facts.OWNER_NATIVE_CORE}).write(workdir / "c" / "owner-map.json")
    daemon._owner_map = facts.OwnerMap.load(workdir / "c" / "owner-map.json")
    before = _invocation(unit)
    decision = run(daemon, resource)
    report["C_misowned_unit"] = {"performed": decision.get("performed"),
                                 "blocked_by": decision.get("blocked_by"),
                                 "action": decision.get("action"),
                                 "budget_allowed": (decision.get("budget") or {}).get("allowed"),
                                 "invocation_before": before, "invocation_after": _invocation(unit)}

    # D: no explicit lease path -> a default is still taken, and it is the same
    # file, so a holder of that default refuses this daemon too.
    daemon, resource = build(unit, workdir / "d", lease=None)
    default_path = daemon._native_lease.path
    holder = facts.SafeLease(default_path)
    holder.acquire()
    try:
        before = _invocation(unit)
        decision = run(daemon, resource)
        report["D_default_lease_held"] = {
            "lease_path_used": str(default_path),
            "performed": decision.get("performed"),
            "blocked_by": decision.get("blocked_by"),
            "action": decision.get("action"),
            "invocation_before": before, "invocation_after": _invocation(unit)}
    finally:
        holder.release()

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _invocation(unit: str) -> str:
    import subprocess
    out = subprocess.run(["systemctl", "--user", "show", "-p", "InvocationID", "--value", unit],
                         capture_output=True, text=True)
    return out.stdout.strip()


def _restarts(unit: str) -> int:
    import subprocess
    out = subprocess.run(["systemctl", "--user", "show", "-p", "NRestarts", "--value", unit],
                         capture_output=True, text=True)
    try:
        return int(out.stdout.strip() or 0)
    except ValueError:
        return -1


if __name__ == "__main__":
    raise SystemExit(main())
