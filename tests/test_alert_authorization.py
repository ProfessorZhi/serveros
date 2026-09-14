#!/usr/bin/env python3
"""§Y — an alert code is not an instruction.

WHAT THIS FILE IS ABOUT
-----------------------
§Y's sentence is *"any alert code must never reach actuator authorization"*,
and until this file existed that sentence was prose. `alerting.recommendation`
said so in its own docstring — *"Nothing in this system acts on the answer, and
there is no code path in which this word reaches a machine"* — which is a claim
of exactly the kind this repository keeps finding to be true only by accident.
A property that is asserted nowhere is a property that holds until somebody
adds the branch that breaks it, and that branch will look reasonable.

Three vocabularies meet here and they are NOT alternatives to each other:

  alert codes       DISK_CRITICAL, SWAP_PRESSURE, STALE_CONTAINER, ...
  alert advice      MONITOR / REVIEW / GOVERNANCE_REQUIRED
  actuator verbs    PROBE / RECONCILE / RESTART / RECONNECT / STOP

The first two are what a person is told. The third is the only thing in this
product that changes a machine, and the whole of §Z is that the join between
them does not exist yet — real destructive governance needs a verified identity
AND an approval AND actuator admission, and an alert supplies none of the three.
So the assertion is disjointness of the vocabularies, plus the actuator
refusing an alert word by name. That is the mechanical half of §Y; the other
half — that a *review* candidate is not a CLEANUP_CANDIDATE and not destructive
eligibility — is the last test below.

NOT asserted here: that `hygiene.CLEANUP_CANDIDATE` and
`runtime_hygiene.CLEANUP_CANDIDATE` are different words for different objects.
They are aliases by declaration, and the traversal that makes that look like a
permission escalation is recorded beside the alias rather than papered over.

Hermetic: no docker, no systemd, no socket.
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


registry_mod = _load("serveros_runtime_registry", "registry.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
runtime_hygiene_mod = _load("serveros_runtime_runtime_hygiene", "runtime_hygiene.py")
anomaly_mod = _load("serveros_runtime_anomaly", "anomaly.py")
resource_anomaly_mod = _load(
    "serveros_runtime_resource_anomaly", "resource_anomaly.py")
alerting_mod = _load("serveros_runtime_alerting", "alerting.py")


# ---------------------------------------------------------------------------
# the vocabularies, each read from wherever it is declared
# ---------------------------------------------------------------------------


def actuator_operations() -> set:
    """The closed §8 operation vocabulary. Five, and a sixth is a contract edit."""
    return set(registry_mod.contract()["operations"]["values"])


def alert_codes() -> set:
    """Every code any producer can put on an alert.

    Both producers, because they grade the same alert list: `anomaly.py`'s
    fourteen runtime codes and `resource_anomaly.py`'s host codes. A disjointness
    test that read only one of them would pass for the wrong reason.
    """
    codes = set(anomaly_mod.SEVERITY) | set(resource_anomaly_mod.CODES)
    codes |= set(resource_anomaly_mod.SEVERITY)
    return codes


def alert_advice() -> set:
    """Every word the alert layer may suggest.

    Derived by calling the producer over every severity it is declared for,
    rather than by transcribing the three words — a transcription here would be
    a second copy of the vocabulary, and the failure mode of a second copy is
    that it agrees with the first one while both drift from the contract.
    """
    words = {alerting_mod.recommendation(severity) for severity in
             ("low", "medium", "high")}
    declared = (alerting_mod.policy().get("text") or {}).get("detail_shape") or []
    for line in declared:
        if "REVIEW / MONITOR / GOVERNANCE_REQUIRED" in line:
            words |= {"REVIEW", "MONITOR", "GOVERNANCE_REQUIRED"}
    return words


# ---------------------------------------------------------------------------
# the gate
# ---------------------------------------------------------------------------


def test_the_vocabularies_are_populated():
    """The positive control the disjointness assertions below need.

    An empty set is disjoint from everything. If a rename or a refactor made
    any of these readers return nothing, the tests below would keep passing
    and would be measuring nothing at all.
    """
    assert actuator_operations() >= {"RESTART", "PROBE", "RECONCILE"}
    assert len(alert_codes()) >= 20, sorted(alert_codes())
    assert alert_advice() >= {"REVIEW", "MONITOR"}, sorted(alert_advice())


def test_no_alert_code_is_an_actuator_operation():
    """§Y, mechanically. Two names for one thing is how an alert becomes an
    instruction — `PRUNE` as an alert code would be a prune with a person's
    attention as its only gate."""
    overlap = alert_codes() & actuator_operations()
    assert overlap == set(), f"alert codes that are also operations: {overlap}"


def test_no_alert_advice_is_an_actuator_operation():
    """The words a person is shown, against the words a machine obeys."""
    overlap = alert_advice() & actuator_operations()
    assert overlap == set(), f"advice that is also an operation: {overlap}"


def test_the_one_word_the_two_vocabularies_share_is_a_word_that_does_nothing():
    """`hygiene.RISK_TIERS` is what a HUMAN approval is asked about — STOP,
    REMOVE_CONTAINER, REMOVE_IMAGE, REMOVE_VOLUME, PRUNE — and it OVERLAPS the
    actuator's operation vocabulary in exactly one word.

    That overlap was measured here rather than assumed away, and it is not
    asserted to be empty because it is not empty: `STOP` is a cleanup tier a
    person approves AND a declared §8 operation. Today the collision is inert —
    `STOP` has no actuator, so `perform` answers NO_ACTUATOR and nothing runs,
    and the two callers (`hygiene.admission(tier=...)` and
    `Actuator.perform(operation)`) are distinct entry points that cannot be
    reached from one another.

    So this test pins the inert state instead of wishing for a tidier one. It
    goes red on the day the STOP actuator is written, which is precisely the
    conversation §Y wants to have at that moment and not after.
    """
    shared = set(hygiene_mod.RISK_TIERS) & actuator_operations()
    assert shared == {"STOP"}, (
        f"the two vocabularies now collide on {shared}; §Y's guarantee that a "
        f"review is not an authorization has to be re-argued")

    result = daemon_mod.Actuator(dry_run=True).perform("STOP", _a_container_resource())
    assert result["performed"] is False
    assert result["reason"] == "NO_ACTUATOR"


def test_the_actuator_refuses_every_alert_word_by_name():
    """Disjoint vocabularies are necessary and not sufficient: the actuator
    could still be mapping an unknown word onto a known one, which `perform`'s
    own docstring names as the one thing a control plane must never do.

    The positive control is the second half of the test — a real operation must
    NOT come back `UNKNOWN_OPERATION`, or the loop below would pass against an
    actuator that refuses literally everything.
    """
    actuator = daemon_mod.Actuator(dry_run=True)
    resource = _a_container_resource()

    for word in sorted(alert_codes() | alert_advice() | set(hygiene_mod.CLASSES)
                       | set(runtime_hygiene_mod.CLASSES)):
        result = actuator.perform(word, resource)
        assert result["performed"] is False, word
        assert result["reason"] == "UNKNOWN_OPERATION", word

    control = actuator.perform("RESTART", resource)
    assert control.get("reason") != "UNKNOWN_OPERATION", control
    assert control["dry_run"] is True and control["performed"] is False, control


def test_a_review_candidate_is_not_the_actionable_class():
    """§Y's other half, and the reason `STOPPED_RECLAIM_CANDIDATE` was renamed.

    Three things a reader must not be able to substitute for one another:
    something a person should REVIEW, something called a CLEANUP_CANDIDATE, and
    something that may actually be acted on. The first two are the same class on
    both ladders; the third is `STALE_CANDIDATE` alone.
    """
    assert "STOPPED_REVIEW_CANDIDATE" in hygiene_mod.CLASSES
    assert "STOPPED_RECLAIM_CANDIDATE" not in hygiene_mod.CLASSES
    assert "STOPPED_REVIEW_CANDIDATE" in hygiene_mod.NEVER_ACTIONABLE
    assert "STOPPED_REVIEW_CANDIDATE" not in hygiene_mod.ACTIONABLE_CLASSES
    assert hygiene_mod.ACTIONABLE_CLASSES == frozenset({"STALE_CANDIDATE"})

    # The runtime ladder's label is never actionable either, and its alias to
    # `STALE_CANDIDATE` is a statement about which question the two ladders
    # answer — not a grant. Asserted, because the alias read alone inverts this.
    assert runtime_hygiene_mod.CLEANUP_CANDIDATE in runtime_hygiene_mod.LABEL_ONLY
    assert runtime_hygiene_mod.CLEANUP_CANDIDATE not in hygiene_mod.ACTIONABLE_CLASSES
    assert runtime_hygiene_mod.NEVER_ACTIONABLE == frozenset(
        set(runtime_hygiene_mod.CLASSES) - {runtime_hygiene_mod.CLEANUP_CANDIDATE})


def test_the_alert_contract_still_says_nothing_was_stopped():
    """§V/§Z in the delivered text: an alert about a resource that a person
    might want gone has to say, every time, that nothing was done about it."""
    alarm = alerting_mod.policy()["text"]["detail_shape"]
    joined = " ".join(alarm)
    assert "nothing was stopped" in joined
    assert "REVIEW / MONITOR / GOVERNANCE_REQUIRED" in joined


# ---------------------------------------------------------------------------
# a resource the actuator will actually consider
# ---------------------------------------------------------------------------


def _a_container_resource():
    """Built by the registry's own constructor rather than hand-written.

    A literal dict would be structurally immune to a change in the record
    shape, which is the defect `tests/test_core_ability_boundary.py` records
    elsewhere: a fake that agrees with a stale producer cannot notice it moved.
    """
    document = {
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": [{
            "id": "res",
            "kind": "container",
            "owner": {"type": "project", "ref": "project:litellm"},
            "scope": "user",
            "lifecycle": {"class": "persistent"},
            "desired": {"state": "running"},
            "protection": {"level": "normal"},
            "recovery": {"model": "restart"},
            "reconcile": {"mode": "restart", "max_attempts": 2, "backoff": [60, 300]},
            "probes": [{"id": "l", "kind": "container-state", "axis": "liveness",
                        "container": "res"}],
        }],
    }
    registry = registry_mod.Registry(document=document)
    return registry.get("res")


def test_the_contract_this_file_reads_is_the_one_on_disk(tmp_path):
    """The vocabularies above are read through modules; this asserts that the
    module's reading of the operation contract is the file's, so a test cannot
    pass against a contract that was replaced in memory."""
    declared = yaml.safe_load(
        (RUNTIME / "managed-runtime-contract.yaml").read_text())["operations"]["values"]
    assert set(declared) == actuator_operations()
    assert {"REMOVE", "PRUNE", "STOP_CONTAINER"} & set(declared) == set()
