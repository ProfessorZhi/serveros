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
kernel and the system manager write and a process cannot spell. Two CLASSES,
and the second is not a failure:

    EXTERNAL   the cgroup path itself names an owner that is not this
               deployment's — with a `role` saying which kind
    UNKNOWN    nobody can say from the cgroup path

The load-bearing half is UNKNOWN. §8 forbids dressing an unprovable process as
external to quiet an alert, because the only reason an attributed group is
safe to downgrade is that the evidence is real. A scope in a slice this file
knows nothing about stays UNKNOWN, and so does a group whose producer recorded
no scope at all — so the tests below spend more effort on what must NOT be
called external than on what must.

WHY THE ANSWER CHANGED ON 2026-09-17. The first version answered
EXTERNAL_SYSTEM or UNKNOWN and put all of `user.slice` in the second, on the
reasoning that it holds every user's sessions and does not say whose. That is
true of the bare `user.slice` segment and FALSE of everything under it:
`user-1002.slice` names the uid and `session-<N>.scope` names the login, both
written by the kernel, and `user_of_cgroup`/`session_of_cgroup` were already in
this file reading those segments for other callers. The rule was declining to
read evidence it already knew how to read, and the cost was that every
per-user systemd manager on the host — six of them — was reported as an
ORPHAN: a group the cgroup attributes perfectly well, filed as unattributable.

The role names carry the distinction one string could not. A per-user manager
is EXTERNAL_USER_MANAGER and never EXTERNAL_SYSTEM, so
`test_a_users_systemd_manager_is_not_a_system_service` — which is about the
system-service claim and never was about the UNKNOWN claim — now asserts that
directly instead of asserting UNKNOWN as the only available way to say it.

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
# A runtime grouping under somebody's live user instance — the shape tmux's
# spawn helper and `systemd-run --scope` both leave behind.
USER_RUNTIME_SCOPE = ("/user.slice/user-1042.slice/user@1042.service/"
                      "tmux-spawn-abc.scope")

# A slice this file has no evidence about. Not present on the live host — every
# cgroup there is under `system.slice`, `user.slice`, `/init.scope`, `/`, or no
# slice at all — which is the point: the UNKNOWN answer has to be reachable
# without the machine happening to supply an example, or the guard against
# over-attributing is untested until the day it matters.
OTHER_SLICE = "/machine.slice/libvirtd.service"

# A slice nested under system.slice. `slice_of_cgroup` reports the FIRST slice
# in the path, so this is a host service and the nesting does not change that.
NESTED_SYSTEM = "/system.slice/system-getty.slice/getty@tty1.service"

# The root cgroup. PID2 and the kernel threads live here, and `/YunJing/YDEyes`
# is the same shape for a vendor agent: no slice at all.
ROOT_CGROUP = "/"
VENDOR_AGENT = "/YunJing/YDEyes"

# Where this deployment's own daemon actually runs. Not a hypothetical: this
# is the cgroup `serverosd` was observed under, and it is why the MANAGED
# class exists — the cgroup names a manager, but the membership claim the
# EXTERNAL class carries was false for it. Same uid and same position as the
# next fixture, different unit, opposite answer.
THIS_DEPLOYMENT_UNIT = (
    "/user.slice/user-1002.slice/user@1002.service/app.slice/serverosd.service")
# A unit under the SAME manager for the SAME uid that this deployment does not
# ship. Nothing in the path tells the two apart; the declaration does.
FOREIGN_UNIT_SAME_PLACE = (
    "/user.slice/user-1002.slice/user@1002.service/app.slice/gh-runner-quay.service")


def ownership(scope: str) -> str:
    return att.ownership_of_scope(scope)["class"]


def codes(signals) -> set[str]:
    return {s["code"] for s in signals}


def by_code(signals, code):
    return [s for s in signals if s["code"] == code]


def group(scope, *, ref="runtime-group:process-tree:900", age=3600, cpu=0.0,
          memory=0, reparented=True, agent=None, owns_its_session=None,
          instance_live=None):
    """One group record shaped as `graph._describe` builds it.

    `ownership` is computed by the classifier rather than written by hand, so
    these tests exercise the real derivation and not a fixture that agrees with
    itself about what the answer should be.

    `owns_its_session` defaults to None, which leaves
    `root_owns_its_session` OUT of the tree entirely — the shape a group read
    by an older collector has, and the shape most of this file wants, since
    most of these tests are about the cgroup half. Say True or False
    explicitly when the test is about the process half.
    """
    tree = {"reparented_to_init": reparented, "root_ppid": 1,
            "chain_complete": True}
    if owns_its_session is not None:
        tree["root_owns_its_session"] = owns_its_session
        tree["root_session"] = 4242 if owns_its_session else 999999
    if instance_live is not None:
        tree["owner_instance_live"] = instance_live
    return {
        "ref": ref,
        "scope": scope,
        "ownership": att.ownership_of_scope(scope),
        "aggregate": {"cpu_percent": cpu, "age_seconds": age,
                      "memory_rss_bytes": memory, "process_count": 1},
        "contributors": [{"pid": 4242}],
        "agent": {"value": agent},
        "tree": tree,
    }


def detect(*groups, **kwargs):
    thresholds = {**an.load_thresholds(), "age_long_seconds": 60}
    return an.detect(groups=list(groups), thresholds=thresholds, **kwargs)


# ---------------------------------------------------------------------------
# What proves ownership
# ---------------------------------------------------------------------------


def test_a_system_unit_is_proven_by_its_slice():
    result = att.ownership_of_scope(SYSTEM_UNIT)
    assert result["class"] == att.EXTERNAL
    assert result["role"] == att.ROLE_EXTERNAL_SYSTEM
    assert result["slice"] == "system.slice"
    assert result["unit"] == "docker.service"


def test_pid1_is_proven_by_the_root_init_scope():
    """PID1's own cgroup. It is not under any slice, so the slice test cannot
    reach it and the scope is the only evidence there is."""
    result = att.ownership_of_scope(PID1)
    assert result["class"] == att.EXTERNAL
    assert result["role"] == att.ROLE_EXTERNAL_SYSTEM


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
    them was this user's own manager.

    The claim this test protects is "never a SYSTEM service", and it is now
    asserted directly. It used to be expressed as `class == UNKNOWN` because
    the classifier had only two answers and the other one was wrong — which
    meant the test was also quietly asserting that these six groups were
    unattributable. They are not: `user-1002.slice` names the uid. That extra
    claim is gone; the one that matters is asserted harder, and the uid is
    asserted to be on the record.
    """
    result = att.ownership_of_scope(scope)
    assert result["role"] == att.ROLE_EXTERNAL_USER_MANAGER
    assert result["role"] != att.ROLE_EXTERNAL_SYSTEM
    assert result["class"] == att.EXTERNAL
    assert result["uid"] == scope.split("user-")[1].split(".")[0]
    assert att.is_external_system({"ownership": result}) is False


def test_a_unit_under_user_slice_is_not_proven_by_its_name():
    """`dbus.service` is a system unit in `system.slice` and a user unit in
    `user.slice`. The cgroup is what decides; the name is not consulted, and
    must not be, or this becomes an allowlist that is wrong the day a unit is
    renamed. One name, two answers — which is the assertion that shows the
    name was never read."""
    assert att.ownership_of_scope(SYSTEM_UNIT)["role"] == att.ROLE_EXTERNAL_SYSTEM
    assert att.ownership_of_scope(THIS_USER_UNIT)["role"] == \
        att.ROLE_EXTERNAL_USER_MANAGER


def test_another_users_session_is_not_this_hosts_service():
    """A session scope the kernel created for a login. It is not the host's
    service — and it is not unattributable either, which is the half the old
    answer got wrong. The uid is on the record because `user-1042.slice`
    names it, and that is what makes this a tenant's session rather than a
    mystery."""
    result = att.ownership_of_scope(OTHER_USER_SESSION)
    assert result["role"] == att.ROLE_EXTERNAL_USER_SESSION
    assert result["uid"] == "1042"
    assert result["session"] == "91"
    assert att.is_external_system({"ownership": result}) is False


def test_a_name_that_merely_contains_init_scope_is_not_the_root_scope():
    """The first version matched whole path segments and so could not be fooled
    by `foo-init.scope`. Requiring the FIRST segment is the stricter test, and
    this pins that it stayed strict."""
    assert att.ownership_of_scope("/foo-init.scope")["role"] != \
        att.ROLE_EXTERNAL_SYSTEM
    assert att.ownership_of_scope(
        "/system.slice/init.scope.not-really")["role"] == \
        att.ROLE_EXTERNAL_SYSTEM, \
        "a file under system.slice is still under system.slice"


@pytest.mark.parametrize("scope", ["", "0::/", "0::/user.slice", "garbage"])
def test_an_unreadable_scope_is_unknown(scope):
    """`""` and the raw `0::/...` lines are not cgroup paths, so they are not
    evidence about an owner and are not answered as if they were. `/` used to
    be in this list and is deliberately no longer: the root cgroup IS a
    measurement, and it is PID2's."""
    assert ownership(scope) == att.UNKNOWN


@pytest.mark.parametrize("scope", [ROOT_CGROUP, VENDOR_AGENT])
def test_an_unsliced_cgroup_is_attributed_by_the_absence_of_a_slice(scope):
    """The system manager puts everything it starts in a slice, so a cgroup
    with no slice was not started by it. That is a fact the kernel recorded
    — the ABSENCE of a slice — and it is all this answer claims. It does not
    claim "kernel thread": `ProcessFacts` carries no such flag, and inferring
    one from the cgroup would be a guess wearing a conclusion's name."""
    result = att.ownership_of_scope(scope)
    assert result["class"] == att.EXTERNAL
    assert result["role"] == att.ROLE_EXTERNAL_UNSLICED
    assert result["slice"] is None
    assert att.is_external_system({"ownership": result}) is False


def test_a_slice_nested_under_system_slice_is_still_a_host_service():
    """`slice_of_cgroup` reports the FIRST slice, and `system-getty.slice`
    sits inside `system.slice`, so a rule that read the LAST slice would miss
    this one. It exists on the live host; the fixture is its real path."""
    assert att.ownership_of_scope(NESTED_SYSTEM)["role"] == att.ROLE_EXTERNAL_SYSTEM


def test_a_slice_with_no_evidence_stays_unknown():
    """§8's line. Nothing here knows what `machine.slice` is, so nothing here
    says. `provenance` is None rather than the word `unknown` — a later reader
    that compares provenance to a vocabulary finds nothing to compare, which
    is the right outcome for an absence."""
    result = att.ownership_of_scope(OTHER_SLICE)
    assert result["class"] == att.UNKNOWN
    assert result["role"] is None
    assert result["lifecycle"] is None
    assert result["provenance"] is None


def test_no_scope_is_not_the_same_answer_as_no_idea():
    """`""` and `machine.slice` are both UNKNOWN but they are not the same
    fact, and the `why` says which. The "carries no cgroup scope" text was
    written for the empty case and was unreachable until the guard above was
    added — the fallthrough answered it with "not under system.slice"."""
    empty = att.ownership_of_scope("")["why"]
    foreign = att.ownership_of_scope(OTHER_SLICE)["why"]
    assert "no cgroup scope" in empty, empty
    assert "no cgroup scope" not in foreign, foreign


def test_an_unproven_group_keeps_no_evidence_of_ownership():
    """`provenance` is None rather than the word `unknown`. A later reader that
    compares provenance to a vocabulary finds nothing to compare, which is the
    correct outcome for an absence."""
    result = att.ownership_of_scope(OTHER_SLICE)
    assert result["provenance"] is None
    assert result["class"] == att.UNKNOWN


@pytest.mark.parametrize("scope", [SYSTEM_UNIT, PID1, THIS_USER_MANAGER,
                                   OTHER_USER_MANAGER, THIS_USER_UNIT,
                                   OTHER_USER_SESSION, OTHER_SLICE, "", "/",
                                   VENDOR_AGENT, NESTED_SYSTEM])
def test_the_role_never_contradicts_the_slice(scope):
    """The end-to-end tell, and the check that caught the defect: one record
    cannot say `user.slice` and `EXTERNAL_SYSTEM` at once.

    With roles the check gets stricter rather than looser. It used to read
    "class EXTERNAL_SYSTEM implies no user slice"; now each role pins the
    exact segment that backs it, so a role and a slice disagreeing in EITHER
    direction fails."""
    result = att.ownership_of_scope(scope)
    role, slice_name = result["role"], result["slice"]

    if role == att.ROLE_EXTERNAL_SYSTEM:
        assert slice_name in ("system.slice", None), result
        if slice_name is None:
            # not under a slice -> must be the root init.scope, not a nested one
            assert scope.split("/")[1:2] == ["init.scope"], scope
    elif role == att.ROLE_EXTERNAL_USER_MANAGER:
        assert slice_name == "user.slice", result
        assert result["session"] is None, result
    elif role == att.ROLE_EXTERNAL_USER_SESSION:
        assert slice_name == "user.slice", result
        assert result["session"], result
    elif role == att.ROLE_EXTERNAL_UNSLICED:
        assert slice_name is None, result
    else:
        assert role is None and result["class"] == att.UNKNOWN, result


def test_every_external_answer_is_observe_only():
    """`lifecycle` rides with the answer so a consumer cannot mistake
    "attributed" for "actionable". Nothing in this file reclaims, cleans up or
    mutates, and the field is what says so on the record."""
    for scope in (SYSTEM_UNIT, PID1, THIS_USER_MANAGER, OTHER_USER_SESSION,
                  ROOT_CGROUP, VENDOR_AGENT):
        result = att.ownership_of_scope(scope)
        assert result["class"] == att.EXTERNAL, scope
        assert result["lifecycle"] == att.OBSERVE_ONLY, scope
    for scope in (OTHER_SLICE, ""):
        assert att.ownership_of_scope(scope)["lifecycle"] is None, scope


@pytest.mark.parametrize("scope", [SYSTEM_UNIT, PID1, THIS_USER_MANAGER,
                                   OTHER_USER_SESSION, ROOT_CGROUP,
                                   VENDOR_AGENT])
def test_every_external_answer_quotes_its_evidence(scope):
    """An EXTERNAL answer with no `why` is a conclusion with nothing behind
    it, which is the shape §8 forbids. Every one of them has to say what it
    read."""
    result = att.ownership_of_scope(scope)
    assert result["provenance"] == "cgroup", scope
    assert result["why"] and len(result["why"]) > 30, scope


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
    """§8's line, end to end. A group nobody can attribute is exactly as
    alarming as it was before this round; the attribution work did not touch
    it."""
    signals = detect(group(OTHER_SLICE))

    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"


def test_a_users_systemd_manager_is_not_quieted_as_a_system_service():
    """The defect, end to end, and the claim is unchanged: this must never be
    reported as the host's own service.

    What changed is what happens instead. It used to stay ORPHAN_PROCESS_TREE
    at `high` — an alert asking a reader to go and clean up a login manager
    that the cgroup attributes perfectly well. It is now reported at `low` as
    EXTERNAL_PROCESS_TREE, observe-only, with the uid it was read from on the
    evidence. Nothing was silenced: the code is different, the group is still
    in the record, and the reader can still see it.
    """
    signals = detect(group(THIS_USER_MANAGER))
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    observed = by_code(signals, "EXTERNAL_PROCESS_TREE")[0]
    assert observed["severity"] == "low"
    assert observed["implies_stale"] is False
    assert observed["measured"]["ownership"]["role"] == \
        att.ROLE_EXTERNAL_USER_MANAGER
    assert observed["measured"]["ownership"]["uid"] == "1002"
    assert observed["measured"]["ownership"]["lifecycle"] == att.OBSERVE_ONLY


def test_the_owner_being_known_answers_the_unknown_owner_code():
    """`UNKNOWN_OWNER_LONG_RUNNING` claims no agent rule identifies the group.
    For a system unit the owner IS identified — it is the host's — and no
    agent rule should match it, so the claim is false and the code is not
    emitted. The 34 OPEN alerts on sshd/cron/dbus were this and nothing else."""
    signals = detect(group(SYSTEM_UNIT, agent=None, age=10_000))
    assert "UNKNOWN_OWNER_LONG_RUNNING" not in codes(signals)


def test_the_unknown_owner_code_still_fires_where_nobody_can_say():
    signals = detect(group(OTHER_SLICE, agent=None, age=10_000))
    assert "UNKNOWN_OWNER_LONG_RUNNING" in codes(signals)
    assert by_code(signals, "UNKNOWN_OWNER_LONG_RUNNING")[0]["measured"]["ownership"]["class"] == att.UNKNOWN


def test_the_unknown_owner_code_clears_for_an_attributed_group():
    """Same claim as the system-unit case above, one role over: for a login
    session the owner IS identified — it is a person's — and no agent rule
    should match it, so the code's premise is false and it is not emitted.
    This is the code that was mislabelling host systemd services as UNKNOWN,
    so each role that answers it is checked rather than assumed."""
    for scope in (THIS_USER_MANAGER, OTHER_USER_SESSION, ROOT_CGROUP):
        signals = detect(group(scope, agent=None, age=10_000))
        assert "UNKNOWN_OWNER_LONG_RUNNING" not in codes(signals), scope


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


def test_the_wider_reader_answers_for_every_attributed_group():
    """`is_external_system` and `is_external` answer two different questions
    and must not drift into each other. The narrow one is "is this the host's
    own service"; the wide one, which the hygiene alerts use, is "is this
    ours at all". Every system group is both; every other attributed group is
    only the second; an unattributable group is neither."""
    assert att.is_external(group(SYSTEM_UNIT)) is True
    assert att.is_external(group(THIS_USER_MANAGER)) is True
    assert att.is_external(group(OTHER_USER_SESSION)) is True
    assert att.is_external(group(ROOT_CGROUP)) is True
    assert att.is_external_system(group(THIS_USER_MANAGER)) is False
    assert att.is_external_system(group(OTHER_USER_SESSION)) is False
    assert att.is_external(group(OTHER_SLICE)) is False
    assert att.is_external({}) is False


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
    assert "EXTERNAL_PROCESS_TREE" in an.INPUT_CODES["groups"]
    assert "ORPHAN_PROCESS_TREE" in an.INPUT_CODES["groups"]


def test_the_contract_declares_the_widened_observe_only_code():
    """The new code is not an exception to the contract rules: it is declared,
    ranked below the orphan, and never stale."""
    declared = {a["code"]: a for a in CONTRACT["anomalies"]}
    assert "EXTERNAL_PROCESS_TREE" in declared
    assert declared["EXTERNAL_PROCESS_TREE"]["severity"] == \
        an.SEVERITY["EXTERNAL_PROCESS_TREE"] == "low"
    assert declared["EXTERNAL_PROCESS_TREE"]["implies_stale"] is False


def test_a_session_leftover_keeps_the_high_severity_signal():
    """THE ONE THAT MATTERS. A process in a login's session scope whose parent
    is PID1 has outlived its session leader. It is attributed — the role says
    whose session, so a reader is not left guessing — and it is STILL an
    orphan at `high`, because being attributed is not the same as being
    somebody's deliberate daemon.

    This nearly shipped the other way. The first version of this round
    exempted every attributed group, and `test_view.py` caught it: a codex
    process burning 4000 ticks out of a dead session dropped to `low`, with
    RUNTIME_OUTLIVED_OWNER not covering it (it needs the session to still
    report `closing`, and a session that is gone reports nothing). The
    contract's 2026-09-13 case is exactly this shape.
    """
    # `owns_its_session=False` is the leftover shape read from the kernel:
    # the root carries a session id that is not its own, because whoever led
    # that session is gone. Asserted explicitly rather than left out of the
    # fixture, so this test proves the discriminator answers "leftover"
    # instead of proving that a missing field fails closed.
    signals = detect(group(OTHER_USER_SESSION, owns_its_session=False))
    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)
    orphan = by_code(signals, "ORPHAN_PROCESS_TREE")[0]
    assert orphan["severity"] == "high"
    assert orphan["measured"]["ownership"]["role"] == \
        att.ROLE_EXTERNAL_USER_SESSION
    assert orphan["measured"]["ownership"]["uid"] == "1042"
    assert orphan["measured"]["detached_by_design"] is False
    assert orphan["measured"]["root_session"] == 999999


def test_a_login_session_that_detached_on_purpose_is_not_an_orphan():
    """The case that made the renderer look broken: a live `tmux` server, the
    editor's server process, a bot multiplexer — all of them real daemons,
    all of them sitting in a `session-<N>.scope` with PID1 as their parent.

    The scope name cannot tell them from the leftover above, and the role
    cannot either: they belong to the same login. What separates them is
    `/proc/<pid>/stat` field 6. A `setsid()` caller is its own session
    leader, so its session id IS its pid — that IS what daemonising means.

    Measured on `device:tokyo-shared-dev-01`, 2026-09-18: `tmux: server` pid
    328129, `botmux` 1444974 and two VS Code server processes each with
    sid == pid; three `sh` with a sid whose pid was gone.
    """
    signals = detect(group(OTHER_USER_SESSION, owns_its_session=True))
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_PROCESS_TREE" in codes(signals)
    external = by_code(signals, "EXTERNAL_PROCESS_TREE")[0]
    assert external["severity"] == "low"
    # The cgroup half is still reported, and still true: a reader can see
    # whose login it detached from. Only the conclusion changed.
    assert external["measured"]["ownership"]["role"] == \
        att.ROLE_EXTERNAL_USER_SESSION
    assert external["measured"]["detached_by_design"] is True
    assert "setsid" in external["why"]


def test_a_group_with_no_session_evidence_keeps_the_orphan_alert():
    """Absent evidence is not the same as absent risk, and this is the
    direction that decides it. A session-scope group whose tree carries no
    `root_owns_its_session` — an older collector, a partial read — keeps
    ORPHAN_PROCESS_TREE at `high`.

    Went the other way in the first draft of this round, which exempted a
    session scope as soon as the role matched. That would have been a name
    test wearing a cgroup's clothes: every leftover on the host is in a
    session scope too.
    """
    signals = detect(group(OTHER_USER_SESSION))
    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)


def test_detached_by_design_reads_the_tree_and_nothing_else():
    """The predicate on its own, including the two shapes that must be False.

    No name is consulted anywhere in it, so no amount of process naming can
    widen the exemption.
    """
    assert att.detached_by_design(group(OTHER_USER_SESSION,
                                        owns_its_session=True)) is True
    assert att.detached_by_design(group(OTHER_USER_SESSION,
                                        owns_its_session=False)) is False
    assert att.detached_by_design(group(OTHER_USER_SESSION)) is False
    assert att.detached_by_design({"scope": OTHER_USER_SESSION}) is False
    assert att.detached_by_design({}) is False

    # A truthy-but-not-True value from a JSON round trip still works, and a
    # falsey one does not sneak through as True.
    assert att.detached_by_design(
        {"tree": {"root_owns_its_session": 1}}) is True
    assert att.detached_by_design(
        {"tree": {"root_owns_its_session": None}}) is False

    # It is asked of the process, so the cgroup class does not gate it: the
    # caller decides when the question is worth asking.
    assert att.detached_by_design(
        {"tree": {"root_owns_its_session": True}}) is True


def test_a_runtime_scope_under_a_live_instance_is_not_an_orphan():
    """The second shape with this defect, and the reason it is a separate
    predicate rather than a wider `detached_by_design`.

    A `.scope` is never backed by a unit file and systemd does not fork what
    runs in it: `systemd-run --scope` registers the scope around a process it
    started and then exits, so the payload has PPID 1 from the moment the
    command returns. Its instance is still running the whole time, and
    `init.scope` under that instance is the kernel's record of that.

    Measured on `device:tokyo-shared-dev-01`, 2026-09-18: `tmux-spawn-*.scope`
    under a live `user@1004.service`, carrying yale's live `claude.exe`
    sessions, with both cgroup directories present.

    Note what is NOT required here: the root need not have called `setsid`.
    The scope's payload reparents to PID1 because its creator exited, not
    because it made itself a session leader, so asking that question would
    reject groups for a reason that is not the reason.
    """
    signals = detect(group(USER_RUNTIME_SCOPE, owns_its_session=False,
                           instance_live=True))
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_PROCESS_TREE" in codes(signals)
    external = by_code(signals, "EXTERNAL_PROCESS_TREE")[0]
    assert external["severity"] == "low"
    assert external["measured"]["ownership"]["role"] == \
        att.ROLE_EXTERNAL_USER_MANAGER
    assert external["measured"]["owner_instance_live"] is True


def test_a_runtime_scope_whose_instance_is_gone_is_still_an_orphan():
    """The half that keeps this honest. A scope whose `init.scope` is not in
    the table is a scope whose instance is not running, and the alert stands
    at `high` — which is also where a short table lands."""
    signals = detect(group(USER_RUNTIME_SCOPE, instance_live=False))
    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)

    # And with no evidence at all in the tree, which is the older-collector
    # shape. Fail closed.
    signals = detect(group(USER_RUNTIME_SCOPE))
    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"


def test_a_login_session_under_a_live_instance_keeps_its_own_rule():
    """The exclusion that matters most. A `session-<N>.scope` is also a
    runtime scope under the very same live instance, so a predicate that only
    asked "is the instance alive" would exempt every leftover on the host —
    the exact false negative the session tests above were written to prevent.

    A login ends. A scope someone made does not. Those are different facts
    and the cgroup path is where the kernel records the difference.
    """
    signals = detect(group(OTHER_USER_SESSION, owns_its_session=False,
                           instance_live=True))
    assert "ORPHAN_PROCESS_TREE" in codes(signals)
    assert by_code(signals, "ORPHAN_PROCESS_TREE")[0]["severity"] == "high"
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)


def test_scope_of_a_live_instance_answers_only_for_runtime_scopes():
    """The predicate on its own: what it accepts, what it refuses, and why.

    Every refusal below is a shape that would otherwise be exempted by a
    predicate that was looser about the path — and each one has its own rule
    that is stricter for a reason.
    """
    def sc(scope, live):
        return att.scope_of_a_live_instance(group(scope, instance_live=live))

    assert sc(USER_RUNTIME_SCOPE, True) is True
    assert sc(USER_RUNTIME_SCOPE, False) is False
    assert sc(USER_RUNTIME_SCOPE, None) is False

    # The manager's own scope: already exempt, and exempt for its own reason.
    assert sc(THIS_USER_MANAGER, True) is False
    # A service below the instance: its parent IS the manager, so PPID 1
    # means the manager is gone. Its pending deletion is what that rule is.
    assert sc(THIS_USER_UNIT, True) is False
    # A login: its own rule, and the one case that must never be widened.
    assert sc(OTHER_USER_SESSION, True) is False
    # Not under a user instance at all.
    assert sc(SYSTEM_UNIT, True) is False
    assert sc("", True) is False

    # Nothing here reads a process name.
    assert att.scope_of_a_live_instance(
        {"scope": USER_RUNTIME_SCOPE, "tree": {"owner_instance_live": 1}}) is True
    assert att.scope_of_a_live_instance({}) is False


def test_our_own_daemon_is_ours_and_not_somebody_elses():
    """The cgroup says which manager started it. That is true and it is not the
    question. `EXTERNAL` claims membership — "not this deployment's" — and for
    the ServerOS daemon that claim is false. It was answered EXTERNAL_USER_MANAGER
    when this round started, and UNKNOWN before it, and neither is an answer
    about a process we started ourselves."""
    own = att.ownership_of_scope(THIS_DEPLOYMENT_UNIT)
    assert own["class"] == att.MANAGED
    assert own["role"] == att.ROLE_MANAGED_UNIT
    assert own["unit"] == "serverosd.service"
    assert att.is_managed(group(THIS_DEPLOYMENT_UNIT)) is True
    assert att.is_external(group(THIS_DEPLOYMENT_UNIT)) is False
    assert att.is_external_system(group(THIS_DEPLOYMENT_UNIT)) is False


def test_the_same_place_is_not_the_same_answer():
    """Position proves the manager, not the membership. A unit this deployment
    does not ship, under the same manager and the same uid, is still external
    — otherwise the MANAGED answer would be "anything under our own user",
    which is exactly the uid-shaped guess §8 forbids."""
    assert att.ownership_of_scope(FOREIGN_UNIT_SAME_PLACE)["class"] == \
        att.EXTERNAL
    assert att.ownership_of_scope(FOREIGN_UNIT_SAME_PLACE)["role"] == \
        att.ROLE_EXTERNAL_USER_MANAGER
    assert att.is_managed(group(FOREIGN_UNIT_SAME_PLACE)) is False


def test_the_registry_is_the_deployment_own_tree():
    """Read from the tree, not typed. If the set were written by hand this test
    would pass for the wrong reason, so it asserts the entry comes from a file
    that is actually there and that the answer follows the file."""
    units = att.deployment_units()
    assert "serverosd.service" in units
    assert units == {p.name for p in (RUNTIME).glob("*.service")}
    # A unit that is not shipped is not in the set, by construction rather
    # than by this test's opinion.
    assert "gh-runner-quay.service" not in units


def test_a_managed_group_is_never_an_orphan_and_never_unknown():
    """§1 lists "not managed" as a precondition of ORPHAN_PROCESS_TREE. Our own
    daemon reparented to init is a restart, not a leftover — and its restart is
    already somebody's job."""
    signals = detect(group(THIS_DEPLOYMENT_UNIT, reparented=True, age=999999))
    assert "ORPHAN_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_PROCESS_TREE" not in codes(signals)
    assert "EXTERNAL_SYSTEM_PROCESS_TREE" not in codes(signals)
    assert "UNKNOWN_OWNER_LONG_RUNNING" not in codes(signals)


def test_every_managed_answer_is_observe_only_and_quotes_its_evidence():
    """MANAGED says who, not what to do. It rides the same lifecycle as every
    other attributed answer, and like them it must quote what it read."""
    own = att.ownership_of_scope(THIS_DEPLOYMENT_UNIT)
    assert own["lifecycle"] == att.OBSERVE_ONLY
    assert own["provenance"] == "deployment-manifest"
    assert "serverosd.service" in own["why"]
    assert own["why"].strip()


def test_an_empty_registry_makes_the_classifier_quieter_not_louder():
    """The failure direction. If the tree cannot be read there is no MANAGED
    answer, and the scope falls back to the cgroup rules instead of being
    claimed — the same rule the contract states as
    `foreign_actor: {fails_toward: not_foreign}`."""
    saved = att._DEPLOYMENT_UNITS
    try:
        att._DEPLOYMENT_UNITS = frozenset()
        assert att.is_managed(group(THIS_DEPLOYMENT_UNIT)) is False
        assert att.ownership_of_scope(THIS_DEPLOYMENT_UNIT)["class"] == \
            att.EXTERNAL
    finally:
        att._DEPLOYMENT_UNITS = saved


def test_exactly_three_roles_are_exempt_from_the_orphan_signal():
    """Pins the membership so adding a role to the exemption is a decision
    somebody makes rather than a side effect of adding a role. A session is
    the one that must never be added.

    Note what this does and does not say. The tuple lists roles, and the
    session role is still not in it. A session group CAN be exempt — but
    through `detached_by_design`, which reads the process rather than the
    scope, and answers False when there is nothing to read. So the exemption
    is never a property of the role, and the loop below holds: a session
    scope with no session evidence in its tree is still False."""
    assert set(att.PARENTED_TO_INIT_BY_DESIGN) == {
        att.ROLE_EXTERNAL_SYSTEM,
        att.ROLE_EXTERNAL_USER_MANAGER,
        att.ROLE_EXTERNAL_UNSLICED,
    }
    assert att.ROLE_EXTERNAL_USER_SESSION not in att.PARENTED_TO_INIT_BY_DESIGN

    for scope in (SYSTEM_UNIT, PID1, THIS_USER_MANAGER, ROOT_CGROUP,
                  VENDOR_AGENT):
        assert att.parented_to_init_by_design(group(scope)) is True, scope
    for scope in (OTHER_USER_SESSION, THIS_USER_UNIT, OTHER_SLICE, ""):
        assert att.parented_to_init_by_design(group(scope)) is False, scope


def test_a_user_manager_exemption_covers_the_manager_and_not_its_units():
    """The role is the same for both — they are both the user manager's — so
    the role cannot carry this. `user@1002.service` is a unit PID1 starts; the
    `dbus.service` underneath it was started by the user's instance. Parented
    to PID1 therefore means two different things, and only one of them is by
    design."""
    assert att.parented_to_init_by_design(group(THIS_USER_MANAGER)) is True
    assert att.parented_to_init_by_design(group(THIS_USER_UNIT)) is False
    assert att.ownership_of_scope(THIS_USER_MANAGER)["role"] == \
        att.ownership_of_scope(THIS_USER_UNIT)["role"] == \
        att.ROLE_EXTERNAL_USER_MANAGER

    assert "ORPHAN_PROCESS_TREE" in codes(detect(group(THIS_USER_UNIT)))
    assert "EXTERNAL_PROCESS_TREE" not in codes(detect(group(THIS_USER_UNIT)))


def test_the_wider_reader_is_not_the_narrow_one():
    """`is_external` answers "who owns this"; `parented_to_init_by_design`
    answers "is PID1 being the parent explained". Using the first for the
    second is the defect this pair of functions now prevents."""
    for scope in (THIS_USER_MANAGER, OTHER_USER_SESSION):
        assert att.is_external(group(scope)) is True, scope
    assert att.parented_to_init_by_design(group(THIS_USER_MANAGER)) is True
    assert att.parented_to_init_by_design(group(OTHER_USER_SESSION)) is False


def test_the_detector_never_emits_the_two_observe_only_codes_together():
    """One group, one owner, one code. If both fired the reader would be told
    two different things about the same cgroup, which is the failure mode the
    roles exist to prevent."""
    for scope in (SYSTEM_UNIT, THIS_USER_MANAGER, OTHER_USER_SESSION,
                  ROOT_CGROUP, VENDOR_AGENT, OTHER_SLICE):
        found = codes(detect(group(scope)))
        assert not ({"EXTERNAL_SYSTEM_PROCESS_TREE", "EXTERNAL_PROCESS_TREE"}
                    <= found), (scope, found)
        assert not ({"ORPHAN_PROCESS_TREE"} & found
                    and {"EXTERNAL_SYSTEM_PROCESS_TREE",
                         "EXTERNAL_PROCESS_TREE"} & found), (scope, found)


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
