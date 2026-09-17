#!/usr/bin/env python3
"""§8 ALERT ATTRIBUTION — what proves a group is a host service, and what does
not.

The defect these guard against was measured on this host, not imagined. The
live detector reported 43 OPEN `ORPHAN_PROCESS_TREE` alerts at `high`, and
every subject was the machine's own service inventory — `cron.service`,
`docker.service`, `dbus.service`, `ssh.service`, `systemd-journald.service` and
so on. Reparenting to init is what a systemd service IS: PID1 is its parent by
design, so the signal was true of all of them and meant "this is a daemon" in
all of them.

The fix is an ownership classification read off the cgroup path, which the
kernel and the system manager write and a process cannot spell. Two answers,
and the second is not a failure:

    EXTERNAL_SYSTEM   the system manager started it from an installed unit
    UNKNOWN           nobody can say from the cgroup path

The load-bearing half is UNKNOWN. §8 forbids dressing an unprovable process as
external to quiet an alert, because the only reason a proven system service is
safe to downgrade is that the proof is real. If `user.slice` were swept in
with `system.slice` the classification would stop meaning anything and the
alerts it silenced would include the ones that mattered — so the tests below
spend more effort on what must NOT be called external than on what must.

The first version of the classifier got that wrong, and it was caught by
reading its own output rather than by a test: six records said
`slice=user.slice | unit=user@1002.service` and `class=EXTERNAL_SYSTEM` at the
same time. Two fields of one record disagreeing is the tell. Every per-user
systemd manager creates its own `init.scope` nested inside `user@<uid>.service`
— `/user.slice/user-1002.slice/user@1002.service/init.scope` — and the rule
matched that segment anywhere in the path. `user@1002.service` is this user's
own manager. `test_a_users_systemd_manager_is_not_a_system_service` is the
regression test for exactly that, and it is why the class is now derived from
the same segment `slice_of_cgroup` reports.

Every host read here is synthetic. Nothing in this file touches `/proc`.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    """The house loader. Module names are shared with the rest of the suite so
    every surface reaches one module object rather than one per test file."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


att = _load("serveros_runtime_attribution", "attribution.py")
an = _load("serveros_runtime_anomaly", "anomaly.py")

CONTRACT = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())

# The scopes a reader is most likely to get wrong, written as the kernel writes
# them. `user-1002` is this account; the others are other people on the host.
SYSTEM_UNIT = "/system.slice/docker.service"
PID1 = "/init.scope"
THIS_USER_MANAGER = "/user.slice/user-1002.slice/user@1002.service/init.scope"
OTHER_USER_MANAGER = "/user.slice/user-1042.slice/user@1042.service/init.scope"
THIS_USER_UNIT = "/user.slice/user-1002.slice/user@1002.service/dbus.service"
OTHER_USER_SESSION = "/user.slice/user-1042.slice/session-91.scope"


def ownership(scope: str) -> str:
    return att.ownership_of_scope(scope)["class"]


def codes(signals) -> set[str]:
    return {s["code"] for s in signals}


def by_code(signals, code):
    return [s for s in signals if s["code"] == code]


def group(scope, *, ref="runtime-group:process-tree:900", age=3600, cpu=0.0,
          memory=0, reparented=True, agent=None):
    """One group record shaped as `graph._describe` builds it.

    `ownership` is computed by the classifier rather than written by hand, so
    these tests exercise the real derivation and not a fixture that agrees with
    itself about what the answer should be.
    """
    return {
        "ref": ref,
        "scope": scope,
        "ownership": att.ownership_of_scope(scope),
        "aggregate": {"cpu_percent": cpu, "age_seconds": age,
                      "memory_rss_bytes": memory, "process_count": 1},
        "contributors": [{"pid": 4242}],
        "agent": {"value": agent},
        "tree": {"reparented_to_init": reparented, "root_ppid": 1,
                 "chain_complete": True},
    }


def detect(*groups, **kwargs):
    thresholds = {**an.load_thresholds(), "age_long_seconds": 60}
    return an.detect(groups=list(groups), thresholds=thresholds, **kwargs)


# ---------------------------------------------------------------------------
# What proves ownership
# ---------------------------------------------------------------------------


def test_a_system_unit_is_proven_by_its_slice():
    result = att.ownership_of_scope(SYSTEM_UNIT)
    assert result["class"] == att.EXTERNAL_SYSTEM
    assert result["slice"] == "system.slice"
    assert result["unit"] == "docker.service"


def test_pid1_is_proven_by_the_root_init_scope():
    """PID1's own cgroup. It is not under any slice, so the slice test cannot
    reach it and the scope is the only evidence there is."""
    result = att.ownership_of_scope(PID1)
    assert result["class"] == att.EXTERNAL_SYSTEM


def test_a_proven_owner_names_its_evidence():
    """A reader has to be able to tell a conclusion from a guess."""
    assert att.ownership_of_scope(SYSTEM_UNIT)["provenance"] == "cgroup"
    assert att.ownership_of_scope(PID1)["provenance"] == "cgroup"


# ---------------------------------------------------------------------------
# What does not — the half §8 is actually about
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scope", [THIS_USER_MANAGER, OTHER_USER_MANAGER])
def test_a_users_systemd_manager_is_not_a_system_service(scope):
    """THE REGRESSION. Every per-user systemd manager creates its own
    `init.scope` nested inside `user@<uid>.service`. Matching that segment
    anywhere in the path called six of them host system services, and one of
    them was this user's own manager."""
    assert ownership(scope) == att.UNKNOWN


def test_a_unit_under_user_slice_is_not_proven_by_its_name():
    """`dbus.service` is a system unit in `system.slice` and a user unit in
    `user.slice`. The cgroup is what decides; the name is not consulted, and
    must not be, or this becomes an allowlist that is wrong the day a unit is
    renamed."""
    assert ownership(SYSTEM_UNIT) == att.EXTERNAL_SYSTEM
    assert ownership(THIS_USER_UNIT) == att.UNKNOWN


def test_another_users_session_is_not_this_hosts_service():
    assert ownership(OTHER_USER_SESSION) == att.UNKNOWN


def test_a_name_that_merely_contains_init_scope_is_not_the_root_scope():
    """The first version matched whole path segments and so could not be fooled
    by `foo-init.scope`. Requiring the FIRST segment is the stricter test, and
    this pins that it stayed strict."""
    assert ownership("/foo-init.scope") == att.UNKNOWN
    assert ownership("/system.slice/init.scope.not-really") == att.EXTERNAL_SYSTEM, \
        "a file under system.slice is still under system.slice"


@pytest.mark.parametrize("scope", ["", "0::/", "/", "0::/user.slice"])
def test_an_unreadable_scope_is_unknown(scope):
    assert ownership(scope) == att.UNKNOWN


def test_an_unproven_group_keeps_no_evidence_of_ownership():
    """`provenance` is None rather than the word `unknown`. A later reader that
    compares provenance to a vocabulary finds nothing to compare, which is the
    correct outcome for an absence."""
    result = att.ownership_of_scope(THIS_USER_MANAGER)
    assert result["provenance"] is None
    assert result["class"] == att.UNKNOWN


@pytest.mark.parametrize("scope", [SYSTEM_UNIT, PID1, THIS_USER_MANAGER,
                                   OTHER_USER_MANAGER, THIS_USER_UNIT,
                                   OTHER_USER_SESSION, "", "/"])
def test_the_class_never_contradicts_the_slice(scope):
    """The end-to-end tell, and the check that caught the defect: `slice` is
    the FIRST slice in the path and the class is now derived from the same
    segment, so one record cannot say `user.slice` and `EXTERNAL_SYSTEM` at
    once."""
    result = att.ownership_of_scope(scope)
    if result["class"] == att.EXTERNAL_SYSTEM:
        assert result["slice"] in ("system.slice", None), result
        # not under a slice at all -> must be the root init.scope
        if result["slice"] is None:
            assert scope.split("/")[1:2] == ["init.scope"], scope


# ---------------------------------------------------------------------------
# What the ownership answer does to the alerts
# ---------------------------------------------------------------------------


def test_a_proven_system_service_is_observed_not_accused():
    """Still reported, still in the record, no longer ranked as hygiene and no
    longer grouped with the things a reader is being asked to clean up."""
    signals = detect(group(SYSTEM_UNIT))

    assert codes(signals) == {"EXTERNAL_SYSTEM_PROCESS_TREE", "AGE_WITHOUT_ACTIVITY"}
    observed = by_code(signals, "EXTERNAL_SYSTEM_PROCESS_TREE")[0]
    assert observed["severity"] == "low"
    assert observed["implies_stale"] is False
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    assert observed["measured"]["ownership"]["provenance"] == "cgroup"


def test_an_unattributable_group_is_still_an_orphan():
    signals = detect(group(THIS_USER_SESSION := OTHER_USER_SESSION))

    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"


def test_a_users_systemd_manager_is_not_quieted_as_a_system_service():
    """The defect, end to end. Classifying this as external would have removed
    six alerts on the strength of a path that does not say whose manager it
    is."""
    signals = detect(group(THIS_USER_MANAGER))
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)
    assert "ORPHAN_PROCESS_TREE" in codes(signals)


def test_the_owner_being_known_answers_the_unknown_owner_code():
    """`UNKNOWN_OWNER_LONG_RUNNING` claims no agent rule identifies the group.
    For a system unit the owner IS identified — it is the host's — and no
    agent rule should match it, so the claim is false and the code is not
    emitted. The 34 OPEN alerts on sshd/cron/dbus were this and nothing else."""
    signals = detect(group(SYSTEM_UNIT, agent=None, age=10_000))
    assert "UNKNOWN_OWNER_LONG_RUNNING" not in codes(signals)


def test_the_unknown_owner_code_still_fires_where_nobody_can_say():
    signals = detect(group(OTHER_USER_SESSION, agent=None, age=10_000))
    assert "UNKNOWN_OWNER_LONG_RUNNING" in codes(signals)
    assert by_code(signals, "UNKNOWN_OWNER_LONG_RUNNING")[0]["measured"]["ownership"]["class"] == att.UNKNOWN


def test_a_group_still_parented_is_not_a_process_tree_signal():
    """Ownership must not widen the trigger. A group whose parent is still
    alive is neither an orphan nor an observed system service."""
    signals = detect(group(SYSTEM_UNIT, reparented=False))
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)


def test_ownership_does_not_silence_the_observations_about_resources():
    """AGE_WITHOUT_ACTIVITY and HIGH_MEMORY_SUSTAINED say nothing about an
    owner, so ownership must not touch them. Gating them would be widening the
    rule past the evidence."""
    signals = detect(group(SYSTEM_UNIT, age=10_000, memory=10 ** 12))
    assert "AGE_WITHOUT_ACTIVITY" in codes(signals)
    assert "HIGH_MEMORY_SUSTAINED" in codes(signals)


def test_a_group_with_no_ownership_record_is_not_treated_as_external():
    """Groups built by an older producer carry no `ownership`. Absence is not
    proof, so the reader must answer False rather than raise."""
    assert att.is_external_system({}) is False
    assert att.is_external_system({"ownership": {}}) is False
    assert att.is_external_system({"ownership": {"class": att.UNKNOWN}}) is False
    assert att.is_external_system(group(SYSTEM_UNIT)) is True


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def test_the_contract_declares_the_observe_only_code():
    declared = {a["code"]: a for a in CONTRACT["anomalies"]}
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" in declared
    assert declared["EXTERNAL_SYSTEM_PROCESS_TREE"]["severity"] == an.SEVERITY["EXTERNAL_SYSTEM_PROCESS_TREE"]
    assert declared["EXTERNAL_SYSTEM_PROCESS_TREE"]["implies_stale"] is False


def test_the_contract_ranks_it_below_the_orphan():
    """Observe-only is a ranking claim as well as a semantic one."""
    declared = {a["code"]: a for a in CONTRACT["anomalies"]}
    order = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    assert order[declared["EXTERNAL_SYSTEM_PROCESS_TREE"]["severity"]] \
        < order[declared["ORPHAN_PROCESS_TREE"]["severity"]]


def test_the_new_code_is_gated_by_the_groups_input():
    """A code the detector can emit but the input gate omits is unreachable in
    production and passing in tests."""
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" in an.INPUT_CODES["groups"]
    assert "ORPHAN_PROCESS_TREE" in an.INPUT_CODES["groups"]


def test_the_classifier_is_not_an_allowlist():
    """§8 forbids the per-service allowlist by name.

    A unit name is allowed to appear in prose. `unit_of_cgroup` documents why
    the innermost unit is the right answer to "what supervises this", and
    `dbus.service` is the example it uses — that is a parser being explained,
    not a service being trusted. What must not exist is a service name used as
    a value: a set, tuple or list of names to trust, or a comparison against
    one. The distinction is the reason this scans the syntax tree instead of
    the text, which is what the first version did and what it got wrong.
    """
    tree = ast.parse((RUNTIME / "attribution.py").read_text(encoding="utf-8"))

    prose = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr):
            first = body[0].value
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                prose.add(id(first))

    # A bare `.service` is the suffix a parser tests for, not a service. Only a
    # stem in front of it makes a name.
    used_as_value = [n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str)
                     and n.value.endswith(".service") and n.value != ".service"
                     and id(n) not in prose]
    assert used_as_value == [], f"unit names are used as values: {used_as_value}"
