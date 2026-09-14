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
import socket
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
# The shared answer layer. `serverosd` and the MCP adapter call the SAME
# functions, so a question asked here and the same question asked over the
# socket cannot produce two different answers — only two measurements taken at
# two moments, which is what `answered_by` and `measured_at` are for.
view_mod = _load("serveros_runtime_view", "view.py")

EXIT_OK = 0
EXIT_FAULT = 1
EXIT_COULD_NOT_ANSWER = 2

FAULT_STATES = frozenset(
    {"FUNCTIONALLY_FAILED", "NOT_RUNNING", "DEGRADED", "NOT_READY"}
)


def default_registry_path() -> Path:
    """Kept as a name because callers here and in tests ask for a path.

    The resolution itself lives in `registry.py` — the module that owns the
    document — so the CLI and `serverosd` cannot come to disagree about where
    the node's registry is. Both ask this one function's producer.
    """
    return registry_mod.default_path()


def load_registry(path: str | None):
    return registry_mod.load_default(path)


# ---------------------------------------------------------------------------
# Rendering. Machine output is the primary form; the human form is a view of
# it, so the two can never disagree about a fact.
# ---------------------------------------------------------------------------


def daemon_socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "serveros/serveros.sock"


def ask_daemon(method: str, params: dict | None = None,
               *, timeout: float = 5.0) -> dict:
    """One request to the running node runtime.

    Every other command in this file computes its answer in-process from
    `view.py`, and that is deliberate — an answer you can reproduce by running
    a thing is an answer you can check. This is the exception, and only for
    the questions that are *about the daemon*: whether it is up, how long it
    has been up, when it last looked. Computing those locally would be
    answering a question about one process from inside another.

    Raises `DaemonUnavailable` rather than falling back to a local
    computation, because a status command that quietly answers from somewhere
    else is worse than one that says it cannot answer.
    """
    path = daemon_socket_path()
    if not path.exists():
        raise DaemonUnavailable(
            f"no node runtime at {path}. The socket is created by "
            f"`serverosd`; without it there is no running control plane to "
            f"ask, and this command will not answer from a different source."
        )
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(path))
        connection.sendall(
            (json.dumps({"method": method, "params": params or {}}) + "\n").encode())
        buffer = b""
        while b"\n" not in buffer:
            chunk = connection.recv(1 << 20)
            if not chunk:
                break
            buffer += chunk
    except OSError as exc:
        raise DaemonUnavailable(f"{path}: {exc}") from exc
    finally:
        connection.close()
    if not buffer.strip():
        raise DaemonUnavailable(
            f"{path} accepted the connection and closed it without answering.")
    reply = json.loads(buffer.split(b"\n")[0])
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise DaemonUnavailable(
            f"serverosd refused {method}: {error.get('code')}: "
            f"{error.get('message')}")
    return reply["result"]


class DaemonUnavailable(RuntimeError):
    """The node runtime could not answer. Not a fault — a missing measurement."""


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
        # Read from the registry, not repeated here. This was a hard-coded
        # copy of a fact the registry document already states, and two places
        # declaring one node's identity is exactly how they come to disagree.
        "device_ref": reg.device_ref,
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
        f"device      {reg.device_ref}",
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


def cmd_status(args) -> int:
    """The node runtime, as it describes itself. §52.

    Every other command here answers about the world. This one answers about
    the control plane — whether it is running, how long it has been up, when
    it last looked — which is why it asks the daemon instead of computing the
    answer locally. A status command that falls back to a local computation
    reports a healthy node while the daemon is dead.

    Exit 2 when the daemon cannot be reached: that is "could not answer", not
    "the node is broken", and the CLI's exit contract keeps those apart.
    """
    try:
        payload = ask_daemon("node.status")
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        if args.json:
            json.dump({"ok": False, "runtime": "NOT_RUNNING", "detail": str(exc)},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return EXIT_COULD_NOT_ANSWER

    stale = payload["answer_is_stale"]
    lines = [
        f"runtime     {'STALE' if stale else 'RUNNING'}"
        + (f" · last tick {payload['seconds_since_last_tick']}s ago"
           if payload["seconds_since_last_tick"] is not None else " · no tick yet"),
        f"device      {payload['device_ref']}",
        f"pid         {payload['pid']}  (up {_human_seconds(payload['uptime_seconds'])})",
        f"ticks       {payload['ticks']} at {payload['tick_interval_seconds']}s"
        f" · failures {payload['tick_failure_count']}",
        f"registry    {payload['registry_count']} registered",
        f"reconcile   {'enabled' if payload['reconcile_enabled'] else 'disabled'}"
        f" (process interlock)",
        f"state       {payload['state_dir']}",
        f"socket      {payload['socket']}",
        "",
        payload["note"],
    ]
    emit(payload, as_json=args.json, lines=lines)
    # Stale is a fault: the daemon is answering, and its answer is about a
    # node it stopped looking at. Exit 1 so a shell can tell it from a
    # healthy run without parsing the text.
    return EXIT_FAULT if stale else EXIT_OK


def cmd_ability_list(args) -> int:
    """§55 item 14 — what capabilities this node can actually resolve.

    The daemon has served `ability.list` since §24, but the only way to reach
    it was to write a newline-delimited JSON request onto the socket by hand,
    so "Ability discovery" was implemented and unreachable. This asks the
    same question over IPC.

    It asks the daemon rather than reading the projection file directly, and
    that is the point: the daemon grades the projection (FRESH / STALE /
    UNREADABLE / NOT_MATERIALIZED). A CLI that opened `effective.json` itself
    would report an empty answer for all four states, and "the projection is
    two days old" is not the same finding as "nothing is installed".
    """
    try:
        payload = ask_daemon("ability.list")
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        if args.json:
            json.dump({"ok": False, "registry_status": "UNREADABLE", "detail": str(exc)},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return EXIT_COULD_NOT_ANSWER

    abilities = payload.get("abilities")
    age = payload.get("age_seconds")
    lines = [f"authority   {payload.get('authority')}",
             f"projection  {payload.get('registry_status')}"
             + (f" · {_human_seconds(age)} old" if age is not None else "")
             + (f" · {payload['source']}" if payload.get("source") else "")]
    if not abilities:
        # Three different facts print as the same empty list unless they are
        # told apart here, and they mean opposite things.
        lines.append(f"abilities   none readable — {payload.get('note', 'no note')}")
    else:
        lines.append(f"{'NAME':<28} {'TYPE':<7} {'REVS':<5} AGENTS")
        for ability in abilities:
            agents = ability.get("agents") or []
            lines.append(f"{str(ability.get('name', '?')):<28} "
                         f"{str(ability.get('type', '?')):<7} "
                         f"{str(ability.get('revisions_installed', '?')):<5} "
                         f"{','.join(agents) if agents else '-'}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_ability_status(args) -> int:
    """One ability, by name. Exit 1 when the projection does not carry it.

    Not an error — absence from a local projection is not proof of absence on
    the authority, and the daemon's answer says so — but it is a state a shell
    needs to distinguish, so it gets its own exit code.
    """
    try:
        payload = ask_daemon("ability.status", {"name": args.name})
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        if args.json:
            json.dump({"ok": False, "registry_status": "UNREADABLE", "detail": str(exc)},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return EXIT_COULD_NOT_ANSWER

    installed = payload.get("installed")
    lines = [f"ability     {payload.get('name')}",
             f"projection  {payload.get('registry_status')}",
             f"installed   {installed}"]
    if installed:
        # The projection's own field names, not a guess at them. Rendering
        # `revision`/`state` columns for a row that carries `version`/
        # `revisions_installed` printed a table of `?` and looked like an
        # empty registry rather than a wrong reader.
        for key in ("id", "type", "path", "version", "revisions_installed",
                    "fingerprint", "agents"):
            if payload["ability"].get(key) is not None:
                lines.append(f"{key:<19} {payload['ability'][key]}")
    else:
        lines.append(payload.get("note", ""))
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK if installed else EXIT_FAULT


def cmd_device_agents(args) -> int:
    """§9/§18 — the agent runtimes this node has, and whether parity holds.

    The observation half is read-only: whether each declared adapter surface
    exists on this device. Nothing is created, and a surface that cannot be
    resolved is reported as unobserved rather than as absent — a path role
    that is not bound means nobody has looked, which is not the same finding
    as a file that is not there.
    """
    parity_mod = _load_module_from(REPO_ROOT / "abilities/runtime_parity.py",
                                   "serveros_runtime_parity")
    contract_path = REPO_ROOT / "abilities/agent-runtime-contract.yaml"
    registry_path = REPO_ROOT / "fleet/registry.yaml"

    import yaml
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    registry = (yaml.safe_load(registry_path.read_text(encoding="utf-8"))
                if registry_path.is_file() else None)

    # Observe what is actually on this disk, per runtime per surface.
    paths = paths_mod.Paths()
    observed: dict[str, dict] = {}
    for runtime in contract.get("ids") or ():
        entry = {}
        for field in parity_mod.OBSERVABLE_SURFACES:
            specs = ((contract.get("capabilities") or {}).get(field) or {}).get(runtime)
            if specs is None:
                continue
            if isinstance(specs, dict):
                specs = [specs]
            measured = []
            for spec in specs:
                try:
                    base = paths.root(spec["path_role"])
                except Exception:  # noqa: BLE001 - an unbound role is a finding
                    continue
                target = base / spec["relative"]
                measured.append({"path": str(target), "present": target.exists()})
            entry[field] = measured
        observed[runtime] = entry

    result = parity_mod.report(contract, registry, observed)

    lines = [f"runtimes    {', '.join(result['runtimes'])}",
             f"compared    {', '.join(result['compared_fields'])}",
             f"may differ  {', '.join(result['allowed_differences'])}"]
    for runtime in result["runtimes"]:
        for field in parity_mod.OBSERVABLE_SURFACES:
            for surface in (observed.get(runtime) or {}).get(field) or []:
                mark = "present" if surface["present"] else "MISSING"
                lines.append(f"  {runtime:<12} {field:<20} {mark}  {surface['path']}")
    lines.append(f"verdict     {result['verdict']}")
    for finding in result["findings"]:
        lines.append(f"  finding   {finding['code']}: {finding['detail']}")
    # Acknowledged defects are printed, not hidden. Moving them out of
    # `findings` is what stops the check being permanently red; printing them
    # is what stops that from becoming a way to stop looking.
    for item in result["acknowledged"]:
        lines.append(f"  known     {item['code']}: {item['detail']} "
                     f"[{item['declared_gap'].get('state')}, "
                     f"{item['declared_gap'].get('closing_action')}]")
    for unknown in result["unknowns"]:
        lines.append(f"  unknown   {unknown['code']}: {unknown['detail']}")
    for surface in result["unobserved_declared_gaps"]:
        lines.append(f"  stale gap {surface}: recorded but not observed; "
                     "either it was fixed or the record is wrong")
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK if result["verdict"] == "CONFORMANT" else EXIT_FAULT


def cmd_ability_resolve(args) -> int:
    """§45/§46 — can THIS device do THIS thing, now, and if not why not.

    The distinction from `ability status` is the reason both exist and is worth
    stating, because they read like the same question. `ability status` reads
    the projection: what the authority says is installed, and how fresh that
    answer is. This runs the resolver: it intersects the eight inputs for one
    device, one runtime and one moment. A thing can be listed and not
    resolvable — installed nowhere this runtime can reach is the ordinary case,
    and reporting it as available because the authority lists it is the defect
    `abilities/resolver.py` was written to prevent.

    Until this command existed the kernel had no caller at all outside its own
    tests. It was complete, correct, thirty-odd green tests, and unreachable —
    the same shape as `admission()`, and the reason the test for this asserts
    the wiring rather than the logic.

    The inputs come from `--request`, and deliberately from nowhere else yet.
    Assembling them from live state is the next stage; a version of this that
    guessed at them would produce a confident `AVAILABLE` computed from inputs
    nobody supplied, which is precisely what the kernel now refuses to do.
    """
    resolver_mod = _load_module_from(REPO_ROOT / "abilities" / "resolver.py",
                                     "serveros_ability_resolver")
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    if args.ability:
        request["ability"] = args.ability
    if args.device:
        request["device_ref"] = args.device
    if args.runtime:
        request["runtime"] = args.runtime

    try:
        record = resolver_mod.resolve(request)
    except resolver_mod.Rejected as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    lines = [
        f"ability     {record['ability']}",
        f"device      {record['device_ref']}",
        f"runtime     {record['runtime']}",
        f"status      {record['status']}"
        + (f" · {record['reason']}" if record.get("reason") else ""),
    ]
    if record.get("provider"):
        lines.append(f"provider    {record['provider']}")
    if record.get("detail"):
        lines.append(f"detail      {record['detail']}")
    # What it could see, always. A status computed from whichever inputs
    # happened to be readable is the finding this line exists to expose, and
    # printing it only when something is missing would hide it exactly when a
    # caller has stopped reading.
    lines.append(f"inputs seen {', '.join(record['inputs_seen']) or 'none'}")
    lines.append(f"missing     {', '.join(record['inputs_missing']) or 'none'}")
    for candidate in record.get("candidates", []):
        lines.append(f"  candidate {candidate['provider']} → {candidate['status']}"
                     + (f" ({candidate['reason']})" if candidate.get("reason") else "")
                     + (f" refused_by={candidate['refused_by']}"
                        if candidate.get("refused_by") else ""))
    emit(record, as_json=args.json, lines=lines)

    # Three answers, three exit codes, because a shell has to tell them apart:
    # it can go and use it, it must go and fix something, or it must go and
    # supply an input. UNKNOWN is not a failure of this command.
    if record["status"] in ("AVAILABLE", "DEGRADED"):
        return EXIT_OK
    if record["status"] == "UNKNOWN":
        return EXIT_COULD_NOT_ANSWER
    return EXIT_FAULT


def _human_seconds(seconds: float) -> str:
    seconds = int(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}{seconds % size // 60 if unit == 'd' else ''}"
    return f"{seconds}s"


def cmd_context_startup(args) -> int:
    """§29–§32 — the whole of what a session is told before it asks anything.

    This exists so the disclosure kernel has a caller. Built the same day as
    `ability resolve`, which was added because `abilities/resolver.py` turned
    out to have no caller outside its own tests — and a second kernel written
    in the same round with the same defect would have been a poor showing.

    Every input here is either read cheaply or reported missing:

      item            inferred, never guessed, by the same resolver
                      `context current` uses
      capability      ONE projection read, matching `costs.capability.L0` =
                      "one manifest read, no probes". It is therefore a
                      CATALOGUE count and it is rendered as one. The resolved
                      count needs the eight-input intersection and is not
                      claimed here.
      authority       the bound roots from the path contract

    A daemon that will not answer degrades the line to `unknown` rather than
    dropping it. Three lines are not smaller than five; they are five with two
    missing, and a reader cannot tell which.
    """
    disclosure_mod = _load_module_from(REPO_ROOT / "context/disclosure.py",
                                       "serveros_disclosure")

    item = None
    item_why = "no item inferred from this directory"
    try:
        context_resolver = _load_module_from(REPO_ROOT / "context/resolver.py",
                                             "serveros_context_resolver")
        item = context_resolver.infer_item(context_resolver.Roots.from_env(),
                                           Path.cwd())
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        # SHORT, and this is the disclosure rule rather than a lost detail.
        # The first version interpolated `str(exc)`, whose text runs to a full
        # sentence naming the cwd and the three directory shapes an Item can
        # live in — and that one line took the startup view from inside its
        # budget to 160 tokens against a declared 120. Measured on the live
        # node, minutes after the budget was declared.
        #
        # The long form is not deleted. It is what `serverosctl context current`
        # prints, which the pointer line at the bottom of this view names. L0
        # says that it does not know; L1 says why. That is the whole of §29.
        item_why = f"{type(exc).__name__}; `serverosctl context current` explains"

    # The catalogue count, over IPC, because the daemon grades its own
    # projection and a CLI opening the file itself would report an empty
    # answer for FRESH, STALE, UNREADABLE and NOT_MATERIALIZED alike.
    capability_count = None
    capability_why = None
    try:
        payload = ask_daemon("ability.list")
        abilities = payload.get("abilities")
        if abilities is None:
            capability_why = (f"the daemon could not read its projection "
                              f"({payload.get('registry_status')})")
        else:
            capability_count = len(abilities)
    except DaemonUnavailable as exc:
        capability_why = str(exc)

    # A COUNT, not the list, for the same reason capability L0 is a count:
    # naming all twelve bound roots costs about 60 of the 120 tokens and is
    # the sentence a reader skips. The first version of this line did name
    # them, and the budget refused it — the same mutation as capability L0
    # growing a list, in the axis next door, which the mutation tests had not
    # covered because they only imagined it happening to capability.
    contract = paths_mod.Paths().contract()
    authority_line = (f"authority: writes under {len(contract['roots'])} bound "
                      "roots are admitted; everything else asks "
                      "(`serverosctl path contract` lists them)")

    if args.json:
        record = disclosure_mod.startup_view(
            item=item, capability_count=capability_count,
            authority_line=authority_line,
            context_why=item_why if item is None else None,
            authority_why=None)
        emit(record, as_json=True)
        return EXIT_OK

    # The rendered view is the output. Printing the record as well would double
    # the text and make `within_budget` a statement about the wrong number.
    record = disclosure_mod.startup_view(
        item=item, capability_count=capability_count,
        authority_line=authority_line,
        context_why=item_why if item is None else None,
        authority_why=None)
    sys.stdout.write(record["text"] + "\n")
    if not record["within_budget"]:
        # Not an error, and not silent either. A budget nobody is told about
        # being exceeded is a style guide.
        sys.stderr.write(
            f"serverosctl: startup view is {len(record['lines'])} lines / "
            f"~{sum(len(l['text']) for l in record['lines']) // 4} tokens, "
            f"over the declared {record['budget']['lines']}/"
            f"{record['budget']['tokens']}\n")
    if capability_why:
        sys.stderr.write(f"serverosctl: capabilities line is a catalogue "
                         f"count unavailable — {capability_why}\n")
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
    # §17. No owner states are passed, and that is the honest default rather
    # than an oversight: whether a work item is open is `work-management`'s
    # authority, so every task-scoped resource comes back DEFER until somebody
    # who knows supplies it. A CLI that guessed "closed" here would be
    # proposing removals on the strength of not having asked.
    reclaim = infra_mod.reclaim_report(reg)
    payload = {
        "partition": part,
        "criticality": crit,
        "never_reclaim": never,
        "recovery": recovery,
        "reclaim": reclaim,
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


def cmd_runtime_egress(args) -> int:
    """§58 — why the local Agent cannot reach out, in four answers.

    The default requirement is the one this node actually has: `.bashrc` sends
    every outbound model call through `127.0.0.1:17890`. Defaulting to it is a
    judgement worth stating — it is the only outbound dependency on this host
    that is DECLARED anywhere, and a command that made the operator retype the
    endpoint would be answering a question nobody had.
    """
    reg = load_registry(args.registry)
    requirement = {
        "runtime": args.runtime,
        "kind": args.kind,
        "endpoint": args.endpoint,
        "named_by": egress_named_by(args.endpoint),
        "because": args.because,
    }
    doc = infra_mod.egress_answer(reg, requirement)
    answers = doc["answers"]

    lines = [
        f"endpoint             {requirement['endpoint']}",
        f"runtime              {requirement['runtime']}",
        f"declared by          "
        + (", ".join(d["where"] for d in requirement["named_by"]) or "nobody"),
        "",
        f"reachable            {answers['reachable']['value']}",
        f"  {answers['reachable']['claim']}",
        "",
        f"missing              {answers['which_runtime_is_missing']['state']}",
        f"  {answers['which_runtime_is_missing']['because']}",
        "",
        f"owner                {answers['who_owns_it']['state']}",
        f"  {answers['who_owns_it']['because']}",
        "",
        f"auto recovery        "
        f"{'permitted' if answers['auto_recovery_permitted']['permitted'] else 'NOT permitted'}",
        f"  {answers['auto_recovery_permitted']['because']}",
        "",
        f"action               {doc['action']}",
    ]
    emit(doc, as_json=args.json, lines=lines)
    return EXIT_OK


def egress_named_by(endpoint: str) -> list[dict]:
    """The declarations for an endpoint, measured on this node."""
    return infra_mod.egress_declarations(endpoint)


def cmd_runtime_groups(args) -> int:
    """§3/§5 — every runtime group, largest by summed lifetime CPU first."""
    payload = view_mod.runtime_groups()
    lines = [
        f"{'GROUP':<58} {'N':>3} {'CPU%':>8} {'RSS':>10} {'AGE':>9} AGENT",
    ]
    for row in payload["groups"]:
        agg = row["aggregate"]
        agent = (row["agent"] or {}).get("value") or "-"
        lines.append(
            f"{row['ref'][:58]:<58} {row['member_count']:>3} "
            f"{agg['cpu_percent']:>8.1f} "
            f"{human_bytes(agg['memory_rss_bytes']):>10} "
            f"{_duration(agg['age_seconds']):>9} {agent}"
        )
    lines.append("")
    lines.append(_coverage_line(payload["coverage"]))
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_group(args) -> int:
    """§5 — one group: membership, cost, and the chain that explains it."""
    try:
        payload = view_mod.runtime_group(args.ref)
    except view_mod.UnknownSubject as exc:
        return _unknown_subject(exc, args)
    group = payload["group"]
    lines = [
        f"group      {group['ref']}",
        f"           {group['member_count']} member(s) · kind {group['kind']} · "
        f"root pid {group['root_pid']}",
        f"scope      {group['scope']}",
        f"session    {group['session_ref'] or '—'}",
        f"container  {group['container_ref'] or '—'}",
        _aggregate_line(group["aggregate"]),
        "",
        "chain",
    ]
    for name, value, status in view_mod.chain_rows(payload["chain"]):
        lines.append(f"  {name:<22} {value[:46]:<48} {status}")
    _append_unknown_lines(lines, payload["unknown_links"])
    lines.append("")
    lines.append(_coverage_line(payload["coverage"]))
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_tree(args) -> int:
    """§3 — the whole graph for one group, drawn.

    With no ref, the largest group is drawn: the same group `runtime groups`
    already puts first, by the same ordering, so the two commands cannot
    disagree about which one that is. It is labelled as defaulted in the
    answer rather than chosen quietly — a subject picked for the reader is
    only acceptable if the reader can see it was picked.
    """
    ref = args.ref
    defaulted = ref is None
    if defaulted:
        groups = view_mod.runtime_groups()["groups"]
        if not groups:
            sys.stderr.write("serverosctl: no runtime group is visible to this "
                             "uid, so there is no tree to draw.\n")
            return EXIT_COULD_NOT_ANSWER
        ref = groups[0]["ref"]
    try:
        payload = view_mod.runtime_tree(ref)
    except view_mod.UnknownSubject as exc:
        return _unknown_subject(exc, args)
    if defaulted:
        payload["ref_defaulted"] = True
        payload["ref_defaulted_because"] = (
            "no ref was given. The largest group by summed lifetime CPU was "
            "drawn, which is the same group `runtime groups` lists first. "
            "Pass a ref — a group ref, its id, or any member pid — to choose."
        )
    group = payload["group"]
    lines = []
    if defaulted:
        lines.append("no ref given: drawing the largest group. Pass a ref to choose.")
        lines.append("")
    lines += [
        f"group      {group['ref']}",
        _aggregate_line(group["aggregate"]),
        "",
        "chain",
    ]
    for name, value, status in view_mod.chain_rows(payload["chain"]):
        lines.append(f"  {name:<22} {value[:46]:<48} {status}")
    _append_unknown_lines(lines, payload["unknown_links"])
    lines.append("")
    tree = payload.get("tree") or {}
    lineage_note = (
        "root is reparented to init — its actual parent is gone"
        if tree.get("reparented_to_init")
        else f"root is parented to {tree.get('root_ppid')}"
    )
    lines.append(f"process tree ({lineage_note})")
    lines.extend(_render_tree(tree.get("tree")))
    stopped = (
        "the top of the process table was reached"
        if tree.get("chain_terminated_because") == "reached_pid_1"
        else "that ancestor is not readable to this uid, so the walk stopped "
             "there and the tree above it is unknown rather than absent"
    )
    lines.append(f"  lineage read as far as pid {tree.get('chain_terminated_at')} — "
                 f"{stopped}")
    lines.append("")
    lines.append(_coverage_line(payload["coverage"]))
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_budget(args) -> int:
    """§19 — what the daemon has retried, and what it may not retry again."""
    reg = load_registry(args.registry)
    payload = view_mod.retry_budget(registry=reg)
    lines = [
        f"budget     {payload['max_attempts']} attempts per "
        f"{payload['window_seconds']}s, backoff {payload['backoff_seconds']}",
        f"state      {payload['budget_file']}",
        "",
    ]
    if not payload["resources"]:
        lines.append("no resource has ever been acted on.")
    else:
        lines.append(f"{'RESOURCE':<28} {'USED':>4} {'MAY ACT':<8} {'LAST':<10} BLOCKED BY")
        for row in payload["resources"]:
            lines.append(
                f"{row['resource']:<28} {row['attempts_in_window']:>4} "
                f"{'yes' if row['may_act_now'] else 'no':<8} "
                f"{str(row['last_action'] or '—'):<10} {row['blocked_by'] or ''}"
            )
    if payload["unregistered_with_history"]:
        lines.append("")
        lines.append("acted on, and no longer registered: "
                     + ", ".join(payload["unregistered_with_history"]))
    lines.append("")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# process (§14)
# ---------------------------------------------------------------------------


def cmd_process_top(args) -> int:
    """§14 — the busiest processes, each with what it belongs to."""
    payload = view_mod.process_top(limit=args.limit, by=args.by)
    lines = [
        f"{'PID':>8} {'CPU%':>7} {'RSS':>10} {'AGE':>9} {'USER':<10} "
        f"{'ROLE':<16} {'SESSION':<12} COMMAND"
    ]
    for row in payload["rows"]:
        lines.append(
            f"{row['pid']:>8} {row['cpu_percent']:>7.1f} "
            f"{human_bytes(row['rss_bytes']):>10} "
            f"{_duration(row['age_seconds']):>9} {str(row['user']):<10} "
            f"{row['role']:<16} {str(row['session_ref'] or '—'):<12} "
            f"{row['comm']}"
        )
    lines.append("")
    lines.append(_coverage_line(payload["coverage"]))
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_process_trace(args) -> int:
    """§14 — one pid, attributed, with its lineage and its scope."""
    try:
        pid = int(args.pid)
    except ValueError:
        sys.stderr.write(
            f"serverosctl: {args.pid!r} is not a pid. This command traces a "
            f"single process; use `runtime groups` to find one.\n"
        )
        return EXIT_COULD_NOT_ANSWER
    payload = view_mod.process_trace(pid)
    process = payload["process"]
    if not payload["readable"]:
        lines = [
            f"pid        {pid}",
            f"readable   no",
            f"why        {process.get('why')}",
            "",
            _coverage_line(payload["coverage"]),
        ]
        emit(payload, as_json=args.json, lines=lines)
        # An unreadable pid is a COMPLETE answer — "not readable, and here is
        # why" — so it exits 0 unless the caller asked for strictness. Exiting 1
        # by default would report another user's process as this node's fault.
        return EXIT_FAULT if args.strict else EXIT_OK

    runtime = process["runtime"]
    origin = process.get("origin") or {}
    lines = [
        f"pid        {runtime['pid']}  ({runtime['comm']})  ppid {runtime['ppid']}",
        f"command    {runtime.get('command', {}).get('executable')}",
        f"user       {runtime.get('user')} · threads {runtime.get('num_threads')} "
        f"· age {_duration(runtime.get('age_seconds'))}",
        f"cpu        {runtime.get('cpu_percent_lifetime')}% "
        f"({runtime.get('cpu_metric')})",
        f"rss        {human_bytes(runtime.get('rss_bytes'))}",
        f"cgroup     {runtime.get('cgroup') or '—'}",
        "",
        f"session    {process.get('session_ref') or '—'}",
        f"unit       {process.get('systemd_unit_ref') or '—'}",
        f"container  {process.get('container_ref') or '—'}",
        f"origin     device={_link_value(origin.get('device_ref'))} "
        f"transport={_link_value(origin.get('transport'))} "
        f"task={_link_value(origin.get('task_ref'))}",
        f"owner      {(process.get('owner') or {}).get('ref')} "
        f"({(process.get('owner') or {}).get('status')})",
        "",
        "lineage",
    ]
    for entry in payload["ancestry"]:
        marker = "*" if entry["is_self"] else " "
        lines.append(f"  {marker} {entry['pid']:>8} {str(entry['role'] or ''):<16} "
                     f"{entry['comm']}")
    lines.append(f"  {payload['descendant_count']} descendant(s), "
                 f"{len(payload['children'])} direct child(ren)")
    if payload.get("group"):
        lines.append("")
        lines.append(f"group      {payload['group']['ref']}")
    lines.append("")
    lines.append(process.get("attribution", {}).get("note", ""))
    lines.append(_coverage_line(payload["coverage"]))
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# anomaly (§10/§11)
# ---------------------------------------------------------------------------


def cmd_anomaly_list(args) -> int:
    """§10 — every anomaly the node can support, and every input it lacked."""
    reg = load_registry(args.registry)
    payload = view_mod.anomaly_list(registry=reg)
    lines = []
    if not payload["signals"]:
        lines.append("no anomaly fired.")
    else:
        lines.append(f"{'CODE':<34} {'SEV':<7} SUBJECT")
        for entry in payload["signals"]:
            lines.append(
                f"{entry['code']:<34} {entry['severity']:<7} {entry['subject']}"
            )
    lines.append("")
    lines.append("classifications")
    if not payload["classifications"]:
        lines.append("  (none — nothing had a signal)")
    for item in payload["classifications"]:
        lines.append(
            f"  {item['classification']:<38} {item['subject']}"
        )
        lines.append(
            f"    → {item['recommendation']} · criticality {item['criticality']} · "
            f"protection {item['protection']}"
        )
    lines.append("")
    lines.append("inputs the detector could NOT evaluate")
    for entry in payload["unchecked"]:
        lines.append(f"  {entry['input']:<14} {entry['state']:<8} {entry['why']}")
    lines.append("")
    lines.append(_coverage_line(payload["coverage"]))
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)

    if args.strict:
        # Anything other than "no anomaly fired" or "signals with no declared
        # combination" is worth a non-zero exit for a monitor. It is still not
        # an action: every recommendation on this surface is a proposal.
        interesting = {
            c["classification"] for c in payload["classifications"]
        } - {"HEALTHY_OBSERVED", "SIGNALS_PRESENT"}
        if interesting:
            return EXIT_FAULT
    return EXIT_OK


def cmd_anomaly_explain(args) -> int:
    """§10 — why one subject classified the way it did, including what did not."""
    reg = load_registry(args.registry)
    payload = view_mod.anomaly_explain(args.subject, registry=reg)
    if not payload["found"]:
        lines = [
            f"subject    {payload['subject']}",
            "found      no",
            payload["because"],
        ]
        if payload["subjects_with_signals"]:
            lines.append("")
            lines.append("subjects that DID have signals:")
            for subject in payload["subjects_with_signals"]:
                lines.append(f"  {subject}")
        lines.append("")
        lines.append("inputs the detector could NOT evaluate")
        for entry in payload["unchecked"]:
            lines.append(f"  {entry['input']:<14} {entry['why']}")
        emit(payload, as_json=args.json, lines=lines)
        return EXIT_OK

    lines = [
        f"subject        {payload['subject']}",
        f"classification {payload['classification']}",
        f"because        {payload['because']}",
        f"criticality    {payload['criticality']} · protection {payload['protection']}",
        "",
        f"recommendation {payload['recommendation']}",
    ]
    if payload["recommendation_because"]:
        lines.append(f"  because      {payload['recommendation_because']}")
    blast = payload["blast_radius"]
    lines.append(f"  affects      {', '.join(blast['affected_subjects'])}")
    if blast["protected_excluded"]:
        lines.append(f"  excluded     {'; '.join(blast['protected_excluded'])}")
    lines.append("")
    lines.append("signals (every one, unchanged)")
    for entry in payload["signals"]:
        measured = ", ".join(f"{k}={v}" for k, v in entry["measured"].items())
        lines.append(f"  {entry['code']:<34} {entry['severity']:<7} {measured}")
        lines.append(f"    window {entry['window']}")
        lines.append(f"    {entry['why']}")
    lines.append("")
    lines.append("combinations that did NOT fire")
    if not payload["combinations_not_met"]:
        lines.append("  (every declared combination was satisfied)")
    for entry in payload["combinations_not_met"]:
        lines.append(
            f"  {entry['combination']:<38} missing {', '.join(entry['missing'])}"
        )
    lines.append("")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# container (§6)
# ---------------------------------------------------------------------------


def cmd_container_attribution(args) -> int:
    """§6 — every container, attributed from evidence, with the evidence kept."""
    reg = load_registry(args.registry)
    payload = view_mod.container_attribution(registry=reg)
    lines = [
        f"{'DISPOSITION':<12} {'STATE':<10} {'CONF':<7} OWNER / LOCATOR",
    ]
    for row in payload["containers"]:
        owner = (row.get("owner") or {})
        where = owner.get("ref")
        if not where:
            locator = row.get("locator") or {}
            where = (f"no owner — locator {locator.get('path')}"
                     f" ({locator.get('root')}, from {locator.get('from')})"
                     if locator.get("path") else "no owner, and no locator either")
        lines.append(
            f"{row['disposition']:<12} {str(row.get('state')):<10} "
            f"{row['attribution']['confidence']:<7} {where}"
        )
        if row.get("unknown_owner"):
            lines.append(f"    {row['disposition_because']}")
    lines.append("")
    lines.append(f"{payload['count']} container(s) · "
                 f"{payload['unknown_owner_count']} unknown owner · "
                 f"by disposition {payload['by_disposition']}")
    lines.append("")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# rendering helpers for the commands above
# ---------------------------------------------------------------------------


def _duration(seconds) -> str:
    """A compact duration. Each unit divides, which the first version did not.

    It printed `16397h` for four and a half hours — the seconds value with an
    `h` glued on. The rendering was the only thing wrong and it was wrong in
    the direction that looks plausible, which is the kind of wrong a reader
    does not check.
    """
    if seconds is None:
        return "UNKNOWN"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _aggregate_line(aggregate: dict) -> str:
    return (
        f"aggregate  cpu {aggregate['cpu_percent']}% (summed lifetime average, "
        f"so 400% is four cores) · rss {human_bytes(aggregate['memory_rss_bytes'])} "
        f"· age {_duration(aggregate['age_seconds'])} · "
        f"{aggregate['process_count']} process(es)"
    )


def _coverage_line(coverage: dict) -> str:
    state = "hidepid=invisible" if coverage.get("hidepid_invisible") else "no hidepid"
    return (
        f"coverage   {coverage['visible_processes']} process(es) visible to "
        f"{coverage['scope']} of {coverage['proc_pids_present']} pids in /proc "
        f"({state})"
    )


def _append_unknown_lines(lines: list[str], unknown: list[dict]) -> None:
    if not unknown:
        return
    lines.append("")
    lines.append("this node did NOT establish")
    for entry in unknown:
        lines.append(f"  {entry['link']:<22} {entry['why']}")


def _link_value(link) -> str:
    if not isinstance(link, dict):
        return "—"
    value = link.get("value")
    if value is None:
        return f"UNKNOWN ({link.get('status')})"
    return f"{value} ({link.get('status')})"


def _render_tree(node) -> list[str]:
    """An ASCII tree. `node` is `attribution.process_tree`'s nested form.

    No depth limit of its own: the tree comes from `descendants()`, which is
    already cycle-safe, and a second guard here would be a limit nobody could
    reach and nobody would test.
    """
    if not node:
        return []
    lines: list[str] = []

    def walk(current: dict, prefix: str, last: bool, root: bool) -> None:
        branch = "" if root else ("└── " if last else "├── ")
        lines.append(f"  {prefix}{branch}{current['pid']} {current.get('comm') or ''}".rstrip())
        children = current.get("children") or []
        # The vertical bar is carried down only while there are siblings still
        # to come, so the drawing stays a tree rather than a ladder.
        child_prefix = prefix + ("" if root else ("    " if last else "│   "))
        for index, child in enumerate(children):
            walk(child, child_prefix, index == len(children) - 1, False)

    walk(node, "", True, True)
    return lines


def _unknown_subject(exc, args) -> int:
    sys.stderr.write(f"serverosctl: {exc}\n")
    if not args.json:
        sys.stderr.write(
            "serverosctl: run `runtime groups` for the refs this node can see.\n"
        )
    return EXIT_COULD_NOT_ANSWER


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
# memory (§20–§28)
# ---------------------------------------------------------------------------


def _memory_modules():
    return (
        _load_module_from(REPO_ROOT / "memory" / "record.py",
                          "serveros_memory_record"),
        _load_module_from(REPO_ROOT / "memory" / "projection.py",
                          "serveros_memory_projection"),
    )


def _scope_module():
    """§18's kernel. The same synthetic name `record.py` and `projection.py`
    load it under, so the `RecallRefused` this file catches is the one they
    raise rather than a second class with the same name."""
    return _load_module_from(REPO_ROOT / "memory" / "scope.py",
                             "serveros_memory_scope")


def _ledger_revision(directory: Path) -> dict:
    """What revision the ledger ON DISK is at, and how that was decided.

    Not `git rev-parse HEAD`, which is the answer to a different question. The
    ledger's files may have been edited since the last commit, and a projection
    stamped with HEAD while the working tree has moved is exactly the failure
    this round has already recorded once under another name: a pin that matches
    the declaration it was copied from, and not the content it is supposed to
    identify.

    So the revision names the content. Clean tree: the commit, which is what a
    reader can `git show`. Dirty tree, or no repository at all: a hash of the
    ledger files themselves, prefixed so that the two are never confused —
    `sha+content:abcd1234` says "commit sha, modified by local content".
    """
    import hashlib
    import subprocess

    files = sorted(p for p in Path(directory).glob("*") if p.is_file())
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    content = digest.hexdigest()[:8]

    head = None
    try:
        proc = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=10)
        if proc.returncode == 0:
            head = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None

    dirty = False
    if head:
        try:
            proc = subprocess.run(
                ["git", "-C", str(directory), "status", "--porcelain", "--",
                 str(directory), *[str(p) for p in files]],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                timeout=10)
            dirty = bool(proc.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            dirty = True

    if head and not dirty:
        revision = head
    elif head:
        revision = f"{head}+content:{content}"
    else:
        revision = f"content:{content}"
    return {"revision": revision, "head": head, "dirty": dirty,
            "files": len(files), "content": content}


def _ledger_dir(args) -> Path:
    paths = paths_mod.Paths()
    if getattr(args, "ledger_dir", None):
        return Path(args.ledger_dir)
    return Path(paths.memory_ledger(args.project)["path"])


def cmd_memory_audit(args) -> int:
    """The whole-ledger check the record contract declares and nothing ran."""
    record_mod, _ = _memory_modules()
    directory = _ledger_dir(args)
    files = sorted(p for p in directory.glob("*")
                   if p.is_file() and p.suffix in (".yaml", ".yml", ".json"))
    records = record_mod.load(files) if files else []
    report = {**record_mod.audit(records), "directory": str(directory)}
    lines = [
        f"ledger      {directory}",
        f"files       {len(files)}",
        f"records     {report['records']}  valid {report['valid']}",
        f"verdict     {report['verdict']}",
    ]
    for finding in report["findings"]:
        lines.append(f"  {finding['code']}  {finding.get('id') or ''}")
        for reason in finding["reasons"]:
            lines.append(f"      {reason}")
    if not files:
        # Not a defect. A project with no memory has no memory, which is the
        # state every project starts in and most stay in.
        lines.append("  (no ledger yet — an empty ledger is the starting state, "
                     "not a failure)")
    for note in report["not_checked"]:
        lines.append(f"  not checked: {note}")
    emit(report, as_json=args.json, lines=lines)
    return EXIT_OK if report["verdict"] == "CONFORMANT" else EXIT_FAULT


def cmd_memory_project(args) -> int:
    """Build the Layer A projection. The write side `projections/` never had."""
    record_mod, projection_mod = _memory_modules()
    directory = _ledger_dir(args)
    files = sorted(p for p in directory.glob("*")
                   if p.is_file() and p.suffix in (".yaml", ".yml", ".json"))
    records = record_mod.load(files) if files else []

    # Refuse before writing. A projection built from a ledger with a broken
    # record in it is a derived copy of a defect, and the defect is much easier
    # to see in the ledger than in a projection of it.
    report = record_mod.audit(records)
    if report["verdict"] != "CONFORMANT":
        emit({**report, "directory": str(directory), "written": []},
             as_json=args.json,
             lines=[f"refused     ledger has {len(report['findings'])} finding(s)",
                    "            `serverosctl memory audit` names them"])
        return EXIT_FAULT

    revision = _ledger_revision(directory)
    generated_at = args.generated_at or _utc_now()
    ledger_ref = args.ledger_ref or f"records/projects/{args.project}/memory"
    document = projection_mod.build(
        records, ledger_ref=ledger_ref, ledger_revision=revision["revision"],
        generated_at=generated_at,
        generator="serverosctl memory project")
    manifest = projection_mod.manifest_for(document, generated_at=generated_at,
                                           generator="serverosctl memory project")
    out = Path(args.out) if args.out else Path(
        paths_mod.Paths().memory_projection()["path"])
    written = projection_mod.write(document, manifest, out)
    payload = {"written": [str(p) for p in written], "records": len(records),
               "subjects": len(document["subjects"]), "ledger": revision,
               "generated_at": generated_at}
    emit(payload, as_json=args.json,
         lines=[f"wrote       {', '.join(str(p) for p in written)}",
                f"records     {len(records)}  subjects {len(document['subjects'])}",
                f"ledger      {revision['revision']}"
                + ("  (dirty: the revision names the content, not HEAD)"
                   if revision["dirty"] else "")])
    return EXIT_OK


def cmd_memory_status(args) -> int:
    """Is the projection current, and if not, against what?"""
    _, projection_mod = _memory_modules()
    directory = _ledger_dir(args)
    revision = _ledger_revision(directory)
    out = Path(args.projection_dir) if args.projection_dir else Path(
        paths_mod.Paths().memory_projection()["path"])
    result = projection_mod.status(out, ledger_revision=revision["revision"])
    payload = {**result, "ledger": revision, "ledger_dir": str(directory),
               "records": None}
    lines = [f"projection  {result['directory']}",
             f"ledger      {revision['revision']}",
             f"status      {result['status']}",
             f"            {result['because']}"]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK if result["status"] == "CURRENT" else EXIT_FAULT


def cmd_memory_subject(args) -> int:
    """What this work currently remembers about one subject.

    `--as` is required by the parser, and the two readings it accepts are
    opposite rather than two flavours of the same thing: `operator` admits
    everything, an Item ref admits that Item. There is no default because a
    default is a guess about who is asking, and here it is a guess about
    whether a boundary applies.
    """
    record_mod, _ = _memory_modules()
    scope_mod = _scope_module()
    directory = _ledger_dir(args)
    files = sorted(p for p in directory.glob("*")
                   if p.is_file() and p.suffix in (".yaml", ".yml", ".json"))
    records = record_mod.load(files) if files else []
    try:
        reader = scope_mod.parse_reader(args.reader)
    except scope_mod.ScopeRefused as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_COULD_NOT_ANSWER
    try:
        value = record_mod.current_value(records, args.subject, reader=reader)
    except scope_mod.RecallRefused as exc:
        # §18. Reported as a refusal and not as an absence, because the wrong
        # answer here is the specific one that reads as "this Item has no
        # memory of that" about an Item the reader is not allowed to remember.
        emit({"subject": exc.subject, "reader": exc.reader, "recalled": False,
              "reason": exc.reason, "refused": exc.refusals},
             as_json=args.json,
             lines=[f"subject     {exc.subject}",
                    f"reader      {scope_mod.describe(exc.reader)}",
                    f"            REFUSED  {exc.reason}",
                    f"            {exc.why}",
                    f"            refused {len(exc.refusals)} record(s)"])
        return EXIT_FAULT
    if value is None:
        emit({"subject": args.subject, "current": None}, as_json=args.json,
             lines=[f"subject     {args.subject}",
                    "            no records. Not an error — most subjects "
                    "have no memory."])
        return EXIT_COULD_NOT_ANSWER
    lines = [f"subject     {args.subject}",
             f"reader      {scope_mod.describe(reader)}",
             f"current     {value.get('id')}  {value.get('kind')}  {value.get('at')}"]
    for field in ("by", "source", "revision", "scope"):
        if value.get(field):
            lines.append(f"{field:<12}{value[field]}")
    chain = record_mod.chain(records, value["id"])
    if len(chain) > 1:
        lines.append(f"supersedes  {' <- '.join(r['id'] for r in chain[1:])}")
    if value.get("body"):
        lines.append("")
        lines.append(str(value["body"]).strip())
    elif value.get("note"):
        lines.append("")
        lines.append(str(value["note"]).strip())
    # The reader is echoed on the answer as well as the refusal. A read that
    # says who made it is the trace §18 has, since the ledger records what was
    # written and never what was read.
    emit({"subject": args.subject, "reader": reader, "current": value,
          "chain": chain}, as_json=args.json, lines=lines)
    return EXIT_OK


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# §17 — what a thread is for.
#
# These four are the only commands in this file that WRITE control-plane state
# through the daemon, and the reason they are here rather than behind an MCP
# tool is worth stating: the MCP surface is for a caller that is creating a
# session, and it is the wrong shape for the two things this group exists for.
#
#   1. MIGRATION. Every session that existed before §17 has no binding, and
#      under §17 an unbound thread admits nothing. Somebody has to be able to
#      say what those threads are for — or to close them — from a terminal,
#      without a caller having to pretend to be an Agent Session.
#
#   2. THE COORDINATOR THREAD. A coordinator is a legitimate role and the role
#      that crosses Items, and a person starting one is not an Agent Session.
#
# The rules are not restated here. `coordination.py` owns them and the daemon
# applies them to the write, so this file cannot create a binding the MCP path
# would have refused.
# ---------------------------------------------------------------------------


def _coordination_answer(method: str, params: dict) -> tuple[dict, list[str], int]:
    """One daemon call, and the exit code its answer earns.

    A refusal is an answer and exits 0 when the daemon answered it: "this
    session is bound to another Item" is a fact about the world, reported in a
    word a caller can switch on. Exit 2 is for the daemon not answering at all,
    which is a missing measurement rather than a finding.
    """
    try:
        return ask_daemon(method, params), [], EXIT_OK
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return {}, [], EXIT_COULD_NOT_ANSWER


def cmd_coordination_resolve(args) -> int:
    """What this thread is for, and whether its Item still admits work."""
    payload, _, code = _coordination_answer(
        "agent.binding.resolve", {"session_ref": args.session_ref})
    if code != EXIT_OK:
        return code
    if not payload.get("found"):
        emit(payload, as_json=args.json, lines=[
            f"session     {payload.get('session_ref')}",
            "            unbound. Under §17 an unbound thread admits nothing: "
            "a send into it is refused with INVALID_CONTEXT. Bind it with "
            "`serverosctl coordination bind`, or close it."])
        return EXIT_OK
    binding = payload.get("binding") or {}
    item = payload.get("item") or {}
    lines = [
        f"session     {binding.get('session_ref')}",
        f"role        {binding.get('session_role')}",
        f"item        {binding.get('bound_item_ref') or 'none (coordinator)'}",
        f"thread      {binding.get('state')}",
    ]
    if item:
        lines.append(
            f"item state  {item.get('state')}"
            + (f" / {item.get('outcome')}" if item.get("outcome") else "")
            + f"   axis={payload.get('item_axis')}")
    lines.append(f"read from   {(payload.get('provenance') or {}).get('state_db')}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_coordination_bind(args) -> int:
    """Record what a thread is for. The write, and the migration path.

    Called with a role and (for an execution) an Item. A session that already
    has a DIFFERENT binding is refused by the store rather than rebound: there
    is no flag here to change which Item a thread is for, and its absence is
    the immutability rather than a restriction on top of it.
    """
    params = {"session_ref": args.session_ref, "session_role": args.role}
    if args.item:
        params["bound_item_ref"] = args.item
    if args.work_item:
        params["work_item"] = args.work_item
    payload, _, code = _coordination_answer("agent.binding.record", params)
    if code != EXIT_OK:
        return code
    if not payload.get("recorded"):
        sys.stderr.write(
            f"serverosctl: refused: {payload.get('reason')}: {payload.get('detail')}\n")
        return EXIT_FAULT
    binding = payload.get("binding") or {}
    emit(payload, as_json=args.json, lines=[
        f"session     {binding.get('session_ref')}",
        f"role        {binding.get('session_role')}",
        f"item        {binding.get('bound_item_ref') or 'none (coordinator)'}",
        f"recorded    {binding.get('recorded_at') or ''}"])
    return EXIT_OK


def cmd_coordination_close(args) -> int:
    """Close a thread for new work. Never a deletion, and never a rebind."""
    params = {"session_ref": args.session_ref}
    if args.reason:
        params["reason"] = args.reason
    payload, _, code = _coordination_answer("agent.binding.close", params)
    if code != EXIT_OK:
        return code
    if not payload.get("closed"):
        sys.stderr.write(
            f"serverosctl: refused: {payload.get('reason')}: {payload.get('detail')}\n")
        return EXIT_FAULT
    binding = payload.get("binding") or {}
    emit(payload, as_json=args.json, lines=[
        f"session     {binding.get('session_ref')}",
        f"closed at   {binding.get('closed_at')}",
        f"item kept   {binding.get('bound_item_ref') or 'none (coordinator)'}",
        "            the record stays: closing is what makes a later send "
        "refusable, and a deletion would answer nothing"])
    return EXIT_OK


def cmd_coordination_list(args) -> int:
    """Counts by role. Never the records, and never a directory of Items."""
    payload, _, code = _coordination_answer("agent.binding.list", {})
    if code != EXIT_OK:
        return code
    lines = [f"schema      {payload.get('schema')}",
             f"bindings    {payload.get('count')}"]
    for role, refs in sorted((payload.get("by_role") or {}).items()):
        lines.append(f"  {role:<10}{len(refs)}")
    lines.append(f"closed      {len(payload.get('closed') or [])}")
    lines.append(f"item axis   {', '.join(payload.get('item_axis_values') or [])}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# §14/§15/§16/§16A/§17 — the five surfaces this round added
# ---------------------------------------------------------------------------
#
# Read-only, and read-only in a way a reviewer can check rather than take on
# trust: not one of them takes a flag that names a resource to act on, and each
# prints the `note` its document carries instead of a summary line of its own.
# The rule the whole round is written under is that a command which could only
# be right on a good day is worse than a command that refuses.


def cmd_resource_ports(args) -> int:
    """§14 — what listens, and what is claimed about who owns it."""
    reg = load_registry(args.registry)
    payload = view_mod.port_ownership(registry_doc=reg)
    coverage = payload["coverage"]
    lines = [
        f"endpoints            {coverage['endpoints']}",
        f"claimed by a container {coverage['claimed_by_a_container']}",
        f"claimed by a tunnel  {coverage['claimed_by_a_tunnel']}",
        f"with an authoritative owner {coverage['with_an_authoritative_owner']}",
        # Printed even when it is the largest number, and NOT as a failure.
        # §14 forbids attributing every connection to a managed runtime, so on
        # a shared host this is expected to be big; a report that hid it would
        # make "we could not say" look like "nobody is there".
        f"unattributed         {coverage['unattributed']}",
        f"listening, no visible holder {coverage['listening_with_no_visible_holder']}",
        "",
        f"{'ENDPOINT':<30} {'OWNER':<26} CONF",
    ]
    rows = payload["ports"]
    if getattr(args, "claimed_by_a_container", False):
        # A filter over the SAME payload, reported as a filter. The coverage
        # counts above stay whole on purpose: "how many endpoints did I hide"
        # is not an answer this command may leave implicit. `container_ref` is
        # the measured field — an endpoint a published port resolves to — not
        # a re-derivation of the count.
        rows = [row for row in rows if row.get("container_ref")]
        lines.append(f"(filtered to the container-claimed endpoints: "
                     f"{len(rows)} of {coverage['endpoints']})")
    for row in rows:
        lines.append(f"{row['port_ref']:<30} "
                     f"{str(row.get('owner_ref') or '—'):<26} {row['confidence']}")
    if payload["conflicts"]:
        lines.append("")
        lines.append("claims that disagree — both sides kept, neither chosen:")
        for conflict in payload["conflicts"]:
            lines.append(f"  {conflict.get('port_ref')}: {conflict.get('why')}")
    for name, provider in sorted(payload["providers"].items()):
        if provider["state"] != "AVAILABLE":
            lines.append(f"  {name}: {provider['state']} — {provider['why']}")
    lines.append("")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_desired(args) -> int:
    """§15 — every registration against what was measured about it."""
    reg = load_registry(args.registry)
    payload = view_mod.desired_actual(registry_doc=reg)
    lines = [
        f"registrations  {payload['providers']['registry']['registrations']}",
        f"in mismatch    {len(payload['mismatches'])}",
        # `unchecked` is printed beside `mismatches`, never behind it. An empty
        # mismatch list means one thing when everything was compared and
        # another when nothing was, and the second reads as the first.
        f"unchecked      {len(payload['unchecked'])}",
        "",
        f"{'RESOURCE':<30} {'VERDICT':<17} DESIRED / ACTUAL",
    ]
    for row in payload["comparisons"]:
        shown = f"{row['desired_state']} / {row['actual_state']}"
        if row["actual_derived_state"]:
            shown += f" ({row['actual_derived_state']})"
        lines.append(f"{str(row['resource_id']):<30} {row['verdict']:<17} {shown}")
    for row in payload["comparisons"]:
        if row["verdict"] != "IN_DESIRED_STATE":
            lines.append(f"  {row['resource_id']}: {row['why']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_runtime_classify(args) -> int:
    """§16 — the nine classes over the registrations, with the gaps named."""
    reg = load_registry(args.registry)
    payload = view_mod.runtime_hygiene(registry_doc=reg)
    lines = [f"{'CLASS':<20} COUNT"]
    for name, count in sorted(payload["counts"].items()):
        lines.append(f"{name:<20} {count}")
    lines.append("")
    lines.append(f"{'RESOURCE':<42} CLASS")
    for row in payload["resources"]:
        also = row.get("also_matches") or []
        suffix = f"  (also matches: {', '.join(also)})" if also else ""
        lines.append(f"{row['resource_ref']:<42} {row['class']}{suffix}")
    if payload["candidates"]:
        lines.append("")
        lines.append("candidates — a question for a person, never an instruction:")
        for row in payload["candidates"]:
            lines.append(f"  {row.get('resource_ref')}: {row.get('why')}")
    lines.append("")
    lines.append(f"inputs gathered      {', '.join(payload['inputs_gathered']) or 'none'}")
    # The negative half is the report. A class that could not be decided
    # because its input was never measured is not a class that was checked and
    # found false, and the difference is the whole reason this is printed.
    lines.append(f"inputs NOT gathered  {', '.join(payload['inputs_not_gathered']) or 'none'}")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_fs_hygiene(args) -> int:
    """§16A — the roots classified, and nothing moved."""
    payload = view_mod.fs_hygiene()
    lines = [f"{'ROOT':<12} {'ENTRIES':<8} {'ENFORCED':<9} PATH"]
    for name, root in sorted(payload["roots"].items()):
        coverage = (payload.get("coverage") or {}).get(name) or {}
        lines.append(f"{name:<12} {str(coverage.get('entries', '—')):<8} "
                     f"{str(coverage.get('vocabulary_enforced_on_write', '—')):<9} "
                     f"{root.get('path')}")
        lines.append(f"    {root.get('class')}: {coverage.get('why') or ''}")
    lines.append("")
    lines.append(f"{'CLASS':<24} COUNT")
    for name, count in sorted(payload["counts"].items()):
        lines.append(f"{name:<24} {count}")
    # MISPLACED and the other "this is in the wrong place" classes are the
    # answer to the round's third question, so they are printed as entries
    # rather than only counted.
    for row in payload.get("open_questions") or []:
        lines.append("")
        lines.append(f"{row['class']:<14} {row['path']}  ({row['kind']})")
        lines.append(f"    {row['why']}")
    lines.append("")
    lines.append(f"roles resolved       "
                 f"{(payload.get('role_locations') or {}).get('entries', 0)} entries, "
                 f"{len((payload.get('role_locations') or {}).get('declared') or [])} declared")
    for target, row in sorted((payload.get("symlinks_into_data_root") or {}).items()):
        lines.append(f"    symlink {row.get('home_path')} -> {target}")
    if payload.get("duplicate_semantic_roots"):
        lines.append("")
        lines.append("duplicate semantic roots — REPORTED, never reunited:")
        for name, paths in sorted(payload["duplicate_semantic_roots"].items()):
            lines.append(f"    {name}: {paths}")
    lines.append("")
    lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_resource_docker(args) -> int:
    """§6/§7 — the Docker inventory, and the reclaimable picture on request."""
    space = bool(getattr(args, "space", False))
    payload = view_mod.docker_observation(space=space)
    summary = payload["summary"]
    lines = [
        f"containers           {summary.get('Containers')} "
        f"({summary.get('ContainersRunning')} running, "
        f"{summary.get('ContainersStopped')} stopped)",
        f"images               {summary.get('Images')}",
        f"data root            {summary.get('DockerRootDir')}",
        f"storage driver       {summary.get('Driver')}",
        f"volumes              {payload['volumes'].get('count')}",
        f"networks             {payload['networks'].get('count')}",
        f"images dangling      {payload['images'].get('dangling')}",
        "",
        "largest observed consumers, by measured size",
    ]
    for row in payload["largest_observed_consumers"]:
        lines.append(f"  {str(row['name'])[:30]:<30} "
                     f"writable {human_bytes(row['writable_layer_bytes']):>10}  "
                     f"rootfs {human_bytes(row['rootfs_bytes']):>10}")
    if space:
        sections = (payload.get("space") or {}).get("sections") or {}
        lines.append("")
        lines.append("reclaimable — what a prune WOULD free, not a candidate list")
        for name, row in sorted(sections.items()):
            lines.append(f"  {name:<14} {human_bytes(row.get('reclaimable_bytes'))} "
                         f"of {human_bytes(row.get('size_bytes'))}")
        lines.append(f"  total          "
                     f"{human_bytes((payload.get('space') or {}).get('reclaimable_bytes_total'))}")
    else:
        lines.append("")
        lines.append(payload["space_note"])
    lines.append("")
    lines.append(payload["advisory"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_resource_anomaly(args) -> int:
    """§18/§19 — the resource codes, and the ones that could not be graded.

    The second half is the point of the command and is why it prints
    `codes_not_evaluated` before it prints anything reassuring: a code nobody
    looked for and a code looked for and not found are the same empty list, and
    on this node two of the fifteen need a `docker system df` nobody has paid
    for.
    """
    space = bool(getattr(args, "space", False))
    payload = view_mod.resource_anomaly(space=space)
    lines = [
        f"observed at          {payload.get('observed_at') or 'UNKNOWN'}",
        f"codes graded         {payload['coverage']['graded']} of "
        f"{payload['coverage']['of']}",
        "",
    ]
    if payload["signals"]:
        lines.append(f"{'SEVERITY':<9} {'CODE':<30} SUBJECT")
        for row in payload["signals"]:
            lines.append(f"{row['severity']:<9} {row['code']:<30} {row['subject']}")
            measured = row.get("measured") or {}
            threshold = {k: v for k, v in measured.items()
                         if k.startswith("threshold")}
            lines.append(f"          {row['why'][:96]}")
            if threshold:
                lines.append(f"          measured {measured} "
                             f"(threshold {threshold})")
            # §26. The window is what makes this row ageable later, so it is
            # printed beside the freshness rather than left in the JSON only.
            lines.append(f"          source {row['source']} · window "
                         f"{row['window']} · freshness "
                         f"{row['freshness']['state']}")
    else:
        lines.append("no signal fired")
    lines.append("")
    lines.append("codes NOT evaluated — not looked for, which is not the same as")
    lines.append("looked for and not found:")
    for row in payload["codes_not_evaluated"]:
        lines.append(f"  {row['code']:<30} {row['why']}")
    lines.append("")
    gathering = payload["gathering"]
    lines.append(f"inputs reached       "
                 f"{', '.join(gathering['inputs_reached']) or 'none'}")
    lines.append(f"inputs unreachable   "
                 f"{', '.join(f'{k} ({v})' for k, v in gathering['inputs_unreachable'].items())
                 or 'none'}")
    policy = payload["supported_by_resource_policy"]
    lines.append(f"governance document  {policy.get('state')} "
                 f"(destructive reconcile "
                 f"{(policy.get('destructive_reconcile') or {}).get('enabled')})")
    if not space:
        lines.append("")
        lines.append("the reclaimable/build-cache codes are not graded in this "
                     "answer: they need one `docker system df`, measured at "
                     "~22s. Ask with `serverosctl resource anomaly --space`")
    lines.append("")
    lines.append(payload["advisory"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_tunnel_list(args) -> int:
    """§13 — the tunnels this node can see, and the ones it cannot."""
    payload = view_mod.tunnel_observation(registry_doc=args.registry)
    lines = [
        f"TUNNEL OBSERVATION = {payload['state']}",
        f"declared tunnels     {payload['declared_count']}",
        f"visible forwarders   {payload['observed_count']}",
    ]
    for row in payload["declared"]:
        lines.append(f"  {row['id']:<24} {row['local']} -> {row['remote']} "
                     f"({row['transport_class']})")
    for row in payload["observed_forwarders"]:
        lines.append(f"  pid {row['pid']:<8} {row['transport_class']:<12} "
                     f"age {row['age_seconds']}s")
    if payload["undeclared_forwarders"]:
        lines.append("")
        lines.append("forwarding, and not declared by anyone — candidates, not owners:")
        for row in payload["undeclared_forwarders"]:
            lines.append(f"  pid {row['pid']:<8} {row['transport_class']}")
    lines.append("")
    lines.append("what this node cannot see about tunnels")
    for spot in payload["blind_spots"]:
        measured = ", ".join(f"{k}={v}" for k, v in spot["measured"].items())
        lines.append(f"  {spot['what']}: {spot['state']} ({measured})")
        lines.append(f"    {spot['why']}")
    lines.append("")
    lines.append(payload["advisory"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_container_protection(args) -> int:
    """§17 — protection per container, and every spelling where it differs."""
    reg = load_registry(args.registry)
    inv = inventory_mod.Inventory()
    listing = inv.docker_list()
    payload = view_mod.protection(containers=listing.get("containers") or [],
                                  registry_doc=reg)
    counts = payload["counts"]
    lines = [
        f"containers checked  {payload['checked']}",
        f"protected           {counts['protected']}",
        f"not protected       {counts['not_protected']}",
        # `unknown` is printed and is not a rounding of the other two: it is
        # "nothing on this node could be asked", and folding it into "not
        # protected" would be a protection bypass performed by a summary line.
        f"unknown             {counts['unknown']}",
        "",
        f"{'CONTAINER':<22} {'NAME':<34} PROTECTED",
    ]
    for row in payload["containers"]:
        lines.append(f"{str(row['subject']):<22} {str(row['name']):<34} "
                     f"{row['protected']}")
    if payload["findings"]:
        lines.append("")
        lines.append("the answer differs by spelling:")
        for finding in payload["findings"]:
            lines.append(f"  {finding['subject']}: {finding['why']}")
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

    status = sub.add_parser(
        "status", help="the node runtime itself: up, ticking, when it last looked",
        **_COMMON)
    status.set_defaults(func=cmd_status)

    device = sub.add_parser("device", help="this node", **_COMMON)
    device_sub = device.add_subparsers(dest="subcommand", required=True)
    device_show = device_sub.add_parser("show", help="identity, host summary, managed count", **_COMMON)
    device_show.set_defaults(func=cmd_device_show)
    device_agents = device_sub.add_parser(
        "agents", help="the agent runtimes here, and whether parity holds (§18)",
        **_COMMON)
    device_agents.set_defaults(func=cmd_device_agents)

    context = sub.add_parser("context", help="what this shell is inside", **_COMMON)
    context_sub = context.add_subparsers(dest="subcommand", required=True)
    context_current = context_sub.add_parser("current", help="cwd, item, roots", **_COMMON)
    context_current.set_defaults(func=cmd_context_current)
    context_startup = context_sub.add_parser(
        "startup", help="the startup view: one line per axis, L0 only (§29)", **_COMMON)
    context_startup.set_defaults(func=cmd_context_startup)

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
    r_ports = resource_sub.add_parser(
        "ports", help="listening endpoints and who may be claiming them (§14)",
        **_COMMON)
    r_ports.add_argument(
        "--claimed-by-a-container", action="store_true",
        help="only the endpoints a container's published port claims")
    r_ports.set_defaults(func=cmd_resource_ports)
    r_docker = resource_sub.add_parser(
        "docker", help="containers, images, volumes, networks and sizes (§6/§7)",
        **_COMMON)
    r_docker.add_argument(
        "--space", action="store_true",
        help="also read the reclaimable/build-cache picture. One "
             "`docker system df`, measured at ~22s, so it is opt-in rather "
             "than part of every call")
    r_docker.set_defaults(func=cmd_resource_docker)
    r_anomaly = resource_sub.add_parser(
        "anomaly",
        help="the §18 resource codes, and the ones that could not be graded",
        **_COMMON)
    r_anomaly.add_argument(
        "--space", action="store_true",
        help="also grade the two BuildKit/Docker-store codes. One "
             "`docker system df`, measured at ~22s, so it is opt-in rather "
             "than part of every call")
    r_anomaly.set_defaults(func=cmd_resource_anomaly)

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
    rt_egress = runtime_sub.add_parser(
        "egress",
        help="why the local Agent cannot reach out (§58): which runtime is "
             "missing, who owns it, may anything recover it",
        **_COMMON,
    )
    rt_egress.add_argument("--endpoint", default="127.0.0.1:17890",
                           help="host:port the Agent's traffic is sent to")
    rt_egress.add_argument("--runtime", default="tunnel:agent-openai-outbound",
                           help="the runtime id this endpoint is supposed to have")
    rt_egress.add_argument("--kind", default="tunnel")
    rt_egress.add_argument("--because", default=(
        "the Codex/OpenAI path on this node is routed through this proxy, so "
        "every outbound model call depends on it"))
    rt_egress.set_defaults(func=cmd_runtime_egress)
    rt_groups = runtime_sub.add_parser(
        "groups", help="every runtime group on this host (§3/§5)", **_COMMON)
    rt_groups.set_defaults(func=cmd_runtime_groups)
    rt_group = runtime_sub.add_parser(
        "group", help="one runtime group, its members and its chain (§5)", **_COMMON)
    rt_group.add_argument("ref", help="a group ref, its id, or any member pid")
    rt_group.set_defaults(func=cmd_runtime_group)
    rt_tree = runtime_sub.add_parser(
        "tree", help="one runtime group, drawn: chain, lineage, processes (§3)", **_COMMON)
    rt_tree.add_argument(
        "ref", nargs="?", default=None,
        help="a group ref, its id, or any member pid. Defaults to the largest "
             "group, and says so when it does.")
    rt_tree.set_defaults(func=cmd_runtime_tree)
    rt_budget = runtime_sub.add_parser(
        "budget", help="what the daemon has retried, and what it may not (§19)",
        **_COMMON)
    rt_budget.set_defaults(func=cmd_runtime_budget)
    rt_desired = runtime_sub.add_parser(
        "desired",
        help="what was DECLARED this runtime should be, versus what was "
             "measured (§15). Compares nothing where nothing declared one",
        **_COMMON)
    rt_desired.set_defaults(func=cmd_runtime_desired)
    rt_classify = runtime_sub.add_parser(
        "classify",
        help="every registration under the §16 classes, with the inputs that "
             "could not be gathered named rather than assumed",
        **_COMMON)
    rt_classify.set_defaults(func=cmd_runtime_classify)

    process = sub.add_parser(
        "process", help="processes, attributed (§14)", **_COMMON)
    process_sub = process.add_subparsers(dest="subcommand", required=True)
    pr_top = process_sub.add_parser(
        "top", help="the busiest processes, each with what it belongs to", **_COMMON)
    pr_top.add_argument("--limit", type=int, default=10)
    pr_top.add_argument("--by", choices=("cpu", "mem"), default="cpu")
    pr_top.set_defaults(func=cmd_process_top)
    pr_trace = process_sub.add_parser(
        "trace", help="one pid: lineage, scope, owner, and what was unreadable",
        **_COMMON)
    pr_trace.add_argument("pid")
    pr_trace.add_argument("--strict", action="store_true",
                          help="exit 1 when the pid could not be read")
    pr_trace.set_defaults(func=cmd_process_trace)

    anomaly = sub.add_parser(
        "anomaly", help="anomalies and what they do NOT justify (§10/§11)", **_COMMON)
    anomaly_sub = anomaly.add_subparsers(dest="subcommand", required=True)
    an_list = anomaly_sub.add_parser(
        "list", help="every anomaly this node can support, and its blind spots",
        **_COMMON)
    an_list.add_argument("--strict", action="store_true",
                         help="exit 1 when anything classified beyond "
                              "'no anomaly' or 'signals present'")
    an_list.set_defaults(func=cmd_anomaly_list)
    an_explain = anomaly_sub.add_parser(
        "explain", help="why a subject classified as it did, and what did not fire",
        **_COMMON)
    an_explain.add_argument("subject", help="a subject ref, e.g. a runtime-group ref")
    an_explain.set_defaults(func=cmd_anomaly_explain)

    container = sub.add_parser(
        "container", help="containers, attributed from evidence (§6)", **_COMMON)
    container_sub = container.add_subparsers(dest="subcommand", required=True)
    c_attr = container_sub.add_parser(
        "attribution", help="who each container belongs to, and the evidence", **_COMMON)
    c_attr.set_defaults(func=cmd_container_attribution)
    c_protection = container_sub.add_parser(
        "protection",
        help="is each container protected, by what declaration, and does the "
             "answer change with the spelling asked (§17)",
        **_COMMON)
    c_protection.set_defaults(func=cmd_container_protection)

    fs = sub.add_parser(
        "fs", help="the filesystem roots, classified and not moved (§16A)",
        **_COMMON)
    fs_sub = fs.add_subparsers(dest="subcommand", required=True)
    fs_hygiene = fs_sub.add_parser(
        "hygiene",
        help="what is canonical, what is an allowed exception, and what is "
             "in the wrong place — advisory only, nothing is moved",
        **_COMMON)
    fs_hygiene.set_defaults(func=cmd_fs_hygiene)

    tunnel = sub.add_parser(
        "tunnel", help="tunnels, forwarders and blind spots (§13)", **_COMMON)
    tunnel_sub = tunnel.add_subparsers(dest="subcommand", required=True)
    t_list = tunnel_sub.add_parser(
        "list",
        help="the tunnels this node can see, and — the important half — the "
             "ones it structurally cannot",
        **_COMMON)
    t_list.set_defaults(func=cmd_tunnel_list)

    ability = sub.add_parser(
        "ability", help="what capabilities this node can resolve (§55)",
        **_COMMON)
    ability_sub = ability.add_subparsers(dest="subcommand", required=True)
    ab_list = ability_sub.add_parser(
        "list", help="every ability in the local projection, and how fresh it is",
        **_COMMON)
    ab_list.set_defaults(func=cmd_ability_list)
    ab_status = ability_sub.add_parser(
        "status", help="one ability by name", **_COMMON)
    ab_status.add_argument("name")
    ab_status.set_defaults(func=cmd_ability_status)
    ab_resolve = ability_sub.add_parser(
        "resolve",
        help="intersect the eight inputs for one device and runtime (§45)",
        **_COMMON)
    ab_resolve.add_argument("--request", required=True,
                            help="a JSON file holding the resolver's inputs")
    ab_resolve.add_argument("--ability", help="override request.ability")
    ab_resolve.add_argument("--device", help="override request.device_ref")
    ab_resolve.add_argument("--runtime", help="override request.runtime")
    ab_resolve.set_defaults(func=cmd_ability_resolve)

    memory = sub.add_parser(
        "memory", help="Layer B the ledger, Layer A the projection (§20–§28)",
        **_COMMON)
    memory_sub = memory.add_subparsers(dest="subcommand", required=True)

    def _ledger_args(parser):
        # The same three flags on all four subcommands, because they all read
        # the same ledger and a reader that had to be told the path twice would
        # eventually be told two paths.
        parser.add_argument("--project", default="serveros",
                            help="the project the ledger belongs to (default: serveros)")
        parser.add_argument("--ledger-dir",
                            help="override the resolved ledger directory")

    mem_audit = memory_sub.add_parser(
        "audit", help="the whole-ledger check: invalid records, cycles, dangling "
                      "supersedes (§26)", **_COMMON)
    _ledger_args(mem_audit)
    mem_audit.set_defaults(func=cmd_memory_audit)

    mem_project = memory_sub.add_parser(
        "project", help="build the Layer A projection from the ledger (§27)",
        **_COMMON)
    _ledger_args(mem_project)
    mem_project.add_argument("--out", help="override the projection directory")
    mem_project.add_argument("--ledger-ref",
                             help="the ref a reader uses to find the ledger")
    mem_project.add_argument("--generated-at",
                             help="ISO-8601 UTC. Defaults to now; fixed so that "
                                  "rebuilding the same ledger is byte-identical")
    mem_project.set_defaults(func=cmd_memory_project)

    mem_status = memory_sub.add_parser(
        "status", help="CURRENT, STALE or MISSING, and against what (§27)",
        **_COMMON)
    _ledger_args(mem_status)
    mem_status.add_argument("--projection-dir",
                            help="override the resolved projection directory")
    mem_status.set_defaults(func=cmd_memory_status)

    mem_subject = memory_sub.add_parser(
        "subject", help="what this work currently remembers about one subject",
        **_COMMON)
    _ledger_args(mem_subject)
    mem_subject.add_argument("subject", help="e.g. project/serveros/SRV-0007")
    mem_subject.add_argument(
        "--as", dest="reader", required=True,
        help="who is reading: `operator` (a person holding the ledger), or the "
             "Item ref a thread is bound to (e.g. SRV-0007, or "
             "project/serveros/SRV-0007). Required: §18 refuses a cross-Item "
             "recall, so a read that does not say who is asking is a read the "
             "firewall cannot reach")
    mem_subject.set_defaults(func=cmd_memory_subject)

    probe = sub.add_parser("probe", help="run one resource's declared probes", **_COMMON)
    probe.add_argument("id")
    probe.set_defaults(func=cmd_probe)

    coordination = sub.add_parser(
        "coordination", help="§17 · what a thread is for, and whether it admits "
                             "work", **_COMMON)
    coordination_sub = coordination.add_subparsers(dest="subcommand", required=True)

    co_resolve = coordination_sub.add_parser(
        "resolve", help="a thread's role, its Item, and whether the Item still "
                        "admits work", **_COMMON)
    co_resolve.add_argument("session_ref")
    co_resolve.set_defaults(func=cmd_coordination_resolve)

    co_bind = coordination_sub.add_parser(
        "bind", help="record what a thread is for. Refused for a session that "
                     "already has a different binding", **_COMMON)
    co_bind.add_argument("session_ref")
    co_bind.add_argument("--role", required=True, choices=["coordinator", "execution"],
                         help="execution requires --item; coordinator forbids it")
    co_bind.add_argument("--item", help="the Item this thread is for")
    co_bind.add_argument("--work-item",
                         help="the Item this call also names. Accepted so that "
                              "contradicting yourself inside one call is refused "
                              "here rather than at the first turn")
    co_bind.set_defaults(func=cmd_coordination_bind)

    co_close = coordination_sub.add_parser(
        "close", help="close a thread for new work. The record stays", **_COMMON)
    co_close.add_argument("session_ref")
    co_close.add_argument("--reason")
    co_close.set_defaults(func=cmd_coordination_close)

    co_list = coordination_sub.add_parser(
        "list", help="how many threads this node holds, by role", **_COMMON)
    co_list.set_defaults(func=cmd_coordination_list)

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
    except DaemonUnavailable as exc:
        # Only `status` asks the daemon, and it catches this itself so that it
        # can emit JSON. Reaching here means a command that should not have
        # needed the daemon did — which is a defect, not a missing runtime.
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except BrokenPipeError:  # pragma: no cover - piping into `head`
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
