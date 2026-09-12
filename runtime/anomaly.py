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
    "error_repeats_high": 10,
    "error_window_seconds": 3600,
    "idle_grace_seconds": 1800,
}

# Codes that describe a thing being BROKEN rather than unused. The distinction
# is load-bearing: a process that is up and cannot answer should be restarted,
# and must never be reclaimed — those are different actions with different
# consequences, and conflating them is how a working service gets deleted.
BROKEN_CODES = frozenset({
    "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE",
    "FUNCTIONAL_PROBE_FAILED",
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
    "UNKNOWN_OWNER_LONG_RUNNING": "medium",
    "REPEATED_ERROR_LOOP": "medium",
    "FUNCTIONAL_PROBE_FAILED": "high",
    "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE": "high",
    "STALE_CONTAINER": "medium",
    "STOPPED_RECLAIM_CANDIDATE": "low",
    "AGE_WITHOUT_ACTIVITY": "low",
}


# Resource kinds that ARE an MCP server. The contract declares `mcp-server`;
# `mcp` is accepted because it is the shorter name a caller naturally writes,
# and a rule that only matches one spelling of a thing is a rule that silently
# stops applying the first time somebody registers it under the other. The
# check below used to compare against the literal "mcp" alone, which no kind in
# the contract's own list can ever equal — so the branch was unreachable and
# reported nothing forever, which reads exactly like a rule that found nothing.
MCP_KINDS = ("mcp", "mcp-server")


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
           probe_results=None, error_windows=None, coverage_update=None,
           thresholds=None, criticality=None) -> list[dict]:
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

        if cpu >= thresholds["cpu_percent_high"] and (age or 0) >= thresholds["cpu_sustained_seconds"]:
            signals.append(signal(
                "HIGH_CPU_SUSTAINED", ref,
                {"cpu_percent": cpu, "threshold_percent": thresholds["cpu_percent_high"],
                 "process_count": agg.get("process_count")},
                window=f"lifetime average over {age:.0f}s",
                why="the group's summed lifetime CPU exceeds the threshold over a life long enough for 'sustained' to mean something",
            ))

        if memory >= thresholds["memory_rss_high_bytes"]:
            signals.append(signal(
                "HIGH_MEMORY_SUSTAINED", ref,
                {"memory_rss_bytes": memory, "threshold_bytes": thresholds["memory_rss_high_bytes"]},
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
              probe_results=None, error_windows=None) -> list[dict]:
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
    if not containers:
        out.append({"input": "containers", "state": UNKNOWN, "why": "no containers were supplied"})
    if not host_facts:
        out.append({"input": "host_facts", "state": UNKNOWN, "why": "no filesystem facts were supplied"})
    if not probe_results:
        out.append({"input": "probe_results", "state": UNKNOWN, "why": "no probe results were supplied"})
    if not error_windows:
        out.append({"input": "error_windows", "state": UNKNOWN, "why": "no error windows were supplied"})
    return out


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
