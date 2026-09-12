#!/usr/bin/env python3
"""serverosctl — the deterministic entry point.

Why a CLI and not (yet) an MCP server
-------------------------------------
Every primitive here is exercised by a person or a shell script before an
agent gets an API for it. That ordering is not ceremony: the RDC diagnosis in
this repo was found by running `ss`, `journalctl` and `docker inspect` from a
shell and reading the answer, and an MCP tool that only exposed the
conclusion would have hidden it. The MCP layer (§23) is a thin adapter over
these same functions, and it is written after the functions have been wrong
in public at least once.

Exit codes are part of the contract, because "did it work" has to be
answerable by a shell:

    0  the command answered, and what it answered is not a fault
    1  the command answered, and what it answered IS a fault
       (a resource is down, something is functionally failed)
    2  the command could not answer — bad arguments, unreadable registry,
       unavailable measurement. Distinct from 1 on purpose: a monitor that
       cannot tell "broken" from "could not look" reports outages during its
       own failures.

Nothing in this file mutates anything. `runtime register` and `runtime
reconcile` are NOT implemented here: registration is an authored act on a
machine-state document (§B), and reconciliation needs an admission path that
does not exist for anything above STOP this round. A command that is not
implemented says so and exits 2, rather than being quietly absent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
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


registry_mod = _load("serveros_runtime_registry", "registry.py")
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
conformance_mod = _load("serveros_runtime_conformance", "conformance.py")
infra_mod = _load("serveros_runtime_infrastructure", "infrastructure.py")

EXIT_OK = 0
EXIT_FAULT = 1
EXIT_COULD_NOT_ANSWER = 2

FAULT_STATES = frozenset(
    {"FUNCTIONALLY_FAILED", "NOT_RUNNING", "DEGRADED", "NOT_READY"}
)


def default_registry_path() -> Path:
    state = os.environ.get("SERVEROS_STATE_DIR") or "/opt/applications/data/home-hwz/state/serveros"
    return Path(state) / "runtime/registry/resources.yaml"


def load_registry(path: str | None):
    target = Path(path) if path else default_registry_path()
    if not target.exists():
        raise registry_mod.RegistryError(
            f"no managed-runtime registry at {target}. Nothing is managed "
            f"until something is registered; there is no discovery fallback."
        )
    return registry_mod.Registry(path=target)


# ---------------------------------------------------------------------------
# Rendering. Machine output is the primary form; the human form is a view of
# it, so the two can never disagree about a fact.
# ---------------------------------------------------------------------------


def emit(payload: dict, *, as_json: bool, lines: list[str] | None = None) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
    else:
        for line in lines or []:
            sys.stdout.write(f"{line}\n")


def human_bytes(n) -> str:
    if n is None:
        return "UNKNOWN"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n:.1f} TiB"


# ---------------------------------------------------------------------------
# device / context
# ---------------------------------------------------------------------------


def cmd_device_show(args) -> int:
    reg = load_registry(args.registry)
    inv = inventory_mod.Inventory()
    host = inv.host_summary()
    payload = {
        "device_ref": "device:server-beijing",
        "role": "fleet-controller-primary (reference node)",
        "host": host,
        "managed_resources": {
            "count": len(reg),
            "by_kind": _count_by(reg, "kind"),
            "protected": len(reg.protected()),
            "automatable": len(reg.automatable()),
        },
        "registry_path": str(Path(args.registry) if args.registry else default_registry_path()),
    }
    lines = [
        f"device      device:server-beijing",
        f"cpu         {host['cpu']['model']} · {host['cpu']['logical_cpus']} logical CPUs",
        f"memory      {human_bytes(host['memory']['total_bytes'])} total · "
        f"{human_bytes(host['memory']['available_bytes'])} available",
        f"gpu         {host['gpu']['state']} ({host['gpu'].get('reason', 'present')})",
        f"load        {host['load']['load1']} / {host['load']['load5']} / {host['load']['load15']}",
        f"managed     {len(reg)} registered · {len(reg.protected())} protected · "
        f"{len(reg.automatable())} automatable",
    ]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_context_current(args) -> int:
    """Which project/item/run this shell is inside, and where its roots are.

    The 20th §46 property is that this is deterministic and never invents a
    context. When the answer is unknown it says UNKNOWN — a context guessed
    from the working directory is how an artifact lands in the wrong item.
    """
    paths = paths_mod.Paths()
    cwd = Path.cwd()
    # Read through the context resolver rather than reimplementing inference.
    try:
        resolver = _load_module_from(REPO_ROOT / "context/resolver.py", "serveros_context_resolver")
        roots = resolver.Roots.from_env()
        item = resolver.infer_item(roots, cwd)
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        item = None
        inference_note = f"UNKNOWN ({type(exc).__name__}: {exc})"
    else:
        inference_note = item or "UNKNOWN (no item inferred from this directory)"

    payload = {
        "cwd": str(cwd),
        "inferred_item": item,
        "inference_note": inference_note,
        "roots": {n: str(paths.root(n)) for n in paths.names},
        "state_runtime": {
            name: str(paths.root("state") / "runtime" / name)
            for name in paths_mod.STATE_SERVEROS_RUNTIME
        },
    }
    lines = [
        f"cwd        {cwd}",
        f"item       {inference_note}",
        "",
        "roots",
    ] + [f"  {n:<14} {paths.root(n)}" for n in paths.names]
    emit(payload, as_json=args.json, lines=lines)
    # Unknown context is not a fault; it is a fact that changes where output
    # is allowed to go, and callers must be able to branch on it.
    return EXIT_OK


def _load_module_from(path: Path, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


REPO_ROOT = _HERE.parent


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------


def cmd_path_resolve(args) -> int:
    paths = paths_mod.Paths()
    if args.work_item_run:
        result = paths.work_item_run(*args.work_item_run)
    elif args.skill_run:
        result = paths.skill_run(*args.skill_run)
    else:
        result = paths.resolve(args.ref)
    emit(result, as_json=args.json, lines=[result["path"]])
    return EXIT_OK


def cmd_path_admit(args) -> int:
    paths = paths_mod.Paths()
    try:
        result = paths.admit(args.path, writing=not args.read_only)
    except paths_mod.PathRefused as exc:
        payload = {"path": args.path, "admitted": False, "reason": str(exc)}
        emit(payload, as_json=args.json,
             lines=[f"REFUSED  {args.path}", f"         {exc}"])
        return EXIT_FAULT
    lines = [
        f"admitted   {result['resolved']}",
        f"root       {result['root']}",
        f"relative   {result['relative']}",
        f"kind       {result['canonical']}",
    ]
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_path_contract(args) -> int:
    payload = paths_mod.Paths().contract()
    if args.json:
        emit(payload, as_json=True)
    else:
        for name, path in payload["roots"].items():
            sys.stdout.write(f"{name:<16} {path}\n")
    return EXIT_OK


def cmd_path_conformance(args) -> int:
    """The contract against the disk. Read-only, and it creates nothing."""
    report = conformance_mod.collect(paths_mod.Paths(), repo_root=REPO_ROOT)
    if args.json:
        emit(report, as_json=True)
    else:
        summary = report["summary"]
        sys.stdout.write(
            f"{report['schema']}\n"
            f"findings {summary['findings']} "
            f"(undeclared {summary['undeclared']}, exempt {summary['exempt']}, "
            f"declared-absent {summary['absent']}, "
            f"blocking {summary['blocking_open']})\n")
        for row in report["findings"]:
            entry = row.get("entry") or ""
            sys.stdout.write(f"  [{row['severity']:<6}] {row['status']:<9} "
                             f"{row['id']:<22} {row['class']}/{entry}\n")
    # The exit code distinguishes the three things a caller can do something
    # about: everything is accounted for (0), something is undeclared and open
    # (1), or the check could not answer because the authority it compares
    # against was unreadable (2). Collapsing the last two into "0, no problems"
    # is how an unreadable contract becomes a clean bill of health.
    if any(row["id"] == "DECLARATION_AUTHORITY_UNREADABLE" for row in report["findings"]):
        return EXIT_COULD_NOT_ANSWER
    return EXIT_FAULT if report["summary"]["blocking_open"] else EXIT_OK


# ---------------------------------------------------------------------------
# resource
# ---------------------------------------------------------------------------


def _count_by(reg, attr: str) -> dict:
    counts: dict[str, int] = {}
    for resource in reg.all():
        key = getattr(resource, attr)
        counts[key] = counts.get(key, 0) + 1
    return counts


def cmd_resource_summary(args) -> int:
    """`resources.host.summary` + disk + docker counts (§12)."""
    inv = inventory_mod.Inventory()
    host = inv.host_summary()
    disk = inv.disk_status()
    docker = inv.docker_summary()
    advisory = inv.pressure()

    payload = {"host": host, "disk": disk, "docker": docker, "advisory": advisory}
    lines = [
        f"cpu      {host['cpu']['model']} · {host['cpu']['logical_cpus']} logical · "
        f"threads/core {host['cpu']['threads_per_core']}",
        f"load     {host['load']['load1']} (1m)  {host['load']['load5']} (5m)  "
        f"{host['load']['load15']} (15m)",
        f"memory   {human_bytes(host['memory']['total_bytes'])} total · "
        f"{human_bytes(host['memory']['available_bytes'])} available · "
        f"swap {human_bytes(host['memory']['swap']['used_bytes'])}/"
        f"{human_bytes(host['memory']['swap']['total_bytes'])} "
        f"({host['memory']['swap'].get('used_percent')}%)",
        f"gpu      {host['gpu']['state']}"
        + (f" — {host['gpu']['reason']}" if host['gpu'].get('reason') else ""),
        "",
        "disk",
    ]
    for mount in disk["roles"]:
        if mount.get("state") == "PRESENT":
            lines.append(
                f"  {mount['role']:<9} {mount['path']:<20} {mount['used_percent']:>5}% used  "
                f"{human_bytes(mount['free_bytes'])} free  ({mount.get('source')}, {mount.get('fstype')})"
            )
        else:
            lines.append(f"  {mount['role']:<9} {mount['path']:<20} {mount.get('state')}")
    lines.append("")
    if docker.get("state") == inventory_mod.UNAVAILABLE:
        lines.append(f"docker   UNAVAILABLE — {docker['reason']}")
    else:
        lines.append(
            f"docker   {docker.get('ServerVersion')} · {docker.get('ContainersRunning')} running · "
            f"{docker.get('ContainersStopped')} stopped · {docker.get('Images')} images"
        )
    if advisory["notes"]:
        lines.append("")
        lines.append("advisory (never acted on automatically)")
        for note in advisory["notes"]:
            lines.append(f"  · {note}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_resource_top(args) -> int:
    inv = inventory_mod.Inventory()
    result = inv.processes_top(limit=args.limit, by=args.by)
    if result.get("state") == inventory_mod.UNAVAILABLE:
        emit(result, as_json=args.json, lines=[f"UNAVAILABLE — {result['reason']}"])
        return EXIT_COULD_NOT_ANSWER
    lines = [f"{'PID':>8} {'USER':<10} {'CPU%':>6} {'MEM%':>6} {'RSS':>10} {'ELAPSED':>10}  COMMAND"]
    for row in result["rows"]:
        lines.append(
            f"{row['pid']:>8} {row['user']:<10} {row['cpu_percent']:>6.1f} "
            f"{row['mem_percent']:>6.1f} {human_bytes(row['rss_bytes']):>10} "
            f"{row['elapsed_seconds']:>9}s  {row['command']}"
        )
    lines.append("")
    lines.append(result["scope_note"])
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_resource_docker_list(args) -> int:
    inv = inventory_mod.Inventory()
    result = inv.docker_list(all_=not args.running_only)
    if result.get("state") == inventory_mod.UNAVAILABLE:
        emit(result, as_json=args.json, lines=[f"UNAVAILABLE — {result['reason']}"])
        return EXIT_COULD_NOT_ANSWER
    reg = load_registry(args.registry)
    hygiene = hygiene_mod.Hygiene(reg, inv)
    rows = [hygiene.classify_container(c) for c in result["containers"]]
    payload = {"count": len(rows), "containers": rows, "registry_source": True}
    lines = [f"{'CLASS':<26} {'STATE':<9} NAME"]
    for row in sorted(rows, key=lambda r: (r["classification"], r["name"])):
        lines.append(f"{row['classification']:<26} {row['state']:<9} {row['name']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_resource_user_services(args) -> int:
    inv = inventory_mod.Inventory()
    result = inv.user_services_list()
    if result.get("state") == inventory_mod.UNAVAILABLE:
        emit(result, as_json=args.json, lines=[f"UNAVAILABLE — {result['reason']}"])
        return EXIT_COULD_NOT_ANSWER
    lines = [f"{'UNIT':<44} {'ACTIVE':<10} {'SUB':<10} DESCRIPTION"]
    for unit in result["units"]:
        lines.append(f"{unit['unit']:<44} {unit['active']:<10} {unit['sub']:<10} {unit['description']}")
    lines.append("")
    lines.append(f"linger: {result['linger']}  (systemd --user is the supervisor)")
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------


def cmd_runtime_list(args) -> int:
    reg = load_registry(args.registry)
    rows = []
    for resource in reg.all():
        rows.append({
            "id": resource.id,
            "kind": resource.kind,
            "owner": resource.owner_ref,
            "lifecycle": resource.lifecycle,
            "desired": resource.desired,
            "protection": resource.protection,
            "automatable": resource.is_automatable,
            "why_not": resource.why_not_automatable(),
        })
    payload = {"count": len(rows), "resources": rows}
    lines = [f"{'ID':<26} {'KIND':<20} {'LIFECYCLE':<12} {'PROTECTION':<11} AUTO"]
    for row in rows:
        lines.append(
            f"{row['id']:<26} {row['kind']:<20} {row['lifecycle']:<12} "
            f"{row['protection']:<11} {'yes' if row['automatable'] else 'no'}"
        )
    lines.append("")
    lines.append("Every resource above was registered before anything was asked of it.")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def _observe(reg, resource):
    """Run the declared probes. Returns (health, probe detail)."""
    host = probes_mod.LiveHost()
    health = probes_mod.observe(resource, host)
    return health, None


def cmd_runtime_status(args) -> int:
    reg = load_registry(args.registry)
    resource = reg.get(args.id)
    health, _ = _observe(reg, resource)
    decision = health_mod.action_for(resource, health)
    payload = {
        "id": resource.id,
        "kind": resource.kind,
        "owner": resource.owner_ref,
        "lifecycle": resource.lifecycle,
        "protection": resource.protection,
        "desired": resource.desired,
        "health": health.show(),
        "action": decision,
    }
    lines = [
        f"resource   {resource.id} ({resource.kind})",
        f"owner      {resource.owner_ref} · lifecycle {resource.lifecycle} · "
        f"protection {resource.protection} · desired {resource.desired}",
        "",
        f"state      {payload['health']['state']}",
    ]
    for axis in health_mod.AXES:
        obs = payload["health"]["axes"][axis]
        detail = f"  reason={obs['reason']}" if obs.get("reason") else ""
        prov = obs.get("observed_by", {})
        lines.append(f"  {axis:<11} {obs['state']:<12} via {prov.get('id', 'none')}{detail}")
    diagnostic = payload["health"]["diagnostic"]
    if diagnostic:
        lines.append(
            f"  diagnostic  {diagnostic['reason']} (corroboration only, "
            f"authoritative: false)"
        )
    lines.append("")
    lines.append(f"action     {decision['action']}")
    lines.append(f"  because  {decision['because']}")
    if decision.get("held_on"):
        lines.append(
            f"  held_on  {decision['held_on']} · authoritative: "
            f"{decision.get('authoritative')}"
        )
    if decision.get("restarting_is_the_wrong_response"):
        lines.append(f"  restart  NOT the fix — {decision['restarting_is_the_wrong_response']}")
    if decision.get("blocked_by"):
        lines.append(f"  blocked  {', '.join(decision['blocked_by'])}")
    emit(payload, as_json=args.json, lines=lines)

    if payload["health"]["state"] in FAULT_STATES:
        return EXIT_FAULT
    if payload["health"]["state"] in ("UNKNOWN", "UNAVAILABLE") and args.strict:
        return EXIT_FAULT
    return EXIT_OK


def cmd_runtime_explain(args) -> int:
    """Why this resource is managed the way it is — the decision, not the
    measurement."""
    reg = load_registry(args.registry)
    resource = reg.get(args.id)
    health, _ = _observe(reg, resource)
    decision = health_mod.action_for(resource, health)
    payload = {
        "id": resource.id,
        "protected_refs": [e["ref"] for e in reg.protected_refs()],
        "defer_refs": [e["ref"] for e in reg.defer_refs()],
        "name_matches_protected": [
            e["ref"] for e in reg.refs_matching(resource.id)["protected"]
        ],
        "record": resource.record,
        "health_state": health.state,
        "action": decision,
        "automatable": resource.is_automatable,
        "why_not_automatable": resource.why_not_automatable(),
    }
    lines = [
        f"{resource.id} — {resource.note or 'no note recorded'}",
        "",
        f"desired      {resource.desired}",
        f"protection   {resource.protection}",
        f"automatable  {'yes' if resource.is_automatable else 'no'}",
    ]
    if not resource.is_automatable:
        for reason in resource.why_not_automatable():
            lines.append(f"  · {reason}")
    lines.append("")
    lines.append(f"state        {health.state}")
    lines.append(f"action       {decision['action']} — {decision['because']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_hygiene(args) -> int:
    reg = load_registry(args.registry)
    inv = inventory_mod.Inventory()
    hygiene = hygiene_mod.Hygiene(reg, inv,
                                  known_work_items=_known_work_items(args))
    report = hygiene.report()
    docker = report["docker"]

    payload = report
    lines = ["docker"]
    lines.append(f"  running {docker.get('running')} · stopped {docker.get('stopped')} "
                 f"· unregistered+running {docker.get('unregistered_running')}")
    for klass in sorted(docker.get("by_classification", {})):
        lines.append(f"    {klass:<26} {docker['by_classification'][klass]}")
    lines.append("")
    lines.append(f"  protected              {docker.get('protected')}")
    lines.append(f"  active (task-owned)    {docker.get('active_task_owned')}")
    lines.append(f"  stale candidates       {docker.get('stale_candidates')}")
    lines.append(f"  unknown owner          {docker.get('unknown_owner')}")
    lines.append(f"  orphaned               {docker.get('orphaned')}")
    lines.append("")
    lines.append("  actionable")
    if docker["actionable"]:
        for name in docker["actionable"]:
            lines.append(f"    · {name}")
    else:
        lines.append("    (none)")
    lines.append("")
    lines.append("disk")
    for mount in report["disk"]["roles"]:
        if mount.get("state") == "PRESENT":
            lines.append(f"  {mount['role']:<9} {mount['used_percent']:>5}% used  "
                         f"{human_bytes(mount['free_bytes'])} free")
    for note in report["disk"]["advisory"]["notes"]:
        lines.append(f"  ! {note}")
    lines.append("")
    lines.append(report["summary"]["note"])
    if report["summary"].get("unowned_note"):
        lines.append(report["summary"]["unowned_note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def _known_work_items(args):
    """Which work items are still open, if the caller knows.

    Deliberately NOT discovered here. Deciding whether a work item is closed
    is work-management's authority, and a resource-management component that
    infers "closed" from an empty directory is how a live lane gets reclaimed.
    """
    if not getattr(args, "open_items", None):
        return None
    return {item.strip() for item in args.open_items.split(",") if item.strip()}


def cmd_runtime_infrastructure(args) -> int:
    """§7/§9/§20/§21 — what outlives the work, what outranks what, and what
    restarting would achieve.

    Read-only, and it says so in its own output: every registration this host
    has is `protected`, so the honest report is a list of things nothing may
    touch plus the reason each one is on it.
    """
    reg = load_registry(args.registry)
    part = infra_mod.partition(reg)
    crit = infra_mod.criticality_report(reg)
    never = infra_mod.never_reclaim(reg)
    recovery = infra_mod.recovery_report(reg)
    payload = {
        "partition": part,
        "criticality": crit,
        "never_reclaim": never,
        "recovery": recovery,
    }
    lines = ["permanent infrastructure"]
    for row in part["permanent"]:
        lines.append(
            f"  {row['id']:<26} {row['kind']:<20} {row['criticality']:<9} "
            f"{row['protection']}"
        )
    lines.append("")
    lines.append("transient (outlives nothing — and not thereby reclaimable)")
    for row in part["transient"]:
        lines.append(
            f"  {row['id']:<26} {row['kind']:<20} {row['criticality']:<9} "
            f"{row['protection']}"
        )
    if part["contradictions"]:
        lines.append("")
        lines.append("contradictions")
        for row in part["contradictions"]:
            lines.append(f"  {row['id']}: {row['contradiction']}")
    lines.append("")
    lines.append(f"no automatic path reaches {never['count']} resource(s):")
    for entry in never["entries"]:
        lines.append(f"  {entry['id']:<26} {entry['lane']:<22} {entry['because']}")
    lines.append("")
    lines.append("recovery models")
    for row in recovery["models"]:
        auto = "automatic" if row["automatic"] else "never automatic"
        lines.append(f"  {row['id']:<26} {row['recovery']:<12} {auto}")
    lines.append("")
    lines.append(crit["confers"])
    if crit["defaulted"]:
        lines.append("")
        lines.append(
            "criticality not declared (derived from lifecycle): "
            + ", ".join(crit["defaulted"])
        )
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_tunnels(args) -> int:
    """§8 — declared tunnels, visible forwarders, and what could not be seen.

    The blind-spot list is part of the answer, not a footnote. An inventory
    that reports "0 tunnels" without saying whether it was capable of finding
    one is a number somebody will act on.
    """
    reg = load_registry(args.registry)
    # No host is passed. `tunnel_inventory` defaults to `attribution.LiveHost`,
    # which is the one that can read /proc — `probes.LiveHost` is a different
    # class without a process table, and passing it here raised an
    # `AttributeError` three frames down the first time it was tried.
    inventory = infra_mod.tunnel_inventory(registry_doc=reg)
    payload = inventory
    lines = [
        f"declared tunnels     {inventory['declared_count']}",
        f"visible forwarders   {inventory['observed_count']}",
    ]
    for row in inventory["declared"]:
        lines.append(
            f"  {row['id']:<24} {row['local']} -> {row['remote']} "
            f"({row['transport_class']})"
        )
    for row in inventory["observed_forwarders"]:
        lines.append(
            f"  pid {row['pid']:<8} {row['transport_class']:<12} "
            f"age {row['age_seconds']}s"
        )
    if inventory["undeclared_forwarders"]:
        lines.append("")
        lines.append(
            "forwarding, and not declared by anyone — candidates, not owners:"
        )
        for row in inventory["undeclared_forwarders"]:
            lines.append(f"  pid {row['pid']:<8} {row['transport_class']}")
    lines.append("")
    lines.append("what this node cannot see about tunnels")
    for spot in inventory["blind_spots"]:
        measured = ", ".join(f"{k}={v}" for k, v in spot["measured"].items())
        lines.append(f"  {spot['what']}: {spot['state']} ({measured})")
        lines.append(f"    {spot['why']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_probe(args) -> int:
    reg = load_registry(args.registry)
    resource = reg.get(args.id)
    host = probes_mod.LiveHost()
    results = []
    for probe in resource.probes:
        results.append(probes_mod.run_probe(resource, probe, host))
    payload = {"id": resource.id, "probes": results}
    lines = [f"{'PROBE':<24} {'AXIS':<11} {'STATE':<14} {'EVIDENCE':<20} AUTHORITATIVE"]
    for result in results:
        lines.append(
            f"{result['id']:<24} {result['axis']:<11} {result['state']:<14} "
            f"{result.get('evidence', {}).get('type', 'none'):<20} "
            f"{'yes' if result.get('authoritative') else 'no'}"
        )
        if result.get("error"):
            lines.append(f"    error: {result['error']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="serverosctl",
        description="ServerOS reference-node control CLI. Read-only: no "
                    "subcommand here stops, removes or restarts anything.",
    )
    # Global flags live on a parent parser as well as the top level, so that
    # `serverosctl --json runtime list` and `serverosctl runtime list --json`
    # both work. Only the first is idiomatic argparse, and only the second is
    # what people actually type — a tool whose flags only work in the
    # position the author had in mind gets its output parsed with grep.
    # `default=SUPPRESS` is load-bearing, not tidiness. argparse subparsers
    # write into the SAME namespace as the parent, so a plain default here
    # means the subparser re-sets `--registry` to None after the parent has
    # already parsed it — and `serverosctl --registry X runtime list` silently
    # reads the default registry instead of X. SUPPRESS means "only write this
    # attribute if the flag was actually given", so the parent's value
    # survives and an explicit sub-level flag still wins.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable output")
    common.add_argument("--registry", default=argparse.SUPPRESS,
                        help="path to the managed-runtime registry document")

    parser = argparse.ArgumentParser(
        prog="serverosctl",
        parents=[common],
        description="ServerOS reference-node control CLI. Read-only: no "
                    "subcommand here stops, removes or restarts anything.",
    )
    # NOT `parser.set_defaults(json=False, registry=None)`.
    #
    # `set_defaults` does not just populate `_defaults` — for any key that
    # matches an existing action it also rewrites that action's `.default`.
    # Because `parents=[common]` shares the action OBJECTS with every
    # subparser, setting the default here propagates into all of them, undoing
    # the SUPPRESS above and restoring the exact clobbering it was there to
    # prevent. The two traps compose, and the symptom (`--registry X` silently
    # ignored) looks nothing like the cause.
    #
    # So the missing attributes are filled in after parsing instead, in one
    # place, where it is visible.
    sub = parser.add_subparsers(dest="command", required=True)
    _COMMON = {"parents": [common]}

    device = sub.add_parser("device", help="this node", **_COMMON)
    device_sub = device.add_subparsers(dest="subcommand", required=True)
    device_show = device_sub.add_parser("show", help="identity, host summary, managed count", **_COMMON)
    device_show.set_defaults(func=cmd_device_show)

    context = sub.add_parser("context", help="what this shell is inside", **_COMMON)
    context_sub = context.add_subparsers(dest="subcommand", required=True)
    context_current = context_sub.add_parser("current", help="cwd, item, roots", **_COMMON)
    context_current.set_defaults(func=cmd_context_current)

    path = sub.add_parser("path", help="path governance (§40)", **_COMMON)
    path_sub = path.add_subparsers(dest="subcommand", required=True)
    p_resolve = path_sub.add_parser("resolve", help="a named reference becomes an absolute path", **_COMMON)
    p_resolve.add_argument("ref", nargs="?", help="<root>[/<suffix>]")
    p_resolve.add_argument("--work-item-run", nargs=3, metavar=("PROJECT", "ITEM", "RUN"))
    p_resolve.add_argument("--skill-run", nargs=2, metavar=("SKILL", "RUN"))
    p_resolve.set_defaults(func=cmd_path_resolve)
    p_admit = path_sub.add_parser("admit", help="check a path against the contract", **_COMMON)
    p_admit.add_argument("path")
    p_admit.add_argument("--read-only", action="store_true",
                         help="check as a read rather than a write")
    p_admit.set_defaults(func=cmd_path_admit)
    p_contract = path_sub.add_parser("contract", help="the declared root contract", **_COMMON)
    p_contract.set_defaults(func=cmd_path_contract)
    p_conform = path_sub.add_parser(
        "conformance", help="the declared contract against the directories on disk", **_COMMON)
    p_conform.set_defaults(func=cmd_path_conformance)

    resource = sub.add_parser("resource", help="host resource facts (§12)", **_COMMON)
    resource_sub = resource.add_subparsers(dest="subcommand", required=True)
    r_summary = resource_sub.add_parser("summary", help="cpu, memory, gpu, disk, docker", **_COMMON)
    r_summary.set_defaults(func=cmd_resource_summary)
    r_top = resource_sub.add_parser("top", help="top processes by cpu or memory", **_COMMON)
    r_top.add_argument("--limit", type=int, default=10)
    r_top.add_argument("--by", choices=("cpu", "mem"), default="cpu")
    r_top.set_defaults(func=cmd_resource_top)
    r_ps = resource_sub.add_parser("containers", help="docker containers, classified", **_COMMON)
    r_ps.add_argument("--running-only", action="store_true")
    r_ps.set_defaults(func=cmd_resource_docker_list)
    r_svc = resource_sub.add_parser("user-services", help="systemd --user units", **_COMMON)
    r_svc.set_defaults(func=cmd_resource_user_services)

    runtime = sub.add_parser("runtime", help="managed runtimes (§16)", **_COMMON)
    runtime_sub = runtime.add_subparsers(dest="subcommand", required=True)
    rt_list = runtime_sub.add_parser("list", help="every registration", **_COMMON)
    rt_list.set_defaults(func=cmd_runtime_list)
    rt_status = runtime_sub.add_parser("status", help="probe one resource", **_COMMON)
    rt_status.add_argument("id")
    rt_status.add_argument("--strict", action="store_true",
                           help="exit 1 when the state is UNKNOWN or UNAVAILABLE")
    rt_status.set_defaults(func=cmd_runtime_status)
    rt_explain = runtime_sub.add_parser("explain", help="why it is managed this way", **_COMMON)
    rt_explain.add_argument("id")
    rt_explain.set_defaults(func=cmd_runtime_explain)
    rt_hygiene = runtime_sub.add_parser("hygiene", help="what is here, and what may be touched", **_COMMON)
    rt_hygiene.add_argument("--open-items",
                            help="comma-separated work items known to be OPEN. "
                                 "Omit and nothing becomes a stale candidate: "
                                 "not knowing is not permission.")
    rt_hygiene.set_defaults(func=cmd_runtime_hygiene)
    rt_infra = runtime_sub.add_parser(
        "infrastructure",
        help="permanent vs transient, criticality, recovery models (§7/§9/§20)",
        **_COMMON,
    )
    rt_infra.set_defaults(func=cmd_runtime_infrastructure)
    rt_tun = runtime_sub.add_parser(
        "tunnels", help="declared tunnels, visible forwarders, blind spots (§8)",
        **_COMMON,
    )
    rt_tun.set_defaults(func=cmd_runtime_tunnels)

    probe = sub.add_parser("probe", help="run one resource's declared probes", **_COMMON)
    probe.add_argument("id")
    probe.set_defaults(func=cmd_probe)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Both global flags are declared with `default=SUPPRESS` (see
    # build_parser), so an unused one is simply absent. Normalized here, once.
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "registry"):
        args.registry = None
    try:
        return args.func(args)
    except registry_mod.UnknownResource as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except registry_mod.RegistryError as exc:
        sys.stderr.write(f"serverosctl: registry error: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except paths_mod.PathRefused as exc:
        sys.stderr.write(f"serverosctl: refused: {exc}\n")
        return EXIT_FAULT
    except BrokenPipeError:  # pragma: no cover - piping into `head`
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
