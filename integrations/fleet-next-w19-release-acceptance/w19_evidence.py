"""The recorded judgements, and the document the two verdicts are read from.

The layers and findings below are this lane's *judgements*, written down once
and reviewable. The retire map is not: it is measured by `w19_measure.py` and
passed in, so a reader can re-derive every row without trusting this file.

What a layer means here is narrow on purpose: "does the landing's own delta
leave this layer sound?" — not "is the whole repository release-ready on this
layer". A release has extra questions that the landing does not, and those are
carried as findings with `scope=RELEASE` instead of as a red merge layer, so
the two verdicts cannot be argued from the same number.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import w19_gate as g  # noqa: E402  (a sibling module, not an installed package)

SCHEMA = "serveros/fleet-next-w19-release-acceptance.v1"

# The landing being signed: #519, whose head is an additive delta over main.
LANDING_PR = 519

RECORDED_LAYERS = {
    "CODE": g.READY,
    "UNIT": g.READY,
    "CI": g.READY,
    "INDEPENDENT_QA": g.READY,
    "ARTIFACT": g.READY,
    "REAL_DEVICE": g.NOT_RUN,
}

RECORDED_FINDINGS = (
    g.Finding(
        g.BLOCKER, "CODE", "fleet-next-agent-monitor + monitor-source-adapter",
        "fleet_next_agent_monitor/sources.py:399 vs "
        "fleet_next_monitor_source_adapter/adapter.py:425",
        "two readers of the same completed-run payload disagree about whether a "
        "transcript is native evidence: the adapter says yes and returns "
        "TERMINAL_VERIFIED, the monitor says no and its offline fixture refuses "
        "the key. Both behaviours are asserted by tests that never meet, so a "
        "green suite is compatible with the disagreement. Already in main; the "
        "landing exposes it rather than introducing it.",
        g.RELEASE),
    g.Finding(
        g.BLOCKER, "ARTIFACT", "artifact builder (#518 lane)",
        "integrations/fleet-next-wave18-artifact-builder/wave18_builder.py",
        "the five pinned digests reproduce only at #518's head, which is not in "
        "main. A release cannot cite reproducible artifacts from a tree that "
        "does not contain the builder change that makes them reproducible.",
        g.RELEASE),
    g.Finding(
        g.BLOCKER, "REAL_DEVICE", "release owner",
        "0 of 3 devices",
        "no device has run this build. The directive forbids a real-device gate "
        "in this task, so the layer is NOT_RUN rather than green, and 0/3 is "
        "reported as 0/3.",
        g.RELEASE),
    g.Finding(
        g.NOTE, "CODE", "mainline owner",
        "integrations/fleet-next-agent-monitor/ (sources without tests)",
        "main carries the monitor and adapter sources without the test files "
        "that #519 adds, which is why the disagreement above went unnoticed on "
        "main. The landing closes this gap; that is a reason to land it, not a "
        "reason to hold it.",
        g.RELEASE),
    g.Finding(
        g.NOTE, "UNIT", "fleet-next-agent-monitor",
        "fleet_next_agent_monitor/state.py:34",
        "`import fcntl` at module scope makes the package unimportable on "
        "Windows; 45 of 52 Windows test failures trace to it. The product runs "
        "on Linux, so this is a portability note, not a release blocker.",
        g.RELEASE),
)


def build(measured: dict) -> dict:
    """Compose the evidence document. `measured` is `w19_measure.measure()`."""
    gate = g.Gate.from_layers(RECORDED_LAYERS, findings=RECORDED_FINDINGS)
    return {
        "schema": SCHEMA,
        "landing_pr": LANDING_PR,
        "retire_map": measured,
        "gate": gate.as_dict(),
        "verdicts": {
            "LANDING_VERDICT": gate.landing_verdict(),
            "RELEASE_VERDICT": gate.release_verdict(),
        },
        "layer_question": (
            "does the landing's own delta leave this layer sound? "
            "release-only questions travel as findings with scope=RELEASE"),
    }
