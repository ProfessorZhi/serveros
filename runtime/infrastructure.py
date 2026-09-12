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


def unreadable_listeners(host=None, *, table=None) -> dict:
    """Local TCP listeners that belong to no process this uid can read.

    The join is the one `attribution.sockets_of` already makes, run backwards:
    collect every visible process's socket inodes, then walk /proc/net/tcp and
    report the LISTEN rows whose inode nobody visible holds.

    This is the honest form of the question. On a host with `hidepid=invisible`
    those sockets are not "unowned" — they are owned by a process this uid is
    not allowed to see, which is what sshd's forwards and docker-proxy look
    like from here. Reporting them as unattributed is correct; reporting them
    as nothing is the `docker ps` mistake in a new place.
    """
    host = readable_host(host)
    if table is None:
        table = attribution.process_table(host)

    visible_inodes: set[str] = set()
    for pid in table:
        for fd in host.listdir(f"/proc/{pid}/fd"):
            target = host.readlink(f"/proc/{pid}/fd/{fd}")
            if target and target.startswith("socket:["):
                visible_inodes.add(target[len("socket:["):-1])

    net = {}
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        raw = host.read_text(path)
        if raw:
            net.update(attribution.parse_net_tcp(raw))

    listening = {inode: row for inode, row in net.items() if row["state"] == "LISTEN"}
    unreadable = sorted(
        row["local"] for inode, row in listening.items() if inode not in visible_inodes
    )
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
