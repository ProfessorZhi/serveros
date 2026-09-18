#!/usr/bin/env python3
"""Runtime attribution: which piece of work does this process belong to.

Why this module exists
----------------------
On 2026-09-13 this host had a `codex app-server` tree consuming 407% of eight
cores for one day and eleven hours. No person was in front of it. Everything
needed to explain it was already on the machine:

    /proc/178978/stat      parent is 178963
    /proc/178963/stat      parent is 1 — reparented, its owner is gone
    /proc/178963/cgroup    /user.slice/user-1006.slice/session-2382.scope
    loginctl session 2382  Remote=yes, RemoteHost=203.0.113.251, sshd,
                           State=closing

Four sources of record, none of them consulted together, so the answer to
"whose work is this?" was not available. `ps` shows PIDs. It cannot show
ownership, and ownership is the only thing that turns "a process is busy"
into a decision.

What this module refuses to do
------------------------------
It does not diagnose. It reports that the cgroup of a PID names session-2382
and that session-2382 is closing. It does NOT report that a task is stale,
or that something is a bug. Those are conclusions; conclusions belong to an
agent that can be asked to justify them. §12 draws that line, and it is drawn
here by construction — there is no return value in this file that carries a
cause.

It does not guess. Every field it produces is either read from a source of
record, derived by a rule it names, or UNKNOWN. There is no path from a
plausible-looking name to an owner.

Read-only
---------
Every host method reads. Nothing here writes, signals, or opens a socket. The
`LiveHost` class is the only thing that touches the machine, and it is
injectable so that a test never has to edit the machine it tests.

Visibility — read this before believing an absence
--------------------------------------------------
This host mounts `/proc` with `hidepid=invisible`. The module can see THIS
UID's processes and no others; PID 1 is not merely unowned but unreadable, and
`ps -e` returns the same 50-odd rows. Verified 2026-09-13:

    $ mount | grep 'proc '
    proc on /proc type proc (rw,relatime,hidepid=invisible)
    $ head -c 1 /proc/1/stat
    No such file or directory

So a process this module cannot find is UNKNOWN, never ABSENT. "No root
process holds a listening socket" is not a finding this code can produce, and
a caller that reads `peers: []` as "nothing is connected" is reading a
permission boundary as a measurement. That distinction has already cost this
host once, in the other direction: an unreadable socket owner was recorded as
"no process owns this" when the correct reading was "root owns this" — which
is what an sshd forwarding channel and a docker-proxy both look like.

`coverage()` reports the boundary explicitly so that a consumer can see the
size of the blind spot rather than inferring it from an empty list.
"""
from __future__ import annotations

import importlib.util
import os
import re
import socket
import struct
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

CONTRACT = _HERE / "attribution-contract.yaml"

# Kernel clock tick. `utime`/`stime` in /proc/<pid>/stat are in these units.
try:
    CLK_TCK = os.sysconf("SC_CLK_TCK")
except (ValueError, OSError):  # pragma: no cover - not reachable on Linux
    CLK_TCK = 100

_DEFAULT_TIMEOUT = 8.0

# Epistemic status. See attribution-contract.yaml → epistemic.
FACT = "FACT"
INFERRED = "INFERRED"
UNKNOWN = "UNKNOWN"

# Provenance classes that may decide an owner on their own.
DECISIVE_PROVENANCE = ("docker-label", "registry-match", "cgroup", "systemd-unit")


class AttributionError(Exception):
    """The host could not be read at all. Distinct from a field being UNKNOWN.

    A field that is UNKNOWN is an answer about one thing. This is the module
    saying it could not look, which is a different fact and must not be
    rendered as an empty record — the same rule `serverosctl` already applies
    with its `EXIT_COULD_NOT_ANSWER`.
    """


# ---------------------------------------------------------------------------
# cgroup parsing — pure functions, and the load-bearing part of this file
#
# These are string functions with no host access on purpose. The whole
# attribution chain runs through them, and a parser that can only be tested
# against the machine it runs on is a parser that is tested once.
# ---------------------------------------------------------------------------


def session_of_cgroup(cgroup: str) -> str | None:
    """`.../session-2382.scope` -> `2382`. None if no session scope.

    This is the join between a process tree and a login. It is a FACT when
    present: the kernel put the process in that scope, nobody typed it.
    """
    match = re.search(r"(?:^|/)session-(\d+)\.scope", cgroup or "")
    return match.group(1) if match else None


def user_of_cgroup(cgroup: str) -> str | None:
    """`.../user-1006.slice/...` -> `1006`. The numeric uid."""
    match = re.search(r"(?:^|/)user-(\d+)\.slice", cgroup or "")
    return match.group(1) if match else None


def unit_of_cgroup(cgroup: str) -> str | None:
    """The innermost systemd unit in the path, if any.

    Returns None for a bare `.scope` (those are session/container scopes, and
    naming one as a "unit" would overstate what the kernel recorded).

    The innermost unit is the right answer rather than the outermost: for
    `user@1006.service/app.slice/dbus.service` the question "what supervises
    this" is answered by `dbus.service`, not by the login manager.
    """
    parts = [p for p in (cgroup or "").split("/") if p]
    for part in reversed(parts):
        if part.endswith(".service") or part.endswith(".mount"):
            return part
    return None


def container_of_cgroup(cgroup: str) -> str | None:
    """`/system.slice/docker-<64 hex>.scope` -> the container id.

    Also matches the cgroupfs forms docker uses under user slices. Returns
    None when the process is not in a container, which is a measurement and
    not a failure.
    """
    patterns = (
        r"(?:^|/)docker-([0-9a-f]{12,64})\.scope",
        r"(?:^|/)docker/([0-9a-f]{12,64})",
        # containerd / k8s flavours, present on some hosts
        r"(?:^|/)cri-containerd-([0-9a-f]{12,64})\.scope",
    )
    for pattern in patterns:
        match = re.search(pattern, cgroup or "")
        if match:
            return match.group(1)
    return None


# ---------------------------------------------------------------------------
# Ownership - who a group belongs to, and the evidence for saying so
# ---------------------------------------------------------------------------

# Two CLASSES, borrowed from the vocabulary this file already uses for
# resources, because a reader who learns it once should not have to learn a
# second one:
#
#     EXTERNAL   the cgroup path itself names an owner that is not this
#                deployment's
#     UNKNOWN    nobody can say from what the kernel recorded
#
# `role` carries the SUBTYPE. It exists because "external" is not one thing: a
# host service, the root cgroup, and another tenant's login session are all not
# ours, and reporting them as one undifferentiated class is what let a per-user
# systemd manager be mistaken for a host service. Every role is backed by a
# DIFFERENT clause of the cgroup path, and `why` quotes the clause.
#
# `lifecycle` is OBSERVE_ONLY on every EXTERNAL answer. Nothing in this file
# reclaims, cleans up or mutates anything; the field is here so that a consumer
# reading an EXTERNAL group cannot mistake "attributed" for "actionable".
EXTERNAL = "EXTERNAL"
#: Ours, by the deployment's own declaration. The third answer, beside
#: "somebody else's" and "nobody can say" — see `deployment_units()`.
MANAGED = "MANAGED"
UNKNOWN = "UNKNOWN"

#: An installed unit's process, under `system.slice` or the root `init.scope`.
ROLE_EXTERNAL_SYSTEM = "EXTERNAL_SYSTEM"
#: In the cgroup hierarchy but outside every slice the system manager builds —
#: the root cgroup (kernel threads) and vendor agents such as `/YunJing/YDEyes`.
ROLE_EXTERNAL_UNSLICED = "EXTERNAL_UNSLICED"
#: A login manager: `user@<uid>.service`, which owns that user's session slice.
ROLE_EXTERNAL_USER_MANAGER = "EXTERNAL_USER_MANAGER"
#: A login session: `session-<N>.scope`, which the kernel created for a login.
ROLE_EXTERNAL_USER_SESSION = "EXTERNAL_USER_SESSION"

#: A unit this deployment declares in its own tree. The opposite answer to
#: every role above: not "which other owner is this" but "this is ours".
ROLE_MANAGED_UNIT = "MANAGED_UNIT"

#: Stated once, at module level, so no return site can disagree about it.
OBSERVE_ONLY = "OBSERVE_ONLY"

SYSTEM_SLICE = "system.slice"
USER_SLICE = "user.slice"
SYSTEM_SCOPES = ("init.scope",)


def _owner(role, *, why, slice_name=None, unit=None, uid=None,
           session=None) -> dict:
    """One EXTERNAL answer with its evidence. Never guesses."""
    return {
        "class": EXTERNAL,
        "role": role,
        "lifecycle": OBSERVE_ONLY,
        "provenance": "cgroup",
        "slice": slice_name,
        "unit": unit,
        "uid": uid,
        "session": session,
        "why": why,
    }


def _managed(role, *, why, slice_name=None, unit=None, uid=None,
             session=None) -> dict:
    """One MANAGED answer with its evidence.

    `lifecycle` is OBSERVE_ONLY here for the same reason it is on every
    EXTERNAL answer: this round settles the attribution question and no other.
    Knowing a runtime is ours says nothing about whether it should be stopped,
    and a consumer that reads MANAGED as an instruction has misread the field.
    """
    return {
        "class": MANAGED,
        "role": role,
        "lifecycle": OBSERVE_ONLY,
        "provenance": "deployment-manifest",
        "slice": slice_name,
        "unit": unit,
        "uid": uid,
        "session": session,
        "why": why,
    }


def _unattributed(*, why, slice_name=None, unit=None, uid=None,
                  session=None) -> dict:
    """The answer for a group nobody can attribute from what was recorded."""
    return {
        "class": UNKNOWN,
        "role": None,
        "lifecycle": None,
        "provenance": None,
        "slice": slice_name,
        "unit": unit,
        "uid": uid,
        "session": session,
        "why": why,
    }


#: What makes a path segment a systemd unit rather than a scope. Bare,
#: because it is a parser's test and not a name — `unit_of_cgroup` tests the
#: same suffix. The glob in `deployment_units()` is built from this so that no
#: literal in this file names a unit: a pattern that matches every unit is the
#: opposite of an allowlist.
UNIT_SUFFIX = ".service"

_DEPLOYMENT_UNITS: frozenset | None = None


def deployment_units() -> frozenset:
    """The systemd units this deployment ships, read from its own tree.

    This is the `registry-match` evidence class `attribution-contract.yaml`
    already lists as decisive — not a list of service names somebody typed.
    The answer is whatever unit files sit in this module's own directory, so
    it moves when the deployment does: adding a unit file there is what makes
    a scope MANAGED, and nothing here names `serverosd`.

    It is NOT the live managed-runtime registry, which is a document supplied
    at run time and which is ABSENT on this host. Until one is supplied, this
    is the only positive evidence of deployment membership there is, and it
    covers the deployment's own units and nothing else.

    Unreadable or absent directory -> empty set -> every scope falls through
    to the cgroup rules in `ownership_of_scope`, which is exactly the
    behaviour before this function existed. The failure direction is "not
    ours", never "ours": an empty registry makes the classifier less willing
    to claim a group, not more.
    """
    global _DEPLOYMENT_UNITS
    if _DEPLOYMENT_UNITS is None:
        try:
            _DEPLOYMENT_UNITS = frozenset(
                p.name for p in _HERE.glob("*" + UNIT_SUFFIX))
        except OSError:
            _DEPLOYMENT_UNITS = frozenset()
    return _DEPLOYMENT_UNITS


def ownership_of_scope(scope: str) -> dict:
    """Who owns this cgroup scope, with the evidence, or UNKNOWN.

    Four EXTERNAL roles, one MANAGED role and one UNKNOWN. The UNKNOWN answer
    is not a failure —
    it is the answer for a process nobody can attribute from what the kernel
    recorded, and it is what keeps this function worth reading.

    This used to answer EXTERNAL_SYSTEM or UNKNOWN only, and put all of
    `user.slice` in the second group with this reasoning: it holds this user's
    sessions and every other user's, and the path does not distinguish them.
    That is true of the bare `user.slice` segment and FALSE of everything
    below it — `user-1002.slice` names the uid, `session-<N>.scope` names the
    login, and `user@<uid>.service` names the manager. `user_of_cgroup` and
    `session_of_cgroup` were already in this file reading exactly those
    segments for other callers. So the old rule was refusing to read evidence
    it already knew how to read, and the cost was paid by every per-user
    systemd manager on the host: each one was reported as an orphan.

    The load-bearing half of the rule is unchanged and is the reason the roles
    are separate strings. A per-user manager is EXTERNAL_USER_MANAGER, NOT
    EXTERNAL_SYSTEM, and `is_external_system()` is false for it. An earlier
    version of this function matched `init.scope` anywhere in the path and
    called six user managers host services, one of them this user's own; that
    was caught by reading its own output and is why the `segments[0]` guard
    below exists. Roles keep that distinction while letting the group stop
    being called an orphan.

    What this still does NOT do: claim an owner where the path does not name
    one. A scope in a slice this file does not know (say `machine.slice`) has
    no provable owner and stays UNKNOWN. Unattributable is not a synonym for
    external and is not used as one.
    """
    scope = scope or ""
    # Not a path, so not evidence about an owner. `""` is a group whose
    # producer recorded no scope: the sentence written for it below was
    # UNREACHABLE before this guard, because the fallthrough answered every
    # unmatched input with "not under system.slice" instead — so a caller
    # looking for "no scope" got "not a system service" and could not tell the
    # two apart. That is the kind of gap a reader finds by trying to reach
    # their own error path and failing.
    if not scope:
        return _unattributed(why="the group carries no cgroup scope")
    # A raw `0::/user.slice` line, or any other string that is not a cgroup
    # path. Answering it would be answering about something the kernel never
    # wrote.
    if not scope.startswith("/"):
        return _unattributed(
            why=("the value is not a cgroup path, so it is not evidence about "
                 "an owner"))
    slice_name = slice_of_cgroup(scope)
    unit = unit_of_cgroup(scope)
    segments = [p for p in scope.split("/") if p]
    uid = user_of_cgroup(scope)
    session_id = session_of_cgroup(scope)

    # Ours, by the deployment's own declaration, before any slice rule: an
    # installed unit of this deployment is answered by WHICH DEPLOYMENT it
    # belongs to, not by which manager happened to start it. Without this the
    # ServerOS daemon itself — `user@1002.service/app.slice/serverosd.service`
    # — came back as EXTERNAL_USER_MANAGER, i.e. "somebody else's", which is a
    # false statement about our own process. The cgroup half of that answer
    # was true and the class half was not; a class that carries a membership
    # claim cannot be derived from a mechanism fact alone.
    if unit and unit in deployment_units():
        return _managed(
            ROLE_MANAGED_UNIT, slice_name=slice_name, unit=unit, uid=uid,
            session=session_id,
            why=("the cgroup names " + unit + ", a unit this deployment ships "
                 "in its own tree. The answer is the deployment that declares "
                 "it, not the manager that started it"),
        )
    if slice_name == SYSTEM_SLICE:
        return _owner(
            ROLE_EXTERNAL_SYSTEM, slice_name=slice_name, unit=unit,
            why=("the cgroup is under " + SYSTEM_SLICE
                 + (" as " + unit if unit else "")
                 + ", which is where an administrator-installed unit's "
                   "processes run. The system manager is the owner"),
        )
    # `init.scope` is not a slice and holds no unit: it is PID1 and the units
    # the system manager starts before slices exist. It must be the FIRST
    # segment. Every per-user systemd manager creates its own `init.scope`
    # nested inside `user@<uid>.service`:
    #
    #     PID 1        /init.scope
    #     uid 1002     /user.slice/user-1002.slice/user@1002.service/init.scope
    #
    # so matching the segment anywhere calls six user managers host system
    # services, one of which is this user's own. A nested one belongs to
    # whichever manager created it, and is answered below as a user manager.
    if segments and segments[0] in SYSTEM_SCOPES:
        return _owner(
            ROLE_EXTERNAL_SYSTEM, slice_name=slice_name, unit=unit,
            why=("the cgroup is the root-level init.scope, which holds PID1 "
                 "and the units the system manager starts before any slice "
                 "exists"),
        )
    # The login manager, and the sessions it starts. Read from `user-<uid>
    # .slice` and `session-<N>.scope`, both of which the kernel wrote; neither
    # is typed by a person. The uid is what separates this user's groups from
    # another tenant's, and naming it is the whole point of the role.
    if slice_name == USER_SLICE:
        if session_id:
            return _owner(
                ROLE_EXTERNAL_USER_SESSION, slice_name=slice_name, unit=unit,
                uid=uid, session=session_id,
                why=("the cgroup is session-" + session_id + ".scope under "
                     + ("user-" + uid + ".slice" if uid else USER_SLICE)
                     + ", which the kernel creates for one login. That login "
                       "is not this deployment's process tree"),
            )
        return _owner(
            ROLE_EXTERNAL_USER_MANAGER, slice_name=slice_name, unit=unit,
            uid=uid,
            why=("the cgroup is under " + USER_SLICE + " at "
                 + ("user-" + uid + ".slice" if uid else "a user slice")
                 + (", as " + unit if unit else "")
                 + ". The login manager -- not the system manager -- started "
                   "it, which is why this is not EXTERNAL_SYSTEM"),
        )
    # No slice at all: the root cgroup, and cgroups placed outside the
    # system manager's hierarchy. The evidence is the ABSENCE of a slice,
    # which is itself a fact the kernel recorded -- systemd puts everything it
    # starts in a slice, so a process without one was not started by it. That
    # covers PID2 and the kernel threads in `/`, and vendor agents such as
    # `/YunJing/YDEyes`. It does NOT say "kernel thread": /proc/<pid>/stat
    # carries no such flag in the facts this module collects, and inferring it
    # from the cgroup alone would be a guess wearing a conclusion's name.
    if slice_name is None:
        return _owner(
            ROLE_EXTERNAL_UNSLICED, slice_name=None, unit=unit, uid=uid,
            why=("the cgroup is "
                 + (scope if scope else "the root cgroup")
                 + " -- outside every slice the system manager builds. The "
                   "system manager puts everything it starts in a slice, so "
                   "this was not started by it"),
        )
    return _unattributed(
        slice_name=slice_name, unit=unit, uid=uid, session=session_id,
        why=("the cgroup is under " + slice_name + ", a slice this file has no "
             "evidence about. Unattributable is not external, so it stays "
             "UNKNOWN"),
    )


def is_external_system(group: dict) -> bool:
    """True only for the host's own system-manager-owned groups. Never guesses."""
    ownership = group.get("ownership") or {}
    return (ownership.get("class") == EXTERNAL
            and ownership.get("role") == ROLE_EXTERNAL_SYSTEM)


def is_external(group: dict) -> bool:
    """True for any group whose cgroup names a non-deployment owner.

    Wider than `is_external_system` on purpose: it answers "who owns this",
    and the answer "not us, and here is who" is the same answer whether the
    owner is the system manager or a login. It is the right predicate for a
    code whose claim is "no agent rule identifies this group" -- for a login's
    session that claim is false and no agent rule SHOULD match it.

    It is the WRONG predicate for deciding whether a group is still a hygiene
    problem. Use `parented_to_init_by_design` for that.
    """
    return (group.get("ownership") or {}).get("class") == EXTERNAL


def is_managed(group: dict) -> bool:
    """True for a group this deployment's own declaration names.

    The third answer, beside "somebody else's" and "nobody can say". It is
    read wherever the question is "is this ours": a hygiene code must not rank
    our own declared runtime alongside the groups nobody could attribute, and
    it must not call it an orphan either — §1 makes "not managed" a
    precondition of ORPHAN_PROCESS_TREE, and a runtime we declared is the
    definition of managed.

    Note the failure direction on an unreadable registry: it is empty, so this
    returns False and the caller falls back to the cgroup rules. The
    classifier never claims a group it cannot show evidence for.
    """
    return (group.get("ownership") or {}).get("class") == MANAGED


#: The user manager's own scope, and nothing under it.
#:
#: `user@<uid>.service` is a unit the SYSTEM manager starts, so PID1 is its
#: process's parent by design. The units and sessions BELOW it were started by
#: the user's instance, so for those the same lineage means the user's instance
#: is gone. The role cannot express that difference — it answers "whose is
#: this", and both are the user manager's — so the scope is read here, where
#: the question being asked is a different one.
# The instance segment of a user manager's cgroup path, captured so that its
# `init.scope` can be looked for in a process table. Anchored at the start
# because a cgroup path is absolute.
USER_INSTANCE = re.compile(r"^(?P<instance>/user\.slice/user-\d+\.slice/user@\d+\.service)(?:/|$)")

USER_MANAGER_SCOPE = re.compile(
    r"^/user\.slice/user-\d+\.slice/user@\d+\.service/init\.scope$")

#: Roles whose owner makes "parented to PID1" a design property. Membership is
#: necessary and, for the manager, not sufficient — `USER_MANAGER_SCOPE` above
#: narrows it further.
PARENTED_TO_INIT_BY_DESIGN = (
    ROLE_EXTERNAL_SYSTEM,
    ROLE_EXTERNAL_USER_MANAGER,
    ROLE_EXTERNAL_UNSLICED,
)


def parented_to_init_by_design(group: dict) -> bool:
    """True when the cgroup shows that PID1 being the parent is what this group
    IS, rather than a sign that its parent died.

    This is the narrow question an orphan alert is actually asking. `EXTERNAL`
    is not the answer to it, and using it as one was a real defect found by
    running the suite rather than by reasoning:

        EXTERNAL_SYSTEM        systemd started an installed unit, so PID1 is
                               its parent by design.
        EXTERNAL_USER_MANAGER  and the cgroup is the manager's OWN
                               `user@<uid>.service/init.scope`, which holds
                               `systemd --user`. PID1 started it, so PID1 is
                               its parent by design. A unit underneath it
                               (`user@1002.service/dbus.service`) is also the
                               user manager's, and is NOT exempt: the user's
                               instance started that one, so PID1 being its
                               parent means the user's instance is gone.
        EXTERNAL_UNSLICED      outside the manager's hierarchy entirely --
                               kernel threads and vendor agents such as
                               `/YunJing/YDEyes`. No session's death could
                               explain it, so its lineage explains nothing
                               either way.

    EXTERNAL_USER_SESSION is NOT in that tuple, and the reason has not
    changed: a process inside `session-<N>.scope` whose parent is PID1 may
    have outlived its session leader, and that is the leftover signature this
    detector exists to find. A live login's processes are parented to their
    session leader, not to PID1, so exempting the scope wholesale would blind
    the one case that matters — and nothing else catches it:
    `RUNTIME_OUTLIVED_OWNER` fires only while the session still reports state
    `closing`, and a session that is simply gone reports no state at all.

    What the ROLE cannot carry, though, is whether the group's root still has
    that login. The role answers "whose is this", and both a leftover and a
    daemon that detached from the same login answer it identically. So for a
    session scope the exemption is not granted by role but asked of the
    kernel, per group, through `detached_by_design` — which is False when the
    evidence is absent, leaving ORPHAN exactly where it was.

    The role still travels on the record for a session group, so a reader
    sees whose session it was.
    """
    ownership = group.get("ownership") or {}
    if ownership.get("class") != EXTERNAL:
        return False
    role = ownership.get("role")
    if role in (ROLE_EXTERNAL_SYSTEM, ROLE_EXTERNAL_UNSLICED):
        return True
    if role == ROLE_EXTERNAL_USER_MANAGER:
        # Necessary, not sufficient: only the manager's OWN scope. A unit the
        # user's instance started — `user@1002.service/dbus.service` — is the
        # user manager's too, and there being parented to PID1 means the user
        # instance died. Same signature as a session leftover, same answer.
        if USER_MANAGER_SCOPE.match(group.get("scope") or ""):
            return True
        return scope_of_a_live_instance(group)
    if role == ROLE_EXTERNAL_USER_SESSION:
        return detached_by_design(group)
    return False


def _owner_instance_live(cgroup: str | None, table: dict) -> bool | None:
    """Whether the user instance that owns this cgroup is still running.

    Read from the cgroup the kernel put each process in, not from
    `systemctl`: a `user@<uid>.service` keeps an `init.scope` under itself for
    exactly as long as the instance is alive, and every process in the table
    carries the cgroup the kernel wrote for it. So the answer costs one pass
    over a table the caller already has, and it cannot be stale the way a
    separate query can.

    None means "this cgroup is not under a user instance", which is a
    different fact from False and is why the caller tests it separately.
    False is returned when the instance is not visible in the table — under
    `hidepid` a table can be short, and a short table must not read as a live
    instance.
    """
    if not cgroup or not isinstance(cgroup, str):
        return None
    match = USER_INSTANCE.match(cgroup)
    if match is None:
        return None
    wanted = match.group("instance") + "/init.scope"
    for facts in table.values():
        if getattr(facts, "cgroup", None) == wanted:
            return True
    return False


def detached_by_design(group: dict) -> bool:
    """True when the group's root created its OWN POSIX session.

    `reparented_to_init` alone cannot separate a leftover from a daemon that
    double-forked, and this module said so for a long time: "the difference
    between abandoned and daemonised is not in the lineage". That was true of
    the lineage and false of the kernel's record. `/proc/<pid>/stat` field 6
    is the POSIX session id, and it answers the question directly:

      · A process that calls `setsid()` becomes a session leader, so its
        session id IS its own pid. Its parent exiting afterwards is what
        daemonising means. Measured on this host 2026-09-18: the VS Code
        server (pid 25215) and `botmux` (pid 1444974) both sit in a
        `session-<N>.scope` with ppid 1 and sid == pid.
      · A process whose session leader died keeps the DEAD leader's session
        id — not its own, and belonging to a pid that no longer exists.
        Measured the same day: a `sh` (pid 317189) carrying sid 316851, where
        316851 was gone.

    Nothing here reads a name, so this cannot become an allowlist by another
    route. It is False when the evidence is absent, which is the direction
    that keeps the alert: a group whose lineage cannot be read keeps ORPHAN.

    One case this does not cover, and should not: a daemon whose own session
    leader later dies leaves grandchildren carrying the inherited sid. Those
    are reported as leftovers, and that is the right answer for them — they
    did outlive their leader.
    """
    return bool((group.get("tree") or {}).get("root_owns_its_session"))


def scope_of_a_live_instance(group: dict) -> bool:
    """True when the group is a RUNTIME scope under a user instance that is
    still running.

    The same defect as the one above, in a different cgroup shape. A scope is
    a grouping systemd made at runtime; it is never backed by a unit file, and
    systemd does not fork what runs in it — `systemd-run --scope` registers the
    scope around a process it started and then exits, so the payload is
    parented to PID1 from the moment the command returns. That is why
    `reparented_to_init` looks identical here to a service that outlived its
    manager, and why the existing rule ("only the manager's own scope") reads
    it as the latter.

    What separates them is the instance. A service below `user@<uid>.service`
    has the manager as its parent, so PPID 1 means the manager is gone. A
    runtime scope below it does not, and the manager can be seen to be alive:
    `user@<uid>.service/init.scope` still exists in the cgroup tree. Measured
    on `device:tokyo-shared-dev-01`, 2026-09-18: `tmux-spawn-*.scope` under a
    live `user@1004.service`, both directories present in cgroupfs, roots with
    PPID 1 carrying 2 to 18 processes each.

    Session scopes are excluded and keep their own rule — they are runtime
    groupings too, but a login really does end, and `detached_by_design` is
    the question to ask about those. The user manager's own `init.scope` is
    excluded because it is already exempt above.

    Fail-closed like the other one: absent the cgroup, or a table too short to
    contain the instance, this is False and the alert stands.
    """
    scope = group.get("scope") or ""
    if not scope.endswith(".scope"):
        return False
    if USER_MANAGER_SCOPE.match(scope) or session_of_cgroup(scope):
        return False
    return bool((group.get("tree") or {}).get("owner_instance_live"))


def slice_of_cgroup(cgroup: str) -> str | None:
    """`user.slice` / `system.slice` — coarse, and honest about being coarse."""
    match = re.search(r"(?:^|/)([a-z-]+\.slice)", cgroup or "")
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# /proc parsing
# ---------------------------------------------------------------------------


def parse_stat(raw: str) -> dict:
    """Parse one `/proc/<pid>/stat` line.

    The `comm` field is wrapped in parentheses and may itself contain spaces
    and parentheses — a process can set its own name to `foo) bar`. Splitting
    on whitespace, which is the obvious way to write this, silently misreads
    every field after it for such a process. So the split happens at the LAST
    `)` and everything before it is the name.
    """
    close = raw.rfind(")")
    if close == -1:
        raise AttributionError(f"unparsable stat: {raw[:120]!r}")
    open_paren = raw.find("(")
    comm = raw[open_paren + 1:close]
    rest = raw[close + 2:].split()
    # `rest[0]` is field 3 (state); field N is rest[N-3].
    def field(n: int) -> str:
        return rest[n - 3]

    return {
        "comm": comm,
        "state": field(3),
        "ppid": int(field(4)),
        # Fields 5 and 6. The process group and the POSIX session are what
        # separate "this daemon detached on purpose" from "this outlived a
        # leader that died" -- see `detached_by_design`, which is the only
        # consumer, and which had to be answered from the cgroup alone until
        # these two were parsed.
        "pgrp": int(field(5)),
        "session": int(field(6)),
        "utime": int(field(14)),
        "stime": int(field(15)),
        "starttime": int(field(22)),  # clock ticks since boot
        "rss_pages": int(field(24)),
        "num_threads": int(field(20)),
    }


def parse_cmdline(raw: str) -> dict:
    """argv, kept as a list. The joined string is for display only.

    Kept apart because a later rule may match on argv[0] (the executable)
    where matching the whole command line would also match an argument that
    happens to contain the same word. That distinction has already cost this
    host once: a `pkill -f` pattern matched its own shell.
    """
    argv = [part for part in raw.split("\0") if part]
    return {"argv": argv, "executable": argv[0] if argv else None}


def lifetime_cpu_percent(utime: int, stime: int, starttime: int, uptime: float) -> float:
    """Average CPU over the process's whole life, as a percentage of one core.

    Deliberately NOT an instantaneous sample.

    "Sustained" is the claim the anomaly rules make, and a single sample of a
    busy process cannot support it — a build compiles hard for a minute and
    then stops. Lifetime average over a known-long life is a measurement that
    can actually carry the word "sustained", and it needs no sampling loop,
    which matters because the daemon must not poll to answer a question the
    kernel already recorded.

    Returns percent-of-one-core, so 407.0 means four cores' worth.
    """
    elapsed = uptime - (starttime / CLK_TCK)
    if elapsed <= 0:
        return 0.0
    return round(((utime + stime) / CLK_TCK) / elapsed * 100.0, 1)


# ---------------------------------------------------------------------------
# Network — sockets belonging to ONE process
# ---------------------------------------------------------------------------


def parse_net_tcp(raw: str) -> dict[str, dict]:
    """`/proc/net/tcp` -> {inode: {local, remote, state}}.

    Keyed by inode because that is the only thing that joins a socket back to
    the process holding it: `/proc/net/tcp` is per-network-namespace and does
    NOT say who owns each row. The join is made the other way round, by
    reading `/proc/<pid>/fd/*` for `socket:[inode]` links.
    """
    out: dict[str, dict] = {}
    lines = raw.splitlines()
    for line in lines[1:]:  # first line is the header
        fields = line.split()
        if len(fields) < 10:
            continue
        local, remote, state, inode = fields[1], fields[2], fields[3], fields[9]
        out[inode] = {
            "local": _decode_addr(local),
            "remote": _decode_addr(remote),
            "state": _TCP_STATES.get(state, f"STATE_{state}"),
        }
    return out


def parse_net_unix(raw: str) -> dict[str, dict]:
    """`/proc/net/unix` -> {inode: {path, type, state}}.

    Unix-domain sockets are not a special case here, they are the majority of
    what matters on this host: MCP servers, the codex app-server's
    `--listen unix://` endpoint, the ServerOS daemon socket and the RDC relay
    are all AF_UNIX. A socket reader that only knows /proc/net/tcp reports
    every one of them as UNRESOLVED, which reads as "we could not tell" when
    the truth is "we did not look in the right table".

    Columns:
        Num RefCount Protocol Flags Type St Inode Path
    The path is absent for an unbound or abstract socket, and an abstract
    socket's path begins with `@` — which is how the RDC relay socket is
    named in this host's registry.
    """
    out: dict[str, dict] = {}
    for line in raw.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 7:
            continue
        inode = fields[6]
        out[inode] = {
            "path": fields[7] if len(fields) > 7 else None,
            "type": {"0001": "STREAM", "0002": "DGRAM", "0005": "SEQPACKET"}.get(
                fields[4], fields[4]
            ),
        }
    return out


_TCP_STATES = {
    "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1",
    "05": "FIN_WAIT2", "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT",
    "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING",
}


def _decode_addr(field: str) -> str:
    """`0100007F:1F90` -> `127.0.0.1:8080`. Handles v4 and v6.

    The kernel prints the address hex-encoded little-endian per 32-bit word,
    so it cannot be read off by eye and must be decoded rather than copied.
    """
    try:
        host_hex, port_hex = field.split(":")
        port = int(port_hex, 16)
        if len(host_hex) == 8:
            packed = struct.pack("<I", int(host_hex, 16))
            return f"{socket.inet_ntoa(packed)}:{port}"
        # IPv6: four little-endian 32-bit words
        words = [host_hex[i:i + 8] for i in range(0, len(host_hex), 8)]
        packed = b"".join(struct.pack("<I", int(w, 16)) for w in words)
        return f"[{socket.inet_ntop(socket.AF_INET6, packed)}]:{port}"
    except (ValueError, OSError):
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Whose home is this path under?
#
# One implementation, in the module both callers already load. Two copies of
# this rule would agree on the day they were written and diverge after, which
# is the failure this codebase has already paid for three times.
# ---------------------------------------------------------------------------
def current_user() -> str | None:
    """The name of the user this process runs as, or None if it cannot be had.

    Deliberately NOT `os.environ['USER']`. The first version of this rule read
    that variable, and it is empty under exactly the conditions that matter:
    a systemd unit, a stripped environment, a `sudo -i` shell. Measured on
    this host, with `USER` unset, the old rule called `/home/hwz` — this
    node's OWN home — a foreign user's directory. A rule that inverts on an
    empty environment variable is a rule that inverts in production.

    `getuid()` is a system call and is always there; the passwd lookup is the
    only part that can fail, and a uid that has no passwd entry is still a
    fact worth returning.
    """
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return None


def foreign_home(path: str | None, *, user: str | None = None) -> bool:
    """Is this path under some OTHER user's home?

    Another actor's directory is another actor's resource. That is positive
    evidence about who does NOT own it — not a guess about who does.

    Fails toward False. If the current user cannot be established, no path is
    called foreign: asserting that a directory belongs to somebody else is a
    claim, and a claim nobody can support is how this node's own work gets
    written off as a stranger's. Both readings end in DEFER, so the safe
    branch is also the honest one.

    Read from the path rather than a hardcoded username list, so it covers
    both layouts this machine uses:

        /home/<user>/...                          the conventional layout
        /opt/applications/data/home-<user>/...    the per-user data roots
    """
    if not path:
        return False
    me = user if user is not None else current_user()
    if not me:
        return False

    parts = Path(path).parts
    for i, part in enumerate(parts):
        if part == "home" and i + 1 < len(parts) and parts[i + 1] != me:
            return True
        if part.startswith("home-") and part.removeprefix("home-") != me:
            return True
    return False


# ---------------------------------------------------------------------------
# The host. Every read is here and nowhere else.
# ---------------------------------------------------------------------------


class LiveHost:
    """Read-only access to this machine's attribution sources.

    Mirrors `probes.LiveHost` deliberately: same discipline, same injectability,
    and the same property that nothing in it can write. A second, quicker path
    to the host is how two components end up disagreeing about what is running.
    """

    name = "live-host"

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(errors="replace")
        except (OSError, UnicodeError):
            return None

    def read_bytes(self, path: str) -> bytes | None:
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def listdir(self, path: str) -> list[str]:
        try:
            return os.listdir(path)
        except OSError:
            return []

    def readlink(self, path: str) -> str | None:
        try:
            return os.readlink(path)
        except OSError:
            return None

    def run(self, args: list[str], timeout: float = _DEFAULT_TIMEOUT) -> tuple[int, str, str]:
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return out.returncode, out.stdout, out.stderr
        except FileNotFoundError:
            return 127, "", f"{args[0]}: not found"
        except (OSError, subprocess.SubprocessError) as exc:
            return 1, "", str(exc)

    def uptime(self) -> float:
        raw = self.read_text("/proc/uptime") or ""
        try:
            return float(raw.split()[0])
        except (IndexError, ValueError) as exc:
            raise AttributionError("cannot read /proc/uptime") from exc

    def page_size(self) -> int:
        return os.sysconf("SC_PAGE_SIZE")

    def uid_name(self, uid: int) -> str:
        try:
            import pwd

            return pwd.getpwuid(uid).pw_name
        except (KeyError, ImportError, OSError):
            return str(uid)


# ---------------------------------------------------------------------------
# Process table
# ---------------------------------------------------------------------------


class ProcessFacts:
    """One process, as the kernel records it. No interpretation."""

    __slots__ = ("pid", "ppid", "pgrp", "session", "comm", "state", "argv",
                 "executable", "cgroup", "uid", "user", "rss_bytes",
                 "cpu_percent", "starttime", "num_threads", "cwd",
                 "age_seconds")

    def __init__(self, **kw):
        for name in self.__slots__:
            setattr(self, name, kw.get(name))

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


def process_table(host, *, container_index: dict[str, str] | None = None) -> dict[int, ProcessFacts]:
    """Every process this uid can read, in one pass.

    One pass, not one read per question: `runtime tree` and `process top` both
    need the whole table, and a per-PID walk would re-read /proc/<pid>/stat for
    every ancestor. Deterministic — it reads and returns, it never samples.

    Processes that vanish mid-walk are skipped rather than reported: a PID
    that exited between `listdir` and the read is not an unreadable process.
    """
    uptime = host.uptime()
    page = host.page_size()
    table: dict[int, ProcessFacts] = {}

    for entry in host.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        stat_raw = host.read_text(f"/proc/{pid}/stat")
        if not stat_raw:
            continue
        try:
            stat = parse_stat(stat_raw)
        except AttributionError:
            continue

        cmdline_raw = host.read_text(f"/proc/{pid}/cmdline") or ""
        cmd = parse_cmdline(cmdline_raw)
        cgroup_raw = host.read_text(f"/proc/{pid}/cgroup") or ""
        # Take the first non-empty line: v1 hierarchies list one line per
        # controller and v2 lists a single `0::/path`. The unified path is
        # what carries the session, so a v1 host still resolves.
        cgroup = ""
        for line in cgroup_raw.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[2].strip():
                cgroup = parts[2].strip()
                break

        uid = None
        status = host.read_text(f"/proc/{pid}/status") or ""
        for line in status.splitlines():
            if line.startswith("Uid:"):
                uid = int(line.split()[1])
                break

        cwd = host.readlink(f"/proc/{pid}/cwd")

        table[pid] = ProcessFacts(
            pid=pid,
            ppid=stat["ppid"],
            pgrp=stat["pgrp"],
            session=stat["session"],
            comm=stat["comm"],
            state=stat["state"],
            argv=cmd["argv"],
            executable=cmd["executable"],
            cgroup=cgroup,
            uid=uid,
            user=host.uid_name(uid) if uid is not None else None,
            rss_bytes=stat["rss_pages"] * page,
            cpu_percent=lifetime_cpu_percent(
                stat["utime"], stat["stime"], stat["starttime"], uptime
            ),
            starttime=stat["starttime"],
            num_threads=stat["num_threads"],
            cwd=cwd,
            # Computed once, here, from the single uptime read this function
            # already made. Every consumer downstream then works on the table
            # alone — which is what lets `aggregate` and the group tests be
            # pure functions with no host at all.
            age_seconds=round(uptime - (stat["starttime"] / CLK_TCK), 1),
        )
    return table


def ancestry(pid: int, table: dict[int, ProcessFacts], *, limit: int = 64) -> list[int]:
    """The parent chain from `pid` up to init. Cycle-safe.

    The limit is not cosmetic. A corrupted or racing read can produce a cycle
    (`ppid` pointing at a descendant), and an unbounded walk would hang the
    daemon on the one host state where it is most needed.
    """
    chain: list[int] = []
    seen: set[int] = set()
    cursor = pid
    while cursor and cursor not in seen and len(chain) < limit:
        chain.append(cursor)
        seen.add(cursor)
        facts = table.get(cursor)
        if facts is None:
            break
        if facts.ppid == cursor:  # self-parent: malformed, stop
            break
        cursor = facts.ppid
    return chain


def children_of(pid: int, table: dict[int, ProcessFacts]) -> list[int]:
    return sorted(p for p, f in table.items() if f.ppid == pid)


def descendants(pid: int, table: dict[int, ProcessFacts], *, limit: int = 4096) -> list[int]:
    """Every descendant of `pid`, breadth-first, cycle-safe."""
    out: list[int] = []
    seen: set[int] = {pid}
    queue = [pid]
    while queue and len(out) < limit:
        current = queue.pop(0)
        for child in children_of(current, table):
            if child in seen:
                continue
            seen.add(child)
            out.append(child)
            queue.append(child)
    return out


def process_tree(pid: int, table: dict[int, ProcessFacts]) -> dict:
    """A nested tree rooted at `pid`, plus what its lineage does and does not say.

    The lineage matters as much as the tree. For the 2026-09-13 case the tree
    is three processes and the load-bearing fact is that its root is parented
    to PID 1 — its actual owner is gone.

    What `reparented_to_init` does and does not mean
    ------------------------------------------------
    It means `root.ppid == 1`. It does NOT, by itself, mean the tree is
    abandoned: a legitimate daemon that double-forks also has ppid 1. The
    difference between "abandoned" and "daemonised" is not in the lineage, it
    is in whether anything still owns the work — so this function reports the
    lineage fact and leaves the conclusion to `anomaly.py`, which can combine
    it with a session state that is closing and a task that is closed.

    `root_owns_its_session` is the half of that which IS in the kernel's
    record, so it is reported here rather than left to a reader to rediscover.
    `/proc/<pid>/stat` field 6 is the POSIX session id: a process that called
    `setsid()` becomes a session leader and its session id IS its own pid,
    which is what daemonising means; a process whose leader died keeps the
    dead leader's id, which is not its own. See `detached_by_design` for the
    measurements. It is reported and not concluded on for the same reason as
    the lineage: this function is a fact, and `anomaly.py` is the verdict.

    That restraint is deliberate. An earlier version of this function returned
    `orphaned: true` for any ppid-1 root, which would have flagged every
    correctly-daemonised service on the host as abandoned.

    The chain also stops at the visibility boundary rather than at the real
    root, because this host mounts `/proc` with `hidepid=invisible` and PID 1
    is not merely unowned but unreadable. `chain_complete` says which of the
    two happened, so "we reached the top" and "we ran out of permission" are
    never the same answer.
    """
    chain = ancestry(pid, table)
    root_facts = table.get(pid)

    def build(node: int) -> dict:
        facts = table.get(node)
        return {
            "pid": node,
            "comm": facts.comm if facts else None,
            "state": facts.state if facts else None,
            "children": [build(c) for c in children_of(node, table)],
        }

    last = chain[-1] if chain else None
    # The walk reached a process it could read whose ppid is 0 — the real top.
    chain_complete = bool(chain and table.get(last) and table[last].ppid == 0)

    return {
        "root": pid,
        "root_ppid": root_facts.ppid if root_facts else None,
        "lineage": chain,
        "reparented_to_init": bool(root_facts and root_facts.ppid == 1),
        "root_session": root_facts.session if root_facts else None,
        "root_owns_its_session": bool(
            root_facts and root_facts.session == root_facts.pid
        ),
        # Whether the user instance this cgroup belongs to is still running.
        # None when the cgroup is not under one, so "no instance" and "an
        # instance that is gone" stay distinguishable to a reader.
        "owner_instance_live": _owner_instance_live(
            root_facts.cgroup if root_facts else None, table),
        
        "chain_complete": chain_complete,
        "chain_terminated_at": last,
        "chain_terminated_because": (
            "reached_pid_1" if chain_complete else "ancestor_not_readable"
        ),
        "tree": build(pid),
    }


# ---------------------------------------------------------------------------
# Sockets belonging to one process
# ---------------------------------------------------------------------------


def sockets_of(pid: int, host, *, net_tcp: dict[str, dict] | None = None,
               net_unix: dict[str, dict] | None = None) -> dict:
    """Which sockets this process holds, by joining its fds to the net tables.

    The join direction is the whole trick. `/proc/net/tcp` does not say which
    process owns a row; `/proc/<pid>/fd` does not say what a socket is
    connected to. Reading fds for `socket:[N]` and looking N up in the net
    table is what turns "a connection exists on this host" into "THIS process
    is holding it", which is the difference between a fact and a coincidence.

    Both families are consulted. TCP gives peers and listeners; AF_UNIX gives
    the local IPC sockets that most of this host's agent infrastructure
    actually uses, and `unix` is returned as its own list rather than merged
    into `listening` — a Unix socket has no peer address, and reporting one
    in a field that means "address" would invent a value.
    """
    if net_tcp is None:
        net_tcp = {}
        for path in ("/proc/net/tcp", "/proc/net/tcp6"):
            raw = host.read_text(path)
            if raw:
                net_tcp.update(parse_net_tcp(raw))
    if net_unix is None:
        raw = host.read_text("/proc/net/unix")
        net_unix = parse_net_unix(raw) if raw else {}

    inodes: set[str] = set()
    for fd in host.listdir(f"/proc/{pid}/fd"):
        target = host.readlink(f"/proc/{pid}/fd/{fd}")
        if target and target.startswith("socket:["):
            inodes.add(target[len("socket:["):-1])

    peers: list[dict] = []
    listening: list[dict] = []
    unix: list[dict] = []
    unresolved: list[str] = []

    for inode in sorted(inodes):
        info = net_tcp.get(inode)
        if info is not None:
            row = {"inode": inode, "local": info["local"], "state": info["state"]}
            if info["state"] == "LISTEN":
                listening.append(row)
            elif info["state"] == "ESTABLISHED":
                row["remote"] = info["remote"]
                peers.append(row)
            continue

        uinfo = net_unix.get(inode)
        if uinfo is not None:
            unix.append({"inode": inode, "path": uinfo["path"],
                         "type": uinfo["type"], "bound": uinfo["path"] is not None})
            continue

        # Neither table has it: a socket in another network namespace, or one
        # that closed between the two reads. Kept in its own bucket rather
        # than filed under `listening`, because "we saw an fd we could not
        # resolve" is a fact about this measurement's completeness and not a
        # claim that something is listening.
        unresolved.append(inode)

    return {"peers": peers, "listening": listening, "unix": unix,
            "unresolved": unresolved}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def session_facts(session_id: str, host) -> dict:
    """`loginctl show-session` — who is on the other end of this login.

    This is where a process tree stops being a pile of PIDs and acquires an
    ORIGIN DEVICE. For the 2026-09-13 case it returns `RemoteHost=203.0.113.251`
    and `Service=sshd`, which is what makes the tree attributable to a device
    rather than merely to a user.

    Every field is best-effort and independently UNKNOWN: a session with no
    remote host is a local login and that is an answer, not a gap.
    """
    code, out, err = host.run([
        "loginctl", "show-session", str(session_id),
        "-p", "Id", "-p", "User", "-p", "Name", "-p", "Remote", "-p", "RemoteHost",
        "-p", "Service", "-p", "Type", "-p", "Active", "-p", "State", "-p", "TTY",
    ])
    if code != 0:
        return {"session": session_id, "status": UNKNOWN, "why": (err or "").strip()[:200]}

    props: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key] = value

    # `closing` is the load-bearing value. It means the login is on its way
    # out while its scope's processes are still alive — which is exactly the
    # state the 2026-09-13 tree is in, one day and eleven hours later.
    state = props.get("State")
    return {
        "session": session_id,
        "status": FACT,
        "user": props.get("Name"),
        "uid": props.get("User"),
        "remote": props.get("Remote"),
        "remote_host": props.get("RemoteHost") or None,
        "service": props.get("Service"),
        "type": props.get("Type"),
        "active": props.get("Active"),
        "state": state,
        "tty": props.get("TTY") or None,
    }


# ---------------------------------------------------------------------------
# One process, attributed
# ---------------------------------------------------------------------------


def attribute_process(pid: int, host, *, table=None, net_tcp=None,
                      sessions: dict | None = None) -> dict:
    """The §1 record for one PID.

    Returns `runtime`, `origin`, `owner` and `attribution`, with every field
    either read from a source of record, derived by a named rule, or UNKNOWN.
    There is no field for a cause and no field for a diagnosis — see the
    module docstring.
    """
    if table is None:
        table = process_table(host)
    facts = table.get(pid)
    if facts is None:
        return {
            "runtime": {"ref": f"runtime:pid:{pid}", "kind": "process"},
            "status": UNKNOWN,
            "why": f"pid {pid} is not readable: it exited, or it belongs to another user",
            "attribution": {"confidence": "unknown", "provenance": []},
        }

    cgroup = facts.cgroup or ""
    session_id = session_of_cgroup(cgroup)
    container_id = container_of_cgroup(cgroup)
    unit = unit_of_cgroup(cgroup)

    record: dict = {
        "runtime": {
            "ref": f"runtime:pid:{pid}",
            "kind": "process",
            "pid": pid,
            "ppid": facts.ppid,
            "comm": facts.comm,
            "state": facts.state,
            "command": {"executable": facts.executable, "argv": facts.argv},
            "user": facts.user,
            "uid": facts.uid,
            "cgroup": cgroup or None,
            "started": {"clock_ticks_since_boot": facts.starttime, "status": FACT},
            "age_seconds": _age_seconds(facts.starttime, host),
            "cpu_percent_lifetime": facts.cpu_percent,
            "cpu_metric": "lifetime_average_percent_of_one_core",
            "rss_bytes": facts.rss_bytes,
            "num_threads": facts.num_threads,
            "cwd": facts.cwd,
        },
        "container_ref": f"runtime:container:{container_id}" if container_id else None,
        "systemd_unit_ref": unit,
        "session_ref": f"session:{session_id}" if session_id else None,
    }

    provenance: list[str] = []
    origin: dict = {
        "device_ref": None,
        "agent": None,
        "session_ref": record["session_ref"],
        "task_ref": None,
        "transport": None,
    }

    if session_id:
        provenance.append("cgroup")
        if sessions is not None and session_id in sessions:
            sess = sessions[session_id]
        else:
            sess = session_facts(session_id, host)
        record["session"] = sess
        # A remote sshd session names the far-side device. This is an
        # INFERENCE — the address is a fact, the device identity is not, and
        # mapping one to the other is a rule that a caller must be able to see
        # and disagree with.
        if sess.get("remote") == "yes" and sess.get("remote_host"):
            origin["device_ref"] = {"value": f"ip:{sess['remote_host']}",
                                    "status": INFERRED,
                                    "rule": "ssh-session-remote-host"}
            origin["transport"] = {"value": sess.get("service") or "sshd",
                                   "status": FACT}
        elif sess.get("remote") == "no":
            origin["device_ref"] = {"value": "device:local",
                                    "status": FACT,
                                    "rule": "loginctl Remote=no"}
        if sess.get("state"):
            record["session_state"] = sess["state"]

    if container_id:
        provenance.append("cgroup")
        record["container_ref"] = f"runtime:container:{container_id}"

    if unit:
        provenance.append("systemd-unit")
        record["systemd_unit"] = unit

    # A decisive provenance source: the tree lives in a session scope the
    # kernel assigned. That names the OWNER (a session, hence a login), but it
    # does not name the agent or the task, and both stay UNKNOWN rather than
    # being read off the command line here. `graph.py` is where a command line
    # may contribute — as an INFERENCE with its rule recorded.
    confidence = "high" if session_id or container_id or unit else "unknown"
    record["origin"] = origin
    record["owner"] = {
        "type": "session" if session_id else ("container" if container_id else None),
        "ref": (f"session:{session_id}" if session_id
                else (f"container:{container_id}" if container_id else None)),
        "status": FACT if (session_id or container_id) else UNKNOWN,
    }
    record["attribution"] = {
        "confidence": confidence,
        "provenance": provenance,
        "note": (
            "owner is the kernel-assigned scope. agent and task are NOT "
            "inferred here: they are not readable from /proc, and a command "
            "line is evidence rather than an owner."
        ),
    }
    record["status"] = FACT
    return record


def _age_seconds(starttime: int, host) -> float | None:
    try:
        return round(host.uptime() - (starttime / CLK_TCK), 1)
    except AttributionError:
        return None


def coverage(host, table: dict[int, ProcessFacts]) -> dict:
    """How much of the machine this measurement could actually see.

    Reported alongside every whole-host answer so that an absence is never
    mistaken for a measurement. On this host `hidepid=invisible` means the
    answer is always "this uid only", but a reader must be told that rather
    than left to assume the list was complete.
    """
    hidden = False
    proc_mount = ""
    for line in (host.read_text("/proc/mounts") or "").splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/proc":
            proc_mount = fields[3]
            hidden = "hidepid=invisible" in proc_mount or "hidepid=2" in proc_mount
            break

    pids = [p for p in host.listdir("/proc") if p.isdigit()]
    uid = os.getuid()
    return {
        "visible_processes": len(table),
        "proc_pids_present": len(pids),
        "scope": f"uid:{uid}",
        "hidepid_invisible": hidden,
        "proc_mount_options": proc_mount or None,
        "blind_spot": (
            "processes owned by other users are not readable and are NOT "
            "evidence of absence"
        ) if hidden else "none declared by /proc mount options",
        "status": FACT,
    }


def load_contract() -> dict:
    """Read the attribution contract. Failure is loud, never a default."""
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - yaml is a test dependency
        raise AttributionError("PyYAML is required to read the contract") from exc
    try:
        return yaml.safe_load(CONTRACT.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise AttributionError(f"cannot read {CONTRACT}: {exc}") from exc


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
