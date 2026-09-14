#!/usr/bin/env python3
"""§15 · desired state against actual state.

The comparison itself lives in `health.Health.state`, whose rule table already
separates "at rest, as declared" from "declared running, is not". These tests
are about the two things this module adds: that it does not make a SECOND
comparison, and that it refuses to turn a missing or failed measurement into a
finding about the resource.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"

LIVE_REGISTRY = Path("/opt/applications/data/home-hwz/state/serveros/runtime"
                     "/registry/resources.yaml")


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


des = _load("serveros_runtime_desired", "desired.py")
registry = _load("serveros_runtime_registry", "registry.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")

FACT = des.FACT
UNKNOWN = des.UNKNOWN


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def resource(rid="lit0104-dev-litellm", *, desired="running", lifecycle="task-scoped",
             kind="test-runtime", protection="normal", owner_ref="work-item:LIT-0104",
             owner_type="work-item", probes=None):
    return {
        "id": rid,
        "kind": kind,
        "owner": {"type": owner_type, "ref": owner_ref},
        "scope": "user",
        "lifecycle": {"class": lifecycle},
        "desired": {"state": desired},
        "protection": {"level": protection},
        "probes": probes or [],
    }


def registry_of(*records, device_ref=None):
    document = {"schema": "serveros/managed-runtime-registry.v1",
                "resources": list(records)}
    if device_ref:
        document["device_ref"] = device_ref
    return registry.Registry(document=document)


def measured(state, *, liveness=None, container_status=None, match_count=None,
             systemd_active_state=None):
    """A measurement shaped like the JSON `serverosctl` renders."""
    evidence = {"type": "host-measurement"}
    if container_status is not None:
        evidence["container_status"] = container_status
    if match_count is not None:
        evidence["match_count"] = match_count
    if systemd_active_state is not None:
        evidence["systemd_active_state"] = systemd_active_state
    return {"derived_state": state,
            "liveness": {"state": liveness or ("RUNNING" if state != "NOT_RUNNING" else "STOPPED"),
                         "evidence": evidence,
                         "observed_at": "2026-09-14T06:00:00+00:00"}}


# ---------------------------------------------------------------------------
# The rule §15 states by name
# ---------------------------------------------------------------------------


def test_a_missing_desired_state_produces_no_mismatch():
    """`desired_state = None` is the ABSENCE of a comparison.

    Tested with a measurement that contradicts the most likely default, so
    that an implementation which quietly substitutes `running` fails here
    rather than in production: this resource is stopped, and if `None` became
    `running` it would be reported as a mismatch on the strength of a value
    nobody declared.
    """
    row = des.comparison(declared=None, measured=measured("NOT_RUNNING"),
                         resource_id="unregistered-thing")
    assert row["desired_state"] is None
    assert row["verdict"] == des.UNCHECKED
    assert row["mismatch"] is None
    assert "does not produce a mismatch" in row["why"]


def test_a_missing_desired_state_is_unchecked_even_when_the_health_model_blames_it():
    """The health model said NOT_RUNNING, whose own meaning text names a
    desired state. This module does not read that text, and the row says which
    fact was missing rather than reporting the model's verdict as a finding."""
    row = des.comparison(declared=None, measured=measured("NOT_RUNNING"))
    assert row["actual_derived_state"] == "NOT_RUNNING"
    assert row["verdict"] == des.UNCHECKED


def test_an_unmeasured_resource_is_unchecked_and_not_mismatched():
    row = des.comparison(declared=resource(), measured=None)
    assert row["desired_state"] == "running"
    assert row["actual_state"] is None
    assert row["verdict"] == des.UNCHECKED
    assert "Unmeasured is not mismatched" in row["why"]


@pytest.mark.parametrize("measured_state", ["UNAVAILABLE", "UNKNOWN", "NOT_APPLICABLE"])
def test_a_measurement_that_did_not_happen_never_becomes_a_finding(measured_state):
    """`UNAVAILABLE` means a probe could not ask. Reading that as "not running"
    reports a probe failure as a resource failure."""
    row = des.comparison(declared=resource(), measured=measured("NOT_RUNNING",
                                                                liveness=measured_state))
    assert row["actual_state"] == measured_state
    assert row["verdict"] == des.UNCHECKED
    assert "did not happen" in row["why"]


# ---------------------------------------------------------------------------
# The comparison that does happen
# ---------------------------------------------------------------------------


def test_declared_running_and_measured_stopped_is_a_mismatch():
    row = des.comparison(declared=resource(desired="running"),
                         measured=measured("NOT_RUNNING", container_status="exited"))
    assert row["verdict"] == des.IN_MISMATCH
    assert row["mismatch"] == {"declared": "running", "measured": "STOPPED",
                               "derived": "NOT_RUNNING"}


def test_declared_stopped_and_measured_stopped_is_the_desired_state():
    """The class the whole of §15 exists to make possible: at rest on purpose."""
    row = des.comparison(declared=resource(desired="stopped"),
                         measured=measured("STOPPED", container_status="exited"))
    assert row["verdict"] == des.IN_DESIRED_STATE
    assert row["mismatch"] is None


def test_declared_running_and_measured_running_is_the_desired_state():
    row = des.comparison(declared=resource(), measured=measured("DEGRADED", container_status="running"))
    assert row["verdict"] == des.IN_DESIRED_STATE


def test_a_derived_state_that_contradicts_a_different_declaration_is_not_a_mismatch():
    """`NOT_RUNNING` means "declared running, and is not". A resource that
    declares `stopped` has contradicted nothing, and reading the derived word
    without checking which declaration it is about reports the one resource
    that is exactly where it should be."""
    row = des.comparison(declared=resource(desired="stopped"),
                         measured=measured("NOT_RUNNING", container_status="exited"))
    assert row["verdict"] == des.IN_DESIRED_STATE
    assert "nothing was contradicted" in row["why"]


def test_the_comparison_comes_from_the_contract_not_from_string_equality():
    """With the mapping unreadable, everything is UNCHECKED. A comparator that
    fell back to comparing the two words directly would report IN_DESIRED_STATE
    for every pair whose spelling happened to match — which is how a comparison
    silently stops applying when one side gains a member."""
    original = des.contract_section
    try:
        des.contract_section = lambda: {}
        row = des.comparison(declared=resource(), measured=measured("DEGRADED"))
        assert row["verdict"] == des.UNCHECKED
        assert "gap in the declaration" in row["why"]
    finally:
        des.contract_section = original


def test_the_satisfies_map_is_used_when_there_is_no_derived_state():
    """A measurement rendered without a derived word still compares, through
    the contract's declared join rather than through the two spellings."""
    row = des.comparison(declared=resource(), measured=measured("", liveness="RUNNING"))
    assert row["actual_derived_state"] is None
    assert row["verdict"] == des.IN_DESIRED_STATE


def test_the_satisfies_map_is_used_when_there_is_no_derived_state_and_says_no():
    row = des.comparison(declared=resource(), measured=measured("", liveness="STOPPED"))
    assert row["verdict"] == des.UNCHECKED
    assert "no measured state for" in row["why"]


def test_a_measured_word_the_contract_does_not_name_is_unchecked():
    row = des.comparison(declared=resource(),
                         measured=measured("", liveness="SOMETHING_NEW"))
    assert row["verdict"] == des.UNCHECKED


# ---------------------------------------------------------------------------
# The measurement's own two readings
# ---------------------------------------------------------------------------


def test_a_mismatch_resting_on_a_process_match_carries_both_readings():
    """pgrep matching nothing has two meanings on a host with hidepid, and
    `runtime/serverosd.service` records the second one being measured here."""
    row = des.comparison(declared=resource(), measured=measured("NOT_RUNNING", match_count=0))
    assert row["verdict"] == des.IN_MISMATCH
    assert row["rests_on_a_process_match"] is True
    assert row["caveat"] and "visibility boundary" in row["caveat"]
    assert "positive control" in row["caveat"]


def test_a_mismatch_from_a_provider_response_carries_no_caveat():
    """Docker resolved the container and reported a state. There is no
    namespace in which that reading changes, so a caveat here would be noise —
    and noise on every row is how a caveat that matters stops being read."""
    row = des.comparison(declared=resource(),
                         measured=measured("NOT_RUNNING", container_status="exited"))
    assert row["rests_on_a_process_match"] is False
    assert row["caveat"] is None


def test_the_providers_own_word_survives_beside_the_normalized_one():
    row = des.comparison(declared=resource(),
                         measured=measured("NOT_RUNNING", container_status="restarting"))
    assert row["actual_state"] == "STOPPED"
    assert row["actual_raw"] == "restarting"


def test_an_unchecked_row_still_carries_every_field():
    keys = set(des.comparison(declared=resource(), measured=None))
    for other in (des.comparison(declared=None, measured=measured("NOT_RUNNING")),
                  des.comparison(declared=resource(), measured=measured("NOT_RUNNING"))):
        assert set(other) == keys


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def test_a_registered_resource_is_compared_against_its_own_registration():
    reg = registry_of(resource(desired="stopped"))
    document = des.desired_actual(registry_doc=reg,
                                  measured={"lit0104-dev-litellm": measured("STOPPED")},
                                  observed_at="2026-09-14T06:00:00+00:00")
    assert document["coverage"]["in_desired_state"] == 1
    assert document["comparisons"][0]["declared_by"].startswith("the registration")


def test_an_observed_runtime_nobody_registered_is_reported_as_unchecked():
    """The document is about the node, not about the registry. Dropping this
    row would make a resource that nobody declared invisible at exactly the
    moment §15's rule about missing desired state is the answer."""
    document = des.desired_actual(registry_doc=registry_of(),
                                  measured={"nobody-registered-this": measured("NOT_RUNNING")})
    assert document["coverage"]["unchecked"] == 1
    assert document["coverage"]["unchecked_because_nothing_declares_it"] == 1
    assert document["mismatches"] == []


def test_the_three_reasons_a_comparison_did_not_happen_are_counted_apart():
    """An undeclared resource, an unmeasured one and an unusable measurement
    are three different pieces of work for three different people."""
    reg = registry_of(resource("res-a", desired="running"),
                      resource("res-b", desired="running"),
                      resource("res-c", desired="running"))
    document = des.desired_actual(
        registry_doc=reg,
        measured={"res-b": measured("NOT_RUNNING", liveness="UNAVAILABLE")})
    coverage = document["coverage"]
    assert coverage["comparisons"] == 3
    assert coverage["unchecked"] == 3
    assert coverage["unchecked_because_nothing_declares_it"] == 0
    # `a` and `c` were never measured; `b` was measured and the measurement
    # could not be compared. Two rows because nothing looked, one because
    # something looked and could not see.
    assert coverage["unchecked_because_nothing_measured_it"] == 2
    assert coverage["unchecked_because_the_measurement_could_not_be_compared"] == 1


def test_the_mismatch_counts_split_by_how_the_measurement_was_taken():
    reg = registry_of(resource("res-a", desired="running"), resource("res-b", desired="running"))
    document = des.desired_actual(
        registry_doc=reg,
        measured={"res-a": measured("NOT_RUNNING", container_status="exited"),
                  "res-b": measured("NOT_RUNNING", match_count=0)})
    coverage = document["coverage"]
    assert coverage["in_mismatch"] == 2
    assert coverage["mismatches_resting_on_a_process_match"] == 1
    assert coverage["mismatches_from_a_provider_response"] == 1


def test_every_mismatch_produces_a_signal_and_every_signal_carries_its_threshold():
    reg = registry_of(resource(desired="running"))
    document = des.desired_actual(registry_doc=reg,
                                  measured={"lit0104-dev-litellm": measured("NOT_RUNNING")})
    assert len(document["signals"]) == 1
    signal = document["signals"][0]
    assert signal["code"] == des.CODE
    assert signal["severity"] == "medium"
    assert signal["implies_stale"] is False
    assert signal["measured"]["threshold"] == "running"
    assert signal["measured"]["actual_state"] == "STOPPED"
    assert signal["subject"] == "runtime:lit0104-dev-litellm"


def test_a_document_with_nothing_to_compare_says_which_half_was_missing():
    nothing = des.desired_actual()
    assert nothing["providers"]["registry"]["state"] == "UNAVAILABLE"
    assert nothing["providers"]["measurement"]["state"] == "UNAVAILABLE"
    assert nothing["providers"]["registry"]["why"]
    only_registry = des.desired_actual(registry_doc=registry_of(resource()))
    assert only_registry["providers"]["registry"]["state"] == "AVAILABLE"
    assert only_registry["providers"]["measurement"]["state"] == "UNAVAILABLE"


def test_a_registry_path_may_be_supplied_directly(tmp_path):
    import yaml
    path = tmp_path / "resources.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": [resource()],
    }))
    document = des.desired_actual(registry_doc=path, measured={})
    assert document["providers"]["registry"]["registrations"] == 1


def test_the_document_is_read_only_and_says_so():
    source = (RUNTIME / "desired.py").read_text()
    for forbidden in ("subprocess", "os.kill", "pkill", "docker stop",
                      "docker rm", "systemctl restart", "unlink("):
        assert forbidden not in source, forbidden
    assert "read-only" in des.desired_actual(registry_doc=registry_of())["note"]


# ---------------------------------------------------------------------------
# The live node
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not LIVE_REGISTRY.exists(),
                    reason="this node has no registry; the comparison is untestable here")
def test_the_live_registry_compares_every_registration():
    """A regression gate on the real document rather than on a fixture: the
    registry's `desired` block is required by `validate()`, so every live
    registration must produce a comparison and none of them may be UNCHECKED
    for want of a declaration."""
    reg = registry.Registry(path=LIVE_REGISTRY)
    document = des.desired_actual(registry_doc=reg, measured={})
    assert document["coverage"]["comparisons"] == len(reg.all())
    assert document["coverage"]["unchecked_because_nothing_declares_it"] == 0
