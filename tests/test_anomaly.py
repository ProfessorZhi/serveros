#!/usr/bin/env python3
"""Tests for anomaly detection and multi-signal classification (§10, §11).

The assertions that matter most are the NEGATIVE ones. It is easy to build a
detector that fires; the thing being tested here is a detector that declines
to, and says why:

  * high CPU alone must not classify anything — a build is high CPU;
  * a task state that is UNKNOWN must not be read as idle;
  * a protected subject must never come back with a restart or cleanup
    recommendation, whatever its signals say;
  * two subjects with partial signals must not combine into one conclusion.

The contract-agreement tests exist because severity and combination tables are
transcribed from `attribution-contract.yaml` into `anomaly.py`, and a
transcription is exactly the thing that goes stale. They fail loudly on
divergence instead of letting the code become a second, quieter authority.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

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


an = _load("serveros_runtime_anomaly", "anomaly.py")

CONTRACT = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())

T = an.DEFAULT_THRESHOLDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def group(ref, *, cpu=0.0, age=0.0, memory=0, agent="codex", reparented=False,
          session_ref="session:2382"):
    return {
        "ref": ref,
        "agent": {"value": agent, "status": "INFERRED" if agent else "UNKNOWN"},
        "session_ref": session_ref,
        "aggregate": {"cpu_percent": cpu, "age_seconds": age,
                      "memory_rss_bytes": memory, "process_count": 3},
        "tree": {"reparented_to_init": reparented, "root_ppid": 1 if reparented else 999,
                 "chain_complete": False},
    }


def container(ref, *, state="running", disposition="NORMAL", unknown=False):
    return {"ref": ref, "container": ref, "state": state, "disposition": disposition,
            "unknown_owner": unknown, "evidence": {"exit_code": 0}}


# ---------------------------------------------------------------------------
# The contract is the authority
# ---------------------------------------------------------------------------


def test_no_single_code_claims_to_imply_staleness():
    """§11 in one assertion. If any code here could justify staleness alone,
    every other guard in this module would be decoration."""
    for code in CONTRACT["anomalies"]:
        assert code["implies_stale"] is False, code["code"]


def test_the_severity_table_matches_the_contract():
    declared = {a["code"]: a["severity"] for a in CONTRACT["anomalies"]}
    assert declared == an.SEVERITY


def test_the_combination_table_matches_the_contract():
    declared = {
        (c["classification"], tuple(c["requires_all"]))
        for c in CONTRACT["combinations"]
    }
    assert declared == set(an.COMBINATIONS)


def test_every_code_the_detector_can_emit_is_declared():
    declared = {a["code"] for a in CONTRACT["anomalies"]}
    assert set(an.SEVERITY) == declared


def test_the_thresholds_the_code_ships_match_the_contract():
    assert CONTRACT["thresholds"] == an.DEFAULT_THRESHOLDS
    assert an.load_thresholds() == an.DEFAULT_THRESHOLDS


def test_an_unreadable_contract_keeps_the_built_in_thresholds(tmp_path):
    assert an.load_thresholds(tmp_path / "absent.yaml") == an.DEFAULT_THRESHOLDS


# ---------------------------------------------------------------------------
# §11 · a single signal never classifies
# ---------------------------------------------------------------------------


def test_high_cpu_alone_does_not_classify_anything():
    """A build is high CPU. A test run is high CPU. This is the assertion that
    stops the machine killing its own work."""
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000)])
    assert "HIGH_CPU_SUSTAINED" in [s["code"] for s in signals]

    result = an.classify(signals, subject="g:1")
    assert result["classification"] == "SIGNALS_PRESENT"
    assert result["recommendation"] == "NONE"


def test_high_cpu_without_age_does_not_even_fire():
    """"Sustained" is the claim, and a process that started a minute ago
    cannot support it however busy it is."""
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=30)])
    assert "HIGH_CPU_SUSTAINED" not in [s["code"] for s in signals]


def test_age_alone_does_not_classify_anything():
    signals = an.detect(groups=[group("g:1", cpu=0.0, age=200000)])
    assert [s["code"] for s in signals] == ["AGE_WITHOUT_ACTIVITY"]
    assert an.classify(signals, subject="g:1")["classification"] == "SIGNALS_PRESENT"


def test_no_signals_is_reported_as_observed_not_as_healthy():
    """What was observed is that no anomaly fired. That is not a positive
    statement that the thing works — only a functional probe can say that,
    and none was consulted."""
    result = an.classify([], subject="g:1")
    # Named for what happened — an observation — rather than for a conclusion
    # the detector is not entitled to draw.
    assert result["classification"] == "HEALTHY_OBSERVED"
    assert result["classification"] != "HEALTHY"
    assert "no anomaly fired" in result["because"]


# ---------------------------------------------------------------------------
# The task-state discipline
# ---------------------------------------------------------------------------


def test_an_unknown_task_state_is_not_read_as_idle():
    """The central UNKNOWN rule. This host has no task-to-process mapping at
    all, so treating a missing task state as idle would classify every busy
    group on the machine as stale."""
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000)],
                        task_states={"g:1": {"state": "UNKNOWN"}})
    assert "TASK_IDLE_PROCESS_ACTIVE" not in [s["code"] for s in signals]


def test_a_missing_task_state_leaves_a_visible_gap():
    """An empty signal list must not read as 'nothing to see'."""
    unchecked = an.unchecked(groups=[group("g:1")], task_states={})
    gap = next(u for u in unchecked if u["input"] == "task_states")
    assert gap["state"] == "UNKNOWN"
    assert "NOT evidence" in gap["why"]


def test_task_idle_plus_high_cpu_is_a_candidate():
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000)],
                        task_states={"g:1": {"state": "idle", "since": "2026-09-12T00:00:00Z"}})
    result = an.classify(signals, subject="g:1")
    assert result["classification"] == "STALE_CANDIDATE"
    assert result["recommendation"] == "REQUEST_CLEANUP_APPROVAL"


def test_idle_task_plus_high_cpu_plus_an_error_loop_is_high_confidence():
    signals = an.detect(
        groups=[group("g:1", cpu=800.0, age=90000)],
        task_states={"g:1": {"state": "idle", "since": "x"}},
        error_windows={"g:1": {"count": 85, "window_seconds": 3600, "still_occurring": True,
                               "sample_kind": "failed to refresh available models"}},
    )
    result = an.classify(signals, subject="g:1")
    assert result["classification"] == "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"


def test_orphan_plus_closed_task_is_high_confidence():
    signals = an.detect(
        groups=[group("g:1", cpu=10.0, age=90000, reparented=True)],
        task_states={"g:1": {"state": "closed", "since": "x"}},
    )
    result = an.classify(signals, subject="g:1")
    assert "ORPHAN_PROCESS_TREE" in result["signal_codes"]
    assert "TASK_CLOSED_RESOURCE_RUNNING" in result["signal_codes"]
    assert result["classification"] == "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"


def test_the_lineage_combination_reaches_the_live_hosts_case():
    """The 2026-09-13 codex case, measured: 405% CPU over 1d11h, root parented
    to init, 85 repeats of a failing loop — and no task mapping anywhere.

    Without this combination declared, that case classifies as nothing at all,
    which is fail-safe and uninformative at the same time. It is a CANDIDATE
    because it cannot show the WORK is finished, only that its origin is gone
    and it is failing."""
    signals = an.detect(
        groups=[group("g:1", cpu=405.8, age=128640.0, reparented=True)],
        error_windows={"g:1": {"count": 85, "window_seconds": 86400,
                               "still_occurring": True, "sample_kind": "refresh"}},
        task_states={},
    )
    result = an.classify(signals, subject="g:1")
    assert result["classification"] == "STALE_CANDIDATE"
    assert result["recommendation"] == "REQUEST_CLEANUP_APPROVAL"


def test_signals_on_different_subjects_never_combine():
    """Two subjects with partial signals must not add up to a conclusion about
    either. Merging facts about different things is how a story forms."""
    signals = an.detect(
        groups=[group("g:1", cpu=800.0, age=90000, reparented=True),
                group("g:2", cpu=0.0, age=100)],
        error_windows={"g:2": {"count": 85, "window_seconds": 3600, "still_occurring": True}},
    )
    results = {r["subject"]: r for r in an.classify_all(signals)}
    assert results["g:1"]["classification"] != "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"
    assert results["g:2"]["classification"] != "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"


# ---------------------------------------------------------------------------
# Protection is enforced here, not by the caller
# ---------------------------------------------------------------------------


def test_a_protected_subject_is_escalated_not_recommended():
    """The guard lives in `_recommend` rather than at the call site, because a
    guard the caller has to remember is one the next caller does not have."""
    signals = an.detect(
        groups=[group("g:1", cpu=800.0, age=90000)],
        task_states={"g:1": {"state": "idle", "since": "x"}},
    )
    result = an.classify(signals, subject="g:1", protection="protected")
    assert result["recommendation"] == "NEEDS_HUMAN"
    assert result["blast_radius"]["protected_excluded"]


def test_a_protected_container_never_even_produces_a_signal():
    """Not emitted-then-filtered. A signal that exists has to be filtered by
    every consumer; a signal that was never emitted cannot be forgotten."""
    signals = an.detect(containers=[
        container("runtime:container:baseline", state="exited", disposition="PROTECTED"),
    ])
    assert signals == []


def test_a_protected_subject_is_never_given_an_actionable_recommendation():
    """Across every signal set that would otherwise produce an action.

    `NONE` stays `NONE` for a protected subject — escalating a no-op to a human
    would flood the operator with things that require no decision, which is how
    an escalation channel stops being read. What the guard must prevent is an
    ACTION reaching a protected resource, and that is what is asserted.
    """
    actionable = ("RESTART_RESOURCE", "REQUEST_CLEANUP_APPROVAL")
    cases = [
        an.detect(containers=[container("c", state="exited")]),
        an.detect(probe_results={"c": {"liveness": "RUNNING", "functional": "FAILED"}}),
        an.detect(groups=[group("c", cpu=800.0, age=90000)],
                  task_states={"c": {"state": "idle", "since": "x"}}),
        an.detect(groups=[group("c", cpu=800.0, age=90000)],
                  task_states={"c": {"state": "idle", "since": "x"}},
                  error_windows={"c": {"count": 85, "window_seconds": 3600}}),
    ]
    for signals in cases:
        result = an.classify(signals, subject="c", protection="protected")
        assert result["recommendation"] not in actionable, result["classification"]


def test_an_unknown_owner_container_gets_a_proposal_never_an_action():
    signals = an.detect(containers=[
        container("runtime:container:mystery", state="exited", unknown=True),
    ])
    codes = [s["code"] for s in signals]
    assert codes == ["STOPPED_REVIEW_CANDIDATE"]
    result = an.classify(signals, subject="runtime:container:mystery")
    # No declared combination covers this, so it stays unclassified.
    assert result["recommendation"] == "NONE"


# ---------------------------------------------------------------------------
# §8/§10 · a process that is up and cannot answer
# ---------------------------------------------------------------------------


def test_a_failed_functional_probe_is_broken_and_not_stale():
    """Restarting may be right; reclaiming is not. Conflating the two is how a
    working service gets deleted."""
    signals = an.detect(probe_results={"svc:1": {"liveness": "RUNNING", "functional": "FAILED"}})
    result = an.classify(signals, subject="svc:1")
    assert result["classification"] in ("BROKEN_NOT_STALE", "INFRASTRUCTURE_AT_RISK")
    assert result["recommendation"] != "REQUEST_CLEANUP_APPROVAL"


def test_a_critical_resource_failing_its_functional_probe_is_an_incident():
    signals = an.detect(
        probe_results={"device:server-beijing": {"liveness": "RUNNING", "functional": "FAILED"}},
        criticality={"device:server-beijing": "critical"},
    )
    result = an.classify(signals, subject="device:server-beijing", criticality="critical")
    assert result["classification"] == "INFRASTRUCTURE_AT_RISK"
    assert result["recommendation"] == "NEEDS_HUMAN"


def test_a_non_critical_resource_does_not_become_an_incident():
    signals = an.detect(probe_results={"svc:1": {"liveness": "RUNNING", "functional": "FAILED"}})
    result = an.classify(signals, subject="svc:1", criticality="normal")
    assert result["classification"] != "INFRASTRUCTURE_AT_RISK"


def test_an_mcp_server_that_is_up_and_cannot_handshake_is_broken():
    """A tunnel alive and forwarding nothing, an MCP process alive and not
    answering — the same shape at two layers, and both read as healthy to
    every liveness-only check."""
    signals = an.detect(probe_results={
        "mcp:browser-use": {"kind": "mcp", "liveness": "RUNNING", "functional": "FAILED"},
    })
    codes = [s["code"] for s in signals]
    assert "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE" in codes
    assert an.classify(signals, subject="mcp:browser-use")["classification"] == "BROKEN_NOT_STALE"


def test_a_tunnel_that_is_up_and_forwards_nothing_is_not_healthy():
    signals = an.detect(probe_results={
        "tunnel:rdc": {"liveness": "RUNNING", "functional": "FAILED"},
    })
    assert any(s["code"] == "FUNCTIONAL_PROBE_FAILED" for s in signals)


# ---------------------------------------------------------------------------
# Readings, thresholds, and the cardinality of an absence
# ---------------------------------------------------------------------------


def test_every_signal_reports_the_threshold_it_was_compared_against():
    """A reading without its threshold cannot be reviewed, only believed."""
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000)])
    cpu = next(s for s in signals if s["code"] == "HIGH_CPU_SUSTAINED")
    assert cpu["measured"]["threshold_percent"] == T["cpu_percent_high"]
    assert cpu["measured"]["cpu_percent"] == 800.0
    assert "lifetime average over" in cpu["window"]


def test_an_unreadable_filesystem_is_not_an_empty_one():
    """`used_percent: None` is "we could not read it", and treating it as 0
    would silently report a full disk as an empty one."""
    signals = an.detect(host_facts={"filesystems": [
        {"mount": "/", "used_percent": None},
        {"mount": "/data", "used_percent": 0.0},
    ]})
    assert signals == []


def test_filesystem_pressure_fires_and_names_which_mount():
    signals = an.detect(host_facts={"filesystems": [
        {"mount": "/", "used_percent": 91.0, "available_bytes": 10 ** 9},
        {"mount": "/data", "used_percent": 40.0},
    ]})
    assert [s["code"] for s in signals] == ["ROOT_FS_PRESSURE"]


def test_an_error_count_below_the_threshold_does_not_fire():
    signals = an.detect(error_windows={"g:1": {"count": 3, "window_seconds": 3600}})
    assert signals == []


def test_an_error_window_with_no_count_is_not_zero():
    signals = an.detect(error_windows={"g:1": {"count": None, "window_seconds": 3600}})
    assert signals == []


# ---------------------------------------------------------------------------
# §11 · a classification never replaces its signals
# ---------------------------------------------------------------------------


def test_the_classification_carries_every_signal_that_produced_it():
    signals = an.detect(
        groups=[group("g:1", cpu=800.0, age=90000)],
        task_states={"g:1": {"state": "idle", "since": "x"}},
        error_windows={"g:1": {"count": 85, "window_seconds": 3600, "still_occurring": True}},
    )
    result = an.classify(signals, subject="g:1")
    assert result["classification"] == "STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"
    # The three facts are still there, not summarised away.
    assert result["signals"] == signals
    assert set(result["signal_codes"]) >= {
        "TASK_IDLE_PROCESS_ACTIVE", "HIGH_CPU_SUSTAINED", "REPEATED_ERROR_LOOP",
    }


def test_a_signal_records_that_it_implies_nothing_on_its_own():
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000)])
    assert all(s["implies_stale"] is False for s in signals)
    assert all(s["evidence_kind"] == "measurement" for s in signals)


def test_a_recommendation_always_carries_a_blast_radius():
    signals = an.detect(
        groups=[group("g:1", cpu=800.0, age=90000)],
        task_states={"g:1": {"state": "idle", "since": "x"}},
    )
    result = an.classify(signals, subject="g:1")
    blast = result["blast_radius"]
    assert blast["affected_subjects"] == ["g:1"]
    assert "without a blast radius is not a recommendation" in blast["note"]


def test_the_detector_never_reports_a_cause():
    """§12. There is no field for a diagnosis and adding one would be the
    whole failure mode."""
    signals = an.detect(groups=[group("g:1", cpu=800.0, age=90000, reparented=True)])
    for s in signals:
        assert "cause" not in s
        assert "diagnosis" not in s
        assert "root_cause" not in s
