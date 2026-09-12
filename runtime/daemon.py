#!/usr/bin/env python3
"""serverosd — the semantic controller.

What it is
----------
A small persistent user-space loop that loads the registry, observes, probes,
persists what it saw, detects drift, and reconciles the resources policy
allows it to. It serves those same answers over a Unix socket so that an
agent-facing adapter (§23) can be thin.

What it is NOT
--------------
**Not a supervisor.** systemd, Docker and the provider own process lifecycle;
serverosd owns the *meaning* of what they report. That is why the registry
has `desired` and not `restart_policy`, and why `reconcile` below shells out
to `systemctl --user` rather than forking anything. A process supervisor that
cannot renew a session will restart a broken endpoint forever and call it
recovery — this host did exactly that, five times in two hours.

**Not an OS.** §18: no git workflow, no Feishu, no knowledge ingestion, no
ability authoring, no test implementation, no browser automation. Those are
other components' authority and routing them through the control plane would
make every one of them a dependency of the daemon staying up.

**Not the source of truth for anything it serves.** The registry is authored
state on disk; capabilities belong to ability-management. serverosd reads and
reports. `ability.*` requests are refused *by name*, with the owner named —
because the failure mode of a control plane is not lying, it is answering.

The reconcile gate
------------------
This is the only component in the tree with a code path that changes
something, so it is worth being precise about the gate. An action runs only
when ALL of these hold:

    the resource is registered, owned, user-scope, unprotected, desired
      running, and its declared lifecycle is bounded   (`is_automatable`)
    the health model returned RECONCILE, not NEEDS_HUMAN or DEFER
    the daemon was started with --enable-reconcile
    the retry budget for that resource has not been exhausted

In this round the default is observe-only, and the fourth condition is the
one §19 asks for: a budget with backoff, persisted so that restarting the
daemon does not restart the count. An endpoint that has failed N times in a
window stays failed and reports NEEDS_HUMAN, rather than being restarted
until somebody notices. "Restart it again" is not a recovery strategy; it is
how the original incident stayed invisible for 54 minutes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_mod = _load("serveros_runtime_registry", "registry.py")
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
# The shared answer layer (§3/§6/§10/§14). The daemon computes NOTHING of its
# own for these: it forwards to the same functions `serverosctl` calls, so the
# CLI answer and the socket answer can differ only in the moment they were
# taken — which `answered_by` and `measured_at` make visible rather than
# something a caller has to assume.
view_mod = _load("serveros_runtime_view", "view.py")


class DaemonError(Exception):
    """The daemon cannot continue as configured."""


# ---------------------------------------------------------------------------
# Failure budget — §19's "no infinite restart loop"
# ---------------------------------------------------------------------------

# Deliberately small. The cost of a low budget is that a person is asked
# sooner; the cost of a high one is the incident this module was written
# after. Asymmetric, so it is set on the cautious side.
MAX_ATTEMPTS = 3
WINDOW_SECONDS = 3600
BACKOFF_SECONDS = (60, 300, 900)


class Budget:
    """How many times this resource may be acted on, and how far apart.

    Persisted, because a budget that resets when the supervisor restarts is
    not a budget — `Restart=always` on the daemon would silently grant it a
    fresh three attempts every time, which is the original bug wearing a
    different hat.
    """

    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self._now = now or time.time
        self._state: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def _entry(self, resource_id: str) -> dict:
        now = self._now()
        entry = self._state.get(resource_id) or {"attempts": [], "last_action": None}
        # Prune anything outside the window, so a resource that failed three
        # times last month is not permanently barred.
        entry["attempts"] = [t for t in entry["attempts"] if now - t < WINDOW_SECONDS]
        self._state[resource_id] = entry
        return entry

    def may_act(self, resource_id: str) -> dict:
        entry = self._entry(resource_id)
        used = len(entry["attempts"])
        if used >= MAX_ATTEMPTS:
            oldest = min(entry["attempts"])
            return {
                "allowed": False,
                "reason": "BUDGET_EXHAUSTED",
                "attempts_in_window": used,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": int(WINDOW_SECONDS - (self._now() - oldest)),
                "note": (
                    f"{used} attempts within {WINDOW_SECONDS}s. A {used + 1}th "
                    f"would not be a new attempt at a fix, it would be the "
                    f"loop. Report DEGRADED and ask a person."
                ),
            }
        # The waiting period belongs to the attempt ABOUT to be made, so it is
        # indexed by how many have already happened: 0 attempts means the
        # first retry waits BACKOFF_SECONDS[0]. Indexing by `used` instead
        # would silently skip the shortest interval — the one that matters
        # most, because it is the one that runs while a person is still
        # looking at the screen.
        wait = BACKOFF_SECONDS[min(used, len(BACKOFF_SECONDS) - 1)] if used else 0
        if entry["attempts"] and used:
            wait = BACKOFF_SECONDS[min(used - 1, len(BACKOFF_SECONDS) - 1)]
            since = self._now() - max(entry["attempts"])
            if since < wait:
                return {
                    "allowed": False,
                    "reason": "BACKOFF",
                    "attempts_in_window": used,
                    "retry_after_seconds": int(wait - since),
                }
        return {"allowed": True, "attempts_in_window": used,
                "backoff_before_next_attempt_seconds": wait}

    def record(self, resource_id: str, action: str) -> dict:
        entry = self._entry(resource_id)
        entry["attempts"].append(self._now())
        entry["last_action"] = action
        self._save()
        return {"attempts_in_window": len(entry["attempts"])}

    def last_action(self, resource_id: str) -> str | None:
        return (self._state.get(resource_id) or {}).get("last_action")

    def snapshot(self) -> dict:
        return {rid: {"attempts": len(e.get("attempts", [])),
                      "last_action": e.get("last_action")}
                for rid, e in sorted(self._state.items())}


# ---------------------------------------------------------------------------
# Reconcile — the one place that changes anything
# ---------------------------------------------------------------------------


class SubprocessRunner:
    """The only thing in this tree that may write to the world.

    It is deliberately NOT `probes.LiveHost`. That class documents that none
    of its methods mutates, and every probe holds one; adding a mutating
    method to it would mean "observe only" stops being a property anything
    can check, because any probe could reach the actuator through its own
    host. Keeping the two objects separate means a probe cannot mutate even
    by mistake, which is the only version of that guarantee worth having.
    """

    name = "actuator-subprocess"

    def run(self, args: list[str], timeout: float) -> dict:
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"exit_code": 127, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
        return {"exit_code": out.returncode,
                "stdout": out.stdout[-2000:],
                "stderr": out.stderr[-2000:]}


class Actuator:
    """Executes a reconcile against the thing that actually supervises.

    serverosd never forks, never daemonizes and never signals a process
    directly. It asks systemd, because systemd is the supervisor and a second
    supervisor is how you get two components disagreeing about whether
    something is running.
    """

    def __init__(self, runner=None, *, dry_run: bool = True) -> None:
        self.runner = runner or SubprocessRunner()
        self.dry_run = dry_run
        self.performed: list[dict] = []

    def restart(self, resource) -> dict:
        unit = resource.record.get("unit") or next(
            (p.get("unit") for p in resource.probes if p.get("unit")), None)
        if unit:
            command = ["systemctl", "--user", "restart", unit]
        elif resource.kind == "container":
            container = _container_name(resource)
            if not container:
                return {
                    "performed": False,
                    "reason": "NO_TARGET",
                    "note": "registered as a container but no probe names which one",
                }
            command = ["docker", "restart", container]
        else:
            return {
                "performed": False,
                "reason": "NO_ACTUATOR",
                "note": (
                    f"serverosd has no way to restart a {resource.kind}. It "
                    f"does not implement one: guessing a command for a kind "
                    f"nobody wrote an actuator for is how a control plane "
                    f"becomes a footgun."
                ),
            }

        if self.dry_run:
            return {
                "performed": False,
                "dry_run": True,
                "would_run": command,
                "note": "observe-only; --enable-reconcile is required to act",
            }
        outcome = self.runner.run(command, 60.0)
        result = {"performed": outcome["exit_code"] == 0, "command": command,
                  "exit_code": outcome["exit_code"],
                  "stderr": (outcome.get("stderr") or "").strip()[:300]}
        self.performed.append(result)
        return result


def _container_name(resource) -> str | None:
    for probe in resource.probes:
        if probe.get("container"):
            return probe["container"]
    return None


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class Daemon:
    """Observe, probe, persist, detect drift, reconcile, serve.

    Every step is separately callable, so a test can run one tick without a
    socket, a timer or a signal handler.
    """

    def __init__(self, registry, *, state_dir: Path, socket_path: Path | None = None,
                 host=None, inventory=None, enable_reconcile: bool = False,
                 runner=None, now=None, log=None) -> None:
        self.registry = registry
        self.state_dir = Path(state_dir)
        self.socket_path = Path(socket_path) if socket_path else default_socket_path()
        # Set by `_open_socket`; the identity of the socket file this daemon
        # bound, so that teardown cannot act on a socket that is not ours.
        self._socket_ino: int | None = None
        self.host = host or probes_mod.LiveHost()
        self.inventory = inventory or inventory_mod.Inventory()
        self.enable_reconcile = enable_reconcile
        self._now = now or time.time
        self._log = log or (lambda message: None)

        self.budget = Budget(self.state_dir / "health/reconcile-state.json", now=self._now)
        # Passed `runner`, not `host`: the actuator must not be reachable
        # through the object every probe holds. See SubprocessRunner.
        self.actuator = Actuator(runner=runner, dry_run=not enable_reconcile)
        self.last_tick: dict | None = None
        self.drift: list[dict] = []

        # Register the daemon's own directories through the path contract,
        # rather than mkdir-ing a path that looks right. A daemon that invents
        # its own state location is the exact drift §40 is about.
        self._paths = paths_mod.Paths()

    # -- one pass ----------------------------------------------------------

    def tick(self) -> dict:
        started = self._now()
        observations = self.observe_all()
        drift = self.detect_drift(observations)
        decisions = self.reconcile(observations)
        report = {
            "tick_started_at": _iso(started),
            "duration_seconds": round(self._now() - started, 3),
            "registry_count": len(self.registry),
            "enable_reconcile": self.enable_reconcile,
            "observations": observations,
            "drift": drift,
            "decisions": decisions,
            "budget": self.budget.snapshot(),
        }
        self.last_tick = report
        self.drift = drift
        self.persist(report)
        return report

    def observe_all(self) -> dict:
        out: dict[str, dict] = {}
        for resource in self.registry.all():
            try:
                health = probes_mod.observe(resource, self.host)
            except registry_mod.RegistryError as exc:
                # One broken probe must not take down the loop. The resource
                # reports UNKNOWN with the reason, which is a fact about the
                # probe, and every other resource still gets measured.
                out[resource.id] = {
                    "error": str(exc),
                    "state": "UNKNOWN",
                    "reason": "NOT_OBSERVED",
                    "note": "the probe raised; the resource is not thereby fine",
                }
                continue
            decision = health_mod.action_for(resource, health)
            out[resource.id] = {
                # The state is lifted to the top level as well as living inside
                # `health`. Callers that only need "what is it" should not have
                # to know the shape of an axis record, and the drift detector
                # reads this key — a missing one there would have made every
                # resource look like it had no state rather than like it had a
                # bad one.
                "state": health.state,
                "health": health.show(),
                "action": decision,
                "protection": resource.protection,
                "lifecycle": resource.lifecycle,
                "owner": resource.owner_ref,
                "observed_at": health.observed_at,
            }
        return out

    # -- drift -------------------------------------------------------------

    def detect_drift(self, observations: dict) -> list[dict]:
        """Where the declaration and the world disagree.

        Drift is NOT a fault. A protected resource that is down is a fault; a
        registration naming a container that no longer exists is drift, and
        the response is to fix the registration, not to start something. The
        two are separated because conflating them is how a control plane
        starts recreating things people deliberately deleted.
        """
        drift = []
        for resource in self.registry.all():
            observed = observations.get(resource.id) or {}
            health = observed.get("health") or {}
            state = health.get("state") or observed.get("state")
            if state == "UNAVAILABLE":
                drift.append({
                    "resource": resource.id,
                    "kind": "DECLARED_BUT_NOT_OBSERVABLE",
                    "observed": "UNAVAILABLE",
                    "desired": f"{resource.desired}/{resource.protection}",
                    "proposed_action": "review the registration, not the world",
                    "note": (
                        "the registry names something this node cannot observe "
                        "at all — the container or unit is gone. This is a "
                        "stale declaration, and the fix is to edit the "
                        "declaration, not to recreate the thing."
                    ),
                })
                continue
            if state == "UNKNOWN":
                # Distinct from UNAVAILABLE and worth separating: UNAVAILABLE
                # means the probe reached the host and found nothing to
                # measure; UNKNOWN means no probe ran. The second is a defect
                # in the registry's probe coverage, and reporting it as an
                # outage would send somebody to look at a working resource.
                drift.append({
                    "resource": resource.id,
                    "kind": "REGISTERED_WITHOUT_A_PROBE",
                    "observed": "UNKNOWN",
                    "proposed_action": "add a probe, or the resource is unmanaged in practice",
                    "note": "a registration nobody can observe is not management",
                })
                continue
            if resource.protection == "protected" and state in ("NOT_RUNNING", "FUNCTIONALLY_FAILED"):
                drift.append({
                    "resource": resource.id,
                    "kind": "PROTECTED_AND_DOWN",
                    "observed": state,
                    "desired": "running",
                    "proposed_action": "NEEDS_HUMAN",
                    "note": (
                        "protected means ServerOS will not act. It does not "
                        "mean nobody should be told."
                    ),
                })
        return drift

    # -- reconcile ---------------------------------------------------------

    def reconcile(self, observations: dict) -> list[dict]:
        decisions = []
        for resource in self.registry.all():
            observed = observations.get(resource.id) or {}
            action = (observed.get("action") or {}).get("action")
            if action != "RECONCILE":
                continue
            entry = {
                "resource": resource.id,
                "action": "RECONCILE",
                "automatable": resource.is_automatable,
            }
            if not resource.is_automatable:
                entry.update({
                    "performed": False,
                    "blocked_by": resource.why_not_automatable(),
                    "note": "the health model said RECONCILE; the registry says no",
                })
                decisions.append(entry)
                continue
            budget = self.budget.may_act(resource.id)
            entry["budget"] = budget
            if not budget["allowed"]:
                entry.update({
                    "performed": False,
                    "escalate_to": "DEGRADED/NEEDS_HUMAN",
                    "note": (
                        "the budget is the §19 guard. Repeatedly restarting a "
                        "resource that keeps failing is not recovery, it is the "
                        "loop."
                    ),
                })
                decisions.append(entry)
                continue
            if not self.enable_reconcile:
                entry.update({
                    "performed": False,
                    "dry_run": True,
                    "would_run": self.actuator.restart(resource).get("would_run"),
                    "note": (
                        "reconcile is implemented and gated off. This round "
                        "runs observe-only: no registered resource on this "
                        "host is automatable anyway, and enabling it without "
                        "a resource that needs it would be a switch nobody "
                        "could test."
                    ),
                })
                decisions.append(entry)
                continue
            result = self.actuator.restart(resource)
            self.budget.record(resource.id, "restart")
            entry["result"] = result
            entry["performed"] = bool(result.get("performed"))
            # Re-probe immediately: "did the restart fix it" is a question
            # only a probe can answer, and assuming yes is how a restart loop
            # reports success.
            entry["reprobe"] = self._reprobe(resource)
            decisions.append(entry)
        return decisions

    def _reprobe(self, resource) -> dict:
        try:
            health = probes_mod.observe(resource, self.host)
        except registry_mod.RegistryError as exc:
            return {"state": "UNKNOWN", "error": str(exc)}
        return {"state": health.state, "reason": health.reason}

    # -- persistence -------------------------------------------------------

    def persist(self, report: dict) -> list[str]:
        written = []
        observed = self.state_dir / "observed/current.json"
        health = self.state_dir / "health/current.json"
        hygiene = self.state_dir / "hygiene/current.json"
        for path, payload in (
            (observed, {"schema": "serveros/observed.v1", **report}),
            (health, {
                "schema": "serveros/health.v1",
                "observed_at": report["tick_started_at"],
                "resources": {
                    rid: (obs.get("health") or {"state": obs.get("state")})
                    for rid, obs in report["observations"].items()
                },
            }),
            (hygiene, self._hygiene_snapshot()),
        ):
            _atomic_write(path, payload)
            written.append(str(path))
        return written

    def _hygiene_snapshot(self) -> dict:
        try:
            hygiene = hygiene_mod.Hygiene(self.registry, self.inventory)
            report = hygiene.report()
        except Exception as exc:  # noqa: BLE001 - a failed snapshot is reported
            return {"schema": "serveros/hygiene.v1", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "schema": "serveros/hygiene.v1",
            "generated_at": _iso(self._now()),
            "docker": report["docker"].get("by_classification"),
            "actionable": report["docker"].get("actionable"),
            "summary": report["summary"],
        }

    # -- the served interface ----------------------------------------------

    def handle(self, request: dict) -> dict:
        """Answer one IPC request. Unknown methods are refused by name."""
        method = request.get("method")
        params = request.get("params") or {}
        handler = {
            "device.show": self._r_device_show,
            "context.current": self._r_context_current,
            "path.resolve": self._r_path_resolve,
            "path.admit": self._r_path_admit,
            "runtime.list": self._r_runtime_list,
            "runtime.status": self._r_runtime_status,
            "runtime.explain": self._r_runtime_explain,
            "resource.summary": self._r_resource_summary,
            "resource.hygiene": self._r_resource_hygiene,
            "observation.get": self._r_observation_get,
            "ability.list": self._r_ability_list,
            "ability.status": self._r_ability_status,
            # §3/§5/§6/§10/§14. Read-only, and every one of them is a forward
            # to `view.py` — the daemon adds no logic of its own here, because
            # a daemon that answers a question differently from the CLI is a
            # daemon whose answer cannot be checked by running the CLI.
            "runtime.groups": self._r_runtime_groups,
            "runtime.group": self._r_runtime_group,
            "runtime.tree": self._r_runtime_tree,
            "runtime.budget": self._r_runtime_budget,
            "process.top": self._r_process_top,
            "process.trace": self._r_process_trace,
            "anomaly.list": self._r_anomaly_list,
            "anomaly.explain": self._r_anomaly_explain,
            "container.attribution": self._r_container_attribution,
        }.get(method)

        if handler is None:
            return _error(
                method, "UNKNOWN_METHOD",
                f"{method!r} is not served. Known: {sorted(self._methods())}",
            )
        try:
            return {"ok": True, "method": method, "result": handler(params)}
        except Exception as exc:  # noqa: BLE001 - the caller needs the reason
            return _error(method, type(exc).__name__, str(exc))

    def _methods(self) -> tuple[str, ...]:
        return ("device.show", "context.current", "path.resolve", "path.admit",
                "runtime.list", "runtime.status", "runtime.explain",
                "resource.summary", "resource.hygiene", "observation.get",
                "ability.list", "ability.status",
                "runtime.groups", "runtime.group", "runtime.tree",
                "runtime.budget", "process.top", "process.trace",
                "anomaly.list", "anomaly.explain", "container.attribution")

    # -- the local capability view (§27/§28) --------------------------------

    def _capability_view(self) -> dict:
        """The last-known local projection. Never the authority, always labelled.

        §27 freezes the Capability/Ability Authority as ability-management, and
        §29 puts `ability:*` in that authority's namespace. §24 nevertheless
        lists `ability.list` / `ability.status` on the node surface, and §28
        says the node must keep working — with a projection it is told may be
        stale — while the authority is unreachable.

        Those three are only compatible if the ANSWER says which of the two it
        is. So it does, in a field that cannot be missed: every response names
        `ability-management` as the authority, says this is a local observed
        projection, and carries `registry_status` with an age. A caller can
        then tell "the authority said nothing is installed" from "nobody has
        asked the authority in three days", which are the same empty list and
        completely different facts.

        Deliberately NOT a refusal. The first version of this refused
        `ability.*` outright, which is defensible and wrong: it makes every
        node surface depend on ability-management being reachable, which is
        the coupling §28 exists to remove.
        """
        state = paths_mod.Paths().root("state") / "abilities"
        for name in ("effective.json", "effective.yaml"):
            candidate = state / name
            try:
                raw = candidate.read_text()
            except OSError:
                continue
            try:
                if candidate.suffix == ".json":
                    document = json.loads(raw)
                else:
                    import yaml

                    document = yaml.safe_load(raw)
            except Exception as exc:  # noqa: BLE001 - a corrupt projection is a fact
                return {
                    "authority": "ability-management",
                    "this_is": "a local observed projection, not the authority",
                    "registry_status": "UNREADABLE",
                    "source": str(candidate),
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": (
                        "the projection exists and cannot be parsed. Reporting "
                        "it as empty would say 'nothing is installed', which "
                        "is a different and much more confident claim."
                    ),
                }
            document = document or {}
            age = _age_seconds(document.get("generated_at"), self._now())
            status = str(document.get("registry_status") or "UNKNOWN")
            if status == "FRESH" and age is not None and age > MAX_PROJECTION_AGE_SECONDS:
                # The file says FRESH; the clock disagrees. The clock wins,
                # because `registry_status` is what the writer believed when it
                # wrote, and time has passed since.
                status = "STALE"
            return {
                "authority": "ability-management",
                "this_is": "a local observed projection, not the authority",
                "registry_status": status,
                "generated_at": document.get("generated_at"),
                "age_seconds": age,
                "stale_after_seconds": MAX_PROJECTION_AGE_SECONDS,
                "source_revision": document.get("source_revision"),
                "stale": status != "FRESH",
                "source": str(candidate),
                # Carried through so `ability.list` can return it. Dropping it
                # here and re-deriving it downstream is how a projection ends
                # up reported as FRESH with nothing in it.
                "abilities": document.get("abilities") or [],
                "note": (
                    "readable while ability-management is unreachable, which is "
                    "the point (§28). A stale projection is usable and is not "
                    "an installation authority: do not install from it."
                ),
            }
        return {
            "authority": "ability-management",
            "this_is": "a local observed projection, not the authority",
            "registry_status": "NOT_MATERIALIZED",
            "source": str(state / "effective.json"),
            "stale": True,
            "abilities": None,
            "note": (
                "no local capability projection has been materialized at "
                "state/serveros/abilities/effective.*. This is NOT the same as "
                "'no abilities are installed' — nobody has asked. The node "
                "still works; the answer is that it does not have one."
            ),
        }

    def _r_ability_list(self, params) -> dict:
        # No re-derivation: whatever `_capability_view` established about
        # whether a projection exists is the answer, and filling in `[]` for
        # the two states that mean "there is nothing to read" would collapse
        # them back into "there is nothing installed".
        return self._capability_view()

    def _r_ability_status(self, params) -> dict:
        view = self._capability_view()
        name = params.get("name")
        if name is None:
            return view
        abilities = view.get("abilities") or []
        match = next((a for a in abilities
                      if isinstance(a, dict) and a.get("name") == name), None)
        if match is None:
            return {**view, "name": name, "installed": None,
                    "note": f"{name!r} is not in the local projection. Absence "
                            f"from a projection is not proof of absence on the "
                            f"authority."}
        return {**view, "name": name, "installed": True, "ability": match}

    def _r_device_show(self, params) -> dict:
        host = self.inventory.host_summary()
        return {
            "device_ref": "device:server-beijing",
            "host": host,
            "managed": {"count": len(self.registry),
                        "protected": len(self.registry.protected()),
                        "automatable": len(self.registry.automatable())},
        }

    def _r_context_current(self, params) -> dict:
        paths = paths_mod.Paths()
        return {"roots": {n: str(paths.root(n)) for n in paths.names},
                "note": "ServerOS roots are derived from the root contract, not from cwd"}

    def _r_path_resolve(self, params) -> dict:
        return paths_mod.Paths().resolve(_required(params, "ref"))

    def _r_path_admit(self, params) -> dict:
        return paths_mod.Paths().admit(_required(params, "path"),
                                       writing=params.get("writing", True))

    def _r_runtime_list(self, params) -> dict:
        return {"count": len(self.registry), "resources": [
            {"id": r.id, "kind": r.kind, "owner": r.owner_ref,
             "lifecycle": r.lifecycle, "desired": r.desired,
             "protection": r.protection, "automatable": r.is_automatable}
            for r in self.registry.all()
        ]}

    def _r_runtime_status(self, params) -> dict:
        resource = self.registry.get(_required(params, "id"))
        health = probes_mod.observe(resource, self.host)
        return {"id": resource.id, "health": health.show(),
                "action": health_mod.action_for(resource, health)}

    def _r_runtime_explain(self, params) -> dict:
        resource = self.registry.get(_required(params, "id"))
        return {"id": resource.id, "record": resource.record,
                "automatable": resource.is_automatable,
                "why_not_automatable": resource.why_not_automatable(),
                "budget": self.budget.may_act(resource.id),
                "last_action": self.budget.last_action(resource.id)}

    def _r_resource_summary(self, params) -> dict:
        return {"host": self.inventory.host_summary(),
                "disk": self.inventory.disk_status(),
                "advisory": self.inventory.pressure()}

    def _r_resource_hygiene(self, params) -> dict:
        hygiene = hygiene_mod.Hygiene(self.registry, self.inventory)
        return hygiene.report()

    # -- the shared answer layer (§3/§5/§6/§10/§14) --------------------------

    def _r_runtime_groups(self, params) -> dict:
        return view_mod.runtime_groups(answered_by=ANSWERED_BY)

    def _r_runtime_group(self, params) -> dict:
        return view_mod.runtime_group(_required(params, "ref"),
                                      answered_by=ANSWERED_BY)

    def _r_runtime_tree(self, params) -> dict:
        return view_mod.runtime_tree(_required(params, "ref"),
                                     answered_by=ANSWERED_BY)

    def _r_runtime_budget(self, params) -> dict:
        return view_mod.retry_budget(state_dir=self.state_dir, registry=self.registry,
                                     answered_by=ANSWERED_BY)

    def _r_process_top(self, params) -> dict:
        return view_mod.process_top(limit=int(params.get("limit", 10)),
                                    by=params.get("by", "cpu"),
                                    answered_by=ANSWERED_BY)

    def _r_process_trace(self, params) -> dict:
        return view_mod.process_trace(int(_required(params, "pid")),
                                      answered_by=ANSWERED_BY)

    def _r_anomaly_list(self, params) -> dict:
        return view_mod.anomaly_list(registry=self.registry, answered_by=ANSWERED_BY)

    def _r_anomaly_explain(self, params) -> dict:
        return view_mod.anomaly_explain(_required(params, "subject"),
                                        registry=self.registry,
                                        answered_by=ANSWERED_BY)

    def _r_container_attribution(self, params) -> dict:
        return view_mod.container_attribution(registry=self.registry,
                                              answered_by=ANSWERED_BY)

    def _r_observation_get(self, params) -> dict:
        if self.last_tick is None:
            return {"tick": None,
                    "note": "no tick has completed yet. An empty answer here "
                            "means no observation, not a healthy node."}
        return {"tick": self.last_tick["tick_started_at"],
                "observations": self.last_tick["observations"],
                "drift": self.last_tick["drift"]}

    # -- the server --------------------------------------------------------

    def serve_forever(self, *, interval: float = 60.0, stop_after: int | None = None) -> None:
        """The loop. Deliberately single-threaded and deadline-driven.

        There is no thread pool and no work queue: the amount of work here is
        bounded by the registry, and concurrency would make "what did it see"
        depend on scheduling. Requests are served between ticks, on the same
        thread, so an IPC answer can never be computed against a half-updated
        observation set.
        """
        server = self._open_socket()
        self._log(f"serverosd listening on {self.socket_path}")

        deadline = self._now() + interval
        ticks = 0
        try:
            while True:
                now = self._now()
                if now >= deadline:
                    try:
                        report = self.tick()
                        ticks += 1
                        self._log(
                            f"tick {ticks}: {len(report['observations'])} observed, "
                            f"{len(report['drift'])} drift, "
                            f"{len(report['decisions'])} decisions"
                        )
                    except Exception as exc:  # noqa: BLE001 - the loop survives
                        # A failing tick must not kill the daemon: the next
                        # one may succeed, and a control plane that exits on
                        # the first bad measurement is an outage generator.
                        self._log(f"tick failed: {type(exc).__name__}: {exc}")
                    deadline = now + interval
                    if stop_after is not None and ticks >= stop_after:
                        return
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    if not self._endpoint_live():
                        server = self._rebind(server)
                    continue
                except OSError:
                    continue
                try:
                    self._serve_connection(connection)
                finally:
                    connection.close()
        finally:
            server.close()
            self._release_socket()

    def _serve_connection(self, connection: socket.socket) -> None:
        connection.settimeout(10.0)
        buffer = b""
        try:
            while b"\n" not in buffer:
                chunk = connection.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > 1_000_000:
                    connection.sendall(json.dumps(
                        _error(None, "REQUEST_TOO_LARGE", "request exceeded 1 MiB")
                    ).encode() + b"\n")
                    return
        except (socket.timeout, OSError):
            return
        line, _, _ = buffer.partition(b"\n")
        try:
            request = json.loads(line.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = _error(None, "BAD_REQUEST", f"{type(exc).__name__}: {exc}")
        else:
            response = self.handle(request)
        try:
            connection.sendall(json.dumps(response, default=str).encode() + b"\n")
        except OSError:
            pass

    def _prepare_socket(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            # A stale socket file outlives its daemon. Connecting to it fails
            # with ECONNREFUSED, which reads as "the daemon is down" — and it
            # IS down, so removing the file it left behind is not destructive.
            self.socket_path.unlink()

    def _open_socket(self) -> socket.socket:
        """Bind the listener and remember which socket file is ours.

        The inode is kept because the path alone does not identify a socket:
        a restart can leave two daemons naming the same file in sequence, and
        "remove the file at my path" is then an operation on somebody else's
        socket.
        """
        self._prepare_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(1.0)
        try:
            self._socket_ino = self.socket_path.stat().st_ino
        except OSError:
            self._socket_ino = None
        return server

    def _endpoint_live(self) -> bool:
        """Is the file this daemon bound still the file at that path?

        Asked between accepts, on the one-second timeout — a stat, once a
        second, on a daemon measured at 4 file descriptors and ~25 ms of CPU
        per minute-long tick.
        """
        if self._socket_ino is None:
            return True
        try:
            return self.socket_path.stat().st_ino == self._socket_ino
        except OSError:
            return False

    def _rebind(self, server: socket.socket) -> socket.socket:
        """Put the endpoint back after something removed it.

        MEASURED 2026-09-13: this happened for real. A piloted daemon logged
        `listening on /run/user/1006/serveros/serveros.sock`, ticked happily
        for minutes, and held an UNLINKED socket the whole time — `fd 3 ->
        socket:[69123718]` with no file to reach it by. Every client got
        ENOENT. The daemon had not noticed, had no reason to notice, and
        reported itself well while serving nobody.

        A control plane that cannot be reached must not be the last to know.
        Re-binding is safe precisely because we checked: the file at our path
        is gone, so there is nothing of anyone else's to overwrite.
        """
        server.close()
        server = self._open_socket()
        self._log(
            f"serverosd re-bound {self.socket_path}: the socket file was removed "
            f"while this daemon was running, so the previous endpoint was "
            f"unreachable and nothing could have said so"
        )
        return server

    def _release_socket(self) -> None:
        """Remove our socket file on the way out — only if it is still ours.

        During a restart the old daemon's teardown and the new daemon's bind
        meet on the same path. An unconditional unlink there deletes the
        SOCKET OF THE DAEMON THAT JUST STARTED, which is how a freshly
        restarted service comes up healthy and unreachable at the same time.
        The inode is the identity; the path is only an address.
        """
        if self._socket_ino is None:
            return
        try:
            if self.socket_path.stat().st_ino != self._socket_ino:
                return
            self.socket_path.unlink()
        except OSError:
            pass


def _error(method, code: str, message: str) -> dict:
    return {"ok": False, "method": method, "error": {"code": code, "message": message}}


# What every daemon-served answer says about itself, so that a caller reading a
# socket answer and one reading `serverosctl` output can tell which they have.
ANSWERED_BY = "serverosd"


def _required(params: dict, name: str):
    """A parameter the method cannot answer without, refused by name.

    `params["ref"]` raises a bare `KeyError` which `handle()` turns into an
    error whose message is `'ref'` — a code, not a sentence. The caller of a
    control plane should be told which argument is missing and what a valid one
    looks like.
    """
    if name not in params or params[name] in (None, ""):
        raise ValueError(
            f"missing required parameter {name!r}. This method cannot answer "
            f"without it, and answering with an empty result would read as "
            f"'nothing to report'."
        )
    return params[name]


MAX_PROJECTION_AGE_SECONDS = 24 * 3600


def _age_seconds(generated_at, now: float) -> int | None:
    """How old a projection is, or None if it never said when it was made.

    `None` rather than 0: an undated projection is not a fresh one, and a
    missing timestamp defaulting to "just now" is how a three-day-old
    capability list gets read as current.
    """
    if not generated_at:
        return None
    import datetime

    try:
        stamp = datetime.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return int(now - stamp.timestamp())


def _iso(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    # Rename, so a reader never sees a half-written observation. The daemon
    # is the only writer here and the MCP adapter reads these files, so a
    # torn read is a real possibility on a machine this loaded.
    tmp.replace(path)


def default_socket_path() -> Path:
    """§22: a Unix socket in the runtime directory. Never TCP, never public."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / "serveros/serveros.sock"


def default_state_dir() -> Path:
    return Path(os.environ.get("SERVEROS_STATE_DIR")
                or "/opt/applications/data/home-hwz/state/serveros") / "runtime"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="serverosd",
        description="ServerOS node runtime. Observe-only unless "
                    "--enable-reconcile is passed.",
    )
    parser.add_argument("--registry", help="managed-runtime registry document")
    parser.add_argument("--state-dir", help="where observations are persisted")
    parser.add_argument("--socket", help="Unix socket path")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between ticks")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--enable-reconcile", action="store_true",
                        help="allow restarts of automatable resources. Off by default; "
                             "see the module docstring for the full gate.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    registry_path = Path(args.registry) if args.registry else Path(
        os.environ.get("SERVEROS_STATE_DIR")
        or "/opt/applications/data/home-hwz/state/serveros"
    ) / "runtime/registry/resources.yaml"
    if not registry_path.exists():
        sys.stderr.write(f"serverosd: no registry at {registry_path}\n")
        return 2
    registry = registry_mod.Registry(path=registry_path)
    daemon = Daemon(
        registry,
        state_dir=Path(args.state_dir) if args.state_dir else default_state_dir(),
        socket_path=Path(args.socket) if args.socket else default_socket_path(),
        enable_reconcile=args.enable_reconcile,
        log=lambda message: sys.stderr.write(f"serverosd: {message}\n"),
    )
    if args.once:
        report = daemon.tick()
        if args.json:
            json.dump(report, sys.stdout, indent=2, sort_keys=True, default=str)
            sys.stdout.write("\n")
        else:
            for rid, obs in report["observations"].items():
                state = (obs.get("health") or {}).get("state") or obs.get("state")
                print(f"{rid:<28} {state}")
            print(f"\n{len(report['drift'])} drift item(s), "
                  f"{len(report['decisions'])} decision(s)")
        return 0
    daemon.serve_forever(interval=args.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
