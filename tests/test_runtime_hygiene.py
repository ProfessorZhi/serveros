#!/usr/bin/env python3
"""§16 · Runtime hygiene.

Two things are being tested. The first is the ladder: nine words, each reached
by a fact rather than by a default. The second is §16's specific prohibition —
that no single variable decides CLEANUP_CANDIDATE, and that the age of a
stopped resource is the variable people reach for first.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


rh = _load("serveros_runtime_runtime_hygiene", "runtime_hygiene.py")
registry = _load("serveros_runtime_registry", "registry.py")
hygiene = _load("serveros_runtime_hygiene", "hygiene.py")


def resource(rid="lit0104-dev-litellm", *, lifecycle="task-scoped",
             protection="normal", desired="running", kind="test-runtime"):
    return registry.validate({
        "id": rid,
        "kind": kind,
        "owner": {"type": "work-item", "ref": "work-item:LIT-0104"},
        "scope": "user",
        "lifecycle": {"class": lifecycle},
        "desired": {"state": desired},
        "protection": {"level": protection},
        "probes": [],
    })


def comparison(desired="running", actual="RUNNING", verdict="IN_DESIRED_STATE"):
    return {"desired_state": desired, "actual_state": actual, "verdict": verdict}


# ---------------------------------------------------------------------------
# The single-variable rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [("stopped_days", 90), ("age_seconds", 7_776_000),
                                         ("stopped_since", "2026-06-01")])
def test_there_is_no_field_an_age_could_be_passed_through(field, value):
    """§16's named prohibition, enforced structurally rather than by a check
    inside the function. `stopped_days=90` is not "ignored" — there is no such
    parameter, and the typed keyword-only signature means a caller reaching for
    it gets a TypeError rather than a resource classified on one number. An
    ignored field is one refactor away from being read; a field that does not
    exist has to be added on purpose."""
    with pytest.raises(TypeError):
        rh.classify(resource=resource(), comparison=comparison(), **{field: value})


def test_age_alone_never_makes_a_reclaim_candidate():
    """And the honest reading of what a caller with only an age actually has:
    a stopped resource and nothing else. Two facts are required, so this cannot
    be a candidate whatever the age was."""
    result = rh.classify(resource=resource(),
                         comparison=comparison("running", "STOPPED", "IN_MISMATCH"))
    assert result["class"] != rh.CLEANUP_CANDIDATE
    assert result["is_candidate"] is False
    assert result["candidate_evidence"] == {}


def test_the_fact_set_is_closed():
    """The keys a classification may rest on are the parameters of `classify`
    and the keys of `_facts`, and nothing else reaches the decision: an extra
    keyword cannot be smuggled in through a `**kwargs` pass-through."""
    import inspect
    signature = inspect.signature(rh.classify)
    assert all(p.kind is not inspect.Parameter.VAR_KEYWORD
               for p in signature.parameters.values())
    assert "now" in signature.parameters


def test_one_fact_is_not_enough_for_a_candidate():
    result = rh.classify(
        resource=resource(),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "source": "work-management"})
    assert result["class"] != rh.CLEANUP_CANDIDATE
    assert len(result["candidate_evidence"]) == 1


def test_two_facts_from_one_source_are_one_fact():
    """`owner_closed` and `working_dir_exists` both read from a compose label is
    a single reading reported twice. A threshold of two satisfied by one source
    is a threshold of one, and the shape of the bug is that it cannot be seen
    in the count."""
    result = rh.classify(
        resource=resource(),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "working_dir_exists": False,
                        "source": "com.docker.compose.project.working_dir"})
    assert len(result["candidate_evidence"]) == 2
    assert result["class"] != rh.CLEANUP_CANDIDATE
    assert "facts from 1 sources" not in result["why"]


def test_two_independent_facts_from_two_sources_reach_a_candidate():
    """Independence is declared by where a fact came from. "The owner finished"
    comes from work-management; "nothing is connected to it" comes from §14's
    port view. Two subsystems, one resource."""
    result = rh.classify(
        resource=resource(lifecycle="task-scoped"),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "working_dir_exists": False,
                        "source": "work-management"},
        traffic=False)
    assert result["class"] == rh.CLEANUP_CANDIDATE
    assert result["is_candidate"] is True
    assert result["label_only"] is True
    assert result["actionable"] is False
    # The weaker true words survive under it rather than being hidden.
    assert rh.ORPHANED in result["also_matches"]


def test_being_stopped_does_not_count_as_one_of_the_two_facts():
    """The disguise §16's prohibition wears. `stopped` is the first thing every
    measurement offers and the weakest thing to conclude from, so
    `stopped + one other fact` must not clear a threshold of two."""
    result = rh.classify(
        resource=resource(lifecycle="task-scoped"),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "source": "work-management"})
    assert result["facts"]["is_not_running"] is True
    assert result["class"] != rh.CLEANUP_CANDIDATE
    assert "it_is_not_running" not in result["candidate_evidence"]


def test_an_owner_nobody_could_confirm_is_never_a_candidate():
    """§16's DEFER. Two independent facts are not enough when the owner is
    unknown: reclamation is a decision about somebody's resource, and an
    unknown owner is nobody to decide about."""
    result = rh.classify(
        resource=resource(lifecycle="task-scoped"),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "confirmable": False,
                        "source": "one source"},
        traffic=False)
    assert len(result["candidate_evidence"]) >= 2
    assert result["class"] == rh.UNOWNED
    assert "nobody for reclamation" in result["not_candidate_because"]
    assert "the owner could not be confirmed" in result["not_candidate_because"]


def test_two_facts_about_a_persistent_resource_are_not_a_candidate():
    """A `persistent` resource that is stopped and unused is still exactly what
    its registration says it is. Reclaiming one is a decommissioning decision,
    and the ladder says which condition failed instead of silently not
    emitting the class."""
    result = rh.classify(
        resource=resource(lifecycle="persistent"),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "working_dir_exists": False,
                        "source": "work-management"},
        traffic=False)
    assert result["class"] != rh.CLEANUP_CANDIDATE
    assert "no bound" in result["not_candidate_because"]
    assert result["class"] == rh.ORPHANED


def test_protection_outranks_every_fact_that_would_otherwise_apply():
    result = rh.classify(
        resource=resource(protection="protected", lifecycle="task-scoped"),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "working_dir_exists": False, "confirmable": False,
                        "source": "work-management plus the filesystem"},
        superseded_by=["runtime:something-else"])
    assert result["class"] == rh.PROTECTED
    assert result["is_candidate"] is False
    # Everything else it also is, still visible under the one word.
    assert set(result["also_matches"]) >= {rh.ORPHANED, rh.UNOWNED, rh.SUPERSEDED}


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------


def test_running_as_declared_with_no_measurement_of_use_is_active():
    result = rh.classify(resource=resource(), comparison=comparison())
    assert result["class"] == rh.ACTIVE
    assert "no measurement of use either way" in result["why"]


def test_running_and_measurably_unused_is_idle_and_says_what_measured_it():
    result = rh.classify(resource=resource(), comparison=comparison(),
                         activity={"activity_source": "the port view (§14)"})
    assert result["class"] == rh.IDLE
    assert "the port view" in result["why"]


def test_idle_is_never_reached_without_a_measurement():
    """Nothing measured is not a measurement of nothing. A resource between two
    requests and a resource between two months look identical, and only the
    measurement tells them apart."""
    unmeasured = rh.classify(resource=resource(), comparison=comparison())
    assert unmeasured["class"] != rh.IDLE
    measured = rh.classify(resource=resource(), comparison=comparison(),
                           activity={"last_active_at": "2026-09-14T05:59:00+00:00"})
    assert measured["class"] == rh.ACTIVE


def test_at_rest_because_the_registration_says_so_is_expected_stopped():
    result = rh.classify(resource=resource(desired="stopped"),
                         comparison=comparison("stopped", "STOPPED"))
    assert result["class"] == rh.EXPECTED_STOPPED


def test_at_rest_against_a_running_declaration_is_not_expected_stopped():
    result = rh.classify(resource=resource(desired="running"),
                         comparison=comparison("running", "STOPPED", "IN_MISMATCH"))
    assert result["class"] != rh.EXPECTED_STOPPED


def test_an_owner_that_cannot_be_confirmed_is_unowned():
    result = rh.classify(resource=resource(), comparison=comparison(),
                         owner_evidence={"confirmable": False,
                                         "source": "no typed owner ref"})
    assert result["class"] == rh.UNOWNED
    assert result["is_candidate"] is False


def test_a_departed_owner_is_orphaned_not_reclaimable():
    result = rh.classify(resource=resource(), comparison=comparison(),
                         owner_evidence={"working_dir_exists": False,
                                         "source": "compose working dir"})
    assert result["class"] == rh.ORPHANED
    assert result["is_candidate"] is False


def test_supersession_is_only_reached_from_a_named_replacement():
    """No producer on this node declares supersession yet. The class exists and
    is unreachable without a caller that can name the thing that replaced this
    one — inventing a replacement from a timestamp is how a resource that is
    simply old gets retired."""
    unreached = rh.classify(resource=resource(), comparison=comparison())
    assert unreached["class"] != rh.SUPERSEDED
    named = rh.classify(resource=resource(), comparison=comparison(),
                        superseded_by=["runtime:litellm-container0-production"])
    assert named["class"] == rh.SUPERSEDED
    assert "the thing it replaced" in named["why"]


def test_nothing_established_is_unknown_and_the_row_says_why():
    result = rh.classify(resource=resource(), comparison=None)
    assert result["class"] == rh.UNKNOWN
    assert "nothing established" in result["why"]
    assert "desired=None" in result["why"]


# ---------------------------------------------------------------------------
# The class is a label
# ---------------------------------------------------------------------------


def test_no_hygiene_class_can_be_acted_on():
    """§16's last sentence, asserted over the whole vocabulary rather than over
    the one class that is tempting: adding a class that claims to be actionable
    has to be a decision somebody makes here."""
    for name in rh.CLASSES:
        assert name in rh.NEVER_ACTIONABLE or name in rh.LABEL_ONLY, name
    assert rh.LABEL_ONLY == {rh.CLEANUP_CANDIDATE}
    assert rh.NEVER_ACTIONABLE & rh.LABEL_ONLY == set()
    assert len(rh.CLASSES) == 9


def test_a_candidate_result_is_never_actionable():
    for kwargs in ({}, {"owner_evidence": {"closed": True, "working_dir_exists": False,
                                           "source": "work-management"},
                        "traffic": False}):
        assert rh.classify(resource=resource(), comparison=comparison(), **kwargs)[
            "actionable"] is False


def test_the_module_contains_no_mutating_action():
    source = (RUNTIME / "runtime_hygiene.py").read_text()
    for forbidden in ("subprocess", "os.kill", "pkill", "docker stop", "docker rm",
                      "docker prune", "systemctl stop", "unlink(", "shutil.rmtree"):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# One vocabulary, not two
# ---------------------------------------------------------------------------


def test_every_alias_names_a_class_the_container_ladder_also_has():
    """Two ladders, one vocabulary. Where they overlap the names must resolve
    to each other, and a class that exists in one and not the other must be a
    deliberate omission rather than an oversight."""
    for mine, theirs in rh.ALIAS_OF.items():
        assert theirs in hygiene.CLASSES, (mine, theirs)
    assert set(rh.ALIAS_OF) <= set(rh.CLASSES)


def test_the_classes_the_container_ladder_cannot_answer_are_the_new_ones():
    """Four of the nine have no equivalent in the container ladder, and each is
    one the container ladder is structurally unable to answer: EXPECTED_STOPPED
    needs a desired state (§15), IDLE needs a measurement of use, SUPERSEDED
    needs a named replacement, and UNKNOWN is what this ladder says when
    nothing was established. The other five are the same five words."""
    additions = {rh.EXPECTED_STOPPED, rh.IDLE, rh.SUPERSEDED, rh.UNKNOWN}
    assert set(rh.ALIAS_OF) | additions == set(rh.CLASSES)
    assert set(rh.ALIAS_OF) & additions == set()


def test_the_session_prefixes_are_one_list():
    """`anomaly.py` declares the owner prefixes that denote a session; this
    module declares the same three. They are kept apart deliberately — the two
    answer different questions — and this asserts they have not drifted."""
    anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
    assert set(rh.SESSION_OWNERS) == set(anomaly.SESSION_OWNER_PREFIXES)


def test_the_bounded_lifecycles_are_the_ones_the_registry_declares():
    classes = registry._values("lifecycle_classes")
    for name in rh.BOUNDED_LIFECYCLE:
        assert name in classes, name
    assert "persistent" not in rh.BOUNDED_LIFECYCLE


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def test_the_report_counts_each_class_apart():
    """`stopped: 23` cannot say how many of them are stopped on purpose. That
    is the whole reason the vocabulary has nine words."""
    report = rh.classify_all([
        {"resource_ref": "runtime:one", "resource": resource("res-one"),
         "comparison": comparison("running", "STOPPED", "IN_MISMATCH")},
        {"resource_ref": "runtime:two", "resource": resource("res-two", desired="stopped"),
         "comparison": comparison("stopped", "STOPPED")},
        {"resource_ref": "runtime:three",
         "resource": resource("res-three", protection="protected"),
         "comparison": comparison("running", "STOPPED", "IN_MISMATCH")},
    ])
    assert report["counts"][rh.EXPECTED_STOPPED] == 1
    assert report["counts"][rh.PROTECTED] == 1
    assert report["candidates"] == []
    assert "changed nothing" in report["note"]


def test_a_report_with_a_candidate_lists_it_and_still_acts_on_nothing():
    report = rh.classify_all([
        {"resource_ref": "runtime:one", "resource": resource("res-one"),
         "comparison": comparison("running", "STOPPED", "IN_MISMATCH"),
         "owner_evidence": {"closed": True, "working_dir_exists": False,
                            "source": "work-management"},
         "traffic": False},
    ])
    assert [c["class"] for c in report["candidates"]] == [rh.CLEANUP_CANDIDATE]
    assert all(c["actionable"] is False for c in report["candidates"])


def test_every_row_carries_the_evidence_the_class_rested_on():
    result = rh.classify(
        resource=resource(),
        comparison=comparison("running", "STOPPED", "IN_MISMATCH"),
        owner_evidence={"closed": True, "working_dir_exists": False,
                        "source": "work-management"},
        traffic=False)
    assert result["candidate_evidence"]["the_owner_is_closed"] == "work-management"
    assert result["candidate_evidence"]["nothing_is_connected_to_it"] == "the port view (§14)"
    assert result["facts"]["desired_actual_verdict"] == "IN_MISMATCH"
    assert [m["class"] for m in result["matches"]][0] == result["class"]
    assert result["not_candidate_because"] is None


@pytest.mark.parametrize("class_name", list(rh.CLASSES))
def test_each_class_is_reachable_and_each_reaches_its_own_word(class_name):
    """Every one of the nine must be produced by some fact set, and the primary
    class must be the one the ladder put first. A vocabulary with a word no
    input reaches is the shape of bug this round keeps finding: the declaration
    exists, nothing emits it, and no gate notices."""
    REACHES = {
        rh.PROTECTED: dict(resource=resource(protection="protected")),
        rh.SUPERSEDED: dict(superseded_by=["runtime:something-else"]),
        rh.UNOWNED: dict(owner_evidence={"confirmable": False, "source": "nothing typed"}),
        rh.ORPHANED: dict(owner_evidence={"working_dir_exists": False, "source": "workspace"}),
        rh.CLEANUP_CANDIDATE: dict(
            resource=resource(lifecycle="task-scoped"),
            owner_evidence={"closed": True, "working_dir_exists": False,
                            "source": "work-management"},
            traffic=False),
        rh.EXPECTED_STOPPED: dict(resource=resource(desired="stopped"),
                                  comparison=comparison("stopped", "STOPPED")),
        rh.ACTIVE: dict(resource=resource()),
        rh.IDLE: dict(resource=resource(), activity={"activity_source": "a probe"}),
        rh.UNKNOWN: dict(resource=resource(), comparison=None),
    }
    kwargs = dict(REACHES[class_name])
    kwargs.setdefault("comparison", comparison())
    assert rh.classify(**kwargs)["class"] == class_name
