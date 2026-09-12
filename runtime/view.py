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
        "reparented_to_init": (group.get("tree") or {}).get("reparented_to_init"),
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
        axes["kind"] = resource.kind
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
    # `unknown_owner`, `unknown_owner` is what makes STOPPED_RECLAIM_CANDIDATE
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
    criticality = {k: v["criticality"] for k, v in subjects.items()}
    protection = {k: v["protection"] for k, v in subjects.items()}
    error_windows, window_coverage = _error_windows(groups, registry,
                                                    probes_host=probes_host)

    signals = anomaly.detect(
        groups=groups, containers=container_rows, host_facts=host_facts,
        probe_results=probe_results, error_windows=error_windows,
        criticality=criticality,
    )
    unchecked = anomaly.unchecked(
        groups=groups, containers=container_rows, host_facts=host_facts,
        probe_results=probe_results, error_windows=error_windows,
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
    return _envelope({
        "signal_count": len(signals),
        "signals": signals,
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


def infrastructure_report(registry, *, answered_by: str = DEFAULT_MEASURER) -> dict:
    """§7/§9/§20: what outlives the work, and what restarting would achieve."""
    return _envelope({
        "partition": infra.partition(registry),
        "criticality": infra.criticality_report(registry),
        "never_reclaim": infra.never_reclaim(registry),
        "recovery": infra.recovery_report(registry),
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
