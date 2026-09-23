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
does not exist for anything above STOP this round.

They are also NOT DECLARED, and that is the choice rather than an oversight:
`tests/test_serverosctl.py::test_no_subcommand_mutates_anything` requires both
names to be absent from the parser, on the argument that "a command that is
absent is a clearer fact than one that exists and refuses". Measured 2026-09-22
while acting on this paragraph: neither verb appeared anywhere in the parser
tree, and this paragraph said the opposite --
"A command that is not implemented says so and exits 2, rather than being
quietly absent" -- which was false about `register` and `reconcile` and true
about nothing else either, since no unimplemented verb is declared. The
sentence was removed rather than implemented, because implementing it means
overruling that test, and a gate that encodes a safety property is not
weakened to make a sentence in a docstring come true.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import socket
import sys
import textwrap
import time
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
# SYS-02. The kernel and its durable producer, under the same synthetic names
# `runtime/daemon.py` uses — a caller that asks the CLI and a caller that asks
# the socket must be answered by one module, not two objects that agree today.
environment_mod = _load("serveros_runtime_environment", "environment.py")
lease_store_mod = _load("serveros_runtime_lease_store", "lease_store.py")

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


def _shm_line(shm) -> str:
    """The shm column, printed as the two facts it is.

    Both numbers appear because neither is "the" shared-memory number: the
    /dev/shm mount is what a container runs into, and `Shmem` is what tmpfs
    holds across every mount. On this host they read 24K and 258 MiB at the
    same instant, and a line showing only one of them would be read as the
    other.

    An unmeasurable /dev/shm prints its reason. It does not print a zero — an
    absent /dev/shm and an empty one are different, and only one of them is a
    container that will fail on its first write.
    """
    if not shm:
        return "shm         UNKNOWN (no shm measurement was taken)"
    if shm.get("state") != "PRESENT":
        return f"shm         {shm.get('state')} ({shm.get('reason', 'no reason given')})"
    mount = shm["mount"]
    shmem = shm.get("shmem_bytes")
    return (f"shm         /dev/shm {human_bytes(mount['used_bytes'])} used of "
            f"{human_bytes(mount['total_bytes'])} · tmpfs Shmem "
            + (human_bytes(shmem) if shmem is not None
               else "UNKNOWN (meminfo did not report Shmem)"))


def cmd_report_daily(args) -> int:
    """SYS-23 — one period's real work diff plus Quay's own records.

    Three things this command refuses to decide for the caller, because
    deciding them is how a daily report stops being true:

      * whether the work happened — it reads `git` and Quay's gate ledger and
        reports what those said. A period with nothing in it is written down as
        nothing, not filled in;
      * whether the report was delivered — `--publication-state` is the
        caller's fact and defaults to UNKNOWN. Writing the file is not
        delivering it, and the receipt says so;
      * where the report goes — `--out-dir` is required and must be absolute
        and outside this repository. A report written into the revision is
        replaced wholesale by the next one while looking exactly like a report
        that is kept.

    What it will not read at all is any input outside the contract's accepted
    kinds. That boundary is a whitelist rather than a list of forbidden names:
    a name-based rule only stops the names somebody thought of, and the thing
    that must not enter a work report is whatever has no checkable relation to
    the period's work — which includes names nobody has invented yet.
    """
    mod = _load_module_from(REPO_ROOT / "runtime" / "daily_report.py",
                            "serveros_daily_report")
    out_dir = Path(args.out_dir)
    try:
        window = mod.period_window({"start": args.start, "end": args.end})
        fallbacks = mod.Fallbacks()
        work = mod.measure_worktree(Path(args.worktree), window, fallbacks)
        if args.run_dir:
            quay = mod.measure_quay(Path(args.run_dir), window, fallbacks)
        else:
            # Not a measurement of "no gates this period". Quay's ledger lives
            # in Quay's own workspace and is not in this repository, so a
            # default pointing at one round's scratch directory would read a
            # store that happens to still be on this disk and report it as this
            # period's execution record. Absent input, absent answer.
            quay = {"root": None, "ledger": None, "tasks_dir": None,
                    "answered": False, "gates": [], "tasks": [], "problems": [],
                    "notes": [], "out_of_window": 0, "unknown_verdicts": [],
                    "why": ("没有给 --run-dir，这一次没有读 Quay 的账本。"
                            "这是缺口，不是「本周期没有闸门记录」")}
        document = mod.compose(period={"start": args.start, "end": args.end},
                               work_delta=work, quay=quay, focus=args.focus,
                               generated_at=args.generated_at,
                               fallbacks=fallbacks)
        text = mod.render(document, mod.skeleton())
        receipt_payload = mod.receipt(
            document, text, out_dir=out_dir, recipient=args.recipient,
            channel=args.channel, state=args.publication_state,
            attempted_at=args.attempted_at)
        written = mod.publish(document, text, receipt_payload, out_dir)
    except mod.Refused as exc:
        # A refusal here is a refusal to produce a document that would look
        # like a report while being about something else. Exit 1 with the
        # reason, and nothing half-written on disk: every refusal the module
        # raises fires before the two writes, so there is no partial pair.
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = [
        f"period      {document['period']['start']} → {document['period']['end']}",
        f"progress    {document['progress']} — {document['progress_because']}",
        f"work        {len(work['commits'])} commit(s)"
        + (f", {len(work['uncommitted'])} uncommitted path(s)"
           if work["uncommitted"] else "")
        + ("" if work["answered"] else f"  · GAP: {work['why']}"),
        f"quay        {len(quay['gates'])} gate record(s)"
        + (f", {quay['out_of_window']} outside the window"
           if quay["out_of_window"] else "")
        + ("" if quay["answered"] else f"  · GAP: {quay['why']}"),
        f"blocked     {len(document['blocking'])} named"
        + (f", {len(document['claimed_done_without_a_pass'])} claimed done "
           f"with no passing gate" if document["claimed_done_without_a_pass"]
           else ""),
        f"document    {written['document']}",
        f"receipt     {written['receipt']}",
        f"published   {receipt_payload['publication']['state']} — "
        f"{receipt_payload['publication']['why']}",
    ]
    if document["fallbacks"]:
        # Reported, not swallowed. A fallback reads exactly like a fact in the
        # body of the document, so the count has to appear next to the document
        # itself or nobody will ever look for it.
        lines.append(f"fallbacks   {len(document['fallbacks'])} substituted")
    emit(document, as_json=args.json, lines=lines)
    return EXIT_OK


def _recipes_module():
    return _load_module_from(REPO_ROOT / "runtime" / "recipes.py",
                             "serveros_runtime_recipes")


def _support_module():
    return _load_module_from(REPO_ROOT / "intake" / "support.py",
                             "serveros_intake_support")


def _support_handoff_module():
    return _load_module_from(REPO_ROOT / "intake" / "support_handoff.py",
                             "serveros_intake_support_handoff")


class _AssertedQueue:
    """A receipt the CALLER already holds, and nothing more than that.

    `support handoff` has no queue client: `serveros-work-mcp` is registered in
    no config on this node and `workengine` is not on PATH, which
    `fleet/mcp-contract.yaml:362-366` already says out loud. So the honest thing
    a command line can offer is for an operator who HAS a receipt to hand the
    reference over. This adapter hands back what it was given and says so in
    `provenance`, so a reference that came off a command line cannot pass for
    one that came out of a call this tool made.
    """

    provenance = ("asserted on the command line by the caller; this tool has no "
                  "queue client and called no queue")

    def __init__(self, reference: str):
        self.reference = reference

    def accept(self, packet):
        return self.reference


def _support_call(args, build):
    """One support verb: build the reading, print it, return its exit code.

    The four verbs share this because they share a failure mode worth having in
    one place -- the module reads a knowledge base in a PROTECTED repository,
    and every path out of it is either a named reading or a `Refused`. There is
    no path that raises into a traceback a person would read as "the tool is
    broken".
    """
    mod = _support_module()
    try:
        payload, outcome = build(mod)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    # `.splitlines()`, because `emit` iterates `lines`: handing it one string
    # prints the answer one character per line, which is what the first version
    # of this did.
    emit(payload, as_json=args.json,
         lines=mod.render(args.support_verb, payload).splitlines())
    return mod.exit_code_for(outcome)


def cmd_support_entries(args) -> int:
    """Every formal entry, and what the glob read and did not read.

    Exists so that "the knowledge base has 21 entries" is a measurement rather
    than a belief. It is also the reading that shows what the exclusions cost:
    `99_待整理样本` and the analysis file are listed as read-and-not-entries,
    which is a different fact from a root whose glob matched nothing.
    """
    def build(mod):
        payload = mod.listing(args.kb_root)
        # Two facts, one outcome. `unreadable` is "the glob matched nothing",
        # `unrecognised` is "files were read and no entry came out of any of
        # them"; both mean the listing above is not a count of entries, so
        # neither may be served with the exit code that says it is.
        outcome = ("KB_UNREADABLE" if payload["unreadable"] or payload["unrecognised"]
                   else "ANSWERED")
        return payload, outcome
    return _support_call(args, build)


def cmd_support_match(args) -> int:
    """Which entry a question reaches, and by which of the KB's own rules.

    The outcome is the answer, not an implementation detail: `ANSWERED` and
    `ANSWERED_FROM_AUXILIARY` are different words for a reason, and
    `NEEDS_MORE_INPUT` exists so that a question missing one field gets that
    field named rather than a knowledge dump.
    """
    def build(mod):
        payload = mod.match(args.kb_root, status_code=args.status_code,
                            message=args.message, code_root=args.code_root)
        return payload, payload["outcome"]
    return _support_call(args, build)


def cmd_support_answer(args) -> int:
    """A reply a person can send, and the improvement it implies.

    The two halves are one invocation deliberately. A chain that answered and
    left the follow-up to somebody's memory would produce the answer and lose
    the improvement, which is how a knowledge base stops growing. Nothing is
    written: the candidate is a record the caller may act on.
    """
    def build(mod):
        payload = mod.answer(args.kb_root, status_code=args.status_code,
                             message=args.message, code_root=args.code_root)
        return payload, payload["outcome"]
    return _support_call(args, build)


def cmd_support_check(args) -> int:
    """The index against the entries.

    The exit code says only whether the check could run. A check that exited
    non-zero on findings would make "the index has drifted" and "the knowledge
    base could not be read" the same status, and those are the two readings
    this whole product exists to keep apart.
    """
    def build(mod):
        payload = mod.check(args.kb_root, as_of=args.as_of, code_root=args.code_root)
        outcome = payload["outcome"]
        # `CHECKED` is the check having run. Its FINDINGS are not a failure of
        # the check, so a check with 8 findings and a check with 0 exit the
        # same. Only "could not read it" is a non-zero, and it says so.
        return payload, ("ANSWERED" if outcome == "CHECKED" else outcome)
    return _support_call(args, build)


def cmd_support_handoff(args) -> int:
    """§10's fallback: the packet, and the two receipts before 「已通知」.

    The two halves are one invocation for the same reason `answer` is:
    assembling the packet and routing it are one act, and a chain that
    assembled one without the other is how `support.answer`'s improvement
    candidate came to be printed and dropped for as long as it was.

    This verb SENDS NOTHING. It reads the knowledge base through
    `intake/support.py`, builds the packet, and records what each anchor
    answered. The delivery is reported by whoever walked the delivery path —
    the one delivery path is `runtime/notifications.py`, whose event
    vocabulary is a closed set with no word for this. So the reading is
    `PENDING_DELIVERY` unless an operator supplies a receipt they already hold,
    and that is the honest reading of this node today rather than a
    shortcoming of the verb.
    """
    support = _support_module()
    module = _support_handoff_module()
    try:
        reading = support.answer(args.kb_root, status_code=args.status_code,
                                 message=args.message, code_root=args.code_root)
        payload = module.run(
            reading, trigger=args.trigger, conversation=args.conversation,
            queue=(_AssertedQueue(args.queue_receipt) if args.queue_receipt else None),
            owner=args.owner,
            delivery=({"state": args.delivery_state, "at": args.delivery_at}
                      if args.delivery_state else None))
    except support.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    except module.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(payload, as_json=args.json, lines=module.render(payload).splitlines())
    return module.exit_code_for(payload)


def cmd_recipe_list(args) -> int:
    """§7's three loops, as the contract states them.

    Reads the contract and stops. It does not measure this node, and that is
    deliberate: this is the verb a person runs BEFORE anything has ever run,
    so a version of it that needed a state root would be unusable at exactly
    the moment it is worth something.

    The stops are printed with their exit code, because the contract's own
    distinction is that some of them mean "the loop ran and stopped for a
    reason it declared" (0) and the rest mean "the loop could not tell" (2).
    A listing that showed only the words would leave that out, and it is the
    part a cron log renders.
    """
    mod = _recipes_module()
    try:
        document = mod.listing()
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = [f"key         {document['key_formula']} — {document['period_id']}"]
    for scope in document["scopes"]:
        lines.append(f"scope       {scope['id']:<8} {scope['reads']}")
    transports = document["model"]["transports"]
    lines.append("model       "
                 + (f"transports {transports}"
                    if transports else
                    "no transport is bound on this node, so every step that "
                    "needs a model stops MODEL_UNBOUND and sends nothing"))
    for entry in document["recipes"]:
        lines.append("")
        lines.append(f"{entry['id']} — {entry['loop']}  ({entry['name']})")
        lines.append(f"  trigger   {entry['trigger']}")
        lines.append(f"  action    {entry['action']}")
        for step in entry["steps"]:
            mark = "model" if step["needs_model"] else "     "
            lines.append(f"  step      [{mark}] {step['id']}: {step['does']}")
        for stop in entry["stops"]:
            lines.append(f"  stop      {stop['exit_code']}  {stop['reason']} — "
                         f"{stop['means']}")
    emit(document, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_recipe_run(args) -> int:
    """One loop, one scope, one period -- and the record of what happened.

    EXIT CODES, and they are the contract's, not rearranged here:

      0  the loop ran and stopped for a reason it declared in advance. That
         includes every reason that means it did nothing: NO_CHANGE and
         ALREADY_DONE are answers, and a scheduler that saw them as failures
         would be told to retry work that is already correct.
      2  the loop could not tell. An unbound schedule, an unauthored source
         list, a claim with no outcome, a step warranted with no transport.
         `recipe status` names what it was.
      1  a fault, or a refusal of the arguments themselves. A caller's typo
         must not read as a result.

    A run that cannot even be given a key -- no period, or a period from a
    schedule this instance has not bound -- writes NO record, because there is
    no key to write it under. That is why the refusals below happen before the
    engine exists: a record written for a period nobody chose would be read
    later as though somebody had.
    """
    mod = _recipes_module()
    if args.recipe == "report" and not args.out_dir:
        # Checked here rather than left to the loop, so that a missing --out-dir
        # writes NO record: a key claimed for a run that could never have
        # produced a document would sit there as IN_FLIGHT_OR_ABANDONED and
        # read as a crash. An argument nobody supplied is not a run.
        sys.stderr.write(
            "serverosctl: `recipe run --recipe report` writes a document and a "
            "receipt, so --out-dir is required, absolute, and outside this "
            "repository\n")
        return EXIT_FAULT
    try:
        state_root = mod.state_root_for(args.state_root)
        period = mod.period_from_args(args, state_root)
        budget, budget_from = mod.budget_from_args(args, state_root)
        transport = mod.transport_for(state_root)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    except mod.Unbound as exc:
        # NO_SCHEDULE_BOUND. The reason is in the vocabulary, so its exit code
        # is the contract's; `exit_code_for` refuses a word that is not.
        sys.stderr.write(f"serverosctl: {exc.reason}: {exc.why}\n")
        return mod.exit_code_for(exc.reason)

    revision, revision_why = mod.checkout_revision(args.worktree)
    try:
        engine = mod.Engine(
            state_root=state_root, scope=args.scope, period=period,
            model=mod.default_model_invoker, transport=transport,
            records_root=args.records_root, workspace_root=args.workspace_root,
            worktree=args.worktree, run_dir=args.run_dir, out_dir=args.out_dir,
            recipient=args.recipient, channel=args.channel,
            publication_state=args.publication_state,
            work_engine=args.work_engine)
        result = engine.run(recipe_id=args.recipe, budget=budget,
                            budget_from=budget_from,
                            retake=args.retake, revision=revision)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    ceiling = result["budget"]
    lines = [
        f"recipe      {result['recipe']}",
        f"key         {result.get('key')}",
        f"period      {period['start']} → {period['end']}  ({period['source']})",
        f"record      {result['path']}",
        f"state       {result['state']}  previous {result.get('previous_state')}",
        f"stopped     {result.get('stopped_because')} — "
        f"{result.get('stopped_why')}",
        # `given: None` is not `given: 0`. A run that stopped before any step
        # had no ceiling of its own, and printing "a ceiling of 0" would report
        # a ceiling somebody agreed to where nobody agreed to one.
        ("model       no step ran against a ceiling "
         f"(the number would have come from {ceiling['from']})"
         if ceiling.get("given") is None else
         f"model       {result['model_calls']} call(s) of a ceiling of "
         f"{ceiling['given']} from {ceiling['from']}"),
        f"revision    {revision or '—'}"
        + ("" if revision else f"  · GAP: {revision_why}"),
    ]
    for entry in result["steps"]:
        lines.append(f"step        {entry['step']}: {entry['did']}")
    if result.get("would_call"):
        # Reported on its own line rather than folded into the stop, because it
        # is the one thing a person needs when a loop stops for a missing
        # transport: what it was about to ask.
        lines.append(f"would call  {result['would_call']['step']}: "
                     f"{result['would_call']['why']}")
    for note in result["notes"]:
        lines.append(f"note        {note}")
    emit(result, as_json=args.json, lines=lines)
    return result["exit_code"]


def cmd_recipe_status(args) -> int:
    """What every loop recorded for one key, or the absence of a record.

    The absent reading is the whole point and it is why an empty answer exits
    2 rather than 0: "no loop has been run for this scope and period" is not
    "this period had nothing in it", and a status verb that rendered the first
    as the second would be the one place a person looks to tell them apart.
    """
    mod = _recipes_module()
    try:
        state_root = mod.state_root_for(args.state_root)
        period = mod.period_from_args(args, state_root)
        document = mod.status(state_root=state_root, scope_ref=args.scope,
                              period=period)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    except mod.Unbound as exc:
        sys.stderr.write(f"serverosctl: {exc.reason}: {exc.why}\n")
        return mod.exit_code_for(exc.reason)

    lines = [f"scope       {document['scope']}",
             f"period      {period['start']} → {period['end']}  "
             f"({period['source']})"]
    for recipe_id, entry in document["recipes"].items():
        lines.append(f"{recipe_id:<11} {entry['state']}"
                     + (f" — {entry['stopped_because']}: "
                        f"{entry.get('stopped_why')}"
                        if entry.get("stopped_because") else "")
                     + (f"  · {entry['means']}" if entry.get("means") else ""))
        lines.append(f"{'':<11} {entry['path']}")
    summary = document["summary"]
    lines.append("summary     "
                 + ", ".join(f"{name} {count}" for name, count in summary.items()))
    emit(document, as_json=args.json, lines=lines)
    return document["exit_code"]


def cmd_document_resolve(args) -> int:
    """SYS-10 — one document's identity, its authoritative revision, and
    whether the copy in hand is a derivation of it.

    This is the product entry point for `documents/document_governance.py`.
    Before it existed the kernel had exactly one way to be run — `python
    documents/document_governance.py --request …` from a source checkout — and
    that is the same as having none: a module nothing loads ships in no
    artifact, so on an installed node the verb was not missing, it was
    unnameable.

    Exit codes are the kernel's own, not rearranged here:

      0  a decision was reached, whatever it was. `NOT_DECLARED` and
         `SOURCE_UNAVAILABLE` are answers — the first is a finding about the
         document, the second is a gap in the asking — and a verb that exits
         non-zero on them makes a legitimate reading look like a failure.
         The two are told apart by `is_finding` / `is_gap` in the record and by
         the two words on the line, not by the status.
      2  the request itself is malformed or unreadable. A caller's typo must
         not look like an answer.

    `document_governance.main()` names a `1` for "判定失败" in a comment and
    no path in `answer()` produces it. This verb does not invent one: a status
    that nothing can be observed emitting is worse than no status, because it
    reads as a check.
    """
    mod = _load_module_from(REPO_ROOT / "documents" / "document_governance.py",
                            "serveros_document_governance")
    try:
        vocabulary = mod.load_vocabulary(args.vocabulary)
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        out = mod.answer(request, vocabulary)
    except mod.Rejected as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except (OSError, json.JSONDecodeError) as exc:
        # An unreadable request is not a request that named a document nobody
        # declared. One is a gap in the asking, the other is a finding.
        sys.stderr.write(f"serverosctl: cannot read {args.request}: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    record = out["record"]
    kind = ("GAP" if record["is_gap"]
            else "FINDING" if record["is_finding"]
            else "ANSWER")
    lines = [
        f"ref         {record['ref']}",
        f"decision    {record['decision']}  ({kind})",
        f"source       {record['authority']['source_ref']} — {record['source_state']}",
        f"authority   {'this is it' if record['is_the_authority'] else 'not it'}"
        + (f"  ·  copy of {record['copied_from']}" if record["copied_from"] else ""),
        f"citation    {out['citation']}",
    ]
    if record["owner"]:
        lines.append(f"owner       {record['owner']}"
                     + (f"  ·  item {record['item']}" if record["item"] else ""))
    if record["fallbacks"]:
        # Reported, not swallowed: `source:unstated` is a constant standing in
        # for a source that signed nothing, and a record that uses it without
        # saying so attributes a conclusion to nobody.
        lines.append(f"fallbacks   {len(record['fallbacks'])} substituted: "
                     + ", ".join(f["field"] for f in record["fallbacks"]))
    if "chain" in out:
        chain = out["chain"]
        lines.append(f"chain       {' -> '.join(chain['chain'])}"
                     f"  ::  {chain['terminus']}"
                     f"  ({'canonical root' if chain['root_is_canonical'] else 'no canonical root'})")
    emit(out, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_information_captured(args) -> int:
    """§B1/P1 — what the collectors were asked for, and what actually landed.

    The materialized half of P1's "capture policy 与 materialized coverage 分开".
    `work coverage` already reports the feishu surfaces, but it counts the
    *authored* `sources.yaml` — what somebody declared they follow. This reads
    the 66 manifests the collectors wrote under `records/information/raw/`, which
    until now had no ServerOS-side reader at all.

    What it refuses to hand back as one number:

      * a per-object verdict. One chat manifest can be fully paginated and still
        have three attachments declared pending. The components are reported
        separately with the facts each was decided from, and the rollup counts
        per component.
      * a default policy. 37 of the 47 chat manifests carry no `sync.mode` — the
        collector never recorded which mode it ran — and that is printed as
        undeclared rather than as the mode anybody would assume.
      * freshness. `last_verified_at` is printed as written. Whether it is old
        is a staleness question with its own threshold, and answering it here
        would make this verb the authority on a fact it does not hold.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"capture tree read relative to whatever directory the caller stood "
            f"in is a coverage number nobody else can reproduce.\n")
        return EXIT_FAULT
    mod = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                            "serveros_intake_capture_coverage")
    try:
        report = mod.read_root(records)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    summary = report["summary"]
    lines = [
        f"root        {report['root']}",
        f"manifest    {summary['denominator']} 份"
        + ("  ·  " + " · ".join(f"{name} {count}"
                                for name, count in sorted(summary["by_shape"].items()))
           if summary["by_shape"] else ""),
        f"role        " + " · ".join(f"{name} {count}"
                                     for name, count in sorted(summary["by_role"].items())),
        "",
        "coverage    按分量计——一个对象的「完整」不是一句话",
    ]
    # Component order is the collector's own order of work, not alphabetical:
    # messages/body are what the capture was for, and a reader comparing two
    # runs should not have the rows move because a component was added.
    order = ("messages", "body", "attachments", "links", "media")
    for name in order:
        bucket = summary["components"].get(name)
        if not bucket:
            continue
        parts = " · ".join(f"{state} {count}" for state, count in sorted(bucket.items()))
        lines.append(f"  {name:<12} {parts}")
    if summary["components"]:
        missing = [n for n in order if n not in summary["components"]]
        if missing:
            lines.append(f"  未出现       {'、'.join(missing)}"
                         f" —— 本树下没有带这个块的 manifest，不是它们都通过了")
    verified = summary["verified"]
    lines += [
        "",
        f"复核        RECORDED {verified['RECORDED']} · "
        f"NOT_RECORDED {verified['NOT_RECORDED']}"
        + ("   —— NOT_RECORDED 的 manifests 只有采集者对自己工作的自述，"
           "没有任何独立重核记录" if verified["NOT_RECORDED"] else ""),
    ]
    if summary["capture_undeclared"]:
        lines.append("未声明采集策略  " + " · ".join(
            f"{field} 缺于 {count} 份" for field, count in
            sorted(summary["capture_undeclared"].items()))
            + " —— 采集器没记，不补默认值")
    rows = [o for o in report["objects"] if o.get("coverage")]
    flagged = [
        (o, component) for o in rows
        for component in o["coverage"]["components"].values()
        if component["state"] != "COMPLETE"
    ]
    lines += ["", f"需要看的     {len(flagged)} 个分量，分布在 "
                  f"{len({o['path'] for o, _ in flagged})} 个对象上"]
    for obj, component in flagged:
        lines.append(f"  {component['state']:<11} {obj['path']}"
                     + (f"  [{obj['role']}]" if obj["role"] not in ("chat", "link") else ""))
        for reason in component["why"]:
            lines.append(f"      {reason}")
    for obj in report["objects"]:
        if obj.get("coverage") is None:
            lines.append(f"  {obj['shape']:<11} {obj.get('path', '?')}")
            lines.append(f"      {obj.get('why', '')}")
    emit(report, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_information_watched(args) -> int:
    """§P2 — the follow list against the capture tree: the join neither side had.

    `information captured` reads the 66 manifests the collectors wrote. This
    reads the list that says which documents are *supposed* to be re-checked —
    `records/information/raw/DRIFT_WATCHLIST.txt` — and asks of each entry
    whether the capture tree is actually keeping up. On 2026-09-22
    `grep -rn "DRIFT_WATCHLIST\\|drift-sync-state"` over the fabric tree
    returned nothing: `drift_sync.py:71 load_watchlist()` reads its own list and
    no ServerOS-side reader had ever opened it.

    What it refuses to hand back as one number:

      * a freshness verdict. "Overdue" is a threshold, and a threshold is a
        policy somebody authors. `--before YYYY-MM-DD` is that input; without it
        the report says the question was not asked. This verb reads no clock.
      * the debounce state as a verification record. `.drift-sync-state.json` is
        the collector's commit debounce — `drift_sync.py:246` resets it
        "无论 commit 是否成功" — so it says when this link last tried to commit.
        The live tree holds both: the state says `2026-09-15T00:05:53` and the
        manifest says `last_verified_at: 2026-09-16`. Two facts about two
        things, and subtracting them would invent a contradiction.
      * the collector's builtin fallback as an authored list. When the file is
        missing, `load_watchlist()` returns `["wiki-litellm-work-doc"]`; that is
        reported as the collector's default, because "one document is followed"
        and "the follow list is gone" are different facts.
      * a resolution of a conflict. Two captures under one link_id make
        `drift_sync.locate()` return None, and it logs and skips — the watch is
        silently dead. That is the class this verb exists to surface.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"follow list read relative to whatever directory the caller stood "
            f"in is a coverage number nobody else can reproduce.\n")
        return EXIT_FAULT
    mod = _load_module_from(REPO_ROOT / "intake" / "watch_coverage.py",
                            "serveros_intake_watch_coverage")
    try:
        report = mod.read_root(records, watchlist=args.watchlist or "",
                               before=args.before or "")
    except (mod.Refused, mod.Malformed) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    listing = report["watchlist"]
    summary = report["summary"]
    lines = [
        f"root        {report['root']}",
        f"watchlist   {listing['path']}",
        f"来源        {listing['listed']}  ·  {summary['denominator']} 条"
        + ("  ·  malformed " + str(summary["malformed"]) + " 条（没有丢弃，见下）"
           if summary["malformed"] else ""),
    ]
    if listing["why"]:
        lines.append(f"      {listing['why']}")
    lines += [
        "",
        "解析        一个 link_id 一条——«盯住了» 不是一句话",
    ]
    for name in mod.WATCH_STATES:
        if summary["by_state"].get(name):
            lines.append(f"  {name:<12} {summary['by_state'][name]}")
    lines.append(f"  {'复核记录':<12} " + " · ".join(
        f"{k} {v}" for k, v in sorted(summary["by_verified"].items())))
    lines.append(f"  {'内容漂移':<12} " + " · ".join(
        f"{k} {v}" for k, v in sorted(summary["by_drift"].items())))
    lines.append(f"  {'提交去抖':<12} " + " · ".join(
        f"{k} {v}" for k, v in sorted(summary["by_debounce"].items())))
    if summary["overdue_asked"]:
        lines.append(f"  {'逾期':<12} " + " · ".join(
            f"{k} {v}" for k, v in sorted(summary["by_overdue"].items()))
            + f"   —— 对 {report['cutoff']} 而言")
    else:
        lines.append(f"  {'逾期':<12} 未问——没有给 --before，这个 verb 不读时钟")
    lines += ["", f"逐条        {summary['denominator']} 条"]
    for watch in report["watches"]:
        lines.append(f"  {watch['state']:<11} {watch['link_id']}"
                     + (f"  [{watch['listed']}]" if watch["listed"] != "AUTHORED" else ""))
        if watch["state"] == "RESOLVED":
            lines.append(f"      {watch['title'] or '(无标题)'}"
                         f"  {watch['type']}  {watch['url']}")
            lines.append(f"      复核 {watch['verified']['state']}"
                         f" {watch['verified']['at'] or ''}"
                         f"  ·  漂移 {watch['drift']['state']}"
                         f"  ·  去抖 {watch['debounce']['state']}"
                         f" {watch['debounce']['at'] or ''}")
            if watch["overdue"]["asked"]:
                lines.append(f"      逾期 {watch['overdue']['state']}"
                             f"  —— {watch['overdue']['why']}")
        else:
            lines.append(f"      {watch['why']}")
        for path in watch["captures"]:
            lines.append(f"      → {path}")
    if report["state_without_watch"]:
        lines += ["", f"去抖表里没有被盯的     {len(report['state_without_watch'])} 条"
                      "——以前盯过、现在不在表里，条目是残留"]
        for link_id in report["state_without_watch"]:
            lines.append(f"  {link_id}")
    if listing["malformed"]:
        lines += ["", f"不是 link_id 的行   {len(listing['malformed'])} 条"
                      "——采集器跳过空行与 # 注释、其余照收，所以这些它也会拿去查"]
        for line in listing["malformed"]:
            lines.append(f"  {line}")
    emit(report, as_json=args.json, lines=lines)
    return EXIT_OK


def _space_request(args, *, attribute: str = "request") -> dict:
    """Read one request document, or refuse with the reason it could not be read.

    Shared by the two `space` verbs because they answer the same shape of
    question — a request document in, a decision record out — and because the
    refusal has to read the same in both: an unreadable request is a gap in the
    asking, and it must not be told apart from a decision by anything except
    the words on the line.

    `attribute` names which of the caller's paths is being read. Both are JSON
    documents and both are read the same way, so the message names the file that
    was actually opened rather than the one this function was first written for.
    """
    path = getattr(args, attribute)
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _SpaceRequestUnreadable(f"cannot read {path}: {exc}") from exc


class _SpaceRequestUnreadable(Exception):
    """A request that could not be read. Not a decision about any object."""


def cmd_space_resolve(args) -> int:
    """`space/resolver.py` — where a logical object canonically belongs.

    This is the product entry point for the Space Resolver. Until it existed
    the kernel had exactly one way to be run, `python3 space/resolver.py
    --request …` from a source checkout, and the module ships in the artifact
    with nothing that names it — so on an installed node the verb was not
    missing, it was unnameable. The same sentence is true of
    `space/meeting_relevance.py`, and the two are wired together here because
    they are the two halves of one question: Space answers *where does this
    belong*, Meeting answers *what does this meeting contribute, and what may be
    done to it afterwards*.

    Exit codes are the kernel's own, and are not rearranged here:

      0  a decision was reached, whatever it was. `PROPOSAL_REQUIRED` — no rule
         covers this pair, or the policy declines to choose a canon, or a
         template needs context the request did not carry — is the most honest
         answer this kernel gives, and a verb that exits non-zero on it makes
         honesty look like failure.
      2  the request itself is unreadable, or the kernel raised `Rejected`
         because the request is malformed. A caller's typo must not look like
         an answer.

    `REJECTED` is named in the kernel's `DECISIONS` tuple and no path in
    `resolve()` can return it — it is emitted only by that module's `main()`
    except-branch. The value is therefore reported here the way the kernel
    actually produces it, as a refusal with a reason, rather than as a decision
    this verb might invent a status for. A status nothing can be observed
    emitting is worse than no status, because it reads as a check.

    Nothing here writes, moves or authorizes. `MOVE` is a *proposal*: the
    kernel's own vocabulary sets `approval.cross_surface` to `proposal-only`,
    on the grounds that on Feishu "add to wiki" has no automatic undo. The
    `approval` field is printed for every decision for exactly that reason.
    """
    mod = _load_module_from(REPO_ROOT / "space" / "resolver.py",
                            "serveros_space_resolver")
    try:
        vocabulary = mod.load_vocabulary(args.vocabulary)
        record = mod.decide(_space_request(args), vocabulary)
    except mod.Rejected as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except _SpaceRequestUnreadable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    target = record.get("logical_target") or {}
    where = " ".join(f"{slot}={target[slot]}"
                     for slot in ("project", "repo", "item", "run", "owner")
                     if target.get(slot))
    if target:
        placement = f"{target['surface']}/{record['canonical_role']}"
    else:
        # No rule, or the policy declined to choose, or the role is unclassified:
        # the kernel has no logical target to report, and saying so is the answer.
        # Printing a dash where a surface would go would read as a surface called
        # "—"; the decision line above already carries the surface when there is one.
        placement = f"(undecided)  ·  role={record['canonical_role']}"
    lines = [
        f"decision    {record['decision']}"
        + (f" -> {record['canonical_surface']}" if record.get("canonical_surface") else ""),
        f"placement   {placement}" + (f"  ({where})" if where else ""),
        f"confidence  {record['confidence']}",
        f"approval    {record['approval']}"
        + ("  ·  this is a proposal and not a permission: nothing here writes"
           if record["approval"] != "none-required" else ""),
    ]
    if record.get("index"):
        lines.append(f"index       {record['index']}")
    # A locator is printed as what it is: logical, and present only when the
    # caller's own policy carried a template. The kernel refuses to invent one,
    # so an absent locator here is a property of the policy, not a bug.
    lines.append(f"locator     {record.get('target_locator') or '(none — no template in the policy)'}")
    for index, reason in enumerate(record.get("reason") or []):
        lines.append(f"{'reason' if index == 0 else '':<11} {reason}")
    emit(record, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_space_meeting(args) -> int:
    """`space/meeting_relevance.py` — what a MeetingBundle contributes to work.

    Two questions with two input sets, selected by the request document's
    `kind`: `relevance` (metadata → policy match → bounded content → RELEVANT /
    MIXED / IRRELEVANT / UNKNOWN → an action) and `cleanup` (one of section 8's
    four levels, planned or refused). `--kind` overrides the document, because
    re-asking the same bundle the other question is a thing a person does and
    it should not require editing the file.

    Two ways to say what the bundle IS, and they are different kinds of input:

      * `--request` — somebody already knows how to put the question and hands
        over the `bundle` object directly. Unchanged, and still the only way to
        ask a cleanup question.
      * `--sidecars` — the meeting's own material, declared as what it has.
        `space/meeting_bundle.py` assembles the bundle from it and the kernel is
        asked about THAT. A declaration that is not one unit — missing a member
        the machine policy requires, or naming two meetings — puts no question
        to the kernel at all, and the reading says which member and what state it
        was in. The alternative is what P3 was before this: a kernel that can
        only be asked about bundles somebody hand-wrote.

    `--observed-at` is required with `--sidecars` and absent from the `--request`
    path, which carries its own `as_of`. Neither falls back to the clock: a
    reading whose answer depends on when it ran cannot be compared against a
    recorded one.

    Exit codes follow the same rule as `space resolve`, and the kernel states it
    in its own docstring: 0 for a decision — `REVIEW`, `REFUSAL` and
    `PROPOSAL_REQUIRED` included, because each is a successful determination —
    and 2 only for a request that is malformed or unreadable.

    Two properties of the record are load-bearing enough to be printed rather
    than left in the JSON:

      * a relevance record carries `cleanup: null`, and that None means *this
        kernel does not produce a cleanup level at all* — section 7 makes
        deletability an axis of its own, and a cleanup plan is asked for
        separately. It does not mean "nothing to clean up", and a reader who
        takes it that way has inverted the one field that keeps relevance from
        reaching a deletion;
      * a cleanup `PLAN` is a plan and not an execution. Eligibility is
        evaluated here; applying it belongs to the surface adapters, and each
        acting object carries its own intent and its own receipt.

    `--as-of` is deliberately not offered. The kernel evaluates expiry against
    an explicit `as_of` and fails closed when it cannot, so a flag would invite
    a value derived from the shell's clock — and a check whose answer depends on
    when it ran cannot be compared against a recorded one.
    """
    mod = _load_module_from(REPO_ROOT / "space" / "meeting_relevance.py",
                            "serveros_space_meeting_relevance")
    if args.sidecars and not args.observed_at:
        sys.stderr.write(
            "serverosctl: --sidecars needs --observed-at. The reading is a "
            "statement about a meeting at an instant, and one taken from the "
            "clock cannot be compared against a recorded one\n")
        return EXIT_COULD_NOT_ANSWER
    # Loaded up front when the sidecars path is taken, so the `except` below can
    # name that module's own refusal class. Loaded from the `#47`-style cache, so
    # this is the same module object the rest of this process sees.
    bundle_mod = (_load_module_from(REPO_ROOT / "space" / "meeting_bundle.py",
                                    "serveros_space_meeting_bundle")
                  if args.sidecars else None)
    reading = None
    try:
        vocabulary = mod.load_vocabulary(args.vocabulary)
        if args.sidecars:
            # The producer half: a meeting's declared sidecars assembled into the
            # bundle this kernel takes. `bundle` is None for every reading that is
            # not COMPLETE, and a question is not put in that case -- handing over
            # a bundle missing its audio would produce a decision record about
            # material that is not there.
            declaration = _space_request(args, attribute="sidecars")
            assembled = bundle_mod.assemble(declaration, observed_at=args.observed_at,
                                            vocabulary=vocabulary)
            reading = assembled["reading"]
            if assembled["bundle"] is None:
                emit({"reading": reading, "bundle": None},
                     as_json=args.json,
                     lines=bundle_mod.render(assembled).splitlines())
                return EXIT_COULD_NOT_ANSWER
            request = {"kind": args.kind or "relevance", "bundle": assembled["bundle"],
                       "policies": declaration.get("policies") or []}
            if declaration.get("as_of") is not None:
                request["as_of"] = declaration["as_of"]
        else:
            request = _space_request(args)
        if args.kind:
            # Re-asked, not mutated in place: a caller's document is theirs.
            request = dict(request)
            request["kind"] = args.kind
        record = mod.decide(request, vocabulary)
    except mod.Rejected as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except _SpaceRequestUnreadable as exc:
        sys.stderr.write(f"{exc}\n")
        return EXIT_COULD_NOT_ANSWER
    except (bundle_mod.Refused if bundle_mod else ()) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    cleanup = record.get("cleanup")
    lines = []
    if reading is not None:
        # The reading first, because it is what the rest of the answer is about:
        # a decision made about a bundle assembled from the wrong members is a
        # decision about another meeting, and a reader who sees only the decision
        # has no way to notice.
        bundle_mod = sys.modules["serveros_space_meeting_bundle"]
        lines.extend(bundle_mod.render({"reading": reading, "bundle": {}}).splitlines())
        lines.append("")
    if "level" in record:
        lines.append(f"level       {record['level']}")
    lines.append(f"decision    {record['decision']}"
                 + (f"  relevance={record['relevance']}" if record.get("relevance") else ""))
    if "level" in record:
        lines.append(f"deletability {record['axes']['deletability']}"
                     + ("  ·  a plan is not an execution: each acting object carries"
                        " its own intent and its own receipt"
                        if record["decision"] == "PLAN" else ""))
        lines.append(f"objects     {len(record['objects'])} planned"
                     + (f"  ({len(record['already_executed'])} already executed)"
                        if record.get("already_executed") else ""))
    else:
        stages = " ".join(f"{entry['stage']}={entry['state']}"
                          for entry in record.get("stages") or [])
        lines.append(f"stages      {stages}")
        axes = record.get("axes") or {}
        lines.append("axes        " + " ".join(f"{name}={axes[name]}" for name in sorted(axes)))
        lines.append(
            "cleanup     null — this kernel produces no cleanup level; deletability "
            "is its own axis and a plan is asked for separately"
            if cleanup is None else f"cleanup     {cleanup}")
    for index, reason in enumerate(record.get("reason") or []):
        lines.append(f"{'reason' if index == 0 else '':<11} {reason}")
    payload = record if reading is None else {"reading": reading, "bundle": request["bundle"],
                                              "decision": record}
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


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
        _shm_line(host.get("shm")),
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


def cmd_ability_tree(args) -> int:
    """§4's logical tree, as this node currently believes it.

    The daemon has derived this since v1.2's ability-tree round, and until this
    command the only way to see it was `serverosctl` speaking JSON onto the
    socket by hand — the same "implemented and unreachable" shape `cmd_ability_
    list` was written to fix for `ability.list`.

    Why it asks the daemon instead of reading `effective.json`: the daemon
    grades the projection (FRESH / STALE / UNREADABLE / NOT_MATERIALIZED) and
    returns `nodes: null` rather than `[]` when there is none. A CLI that read
    the file itself would print an empty tree for all four states, and "the
    catalogue has no classified abilities" is the opposite finding from
    "nobody has asked ability-management".
    """
    try:
        payload = ask_daemon("ability.tree")
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        if args.json:
            json.dump({"ok": False, "registry_status": "UNREADABLE", "detail": str(exc)},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return EXIT_COULD_NOT_ANSWER

    nodes = payload.get("nodes")
    lines = [f"authority   {payload.get('authority')}",
             f"projection  {payload.get('registry_status')}",
             f"basis       {payload.get('basis')}"]
    if nodes is None:
        lines.append("tree        none — no local capability projection is "
                     "materialized, so there is nothing to derive from")
        lines.append(f"            {payload.get('note', '')}")
        emit(payload, as_json=args.json, lines=lines)
        # NOT an error. §28: an unreachable authority is a supported answer —
        # the node says so and names the command that would produce it.
        return EXIT_OK

    placements = payload.get("placements") or {}
    for node in nodes:
        # `node` is a prefix such as `serveros.operations`; the label is the
        # last segment, because the indentation already carries the ancestry.
        depth = node.count(".") if isinstance(node, str) else 0
        here = sorted(name for name, at in placements.items() if at == node)
        # Two counts, not one. `serveros` is a node in the tree and it is also
        # every other node's prefix, so a single `(0)` beside it reads as "this
        # branch is empty" when it means "nothing is placed at exactly this
        # level". The first count is the placement rule; the second is what a
        # reader is actually asking.
        below = sum(1 for at in placements.values()
                    if isinstance(at, str) and at.startswith(node + "."))
        counts = f"({len(here)} here, {below} below)" if below else f"({len(here)})"
        lines.append(f"{'  ' * depth}{node.split('.')[-1]}  {counts}")
        for name in here:
            lines.append(f"{'  ' * (depth + 1)}{name}")
    lines.append(f"classified  {payload.get('classified_count')} of "
                 f"{payload.get('ability_count')}")
    unplaced = payload.get("unplaced") or []
    if unplaced:
        # Named, not hidden. An unplaced ability has no `domain.primary`, and
        # §4 refuses to invent one to make the tree look complete — so the
        # count is part of the answer rather than a footnote.
        lines.append(f"unplaced    {len(unplaced)} "
                     f"(no `domain.primary` — see `serverosctl ability inspect`)")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_ability_inspect(args) -> int:
    """One ability at L2: the projection's row, its place in the tree, and
    whether any operation vocabulary exists for it.

    Deliberately NOT a resolution. `installed` on this answer means "the local
    projection carries a row for it" — which is a claim about a file on this
    node, not about this node's ability to do the work. Whether this device and
    runtime can actually run it is `serverosctl ability resolve`, and the note
    on this answer says so, because the two questions print the same three
    columns and mean different things.
    """
    try:
        payload = ask_daemon("ability.inspect", {"name": args.name})
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        if args.json:
            json.dump({"ok": False, "registry_status": "UNREADABLE", "detail": str(exc)},
                      sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        return EXIT_COULD_NOT_ANSWER

    lines = [f"ability     {payload.get('name')}",
             f"projection  {payload.get('registry_status')}",
             f"installed   {payload.get('installed')}"]
    # The daemon's shape, read rather than assumed: the row is `ability`, the
    # tree answer is `tree.placement` / `tree.cross_refs` / `tree.unplaced`.
    # A flat reader over this answer printed three lines and looked like an
    # empty projection — the same wrong-reader failure `cmd_ability_status`
    # records in its own comment.
    row = payload.get("ability") or {}
    for key in ("id", "type", "path"):
        if row.get(key) is not None:
            lines.append(f"{key:<19} {row[key]}")
    domain = row.get("domain")
    if isinstance(domain, dict) and domain.get("primary"):
        lines.append(f"{'domain.primary':<19} {domain['primary']}")
    refs = row.get("taxonomy_refs")
    if refs:
        lines.append(f"{'taxonomy_refs':<19} {','.join(refs)}")
    tree = payload.get("tree")
    if tree:
        lines.append(f"{'tree_node':<19} {tree.get('placement') or '(unplaced)'}")
        if tree.get("cross_refs"):
            lines.append(f"{'cross_refs':<19} {','.join(tree['cross_refs'])}")
    lines.append(f"{'source':<19} {payload.get('source')}")
    lines.append(f"{'resolution':<19} {payload.get('resolution')} "
                 f"(by `{payload.get('resolved_by')}`)")
    lines.append(f"{'operations':<19} {payload.get('operations_declared')}")
    if payload.get("note"):
        lines.append(payload["note"])
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK if payload.get("installed") else EXIT_FAULT


def cmd_pack_inspect(args) -> int:
    """§11 · one pack, resolved against the CATALOGUE — not against this node.

    `pack.inspect` was named in §F as one of the four discovery surfaces and
    did not exist anywhere in the tree: `abilities/packs.py` had a complete
    `resolve()`, a pin-settling pass and its own `main()`, and no product
    surface called any of it. This is that call.

    WHY THE CATALOGUE IS NOT THE LOCAL PROJECTION — AND WAS, FOR ONE HOUR
    --------------------------------------------------------------------
    The first version of this command defaulted `--catalogue` to the abilities
    the daemon reports over IPC, on the reasoning that a node can only honestly
    resolve a pack against what it has observed. It ran, and it printed
    `UNAVAILABLE 0/5 resolved` — for all five packs — and `PIN_MISMATCH` for
    all five pins. Every line of that was wrong, and wrong in the shape this
    product keeps re-learning: **a verdict about one thing computed from
    another, with nothing in the output to say so.**

    A pack's closure is a statement about the canonical catalogue, which is the
    whole name set in `abilities-manifest.yaml`. The node's projection is a
    *device-filtered* set — the abilities this machine has observed installed —
    and it is 25 names against a manifest of 55. Feeding it to `resolve()` makes
    every pack read UNAVAILABLE, which reads as "ability-management cannot
    close its own packs" rather than as "this node is not the catalogue".

    So the catalogue is read the way `scripts/build.py` reads it and the way
    `serverosctl ability refresh` already reads the manifest beside it: from a
    git object at a named revision, in the ability-management checkout. The
    revision is required, because `resolve()` refuses without one and because a
    verdict about an unnamed catalogue cannot be reproduced.

    The node's own question — "can THIS device do the work in this pack" — is
    `serverosctl ability resolve`, which intersects the projection with the
    providers this device actually has. Two questions, two commands, and this
    one does not answer the other's.
    """
    packs_mod = _load_module_from(REPO_ROOT / "abilities" / "packs.py",
                                  "serveros_ability_packs")
    projection_mod = _load_module_from(REPO_ROOT / "runtime" / "ability_projection.py",
                                       "serveros_ability_projection")

    try:
        import yaml
    except ImportError:  # pragma: no cover - environment guarantee
        sys.stderr.write("serverosctl: PyYAML is required to read the pack contract\n")
        return EXIT_COULD_NOT_ANSWER

    contract = yaml.safe_load(args.contract.read_text(encoding="utf-8"))
    if not isinstance(contract, dict):
        sys.stderr.write(f"serverosctl: {args.contract} is not a mapping\n")
        return EXIT_FAULT
    declarations = yaml.safe_load(args.declarations.read_text(encoding="utf-8"))
    if not isinstance(declarations, dict) or "packs" not in declarations:
        sys.stderr.write(
            f"serverosctl: {args.declarations} declares no `packs:` — the "
            f"schema contract stopped carrying them, so a caller that passes "
            f"only the schema gets an empty contract\n")
        return EXIT_FAULT
    contract = dict(contract, packs=declarations["packs"])

    if not args.catalogue and not (args.ability_repo and args.ability_rev):
        sys.stderr.write(
            "serverosctl: a pack verdict needs a catalogue, and both ways of "
            "naming one were omitted. Pass --ability-repo and --ability-rev "
            "(the canonical manifest at a revision), or --catalogue. There is "
            "deliberately no default: the local projection is what this NODE "
            "has observed, and resolving a pack against it reports every pack "
            "UNAVAILABLE as though that were a finding about the catalogue.\n")
        return EXIT_FAULT

    catalogue_repo = args.catalogue_repo or (
        str(args.ability_repo) if args.ability_repo else None)
    revision = args.catalogue_revision
    names: list[str] = []
    if args.catalogue:
        names = [line.strip() for line in args.catalogue.read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    else:
        # The manifest, at a revision, out of a git object. `read_manifest`
        # returns the resolved full SHA, and that — not what the operator
        # typed — is the revision the verdict names, because `--ability-rev`
        # is allowed to be a branch or a short SHA.
        try:
            manifest, resolved = projection_mod.read_manifest(
                args.ability_repo, args.ability_rev)
        except projection_mod.AuthorityUnreachable as exc:
            sys.stderr.write(
                f"serverosctl: cannot read the catalogue — {exc}\n"
                f"This is an answer, not a failure to compute one: a pack "
                f"resolved against a catalogue nobody read would be a verdict "
                f"about nothing.\n")
            return EXIT_COULD_NOT_ANSWER
        names = [str(a.get("name")) for a in (manifest.get("abilities") or [])
                 if isinstance(a, dict) and a.get("name")]
        revision = revision or resolved

    if not revision:
        sys.stderr.write(
            "serverosctl: no catalogue revision is available, and a closure "
            "answer must name the revision it was computed against — without "
            "it the verdict is about a catalogue that cannot be produced "
            "again. Pass --catalogue-revision.\n")
        return EXIT_FAULT

    report = packs_mod.resolve(contract, names, catalogue_revision=revision,
                               catalogue_repo=catalogue_repo)

    # Settled here, not inside `resolve()`, for the reason `packs.py` records:
    # a pin that is not the revision being read may still describe the same
    # catalogue, and that question cannot be answered from a name set.
    unsettled: list[dict] = []
    if catalogue_repo:
        unsettled = packs_mod.settle_pins(report, revision=revision,
                                          repo=catalogue_repo)
    else:
        unsettled = [v for v in report["pins"]["packs"] if v["status"] == "PIN_MISMATCH"]

    if args.pack:
        wanted = args.pack
        if not wanted.startswith("pack:"):
            wanted = f"pack:{wanted}"
        report["packs"] = [p for p in report["packs"] if p["id"] == wanted]
        if not report["packs"]:
            sys.stderr.write(
                f"serverosctl: {args.declarations} declares no pack {wanted!r}. "
                f"Refusing rather than answering about the packs it does "
                f"declare, because a filter that silently widens is a filter "
                f"nobody can use to check anything.\n")
            return EXIT_FAULT
        # The pin lines are part of the answer about this pack, so they are
        # narrowed with it. Left alone they printed another pack's pin verdict
        # underneath the one that was asked for — which is how a filtered read
        # starts reporting facts from outside its filter.
        unsettled = [v for v in unsettled if v["pack"] == wanted]

    pins = report["pins"]
    lines = [f"schema      {report['schema']}",
             f"catalogue   {report['catalogue']['abilities']} abilities @ {revision[:12]}",
             f"pins        {'all match' if pins['all_match'] else 'NOT all match'}"
             + ("" if catalogue_repo else
                "  (unsettled — pass --catalogue-repo to ask the ancestor question)")]
    for pack in report["packs"]:
        # The three lists the kernel keeps apart, kept apart here too. A pack
        # is usable without its optional members by definition, so an
        # unresolved optional member is not a refusal — and an unresolved
        # REQUIRED one is, whether or not the other requireds resolved.
        line = f"{pack['id']:<34} {pack['status']:<12} " \
               f"{len(pack['resolved'])}/{pack['members_total']} resolved"
        lines.append(line)
        for member in pack["unresolved_required"]:
            lines.append(f"{'':<34} ! required, unresolved: {member['name']}")
        for member in pack["unresolved_optional"]:
            lines.append(f"{'':<34} - optional, unresolved: {member['name']}")
    if not args.json:
        emit(report, as_json=False, lines=lines)
        for verdict in unsettled:
            # `because`, not `detail`: the verdict's keys are
            # `pack`/`pinned`/`checked`/`status`/`because`, and the first draft
            # of this loop read `detail` — a key that has never existed on any
            # pin verdict, so it printed the pack and the status and then
            # nothing, which looks like a reason that was checked and came back
            # empty. The reason is the whole point here: a stale pin is
            # invisible precisely because the verdict beside it is correct.
            sys.stdout.write(
                f"  pin {verdict['pack']}: {verdict['status']}\n"
                f"    {textwrap.fill(verdict['because'], 74, subsequent_indent='    ')}\n")
        return EXIT_OK
    emit(report, as_json=True)
    return EXIT_OK


def cmd_ability_refresh(args) -> int:
    """§2 — the producer for the local capability projection.

    The Discovery surface has read `state/abilities/effective.json` since §24
    and nothing ever wrote it. So the surface existed, was correct, reported
    the three facts it is careful to keep apart (FRESH / STALE /
    NOT_MATERIALIZED), and answered `NOT_MATERIALIZED` forever. This is the
    caller that ends that.

    It reads the authority's manifest at an explicitly pinned revision from a
    GIT OBJECT rather than from a working tree, which is `ability_projection`'s
    decision and not one to re-litigate here: a dirty or stale checkout on this
    machine must not silently become what the node believes about its own
    capabilities.

    Two things about the answer it prints are load-bearing:

      * `generated_at` is when the authority was last SUCCESSFULLY read, not
        when this file was written. On a failed refresh it is carried through
        unchanged, because the reader ages the projection from it.
      * `abilities: []` with `registry_status: FRESH` is a real and dangerous
        answer — it means the manifest was read and this node matched nothing
        in it. That is what a missing `legacy_ids` entry looks like. It is
        called out below rather than printed as an empty table.
    """
    projection_mod = _load_module_from(REPO_ROOT / "runtime" / "ability_projection.py",
                                       "serveros_ability_projection")

    # The contract's state root, never cwd. A producer that wrote the node's
    # belief about its own capabilities into whatever directory it was run from
    # would be a second, invisible projection — and the reader would keep
    # answering NOT_MATERIALIZED with no clue that a file existed elsewhere.
    state_root = paths_mod.Paths().root("state")
    try:
        document = projection_mod.refresh(
            args.ability_repo, args.ability_rev, tuple(args.device_id), state_root)
    except OSError as exc:
        sys.stderr.write(f"serverosctl: cannot write the projection: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    status = document.get("registry_status")
    abilities = document.get("abilities") or []
    lines = [f"projection  {document.get('schema')}",
             f"status      {status}",
             f"authority   {document.get('authority')}",
             f"revision    {document.get('source_revision') or 'none read'}",
             f"matched     {', '.join(document.get('device_ids_matched') or [])}",
             f"generated   {document.get('generated_at') or 'never'}",
             f"abilities   {len(abilities)}"]
    if status == "STALE":
        lines.append(f"unreachable {document.get('unreachable_reason')}")
        lines.append(document.get("note", ""))
    elif not abilities:
        # FRESH and empty is not "nothing is installed" in general — it is
        # "nothing in the manifest is installed on the ids I matched", and the
        # usual cause is one this command can name.
        lines.append(
            "            the manifest was read and none of its installations "
            "name this device. Check `--device-id`: a renamed device keeps its "
            "historical id in the registry's `legacy_ids`, and matching only "
            "the canonical one finds nothing and reports no error.")
    emit(document, as_json=args.json, lines=lines)
    # STALE is a fault the shell must be able to see without parsing text: the
    # command answered, and what it answered is that the authority is
    # unreachable.
    return EXIT_FAULT if status == "STALE" else EXIT_OK


def _probe_cli_version(program: str) -> tuple[str | None, str | None]:
    """`<program> --version`, as (first line, why not).

    Read-only, and the only subprocess this CLI runs on behalf of a binding. The
    version string is kept VERBATIM — `codex-cli 0.153.4` is what Codex answers
    and it is what goes in the record, unparsed. Pulling a numeric token out of
    it would add a second place to be wrong and would make "the version changed"
    depend on a regex rather than on the program's own answer.

    The program is located with `shutil.which` and never written down, for the
    reason `mcp/codex_app_server_provider.py:167` gives for its own binary: the
    deployed path differs per device and per install method, so recording one
    would record one machine's installation as the product's.
    """
    import shutil
    import subprocess
    path = shutil.which(program)
    if not path:
        return None, (f"`{program}` is not on PATH for this process, so this "
                      "node cannot ask it anything")
    try:
        proc = subprocess.run([path, "--version"], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"`{program} --version` could not be run: {exc}"
    text = (proc.stdout or "") + (proc.stderr or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if proc.returncode != 0 or not lines:
        return None, (f"`{program} --version` exited {proc.returncode} with "
                      f"{'no output' if not lines else 'output'}; nothing was "
                      "observed")
    return lines[0], None


def _commands_under(node) -> list[str]:
    """Every `command` string anywhere under one hook event.

    Generic over the nesting rather than pinned to one shape. The nesting is
    Claude Code's to change, and a reader that hard-coded `[].hooks[].command`
    would report every driver unregistered on the day it changed — a false
    alarm on the one surface whose whole purpose is to report honestly.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_commands_under(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_commands_under(item))
    return found


def _observe_drivers(contract: dict, paths) -> dict:
    """What this node can measure about the declared session-start drivers.

    Read-only, and it reports no hook command. A registration is found by
    looking for the driver's marker among the `command` strings under the
    declared event; the commands themselves are never emitted, because a hook
    command line is a place a token can be and a report that printed them would
    be a way to leak one, `device agents` at a time.

    `state` is one of REGISTERED, NOT_REGISTERED, UNREADABLE. The third is not
    a softer second: a file that is there and cannot be parsed is a question
    this node could not answer, and reporting it as NOT_REGISTERED would be
    inventing a fact about a file nobody could read.
    """
    parity_mod = _load_module_from(REPO_ROOT / "abilities/runtime_parity.py",
                                   "serveros_runtime_parity")
    out: dict[str, dict] = {}
    for driver in parity_mod.declared_drivers(contract):
        name = driver.get("name")
        spec = driver.get("surface") or {}
        program_spec = driver.get("program") or {}
        entry = {"event": driver.get("event"), "marker": driver.get("marker"),
                 "surface": None, "registrations": None, "program": None,
                 "program_present": None, "because": None}
        try:
            base = paths.root(spec.get("path_role"))
        except Exception:  # noqa: BLE001 - an unbound role is a finding
            entry.update(
                state="UNREADABLE",
                because=(f"the path role {spec.get('path_role')!r} is not bound "
                         "on this device, so the surface the driver would be "
                         "registered on has no location here"))
            out[name] = entry
            continue
        target = base / str(spec.get("relative"))
        entry["surface"] = str(target)
        if program_spec:
            try:
                entry["program"] = str(paths.root(program_spec.get("path_role"))
                                       / str(program_spec.get("relative")))
                entry["program_present"] = Path(entry["program"]).is_file()
            except Exception:  # noqa: BLE001 - unbound role, not an answer
                entry["program_present"] = None
        if not target.is_file():
            entry.update(
                state="NOT_REGISTERED",
                because=("the surface file does not exist on this device, so "
                         "nothing is registered on it"))
            out[name] = entry
            continue
        try:
            document = json.loads(target.read_text(encoding="utf-8"))
            event = ((document.get("hooks") or {}).get(driver.get("event"))
                     or [])
        except (OSError, ValueError) as exc:
            entry.update(state="UNREADABLE",
                         because=f"the surface could not be read: {exc}")
            out[name] = entry
            continue
        commands = _commands_under(event)
        marker = str(driver.get("marker"))
        registered = [c for c in commands if marker and marker in c]
        entry.update(
            registrations=len(commands),
            state="REGISTERED" if registered else "NOT_REGISTERED",
            because=None if registered else
            (f"{len(commands)} command(s) are registered under "
             f"{driver.get('event')} and none names {marker}"))
        out[name] = entry
    return out


def _feature_observations(contract: dict, runtime: str, measured: dict) -> dict:
    """What the surfaces already measured here say about this runtime's features.

    Each of the three mappings below is a MEASUREMENT already taken by this
    command, read as an answer to one feature question. Nothing is inferred
    from the runtime's name, and a feature with no measurement behind it is
    absent from this dict — which `abilities/runtime_binding.py` resolves to
    `UNKNOWN`, not to a no.

      instruction.home / instruction.workspace
        the declared instruction surface for that role. A path role that is not
        bound contributes nothing and leaves the feature unestablished; a
        measured file that is there is AVAILABLE and one that is not is
        UNAVAILABLE, which is a claim only because the path was read.

      mcp.client
        the declared MCP surface file. Same rule.

    `gui` and `app.server` are deliberately absent: nothing in this tree
    measures whether an installation has a graphical surface or serves a control
    protocol, so a state for either would have to be invented. `skill.native`
    and `session.lifecycle` are absent too, and for the opposite reason — they
    are product invariants the contract answers for, and a per-device
    observation of them would be a measurement of something that does not vary
    by device.
    """
    out: dict[str, dict] = {}
    for role, feature in (("home", "instruction.home"),
                          ("workspace", "instruction.workspace")):
        entries = [row for row in (measured.get("instruction_surfaces") or ())
                   if row.get("role") == role]
        if not entries:
            continue
        present = [row for row in entries if row.get("present")]
        out[feature] = {
            "state": "AVAILABLE" if present else "UNAVAILABLE",
            "evidence": (
                f"{len(present)} of {len(entries)} declared {role} instruction "
                "surface(s) exist on this device: "
                + ", ".join(sorted(row["path"] for row in entries))),
        }
    mcp_entries = list(measured.get("mcp_surfaces") or ())
    if mcp_entries:
        present = [row for row in mcp_entries if row.get("present")]
        out["mcp.client"] = {
            "state": "AVAILABLE" if present else "UNAVAILABLE",
            "evidence": ("the declared MCP surface file is "
                         + ("present" if present else "absent")
                         + ": " + ", ".join(sorted(row["path"] for row in mcp_entries))),
        }
    return out


def _runtime_observations(contract: dict, measured: dict,
                          device_ref: str) -> tuple[dict, str | None]:
    """(observations by runtime ref, why the version half may be missing).

    The surface half is this command's own measurement, already taken above. The
    version half is a `cli-version` probe against the family's own program, and
    when that probe cannot run the observation is still built — with a time on
    it and no version — so the record says "this node looked, and could not
    establish a version", which is a different fact from "nobody looked".
    """
    observations: dict[str, dict] = {}
    programs = ((contract.get("version_observation") or {}).get("cli_program") or {})
    reasons: list[str] = []
    for runtime in contract.get("ids") or ():
        program = programs.get(runtime)
        version = None
        if program is None:
            reasons.append(f"no `cli_program` is declared for {runtime}, so the "
                           "cli-version probe has nothing to ask")
        else:
            version, why = _probe_cli_version(str(program))
            if version is None:
                reasons.append(why)
        features = _feature_observations(contract, runtime, measured.get(runtime) or {})
        if version is None:
            entry = {"state": "UNKNOWN", "observed_at": None, "source": None,
                     "surface": None, "features": features,
                     "version": None, "probe": None, "version_state": "UNKNOWN"}
            observations[f"agent-runtime:{runtime}"] = entry
            continue
        source = f"`{program} --version` run by serverosctl on {device_ref}"
        observations[f"agent-runtime:{runtime}"] = {
            "state": "OBSERVED",
            "observed_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": source,
            "surface": "cli",
            "features": features,
            "version": version,
            "probe": "cli-version",
            "version_state": "FACT",
        }
    return observations, ("; ".join(reasons) if reasons else None)


def _bindings_for(registry: dict, device_ref: str, observed: dict | None,
                  because: str | None = None) -> dict:
    """The device's bindings, with why the observation half is what it is.

    `because` is attached rather than inferred, so that a reader learns from
    the record which of three different things happened: nobody measured this
    device, the observation aged out, or nothing declared a binding here at all.
    Those are three different findings and a single `UNKNOWN` would serve all
    three equally badly.
    """
    binding_mod = _load_module_from(REPO_ROOT / "abilities/runtime_binding.py",
                                    "serveros_runtime_binding")
    return binding_mod.list_bindings(registry, device_ref, observed=observed,
                                     reference=binding_mod.now_utc(),
                                     unobserved_because=because)


def cmd_device_agents(args) -> int:
    """§9/§18 — the agent runtimes this node has, and whether parity holds.

    Two questions, and the split between them is the point of the command.
    Parity is device-independent: do the two runtime FAMILIES agree on the
    fields they must share. Bindings are per device: what is authored for this
    machine, and — only with `--probe` — what can be measured about it now.

    The observation half is read-only: whether each declared adapter surface
    exists on this device. Nothing is created, and a surface that cannot be
    resolved is reported as unobserved rather than as absent — a path role
    that is not bound means nobody has looked, which is not the same finding
    as a file that is not there.

    `--probe` is opt-in, and it is opt-in for the reason the disclosure contract
    prices L0 at "one manifest read, no probes": without it this command answers
    from files this node already has and costs nothing. With it, this node runs
    each family's own `--version` and reports what came back — and only for the
    device this node IS. A binding for a laptop nobody queried is reported with
    its observation UNKNOWN, which is the honest shape and the one §14 asks for.
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
                measured.append({"path": str(target), "present": target.exists(),
                                 # Carried so that a caller can answer a
                                 # per-feature question from this measurement
                                 # without re-reading the contract's spec list
                                 # a second time and pairing the two by index.
                                 "role": spec.get("path_role"),
                                 "relative": spec.get("relative")})
            entry[field] = measured
        observed[runtime] = entry

    # And the drivers. Separate from the loop above rather than a third entry
    # in `OBSERVABLE_SURFACES`, because the question is separate: those two
    # ask whether a file is there, and this one asks whether a registration is
    # inside a file that is there either way. Folding it in would have given
    # this command a row that reads `present: true` while `drift_sync` fires
    # nowhere.
    observed_drivers = _observe_drivers(contract, paths)

    # --- W7: the bindings. -------------------------------------------------
    # The device this command is about. Default is this node, because this
    # node is the only machine this process can measure; `--device` names
    # another one and gets its authored binding with no observation, which is
    # the honest answer rather than a missing one.
    local_ref = getattr(load_registry(args.registry), "device_ref", None)
    device_ref = getattr(args, "device", None) or local_ref
    probing = bool(getattr(args, "probe", False))
    observations: dict[str, dict] = {}
    because = None
    if device_ref is None:
        because = ("this host is bound to no device, so there is no device for "
                   "these bindings to be about")
    elif device_ref != local_ref:
        because = (f"no observer on this node reaches {device_ref}; the binding "
                   "is authored and unmeasured, and an unmeasured binding is "
                   "not an absent one")
    elif not probing:
        because = ("`--probe` was not requested, so nothing on this device was "
                   "asked anything; the bindings below are authored only")
    else:
        observations, probe_because = _runtime_observations(
            contract, observed, device_ref)
        because = probe_because

    result = parity_mod.report(contract, registry, observed, observed_drivers)
    result["bindings"] = _bindings_for(registry, device_ref, observations,
                                       because)

    # What was observed goes into the record, not only into the prose.
    #
    # It used not to. The surface rows were built inside the prose renderer
    # below and nowhere else, so `--json` answered with a verdict and no
    # observation behind it — the exact shape this command exists to refuse:
    # a machine-readable record that cannot say what it looked at.
    # `tests/test_runtime_parity.py` asked for these rows and had been red since
    # it was written, which was the right reading of a real gap rather than a
    # stale test. Built once here and read by both renderers, so the two cannot
    # drift apart while each stays defensible on its own.
    observed_surfaces = parity_mod.observed_surface_rows(observed,
                                                         result["runtimes"])
    result["observed_surfaces"] = observed_surfaces
    # Same rule as the surface rows: built once, read by both renderers, so the
    # machine-readable record can say what it looked at and the two renderers
    # cannot drift apart. A driver that was not observed produces no row — an
    # absence of measurement is not a measurement of absence.
    observed_driver_rows = parity_mod.observed_driver_rows(observed_drivers,
                                                           contract)
    result["observed_drivers"] = observed_driver_rows

    lines = [f"runtimes    {', '.join(result['runtimes'])}",
             f"compared    {', '.join(result['compared_fields'])}",
             f"may differ  {', '.join(result['allowed_differences'])}"]
    for surface in observed_surfaces:
        mark = "present" if surface["present"] else "MISSING"
        lines.append(f"  {surface['runtime']:<12} {surface['field']:<20} "
                     f"{mark}  {surface['path']}")
    for row in observed_driver_rows:
        mark = {"REGISTERED": "registered", "NOT_REGISTERED": "NOT WIRED",
                "UNREADABLE": "UNREADABLE"}.get(row["state"], str(row["state"]))
        # No hook command is printed here, on purpose: see `_observe_drivers`.
        lines.append(f"  driver {row['driver']:<12} {row['runtime']:<12} "
                     f"{row['event']:<12} {mark}  {row['surface']}")
        if row["because"]:
            lines.append(f"           {row['because']}")
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

    # The bindings, one block per device runtime. L1 is one line; the feature
    # states are one line each because a reader asking "can this machine do X"
    # is asking per feature and a joined string would make UNKNOWN and
    # UNAVAILABLE look alike at a glance.
    document = result["bindings"]
    lines.append(f"bindings    {document['device_ref']} · "
                 f"{document['count']} · {document['state']}")
    if document.get("state_because"):
        lines.append(f"  why       {document['state_because']}")
    for row in document["bindings"]:
        lines.append(f"  {row['runtime_ref']:<28} {row['observation']:<9} "
                     f"surface={row['surface'] or 'UNKNOWN'} "
                     f"version={row['version'] or 'UNKNOWN'}")
        if row["observation_because"]:
            lines.append(f"    why     {row['observation_because']}")
        for name, entry in row["features"].items():
            lines.append(f"    {name:<22} {entry['state']}")
        lines.append(f"    binding_ref {row['binding_ref']} "
                     f"declared_at {row['declared_at']}")
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK if result["verdict"] == "CONFORMANT" else EXIT_FAULT


def _named_catalogue(args) -> tuple[dict | None, str | None, str | None]:
    """input 1 — a catalogue NAMED by the caller, from one of two places.

    Returns `(catalogue, provenance, why_not)`. Exactly one of the last two is
    set: `why_not` is a line for stderr when the catalogue could not be read,
    and `provenance` is what was read, for the `note` on the answer.

    Copied in shape from `cmd_ability_pack`, deliberately, because it is the
    same decision: a verdict computed against a catalogue nobody read is a
    verdict about nothing, and the pack verb has said so since before this one
    existed. What differs is the failure. A pack verdict has nowhere to record
    "no catalogue", so it refuses; a resolution does — UNKNOWN with `ability`
    in `inputs_missing` — so this returns the gap to be answered rather than
    raised, and only an operator who NAMED a catalogue and could not get it
    gets an error.

    The manifest is read through `ability_projection.read_manifest`, out of a
    git object at a revision, and the RESOLVED full SHA is what the note names
    — not what the operator typed, because `--ability-rev` may be a branch or a
    short SHA and a catalogue that cannot be produced again is not a catalogue
    a verdict may cite.
    """
    if args.ability_repo and args.ability_rev:
        projection_mod = _load_module_from(
            REPO_ROOT / "runtime" / "ability_projection.py",
            "serveros_ability_projection")
        try:
            manifest, resolved = projection_mod.read_manifest(
                args.ability_repo, args.ability_rev)
        except projection_mod.AuthorityUnreachable as exc:
            return None, None, (
                f"cannot read the catalogue at {args.ability_repo} "
                f"@{args.ability_rev} — {exc}. This is an answer, not a "
                f"failure to compute one: a resolution against a catalogue "
                f"nobody read would be a verdict about nothing.")
        # The manifest names abilities by their bare id, so the reference is
        # composed here. This is the one branch that composes.
        keys = [f"ability:{name}" for name in
                (str(a.get("name")) for a in (manifest.get("abilities") or [])
                 if isinstance(a, dict) and a.get("name"))]
        where = f"ability-repo {args.ability_repo}@{resolved[:12]}"
        # What the collapse below is, in this branch's own words. The canonical
        # manifest lists an ability once per SOURCE, so `mcps/X` and `plugins/X`
        # are two entries and one ability — its header says so: `path = storage
        # locator（name != identity != path）`, `跨渠道 ≠ 不同 Ability`. Measured
        # at fbd0b1a7, the pinned revision this round resolves against: 56
        # entries, 51 distinct names.
        unit, collapse = (
            "manifest entries",
            "which the manifest itself calls one ability at a second path")
    else:
        try:
            keys = [line.strip() for line in
                    args.catalogue.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
        except OSError as exc:
            return None, None, f"cannot read {args.catalogue} — {exc}"
        where = f"catalogue {args.catalogue}"
        unit, collapse = "lines", "which are the same key written twice"

    if not keys:
        # An empty catalogue is not a permissive one. Handed through it makes
        # every ability a MISS, and the answer reads `PROVIDER_NOT_INSTALLED ·
        # "<ability> is not in the pinned catalogue"` — a definite finding
        # about a list nobody meant to publish, manufactured by a blank file.
        # `resolver._input_ability` is right to read a supplied empty mapping
        # as a closed list with nothing in it; the mistake is upstream, and
        # this is the only place that can tell the two apart, because the
        # intent to name a catalogue is on the command line.
        return None, None, (
            f"{where} names no abilities, so answering against it would report "
            f"every ability as absent from a catalogue nobody wrote. Refusing "
            f"rather than answering against it.")

    # A file names the catalogue's KEYS — references — while the manifest names
    # bare ids, and this is the one place that can hold the two apart. Written
    # without this check, a file listing `ability:runtime-management` gets
    # prefixed into `ability:ability:runtime-management` and misses everything:
    # a definite catalogue finding, produced by a doubled `ability:`. Measured
    # rather than imagined — that is what the first draft of the example
    # catalogue did.
    for key in keys:
        if "#" in key:
            return None, None, (
                f"{key!r} in {where} names an operation. The catalogue is keyed "
                f"by the bare ability — the operation is split off the reference "
                f"before the lookup, so `ability:x#read` and `ability:x` are the "
                f"same entry and a key carrying a verb can never be asked for.")
        if not key.startswith("ability:"):
            return None, None, (
                f"{key!r} in {where} is not an ability reference. Catalogue keys "
                f"are written `ability:<id>`; `{key}` written bare is a "
                f"different key and would miss every entry in a catalogue keyed "
                f"by reference.")
    distinct = sorted(set(keys))
    if len(distinct) == len(keys):
        counted = f"{len(distinct)} {'ability' if len(distinct) == 1 else 'abilities'}"
    else:
        # Both numbers, because both are true of the same object and they answer
        # different questions. A note that printed only `51` would leave a reader
        # unable to tell a de-duplication from five abilities lost on the way in
        # — and the two readings are 56 and 51 of one manifest, which is exactly
        # the kind of pair that gets resolved by guessing when only one is shown.
        lost = len(keys) - len(distinct)
        counted = (
            f"{len(distinct)} {'ability' if len(distinct) == 1 else 'abilities'}, "
            f"composed from {len(keys)} {unit} — {lost} of them "
            f"{'names' if lost == 1 else 'name'} an ability another one "
            f"already names, {collapse}")
    return ({key: {} for key in distinct}, f"{where} ({counted})", None)


def _catalogue_from_names(names) -> dict:
    """Bare ability ids in, the resolver's input 1 out.

    Keyed `ability:<id>`, which is the identity `parse_ability_ref` splits an
    operation off — a catalogue keyed by anything else misses every ability
    whose reference the caller happened to write with a verb on it.

    The value is empty on purpose. `resolver._input_ability` asks one question
    of this mapping (is the ability in it), and inventing a `revision` field
    here would put a number in the record that nothing read. The revision this
    catalogue was read AT belongs on the answer's `note`, where it is the
    revision of the thing that was actually read.
    """
    return {f"ability:{name}": {} for name in sorted(set(names))}


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

    Input 1 has a producer now, and the other seven do not
    ------------------------------------------------------
    This used to say the inputs come from `--request` "and deliberately from
    nowhere else yet", which was true and was read by callers as "the request
    is where the catalogue goes" — so the shipped example request, which
    supplies no catalogue, came back asserting that its ability was not in the
    pinned catalogue. `ability pack` had already refused that exact shape, in
    its own words, one verb over: the local projection "is what this NODE has
    observed, and resolving a pack against it reports every pack UNAVAILABLE as
    though that were a finding about the catalogue". A resolution has the same
    problem and a third option the pack verdict does not — it can say so.

    So a catalogue is named the same two ways it is named there: a file of
    ability names, or the canonical manifest at a revision. Neither is a
    default. Supplying neither is not an error, because the resolver answers
    UNKNOWN and names the gap; it is merely an answer that costs a second run
    to replace, so the ways of supplying one are printed when it happens.

    Nothing here assembles the other seven. Each of them is a device fact, and
    a CLI that guessed at them would produce a confident `AVAILABLE` computed
    from inputs nobody supplied — which is what the kernel refuses to do, and
    what this command would be reintroducing at the layer above it.
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

    catalogue_note = None
    if args.catalogue or (args.ability_repo and args.ability_rev):
        catalogue, provenance, why_not = _named_catalogue(args)
        if catalogue is None:
            sys.stderr.write(f"serverosctl: {why_not}\n")
            return EXIT_COULD_NOT_ANSWER
        request["abilities"] = catalogue
        catalogue_note = provenance
    elif request.get("abilities") is None:
        # Advice, not a verdict. The resolver's UNKNOWN is the answer; this
        # says how to make the next one a finding.
        sys.stderr.write(
            "serverosctl: no ability catalogue was supplied, so the answer is "
            "UNKNOWN and `inputs_missing` names `ability`. Supply one with "
            "--ability-repo and --ability-rev (the canonical manifest at a "
            "revision), or with --catalogue (a file of ability names).\n")

    try:
        record = resolver_mod.resolve(request)
    except resolver_mod.Rejected as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER

    # Where input 1 came from, recorded on the answer. Without it a resolution
    # naming an ability as absent from the catalogue says nothing about WHICH
    # catalogue — and a caller cannot tell a miss against the pinned revision
    # from a miss against a file of names somebody typed. Stored, not derived:
    # the resolver reads a catalogue and cannot know its provenance, and the
    # `revision` this names is the SHA `read_manifest` resolved, not the ref
    # the operator passed.
    if catalogue_note:
        record["note"] = (record.get("note") or "") + \
            f"input 1 read from: {catalogue_note}"

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
# lease — SYS-02's dynamic environment
#
# Four verbs: two reads and two writes. The two writes are the ONLY producers of
# `state/serveros/resource-leases/`, and neither of them touches a resource: a
# lease record says who wants something and whether it may be let go. Deciding
# to actually stop or remove a resource is `serverosd`'s gated reconcile, which
# is the one code path in this product that acts, and adding a second one here
# would be the mistake the `new-lease-system` non-goal was written about.
# ---------------------------------------------------------------------------

LEASE_OWNER_CLASSES = ("UNKNOWN", "EXTERNAL", "MANAGED")


def _lease_lines(record: dict) -> list[str]:
    rows = [f"{record['resource']}  [{record['scope']} / {record['sharing']}]"]
    references = record.get("references")
    if references is None:
        rows.append("  references  (not measured)")
        return rows
    if not references:
        rows.append("  references  none")
    for reference in references:
        state = environment_mod.reference_state(reference, now=time.time())
        rows.append(f"  {state:<8} {reference.get('scope')}:{reference.get('holder')}"
                    f"  expires_at={reference.get('expires_at')}")
    for orphan in record.get("orphaned") or []:
        rows.append(f"  ORPHAN   {orphan['scope']}:{orphan['holder']}  {orphan['why']}")
    if record.get("release_verdict"):
        verdict = record["release_verdict"]
        rows.append(f"  last release  {verdict['verdict']} {verdict['code'] or ''}")
    return rows


def cmd_lease_list(args) -> int:
    try:
        payload = view_mod.environment_leases(state_dir=args.state_dir,
                                              answered_by="serverosctl")
    except lease_store_mod.LeaseError as exc:
        # Exit 2, not 0-with-an-empty-list. `held: 0` is the claim
        # `NO_LEASES_HELD`, and a namespace nobody could read must not make
        # that claim on the node's behalf.
        emit({"leases": None, "error": str(exc)}, as_json=args.json,
             lines=[f"COULD NOT ANSWER  {exc}"])
        return EXIT_COULD_NOT_ANSWER
    if args.json:
        emit(payload, as_json=True)
    else:
        records = payload["leases"]
        sys.stdout.write(f"{payload['root']}\n"
                         f"{payload['recorded']} resource(s) recorded "
                         f"({payload['answered_by']} at {payload['measured_at']})\n")
        for record in records:
            for line in _lease_lines(record):
                sys.stdout.write(f"  {line}\n")
    return EXIT_OK


def cmd_lease_inspect(args) -> int:
    store = lease_store_mod.open_store(args.state_dir, now=time.time)
    try:
        record = store.get(args.resource)
    except lease_store_mod.LeaseError as exc:
        emit({"resource": args.resource, "error": str(exc)}, as_json=args.json,
             lines=[f"COULD NOT ANSWER  {exc}"])
        return EXIT_COULD_NOT_ANSWER
    if record is None:
        # An ANSWER, not a fault: nothing is declared for this resource, so
        # nothing holds it. Exit 0 and say which question was answered.
        emit({"resource": args.resource, "lease": None, "held": False,
              "note": "nothing is declared for this resource"},
             as_json=args.json,
             lines=[f"{args.resource}: nothing declared "
                    f"(NO_LEASES_HELD for this resource)"])
        return EXIT_OK
    emit(record, as_json=args.json, lines=_lease_lines(record))
    return EXIT_OK


def cmd_lease_declare(args) -> int:
    store = lease_store_mod.open_store(args.state_dir, now=time.time)
    try:
        result = store.declare(
            args.resource, scope=args.scope, holder=args.holder,
            sharing=args.sharing, ttl_s=args.ttl, note=args.note or "")
    except (environment_mod.Rejected, lease_store_mod.LeaseError) as exc:
        payload = {"resource": args.resource, "declared": False,
                   "refused": type(exc).__name__, "reason": str(exc)}
        emit(payload, as_json=args.json,
             lines=[f"REFUSED  {args.resource}", f"         {exc}"])
        return EXIT_FAULT
    emit(result, as_json=args.json,
         lines=[f"{result['act']:<8} {args.resource}  {args.scope}:{args.holder}",
                f"         {store.root}"])
    return EXIT_OK


def cmd_lease_release(args) -> int:
    store = lease_store_mod.open_store(args.state_dir, now=time.time)
    protection = None
    if args.protected:
        protection = True
    elif args.not_protected:
        protection = False
    try:
        result = store.release(args.resource, scope=args.scope, holder=args.holder,
                               owner_class=args.owner_class, protection=protection)
    except (environment_mod.Rejected, lease_store_mod.LeaseError) as exc:
        payload = {"resource": args.resource, "released": False,
                   "refused": type(exc).__name__, "reason": str(exc)}
        emit(payload, as_json=args.json,
             lines=[f"REFUSED  {args.resource}", f"         {exc}"])
        return EXIT_FAULT
    lines = [f"{args.resource}  {args.scope}:{args.holder}",
             f"  released   {result['released']}"]
    if result.get("verdict"):
        lines.append(f"  verdict    {result['verdict']['verdict']} "
                     f"{result['verdict']['code'] or ''}")
    if "removed" in result:
        lines.append(f"  record     {'removed' if result['removed'] else 'kept'}")
    if result.get("reason"):
        lines.append(f"  reason     {result['reason']}")
    if result.get("note"):
        lines.append(f"  note       {result['note']}")
    emit(result, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_lease_environment(args) -> int:
    """The §6 equation, read from the durable record and nothing else.

    There is no verb here that declares or releases. A read that could write
    would be a read whose answer depends on whether it was asked, and the two
    asymmetries `environment.effective` exists to keep — observed never becomes
    desired, and an unmeasured reference count is never zero — both depend on
    this command being unable to change what it reports.
    """
    try:
        payload = view_mod.environment_effective(state_dir=args.state_dir,
                                                 answered_by="serverosctl")
    except lease_store_mod.LeaseError as exc:
        emit({"environment": None, "error": str(exc)}, as_json=args.json,
             lines=[f"COULD NOT ANSWER  {exc}"])
        return EXIT_COULD_NOT_ANSWER
    if args.json:
        emit(payload, as_json=True)
    else:
        counts = payload["counts"]
        sys.stdout.write(
            f"{payload['root']}\n"
            f"declared {counts['declared']}  held {counts['held']}  "
            f"releasable {counts['releasable']}  orphaned {counts['orphaned']}  "
            f"unmeasured {counts['unmeasured']}\n")
        if payload["inputs_not_gathered"]:
            # Printed because it changes what the verdicts below MEAN. With
            # ownership ungathered every release reads DENY/OWNER_UNKNOWN, and
            # a reader who took that for a fact about the resources would be
            # reading an absent input as a property of the world.
            sys.stdout.write(
                f"  not gathered here: {', '.join(payload['inputs_not_gathered'])} "
                f"— every release below is refused for the input this command "
                f"did not collect\n")
        for row in payload["resources"]:
            release = row.get("release") or {}
            sys.stdout.write(
                f"  {row['resource']:<32} {str(row['attachment_state']):<12} "
                f"refs={row['ref_count']} {release.get('verdict') or ''}\n")
    return EXIT_OK


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
    # The account running this daemon, measured by uid. Host counters cannot
    # answer "how much of this is mine" -- they belong to everyone at once.
    user = inv.user_summary()
    quota = inv.user_quota()
    # `load` and `used_percent` are two different measurements and both are
    # reported. Load is queue depth -- how many runnable threads were waiting --
    # so `load1 / logical_cpus` is not a utilisation figure: on a 128-core box
    # a load of 12 is either 9% busy or a machine with twelve threads queued
    # behind one core, and the ratio cannot tell those apart. `cpu_usage` asks
    # `/proc/stat` how many jiffies the CPUs actually spent not-idle, which is
    # the question a reader means by "how busy is it".
    usage = inv.cpu_usage()

    payload = {"host": host, "disk": disk, "docker": docker, "advisory": advisory,
               "cpu_usage": usage,
               "user": {"summary": user, "quota": quota}}
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
        (f"cpu use  UNAVAILABLE — {usage['reason']}"
         if usage.get("state") == inventory_mod.UNAVAILABLE else
         f"cpu use  {usage['used_percent']}% over {usage['sample_seconds']}s"),
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
    lines.append("")
    lines.append("this account")
    if user.get("state") == inventory_mod.UNAVAILABLE:
        lines.append(f"  cpu      UNAVAILABLE — {user['reason']}")
        lines.append(f"  memory   UNAVAILABLE — {user['reason']}")
    else:
        lines.append(f"  cpu      {user['cpu_cores']} core · "
                     f"{user['cpu_percent_of_host']}% of host")
        lines.append(f"  memory   {human_bytes(user['memory_rss_bytes'])} · "
                     f"{user['memory_percent_of_host']}% of host")
        lines.append(f"  procs    {user['process_count']} (uid {user['uid']})")
    if quota.get("state") == "PRESENT":
        lines.append(f"  quota    {human_bytes(quota['used_bytes'])} / "
                     f"{human_bytes(quota['limit_bytes'])} · "
                     f"{quota['used_percent']}%  ({quota['mount']})")
    else:
        lines.append(f"  quota    {quota['state']}"
                     + (f" — {quota['reason']}" if quota.get("reason") else ""))
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


def _console_module():
    """§M/§N's console kernel. Same synthetic name the daemon and the agent MCP
    load it under, so the `ConsoleRefused` caught here is the one this module's
    own vocabulary raises rather than a second class with the same name."""
    return _load_module_from(REPO_ROOT / "runtime" / "human_console.py",
                             "serveros_runtime_human_console")


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


def cmd_console_select(args) -> int:
    """Choose which session this conversation addresses. §M's one console write.

    Reachable from a terminal and from an agent inside a conversation, and that
    is the point of it existing as a command: no provider serves this operation,
    so without a surface here a person could read a session list and have no way
    to say which one they meant.
    """
    params = {"conversation_ref": args.conversation, "session_ref": args.session}
    if args.device:
        params["device_ref"] = args.device
    payload, _, code = _coordination_answer("agent.selection.record", params)
    if code != EXIT_OK:
        return code
    if not payload.get("selected"):
        sys.stderr.write(
            f"serverosctl: refused: {payload.get('reason')}: {payload.get('detail')}\n")
        return EXIT_FAULT
    emit(payload, as_json=args.json, lines=[
        f"conversation {payload.get('conversation_ref')}",
        f"session      {payload.get('session_ref')}",
        "             selecting moves your attention and nothing else: the "
        "thread's Item is untouched, because a selection has no field for one"])
    return EXIT_OK


def cmd_console_selection(args) -> int:
    """What this conversation is addressing, and whether it is still there.

    No live session list is supplied, so the answer says `stored-only` and does
    not claim to have checked whether the session still exists. That is not a
    shortcoming of this command — reaching a provider is the agent MCP's job,
    and a CLI that fetched one would be a second process holding a transport.
    """
    payload, _, code = _coordination_answer(
        "agent.selection.resolve", {"conversation_ref": args.conversation})
    if code != EXIT_OK:
        return code
    axis = payload.get("axis") or {}
    lines = [f"conversation {payload.get('conversation_ref')}",
             f"state        {axis.get('state')}"]
    if axis.get("session_ref"):
        lines.append(f"session      {axis.get('session_ref')}")
        lines.append(f"selected at  {axis.get('selected_at')}")
        lines.append(f"identity     {axis.get('identity')}")
    if axis.get("because"):
        lines.append("             " + str(axis["because"]))
    lines.append(f"basis        {payload.get('basis')}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_console_operations(args) -> int:
    """The semantic operations a person can ask a session to do, and their level.

    Printed from the console's own vocabulary rather than a list written here.
    This command is what makes §M checkable by a person: an operation that is
    missing from this output is one no surface can perform, and one whose
    `never` column names another is one the console refuses to report as that
    other — which is §M's 'close is not interrupt', in the form you can read.
    """
    console = sys.modules.get("serveros_runtime_human_console") or _console_module()
    ops = console.operations_at(args.level)
    lines = [f"level        {args.level} (cumulative; L2 offers what L1 does)",
             f"operations   {len(ops)}"]
    for op in ops:
        marks = []
        if op.local:
            marks.append("local")
        if op.destructive:
            marks.append("human_gated")
        if op.requires_feature:
            marks.append(f"needs {op.requires_feature}")
        never = (f"  never satisfied by {', '.join(op.never_satisfied_by)}"
                 if op.never_satisfied_by else "")
        action = f" -> {op.action}" if op.action and op.action != op.name else ""
        lines.append(f"  {op.name:<20}{op.level}  "
                     f"{('/'.join(marks)) or '-'}{action}{never}")
    payload = {"level": args.level,
               "operations": [{"name": op.name, "action": op.action,
                               "level": op.level, "local": op.local,
                               "destructive": op.destructive,
                               "aliases": list(op.aliases),
                               "never_satisfied_by": list(op.never_satisfied_by),
                               "requires_feature": op.requires_feature}
                              for op in ops]}
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# §21 — the alert register, and the one command that actually says something
# ---------------------------------------------------------------------------
#
# Five commands, and the split between them is the design rather than a
# presentation choice.
#
#   list/inspect    read the register. Answered by the DAEMON, because the
#                   register is its state and a second process reading the
#                   file directly would be a second reader that can see a
#                   half-written document.
#   ack/snooze      what a PERSON does about an alert. Answered by the daemon,
#                   over IPC, from a process a person is running. §11: there is
#                   no inbound transport that carries an acknowledgement back
#                   from a chat, so this is the only way one can happen.
#   sync            the delivery hop, and the ONLY command in this tree that
#                   constructs a HumanControlProvider. It evaluates, asks what
#                   has to be said, says it, and records what happened.
#
# The daemon cannot send and this file does not decide. §2 puts ServerOS on the
# "what is the fact" side and the provider on the "how it reaches a person"
# side, and the two only stay apart if the process that evaluates is not the
# process that sends.


def _alert_answer(method: str, params: dict) -> tuple[dict, int]:
    """One daemon call, and the exit code its answer earns.

    Exit 2 is the daemon not answering — a missing measurement. A refusal the
    daemon DID answer ("no alert with that ref") is a fact about the register
    and exits 0, the same reading `_coordination_answer` makes.
    """
    try:
        return ask_daemon(method, params), EXIT_OK
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return {}, EXIT_COULD_NOT_ANSWER


def _alert_state_word(state: str) -> str:
    """OPEN / ACKNOWLEDGED / SNOOZED / RESOLVED, padded for the column."""
    return f"{state or '?':<13}"


def _alert_exists(alert_ref: str) -> int | None:
    """None if this node holds that ref, otherwise the exit code to return.

    Asked through the ordinary read BEFORE asking for the mutation, and the
    reason is the exit code. `ack` and `snooze` raise for a ref the register
    does not hold, and `ask_daemon` reports every refusal the same way as a
    socket that would not connect — so letting the mutation fail would tell a
    caller "this node could not answer" when the truth is that it answered and
    does not hold that alert. One extra read on a command a person types buys
    an exit code that says which of the two happened.
    """
    try:
        payload = ask_daemon("alert.inspect", {"alert_ref": alert_ref})
    except DaemonUnavailable as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    if not payload.get("found"):
        sys.stderr.write(
            f"serverosctl: no alert {alert_ref} is held by this node. "
            f"`serverosctl alert list` shows the refs it does hold — an alert "
            f"ref is derived from code, device and subject, so a ref that "
            f"never existed and one that was never opened look the same.\n")
        return EXIT_FAULT
    return None


def cmd_alert_list(args) -> int:
    """Every alert this node holds, most in need of attention first."""
    payload, code = _alert_answer("alert.list", {})
    if code != EXIT_OK:
        return code
    by_state = payload.get("by_state") or {}
    lines = [
        f"schema      {payload.get('schema')}",
        f"device      {payload.get('device_ref')}",
        f"alerts      {payload.get('count')}",
        "  " + "  ".join(f"{state}={by_state.get(state, 0)}"
                         for state in ("OPEN", "ACKNOWLEDGED", "SNOOZED",
                                       "RESOLVED")),
        "",
        f"{'STATE':<14}{'SEV':<8}{'CODE':<34}{'SUBJECT':<40}",
    ]
    for row in payload.get("alerts") or []:
        lines.append(
            f"{_alert_state_word(row.get('state'))}"
            f"{str(row.get('severity_word') or row.get('severity')):<8}"
            f"{str(row.get('code')):<34}"
            f"{str(row.get('subject_ref')):<40}")
        # The two things a reader needs to know about an alert they are looking
        # at and cannot act on: it was never said, or it was said and did not
        # arrive. Both are printed rather than folded into the state, because
        # neither is a state the alert is in.
        if row.get("pending"):
            lines.append(f"{'':<14}pending {row.get('pending')} "
                         f"not before {row.get('pending_not_before')}")
        if row.get("delivery_failure"):
            lines.append(f"{'':<14}NOT DELIVERED: {row.get('delivery_failure')}")
        if row.get("hold_reason"):
            lines.append(f"{'':<14}quiet: {row.get('hold_reason')}")
    lines.append("")
    lines.append(payload.get("delivery_surface") or "")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_alert_inspect(args) -> int:
    """One alert: the register's row and the sentence a person would be sent."""
    payload, code = _alert_answer("alert.inspect", {"alert_ref": args.alert_ref})
    if code != EXIT_OK:
        return code
    if not payload.get("found"):
        sys.stderr.write(f"serverosctl: {payload.get('note')}\n")
        return EXIT_FAULT
    alert = payload.get("alert") or {}
    lines = [
        f"ref         {alert.get('alert_ref')}",
        f"code        {alert.get('code')}",
        f"state       {alert.get('state')}",
        f"device      {alert.get('device_ref')}",
        f"subject     {alert.get('subject_ref')}",
        f"first seen  {alert.get('first_seen')}",
        f"last seen   {alert.get('last_seen')}  ({alert.get('occurrence_count')} occurrence(s))",
        f"notified    {alert.get('last_notified_at') or 'never'}",
        f"snoozed to  {alert.get('snooze_until') or '-'}",
        f"resolved at {alert.get('resolved_at') or '-'}",
        "",
        "-- what a person would have been sent --",
        payload.get("rendered") or "",
    ]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_alert_ack(args) -> int:
    """A person has seen this. §11: it does NOT mean the condition ended."""
    refused = _alert_exists(args.alert_ref)
    if refused is not None:
        return refused
    params = {"alert_ref": args.alert_ref}
    if args.note:
        params["note"] = args.note
    payload, code = _alert_answer("alert.ack", params)
    if code != EXIT_OK:
        return code
    emit(payload, as_json=args.json, lines=[
        f"ref         {payload.get('alert_ref')}",
        f"state       {payload.get('state')}",
        f"acked at    {payload.get('acknowledged_at')}",
        "            acknowledged, not resolved: the alert keeps its "
        "observation current and closes only when the condition does"])
    return EXIT_OK


def cmd_alert_snooze(args) -> int:
    """Stop repeating it for a while. The observation keeps updating."""
    refused = _alert_exists(args.alert_ref)
    if refused is not None:
        return refused
    params = {"alert_ref": args.alert_ref}
    if args.seconds:
        params["seconds"] = args.seconds
    payload, code = _alert_answer("alert.snooze", params)
    if code != EXIT_OK:
        return code
    emit(payload, as_json=args.json, lines=[
        f"ref         {payload.get('alert_ref')}",
        f"state       {payload.get('state')}",
        f"quiet until {payload.get('snooze_until')}",
        "            it will speak again if it is still firing when this "
        "expires — a snooze that resolved would be a way to make a problem "
        "go away by waiting"])
    return EXIT_OK


def _alert_provider():
    """The HumanControlProvider, constructed HERE and nowhere else.

    Loaded on this command's first line rather than at import, so that a
    `serverosctl` with no transport configured still answers every other
    command. That is the same rule the daemon follows from the other side: the
    surface that decides what is true never depends on the surface that
    delivers, or neither can answer when the other is broken.
    """
    path = _HERE.parent / "mcp" / "human_control.py"
    module = _load_module_from(path, "serveros_human_control")
    return module.BotMuxHumanControlProvider()


def cmd_alert_sync(args) -> int:
    """Evaluate, then say what has to be said, then record what happened.

    Three steps and each one is reported, because the interesting failure is
    the one in the middle: an alert that is open, has been open, and was never
    said — or was attempted and did not arrive. `--dry-run` stops after the
    second step, which is what makes it possible to see what WOULD be sent
    without sending it.

    A FAILED delivery does not resolve the alert and does not mark it
    delivered. It stays pending with its clock pushed forward by the declared
    cooldown, which is §12's answer to both failure modes at once: not a lie
    about what arrived, and not a retry loop.
    """
    passed, code = _alert_answer("alert.pass", {})
    if code != EXIT_OK:
        return code
    pending, code = _alert_answer("alert.pending", {})
    if code != EXIT_OK:
        return code
    items = pending.get("pending") or []
    lines = [f"evaluated   {passed.get('at')} (both detectors)"]
    for entry in passed.get("passes") or []:
        lines.append(f"  {entry.get('detector'):<30} touched={len(entry.get('alerts') or [])} "
                     f"held={len(entry.get('held') or [])}")
    lines.append(f"to say      {len(items)}")

    if args.dry_run:
        for item in items:
            alert = item.get("alert") or {}
            lines.append(f"  would send  {item.get('kind'):<9} "
                         f"{alert.get('severity')} {alert.get('code')} "
                         f"{alert.get('subject_ref')}")
        emit(pending, as_json=args.json, lines=lines)
        return EXIT_OK

    if not items:
        emit(pending, as_json=args.json, lines=lines + [
            "nothing to say. That is not the same as nothing being wrong — "
            "read `alert list` for the alerts that are open and quiet, and "
            "why"])
        return EXIT_OK

    try:
        provider = _alert_provider()
    except Exception as exc:  # noqa: BLE001 - the reason is the answer
        sys.stderr.write(
            f"serverosctl: no HumanControlProvider could be constructed: "
            f"{type(exc).__name__}: {exc}\n"
            f"            {len(items)} alert notification(s) remain pending. "
            f"Nothing was sent and nothing was marked as sent.\n")
        return EXIT_COULD_NOT_ANSWER

    alerting = _load("serveros_runtime_alerting", "alerting.py")
    conversation = args.conversation or os.environ.get("SERVEROS_ALERT_CONVERSATION")
    if not conversation:
        sys.stderr.write(
            "serverosctl: no conversation to send to. Pass --conversation "
            "(or set SERVEROS_ALERT_CONVERSATION). Nothing was sent; the "
            "alerts stay pending.\n")
        return EXIT_COULD_NOT_ANSWER

    sent = failed = 0
    for item in items:
        answer = alerting.notify_for(
            provider, alert=item["alert"], kind=item["kind"],
            device_ref=item["alert"].get("device_ref") or "(unknown device)",
            at=passed.get("at"), conversation_ref=conversation)
        # The provider's answer is SUPPORTED/UNSUPPORTED/UNKNOWN, which is what
        # it thinks of the TRANSPORT. The register records what happened to the
        # MESSAGE, and the two vocabularies are not the same one: a transport
        # that can carry notifications and a call that threw is a FAILED
        # delivery, not a "supported" one. Translated here, in the only place
        # that has both answers in hand.
        state = ("DELIVERED" if answer.delivered
                 else answer.state if answer.state in ("UNSUPPORTED", "UNKNOWN")
                 else "FAILED")
        _alert_answer("alert.record-delivery", {
            "alert_ref": item["alert_ref"], "kind": item["kind"],
            "state": state, "reason": answer.reason})
        if answer.delivered:
            sent += 1
        else:
            failed += 1
        alert = item["alert"]
        lines.append(f"  {'sent    ' if answer.delivered else 'NOT sent'}  "
                     f"{item['kind']:<9} {alert.get('severity')} "
                     f"{alert.get('code')} {alert.get('subject_ref')}")
        if not answer.delivered:
            lines.append(f"            {state}: {answer.reason}")
    lines.append(f"delivered   {sent}, not delivered {failed}")
    lines.append("            a notification that did not arrive leaves the "
                 "alert pending and un-notified; it does not resolve it")
    emit({"sent": sent, "failed": failed, "evaluated": passed},
         as_json=args.json, lines=lines)
    return EXIT_OK


def _digest_answer(method: str, params: dict) -> tuple[dict, int]:
    """One daemon call for the batch hop. Same reading as `_alert_answer`."""
    return _alert_answer(method, params)


def cmd_alert_digest_baseline(args) -> int:
    """§Q2. Take the delivery baseline. Idempotent, and a no-op after the first.

    Split from `digest` rather than folded into it so that the one command
    which declares "everything currently open is not news" is a command
    somebody chose to run. It is safe to run twice and safe to run late: the
    second call changes nothing, and running it late only means fewer things
    were covered by it.
    """
    payload, code = _digest_answer("alert.digest-baseline", {})
    if code != EXIT_OK:
        return code
    lines = [f"baseline    {payload.get('baseline_at')}",
             f"已记录身份  {payload.get('baseline_keys', '—')}"
             if payload.get("taken") else "已存在，本次未改动",
             "",
             payload.get("note") or ""]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_alert_digest(args) -> int:
    """§Q2. What changed since a person was last told, batched into one card.

    Read-only, and it says so in its own output: `decision.send` answers
    whether the document is worth a message, and NOTHING is marked announced
    until a surface that actually holds a transport reports back through
    `alert digest-record`. The split is §2's — this decides what is true, the
    renderer decides how it reaches a person — and it is also what keeps a
    composed-but-undelivered digest from silently consuming its own deltas.
    """
    payload, code = _digest_answer("alert.digest", {})
    if code != EXIT_OK:
        return code
    counts = payload.get("counts") or {}
    severity = payload.get("severity_counts") or {}
    verdict = payload.get("decision") or {}
    frozen = payload.get("frozen") or {}
    lines = [
        f"as of       {payload.get('at')}",
        f"baseline    {payload.get('baseline_at')}",
        f"generation  {payload.get('generation')}",
        f"版本        {frozen.get('version') or '—'} "
        f"({frozen.get('row_count', 0)} 行 · "
        f"{frozen.get('payload_bytes', 0)} 字节)",
        "",
        f"变化        NEW {counts.get('NEW', 0)} · ESCALATED {counts.get('ESCALATED', 0)}"
        f" · RECOVERED {counts.get('RECOVERED', 0)} · REOPENED {counts.get('REOPENED', 0)}",
        f"严重度      🔴 {severity.get('high', 0)} · 🟡 {severity.get('medium', 0)}"
        f" · ⚪ {severity.get('low', 0)}",
        f"显示        {len(payload.get('shown') or [])} 条，另有 "
        f"{payload.get('more', 0)} 条未进卡面",
        f"积压        {payload.get('backlog', 0)} 条（基线之前就开着，不是新闻）",
        f"静默结束    {payload.get('silent_resolutions', 0)} 条（从未告知过，故不报告恢复）",
        "",
        f"是否发送    {'是' if verdict.get('send') else '否'} — {verdict.get('why')}",
        f"幂等键      {verdict.get('idempotency_key')}",
    ]
    for row in payload.get("shown") or []:
        lines.append(f"  {row.get('kind'):<9} {row.get('severity_word'):<8} "
                     f"`{row.get('code')}` {row.get('subject_ref')}")
    lines.append("")
    lines.append(payload.get("note") or "")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_alert_digest_record(args) -> int:
    """§Q2. What the transport did with the digest it was handed.

    `--shown-file` is the document `alert digest --json` printed. It is a FILE
    and not an argument list for two reasons: the shown rows are the evidence
    for what a person was told and should travel whole rather than being
    retyped, and a command line is a place where a long list gets truncated by
    somebody else's limit without anybody noticing.
    """
    shown = None
    frozen = None
    if args.shown_file:
        try:
            document = json.loads(Path(args.shown_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"serverosctl: --shown-file could not be read "
                             f"({type(exc).__name__}: {exc})\n")
            return EXIT_FAULT
        shown = document.get("shown") if isinstance(document, dict) else document
        # The document's own frozen version travels WITH the rows. The receipt
        # has to name the version that was carried, and the severity/state a
        # row was told at are facts about the composing instant — reading them
        # back off the register after the send is how a card that said MEDIUM
        # gets recorded as having said the HIGH somebody escalated to while
        # the transport was thinking. A document without the block is an older
        # daemon and is recorded without it.
        if isinstance(document, dict):
            frozen = document.get("frozen")
        if not isinstance(shown, list):
            sys.stderr.write(
                "serverosctl: --shown-file is neither a digest document nor a "
                "list of its rows. Nothing was recorded — a delivery whose "
                "contents nobody can name is not one this node will mark as "
                "told.\n")
            return EXIT_FAULT
    params = {"generation": args.generation, "state": args.state,
              "shown": shown or [], "reason": args.reason, "frozen": frozen}
    payload, code = _digest_answer("alert.digest-record", params)
    if code != EXIT_OK:
        return code
    lines = [f"generation  {args.generation}",
             f"结果        {args.state}"]
    if payload.get("delivered"):
        lines.append(f"已标记告知  {payload.get('announced_keys')} 条")
        lines.append(f"登记表落章  {payload.get('rows_stamped')} 行")
        lines.append(f"所记版本    {payload.get('payload_version') or '—'}"
                     + ("" if payload.get("payload_hash") else "（文档未带冻结版本）"))
    else:
        lines.append(f"未送达      {args.reason or '未给出原因'}")
        lines.append("            generation 不前进，delta 窗口保持打开 —— "
                     "没送达的变化没有被任何人被告知，下一批会重新组成")
    gone = payload.get("gone_from_register") or []
    if gone:
        lines.append(f"已不在册    {len(gone)} 条（卡面显示过，游标仍记账）")
    superseded = payload.get("superseded") or []
    if superseded:
        lines.append(f"已被更新    {len(superseded)} 条（更晚的一批已经记过账，"
                     f"这份回执不覆盖它）")
    for label, field in (("未冻结行", "unnamed_rows"), ("与冻结不符", "disagreed_rows")):
        rows = payload.get(field) or []
        if rows:
            lines.append(f"{label}    {len(rows)} 条 —— 这份"
                         f"交付的行与它自带的冻结版本对不上，"
                         f"已按冻结版本记账并在此报出")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The card surfaces — one model, a Feishu payload and a preview
# ---------------------------------------------------------------------------
#
# Three commands, one renderer, and a rule about what they are NOT: none of
# them sends anything. §6 of the observability design asks for a JSON payload
# and a visual preview from the same renderer, and it says in as many words
# that a rendered preview is evidence of what the renderer produces and not of
# what a conversation looks like. Actually sending stays with the transport
# that already holds it, so that "look at this card" never becomes "send this
# card to somebody".


def _card_modules():
    """The three modules the card surfaces need, loaded as siblings."""
    return (_load_module_from(REPO_ROOT / "runtime" / "card_source.py",
                              "serveros_runtime_card_source"),
            _load_module_from(REPO_ROOT / "runtime" / "presentation.py",
                              "serveros_runtime_presentation"),
            _load_module_from(REPO_ROOT / "runtime" / "cards.py",
                              "serveros_runtime_cards"))


def _card_clock() -> str:
    """The instant a card is rendered at, in the form the MODEL expects: ISO.

    Named and separated from `_now_text()` so a test can hold it, because the
    two are not interchangeable and nothing said so. `_now_text()` produces
    `"%Y-%m-%d %H:%M:%S %Z"` for the prose lines, and `_card_common` used it for
    this too — but `presentation.open_duration` ends an open row AT THIS VALUE,
    and `datetime.fromisoformat` cannot read `2026-09-22 10:52:12 CST`. So every
    duration on every live alert card rendered 「持续 未知」 while the ledger held
    the measurement, and the collection time could not be shortened either
    (`cards._local` returns nothing for an instant it cannot parse).

    Measured on the production `card alert --json`, 2026-09-22. The card formats
    this for a person; the model is handed the value. Same rule as
    `cmd_registration_resolve`'s: formatting is for the line, the computation
    gets the instant.
    """
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat()


def _card_common(args) -> tuple[dict, str, str]:
    """The model, its caption and the instant, gathered the one way cards know.

    The instant is returned rather than recomputed by the caller because the
    frozen version §3 requires carries it: a card hashed at one clock reading
    and stamped with another would be a receipt whose two halves came from
    two different sends.
    """
    source, presentation, _cards = _card_modules()
    at = getattr(args, "at", "") or _card_clock()
    kind = args._card_kind
    if kind == "status":
        binding = source.load_binding(getattr(args, "gateway_binding", "") or None,
                                      device_ref=getattr(args, "device_ref", "") or None)
        snapshot = source.snapshot(
            source.gather(), at=at,
            node_label=getattr(args, "device_ref", "") or None, binding=binding)
        model = presentation.status_model(snapshot, at=at, binding=binding,
                                          sample=bool(args.sample))
        caption = ("status card — three blocks and the line above them. "
                   "The gateway block is UNBOUND unless a service binding names it")
    else:
        document = _read_card_document(args)
        if isinstance(document, tuple):
            return document  # an (error_payload, ...) short circuit
        if kind == "alert":
            model = presentation.alert_model(document, at=at,
                                             sample=bool(args.sample))
            caption = "alert card — at most three events, and what did not fit"
        else:
            model = presentation.recovery_model(document, at=at,
                                                sample=bool(args.sample))
            caption = ("recovery card — only what was announced, and only what "
                       "has since recovered")
    return model, caption, at


def _read_card_document(args):
    """The digest document, read whole from a file or produced by the command."""
    path = getattr(args, "digest", "") or ""
    if path:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return (_card_error("CARD_DIGEST_UNREADABLE",
                                f"{path}: {type(exc).__name__}: {exc}"), EXIT_FAULT)
        if not isinstance(document, dict):
            return (_card_error("CARD_DIGEST_NOT_A_DOCUMENT",
                                f"{path} holds a {type(document).__name__}; a card "
                                "is rendered from the digest document, not from a "
                                "bare row list"), EXIT_FAULT)
        return document
    payload, code = _digest_answer("alert.digest", {})
    if code != EXIT_OK:
        return (_card_error("CARD_DIGEST_UNAVAILABLE",
                            str(payload.get("message") or payload)), code)
    return payload


def _card_error(code: str, detail: str) -> dict:
    """A failure with a name, so nothing is rendered from a document nobody read.

    The alternative — rendering an empty card when the digest did not arrive —
    is the "quiet machine" reading arrived at by accident, which is the one
    mistake an alert surface cannot make.
    """
    return {"schema": "serveros/card-error.v1", "code": code, "detail": detail,
            "note": ("这张卡没有渲染。诊断没有到达时渲染一张空卡，会让人读成"
                     "“没有异常”，而真实情况是“没有人问过”。")}


def _emit_card(args) -> int:
    model, caption, at = _card_common(args)
    if isinstance(model, dict) and model.get("schema") == "serveros/card-error.v1":
        emit(model, as_json=True)
        return EXIT_FAULT
    _source, _presentation, cards = _card_modules()
    rendered = cards.preview(model, caption=caption)
    if getattr(args, "html_out", ""):
        Path(args.html_out).write_text(rendered["html"], encoding="utf-8")
    card = rendered["card"]
    lines = [f"card      {card['header']['title']['content']}",
             f"template  {card['header']['template']}",
             f"html      {args.html_out or '（未写出；用 --html-out 落盘）'}"]
    payload = card
    # §3. The version of the card that a sender is about to send. Absent by
    # default so that the JSON a renderer already consumes stays the payload
    # itself: wrapping it unconditionally would change `card alert --json` from
    # the飞书 card into an envelope around one, and every consumer would have to
    # learn the envelope before it could send anything.
    if getattr(args, "with_frozen", False):
        frozen = _card_delivery().freeze_card(
            args._card_kind, card, at=at,
            idempotency_key=getattr(args, "idempotency_key", "") or None)
        payload = {"card": card, "frozen": frozen}
        lines.append(f"version   {frozen['version']} "
                     f"({frozen['payload_bytes']} 字节)")
    lines.append("")
    lines.append("本命令不发送任何东西。预览证明 renderer 产出什么，不证明飞书"
                 "怎么显示；发送由已经持有 transport 的命令负责。")
    emit(payload, as_json=True, lines=lines)
    return EXIT_OK


def _card_delivery():
    return _load_module_from(REPO_ROOT / "runtime" / "card_delivery.py",
                             "serveros_runtime_card_delivery")


def _card_answer(method: str, params: dict) -> tuple[dict, int]:
    """One daemon call for the card receipt hop. Same reading as `_alert_answer`.

    The receipt goes through the daemon for the same reason the digest's does:
    the ledger lives beside the register, the daemon owns the state directory,
    and a local file write would be a second writer for one durable document.
    """
    return _alert_answer(method, params)


def cmd_card_record_delivery(args) -> int:
    """§3. What the transport did with the card version it was handed.

    `--card-file` is a `card <kind> --with-frozen --json` envelope: the payload
    that was sent AND the version it was sent as. Both halves are needed
    because the receipt's whole value is that its version and the payload that
    went out are the same thing, and a file that carried only one of them would
    leave that unverifiable.
    """
    module = _card_delivery()
    try:
        document = json.loads(Path(args.card_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"serverosctl: --card-file could not be read "
                         f"({type(exc).__name__}: {exc})\n")
        return EXIT_FAULT
    if not isinstance(document, dict) or not isinstance(document.get("card"), dict):
        sys.stderr.write(
            "serverosctl: --card-file is not a card envelope. Produce one with "
            "`serverosctl card <kind> --with-frozen --json`. Nothing was "
            "recorded — a receipt for a payload nobody can produce is a receipt "
            "for nothing.\n")
        return EXIT_FAULT
    params = {"kind": args.kind, "state": args.state,
              "card": document["card"], "frozen": document.get("frozen"),
              "reason": args.reason, "idempotency_key": args.idempotency_key}
    payload, code = _card_answer("card.record-delivery", params)
    if code != EXIT_OK:
        return code
    entry = payload.get("entry") or {}
    lines = [f"kind        {entry.get('kind')}",
             f"结果        {entry.get('state')}",
             f"所记版本    {entry.get('version')}",
             f"载荷        {entry.get('payload_bytes')} 字节 · "
             f"{str(entry.get('payload_hash') or '')[:12]}"]
    if not entry.get("delivered"):
        lines.append(f"未送达      {entry.get('reason')}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_card_deliveries(args) -> int:
    """§3. What has been sent, by version. A read of the receipt ledger."""
    payload, code = _card_answer(
        "card.deliveries", {"kind": args.kind or None, "limit": args.limit})
    if code != EXIT_OK:
        return code
    lines = [f"kind        {payload.get('kind') or '（全部）'}",
             f"条目        {payload.get('entries')}"]
    rows = payload.get("recent") or []
    for row in rows:
        lines.append(f"  {row.get('at')}  {row.get('state'):<11} "
                     f"{row.get('version')}")
    if not rows:
        lines.append("  （没有记录：这个节点还没有投递过这种卡）")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def _card_entry(kind: str):
    """One entry point per card kind, sharing the emit path above."""
    def run(args) -> int:
        args._card_kind = kind
        return _emit_card(args)
    run.__name__ = f"cmd_card_{kind}"
    return run


def cmd_card_status(args) -> int:
    """§2/§3 — the three blocks, and a gateway only a binding can name."""
    return _card_entry("status")(args)


def cmd_card_alert(args) -> int:
    """§1/§4 — at most three events, each with its own evidence."""
    return _card_entry("alert")(args)


def cmd_card_recovery(args) -> int:
    """§1/§5 — only what was announced and has since recovered."""
    return _card_entry("recovery")(args)


# ---------------------------------------------------------------------------
# The registration loop: observed → candidate → a person's scope → plan
# ---------------------------------------------------------------------------
#
# `runtime/registration.py` answers the question in the middle — *should this be
# registered at all, and by whose authority* — and until this group existed,
# nothing called it. A module with no caller is a schema, and the loop is not
# closed by the module being right. It is closed by a person being able to run
# it end to end, which is what the design asks for:
#
#     发现 / 受信启动记录 → 候选 → 用户范围选择 → 合法登记 → 重新解析
#
# Three commands, and none of them writes. `plan` produces a validated document
# and stops there; the write stays with whatever owns the registry file, so
# "propose a registration" can never quietly become "register".

LAUNCH_RECORD_SCHEMA = "serveros/launch-records.v1"


def _registration_module():
    return _load_module_from(REPO_ROOT / "runtime" / "registration.py",
                             "serveros_runtime_registration")


def _now_text() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z")


def _observed_without_a_registration(reg) -> dict:
    """What this node can see, and which of it no registration names.

    Both halves come from surfaces that already hold the verdict.
    `container_attribution` answers per container whether any registered
    resource names it — `registry_anchors_tried` is present exactly when the
    answer is no — and `port_ownership` answers per endpoint who claims it. A
    second matcher here would be a second answer to a question this tree has
    already answered once, and the two would disagree the first time either
    side learned something.

    What is deliberately NOT attempted: user units. The tree has a per-unit
    verdict for a unit that IS registered (that is `desired_actual`'s job) and
    none at all for one that is not, so this function reports the count it can
    see and names the gap instead of inventing a name-matching rule.
    """
    containers = view_mod.container_attribution(registry=reg)
    ports = view_mod.port_ownership(registry_doc=reg,
                                    containers_document=containers)
    unregistered = []
    for row in containers.get("containers") or []:
        if not row.get("registry_anchors_tried"):
            continue
        unregistered.append({
            "kind": "container",
            "evidence_key": row.get("ref"),
            "label": row.get("container") or row.get("ref"),
            "detail": {
                "disposition": row.get("disposition"),
                "disposition_because": row.get("disposition_because"),
                # The anchors the tree tried, carried through so that the
                # person reading the discovery can see the registry was
                # consulted and what it failed to match on. A rename lands
                # here, and the registry naming the OLD name is the signature
                # of that rather than of a container nobody registered.
                "registry_anchors_tried": row.get("registry_anchors_tried"),
                "state": row.get("state"),
            },
        })
    for row in ports.get("ports") or []:
        # An endpoint becomes a question only when something on this node
        # already claims it — a container or a tunnel — and no owner is
        # recorded. A port nobody claims is a fact to count, not a question to
        # ask: this node has 62 unattributed listeners, and 62 questions with
        # nothing in them is how the four real findings above get lost in the
        # scroll. The count is printed, so nobody reads the short list as a
        # quiet machine.
        claimant = row.get("container_ref") or row.get("tunnel_ref")
        if row.get("owner_ref") or not claimant:
            continue
        unregistered.append({
            "kind": "transport-endpoint",
            "evidence_key": row.get("port_ref"),
            "label": row.get("port_ref"),
            "detail": {"owner_ref": row.get("owner_ref"),
                       "confidence": row.get("confidence"),
                       "container_ref": row.get("container_ref"),
                       "tunnel_ref": row.get("tunnel_ref"),
                       "state": row.get("state")},
        })
    units = inventory_mod.Inventory().user_services_list()
    return {
        "unregistered": unregistered,
        "counts": {
            "containers_observed": len(containers.get("containers") or []),
            "containers_unregistered": sum(
                1 for r in unregistered if r["kind"] == "container"),
            "endpoints_observed": ports.get("port_count"),
            "endpoints_unregistered": sum(
                1 for r in unregistered if r["kind"] == "transport-endpoint"),
            # What the endpoint rule above left out, and why. Printed rather
            # than dropped: an endpoint with no visible holder is not nothing,
            # it is a fact this command declines to turn into a question.
            "endpoints_with_no_visible_holder":
                (ports.get("coverage") or {}).get("listening_with_no_visible_holder"),
            "units_observed": len(units.get("units") or []),
            "units_unregistered": None,
            "registered": len(reg.all()),
        },
        "observed_at": ports.get("measured_at") or containers.get("measured_at"),
        "gap": ("未登记的用户单元数没有值：容器与端口各自有逐条的登记判定，"
                "用户单元只有已登记那一侧的判定（由 registration resolve 给出）。"
                "本命令不发明一套按名字匹配的规则来填这个空"),
    }


def _read_launch_records(path: str | None) -> dict:
    """The trusted records a client wrote, keyed by evidence key.

    Absent is the normal case on this node, and it is not an error: without a
    launch record nothing can carry an owner, so every observation is a
    discovery note. That is the honest output for a node where nobody has said
    what these things are for.
    """
    if not path:
        return {}
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema") != LAUNCH_RECORD_SCHEMA:
        raise ValueError(
            f"{path} declares schema {document.get('schema')!r}, not "
            f"{LAUNCH_RECORD_SCHEMA!r}. A record in an unknown dialect is not a "
            "launch record, and treating one as trusted is how a scan acquires "
            "an owner it never had.")
    return {r["evidence_key"]: r for r in document.get("records") or []
            if r.get("evidence_key")}


def cmd_registration_discover(args) -> int:
    """§B — what is observed and unregistered, as questions or as notes."""
    registration = _registration_module()
    reg = load_registry(args.registry)
    observed = _observed_without_a_registration(reg)
    records = _read_launch_records(args.launch_records)
    device_ref = args.device or reg.device_ref
    at = args.at or _now_text()

    candidates = []
    for item in observed["unregistered"]:
        record = records.get(item["evidence_key"])
        if record:
            candidate = registration.from_launch_record(
                device_ref=device_ref, kind=item["kind"],
                owner=record.get("owner"), launched_by=record.get("launched_by"),
                evidence_key=item["evidence_key"],
                observed_at=record.get("observed_at") or observed["observed_at"],
                detail=item["detail"])
        else:
            candidate = registration.observe(
                device_ref=device_ref, kind=item["kind"],
                evidence_key=item["evidence_key"],
                observed_at=observed["observed_at"], owner=None,
                detail=item["detail"])
        candidates.append(candidate)

    counts = dict(observed["counts"])
    counts["candidates"] = len(candidates)
    counts["candidates_with_an_owner"] = sum(
        1 for c in candidates if not c.deferrable)
    counts["discovery_notes"] = sum(1 for c in candidates if c.deferrable)

    document = {
        "schema": registration.SCHEMA,
        "at": at,
        "device_ref": device_ref,
        "candidates": [c.as_dict() for c in candidates],
        "counts": counts,
        "launch_records": sorted(records),
        "gap": observed["gap"],
        "note": ("这里没有被写入任何登记。带 owner 的是候选，可以交给 registration "
                 "plan 去回答；owner 未知的是发现记录，它停在这里而不是被猜一个"
                 "所有者。两种都在这里，因为把后者藏起来会让一个没有答案的节点"
                 "看起来像一个没有问题的节点。"),
    }

    if args.out:
        Path(args.out).write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"device     {device_ref}",
        f"collect at {at}",
        f"已登记     {counts['registered']}",
        f"观测到     {counts['containers_observed']} 个容器 · "
        f"{counts['endpoints_observed']} 个监听端点 · "
        f"{counts['units_observed']} 个用户单元",
        f"未被登记   {len(candidates)} 条（容器 {counts['containers_unregistered']}"
        f" · 端口 {counts['endpoints_unregistered']}）",
        f"未变成问题 {(counts['endpoints_with_no_visible_holder'] or 0)} 个监听端点"
        f"没有任何可见持有者（已观测、已计数、未提问）",
        "",
    ]
    if not candidates:
        lines.append("本节点没有“看得见、却没有任何登记指向它、并且有人能回答”的东西。")
    for candidate in candidates:
        state = ("候选，已有所有者" if not candidate.deferrable
                 else "发现记录，所有者未知 —— 停在这里")
        lines.append(f"{candidate.id}  {candidate.kind}  {state}")
        lines.append(f"    {candidate.evidence.source}: {candidate.evidence.key}")
        if candidate.discovery_note:
            lines.append(f"    {candidate.discovery_note}")
        lines.append(f"    {candidate.why}")
    lines.append("")
    lines.append(f"提交凭证   {', '.join(sorted(records)) or '无（因此没有任何候选拿到所有者）'}")
    lines.append(f"空缺       {document['gap']}")
    lines.append("")
    lines.append(document["note"])
    if args.out:
        lines.append(f"已写出     {args.out}")
    emit(document, as_json=args.json, lines=lines)
    return EXIT_OK


def _candidate_from_document(document: dict, evidence_key: str):
    registration = _registration_module()
    for payload in document.get("candidates") or []:
        if (payload.get("evidence") or {}).get("key") == evidence_key:
            return registration.Candidate.from_dict(payload)
    raise KeyError(
        f"{evidence_key!r} is not in this document "
        f"({len(document.get('candidates') or [])} candidates). A plan is an "
        "answer about a thing somebody was shown, so it is built from the "
        "document they read rather than re-observed here.")


def cmd_registration_plan(args) -> int:
    """§B — a person's scope, turned into a validated registration. Not written."""
    registration = _registration_module()
    document = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    at = args.at or _now_text()
    try:
        candidate = _candidate_from_document(document, args.evidence_key)
    except KeyError as exc:
        emit({"schema": "serveros/registration-error.v1",
              "code": "CANDIDATE_NOT_IN_DOCUMENT", "detail": str(exc)},
             as_json=True)
        return EXIT_FAULT

    try:
        decision = registration.authorize(
            candidate, args.scope, actor=args.actor, at=at,
            note=args.note or "", recovery=args.recovery)
    except registration.RegistrationError as exc:
        emit({"schema": "serveros/registration-error.v1",
              "code": "DECISION_REFUSED", "detail": str(exc)},
             as_json=True)
        return EXIT_FAULT

    # `probes` is not passed: the plan carries the registration the registry
    # validated, and a probe is a separate declaration a person adds to the
    # registry deliberately. Inventing one here would register a claim about
    # how to measure the thing into the same act as registering the thing.
    plan = registration.plan(candidate, decision, resource_id=args.id)
    payload = {"schema": registration.SCHEMA, "at": at,
               "device_ref": document.get("device_ref"),
               "plan": plan.as_dict()}

    lines = [
        f"candidate  {candidate.id}  ({candidate.kind})",
        f"scope      {decision.scope}  by {decision.actor}",
        f"admissible {plan.admissible}",
    ]
    if plan.refusal:
        lines.append(f"refused    {plan.refusal}")
    if plan.because:
        lines += ["", plan.because]
    for record in plan.as_dict()["records"]:
        lines += ["", json.dumps(record, ensure_ascii=False, indent=2)]
    for entry in plan.defer:
        lines += ["", f"defer  {json.dumps(entry, ensure_ascii=False)}"]
    lines += ["", ("本命令不写任何登记。写回 registry 由持有该文件的一方执行；"
                   "这里给出的是它已经校验过的内容。")]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_registration_resolve(args) -> int:
    """§B — 重新解析: every registration against what was measured about it."""
    registration = _registration_module()
    reg = load_registry(args.registry)
    # The clock is ISO, and not the display string the other commands use.
    # `resolve` ages every observation against it, and `graph.freshness_of`
    # parses what it is given — handed "2026-09-20 01:22:00 CST" it cannot tell
    # the age of anything and answers UNKNOWN for all four registrations, which
    # renders as four "nobody looked" where the truth was one unparseable
    # argument. Formatting is for the line; the computation gets the value.
    now = args.at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    at = now
    measured = view_mod.desired_actual(registry_doc=reg)
    satisfies = (registry_mod.contract().get("desired_actual") or {}).get("satisfies") or {}
    running_words = set(satisfies.get("running") or ())
    stopped_words = set(satisfies.get("stopped") or ())

    observations: dict[str, dict] = {}
    not_decisive = []
    for row in measured.get("comparisons") or []:
        state = row.get("actual_state")
        entry = {"observed_at": row.get("measured_at")}
        if state in running_words:
            observations[row["resource_id"]] = {**entry, "running": True}
        elif state in stopped_words:
            observations[row["resource_id"]] = {**entry, "running": False}
        else:
            # Neither side established it. Left OUT of the join rather than
            # passed as `running: False`: absent and "looked and could not
            # tell" are different facts, and the contract names the second one
            # (`not_comparable`). It is reported beside the readings, never
            # behind them — an empty reading list means one thing when
            # everything was compared and another when nothing was.
            not_decisive.append({"resource_id": row.get("resource_id"),
                                 "actual_state": state,
                                 "verdict": row.get("verdict"),
                                 "why": row.get("why")})

    result = registration.resolve(reg.all(), observations, now=at)
    counts: dict[str, int] = {}
    for reading in result["readings"]:
        counts[reading["state"]] = counts.get(reading["state"], 0) + 1
    payload = {**result, "device_ref": reg.device_ref,
               "measured_at": measured.get("measured_at"),
               "counts": counts, "not_decisive": not_decisive,
               "coverage": {
                   "registered": len(reg.all()),
                   "joined": len(observations),
                   "not_decisive": len(not_decisive),
                   "unchecked": len(measured.get("unchecked") or []),
               }}

    lines = [f"device      {reg.device_ref}",
             f"collect at  {at}",
             f"measured at {measured.get('measured_at') or 'UNKNOWN'}",
             f"registrations {len(reg.all())}   joined {len(observations)}   "
             f"not decisive {len(not_decisive)}   unchecked "
             f"{len(measured.get('unchecked') or [])}",
             "",
             f"{'RESOURCE':<34} {'READING':<12} {'DESIRED':<10} FRESHNESS"]
    for reading in result["readings"]:
        freshness = reading.get("freshness") or {}
        lines.append(f"{reading['resource_ref']:<34} {reading['state']:<12} "
                     f"{str(reading['desired']):<10} "
                     f"{freshness.get('state')}"
                     # An UNKNOWN freshness names its reason here. Without it
                     # the line says "nobody looked" and the reason it cannot
                     # say that is a property of the clock this command was
                     # given, which is the reader's to repair.
                     + (f" —— {freshness['why']}" if freshness.get("why") else ""))
    for entry in not_decisive:
        lines.append(f"{entry['resource_id']:<34} {'未判定':<12} "
                     f"{'':<10} {entry['actual_state']} —— {entry['why']}")
    lines += ["", result["note"], "",
              "每条读数都是 false 的 actionable：看到不等于可以动手，"
              "看不见也不等于已经结束。"]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


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
# §S — the global requirement board.
#
# The board's rules live in `intake/global_board.py` and this is the shell
# around them: the same split `ability refresh` has, and here for the same
# reason — the part worth testing is the part that does not touch a disk.
#
# IT IS `requirement board` AND NOT `board`. Two programs carry the name
# `serverosctl`, PATH gives it to this repository's, and the other one — the
# work-engine control plane — is what publishes the per-project board
# documents this command READS via `board render`. A `board` verb here would
# be a second thing whose name resolves to a different program's job, which is
# the exact ambiguity the `boards` entry in `fleet/state-root-contract.yaml`
# exists to record. `requirement` is §S's own noun and belongs to no one else.
# ---------------------------------------------------------------------------


def cmd_requirement_board(args) -> int:
    """§S — the Global Requirement Board, composed from projections that exist.

    Read-only unless `--write` is given, and the default is read-only because
    the board is a projection: printing it changes nothing, and a command that
    wrote on every inspection would leave a file whose only reader is the
    person who asked for the printout.

    Two things the answer is careful about:

      * It does NOT read the work-engine store. The statuses it shows came
        from board documents the control plane rendered from the authority,
        which is the one edge `fleet/mcp-contract.yaml` owns. A second reader
        here would be a second answer to "what state is this Item in".
      * `priority: UNPRODUCED` is a complete answer, not a missing one. No
        priority field exists anywhere in this fabric, and the board says so
        in a named state rather than bucketing Items by a proxy and printing
        the result under P0–P4.
    """
    board_mod = _load_module_from(REPO_ROOT / "intake" / "global_board.py",
                                  "serveros_global_board")
    paths = paths_mod.Paths()
    boards_root = Path(paths.resolve("state/boards")["path"])
    workspace = Path(paths.root("workspace"))
    # The W2 store, at the one path the daemon writes it to. Read directly
    # rather than through a daemon call because this command has to answer
    # when the daemon does not — an absent store is "no session is bound",
    # which is a fact and not a missing measurement.
    binding_store = _agent_control_store_path()

    def resolve_locator(locator: str) -> str:
        """A card locator as an absolute path, relative to the WORKSPACE root.

        `card_locator` is written by the authority and has been seen in both
        forms. Relative means relative to the workspace — the cards live under
        `records/`, a workspace child — and never to the cwd: a board composed
        from a different directory would silently join a different set of
        cards, and every row would look complete.
        """
        candidate = Path(locator)
        return str(candidate if candidate.is_absolute() else workspace / locator)

    try:
        document = board_mod.build(boards_root=boards_root,
                                   card_locator_resolver=resolve_locator,
                                   binding_store=binding_store)
    except board_mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    written = None
    if args.write:
        output_dir = Path(args.output_dir) if args.output_dir else Path(
            paths.resolve("state/requirement-board")["path"])
        if not output_dir.is_absolute():
            sys.stderr.write(
                f"serverosctl: --output-dir must be absolute, got {output_dir}. "
                f"A board written into whatever directory the caller was "
                f"standing in is a second board nobody knows about.\n")
            return EXIT_FAULT
        if output_dir == boards_root or boards_root in output_dir.parents:
            # Refused rather than documented, because the failure it prevents
            # is silent for one run and then total: `load_boards` globs
            # `*/current.json` under the boards root, so a board written there
            # would be read back next time as a project board, and `compose`
            # would refuse the whole composition over the schema mismatch this
            # command had just created.
            sys.stderr.write(
                f"serverosctl: --output-dir {output_dir} is inside the boards "
                f"root {boards_root}, which this command reads. The next run "
                f"would pick this board up as a project board and refuse the "
                f"composition over a mismatch it had written itself.\n")
            return EXIT_FAULT
        try:
            written = board_mod.render(document, output_dir)
        except OSError as exc:
            sys.stderr.write(f"serverosctl: cannot write the board: {exc}\n")
            return EXIT_COULD_NOT_ANSWER

    counts = document["counts"]
    priority = document["priority"]
    lines = [
        f"board       {document['schema']}",
        f"generated   {document['generated_at']}",
        f"projects    {', '.join(document['source']['projects']) or 'none'}",
        f"boards      {document['source']['boards']} read",
        f"revision    {document['source']['generation_source_revision'] or 'not recorded'}",
        f"items       {counts['items']} ({counts['active']} active, "
        f"{counts['completed']} completed)",
        f"cards       {counts['items'] - counts['without_a_card']} joined, "
        f"{counts['without_a_card']} not joined",
        f"priority    {priority['state']}",
    ]
    if priority["state"] == "UNPRODUCED":
        # The line above is a state a caller can switch on; this is why it is
        # that state, and it is printed every time because "the priority
        # column is empty" and "nobody produces a priority" send a reader to
        # two different places.
        lines.append(f"            {priority['because']}")
    lines.append("")
    for section in document["sections"]:
        lines.append(f"  {section['title']:<8} {len(section['items'])}")
    named = priority.get("titles_naming_a_level") or []
    if named:
        lines.append("")
        lines.append("titles containing a literal P0-P4. Reported, and NOT read "
                     "as a priority:")
        for row in named:
            lines.append(f"  {row['item_ref']}  {row['title']}")
    if written is not None:
        lines.append("")
        lines.append(f"written     {written['json']}")
        lines.append(f"            {written['markdown']}")
    emit(document, as_json=args.json, lines=lines)
    return EXIT_OK


# ---------------------------------------------------------------------------
# §6/§8 — the personal board, and the documents behind each row
# ---------------------------------------------------------------------------
#
# NOT `requirement board`. The two read the same projections and answer
# different questions, and the split is §8's: the requirement board is the
# global projection and has no authority; the personal board is what a person
# carries today, and it reads a file a person WROTE. Folding the second into
# the first would put a decision inside the document whose whole value is that
# it is derived and never edited.
#
# Neither one opens the work-engine store. `state/boards/<project>/current.json`
# is the only path by which Item state reaches either of them, which is the
# boundary `intake/board-contract.yaml` and `fleet/mcp-contract.yaml` both draw.
# ---------------------------------------------------------------------------


def _workchain_module():
    return _load_module_from(REPO_ROOT / "intake" / "workchain.py",
                             "serveros_intake_workchain")


def _workchain_skeletons(chain):
    """The declared document shapes, or None when the file is not here.

    `None` rather than an empty dict, because the two mean different things
    downstream: an empty dict would make every present document read
    `NOT_CHECKED` with the reason "no skeleton declares this document", which is
    a sentence about a missing FILE reported as a claim about a document.
    """
    path = REPO_ROOT / "intake" / "document-skeletons.yaml"
    return chain.load_skeletons(path) if path.is_file() else None


def _workchain_roots(args) -> tuple[Path, Path, Path] | tuple[None, None, None]:
    """The roots the chain reads, all overridable and all absolute.

    `--records-root` and `--workspace-root` exist because a fixture has to be
    able to run this against a directory it owns. They are two roots and not
    one because they answer two questions: the records root is where an Item's
    documents are looked for when nothing better is known, and the workspace
    root is what a RELATIVE `card_locator` is relative to. Composing a card path
    from the records root alone — which a first version did — dropped the
    `projects/` segment the locators carry and put all 17 real cards in the
    missing bucket.
    """
    paths = paths_mod.Paths()
    workspace = Path(getattr(args, "workspace_root", "") or
                     paths.root("workspace"))
    boards = Path(getattr(args, "boards_root", "") or
                  paths.resolve("state/boards")["path"])
    records = Path(getattr(args, "records_root", "") or workspace / "records")
    for label, root in (("--boards-root", boards), ("--records-root", records),
                        ("--workspace-root", workspace)):
        if not root.is_absolute():
            sys.stderr.write(
                f"serverosctl: {label} must be absolute, got {root}. A board "
                f"composed from whatever directory the caller was standing in "
                f"is a board nobody else can reproduce.\n")
            return None, None, None
    return boards, records, workspace


def _today_for(chain, records_root: Path, projects) -> dict:
    """One authored selection per project, or an explicit statement of none.

    Read per project because the selection is per project: `today.yaml` sits
    beside that project's board, and a person carrying two projects chose
    separately in each. Merging the files first and reading once would make a
    single unreadable file look like a whole-node answer.

    The path comes from `chain.today_path` rather than being composed here, so
    the writer and this reader cannot disagree about where the file is. They
    DID disagree: this function looked at `<records>/<project>/board/` while the
    writer wrote `<records>/projects/<project>/board/`, and the board said
    "nobody has chosen" about a project where somebody had.
    """
    out = {}
    for project in projects:
        name = project["project"]
        out[name] = chain.load_today(chain.today_path(records_root, name))
    return out


def _workchain_refusals(fn):
    """Turn a `workchain.Refused` into the sentence it was written to be.

    `Refused` is this module's way of saying "I will not answer this, and here
    is why" — an unknown resolution rule, a contract that names no sections, a
    skeleton file present but naming no scopes. Each of those messages was
    written to be read. Without this wrapper they arrive as a traceback, which
    is the same information with the explanation buried under a stack, and the
    one thing a traceback does not do is tell a reader which of their own files
    to look at.
    """
    def run(args):
        try:
            return fn(args)
        except _workchain_module().Refused as exc:
            sys.stderr.write(f"serverosctl: {exc}\n")
            return EXIT_FAULT
    run.__name__ = getattr(fn, "__name__", "run")
    return run


def _agent_control_store_path() -> Path:
    """The one file the daemon writes control-plane state to.

    Named once, here, because three verbs now read it — the board (for §17
    bindings), `work focus` (for the delegations a cycle may be admitted
    under) and `work grants` (for the same records, without a board). Three
    compositions of the path would be three answers to "where is this node's
    control-plane state", and the one that drifted would read an absent store
    as "nothing was ever delegated".

    Read directly rather than through the daemon for the reason `work board`
    already states: a verb that has to answer when the daemon does not. An
    absent store is a node where nothing has been recorded — a fact, not a
    missing measurement — and the daemon's own `_load` draws that distinction
    from the other side.
    """
    paths = paths_mod.Paths()
    return Path(paths.resolve("state/runtime/agent-control")["path"]) / "store.json"


def _control_store():
    """The control store, opened for a verb that has to answer without the daemon.

    `AgentControlStore.__init__` reads the file, so an unreadable store raises
    here rather than silently answering empty — the module's own refusals are
    the point and this function does not soften them.
    """
    mod = _load_module_from(REPO_ROOT / "runtime" / "agent_control_store.py",
                            "serveros_runtime_agent_control_store")
    return mod, mod.AgentControlStore(_agent_control_store_path())


def _work_board_document(args):
    """The composed personal board, shared by every verb that reads it.

    Factored rather than composed twice. `work board` and `work focus` must see
    the same Items, and two compositions would be two answers to "which Items
    exist" — the pair only has to disagree once, and the disagreement would show
    up as a focus selection that names an Item the board does not print, or
    omits one it does.
    """
    chain = _workchain_module()
    boards, records, workspace = _workchain_roots(args)
    if boards is None:
        return None
    contract = chain.load_contract(
        REPO_ROOT / "intake" / "workchain-contract.yaml")
    projects = chain.load_projects(
        boards_root=boards,
        projects=(args.project.split(",") if getattr(args, "project", "") else None))
    return chain.compose_board(contract=contract, projects=projects,
                               records_root=records,
                               workspace_root=workspace,
                               skeletons=_workchain_skeletons(chain),
                               today_by_project=_today_for(chain, records, projects))


def cmd_work_board(args) -> int:
    """§6 — the personal board: 今日重点 through 已完成, in §6's order."""
    board = _work_board_document(args)
    if board is None:
        return EXIT_FAULT

    if args.write:
        output = Path(args.output_dir) if args.output_dir else (
            Path(paths_mod.Paths().resolve("state/workchain")["path"]))
        if not output.is_absolute():
            sys.stderr.write(
                f"serverosctl: --output-dir must be absolute, got {output}. A "
                f"board written into the caller's cwd is a second board nobody "
                f"knows about.\n")
            return EXIT_FAULT
        output.mkdir(parents=True, exist_ok=True)
        (output / "board.json").write_text(
            json.dumps(board, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (output / "board.md").write_text(_work_board_markdown(board), encoding="utf-8")
        board = {**board, "written_to": str(output)}

    emit(board, as_json=args.json,
         lines=_work_board_lines(board))
    return EXIT_OK


def _work_board_lines(board: dict) -> list[str]:
    counts = board["counts"]
    today = board["today"]
    if today["state"] == "SELECTED":
        head = f"今日重点   {len(today['items'])} 项：{'、'.join(today['items'])}"
    elif today["state"] == "EMPTY":
        head = "今日重点   已记录，未选任何项 —— 是「选了空」，不是「还没选」"
    elif today["state"] == "UNPRODUCED":
        head = "今日重点   未记录选择 —— 不是没有重点，是还没有人选"
    else:
        head = f"今日重点   {today['state']}：{today['why']}"
    lines = [head,
             f"项目       {counts['projects_read']} 读到 / "
             f"{counts['projects_unread']} 未读到",
             f"条目       {counts['items']}"]
    for project in board["projects"]:
        if project["state"] != "READ":
            lines.append(f"  {project['project']}  {project['state']} —— "
                         f"{project['why']}")
            lines.append(f"      看的路径 {project['path']}")
    for section in board["sections"]:
        lines += ["", f"— {section['title']}（{len(section['rows'])}）"]
        if section.get("because"):
            # A section this contract ADDED to §6's list says so on the board
            # itself. A reader who disagrees with the filing needs to be able
            # to see it was a decision rather than read it as §6's own order.
            lines.append(f"    〔本节的来源：{section['because']}〕")
        if not section["rows"]:
            lines.append("    （无）")
            continue
        for row in section["rows"]:
            title = row["title"] or ""
            if section["struck_through"]:
                title = "".join(f"{ch}̶" for ch in title)
            lines.append(f"    {row['id']:<12} {row['state']:<11} "
                         f"{row['priority']['personal']['state']:<11} {title}")
    if board["unplaced"]:
        lines += ["", f"— 未被任何栏目认领（{len(board['unplaced'])}）"]
        for row in board["unplaced"]:
            lines.append(f"    {row['id']:<12} {row['state']:<11} {row['why']}")
    lines += ["",
              # NOT "0 missing, of which 1 unreadable". An unreadable card is
              # not a subset of the missing ones — it is there and cannot be
              # read — so the parenthetical that read as a fraction of the
              # first number was arithmetic over an empty set.
              f"缺卡       {counts['cards_missing']} 张",
              f"卡片不可读   {counts['cards_unreadable']} 张",
              f"缺文档     {counts['documents_missing']} 份（仅计入当前状态已要求的）",
              f"空文档     {counts.get('documents_empty', 0)} 份 —— "
              f"这一项才是缺陷；另有 {counts.get('documents_diverging', 0)} 份"
              f"用的是自有小节，提案小节见 `work skeleton`，不计为缺陷",
              f"优先级     未产出 {counts['priorities_unproduced']} 处 —— "
              f"字段本身还没有人批准，见 intake/workchain-contract.yaml#card_extension"]
    if counts.get("cards_rebased") or counts.get("cards_unresolved"):
        # Not folded into 缺卡. A card that resolved under another rule is
        # present and correct while the locator beside it is still wrong, and a
        # reader who saw only "0 缺卡" would have no reason to go and fix the
        # pointer that would have sent them to the wrong machine.
        lines.append(
            f"卡片定位   改挂 {counts.get('cards_rebased', 0)} 张、"
            f"无法定位 {counts.get('cards_unresolved', 0)} 张 —— "
            f"改挂 = locator 写的是另一台机器的路径，按本机 workspace 重挂；"
            f"用 `work item <ID>` 看原 locator 与改挂原因")
    return lines


def _work_board_markdown(board: dict) -> str:
    """The same board as a document, and the same sections in the same order.

    Generated from the model rather than from the lines above, so the two
    outputs cannot disagree about a row — the same reason the status card
    renders its JSON and its HTML from one model.
    """
    lines = ["# 个人看板", "",
             f"生成：`{board['at']}`", ""]
    today = board["today"]
    lines += [f"**今日重点** {today['state']}"
              + (f" — {'、'.join(today['items'])}" if today["items"] else
                 (f" — {today['why']}" if today["why"] else "")), ""]
    for section in board["sections"]:
        lines.append(f"## {section['title']}（{len(section['rows'])}）")
        lines.append("")
        if section.get("because"):
            lines.append(f"> 本节的来源：{section['because']}")
            lines.append("")
        if not section["rows"]:
            lines.append("（无）")
            lines.append("")
            continue
        lines.append("| Item | 状态 | 个人优先级 | 标题 | 文档 |")
        lines.append("|---|---|---|---|---|")
        for row in section["rows"]:
            title = row["title"] or ""
            if section["struck_through"]:
                title = f"~~{title}~~"
            docs = "".join(
                "●" if d["state"] == "PRESENT" else
                ("○" if d["state"] == "NOT_YET_REQUIRED" else "×")
                for d in row["documents"])
            lines.append(f"| {row['id']} | {row['state']} | "
                         f"{row['priority']['personal']['state']} | {title} | {docs} |")
        lines.append("")
    if board["unplaced"]:
        lines.append(f"## 未被任何栏目认领（{len(board['unplaced'])}）")
        lines.append("")
        for row in board["unplaced"]:
            lines.append(f"- `{row['id']}`（{row['project']}）{row['state']} — {row['why']}")
        lines.append("")
    order = " → ".join(
        d["document"] for d in board["sections"][0]["rows"][0]["documents"]
    ) if any(s["rows"] for s in board["sections"]) else "card.yaml → brief.md …"
    lines += [f"> 文档列按 `{order}` 的顺序；`●` 已有 · `○` 此状态还不要求 · "
              f"`×` 此状态要求但缺", ""]
    return "\n".join(lines)


def _focus_decision(args):
    """The board, the envelope, and the decision — `work focus`'s whole answer.

    Shared with `work admit` rather than re-derived there, and that is the point
    of the extraction: what a cycle is admitted for must be what this verb would
    have selected, from the same board, under the same envelope, at one instant.
    A second composition in the admitting verb would be a second answer to one
    question, and the two would agree only until one of them was edited.

    Returns the four values, or None after writing the fault to stderr. The
    caller returns `EXIT_FAULT`; this helper does not, because a fault path that
    returns an exit code from two places is how one of them ends up returning
    the wrong one.
    """
    board = _work_board_document(args)
    if board is None:
        return None
    focus_mod = _load_module_from(REPO_ROOT / "intake" / "focus_source.py",
                                 "serveros_intake_focus_source")
    cycle = _load_module_from(REPO_ROOT / "intake" / "daily_cycle.py",
                              "serveros_intake_daily_cycle")
    delegation_mod = _load_module_from(REPO_ROOT / "context" / "delegation.py",
                                       "serveros_context_delegation")
    try:
        composed = focus_mod.compose(board)
    except focus_mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    # The envelope a cycle may be admitted under. It is built from what this
    # node RECORDED — `runtime/agent_control_store.py`'s `grants` collection —
    # and every grant is re-checked against the delegation it derives from
    # before it enters the map. A grant file is deliberately not an option:
    # there is no shape of authority this fabric refuses more firmly, and no
    # way to tell one from a file somebody edited.
    store_mod, control = _control_store()
    try:
        recorded, contexts = control.grants(), control.contexts()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return None
    named_by = {str(row["grant_ref"]) for row in composed["rows"]
                if row.get("grant_ref")}
    try:
        delegations = delegation_mod.envelopes(
            recorded, contexts, now=now.isoformat(), named_by=named_by)
    except delegation_mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return None

    if composed["observed_at"] is None:
        decision = {"status": "BLOCKED", "reason": "PROJECTION_AGE_UNKNOWN",
                    "selected": [], "carryover": []}
    else:
        decision = cycle.select_focus(
            composed["rows"], delegations["envelopes"], now=now,
            observed_at=composed["observed_at"],
            ttl_seconds=args.ttl, limit=args.limit)
    return composed, delegations, decision, now


def cmd_work_focus(args) -> int:
    """§12 — which two objectives the next cycle may carry, and why not more.

    The one verb that points `intake/daily_cycle.select_focus` at a projection a
    producer actually rendered. It decides nothing on its own: `select_focus`
    returns `admission_required: True`, and the authority for admitting work is
    work-engine's atomic admission, not this printout.

    What it refuses to paper over, all three measured on this node:

      * a projection whose FACTS are old. The board composes in this second, so
        a reader who takes the composition time for the observation time gets a
        fresh-looking answer built on files last written on 2026-09-10. The age
        used here is the oldest underlying projection, and a stale one produces
        STALE_BOARD naming the age rather than a selection.
      * an age nobody can state — an unreadable or undated projection — which
        is `PROJECTION_AGE_UNKNOWN` and not "fresh enough".
      * grants. None are recorded on this node, and a row cannot name one — the
        board carries no `grant_ref`. Both halves are printed as the states
        they are, because "the board names no grant" and "the store holds none"
        are different gaps with different producers, and one sentence covering
        both would point a reader at the wrong one.
    """
    state = _focus_decision(args)
    if state is None:
        return EXIT_FAULT
    composed, delegations, decision, now = state
    recorded = delegations["summary"]["recorded"]
    named_by = {str(row["grant_ref"]) for row in composed["rows"]
                if row.get("grant_ref")}

    lines = [
        f"board       {composed['board_at']} (composed now)",
        f"observed    {composed['observed_at'] or 'unknown'} (oldest projection)",
        f"now         {now.isoformat()}",
        f"ttl         {args.ttl}s",
        f"projects    {len(composed['projects'])}: "
        + ", ".join(f"{name} {stamp or 'undated'}"
                    for name, stamp in sorted(composed["projection_times"].items())),
        f"rows        {len(composed['rows'])}",
        f"delegations {delegations['summary']['recorded']} recorded · "
        f"{delegations['summary']['authorizing']} authorizing"
        + ("" if recorded else " — none recorded on this node"),
        f"named       {len(named_by)} row(s) name a grant_ref"
        + ("" if named_by else " — the board carries none"),
    ]
    for row in delegations["rows"]:
        lines.append(f"  {row['grant_ref']:<24} {row['state']:<30} "
                     f"{row['because']}")
    lines.append(f"decision    {decision['status']}")
    if decision.get("reason"):
        lines.append(f"reason      {decision['reason']}")
    lines.append("")

    by_id = {row["id"]: row for row in composed["rows"]}
    selected = decision.get("selected") or []
    lines.append(f"selected    {len(selected)}")
    for item_id in selected:
        row = by_id.get(item_id, {})
        lines.append(f"  {item_id:<10} {row.get('state','?'):<11} "
                     f"{row.get('priority') or 'no priority'}  {row.get('title','')}")
    because = decision.get("carryover_because") or {}
    lines.append("")
    lines.append(f"carryover   {len(decision.get('carryover') or [])} "
                 f"(slots {args.limit}) — unresolved, each holding a slot")
    for item_id in decision.get("carryover") or []:
        row = by_id.get(item_id, {})
        lines.append(f"  {item_id:<10} {row.get('state','?'):<11} "
                     f"{because.get(item_id,'')}")
    lines.append("")
    lines.append(f"excluded    {len(decision.get('excluded') or [])}")
    for entry in decision.get("excluded") or []:
        row = by_id.get(entry["id"], {})
        lines.append(f"  {entry['id']:<10} {row.get('state','?'):<11} {entry['reason']}")
    unevaluated = decision.get("unevaluated") or []
    if unevaluated:
        lines.append("")
        lines.append(f"unevaluated {len(unevaluated)} — no producer states whether a "
                     f"worker is still held; state decided")
        for entry in unevaluated[:5]:
            lines.append(f"  {entry['id']:<10} {','.join(entry['fields'])}")
        if len(unevaluated) > 5:
            lines.append(f"  … {len(unevaluated) - 5} more, same two fields")
    if not named_by:
        lines.append("")
        lines.append("no row names a grant: " +
                     composed["not_produced_elsewhere"]["grants"])
    emit({"decision": decision, "focus": composed, "delegations": delegations},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_grants(args) -> int:
    """§12 — the recorded delegations, and which of them still authorize.

    The half of `work focus` that answers without a board. That verb prints the
    same rows beside the decision they produced; this one asks only the
    delegation question, so it needs no fresh projection and can be run before
    a cycle to see whether one may start at all. The two verbs share
    `context/delegation.py`, so a grant cannot authorize in one and not the
    other.

    What it will not do, and the reason each is missing rather than pending:

      * it writes nothing. There is no `--approve` and no way to mint a grant
        from here — a delegation is a person's decision, and a verb that could
        record one is a verb that could authorize work on its own.
      * it does not print the store as a table of people. The collection is
        keyed by `grant_ref`; the actor is a field on the record, and this
        prints the delegation, not the delegatee.
      * it does not treat its own output as a decision. `select_focus` returns
        `admission_required: True` and work-engine's atomic admission is what
        admits anything.
    """
    delegation_mod = _load_module_from(REPO_ROOT / "context" / "delegation.py",
                                       "serveros_context_delegation")
    store_mod, control = _control_store()
    # The one place this question is answered from a clock. `--now` is offered
    # rather than hidden because the reader's clock IS part of the answer: a
    # grant that authorizes at one instant and not the next is the whole point
    # of re-checking the delegation on every read.
    raw = getattr(args, "now", "") or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    try:
        stamp = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        sys.stderr.write(
            f"serverosctl: --now {raw!r} is not a timestamp ({exc}). This verb "
            f"judges delegations against a clock; one it cannot read is not a "
            f"clock it may fall back from.\n")
        return EXIT_FAULT
    if stamp.tzinfo is None:
        sys.stderr.write(
            f"serverosctl: --now {raw!r} carries no timezone, so the instant it "
            f"names depends on where the reader is standing.\n")
        return EXIT_FAULT
    now = stamp.astimezone(datetime.timezone.utc).isoformat()

    try:
        recorded, contexts = control.grants(), control.contexts()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    try:
        report = delegation_mod.envelopes(recorded, contexts, now=now)
    except delegation_mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    summary = report["summary"]
    lines = [f"store       {control.path}",
             f"now         {now}",
             f"recorded    {summary['recorded']}"
             + ("" if summary["recorded"] else
                " — nothing has delegated work on this node"),
             f"authorizing {summary['authorizing']} of {summary['recorded']}"
             + (" (the rest are excluded, each with its reason below)"
                if summary["recorded"] else ""),
             f"contexts    {len(contexts)} trusted context(s) held; a grant "
             f"derives from one and cannot outlive it"]
    if summary["states"]:
        lines.append("states      " + ", ".join(
            f"{state} {count}" for state, count in sorted(summary["states"].items())))
    for row in report["rows"]:
        lines.append("")
        lines.append(f"{row['grant_ref']}  {row['state']}"
                     + ("  (in the envelope)" if row["in_envelope"] else ""))
        lines.append(f"  delegated by  {row.get('delegated_by') or '—'}")
        if row.get("revoked") is not None:
            lines.append(f"  revoked       {row['revoked']}"
                         + (f" at {row.get('revoked_at')}"
                            if row.get("revoked_at") else ""))
        for label, field in (("window", "valid_from"), ("until", "expires_at")):
            if row.get(field):
                lines.append(f"  {label:<13} {row[field]}")
        if row.get("projects"):
            lines.append(f"  projects      {', '.join(map(str, row['projects']))}")
        if row.get("items"):
            lines.append(f"  items         {', '.join(map(str, row['items']))}")
        if row.get("actions"):
            lines.append(f"  actions       {', '.join(map(str, row['actions']))}")
        if row.get("budget"):
            lines.append(f"  budget        {json.dumps(row['budget'], sort_keys=True)}")
        lines.append(f"  because       {row['because']}")
    emit({"delegations": report, "store": str(control.path), "now": now},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_admit(args) -> int:
    """§12/P6 — admit one cycle: selection, reservation and intent in one write.

    The verb that makes `select_focus`'s `admission_required: True` doable. It
    adds no decision of its own: `intake/daily_cycle.admission_plan` decides
    what the record is (and refuses, naming why, when the board cannot supply
    one) and `runtime/agent_control_store.admit` is the transaction that commits
    it. Between them there is nothing this function is allowed to invent.

    There is deliberately NO `--grant` here, and its absence is the whole of
    §9.11's fix kept: the grant is read off the selected row and resolved
    through `context/delegation.py`'s envelope, so a grant file cannot make a
    cycle admissible. A verb that could take one would be the hole again, one
    layer up.

    What it records, it does not act on. Nothing leaves this process: the
    dispatch and the lease declaration are the next two phases, and each is
    confirmed by the store that actually performed it.
    """
    state = _focus_decision(args)
    if state is None:
        return EXIT_FAULT
    composed, delegations, decision, now = state
    cycle = _load_module_from(REPO_ROOT / "intake" / "daily_cycle.py",
                              "serveros_intake_daily_cycle")
    plan = cycle.admission_plan(
        decision, composed["rows"], delegations["envelopes"],
        cycle_ref=args.cycle, observed_at=composed["observed_at"] or "",
        action_key=args.action_key,
        # (scope, holder) is what `runtime/lease_store.declare` is idempotent
        # on, so the holder names the cycle: a resume under the same cycle
        # refreshes its own reference instead of declaring beside it.
        lease={"resource": args.resource, "scope": args.scope,
               "holder": args.cycle})
    if plan.get("refused"):
        # An answer, not a fault, for the same reason `work focus` returns OK
        # on STALE_BOARD: on a node with no recorded grant this is the ordinary
        # outcome, and the word and the sentence are what a reader needs.
        lines = [f"refused     {plan['refused']}", f"because     {plan['because']}",
                 "", f"decision    {decision.get('status')}"
                 + (f" ({decision['reason']})" if decision.get("reason") else ""),
                 f"selected    {len(decision.get('selected') or [])}",
                 f"grants      {delegations['summary']['authorizing']} authorizing "
                 f"of {delegations['summary']['recorded']} recorded"]
        emit({"plan": plan, "decision": decision,
              "delegations": delegations["summary"]}, as_json=args.json,
             lines=lines)
        return EXIT_OK

    record = {**plan, "admitted_at": now.isoformat()}
    store_mod, control = _control_store()
    try:
        stored = control.admit(record)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = [
        f"cycle       {stored['cycle_ref']}",
        f"grant       {stored['grant_ref']}",
        f"intent      {stored['intent_digest']}",
        f"selection   {stored['selection_digest']}",
        f"observed    {stored['selection'].get('observed_at') or 'unknown'}",
        f"objectives  {len(stored['selection'].get('selected') or [])}: "
        + ", ".join(stored["selection"].get("selected") or []),
        f"action key  {stored['action_key']}",
        f"lease       {stored['lease']['resource']} in {stored['lease']['scope']}, "
        f"holder {stored['lease']['holder']}",
        f"reservation " + " ".join(f"{k}={stored['reservation'][k]}"
                                   for k in sorted(stored["reservation"])),
        f"phase       {stored['phase']}",
        f"replayed    {'yes — same intent, same admission' if stored.get('replayed') else 'no'}",
        "",
        "recorded, not acted on: the lease is declared and the dispatch made by",
        "whatever performs them, and each reports back through",
        "`serverosctl work confirm <cycle> --phase …`. Until the dispatch phase is",
        "confirmed the action key stays held, which is what stops a second cycle",
        "receiving this one's receipt.",
    ]
    emit({"admission": stored, "store": str(control.path)}, as_json=args.json,
         lines=lines)
    return EXIT_OK


def cmd_work_admissions(args) -> int:
    """§12/P6 — the cycles currently admitted, and what each is holding.

    The read half of `work admit`, and the one place a person can see what is
    occupied rather than what is planned. `work focus` says which objectives the
    next cycle MAY carry; this says which reservations, leases and action keys
    something is holding right now, which is the question that is unanswerable
    from the board.

    It reads the store and nothing else — no board, no clock, no projection —
    for the same reason `work grants` does: what is held is a stored fact, and
    a stale projection must not be able to change the answer.
    """
    store_mod, control = _control_store()
    try:
        held = control.admissions()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    open_now = [a for a in held.values() if not a.get("retired_at")]
    by_phase = {}
    for admission in open_now:
        by_phase[admission.get("phase")] = by_phase.get(admission.get("phase"), 0) + 1
    lines = [f"store       {control.path}",
             f"admissions  {len(open_now)} open of {len(held)} recorded"
             + ("" if held else " — no cycle has been admitted on this node"),
             f"phases      " + (", ".join(f"{p} {n}" for p, n in sorted(by_phase.items()))
                                if by_phase else "— nothing open")]
    for cycle_ref in sorted(held):
        admission = held[cycle_ref]
        retired = admission.get("retired_at")
        lines.append("")
        lines.append(f"{cycle_ref}  {admission.get('phase')}"
                     + (f"  (retired {retired})" if retired else "  (holding)"))
        lines.append(f"  admitted at   {admission.get('admitted_at') or '—'}")
        lines.append(f"  grant         {admission.get('grant_ref')}")
        lines.append(f"  action key    {admission.get('action_key')} — "
                     f"held while this admission is open")
        lines.append(f"  selection     {admission.get('selection_digest')}")
        lines.append(f"  objectives    "
                     + ", ".join(admission.get("selection", {}).get("selected") or []))
        lines.append(f"  reservation   "
                     + json.dumps(admission.get("reservation") or {}, sort_keys=True))
        lease = admission.get("lease") or {}
        lines.append(f"  lease intent  {lease.get('resource')} in {lease.get('scope')}"
                     f", holder {lease.get('holder')}")
        confirmations = admission.get("confirmations") or {}
        for phase in sorted(confirmations):
            # The `at` of a confirmation is NOT in its evidence. The evidence is
            # the other store's answer — what it said — and the instant this
            # store recorded it is a field on the admission (`confirmed_at`,
            # which holds the latest). Printing "at —" beside each phase would
            # invent a per-phase timestamp that was never written.
            evidence = confirmations[phase]
            where = evidence.get("store") or evidence.get("receipt") or "an unnamed store"
            said = ", ".join(f"{k}={v}" for k, v in sorted(evidence.items())
                             if k not in ("store", "receipt"))
            lines.append(f"  confirmed     {phase} from {where}")
            lines.append(f"                {said}")
        if confirmations:
            lines.append(f"  confirmed at  {admission.get('confirmed_at') or '—'}"
                         f"  (the latest of {len(confirmations)})")
        if admission.get("outcome"):
            lines.append(f"  outcome       {admission['outcome']}")
    emit({"admissions": held, "open": len(open_now), "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def _lease_answer(admission: dict) -> dict:
    """What `runtime/lease_store.py` says about this admission's lease intent.

    The next phase after the intent is the lease, and the admission does not
    declare it: `runtime/lease_store.declare` is the only thing that writes a
    reference, and this reads back what it wrote. So the evidence recorded with
    the phase is the OTHER store's answer and not this verb's summary of one.

    Raises `LookupError` when that store does not answer with a live reference
    at the intent's `(scope, holder)`. That is not a soft case: the phase means
    "this node is holding the resource", and a confirm that recorded it on
    anything less would be a lease nobody took.

    `act` and `declared_at` are in the evidence and `expires_at` is not. The
    three look alike and only two are stable: a `declare` by the same holder
    REFRESHES its reference, which moves `expires_at` on every retry of a
    resume and would make the same fact into `different evidence` the second
    time it was reported. `declared_at` is written once, at DECLARE.
    """
    intent = admission["lease"]
    store = lease_store_mod.open_store(now=time.time())
    record = store.get(intent["resource"])
    if record is None:
        raise LookupError(
            f"`runtime/lease_store.py` holds no record for "
            f"{intent['resource']!r}, so nothing declared it. The phase being "
            f"confirmed says this node is holding that resource, and the store "
            f"that would hold the reference says it is not")
    references = [r for r in record.get("references", [])
                  if r.get("scope") == intent["scope"]
                  and r.get("holder") == intent["holder"]]
    if not references:
        # A RELEASED reference arrives here as an ABSENT one, and that was
        # measured rather than assumed: `runtime/lease_store.release` sets
        # `retired_at` and then drops every retired row from `references`
        # before writing, so a `retired_at` read back from this list cannot
        # happen. The `history` list is what tells "was never declared" apart
        # from "was declared and handed back", and the two need different
        # sentences: one is a wrong holder, the other is a cycle claiming a
        # lease this node has already accounted for as free.
        released = [h.get("at") for h in record.get("history", [])
                    if h.get("act") == "RELEASE"
                    and h.get("scope") == intent["scope"]
                    and h.get("holder") == intent["holder"]]
        if released:
            raise LookupError(
                f"{intent['holder']!r} released its reference on "
                f"{intent['resource']!r} at {released[-1]}, so the lease is not "
                f"held. A phase recorded after the release would say the cycle "
                f"is holding a resource the lease store has already accounted "
                f"for as free")
        raise LookupError(
            f"{intent['resource']!r} is declared, and not by "
            f"{intent['holder']!r} in scope {intent['scope']!r}. "
            f"`declare` is idempotent on `(scope, holder)` so a resumed cycle "
            f"refreshes its own reference; this reference does not exist and "
            f"recording the phase would credit the cycle with a lease another "
            f"holder's")
    reference = references[-1]
    acts = [h.get("act") for h in record.get("history", [])
            if h.get("scope") == intent["scope"]
            and h.get("holder") == intent["holder"]]
    if not acts:
        raise LookupError(
            f"the reference {intent['holder']!r} holds on "
            f"{intent['resource']!r} has no history entry, and "
            f"`runtime/lease_store.declare` writes one with every DECLARE and "
            f"REFRESH it makes. A reference with none was not written by that "
            f"helper, so what it means is not established here")
    return {"act": acts[-1], "resource": record["resource"],
            "scope": intent["scope"], "holder": intent["holder"],
            "declared_at": reference.get("declared_at"),
            "store": str(store.root)}


def _dispatch_answer(admission: dict, receipts_root: str) -> dict:
    """What the receipts directory says about this admission's action key.

    `runtime/bounded_invocation.invoke_once` writes one receipt per action key
    and this reads that file. The path is a caller's because the receipts
    directory is where the executing side put it and this process did not
    perform the dispatch — this is the "可恢复幂等关系" and not a distributed
    transaction. What it means is therefore bounded and worth stating: this
    verb records what the receipts directory ANSWERS, and a caller who
    fabricates that file is fabricating evidence in the place evidence lives,
    not through this verb. Three fields are taken and not the whole receipt —
    `action_key`, `status` and `input_digest` are what identify the dispatch,
    and the rest (timestamps, output paths) move as it runs.
    """
    if not receipts_root:
        raise LookupError(
            "confirming the dispatch phase needs `--receipts`, the directory "
            "`runtime/bounded_invocation.invoke_once` was given. There is no "
            "default: the one on this node is chosen by whoever dispatched, and "
            "guessing it would read a receipt from somewhere nobody ran this")
    path = Path(receipts_root) / (admission["action_key"] + ".json")
    if not path.is_file():
        raise LookupError(
            f"{path} does not exist, so no receipt was written for action key "
            f"{admission['action_key']!r}. The phase being confirmed says the "
            f"cycle dispatched, and the file that records a dispatch is not "
            f"there")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(
            f"{path} cannot be read as a receipt ({type(exc).__name__}: {exc}). "
            f"`invoke_once` writes this file itself, so one it cannot read is "
            f"not an answer about this dispatch") from exc
    if not isinstance(receipt, dict):
        raise LookupError(f"{path} is not a receipt")
    return {"action_key": receipt.get("action_key"), "status": receipt.get("status"),
            "input_digest": receipt.get("input_digest"),
            "store": str(Path(receipts_root)), "receipt": str(path)}


def _admission_or_fault(control, store_mod, cycle_ref: str):
    """One admission, or None after writing why to stderr."""
    try:
        admission = control.get_admission(cycle_ref)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return None
    if admission is None:
        sys.stderr.write(
            f"serverosctl: {cycle_ref} has no admission. `work admissions` lists "
            f"the cycles this node has admitted; a phase on a cycle that was "
            f"never admitted would put a lease and a receipt on work with no "
            f"selection behind it\n")
        return None
    return admission


def cmd_work_confirm(args) -> int:
    """§12/P6 — record one other store's answer, and advance one phase.

    `work admit` records the intent; the two things it is an intent ABOUT are
    performed elsewhere — the lease by `runtime/lease_store.declare`, the
    dispatch by `runtime/bounded_invocation.invoke_once` — and this is how each
    reports back. It reads the store that performed the step and records what
    that store answers, so it cannot advance a phase on a caller's word.

    `INTENT_RECORDED` is not a confirmable phase and asking for it is refused:
    `admit` itself wrote it, and a caller reporting this store's own act back to
    this store is not evidence of anything.
    """
    # The phase is checked BEFORE the record, and the order is the message's:
    # "that is not a phase this verb accepts" is a fact about the verb, true
    # whatever the store holds, and answering "no such cycle" first would send
    # a caller to look for an admission when what they got wrong was the verb.
    if args.phase not in ("LEASE_HELD", "DISPATCHED"):
        sys.stderr.write(
            f"serverosctl: {args.phase!r} is not a phase a caller confirms. "
            f"The two are LEASE_HELD and DISPATCHED, in that order, and each "
            f"is a different store's answer\n")
        return EXIT_FAULT
    store_mod, control = _control_store()
    admission = _admission_or_fault(control, store_mod, args.cycle)
    if admission is None:
        return EXIT_FAULT
    try:
        if args.phase == "LEASE_HELD":
            evidence = _lease_answer(admission)
        else:
            evidence = _dispatch_answer(admission, args.receipts)
    except LookupError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    at = getattr(args, "at", "") or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    try:
        confirmed = control.confirm_admission(args.cycle, phase=args.phase,
                                              evidence=evidence, at=at)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = [f"cycle       {confirmed['cycle_ref']}",
             f"phase       {confirmed['phase']}"]
    if confirmed.get("replayed"):
        lines.append("replayed    yes — this phase already carried this same "
                     "evidence, so nothing was written")
    else:
        lines.append(f"confirmed   {args.phase} at {at}")
    for field, value in sorted(evidence.items()):
        lines.append(f"  {field:<11} {value}")
    lines.append("")
    lines.append("What was recorded is what the other store answered. This "
                 "store holds no lease and no receipt; it records which half of "
                 "the cycle is done.")
    emit({"admission": confirmed, "evidence": evidence, "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_retire(args) -> int:
    """§12/P6 — close an admission, releasing the reservation and action key.

    The other end of `work admit`. Until a cycle is retired its action key stays
    held — `admit` refuses a second admission under a key an open one holds,
    because `runtime/bounded_invocation` would replay the first receipt to the
    second — so this is what a finished cycle runs, and it is also what a cycle
    runs when it is abandoned.

    Idempotent, and it keeps the FIRST retirement. A second call that moved the
    instant would move it to whenever somebody last mentioned the cycle, and the
    question a retired admission answers is when the reservation was released.
    It is not a deletion: the record stays, saying what the cycle was admitted
    for, because a review afterwards is computed from exactly that.
    """
    store_mod, control = _control_store()
    try:
        retired = control.retire_admission(args.cycle, at=args.at or
                                           datetime.datetime.now(
                                               datetime.timezone.utc).isoformat(),
                                           outcome=args.outcome or None)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    lines = [f"cycle       {retired['cycle_ref']}",
             f"phase       {retired['phase']}",
             f"retired at  {retired['retired_at']}"]
    if retired.get("outcome"):
        lines.append(f"outcome     {retired['outcome']}")
    lines.append(f"action key  {retired['action_key']} — released, so another "
                 f"admission may hold it now")
    lines.append("")
    lines.append("The record stays. A retired admission is what the cycle did, "
                 "and `work admissions` still lists it.")
    emit({"admission": retired, "store": str(control.path)}, as_json=args.json,
         lines=lines)
    return EXIT_OK


def cmd_work_item(args) -> int:
    """§6 — one Item's chain: which document exists, and where it stops."""
    chain = _workchain_module()
    boards, records, workspace = _workchain_roots(args)
    if boards is None:
        return EXIT_FAULT
    contract = chain.load_contract(
        REPO_ROOT / "intake" / "workchain-contract.yaml")
    projects = chain.load_projects(boards_root=boards)
    item = chain.compose_item(contract=contract, records_root=records,
                              workspace_root=workspace,
                              skeletons=_workchain_skeletons(chain),
                              project=args.project, item_id=args.item,
                              projects=projects)
    if not item["found"]:
        emit(item, as_json=args.json,
             lines=[f"未找到     {args.item} —— {item['why']}"]
             + [f"  {p['project']}  {p['state']}"
                + (f" —— {p['why']}" if p["why"] else "") for p in item["projects"]])
        return EXIT_COULD_NOT_ANSWER
    lines = [f"{item['item']}  {item['state']}" + (f" / {item['outcome']}"
                                                   if item["outcome"] else ""),
             f"标题       {item['title']}",
             f"项目       {item['project']} · 类型 {item['kind']}",
             f"来源优先级 {item['priority']['source']['state']}"
             + (f" —— {item['priority']['source']['why']}"
                if item["priority"]["source"]["why"] else ""),
             f"个人优先级 {item['priority']['personal']['state']}",
             f"Run        {item['runs']} · 制品 {item['deliverables']}", ""]
    for document in item["documents"]:
        mark = {"PRESENT": "●", "MISSING": "×", "NOT_YET_REQUIRED": "○"}.get(
            document["state"], "?")
        # How it resolved and how much it holds, because `design/` with three
        # research notes and `design.md` with one line are not the same answer,
        # and a reader deciding whether a design exists needs both.
        shape = ""
        if document["state"] == "PRESENT":
            shape = ("  ← 目录，共 %d 份" % document["count"]
                     if document["resolved_by"] == "directory" else
                     "  ← %s" % document["resolved_by"])
        lines.append(f"{mark} {document['document']:<12} {document['state']:<17}"
                     + f"{shape or (' ' + document['why'] if document['why'] else '')}")
        check = document.get("skeleton") or {}
        if check.get("state") == "EMPTY":
            lines.append(f"     ↳ 空文件 —— {check['why']}")
        elif check.get("state") == "DIVERGES":
            # Reported, and marked as ordinary. The proposed headings are
            # `PROPOSED_NOT_APPROVED` and no document on this node uses them, so
            # this line appears on nearly every real row and must not read as an
            # alarm — a reader needs to see the document HAS a structure.
            lines.append(f"     ↳ 自有小节 {len(check['own_headings'])} 个："
                         f"{'、'.join(check['own_headings'][:4])}"
                         + ("…" if len(check["own_headings"]) > 4 else "")
                         + f"（提案小节缺 {len(check['missing'])} 个，非缺陷）")
    checks = [d.get("skeleton") or {} for d in item["documents"]]
    empty = sum(1 for c in checks if c.get("state") == "EMPTY")
    diverging = sum(1 for c in checks if c.get("state") == "DIVERGES")
    matching = sum(1 for c in checks if c.get("state") == "MATCHES")
    unchecked = sum(1 for c in checks if c.get("state") == "NOT_CHECKED")
    # Said out loud, because the per-document line appears only for a document
    # with something to report and silence would otherwise be ambiguous.
    lines.append(
        f"形状检查   空文件 {empty} · 符合提案小节 {matching} · 自有小节 "
        f"{diverging}（非缺陷）· 未核对 {unchecked} —— 未核对的是目录形态，"
        f"那是多份文档合起来的一件事，标题检查无从下手")
    lines.append(f"卡片       {item['card']['how']}"
                 + (f" —— {item['card']['resolution_why']}"
                    if item["card"]["resolution_why"] else ""))
    lines.append(f"卡片定位   {item['card']['path']}")
    if item["superseded_by"]:
        lines.append(f"\n被替代     {item['superseded_by']}")
    if item["undecided"]:
        lines += ["", "链条停在这里："]
        for row in item["undecided"]:
            lines.append(f"  {row['document']} —— {row['because']}")
    emit(item, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_workbench(args) -> int:
    """§6 — the START-HERE set, rendered from this node rather than written by hand.

    The renderer lives in `runtime/workbench.py` and is loaded here rather than
    restated: a second implementation of "what the documents say" would be a
    second answer, and the drift between them would be invisible because both
    would look like markdown.

    `check` is the half that matters. A rendered set that nobody re-renders is a
    screenshot; a set with a check is a claim that can go red on its own, which
    is the only kind of claim worth publishing.
    """
    module = _load_module_from(REPO_ROOT / "runtime" / "workbench.py",
                               "serveros_workbench")
    argv = ["--write"] if args.workbench_verb == "render" else ["--check"]
    if args.out:
        argv += ["--out", args.out]
    if args.projects:
        argv += ["--projects", *args.projects]
    if args.project:
        argv += ["--project", args.project]
    if args.item:
        argv += ["--item", args.item]
    if args.quay_run:
        argv += ["--quay-run", args.quay_run]
    if args.quay_bin:
        argv += ["--quay-bin", args.quay_bin]
    if args.ability_repo:
        argv += ["--ability-repo", args.ability_repo]
    if args.reports_dir:
        argv += ["--reports-dir", *args.reports_dir]
    if args.release_root:
        argv += ["--release-root", args.release_root]
    if args.documents:
        argv += ["--documents", *args.documents]
    if args.stdout:
        argv += ["--stdout", args.stdout]
    try:
        return module.main(argv)
    except SystemExit as exc:          # argparse refusals arrive this way
        sys.stderr.write(f"serverosctl: workbench {args.workbench_verb}: {exc}\n")
        return EXIT_COULD_NOT_ANSWER


def cmd_work_skeleton(args) -> int:
    """§8 — print the shape of one document, so a person writes to it.

    This is the consumer the skeleton file needed. A declared shape that only a
    checker reads would tell a person their brief was incomplete without ever
    telling them what complete looked like — the shape would exist to grade
    work rather than to start it. With no `--name`, every declared document is
    listed, which is the answer to "what am I supposed to write".
    """
    chain = _workchain_module()
    skeletons = _workchain_skeletons(chain)
    if skeletons is None:
        sys.stderr.write(
            f"serverosctl: {REPO_ROOT / 'intake' / 'document-skeletons.yaml'} "
            f"is not present, so there is no shape to print. This is a missing "
            f"file and not an empty set of documents.\n")
        return EXIT_FAULT
    documents = skeletons.get("documents") or []
    if not args.name:
        lines = ["本文档形状（intake/document-skeletons.yaml）", ""]
        for entry in documents:
            sections = [s.get("heading") for s in entry.get("sections") or []]
            lines.append(f"{entry.get('id'):<14} {entry.get('file'):<16} "
                         f"→ {entry.get('hop')}")
            lines.append(f"    {entry.get('is')}")
            if sections:
                lines.append(f"    {len(sections)} 节："
                             f"{'、'.join(str(x) for x in sections)}")
            elif entry.get("scopes"):
                # A document family, not a document. Saying "0 节" would read
                # as a shape nobody filled in, when the truth is that this one
                # has no single shape to declare.
                names = "、".join(str(x.get("id")) for x in entry["scopes"])
                lines.append(f"    {len(entry['scopes'])} 个 scope：{names}"
                             f"（每一类的小节各不相同，见 "
                             f"`work skeleton {entry.get('id')}`）")
        lines += ["", "用法：`serverosctl work skeleton <id>` 打印完整形状"]
        emit({"schema": "serveros/document-skeletons.v1",
              "documents": documents}, as_json=args.json, lines=lines)
        return EXIT_OK
    entry = chain.skeleton_of(skeletons, args.name)
    if entry is None:
        known = ", ".join(str(e.get("id")) for e in documents)
        emit({"schema": "serveros/document-skeletons.v1", "id": args.name,
              "found": False, "known": known},
             as_json=args.json,
             lines=[f"未声明     {args.name} —— 没有给这个文档声明形状；"
                    f"已声明的是 {known}"])
        return EXIT_COULD_NOT_ANSWER
    lines = [f"{entry.get('file')}  （{entry.get('hop')}）",
             f"是什么     {entry.get('is')}", ""]
    for section in entry.get("sections") or []:
        lines.append(f"  ## {section.get('heading')}")
        lines.append(f"     {section.get('because')}")
    for scope in entry.get("scopes") or []:
        lines.append("")
        lines.append(f"  [{scope.get('id')}]  {scope.get('is')}")
        observed = [str(x) for x in scope.get("observed_headings") or []]
        if observed:
            lines.append(f"     这些小节是本机上已有的文档在用的，"
                         f"不是本文档提出的：{'、'.join('## ' + x for x in observed)}")
        else:
            lines.append(f"     本机上这一类的文档没有二级小节")
        if scope.get("read_from"):
            lines.append(f"     读自：{scope['read_from']}")
        if scope.get("also_seen"):
            lines.append(f"     还见到过："
                         f"{'、'.join('## ' + str(x) for x in scope['also_seen'])}")
    traceability = entry.get("traceability") or {}
    if traceability:
        lines.append("")
        lines.append(f"  溯源要求：每份文档带一个 `## "
                     f"{traceability.get('heading')}` 小节")
        lines.append(f"     {traceability.get('because')}")
    if entry.get("root"):
        lines.append("")
        lines.append(f"  位置：{entry['root']}/ —— 在 records/ 下、projects/ 旁边，"
                     f"不是某个项目里的一个文件")
    if entry.get("scope"):
        lines.append("")
        lines.append(f"  范围：{entry['scope']}（不是逐 Item 的文档）")
    emit(entry, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_today(args) -> int:
    """§6 — record the 1-2 Items a person is carrying today.

    Authored, and the command says so in its own output. This is the one
    writing command in the group and it writes a SELECTION, not a state: the
    Item's state belongs to work-engine and nothing here touches it.
    """
    chain = _workchain_module()
    if not args.select:
        sys.stderr.write(
            "serverosctl: `work today` records a person's selection and "
            "requires `--select`. Reading today without choosing is `work "
            "board`, which reports whether a selection exists.\n")
        return EXIT_FAULT
    items = [x for x in args.select.split(",") if x]
    if len(items) > 2:
        sys.stderr.write(
            f"serverosctl: {len(items)} Items were named and §6 asks for 1-2. "
            f"A today list that can hold everything is a backlog with a "
            f"different title.\n")
        return EXIT_FAULT
    _, records, workspace = _workchain_roots(args)
    if records is None:
        return EXIT_FAULT
    written = chain.write_today(
        records_root=records, project=args.project, items=items,
        date=args.date or datetime.date.today().isoformat(),
        note=args.note or "", actor=args.actor or "")
    document = written["document"]
    emit({**document, "written_to": written["path"]}, as_json=args.json,
         lines=[f"已记录     {written['path']}", f"日期       {document['date']}",
                f"今日重点   {len(items)} 项：{'、'.join(items)}",
                "这是一个人写下选择，不是状态变更：work-engine 的 Item 状态没有被这里写过"])
    return EXIT_OK


def cmd_work_coverage(args) -> int:
    """§7/§8 — the three maps, each with its denominator and its unknowns."""
    chain = _workchain_module()
    boards, records, workspace = _workchain_roots(args)
    if boards is None:
        return EXIT_FAULT
    contract = chain.load_contract(
        REPO_ROOT / "intake" / "workchain-contract.yaml")
    paths = paths_mod.Paths()

    authored = chain.load_sources(records_root=records, project=args.project)
    disk = chain.disk_coverage(
        paths=paths,
        role_names=sorted(paths_mod.SEMANTIC_ROLES),
        # The refs §8 names as the places this work lands. Named rather than
        # derived from every root, because `state/runtime` and `state/boards`
        # are the two this chain actually reads and a map of all of them would
        # be a map of the root contract rather than of the work.
        refs=("state/boards", "state/requirement-board", "state/workchain",
              "state/serveros", "artifacts/work-items"))
    feishu = chain.feishu_coverage(sources=authored["sources"], contract=contract)
    followed = chain.source_coverage(contract=contract, sources=authored["sources"])
    understanding = chain.understanding_coverage(
        records_root=records, skeletons=_workchain_skeletons(chain))
    payload = {"schema": "serveros/workchain-coverage.v1",
               "project": args.project or None,
               "sources_file": authored["path"],
               "sources_state": authored["state"],
               "sources_why": authored["why"],
               "understanding": understanding,
               "disk": disk, "feishu_spaces": feishu, "followed_sources": followed}
    lines = ["磁盘根与角色"]
    for entry in disk["entries"]:
        place = entry["path"] or entry.get("value_as_composed") or ""
        lines.append(f"  {entry['name']:<34} {entry['kind']:<5} "
                     f"{entry['state']:<11} {place}")
        if entry["why"]:
            lines.append(f"      {entry['why']}")
    entries = disk.get("coverage") or {}
    lines.append(f"  分母：{entries.get('names')} 个名字 · 命中 {entries.get('present')} · "
                 f"缺失 {entries.get('missing')} · 无人回答 {entries.get('unbound')}")
    lines += ["", f"飞书三空间   {feishu['state']} —— 分母：{feishu['denominator']}"]
    for row in feishu["surfaces"]:
        lines.append(f"  {row['surface']:<12} 对象数 {row['objects']} "
                     f"· 已关注 {row['followed']}")
    # The reason is the same for all three surfaces and is stated once here
    # rather than three times beside three identical `None`s — a note repeated
    # per row reads as three separate findings when it is one boundary.
    if feishu["surfaces"]:
        lines.append(f"  注：{feishu['surfaces'][0]['because']}")
    # The row's OWN state and reason, from `source_coverage` — not the
    # authored file's. They differ: a file that reads fine (`READ`) can hold a
    # row with a missing field, and printing the file's verdict beside a row's
    # name would say the row was fine because the FILE was.
    # 理解变化 is not a row under a project: it is four scopes under `records/`.
    # Counted per scope with the traceability rule the layer's own README states
    # — and the count is of documents CARRYING the heading, never a verdict on
    # the ones that do not.
    lines += ["", f"结构化认识   {understanding['state']} —— "
              + (f"分母：{understanding['denominator']} · "
                 if understanding.get("denominator") else "")
              + f"{understanding['root']}"]
    if not understanding["scopes"]:
        lines.append(f"  注：{understanding['why']}")
    traceable_key = "with_" + understanding["traceability_heading"].lower()
    for row in understanding["scopes"]:
        counts = (f"{row['documents']} 份 · 带 "
                  f"## {understanding['traceability_heading']} 的 "
                  f"{row.get(traceable_key, 0)} 份")
        lines.append(f"  {row['scope']:<17} {row['state']:<10} {counts}")
        if row.get("is"):
            lines.append(f"      {row['is']}")
    if understanding["undeclared_scopes"]:
        lines.append(f"  未申报的 scope："
                     f"{'、'.join(understanding['undeclared_scopes'])} —— "
                     f"有人在认识层加了目录而 skeleton 没有申明它")
    lines.append(f"  注：{understanding['traceability_because']}")

    lines += ["", f"关注源       {followed['state']}"
              + (f" —— {followed['why']}" if followed["why"] else "")]
    for row in followed["rows"]:
        lines.append(f"  {row.get('identity') or '(未命名)'}  {row['state']}"
                     + (f" —— {row['why']}" if row["why"] else ""))
        if row.get("missing_fields"):
            lines.append(f"      缺字段 {', '.join(row['missing_fields'])}")
    if followed["rows"]:
        coverage = followed["coverage"]
        lines.append(f"  分母：{coverage['authored']} 条 · 完整 {coverage['complete']} · "
                     f"被拒 {coverage['refused']}")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_understanding(args) -> int:
    """§B2/P3 — which understanding cards a source delta touches, and why.

    `work coverage` counts the understanding layer by DOCUMENT — how many cards
    exist per scope and how many carry a `## Sources` heading. That measures
    whether the layer's rule was written down. This measures whether it is
    true, by resolving each card's own citations against the capture tree and
    comparing the newest position the card cites to the newest position the
    source now holds.

    What it refuses to hand back as one number:

      * one watermark. The card's reach and the source's reach are printed
        beside each other and the lag is the difference of two positions,
        computed only when both are positions. A count is not a position.
      * a claim. This verb reads no message body, so it proposes a range to
        look at and never a statement about what the range says.
      * freshness for a card that cites a source in full. A citation with no
        reach cannot be behind, and calling it current would be the comparison
        nobody made.

    The cards are the operator's own record. This reads them and writes
    nothing.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"card tree read relative to whatever directory the caller stood in "
            f"is a lag nobody else can reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                               "serveros_intake_capture_coverage")
    delta = _load_module_from(REPO_ROOT / "intake" / "understanding_delta.py",
                              "serveros_intake_understanding_delta")
    try:
        # The capture objects are read here and passed in rather than recomputed
        # inside the other module: two readers of one tree drift, and the one
        # that drifts is the one nobody feeds.
        objects = capture.read_root(records)["objects"]
        report = delta.read_and_reconcile(records, objects)
    except (capture.Refused, delta.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    summary = report["summary"]
    lines = [
        f"root        {report['root']}",
        f"card        {summary['denominator']} 份  ·  "
        + " · ".join(f"{name} {count}"
                     for name, count in sorted(summary["by_state"].items())),
        "",
    ]
    for card in report["cards"]:
        verdict = card["judged"]
        heading = card["card"].split("/", 3)[-1]
        lines.append(f"{heading}  [{card['sources_marker']}]  {verdict['state']}")
        lines.append(f"    {verdict['why']}")
        if verdict.get("partial"):
            # Both kinds of unchecked citation are named, because they are
            # different answers to different questions: one named no reach, the
            # other named a source that is not on this node. A partial line
            # reporting only the first would read as though the rest of the card
            # had been compared.
            unchecked = []
            if verdict["unmeasured"]:
                unchecked.append(f"{verdict['unmeasured']} 条没有可比的位置")
            if verdict.get("unresolved"):
                unchecked.append(f"{verdict['unresolved']} 条没有对应的源")
            lines.append(f"    部分：{verdict['measured']} 条引文可比、"
                         + "、".join(unchecked))
        for row in card["measurements"]:
            lines.append("    · " + _measurement_line(row))
        for row in verdict["review_ranges"]:
            lines.append("    → " + _review_line(row))
    emit(report, as_json=args.json, lines=lines)
    return EXIT_OK


def _measurement_line(row: dict) -> str:
    """One citation's measurement, with the two watermarks kept apart."""
    kind = row["kind"]
    source = row["source"].rsplit("/", 2)[-2:]
    where = "/".join(source)
    if row.get("via_glob"):
        where += "  ← glob"
    if kind == "POSITION":
        positions = row["source_positions"]
        return (f"{where}  卡 {row['applied']} / 源 {positions['max']}"
                f"（{positions['count']} 条）  落后 {row['lag']}"
                + (f"  · 已读区间内 改 {row['revisions_in_range']['edited']} "
                   f"删 {row['revisions_in_range']['deleted']}"
                   if row["revisions_in_range"]["edited"]
                   or row["revisions_in_range"]["deleted"] else ""))
    if kind == "DIGEST":
        agreed = row.get("lag") == 0
        return (f"{where}  摘要 {row['cited_digest']} 与 manifest "
                f"{'一致' if agreed else '不一致'}"
                + (f" —— {row['digest_why']}" if row.get("digest_why") else ""))
    if kind == "WHOLE_FILE":
        return (f"{where}  整份引用，没有位置"
                + (f" —— {row['unresolved_why']}" if row.get("unresolved_why") else ""))
    return f"{where}  {row.get('unresolved_why', '')}"


def _review_line(row: dict) -> str:
    """One range to look at. A range, never a claim."""
    what = {"BEYOND_APPLIED": "需要往下读",
            "REVISITED_IN_PLACE": "已读区间被改动过",
            "WHOLE_DOCUMENT": "文档已换版"}[row["kind"]]
    if row["kind"] == "BEYOND_APPLIED":
        span = f"位置 {row['to_position']}"
    elif row["kind"] == "REVISITED_IN_PLACE":
        span = f"位置 {row['from_position']}–{row['to_position']}"
    else:
        # No positions to print: a document has none, and printing one would be
        # the reader naming a span it never measured.
        span = (f"摘要 {row['cited_digest']} → {row['content_sha256']}"
                if row.get("content_sha256") else "整份重读")
    detail = (f"  改 {row['revisions']['edited']} 删 {row['revisions']['deleted']}"
              if row.get("revisions") else
              f"  最新 {row['latest_create_time']}" if row.get("latest_create_time")
              else "")
    return f"{row['card'].split('/', 3)[-1]}  {what}  {span}{detail}"


def cmd_work_understanding_patch(args) -> int:
    """§B2/P3 — the write half: a proposal a person confirms, never a card rewritten.

    `work understanding` answers which cards a source delta touches. This turns
    that answer into the patch an operator acts on, and it is deliberately the
    half that cannot decide anything:

      * confirmation is authored input. `--decisions` is read and never written,
        so with no decisions file every item is AWAITING — there is no path from
        a derived record to a confirmation on this tool's own authority.
      * an item is identified by the exact range it proposes. A source that
        moves again makes a different item, so a superseded confirmation does
        not follow the card; it is reported in `stale_decisions` instead.
      * no card is edited. The patch is one new file, written atomically to an
        absolute path the caller names.

    `--verify` recomputes the patch from the records and compares it field by
    field: a proposal added by hand, one that should have expired and did not,
    or one whose version pair drifted all come back by name.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"patch computed against a card tree read relative to whatever "
            f"directory the caller stood in is a proposal nobody else can "
            f"reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                                "serveros_intake_capture_coverage")
    delta = _load_module_from(REPO_ROOT / "intake" / "understanding_delta.py",
                              "serveros_intake_understanding_delta")
    patch_mod = _load_module_from(REPO_ROOT / "intake" / "understanding_patch.py",
                                  "serveros_intake_understanding_patch")
    try:
        # Read here and passed in, for the same reason `work understanding` does
        # it: two readers of one tree drift, and the writer must never acquire
        # its own opinion about what the cards say.
        objects = capture.read_root(records)["objects"]
        report = delta.read_and_reconcile(records, objects)
        decisions = patch_mod.read_decisions(args.decisions) if args.decisions else {}
    except (capture.Refused, delta.Refused, patch_mod.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    decisions_path = str(args.decisions) if args.decisions else None
    if args.verify:
        reasons = patch_mod.verify(report, decisions, decisions_path, args.verify)
        payload = {"schema": patch_mod.SCHEMA, "ok": not reasons,
                   "verified": str(args.verify), "reasons": reasons}
        emit(payload, as_json=args.json, lines=([f"verify      {args.verify}  ok"]
               if not reasons else
               [f"verify      {args.verify}  {len(reasons)} 处不一致"] +
               [f"  ! {reason}" for reason in reasons]))
        return EXIT_OK if not reasons else EXIT_FAULT

    patch = patch_mod.build(report, decisions, decisions_path)
    if args.write:
        # Absolute, and refused rather than guessed: see the module's `write`.
        try:
            patch_mod.write(patch, args.write)
        except patch_mod.Refused as exc:
            sys.stderr.write(f"serverosctl: {exc}\n")
            return EXIT_FAULT
    lines = patch_mod.render(patch)
    if args.write:
        lines.append(f"    written     {args.write}")
    emit(patch, as_json=args.json, lines=lines)
    return EXIT_OK


def _reading_current(semantic, report: dict, reading: dict) -> dict:
    """What the span a reading is about measures at, now — from the reader's report.

    Not recomputed here. The reader already decided which ranges each card is
    behind on, and taking a second opinion about that would be a second reader of
    one tree. A reading whose span the reader no longer proposes is refused by
    name rather than measured against something else: the range is gone because
    the card moved past it, and interpreting a span that is no longer the reason
    to read anything is how a stale reading gets committed.
    """
    source = reading["source"]
    card_row = next((card for card in report["cards"]
                     if card.get("card") == source.get("card")), None)
    if card_row is None:
        raise semantic.Refused(
            f"the reader reports no card at {source.get('card')!r}: the reading "
            f"is about a card that is not in the understanding layer")
    if "card_digest" not in card_row:
        raise semantic.Refused(
            f"{source.get('card')!r} carries no card digest, so the reading "
            f"cannot be bound to the revision it read")
    matching = [row for row in (card_row.get("judged") or {}).get("review_ranges", [])
                if row.get("manifest") == source.get("manifest")
                and row.get("from_position") == source.get("from_position")
                and row.get("to_position") == source.get("to_position")]
    if not matching:
        proposed = ", ".join(
            f"{row.get('manifest')} {row.get('from_position')}–"
            f"{row.get('to_position')}"
            for row in (card_row.get("judged") or {}).get("review_ranges", []))
        raise semantic.Refused(
            f"the reader no longer proposes {source.get('manifest')} "
            f"{source.get('from_position')}–{source.get('to_position')} for "
            f"{source.get('card')!r}"
            + (f"; it now proposes {proposed}" if proposed else
               "; it proposes no range for this card at all")
            + ". Read the current proposal rather than applying an "
              "interpretation of a span nothing is asking about")
    row = matching[0]
    return {"card_digest": card_row["card_digest"],
            "content_identity": row.get("content_identity"),
            "from_position": row.get("from_position"),
            "to_position": row.get("to_position")}


def cmd_work_understanding_semantic(args) -> int:
    """§B2 — the semantic step: a native Agent's reading becomes understanding.

    The three verbs are three different things and this is the third:

      * `work understanding` reads no message body and says which cards a source
        delta touches;
      * `work understanding-patch` turns those ranges into a proposal a person
        confirms, and edits nothing;
      * this one takes a READING — what an authorised native Agent found when it
        read the fixed-version material — and commits the part of it that may be
        committed without a person, leaving the rest as candidates.

    It cannot read a body itself, and that is deliberate rather than unfinished:
    `--reading` is authored input in the same sense `--decisions` is, and the
    absence of a model client here is what keeps the two from being the same
    author. Three things gate the commit and none of them is a field the reading
    writes: the source revision and understanding baseline it was made against,
    the standing delegation `--delegation` supplies, and what each claim touches.

    `--accepted` is the fourth thing and the only one a PERSON writes: what a
    human said yes to, identified down to both digests and the reading's own
    digest. It is what makes `user_confirmed` reachable at all — without it a
    claim touching a major change is prepared and never committed — and it is
    also why a confirmation cannot be inherited: an acceptance made about this
    span at the text that was there before somebody edited the card matches
    nothing, and the resolution says so instead of quietly doing nothing.

    Without `--commit` nothing is written and you get the resolution. With it,
    the write goes into each owning card's own fence — the prose around it is
    preserved byte for byte — and a claim whose view could not be updated comes
    back as PARTIAL rather than as a commit that finished. A commit whose every
    claim still awaits a person writes nothing and leaves no receipt: the
    proposal stays open, which is what lets the acceptance arriving afterwards be
    the thing that commits it.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"reading validated against a card tree read relative to whatever "
            f"directory the caller stood in is a commit nobody else can "
            f"reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                               "serveros_intake_capture_coverage")
    delta = _load_module_from(REPO_ROOT / "intake" / "understanding_delta.py",
                              "serveros_intake_understanding_delta")
    semantic = _load_module_from(
        REPO_ROOT / "intake" / "understanding_semantic.py",
        "serveros_intake_understanding_semantic")

    delegation = None
    if args.delegation:
        try:
            delegation = json.loads(Path(args.delegation).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            sys.stderr.write(
                f"serverosctl: cannot read --delegation {args.delegation}: {exc}. "
                f"A grant that could not be read is not an absent one, and "
                f"treating it as absent would silently turn every claim into a "
                f"candidate.\n")
            return EXIT_FAULT
    accepted = None
    if args.accepted:
        try:
            accepted = semantic.read_acceptances(args.accepted)
        except (OSError, semantic.Refused) as exc:
            sys.stderr.write(
                f"serverosctl: cannot read --accepted {args.accepted}: {exc}. A "
                f"confirmation that could not be read is not an absent one, and "
                f"treating it as absent would either drop a person's decision "
                f"silently or commit the claims it names as the machine's own.\n")
            return EXIT_FAULT
    if args.commit and not args.committed_at:
        sys.stderr.write(
            "serverosctl: --commit needs --committed-at. A receipt stamped from "
            "this process's clock cannot be compared with one from another "
            "machine, and the comparison is what makes a replay detectable.\n")
        return EXIT_FAULT

    try:
        objects = capture.read_root(records)["objects"]
        report = delta.read_and_reconcile(records, objects)
        document = semantic.read_reading(args.reading)

        def measure():
            return _reading_current(semantic, report, document)
    except (capture.Refused, delta.Refused, semantic.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    if not args.commit:
        try:
            resolution = semantic.resolve(document, measure(), delegation,
                                          accepted)
        except semantic.Refused as exc:
            sys.stderr.write(f"serverosctl: {exc}\n")
            return EXIT_FAULT
        emit(resolution, as_json=args.json,
             lines=semantic.render_resolution(resolution))
        return EXIT_OK

    try:
        receipt = semantic.apply(args.reading, records, measure=measure,
                                 delegation=delegation, accepted=accepted,
                                 committed_at=args.committed_at)
    except semantic.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(receipt, as_json=args.json, lines=semantic.render_receipt(receipt))
    return EXIT_OK


def cmd_work_claim_recall(args) -> int:
    """A2 — what may still be recalled, and for the rest, why not.

    The verb answers one question about the understanding layer: given what the
    durable record says was written and what the sources say now, may this claim
    still be used as current? It writes nothing — `intake/claim_recall.py` has no
    write path at all — and it consults no provider, which the output states
    rather than implies.

    It exits 0 on any answer it could produce, including an answer that a person
    has work to do. An exit code that conflated "I answered" with "the answer was
    unpleasant" would make `CURRENT` and `STALE` indistinguishable to a shell,
    and `STALE` is the ordinary consequence of somebody editing a source. The
    answer is in `counts` and in each claim's `recallable`; a caller that wants a
    gate reads `--json`.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. An "
            f"index built from a tree read relative to whatever directory the "
            f"caller stood in is one nobody else can rebuild, and the receipts "
            f"it finds would be whichever ones happened to be under that "
            f"directory.\n")
        return EXIT_FAULT
    recall_mod = _load_module_from(REPO_ROOT / "intake" / "claim_recall.py",
                                   "serveros_intake_claim_recall")
    try:
        document = recall_mod.recall(records, only=list(args.claim or []))
    except recall_mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(document, as_json=args.json, lines=recall_mod.render_recall(document))
    return EXIT_OK


def cmd_work_waterline(args) -> int:
    """§B2/§6 — the two waterlines per source, with the gaps named.

    `work understanding` answers, per CARD, which sources a card is behind on.
    This answers, per SOURCE, how far the capture got and how far understanding
    got, and it reports the two facts a per-card lag cannot carry: a hole in the
    capture's own positions, and an extent that is where the collector stopped
    rather than where the source ended.

    任何中间 gap 都不能被最大位置掩盖 — so nothing here is a maximum. Both
    waterlines are runs, `lag` is the set difference between them, and a source
    whose every captured position is understood but which is missing positions
    in the middle comes back GAPPED with an empty lag rather than UP_TO_DATE.
    That is the answer a scalar implementation cannot produce, and it is the
    reason this verb exists rather than a `lag` integer beside the card's.

    It writes nothing. The raw half is already committed by the collectors and
    the understanding half is what the cards say; a verb that wrote here would
    be a second author for both.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. "
            f"Positions measured against a tree read relative to whatever "
            f"directory the caller stood in are positions nobody else can "
            f"reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                               "serveros_intake_capture_coverage")
    delta = _load_module_from(REPO_ROOT / "intake" / "understanding_delta.py",
                              "serveros_intake_understanding_delta")
    waterline = _load_module_from(REPO_ROOT / "intake" / "source_waterline.py",
                                  "serveros_intake_source_waterline")
    try:
        objects = capture.read_root(records)["objects"]
        document = waterline.waterlines(records, delta, objects)
    except (capture.Refused, delta.Refused, waterline.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(document, as_json=args.json, lines=waterline.render(document))
    return EXIT_OK


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------

def _network_module():
    return _load_module_from(REPO_ROOT / "runtime" / "network_mutation.py",
                             "serveros_runtime_network_mutation")


def _network_levels() -> tuple:
    """The disclosure levels, from the module that declares them.

    Written out here instead, this list is a second vocabulary that agrees with
    `disclosure-contract.yaml` until one of the two moves. The module name is
    the one `network_mutation` loads it under, so `main()` and this agree on a
    single module object.
    """
    return _load_module_from(REPO_ROOT / "runtime" / "network_policy.py",
                             "serveros_runtime_network_policy").LEVELS


def _network_argv(args) -> list[str]:
    argv = [args.subcommand, "--request", args.request]
    if getattr(args, "policy", None):
        argv += ["--policy", args.policy]
    if getattr(args, "context", None):
        argv += ["--context", args.context]
    if getattr(args, "registry", None):
        # Forwarded because it was silently dropped before: `common` declares
        # `--registry` for every subcommand, `network_mutation.run` takes it,
        # and nothing carried it across. `serverosctl --registry X network
        # preflight` therefore read THIS node's registry while reading as
        # though it had been pointed elsewhere.
        argv += ["--registry", args.registry]
    if getattr(args, "level", None):
        argv += ["--level", args.level]
    if getattr(args, "execute", False):
        argv.append("--execute")
    return argv


def cmd_network(args) -> int:
    """The one subcommand here that can change the host.

    It delegates the whole argv to `network_mutation.run` rather than
    re-deriving the request, the policy and the level in this file. The other
    arrangement — parse here, act there — makes two places that know what
    `--policy` means, and the second one is the one that goes stale.

    The exit code is that module's vocabulary and not this CLI's: 0 allowed,
    10 refused with a code and a repair, 11 nothing refused it and nobody may
    take it on their own authority, 2 the request was not a candidate at all.
    `network_mutation` documents why those four are not three.
    """
    return _network_module().run(_network_argv(args))


# ---------------------------------------------------------------------------
# ownership
# ---------------------------------------------------------------------------

def _ownership_module():
    return _load_module_from(REPO_ROOT / "runtime" / "ownership_gate.py",
                             "serveros_runtime_ownership_gate")


def _ownership_levels() -> tuple:
    """Same reasoning as `_network_levels`: read from the module that declares
    them, so `main()` and the gate cannot disagree about the ladder."""
    return _ownership_module().LEVELS


def cmd_ownership(args) -> int:
    """The ownership gate's verb. Read-only, and there is no `--execute`.

    `ownership check` answers one question — may this subject be acted on —
    and the answer is one of three values. It is the same module
    `network_mutation` calls before every creation, so the verdict a person
    reads here is the verdict that gate receives, not a second opinion.

    Set `SERVEROS_STATE_DIR` to read a registry other than this node's; the
    path is `registry.default_path()`'s and is not composed here.
    """
    argv = ["check", "--kind", args.kind, "--name", args.name]
    if getattr(args, "action", None):
        argv += ["--action", args.action]
    if getattr(args, "tier", None):
        argv += ["--tier", args.tier]
    if getattr(args, "context", None):
        argv += ["--context", args.context]
    if getattr(args, "registry", None):
        argv += ["--registry", args.registry]
    if getattr(args, "level", None):
        argv += ["--level", args.level]
    if getattr(args, "json", False):
        argv.append("--json")
    try:
        return _ownership_module().run(argv)
    except ValueError as exc:
        # A backstop and not the path: `_cmd_check` catches `Rejected` itself —
        # an action it cannot weigh is exit 2 there, the way `network_mutation`
        # reports a malformed request. This catches anything that still escapes
        # as a `ValueError`, and 2 says the same thing: the question could not
        # be put, so no verdict about the subject exists to report.
        sys.stderr.write(f"ownership: {exc}\n")
        return 2


def _mutation_module():
    return _load_module_from(REPO_ROOT / "runtime" / "managed_mutation.py",
                             "serveros_runtime_managed_mutation")


def cmd_mutation(args) -> int:
    """The managed path's verb. `plan` writes nothing; `run --execute` does.

    The exit codes are the module's own policy and not a mapping written again
    here — `exit_code()` exists in each of them for exactly this reason, and a
    second translation table is how two verbs come to mean different things by
    the same number.
    """
    argv = [getattr(args, "mutation_verb", "plan")]
    argv += ["--request", args.request]
    if getattr(args, "context", None):
        argv += ["--context", args.context]
    if getattr(args, "level", None):
        argv += ["--level", args.level]
    if getattr(args, "execute", False):
        argv.append("--execute")
    try:
        return _mutation_module().run(argv)
    except ValueError as exc:
        # The same backstop as `cmd_ownership`'s, saying the same thing: a
        # request that cannot be turned into a plan is exit 2, and no verdict
        # about a subject exists to report.
        sys.stderr.write(f"mutation: {exc}\n")
        return 2


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def cmd_taxonomy(args) -> int:
    """The declared ability navigation DAG (§G).

    Two things here look like "the ability tree" and answer different
    questions. `ability tree` reports PLACEMENT: where abilities actually are,
    derived from the catalogue's `domain.primary`, and in that derivation an
    internal node exists only because some ability's path runs through it.
    This reports the DECLARED NAVIGATION DAG: which families exist, which are
    occupied, and which are declared and empty.

    That second question had no answer before this command. A family with
    nothing in it yet — Safety & Compliance is the live example, six real
    enforcement surfaces and no ability — was, under placement alone, not a
    node at all, so "this fabric has no safety concept" and "the concept is
    declared and unoccupied" rendered identically.

    The delegation is deliberate: `abilities/taxonomy.py` has a `main()` its
    own tests call directly, and this forwards to it rather than reimplementing
    the rendering, so the two entry points cannot drift apart.
    """
    taxonomy_mod = _load_module_from(REPO_ROOT / "abilities" / "taxonomy.py",
                                     "serveros_abilities_taxonomy")
    forwarded: list[str] = []
    if args.repository:
        forwarded += ["--repository", args.repository]
    if args.revision:
        forwarded += ["--revision", args.revision]
    if getattr(args, "catalogue", None):
        forwarded += ["--catalogue", args.catalogue]
    if args.json:
        forwarded += ["--json"]
    forwarded.append(args.subcommand)
    for extra in (getattr(args, "taxon", None), getattr(args, "query", None)):
        if extra:
            forwarded.append(extra)
    try:
        return taxonomy_mod.main(forwarded)
    except taxonomy_mod.TaxonomyError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER


def cmd_workspace(args) -> int:
    """workspace-management: placement and governance (§I).

    Six questions, all answered from the path contract rather than from a
    directory listing: overview, reconcile, drift, locate, inspect, policy.

    Every one of them except `policy` refuses to answer when this process did
    not resolve this node's roots. The refusal is not defensive coding. The
    fallback root is the first node's — Beijing's — so an unconfigured process
    resolves `workspace` to a path on another machine, and both `locate`
    (which would then recommend writing there) and `overview` (which would
    report it as simply absent) look exactly like correct answers. The
    reasoning is in `require_bound`, stated once, where it can be disagreed
    with.

    `policy` stays readable, with its paths withheld: the rules are the same
    on every node and an agent that cannot read them cannot place anything.

    Delegation is deliberate, as with `taxonomy`: `runtime/workspace.py` has a
    `main()` its own tests call, and reimplementing the rendering here would
    give the two entry points a way to drift.
    """
    workspace_mod = _load_module_from(REPO_ROOT / "runtime" / "workspace.py",
                                      "serveros_runtime_workspace")
    forwarded: list[str] = []
    if args.json:
        forwarded.append("--json")
    forwarded.append(args.subcommand)
    if getattr(args, "dirty_detail", False):
        forwarded.append("--dirty-detail")
    for extra in (getattr(args, "class_name", None),
                  getattr(args, "path", None),
                  getattr(args, "name", None)):
        if extra:
            forwarded.append(extra)
    try:
        return workspace_mod.main(forwarded)
    except workspace_mod.WorkspaceError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER


# ---------------------------------------------------------------------------
# architecture
# ---------------------------------------------------------------------------

# The two checkers live beside the skill that owns them, not under `runtime/`,
# because they are §15/§31 gates over the WHOLE tree rather than node-runtime
# behaviour. They have been runnable by path all along and were named to
# readers as `serverosctl architecture project` in two places — a comment
# `map_projection.py` emits into every map it cannot anchor, and
# `architecture_check.py`'s `refresh_via` on a MAP_VIEW_UNBOUND finding.
#
# That named verb did not exist. Nothing caught it, and the reason is worth
# recording, because it is the same shape this repository keeps meeting: the
# gate that checks declared commands (`tests/test_declared_commands.py`) scans
# *.yaml, skills/**/*.md, docs/*.md and workbench/*.md, and its own comment
# says why the set is hand-listed. A command a reader is told to run is
# usually a line in a document. Here it was a string emitted from a `.py`, and
# a `.py` is not in the tuple — so the declaration was invisible to the scan
# and the scan was, by its own standard, indistinguishable from a clean one.
#
# These wrappers are the missing verb, and they are deliberately the thinnest
# thing that could be: the scripts already have their own argv surface, their
# own root resolution (this node's host profile, with no flag needed — measured
# 2026-09-22) and their own exit codes. `architecture_check.py`'s own design
# rule is "no second parser", so this adds none: it forwards the tail verbatim
# and returns the checker's exit code.

_ARCHITECTURE_SCRIPTS = {
    "check": ("architecture_check.py", "serveros_architecture_check"),
    "project": ("map_projection.py", "serveros_map_projection"),
}


def _architecture_checker(verb: str):
    """The checker module for `verb`, or None with the reason written to stderr.

    A wrapper whose target is absent must say so. Returning 0 here would report
    "nothing found to fix" for a revision that has nothing to run — the exact
    conflation of an empty answer with a clean one that both checkers spend
    their docstrings refusing.
    """
    filename, module_name = _ARCHITECTURE_SCRIPTS[verb]
    path = REPO_ROOT / "skills" / "serveros" / "scripts" / filename
    if not path.is_file():
        sys.stderr.write(
            f"serverosctl: architecture {verb}: {path} is not in this "
            f"revision. This verb is a wrapper over the checker that ships "
            f"beside the serveros skill; a wrapper whose target is absent "
            f"answers nothing rather than reporting a clean tree.\n")
        return None
    return _load_module_from(path, module_name)


def _cmd_architecture(args, verb: str) -> int:
    module = _architecture_checker(verb)
    if module is None:
        return EXIT_COULD_NOT_ANSWER
    forwarded: list[str] = []
    # REMAINDER means a flag typed after the verb reaches the checker rather
    # than this parser, which is what keeps the checker's `--help` and its
    # `--json` its own. The global form still works, and it is merged here
    # rather than in the parser so that the two positions cannot both claim it.
    if getattr(args, "json", False):
        forwarded.append("--json")
    forwarded.extend(getattr(args, "checker_argv", None) or [])
    return module.main(forwarded)


def cmd_architecture_check(args) -> int:
    """§31–§42 conformance over the live tree; reports, never repairs."""
    return _cmd_architecture(args, "check")


def cmd_architecture_project(args) -> int:
    """§15 the machine-owned region of each human map, regenerated as a candidate."""
    return _cmd_architecture(args, "project")


def build_parser() -> argparse.ArgumentParser:
    # There used to be a second `ArgumentParser(prog="serverosctl", ...)` right
    # here, immediately overwritten by the one below. Its `description` was the
    # one a reader edits — `python3 -c "import cli; print(cli.build_parser()
    # .description)"` prints the OTHER one — so a change made there took effect
    # in the source and not in `--help`. Found by making exactly that change.
    # One construction now, and it is the live one.

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
        # This said "Read-only: no subcommand here stops, removes or restarts
        # anything" until `network create` existed. The sentence was true when
        # it was written and the behaviour changed underneath it, which is the
        # failure mode this repository keeps recording.
        description="ServerOS reference-node control CLI. Nothing here stops, "
                    "removes or restarts anything; `network create` is the one "
                    "subcommand that creates, it is gated by a preflight that "
                    "runs on the same invocation, and it performs nothing "
                    "without `--execute`.",
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
    device_agents.add_argument(
        "--device", help="a device ref (default: the device this node is bound "
                         "to). A device that is not this node is reported with "
                         "its authored binding and an UNKNOWN observation.")
    device_agents.add_argument(
        "--probe", action="store_true",
        help="also run each runtime's own `--version` and report what came "
             "back. Opt-in: without it this command reads files it already has "
             "and asks nothing.")
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

    # SYS-02. `lease` and not `resource`: the resource group is about what is on
    # the host, and this is about who wants it. Putting the two under one verb
    # would make "browser=clone-per-run" look like a fact about the browser.
    lease = sub.add_parser(
        "lease", help="dynamic environment: who wants a resource, and may it go (§SYS-02)",
        **_COMMON)
    lease_sub = lease.add_subparsers(dest="subcommand", required=True)

    def _lease_parser(name: str, help_text: str):
        parser = lease_sub.add_parser(name, help=help_text, parents=[common])
        # The default is the root contract's, and the override exists for the
        # same reason `--registry` does: an isolated run needs a namespace of
        # its own, and the alternative — tests writing into the node's live
        # `state/serveros/resource-leases/` — is a test that changes what it
        # measures. It does not make the composed path legitimate: `paths.py`
        # is still the only thing that says where this namespace IS.
        #
        # It means the RUNTIME state dir, exactly what `serverosd --state-dir`
        # takes, and NOT the namespace root. Both verbs of a read/write pair
        # take the same value and reach the same directory through
        # `lease_store.namespace_root`; spelling this as the namespace root
        # is how the two verbs came to address different directories while
        # both reporting success.
        parser.add_argument(
            "--state-dir", default=None,
            help="the runtime state dir, e.g. <data>/state/serveros/runtime — "
                 "the same value serverosd takes. The resource-leases "
                 "namespace is its sibling. Default: this node's root contract. "
                 "For an isolated run, not for a durable one.")
        return parser

    l_list = _lease_parser("list", "every resource with something recorded")
    l_list.set_defaults(func=cmd_lease_list)
    l_inspect = _lease_parser("inspect", "one resource's references and release verdict")
    l_inspect.add_argument("resource", help="e.g. browser:chromium")
    l_inspect.set_defaults(func=cmd_lease_inspect)
    l_env = _lease_parser(
        "environment",
        "the §6 equation over what is recorded: desired + observed + "
        "ownership/policy + context. Read-only.")
    l_env.set_defaults(func=cmd_lease_environment)
    l_declare = _lease_parser("declare", "this holder needs this resource")
    l_declare.add_argument("resource")
    l_declare.add_argument("--scope", required=True)
    l_declare.add_argument("--holder", required=True,
                           help="who holds it — a conversation, a session, a Run")
    l_declare.add_argument("--sharing", required=True)
    l_declare.add_argument("--ttl", type=float, default=None,
                           help="seconds until this reference stops being backed "
                                "by its holder. Omitted means it lasts until the "
                                "holder retires it, which is a declaration and "
                                "not a missing value.")
    l_declare.add_argument("--note", default="")
    l_declare.set_defaults(func=cmd_lease_declare)
    l_release = _lease_parser("release", "retire one reference and get the verdict")
    l_release.add_argument("resource")
    l_release.add_argument("--scope", required=True)
    l_release.add_argument("--holder", required=True)
    l_release.add_argument("--owner-class", choices=LEASE_OWNER_CLASSES, default="MANAGED",
                           help="from decisive evidence. UNKNOWN and EXTERNAL both "
                                "refuse the release; the default is the one that "
                                "does not require the caller to have looked.")
    protection = l_release.add_mutually_exclusive_group()
    protection.add_argument(
        "--protected", action="store_true", default=None,
        help="protection.resolve said True")
    protection.add_argument(
        "--not-protected", action="store_true", default=False,
        help="protection.resolve said False, i.e. a declaration was available and "
             "named nothing. Omitted means protection is UNDECLARED, which is a "
             "third value and not the same as unprotected.")
    l_release.set_defaults(func=cmd_lease_release)

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
    ab_tree = ability_sub.add_parser(
        "tree",
        help="the logical ability tree this node derives from `domain.primary` (§4)",
        **_COMMON)
    ab_tree.set_defaults(func=cmd_ability_tree)
    ab_inspect = ability_sub.add_parser(
        "inspect",
        help="one ability's projection row and its place in the tree (§6)",
        **_COMMON)
    ab_inspect.add_argument("name")
    ab_inspect.set_defaults(func=cmd_ability_inspect)
    ab_resolve = ability_sub.add_parser(
        "resolve",
        help="intersect the eight inputs for one device and runtime (§45)",
        **_COMMON)
    ab_resolve.add_argument("--request", required=True,
                            help="a JSON file holding the resolver's inputs")
    ab_resolve.add_argument(
        "--ability",
        help=("override request.ability. An operation may be named on it — "
              "`ability:<id>#<verb>` — and MUST be quoted, because `#` starts "
              "a shell comment: --ability 'ability:test-management#read'. The "
              "bare form names no operation and does not consult the provider's "
              "operations table."))
    ab_resolve.add_argument("--device", help="override request.device_ref")
    ab_resolve.add_argument("--runtime", help="override request.runtime")
    # input 1, named the two ways `ability pack` names a catalogue. Neither has
    # a default: the local projection is what this NODE has observed, and it
    # answers "is this installed here" — which is `device_binding`, input 4.
    # Reading it as the catalogue would make input 1 and input 4 the same
    # question, and would report every ability the authority lists but this
    # device lacks as absent from a catalogue nobody read.
    ab_resolve.add_argument(
        "--catalogue", type=Path,
        help=("a file of ability names, one per line, `#` for comments. Sets "
              "request.abilities. Mutually usable with --ability-repo/--ability-rev, "
              "which take precedence when both are given."))
    ab_resolve.add_argument(
        "--ability-repo", type=Path,
        help=("the canonical ability-management checkout to read the catalogue "
              "from. Needs --ability-rev."))
    ab_resolve.add_argument(
        "--ability-rev",
        help=("the pinned revision the manifest is read at; a branch or short "
              "SHA is resolved to the full SHA, and that resolved SHA is what "
              "the answer names."))
    ab_resolve.set_defaults(func=cmd_ability_resolve)
    ab_refresh = ability_sub.add_parser(
        "refresh",
        help="read the authority and write this node's capability projection (§2)",
        **_COMMON)
    ab_refresh.add_argument(
        "--ability-repo", type=Path, required=True,
        help="the ability-management checkout the manifest is read from")
    ab_refresh.add_argument(
        "--ability-rev", required=True,
        help="the pinned revision the manifest is read at; read from a git "
             "object, so a dirty checkout on this machine cannot become what "
             "the node believes about its own capabilities")
    ab_refresh.add_argument(
        "--device-id", action="append", required=True,
        help="an id that means THIS node — canonical first, then the registry's "
             "`legacy_ids` for it. Repeatable, and repeat it: a renamed device "
             "whose historical id is omitted matches nothing and reports no "
             "error, because the manifest records installations under the id "
             "in force when they happened")
    ab_refresh.set_defaults(func=cmd_ability_refresh)

    pack = sub.add_parser(
        "pack", help="a declared pack, resolved against this node's catalogue (§11)",
        **_COMMON)
    pack_sub = pack.add_subparsers(dest="subcommand", required=True)
    pk_inspect = pack_sub.add_parser(
        "inspect",
        help="resolve one pack (or all) against the abilities this node has observed",
        **_COMMON)
    pk_inspect.add_argument(
        "pack", nargs="?", default=None,
        help="a pack id, with or without the `pack:` prefix. Omitted inspects "
             "every declared pack. A name that matches none is refused rather "
             "than widened to all of them.")
    pk_inspect.add_argument(
        "--declarations", type=Path, required=True,
        help="the published packs (ability-packs.yaml). The schema contract "
             "stopped carrying them, so this is required and not defaulted — a "
             "caller that supplies only the schema gets an empty contract.")
    pk_inspect.add_argument(
        "--contract", type=Path,
        default=REPO_ROOT / "abilities" / "pack-contract.yaml",
        help="the SCHEMA half: record shape, member roles, pin rule")
    pk_inspect.add_argument(
        "--ability-repo", type=Path,
        help="the ability-management checkout the CATALOGUE is read from. Not "
             "the local projection: a pack's closure is a statement about the "
             "whole canonical catalogue, and the node's projection is a "
             "device-filtered subset of it.")
    pk_inspect.add_argument(
        "--ability-rev",
        help="the revision the catalogue is read at, as a git object. Read "
             "this way for the same reason `ability refresh` does: a dirty "
             "checkout on this machine must not become the catalogue a pack "
             "verdict is about.")
    pk_inspect.add_argument(
        "--catalogue", type=Path,
        help="a file of ability names, one per line, INSTEAD of reading the "
             "manifest — for checking a pack against a name set that is not "
             "`abilities-manifest.yaml`. Mutually exclusive in effect with "
             "--ability-repo, which wins if both are given.")
    pk_inspect.add_argument(
        "--catalogue-revision",
        help="override the revision the verdict names; defaults to the full "
             "SHA the catalogue was actually resolved at")
    pk_inspect.add_argument(
        "--catalogue-repo",
        help="the ability-management checkout, so the pin's ancestor question "
             "can be asked. Without it an ancestor-passing pin is reported "
             "UNSETTLED rather than PASS or FAIL. Defaults to --ability-repo.")
    pk_inspect.set_defaults(func=cmd_pack_inspect)

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

    network = sub.add_parser(
        "network", help="the network mutation gate: preflight, and create (§C1)",
        **_COMMON)
    network_sub = network.add_subparsers(dest="subcommand", required=True)
    for name, helptext in (
            ("preflight",
             "decide, without doing anything: ALLOW, REQUIRE_APPROVAL or DENY"),
            ("create",
             "preflight, then create the network if it allowed it (needs "
             "--execute; a DENY never reaches the executor)")):
        net_cmd = network_sub.add_parser(name, help=helptext, **_COMMON)
        net_cmd.add_argument("--request", required=True,
                             help="JSON or YAML `{network: {...}}` request file")
        net_cmd.add_argument(
            "--policy",
            help="host profile holding `network_policy`. No path is built in: "
                 "an absent policy is NETWORK_POLICY_INCOMPLETE, and a default "
                 "location nothing writes is indistinguishable from a host "
                 "whose policy is genuinely empty")
        net_cmd.add_argument("--level", choices=_network_levels(),
                             help="disclosure level; L1 is the default and a "
                                  "DENY promotes itself to L2")
        net_cmd.add_argument(
            "--context",
            help="trusted context document ({context_ref, actor_ref}). It is "
                 "what names the requester, and without it the ownership gate "
                 "answers NO_TRUSTED_CONTEXT and nothing runs — a missing "
                 "requester is a refusal with a repair, not a usage error")
        if name == "create":
            net_cmd.add_argument(
                "--execute", action="store_true",
                help="perform the creation. Without it the preflight still "
                     "runs and nothing is performed")
        net_cmd.set_defaults(func=cmd_network)

    ownership = sub.add_parser(
        "ownership",
        help="who may be acted on: ALLOW, REQUIRE_APPROVAL or DENY (§C2)",
        **_COMMON)
    ownership_sub = ownership.add_subparsers(dest="subcommand", required=True)
    own_check = ownership_sub.add_parser(
        "check", help="evaluate one subject and say why", **_COMMON)
    own_check.add_argument("--kind", required=True,
                           help="a kind `ownership-contract.yaml` declares. "
                                "The list is that contract's and not this "
                                "help text's: a second copy here is a second "
                                "vocabulary, and it had already drifted by one "
                                "entry before anyone read it")
    own_check.add_argument("--name", required=True, help="the subject's name")
    own_check.add_argument("--action", default="additive",
                           choices=("additive", "destructive"),
                           help="additive changes the subject and destroys "
                                "nothing. A protected subject is refused for "
                                "the first and needs approval for the second")
    own_check.add_argument("--tier",
                           help="required with --action destructive; the tiers "
                                "are the ones `hygiene.RISK_TIERS` declares")
    own_check.add_argument("--context", help="a trusted context document "
                                             "({context_ref, actor_ref})")
    # `--registry` is NOT declared here: the shared `common` parent already
    # declares it as "path to the managed-runtime registry document", and a
    # second declaration is an argparse conflict rather than an override.
    # `cmd_ownership` forwards the inherited one.
    own_check.add_argument("--level", default="L1",
                           choices=_ownership_levels(),
                           help="L0 the decision, L1 the decision and why, "
                                "L2 the evidence it rests on, L3 the resolved "
                                "record verbatim")
    # `--json` is not declared here either; `common` carries it.
    own_check.set_defaults(func=cmd_ownership)

    mutation = sub.add_parser(
        "mutation",
        help="the managed path: intent, plan, compliance, executor, audit (§C3)",
        **_COMMON)
    mutation_sub = mutation.add_subparsers(dest="subcommand", required=True)
    for verb, blurb in (("plan", "build the transient plan and the verdict, "
                                 "and write nothing"),
                        ("run", "the same, and with --execute perform it and "
                                "audit the result")):
        mut = mutation_sub.add_parser(verb, help=blurb, **_COMMON)
        mut.add_argument("--request", required=True,
                         help="a JSON or YAML request: the action, the "
                              "subject, the requester and what to write")
        mut.add_argument("--context", help="a trusted context document "
                                           "({context_ref, actor_ref})")
        mut.add_argument("--level", choices=("L0", "L1", "L2", "L3"),
                         help="disclosure level for the compliance verdict")
        if verb == "run":
            mut.add_argument("--execute", action="store_true",
                             help="perform the mutation. Without it the plan, "
                                  "the verdict and the audit's scope are still "
                                  "produced and nothing is written")
        mut.set_defaults(func=cmd_mutation, mutation_verb=verb)

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

    console = sub.add_parser(
        "console", help="§M/§N · which session a conversation is addressing, "
                        "and what may be done with it", **_COMMON)
    console_sub = console.add_subparsers(dest="subcommand", required=True)

    con_select = console_sub.add_parser(
        "select", help="choose which session this conversation addresses. "
                       "Moves your attention, never a thread's Item", **_COMMON)
    con_select.add_argument("--conversation", required=True,
                            help="the conversation making the choice")
    con_select.add_argument("--session", required=True,
                            help="the session_ref it now addresses")
    con_select.add_argument("--device", help="the device that session is on")
    con_select.set_defaults(func=cmd_console_select)

    con_show = console_sub.add_parser(
        "selection", help="what this conversation is addressing, and whether it "
                          "is still there", **_COMMON)
    con_show.add_argument("--conversation", required=True)
    con_show.set_defaults(func=cmd_console_selection)

    con_ops = console_sub.add_parser(
        "operations", help="the semantic operations a person can ask a session "
                           "to do, and which level offers each", **_COMMON)
    con_ops.add_argument("--level", default="L2", choices=["L0", "L1", "L2", "L3"])
    con_ops.set_defaults(func=cmd_console_operations)

    card = sub.add_parser(
        "card", help="what a person is shown: one model, a Feishu payload and "
                     "a preview, and NOTHING is sent", **_COMMON)
    card_sub = card.add_subparsers(dest="subcommand", required=True)

    def _add_card(name: str, func, help_text: str, *, from_digest: bool) -> None:
        """Every card kind gets the same four flags and the same default.

        `--html-out` rather than `--preview` because the preview is a FILE a
        person opens, and a command that printed HTML to a terminal would be a
        preview nobody can look at.
        """
        parser_ = card_sub.add_parser(name, help=help_text, **_COMMON)
        if from_digest:
            parser_.add_argument(
                "--digest", default="",
                help="a `serverosctl alert digest --json` document on disk. "
                     "Omitted means the digest is produced now")
        parser_.add_argument(
            "--html-out", default="", metavar="PATH",
            help="write the standalone HTML preview (390px frame beside a "
                 "desktop one) to this path")
        parser_.add_argument(
            "--sample", action="store_true",
            help="mark the card 示例 / 尚未发送. Every layout fixture uses it")
        parser_.add_argument("--at", default="", help="override the timestamp")
        # §3. Off by default, and the default is the payload itself. A renderer
        # reads this JSON to send a card; wrapping it unconditionally would
        # make every existing consumer learn an envelope before it could send
        # anything, and the version would arrive as a change they did not ask
        # for rather than as a thing they opted into.
        parser_.add_argument(
            "--with-frozen", action="store_true",
            help="emit {card, frozen} instead of the card alone, where `frozen` "
                 "names the version of this payload. Pair it with --card-file "
                 "on `card record-delivery`")
        parser_.add_argument(
            "--idempotency-key", default="", metavar="KEY",
            help="the window this send dedupes within, if the sender has one. "
                 "The default names the instant the card was rendered at, so a "
                 "re-render is a new key: ServerOS does not know the cadence — "
                 "an hourly timer's window is a property of the timer — and "
                 "inventing one here would be a second clock beside the one "
                 "that actually drives the send")
        parser_.set_defaults(func=func)

    _add_card("status", cmd_card_status,
              "the three blocks and the env-health line above them — the shared "
              "gateway is shown only when a service binding names it",
              from_digest=False)
    card_sub.choices["status"].add_argument(
        "--gateway-binding", default="", metavar="PATH",
        help="the service-binding document that names this node's shared AI "
             "gateway. Without one the block renders UNBOUND")
    card_sub.choices["status"].add_argument(
        "--device-ref", default="", metavar="REF",
        help="which device's binding to read, e.g. device:tokyo-shared-dev-01")
    _add_card("alert", cmd_card_alert,
              "at most three events, each with its what/impact/certainty/"
              "action/evidence fields, and a count of what did not fit",
              from_digest=True)
    _add_card("recovery", cmd_card_recovery,
              "only what was announced and has since recovered — never a "
              "resolution nobody was told about",
              from_digest=True)

    # §3. The receipt, and the ONE surface that writes it. `card record-delivery`
    # is the second half of the same split `alert record-delivery` states: this
    # side decides what a card IS and records what happened to it, and the
    # transport stays with whoever holds one.
    card_rec = card_sub.add_parser(
        "record-delivery",
        help="§3 · say what the transport did with the card version it was "
             "handed. The record names the version, so a re-send of one card "
             "and a send of the next one cannot read the same",
        **_COMMON)
    card_rec.add_argument("--kind", required=True, choices=list(_card_delivery().KINDS),
                          help="which card kind this delivery is")
    card_rec.add_argument("--card-file", required=True, metavar="PATH",
                          help="a `card <kind> --with-frozen --json` envelope: "
                               "the payload AND the version it was sent as")
    card_rec.add_argument("--state", required=True,
                          choices=list(_card_delivery().OUTCOMES),
                          help="DELIVERED, FAILED, UNSUPPORTED or UNKNOWN")
    card_rec.add_argument("--reason", default="",
                          help="why, when the outcome was not DELIVERED")
    card_rec.add_argument("--idempotency-key", default="",
                          help="override the key the frozen block carries. The "
                               "default comes from the rendering: it names the "
                               "instant, so a re-render is a new key and the "
                               "cadence-window rule stays with whoever runs "
                               "the clock")
    card_rec.set_defaults(func=cmd_card_record_delivery)

    card_del = card_sub.add_parser(
        "deliveries", help="§3 · what has been sent, by version",
        **_COMMON)
    card_del.add_argument("--kind", default="", choices=[""] + list(_card_delivery().KINDS),
                          help="one card kind; omitted means all of them")
    card_del.add_argument("--limit", type=int, default=10,
                          help="how many recent entries to print")
    card_del.set_defaults(func=cmd_card_deliveries)

    registration = sub.add_parser(
        "registration", help="the loop from 'something is running' to a "
                             "registration a person authorized. Nothing here "
                             "writes anything", **_COMMON)
    registration_sub = registration.add_subparsers(dest="subcommand",
                                                   required=True)

    rg_disc = registration_sub.add_parser(
        "discover", help="what is observed and named by no registration: a "
                         "candidate when a launch record carries an owner, a "
                         "discovery note when it does not", **_COMMON)
    rg_disc.add_argument("--device", default="",
                         help="override the device ref the registry declares")
    rg_disc.add_argument(
        "--launch-records", default="", metavar="PATH",
        help=f"a {LAUNCH_RECORD_SCHEMA} document written by the client that "
             "started the things. Without one nothing can carry an owner, so "
             "every observation is a discovery note")
    rg_disc.add_argument("--out", default="", metavar="PATH",
                         help="write the candidate document that "
                              "`registration plan` reads back")
    rg_disc.add_argument("--at", default="")
    rg_disc.set_defaults(func=cmd_registration_discover)

    rg_plan = registration_sub.add_parser(
        "plan", help="a person's scope, turned into a registration the "
                     "registry has already validated — and not written",
        **_COMMON)
    rg_plan.add_argument("--candidates", required=True, metavar="PATH",
                         help="the document `registration discover --out` wrote")
    rg_plan.add_argument(
        "--evidence-key", required=True, metavar="REF",
        help="which candidate in it to answer, by its evidence key")
    rg_plan.add_argument(
        "--scope", required=True,
        choices=["session", "project", "node", "external", "ignore"],
        help="session/project/node write a resource; external records a "
             "dependency and writes nothing; ignore remembers the answer")
    rg_plan.add_argument(
        "--actor", required=True,
        help="the subject giving the answer. A scope that writes a resource "
             "refuses without one")
    rg_plan.add_argument("--id", default="", metavar="RESOURCE_ID",
                         help="the name a person will read in an alert. A "
                              "writing scope refuses without one rather than "
                              "deriving an opaque hash")
    rg_plan.add_argument("--note", default="")
    rg_plan.add_argument(
        "--recovery", default=None,
        # The closed set is the contract's, read here rather than retyped. A
        # restated vocabulary is one that agrees today: `redeclare` and
        # `reauthorize` are the two models where acting on the resource is
        # acting on the wrong object, and a hand-written list would be exactly
        # the place they went missing.
        choices=[model["id"] for model in
                 (registry_mod.contract().get("recovery_models") or [])],
        help="separate from the scope on purpose: registering a node-scoped "
             "resource does not authorize restarting it")
    rg_plan.add_argument("--at", default="")
    rg_plan.set_defaults(func=cmd_registration_plan)

    rg_res = registration_sub.add_parser(
        "resolve", help="every registration against what was measured about "
                        "it: OBSERVED / ABSENT / STALE / UNREPORTED", **_COMMON)
    rg_res.add_argument("--at", default="")
    rg_res.set_defaults(func=cmd_registration_resolve)

    alert = sub.add_parser(
        "alert", help="§21 · what this node has found, and what it has told "
                      "you about it", **_COMMON)
    alert_sub = alert.add_subparsers(dest="subcommand", required=True)

    al_list = alert_sub.add_parser(
        "list", help="every alert, most in need of attention first. An open "
                     "alert with a hold reason is one it has chosen not to "
                     "repeat yet, not a closed one", **_COMMON)
    al_list.set_defaults(func=cmd_alert_list)

    al_inspect = alert_sub.add_parser(
        "inspect", help="one alert, plus the sentence a person would have "
                        "been sent", **_COMMON)
    al_inspect.add_argument("alert_ref")
    al_inspect.set_defaults(func=cmd_alert_inspect)

    al_ack = alert_sub.add_parser(
        "ack", help="a person has seen it. NOT a resolution — the alert keeps "
                    "its observation current and closes only when the "
                    "condition does", **_COMMON)
    al_ack.add_argument("alert_ref")
    al_ack.add_argument("--note", help="why, in your words. Recorded with the "
                                       "acknowledgement")
    al_ack.set_defaults(func=cmd_alert_ack)

    al_snooze = alert_sub.add_parser(
        "snooze", help="stop repeating it for a while. It speaks again if it "
                       "is still firing when the window ends", **_COMMON)
    al_snooze.add_argument("alert_ref")
    al_snooze.add_argument("--seconds", type=int,
                           help="the window, in seconds. Omitted means the "
                                "window the contract declares")
    al_snooze.set_defaults(func=cmd_alert_snooze)

    al_sync = alert_sub.add_parser(
        "sync", help="evaluate, then send whatever has to be said, then record "
                     "what happened. The only command that holds a transport",
        **_COMMON)
    al_sync.add_argument(
        "--conversation", help="where to send. Falls back to "
                               "SERVEROS_ALERT_CONVERSATION")
    al_sync.add_argument(
        "--dry-run", action="store_true",
        help="evaluate and show what WOULD be sent, without sending it")
    al_sync.set_defaults(func=cmd_alert_sync)

    al_digest = alert_sub.add_parser(
        "digest", help="§Q2 · what CHANGED since a person was last told, "
                       "batched into one card. Read-only: it decides whether "
                       "the batch is worth a message and sends nothing",
        **_COMMON)
    al_digest.set_defaults(func=cmd_alert_digest)

    al_digest_base = alert_sub.add_parser(
        "digest-baseline", help="§Q2 · declare everything currently open to be "
                                "backlog rather than news. Run once; safe to "
                                "run twice", **_COMMON)
    al_digest_base.set_defaults(func=cmd_alert_digest_baseline)

    al_digest_rec = alert_sub.add_parser(
        "digest-record", help="§Q2 · say what the transport did with the digest "
                              "it was handed", **_COMMON)
    al_digest_rec.add_argument("--generation", type=int, required=True)
    al_digest_rec.add_argument("--state", required=True,
                               choices=["DELIVERED", "FAILED", "UNSUPPORTED",
                                        "UNKNOWN"])
    al_digest_rec.add_argument("--shown-file",
                               help="the document `alert digest --json` printed; "
                                    "its `shown` rows are what the card carried")
    al_digest_rec.add_argument("--reason")
    al_digest_rec.set_defaults(func=cmd_alert_digest_record)

    requirement = sub.add_parser(
        "requirement",
        help="requirements, and the global board derived from them (§S)",
        **_COMMON)
    requirement_sub = requirement.add_subparsers(dest="subcommand", required=True)
    rq_board = requirement_sub.add_parser(
        "board",
        help="compose the global requirement board from the per-project "
             "projections and print it; --write persists it",
        **_COMMON)
    rq_board.add_argument(
        "--write", action="store_true",
        help="persist to `state/requirement-board/current.{json,md}`. Omitted "
             "prints and writes nothing — the board is a projection, and a "
             "command that wrote on every inspection would leave a file whose "
             "only reader was the person who asked for the printout")
    rq_board.add_argument(
        "--output-dir", type=Path, default=None,
        help="where --write puts the board, overriding the contract's state "
             "root. Must be absolute; a board that landed in the caller's cwd "
             "would be a second board nobody knows about")
    rq_board.set_defaults(func=cmd_requirement_board)

    work = sub.add_parser(
        "work", help="the personal board, one Item's chain of documents, and "
                     "the coverage maps (§6/§7/§8)", **_COMMON)
    work_sub = work.add_subparsers(dest="subcommand", required=True)

    def _add_work_roots(parser_, *, project_required: bool) -> None:
        """The three roots every `work` verb reads, plus the project.

        All three are on all four verbs because a fixture has to be able to run
        the whole group against a directory it owns, and a flag that exists on
        three of them is a flag somebody will expect on the fourth and get the
        node's real records instead.

        `--workspace-root` is the third and not a synonym for `--records-root`:
        it is what a relative `card_locator` is resolved against, and on this
        node the cards live under `workspace/records/projects/...` — one segment
        below the records root a caller passes.
        """
        if project_required:
            parser_.add_argument("--project", required=True)
        else:
            parser_.add_argument("--project", default="")
        parser_.add_argument("--boards-root", default="")
        parser_.add_argument("--records-root", default="")
        parser_.add_argument("--workspace-root", default="",
                             help="what a relative `card_locator` resolves "
                                  "against (default: this node's workspace root)")

    wk_board = work_sub.add_parser(
        "board", help="§6's sections in §6's order: 今日重点 → P0 → P1 → 待排期 "
                      "→ 等待 → 待验收 → 取消 → 已完成", **_COMMON)
    _add_work_roots(wk_board, project_required=False)
    wk_board.add_argument("--write", action="store_true",
                          help="persist board.{json,md} under "
                               "`state/workchain` unless --output-dir is given")
    wk_board.add_argument("--output-dir", default="")
    wk_board.set_defaults(func=_workchain_refusals(cmd_work_board))

    wk_focus = work_sub.add_parser(
        "focus", help="§12 — at most two objectives for the next cycle, decided "
                      "from the board's own rows over the oldest projection's "
                      "age. Reads; admits nothing", **_COMMON)
    _add_work_roots(wk_focus, project_required=False)
    wk_focus.add_argument("--ttl", type=int, default=900,
                          help="how old the underlying projection may be, in "
                               "seconds (default 900)")
    wk_focus.add_argument("--limit", type=int, default=2)
    wk_focus.set_defaults(func=_workchain_refusals(cmd_work_focus))

    wk_grants = work_sub.add_parser(
        "grants", help="§12 — the standing delegations this node has recorded, "
                       "each one re-checked against the delegation it derives "
                       "from. Reads the control store; admits nothing",
        **_COMMON)
    wk_grants.add_argument("--now", default="",
                           help="the clock to judge the delegations against "
                                "(default: now). The store holds no clock and "
                                "neither does `context/delegation.py`, so this "
                                "is the one place the question is answered")
    wk_grants.set_defaults(func=cmd_work_grants)

    wk_admit = work_sub.add_parser(
        "admit", help="§12/P6 — admit one cycle: the focus selection, its budget "
                      "reservation and its lease/dispatch intent in one write. "
                      "Records the intent; performs nothing", **_COMMON)
    _add_work_roots(wk_admit, project_required=False)
    wk_admit.add_argument("--cycle", required=True,
                          help="the ref this cycle is admitted under, and what a "
                               "resume names. There is deliberately no --grant: "
                               "the grant is read off the selected row")
    wk_admit.add_argument("--action-key", required=True,
                          help="the key `runtime/bounded_invocation.invoke_once` "
                               "will be called with ([a-zA-Z0-9_-]{1,100})")
    wk_admit.add_argument("--resource", required=True,
                          help="the resource this cycle will declare a lease on")
    wk_admit.add_argument("--scope", required=True,
                          help="the scope that lease is declared under; scope and "
                               "holder are a resource's properties, not the "
                               "caller's")
    wk_admit.add_argument("--ttl", type=int, default=900,
                          help="how old the underlying projection may be, in "
                               "seconds (default 900)")
    wk_admit.add_argument("--limit", type=int, default=2)
    wk_admit.set_defaults(func=_workchain_refusals(cmd_work_admit))

    wk_admissions = work_sub.add_parser(
        "admissions", help="§12/P6 — the cycles currently admitted, and the "
                           "reservation, lease and action key each is holding. "
                           "Reads the control store", **_COMMON)
    wk_admissions.set_defaults(func=cmd_work_admissions)

    wk_confirm = work_sub.add_parser(
        "confirm", help="§12/P6 — record the lease store's or the receipts "
                        "directory's answer for one admission, and advance one "
                        "phase", **_COMMON)
    wk_confirm.add_argument("cycle")
    # No `choices=` here, deliberately. argparse would reject INTENT_RECORDED
    # with "invalid choice", and the reason it is not confirmable is a sentence
    # the command writes: `admit` recorded that phase itself, and a caller
    # reporting this store's own act back to this store is not evidence.
    wk_confirm.add_argument("--phase", required=True,
                            help="LEASE_HELD or DISPATCHED — the two answers a "
                                 "store other than this one gives. INTENT_RECORDED "
                                 "is what `work admit` writes and is refused here")
    wk_confirm.add_argument("--receipts", default="",
                            help="the directory `invoke_once` was given; required "
                                 "for --phase DISPATCHED, and there is no default")
    wk_confirm.add_argument("--at", default="",
                            help="the instant to record (default: now)")
    wk_confirm.set_defaults(func=cmd_work_confirm)

    wk_retire = work_sub.add_parser(
        "retire", help="§12/P6 — close an admission, releasing its reservation "
                       "and its action key. Keeps the first retirement, and "
                       "deletes nothing", **_COMMON)
    wk_retire.add_argument("cycle")
    wk_retire.add_argument("--outcome", default="",
                           help="one word for how the cycle ended, if it ended")
    wk_retire.add_argument("--at", default="",
                           help="the instant to record (default: now)")
    wk_retire.set_defaults(func=cmd_work_retire)

    wk_item = work_sub.add_parser(
        "item", help="one Item's chain: which document exists, which is "
                     "required at this state, and where the chain stops",
        **_COMMON)
    _add_work_roots(wk_item, project_required=True)
    wk_item.add_argument("item")
    wk_item.set_defaults(func=_workchain_refusals(cmd_work_item))

    wk_today = work_sub.add_parser(
        "today", help="record the 1-2 Items a person is carrying today. This "
                      "is the one verb here that writes, and what it writes is "
                      "a selection: no Item state is touched", **_COMMON)
    _add_work_roots(wk_today, project_required=True)
    wk_today.add_argument("--select", default="",
                          help="comma-separated Item ids. Required — reading "
                               "today without choosing is `work board`")
    wk_today.add_argument("--date", default="")
    wk_today.add_argument("--note", default="")
    wk_today.add_argument("--actor", default="")
    wk_today.set_defaults(func=_workchain_refusals(cmd_work_today))
    wk_skel = work_sub.add_parser(
        "skeleton", help="§8 — the shape of each document in the chain, and "
                         "why each part is there", **_COMMON)
    wk_skel.add_argument("name", nargs="?", default="",
                         help="a document id (brief, design, plan, report, "
                              "daily-report); omit to list them all")
    wk_skel.set_defaults(func=_workchain_refusals(cmd_work_skeleton))

    wk_cov = work_sub.add_parser(
        "coverage", help="the disk map, the three feishu surfaces and the "
                         "followed-source list, each with its denominator",
        **_COMMON)
    _add_work_roots(wk_cov, project_required=True)
    wk_cov.set_defaults(func=_workchain_refusals(cmd_work_coverage))

    # Deliberately NOT `_add_work_roots`: the understanding layer sits under
    # `records/information/understanding/` and is not a property of a project,
    # so `--project` would be a flag whose value this verb never reads.
    wk_und = work_sub.add_parser(
        "understanding", help="§B2 — which understanding cards a source delta "
                              "touches: each card's own cited reach against the "
                              "newest position its sources now hold. Reads no "
                              "message body, proposes ranges and never claims, "
                              "writes nothing", **_COMMON)
    wk_und.add_argument("--records-root", default="",
                        help="the records checkout to read. Default: this node's "
                             "workspace root + /records. Must be absolute")
    wk_und.set_defaults(func=cmd_work_understanding)

    wk_upatch = work_sub.add_parser(
        "understanding-patch", help="§B2 — the write half: the patch a person "
                                    "acts on, built from the reader's ranges. "
                                    "Confirmation is authored input; with no "
                                    "--decisions every item is AWAITING and no "
                                    "card is edited", **_COMMON)
    wk_upatch.add_argument("--records-root", default="",
                           help="the records checkout to read. Default: this "
                                "node's workspace root + /records. Must be absolute")
    wk_upatch.add_argument("--decisions", type=Path, default=None,
                           help="an authored decisions file (JSONL). Read only: "
                                "this verb has no way to write one")
    wk_upatch.add_argument("--write", type=Path, default=None,
                           help="write the patch here. Must be absolute")
    wk_upatch.add_argument("--verify", type=Path, default=None,
                           help="recompute from the records and compare against "
                                "this patch instead of building a new one")
    wk_upatch.set_defaults(func=cmd_work_understanding_patch)

    wk_usem = work_sub.add_parser(
        "understanding-semantic", help="§B2 — the semantic step: what an "
                                       "authorised native Agent read becomes "
                                       "committed understanding, or a question. "
                                       "The reading is authored input; without "
                                       "--commit nothing is written",
        **_COMMON)
    wk_usem.add_argument("--reading", type=Path, required=True,
                         help="the reading an authorised native Agent produced "
                              "(JSON). Read only: this verb cannot write one")
    wk_usem.add_argument("--records-root", default="",
                         help="the records checkout to read and, with --commit, "
                              "to write into. Default: this node's workspace "
                              "root + /records. Must be absolute")
    wk_usem.add_argument("--delegation", type=Path, default=None,
                         help="the standing grant that lets machine facts "
                              "commit without a person (JSON: covers/until/"
                              "granted_by/source). Read only, and absent means "
                              "every claim is a candidate")
    wk_usem.add_argument("--accepted", type=Path, default=None,
                         help="what a person accepted about this proposal "
                              "(JSONL: card/manifest/from_position/to_position/"
                              "card_digest/content_identity/reading_digest/"
                              "claims/by/at). Read only: this verb has no way to "
                              "write one. An acceptance made about this span at "
                              "another revision matches nothing and is reported")
    wk_usem.add_argument("--commit", action="store_true",
                         help="write the claims that may be committed into "
                              "their owning cards' own fence. Without this, "
                              "nothing is written and the resolution is printed")
    wk_usem.add_argument("--committed-at", default="",
                         help="the time to stamp the receipt with, supplied by "
                              "the caller. Required with --commit")
    wk_usem.set_defaults(func=cmd_work_understanding_semantic)

    wk_recall = work_sub.add_parser(
        "claim-recall", help="§A2 — whether a committed claim may still be "
                             "recalled as current: the source revision it was "
                             "read from, the durable record of what replaced "
                             "it, and the card the line is in. Reads only, "
                             "consults no provider, writes nothing",
        **_COMMON)
    wk_recall.add_argument("--records-root", default="",
                           help="the records checkout to read. Default: this "
                                "node's workspace root + /records. Must be "
                                "absolute")
    wk_recall.add_argument("--claim", action="append", default=[],
                           help="restrict the report to this claim id. Repeatable. "
                                "The index is NOT restricted: an id no receipt "
                                "names is refused rather than reported, because "
                                "a claim the record never held is not a claim "
                                "that was withdrawn")
    wk_recall.set_defaults(func=cmd_work_claim_recall)

    wk_wl = work_sub.add_parser(
        "waterline", help="§B2/§6 — the two waterlines per source: how far the "
                          "capture got, how far understanding got, and the gaps "
                          "between them. Reads only; writes nothing",
        **_COMMON)
    wk_wl.add_argument("--records-root", default="",
                       help="the records checkout to read. Default: this node's "
                            "workspace root + /records. Must be absolute")
    wk_wl.set_defaults(func=cmd_work_waterline)

    workbench = sub.add_parser(
        "workbench",
        help="§6's START-HERE set: one entry point a person opens to inspect "
             "what this node can actually reach", **_COMMON)
    workbench_sub = workbench.add_subparsers(dest="workbench_verb", required=True)

    def _add_workbench(parser_):
        """The inputs a rendered document can need, plus the subset selectors.

        They are options and not defaults, deliberately: a default pointing at one
        round's scratch directory would render a Quay section out of a store that
        happens to still be on this disk, and would do it on this node only. An
        input that is absent produces a section saying it has no answer, which is
        the honest rendering of not having been told.
        """
        parser_.add_argument("--out", default="",
                             help="where to write (default: <repo>/workbench)")
        parser_.add_argument("--projects", nargs="*", default=None,
                             help="the projects the coverage page reads")
        parser_.add_argument("--project", default=None,
                             help="the project the Item chain is read from")
        parser_.add_argument("--item", default=None, help="the Item id")
        parser_.add_argument("--quay-bin", default=None,
                             help="the Quay CLI the results page asks for the goal "
                                  "store. A fact about THIS node, so no default: "
                                  "the renderer bakes in no path")
        parser_.add_argument("--quay-run", default=None,
                             help="a directory holding a quay workspace (`ws/`)")
        parser_.add_argument("--ability-repo", default=None,
                             help="the ability-management clone to render the "
                                  "capability-family view from. This module does "
                                  "not reimplement that renderer")
        parser_.add_argument("--documents", nargs="*", default=None,
                             help="render only these documents. A subset renders "
                                  "in well under a second, which is what makes a "
                                  "targeted test of the check affordable; the full "
                                  "set re-runs every command its pages read from")
        parser_.add_argument("--reports-dir", nargs="*", default=None, metavar="DIR",
                             help="directories holding a run's daily reports, "
                                  "searched for files named *daily-report* in "
                                  "addition to <repo>/docs, <repo>/workbench and "
                                  "--quay-run. A run's reports live in the run's "
                                  "own directory, which is never a default")
        parser_.add_argument("--stdout", default=None,
                             help="print one document, write nothing")
        parser_.add_argument("--release-root", default=None,
                             help="an isolated runtime root's COMPOSITION directory "
                                  "(the one holding `current` and `previous`), for "
                                  "the manifest's install section. Never the live "
                                  "one, and not the `current` symlink: the panel "
                                  "reads `current`/`previous` beneath the path it "
                                  "is given")

    wb_render = workbench_sub.add_parser(
        "render", help="write the documents", **_COMMON)
    _add_workbench(wb_render)
    wb_render.set_defaults(func=cmd_workbench)

    wb_check = workbench_sub.add_parser(
        "check", help="fail if the documents on disk no longer match the "
                      "commands that produced them", **_COMMON)
    _add_workbench(wb_check)
    wb_check.set_defaults(func=cmd_workbench)

    report = sub.add_parser(
        "report", help="periodic reports (§23)", **_COMMON)
    report_sub = report.add_subparsers(dest="report_verb", required=True)
    rp_daily = report_sub.add_parser(
        "daily",
        help="one period's real work diff plus Quay's own gate ledger, as a "
             "canonical document and a publication receipt. This command writes "
             "two files and sends nothing", **_COMMON)
    rp_daily.add_argument(
        "--start", required=True,
        help="window start, ISO-8601 WITH an offset. Required and undefaulted: "
             "a generator that picks its own window writes a report about a "
             "period nobody chose, and it reads exactly like a normal one")
    rp_daily.add_argument("--end", required=True,
                          help="window end, ISO-8601 with an offset, >= --start")
    rp_daily.add_argument(
        "--worktree", required=True,
        help="the checkout the period's work diff is read from. A path that is "
             "not a checkout is reported as a GAP, not as zero commits")
    rp_daily.add_argument(
        "--run-dir", default=None,
        help="a round's directory holding `.quay/gate-events.jsonl`. Optional, "
             "and its absence is recorded as a GAP rather than as an empty "
             "ledger — Quay's records are not in this repository")
    rp_daily.add_argument("--focus", nargs="*", default=None,
                          help="the Item ids this period was aimed at. Absent "
                               "means nobody wrote one down, which is not the "
                               "same as there having been none")
    rp_daily.add_argument("--generated-at", default=None,
                          help="ISO-8601 instant to stamp the document with. "
                               "Default: read the host clock and say so in the "
                               "document, because a defaulted clock is a "
                               "different kind of reading from a supplied one")
    rp_daily.add_argument("--out-dir", required=True,
                          help="where to write the document and its receipt. "
                               "Must be absolute and outside this repository: a "
                               "report written into the revision is replaced "
                               "wholesale by the next one while looking exactly "
                               "like one that is kept")
    rp_daily.add_argument("--recipient", default=None,
                          help="who the report is for. Required in practice for "
                               "a DELIVERED state: a delivery that cannot say "
                               "who it went to is one this producer cannot "
                               "corroborate")
    rp_daily.add_argument("--channel", default=None, help="the channel or transport")
    rp_daily.add_argument(
        "--publication-state", default=None,
        help="what the CALLER says happened to this report — one of the words "
             "in runtime/notification-contract.yaml#alerts.delivery.values. "
             "Default UNKNOWN. Writing the file is not delivering it, so this "
             "command never infers this value from having succeeded")
    rp_daily.add_argument("--attempted-at", default=None,
                          help="when the caller attempted delivery, if it did")
    rp_daily.set_defaults(func=cmd_report_daily)

    recipe = sub.add_parser(
        "recipe", help="§7's three finite loops, and what each run recorded",
        **_COMMON)
    recipe_sub = recipe.add_subparsers(dest="recipe_verb", required=True)

    rc_list = recipe_sub.add_parser(
        "list", help="Perception, Work and Report, with the stops each one "
                     "declares in advance. Reads the contract and stops there",
        **_COMMON)
    rc_list.set_defaults(func=cmd_recipe_list)

    rc_run = recipe_sub.add_parser(
        "run",
        help="one loop, one scope, one period. The key is those three, so a "
             "run of a key that already has a record does nothing a second "
             "time — and `no change` is decided before the model boundary is "
             "reached, so a quiet period calls no model", **_COMMON)
    rc_run.add_argument("--recipe", required=True,
                        choices=["perception", "work", "report"],
                        help="which of §7's loops")
    rc_run.add_argument(
        "--scope", required=True,
        help="node, project:<name>, or item:<ITEM>. The scope is part of the "
             "key: §7 recomputes the affected claims and not the whole library, "
             "and one cursor per scope is what lets one move without "
             "invalidating another's last-success")
    rc_run.add_argument(
        "--budget", default=None, type=int,
        help="model steps this run may take. Required in effect and never "
             "defaulted: a ceiling a loop picked for itself is one nobody "
             "agreed to. Omit it only when --schedule supplies one, since a "
             "schedule binds its own budget; with neither, the run is refused "
             "rather than given a ceiling of this machine's choosing. "
             "`--budget 0` is a complete run of every step that needs no model")
    rc_run.add_argument("--period-start", default=None,
                        help="window start, ISO-8601 WITH an offset. Either "
                             "this pair or --schedule is required: a loop that "
                             "picks its own window writes a record about a "
                             "period nobody chose")
    rc_run.add_argument("--period-end", default=None,
                        help="window end, ISO-8601 with an offset, >= start")
    rc_run.add_argument(
        "--schedule", default=None,
        help="take the window from a schedule this INSTANCE has bound "
             "(state/serveros/recipes/schedules.yaml). An unbound schedule "
             "refuses rather than falling back to the host clock — §7 binds the "
             "timezone, window, recipients, data scope and cost ceiling by "
             "hand, and this repository enables no timer")
    rc_run.add_argument("--state-root", default=None,
                        help="where run records live. Default: the declared "
                             "`recipes` namespace of this node's state root")
    rc_run.add_argument("--records-root", default=None,
                        help="the records root the scope's authored inputs are "
                             "read from (sources.yaml, today.yaml)")
    rc_run.add_argument("--workspace-root", default=None)
    rc_run.add_argument("--worktree", default=None,
                        help="the checkout a period's real work delta is read "
                             "from. A path that is not a checkout is reported "
                             "as a GAP, not as zero commits")
    rc_run.add_argument("--run-dir", default=None,
                        help="a round's directory holding `.quay/gate-events.jsonl`. "
                             "Optional; its absence is a GAP rather than an "
                             "empty ledger")
    rc_run.add_argument("--out-dir", default=None,
                        help="where `report` writes its document and receipt. "
                             "Absolute and outside this repository")
    rc_run.add_argument("--recipient", default=None)
    rc_run.add_argument("--channel", default=None)
    rc_run.add_argument(
        "--publication-state", default=None,
        help="what the CALLER says happened to the report — one of "
             "runtime/notification-contract.yaml#alerts.delivery.values. "
             "Default UNKNOWN: writing the file is not delivering it")
    rc_run.add_argument("--work-engine", default=None,
                        help="the work-engine command a `work` run would ask for "
                             "the next Activity. Absent or not a file on this "
                             "node is reported as WORK_ENGINE_UNBOUND rather "
                             "than as an Item with nothing to do")
    rc_run.add_argument("--retake", action="store_true",
                        help="proceed over a claim that was never completed. "
                             "The record keeps both timestamps")
    rc_run.set_defaults(func=cmd_recipe_run)

    rc_status = recipe_sub.add_parser(
        "status", help="what every loop recorded for one key. A period no loop "
                       "has a record for exits COULD_NOT_ANSWER — 'nobody ran "
                       "this' is not 'nothing happened'", **_COMMON)
    rc_status.add_argument("--scope", required=True)
    rc_status.add_argument("--period-start", default=None)
    rc_status.add_argument("--period-end", default=None)
    rc_status.add_argument("--schedule", default=None)
    rc_status.add_argument("--state-root", default=None)
    rc_status.set_defaults(func=cmd_recipe_status)

    support = sub.add_parser(
        "support",
        help="a support question, and where the answer is allowed to come "
             "from (§J). Reads a knowledge base in another repository and "
             "writes nothing", **_COMMON)
    support_sub = support.add_subparsers(dest="support_verb", required=True)

    def _roots(parser):
        # On every verb rather than on the group, because `support --kb-root X
        # entries` and `support entries --kb-root X` are two different readings
        # of one command line and only the second is the one this tree's other
        # groups use. `--kb-root` is required and never defaulted: the
        # knowledge base belongs to another author on another schedule, and a
        # path baked into a module answers for the machine it was written on.
        parser.add_argument(
            "--kb-root", required=True,
            help="the knowledge base checkout to read. Required and undefaulted")
        parser.add_argument(
            "--code-root", default=None,
            help="the support CODE checkout, passed only so that it can be "
                 "COMPARED with --kb-root. Nothing is read from it. If the two "
                 "are one repository the call is refused: §J's 双仓边界")
        return parser

    sp_entries = _roots(support_sub.add_parser(
        "entries", help="every formal entry the glob reads, with what it "
                        "excluded and why", **_COMMON))
    sp_entries.set_defaults(func=cmd_support_entries)

    sp_match = _roots(support_sub.add_parser(
        "match", help="which entry a question reaches, and by which of the "
                      "knowledge base's own rules", **_COMMON))
    sp_match.add_argument("--status-code", type=int, default=None)
    sp_match.add_argument("--message", default=None)
    sp_match.set_defaults(func=cmd_support_match)

    sp_answer = _roots(support_sub.add_parser(
        "answer", help="a reply assembled only from formal entries, with its "
                       "provenance, and the knowledge-improvement candidate it "
                       "implies", **_COMMON))
    sp_answer.add_argument("--status-code", type=int, default=None)
    sp_answer.add_argument("--message", default=None)
    sp_answer.set_defaults(func=cmd_support_answer)

    sp_check = _roots(support_sub.add_parser(
        "check", help="the index against the entries. Reports drift; a finding "
                      "is not a non-zero exit", **_COMMON))
    sp_check.add_argument(
        "--as-of", default=None,
        help="the date to judge staleness against. Never read from the clock: "
             "a check whose answer depends on when it ran cannot be compared "
             "against a recorded one")
    sp_check.set_defaults(func=cmd_support_check)

    sp_handoff = _roots(support_sub.add_parser(
        "handoff", help="§10's fallback: the small decision packet for a "
                        "question nobody reached, and the two receipts it "
                        "needs before anybody may say 「已通知」. Sends "
                        "nothing", **_COMMON))
    sp_handoff.add_argument("--status-code", type=int, default=None)
    sp_handoff.add_argument("--message", default=None)
    sp_handoff.add_argument(
        "--trigger", default=None,
        help="which of the contract's five fired. Undefaulted: it is derived "
             "from the reading when the reading came from this chain, and the "
             "other four have no producer here, so a caller who knows one names "
             "it rather than this tool guessing")
    sp_handoff.add_argument(
        "--conversation", default=None,
        help="a conversation REF, never a body. A packet leaves this machine")
    sp_handoff.add_argument(
        "--owner", default=None,
        help="the bound personal work owner. Undefaulted on purpose: §10's "
             "fallback goes 「交绑定的个人工作 owner，而非擅自指定同事或 SLA」, so "
             "a default here would be 擅自指定同事")
    sp_handoff.add_argument(
        "--queue-receipt", default=None,
        help="the reference an EXISTING queue already holds this packet under. "
             "Supplying it asserts the queue accepted the packet: this tool has "
             "no queue client and calls no queue, and it records that the "
             "assertion came from the command line")
    sp_handoff.add_argument(
        "--delivery-state", default=None, choices=["DELIVERED", "FAILED",
                                                   "UNSUPPORTED", "UNKNOWN"],
        help="what the delivery path ANSWERED. This tool sends nothing, so a "
             "caller reports it. Never recorded without --queue-receipt: §10 "
             "orders 「先入…队列并得到回执，再…提醒」")
    sp_handoff.add_argument(
        "--delivery-at", default=None,
        help="when that was recorded. Never read from the clock: a time that "
             "is really when the tool ran is not when the thing happened")
    sp_handoff.set_defaults(func=cmd_support_handoff)

    document = sub.add_parser(
        "document", help="document identity and authority (§10)", **_COMMON)
    document_sub = document.add_subparsers(dest="document_verb", required=True)
    doc_resolve = document_sub.add_parser(
        "resolve",
        help="one ref and one source answer in, one identity record out. Reads "
             "a request file and answers; it declares nothing and writes "
             "nothing", **_COMMON)
    doc_resolve.add_argument(
        "--request", required=True,
        help="path to a request JSON. Required and undefaulted: there is no "
             "useful default ref, and a defaulted one would answer about a "
             "document nobody asked after")
    doc_resolve.add_argument(
        "--vocabulary", default=None,
        help="the vocabulary to read the declaration against. Default: the one "
             "that ships beside the kernel, so an installed node judges by the "
             "same closed set a source checkout does")
    doc_resolve.set_defaults(func=cmd_document_resolve)

    information = sub.add_parser(
        "information", help="what was captured from the sources (§B1): the "
                            "declared policy and the coverage achieved, kept "
                            "apart", **_COMMON)
    information_sub = information.add_subparsers(dest="information_verb",
                                                 required=True)

    info_captured = information_sub.add_parser(
        "captured",
        help="the 66 manifests under <records>/information/raw, read as capture "
             "records. Reports per component and not per object; reads nothing, "
             "writes nothing", **_COMMON)
    info_captured.add_argument(
        "--records-root", default="",
        help="the records checkout to read. Default: this node's workspace "
             "root + /records. Must be absolute")
    info_captured.set_defaults(func=cmd_information_captured)

    info_watched = information_sub.add_parser(
        "watched",
        help="the follow list (DRIFT_WATCHLIST.txt) against the capture tree: "
             "which demanded links resolve to one capture, which are silently "
             "dead, and when each was last re-checked. Reads no clock — "
             "overdue needs a --before cutoff. Reads nothing, writes nothing",
        **_COMMON)
    info_watched.add_argument(
        "--records-root", default="",
        help="the records checkout to read. Default: this node's workspace "
             "root + /records. Must be absolute")
    info_watched.add_argument(
        "--watchlist", default="",
        help="the watchlist file the collector would read, overriding "
             "<records>/information/raw/DRIFT_WATCHLIST.txt. Honoured for the "
             "same reason the default is: this verb reports what the collector "
             "was actually pointed at")
    info_watched.add_argument(
        "--before", default="",
        help="YYYY-MM-DD. Reports which watched captures had no re-check before "
             "this day. Omit it and the overdue question is reported as not "
             "asked — this verb does not read a clock")
    info_watched.set_defaults(func=cmd_information_watched)

    space = sub.add_parser(
        "space", help="where a logical object belongs, and what a meeting "
                      "contributes (§B9)", **_COMMON)
    space_sub = space.add_subparsers(dest="space_verb", required=True)

    space_resolve = space_sub.add_parser(
        "resolve",
        help="placement decision for one logical object: KEEP / MOVE / INDEX / "
             "PROPOSAL_REQUIRED. Reads a request file and answers; a MOVE is a "
             "proposal and nothing here writes", **_COMMON)
    space_resolve.add_argument(
        "--request", required=True,
        help="path to a request JSON. Required and undefaulted: there is no "
             "useful default object, and a defaulted one would answer about "
             "something nobody asked after")
    space_resolve.add_argument(
        "--vocabulary", default=None,
        help="the placement table to judge against. Default: the one that ships "
             "beside the kernel, so an installed node decides by the same closed "
             "set a source checkout does")
    space_resolve.set_defaults(func=cmd_space_resolve)

    space_meeting = space_sub.add_parser(
        "meeting",
        help="a MeetingBundle's relevance, or a cleanup plan for explicit "
             "objects. Reads a request file and answers; applying a plan "
             "belongs to the surface adapters", **_COMMON)
    # Exactly one input, and the two are different kinds of thing: `--request` is
    # a question somebody already knew how to put, `--sidecars` is the meeting's
    # own material and the question is assembled from it. Defaulting either would
    # make "nobody said which" and "this file was meant" the same command line.
    space_input = space_meeting.add_mutually_exclusive_group(required=True)
    space_input.add_argument(
        "--request",
        help="path to a request JSON. Its `kind` selects the question: "
             "`relevance` (default) or `cleanup`")
    space_input.add_argument(
        "--sidecars",
        help="path to a declaration of what this meeting actually has. The "
             "bundle is assembled from it and the kernel is asked about THAT, "
             "so the question cannot be put about members that are not there")
    space_meeting.add_argument(
        "--observed-at", default=None,
        help="ISO-8601 UTC instant to read `--sidecars` as of. Required with "
             "`--sidecars` and never read from the clock: a reading whose answer "
             "depends on when it ran cannot be compared against a recorded one")
    space_meeting.add_argument(
        "--kind", choices=("relevance", "cleanup"), default=None,
        help="override the request document's kind, for when the same bundle "
             "has to be asked the other question")
    space_meeting.add_argument(
        "--vocabulary", default=None,
        help="the policy vocabulary to judge against. Default: the one that "
             "ships beside the kernel")
    space_meeting.set_defaults(func=cmd_space_meeting)

    taxonomy = sub.add_parser(
        "taxonomy",
        help="the declared ability navigation DAG (§G)", **_COMMON)
    taxonomy_sub = taxonomy.add_subparsers(dest="subcommand", required=True)

    def _taxon_verb(name: str, help_text: str):
        """One subcommand, with the catalogue arguments every one of them needs.

        The three catalogue options are repeated per subcommand rather than
        declared once on the group. A group-level optional has to be typed
        BEFORE the subcommand — `taxonomy --repository X tree` — which is the
        opposite of how every other group in this file reads, and a flag that
        only works in an unexpected position is a flag people conclude does not
        work. `ability resolve --request ...` sets the expectation and this
        matches it.
        """
        verb = taxonomy_sub.add_parser(name, help=help_text, **_COMMON)
        verb.add_argument(
            "--repository",
            help="the ability-management checkout holding the catalogue "
                 "(default: SERVEROS_ABILITY_REPO, else the sibling checkout)")
        verb.add_argument(
            "--revision",
            help="the catalogue revision to read (default: origin/main). Read "
                 "from a git object, never from the working tree — a dirty "
                 "checkout must not become what this node believes about its "
                 "own capabilities")
        verb.add_argument("--catalogue", help="override abilities-manifest.yaml")
        verb.set_defaults(func=cmd_taxonomy)
        return verb

    _taxon_verb(
        "tree",
        "the declared DAG projected as a tree, with placement counts")
    _taxon_verb(
        "validate",
        "the declaration against itself, and against the catalogue")
    _taxon_verb(
        "show",
        "one declared taxon: its state, its abilities, its surface"
    ).add_argument("taxon")
    _taxon_verb(
        "search",
        "find a taxon by id, label or ability name — discovery is "
        "search-first, not enumerate-everything (§AA)"
    ).add_argument("query")

    workspace = sub.add_parser(
        "workspace",
        help="placement and governance: where a thing belongs (§I)", **_COMMON)
    workspace_sub = workspace.add_subparsers(dest="subcommand", required=True)

    w_overview = workspace_sub.add_parser(
        "overview", help="the declared areas, measured, area by area", **_COMMON)
    w_overview.set_defaults(func=cmd_workspace)

    # `reconcile` is deliberately NOT on this surface, though it is read-only.
    # `serverosctl`'s contract is that no subcommand mutates, and
    # `tests/test_serverosctl.py` enforces it by name — a verb called
    # `reconcile` that happens to only report is exactly the name that
    # erodes the surface's promise. The capability is unchanged and still
    # reachable: `runtime/workspace.py reconcile` has its own `main()`.

    w_policy = workspace_sub.add_parser(
        "policy",
        help="the six placement classes and their rules, readable without "
             "running anything — the one verb that answers when this "
             "process has not resolved the node's roots", **_COMMON)
    w_policy.set_defaults(func=cmd_workspace)

    # `locate` takes the class positionally and NOT as a --flag, because the
    # class is the question. There is no default class: a `locate` that
    # guessed would place a record in a scratch directory the first time it
    # was asked about something new.
    w_locate = workspace_sub.add_parser(
        "locate",
        help="where a THING of a known kind belongs, before it is created",
        **_COMMON)
    w_locate.add_argument("class_name",
                          help="src | worktree | test-env | record | "
                               "artifact | scratch")
    w_locate.add_argument("name", nargs="?",
                          help="the thing, when its name is already known")
    w_locate.set_defaults(func=cmd_workspace)

    w_inspect = workspace_sub.add_parser(
        "inspect", help="what KIND an existing path is, and what that obliges",
        **_COMMON)
    w_inspect.add_argument("path")
    w_inspect.set_defaults(func=cmd_workspace)

    w_drift = workspace_sub.add_parser(
        "drift",
        help="per-checkout git state — the git half, and only the git half. "
             "A workspace question that needed Docker running would be a "
             "workspace question nobody could ask", **_COMMON)
    w_drift.add_argument("--dirty-detail", action="store_true",
                         help="list up to 50 dirty paths per checkout")
    w_drift.set_defaults(func=cmd_workspace)

    architecture = sub.add_parser(
        "architecture",
        help="the §15/§31 gates over the whole tree: what the declarations "
             "agree with, and what the human maps should say (§15, §31–§42)",
        **_COMMON)
    architecture_sub = architecture.add_subparsers(dest="subcommand", required=True)

    def _architecture_verb(verb: str, help_text: str):
        """One checker, with its own argv surface forwarded rather than re-declared.

        The tail is NOT declared here. Declaring the checker's flags as well
        would be a second parser for the same interface, which
        `architecture_check.py` names as a design rule it does not do and which
        drifts the first time a flag is added on one side. So the verb declares
        no arguments at all and instead sets `accepts_unknown_argv`, which
        `main` reads to hand this handler the leftovers it would otherwise
        refuse (see `main`). What this costs is that `--help` after the verb
        reaches the CHECKER and prints its own usage; that is the surface a
        reader of these two verbs wants, and the wrapper's own help is the line
        above.
        """
        created = architecture_sub.add_parser(
            verb, help=help_text,
            description=f"Runs the checker beside the serveros skill with the "
                        f"arguments after `{verb}` passed through unchanged. "
                        f"Roots resolve from this node's host profile when no "
                        f"flag is given.",
            **_COMMON)
        created.set_defaults(**{
            "accepts_unknown_argv": True,
            "func": cmd_architecture_check if verb == "check"
                    else cmd_architecture_project})
        return created

    _architecture_verb(
        "check",
        "do the declarations agree with each other and with the world — the "
        "observed-side conformance report, warn-only")
    _architecture_verb(
        "project",
        "regenerate each human map's machine-owned region as a candidate. "
        "Writes candidates to --out; never rewrites a map in place")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, unknown = parser.parse_known_args(argv)
    # Almost every verb's argv surface is closed, and a misspelled flag has to
    # be an error rather than a silently ignored argument — that is the whole
    # value of `parse_args` here. The one exception is `architecture`, whose two
    # verbs forward their tail to a checker whose flags this file deliberately
    # does not re-declare, and which therefore asks for the leftovers by name.
    #
    # `nargs=argparse.REMAINDER` was the obvious way to express "everything
    # after this point, options included", and it does not work: argparse does
    # not match a REMAINDER positional when the first token is an option
    # (bpo-17050), so `architecture project --out d` parsed as an empty tail
    # plus one unrecognized argument. Measured, not assumed. The leftovers come
    # back from argparse in the order they were typed, which is what a forwarded
    # argv requires and what a per-positional reconstruction would lose.
    if unknown:
        if not getattr(args, "accepts_unknown_argv", False):
            parser.error("unrecognized arguments: " + " ".join(unknown))
        args.checker_argv = unknown
    elif getattr(args, "accepts_unknown_argv", False):
        # Set even when empty, so the handler never has to tell "no tail" from
        # "this verb was reached some other way".
        args.checker_argv = []
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
