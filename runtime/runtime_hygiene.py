#!/usr/bin/env python3
"""§16 · Runtime hygiene — what a resource IS, as opposed to what it should be.

Nine words
----------
    ACTIVE            running, as declared, and recently in use
    EXPECTED_STOPPED  at rest, and the registration says at rest. Not a fault
    IDLE              running, as declared, and measurably not doing anything
    ORPHANED          the owner's working directory is gone
    SUPERSEDED        something else on this host has replaced it
    UNOWNED           no owner could be confirmed
    PROTECTED         declared protected; no automatic path reaches it
    CLEANUP_CANDIDATE a set of independent facts agree that it may be reclaimed
    UNKNOWN           nothing established

The single-variable rule, enforced structurally
-----------------------------------------------
§16 forbids one variable deciding CLEANUP_CANDIDATE, and names the tempting
one: `stopped > N days`. Age is the easiest thing to measure and the weakest
thing to conclude from — a machine that has been off for two months and one
that has been off since lunch are the same number of days apart from each
other and completely different in every way that matters.

So `CLEANUP_CANDIDATE` here requires a MINIMUM OF TWO INDEPENDENT FACTS, where
independence is declared rather than counted: `_candidate_evidence` returns a
dict of named facts and the class requires at least two of them, from different
sources. `age_seconds` is deliberately not one of them. A caller that supplies
nothing but an age gets `UNKNOWN` with a reason saying so, which is the whole
test: `test_age_alone_never_makes_a_reclaim_candidate`.

The class is a LABEL
--------------------
`CLEANUP_CANDIDATE` is a fact about what the evidence supports. It is not an
instruction, it confers nothing, and nothing in this round may act on it —
§24's Docker hygiene report and §25's governance contract both take this module's
output as input and neither of them executes it. The word `candidate` is doing
work: it names a question for a person, not a queue.

Reused, not rebuilt
-------------------
The container-level vocabulary already exists in `hygiene.py` (PROTECTED,
ACTIVE, STALE_CANDIDATE, ORPHANED, UNKNOWN_OWNER, STOPPED_RECLAIM_CANDIDATE) and
that module owns §24's Docker report. This module is the RUNTIME-level ladder
from the same facts and adds the three classes that need a registration to
answer — EXPECTED_STOPPED (which needs a desired state), IDLE (which needs a
measurement of use) and SUPERSEDED (which needs a replacement) — plus the
`alias_of` map below, so a reader can see the two vocabularies are the same
vocabulary rather than two that must be reconciled. The mapping is a test, not
a comment.
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


registry = _load("serveros_runtime_registry", "registry.py")
# Loaded under the name the rest of the codebase already uses for it, on
# purpose: `daemon.py`, `cli.py` and three test modules all load `hygiene.py`
# as `serveros_runtime_hygiene`, and adding a second name for it would put two
# module objects with two copies of the container vocabulary in one process —
# the drift this codebase's `_load` pattern exists to prevent.
hygiene = _load("serveros_runtime_hygiene", "hygiene.py")

if __name__ == "serveros_runtime_hygiene":
    # `_load` returns whatever `sys.modules` already holds under a name, so if
    # THIS module were ever loaded under `serveros_runtime_hygiene` a caller
    # would silently get these nine runtime classes where it asked for the
    # container ladder. Fail loudly instead: the two vocabularies are related
    # and are not the same one.
    raise RuntimeError(
        "runtime_hygiene.py was loaded as `serveros_runtime_hygiene`, which is "
        "the established name for hygiene.py (the container ladder). Load it "
        "under any other name; the two modules are different ladders."
    )

PROTECTED = "PROTECTED"
ACTIVE = "ACTIVE"
EXPECTED_STOPPED = "EXPECTED_STOPPED"
IDLE = "IDLE"
ORPHANED = "ORPHANED"
SUPERSEDED = "SUPERSEDED"
UNOWNED = "UNOWNED"
CLEANUP_CANDIDATE = "CLEANUP_CANDIDATE"
UNKNOWN = "UNKNOWN"

CLASSES = (PROTECTED, SUPERSEDED, UNOWNED, ORPHANED, CLEANUP_CANDIDATE,
           EXPECTED_STOPPED, ACTIVE, IDLE, UNKNOWN)

# The classes no automatic path may ever act on, listed explicitly so that
# adding a class forces a decision about it. `CLEANUP_CANDIDATE` is absent and
# that absence is the point of §16's last sentence: it is a label, and every
# consumer in this round treats it as one.
LABEL_ONLY = frozenset({CLEANUP_CANDIDATE})
NEVER_ACTIONABLE = frozenset(set(CLASSES) - {CLEANUP_CANDIDATE})

# `hygiene.py`'s container vocabulary and this one, side by side. Not a merge —
# the container ladder answers a different question about a different object —
# but they must not be two words for one state that nobody reconciled.
ALIAS_OF = {
    PROTECTED: "PROTECTED",
    ACTIVE: "ACTIVE",
    ORPHANED: "ORPHANED",
    UNOWNED: "UNKNOWN_OWNER",
    CLEANUP_CANDIDATE: "STALE_CANDIDATE",
}

# Lifecycle classes with a bounded lifetime. A resource whose lifetime ends by
# itself is the only kind that can be called reclaimable; a `persistent` one
# cannot be, because its whole declaration is that it should still be here.
BOUNDED_LIFECYCLE = ("task-scoped", "disposable")

# The lifecycle classes a session may own. Duplicated from `anomaly` on purpose
# — see `test_the_two_declared_session_lists_are_one_list` — with the SSOT in
# `managed-runtime-contract.yaml`.
SESSION_OWNERS = ("session:", "agent-runtime:", "agent-session:")


def _facts(comparison=None, activity=None, owner_evidence=None, superseded_by=None,
           traffic=None, protection=None) -> dict:
    """The named facts a classification is allowed to rest on.

    Each key names where it came from. A caller cannot pass `stopped_days` and
    have it counted: it is not a key here, and `_candidate_evidence` reads only
    the keys below.
    """
    comparison = comparison or {}
    activity = activity or {}
    owner_evidence = owner_evidence or {}
    return {
        "protection": protection,
        "desired_state": comparison.get("desired_state"),
        "actual_state": comparison.get("actual_state"),
        "desired_actual_verdict": comparison.get("verdict"),
        # A measurement of use, with its source. `None` means nobody measured
        # it, which is different from a measurement of zero.
        "last_active_at": activity.get("last_active_at"),
        "activity_source": activity.get("activity_source"),
        "activity_measured": bool(activity),
        "carrying_traffic": traffic if traffic is not None else activity.get("carrying_traffic"),
        # Ownership: whether an owner could be confirmed, and whether the
        # owner's working directory is still there.
        "owner_confirmable": owner_evidence.get("confirmable"),
        "owner_closed": owner_evidence.get("closed"),
        "owner_evidence_source": owner_evidence.get("source"),
        "working_dir_exists": owner_evidence.get("working_dir_exists"),
        "superseded_by": list(superseded_by or []),
        # Corroboration that counts toward nothing. Kept as a fact because a
        # reader of the row should see it, and kept out of `_candidate_evidence`
        # because §16 names it as the variable that must not decide.
        "is_not_running": comparison.get("actual_state") == "STOPPED",
    }


def _candidate_evidence(facts: dict) -> dict:
    """The independent facts that would support CLEANUP_CANDIDATE, by name.

    Independent means "a different source answered it", and that is why each
    entry carries its source rather than being a bare boolean. Two facts from
    one source are one fact: `owner_closed` and `working_dir_exists` both
    coming from a compose label would be a single reading reported twice, and a
    threshold of two would then be satisfied by a threshold of one.
    """
    out = {}
    if facts["owner_closed"] is True and facts["owner_evidence_source"]:
        out["the_owner_is_closed"] = facts["owner_evidence_source"]
    if facts["working_dir_exists"] is False and facts["owner_evidence_source"]:
        out["the_owners_working_directory_is_gone"] = facts["owner_evidence_source"]
    if facts["activity_measured"] and not facts["last_active_at"]:
        out["no_activity_was_measured"] = facts["activity_source"] or "unspecified probe"
    if facts["carrying_traffic"] is False:
        out["nothing_is_connected_to_it"] = "the port view (§14)"
    # `it is not running` is deliberately NOT in here, though it is the first
    # thing every measurement offers. Counting it would make `stopped + one
    # other fact` sufficient — two variables one of which is the forbidden one,
    # which is §16's prohibition wearing a disguise. It is reported in `facts`
    # as corroboration and counted toward nothing.
    return out


def _distinct_sources(evidence: dict) -> set:
    return {source for source in evidence.values() if source}


def classify(*, resource=None, comparison=None, activity=None, owner_evidence=None,
             superseded_by=None, traffic=None, protection=None, now=None) -> dict:
    """One resource's hygiene class, plus every other class its facts support.

    `also_matches` is not decoration. ORPHANED and CLEANUP_CANDIDATE are
    routinely true of the same resource — an owner who left and a lane that may
    be reclaimed — and a ladder that reported only the first would hide the
    second from whoever is deciding what to do. The primary class is the one a
    single word must name; the rest are the answer's working.
    """
    facts = _facts(comparison=comparison, activity=activity,
                   owner_evidence=owner_evidence, superseded_by=superseded_by,
                   traffic=traffic, protection=protection)
    declared_protection = protection if protection is not None else (
        resource.protection if isinstance(resource, registry.Resource) else None)
    lifecycle = resource.lifecycle if isinstance(resource, registry.Resource) else None

    matches: list[tuple] = []

    if declared_protection == "protected":
        matches.append((PROTECTED, (
            "this resource is declared protected, so no automatic path reaches "
            "it whatever else is true of it. §17: protection is declared, never "
            "inferred, and it is checked before every other conclusion here"
        )))

    if facts["superseded_by"]:
        matches.append((SUPERSEDED, (
            f"something else names this resource as the thing it replaced "
            f"({', '.join(facts['superseded_by'])}). A resource that has been "
            f"replaced is not stale — it is finished, and those are different "
            f"dispositions: the stale one is reclaimed, the superseded one is "
            f"retired by whoever replaced it"
        )))

    if facts["owner_confirmable"] is False and facts["owner_evidence_source"]:
        matches.append((UNOWNED, (
            f"no owner could be confirmed from anything checkable "
            f"({facts['owner_evidence_source']}). Terminal: reported, counted, "
            f"and never a candidate. This is the correct and expected answer "
            f"for most containers on a shared host, and it outranks "
            f"CLEANUP_CANDIDATE deliberately: reclamation is a decision about "
            f"somebody's resource, and a resource whose owner is unknown has no "
            f"somebody to decide about it. §16's standing rule — unknown owner "
            f"is DEFER, never a candidate"
        )))

    # The candidate sits ABOVE ORPHANED rather than below it. Both are true of
    # the canonical case — a task-scoped container whose owner finished and
    # whose working directory is gone — and if ORPHANED took the primary word
    # the more specific conclusion would never appear as a primary anywhere,
    # which is the "declared but nothing emits it" shape this round keeps
    # finding. ORPHANED is not lost: it is in `also_matches`.
    candidate_evidence = _candidate_evidence(facts)
    sources = _distinct_sources(candidate_evidence)
    bounded = lifecycle in BOUNDED_LIFECYCLE
    owner_confirmed = facts["owner_confirmable"] is not False
    not_candidate_because = None
    if (len(candidate_evidence) >= 2 and len(sources) >= 2 and bounded
            and declared_protection != "protected" and owner_confirmed):
        matches.append((CLEANUP_CANDIDATE, (
            f"{len(candidate_evidence)} independent facts from {len(sources)} "
            f"sources agree ({'; '.join(sorted(candidate_evidence))}), the "
            f"lifecycle is {lifecycle} so its lifetime already ends by itself, "
            f"and it is not protected. This is a LABEL: it says the evidence "
            f"supports asking a person, and nothing in this round acts on it"
        )))
    elif len(candidate_evidence) >= 2:
        # Two facts were not enough here, and saying WHICH condition failed is
        # the difference between a report somebody can act on and a class that
        # silently did not appear.
        if declared_protection == "protected":
            not_candidate_because = ("the resource is protected; protection "
                                     "outranks every fact below it")
        elif not owner_confirmed:
            not_candidate_because = ("the owner could not be confirmed, so "
                                     "there is nobody for reclamation to be a "
                                     "decision about")
        elif not bounded:
            not_candidate_because = (
                f"{len(candidate_evidence)} facts point at reclamation and the "
                f"lifecycle is {lifecycle or 'undeclared'}, which has no bound: "
                f"a persistent resource that is stopped and unused is still "
                f"exactly what its declaration says it is. Reclaiming one is a "
                f"decommissioning decision, and it is not this module's to make"
            )
        else:
            not_candidate_because = (
                f"{len(candidate_evidence)} facts agree and they came from "
                f"{len(sources)} source, which is one source: independence is "
                f"declared by where a fact came from, not counted"
            )

    if facts["working_dir_exists"] is False and facts["owner_evidence_source"]:
        matches.append((ORPHANED, (
            f"the owner's working directory is gone ({facts['owner_evidence_source']}). "
            f"ORPHANED says the owner departed, not that the contents are "
            f"worthless — those are different claims and only one of them is "
            f"checkable"
        )))

    if facts["desired_state"] == "stopped" and facts["actual_state"] == "STOPPED":
        matches.append((EXPECTED_STOPPED, (
            "the registration declares `stopped` and the measurement agrees. "
            "At rest is the desired state here, not a fault — this is the class "
            "§15 unlocked, and it is why a desired state had to come from a "
            "declaration rather than a default"
        )))

    if facts["desired_state"] == "running" and facts["actual_state"] == "RUNNING":
        if facts["activity_measured"] and not facts["last_active_at"]:
            matches.append((IDLE, (
                f"running as declared, and {facts['activity_source'] or 'a probe'} "
                f"measured no activity. IDLE needs the measurement: a resource "
                f"between two requests looks identical to a resource between two "
                f"months, and only the measurement tells them apart"
            )))
        else:
            matches.append((ACTIVE, (
                "running as declared"
                + (", and measured in use" if facts["last_active_at"] else
                   ", with no measurement of use either way")
            )))

    if not matches:
        matches.append((UNKNOWN, (
            "nothing established this resource's condition: "
            f"desired={facts['desired_state']!r}, actual={facts['actual_state']!r}, "
            f"activity_measured={facts['activity_measured']}, "
            f"owner_confirmable={facts['owner_confirmable']!r}. §16's standing "
            f"rule: when nothing is established the answer is UNKNOWN, and it "
            f"is not reached by defaulting the missing part to the "
            f"convenient value"
        )))

    primary, why = matches[0]
    return {
        "class": primary,
        "why": why,
        "matches": [{"class": c, "why": w} for c, w in matches],
        "also_matches": [c for c, _ in matches[1:]],
        # What the class is allowed to cause, which today is nothing.
        "is_candidate": primary == CLEANUP_CANDIDATE,
        "label_only": primary in LABEL_ONLY,
        "actionable": False,
        "facts": facts,
        "candidate_evidence": candidate_evidence,
        # Set only when the evidence reached the threshold and the class still
        # did not appear. `None` means either the threshold was never reached or
        # it was and the class did appear — the two are told apart by `class`.
        "not_candidate_because": not_candidate_because,
        "schema": "serveros/runtime-hygiene.v1",
    }


def classify_all(rows, *, now=None) -> dict:
    """`classify` over many resources, plus the counts a report needs.

    Each row is the keyword arguments `classify` takes. The counts are split by
    class because the whole point of §16's vocabulary is that the reader can
    see how many of the stopped things are stopped on purpose, and a single
    `stopped: 23` cannot say that.
    """
    classified = [{"resource_ref": (row.get("resource_ref")),
                   **classify(**{k: v for k, v in row.items() if k != "resource_ref"},
                              now=now)}
                  for row in rows]
    counts = {name: 0 for name in CLASSES}
    for row in classified:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    return {
        "schema": "serveros/runtime-hygiene-report.v1",
        "resources": classified,
        "counts": counts,
        "candidates": [r for r in classified if r["is_candidate"]],
        "note": (
            "§16: every class here is a description and `CLEANUP_CANDIDATE` is "
            "a LABEL. This report was produced by reading; it changed nothing, "
            "it stopped nothing, and it removed nothing. A candidate names a "
            "question for a person"
        ),
    }
