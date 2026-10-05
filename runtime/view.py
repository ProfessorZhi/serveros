#!/usr/bin/env python3
"""The shared answer layer: one implementation, three callers.

`serverosctl`, `serverosd` and the MCP adapter must give the SAME answer to the
same question. If each computed its own there would be three implementations of
one policy, which is not redundancy — it is an eventual disagreement, and the
caller has no way to know which one it just talked to. So the answers live
here, and the three surfaces are transports.

What "the same answer" does NOT mean
------------------------------------
The CLI and the daemon measure at different TIMES, and that is fine as long as
it is labelled. A `serverosctl` answer is a fresh measurement taken by that
process, seconds old. A daemon answer is the one it took on its last tick, and
its last tick may have been four minutes ago. "The node is fine" and "the node
was fine four minutes ago" are the same JSON and different facts, so every
payload carries `answered_by` and `measured_at`.

`retry_budget` is the one exception and it is deliberate: the budget is
persisted state that the daemon owns, so it is READ from disk rather than
recomputed. Recomputing it would produce a fresh, empty budget every time —
which is exactly the bug the persistence exists to prevent.

An absence is never returned as a measurement
---------------------------------------------
Every whole-host answer carries the limits of what produced it:

  * `coverage`   — how much of /proc this uid could actually read
  * `unchecked`  — which detector inputs were absent, so an empty signal list
                   is not read as "nothing is wrong"
  * chain links with status UNKNOWN, with the reason, so a hop the node could
                   not prove is not reported as a hop it did not need

That is the §4 rule applied to the query layer. A module that returns a clean
answer it cannot support is worse than one that fails.
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


attribution = _load("serveros_runtime_attribution", "attribution.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
graph = _load("serveros_runtime_graph", "graph.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
containers = _load("serveros_runtime_containers", "containers.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
infra = _load("serveros_runtime_infrastructure", "infrastructure.py")
# §14/§15/§16/§16A/§17. Loaded under the names the rest of the tree already
# uses — `runtime_hygiene.py` in particular refuses to be loaded as
# `serveros_runtime_hygiene`, because the two modules answer different
# questions about containment and one name for both is how a reader ends up
# holding the wrong one.
ports_mod = _load("serveros_runtime_ports", "ports.py")
protection_mod = _load("serveros_runtime_protection", "protection.py")
fs_hygiene_mod = _load("serveros_runtime_fs_hygiene", "fs_hygiene.py")
desired_mod = _load("serveros_runtime_desired", "desired.py")
runtime_hygiene_mod = _load("serveros_runtime_runtime_hygiene", "runtime_hygiene.py")
# §18. The resource anomaly catalogue, and the container hygiene report it
# classifies from. `hygiene.py` is loaded under the name `daemon.py` already
# gives it — the third hygiene module in this file, and the three answer
# different questions: `hygiene.py` classifies CONTAINERS, `runtime_hygiene.py`
# classifies REGISTRATIONS, `fs_hygiene.py` classifies ROOTS.
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
resource_anomaly_mod = _load("serveros_runtime_resource_anomaly", "resource_anomaly.py")
resource_policy_mod = _load("serveros_runtime_resource_policy", "resource_policy.py")
# SYS-02. The kernel and the producer of `state/serveros/resource-leases/`,
# loaded under the names `daemon.py` and `cli.py` already give them. The kernel
# is pure and holds no root; the store is the only thing here that reads the
# namespace, so there is exactly one place that knows where it is.
environment_mod = _load("serveros_runtime_environment", "environment.py")
lease_store_mod = _load("serveros_runtime_lease_store", "lease_store.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN

DEFAULT_MEASURER = "direct-measurement"


class UnknownSubject(Exception):
    """The caller named a group or process this node cannot see.

    Raised rather than answered with an empty record. `runtime group typo` and
    `runtime group <a group that just exited>` must not both be a clean, empty
    success — the first is a mistake and the second is a finding.
    """


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def proc_host(host=None):
    """The `attribution.LiveHost` this module needs, checked rather than assumed.

    Delegates to `infrastructure.readable_host` rather than repeating the
    check: two copies of a capability guard is how one of them ends up without
    the newest capability on it.
    """
    return infra.readable_host(host)


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _now() -> float:
    """The epoch clock, for the one question that needs a comparison rather
    than a stamp: whether a lease reference is past its own `expires_at`.
    `_now_iso` above is for records; a TTL cannot be evaluated against text.
    """
    import time

    return time.time()


def _envelope(payload: dict, *, answered_by: str, measured_at: str | None = None) -> dict:
    """Every answer says who produced it and when.

    Not decoration. `serverosctl` and the daemon can both answer `runtime
    groups`, and the two answers are measurements taken at different moments.
    Without this field a caller cannot tell a fresh reading from a four-minute
    old one, and will treat whichever it got as current.
    """
    return {
        "answered_by": answered_by,
        "measured_at": measured_at or _now_iso(),
        **payload,
    }


def _table(host) -> dict:
    return attribution.process_table(host)


def _groups(table: dict) -> list[dict]:
    return graph.build_groups(table)


def _group_of(table: dict, ref: str) -> dict | None:
    """A group by ref, id, root pid, or ANY member pid.

    `graph.find_group` matches the first three. The member-pid case is added
    here because `runtime tree <pid>` is the form an operator types while
    looking at a process, and answering "no such group" for a pid that is
    plainly in one would be a riddle rather than a refusal.
    """
    found = graph.find_group(ref, table)
    if found is not None:
        return found
    if str(ref).isdigit() and int(ref) in table:
        pid = int(ref)
        for group in _groups(table):
            if pid in group["members"]:
                return group
    return None


def _available(table: dict, *, limit: int = 20) -> dict:
    """What the caller could have meant instead. Attached to every refusal."""
    groups = _groups(table)
    return {
        "group_refs": [g["ref"] for g in groups][:limit],
        "group_count": len(groups),
        "root_pids": [g["root_pid"] for g in groups][:limit],
        "note": (
            "refs are derived from cgroup scopes, so a ref that worked a minute "
            "ago can be gone because the scope is gone — that is a finding, not "
            "a typo."
        ),
    }


# ---------------------------------------------------------------------------
# §3/§5 — runtime groups and the chain
# ---------------------------------------------------------------------------


def _group_row(group: dict) -> dict:
    aggregate = group.get("aggregate") or {}
    return {
        "ref": group["ref"],
        "kind": group["kind"],
        "id": group["id"],
        "root_pid": group["root_pid"],
        "scope": group["scope"],
        "agent": group.get("agent"),
        "session_ref": group.get("session_ref"),
        "container_ref": group.get("container_ref"),
        "member_count": len(group["members"]),
        "members": group["members"],
        "member_roles": group.get("member_roles"),
        "aggregate": aggregate,
        # §14: the aggregate plus its largest terms. A sum without them is the
        # one shape a real consumer can hide in — see `graph.contributors()`.
        "contributors": group.get("contributors"),
        "reparented_to_init": (group.get("tree") or {}).get("reparented_to_init"),
        # The class, the role and the cgroup clause behind them. Carried on the
        # row because an alert says a group is an orphan: without this the
        # reader cannot see the evidence that made something NOT be one, and a
        # verdict whose evidence is unreachable is a verdict nobody can check.
        "ownership": group.get("ownership"),
    }


def runtime_groups(*, host=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """Every runtime group on this host, largest by summed lifetime CPU first."""
    host = proc_host(host)
    table = _table(host)
    groups = _groups(table)
    return _envelope({
        "count": len(groups),
        "groups": [_group_row(g) for g in groups],
        "coverage": attribution.coverage(host, table),
        "note": (
            "the unit of reasoning is the group, never the pid. One agent task "
            "routinely owns a proxy, a supervisor, several MCP servers and a "
            "browser helper, and judging them one at a time is how a live task "
            "gets half-reclaimed."
        ),
    }, answered_by=answered_by)


def runtime_group(ref: str, *, host=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """One group: who is in it, what it costs, and the chain that explains it."""
    host = proc_host(host)
    table = _table(host)
    group = _group_of(table, ref)
    if group is None:
        raise UnknownSubject(
            f"no runtime group matches {ref!r} on this node. "
            f"{len(_groups(table))} group(s) are visible to this uid."
        )
    chain = graph.build_chain(group, table, host=host,
                              endpoint_map=graph.load_endpoint_map())
    return _envelope({
        "group": _group_row(group),
        "chain": chain,
        "unknown_links": unknown_links(chain),
        "coverage": attribution.coverage(host, table),
        "available": None,
    }, answered_by=answered_by)


def unknown_links(chain: dict) -> list[dict]:
    """Every hop in the chain the node could NOT prove, with its reason.

    Returned as a first-class list rather than left inside the chain, because
    the unknown hops are the answer to "what does this node not know about this
    runtime", and a reader who has to scan seven nested dicts for
    `status: UNKNOWN` will not scan them.

    The Task hop is UNKNOWN on this host by design (§4): nothing in a
    user-space reader joins a task to a process, so claiming one would be a
    guess dressed as a fact.
    """
    out = []
    for name, link in chain.items():
        if not isinstance(link, dict):
            continue
        if link.get("status") == UNKNOWN:
            out.append({"link": name, "why": link.get("why") or "not established"})
    return out


def runtime_tree(ref: str, *, host=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """The whole graph for one group: chain, process tree, and the gaps.

    `runtime group` answers "who is this". This answers "what would it take
    down", which needs the tree as well as the membership — the same three
    processes that look like three harmless rows in `ps` are one thing when the
    lineage is drawn.
    """
    host = proc_host(host)
    table = _table(host)
    group = _group_of(table, ref)
    if group is None:
        raise UnknownSubject(
            f"no runtime group matches {ref!r} on this node. "
            f"{len(_groups(table))} group(s) are visible to this uid."
        )
    chain = graph.build_chain(group, table, host=host,
                              endpoint_map=graph.load_endpoint_map())
    return _envelope({
        "group": _group_row(group),
        "chain": chain,
        "tree": group.get("tree"),
        "unknown_links": unknown_links(chain),
        "coverage": attribution.coverage(host, table),
    }, answered_by=answered_by)


# ---------------------------------------------------------------------------
# §14 — the process view, attributed
# ---------------------------------------------------------------------------


def process_top(*, limit: int = 10, by: str = "cpu", host=None,
                answered_by: str = DEFAULT_MEASURER) -> dict:
    """The busiest processes, each with what it belongs to.

    Different from `resource top`: that command is a process list, and a
    process list cannot answer "whose is this". Every row here carries its
    group, its session, its container and its role, so a process that is
    expensive and unattributable is visibly that rather than merely expensive.
    """
    if by not in ("cpu", "mem"):
        raise ValueError(f"by must be 'cpu' or 'mem', not {by!r}")
    host = proc_host(host)
    table = _table(host)

    group_of: dict[int, str] = {}
    for group in _groups(table):
        for pid in group["members"]:
            # First write wins, and groups are built largest-CPU first. A pid
            # in two groups is not possible under the scope rule; if the table
            # was read across a cgroup migration it could happen, and the
            # answer then names the group it was in when the reading started.
            group_of.setdefault(pid, group["ref"])

    def sort_key(facts):
        return (-(facts.cpu_percent or 0.0) if by == "cpu"
                else -(facts.rss_bytes or 0))

    ordered = sorted(table.values(), key=sort_key)
    rows = []
    for facts in ordered[: max(0, limit)]:
        scope = facts.cgroup or ""
        session_id = attribution.session_of_cgroup(scope)
        container_id = attribution.container_of_cgroup(scope)
        rows.append({
            "pid": facts.pid,
            "ppid": facts.ppid,
            "comm": facts.comm,
            "state": facts.state,
            "user": facts.user,
            "cpu_percent": facts.cpu_percent,
            "cpu_metric": "lifetime_average_percent_of_one_core",
            "rss_bytes": facts.rss_bytes,
            "age_seconds": facts.age_seconds,
            "num_threads": facts.num_threads,
            "role": graph.identify_role(facts)["value"],
            "agent": graph.identify_agent(facts),
            "group_ref": group_of.get(facts.pid),
            "session_ref": f"session:{session_id}" if session_id else None,
            "container_ref": f"runtime:container:{container_id}" if container_id else None,
            "systemd_unit_ref": attribution.unit_of_cgroup(scope),
            "cgroup": facts.cgroup or None,
        })
    return _envelope({
        "by": by,
        "limit": limit,
        "count": len(rows),
        "rows": rows,
        "coverage": attribution.coverage(host, table),
        "note": (
            "`cpu_percent` is a LIFETIME AVERAGE, not a sample: it is "
            "(utime+stime)/uptime, so a process at 400% for an hour and one at "
            "400% for a minute report differently and neither is a spike. Every "
            "row is a measurement; `agent` is an INFERENCE and `group_ref` is "
            "the kernel's own scope."
        ),
    }, answered_by=answered_by)


def process_trace(pid: int, *, host=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """One pid, attributed: lineage, scope, owner, and what could not be read."""
    host = proc_host(host)
    table = _table(host)
    pid = int(pid)
    record = attribution.attribute_process(pid, host, table=table)
    if record.get("status") != FACT:
        # The record from `attribute_process` IS the answer — "unreadable, and
        # here is why" — so it is returned rather than raised. It is marked so
        # the surface can exit non-zero if asked to.
        return _envelope({
            "process": record,
            "readable": False,
            "coverage": attribution.coverage(host, table),
        }, answered_by=answered_by)

    lineage = []
    for ancestor in attribution.ancestry(pid, table):
        facts = table.get(ancestor)
        lineage.append({
            "pid": ancestor,
            "comm": facts.comm if facts else None,
            "role": graph.identify_role(facts)["value"] if facts else None,
            "is_self": ancestor == pid,
        })

    group = None
    for candidate in _groups(table):
        if pid in candidate["members"]:
            group = _group_row(candidate)
            break

    return _envelope({
        "process": record,
        "readable": True,
        "ancestry": lineage,
        "descendant_count": len(attribution.descendants(pid, table)),
        "children": attribution.children_of(pid, table),
        "group": group,
        "coverage": attribution.coverage(host, table),
        "note": (
            "`owner` is the kernel-assigned scope, which is a FACT. `agent` and "
            "`task` are NOT inferred from the command line here — a command "
            "line is evidence, not an owner."
        ),
    }, answered_by=answered_by)


# ---------------------------------------------------------------------------
# §6 — container attribution
# ---------------------------------------------------------------------------


def _declared_roots() -> dict:
    paths = paths_mod.Paths()
    return {name: str(paths.root(name)) for name in paths.names}


def attribution_index(registry, *, registry_path=None) -> dict:
    """The container-attribution index for a `Registry`, or for a path.

    A `Registry` and the index `containers.attribute_all` needs are DIFFERENT
    OBJECTS: one is validated registrations keyed by resource id, the other is
    a lookup from container name to a claimed owner. Passing one where the
    other is expected does not raise a type error — `Registry.get("registry_present")`
    raises `UnknownResource`, which every caller in this file catches as "Docker
    could not be asked". The report then blames Docker for a mistake the caller
    made, and the mistake is invisible because the failure looks like a
    component outage.

    So the two accepted types are checked here, and anything else is refused by
    name. `None` means "no registry", which is a valid state: nothing is
    registered, so every container is UNKNOWN_OWNER and DEFER.
    """
    if registry is None:
        return containers.load_registry(registry_path)
    if isinstance(registry, dict):
        return registry
    path = getattr(registry, "path", None)
    if path is None and registry_path is not None:
        path = registry_path
    if path is None:
        raise TypeError(
            f"cannot build a container attribution index from "
            f"{type(registry).__name__}: it carries no document path. Pass the "
            f"Registry loaded with `path=`, or its index dict, or the path "
            f"itself. A Registry built from a document in memory has no file to "
            f"re-read and re-reading the file it looks like is how a registry "
            f"that was refused gets loaded anyway."
        )
    return containers.load_registry(path)


def container_attribution(*, docker_host=None, registry=None, registry_path=None,
                          roots=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """Every container, attributed from evidence, with the evidence kept.

    Uses `probes.LiveHost` rather than `attribution.LiveHost`: this is the one
    question in this module that shells out to Docker, and the two host classes
    are not interchangeable. It is the only place the distinction appears, and
    it appears because the wrong host fails with an AttributeError three frames
    down instead of saying so.
    """
    docker_host = docker_host or probes_mod.LiveHost()
    index = attribution_index(registry, registry_path=registry_path)
    result = containers.attribute_all(
        docker_host, registry=index,
        roots=roots if roots is not None else _declared_roots(),
    )
    return _envelope({
        **result,
        "note": (
            "`disposition` is a decision and `evidence` is an observation, and "
            "they are kept apart on purpose. An UNKNOWN_OWNER container is "
            "DEFER and is never a reclaim candidate — unknown ownership fails "
            "safe, and this layer may not stop, remove or prune anything."
        ),
    }, answered_by=answered_by)


# ---------------------------------------------------------------------------
# §10/§11 — anomalies
# ---------------------------------------------------------------------------


def _subject_registry(registry) -> dict:
    """Which registered subjects are critical / protected, keyed by subject ref.

    The detector needs this to reach two conclusions it cannot otherwise reach:
    INFRASTRUCTURE_AT_RISK only fires for `criticality: critical`, and the
    protection guard downgrades a recommendation to NEEDS_HUMAN. Both lookups
    currently always miss, which makes them unreachable branches — the exact
    pathology this round exists to remove.

    Keys are the refs the SIGNALS carry, which are the resource id for a probe
    and `runtime:container:<name>` for a container. A subject that is not
    registered is genuinely absent rather than defaulted here: the detector's
    own defaults apply, and those are `normal`/`normal`.
    """
    out: dict[str, dict] = {}
    if registry is None:
        return out
    for resource in registry.all():
        entry = {
            "resource_id": resource.id,
            "criticality": resource.criticality,
            "protection": resource.protection,
            "kind": resource.kind,
        }
        out[resource.id] = entry
        container = _container_name(resource)
        if container:
            out[f"runtime:container:{container}"] = entry
    return out


def _alias_subjects(subjects: dict, container_rows) -> None:
    """Let every identity a container carries reach its registration.

    §9 makes `runtime_ref` the canonical identity and a rename must not move
    it. The detector looks subjects up by that ref, but the registry is
    written against a container NAME. Without this, making the ref
    name-independent would have silently darkened two branches: the
    INFRASTRUCTURE_AT_RISK guard (which only fires for `criticality:
    critical`) and the protection guard — the same class of defect the
    name-keyed dict was introduced to fix, arriving from the other side.

    So each row's registration is looked up ONCE, by the name the registry
    knows, and then registered under every anchor the row reports. A container
    nothing is registered for resolves to nothing and stays UNKNOWN_OWNER:
    this adds aliases to a known answer and never invents one.
    """
    if not container_rows:
        return
    for row in container_rows:
        entry = None
        name = row.get("container")
        if name and f"runtime:container:{name}" in subjects:
            entry = subjects[f"runtime:container:{name}"]
        if entry is None:
            # Already anchor-matched during attribution (rung 3b), or not
            # matched at all. Either way the row itself is the authority on
            # whether it reached a registration.
            resource_id = (
                ((row.get("attribution") or {}).get("registry_resource_id"))
            )
            if resource_id:
                entry = subjects.get(resource_id)
        if entry is None:
            continue
        for anchor in (row.get("identity") or {}).get("anchors") or []:
            ref = anchor.get("runtime_ref")
            if ref:
                subjects.setdefault(ref, entry)


def _container_name(resource) -> str | None:
    for probe in resource.probes:
        if probe.get("container"):
            return probe["container"]
    if resource.kind == "container":
        return resource.record.get("container")
    return None


def _host_facts(inventory=None) -> dict:
    """Filesystem usage, in the shape the detector's contract declares.

    `used_percent` stays None when the mount could not be measured. It is NOT
    coerced to 0: an unread filesystem is not an empty one, and the detector
    already skips a None rather than treating it as a reading.
    """
    inventory = inventory or inventory_mod.Inventory()
    try:
        disk = inventory.disk_status()
    except Exception as exc:  # noqa: BLE001 - a failed reading is reported, not silent
        return {"filesystems": [], "error": f"{type(exc).__name__}: {exc}"}

    filesystems = []
    for entry in disk.get("roles") or []:
        if entry.get("state") != "PRESENT":
            continue
        filesystems.append({
            "mount": entry.get("path"),
            "role": entry.get("role"),
            "used_percent": entry.get("used_percent"),
            "available_bytes": entry.get("free_bytes"),
        })
    return {"filesystems": filesystems}


def _probe_results(registry, *, host=None) -> dict:
    """The four-axis health of every registration, in the detector's shape.

    `kind` is carried through because MCP_PROCESS_ALIVE_BUT_UNAVAILABLE keys on
    it. It is the resource's declared kind, so an `mcp-server` registration can
    reach that rule — the rule as written compared against the literal `"mcp"`,
    which no registration in the contract's kind list can ever equal, so the
    branch was dead on arrival.

    Each axis is carried as its STATE WORD (the anomaly detector compares those
    strings) and the health model's derived `state` is carried beside them as
    `derived_state`, with the liveness axis's own evidence and timestamp. That
    one extra key is §15's whole input:

        measured on 2026-09-14 — `litellm-baseline-litellm`,
        `litellm-baseline-postgres` and `botmux-host` are registered
        `desired: running`, probed `liveness: STOPPED`, and the health model
        classifies all three `NOT_RUNNING` — which the contract's
        `desired_actual.mismatch_states` maps to `running`, i.e. a mismatch.
        `desired_actual()` reported `mismatches: []` anyway, because this
        function kept only the axis words and threw `Health.state` away, so
        `desired.comparison` found `derived is None`, took its
        "no derived state to read" branch, and returned UNCHECKED with a `why`
        that blamed a gap in the declaration.

    So the shape to keep in mind: a derived state that is COMPUTED one call
    above the comparator and DISCARDED by the shim between them makes §15
    silent without making it wrong, and `mismatches: []` reads as "every
    registration is in its desired state" while three of them are not.
    """
    if registry is None:
        return {}
    host = host or probes_mod.LiveHost()
    out: dict[str, dict] = {}
    for resource in registry.all():
        try:
            health = probes_mod.observe(resource, host)
        except registry_mod.RegistryError as exc:
            out[resource.id] = {"kind": resource.kind, "error": str(exc)}
            continue
        shown = health.show()
        axes = {axis: (shown["axes"][axis]["state"]) for axis in health_mod.AXES}
        liveness = shown["axes"]["liveness"]
        axes["kind"] = resource.kind
        axes["derived_state"] = shown["state"]
        axes["evidence"] = dict(liveness.get("evidence") or {})
        axes["observed_at"] = liveness.get("observed_at")
        out[resource.id] = axes
    return out


def _error_windows(groups: list[dict], registry, *, probes_host=None) -> tuple[dict, dict]:
    """§10: repeated failures, from a DECLARED source and from nowhere else.

    REPEATED_ERROR_LOOP needs a count of identical failures inside a window.
    Nothing on this host can produce one from a name or a command line: it
    comes from a `journal-diagnostic` probe, which already DECLARES the unit it
    reads and the patterns that count as a failure. `run_probe` is reused
    rather than the matching rule reimplemented, so "what counts as a repeated
    failure" stays in one place.

    This exists because `error_windows` was, until now, constructed only by
    tests. The detector's highest-confidence combination needs a third signal
    that no production caller could supply, so the combination was
    unreachable — and an unreachable rule reports exactly what a satisfied one
    does, which is nothing.

    A group with no source gets NO window rather than a zero. The two are the
    same absence in the signal list and completely different facts, which is
    why the coverage is returned beside the windows and reported as unchecked.
    """
    coverage: dict = {
        "subjects_total": len(groups),
        "subjects_with_a_source": 0,
        "subjects_without_a_source": len(groups),
        "sources": [],
    }
    if registry is None:
        coverage["note"] = "no registry was supplied, so no unit's errors can be counted"
        return {}, coverage

    host = probes_host or probes_mod.LiveHost()

    # A runtime group is reached through the systemd unit in its cgroup scope,
    # which is the only join between "a resource that declares a journal" and
    # "a group of processes". A group with no unit — every agent session on this
    # host — has no declared journal source, and saying so is the answer.
    groups_by_unit: dict[str, list[str]] = {}
    for group in groups:
        unit = attribution.unit_of_cgroup(group.get("scope") or "")
        if unit:
            groups_by_unit.setdefault(unit, []).append(group["ref"])

    windows: dict[str, dict] = {}
    for resource in registry.all():
        for spec in resource.probes:
            if spec.get("kind") != "journal-diagnostic":
                continue
            unit = spec.get("unit")
            since = spec.get("since", "-30 min")
            result = probes_mod.run_probe(resource, spec, host)
            evidence = result.get("evidence") or {}
            hits = evidence.get("hits")
            source = {
                "resource": resource.id,
                "probe": spec.get("id"),
                "unit": unit,
                "since": since,
                "pattern": evidence.get("pattern"),
                "hits": hits,
                "error": result.get("error"),
            }
            coverage["sources"].append(source)
            # `hits` is None when the journal could not be read. It is carried
            # through as None rather than defaulted to 0, and the detector
            # already skips a None count — an unread journal is not a quiet one.
            window = {
                "count": hits,
                "window_seconds": None,
                "window_text": since,
                "still_occurring": None,
                "sample_kind": evidence.get("pattern"),
                "source": f"journal-diagnostic:{spec.get('id')}",
                "unit": unit,
            }
            targets = [resource.id]
            if unit:
                targets.extend(groups_by_unit.get(unit, []))
            for subject in targets:
                # The largest count wins when two probes watch one subject:
                # the signal is "this keeps failing", and the busiest pattern
                # is the least flattering honest answer.
                existing = windows.get(subject)
                if existing is None or (hits or 0) > (existing.get("count") or 0):
                    windows[subject] = dict(window)

    covered = {ref for ref in windows if ref in {g["ref"] for g in groups}}
    coverage["subjects_with_a_source"] = len(covered)
    coverage["subjects_without_a_source"] = len(groups) - len(covered)
    coverage["note"] = (
        "a subject with no source has no window. REPEATED_ERROR_LOOP cannot "
        "fire for it, and the combination that needs that signal cannot be "
        "satisfied — which is NOT the same as the signal having been checked "
        "and found absent."
    )
    return windows, coverage


def anomaly_list(*, host=None, registry=None, docker_host=None,
                 inventory=None, probes_host=None,
                 answered_by: str = DEFAULT_MEASURER) -> dict:
    """Every anomaly the node can support, and every input it lacked."""
    host = proc_host(host)
    table = _table(host)
    groups = _groups(table)

    container_error = None
    try:
        containers_report = container_attribution(
            docker_host=docker_host, registry=registry, answered_by=answered_by
        )
        container_rows = containers_report["containers"]
    except Exception as exc:  # noqa: BLE001 - Docker being down is a fact, not a crash
        containers_report = None
        container_rows = None
        container_error = (
            f"{type(exc).__name__}: {exc}. Containers were NOT evaluated; this "
            f"is a component that could not be asked, not a finding about it."
        )

    # A container set attributed WITHOUT a registry is not a weaker attribution,
    # it is a different and dangerous one: every container comes back
    # `unknown_owner`, `unknown_owner` is what makes STOPPED_REVIEW_CANDIDATE
    # fire, and a PROTECTED container would be reported that way because the
    # document that says it is protected was not loaded. Fail-safe means the
    # detector is not fed an input whose ownership cannot be established. It is
    # reported as unchecked instead, so the gap is visible rather than silent.
    containers_unattributable = None
    if container_rows is not None and not (containers_report or {}).get("registry_present"):
        containers_unattributable = (
            "the managed-runtime registry is absent, so no container's ownership "
            "can be established: every one of them would come back unknown_owner, "
            "and unknown_owner is what makes a reclaim candidate. The containers "
            "were measured and deliberately NOT passed to the detector."
        )
        container_rows = None

    host_facts = _host_facts(inventory)
    probe_results = _probe_results(registry, host=probes_host)
    subjects = _subject_registry(registry)
    _alias_subjects(subjects, container_rows)
    criticality = {k: v["criticality"] for k, v in subjects.items()}
    protection = {k: v["protection"] for k, v in subjects.items()}
    error_windows, window_coverage = _error_windows(groups, registry,
                                                    probes_host=probes_host)

    # §5. The origin session of each group, so RUNTIME_OUTLIVED_OWNER can fire.
    # Derived here rather than inside the detector because the detector is pure
    # and must stay so: a detector that shells out to `loginctl` is a detector
    # whose answer depends on when it was asked. One call per distinct session,
    # and the groups are already in hand.
    sessions = graph.session_states(groups, host=host)

    signals = anomaly.detect(
        groups=groups, containers=container_rows, host_facts=host_facts,
        probe_results=probe_results, error_windows=error_windows,
        criticality=criticality, session_states=sessions,
    )
    unchecked = anomaly.unchecked(
        groups=groups, containers=container_rows, host_facts=host_facts,
        probe_results=probe_results, error_windows=error_windows,
        session_states=sessions,
    )
    if container_error:
        unchecked.append({
            "input": "containers", "state": UNKNOWN,
            "why": f"Docker could not be asked: {container_error}",
        })
    if containers_unattributable:
        unchecked.append({
            "input": "containers", "state": UNKNOWN,
            "why": containers_unattributable,
        })

    # `anomaly.unchecked` reports `error_windows` only when there are none at
    # all. The gap that matters is per-subject: one declared journal source
    # makes the whole input non-empty while thirteen groups still have no way
    # to reach REPEATED_ERROR_LOOP, and the difference would be invisible.
    if window_coverage["subjects_without_a_source"]:
        unchecked.append({
            "input": "error_windows",
            "state": UNKNOWN,
            "why": (
                f"{window_coverage['subjects_without_a_source']} of "
                f"{window_coverage['subjects_total']} subject(s) have no declared "
                f"journal source, so REPEATED_ERROR_LOOP cannot fire for them and "
                f"the combinations requiring it are UNREACHABLE rather than "
                f"unsatisfied."
            ),
        })

    classifications = anomaly.classify_all(
        signals, criticality=criticality, protection=protection
    )
    levels, subjects = anomaly.levels(groups=groups, host_facts=host_facts,
                                      error_windows=error_windows)
    # §9/§21. The two halves a signal list cannot carry, both derived from the
    # inputs already in hand rather than measured again: which codes nobody
    # looked at, and what every graded measurement IS. The alert layer needs
    # both — without the first it closes alerts about codes that stopped being
    # evaluated, without the second it closes them at the open threshold and
    # reopens them one tick later.
    #
    # `subjects` is the third half, and it is a different question from both.
    # `levels` says what was measured; `subjects` says what was even there to
    # measure. A runtime group's ref embeds a pid, so its ref is a lifetime —
    # when the group ends its ref never recurs, and a reader holding only
    # `levels` cannot tell "this came back unreadable" from "this is gone".
    # Measured 2026-10-04: 1,088 alerts were frozen OPEN under the first
    # reading when all 1,088 were the second.
    return _envelope({
        "signal_count": len(signals),
        "signals": signals,
        "levels": levels,
        "subjects": sorted(subjects),
        "codes_not_evaluated": anomaly.unevaluated(unchecked),
        "classifications": classifications,
        "unchecked": unchecked,
        "error_window_coverage": window_coverage,
        "coverage": attribution.coverage(host, table),
        "note": (
            "an empty signal list is NOT a clean bill of health. Read "
            "`unchecked` first: on this host `task_states` is structurally "
            "empty because nothing joins a task to a process, so two of the "
            "fourteen codes cannot fire and their absence is not evidence."
        ),
    }, answered_by=answered_by)


def anomaly_explain(subject: str, *, host=None, registry=None, docker_host=None,
                    inventory=None, probes_host=None,
                    answered_by: str = DEFAULT_MEASURER) -> dict:
    """Why one subject classified the way it did — including what did not fire.

    The negative half is the point. A report that says STALE_CANDIDATE without
    saying that STALE_AGENT_RUNTIME_HIGH_CONFIDENCE needed a task state this
    node cannot read is a report nobody can act on and nobody can argue with.
    `combinations_not_met` names every declared combination that did not fire
    and which codes it was missing.
    """
    report = anomaly_list(host=host, registry=registry, docker_host=docker_host,
                          inventory=inventory, probes_host=probes_host,
                          answered_by=answered_by)

    matched = next((c for c in report["classifications"]
                    if c["subject"] == subject), None)
    if matched is None:
        subjects = sorted({s["subject"] for s in report["signals"]})
        return _envelope({
            "subject": subject,
            "found": False,
            "classification": None,
            "because": (
                "no anomaly fired against this subject, or it was not supplied "
                "to the detector. Those are different answers and this payload "
                "does not choose between them — read `unchecked`."
            ),
            "subjects_with_signals": subjects,
            "unchecked": report["unchecked"],
            "coverage": report["coverage"],
        }, answered_by=answered_by)

    present = set(matched["signal_codes"])
    unmet = []
    for name, required in anomaly.COMBINATIONS:
        missing = [code for code in required if code not in present]
        if not missing:
            continue
        unmet.append({
            "combination": name,
            "required": list(required),
            "missing": missing,
            "because": f"{len(missing)} of {len(required)} required signal(s) absent",
        })

    return _envelope({
        "subject": subject,
        "found": True,
        "classification": matched["classification"],
        "because": matched["because"],
        "criticality": matched["criticality"],
        "protection": matched["protection"],
        "signals": matched["signals"],
        "signal_codes": matched["signal_codes"],
        "recommendation": matched["recommendation"],
        "recommendation_because": matched["recommendation_because"],
        "blast_radius": matched["blast_radius"],
        # The negative half. Every combination that did NOT fire, and why.
        "combinations_not_met": unmet,
        "unchecked": report["unchecked"],
        "coverage": report["coverage"],
        "note": (
            "`recommendation` is a PROPOSAL. Nothing on this surface performs "
            "it: a protected subject is escalated to NEEDS_HUMAN rather than "
            "recommended, and a cleanup request requires a human approval that "
            "does not exist as an API."
        ),
    }, answered_by=answered_by)


# ---------------------------------------------------------------------------
# §7/§19 — the criticality map and the retry budget
# ---------------------------------------------------------------------------


def docker_observation(*, inventory=None, space: bool = False,
                       answered_by: str = DEFAULT_MEASURER) -> dict:
    """§6/§7/§19 — the Docker inventory, for the read surface that lacked it.

    Every fact here comes from `inventory.py`, which is §6's producer. This
    function decides nothing and measures nothing itself: §18 asks for one
    producer per fact, and a view layer that ran its own `docker` command would
    be a second producer whose answer could disagree with the daemon's while
    both stayed internally consistent.

    `docker_space` is opt-in and the reason is cost, not correctness: it is the
    one `docker system df` call, measured here at ~22s against every other read
    in this payload at under 3s. A caller that wants the reclaimable picture
    asks for it and waits; a caller that does not is not made to.

    The reclaimable figure is reported as what a prune WOULD free. It is not a
    candidate list and nothing here may act on it — hence `advisory`, which is
    spelled out in the payload rather than left to the reader.
    """
    inv = inventory if inventory is not None else inventory_mod.Inventory()
    payload = {
        "summary": inv.docker_summary(),
        "containers": inv.docker_list(),
        "images": inv.docker_images(),
        "volumes": inv.docker_volumes(),
        "networks": inv.docker_networks(),
        "sizes": inv.docker_sizes(),
    }
    if space:
        payload["space"] = inv.docker_space()
    return _envelope({
        **payload,
        "space_included": space,
        "largest_observed_consumers": _largest_observed_consumers(payload),
        "advisory": (
            "every number here is a measurement. Nothing in this payload is a "
            "cleanup candidate, and no tool on any surface built from it stops, "
            "removes or prunes anything. `space.reclaimable_bytes_total`, when "
            "included, is what a prune WOULD free — an estimate of a possible "
            "action, not a list of things to act on"
        ),
        **({} if space else {
            "space_note": (
                "the reclaimable/build-cache picture is NOT in this answer. It "
                "is one `docker system df` call, measured at ~22s, and it is "
                "opt-in rather than omitted: ask with `serverosctl resource "
                "docker --space`"
            )}),
    }, answered_by=answered_by)


def _largest_observed_consumers(payload: dict) -> list[dict]:
    """The biggest containers BY MEASURED SIZE, and nothing else.

    `docker ps --size` reports a writable-layer and a rootfs size per
    container. Those are the only per-container storage numbers Docker
    actually measures, and the empty writable layer is reported as exactly
    that rather than as an absence — a container that has written nothing is a
    measurement of zero, not a missing value.
    """
    rows = ((payload.get("sizes") or {}).get("sizes")
            or (payload.get("sizes") or {}).get("containers")
            or (payload.get("containers") or {}).get("containers") or [])
    def total(row):
        return (row.get("size_rw_bytes") or 0) + (row.get("size_root_fs_bytes") or 0)
    ranked = sorted((row for row in rows if isinstance(row, dict)), key=total,
                    reverse=True)
    return [{"name": row.get("name"), "container_id": row.get("id"),
             "writable_layer_bytes": row.get("size_rw_bytes"),
             "rootfs_bytes": row.get("size_root_fs_bytes")}
            for row in ranked[:10]]


def tunnel_observation(*, registry_doc=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """§13 — the tunnels this node can see, and the ones it structurally cannot.

    A thin envelope over `infrastructure.tunnel_inventory`, for the same reason
    `docker_observation` is one: the tunnels have exactly one producer, and a
    read surface that re-derived them would be a second one.

    The blind spots are carried through unchanged and are the most important
    field in the payload. `declared_count: 0` and `observed_count: 0` are
    facts about what was found; `blind_spots` is the fact about what could not
    be looked for, and on a host with `hidepid=invisible` the second is larger
    than the first. A caller that read only the counts would conclude this
    node has no tunnels, which is not something anything here measured.
    """
    reg = _registry_or_none(registry_doc)
    inventory = infra.tunnel_inventory(registry_doc=reg)
    return _envelope({
        **inventory,
        "state": ("UNKNOWN" if inventory.get("blind_spots")
                  else "COMPLETE"),
        "advisory": (
            "a declared tunnel and an observed forwarder are joined by PID. "
            "Without that join a visible forwarder is a candidate and never an "
            "owner: the registry decides what is ours, and a process that "
            "happens to be forwarding does not register itself by forwarding"
        ),
    }, answered_by=answered_by)


def infrastructure_report(registry, owner_states: dict | None = None, *,
                          answered_by: str = DEFAULT_MEASURER) -> dict:
    """§7/§9/§17/§20: what outlives the work, what restarting would achieve,
    and what may be proposed for reclaim.

    `owner_states` is optional and defaults to nothing, which makes every
    task-scoped resource DEFER. That is the correct reading of silence — see
    `infrastructure.reclaim_report`.
    """
    return _envelope({
        "partition": infra.partition(registry),
        "criticality": infra.criticality_report(registry),
        "never_reclaim": infra.never_reclaim(registry),
        "recovery": infra.recovery_report(registry),
        "reclaim": infra.reclaim_report(registry, owner_states),
    }, answered_by=answered_by)


def retry_budget(*, state_dir=None, registry=None,
                 answered_by: str = DEFAULT_MEASURER) -> dict:
    """§19: what the daemon has retried, and what it may not retry again.

    READ from the daemon's persisted file, never recomputed. A freshly built
    `Budget` has no attempts recorded, so recomputing would report "nothing has
    been retried" no matter how many times something was — which is precisely
    the loop the persistence exists to stop.

    The import is inside the function because `daemon.py` imports this module
    at load time. A module-level import here would be a cycle; a function-level
    one is a single lookup that is already in `sys.modules` by the time any
    caller can reach this.
    """
    daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
    root = Path(state_dir) if state_dir else daemon_mod.default_state_dir()
    budget = daemon_mod.Budget(root / "health/reconcile-state.json")
    snapshot = budget.snapshot()

    rows = []
    for resource_id, entry in snapshot.items():
        decision = budget.may_act(resource_id)
        rows.append({
            "resource": resource_id,
            "attempts_in_window": entry["attempts"],
            "last_action": entry["last_action"],
            "may_act_now": decision["allowed"],
            "blocked_by": None if decision["allowed"] else decision["reason"],
            "retry_after_seconds": decision.get("retry_after_seconds"),
        })

    registered = {r.id for r in registry.all()} if registry is not None else set()
    return _envelope({
        "state_dir": str(root),
        "budget_file": str(root / "health/reconcile-state.json"),
        "max_attempts": daemon_mod.MAX_ATTEMPTS,
        "window_seconds": daemon_mod.WINDOW_SECONDS,
        "backoff_seconds": list(daemon_mod.BACKOFF_SECONDS),
        "count": len(rows),
        "resources": rows,
        "unregistered_with_history": sorted(
            r["resource"] for r in rows if registered and r["resource"] not in registered
        ),
        "note": (
            "a resource with no entry has never been acted on. This is not the "
            "same as a resource that is healthy, and the two are the same empty "
            "list in every cheaper implementation of this command."
        ),
    }, answered_by=answered_by)


# ---------------------------------------------------------------------------
# Rendering helpers shared by the CLI and the daemon's human form
# ---------------------------------------------------------------------------


def chain_rows(chain: dict) -> list[tuple[str, str, str]]:
    """`(link, value, status)` for every hop, in graph order.

    Order is the chain's own order — device, transport, agent, task,
    processes, mcp, container — not alphabetical, because a reader is looking
    for the shape and an alphabetical list has no shape.
    """
    order = ("device", "transport", "origin_session", "origin_session_state",
             "agent", "task", "processes", "mcp_servers", "helpers", "container")
    rows = []
    for name in order:
        link = chain.get(name)
        if not isinstance(link, dict):
            continue
        value = link.get("value")
        if isinstance(value, list):
            value = f"{len(value)} item(s)"
        rows.append((name, "—" if value is None else str(value), str(link.get("status"))))
    for name, link in chain.items():
        if name in order or not isinstance(link, dict):
            continue
        value = link.get("value")
        rows.append((name, "—" if value is None else str(value), str(link.get("status"))))
    return rows


# ---------------------------------------------------------------------------
# §14/§15/§16/§16A/§17 — the observation surfaces this round added
# ---------------------------------------------------------------------------
#
# Five modules, one wrapper each, and the wrappers are deliberately thin. The
# rule this file exists to hold is that ONE implementation answers a question
# and the CLI, the daemon and the MCP adapter are transports — so a wrapper
# here that recomputed what its module already computed would be the fourth
# implementation, arriving through the file that exists to prevent it.
#
# What the wrappers do add is the envelope: `answered_by` and `measured_at`, so
# that "this is a fresh measurement" and "this is the daemon's last tick" are
# distinguishable JSON. A module that measures the host has no way to know
# which caller is asking or when its answer will be read; this layer does.

# The registry is the authority for what SHOULD be running, so every surface
# below that compares two states takes it from the same loader the CLI uses.
_UNSETTLED_ADVISORY = (
    "advisory only. Nothing in this answer stops, removes, moves, prunes or "
    "reclaims anything, and no field here is an instruction to."
)

#: Pass as `registry_doc=` to say, deliberately, that this caller has none.
#: Deliberately not `None`. See `_registry_or_none` for why the two are
#: different statements; an object rather than a sentinel string so that a
#: string that happens to read "none" can never be taken for it.
NO_REGISTRY = object()


def _registry_or_none(registry_doc=None):
    """A `Registry` from whatever the caller had, or None.

    Four inputs and four different statements. Collapsing any two of them is a
    defect this round has already made once, so they are written out:

      not supplied      the node's own registry, through the one loader
                        `serverosctl` and `serverosd` also resolve it. If this
                        node has none the loader raises and names the path,
                        because `{"resources": 0}` served to a caller who
                        simply forgot an argument reads as a claim about the
                        node: *nothing is registered here*. That claim is what
                        this module's own docstring forbids — an absence is
                        never returned as a measurement.
      `NO_REGISTRY`     None. The caller has no registry and said so. None is
                        legitimate downstream: §15's central rule is that
                        nothing may be reported as a MISMATCH when nothing
                        declared a desired state.
      a Registry        itself, untouched.
      a path, or a      loaded through the same loader, so a path that does not
      parsed document   exist refuses rather than becoming an empty document.
    """
    if registry_doc is NO_REGISTRY:
        return None
    if registry_doc is None:
        return registry_mod.load_default()
    if isinstance(registry_doc, registry_mod.Registry):
        return registry_doc
    if isinstance(registry_doc, (str, Path)):
        return registry_mod.load_default(registry_doc)
    if isinstance(registry_doc, dict):
        return registry_mod.Registry(document=registry_doc)
    raise TypeError(
        f"registry_doc must be a Registry, a path, a parsed document, "
        f"NO_REGISTRY or None; got {type(registry_doc).__name__}. Nothing here "
        f"guesses a registry from an object of unknown shape — a surface that "
        f"did would report on a registry nobody authored."
    )


def port_ownership(*, host=None, registry_doc=None, containers_document=None,
                   answered_by: str = DEFAULT_MEASURER) -> dict:
    """§14 — every endpoint this node holds, and the claims on each of them.

    Ports are the surface where "who owns this" is most often answered by a
    guess, so the module keeps every claim and reports disagreements instead of
    picking one. `unattributed` is not an error: §14 forbids attributing every
    connection to a managed runtime, and the largest honest bucket on a shared
    host is the one that says nobody could be named.

    The container half is GATHERED here when the caller does not supply it, for
    the reason measured on 2026-09-14: called with only the socket table, this
    returned `coverage.claimed_by_a_container: 0` and
    `providers.docker.state: UNAVAILABLE`, and 37 unattributed endpoints. Those
    zeros are about the ARGUMENT, not about the node — the node publishes
    ports, and a caller reading `claimed_by_a_container: 0` has no way to tell
    a host with no published bindings from a surface that never asked. It is
    the same shape as `resources: 0` from an unread registry, one namespace
    over.
    """
    registry = _registry_or_none(registry_doc)
    if containers_document is None:
        # Through this layer's own container view, so the ports report and the
        # attribution report are answering about the same measurement rather
        # than two reads taken a second apart.
        containers_document = container_attribution(registry=registry)
    document = ports_mod.port_inventory(
        host=host,
        registry_doc=registry,
        containers_document=containers_document)
    return _envelope(document, answered_by=answered_by)


def desired_actual(*, registry_doc=None, probes_host=None, measured=None,
                   answered_by: str = DEFAULT_MEASURER) -> dict:
    """§15 — every registration against what was measured about it.

    `measured` is `{resource_id: four-axis health}`. Omitted, it is taken from
    the same probe path the rest of this layer uses, so the CLI and the daemon
    compare against one measurement rather than two.

    `probes_host` is the PROBE host — the object with `docker_inspect` and
    `systemd_user_unit` on it. It was called `host` until this round, which is
    also the name `resource_anomaly` below uses for a host FACTS DOCUMENT: two
    different things, one word, and the shim between them passed the document
    into `probes.observe`, where it would have raised `AttributeError` rather
    than answering. No caller does that today, which is why it is a rename
    rather than a repair — and why the rename is worth making before one does.
    """
    registry = _registry_or_none(registry_doc)
    if measured is None and registry is not None:
        measured = _probe_results(registry, host=probes_host)
    document = desired_mod.desired_actual(registry_doc=registry, measured=measured)
    # Each mismatch is also rendered in the anomaly catalogue's signal shape, so
    # that a reader filtering signals does not have to learn a second shape for
    # the one detector this round added to it.
    document = {**document,
                "signals": [desired_mod.signal_of(row)
                            for row in document.get("mismatches", [])]}
    return _envelope(document, answered_by=answered_by)


# ---------------------------------------------------------------------------
# SYS-02 · the dynamic environment
# ---------------------------------------------------------------------------

def _lease_store(state_dir=None, *, root=None):
    """The resource-leases namespace, resolved by `lease_store.namespace_root`.

    Deliberately a thin forwarder and not a second derivation. The CLI's write
    verbs build their store from the same function, so a `lease declare` and a
    `lease list` given the same `--state-dir` cannot address two directories —
    which they did, silently, until a daemon-socket test compared them.

    `state_dir` is the RUNTIME state dir (`state/serveros/runtime`), the same
    value `serverosd --state-dir` takes, so the daemon's `environment.leases`
    and the CLI's `lease list` answer about the same namespace. `root` names the
    namespace outright, for a test or an isolated run.
    """
    return lease_store_mod.open_store(state_dir, root=root, now=_now)


def environment_leases(*, state_dir=None, root=None,
                       answered_by: str = DEFAULT_MEASURER) -> dict:
    """Every resource with something recorded against it.

    `held: 0` and `unreadable: true` are different answers and this layer keeps
    them apart. An unreadable namespace raises rather than returning an empty
    list: an empty list is the claim `NO_LEASES_HELD`, and a namespace nobody
    could read must not make that claim on the node's behalf. The caller that
    cannot handle an exception gets one — which is the honest outcome, and the
    reason this function is not wrapped in a `try` that returns `[]`.
    """
    store = _lease_store(state_dir, root=root)
    records = store.records()
    return _envelope({
        "schema": lease_store_mod.SCHEMA,
        "namespace": lease_store_mod.NAMESPACE_REF,
        "root": str(store.root),
        # `recorded` and NOT `held`. A record exists as soon as anything is
        # recorded against a resource, and a record can exist with no live
        # reference at all — a crashed holder's expired claim, or a shared
        # resource nobody currently wants. `HELD` is the contract's word for
        # "at least one LIVE reference", so a count under that name would
        # report a resource as in use on the strength of a claim its holder
        # already stopped backing. `environment.effective` computes `held`
        # properly, from the references, and that is the only place it means
        # what it says.
        "recorded": len(records),
        "absent_means": "NO_LEASES_HELD",
        "leases": records,
    }, answered_by=answered_by)


def environment_effective(*, state_dir=None, root=None, observed=None,
                          ownership=None, policy=None, context=None,
                          answered_by: str = DEFAULT_MEASURER) -> dict:
    """The §6 equation over what is recorded.

    `observed`, `ownership` and `policy` are inputs this layer does not gather
    itself this round, and it says so rather than filling them: a resource whose
    ownership nobody resolved is `UNKNOWN` and its release is refused, which is
    the safe direction. The alternative — treating "we did not look" as
    "nothing owns it" — is the default value that releases somebody else's
    resource.

    `context` is the caller's own (device, session, conversation). It is
    reported and changes no verdict: §6 lists Context as a term of the equation
    because the SAME desired and observed state can mean different things in
    different conversations, and the term this round can honestly contribute is
    the caller's statement of where they are asking from.
    """
    store = _lease_store(state_dir, root=root)
    records = store.records()
    desired = {
        record["resource"]: {
            "scope": record["scope"],
            "sharing": record["sharing"],
            "references": list(record["references"]),
        }
        for record in records
    }
    payload = environment_mod.effective(
        desired=desired, observed=observed or {}, ownership=ownership or {},
        policy=policy or {}, context=context or {}, now=_now())
    payload["namespace"] = lease_store_mod.NAMESPACE_REF
    payload["root"] = str(store.root)
    payload["inputs_not_gathered"] = sorted(
        name for name, value in
        (("observed", observed), ("ownership", ownership), ("policy", policy))
        if not value)
    return _envelope(payload, answered_by=answered_by)


# The classification inputs §16 reads, and which of them this layer can gather.
# It is a literal rather than a comment because the answer is printed: a report
# that consulted only some of these and did not say which is read as one that
# found nothing in the rest. This said "four of eight" until 2026-09-27; the six
# names below are the whole set `runtime_hygiene.classify` reads, so the printed
# `inputs_not_gathered` can never name more than they hold, and the pair of
# numbers described a set neither the module nor this layer has.
HYGIENE_INPUTS = ("comparison", "protection", "activity", "owner_evidence",
                  "superseded_by", "traffic")


def runtime_hygiene(*, registry_doc=None, host=None, traffic=None,
                    owner_evidence=None, superseded_by=None,
                    answered_by: str = DEFAULT_MEASURER) -> dict:
    """§16 — the nine-class vocabulary over the registrations.

    `CLEANUP_CANDIDATE` is a label attached to a question for a person. The
    classes are assigned from evidence with the evidence kept beside the
    decision, and the one class that is never inferred from a single variable
    is the one that would be a deletion instruction if it were.

    `traffic` and `owner_evidence` are gathered by the caller — the port view
    (§14) and the container attribution (§6) already own those facts, and
    re-deriving them here would be the second implementation this file exists
    to prevent. What this layer does is report which inputs it was given, so a
    class that could not be reached is distinguishable from one that was
    checked and found false.
    """
    registry = _registry_or_none(registry_doc)
    rows = _runtime_hygiene_rows(registry, host=host, traffic=traffic,
                                owner_evidence=owner_evidence,
                                superseded_by=superseded_by)
    document = runtime_hygiene_mod.classify_all(rows)
    gathered = sorted({key for row in rows for key in row
                       if key in HYGIENE_INPUTS})
    document = {**document,
                "inputs_gathered": gathered,
                "inputs_not_gathered": [k for k in HYGIENE_INPUTS
                                        if k not in gathered],
                "note": (
                    document.get("note", "") + " Inputs listed in "
                    "`inputs_not_gathered` were not measured, so a class they "
                    "would have decided is absent rather than false.")}
    return _envelope(document, answered_by=answered_by)


def _runtime_hygiene_rows(registry, *, host=None, traffic=None, owner_evidence=None,
                          superseded_by=None) -> list[dict]:
    """One classification input per registration.

    Every optional mapping is keyed by resource id, and a key that is missing
    is left OUT of the row rather than defaulted: `traffic: False` and no
    traffic measurement at all are different statements, and §16's rule about
    `stopped > N days` exists because the second keeps being written as the
    first.
    """
    if registry is None:
        return []
    health = _probe_results(registry, host=host)
    rows = []
    for resource in registry.all():
        row = {
            "resource_ref": f"managed-runtime:{resource.id}",
            "resource": resource,
            # The comparison is §15's, reused rather than re-made here, so that
            # "in desired state" means one thing on both surfaces — and
            # `desired_state = None` keeps producing UNCHECKED rather than a
            # mismatch, which is §15's central rule.
            "comparison": desired_mod.comparison(
                declared=resource,
                measured=health.get(resource.id),
                resource_ref=f"managed-runtime:{resource.id}"),
        }
        for key, mapping in (("traffic", traffic),
                             ("owner_evidence", owner_evidence),
                             ("superseded_by", superseded_by)):
            if mapping is not None and resource.id in mapping:
                row[key] = mapping[resource.id]
        rows.append(row)
    return rows


def fs_hygiene(*, home=None, data=None, sizes=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """§16A — the two roots classified, and nothing moved.

    Every class this reports is a DESCRIPTION. `.botmux-clone.log` and
    `.ccbridge-bridge.log` are classified, never deleted or moved (§16A), and
    duplicate semantic roots are reported as DUPLICATE_SEMANTIC_ROOT rather
    than resolved — a report that reunited them would be making the ruling the
    report exists to ask for.
    """
    document = fs_hygiene_mod.report(home=home, data=data, sizes=sizes)
    return _envelope(document, answered_by=answered_by)


def protection(*, containers=None, registry_doc=None, answered_by: str = DEFAULT_MEASURER) -> dict:
    """§17 — protection resolved per identity spelling, and where it lapses.

    Every spelling of one container is checked and the union decides, because
    the failure §17 names is protection that holds under the name and is lost
    when the identity switches to the container id. `PROTECTION_LOST_ON_IDENTITY_SWITCH`
    is the finding for exactly that, and it is reported rather than repaired:
    nothing here writes a label, and nothing here registers a resource.

    A row is read in either spelling — this layer's own (`id`/`name`/`identity`)
    or the one `docker ps --format {{json .}}` emits (`ID`/`Names`). The second
    is not a convenience: the daemon's container set comes straight off that
    command, and a caller that had to translate it first would be the second
    place a container's identity is spelled, free to spell it differently.
    """
    registry = _registry_or_none(registry_doc)
    rows = []
    for container in containers or []:
        if not isinstance(container, dict):
            continue
        name = container.get("name") or container.get("Names")
        if isinstance(name, str) and "," in name:
            # Docker can report several names in one comma-separated field. The
            # first is the one `docker inspect` accepts; the rest are aliases.
            name = name.split(",")[0].strip()
        container_id = container.get("id") or container.get("ID")
        labels = protection_mod.parse_labels(
            container.get("labels") if container.get("labels") is not None
            else container.get("Labels"))
        # ONE container per resolution, with ITS labels. Merging the node's
        # containers into a single call is not a smaller version of this — the
        # module keys its per-identity results by identity KIND, so two
        # containers both carrying a `container_name` collapse into one entry
        # and the second container's resolution is served as the first's. The
        # labels have the same failure: one dict for the set means the last
        # container's compose names are attached to every container's identity.
        resolved = protection_mod.resolve_by_identity(
            identities=protection_mod.identities_of(
                container.get("identity"), name=name,
                container_id=container_id, labels=labels),
            registry_doc=registry,
            labels=labels)
        rows.append({
            # What a reader can point at, preferring the id because §9 made it
            # canonical and a name can be changed.
            "subject": container_id or name or None,
            "container_id": container_id,
            "name": name,
            "protected": _protection_union(resolved["by_identity"]),
            "by_identity": resolved["by_identity"],
            "findings": resolved["findings"],
        })

    findings = [{**finding, "subject": row["subject"]}
                for row in rows for finding in row["findings"]]
    document = {
        "schema": "serveros/protection-resolution.v1",
        "containers": rows,
        "findings": findings,
        "checked": len(rows),
        # The classes are three-valued and the third value is the point: `None`
        # is "nothing on this node could be asked", and a summary that folded it
        # into `False` would be a protection bypass performed by a count.
        "counts": {
            "protected": sum(1 for r in rows if r["protected"] is True),
            "not_protected": sum(1 for r in rows if r["protected"] is False),
            "unknown": sum(1 for r in rows if r["protected"] is None),
        },
    }
    return _envelope(document, answered_by=answered_by)


def _protection_union(by_identity: dict[str, dict]):
    """§17's union rule over the spellings of ONE container.

    `True` if any spelling is protected — that is the direction that keeps the
    answer from depending on which spelling the caller happened to hold. `False`
    only if something on this node was asked and named nothing, over every
    spelling. `None` when nothing could be asked at all, which is not `False`:
    the wrong `False` here is a protection bypass performed by a default.
    """
    verdicts = [result.get("protected") for result in by_identity.values()]
    if any(v is True for v in verdicts):
        return True
    if any(v is False for v in verdicts):
        return False
    return None


# ---------------------------------------------------------------------------
# §18/§19 — the resource anomaly catalogue, over documents this layer gathered
# ---------------------------------------------------------------------------

# The inputs §18's detector consumes, and which of them this layer can gather
# on its own. A literal for the same reason `HYGIENE_INPUTS` is one: the answer
# is printed, and a report that reached five of six inputs without saying so is
# read as one that found nothing in the sixth.
ANOMALY_INPUTS = ("host", "disk", "docker", "hygiene", "desired", "tunnel")


def resource_anomaly(*, inventory=None, host=None, disk=None, docker=None,
                     hygiene=None, desired=None, tunnel=None, measured=None,
                     thresholds=None, space: bool = False, now=None,
                     registry_doc=None, probes_host=None,
                     answered_by: str = DEFAULT_MEASURER) -> dict:
    """§18/§19 — the fifteen resource codes, graded, plus the ones ungraded.

    This function measures NOTHING. Every document below is read from the
    producer that already owns that fact — `inventory.py` for the host, the
    disk and the four Docker reads; `hygiene.py` for the container
    classifications; `desired.py` for the declaration comparison;
    `infrastructure.py` for the tunnels. §28 asks for one producer per fact and
    this layer is where that is easy to lose: it already imports all four
    modules, so a re-derivation here would look like a convenience rather than
    like the second implementation it is.

    `space` is opt-in and stays opt-in, for the reason `docker_observation`
    spells out: one `docker system df` at ~22s against reads that cost under
    3s. Not asking is not the same as finding 0 — the two codes that need it
    come back in `codes_not_evaluated` with the cost stated, which is the
    honest answer and the one §27 asks for.

    `measured` is the `{resource_id: health}` mapping §15's comparison reads.
    A daemon should pass its own last tick's observations: it has already paid
    for them, and re-probing every registration would produce a second
    measurement whose answer could disagree with the one the daemon is acting
    on. Supplying it is also what makes `now` worth passing — the tick's
    observation time and the caller's clock are then genuinely different.

    `probes_host` is the object §15's comparison probes through when the caller
    supplied no `measured` — the daemon's own, whether that is `LiveHost` or a
    test's. It is a separate parameter from `host` because `host` here is the
    host FACTS document this function passes to the detector, and a comparison
    taken through a different observer than the rest of the document is the
    §28 shape: one answer assembled from two measurements that can disagree.
    """
    registry = _registry_or_none(registry_doc)
    inv = inventory if inventory is not None else inventory_mod.Inventory()

    # Falsy and not-supplied are the same statement here, and deliberately so:
    # `_Gathered.take` refuses an empty document one layer down, so a caller
    # that passed `{}` would otherwise be recorded as having supplied an input
    # the detector reports as missing. Two spellings of one absence, one
    # meaning.
    supplied = [name for name, value in (
        ("host", host), ("disk", disk), ("docker", docker),
        ("hygiene", hygiene), ("desired", desired), ("tunnel", tunnel),
    ) if value]

    disk_document = disk if disk else inv.disk_status()
    host_document = host if host else inv.host_summary()
    docker_document = (docker if docker
                       else _docker_anomaly_inputs(inv, disk_document, space=space))

    absent: dict[str, str] = {}
    if not hygiene:
        if registry is None:
            # §15's rule, one namespace over: with no registry nothing declared
            # a container is ours, so the classification would be made against
            # a registry nobody authored. Reported as a gap rather than as a
            # host with no unowned containers.
            absent["hygiene"] = (
                "no managed-runtime registry was resolved, so no container "
                "could be classified as owned or unowned")
        else:
            hygiene = hygiene_mod.Hygiene(registry, inv).report()
    if not desired:
        if registry is None:
            absent["desired"] = (
                "no managed-runtime registry was resolved, so there is nothing "
                "whose declared state could be compared")
        else:
            # Through THIS LAYER's own `desired_actual`, not the module's, so
            # that the mismatches arrive already rendered into the signal
            # shape. Calling the module directly returns `mismatches` and no
            # `signals`, and the detector reads `signals` — so the comparison
            # would be made, the rows published on one surface and silently
            # absent on this one, which is this round's recurring defect
            # exactly: a value computed one call above the consumer and
            # discarded by the shim between them.
            desired = desired_actual(registry_doc=registry,
                                     probes_host=probes_host,
                                     measured=measured)
    if not tunnel:
        tunnel = infra.tunnel_inventory(registry_doc=registry)

    document = resource_anomaly_mod.detect(
        host=host_document, disk=disk_document, docker=docker_document,
        hygiene=hygiene, desired=desired, tunnel=tunnel,
        thresholds=thresholds, observed_at=_now_iso(), now=now)

    gathered = [name for name, value in (
        ("host", host_document), ("disk", disk_document), ("docker", docker_document),
        ("hygiene", hygiene), ("desired", desired), ("tunnel", tunnel),
    ) if value]
    return _envelope({
        **document,
        # §20/§27. Which inputs this call reached, which the caller supplied,
        # and which it could not reach at all with the reason. The middle list
        # is worth printing: `detect` cannot tell a supplied document from a
        # gathered one, and a reader checking whether this answer came from a
        # fresh measurement or from a caller's snapshot needs to.
        "gathering": {
            "inputs_supplied_by_the_caller": supplied,
            "inputs_reached": gathered,
            "inputs_unreachable": absent,
        },
        "space_included": space,
        "supported_by_resource_policy": _policy_summary(registry, hygiene),
        "advisory": (
            document["advisory"] + " Every input above was read from the "
            "producer that owns it, and `gathering` names which of the six "
            "this call reached — a code listed in `codes_not_evaluated` was "
            "not looked for, which is not the same as one that was looked for "
            "and not found"),
    }, answered_by=answered_by)


def _docker_anomaly_inputs(inv, disk_document: dict, *, space: bool) -> dict:
    """The four Docker reads §18's Docker codes consume, from their producers.

    `data_root_total_bytes` is the total size of the filesystem the DOCKER_FS
    role LANDED ON, not of the path the role asked about. The distinction is
    `inventory._mount`'s own: on this host DOCKER_FS is asked about
    `/var/lib/docker` and lands on `/`, and a denominator taken from the
    question instead of the answer would report Docker's share of a disk it
    does not stand on.
    """
    payload = {
        "sizes": inv.docker_sizes(),
        "logs": inv.container_logs(),
        "data_root_total_bytes": next(
            (row.get("total_bytes") for row in (disk_document.get("roles") or [])
             if row.get("role") == "DOCKER_FS" and row.get("state") == "PRESENT"),
            None),
    }
    if space:
        payload["space"] = inv.docker_space()
    return payload


def _policy_summary(registry, hygiene) -> dict:
    """§22 — what the governance document permits, read as a policy input.

    Read through `resource_policy.py` so that §22's four classes have one
    reader, and carried here rather than merged into the signals because the
    document states what an owner said, not what was measured — §18's
    `PROTECTED_RESOURCE_AT_RISK` fires on the classification the report made,
    and a document saying "deny" is not a measurement of anything.

    The group-key trap is why this is a summary and not a join: the document's
    top-level keys are GROUP names, so `declared_objects` unwraps the second
    level. A caller that compared the group names to container names would find
    every one of them dangling.
    """
    known = [row.get("name") for row in
             ((hygiene or {}).get("docker") or {}).get("containers") or []]
    try:
        payload = resource_policy_mod.resolve()
    except Exception as exc:  # noqa: BLE001 - a report, not a gate
        # This is a REPORT and it must not be able to fail the observation it
        # is reported beside: a governance document that moved, or that a
        # later revision made unparseable, is a finding about the document.
        return {"state": "UNREADABLE", "why": f"{type(exc).__name__}: {exc}"}
    return {
        "state": payload.get("state"),
        "location": payload.get("location"),
        "coverage": resource_policy_mod.coverage(payload),
        "destructive_reconcile": payload.get("destructive_reconcile"),
        "protection": resource_policy_mod.protection_input(
            payload, known_containers=[n for n in known if n],
            path=payload.get("location")),
    }
