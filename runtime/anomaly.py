#!/usr/bin/env python3
"""Anomaly detection and multi-signal classification.

The rule that makes this safe
-----------------------------
A single signal never marks a runtime stale. Not high CPU — a build is high
CPU. Not high memory — a database is high memory. Not age — everything
long-lived is old. Not an error loop — a retrying client is an error loop.

What may classify is a NAMED COMBINATION of independent signals, written down
in `attribution-contract.yaml` before this module runs, so the same facts
produce the same answer twice. Three rules follow and they are the whole
design:

  1. EVERY SIGNAL IS A MEASUREMENT, NEVER A DIAGNOSIS.
     `HIGH_CPU_SUSTAINED` means "this averaged 407% of one core over its
     128640-second life". It does not mean "this is stuck". The daemon may
     report the first; only an agent that can be asked to justify itself may
     claim the second. §12 draws that line.

  2. A COMBINATION UPGRADES A CLASSIFICATION; IT NEVER DELETES A SIGNAL.
     The record that comes out of here carries every signal that went in.
     A consumer that sees `STALE_AGENT_RUNTIME_HIGH_CONFIDENCE` can still see
     which three facts produced it and disagree with the conclusion without
     having to re-derive them.

  3. NO SINGLE CODE CARRIES `implies_stale: true`. The contract asserts this
     for all fourteen, and a test asserts the contract. If a code could
     justify staleness alone, rule 1 would be decoration.

What this module does NOT do
----------------------------
It does not act. It produces signals, a classification, and a recommendation
with the blast radius attached. §18 forbids the daemon from removing, pruning
or stopping anything, and a `protected` subject is refused a restart
recommendation here rather than at the call site — because a guard at the call
site is a guard that the next call site does not have.

The UNKNOWN discipline
----------------------
A task whose state is UNKNOWN is not idle. An unknown filesystem reading is
not an empty one. This is the same rule `health.py` applies to a failed probe
and `attribution.py` applies to an unreadable process: the failure mode being
prevented is a reader treating "could not read it" as "nothing to see".
"""
from __future__ import annotations

import importlib.util
import sys
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


attribution = _load("serveros_runtime_attribution", "attribution.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN

CONTRACT = _HERE / "attribution-contract.yaml"

DEFAULT_THRESHOLDS = {
    "cpu_percent_high": 100.0,
    "cpu_sustained_seconds": 3600,
    "memory_rss_high_bytes": 1073741824,
    "age_long_seconds": 86400,
    "filesystem_percent_high": 85.0,
    "inode_percent_high": 85.0,
    "swap_percent_high": 90.0,
    "load1_per_cpu_high": 1.0,
    "error_repeats_high": 10,
    "error_window_seconds": 3600,
    "idle_grace_seconds": 1800,
    # -- §18 resource-observation thresholds ---------------------------------
    # A SECOND disk tier. `filesystem_percent_high` above is the warning tier
    # and already existed; this is the one that says "do not create anything
    # else here". One number cannot say both, and §31's four-valued verdict
    # cannot be produced from a single threshold.
    "filesystem_percent_critical": 92.0,
    "memory_percent_high": 90.0,
    "docker_storage_percent_high": 85.0,
    "docker_build_cache_bytes_high": 21474836480,   # 20 GiB
    "container_writable_layer_bytes_high": 1073741824,   # 1 GiB
    "container_log_bytes_high": 1073741824,   # 1 GiB
    # -- alert hysteresis (§9) ------------------------------------------------
    # How far back a measurement has to come before an alert about it closes.
    # Transcribed from the contract like every other value here, and asserted
    # equal to it, so the contract can be read without opening this module. The
    # alert layer reads them from `load_thresholds()`; none of them is used by
    # any branch below, because detection asks "is it over the line" and only
    # the alert layer asks "has it come back far enough".
    "filesystem_percent_recovery_margin": 3.0,
    "inode_percent_recovery_margin": 3.0,
    "memory_percent_recovery_margin": 5.0,
    "swap_percent_recovery_margin": 5.0,
    "docker_storage_percent_recovery_margin": 3.0,
    # The process layer's own margins, and the three byte-valued aggregates
    # whose margins were derivable but undeclared — see the contract for why
    # each number is what it is. `memory_rss_recovery_margin` is the one that
    # needed `margin_key` to learn the `_high_bytes` spelling; without it the
    # alert about a group holding 1 GiB resident could never close.
    "cpu_percent_recovery_margin": 10.0,
    "memory_rss_recovery_margin": 268435456,
    "container_log_bytes_recovery_margin": 268435456,
    "container_writable_layer_bytes_recovery_margin": 268435456,
    "docker_build_cache_bytes_recovery_margin": 4294967296,
    "error_repeats_recovery_margin": 2,
}

# Codes that describe a thing being BROKEN rather than unused. The distinction
# is load-bearing: a process that is up and cannot answer should be restarted,
# and must never be reclaimed — those are different actions with different
# consequences, and conflating them is how a working service gets deleted.
BROKEN_CODES = frozenset({
    "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE",
    "FUNCTIONAL_PROBE_FAILED",
})

# Codes that describe the DECLARATION being wrong rather than the resource
# being wrong. A third outcome beside "stale" and "broken", because the correct
# response is neither: restarting a session-owned tunnel re-creates the same
# defect with a fresh clock, and starting an undeclared tunnel does nothing
# about what made it undeclared. The resource is fine. What is wrong is the
# record of it.
DECLARATION_CODES = frozenset({
    "SESSION_OWNED_DURABLE_RESOURCE",
    "PERSISTENT_RUNTIME_FLOATING_DEPENDENCY",
    "UNDECLARED_DURABLE_RESOURCE",
})

# Severity per code. Transcribed from the contract, and a test asserts the two
# agree — a severity that lives only in code is a severity nobody reviews.
SEVERITY = {
    "HIGH_CPU_SUSTAINED": "medium",
    "HIGH_MEMORY_SUSTAINED": "medium",
    "ROOT_FS_PRESSURE": "high",
    "DATA_FS_PRESSURE": "high",
    "TASK_IDLE_PROCESS_ACTIVE": "high",
    "TASK_CLOSED_RESOURCE_RUNNING": "high",
    "ORPHAN_PROCESS_TREE": "high",
    # §5. Medium, and deliberately not high: the two halves are each true of
    # things that are fine, so what this ranks is "a person should look at the
    # lifecycle question", not "something is broken". It is also NOT a staleness
    # signal and not a cleanup authorisation — `implies_stale: false` is asserted
    # of it like every other code.
    "RUNTIME_OUTLIVED_OWNER": "medium",
    "UNKNOWN_OWNER_LONG_RUNNING": "medium",
    "REPEATED_ERROR_LOOP": "medium",
    "FUNCTIONAL_PROBE_FAILED": "high",
    "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE": "high",
    "STALE_CONTAINER": "medium",
    "STOPPED_RECLAIM_CANDIDATE": "low",
    "AGE_WITHOUT_ACTIVITY": "low",
    "PERSISTENT_RUNTIME_FLOATING_DEPENDENCY": "medium",
    "SESSION_OWNED_DURABLE_RESOURCE": "high",
    "UNDECLARED_DURABLE_RESOURCE": "medium",
    # §15. Medium, not high: the desired state on the other side of this
    # comparison is a DECLARATION, so a mismatch says the declaration and the
    # world disagree and does not say which one is wrong.
    "DESIRED_ACTUAL_MISMATCH": "medium",
    # §17. High, and the only code here whose subject is a permission rather
    # than a resource: a protection that lapses through a change of identity
    # produces no error and no anomaly of its own.
    "PROTECTION_LOST_ON_IDENTITY_SWITCH": "high",
}

# ---------------------------------------------------------------------------
# Owner references that denote a SESSION lifetime. ARCHITECTURE.md invariant 6:
# an Agent session may cause creation, it may not be the owner of record for
# anything that must outlive it. Case A — a tunnel a task depended on carried
# `session:*` as its owner and `persistent` as its lifecycle, was retired on
# time when an unrelated session ended, and reported healthy for the whole
# period that mattered. Nothing downstream could catch it; nothing is wrong
# until the exact instant it is wrong.
#
# Matched by prefix because a session id carries the session. The list is short
# on purpose: an owner kind that is not a session must not land here, or the
# check starts refusing legitimate registrations and gets switched off.
# ---------------------------------------------------------------------------
SESSION_OWNER_PREFIXES = ("session:", "agent-runtime:", "agent-session:")

# Lifecycle classes that outlive a session. `session-scoped` and `disposable`
# are absent because they are the two a session may legitimately own.
DURABLE_LIFECYCLE_CLASSES = ("persistent", "task-scoped")

# ---------------------------------------------------------------------------
# Floating dependency markers. These are TEXT patterns, and text patterns are
# a weak instrument — `@latest` inside a comment would match. It is still the
# right instrument here: the alternative is a resolver that decides what a
# command actually resolves to, which is a network call and a cache read, and
# this detector is pure and runs in the daemon's tick.
#
# Case C: one node reached Playwright through `npx @playwright/mcp@latest`,
# the other through an absolute path to a versioned artifact. Both worked. The
# two ran "the same" toolchain and did not, and nothing in the catalogue could
# have said so.
# ---------------------------------------------------------------------------
FLOATING_DEPENDENCY_PATTERNS = (
    ("npx-npm-latest", "npx "),
    ("floating-branch", "@main"),
    ("floating-branch", "@master"),
    ("floating-branch", "@HEAD"),
    ("install-on-start", "npm install "),
    ("install-on-start", "pip install "),
    ("install-on-start", "docker pull "),
)


# Resource kinds that ARE an MCP server. The contract declares `mcp-server`;
# `mcp` is accepted because it is the shorter name a caller naturally writes,
# and a rule that only matches one spelling of a thing is a rule that silently
# stops applying the first time somebody registers it under the other. The
# check below used to compare against the literal "mcp" alone, which no kind in
# the contract's own list can ever equal — so the branch was unreachable and
# reported nothing forever, which reads exactly like a rule that found nothing.
MCP_KINDS = ("mcp", "mcp-server")


# Origin-session states that mean "this login is on its way out", as
# `loginctl show-session -p State` reports them. A tuple rather than an equality
# for the reason `resource_anomaly.TUNNEL_BAD_STATES` is one: matching a single
# spelling is how a rule stops applying the first time the source grows a
# synonym. `closing` is the value `attribution.session_facts` names as
# load-bearing — "the login is on its way out while its scope's processes are
# still alive" — and it is the only one this host has been observed to produce.
TERMINATING_SESSION_STATES = ("closing",)


def load_thresholds(path=None) -> dict:
    """Thresholds from the contract, over the built-in defaults.

    The defaults are the contract's values transcribed, which is exactly the
    kind of duplicate that goes stale. A test asserts they still agree, so the
    duplication fails loudly instead of silently diverging.
    """
    thresholds = dict(DEFAULT_THRESHOLDS)
    try:
        import yaml

        document = yaml.safe_load(Path(path or CONTRACT).read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - unreadable contract keeps defaults
        return thresholds
    thresholds.update(document.get("thresholds") or {})
    return thresholds


def margin_key(threshold_key: str | None) -> str | None:
    """The policy key that says how far a measurement must come back to close.

    Derived from the threshold key rather than tabulated per code, because two
    tiers of one measurement — `filesystem_percent_high` and
    `filesystem_percent_critical` — are the same quantity and come back down
    the same distance; a second table mapping codes to margins would be a
    second place for the two tiers to drift apart. A threshold key with none of
    the suffixes below has no margin, and that is the answer for every code
    whose subject is a declaration rather than a measurement.

    `_high_bytes` is the third suffix because the byte-valued thresholds are
    spelled from the largest unit down (`memory_rss_high_bytes`) while the
    percent-valued ones are spelled up (`cpu_percent_high`) — the same English
    phrase, two orders. Without it, `memory_rss_high_bytes` would derive no
    margin, the alert about it could never close, and the failure would look
    like an alert nobody had got round to dealing with.

    Lives here rather than in `resource_anomaly` because the runtime detector
    grades codes too, and `resource_anomaly` already loads this module: putting
    it the other way round would make the two load each other.
    """
    if not threshold_key:
        return None
    for suffix in ("_high", "_critical"):
        if threshold_key.endswith(suffix):
            return threshold_key[: -len(suffix)] + "_recovery_margin"
    if threshold_key.endswith("_high_bytes"):
        return threshold_key[: -len("_high_bytes")] + "_recovery_margin"
    return None


def signal(code: str, subject: str, measured: dict, *, window: str,
           why: str) -> dict:
    """One anomaly, as a measurement with the window it was measured over.

    `measured` always carries the threshold alongside the value. A signal that
    reports `cpu_percent: 407` without reporting what it was compared against
    cannot be reviewed, only believed.

    `implies_stale` is present and always False. It is here so that a consumer
    that tries to use one signal to justify a reclaim finds an explicit `false`
    rather than a missing key it can read as permission.
    """
    return {
        "code": code,
        "severity": SEVERITY.get(code, "unknown"),
        "subject": subject,
        "measured": measured,
        "window": window,
        "why": why,
        "implies_stale": False,
        "evidence_kind": "measurement",
    }


# ---------------------------------------------------------------------------
# Detection. Pure: facts in, signals out.
# ---------------------------------------------------------------------------


def detect(*, groups=None, containers=None, task_states=None, host_facts=None,
           probe_results=None, error_windows=None, resources=None,
           coverage_update=None, thresholds=None, criticality=None,
           session_states=None) -> list[dict]:
    """Every anomaly the supplied facts support. Nothing is inferred here.

    Each block below is skipped when its input is absent, and skipped inputs
    are reported by `unchecked()` rather than passed over silently — a detector
    that cannot tell "nothing found" from "nothing looked at" is the same
    pathology one layer up.

    `task_states` is the one that matters most on this host: it is
    `{}` because no source of record joins a task to a process, so
    TASK_IDLE_PROCESS_ACTIVE cannot fire, and the codex case of 2026-09-13
    comes out as STALE_CANDIDATE rather than the higher-confidence
    classification. That is the honest answer and it is the point.
    """
    thresholds = thresholds or load_thresholds()
    criticality = criticality or {}
    signals: list[dict] = []

    # -- runtime groups ------------------------------------------------------
    for group in groups or []:
        ref = group["ref"]
        agg = group.get("aggregate") or {}
        cpu = agg.get("cpu_percent") or 0.0
        age = agg.get("age_seconds")
        memory = agg.get("memory_rss_bytes") or 0
        # §5/§14. The group's largest term, carried on the signal itself so
        # that a notification about a sum can name what is in the sum. It is
        # the group document's own `contributors[0]` rather than a second
        # computation: the detector does not have the process table and must
        # not grow one, and two rankings of the same facts is how the alert
        # and the process view start disagreeing about which process is busy.
        top = (group.get("contributors") or [None])[0]

        if cpu >= thresholds["cpu_percent_high"] and (age or 0) >= thresholds["cpu_sustained_seconds"]:
            signals.append(signal(
                "HIGH_CPU_SUSTAINED", ref,
                {"cpu_percent": cpu, "threshold_percent": thresholds["cpu_percent_high"],
                 "process_count": agg.get("process_count"),
                 "top_contributor": top},
                window=f"lifetime average over {age:.0f}s",
                why="the group's summed lifetime CPU exceeds the threshold over a life long enough for 'sustained' to mean something",
            ))

        if memory >= thresholds["memory_rss_high_bytes"]:
            signals.append(signal(
                "HIGH_MEMORY_SUSTAINED", ref,
                {"memory_rss_bytes": memory, "threshold_bytes": thresholds["memory_rss_high_bytes"],
                 "top_contributor": top},
                window=f"resolved over {age:.0f}s" if age else "current",
                why="resident memory exceeds the declared threshold",
            ))

        if (age or 0) >= thresholds["age_long_seconds"]:
            signals.append(signal(
                "AGE_WITHOUT_ACTIVITY", ref,
                {"age_seconds": age, "threshold_seconds": thresholds["age_long_seconds"],
                 "cpu_percent": cpu},
                window=f"{age:.0f}s",
                why="the group is older than the declared long-lived threshold",
            ))

        if (group.get("tree") or {}).get("reparented_to_init"):
            session_state = None
            if group.get("session_ref"):
                session_state = (task_states or {}).get(group["session_ref"], {}).get("state")
            signals.append(signal(
                "ORPHAN_PROCESS_TREE", ref,
                {"root_ppid": (group.get("tree") or {}).get("root_ppid"),
                 "chain_complete": (group.get("tree") or {}).get("chain_complete"),
                 "origin_session_state": session_state},
                window="lineage at observation time",
                why=("the group's root is parented to init. This is a fact about "
                     "lineage and NOT a claim that the work is abandoned — a "
                     "daemonised service looks identical, which is why the "
                     "combination needs a second signal."),
            ))

        if not group.get("agent", {}).get("value") and (age or 0) >= thresholds["age_long_seconds"]:
            signals.append(signal(
                "UNKNOWN_OWNER_LONG_RUNNING", ref,
                {"age_seconds": age, "threshold_seconds": thresholds["age_long_seconds"]},
                window=f"{age:.0f}s",
                why="no agent rule identifies this group and it has been running longer than the long-lived threshold",
            ))

        # -- §5. The origin session is leaving and the work is not. ---------
        #
        # Both halves are separately true of things that are FINE, which is why
        # this is a combination and not either test alone: a login mid-logout
        # has nothing left running, and a long build has an owner who is still
        # logged in. Only the pair says "the thing that started this has gone
        # away and this is still spending the machine".
        #
        # The subject is the GROUP, not a runtime family. There is no branch on
        # a program name here and there must not be one: a transport can be on
        # its way out while what it started keeps running for any runtime, and
        # a code named after the process where it was first noticed would be a
        # Codex-specific detector wearing a general name.
        session = (session_states or {}).get(group.get("session_ref") or "")
        state = (session or {}).get("state")
        if state in TERMINATING_SESSION_STATES and (
                cpu >= thresholds["cpu_percent_high"]
                or memory >= thresholds["memory_rss_high_bytes"]):
            signals.append(signal(
                "RUNTIME_OUTLIVED_OWNER", ref,
                {"origin_session_state": state,
                 "origin_session": group.get("session_ref"),
                 "cpu_percent": cpu,
                 "threshold_percent": thresholds["cpu_percent_high"],
                 "memory_rss_bytes": memory,
                 "threshold_bytes": thresholds["memory_rss_high_bytes"],
                 "top_contributor": top},
                window=f"lifetime average over {age:.0f}s" if age else "current",
                why=("the login that owns this group is terminating and the "
                     "group is still consuming. Neither fact alone justifies "
                     "anything: what this reports is a lifecycle mismatch, not "
                     "a fault and not a cleanup authorisation"),
            ))

        # -- task state. The honest branch. --------------------------------
        task = (task_states or {}).get(ref)
        if task is None:
            pass  # nothing to check; reported by unchecked()
        elif task.get("state") == "idle" and cpu >= thresholds["cpu_percent_high"]:
            signals.append(signal(
                "TASK_IDLE_PROCESS_ACTIVE", ref,
                {"task_state": "idle", "idle_since": task.get("since"),
                 "cpu_percent": cpu, "threshold_percent": thresholds["cpu_percent_high"]},
                window=f"task state since {task.get('since')}",
                why="the task reports idle while its runtime keeps consuming",
            ))
        elif task.get("state") == "closed":
            signals.append(signal(
                "TASK_CLOSED_RESOURCE_RUNNING", ref,
                {"task_state": "closed", "closed_at": task.get("since")},
                window="task state at observation time",
                why="the task that owned this runtime is closed and the runtime is still present",
            ))

    # -- containers ----------------------------------------------------------
    for container in containers or []:
        ref = container.get("ref") or container.get("container")
        if container.get("disposition") == "PROTECTED":
            # A protected container is never a candidate for anything, so no
            # staleness signal is emitted for it at all. Emitting one and
            # filtering it later would put the guard in the wrong place.
            continue
        if container.get("unknown_owner"):
            state = container.get("state")
            if state in ("exited", "dead"):
                signals.append(signal(
                    "STOPPED_RECLAIM_CANDIDATE", ref,
                    {"state": state, "exit_code": (container.get("evidence") or {}).get("exit_code"),
                     "unknown_owner": True},
                    window="state at observation time",
                    why="stopped, and its owner is not established — a cleanup PROPOSAL, never an action",
                ))
            continue
        if container.get("state") == "exited":
            signals.append(signal(
                "STALE_CONTAINER", ref,
                {"state": "exited", "finished_at": (container.get("evidence") or {}).get("finished_at")},
                window="state at observation time",
                why="the container is attributed and has exited",
            ))

    # -- host filesystems ----------------------------------------------------
    for fact in (host_facts or {}).get("filesystems") or []:
        used = fact.get("used_percent")
        if used is None:
            continue  # unread is not empty
        code = "ROOT_FS_PRESSURE" if fact.get("mount") == "/" else "DATA_FS_PRESSURE"
        if used >= thresholds["filesystem_percent_high"]:
            signals.append(signal(
                code, f"filesystem:{fact.get('mount')}",
                {"used_percent": used, "threshold_percent": thresholds["filesystem_percent_high"],
                 "available_bytes": fact.get("available_bytes")},
                window="point in time",
                why="filesystem usage exceeds the declared threshold",
            ))

    # -- probes: liveness is not function ------------------------------------
    for ref, axes in (probe_results or {}).items():
        functional = (axes or {}).get("functional")
        liveness = (axes or {}).get("liveness")
        if functional == "FAILED":
            signals.append(signal(
                "FUNCTIONAL_PROBE_FAILED", ref,
                {"functional": "FAILED", "liveness": liveness,
                 "criticality": criticality.get(ref, "normal")},
                window="most recent probe",
                why=("the resource answers the liveness question and fails the "
                     "functional one — the shape that reads as healthy in every "
                     "process-, port- and container-based check"),
            ))
        if axes and axes.get("kind") in MCP_KINDS and functional == "FAILED":
            signals.append(signal(
                "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE", ref,
                {"handshake": "FAILED", "process_state": liveness},
                window="most recent probe",
                why="the MCP server process exists and does not complete a handshake",
            ))

    # -- resource declarations (§40) -----------------------------------------
    # Three findings that are about the DECLARATION rather than about the
    # resource. Every one of them describes a resource that is working, which
    # is why none of them implies stale and why none of them is a health axis.
    for resource in resources or []:
        ref = resource.get("id") or "?"
        owner = (resource.get("owner") or {}).get("ref") or ""
        lifecycle = (resource.get("lifecycle") or {}).get("class") or ""

        # Case A. The resource is healthy; the ownership is what is wrong.
        if (owner.startswith(SESSION_OWNER_PREFIXES)
                and lifecycle in DURABLE_LIFECYCLE_CLASSES):
            signals.append(signal(
                "SESSION_OWNED_DURABLE_RESOURCE", ref,
                {"owner": owner, "lifecycle": lifecycle,
                 "declared": lifecycle, "permits": ["session-scoped", "disposable"]},
                window="current registration",
                why=("the declared lifecycle outlives every owner that can be "
                     "confirmed for it. The resource is not misconfigured — it "
                     "is exactly what its class says, and every observation "
                     "before its retirement will report it healthy."),
            ))

        # Case C. It works, and it cannot be said to be the same thing tomorrow.
        text = " ".join(str(resource.get(k) or "") for k in
                        ("command", "observe", "materialization", "note"))
        if text.strip():
            hits = sorted({name for name, needle in FLOATING_DEPENDENCY_PATTERNS
                           if needle in text})
            # A pinned absolute path is the repair, and a resource that names
            # one is not floating even if the surrounding note mentions npx.
            pinned = ("/" in text or "\\" in text) and "@latest" not in text
            if hits and not pinned:
                signals.append(signal(
                    "PERSISTENT_RUNTIME_FLOATING_DEPENDENCY", ref,
                    {"patterns": hits, "lifecycle": lifecycle,
                     "criticality": criticality.get(ref, "normal"),
                     "protection": resource.get("protection", {}).get("level", "normal")},
                    window="current registration",
                    why=("the resource resolves through something that moves. "
                         "'It worked yesterday' and 'it works today' are then "
                         "statements about two different programs."),
                ))

        # Case B. Required by something durable, declared by nothing.
        if resource.get("required_by") and not resource.get("declared_by"):
            signals.append(signal(
                "UNDECLARED_DURABLE_RESOURCE", ref,
                {"required_by": resource.get("required_by"),
                 "declared_by": None},
                window="current registration",
                why=("a durable artifact names this as required and no "
                     "reconcilable store declares it. There is nothing to "
                     "compare against, so nothing that can ever be found "
                     "wrong."),
            ))

    # -- error loops ---------------------------------------------------------
    for ref, window in (error_windows or {}).items():
        count = window.get("count")
        if count is None:
            continue
        if count >= thresholds["error_repeats_high"]:
            signals.append(signal(
                "REPEATED_ERROR_LOOP", ref,
                {"repeats": count, "threshold_repeats": thresholds["error_repeats_high"],
                 "window_seconds": window.get("window_seconds"),
                 "still_occurring": window.get("still_occurring"),
                 "sample": window.get("sample_kind")},
                window=f"{window.get('window_seconds')}s",
                why="the same failure repeats more often than the declared threshold within the window",
            ))

    return signals


def unchecked(*, groups=None, containers=None, task_states=None, host_facts=None,
              probe_results=None, error_windows=None, resources=None,
              session_states=None) -> list[dict]:
    """Which inputs the detector could NOT evaluate, and why.

    This exists so that an empty signal list can be read correctly. On this
    host `task_states` is empty because no source of record joins a task to a
    process — so TASK_IDLE_PROCESS_ACTIVE is structurally unreachable, and a
    report that said only "no anomalies" would be hiding the single largest
    gap in the answer.
    """
    out: list[dict] = []
    if not task_states:
        out.append({
            "input": "task_states",
            "state": UNKNOWN,
            "why": ("no source of record maps a task to a process on this host, "
                    "so TASK_IDLE_PROCESS_ACTIVE and TASK_CLOSED_RESOURCE_RUNNING "
                    "cannot fire. Their absence is NOT evidence that no task is "
                    "idle."),
        })
    if not groups:
        out.append({"input": "groups", "state": UNKNOWN, "why": "no runtime groups were supplied"})
    if session_states is None:
        # `None` rather than a falsy test, and the difference is a real reading.
        # `graph.session_states()` derives the document from the groups and
        # returns `{}` when no group has a session scope in its cgroup — that
        # is a caller that LOOKED and found nothing to look at. A truthiness
        # test here would report every session-free host as an unmeasured one,
        # which is the declared-but-absent mistake with the sign flipped: a
        # measurement reported as a gap.
        out.append({
            "input": "session_states",
            "state": UNKNOWN,
            "why": ("no origin-session states were supplied, so "
                    "RUNTIME_OUTLIVED_OWNER could not be evaluated. Its absence "
                    "is NOT evidence that every group's owner is still there."),
        })
    if not containers:
        out.append({"input": "containers", "state": UNKNOWN, "why": "no containers were supplied"})
    if not host_facts:
        out.append({"input": "host_facts", "state": UNKNOWN, "why": "no filesystem facts were supplied"})
    if not probe_results:
        out.append({"input": "probe_results", "state": UNKNOWN, "why": "no probe results were supplied"})
    if not error_windows:
        out.append({"input": "error_windows", "state": UNKNOWN, "why": "no error windows were supplied"})
    if not resources:
        out.append({
            "input": "resources",
            "state": UNKNOWN,
            "why": ("no registered resources were supplied, so neither "
                    "SESSION_OWNED_DURABLE_RESOURCE, PERSISTENT_RUNTIME_FLOATING_"
                    "DEPENDENCY nor UNDECLARED_DURABLE_RESOURCE could be "
                    "evaluated. Their absence is NOT evidence that every "
                    "registration is well-formed."),
        })
    return out


# Which code each input gates. Declared here, beside `unchecked()`, because
# this module owns both vocabularies — the input names and the code names — and
# a mapping between two vocabularies belongs where they meet.
#
# It exists for one reader: the alert state machine. An alert that stops being
# reported can close for two entirely different reasons — the condition ended,
# or nobody looked. The signal list cannot tell them apart (both are an absence)
# and `unchecked()` speaks in INPUTS, so something has to say "because
# `containers` was missing, these two codes were not evaluated". Without this
# table the runtime detector's alerts would all close on the first tick whose
# process table, Docker socket or registry failed to answer, and the alert list
# would report a broken node as a recovered one.
INPUT_CODES = {
    "groups": ("HIGH_CPU_SUSTAINED", "HIGH_MEMORY_SUSTAINED",
               "AGE_WITHOUT_ACTIVITY", "ORPHAN_PROCESS_TREE",
               "UNKNOWN_OWNER_LONG_RUNNING"),
    "session_states": ("RUNTIME_OUTLIVED_OWNER",),
    "task_states": ("TASK_IDLE_PROCESS_ACTIVE", "TASK_CLOSED_RESOURCE_RUNNING"),
    "containers": ("STALE_CONTAINER", "STOPPED_RECLAIM_CANDIDATE"),
    "probe_results": ("FUNCTIONAL_PROBE_FAILED", "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE"),
    "host_facts": ("ROOT_FS_PRESSURE", "DATA_FS_PRESSURE"),
    "error_windows": ("REPEATED_ERROR_LOOP",),
    "resources": ("SESSION_OWNED_DURABLE_RESOURCE",
                  "PERSISTENT_RUNTIME_FLOATING_DEPENDENCY",
                  "UNDECLARED_DURABLE_RESOURCE"),
}

# Codes in `SEVERITY` that no input in this module gates, because this module
# never emits them. `DESIRED_ACTUAL_MISMATCH` and `PROTECTION_LOST_ON_IDENTITY_
# SWITCH` are declared with a severity here so that one table answers "how bad
# is this code" for every code in the product; the detectors that emit them are
# `desired.py` and `protection.py`, and `resource_anomaly` re-labels the first
# as one of its own. A test asserts these two sets and `SEVERITY` cover each
# other, so a new code cannot be added here without answering which input
# decides whether it was evaluated.
NOT_GATED_BY_AN_INPUT = {
    "DESIRED_ACTUAL_MISMATCH": (
        "emitted by `desired.py` and carried by `resource_anomaly`, which "
        "reports its own coverage for it"),
    "PROTECTION_LOST_ON_IDENTITY_SWITCH": (
        "emitted by `protection.py`, whose input is the container list rather "
        "than any of this module's"),
}


def unevaluated(rows: list[dict]) -> list[dict]:
    """`codes_not_evaluated`, derived from what `unchecked()` reported.

    The list a caller assembles for the alert layer, in the shape
    `resource_anomaly` already emits: one row per CODE, each carrying the input
    that was missing. Codes whose input WAS supplied are absent rather than
    present-and-clean, which is the distinction the alert layer reads.
    """
    out = []
    for row in rows or []:
        why = row.get("why")
        for code in INPUT_CODES.get(str(row.get("input")), ()):
            out.append({"code": code, "input": row.get("input"),
                        "state": row.get("state"), "why": why})
    out.sort(key=lambda item: item["code"])
    return out


def levels(*, groups=None, host_facts=None, error_windows=None) -> dict:
    """Every GRADED measurement, reported whether or not it fired.

    A signal list cannot answer "has this come back down far enough to close
    the alert about it", because a signal is only emitted ABOVE its threshold:
    at 84.9% the list is empty, and empty is also what an unmeasurable host
    produces. The alert layer's hysteresis compares against these rows instead,
    which is the same discipline `codes_not_evaluated` applies one level up.

    Only the codes whose subject is a CONTINUOUS quantity appear here. The
    declaration-shaped codes — ORPHAN_PROCESS_TREE, RUNTIME_OUTLIVED_OWNER,
    the durable-resource family — have no distance to come back and are absent
    on purpose: an alert about one of those closes when it stops being
    reported, and giving it a level row would invent a measurement nobody
    takes.
    """
    out: dict = {}
    thresholds = load_thresholds()

    for group in groups or []:
        ref = group["ref"]
        agg = group.get("aggregate") or {}
        rows = (
            ("HIGH_CPU_SUSTAINED", agg.get("cpu_percent"),
             thresholds["cpu_percent_high"]),
            ("HIGH_MEMORY_SUSTAINED", agg.get("memory_rss_bytes"),
             thresholds["memory_rss_high_bytes"]),
        )
        for code, value, threshold in rows:
            if value is None:
                continue
            out[f"{code}|{ref}"] = _level_row(code, ref, value, threshold)

    for fact in (host_facts or {}).get("filesystems") or []:
        used = fact.get("used_percent")
        if used is None:
            continue  # unread is not empty
        mount = fact.get("mount")
        code = "ROOT_FS_PRESSURE" if mount == "/" else "DATA_FS_PRESSURE"
        out[f"{code}|filesystem:{mount}"] = _level_row(
            code, f"filesystem:{mount}", used, thresholds["filesystem_percent_high"])

    for ref, window in (error_windows or {}).items():
        count = window.get("count")
        if count is None:
            continue
        out[f"REPEATED_ERROR_LOOP|{ref}"] = _level_row(
            "REPEATED_ERROR_LOOP", ref, count, thresholds["error_repeats_high"])

    return out


def _level_row(code: str, subject: str, value, threshold) -> dict:
    """One graded measurement. `margin_key` derived, never tabulated."""
    return {"code": code, "subject": subject, "value": value,
            "threshold": threshold,
            "margin_key": margin_key(THRESHOLD_KEYS.get(code))}


# The threshold key per graded code, so `margin_key()` derives the recovery
# margin from the same key the detector compares against. A code whose key has
# no `_high`/`_critical`/`_high_bytes` suffix derives no margin, and the alert
# layer holds rather than closes — which is why `TO_GRADED` and this table are
# asserted to agree.
THRESHOLD_KEYS = {
    "HIGH_CPU_SUSTAINED": "cpu_percent_high",
    "HIGH_MEMORY_SUSTAINED": "memory_rss_high_bytes",
    "ROOT_FS_PRESSURE": "filesystem_percent_high",
    "DATA_FS_PRESSURE": "filesystem_percent_high",
    "REPEATED_ERROR_LOOP": "error_repeats_high",
}


# ---------------------------------------------------------------------------
# Classification (§11)
# ---------------------------------------------------------------------------

# Transcribed from the contract's `combinations`. A test asserts the two agree.
COMBINATIONS = (
    ("STALE_AGENT_RUNTIME_HIGH_CONFIDENCE",
     ("TASK_IDLE_PROCESS_ACTIVE", "HIGH_CPU_SUSTAINED", "REPEATED_ERROR_LOOP")),
    ("STALE_AGENT_RUNTIME_HIGH_CONFIDENCE",
     ("ORPHAN_PROCESS_TREE", "TASK_CLOSED_RESOURCE_RUNNING")),
    ("STALE_CANDIDATE",
     ("TASK_IDLE_PROCESS_ACTIVE", "HIGH_CPU_SUSTAINED")),
    # Lineage standing in for task state. See the contract for why this exists
    # and why it is a candidate rather than a confident call.
    ("STALE_CANDIDATE",
     ("ORPHAN_PROCESS_TREE", "HIGH_CPU_SUSTAINED", "REPEATED_ERROR_LOOP")),
    ("BROKEN_NOT_STALE",
     ("MCP_PROCESS_ALIVE_BUT_UNAVAILABLE",)),
    ("INFRASTRUCTURE_AT_RISK",
     ("FUNCTIONAL_PROBE_FAILED",)),
)


def classify(signals: list[dict], *, subject: str, criticality: str = "normal",
             protection: str = "normal") -> dict:
    """Turn one subject's signals into a classification.

    Returns every input signal, unchanged, alongside the conclusion. §11: a
    combination may upgrade the classification and may never delete the
    underlying signals — so the caller can always check the reasoning.

    A subject with no signals classifies as HEALTHY_OBSERVED rather than
    HEALTHY: what was observed is that no anomaly fired, which is not the same
    as a positive statement that the thing works. Only the functional axis can
    say that, and it is not consulted here.
    """
    codes = [s["code"] for s in signals]

    classification = None
    because = None
    for name, required in COMBINATIONS:
        if all(code in codes for code in required):
            if name == "INFRASTRUCTURE_AT_RISK" and criticality != "critical":
                continue
            classification = name
            because = (
                f"combination {name}: all of {list(required)} present "
                f"({len(required)} independent signal(s)); no single one of them "
                f"is sufficient alone"
            )
            break

    if classification is None:
        if not signals:
            classification = "HEALTHY_OBSERVED"
            because = "no anomaly fired against this subject"
        elif all(c in BROKEN_CODES for c in codes):
            # Broken, not idle. A distinct outcome because the right response
            # is different: restart, do not reclaim.
            classification = "BROKEN_NOT_STALE"
            because = "the only signals present describe a failure to function, not disuse"
        elif all(c in DECLARATION_CODES for c in codes):
            # The declaration is stale, not the resource. Checked AFTER the
            # combination table and after BROKEN, so a resource that is both
            # broken and badly declared is classified by its breakage and
            # carries the declaration findings alongside it.
            classification = "DECLARATION_STALE"
            because = ("every signal present describes the record rather than "
                       "the resource: the thing is working and what is written "
                       "about it is wrong")
        else:
            classification = "SIGNALS_PRESENT"
            because = (
                "signals fired but no declared combination is satisfied; a single "
                "signal never classifies and this stays unclassified on purpose"
            )

    recommendation = _recommend(classification, signals, protection=protection,
                                criticality=criticality)

    return {
        "subject": subject,
        "classification": classification,
        "because": because,
        "criticality": criticality,
        "protection": protection,
        # Every signal, unchanged. Not a summary of them.
        "signals": signals,
        "signal_codes": codes,
        "recommendation": recommendation["value"],
        "recommendation_because": recommendation["because"],
        "blast_radius": recommendation["blast_radius"],
    }


def _recommend(classification: str, signals: list[dict], *, protection: str,
               criticality: str) -> dict:
    """What to do, and — always — what else would stop.

    The protected check lives HERE rather than at the call site. A guard the
    caller has to remember is a guard the next caller does not have, and the
    consequence of forgetting it is a protected resource being restarted by
    the automatic path, which is the one thing the registry exists to stop.
    """
    values = {
        "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE": "REQUEST_CLEANUP_APPROVAL",
        "STALE_CANDIDATE": "REQUEST_CLEANUP_APPROVAL",
        "BROKEN_NOT_STALE": "RESTART_RESOURCE",
        "DECLARATION_STALE": "REDECLARE_RESOURCE",
        "INFRASTRUCTURE_AT_RISK": "NEEDS_HUMAN",
        "SIGNALS_PRESENT": "NONE",
        "HEALTHY_OBSERVED": "NONE",
    }
    value = values.get(classification, "NONE")
    because = None

    if protection == "protected" and value in ("RESTART_RESOURCE", "REQUEST_CLEANUP_APPROVAL"):
        value = "NEEDS_HUMAN"
        because = (
            "the subject is protected: the automatic path does not reach it, so "
            "this is escalated to a human rather than recommended"
        )
    elif classification == "BROKEN_NOT_STALE":
        because = "a process that is up and cannot answer is broken, not unused; restarting may be right and reclaiming is not"
    elif classification == "DECLARATION_STALE":
        because = (
            "the resource is working and the record of it is wrong. Acting on "
            "the resource would be acting on the wrong object — restarting a "
            "session-owned resource re-creates the defect with a fresh clock, "
            "and starting an undeclared one changes nothing that made it "
            "undeclared"
        )

    affected = [s["subject"] for s in signals]
    blast = {
        "affected_subjects": sorted(set(affected)),
        "affected_signals": sorted({s["code"] for s in signals}),
        # Populated by the caller from the runtime graph; declared here so the
        # shape is fixed and a recommendation without one is visibly incomplete.
        "affected_processes": [],
        "protected_excluded": [],
        "note": (
            "a recommendation without a blast radius is not a recommendation. "
            "The operator cannot consent to a consequence nobody described."
        ),
    }
    if protection == "protected":
        blast["protected_excluded"] = [f"the subject itself is protected ({criticality} criticality)"]

    return {"value": value, "because": because, "blast_radius": blast}


def classify_all(signals: list[dict], *, criticality=None, protection=None) -> list[dict]:
    """Classify every subject that has at least one signal."""
    criticality = criticality or {}
    protection = protection or {}
    by_subject: dict[str, list[dict]] = {}
    for item in signals:
        by_subject.setdefault(item["subject"], []).append(item)

    return [
        classify(items, subject=subject,
                 criticality=criticality.get(subject, "normal"),
                 protection=protection.get(subject, "normal"))
        for subject, items in sorted(by_subject.items())
    ]
