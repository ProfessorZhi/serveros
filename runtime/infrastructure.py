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
# Loaded here rather than lazily inside `tunnel_observation`, because the
# confidence vocabulary below is a value this module exports and an export
# cannot be resolved at call time. `graph.py` loads only `attribution.py`, so
# there is no cycle — and both modules reach each other through the same
# `sys.modules` cache, so a caller that loaded one gets the other for free.
graph = _load("serveros_runtime_graph", "graph.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN

# §12's confidence axis, taken from the module that declares it rather than
# spelled a second time here. `AUTHORITATIVE` is NOT the same word as `FACT`:
# provenance says how a value was obtained, confidence says how far what it
# obtained binds. A second copy of these three would be a second answer waiting
# to disagree about a fourth.
AUTHORITATIVE = graph.AUTHORITATIVE

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


def _declared_tunnel_pids(resource) -> list[int]:
    """The process ids a registration says belong to this tunnel.

    Two places, because a registration may say it either way and both are
    declarations: a `pids` list on the record, and a `pid` on any of its
    probes. Nothing else is read — a pid is never guessed from a port or a
    command line, because that inference is what `tunnel_inventory`'s join
    exists to avoid making.

    This function is the PRODUCER for a key that `tunnel_inventory` has been
    consuming since it was written. Until 2026-09-14 `declared_tunnels` did
    not emit `pids`, so `declared_pids` was the empty set on every host, every
    observed forwarder was reported as `undeclared`, and the join the note
    described could never match anything. The empty result was correct for
    this host by accident: with no tunnel registered, every forwarder IS
    undeclared. The defect was that the two cases were indistinguishable.
    """
    values = list(resource.record.get("pids") or [])
    for probe in resource.probes:
        if "pid" in probe:
            values.append(probe.get("pid"))
    return sorted({int(v) for v in values if str(v).strip().isdigit()})


def declared_tunnels(registry_doc) -> list[dict]:
    """Tunnels in the registry, with the two things a tunnel must name.

    Registration already refused anything that could never be measured (see
    `kind_requirements` in the contract), so by the time anything is returned
    from here it has both endpoints and either a transport probe or a written
    reason there is none.

    §13 needs the ownership fields beside the transport ones. They come from
    the same resource and are read here rather than in `tunnel_observation`
    for §18's reason: one fact, one producer. A second reader of the same
    registration is a second answer waiting to disagree with the first.
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
            # §13's ownership half.
            "owner_ref": resource.owner_ref,
            "owner_type": resource.owner_type,
            "scope": resource.scope,
            "lifecycle": resource.lifecycle,
            "desired_state": resource.desired,
            "device_ref": record.get("device_ref"),
            "runtime_ref": record.get("runtime_ref"),
            "pids": _declared_tunnel_pids(resource),
        })
    return out


def tunnel_health(resource, observations: dict | None = None, *,
                  observed_at: str | None = None, why: str | None = None):
    """A tunnel's four axes, with liveness honestly separated from function.

    With no observations this returns a Health whose `functional` axis is
    UNKNOWN — and the derived state for that is DEGRADED, not HEALTHY, because
    a tunnel that is up and unmeasured is the exact shape of the 2026-09-12
    endpoint. That is the whole point of routing a tunnel through `Health`
    rather than through a boolean: the rule ordering in the contract already
    puts `DEGRADED when functional: UNKNOWN` ahead of
    `HEALTHY when liveness: RUNNING, functional: OK`, so the trap is closed by
    the same table that closes it for every other resource.

    `observations` maps an axis to EITHER state string or an `Observation`. A
    string is wrapped in one here, because a caller that has only the state —
    which is the case for a sweep reading `/proc/net/tcp` — should not have to
    invent an observer to say so. An `Observation` is passed through
    untouched: it already carries its own provenance, and replacing it would
    discard the more specific of the two.

    The wrap used to be missing entirely: strings went straight to `Health`,
    which takes `Observation` objects, so every measured axis raised
    `AttributeError: 'str' object has no attribute 'state'` at `.state`. The
    caller caught it and reported the tunnel UNKNOWN, so the measurement path
    of this function was dead and reported its own bug as a gap in the
    contract's rule table. Found 2026-09-14 by
    `test_a_listener_serving_the_declared_endpoint_is_listening_not_healthy`.
    The existing tests passed only because they all hand in real
    `Observation`s, which is why the two callers never met.

    The wrapped evidence is `host-measurement`, which is what a tunnel reading
    is: a process in `/proc`, or a LISTEN socket in `/proc/net/tcp`. It is not
    `provider-response` — Docker's API is not answering this question — and
    `Observation` refuses any state above UNKNOWN without evidence strong
    enough to carry it, which is the check that keeps a string from becoming a
    verdict by being passed to the right constructor.
    """
    observations = observations or {}
    axes = {}
    for axis in health.AXES:
        if axis in observations:
            value = observations[axis]
            axes[axis] = value if isinstance(value, health.Observation) else health.Observation(
                axis=axis,
                state=value,
                observed_by={"type": "host-measurement", "id": "tunnel-observation"},
                observed_at=observed_at,
                evidence={
                    "type": "host-measurement",
                    "why": why or "read from /proc and /proc/net/tcp on this node",
                },
            )
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


def host_sockets(host=None, *, table=None) -> list[dict]:
    """Every TCP socket row this uid can see, with the visible pids holding it.

    The one walk of `/proc/net/tcp`, `/proc/net/tcp6` and `/proc/<pid>/fd` in
    this codebase. `listening_sockets` filters it, and §14's port view filters
    it a different way — the two questions must not drift, and a second walk is
    how one of them keeps answering about a host that has since changed.

    The join direction is the one `attribution.sockets_of` makes, run
    backwards: collect every visible process's socket inodes, then read the net
    tables and report which inode belongs to whom. `/proc/net/tcp` is
    per-network-namespace and does not say who owns a row, so this is the only
    direction that answers anything.

    Rows are returned UNFILTERED by state. Filtering at the producer is how a
    caller that wants ESTABLISHED ends up with a second producer and a second
    answer — the defect this function exists to prevent, one level up.
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
        {"local": row["local"], "remote": row["remote"], "state": row["state"],
         "inode": inode,
         "holders": sorted(holders.get(inode, ())),
         "attributed": inode in visible_inodes}
        for inode, row in net.items()
    ]
    return sorted(out, key=lambda row: (row["state"], row["local"], row["inode"]))


def listening_sockets(host=None, *, table=None) -> list[dict]:
    """Every local TCP LISTEN socket, and whether a visible process holds it.

    Split out of `unreadable_listeners` because two questions are asked of this
    walk and they must not drift: "which listeners are unattributed" (the
    blind-spot report) and "is anything listening on the port a declaration
    names" (§58's egress answer). Two copies of a /proc walk is how one of them
    keeps answering the original question about a host that has since changed —
    and the second question is the one that decides whether an Agent can reach
    the network, so a stale answer there is the expensive kind.
    """
    return [{"local": row["local"], "inode": row["inode"],
             "holders": row["holders"], "attributed": row["attributed"]}
            for row in host_sockets(host, table=table) if row["state"] == "LISTEN"]


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
        # Whether the join HAD a key at all. `undeclared_count ==
        # observed_count` means one of two very different things — nothing is
        # registered, or nothing registered names a pid — and a reader who
        # cannot tell them apart cannot tell a clean host from a broken join.
        "join_coverage": {
            "declared_pids": sorted(declared_pids),
            "join_had_a_key": bool(declared_pids),
            "observed_forwarders": len(observed),
            "matched": len(observed) - len(undeclared),
        },
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


# ---------------------------------------------------------------------------
# §13 · Tunnel observation, identity and ownership
#
# Everything below OBSERVES. Nothing here restarts, re-establishes, replaces or
# removes a tunnel — §13 forbids repairing history, and the 2026-09-12 incident
# in this module's own docstring is what happens when a supervisor treats a
# channel it cannot measure as a channel it should re-open.
#
# The vocabulary is not invented here. `scope` is the registry's `user|system`;
# §13's four words (session / task / persistent / unknown) are the contract's
# existing `lifecycle_classes` — `session-scoped`, `task-scoped`, `persistent`
# and a lifetime that is not declared — and they are reported under the name
# the contract already gave them. A parallel enum spelled `session|task|
# persistent` would be a second vocabulary for one concept, and the two would
# disagree the first time somebody changed one of them.
# ---------------------------------------------------------------------------

import fnmatch  # noqa: E402 - kept beside the one section that uses it

# The normalized field set §13 asks for, in the brief's order. Present on every
# row, including the ones this host cannot fill: an absent field and a null
# field read differently, and only one of them is a statement.
TUNNEL_FIELDS = (
    "tunnel_ref", "runtime_ref", "device_ref", "provider", "owner_ref",
    "owner_type", "scope", "lifecycle_class", "direction", "local_endpoint",
    "remote_endpoint", "desired_state", "actual_state", "health",
    "last_healthy_at", "restart_policy", "criticality", "observed_at",
    "provenance", "freshness",
    # §13's invariant needs somewhere to be visible, so the confidence in the
    # ownership answer and any refusal to honour it are fields rather than
    # prose in a report.
    "ownership_confidence", "ownership",
)

# How far back an observation of a tunnel is still talking about now. Declared
# rather than assumed, and deliberately the same number `graph.py` uses for a
# provider API read (`docker-inspect`) rather than the one it uses for a
# registry read: this window is about the OBSERVATION — whether the process
# table and the socket table we just read are still describing the present —
# and not about how often the registration document changes. Those are two
# different bars, and taking the registry's day-long one here would let an
# observation from this morning be reported as current.
TUNNEL_FRESH_WITHIN_SECONDS = graph.FRESH_WITHIN_SECONDS["docker-inspect"]


def ownership_rule() -> dict:
    """The contract's `ownership:` block, or `{}`.

    Read through the existing contract loader rather than by opening the YAML
    a second time. Two readers of one file is how the two come to disagree
    about it, and this is the block that decides whether a resource may be
    owned by the session that created it.
    """
    try:
        return registry.contract().get("ownership") or {}
    except Exception:  # noqa: BLE001 - an unreadable contract is "no rule"
        return {}


def owner_is_session(owner_ref, *, rule: dict | None = None) -> bool:
    """Whether an owner ref denotes a session lifetime.

    Matched against the contract's own `session_owner_ids` globs, so adding a
    third session-shaped owner id is a declaration rather than a code change.
    An unreadable rule matches nothing, which is the answer that keeps the
    caller from treating "we could not check" as "this is a session".
    """
    if not owner_ref:
        return False
    patterns = (rule if rule is not None else ownership_rule()).get(
        "session_owner_ids") or []
    return any(fnmatch.fnmatchcase(str(owner_ref), str(p)) for p in patterns)


def ownership_verdict(owner_ref, lifecycle_class, *, rule: dict | None = None) -> dict:
    """Whether a registration may be owned by the ref it names.

    §13's invariant, as a check rather than a paragraph: a resource that must
    outlive the Agent session that caused it needs a durable owner, and an
    Agent session may cause creation without being the owner of record.

    Three outcomes, and the third is why this is not a boolean:

      CONSISTENT   a durable owner, or a session owner of a session-lifetime
                   resource.
      VIOLATION    a session owner of a `persistent` or `task-scoped`
                   resource. The contract's disposition is REFUSE, and the
                   because is the contract's own prose about the incident.
      UNKNOWN      the contract could not be read, or the lifecycle was not
                   declared. NOT reported as consistent: the whole point is
                   that this shape fails silently, so an unchecked one is a
                   finding.
    """
    rule = rule if rule is not None else ownership_rule()
    if not rule:
        return {
            "state": UNKNOWN,
            "disposition": None,
            "because": (
                "the ownership rule could not be read, so this registration "
                "was not checked against it. That is not the same as it "
                "passing the check"
            ),
        }
    if not lifecycle_class:
        return {
            "state": UNKNOWN,
            "disposition": None,
            "because": (
                "no lifecycle class is declared, so whether a session may own "
                "this cannot be decided either way"
            ),
        }
    if not owner_is_session(owner_ref, rule=rule):
        return {
            "state": "CONSISTENT",
            "disposition": None,
            "because": (
                f"{owner_ref or 'no owner'} is not a session-shaped id, so the "
                f"session-ownership rule does not constrain it"
            ),
        }
    permitted = (rule.get("permitted") or {}).get("session_owner_lifecycle") or []
    if lifecycle_class in permitted:
        return {
            "state": "CONSISTENT",
            "disposition": None,
            "because": (
                f"{owner_ref} is a session and {lifecycle_class} is a session "
                f"lifetime, which is the pairing the contract permits"
            ),
        }
    forbidden = (rule.get("permitted") or {}).get("forbidden") or {}
    return {
        "state": "VIOLATION",
        "disposition": (rule.get("on_violation") or {}).get("disposition"),
        "because": forbidden.get(lifecycle_class) or (
            f"{owner_ref} denotes a session lifetime and {lifecycle_class} "
            f"outlives it"
        ),
        "alternative": (rule.get("on_violation") or {}).get("alternative"),
    }


def session_end_consequence(row: dict, *, creating_session_present) -> dict:
    """What an Agent session ending means for a tunnel. Usually: nothing.

    §13's most important invariant, written as a function so that it is one
    thing rather than a habit:

        a task-scoped tunnel is NOT an agent-session-owned tunnel, and the
        session that caused its creation ending must not make it an orphan.

    The failure this refuses is concrete. A tunnel a task depended on was
    registered with session lifetime; an unrelated Agent session ended; the
    task lost its transport mid-flight while the far side was healthy
    throughout. Every observation up to that moment said the tunnel was fine,
    because it was — the resource was healthy and its OWNERSHIP was wrong.

    `creating_session_present` is deliberately accepted and deliberately not
    enough on its own to change the answer. It is reported so a reader can see
    what was known, and it can only ever move the result towards "a human
    should look", never towards `orphaned`.

    Returns `{"orphaned", "state", "because"}`. `orphaned` is false for every
    durable lifecycle class, and it is false — not `None` — because a false
    here is a decision this function is entitled to make from the declared
    lifecycle alone.
    """
    lifecycle = row.get("lifecycle_class")
    owner_side = (row.get("ownership") or {}).get("state")
    if lifecycle in ("persistent", "task-scoped"):
        return {
            "orphaned": False,
            "state": "UNAFFECTED",
            "because": (
                f"this tunnel is {lifecycle}: it is owned by the work item "
                f"that depends on it, not by the session that created it, so "
                f"a session ending does not reach it. Two sessions working one "
                f"item is a normal case, and the second one ending must not "
                f"reclaim the first one's transport"
            ),
        }
    if lifecycle in ("session-scoped", "disposable"):
        return {
            "orphaned": bool(creating_session_present is False),
            "state": "SESSION_LIFETIME",
            "because": (
                f"this tunnel is {lifecycle} and was declared to end with its "
                f"session, so the session ending is its lifetime and not an "
                f"orphaning"
            ),
        }
    return {
        "orphaned": False,
        "state": UNKNOWN,
        "because": (
            f"no lifecycle class is declared"
            + (f" and its ownership checks out as {owner_side}" if owner_side else "")
            + "; this is NOT an orphan, because orphanhood is a claim about a "
              "declared lifetime and there is no declaration to claim it from"
            if not lifecycle else " for this tunnel"
        ),
    }


def _tunnel_direction(local, remote) -> dict:
    """Which way a tunnel points, from its two declared endpoints.

    A reading of the declaration, so INFERRED however confident it reads.
    `127.0.0.1:17890 -> remote:443` is outbound by shape; the shape is not a
    measurement of traffic.
    """
    lhost, lport = _endpoint_parts(local)
    rhost, _ = _endpoint_parts(remote)
    if lhost is None or rhost is None:
        return {
            "value": UNKNOWN,
            "status": UNKNOWN,
            "rule": "declared endpoint shape: which end is loopback",
            "why": (
                "the two endpoints are not both of the form host:port, so the "
                "direction cannot be read from them"
            ),
        }
    if _is_loopback(lhost) and not _is_loopback(rhost):
        value = "outbound"
    elif _is_loopback(rhost) and not _is_loopback(lhost):
        value = "inbound"
    elif _is_loopback(lhost) and _is_loopback(rhost):
        value = "local"
    else:
        value = UNKNOWN
    return {
        "value": value,
        "status": INFERRED if value != UNKNOWN else UNKNOWN,
        "rule": "declared endpoint shape: which end is loopback",
        "why": None if value != UNKNOWN else (
            f"neither endpoint is loopback ({lhost}, {rhost}), so this "
            f"declaration does not say which side initiates"
        ),
    }


def _tunnel_liveness(*, local_endpoint, observed_pids, visible_pids, listeners) -> tuple:
    """`actual_state` and how it was reached — with the hidepid trap closed.

    The trap is the whole reason this is a function. On this host `/proc` is
    mounted `hidepid=invisible`, so an sshd-owned forward is NOT in the
    process table this uid can read. Every naive reading of that is
    `STOPPED` — a live tunnel reported as down, which is the `docker ps`
    mistake in a new place and the direction that gets something restarted.

    So a declared pid that is not visible is UNKNOWN and says why. Only a
    measurement that could have seen it and did not may say STOPPED, and on
    this host no such measurement exists for a root-owned forward.

    The endpoint is a PARAMETER rather than read off the row. It was read as
    `row["local_endpoint"]` and the producer spells that key `local`, so the
    listener branch below was unreachable on the real path: every declared
    tunnel with a bound socket reported "nothing listens at None". Only the
    fixtures could reach it, because a fixture written by the same hand spells
    both sides the same way. Found 2026-09-14 by
    `test_a_listener_serving_the_declared_endpoint_is_listening_not_healthy`,
    which goes through `declared_tunnels` rather than constructing a row.
    """
    local = local_endpoint
    if observed_pids and (observed_pids & visible_pids):
        return "RUNNING", FACT, (
            "a forwarding process this uid can read is running with a pid the "
            "registration names"
        )
    served = [s for s in listeners if local and _serves(s["local"], local)]
    if served:
        return "LISTENING", FACT, (
            f"a LISTEN socket at {served[0]['local']} serves the declared "
            f"local endpoint {local}"
        )
    if observed_pids:
        hidden = sorted(observed_pids - visible_pids)
        return UNKNOWN, UNKNOWN, (
            f"the registration names pid(s) {hidden} and none is visible in "
            f"the process table this uid can read. On a host with "
            f"hidepid=invisible that is what a root-owned forward looks like, "
            f"so this is UNKNOWN and NOT stopped: reporting a live tunnel as "
            f"down is the direction that gets it restarted"
        )
    if not local:
        return UNKNOWN, UNKNOWN, (
            "the registration declares neither a pid nor a local endpoint, so "
            "there is nothing on this host to look for"
        )
    return UNKNOWN, UNKNOWN, (
        f"nothing listens at {local} and no pid is declared for this tunnel, "
        f"so this host has no measurement that could answer whether it is up"
    )


def tunnel_observation(host=None, *, registry_doc, device_ref=None,
                       observed_at=None, now=None) -> dict:
    """§13. Every declared tunnel, normalized, with its ownership check.

    Declared and observed are kept apart on purpose, and the separation is the
    §13 rule:

      declared   the registry says this tunnel exists and who owns it. Its
                 owner may be authoritative.
      observed   a process this uid can see is forwarding, and no
                 registration covers it. It is a CANDIDATE — it gets a row so
                 that it is visible, and it gets `ownership_confidence:
                 INFERRED` at best and no owner at all. A process that is
                 forwarding has not declared itself anything.

    Nothing here reads `~/.bashrc`, and nothing derives an owner from one. The
    environment this shell inherits is evidence that somebody once started a
    forward; it is not a producer of tunnel truth, and a tunnel that only ever
    existed as an exported variable is exactly the case this surface exists to
    report as UNKNOWN rather than to explain away.

    `runtime_ref` is reported only when a declaration names it. Inferring it
    from which container happens to publish a nearby port would be the
    name-similarity mistake §12 forbids, one layer down.
    """
    host = readable_host(host)
    table = attribution.process_table(host)
    visible_pids = {int(p) for p in table}
    listeners = listening_sockets(host, table=table)
    forwarders = observe_forwarders(host, table=table)
    rule = ownership_rule()

    def _aged(source):
        return graph.freshness_of(
            observed_at, now=now, source=source,
            within_seconds=TUNNEL_FRESH_WITHIN_SECONDS,
        )

    rows: list[dict] = []
    declared_pids: set[int] = set()
    for declared in declared_tunnels(registry_doc):
        pids = {int(p) for p in (declared.get("pids") or []) if str(p).isdigit()}
        declared_pids |= pids
        actual, actual_status, actual_why = _tunnel_liveness(
            local_endpoint=declared.get("local"),
            observed_pids=pids, visible_pids=visible_pids, listeners=listeners,
        )
        ownership = ownership_verdict(
            declared.get("owner_ref"), declared.get("lifecycle"), rule=rule,
        )
        row = {
            **_declared_tunnel_identity(declared),
            "actual_state": actual,
            "actual_state_status": actual_status,
            "actual_state_why": actual_why,
            "observed_at": observed_at,
            "freshness": _aged("registry"),
            "provenance": {
                "identity": FACT,
                "endpoints": FACT,
                "owner": FACT if declared.get("owner_ref") else UNKNOWN,
                "actual_state": actual_status,
                "why": (
                    "the declaration is this node's own registry, so the "
                    "identity and the endpoints are facts about a declaration "
                    "rather than measurements of a channel"
                ),
            },
            "ownership": ownership,
            "ownership_confidence": _ownership_confidence(
                declared, ownership, actual_status,
            ),
            "health": _tunnel_health_row(
                _resource_by_id(registry_doc, declared.get("id")), declared, actual,
                observed_at=observed_at, why=actual_why),
            "session_end_consequence": None,  # filled below, needs the row
        }
        row["session_end_consequence"] = session_end_consequence(
            row, creating_session_present=None,
        )
        rows.append(row)

    declared_runtime_refs = {
        r.get("runtime_ref") for r in rows if r.get("runtime_ref")
    }
    undeclared = []
    for forwarder in forwarders:
        pid = forwarder["pid"]
        if pid in declared_pids:
            continue
        undeclared.append({
            "tunnel_ref": f"tunnel:observed:{pid}",
            "runtime_ref": None,
            "device_ref": device_ref,
            "provider": forwarder["transport_class"],
            "owner_ref": None,
            "owner_type": None,
            "scope": None,
            "lifecycle_class": None,
            "direction": _tunnel_direction(None, None),
            "local_endpoint": None,
            "remote_endpoint": None,
            "desired_state": None,
            "actual_state": "RUNNING",
            "actual_state_status": FACT,
            "actual_state_why": (
                "a process this uid can read is forwarding right now. What it "
                "forwards, for whom, and until when are not in its argv"
            ),
            "health": None,
            "last_healthy_at": None,
            "restart_policy": None,
            "criticality": None,
            "observed_at": observed_at,
            "freshness": _aged("/proc/<pid>/cmdline"),
            "provenance": {
                "identity": INFERRED,
                "endpoints": UNKNOWN,
                "owner": UNKNOWN,
                "actual_state": FACT,
                "why": (
                    "a forwarding process is a measurement. It is not a "
                    "declaration, so nothing here makes it ours"
                ),
            },
            "ownership": {
                "state": UNKNOWN,
                "disposition": None,
                "because": (
                    "a process that is forwarding has not said who it is for. "
                    "This is a candidate, and a candidate is never an owner"
                ),
            },
            "ownership_confidence": "UNKNOWN",
            "observed_pid": pid,
            "age_seconds": forwarder.get("age_seconds"),
        })

    return {
        "schema": "serveros/tunnel-observation.v1",
        "device_ref": device_ref,
        "observed_at": observed_at,
        "declared": sorted(rows, key=lambda r: r["tunnel_ref"]),
        "declared_count": len(rows),
        "undeclared": sorted(undeclared, key=lambda r: r["tunnel_ref"]),
        "undeclared_count": len(undeclared),
        "blind_spots": tunnel_blind_spots(host, table=table),
        "coverage": {
            "declared_tunnels": len(rows),
            "with_authoritative_owner": sum(
                1 for r in rows if r["ownership_confidence"] == "AUTHORITATIVE"
            ),
            "with_unknown_actual_state": sum(
                1 for r in rows if r["actual_state"] == UNKNOWN
            ),
            "ownership_violations": sorted(
                r["tunnel_ref"] for r in rows
                if (r["ownership"] or {}).get("state") == "VIOLATION"
            ),
            "declared_runtime_refs": sorted(x for x in declared_runtime_refs if x),
            "rule_readable": bool(rule),
        },
        "note": (
            "§13: this is an observation and not a repair. No tunnel here was "
            "restarted, re-established, replaced or removed, and no historical "
            "binding was corrected — a tunnel repaired during an audit leaves "
            "no evidence of the defect that the audit existed to find."
        ),
    }


def _resource_by_id(registry_doc, resource_id):
    """One registration, or None. `Registry.get` is O(1) and already validates."""
    if not resource_id:
        return None
    try:
        return registry_doc.get(resource_id)
    except Exception:  # noqa: BLE001 - a lookup that misses is not an outage
        return None


def _declared_tunnel_identity(declared: dict) -> dict:
    """The declared half of a §13 row, under the brief's field names."""
    local = declared.get("local")
    remote = declared.get("remote")
    return {
        "tunnel_ref": f"tunnel:{declared['id']}" if declared.get("id") else None,
        "runtime_ref": declared.get("runtime_ref"),
        "device_ref": declared.get("device_ref"),
        "provider": declared.get("transport_class"),
        "owner_ref": declared.get("owner_ref"),
        "owner_type": declared.get("owner_type"),
        "scope": declared.get("scope"),
        "lifecycle_class": declared.get("lifecycle"),
        "direction": _tunnel_direction(local, remote),
        "local_endpoint": local,
        "remote_endpoint": remote,
        "desired_state": declared.get("desired_state"),
        "last_healthy_at": None,
        "restart_policy": declared.get("recovery"),
        "criticality": declared.get("criticality"),
        "declared_pids": declared.get("pids") or [],
        "protected": declared.get("protection"),
        "unknowns": {
            "last_healthy_at": (
                "nothing on this host records when a tunnel was last healthy. "
                "A probe's current result is not a history, and reporting the "
                "observation time here would make every tunnel look like it "
                "was healthy a moment ago"
            ),
             "runtime_ref": None if declared.get("runtime_ref") else (
                "no declaration names the runtime this tunnel belongs to, and "
                "it is not inferred from a nearby port or a similar name"
            ),
        },
    }


def _ownership_confidence(declared, ownership, actual_status) -> str:
    """How far the ownership answer binds.

    AUTHORITATIVE requires a declaration AND a check that passed against the
    rule. A registration whose owner field looks fine but whose lifecycle the
    rule forbids is INFERRED at best — the v1.0.1 defect this exists to catch
    is that such a resource reads as owned right up to the moment it is
    destroyed.
    """
    if not declared.get("owner_ref"):
        return UNKNOWN
    if (ownership or {}).get("state") == "VIOLATION":
        return INFERRED
    if (ownership or {}).get("state") == UNKNOWN:
        return INFERRED
    return AUTHORITATIVE


def _tunnel_health_row(resource, declared: dict, actual: str, *,
                       observed_at=None, why=None):
    """The health model's verdict for one tunnel, or why it has none.

    `health.Health` raises `HealthUndetermined` on an axis combination the
    contract's rules do not cover, and that exception is deliberately fatal
    everywhere else — a wrong-for-this-resource fallback word is worse than an
    exception. Here it is caught, because an observation sweep that dies on
    one row reports nothing about the other thirty; the row says UNKNOWN and
    names the combination, so the defect is still visible.

    The two failures are reported SEPARATELY. They were one branch, and it
    told every reader "gap in the contract's rule table" — including about a
    `TypeError` in this module, which is how a broken measurement path got
    reported as a contract gap for as long as it did.
    """
    if resource is None:
        return {"state": UNKNOWN, "why": "the resource is no longer in the registry"}

    axes: dict = {}
    if actual == "RUNNING":
        axes["liveness"] = "RUNNING"
    elif actual == "LISTENING":
        # A local socket proves a listener, not that the channel to the far
        # end carried anything. Liveness RUNNING with transport UNKNOWN is the
        # honest pair, and it derives DEGRADED rather than HEALTHY.
        axes["liveness"] = "RUNNING"
    try:
        verdict = tunnel_health(resource, axes or None,
                                observed_at=observed_at, why=why)
        return {
            "state": verdict.state,
            "reason": verdict.reason,
            "axes": {axis: verdict.axis(axis).state for axis in health.AXES},
            "why": None,
        }
    except health.HealthUndetermined as exc:
        return {
            "state": UNKNOWN,
            "axes": None,
            "why": (
                f"the contract's rule table covers no combination for this "
                f"tunnel ({exc}). That is a gap in the declaration, not a "
                f"statement about the tunnel"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - one row must not end the sweep
        return {
            "state": UNKNOWN,
            "axes": None,
            "why": (
                f"the health model raised {type(exc).__name__}: {exc}. That is "
                f"a defect in this surface, NOT a statement about the tunnel "
                f"and NOT a gap in the contract's rule table"
            ),
        }
