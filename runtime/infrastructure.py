#!/usr/bin/env python3
"""Persistent infrastructure: what outlives the work, and what may never be touched.

Three questions, one module, because on this host they are the same question
asked from three directions:

  §7   Which of the things running here are PERMANENT INFRASTRUCTURE and which
       are expired development runtimes? A registry that cannot tell them apart
       is a list, and a list is what gets pruned.
  §8   Which of them are TUNNELS — the one runtime kind whose failure is
       routinely reported as success?
  §9   Which matter most, and what may ServerOS do about the ones that do?

The answers are deliberately different in kind from each other. Partition is a
reading of the registry. The tunnel inventory is a MEASUREMENT of this host plus
an honest statement of what this host cannot measure. Criticality is a ranking,
and a ranking is the field most likely to be misread as authority, so most of
what §9 needs is a set of prohibitions rather than a feature.

Two facts about this host shape everything below, and both were measured rather
than assumed:

  1. /proc is mounted with `hidepid=invisible`. Only uid 1006 is visible — 56
     processes at the time of writing, and the 50-odd local listeners that
     belong to docker-proxy and to other uids are simply not there. "I cannot
     see it" and "it is not running" are different findings and this module
     keeps them apart.

  2. There is no task-to-process mapping anywhere on this machine. That is why
     `attribution-contract.yaml` had to invent a lineage-based staleness
     candidate, and it is why a tunnel that nobody declared cannot be found by
     asking a task about it.

Nothing here recommends, and nothing here acts. `recovery_report` exists
because on 2026-09-12 the reflex to restart an endpoint that could not be fixed
by restarting is what turned a 54-minute outage into a 54-minute outage with
twenty restarts in it.
"""
from __future__ import annotations

import importlib.util
import os
import re
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
health = _load("serveros_runtime_health", "health.py")
attribution = _load("serveros_runtime_attribution", "attribution.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN

CRITICALITY_ORDER = ("critical", "important", "normal")

# Lifecycle classes that mean "this outlives the work that started it". The
# contract is the authority; this tuple is only used to ORDER a report, and
# `test_lifecycle_classes_are_the_contracts` fails if the two diverge.
PERMANENT_CLASSES = ("persistent",)


# ---------------------------------------------------------------------------
# §8 · What a forwarding process looks like
#
# Matching is by ARGV TOKEN, never by substring over the joined command line.
# This codebase has paid for the substring version once already: a `pkill -f`
# pattern matched the shell running it because the pattern appeared in that
# shell's own command line. A rule that matches the thing measuring it is not a
# detector.
# ---------------------------------------------------------------------------
_FORWARD_FLAGS = {"-L", "-R", "-D"}


def _forwarder_kind(argv: list[str]) -> str | None:
    """The transport a process forwards over, or None.

    Deliberately narrow. `ssh host` is a login; `ssh -L a:b -N host` is a
    tunnel. Only the second one is a forwarding path, and the difference is a
    flag token, so the flag token is what gets tested.
    """
    tokens = [t for t in argv if t]
    if not tokens:
        return None
    base = Path(tokens[0]).name

    if base == "autossh":
        return "reverse-ssh" if _has_forward_flag(tokens) else "ssh"

    if base in ("ssh", "ssh.exe"):
        if _has_forward_flag(tokens):
            return "ssh"
        return None

    if base == "socat":
        joined = " ".join(tokens)
        if "TCP-LISTEN" in joined or "TCP4-LISTEN" in joined:
            return "socat"
        return None

    return None


def _has_forward_flag(tokens: list[str]) -> bool:
    """True when a token is exactly `-L`, `-R` or `-D`, or opens with one.

    `-L8080:host:80` and `-L 8080:host:80` are both real forms, so both are
    accepted; `--label` and `-Rf` are handled by requiring the character after
    a single-dash flag to be absent or to start an address spec. Getting this
    wrong in the permissive direction classifies every `ssh` call as a tunnel.
    """
    for token in tokens[1:]:
        if token in _FORWARD_FLAGS:
            return True
        if len(token) > 2 and token[0] == "-" and token[1] in "LRD" and token[1] != "-":
            # `-L8080:...` — but not `-Login` or `--Long`. A forward spec's
            # next character is a digit, `[`, `/` or `*`.
            if token[2].isdigit() or token[2] in "[/*":
                return True
    return False


# ---------------------------------------------------------------------------
# §7 · Permanent infrastructure, and the thing that is not
# ---------------------------------------------------------------------------


def classify(resource) -> dict:
    """Is this registration permanent infrastructure, or transient?

    The discriminator is the lifecycle class, because that is what the field
    means: something expected to be up across a reboot outlives the work that
    started it. Criticality is deliberately NOT used here — it ranks attention,
    and letting it also decide permanence would make a ranking into a
    lifecycle claim, which is the §9 mistake in a quieter form.
    """
    permanent = resource.lifecycle in PERMANENT_CLASSES
    return {
        "id": resource.id,
        "kind": resource.kind,
        "lifecycle": resource.lifecycle,
        "protection": resource.protection,
        "criticality": resource.criticality,
        "class": "permanent" if permanent else "transient",
        "because": (
            f"lifecycle: {resource.lifecycle}"
            if permanent
            else f"lifecycle: {resource.lifecycle} — expected to end with its owner"
        ),
        # A critical resource that is NOT permanent is a contradiction worth
        # surfacing rather than smoothing over: something whose absence
        # degrades the node should not be scheduled to disappear with a task.
        # Reported, never enforced — this module does not act.
        "contradiction": (
            "criticality=critical on a transient resource"
            if (resource.criticality == "critical" and not permanent)
            else None
        ),
    }


def partition(registry_doc) -> dict:
    """Everything registered, split by whether it outlives its work."""
    rows = [classify(r) for r in registry_doc.all()]
    permanent = [r for r in rows if r["class"] == "permanent"]
    transient = [r for r in rows if r["class"] == "transient"]
    return {
        "permanent": permanent,
        "transient": transient,
        "counts": {"permanent": len(permanent), "transient": len(transient),
                   "total": len(rows)},
        "contradictions": [r for r in rows if r["contradiction"]],
        "note": (
            "Permanent here means `lifecycle: persistent` — a statement about "
            "lifetime, not about importance. A transient resource is not a "
            "reclaim candidate: §18 forbids this daemon from removing anything, "
            "and the registry's protection level is what decides whether an "
            "automatic path may reach a resource at all."
        ),
    }


# ---------------------------------------------------------------------------
# §17 · Reclaim candidacy
#
# `lifecycle_classes` has said since it was written that `task-scoped` is
# "Bound to one work item or task; reclaimable when that closes." Nothing
# implemented it, which meant the sentence had no consumer — the defect this
# project keeps finding, and the reason it is worth checking for rather than
# assuming. This is the implementation, and it exists because of Case A.
#
# Case A's tunnel was NOT a reclaim candidate at any point during the incident.
# It was running, the far side was healthy, and its owner — a work item —
# was still active. The task was mid-flight and its transport was live. What
# went wrong is that the tunnel's lifetime had been attached to a *different*
# thing entirely, an Agent session, which ended on schedule and took a healthy
# resource with it.
#
# So this function is where the two are told apart. It is given the owner's
# state, which is the fact the registry cannot observe for itself: whether a
# work item is open is `work-management`'s authority, not this daemon's. Given
# it, the answer for Case A is "not a candidate, the owning task is still
# active" — which is the sentence nobody could say during the incident.
# ---------------------------------------------------------------------------

# The owner's own state, as supplied by whoever owns that fact. `UNKNOWN` is
# not a synonym for `CLOSED`: it is the absence of the one input this function
# needs, and it fails toward doing nothing.
OWNER_ACTIVE = "ACTIVE"
OWNER_CLOSED = "CLOSED"
OWNER_UNKNOWN = "UNKNOWN"

# Dispositions. `CANDIDATE` is the only one that proposes anything, and even it
# proposes rather than acts — §17 and §18 make removal a person's decision at
# every tier.
CANDIDATE = "CANDIDATE"
NOT_CANDIDATE = "NOT_CANDIDATE"
DEFER = "DEFER"


def _declared_owner_types() -> tuple[str, ...]:
    """Read from the contract, so this check and `validate` cannot drift into
    disagreeing about which owners exist — the failure this round has now
    produced twice."""
    return tuple(registry.contract()["owner_types"])


def reclaim_candidacy(resource, owner_state: str = OWNER_UNKNOWN) -> dict:
    """Whether a registered resource may be proposed for reclaim, and why.

    Emits a finding. Removes nothing, stops nothing, and has no path to an
    actuator — the answer is a sentence with a reason, which is what §17 asks
    for and what a person needs in order to decide.

    The order of the checks is the contract's, and it is not arbitrary: the
    indefeasible answers come first. A protected resource is never a candidate
    whatever its owner is doing, so asking about the owner first would produce
    a "yes" that a later check has to take back, and a function that can say
    yes and then no will eventually be read at the wrong moment.
    """
    base = {
        "id": resource.id,
        "kind": resource.kind,
        "owner": resource.owner_ref,
        "lifecycle": resource.lifecycle,
        "protection": resource.protection,
        "owner_state": owner_state,
        # Nothing here acts, so this field is not a flag anyone can flip. It is
        # the statement §17 requires: the finding is a proposal to a person.
        "authority": "recommendation",
    }

    if resource.protection == "protected":
        return {**base, "disposition": NOT_CANDIDATE,
                "because": ("protection: protected — never reclaimed by an "
                            "automatic path, independently of owner state")}

    if resource.owner_type not in _declared_owner_types():
        # Defence in depth, and honestly labelled: `validate` already refuses
        # an `owner.type` outside the declared four, so no REGISTERED resource
        # reaches this line. The rule it implements is real and lives
        # elsewhere — for an unregistered thing, attribution is the whole
        # question and `containers.py` answers it with `reclaim_candidate:
        # false`. Keeping the check here costs nothing and means a `Resource`
        # built by some future path that skips registration still cannot be
        # proposed for removal on the strength of an owner nobody confirmed.
        return {**base, "disposition": DEFER,
                "because": ("the owner cannot be confirmed, and unknown owner "
                            "fails safe: DEFER, never a reclaim candidate")}

    # A persistent resource's owner is not the task. Case B is the shape: if
    # the outbound tunnel is genuinely permanent infrastructure, then a work
    # item closing says nothing about it, and reading the task's closure as its
    # death warrant would remove something every session depends on.
    if resource.lifecycle == "persistent":
        return {**base, "disposition": NOT_CANDIDATE,
                "because": ("lifecycle: persistent — its owner is not the task, "
                            "so the task closing does not bear on it")}

    if owner_state == OWNER_UNKNOWN:
        return {**base, "disposition": DEFER,
                "because": ("the owner's state was not supplied, and "
                            "`task-scoped` is reclaimable *when the task "
                            "closes* — which is not established")}

    if resource.lifecycle != "task-scoped":
        # session-scoped, disposable. Nothing to reclaim on a task closing:
        # they are expected to disappear on their own, and proposing them would
        # spend a person's attention on resources that need no decision.
        return {**base, "disposition": NOT_CANDIDATE,
                "because": (f"lifecycle: {resource.lifecycle} — expected to end "
                            f"on its own, so no decision is owed")}

    if owner_state == OWNER_ACTIVE:
        # Case A, in the state it was actually in. This is the sentence that
        # nobody could say during the incident, and the whole reason the owner
        # is a work item rather than a session: a still-open task means the
        # resource is still required, no matter which Agent sessions have come
        # and gone.
        return {**base, "disposition": NOT_CANDIDATE,
                "because": (f"the owning task is still active, so "
                            f"{resource.id} is still desired — its owner "
                            f"outlives the session that created it")}

    return {**base, "disposition": CANDIDATE,
            "because": (f"the owning task has closed and {resource.id} is "
                        f"task-scoped, which is the condition the lifecycle "
                        f"class names. A proposal — removal is a person's "
                        f"decision")}


def reclaim_report(registry_doc, owner_states: dict | None = None) -> dict:
    """Every registered resource, its reclaim disposition, and why.

    `owner_states` maps an owner ref to `OWNER_ACTIVE` or `OWNER_CLOSED`. It is
    supplied by whoever owns that fact — `work-management` — because this
    daemon cannot observe whether a work item is open and must not guess.

    Anything not in the mapping is `OWNER_UNKNOWN`, which is DEFER. That
    default is the whole design: a report that quietly treated an unlisted
    owner as closed would turn every omission into a removal proposal, and it
    would look identical to a correct one until somebody read the list.

    Read-only. Emits dispositions, never actions — §17 makes removal a
    person's decision at every tier.
    """
    states = owner_states or {}
    rows = []
    for resource in registry_doc.all():
        verdict = reclaim_candidacy(
            resource, states.get(resource.owner_ref, OWNER_UNKNOWN))
        rows.append(verdict)

    by_disposition: dict[str, int] = {}
    for row in rows:
        by_disposition[row["disposition"]] = by_disposition.get(row["disposition"], 0) + 1

    return {
        "count": len(rows),
        # Named in the order they matter to a reader: what is proposed, what
        # was considered and refused, and what could not be decided. The third
        # is not a footnote — it is the list of questions to go and answer.
        "by_disposition": by_disposition,
        "candidates": [r for r in rows if r["disposition"] == CANDIDATE],
        "deferred": [r for r in rows if r["disposition"] == DEFER],
        "not_candidates": [r for r in rows if r["disposition"] == NOT_CANDIDATE],
        "owner_states_supplied": sorted(states),
        "authority": "recommendation",
        "note": (
            "Nothing here removes anything. §18 forbids this daemon from "
            "removing a resource at any tier, and §17 makes removal a person's "
            "decision with a written reason. A resource whose owner state was "
            "not supplied is DEFER rather than assumed closed."
        ),
    }


# ---------------------------------------------------------------------------
# §21 · What no automatic path may ever touch
# ---------------------------------------------------------------------------


def never_reclaim(registry_doc) -> dict:
    """The resources no automatic path reaches, and the reason for each.

    Protection is the only input. Not criticality, not age, not size — because
    every one of those is a heuristic, and a heuristic that is usually right is
    exactly what removes somebody's baseline on the evening it is wrong.
    """
    entries = []
    for resource in registry_doc.all():
        if resource.protection != "protected":
            continue
        entries.append({
            "id": resource.id,
            "kind": resource.kind,
            "owner": resource.owner_ref,
            "criticality": resource.criticality,
            "because": "protection: protected",
            # Named so that an operator can see WHICH lane this belongs to
            # without reading the whole registry. This is the field that makes
            # "the litellm baseline" legible as a reference other work depends
            # on rather than as two idle containers.
            "lane": _lane_of(resource),
        })
    return {
        "count": len(entries),
        "entries": entries,
        "note": (
            "`protection: protected` means the automatic path does not reach "
            "it, ever. Not on a timeout, not on a low-disk trigger, not because "
            "the resource is old. Removing one is a human decision with a "
            "written reason, and §18 forbids this daemon from performing one at "
            "all."
        ),
    }


def _lane_of(resource) -> str:
    """A human-readable lane for a resource, from its own owner ref.

    `work-item:LIT-0104` -> `work-item LIT-0104`. The owner ref is the typed
    identity written at registration; this unpacks it for display and invents
    nothing.
    """
    ref = resource.owner_ref or ""
    if ":" in ref:
        kind, _, ident = ref.partition(":")
        return f"{kind} {ident}"
    return ref or "unowned"


# ---------------------------------------------------------------------------
# §9 · Criticality, and the four things it does not confer
# ---------------------------------------------------------------------------


def criticality_report(registry_doc) -> dict:
    """Resources ordered by attention, with the prohibitions attached.

    The `confers` paragraph is carried in the OUTPUT rather than only in the
    contract file, because the contract is read by whoever writes a
    registration and this is read by whoever is deciding what to do at 2am.
    """
    rows = []
    for resource in registry_doc.all():
        rows.append({
            "id": resource.id,
            "kind": resource.kind,
            "criticality": resource.criticality,
            "declared": resource.criticality_is_declared,
            "protection": resource.protection,
            # The load-bearing field. `is_automatable` is computed by the
            # registry from scope, protection, owner and desired state — and it
            # does not look at criticality, because criticality must not change
            # it. Reported side by side so that a reader can SEE that a
            # `critical` resource is no more actionable than a `normal` one.
            "automatable": resource.is_automatable,
            "not_automatable_because": resource.why_not_automatable(),
        })
    order = {name: i for i, name in enumerate(CRITICALITY_ORDER)}
    rows.sort(key=lambda r: (order.get(r["criticality"], len(order)), r["id"]))
    return {
        "order": rows,
        "confers": (
            "PRIORITY OF ATTENTION ONLY. Criticality does not grant authority to "
            "act, does not relax the retry budget, does not change "
            "`is_automatable`, and does not exempt a resource from the "
            "protection rules. `critical` and `protected` are checked "
            "independently and the stricter one wins; a critical protected "
            "resource is still never restarted."
        ),
        "defaulted": [r["id"] for r in rows if not r["declared"]],
    }


# ---------------------------------------------------------------------------
# §8 · Tunnels
# ---------------------------------------------------------------------------


def declared_tunnels(registry_doc) -> list[dict]:
    """Tunnels in the registry, with the two things a tunnel must name.

    Registration already refused anything that could never be measured (see
    `kind_requirements` in the contract), so by the time anything is returned
    from here it has both endpoints and either a transport probe or a written
    reason there is none.
    """
    out = []
    for resource in registry_doc.all():
        if resource.kind != "tunnel":
            continue
        record = resource.record
        transport = record.get("transport") or {}
        na = resource.not_applicable
        out.append({
            "id": resource.id,
            "local": transport.get("local"),
            "remote": transport.get("remote"),
            "transport_class": transport.get("class"),
            "protection": resource.protection,
            "criticality": resource.criticality,
            "recovery": resource.recovery,
            "transport_probe": next(
                (p.get("id") for p in resource.probes if p.get("axis") == "transport"),
                None,
            ),
            "functional_probe": next(
                (p.get("id") for p in resource.probes if p.get("axis") == "functional"),
                None,
            ),
            "functional_not_applicable_because": (
                (na.get("functional") or {}).get("because") if na.get("functional") else None
            ),
        })
    return out


def tunnel_health(resource, observations: dict | None = None):
    """A tunnel's four axes, with liveness honestly separated from function.

    With no observations this returns a Health whose `functional` axis is
    UNKNOWN — and the derived state for that is DEGRADED, not HEALTHY, because
    a tunnel that is up and unmeasured is the exact shape of the 2026-09-12
    endpoint. That is the whole point of routing a tunnel through `Health`
    rather than through a boolean: the rule ordering in the contract already
    puts `DEGRADED when functional: UNKNOWN` ahead of
    `HEALTHY when liveness: RUNNING, functional: OK`, so the trap is closed by
    the same table that closes it for every other resource.
    """
    observations = observations or {}
    axes = {}
    for axis in health.AXES:
        if axis in observations:
            axes[axis] = observations[axis]
            continue
        na = (resource.not_applicable or {}).get(axis)
        if na:
            axes[axis] = health.not_applicable(axis, why=na.get("because") or "declared")
        else:
            # `NOT_DECLARED` is the reason vocabulary's own word for "nobody
            # asked this axis for this resource" — distinct from NOT_OBSERVED,
            # which means a probe was declared and produced nothing. The
            # distinction is the point: the first is a gap in the declaration,
            # the second is a gap in the measurement, and only one of them is
            # fixed by fixing the resource.
            axes[axis] = health.Observation(
                axis=axis,
                state="UNKNOWN",
                observed_by={"type": "none", "id": "no-probe"},
                observed_at=None,
                evidence={"type": "none", "why": "NOT_PROBED: not measured here"},
                reason="NOT_DECLARED",
            )
    return health.Health(desired=resource.desired, **axes)


def readable_host(host=None):
    """A host that can answer the questions THIS module asks.

    There are two `LiveHost` classes in this codebase — `attribution.LiveHost`
    (process table, net tables) and `probes.LiveHost` (docker, systemd,
    network calls) — and they are not interchangeable. Passing the wrong one
    fails with `AttributeError: 'LiveHost' object has no attribute 'uptime'`
    from three frames down, which reads like a bug in the caller's host rather
    than a mismatch a person can act on.

    So the requirement is checked here, by name, and the error says which class
    to pass. It is the same rule as everywhere else in this round: a
    capability that is assumed rather than declared fails somewhere nobody is
    looking.

    Public because `view.py` asks the same questions and must ask them through
    the same guard. A second copy of a capability check is how one of the two
    ends up missing the capability somebody added last week.
    """
    if host is None:
        return attribution.LiveHost()
    missing = [m for m in ("uptime", "listdir", "readlink", "read_text")
               if not hasattr(host, m)]
    if missing:
        raise TypeError(
            f"{type(host).__name__} cannot answer this module's questions: it "
            f"has no {', '.join(missing)}. This module reads /proc, so it needs "
            f"`attribution.LiveHost`, not `probes.LiveHost` — they are different "
            f"classes with different capabilities and this one is checked rather "
            f"than assumed."
        )
    return host


def observe_forwarders(host=None, *, table=None) -> list[dict]:
    """Forwarding processes this uid can read.

    A MEASUREMENT, not an inventory of tunnels. It answers "which visible
    processes are forwarding", which is strictly less than "which tunnels
    exist" on this host — see `tunnel_blind_spots`.
    """
    host = readable_host(host)
    if table is None:
        table = attribution.process_table(host)
    out = []
    for pid in sorted(table):
        facts = table[pid]
        kind = _forwarder_kind(list(facts.argv or []))
        if not kind:
            continue
        out.append({
            "pid": pid,
            "transport_class": kind,
            "argv_head": (list(facts.argv or []) + [""])[0],
            "age_seconds": facts.age_seconds,
            "cpu_percent": facts.cpu_percent,
            "uid": facts.uid,
            "evidence": {"type": "fact", "source": "/proc/<pid>/cmdline"},
        })
    return out


def listening_sockets(host=None, *, table=None) -> list[dict]:
    """Every local TCP LISTEN socket, and whether a visible process holds it.

    The join is the one `attribution.sockets_of` already makes, run backwards:
    collect every visible process's socket inodes, then walk /proc/net/tcp and
    report the LISTEN rows whose inode nobody visible holds.

    Split out of `unreadable_listeners` because two questions are asked of this
    walk and they must not drift: "which listeners are unattributed" (the
    blind-spot report) and "is anything listening on the port a declaration
    names" (§58's egress answer). Two copies of a /proc walk is how one of them
    keeps answering the original question about a host that has since changed —
    and the second question is the one that decides whether an Agent can reach
    the network, so a stale answer there is the expensive kind.
    """
    host = readable_host(host)
    if table is None:
        table = attribution.process_table(host)

    visible_inodes: set[str] = set()
    holders: dict[str, list[int]] = {}
    for pid in table:
        for fd in host.listdir(f"/proc/{pid}/fd"):
            target = host.readlink(f"/proc/{pid}/fd/{fd}")
            if target and target.startswith("socket:["):
                inode = target[len("socket:["):-1]
                visible_inodes.add(inode)
                holders.setdefault(inode, []).append(pid)

    net = {}
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        raw = host.read_text(path)
        if raw:
            net.update(attribution.parse_net_tcp(raw))

    out = [
        {"local": row["local"], "inode": inode,
         "holders": sorted(holders.get(inode, ())),
         "attributed": inode in visible_inodes}
        for inode, row in net.items() if row["state"] == "LISTEN"
    ]
    return sorted(out, key=lambda row: row["local"])


def unreadable_listeners(host=None, *, table=None) -> dict:
    """Local TCP listeners that belong to no process this uid can read.

    This is the honest form of the question. On a host with `hidepid=invisible`
    those sockets are not "unowned" — they are owned by a process this uid is
    not allowed to see, which is what sshd's forwards and docker-proxy look
    like from here. Reporting them as unattributed is correct; reporting them
    as nothing is the `docker ps` mistake in a new place.
    """
    listening = listening_sockets(host, table=table)
    unreadable = sorted(row["local"] for row in listening if not row["attributed"])
    return {
        "listening_total": len(listening),
        "attributed": len(listening) - len(unreadable),
        "unreadable": unreadable,
        "unreadable_count": len(unreadable),
    }


def tunnel_blind_spots(host=None, *, table=None) -> list[dict]:
    """What this host cannot answer about tunnels, stated as measurements.

    An inventory that returns an empty list for "no tunnels" is only useful if
    the reader knows the inventory was CAPABLE of finding one. These entries
    are how it knows.
    """
    host = readable_host(host)
    if table is None:
        table = attribution.process_table(host)

    uids = sorted({f.uid for f in table.values() if f.uid is not None})
    listeners = unreadable_listeners(host, table=table)

    spots = []
    if len(uids) == 1:
        spots.append({
            "what": "sshd-owned forwards",
            "state": UNKNOWN,
            "measured": {
                "visible_uids": uids,
                "visible_processes": len(table),
                "listening_sockets_held_by_no_visible_process": listeners["unreadable_count"],
            },
            "why": (
                "`/proc` is mounted with hidepid=invisible, so only uid "
                f"{uids[0]} is readable. An `ssh -R` forward ends at the sshd "
                "that created it and sshd runs as root. This inventory can "
                "therefore never prove a tunnel does not exist; it can only "
                "report the forwarding processes it can see."
            ),
        })
    if listeners["unreadable_count"]:
        spots.append({
            "what": "listeners held by no visible process",
            "state": UNKNOWN,
            "measured": {
                "count": listeners["unreadable_count"],
                "sample": listeners["unreadable"][:8],
            },
            "why": (
                "these are root-owned or held by another uid. Most on this host "
                "are Docker's port mappings and cross-check against `docker ps`; "
                "the ones that do not are exactly the sockets a tunnel would "
                "hide behind. Unattributed is not the same as unknown-owner "
                "reclaimable, and nothing may be concluded about them either way."
            ),
        })
    return spots


def tunnel_inventory(host=None, *, registry_doc) -> dict:
    """Declared tunnels, visible forwarders, and what could not be measured."""
    host = readable_host(host)
    table = attribution.process_table(host)

    declared = declared_tunnels(registry_doc)
    observed = observe_forwarders(host, table=table)

    # A visible forwarder that no registration covers is a CANDIDATE, never an
    # owner — the same rule `containers.py` applies to container names, for the
    # same reason: a process that is forwarding is not thereby declared, and
    # "it looks like the tunnel you meant" is how one lane's forward gets
    # cleaned up as another's.
    declared_pids = {
        int(p) for r in declared for p in (r.get("pids") or []) if str(p).isdigit()
    }
    undeclared = [o for o in observed if o["pid"] not in declared_pids]

    return {
        "declared": declared,
        "declared_count": len(declared),
        "observed_forwarders": observed,
        "observed_count": len(observed),
        "undeclared_forwarders": undeclared,
        "blind_spots": tunnel_blind_spots(host, table=table),
        "note": (
            "A declared tunnel and an observed forwarder are joined by PID, and "
            "the join is present only when a registration names its PIDs. "
            "Without it, a visible forwarder is a candidate and never an owner: "
            "the registry decides what is ours, and a process that happens to be "
            "forwarding does not register itself by forwarding."
        ),
    }


# ---------------------------------------------------------------------------
# §20 · Recovery models
# ---------------------------------------------------------------------------


def recovery_report(registry_doc) -> dict:
    """What restarting each resource would achieve.

    The field exists because the supervisor's default reflex is wrong for a
    whole class of failures, and the default is not a judgement — it is the
    absence of one. On 2026-09-12 `Restart=always` restarted the RDC endpoint
    every ~24 minutes through a 54-minute outage, and each restart spent a
    fresh device code on the same consumed authorization. The supervisor was
    working exactly as configured.
    """
    models = {m["id"]: m for m in registry.contract()["recovery_models"]}
    rows = []
    for resource in registry_doc.all():
        model = resource.recovery
        spec = models.get(model) or {}
        automatic = spec.get("automatic") == "allowed_unless_protected"
        if automatic and resource.protection == "protected":
            automatic = False
        rows.append({
            "id": resource.id,
            "recovery": model,
            "declared": bool(resource.record.get("recovery")),
            "automatic": automatic,
            "meaning": (spec.get("meaning") or "").strip(),
            "protection": resource.protection,
            "because": (resource.record.get("recovery") or {}).get("because"),
        })
    undeclared = [r["id"] for r in rows if not r["declared"]]
    return {
        "models": rows,
        "undeclared": undeclared,
        "note": (
            f"{len(undeclared)} resource(s) declare no recovery model and "
            "therefore default to `restart`, which is the reflex rather than a "
            "decision. That default is only reached when nobody said otherwise, "
            "and saying otherwise is the entire point of the field."
        ),
    }


# ---------------------------------------------------------------------------
# §58 · Egress: which runtime is missing, who owns it, may anything act
#
# Case B is a local Agent that cannot reach OpenAI and gets the word "network
# error" instead of an answer. Everything upstream of that word is fine: the
# request was made, the dial failed, and the failure was reported accurately.
# What is missing is a layer that can say WHICH object was absent, whether
# anyone had declared it, and whether anyone may bring it back.
#
# The brief forbids guessing the owner (§38: "不要猜"). That constraint is what
# shapes the code below, and it is the reason this is a small module and not a
# discovery service: the useful answer is not a guess with a confidence score,
# it is a precise statement of what was looked for, where, and what was found —
# including, and especially, the case where nothing was.
#
# The four facts §58 requires are enumerated rather than left to the renderer,
# so an answer document can be checked against the requirement instead of
# against whoever wrote the renderer last.
# ---------------------------------------------------------------------------

EGRESS_SCHEMA = "serveros/egress-answer.v1"

EGRESS_QUESTIONS = (
    "reachable",
    "which_runtime_is_missing",
    "who_owns_it",
    "auto_recovery_permitted",
)

# The runtime states, all four of which are answers to "which runtime is
# missing". They differ in a way that decides what a person does next, and the
# pair most often collapsed into one — `NOT_REGISTERED` and `NOT_SERVING` — are
# the pair that means "we never knew about it" and "we knew and it is down".
EGRESS_REGISTERED_SERVING = "REGISTERED_AND_SERVING"
EGRESS_REGISTERED_NOT_SERVING = "REGISTERED_NOT_SERVING"
EGRESS_UNDECLARED_PRESENT = "PRESENT_BUT_UNDECLARED"
EGRESS_NOT_REGISTERED = "NOT_REGISTERED"

# Ownership, in the three states this host can actually distinguish.
# `UNATTRIBUTED` is not a softer `UNKNOWN_OWNER`: the first says something is
# running that this uid may not look at, the second says nothing was found
# running at all. Only one of them is a job for `ss`/`sshd` and only one is a
# job for whoever was supposed to set the thing up.
OWNER_DECLARED = "DECLARED"
OWNER_UNATTRIBUTED = "UNATTRIBUTED"
OWNER_UNDECLARED = "UNKNOWN_OWNER"


class EgressRequirementInvalid(Exception):
    """A requirement that cannot be answered was asked about.

    Raised rather than defaulted, because every field below is used to decide
    whether a registration covers the endpoint — and an unanswered question
    that returns "nothing listens" is indistinguishable from a measurement.
    """


def _endpoint_parts(endpoint):
    """`127.0.0.1:17890` → `("127.0.0.1", 17890)`, else `(None, None)`.

    Nothing is inferred from a malformed endpoint. A lenient parser would let a
    registration named `127.0.0.1:17890` cover a requirement named `17890`, and
    whether a registration covers the endpoint is the input to three of the four
    answers below.
    """
    if not isinstance(endpoint, str) or ":" not in endpoint:
        return None, None
    host, _, port = endpoint.rpartition(":")
    return (host.strip("[]"), int(port)) if port.isdigit() else (None, None)


def _is_loopback(host: str) -> bool:
    return host.startswith("127.") or host in ("::1", "localhost")


def _is_wildcard(host: str) -> bool:
    return host in ("0.0.0.0", "::", "*", "")


def _serves(listener_local: str, endpoint: str) -> bool:
    """Whether a LISTEN socket at `listener_local` serves `endpoint`.

    Direction matters and only one direction is true. A socket bound to
    `0.0.0.0:17890` accepts connections to `127.0.0.1:17890`; a socket bound to
    `127.0.0.1:17890` does not accept them for a requirement named at another
    address. Treating the two as interchangeable reports the endpoint as up on
    a host where the requirement is not served, which is worse than reporting
    nothing — the reader stops looking.
    """
    lh, lp = _endpoint_parts(listener_local)
    eh, ep = _endpoint_parts(endpoint)
    if lp is None or ep is None or lp != ep:
        return False
    if _is_wildcard(lh):
        return True
    if _is_loopback(eh):
        return _is_loopback(lh)
    return lh == eh


def _covering_registrations(registry_doc, requirement: dict) -> list[dict]:
    """Registrations that cover this requirement, and how each one matched.

    Two ways, and both are recorded. By `id`, because §38 names a runtime id;
    and by the local port of a `transport`, because what a declaration in
    `.bashrc` actually names is a socket, and a registration serving that socket
    under a different id still serves it. Matching on id alone would report "no
    tunnel" for a tunnel that is running under a name nobody agreed on — which
    is the shape of the problem, not a hypothetical about it.
    """
    endpoint = requirement["endpoint"]
    wanted = requirement["runtime"]
    out = []
    for resource in registry_doc.all():
        record = resource.record
        transport = record.get("transport") or {}
        matched = []
        if resource.id == wanted:
            matched.append("id")
        if _serves(transport.get("local") or "", endpoint):
            matched.append("transport.local")
        if matched:
            out.append({
                "resource": resource,
                "id": resource.id,
                "kind": resource.kind,
                "matched_by": matched,
                "local": transport.get("local"),
                "remote": transport.get("remote"),
                "owner": record.get("owner"),
                "protection": resource.protection,
            })
    return out


def egress_declarations(endpoint: str, *, host=None, home=None) -> list[dict]:
    """Where this endpoint is NAMED on this node, cited by file and line.

    This is the evidence half of §58, and it is what makes the difference
    between "something depends on this endpoint" and "I believe something
    depends on it". A requirement whose `named_by` is empty is still
    answerable — the report simply says nobody declared it, which is itself the
    finding.

    Three sources, each labelled with which one it is, because they carry
    different weight:

      shell config   durable; survives a reboot and applies to new sessions
      this process   the environment of whoever ran the command; may have been
                     exported by hand and may be gone tomorrow

    `$HOME/.bashrc` is the one that mattered in Case B, and it is worth saying
    why: the value there was a live `export`, not a comment. A comment that
    mentions a dead proxy is documentation; an export that points at one is a
    working tree that fails at the next dial.
    """
    host = readable_host(host)
    home = Path(home) if home else Path(os.environ.get("HOME", ""))
    found = []
    for name in (".bashrc", ".bash_profile", ".profile"):
        path = home / name
        raw = host.read_text(str(path))
        if not raw:
            continue
        for number, line in enumerate(raw.splitlines(), start=1):
            if endpoint in line:
                found.append({
                    "where": f"{name}:{number}",
                    "how": "shell-config",
                    "text": line.strip(),
                })
    for key, value in sorted(os.environ.items()):
        if endpoint in str(value):
            found.append({"where": f"env:{key}", "how": "this-process",
                          "text": f"{key}={value}"})
    return found


def egress_answer(registry_doc, requirement: dict, *, host=None, table=None) -> dict:
    """§58 — what is missing, whose it is, and whether anything may act.

    `requirement` names the thing an Agent is trying to reach and who said it
    was needed:

        {"runtime": "tunnel:agent-openai-outbound",
         "kind": "tunnel",
         "endpoint": "127.0.0.1:17890",
         "named_by": [".bashrc:154 HTTP_PROXY", ...],
         "because": "every OpenAI call from this node is routed through it"}

    The answer is four facts and a disposition, and none of them is
    "network error". Two properties are load-bearing:

    **It never invents an owner.** With no covering registration the owner is
    `UNKNOWN_OWNER` and the disposition is `NEEDS_HUMAN`, which is §38's
    instruction rather than a limitation of the code: an owner that cannot be
    confirmed is not a thing to guess at, because the guess is what gets
    written into a registry and then believed by the next reader.

    **It separates "not declared" from "declared and down".** Those are the two
    ways a thing can be missing and they have different owners, different
    evidence and different next steps. A report that collapses them is the
    §57 report in another domain: the reader is told something is wrong and not
    which of the two worlds they are in.
    """
    for field in ("runtime", "endpoint"):
        if not requirement.get(field):
            raise EgressRequirementInvalid(
                f"a requirement with no {field} cannot be answered: {requirement!r}. "
                "Absence of a fact about the endpoint is not absence of the "
                "endpoint, and this function has no way to say which happened"
            )
    if _endpoint_parts(requirement["endpoint"])[1] is None:
        raise EgressRequirementInvalid(
            f"{requirement['endpoint']!r} is not a host:port. Whether anything "
            "serves it is decided by matching the port, so an endpoint that "
            "cannot be parsed would be reported as unserved by default"
        )

    host = readable_host(host)
    if table is None:
        table = attribution.process_table(host)

    endpoint = requirement["endpoint"]
    covering = _covering_registrations(registry_doc, requirement)
    listeners = [row for row in listening_sockets(host, table=table)
                 if _serves(row["local"], endpoint)]
    serving = bool(listeners)

    # -- 1 · reachable -------------------------------------------------------
    #
    # Stated as a measurement of the endpoint and not as a claim about OpenAI:
    # this node cannot see OpenAI, and a report that said "OpenAI is
    # unreachable" from a refused local dial would be inferring a remote fact
    # from a local one. What it CAN say is where the client's packets stop.
    reachable = {
        "value": serving,
        "endpoint": endpoint,
        "claim": ("the local Agent can reach its outbound endpoint"
                  if serving else
                  "the local Agent cannot reach its outbound endpoint"),
        "because": (
            f"nothing is listening on {endpoint} on this node — measured "
            "against /proc/net/tcp, not inferred from a log. A client that "
            "dials it is refused, and the Agent reports the refusal as a "
            "network error, which is accurate and is not the answer"
            if not serving else
            f"{len(listeners)} socket(s) on this node serve {endpoint}"
        ),
        "measured": {
            "listeners": [{"local": row["local"], "holders": row["holders"],
                           "attributed": row["attributed"]} for row in listeners],
        },
    }

    # -- 2 · which runtime is missing ---------------------------------------
    if covering:
        state = EGRESS_REGISTERED_SERVING if serving else EGRESS_REGISTERED_NOT_SERVING
        missing = {
            "kind": covering[0]["kind"],
            "id": covering[0]["id"],
            "candidate_id": None,
            "state": state,
            "because": (
                f"{covering[0]['id']} is registered and matched by "
                f"{'+'.join(covering[0]['matched_by'])}; "
                + ("something is serving it" if serving else
                   "nothing is serving it, so the registration is a declaration "
                   "without an implementation")
            ),
        }
    else:
        state = EGRESS_UNDECLARED_PRESENT if serving else EGRESS_NOT_REGISTERED
        missing = {
            "kind": requirement.get("kind"),
            "id": None,
            "candidate_id": requirement["runtime"],
            "state": state,
            "because": (
                f"no registered resource has the id {requirement['runtime']!r} "
                f"and none carries a transport on {endpoint}. "
                + ("Something is listening there and nobody declared it, so the "
                   "runtime exists and is undeclared — §38's other failure "
                   "shape, and the more dangerous of the two because it works "
                   "until the day it does not"
                   if serving else
                   "There is no declaration to be missing FROM — the object the "
                   "Agent depends on was never registered by anyone, which is "
                   "why the failure arrives at the Agent as an undifferentiated "
                   "network error")
            ),
        }

    # -- 3 · who owns it -----------------------------------------------------
    if covering:
        owner_record = covering[0]["owner"] or {}
        owner = {
            "state": OWNER_DECLARED,
            "ref": owner_record.get("ref"),
            "owner_type": owner_record.get("type"),
            "candidates": [],
            "because": (f"{covering[0]['id']} declares it; the registry is the "
                        "declaration of record and this is that declaration"),
        }
    else:
        hidden = [row for row in listeners if not row["attributed"]]
        if hidden:
            owner = {
                "state": OWNER_UNATTRIBUTED,
                "ref": None,
                "owner_type": None,
                "candidates": [{"local": row["local"], "inode": row["inode"]}
                               for row in hidden],
                "because": (
                    f"{len(hidden)} socket(s) serve {endpoint} and are held by no "
                    "process this uid can read. That is not the same as unowned: "
                    "/proc is mounted hidepid=invisible, so a root-held forward "
                    "looks exactly like this. Who owns it cannot be established "
                    "from this node"
                ),
            }
        else:
            owner = {
                "state": OWNER_UNDECLARED,
                "ref": None,
                "owner_type": None,
                "candidates": [],
                "because": (
                    "no registration names it and no process serves it. The owner "
                    "is not hidden — it is absent, and there is nothing on this "
                    "node to attribute. §38: an owner that cannot be confirmed is "
                    "recorded as UNKNOWN_OWNER with a human decision, never "
                    "guessed, because the guess is what gets written into a "
                    "registry and believed by the next reader"
                ),
            }

    # -- 4 · may anything act ------------------------------------------------
    if covering:
        row = next((r for r in recovery_report(registry_doc)["models"]
                    if r["id"] == covering[0]["id"]), None)
        permitted = bool(row and row["automatic"])
        recovery = {
            "permitted": permitted,
            "model": (row or {}).get("recovery"),
            "protection": covering[0]["protection"],
            "declared": bool(row and row["declared"]),
            "because": (
                f"the registration declares recovery={((row or {}).get('recovery'))!r} "
                f"with protection={covering[0]['protection']!r}; "
                + ("automatic recovery is permitted" if permitted else
                   "automatic recovery is not permitted, and the reason is in "
                   "the registration rather than in this report")
            ),
        }
    else:
        recovery = {
            "permitted": False,
            "model": None,
            "protection": None,
            "declared": False,
            "because": (
                "there is no resource to recover. Automatic recovery restores a "
                "declared resource to a desired state that was also declared, and "
                "nothing here declares either — so the only way to 'recover' this "
                "endpoint is to invent a resource and an owner, start something "
                "on behalf of someone unknown, and then have every later report "
                "treat that invention as a fact"
            ),
        }

    # Three cases, and the first is the one §38 decides. An endpoint nobody
    # declared gets a human even when something is serving it — an undeclared
    # runtime that works is the same defect as one that does not, minus the
    # symptom that would have found it.
    if not covering:
        action = "NEEDS_HUMAN"
    elif not serving:
        action = "RECONCILE" if recovery["permitted"] else "NEEDS_HUMAN"
    else:
        action = "NONE"

    return {
        "schema": EGRESS_SCHEMA,
        "requirement": {
            "runtime": requirement["runtime"],
            "kind": requirement.get("kind"),
            "endpoint": endpoint,
            "named_by": list(requirement.get("named_by") or []),
            "because": requirement.get("because"),
        },
        "registrations": [{k: v for k, v in one.items() if k != "resource"}
                          for one in covering],
        "answers": {
            "reachable": reachable,
            "which_runtime_is_missing": missing,
            "who_owns_it": owner,
            "auto_recovery_permitted": recovery,
        },
        "action": action,
        "note": (
            "`action` uses the same vocabulary as every other resource in this "
            "runtime — NONE · DEFER · NEEDS_HUMAN · RECONCILE — because an "
            "egress problem and a container problem are answered by the same "
            "decision procedure. What this function never returns is a bare "
            "network error: that is the name of a symptom, and the four answers "
            "above are the names of the things that were missing"
        ),
    }
