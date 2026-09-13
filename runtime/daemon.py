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
import struct
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
agent_sessions_mod = _load("serveros_runtime_agent_sessions", "agent_sessions.py")
# The shared answer layer (§3/§6/§10/§14). The daemon computes NOTHING of its
# own for these: it forwards to the same functions `serverosctl` calls, so the
# CLI answer and the socket answer can differ only in the moment they were
# taken — which `answered_by` and `measured_at` make visible rather than
# something a caller has to assume.
view_mod = _load("serveros_runtime_view", "view.py")
# §4/§5/§6. The trusted-actor and approval kernels, plus the store they rest on.
# The kernels are pure and the store is not, which is the split that lets the
# decision a security boundary rests on be tested without a filesystem.
agent_control_store_mod = _load("serveros_runtime_agent_control_store",
                                "agent_control_store.py")
_ACTOR_PATH = _HERE.parent / "context" / "actor.py"
if "_serveros_context_actor" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_serveros_context_actor", _ACTOR_PATH)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["_serveros_context_actor"] = _module
    _spec.loader.exec_module(_module)
actor_mod = sys.modules["_serveros_context_actor"]
approval_mod = _load("serveros_runtime_approval", "approval.py")


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

    def may_act(self, resource_id: str, *, max_attempts: int | None = None,
                backoff: list | tuple | None = None) -> dict:
        """§11: the bounds come from the resource when it declares them.

        The module constants are the fallback, not the authority. A resource
        that says `max_attempts: 1` and is given three is a resource whose
        declaration nothing read — and a per-resource policy block that no
        code consults is the exact defect this round keeps finding, one layer
        down from where it was found last time.
        """
        limit = MAX_ATTEMPTS if max_attempts is None else int(max_attempts)
        ladder = tuple(backoff) if backoff else BACKOFF_SECONDS
        entry = self._entry(resource_id)
        used = len(entry["attempts"])
        if used >= limit:
            oldest = min(entry["attempts"])
            return {
                "allowed": False,
                "reason": "BUDGET_EXHAUSTED",
                "attempts_in_window": used,
                "max_attempts": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": int(WINDOW_SECONDS - (self._now() - oldest)),
                "note": (
                    f"{used} attempts within {WINDOW_SECONDS}s and this "
                    f"resource permits {limit}. One more would not be a new "
                    f"attempt at a fix, it would be the loop. Report "
                    f"DEGRADED and ask a person."
                ),
            }
        # The waiting period belongs to the attempt ABOUT to be made, so it is
        # indexed by how many have already happened: 0 attempts means the
        # first retry waits BACKOFF_SECONDS[0]. Indexing by `used` instead
        # would silently skip the shortest interval — the one that matters
        # most, because it is the one that runs while a person is still
        # looking at the screen.
        wait = ladder[min(used, len(ladder) - 1)] if used else 0
        if entry["attempts"] and used:
            wait = ladder[min(used - 1, len(ladder) - 1)]
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

    def perform(self, operation: str, resource) -> dict:
        """Dispatch one §8 operation.

        This method is the whole of what the runtime can do to the machine.
        The vocabulary is closed by `managed-runtime-contract.yaml` and the
        mode→operation mapping is read from the same file, so adding a sixth
        operation is an edit to a reviewed document rather than a new branch
        somebody adds here.

        An operation with no actuator returns NO_ACTUATOR — the same answer
        the restart path already gives for a resource kind nobody wrote an
        actuator for. `RECONNECT` and `STOP` are declared and unimplemented
        on purpose: a declared operation with no implementation has to be
        visible, or the next person infers it works from its absence in a
        list of failures.
        """
        if operation == "RESTART":
            return self.restart(resource)
        if operation in ("RECONNECT", "STOP"):
            return {
                "performed": False,
                "reason": "NO_ACTUATOR",
                "operation": operation,
                "note": (
                    f"{operation} is declared in the operation vocabulary and "
                    f"has no actuator. It is not a lesser RESTART: for a "
                    f"transport-endpoint or a tunnel the channel is the thing "
                    f"that failed, and running the restart path against it "
                    f"would be acting on a different object and reporting it "
                    f"as this one."
                ),
            }
        return {
            "performed": False,
            "reason": "UNKNOWN_OPERATION",
            "operation": operation,
            "note": (
                "not in the declared operation vocabulary. Refused rather than "
                "defaulted, because the one thing a control plane must never "
                "do is turn an unrecognised instruction into the nearest "
                "recognised one."
            ),
        }

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


def _operation_for_mode(mode: str) -> str:
    """The §8 operation a reconcile mode authorises.

    Read from `managed-runtime-contract.yaml` so the mapping is declared once.
    An unknown mode raises rather than falling back: the registry validates
    modes at registration, so reaching here with one means the contract and
    the validator have diverged, and the safe response to that is to stop
    rather than to perform the nearest recognised operation.
    """
    mapping = registry_mod.contract()["reconcile_policy"]["mode_operations"]
    if mode not in mapping:
        raise registry_mod.RegistryError(
            f"reconcile mode {mode!r} has no declared operation; the contract's "
            f"modes and its mode_operations disagree"
        )
    operation = mapping[mode]
    if operation is None:
        # `observe` authorises nothing. Returning a name here would be the
        # exact defect this whole block exists to prevent, so it is answered
        # as a refusal.
        return "NONE"
    return operation


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
                 runner=None, now=None, sleep=None, log=None) -> None:
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
        # Injectable alongside `now`, because the settle window below is a
        # real wait and a test that cannot compress it either sleeps for
        # seconds or does not test the window at all. Tests pass a clock and
        # a sleep that advance each other; the daemon passes neither.
        self._sleep = sleep or time.sleep
        self._log = log or (lambda message: None)

        self.budget = Budget(self.state_dir / "health/reconcile-state.json", now=self._now)
        # Passed `runner`, not `host`: the actuator must not be reachable
        # through the object every probe holds. See SubprocessRunner.
        self.actuator = Actuator(runner=runner, dry_run=not enable_reconcile)
        self.last_tick: dict | None = None
        self.drift: list[dict] = []
        # The node runtime's own facts, for `node.status`. A control plane that
        # cannot say how long it has been up and when it last looked is asking
        # a reader to trust an answer without being able to date it.
        self.started_at = self._now()
        self.interval: float | None = None
        self.ticks = 0
        self.last_tick_monotonic: float | None = None
        self.tick_failures: list[dict] = []

        # Register the daemon's own directories through the path contract,
        # rather than mkdir-ing a path that looks right. A daemon that invents
        # its own state location is the exact drift §40 is about.
        self._paths = paths_mod.Paths()

        # §5/§6, §23. Trusted contexts and approvals survive a restart, and the
        # store is opened lazily so that `serverosd --check` and the many tests
        # that never touch agent control do not create a file they do not use.
        self._control_store: object | None = None
        # Whose connection the request in flight arrived on. Cleared by
        # `handle`; read only by `agent.context.attach`.
        self._peer: dict | None = None

    # A status answer older than this many tick intervals is labelled stale.
    # Three, not one: a single missed deadline is a slow machine, and calling
    # that stale would make the field useless on a busy host.
    STALE_TICKS = 3

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
        self.last_tick_monotonic = self._now()
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
            # §8/§11. The operation is derived from the resource's own policy,
            # never from the caller. Recorded even when the decision is
            # "recommendation only", because a recommendation that does not
            # name the operation it recommends is not actionable by the person
            # it is addressed to.
            #
            # Recording the mode alongside the operation is what makes the
            # decision auditable: "RESTART" alone cannot distinguish a resource
            # whose policy says restart from one whose policy was bypassed.
            policy = resource.reconcile
            operation = _operation_for_mode(policy["mode"])
            split = resource.automatability()
            entry = {
                "resource": resource.id,
                "action": "RECONCILE",
                "automatable": split["automatable"],
                "recommendation_only": split["recommendation_only"],
                "reconcile_mode": policy["mode"],
                "operation": operation,
            }
            if not resource.is_automatable:
                entry.update({
                    "performed": False,
                    # `blocked_by` keeps its one meaning across the tree: these
                    # are reasons that FORBID the action. A resource whose
                    # policy is merely `observe` is not forbidden — it is
                    # permitted-and-not-enabled, which is a different fact and
                    # is reported as `blocked_by_policy` so that a reader is
                    # not told a policy question is a permission question.
                    "blocked_by": split["permission"],
                    "blocked_by_policy": split["policy"],
                    "note": (
                        "§9: recommendation only. The health model says this "
                        "is a candidate and nothing here authorises ServerOS "
                        "to carry the operation out by itself."
                    ) if split["recommendation_only"] else (
                        "the health model said RECONCILE; the registry says no"
                    ),
                })
                decisions.append(entry)
                continue
            budget = self.budget.may_act(resource.id,
                                         max_attempts=policy["max_attempts"],
                                         backoff=policy["backoff"])
            entry["budget"] = budget
            if not budget["allowed"]:
                # §54: an exhausted budget is NEEDS_HUMAN, and it has to say
                # so in the field a reader filters on. Leaving `action` at
                # RECONCILE with a prose `escalate_to` beside it produces a
                # decision record that goes on naming a reconcile candidate
                # every tick, forever, for a resource nothing will reconcile —
                # which is the restart loop reported instead of run.
                entry.update({
                    "action": "NEEDS_HUMAN",
                    "performed": False,
                    "budget_exhausted": True,
                    "escalate_to": "DEGRADED/NEEDS_HUMAN",
                    "because": (
                        f"the {policy['mode']} budget is spent: "
                        f"{budget['attempts_in_window']} attempt(s) in the "
                        f"window against max_attempts={policy['max_attempts']}"
                        + (
                            f", next permitted in "
                            f"{budget['backoff_before_next_attempt_seconds']}s"
                            if budget.get("backoff_before_next_attempt_seconds")
                            else ""
                        ) + ". Nothing here will act again until a person "
                        "does."
                    ),
                    "note": (
                        "the budget is the §19 guard. Repeatedly restarting a "
                        "resource that keeps failing is not recovery, it is the "
                        "loop."
                    ),
                })
                decisions.append(entry)
                # And the observation says the same thing. `observe_all` ran
                # before this point and could only ask the health model, which
                # does not know the budget — so without this, `observations[id]
                # .action.action` went on reading RECONCILE while the decision
                # beside it read NEEDS_HUMAN. Two fields disagreeing about one
                # resource is how a reader learns to trust neither.
                self._escalate_observation(observations, resource.id, entry)
                continue
            if not self.enable_reconcile:
                # The process-level interlock. Note what it does NOT do: it is
                # not what authorises a resource. Every resource whose policy
                # is `observe` has already been filtered out above, so this
                # switch can only ever *withhold* an action a resource already
                # permitted. §11 forbids one switch that turns recovery on for
                # everything; this is the opposite shape — it grants nothing on
                # its own.
                dry = self.actuator.perform(operation, resource)
                entry.update({
                    "performed": False,
                    "dry_run": True,
                    "would_run": dry.get("would_run"),
                    "dry_run_reason": dry.get("reason"),
                    "note": (
                        "the resource's policy permits this operation and the "
                        "process interlock withholds it. --enable-reconcile "
                        "clears the interlock; it does not clear "
                        "reconcile.mode=observe, which is the gate that applies "
                        "to every resource registered on this host."
                    ),
                })
                decisions.append(entry)
                continue
            result = self.actuator.perform(operation, resource)
            self.budget.record(resource.id, operation.lower())
            entry["result"] = result
            entry["performed"] = bool(result.get("performed"))
            # Re-probe, letting the resource settle: "did the restart fix it"
            # is a question only a probe can answer, and assuming yes is how a
            # restart loop reports success.
            entry["reprobe"] = self._reprobe(resource, settle=True)
            # Whether the operation worked is a separate fact from whether it
            # was carried out, and the two are reported separately so that
            # "performed: true" cannot be read as "fixed".
            entry["recovered"] = entry["reprobe"]["settled"]
            decisions.append(entry)
        return decisions

    def _escalate_observation(self, observations: dict, resource_id: str,
                              entry: dict) -> None:
        """Keep `observations[id].action` and the reconcile decision in step.

        The observation is built before the budget is consulted, so on its own
        it can only ever report the health model's verdict. The budget is the
        other half of "may this be acted on", and it belongs to the same
        report.
        """
        row = observations.get(resource_id)
        if not isinstance(row, dict) or not isinstance(row.get("action"), dict):
            return
        row["action"] = {
            **row["action"],
            "action": entry["action"],
            "budget_exhausted": entry["budget_exhausted"],
            "because": entry["because"],
        }

    # How long a restarted resource is given to become answerable again, and
    # how often to ask in the meantime. Measured, not guessed: the §53
    # acceptance fixture is a trivial HTTP responder and still needed ~1s
    # after `systemctl restart` returned before it accepted a connection.
    SETTLE_SECONDS = 10.0
    SETTLE_INTERVAL = 0.5

    def _reprobe(self, resource, *, settle: bool = False) -> dict:
        """Ask the resource whether the operation worked.

        Measured on 2026-09-13: a `systemctl --user restart` that returned
        exit 0 on a trivial HTTP responder was recorded as `FUNCTIONALLY_FAILED`,
        because the re-probe ran in the window between the supervisor returning
        and the service listening. That is the restart's *latency* being
        reported as the restart's *effect*. §53 asks for a re-probe that is OK,
        so a resource that is not yet answering is asked again, for a bounded
        time, until it answers.

        It is deliberately not "sleep N, then ask once": a restart that did not
        help must be reported as not helping, and the budget must count it. The
        loop leaves as soon as the resource answers, and a resource that never
        answers returns its last real observation with `settled: false`. It
        never manufactures a success and never waits indefinitely.

        `settle=False` takes a single sample, which is the honest answer when
        no operation was performed and there is nothing to settle from.
        """
        started = self._now()
        deadline = started + self.SETTLE_SECONDS if settle else None
        samples = 0
        while True:
            try:
                health = probes_mod.observe(resource, self.host)
            except registry_mod.RegistryError as exc:
                return {"state": "UNKNOWN", "error": str(exc), "settled": False}
            samples += 1
            state = health.state
            answered = state in health_mod.HEALTHY_STATES
            if answered or deadline is None or self._now() >= deadline:
                result = {
                    "state": state,
                    "reason": health.reason,
                    "settled": answered,
                    "samples": samples,
                    "waited_seconds": round(self._now() - started, 2),
                }
                if not answered:
                    result["note"] = (
                        f"the resource did not answer within "
                        f"{self.SETTLE_SECONDS:g}s of the operation. Reported as "
                        "the last real observation rather than retried further: "
                        "the budget counts attempts, and a longer wait here "
                        "would be the restart loop wearing a timeout."
                    )
                return result
            self._sleep(self.SETTLE_INTERVAL)

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

    def handler_map(self) -> dict:
        """The served methods, as one object.

        Separate from `handle` so that the map and `_methods()` can be
        compared. While this was a literal inside `handle`, nothing could
        enumerate it, and the docstring on the coverage test described a drift
        the test was structurally unable to detect.
        """
        return {
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
            # Internal policy primitive used by the dedicated Agent Session MCP.
            # The daemon decides intent only and never calls an Agent provider.
            "agent.action.admit": self._r_agent_action_admit,
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
            "node.status": self._r_node_status,
            # §5/§6. The trusted half of Agent Control. These are on the
            # NODE runtime plane, not on the control-agent MCP surface: an
            # Agent runtime is a caller of `agent.*`, never a source of the
            # identity behind one.
            "agent.context.attach": self._r_agent_context_attach,
            "agent.context.inspect": self._r_agent_context_inspect,
            "agent.approval.inspect": self._r_agent_approval_inspect,
            "agent.approval.record": self._r_agent_approval_record,
            "agent.control.status": self._r_agent_control_status,
        }

    def handle(self, request: dict, *, peer: dict | None = None) -> dict:
        """Answer one IPC request. Unknown methods are refused by name.

        `peer` is who is on the other end of the connection — pid, uid, gid,
        from `SO_PEERCRED`. It is a parameter rather than something a handler
        fetches, because a handler that can go looking for its caller's
        identity can also go looking at the wrong moment. Only one method
        reads it (`agent.context.attach`), and that method's whole job is to
        bind an observation to the process that made it.
        """
        method = request.get("method")
        params = request.get("params") or {}
        handler = self.handler_map().get(method)

        if handler is None:
            return _error(
                method, "UNKNOWN_METHOD",
                f"{method!r} is not served. Known: {sorted(self._methods())}",
            )
        # Set for the duration of the call and cleared after. Safe, and only
        # safe, because this daemon serves one connection at a time on one
        # thread: a thread pool here would turn this into a race and make the
        # sentence above a lie.
        self._peer = peer
        try:
            return {"ok": True, "method": method, "result": handler(params)}
        except Exception as exc:  # noqa: BLE001 - the caller needs the reason
            return _error(method, type(exc).__name__, str(exc))
        finally:
            self._peer = None

    def _methods(self) -> tuple[str, ...]:
        return ("device.show", "context.current", "path.resolve", "path.admit",
                "runtime.list", "runtime.status", "runtime.explain",
                "resource.summary", "resource.hygiene", "observation.get",
                "ability.list", "ability.status", "agent.action.admit",
                "runtime.groups", "runtime.group", "runtime.tree",
                "runtime.budget", "process.top", "process.trace",
                "anomaly.list", "anomaly.explain", "container.attribution",
                "node.status",
                # §5/§6. Trusted actor contexts and approvals. `agent.approval.record`
                # is deliberately NOT in the MCP surface — see `mcp/agent_mcp.py`.
                "agent.context.attach", "agent.context.inspect",
                "agent.approval.inspect", "agent.approval.record",
                "agent.control.status")

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

    def _r_node_status(self, params) -> dict:
        """The node runtime, describing itself.

        The one method on this surface that is NOT a forward to `view.py`,
        because it is the only question `view.py` cannot answer: `view` reads
        the world from the calling process, and this is a fact about *this*
        process — how long it has been up, how many times it has looked, and
        whether any of those looks failed.

        `answer_is_stale` is the field that makes the rest readable. A status
        query answered by a daemon that stopped ticking ten minutes ago is not
        the same answer as one from a daemon that ticked a second ago, and the
        caller cannot tell them apart from `ticks` alone.
        """
        now = self._now()
        age = (None if self.last_tick_monotonic is None
               else round(now - self.last_tick_monotonic, 1))
        stale_after = (self.interval or 60.0) * self.STALE_TICKS
        return {
            "device_ref": self.registry.device_ref,
            "pid": os.getpid(),
            "started_at": _iso(self.started_at),
            "uptime_seconds": round(now - self.started_at, 1),
            "ticks": self.ticks,
            "tick_interval_seconds": self.interval,
            "last_tick_at": (self.last_tick or {}).get("tick_started_at"),
            "seconds_since_last_tick": age,
            "stale_after_seconds": stale_after,
            # A daemon that has not ticked yet is not stale — it is new. The
            # distinction matters because `--once` never enters this loop, and
            # reporting a fresh one-shot run as stale would be a false alarm
            # in the one place a false alarm costs the most.
            "answer_is_stale": bool(age is not None and age > stale_after),
            "tick_failures": list(self.tick_failures[-5:]),
            "tick_failure_count": len(self.tick_failures),
            "registry_count": len(self.registry),
            "reconcile_enabled": self.enable_reconcile,
            "state_dir": str(self.state_dir),
            "socket": str(self.socket_path),
            "note": (
                "`reconcile_enabled` is this process's interlock, not a "
                "resource's permission: every resource whose `reconcile.mode` "
                "is the default `observe` is filtered out before the interlock "
                "is consulted. A `false` here means this process would not "
                "act; it does not mean anything would otherwise."
            ),
        }

    def _r_device_show(self, params) -> dict:
        host = self.inventory.host_summary()
        return {
            # Read from the registry. This was a hard-coded string, so the
            # daemon answered "which device is this" from a literal while
            # `serverosctl device show` answered it from the registry — two
            # answers to one question, and the daemon's was the one an agent
            # over IPC would believe. They agreed only while nobody renamed
            # the node, which is precisely when an identity is load-bearing.
            "device_ref": self.registry.device_ref,
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

    def _r_agent_action_admit(self, params) -> dict:
        # `context` is required, not optional-with-a-default. An admission
        # decision that silently ignores the subject it is adjudicating cannot
        # become a context-sensitive one later without every existing caller
        # having been wrong in the meantime; requiring it now means the callers
        # that will need it already send it.
        #
        # §5. The payload is refused if it carries identity. Not ignored —
        # refused by name, with the offending keys listed. A caller that sends
        # `{"actor": "hwz"}` and gets back a decision has learned the wrong
        # lesson about which fields ServerOS reads.
        actor_mod.refuse_if_forged(params)
        action = _required(params, "action")
        base = agent_sessions_mod.admission(action, _required(params, "context"))

        if base["decision"] != "REQUIRE_HUMAN":
            # Read-only and delegation actions are decided by the contract
            # alone. Nothing about who asked changes that, and consulting the
            # store here would make an ordinary observation depend on a
            # control-plane file being readable.
            return {**base, "approval": None}

        # Human-gated. The verdict below is assembled from three separate
        # facts, and every one of them is reported even when the answer is no:
        # whether a trusted context was presented at all, whether it still
        # authorizes, and whether an approval matches. Collapsing them into a
        # bare DENY is what sends a requester to ask for something they
        # already have.
        context_ref = params.get("context_ref")
        if not context_ref:
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": ("no trusted context was presented, so no approval can "
                           "be matched to anyone"),
                "repair": ("obtain a trusted context from a HumanControlProvider "
                           "and present its context_ref"),
            }}

        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            # A well-formed ref ServerOS never minted. Reported as MISS rather
            # than raised, and never substituted: a caller that invented a
            # handle learns that inventing one does not work.
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": resolved["because"],
                "context_ref": context_ref, "context_found": False,
            }}

        record = resolved["context"]
        authority = actor_mod.authorizes(record, _iso(self._now()))
        if not authority["authorizes"]:
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": authority["because"],
                "context_ref": context_ref, "context_found": True,
                "context_freshness": authority["state"],
            }}

        held = self.control_store().for_actor_action(
            actor_ref=record["actor_ref"], action=action)
        verdict = approval_mod.decide(
            approvals=held,
            request={"action": action, "target": params.get("target") or {},
                     "params": params.get("args") or {},
                     "actor_ref": record["actor_ref"]},
            context_ref=context_ref,
            now=_iso(self._now()),
        )
        if verdict["decision"] != "ALLOW":
            return {**base, "approval": verdict, "context_ref": context_ref}

        # Approved. The decision is ALLOW, and the class changes from
        # `human_gated` to `human_approved`: `human_gated` names the rule that
        # applied, and leaving it on an allowed request would make an audit
        # unable to tell a request that was gated from one that is still
        # waiting. The approval is NOT consumed here — `consume` is a write
        # that happens after the action runs, so a caller that fails to
        # perform the action can retry without spending a person's decision.
        return {"action": action, "decision": "ALLOW", "class": "human_approved",
                "reason": verdict["reason"], "context": dict(params.get("context") or {}),
                "approval": verdict, "context_ref": context_ref}

    # -- §5: trusted actor contexts ----------------------------------------

    def control_store(self):
        """The persisted contexts and approvals. Opened on first use."""
        if self._control_store is None:
            self._control_store = agent_control_store_mod.AgentControlStore(
                Path(self.state_dir) / "agent-control" / "store.json",
                now=self._now)
        return self._control_store

    def _r_agent_context_attach(self, params) -> dict:
        """Mint a trusted context from a provider report. §5's only doorway.

        Three refusals stand between an Agent runtime and a context that names
        a human, and this method is where all three are enforced:

          forged payload   the call carried `human` / `actor` / `approved`
          wrong observer   the report names a pid that is not this caller
          no human         the provider reported BLOCKED, so there is nobody

        The middle one is the one that makes this more than a schema check. A
        report is produced by running a probe inside the process tree of the
        turn being identified — that is the whole mechanism by which an
        identity provider decides who somebody is. So the report carries the
        pid that observed it, and
        this method requires that pid to be the process on the other end of
        this connection. An agent cannot present a report observed by somebody
        else's turn, because it cannot change which process it is.
        """
        actor_mod.refuse_if_forged(params)
        report = params.get("report")
        if not isinstance(report, dict):
            raise actor_mod.Rejected("agent.context.attach needs a `report` object "
                                     "from a HumanControlProvider")

        observed_by = report.get("provider_pid")
        peer = self._peer or {}
        peer_pid = peer.get("pid")
        if observed_by is None:
            raise actor_mod.Rejected(
                "the provider report does not say which process observed the "
                "actor. Without it ServerOS cannot tell an observation made in "
                "a real turn from one assembled by the caller.")
        if peer_pid is None:
            raise actor_mod.Rejected(
                "this request did not arrive on a socket, so there is no caller "
                "process to bind the observation to")
        if int(observed_by) != int(peer_pid):
            raise actor_mod.Rejected(
                f"the report was observed by pid {observed_by}, and this request "
                f"arrived from pid {peer_pid}. A trusted context binds to the "
                "process that established it; a report carried in from "
                "elsewhere is not evidence about this caller.")

        now = _iso(self._now())
        record = actor_mod.build(report, now=now)
        self.control_store().put_context(record)
        return {
            "context_ref": record["context_ref"],
            "actor_ref": record["actor_ref"],
            "observed_by_pid": int(observed_by),
            "provenance": record["provenance"],
            "provider_status": record["provider_status"],
            "freshness": actor_mod.freshness(record, now),
            "note": ("this ref is the caller's half of the protocol; ServerOS "
                     "resolves the actor from it and never from a field the "
                     "caller supplies"),
        }

    def _r_agent_context_inspect(self, params) -> dict:
        """What ServerOS recorded for a presented ref, aged now.

        Read-only, and it takes a REF rather than an actor. There is no method
        here that answers "who is this", because that question has no
        trustworthy answer outside the evidence that produced the ref.
        """
        ref = _required(params, "context_ref")
        resolved = actor_mod.resolve(ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"found": False, "context_ref": ref,
                    "because": resolved["because"]}
        record = resolved["context"]
        now = _iso(self._now())
        return {"found": True, "context_ref": ref,
                "actor_ref": record["actor_ref"],
                "human_control_provider": record["human_control_provider"],
                "transport": record["transport"],
                "observed_at": record["observed_at"],
                "expires_at": record["expires_at"],
                "freshness": actor_mod.freshness(record, now),
                "authorizes": actor_mod.authorizes(record, now)}

    # -- §6: approvals ------------------------------------------------------

    def _r_agent_approval_inspect(self, params) -> dict:
        """What a person has approved for this context, and whether it covers
        the request in hand.

        The actor comes from the context, never from the caller, and the
        request fingerprint is computed here rather than accepted — a
        caller-supplied fingerprint would let the requester choose what its
        approval was for.
        """
        context_ref = _required(params, "context_ref")
        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"found": False, "context_ref": context_ref,
                    "because": resolved["because"]}
        record = resolved["context"]
        action = _required(params, "action")
        target = params.get("target") or {}
        args = params.get("args") or {}
        held = self.control_store().for_actor_action(
            actor_ref=record["actor_ref"], action=action)
        out = {
            "found": True, "context_ref": context_ref, "action": action,
            "actor_ref": record["actor_ref"],
            "request_fingerprint": approval_mod.fingerprint(
                action=action, target=target, params=args),
            "held": [{"approval_ref": a.get("approval_ref"),
                      "state": a.get("state"),
                      "request_fingerprint": a.get("request_fingerprint"),
                      "expires_at": a.get("expires_at")} for a in held],
        }
        if action in approval_mod.human_gated_actions():
            out["verdict"] = approval_mod.decide(
                approvals=held,
                request={"action": action, "target": target, "params": args,
                         "actor_ref": record["actor_ref"]},
                context_ref=context_ref, now=_iso(self._now()))
        else:
            out["verdict"] = {
                "decision": "NOT_GATED", "action": action,
                "reason": (f"{action!r} is not a human-gated action, so there is "
                           "nothing for a person to approve"),
            }
        return out

    def _r_agent_approval_record(self, params) -> dict:
        """Record a human decision. Served, and not reachable by an Agent.

        This method exists, is tested, and is deliberately absent from the
        control-agent MCP surface. That absence is the enforcement, and it is
        the honest limit of what a single-user host can do: agents and the
        operator run as the same uid, so no permission bit separates them. What
        separates them is that the interface an agent speaks has no verb for
        granting approval, and a test asserts the tool list contains none.

        It refuses a provider that cannot carry approvals rather than
        recording a decision it did not receive. `human.approval.transport` is
        UNSUPPORTED on the configured provider — there is no callback surface,
        and §6 says to mark that as a limitation instead of improvising around
        it. Accepting a "decision" here would be exactly the improvised path.
        """
        actor_mod.refuse_if_forged(params)
        action = _required(params, "action")
        if action not in approval_mod.human_gated_actions():
            return {"recorded": False, "action": action,
                    "decision": "NOT_GATED",
                    "reason": (f"{action!r} is not a human-gated action; issuing "
                               "an approval for it would create authority the "
                               "contract never granted")}
        # Always refused, with or without an `inbound_event_ref`. Accepting a
        # reference to an event would be accepting the caller's word that the
        # event happened, which is the same as accepting the caller's word
        # about who approved — and that is what this whole section exists to
        # not do. The field is named in the refusal anyway, so that a client
        # which guessed at the shape learns why the shape does not exist yet.
        supplied = bool(params.get("inbound_event_ref"))
        return {"recorded": False, "action": action,
                "decision": "APPROVAL_TRANSPORT_UNSUPPORTED",
                "reason": (
                    ("an inbound_event_ref was supplied, and no provider on this "
                     "node can produce one: a reference is only evidence if "
                     "ServerOS received the event, and it has no surface that "
                     "could have. " if supplied else
                     "an approval must come from an inbound event the "
                     "HumanControlProvider attributed to a person and bound to "
                     "this request's fingerprint. Nothing supplied one. ")
                    + "The configured provider cannot: measured on 2026-09-14, "
                      "its event, message and approval endpoints all answer 404 "
                      "not_found_yet and its session list is read-only. ServerOS "
                      "reports a transport limitation rather than recording a "
                      "decision it did not receive (§6)."),
                "repair": ("record the decision deliberately as the operator "
                           "with `runtime/approval.py`, or wait for a provider "
                           "that carries approvals"),
                "implementation_status": ("the binding, the window, the "
                                          "consumption and the five verdicts are "
                                          "implemented and tested; what is "
                                          "missing is a transport, not a rule")}

    def _r_agent_control_status(self, params) -> dict:
        """How much of Agent Control is live on THIS node, and what it cannot see.

        Deliberately silent about which transport is in use, and about what that
        transport can do. §18 forbids the daemon from growing into a client of
        anything, and the first draft of this method broke that: it imported
        `mcp/human_control.py` to ask the provider for its own capability list.
        That import is a dependency, and a daemon that depends on a provider is
        a daemon that cannot answer when the provider is broken — which is the
        moment somebody needs the status.

        So the split is: the daemon answers for the control plane it OWNS (the
        store, the gated verbs, the decision it will make). The provider answers
        for the transport, on a surface that is allowed to know about transports
        — `python3 mcp/human_control.py --capabilities`. A caller that wants the
        whole picture asks both, and each answer names which it is.
        """
        store = self.control_store()
        return {
            "authority": "serverosd",
            "this_is": ("the node's own control-plane state. It says nothing "
                        "about which HumanControlProvider is configured or what "
                        "that provider can do; ask the provider surface for that."),
            "store": store.summary(),
            "human_gated_actions": list(approval_mod.human_gated_actions()),
            "identity_source": (
                "a trusted actor context can only be minted by "
                "`agent.context.attach`, from a report produced by a process "
                "the caller is. Nothing on this surface accepts an actor."),
            "approval_issuance": (
                "refused on every served method. Recording a human decision "
                "needs a transport that carries one, and no transport reachable "
                "from here does; see `agent.approval.record` for the reason."),
            "provider_surface": "mcp/human_control.py",
        }

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

        self.interval = interval
        deadline = self._now() + interval
        try:
            while True:
                now = self._now()
                if now >= deadline:
                    try:
                        report = self.tick()
                        self.ticks += 1
                        self._log(
                            f"tick {self.ticks}: {len(report['observations'])} observed, "
                            f"{len(report['drift'])} drift, "
                            f"{len(report['decisions'])} decisions"
                        )
                    except Exception as exc:  # noqa: BLE001 - the loop survives
                        # A failing tick must not kill the daemon: the next
                        # one may succeed, and a control plane that exits on
                        # the first bad measurement is an outage generator.
                        # Recorded as well as logged, because a log line is
                        # not something a status query can read.
                        self.tick_failures.append({
                            "at": _iso(now), "error": type(exc).__name__,
                            "message": str(exc),
                        })
                        self._log(f"tick failed: {type(exc).__name__}: {exc}")
                    deadline = now + interval
                    if stop_after is not None and self.ticks >= stop_after:
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
            response = self.handle(request, peer=_peer_credentials(connection))
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


def _peer_credentials(connection) -> dict | None:
    """Who is on the other end of an AF_UNIX connection, as the kernel tells it.

    `SO_PEERCRED` is filled in by the kernel, not sent by the client, which is
    the entire reason it is usable as evidence: a client cannot state its own
    pid here any more than it can state its own uid. Anyone can write
    `"pid": 1234` into a JSON body.

    Returns None when the platform has no such option (macOS, where the
    equivalent is `getpeereid` and the pid is not available at all). A caller
    that cannot be identified is reported as unidentified rather than guessed
    at — `agent.context.attach` refuses, by name, rather than proceeding on an
    assumption that happens to hold on Linux.
    """
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:  # pragma: no cover - Linux is the deployment target
        return None
    try:
        size = struct.calcsize("3i")
        raw = connection.getsockopt(socket.SOL_SOCKET, option, size)
        pid, uid, gid = struct.unpack("3i", raw)
    except OSError:  # pragma: no cover - the socket went away mid-request
        return None
    return {"pid": pid, "uid": uid, "gid": gid}


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
