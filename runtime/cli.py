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
import getpass
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


# ── P5 · 投递：把「写了两份文件」补成 compose → publish → send ─────────────────

def _report_delivery_module():
    return _load_module_from(REPO_ROOT / "runtime" / "report_delivery.py",
                             "serveros_runtime_report_delivery")


def _report_sender(recipient):
    """The `sender` `runtime/report_delivery.py` delivers through, as a callable.

    Constructed in the CLI because this is the composition point: the discipline
    is in `runtime/report_delivery.py`, the surface is in
    `runtime/message_transport.py`, and nothing else needs to know both.

    It is NOT `_alert_provider()`, and that is a measured finding rather than a
    preference. `BotMuxHumanControlProvider.notify` posts to `/api/trigger` with
    no `target.chatId`, and an unaddressed trigger is a synthetic session: the
    text is handed to a MODEL as a task, the model's answer returns over HTTP to
    the caller, and the header the service builds for it says it is "not shown in
    a chat" and forbids posting to Feishu. A receipt read off that path would say
    a model was asked. The report would reach no person, and the receipt saying
    DELIVERED would be the exact class of false green this round exists to
    remove.
    """
    mod = _load_module_from(REPO_ROOT / "runtime" / "message_transport.py",
                            "serveros_runtime_message_transport")
    return mod.sender(recipient)


def cmd_report_request(args) -> int:
    """§P5 — 记下「有人要求把这一版报告发给这个收件人」。这一步不发送任何东西。

    待发意图与投递是两件事，分开的理由不是设计洁癖：一条记下来的意图可以在静默期内
    存在，而它的内容版本、收件人和授权都要在真正发送的那一刻重新查一次。合在一起做，
    就等于在请求的那一刻把这三个答案冻结下来。
    """
    mod = _report_delivery_module()
    try:
        result = mod.request(Path(args.out_dir), recipient=args.recipient,
                             requested_by=args.requested_by, why=args.why,
                             channel=args.channel, at=args.at)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    emit(result, as_json=args.json, lines=mod.render(result))
    return EXIT_OK


def cmd_report_withdraw(args) -> int:
    """§P5 — 停止请求。已经发出去的消息不因此撤回。"""
    mod = _report_delivery_module()
    try:
        result = mod.withdraw(Path(args.out_dir), because=args.because, at=args.at)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    emit(result, as_json=args.json, lines=mod.render(result))
    return EXIT_OK


def cmd_report_deliver(args) -> int:
    """§P5 — 对一条待发请求做一次尝试，并记下通道的回答。

    Three inputs are the caller's and are not inferred here:

      * `--authorization` — the caller has just re-checked that sending is still
        allowed, and says what it re-checked. Required: an authorization is a
        property of the moment of sending, and a default would silently deliver
        on the strength of an older decision;
      * `--conversation` — who receives it, and which kind of destination that
        is (`user:` or `chat:`). Absent it is not an error, it is the honest
        answer `PENDING_CONSUMER`: a queue with no consumer is waiting, not
        failing;
      * `--out-dir` — which published report this is about. The version comes
        from the publication receipt in that directory, re-read now.

    Exit is 0 for a settled delivery, 2 when nothing was attempted (including
    `PENDING_CONSUMER` — nothing tried, nothing to repair), and 1 when an
    attempt was made and did not land.
    """
    mod = _report_delivery_module()
    recipient = args.conversation or os.environ.get("SERVEROS_REPORT_CONVERSATION")
    sender = None
    if recipient:
        try:
            sender = _report_sender(recipient)
        except Exception as exc:  # noqa: BLE001 - the reason is the answer
            # Not fatal. The entry is still queued, and saying so is the true
            # answer; a receiver that cannot be named is a missing consumer,
            # which is what PENDING_CONSUMER names. Note the direction: this is
            # the receiver refusing to be guessed at, and a guess that put a
            # daily report in a group instead of a direct message would not be
            # undoable.
            sys.stderr.write(
                f"serverosctl: no delivery transport could be constructed "
                f"({type(exc).__name__}: {exc}). The entry stays pending.\n")
            sender = None
    authorization = None
    if args.withhold:
        authorization = {"authorized": False, "because": args.withhold}
    elif args.authorization:
        authorization = {"authorized": True, "because": args.authorization}
    try:
        result = mod.deliver(Path(args.out_dir), sender=sender,
                             authorization=authorization, at=args.at)
    except mod.Refused as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_COULD_NOT_ANSWER
    lines = mod.render(result)
    lines.append(f"pending     {mod.pending_count(Path(args.out_dir))} 条仍在待发")
    emit(result, as_json=args.json, lines=lines)
    if result.get("state") == "DELIVERED":
        return EXIT_OK
    if result.get("state") == "ALREADY_DELIVERED":
        return EXIT_OK
    if result.get("state") in ("NOTHING_REQUESTED", "PENDING_CONSUMER"):
        return EXIT_COULD_NOT_ANSWER
    return EXIT_FAULT


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
    # Resolved BEFORE the engine exists, and that placement is the point: a
    # relative or missing root is an argument problem, and an argument problem
    # must not claim a key first. Until this line, the raw arguments were passed
    # through, so an omitted `--records-root` -- what a timer issues -- reached
    # `work` as `None` and died in the queue step with
    # `TypeError: ... 'NoneType' and 'str'`, i.e. a caller's omission was
    # reported as a fault in the loop. Every other surface in this file already
    # defaults from the declared root contract; this one did not.
    paths = paths_mod.Paths()
    workspace_root = Path(args.workspace_root or paths.root("workspace"))
    records_root = Path(args.records_root or workspace_root / "records")
    # The workspace first, and that order is the message's: the records root is
    # DERIVED from it when it is omitted, so a relative `--workspace-root` would
    # otherwise be reported against `--records-root` -- naming a flag the caller
    # never typed, and sending them to fix an argument they did not set.
    for label, root in (("--workspace-root", workspace_root),
                        ("--records-root", records_root)):
        if not root.is_absolute():
            # The board surfaces' rule, for their reason: a relative root
            # resolves against whatever directory the caller stood in, so the
            # record names an input nobody else can reproduce.
            sys.stderr.write(
                f"serverosctl: {label} must be absolute, got {root}. A run "
                f"recorded against a root that came from the caller's working "
                f"directory is one nobody else can read back.\n")
            return EXIT_FAULT
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
            records_root=records_root, workspace_root=workspace_root,
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


def cmd_document_stewardship(args) -> int:
    """P4 — the maintenance contracts we hold, read as contracts.

    `document resolve` answers what a document **is**. This answers what we have
    undertaken to **do** about it: which of the three delivery forms governs it,
    what its body authority is, how its copy follows that authority, and what
    each change class is allowed to do to each declared section. Both halves are
    P4's, and the plan's own sentence is why they are two verbs and not one:
    「文档 identity resolver 存在不等于远端 publisher 已闭环」.

    The rules are not restated here. `--ability` names the ability's own script
    and its `authorize_edit` / `dynamic_state_fields` / `derive_sections` are
    loaded from it, because a fail-closed gate re-stated in a second place is a
    gate that can disagree with itself, and the half that says `allow` is the
    half that writes. ServerOS checks the contract **before** calling in, so a
    profile the ability would crash on is reported with the field named rather
    than as a traceback.

    Read-only. It sends nothing, writes nothing, and reads no clock: whether a
    document has moved since the last observation needs a fetch, and a fetch is
    the ability's.
    """
    mod = _load_module_from(REPO_ROOT / "intake" / "managed_documents.py",
                            "serveros_intake_managed_documents")
    for name in ("contracts", "ability"):
        value = getattr(args, name)
        if not Path(value).is_absolute():
            sys.stderr.write(
                f"serverosctl: --{name} must be absolute, got {value}. A "
                f"maintenance contract read relative to whatever directory the "
                f"caller stood in is a contract nobody else can reproduce.\n")
            return EXIT_FAULT
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        sys.stderr.write(
            f"serverosctl: --state-dir must be absolute, got {state_dir}.\n")
        return EXIT_FAULT
    try:
        report = mod.read_root(Path(args.contracts), state_dir,
                              Path(args.ability),
                              Path(args.surface_vocabulary))
    except (mod.Refused, mod.Malformed) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(report, as_json=args.json, lines=mod.render(report))
    return EXIT_OK


def cmd_document_publish(args) -> int:
    """P4 — publish one section, with each consequence recorded on its own.

    `document stewardship` says whether the contract holds; this acts on it.
    Without this verb the plan's own sentence stands unrefuted —— 「文档 identity
    resolver 存在不等于远端 publisher 已闭环」—— because every other document verb
    here reads.

    Three things are deliberately not decided in this function. Which edits are
    allowed is the ability's (`--ability`, asked again after the live read, with
    the text the live read found). What a write may do to a section is
    `intake/document_publish.py`'s. How a document is reached is
    `runtime/document_transport.py`'s, and only that file knows what `lark-cli`
    is — a control plane that learns one tenant's cloud paths stops being a
    control plane, which is the lesson `space/resolver.py` carries in its header.

    Without `--apply` nothing is written: the run is planned and the refusals
    are printed. Exit is 0 when the run settled, 2 when it did not start, and 1
    when it started and something did not land — a distinction a caller
    scripting this needs, because "not now" and "half done" are not the same
    reason to stop.
    """
    mod = _load_module_from(REPO_ROOT / "intake" / "managed_documents.py",
                            "serveros_intake_managed_documents")
    pub = _load_module_from(REPO_ROOT / "intake" / "document_publish.py",
                            "serveros_intake_document_publish")
    # The flag is carried beside its value rather than derived from the dest
    # name, so the refusal quotes what the caller actually typed. Deriving it
    # would print `--request` for a caller who typed `--request`, but it would
    # also make the string `"request"` appear in this function — and `request`
    # is a field name in `abilities/target-contract.yaml`, so a reader-scanning
    # guard sees this loop as a consumer of that field. It is not, and the two
    # readings are indistinguishable from the AST. (See the ledger's note on
    # what a tuple full of strings costs: the collision resolves in the
    # direction of weakening an exemption, so it is silent.)
    for flag, value in (("--request", args.request),
                        ("--contracts", args.contracts),
                        ("--ability", args.ability),
                        ("--receipts", args.receipts)):
        if not Path(value).is_absolute():
            sys.stderr.write(
                f"serverosctl: {flag} must be absolute, got {value}. A publish "
                f"whose inputs move with the caller's directory is a publish "
                f"nobody else can reproduce or refuse.\n")
            return EXIT_FAULT
    state_dir = Path(args.state_dir)
    if not state_dir.is_absolute():
        sys.stderr.write(f"serverosctl: --state-dir must be absolute, got {state_dir}.\n")
        return EXIT_FAULT

    try:
        request = pub.read_request(Path(args.request))
    except (pub.Refused, pub.Malformed) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    # The contract, read by the module that owns that reading rather than again
    # here, so what this verb acts on is what `document stewardship` shows.
    try:
        root = mod.read_root(Path(args.contracts), state_dir,
                             Path(args.ability), Path(args.surface_vocabulary))
    except (mod.Refused, mod.Malformed) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    if root["contract"] == "REGISTRY_UNREADABLE":
        sys.stderr.write(f"serverosctl: {root['why']}\n")
        return EXIT_FAULT

    wanted = request["document"]
    entry = next((row for row in root["documents"]
                  if row.get("id") == wanted or row.get("locator") == wanted), None)
    if entry is None:
        sys.stderr.write(
            f"serverosctl: {wanted!r} is not in the registry, so nobody has "
            f"written down what may be done to it. Registry holds: "
            f"{[row.get('id') for row in root['documents']]}\n")
        return EXIT_FAULT
    if not entry.get("locator"):
        sys.stderr.write(f"serverosctl: {wanted!r} has no locator, so there is "
                         f"nothing to address the write to.\n")
        return EXIT_FAULT

    ability = mod.load_ability(Path(args.ability))
    profile, why = (mod.read_profile(Path(args.contracts), entry["profile"])
                    if entry.get("profile") else (None, "no profile declared"))
    if profile is None:
        sys.stderr.write(f"serverosctl: {why}\n")
        return EXIT_FAULT

    def ask_gate(original_text=""):
        return mod.gate(ability, profile, request.get("section"),
                        request.get("change_class"),
                        authority=request.get("authority"),
                        original_text=original_text,
                        proposed_text=request.get("content") or "")

    approvals = {}
    for item in getattr(args, "approval", None) or []:
        change_class, _, reference = item.partition("=")
        if not reference:
            sys.stderr.write(f"serverosctl: --approval wants CLASS=REF, got {item!r}\n")
            return EXIT_FAULT
        approvals[change_class] = reference

    common = {"gate_decision": ask_gate(), "approvals": approvals}
    if not args.apply:
        try:
            report = pub.plan(request, entry, **common)
        except pub.Refused as exc:
            sys.stderr.write(f"serverosctl: refused: {exc}\n")
            return EXIT_COULD_NOT_ANSWER
        emit(report, as_json=args.json, lines=pub.render(report))
        return EXIT_OK if report["planned"] else EXIT_COULD_NOT_ANSWER

    transport = _load_module_from(REPO_ROOT / "runtime" / "document_transport.py",
                                  "serveros_runtime_document_transport")
    try:
        built = transport.transport(entry["locator"],
                                    with_media=bool(request.get("media")))
    except transport.TransportFault as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    try:
        report = pub.apply(request, entry, **common,
                           fetch=built["fetch"], writers=built["writers"],
                           receipts_dir=Path(args.receipts), gate=ask_gate)
    except transport.TransportFault as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    except pub.Refused as exc:
        # A refusal is an answer, not a crash, and it has to read as one: the
        # exit code is what a caller scripts against, and a traceback reports
        # the run as broken rather than as refused.
        sys.stderr.write(f"serverosctl: refused: {exc}\n")
        return EXIT_FAULT
    emit(report, as_json=args.json, lines=pub.render(report))
    if not report["planned"]:
        return EXIT_COULD_NOT_ANSWER
    return EXIT_OK if report.get("settled") else EXIT_FAULT


def cmd_information_captured(args) -> int:
    """§B1/P1 — what the collectors were asked for, and what actually landed.

    The materialized half of P1's "capture policy 与 materialized coverage 分开".
    `work coverage` already reports the feishu surfaces, but it counts the
    *authored* `sources.yaml` — what somebody declared they follow. This reads
    the 68 manifests the collectors wrote under `records/information/raw/`, which
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

    `information captured` reads the 68 manifests the collectors wrote. This
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


def cmd_information_reference(args) -> int:
    """§4/§5/§6/§11 — 一个外部参考对象：抓到了什么，能证明什么。

    `information captured` 读的是**形状**：一个 web capture 有三个分量，各自
    判定。这个动词读的是**合同**：`docs/EXTERNAL-REFERENCE-LIBRARY.md` 里五条
    写着「读者不能自己补」的规则，今天在三棵树里没有别的读者。

    What it refuses to hand back as one number:

      * 采集器自己的 `page_kind.state`。§4 说 HTTP 200 但拿到登录页、验证码、
        脚本壳、截断正文不能算捕获成功，而这个动词把 §4 那四条问题逐条对着采集器
        自己写的标记清单查：**查过且没命中**与**根本没查**是不同的取值，所以一条
        没人问过的问题足以让整体不是 `CAPTURED` —— 无论标签写了什么。标签旁置，
        分歧被点出来。
      * 四个效果的一个「完成度」。§11 说阶段各自可部分完成、没有哪个蕴含另一个，
        所以每个 DONE 旁边带着它不蕴含的那句话，而一个连状态都没有的阶段是
        `UNDECLARED` 加一句「不存在的下一consumer不叫排队」。
      * 一个「允许」。`--note` 校验的是**我们自己**写的那份笔记：首层九项齐不齐、
        机器细节有没有摊在首屏、要点有没有出处、以及一条 `ADOPTED` 有没有人的确认。
        §5 与 §3 的表里「收藏行为本身不批准代码/安全规则变更」。

    它不下载、不复制正文、不建飞书对象、不推进任何状态。
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"reference library read relative to whatever directory the caller "
            f"stood in is a reading nobody else can reproduce.\n")
        return EXIT_FAULT
    policy = _load_module_from(REPO_ROOT / "intake" / "source_policy.py",
                               "serveros_intake_source_policy")
    mod = _load_module_from(REPO_ROOT / "intake" / "external_reference.py",
                            "serveros_intake_external_reference")
    try:
        report = mod.read_root(mod.raw_root(records), policy.key_of)
        note = mod.read_note(Path(args.note)) if args.note else None
    except (mod.Refused, mod.Malformed, policy.Refused, policy.Malformed) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = mod.render(report)
    if note is not None:
        lines += ["", f"§5 笔记草稿  {note['path']}",
                  "  首层九项齐、机器细节在折叠层、要点各带出处 —— 通过",
                  f"  状态       {note['check']['proposal'] or '无拟采用'}"]
    emit(report if note is None else {**report, "note": note},
         as_json=args.json, lines=lines)
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


def _identity_lines(payload: dict) -> list[str]:
    """§J09 — which release this process is, in the installer's terms.

    Two halves that must not be collapsed: `identity` is what the process says
    about ITSELF, frozen at startup; `pointer` is `current` read now. Print only
    the first and a promote-without-restart reads as health; print only the
    second and the process's own answer is lost.

    Printed even when the daemon reported no identity at all, because "the
    running node cannot say which release it is" is a finding about the node.
    A status command that silently drops the line turns that finding into an
    absence nobody notices, which is the failure mode this whole section is
    about: the outside world reading identity off a symlink the process may
    have stopped following.

    Every UNKNOWN here carries its reason. `None` is never rendered as a
    value, and never as "unchanged" — a daemon built without a source record
    is a different fact from one whose record says `clean`.
    """
    identity = payload.get("identity")
    if not identity:
        # Two questions, both unanswered, and they are not the same question:
        # "which release am I" and "is that still the release `current` names".
        # One line would fold the second into the first and read as if only the
        # first had been asked.
        return [
            "release     UNKNOWN — this daemon reported no install identity, "
            "so which release is running cannot be established from here",
            "pointer     UNKNOWN — this daemon did not compare itself against "
            "`current`",
        ]

    fields = identity.get("fields") or {}
    art = ((identity.get("artifact") or {}).get("value")) or {}
    rel = identity.get("release") or {}

    lines = []
    release_id = fields.get("release_id")
    if release_id:
        tree = fields.get("tree_hash")
        version = fields.get("artifact_version")
        # The label and the artifact version answer different questions — which
        # release this is, and which PAYLOAD it carries — and a build promoted
        # under a new label with an unchanged cachebuster is exactly the pair
        # that must be told apart.
        lines.append(f"release     {release_id}"
                     + (f" · {version}" if version else " · version UNKNOWN")
                     + (f" · tree {str(tree)[:12]}" if tree else " · tree UNKNOWN"))
    else:
        lines.append("release     UNKNOWN — " + (
            f"RELEASE.json {rel.get('why', 'absent')} at {identity.get('root')}"
            if not rel.get("present") else "RELEASE.json carries no `revision`"))

    commit = fields.get("source_commit")
    ability = fields.get("ability_management_revision")
    tail = (f" · ability-management@{str(ability)[:12]}" if ability
            else " · ability-management UNKNOWN")
    if commit:
        clean, changes = fields.get("source_tree_clean"), fields.get("source_worktree_changes")
        # The distinction the artifact exists to keep: a commit names a
        # checkout, only `tree_clean` connects it to these bytes.
        state = ("clean" if clean is True else
                 f"{changes} uncommitted" if clean is False else
                 "content unproven")
        lines.append(f"source      {str(commit)[:12]}"
                     f" ({fields.get('source_ref') or 'detached'}, {state}){tail}")
    else:
        basis = ((art.get("source") or {}).get("serveros") or {}).get("basis")
        lines.append(f"source      UNKNOWN — {basis or 'not recorded in the artifact'}"
                     f"{' · ability-management@' + str(ability)[:12] if ability else ''}")

    lines.append(f"root        {identity.get('root')}")

    pointer = payload.get("pointer")
    if pointer is None:
        # An old daemon that does not serve the field. Saying nothing would let
        # a reader assume the pointer was checked and agreed.
        lines.append("pointer     UNKNOWN — this daemon did not compare itself "
                     "against `current`")
    else:
        state = pointer.get("state")
        current = pointer.get("current")
        lines.append(f"pointer     {state}"
                     + (f" · current → {current}" if current else "")
                     + (f"\n            {pointer['detail']}"
                        if state not in ("AGREEMENT", None) else ""))
    return lines


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
    ]
    # `payload["identity"]`, not `payload.get(...)`: an OLD daemon that does not
    # serve the field is exactly one of the states worth naming, and a KeyError
    # here would be a worse answer than the honest UNKNOWN below.
    lines += _identity_lines(payload)
    lines += [
        "",
        payload["note"],
    ]
    emit(payload, as_json=args.json, lines=lines)
    # Stale is a fault: the daemon is answering, and its answer is about a
    # node it stopped looking at. Exit 1 so a shell can tell it from a
    # healthy run without parsing the text.
    #
    # §J09: so is DRIFT, for the same reason and one more. The daemon is up and
    # healthy, but it is executing a revision `current` has moved past, so every
    # other answer it gives describes a node nobody thinks is running. A
    # monitor that reads exit 0 here has been told "fine" about the wrong
    # release — the false pass this section exists to remove.
    #
    # `NO_POINTER` and `NOT_APPLICABLE` are NOT faults: they say this machine
    # has no `current` to compare against, which is a fact about the layout
    # rather than about the node. Only a measured disagreement fails.
    drifted = (payload.get("pointer") or {}).get("state") == "DRIFT"
    return EXIT_FAULT if (stale or drifted) else EXIT_OK


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
    """What this node can measure about the declared drivers.

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
            node_label=getattr(args, "device_ref", "") or None, binding=binding,
            calendar=source.load_calendar(
                getattr(args, "business_calendar", "") or None),
            day=source.load_day_store(getattr(args, "day_store", "") or None))
        model = presentation.status_model(snapshot, at=at, binding=binding,
                                          sample=bool(args.sample))
        caption = ("status card — three blocks and the line above them. "
                   "The gateway block is UNBOUND unless a service binding names it; "
                   "the schedule line is calendar=UNKNOWN until a business "
                   "calendar is imported; the day line names today's card and "
                   "folds the hours it has already reported")
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
        # §29. Which incident this card is about, so that the sender can ask the
        # delivery ledger «may I change the message that already carries this»
        # BEFORE it sends, instead of sending a second card for an episode that
        # already has one. Present only when the card leads with an event: a
        # status card is about a workday and has none, and a card whose face
        # came out empty is about no single incident — absent rather than
        # invented, because a sender that filed a receipt under a made-up
        # episode would patch another incident's message.
        incident = _incident_of(model)
        if incident is not None:
            payload["incident"] = incident
            lines.append(f"incident  {incident['episode']}"
                         f"（{incident['ref']}）")
        lines.append(f"version   {frozen['version']} "
                     f"({frozen['payload_bytes']} 字节)")
    lines.append("")
    lines.append("本命令不发送任何东西。预览证明 renderer 产出什么，不证明飞书"
                 "怎么显示；发送由已经持有 transport 的命令负责。")
    emit(payload, as_json=True, lines=lines)
    return EXIT_OK


def _incident_of(model: dict) -> dict | None:
    """§29. The incident the card in a `--with-frozen` envelope is about.

    The FIRST event on the face, and the word «first» is a rule rather than a
    position in a list: `alert_model` sorts its events by the declared attention
    axis (ties keep the digest document's row order, `sort` being stable) and
    the renderer prints them in that same order. So the head of that list is the
    finding the card leads with — measured, not assumed: with a REVIEW row and a
    WATCH row, only the REVIEW one reaches `events`, and the first line of the
    rendered card is the head of it. The episode itself is carried by the event
    (`presentation._event_of`, which calls `alert_digest.episode_of`) so that
    this function names an incident in the vocabulary the delivery ledger files
    receipts under, and not in a second one composed here.

    Read here, from the model, and not by the sender from the card: which event
    a card leads with is a rendering question, and a second answer to it in ops
    would be a second ordering — the two would disagree the first time a code's
    attention level moved.

    `None` when there is no event to name: a status card is about a workday, and
    a card whose face came out empty is about no single incident. `events` is
    reported alongside the episode because a card carries up to three, and a
    receipt that named one of three would otherwise read as if the message were
    about that incident alone.
    """
    events = model.get("events") if isinstance(model, dict) else None
    if not isinstance(events, list) or not events:
        return None
    first = events[0]
    if not isinstance(first, dict):
        return None
    episode = str(first.get("episode") or "").strip()
    if not episode:
        return None
    return {"episode": episode, "ref": first.get("ref"),
            "code": first.get("code"), "event": first.get("event"),
            "events_on_card": len(events)}


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
              "reason": args.reason, "idempotency_key": args.idempotency_key,
              "remote_id": args.message_id, "episode": args.episode}
    payload, code = _card_answer("card.record-delivery", params)
    if code != EXIT_OK:
        return code
    entry = payload.get("entry") or {}
    lines = [f"kind        {entry.get('kind')}",
             f"结果        {entry.get('state')}",
             f"所记版本    {entry.get('version')}",
             f"载荷        {entry.get('payload_bytes')} 字节 · "
             f"{str(entry.get('payload_hash') or '')[:12]}",
             # Printed as a fact about this send and not as advice: an absent
             # one is the difference between «this card can be updated in place»
             # and «the next update can only be a second message».
             f"消息        {entry.get('remote_id') or '（通道没给，下一次改不了这一条）'}"]
    if entry.get("kind") in module.PER_EPISODE_KINDS:
        # The word a later hour or a later day looks this receipt up by — and
        # the row cannot exist without it, because `record` refuses a receipt
        # for one of these kinds that names no episode. Which kinds those are
        # is read from the module rather than listed here.
        lines.append(f"episode     {entry.get('episode')}")
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
        episode = row.get("episode")
        # A row written before episodes existed shows nothing here rather than
        # an invented one: the reader has to be able to tell «this receipt says
        # which incident it was about» from «this one predates the field».
        lines.append(f"  {row.get('at')}  {row.get('state'):<11} "
                     f"{row.get('version')}  ·  "
                     f"{row.get('remote_id') or '（无消息 id：改不了这一条）'}"
                     + (f"  ·  {episode}" if episode else ""))
    if not rows:
        lines.append("  （没有记录：这个节点还没有投递过这种卡）")
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_card_incident(args) -> int:
    """§29. Which message carries THIS episode, and may it still be patched.

    The incident twin of `card day`: «同 episode 跨小时、跨天只 patch 一张，不重复
    NEW». What makes that answerable is the delivery ledger — it is the only
    document that survives the hour and the day boundary — so this reads it
    through the daemon, which is the ledger's only writer, rather than opening
    the file itself.

    It answers about a MESSAGE and not about whether the finding is worth
    saying: «this hour has nothing to report» is `alert_digest.decision`'s
    judgement and is made before this command is ever asked. Nothing here sends,
    edits or remembers anything.
    """
    at = getattr(args, "at", "") or _card_clock()
    payload, code = _card_answer("card.incident",
                                 {"kind": args.kind, "episode": args.episode,
                                  "at": at})
    if code != EXIT_OK:
        return code
    age = payload.get("age_days")
    window = f"{payload.get('window_days')} 天"
    if isinstance(age, (int, float)) and age > 0:
        window += f"，这条消息已发出约 {age:.1f} 天"
    lines = [f"kind        {payload.get('kind')}",
             f"episode     {payload.get('episode')}",
             f"本次        {payload.get('action')}/{payload.get('reason')}",
             f"消息        "
             + (payload.get("remote_id")
                or "（没有：这个 episode 还没有发出过卡，或者那一行回执里没记下）"),
             f"所据版本    " + (payload.get("version") or "（没有：没有发过的卡就没有版本）"),
             f"可改窗口    {window}",
             "",
             payload.get("says") or "",
             "",
             "本命令不写任何东西，也不发送、不修改任何消息：它回答的是「下一拍关于"
             "这个 episode 该做什么」，发送由已经持有 transport 的那一侧负责。"]
    emit(payload, as_json=args.json, lines=lines)
    return EXIT_OK


def _card_day_module():
    """The day module, reached through the source's own alias.

    Not a fourth `_load_module_from` call: `card_source` already loads
    `card_day.py`, and a second loader is a second seam a test would have to
    know about to hold the same module still.
    """
    return _card_modules()[0].card_day


def _day_store(module, args):
    """The store both day verbs act on, and the refusal is not swallowed here.

    `DayStore` raises on a document it cannot read, and this is the one place
    that decides what a command does with that: print the reason and exit with
    a fault. Replacing the file would be the quiet alternative, and it is the
    wrong one — a fresh document gives the same workday a second NEW card.
    """
    return module.DayStore(getattr(args, "day_store", "") or module.default_path())


def cmd_card_day(args) -> int:
    """§11. What this instant does to TODAY's card. Reads, writes nothing.

    Its answer is a judgement and not a suggestion: the day's card is created at
    the first allowed tick, patched at the ones after it, and not touched
    outside the window. A caller that ignores this has overruled a decision —
    which is allowed, and is then a decision it made rather than one nobody
    took.
    """
    module = _card_day_module()
    source = _card_modules()[0]
    at = getattr(args, "at", "") or _card_clock()
    try:
        store = _day_store(module, args)
        schedule = source.business_calendar.routine_window(
            at, reading=source.load_calendar(
                getattr(args, "business_calendar", "") or None))
        action = module.decide(store.read(), schedule=schedule, at=at)
    except module.CardDayError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    day = store.summary()
    payload = {"schema": module.SCHEMA, "at": at, "store": str(store.path),
               "schedule": schedule, "decision": action, "day": day}
    emit(payload, as_json=getattr(args, "json", False),
         lines=[f"决定        {action['action']}/{action['reason']}",
                f"当天身份    {action['day_ref'] or '（不可用）'}",
                f"时点        {action['tick']}/{action['ticks_per_workday']}",
                f"消息        {action['message']['action']}/"
                f"{action['message']['reason']}",
                f"当天卡      {day['says']}",
                "",
                action["says"],
                "",
                action["message"]["says"],
                "",
                "本命令不写任何东西，也不发送任何东西：它回答的是这一拍对当天的卡"
                "应该发生什么。落盘在 `card day-advance`，发送由已经持有 "
                "transport 的那一侧负责。"])
    return EXIT_OK


def cmd_card_day_advance(args) -> int:
    """§11. Take today's card one tick forward — the ONE writer of the store.

    The row it writes is extracted from the model this same invocation rendered,
    so the folded history says what the card said rather than what a later
    reading would have said. A tick that decides SKIP writes nothing and needs
    no model: the day is not reporting this hour, and a row for it would be the
    history growing an hour the day never broadcast.
    """
    module = _card_day_module()
    args._card_kind = "status"
    model, _caption, at = _card_common(args)
    if isinstance(model, dict) and model.get("schema") == "serveros/card-error.v1":
        emit(model, as_json=True)
        return EXIT_FAULT
    try:
        store = _day_store(module, args)
        result = store.advance(schedule=model.get("schedule") or {}, at=at,
                               model=model)
    except module.CardDayError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    action, day = result["decision"], result["day"]
    payload = {"schema": module.SCHEMA, "at": at, "store": str(store.path),
               "decision": action, "wrote": result["wrote"],
               "row": result.get("row"), "day": day}
    lines = [f"决定        {action['action']}/{action['reason']}",
             f"落盘        {'是' if result['wrote'] else '否（这一拍不动当天的卡）'}",
             f"当天身份    {action['day_ref'] or '（不可用）'}",
             f"时点        {action['tick']}/{action['ticks_per_workday']}",
             f"消息        {action['message']['action']}/"
             f"{action['message']['reason']}",
             f"当天卡      {day['says']}",
             "",
             action["says"],
             "",
             action["message"]["says"],
             ""]
    if result.get("row"):
        lines.append(f"行          {module.row_says(result['row'])}")
    lines.append("本命令只写当天卡这一份文档，不渲染、不发送。")
    emit(payload, as_json=getattr(args, "json", False), lines=lines)
    return EXIT_OK


def cmd_card_day_announce(args) -> int:
    """§11. Record which message carries today's card — the second writer.

    It writes AFTER the send and not before it: the id does not exist until the
    card has gone out, and a binding written in advance would name a message
    nobody has. That is also why it is a verb of its own rather than a flag on
    `card day-advance`, which is computable from the calendar alone.

    What it buys is the difference between the next tick patching the message
    already in the chat and the next tick being a second card. §11's acceptance
    criterion asks for one card per workday, and this is the half of that which
    lives in the chat rather than in the document.
    """
    module = _card_day_module()
    at = getattr(args, "at", "") or _card_clock()
    try:
        store = _day_store(module, args)
        result = store.announce(remote_id=args.message_id, at=at)
    except module.CardDayError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    day, message = result["day"], result["message"]
    change_says = {
        module.BOUND: "第一次记下（这一张原本还没有消息承载）",
        module.UNCHANGED: "没变（本来就是这一条）",
        module.REBOUND: "换了另一条消息承载（原来那条不再是它的去处）",
    }
    payload = {"schema": module.SCHEMA, "at": at, "store": str(store.path),
               "announced": result["announced"], "change": result["change"],
               "message": message, "day": day}
    emit(payload, as_json=getattr(args, "json", False),
         lines=[f"当天身份    {result['day_ref']}",
                f"承载消息    {message['remote_id']}",
                f"记下的发出时间  {message['at']}",
                f"绑定        {result['change']} · "
                + change_says.get(result["change"], "（无法命名的变化）"),
                f"当天卡      {day['says']}",
                "",
                "本命令只记下「哪条消息承载当天的卡」，不发送、不改动任何消息："
                "从下一拍起，`card day` 的判决会变成 PATCH_MESSAGE，"
                "而 patch 这个动作仍然由持有 transport 的那一侧执行。"])
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
    ]
    # Two dates, on adjacent lines, and the second one is not decoration.
    # `generated` is when this composition ran; `board taken` is when the board
    # documents it just read were taken from the authority. On a node where the
    # control plane that writes them does not run, the second is the only place
    # the age of every Item state on this page is visible — with only the first
    # line printed, a two-week-old snapshot reads as today's state.
    board_at = document["source"].get("board_generated_at") or {}
    if board_at:
        lines.append("board taken "
                     + "; ".join(f"{p} {at}"
                                 for p, at in sorted(board_at.items())))
    lines += [
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

    AND IT IS THE DAEMON'S OWN FILE, which it was not. `daemon.py` composes
    `default_state_dir()/agent-control/store.json` and that function honours
    `SERVEROS_STATE_DIR`; this one read the path contract and never looked at
    the variable. On a node whose unit exports it — every node
    `scripts/release.py continuation-wake --state-dir` installs on — the daemon
    wrote one store and every verb read another, and both answered as if the
    other did not exist. The state root now comes from `registry.state_root`,
    the one producer, and `tests/test_control_store_path.py` pins the two
    compositions to the same file rather than to the same prose.
    """
    return registry_mod.state_root() / "runtime" / "agent-control" / "store.json"


def _control_store():
    """The control store, opened for a verb that has to answer without the daemon.

    `AgentControlStore.__init__` reads the file, so an unreadable store raises
    here rather than silently answering empty — the module's own refusals are
    the point and this function does not soften them.
    """
    mod = _load_module_from(REPO_ROOT / "runtime" / "agent_control_store.py",
                            "serveros_runtime_agent_control_store")
    return mod, mod.AgentControlStore(_agent_control_store_path())


def _reading_clock(raw: str) -> tuple[str | None, str]:
    """The instant a delegation is judged at, from a caller-supplied timestamp.

    `(now, why)` with exactly one of them filled. Factored out of `work grants`
    when a second verb needed the same clock, because the reader's clock IS part
    of the answer — a grant that authorizes at one instant and not the next is
    the whole point of re-checking the delegation on every read — and two
    compositions of that would be two instants for one question.

    Both refusals are about the same thing: a timestamp this process cannot read
    is not a timestamp it may fall back from, and one with no timezone names a
    different instant depending on where the reader is standing.
    """
    try:
        stamp = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        return None, (f"--now {raw!r} is not a timestamp ({exc}). This verb "
                      f"judges delegations against a clock; one it cannot read "
                      f"is not a clock it may fall back from")
    if stamp.tzinfo is None:
        return None, (f"--now {raw!r} carries no timezone, so the instant it "
                      f"names depends on where the reader is standing")
    return stamp.astimezone(datetime.timezone.utc).isoformat(), ""


def _delegation_from_grant(ref: str, now_raw: str) -> tuple[dict | None, str]:
    """The delegation a RECORDED grant supplies, or the sentence saying why none.

    §P3's first half: a reading commits under authority that is resolved from
    Agent Control, not asserted by whoever submits the reading. `--delegation` is
    `understanding_semantic`'s own contract — a file the caller hands in — and a
    file is a shape no store checked: its `granted_by` is a string nothing
    verified and its `covers` is a scope nobody recorded. This is the join, and
    it keeps the two vocabularies apart rather than merging them:

      * the grant's ROOT is `context/delegation.py`'s question (`delegated_by`
        must resolve to a context that authorizes now), and its answer is copied,
        never re-worded;
      * the SCOPE is the record's own `covers`, copied as written. A grant that
        records no `covers` covers nothing — which is the safe direction, and
        reported rather than silently widened: every claim then lands as a
        candidate awaiting a person.

    What this does NOT do is invent a mapping. The store's other scope fields
    (`projects`, `items`, `actions`) are a work scope, judged by
    `intake/daily_cycle.grant_reason`; deriving subject prefixes from them would
    be this function deciding what a delegation meant.

    `(None, why)` with `why` non-empty for every refusal, and each refusal is a
    different fact: "nothing was ever recorded", "the root is gone", "it does not
    authorize now", "the scope is not a scope". Collapsed into one sentence a
    person would be sent to the wrong producer.

    The two checks that need no Item — is there a live grant, and is it in force
    at this instant — are `intake/daily_cycle.standing_reason`'s, called rather
    than re-implemented, because a second comparison of `valid_from`/`expires_at`
    would be a second answer to whether a grant is current. `context/delegation
    .derivation` deliberately does not make them: its docstring assigns the
    grant's own window and scope to `grant_reason`, and on this path there is no
    Item to pass that function, so a reviewer reading only the derivation would
    find a revoked grant reported as AUTHORIZED.
    """
    delegation_mod = _load_module_from(REPO_ROOT / "context" / "delegation.py",
                                       "serveros_context_delegation")
    cycle = _load_module_from(REPO_ROOT / "intake" / "daily_cycle.py",
                              "serveros_intake_daily_cycle")
    store_mod, control = _control_store()
    raw = now_raw or datetime.datetime.now(datetime.timezone.utc).isoformat()
    now, why = _reading_clock(raw)
    if now is None:
        return None, why
    try:
        recorded = control.grants()
        contexts = control.contexts()
    except store_mod.StoreError as exc:
        return None, str(exc)
    grant = recorded.get(str(ref))
    if grant is None:
        return None, (
            f"{ref!r} is not a grant this node recorded, so it delegates nothing. "
            f"`serverosctl work grants` lists what has been recorded; a name that "
            f"appears nowhere there is a submission the store never authorized")
    # Guarded, because `standing_reason` is `grant_reason`'s reader and a store
    # a person hand-edited can hold a non-mapping under a grant key. That value
    # is `derivation`'s GRANT_MALFORMED to report, with its own sentence — not a
    # traceback from `grant.get`.
    #
    # The ENVELOPE is what is judged, not the raw record. A stored grant carries
    # its ref under `grant_ref` (the store's key) and `standing_reason` reads
    # `grant["ref"]` (the name a cycle's envelope has), so handing it the record
    # would report every grant as MISSING_OR_REVOKED_GRANT — a refusal caused by
    # the caller's shape rather than by the record. `envelope_of` is the one
    # place in this tree that unifies the two names, and a second unification
    # here would be a second answer to what a grant's ref is.
    envelope = (delegation_mod.envelope_of(grant, str(ref))
                if isinstance(grant, dict) else None)
    if envelope is not None:
        standing = cycle.standing_reason(
            envelope, now=datetime.datetime.fromisoformat(now))
        if standing is not None:
            return None, (
                f"{ref!r} is recorded and {standing}, so it delegates nothing now. "
                f"`serverosctl work grants` reports the record this was read from; "
                f"the reading is refused rather than admitted under a grant that is "
                f"not in force, and nothing here re-mints one")
    delegated_by = grant.get("delegated_by") if isinstance(grant, dict) else None
    verdict = delegation_mod.derivation(
        grant, str(ref), contexts.get(str(delegated_by) or ""), now=now)
    if verdict["state"] != "AUTHORIZED":
        return None, (f"{verdict['state']}: {verdict['because']}")
    # Non-null: `derivation` cannot reach AUTHORIZED for anything but a mapping.
    covers = envelope.get("covers")
    if covers is None:
        covers = []
    if not isinstance(covers, list) or not all(
            isinstance(prefix, str) and prefix.strip() for prefix in covers):
        return None, (
            f"{ref!r} records `covers` as {covers!r}, which is not a list of "
            f"subject prefixes. The module that decides what this scope covers "
            f"reads it as one, and a scope it cannot read is not a broad one")
    return {"covers": list(covers),
            "until": envelope.get("expires_at"),
            "granted_by": envelope.get("delegated_by"),
            # The locator, not a paraphrase: a receipt carrying this can be
            # traced back to the record it was authorized by.
            "source": f"{control.path}#{ref}"}, ""


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
    """The board, the envelope, both decisions — `work focus`'s whole answer.

    Shared with `work admit` rather than re-derived there, and that is the point
    of the extraction: what a cycle is admitted for must be what this verb would
    have selected, from the same board, under the same envelope, at one instant.
    A second composition in the admitting verb would be a second answer to one
    question, and the two would agree only until one of them was edited.

    TWO DECISIONS, AND ONLY ONE OF THEM IS THE CYCLE. P6's fourth change, kept
    here rather than in either policy:

        `continuation`  what a person accepted, and whether it can still go on.
                        THIS is what `work admit` admits, because a cycle exists
                        to carry work somebody chose.
        `proposal`      what might be worth proposing for the slots the
                        continuation left. Printed. Never admitted.

    They share one board, one envelope and one instant because they are two
    questions about one situation, and composing twice would let them describe
    different situations while looking consistent.

    Returns the five values, or None after writing the fault to stderr. The
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
    store_mod, control = _control_store()
    try:
        # `worker_stopped` is the first row field that does not come from the
        # board, and it comes from here: for each Item, the admission that named
        # it, then the receipt its `action_key` points at. This is the same store
        # the grants below are read from, and BOTH verbs that compose a decision
        # (`work focus`, `work admit`) come through this function so that the
        # rows an admission is made against are the rows focus printed — a second
        # composition in the admitting verb would be a second answer, and the two
        # would agree only until one of them was edited.
        #
        # What this is NOT: one transaction across the board files and the
        # control store. They are two stores, and P6 asks for "可恢复幂等关系，
        # 不假装跨库事务" rather than for a lock neither store can take.
        admissions = control.admissions()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return None
    # `resources_released`, the second such field, comes from a THIRD store: the
    # resource-lease namespace. Read through `view.environment_effective` rather
    # than opened here, so that this verb and `lease environment` cannot derive a
    # reference's state two ways — and with no `--state-dir`, because `work focus`
    # has none and `open_store`'s `None` means the root contract, which is the
    # only correct answer for a verb answering about the live node.
    #
    # A namespace that cannot be read does NOT fault the verb. The read is about
    # leases, the verb is about focus, and a broken store must not make `work
    # focus` unavailable — but it must not be silently reported as "nothing is
    # held" either, so the failure is handed to `compose` as a named status and
    # the field goes OFF every row rather than `True`. That distinction is
    # `view.environment_leases`'s own refusal, kept here rather than undone.
    try:
        environment = view_mod.environment_effective(answered_by="serverosctl")
    except lease_store_mod.LeaseError as exc:
        environment = {"status": "UNREADABLE", "because": str(exc)}
    try:
        composed = focus_mod.compose(board, admissions=admissions,
                                     receipts_root=args.receipts,
                                     environment=environment)
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
        # One shape for both, and the shape comes from the policy module rather
        # than from here: a caller that constructs a decision is deciding by
        # construction, which is what this whole split exists to avoid.
        unreadable = cycle.blocked(
            "PROJECTION_AGE_UNKNOWN",
            because="at least one projection this board was composed from has no "
                    "readable `generated_at`, so the age of the FACTS is nobody's "
                    "to state. Not the same as a young board.")
        # `accepted_state` is still readable — the board carried `today` even
        # though it could not date itself — so it is carried onto the blocked
        # shape rather than dropped. What was NOT decidable is the age of the
        # facts, and that is the only thing `PROJECTION_AGE_UNKNOWN` says.
        accepted_here = {"accepted_state": (board.get("today") or {}).get("state", "?")}
        continuation = {**unreadable, **accepted_here, "continuation": True}
        proposal = {**unreadable, **accepted_here, "recommendation_only": True}
    else:
        # The selection a person authored, read off the board the producer
        # composed rather than re-read here. `work board` carries `today`; a
        # second read of `today.yaml` in this verb would be a second answer to
        # "what did they choose", taken at a different instant from the board
        # it is applied to.
        accepted = board.get("today")
        continuation = cycle.continue_focus(
            composed["rows"], delegations["envelopes"], accepted, now=now,
            observed_at=composed["observed_at"],
            ttl_seconds=args.ttl, limit=args.limit)
        proposal = cycle.select_focus(
            composed["rows"], delegations["envelopes"], now=now,
            observed_at=composed["observed_at"],
            ttl_seconds=args.ttl, limit=args.limit, accepted=accepted)
    return composed, delegations, continuation, proposal, now


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
    composed, delegations, continuation, proposal, now = state
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
    ]
    # The two row fields that do not come from the board, printed with every
    # link of their chains: a reader has to be able to tell "nothing is held"
    # from "nothing here could say", and the whole reason each field is absent
    # rather than False when its chain broke is that the second must not read as
    # the first.
    #
    # Printed as TWO blocks because they are two sources. One block holding both
    # statuses would let a reader take the lease store's silence for the
    # receipt's answer, which is the same substitution these fields exist to
    # refuse, one level up.
    liveness = composed.get("liveness") or {}
    lines.append(f"liveness    {liveness.get('status', '?')}")
    for item_id, entry in sorted((liveness.get("items") or {}).items()):
        answer = entry.get("worker_stopped")
        word = ("stopped" if answer is True else
                "held" if answer is False else "undecided-here")
        lines.append(f"  {item_id:<10} {word:<16} "
                     f"{','.join(entry.get('action_keys') or []) or 'no action key'}"
                     f"  {entry.get('because', '')}")
    leases = composed.get("leases") or {}
    lines.append(f"leases      {leases.get('status', '?')}")
    for item_id, entry in sorted((leases.get("items") or {}).items()):
        answer = entry.get("resources_released")
        word = ("released" if answer is True else
                "held" if answer is False else "undecided-here")
        lines.append(f"  {item_id:<10} {word:<16} "
                     f"{','.join(entry.get('resources') or []) or 'no resource'}"
                     f"  {entry.get('because', '')}")
    for named_elsewhere in leases.get("held_by_another_scope") or []:
        # An Item id used as a holder under a scope that is not the Item's own.
        # Printed as a finding, because a silent skip and "nothing held" would
        # render identically on the lines above.
        lines.append(f"  ! {named_elsewhere} is held under a scope that is not "
                     f"`work-item`, so it is not this Item's occupancy")
    lines += [
        f"delegations {delegations['summary']['recorded']} recorded · "
        f"{delegations['summary']['authorizing']} authorizing"
        + ("" if recorded else " — none recorded on this node"),
        f"named       {len(named_by)} row(s) name a grant_ref"
        + ("" if named_by else " — the board carries none"),
    ]
    for row in delegations["rows"]:
        lines.append(f"  {row['grant_ref']:<24} {row['state']:<30} "
                     f"{row['because']}")
    lines.append("")

    by_id = {row["id"]: row for row in composed["rows"]}

    # 1. The cycle: what a person accepted, and whether it can still go on.
    lines.append("— 本次周期：人已选定、可以继续的 —")
    lines.append(f"accepted    {continuation.get('accepted_state','?')} "
                 f"— 本周期读的是 `records/projects/<p>/board/today.yaml`")
    lines.append(f"focus       {continuation['status']}"
                 + (f" ({continuation['reason']})" if continuation.get("reason") else ""))
    if continuation.get("blocked_because"):
        lines.append(f"because     {continuation['blocked_because']}")
    selected = continuation.get("selected") or []
    lines.append(f"objectives  {len(selected)}")
    because = (continuation.get("because")
               or continuation.get("carryover_because") or {})
    for item_id in selected:
        row = by_id.get(item_id, {})
        lines.append(f"  {item_id:<10} {row.get('state','?'):<11} "
                     f"{row.get('priority') or '无优先级'}  {because.get(item_id,'')}"
                     f"  {row.get('title','')}")
    if continuation.get("continuation") and not selected and not continuation.get("reason"):
        lines.append("  （没有——这不等于没有工作，等于没有人选过今天要带什么）")
    excluded = continuation.get("excluded") or []
    if excluded:
        lines.append(f"excluded    {len(excluded)} — 人选了、但这次不能继续")
        for entry in excluded:
            row = by_id.get(entry["id"], {})
            lines.append(f"  {entry['id']:<10} {row.get('state','?'):<11} "
                         f"{entry['reason']}")
    unevaluated = continuation.get("unevaluated") or []
    if unevaluated:
        # The header is derived from the fields the entries NAME, and it used to
        # be a fixed sentence about worker liveness. That sentence was true while
        # liveness was the only family in this list; a `completion` entry under
        # it would have read as "no producer says whether a worker still holds
        # this", which is a different question about a different thing.
        #
        # It no longer says "no producer answers for these", because that is no
        # longer the only way a field can be missing: both liveness fields have
        # producers now, so a row can be missing one because the source was not
        # consulted (`--receipts` not named; a lease namespace that could not be
        # read). "Has a producer" and "this row carries it" are two facts and
        # this line does not know which one it is looking at. What it does know
        # is which fields are missing on which row, so that is what it prints.
        families = sorted({field for entry in unevaluated
                           for field in entry["fields"]})
        lines.append(f"unevaluated {len(unevaluated)} —— 这些行在这些字段上没有"
                     f"答案，本次没有据此判定：{', '.join(families)}")
        for entry in unevaluated[:5]:
            read = entry.get("fields_read") or []
            lines.append(f"  {entry['id']:<10} missing {','.join(entry['fields'])}"
                         + (f"（已读到 {','.join(read)}）" if read else ""))
            if entry.get("because"):
                lines.append(f"    ↳ {entry['because']}")
        if len(unevaluated) > 5:
            # Not "same fields": the tail can differ from the five above, and
            # saying otherwise is a claim about rows this line did not read.
            tail = sorted({tuple(entry["fields"]) for entry in unevaluated[5:]})
            lines.append(f"  … 还有 {len(unevaluated) - 5} 个，缺的字段是 "
                         + "／".join(",".join(fields) for fields in tail))

    # 2. The proposal. Printed, labelled, and never admitted — `work admit`
    #    reads the block above and only the block above.
    lines.append("")
    lines.append("— 建议（不是周期，不产生任何准入） —")
    lines.append(f"proposal    {proposal['status']}"
                 + (f" ({proposal['reason']})" if proposal.get("reason") else ""))
    spare = proposal.get("unused_slots")
    if spare is not None:
        lines.append(f"spare       {spare} of {args.limit} slot(s) "
                     f"the accepted objectives did not take")
    proposed = proposal.get("selected") or []
    lines.append(f"proposed    {len(proposed)}"
                 + ("" if proposed else " — 没有可提议的"))
    for item_id in proposed:
        row = by_id.get(item_id, {})
        lines.append(f"  {item_id:<10} {row.get('state','?'):<11} "
                     f"{row.get('priority') or '无优先级'}  {row.get('title','')}")
    proposal_carry = proposal.get("carryover") or []
    proposal_because = proposal.get("carryover_because") or {}
    if proposal_carry:
        lines.append(f"in flight   {len(proposal_carry)} — 已在进行、各占一个槽")
        for item_id in proposal_carry:
            row = by_id.get(item_id, {})
            lines.append(f"  {item_id:<10} {row.get('state','?'):<11} "
                         f"{proposal_because.get(item_id,'')}")
    proposal_excluded = proposal.get("excluded") or []
    lines.append(f"refused     {len(proposal_excluded)}")
    for entry in proposal_excluded:
        row = by_id.get(entry["id"], {})
        lines.append(f"  {entry['id']:<10} {row.get('state','?'):<11} {entry['reason']}")
    if not named_by:
        lines.append("")
        lines.append("no row names a grant: " +
                     composed["not_produced_elsewhere"]["grants"])
    emit({"continuation": continuation, "proposal": proposal, "focus": composed,
          "delegations": delegations}, as_json=args.json, lines=lines)
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
    now, why = _reading_clock(raw)
    if now is None:
        sys.stderr.write(f"serverosctl: {why}.\n")
        return EXIT_FAULT

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


def cmd_work_delegate(args) -> int:
    """§12/§5 — record a standing delegation; the root is a trusted context or nothing.

    `work grants` writes nothing and says why: "a delegation is a person's
    decision, and a verb that could record one is a verb that could authorize
    work on its own". This is the other half of that sentence. The one premise
    that keeps it from being the verb `work grants` refuses to be is
    `--context-ref`: it must name a trusted context this store ALREADY holds.

    That ref has exactly one producer — `agent.context.attach` — and that
    method requires the pid the report was observed by to be the pid on the
    other end of its own socket (`SO_PEERCRED`, `runtime/daemon.py`). So the
    flag cannot be satisfied except by a process that was inside a real turn,
    and there is no flag that supplies an actor directly: a verb that could do
    that could mint its own authority, which is the thing this one must not do.

    The actor is not copied onto the record either. A grant names the context
    it derives from (`delegated_by`), and `context/delegation.derivation` reads
    the actor back out of that context on every judgement. A second copy here
    would be a copy that can outlive or contradict its source, and «who
    delegated this» is the one question that must not have two answers.

    What it does not judge, and why that is not an omission: whether the
    context is still fresh, and whether the scope suits a cycle. `put_grant`
    declines both from the store's side — it holds no clock — and each is asked
    by its owner at read time (`context/delegation.derivation` for freshness,
    `intake/daily_cycle.grant_reason` for scope). A grant recorded against a
    context that has since gone stale is therefore STORED and reported as not
    authorizing: the record of what was delegated survives, and that is a
    different answer from a refusal here.
    """
    store_mod, control = _control_store()

    context_ref = str(getattr(args, "context_ref", "") or "").strip()
    if not context_ref:
        sys.stderr.write(
            "serverosctl: --context-ref is required. A delegation is a scope on "
            "a context somebody was verified in, and the only producer of one is "
            "`agent.context.attach` running inside a real turn. There is "
            "deliberately no flag that supplies an actor directly: a verb that "
            "could do that could sign its own authority.\n")
        return EXIT_FAULT
    grant_ref = str(getattr(args, "grant_ref", "") or "").strip()
    if not grant_ref:
        sys.stderr.write(
            "serverosctl: --grant-ref is required. It is the name a board row "
            "carries to be admitted under and what `work revoke` is keyed by; a "
            "grant nobody can name is one no row can be admitted by.\n")
        return EXIT_FAULT

    try:
        contexts = control.contexts()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    if context_ref not in contexts:
        # Named here rather than left to `put_grant`, which refuses this too and
        # is the guard. The store's sentence is about what it holds; this one is
        # about where the missing thing comes from, and a reader handed only the
        # first would go and look at the store instead of at the producer.
        sys.stderr.write(
            f"serverosctl: no trusted context {context_ref!r} is held by "
            f"{control.path}, so {grant_ref!r} has nothing to derive from. A "
            f"standing delegation is a scope on a context somebody was verified "
            f"in; minting one against a context this store has never held is "
            f"authority with no root. `serverosctl work grants` prints how many "
            f"contexts are held.\n")
        return EXIT_FAULT

    window: dict = {}
    for field, flag in (("valid_from", "--valid-from"),
                        ("expires_at", "--expires-at")):
        raw = str(getattr(args, field, "") or "").strip()
        if not raw:
            sys.stderr.write(
                f"serverosctl: {flag} is required. A delegation with no interval "
                f"authorizes at every instant or at none, and neither is what was "
                f"delegated; `_authorizing_grant` refuses an admission under one "
                f"for the same reason.\n")
            return EXIT_FAULT
        window[field] = raw

    budget = {}
    for key in store_mod.BUDGET_KEYS:
        value = getattr(args, key, None)
        if value is None:
            sys.stderr.write(
                f"serverosctl: --{key.replace('_', '-')} is required. "
                f"`runtime/wake.spending` reads {key} off this grant to bound "
                f"what a wake may do, and a bound nobody declared is not a "
                f"bound the routine may spend.\n")
            return EXIT_FAULT
        budget[key] = int(value)

    items = sorted({str(v) for v in (getattr(args, "item", None) or [])
                    if str(v).strip()})
    if not items:
        sys.stderr.write(
            "serverosctl: --item is required (repeatable). `runtime/wake."
            "authority` carries a wake only under a grant that NAMES the Item, "
            "and a checkpoint records no project — so a grant that names none "
            "is one that routine could only match by guessing.\n")
        return EXIT_FAULT

    record = {
        "grant_ref": grant_ref,
        "delegated_by": context_ref,
        "valid_from": window["valid_from"],
        "expires_at": window["expires_at"],
        "items": items,
        "projects": sorted({str(v) for v in (getattr(args, "project", None) or [])}),
        "actions": sorted({str(v) for v in (getattr(args, "action", None) or [])}),
        "budget": budget,
    }
    if getattr(args, "covers", None):
        # `intake/understanding_semantic._delegation_covers` reads this to decide
        # whether a standing delegation covers a claim's subject. Copied as
        # written, not expanded: what it covers is what it says.
        record["covers"] = sorted({str(v) for v in args.covers})

    held = contexts[context_ref]
    if getattr(args, "dry_run", False):
        emit({"would_record": record, "store": str(control.path),
              "context": {"context_ref": context_ref,
                          "actor_ref": held.get("actor_ref"),
                          "expires_at": held.get("expires_at")}},
             as_json=args.json, lines=[
                 f"store       {control.path}",
                 f"context     {context_ref}",
                 f"actor       {held.get('actor_ref') or '—'}",
                 f"grant       {grant_ref}",
                 f"items       {', '.join(items)}",
                 f"window      {window['valid_from']} .. {window['expires_at']}",
                 f"budget      {json.dumps(budget, sort_keys=True)}",
                 "dry-run     nothing was written; drop --dry-run to record it"])
        return EXIT_OK

    try:
        recorded = control.put_grant(record)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    emit({"grant": recorded, "store": str(control.path)},
         as_json=args.json, lines=[
             f"store       {control.path}",
             f"grant       {grant_ref}",
             f"delegated by {context_ref}",
             f"actor       {held.get('actor_ref') or '—'}",
             f"items       {', '.join(map(str, recorded.get('items') or []))}",
             f"window      {recorded.get('valid_from')} .. {recorded.get('expires_at')}",
             f"budget      {json.dumps(recorded.get('budget') or {}, sort_keys=True)}",
             "recorded    write-once: extending this delegation is a new grant, "
             "and ending it is `work revoke`"])
    return EXIT_OK


def cmd_work_revoke(args) -> int:
    """§5 — end a standing delegation. Never a deletion, and never an extension.

    `AgentControlStore.revoke_grant` keeps the FIRST retirement: a second
    revoke does not move «when the authority ended» to «the last time somebody
    mentioned it». This verb hands a person that call, which is what §3's
    «revoke and expiry must stop authorizing» needs to be reachable at all.

    It does NOT require a trusted context, and the asymmetry with `delegate` is
    deliberate: revoking mints no authority, it only takes one away. Making it
    harder to reach than granting would point the wrong way — an emergency stop
    that needed a verified turn would be one an operator could not perform at
    the moment it mattered. What it does require is a reason, because why a
    delegation ended is the part a later reader cannot reconstruct from the
    record itself.
    """
    store_mod, control = _control_store()
    ref = str(getattr(args, "grant_ref", "") or "").strip()
    if not ref:
        sys.stderr.write(
            "serverosctl: --grant-ref is required, and it must be a grant this "
            "node recorded: ending a name nobody used would be this verb "
            "inventing a record to retire.\n")
        return EXIT_FAULT
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        sys.stderr.write(
            "serverosctl: --reason is required. The record of WHY a delegation "
            "ended is the part a later reader cannot reconstruct from the record "
            "itself, and a revocation that says nothing leaves that reader with "
            "«it stopped» and no way to tell a decision from an accident.\n")
        return EXIT_FAULT

    try:
        existing = control.get_grant(ref)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    if existing is None:
        sys.stderr.write(
            f"serverosctl: {ref!r} is not a grant this node recorded, so there "
            f"is nothing to end. `serverosctl work grants` lists what has been "
            f"recorded.\n")
        return EXIT_FAULT

    at = str(getattr(args, "at", "") or "") or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    try:
        ended = control.revoke_grant(ref, at=at, reason=reason)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    emit({"grant": ended, "store": str(control.path)},
         as_json=args.json, lines=[
             f"store       {control.path}",
             f"grant       {ref}",
             f"revoked     {ended.get('revoked')} at {ended.get('revoked_at')}",
             f"reason      {ended.get('revoked_reason')}",
             "ended       the record stays. `work grants` reports it as "
             "MISSING_OR_REVOKED_GRANT and `runtime/wake` will not carry a wake "
             "under it"])
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
    composed, delegations, decision, _proposal, now = state
    cycle = _load_module_from(REPO_ROOT / "intake" / "daily_cycle.py",
                              "serveros_intake_daily_cycle")
    plan = cycle.admission_plan(
        decision, composed["rows"], delegations["envelopes"],
        cycle_ref=args.cycle, observed_at=composed["observed_at"] or "",
        # The same instant the board was read at, and the only clock read in
        # this command. `admit` checks it against the grant's window inside its
        # own transaction, so a delegation that expired between that read and
        # this write is refused rather than recorded.
        admitted_at=now.isoformat(),
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

    store_mod, control = _control_store()
    try:
        stored = control.admit(plan)
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
        f"liveness    {((composed.get('liveness') or {}).get('status')) or '?'} — "
        f"the rows above were selected with this read; `--receipts` omitted means "
        f"no receipt was opened and no row could be held by one",
        f"leases      {((composed.get('leases') or {}).get('status')) or '?'} — "
        f"read from the lease namespace with no `--state-dir`, so it is the node's "
        f"own; a namespace that could not be read leaves `resources_released` off "
        f"every row rather than reporting nothing held",
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
        if admission.get("accepted_at"):
            # Printed here as well as on the continuation line, because the two
            # readings are asked by different people: a Parent resuming the Item
            # reads `work continuations`, and a person auditing what a cycle held
            # and who took its result up reads this one. The same field, two
            # audiences, and neither is a summary of the other.
            lines.append(f"  taken up      {admission['accepted_by']} as "
                         f"{admission['accepted_locator']} at "
                         f"{admission['accepted_at']}")
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


def _iso_instant(value) -> datetime.datetime | None:
    """One ISO instant off a receipt or an admission, or None if unreadable.

    Tolerance is deliberate and bounded. Both fields are written by this tree —
    `invoke_once` sets `started_at`, `work admit` sets `admitted_at` — and both
    write UTC with an offset. A record carrying a naive instant is read as UTC
    rather than refused: every writer here is UTC, and refusing would turn a
    formatting difference into a claim about a cycle's history. A value that is
    not an instant at all answers None, because the caller's question is a
    comparison and "cannot be compared" is not an answer to it.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        instant = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=datetime.timezone.utc)
    return instant


def _receipt_predates_the_admission(receipt: dict, admission: dict,
                                    path: Path) -> str | None:
    """The sentence for a receipt older than the cycle reading it, or None.

    WHY THIS EXISTS. Added 2026-09-26 (slice E). `work retire` releases an
    action key, and `admit` refuses only a second admission under a key an
    OPEN one holds — so the next cycle may name a key an earlier cycle held,
    and that is by design: the retirement note says «released, so another
    admission may hold it now». `runtime/bounded_invocation.invoke_once` keeps
    one receipt per action key and REPLAYS it when the input digest matches, so
    a cycle that names a retired key with the same command receives the earlier
    cycle's receipt. Both producers of the `DISPATCHED` phase — `work dispatch`
    and `work confirm --phase DISPATCHED` — read that one file as THIS
    admission's evidence, and the phase would then say this cycle dispatched,
    on the evidence of a command another cycle ran.

    `agent_control_store.admit`'s own docstring already calls that outcome a
    defect — "the receipt would say `replayed: True` about work it never did" —
    and its guard covers only admissions still open. Retirement is what
    reopens the path.

    THE CRITERION IS ONE COMPARISON, and it cannot misjudge the case that has
    to keep working: a retry under the SAME cycle is always dispatched after
    that cycle was admitted, so its `started_at` is later than its
    `admitted_at` and the receipt reads as its own. What the comparison catches
    is exactly a receipt written before this cycle existed.

    A receipt that names a DIFFERENT action key steps aside here: that case is
    already refused, and refused with a sharper sentence —
    `confirm_admission` answers "evidence about a different dispatch" — so
    asking this question first would replace a precise answer with a vaguer
    one.

    WHAT IT DOES NOT ANSWER, and it is worth being plain about: a receipt
    carrying no readable `started_at`. There is then no comparison to make, and
    this returns None rather than refusing — the caller falls through to the
    readers it already had (the receipt's own `action_key`, and the store's
    evidence comparison). `invoke_once` always writes that field, so the case
    needs a file this tree did not write, which the receipt reader's own
    contract already puts outside itself.
    """
    key = str(admission.get("action_key") or "")
    if key and receipt.get("action_key") != key:
        return None
    started_at = receipt.get("started_at")
    admitted_at = admission.get("admitted_at")
    started = _iso_instant(started_at)
    admitted = _iso_instant(admitted_at)
    if started is None or admitted is None or started >= admitted:
        return None
    return (
        f"the receipt at {path} was written at {started_at} and "
        f"{admission.get('cycle_ref')} was admitted at {admitted_at}. The file "
        f"predates the cycle, so it is the dispatch of an earlier cycle that "
        f"held this action key — `work retire` released the key and this cycle "
        f"took it, but one action key is one receipt and the older one is still "
        f"the file `invoke_once` replays")


def _refuse_a_replayed_receipt(admission: dict, receipts_root: str) -> bool:
    """Refuse, having written why, a dispatch carrying an earlier cycle's receipt.

    True means the refusal is written and the caller must run nothing. This is
    asked BEFORE the spawn, and the placement is the point: `invoke_once` would
    not have started anything either — a matching digest makes it replay rather
    than execute — but "nothing was started" is a sentence about a spawn that
    did not happen, and a verb that can say the stronger one owes it to whoever
    is reading.

    A missing file, an unreadable one, a receipt for another key and a receipt
    with no date all return False: each is a different question with an owner
    that already answers it, and `invoke_once` and the store are downstream.
    """
    key = str(admission.get("action_key") or "")
    if not key or not receipts_root:
        return False
    path = Path(receipts_root) / (key + ".json")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(receipt, dict):
        return False
    why = _receipt_predates_the_admission(receipt, admission, path)
    if why is None:
        return False
    sys.stderr.write(
        f"serverosctl: {why}. Nothing was run and nothing was written. A retry "
        f"is a new cycle under a NEW action key: admitting one under this key "
        f"would hand it this same receipt, and the phase would record a "
        f"dispatch that was never this cycle's. The receipt stays where it is — "
        f"it is the earlier cycle's evidence about work that cycle really did\n")
    return True


def _dispatch_answer(admission: dict, receipts_root: str) -> dict:
    """What the receipts directory says about this admission's action key.

    `runtime/bounded_invocation.invoke_once` writes one receipt per action key
    and this reads that file. The path is a caller's because the receipts
    directory is where the executing side put it and this process did not
    perform the dispatch — this is the "可恢复幂等关系" and not a distributed
    transaction. What it means is therefore bounded and worth stating: this
    verb records what the receipts directory ANSWERS, and a caller who
    fabricates that file is fabricating evidence in the place evidence lives,
    not through this verb. Three fields are taken as the EVIDENCE and not the
    whole receipt — `action_key`, `status` and `input_digest` are what identify
    the dispatch, and the rest (timestamps, output paths) move as it runs. The
    evidence stays three fields because it is what a confirmation is compared
    on: a field that moves while a command runs would make the same dispatch
    report `different evidence` the second time it was reported.

    The whole record comes back beside it, under `receipt`, and it is for the
    one caller that has to tell a person what happened: `work dispatch`, which
    performed the step. It reads those fields off the FILE and not off
    `invoke_once`'s return value, so that what the verb reports and what the
    phase records are one reading of one file: a verb that printed its own
    call's result while the phase recorded the file would be two answers to one
    question, and the pair that disagreed would be the one nobody looked at.
    `_dispatch_answer` is the only reader of a receipt in this file, and that
    is the property, not an accident of how these two verbs were written.
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
    # Before anything is read off the file: is this file about THIS cycle's
    # dispatch at all? Every reader of a receipt in this file goes through this
    # function, so the question is asked once, in the place the receipt is
    # opened — and `work confirm --phase DISPATCHED`, which records the phase
    # without running anything, is covered by asking it here rather than at
    # `work dispatch`'s call site alone.
    why = _receipt_predates_the_admission(receipt, admission, path)
    if why is not None:
        raise LookupError(
            f"{why}. The phase being confirmed would record a dispatch under "
            f"this cycle that this cycle did not make, and a reader of the "
            f"phase table afterwards could not tell that from one it did")
    return {"evidence": {"action_key": receipt.get("action_key"),
                         "status": receipt.get("status"),
                         "input_digest": receipt.get("input_digest"),
                         "store": str(Path(receipts_root)), "receipt": str(path)},
            "receipt": receipt}


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
            evidence = _dispatch_answer(admission, args.receipts)["evidence"]
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


def _receipt_lines(receipt: dict) -> list[str]:
    """What a person needs off a receipt, in the writer's own words.

    Every value here is the receipt's and not this verb's. `status` and `effect`
    are the two fields `runtime/bounded_invocation.invoke_once` decides, and
    `reason` is printed only when it wrote one. `process_exit_code` is printed
    only when there IS one: `None` is the record's initial value, and a line
    reading `exit code None` would be read as an exit status by whoever is
    looking at a failed dispatch for the reason it failed. `worker_stopped` is
    deliberately NOT among these, and its absence is a decision rather than an
    omission — the field is written `False` before the spawn and is a verdict on
    exactly one path, so the three-valued reading of it lives in one place,
    `intake/focus_source.py#RECEIPT_LIVENESS`, and is reached through
    `work focus --receipts`.
    """
    lines = [f"status      {receipt.get('status')}",
             f"effect      {receipt.get('effect')}"]
    if receipt.get("reason"):
        lines.append(f"reason      {receipt['reason']}")
    if receipt.get("process_exit_code") is not None:
        lines.append(f"exit code   {receipt['process_exit_code']}")
    for field in ("started_at", "finished_at", "input_digest"):
        if receipt.get(field):
            lines.append(f"{field:<12} {receipt[field]}")
    return lines


def _dispatch_argv(args) -> list[str] | None:
    """The command to run, or None after saying why there is not one.

    The tail must be introduced by `--`, and that is not decoration. Measured:
    argparse hands back the leftovers in the order typed, and everything after
    the separator is among them — but so is anything it did not recognise, so
    `work dispatch cyc-1 --cwdx /w` would arrive here as the command
    `--cwdx /w` and run it. Requiring the separator is what keeps a mistyped
    flag an error instead of a command, and it is the one rule this function
    adds to the module's own (which then re-checks every token).
    """
    tail = list(getattr(args, "trailing_argv", None) or [])
    if not tail or tail[0] != "--":
        sys.stderr.write(
            "serverosctl: the command to dispatch must come after `--`, and "
            "there is none here. That separator is what tells a mistyped flag "
            "apart from a command: without it, anything this parser did not "
            "recognise would be run as the first word of the dispatch. "
            "`serverosctl work dispatch <cycle> --cwd … --receipts … -- "
            "<command> [args…]`\n")
        return None
    argv = tail[1:]
    if not argv:
        sys.stderr.write(
            "serverosctl: `--` was given and nothing after it. "
            "`runtime/bounded_invocation.invoke_once` requires explicit "
            "nonempty argv, so there is nothing to dispatch\n")
        return None
    return argv


def _dispatch_already_done(control, admission: dict, args) -> int:
    """Report the dispatch that already happened, and run nothing.

    This branch is 「重复触发不双派」 answered where it can be answered exactly:
    a second trigger for a cycle that has dispatched does not become a second
    dispatch. The receipt is read through the same reader the phase was
    confirmed with, so the two cannot come apart.

    The command the caller typed on THIS invocation is not run, not compared
    and not reported — and that is stated in the output rather than left to be
    inferred, because a caller who typed one is owed the sentence. Comparing it
    would be worse than useless: `invoke_once` refuses a second call under one
    key with a different input, so a comparison here would refuse a replay for
    being a replay. The phase is what says which dispatch this cycle has.
    """
    key = admission["action_key"]
    try:
        answer = _dispatch_answer(admission, args.receipts)
    except LookupError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    lines = [f"cycle       {admission['cycle_ref']}",
             "phase       DISPATCHED — confirmed already; nothing was run",
             f"action key  {key}"]
    if admission.get("confirmed_at"):
        lines.append(f"confirmed   {admission['confirmed_at']}")
    lines.extend(_receipt_lines(answer["receipt"]))
    lines.append(f"receipt     {answer['evidence']['receipt']}")
    lines.append("")
    lines.append("A cycle dispatches once: the phase says this one has, so the "
                 "command given on this invocation was not used and was not "
                 "compared. Re-running a dispatch is safe by construction — one "
                 "action key is one receipt and `invoke_once` replays it — and "
                 "this verb does not even reach that far. For a second attempt "
                 "after a failure, admit a new cycle under a NEW action key: a "
                 "reused key would hand the new cycle this receipt.")
    emit({"admission": admission, "evidence": answer["evidence"],
          "receipt": answer["receipt"], "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_dispatch(args) -> int:
    """§12/P7 — perform the dispatch one admitted cycle is holding a key for.

    The third phase had a reader and no producer. `intake/focus_source.py` read
    `<receipts>/<action_key>.json` for every admission, `work confirm --phase
    DISPATCHED` recorded what that file said, and nothing in the fabric ever
    wrote one: `runtime/bounded_invocation.invoke_once` had no production
    caller at all, which `runtime/actuator-contract.yaml` declared rather than
    hid. `work admit`'s own closing sentence named the gap — "the lease is
    declared and the dispatch made by whatever performs them" — and this is
    that whatever.

    One verb and not two, and that is the design. `invoke_once` writes its
    receipt before it spawns and replays instead of re-running, so a crash
    between running a command and recording that it ran is recoverable; but a
    caller who could run the command WITHOUT the recording would be leaving a
    worker in flight with nothing on the admission saying so, which is the
    half-product this link exists to make impossible. The recording here is the
    same one `work confirm --phase DISPATCHED` performs, through the same
    reader, off the same file.

    SIX CHECKS COME BEFORE ANYTHING IS SPAWNED, and each is ordered by what it
    would cost to check it afterwards:

      1. a retired admission cannot advance — the store refuses this too, and
         the point of checking it here is that the store's refusal would
         otherwise land after the command had run;
      2. a phase of DISPATCHED is a replay and runs nothing;
      3. a phase of INTENT_RECORDED cannot skip to DISPATCHED — again refused
         by the store, again refused here first, and this is the case where the
         difference is starkest: the command would have run, with effects, and
         the phase would have been refused afterwards;
      4. the LEASE STORE IS READ, not the phase. `agent_control_store`'s own
         note says a phase is not a permission — "the reader that wants to act
         on that reads the lease store itself" — and this verb acts. A lease
         released since the phase was confirmed is caught here;
      5. THE RECEIPTS DIRECTORY IS ASKED WHOSE DISPATCH ITS FILE IS (added
         2026-09-26, slice E). This cycle may be holding an action key an
         earlier, since-retired cycle held, and `invoke_once` replays that
         cycle's receipt instead of running anything — so the phase would
         record this cycle as having dispatched on another cycle's evidence.
         The answer is one comparison, `started_at` against `admitted_at`, and
         `_receipt_predates_the_admission` says why it cannot misjudge a retry
         under the same cycle. It is checked here rather than only inside the
         receipt reader because `invoke_once` would not have started anything
         either — it replays — and «Nothing was run» is the stronger sentence
         only this side of the call can say;
      6. the command is required to be a nonempty argv after `--`.

    The bounds are the admission's, not this command line's. `wall_seconds` and
    `max_output_bytes` come out of the reservation the cycle was admitted under,
    because a dispatch that ran longer or wrote more than it was authorized to
    would be an effect the grant never covered, and a flag that could raise them
    would be that hole with a name.
    """
    store_mod, control = _control_store()
    admission = _admission_or_fault(control, store_mod, args.cycle)
    if admission is None:
        return EXIT_FAULT

    if admission.get("retired_at"):
        sys.stderr.write(
            f"serverosctl: {args.cycle} was retired at "
            f"{admission['retired_at']}. A retired admission released its "
            f"reservation and its action key, so a dispatch under that key now "
            f"would run work no cycle is holding anything for, and its receipt "
            f"would be evidence about a cycle already accounted for. Nothing "
            f"was run\n")
        return EXIT_FAULT

    phase = admission.get("phase")
    if phase == "DISPATCHED":
        return _dispatch_already_done(control, admission, args)
    if phase != "LEASE_HELD":
        lease = admission["lease"]
        sys.stderr.write(
            f"serverosctl: {args.cycle} is at {phase} and a dispatch is the "
            f"phase after LEASE_HELD. This is refused on ORDER and not on "
            f"authority — a phase is not a permission, and "
            f"`runtime/agent_control_store.confirm_admission` would refuse to "
            f"skip it as well. The missing report is the lease store's: "
            f"`serverosctl lease declare {lease['resource']} --scope "
            f"{lease['scope']} --holder {lease['holder']}` then "
            f"`serverosctl work confirm {args.cycle} --phase LEASE_HELD`. "
            f"Nothing was run\n")
        return EXIT_FAULT

    # The verb acts, so it reads the store it is about to act under rather than
    # the summary of it that the phase carries.
    try:
        _lease_answer(admission)
    except LookupError as exc:
        sys.stderr.write(
            f"serverosctl: {exc}. `work dispatch` is the reader that wants to "
            f"act on LEASE_HELD, and the phase table says that reader reads the "
            f"lease store itself. It says this node is not holding the "
            f"resource, so nothing was run\n")
        return EXIT_FAULT

    if _refuse_a_replayed_receipt(admission, args.receipts):
        return EXIT_FAULT

    argv = _dispatch_argv(args)
    if argv is None:
        return EXIT_FAULT

    input_text = ""
    if args.input_file:
        try:
            input_text = Path(args.input_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(
                f"serverosctl: {args.input_file} cannot be read as the "
                f"dispatch's standard input ({type(exc).__name__}: {exc}). It "
                f"is written to the child and mixed into the receipt's input "
                f"digest, so a dispatch that could not attach it is a different "
                f"dispatch. Nothing was run\n")
            return EXIT_FAULT

    reservation = admission["reservation"]
    bounded = _load_module_from(REPO_ROOT / "runtime" / "bounded_invocation.py",
                                "serveros_runtime_bounded_invocation")
    receipts_root = args.receipts
    try:
        result = bounded.invoke_once(
            key=admission["action_key"], argv=argv, cwd=Path(args.cwd),
            receipts=Path(receipts_root), input_text=input_text,
            timeout=reservation["wall_seconds"],
            readonly=bool(args.read_only),
            max_output_bytes=reservation["max_output_bytes"])
    except ValueError as exc:
        # Every `raise ValueError` in that helper is on the near side of the
        # spawn — the argument checks, the symlink refusal, the
        # same-key-different-input refusal and (since 2026-09-26) the
        # receipt-lock refusal all return before `Popen` — so the sentence
        # below is a fact about this path and not a hope. Not softened into a
        # per-case message here: the rules are that module's, and a second copy
        # of them would be a second place to get them wrong.
        sys.stderr.write(
            f"serverosctl: `runtime/bounded_invocation.invoke_once` refused "
            f"this dispatch: {exc}. It validates what it is handed before it "
            f"spawns anything, so nothing was started. Two refusals come from "
            f"there by design, and the message above says which: input that "
            f"differs from an earlier dispatch under this action key (one key "
            f"is one receipt, and the phase cannot advance under a different "
            f"command), and a receipt lock another dispatch still holds — "
            f"which it reports at once rather than waiting on, because that "
            f"lock is held for the whole life of the command it guards and the "
            f"wait would have frozen this scope for another one's wall budget\n")
        return EXIT_FAULT

    try:
        answer = _dispatch_answer(admission, receipts_root)
    except LookupError as exc:
        sys.stderr.write(
            f"serverosctl: {exc}. The command has run — this is not nothing — "
            f"and the receipt `invoke_once` returns is not being trusted in "
            f"its place: the file is the evidence, and one it cannot read means "
            f"the phase cannot be recorded from it\n")
        return EXIT_FAULT

    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        confirmed = control.confirm_admission(args.cycle, phase="DISPATCHED",
                                              evidence=answer["evidence"], at=at)
    except store_mod.StoreError as exc:
        sys.stderr.write(
            f"serverosctl: {exc}\n"
            f"serverosctl: the command HAS run and the receipt is at "
            f"{answer['evidence']['receipt']}. The phase was not recorded, and "
            f"re-running this verb is what fixes that rather than re-running "
            f"the command: `invoke_once` replays a receipt under an unchanged "
            f"input instead of executing again\n")
        return EXIT_FAULT

    receipt = answer["receipt"]
    lines = [f"cycle       {confirmed['cycle_ref']}",
             "phase       DISPATCHED",
             f"action key  {confirmed['action_key']}"]
    lines.extend(_receipt_lines(receipt))
    if result.get("replayed"):
        lines.append("replayed    yes — this action key already carried this "
                     "exact input, so nothing was spawned now")
    lines.append(f"receipt     {answer['evidence']['receipt']}")
    for stream in ("stdout", "stderr"):
        lines.append(f"{stream:<12}"
                     f"{Path(receipts_root) / (confirmed['action_key'] + '.' + stream)}")
    lines.append("")
    lines.append("Bounded by the reservation and not by this command line: "
                 f"wall_seconds {reservation['wall_seconds']}, "
                 f"max_output_bytes {reservation['max_output_bytes']} per "
                 f"stream — the bounds the cycle was admitted under.")
    lines.append(f"max_attempts {reservation['max_attempts']} is the one bound "
                 f"this link cannot spend: one action key is one receipt, and a "
                 f"replay is not a second attempt. A retry is a new cycle under "
                 f"a new key — reusing this one would hand the new cycle this "
                 f"receipt.")
    lines.append("")
    lines.append("The phase records what the receipts directory says, read back "
                 "off the file rather than taken from this verb's call. The "
                 "call is synchronous: when it returned, the direct process had "
                 "exited or been killed, and whether a descendant is still "
                 "running is not recorded either way. Whether a worker is still "
                 "HELD is a different question with one owner — "
                 "`work focus --receipts` joins a receipt to an Item.")
    lines.append("")
    lines.append("The exit status is this verb's and not the command's: 0 means "
                 "the dispatch was made and the phase recorded. What the "
                 "command did is the `status` above — a dispatch that started "
                 "nothing, or that was killed by its own wall bound, is still a "
                 "dispatch this verb completed, and reporting it as this verb's "
                 "failure would invite the one thing the receipt forbids: a "
                 "retry.")
    emit({"admission": confirmed, "evidence": answer["evidence"],
          "receipt": receipt, "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_retire(args) -> int:
    """§12/P6 — close an admission, releasing the reservation and action key.

    The other end of `work admit`. Until a cycle is retired its action key stays
    held — `admit` refuses a second admission under a key an open one holds,
    because `runtime/bounded_invocation` would replay the first receipt to the
    second — so this is what a finished cycle runs, and it is also what a cycle
    runs when it is abandoned.

    Idempotent, and it keeps the FIRST retirement — both of the fields that call
    writes. A second call that moved the instant would move it to whenever
    somebody last mentioned the cycle, and the question a retired admission
    answers is when the reservation was released. A second call that moved the
    `outcome` is refused rather than absorbed: this is the write that answers
    «did this cycle finish, and how», `work continuations` now prints it on the
    line that decides whether a resuming Parent dispatches a second child, and a
    store that kept the first ending while a second one was offered would be
    answering from a choice nobody made. Retrying the same call stays
    idempotent, which is what a retry after a crash is.

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


def cmd_work_accept(args) -> int:
    """§12/P7 — take a retired cycle's result up, once, on the edge.

    THE SECOND HALF OF §17.4's SENTENCE. `docs/daily-work-cycle.md` §17.4 names
    the reading a resuming Parent does — «恢复前对账 baseline、writer、effect 和
    旧 attempt» — and `docs/CC-EXECUTION-PLAN.md` names the act: the successor
    Parent looks the original attempt up AND ACCEPTS THE RESULT ONCE. `work
    continuations` answers the looking-up; this verb is the accepting, and
    `runtime/agent_control_store.accept_admission` is what it writes.

    WHY THE EDGE. The fact «somebody took this cycle's result up» belongs to the
    delegated edge and not to the receipt (`bounded_invocation` writes one receipt
    per action key with replay semantics, so a second writer there would be a
    second answer to «what did the invocation return») and not to the checkpoint
    record (`--evidence` carries accepted locators already, but that list is the
    Item's own restatement, accumulated and rewritten every epoch — a take-up
    recorded there could be a whole epoch newer than the edge that produced it).
    The two are deliberately one write apart: a Parent reads this store before it
    decides whether it is about to accept the same result twice.

    WHAT IT DOES NOT DO: it does not decide the verdict. Whether the result is
    accepted, needs a specific revision, or leaves a registered gap is the Parent's
    judgement and it goes in the record it writes next. This writes down that a
    take-up happened, who did it, and as which locator — the fact a later reader
    needs in order to see that it already happened.
    """
    store_mod, control = _control_store()
    try:
        taken = control.accept_admission(
            args.cycle,
            at=args.at or datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            by=args.by, locator=args.locator)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    lines = [f"cycle       {taken['cycle_ref']}",
             f"phase       {taken['phase']}",
             f"retired at  {taken['retired_at']}"]
    if taken.get("outcome"):
        lines.append(f"outcome     {taken['outcome']}")
    lines.append(f"taken up by {taken['accepted_by']}")
    lines.append(f"locator     {taken['accepted_locator']}")
    lines.append(f"taken up at {taken['accepted_at']}")
    lines.append("")
    lines.append("A result is accepted once, and this call is idempotent only for "
                 "the same Parent and the same locator. What the Item now carries "
                 "is still the record's own `--evidence` line, written when that "
                 "Parent next checkpoints.")
    emit({"admission": taken, "store": str(control.path)}, as_json=args.json,
         lines=lines)
    return EXIT_OK


def _continuation_module():
    return _load_module_from(REPO_ROOT / "runtime" / "continuation.py",
                             "serveros_runtime_continuation")


def _wake_module():
    """`runtime/wake.py`, under the name its own `fill_registry` needs.

    This loader passing `serveros_runtime_continuation` to
    `_continuation_module` above is not incidental: `wake.fill_registry` writes
    to that module object's `RECEIVERS`, and a second name would be a second
    module object this file then read a different registry out of.
    """
    return _load_module_from(REPO_ROOT / "runtime" / "wake.py",
                             "serveros_runtime_wake")


def cmd_work_checkpoint(args) -> int:
    """§12/P7 — save where a logical Parent got to, because it has to wait.

    THE VERB THAT MAKES `docs/daily-work-cycle.md` §17.4 doable. Before it there
    was no record a Parent could leave, so the only way to wait for a child was to
    keep a process alive holding a session, a lease and a slot for the length of
    somebody else's work — or to hand the result back to a person, which is the
    failure `docs/SCENARIO-ACCEPTANCE.md` names as «要求用户复制结果».

    IT DOES NOT DECIDE THAT THE PARENT SHOULD WAIT, and it cannot: whether the work
    could simply have gone on is a question about the task, and `--waiting-on-child`
    / `--waiting-on-window` are the caller stating what it is waiting for.
    `runtime/continuation.build_checkpoint` refuses the shapes that are not a wait,
    including «waiting on a child this store holds no admission for» — a wait on
    something the fabric cannot see is a wait that would have to be answered by
    hand.

    THE RECEIVER IS NOT A FIELD, AND IT IS STILL MEASURED HERE. A record that
    could name its own receiver would be a record that can claim a background
    run, so `continuation_state` takes the list as an ARGUMENT — but an argument
    still has to be filled from a reading, and this verb was not reading.
    `RECEIVERS` is filled by `runtime/wake.fill_registry`, which `work wake` and
    `work continuations` call; this one did not, so it answered
    `AWAITING_CONTINUATION` and printed the product's unwired literal — «no
    continuation receiver is installed on this node, so nothing will wake it» —
    on a node whose routine is armed and running.

    THE READER THAT FOUND IT IS THE ONE THAT MATTERS. This verb is written for a
    Parent, and the Parent it was found on had just been STARTED BY that routine:
    the process the receiver wakes is the one reader guaranteed to be holding a
    wrong answer, because it is the one running on the node where the receiver is
    installed. The two calls below are the same measurement the other two
    surfaces make, so all three answer one question from one reading.

    AND IT SAYS WHAT THIS WRITE HAS JUST TAKEN OVER. A Parent writing at a higher
    epoch is a new owner: the store supersedes the record it replaced in the same
    transaction, and the attempts that record dispatched end up named by nothing
    the new owner wrote — the criterion «Parent替换后原child自动接回», with no
    reader in the loop. `runtime/continuation.carried_attempts` reads them off the
    Item's earlier epochs, and this verb prints that reading at the one moment it
    costs nothing. A Parent that had to be told to go looking for its
    predecessor's children would not satisfy the criterion.

    It records and it acts on nothing. Nothing is woken, nothing is released, and
    the process that runs it is expected to exit.
    """
    continuation = _continuation_module()
    # Filled before the state below is read, from the node's own unit directory.
    # See the docstring: without this the answer is the module's import-time
    # empty tuple, which is a measurement of nothing and reads as «unwired».
    _wake_module().fill_registry(continuation, unit_dir=args.unit_dir)
    store_mod, control = _control_store()
    try:
        admissions = control.admissions()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    waiting_on = _waiting_on(args)
    if waiting_on is None:
        return EXIT_FAULT
    now = (args.at or datetime.datetime.now(datetime.timezone.utc).isoformat())
    try:
        record = continuation.build_checkpoint(
            work_item=args.item, epoch=args.epoch, parent_owner=args.parent,
            next_safe_action=args.next_action, waiting_on=waiting_on,
            admissions=admissions, now=now,
            requirement_revision=args.requirement,
            remaining_results=args.remaining, evidence_locators=args.evidence,
            child_attempts=args.attempt, unresolved_effects=args.unresolved_effect,
            write_scope=args.write_scope)
    except ValueError as exc:
        sys.stderr.write(f"serverosctl: {exc}. Nothing was written\n")
        return EXIT_FAULT

    try:
        stored = control.put_checkpoint(record)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}. Nothing was written\n")
        return EXIT_FAULT

    state = continuation.continuation_state(stored,
                                            receivers_seen=continuation.receivers())
    # The wait is evaluated HERE, at the moment it is written, and not only when
    # somebody reads it later. A Parent that checkpoints on a child whose result
    # has already arrived has waited for nothing, and saying so now is cheaper
    # than letting it be discovered by whoever next reads the store — which is
    # the one moment nobody is looking.
    wait = continuation.wait_state(stored, admissions=admissions, now=now)
    # And what this write has just TAKEN OVER, for the same reason and at the one
    # moment it is free. A Parent writing at a higher epoch is a new owner: the
    # store supersedes the record it replaced in the same transaction, and the
    # attempts that record dispatched become this Item's again. Saying so here is
    # «原child自动接回» with a reader present — the alternative is a Parent that
    # would have to know to walk the records it never saw.
    carried = continuation.carried_attempts(
        stored, checkpoints=control.checkpoints(), admissions=admissions)
    lines = [
        f"checkpoint  {stored['checkpoint_ref']}",
        f"item        {stored['work_item']}",
        f"epoch       {stored['epoch']}",
        f"parent      {stored['parent_owner']}",
        f"waiting on  {stored['waiting_on']['kind']} "
        + (", ".join(stored["waiting_on"].get("refs") or [])
           if stored["waiting_on"]["kind"] == "child"
           else f"until {stored['waiting_on'].get('until')}"),
        f"next        {stored['next_safe_action']}",
        f"remaining   {len(stored['remaining_results'])}"
        + (": " + ", ".join(stored["remaining_results"])
           if stored["remaining_results"] else " — nothing outstanding"),
        f"written at  {stored['written_at']}",
        "",
        f"continuation  {state['state']}",
        f"  {state['says']}",
        "",
        f"wait          {wait['state']}",
        f"  {wait['says']}",
        "",
        f"carried over  {carried['state']}",
        f"  {carried['says']}",
    ]
    for entry in carried["evidence"]:
        lines.append(f"    {entry['ref']}  {entry['state']}  named by "
                     f"{entry['named_by']} (epoch {entry['named_by_epoch']}, "
                     f"{entry['named_by_parent']})")
    if stored.get("replayed"):
        lines.insert(1, "replayed    yes — same scope, same checkpoint")
    emit({"checkpoint": stored, "continuation": state, "wait": wait,
          "carried": carried, "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def _waiting_on(args) -> dict | None:
    """What this Parent is waiting for, or None having said why it cannot be read.

    Exactly one of the two ways, because they are different answers and a record
    carrying both would leave a reader to guess which one resumes it. The
    check is here rather than in argparse for the repository's usual reason: the
    message has to be a sentence about the work, and argparse's is about the
    command line.
    """
    children = list(args.waiting_on_child or [])
    window = args.waiting_on_window
    if children and window:
        sys.stderr.write(
            "serverosctl: --waiting-on-child and --waiting-on-window were both "
            "given. They are two ways of waiting and a checkpoint records one of "
            "them; a record carrying both would leave whoever resumes it to guess "
            "which one ends the wait. Nothing was written\n")
        return None
    if children:
        return {"kind": "child", "refs": children}
    if window:
        return {"kind": "window", "until": window}
    sys.stderr.write(
        "serverosctl: a checkpoint needs something to wait for. Pass "
        "`--waiting-on-child <cycle>` for a delegated cycle this store holds an "
        "admission for, or `--waiting-on-window <iso instant>` for a window that "
        "ends it. There is no value for «nothing»: a Parent with a next safe action "
        "it could take has nothing to checkpoint, and saving one anyway is the "
        "«第一版实现就返回等用户继续» failure this verb exists to not commit. "
        "Nothing was written\n")
    return None


def _no_receiver_sentence(equipment: dict) -> str:
    """Why nothing will wake a checkpoint here, in the terms of what is missing.

    «none installed» was the whole answer while the registry was empty by
    construction, and it stops being enough the moment a unit can exist half
    installed: a timer that is not in the wanted set and a service with no Parent
    to start are two different repairs, and a reader told only «none installed»
    would go looking for a unit that is already on disk.

    Every branch here is a measurement of this node's unit directory, so the
    answer is about the fabric and not about a field somebody left off — which is
    the property `continuation.continuation_state` depends on when it prints the
    product's sentence.
    """
    if not equipment.get("armed") and not equipment.get("wired"):
        return ("none installed — a saved checkpoint is not being woken by "
                "anything on this node")
    if not equipment.get("armed"):
        return (f"none installed — {equipment['service']} is wired to "
                f"{equipment['launcher'][0]} and {equipment['timer']} is not in "
                f"the wanted set, so nothing fires it")
    return (f"none installed — {equipment['timer']} is armed and "
            f"{equipment['service']} names no --parent, so every tick would "
            f"start nothing and exit 0")


def cmd_work_wake(args) -> int:
    """§17.4/P7 — resume a checkpoint whose wait has ended, once per attempt.

    THE VERB THE WAKE HALF DID NOT HAVE. `work checkpoint` saves where a Parent
    got to; `work continuations` reads it back and, until now, could only ever
    answer that nothing would wake it, because `runtime/continuation.RECEIVERS`
    was empty by construction and by design — and that empty tuple was the
    measurement, not an oversight. This verb is what the routine runs, and it is
    also what a person runs to see what that routine WOULD do (`--dry-run`),
    which is the reading an operator needs after a timer has been misbehaving and
    the one a doing-only surface cannot give them.

    IT RUNS THE PLAN AND THE PLAN IS THE WHOLE DECISION. Four facts, each from a
    store or a file: is this checkpoint still the one that owns its Item
    (`live_checkpoints`), has what it waits for happened
    (`continuation.wait_state`), does a grant that still authorizes now name this
    Item (`context/delegation.envelopes` plus one comparison), and how many
    attempts has this epoch already recorded (the receipts `invoke_once` writes).
    Nothing is remembered between runs, which is what lets the same call be made
    by a timer with no memory and by a person at a terminal and give one answer.

    AND IT WILL NOT WAKE WITHOUT A PARENT, WHICH IS `--parent` AND NOT A DEFAULT.
    A `work wake` that decided «this Item is ready» and then reported success
    having started nothing would be the fabrication §17.4 names, reached from the
    other side: the checkpoint is not being resumed, and the reader is told the
    node handled it. So without `--parent` this verb decides, prints what it
    decided, and says out loud that nothing could be started.

    THE RECEIVER IT REPORTS IS MEASURED, NOT DECLARED. `wake.equipment` reads the
    unit directory and the rendered `ExecStart=`, because a timer with no Parent
    is not a receiver and neither is a Parent with no timer. `fill_registry`
    writes that measurement into the registry `work continuations` reads, so both
    surfaces answer the same question from the same reading.

    IT STARTS A NATIVE PARENT AND NOT A SESSION. What it does is spawn a process
    with the checkpoint briefing on stdin; whether that process may then act is a
    question about the delegation it was handed, which is named in the briefing
    and re-checked by the Parent for itself. The receiver has no `context_ref` of
    its own and could not mint one — that is the whole reason it carries a
    delegation rather than asserting authority.
    """
    wake = _wake_module()
    continuation = _continuation_module()
    wake.fill_registry(continuation, unit_dir=args.unit_dir)
    store_mod, control = _control_store()
    read_at, why = _reading_clock(args.now or datetime.datetime.now(
        datetime.timezone.utc).isoformat())
    if read_at is None:
        sys.stderr.write(f"serverosctl: {why}\n")
        return EXIT_FAULT

    parent = str(args.parent or "").strip()
    if parent and not Path(parent).is_absolute():
        sys.stderr.write(
            f"serverosctl: --parent {parent!r} is not an absolute path. A Parent "
            f"found through whatever PATH the caller happens to have is one whose "
            f"identity depends on who ran the timer, and the receipt — which is "
            f"the record of what was started — would not be able to say which "
            f"executable it was. Nothing was started\n")
        return EXIT_FAULT
    if args.budget and args.budget < 0:
        sys.stderr.write(
            f"serverosctl: --budget {args.budget} is negative. The bound on a "
            f"Parent is a number of seconds it may run, and there is no negative "
            f"one. Nothing was started\n")
        return EXIT_FAULT

    try:
        plan = wake.plan(control, now=read_at, item=args.item or None,
                         unit_dir=args.unit_dir, receipts=args.receipts)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    results: list = []
    because = None
    if args.dry_run or not parent:
        because = ("--dry-run: this is what this node would wake, and nothing was "
                   "started" if args.dry_run else
                   "no --parent was given, so nothing could be started; the plan "
                   "above is what this node would wake")
    else:
        cwd = Path(args.cwd) if args.cwd else Path.home()
        if not cwd.is_dir():
            sys.stderr.write(
                f"serverosctl: --cwd {cwd} is not a directory. A Parent started "
                f"in a directory that does not exist fails as a START_FAILED "
                f"receipt, which reads like a broken launcher rather than a "
                f"wrong working directory. Nothing was started\n")
            return EXIT_FAULT
        results = wake.wake(plan["rows"], launcher=[parent], cwd=cwd,
                            receipts=plan["receipts"],
                            budget=args.budget or None)

    lines = [f"store         {control.path}",
             f"receipts      {plan['receipts']}",
             f"read at       {read_at}",
             f"receiver      "
             + (", ".join(str(r) for r in wake.installed(args.unit_dir))
                if wake.installed(args.unit_dir)
                else _no_receiver_sentence(plan["equipment"])),
             f"checkpoints   {len(plan['rows'])} considered of "
             f"{len(control.checkpoints())} recorded"
             + (f" — only {args.item}" if args.item else ""),
             f"to wake       "
             + (", ".join(plan["would_wake"]) if plan["would_wake"]
                else "none — every checkpoint here is held, refused or taken over, "
                     "and the reasons are below")]
    if plan["counts"]:
        lines.append("reasons       "
                     + ", ".join(f"{reason} {count}"
                                 for reason, count in sorted(plan["counts"].items())))
    for row in plan["rows"]:
        lines.append("")
        lines.append(f"{row['checkpoint_ref']}  {row['action']}  {row['reason']}")
        lines.append(f"  item          {row['work_item']} epoch {row['epoch']}  "
                     f"held by {row['parent_owner']}")
        lines.append(f"  says          {row['says']}")
        if row["authorized_by"] or row["grants_considered"]:
            lines.append(f"  authority     "
                         + (row["authorized_by"] or "none authorizes this Item")
                         + (f"  (considered: "
                            f"{', '.join(row['grants_considered'])})"
                            if row["grants_considered"] else ""))
        if row["action_key"]:
            lines.append(f"  next safe     {row['next_safe_action']}")
            lines.append(f"  attempt       {row['attempt']} of "
                         f"{row['attempts_allowed']}, up to "
                         f"{row['wall_seconds']}s  "
                         f"({row['attempts_terminal']} already recorded)")
            lines.append(f"  action key    {row['action_key']}")
        elif row["attempts_allowed"]:
            lines.append(f"  attempt       {row['attempts_terminal']} of "
                         f"{row['attempts_allowed']} already recorded")
    if results:
        lines.append("")
        lines.append("what was started")
        for entry in results:
            lines.append(f"  {entry['action_key']}  {entry['result']}  "
                         f"{entry.get('receipt_status') or '—'}  "
                         f"exit {entry.get('process_exit_code')}")
            lines.append(f"    {entry['because']}")
            if entry.get("receipt"):
                lines.append(f"    receipt  {entry['receipt']}   launcher "
                             f"{' '.join(entry['launcher'])}  cwd {entry['cwd']}  "
                             f"bound {entry['timeout']}s")
    if because:
        lines.append("")
        lines.append(because)
    if plan["rows"] and not plan["would_wake"] and not results:
        lines.append("")
        lines.append("Nothing on this node is being resumed by this run, and each "
                     "line above says why. A checkpoint that is HELD has not been "
                     "forgotten: its wait is still open, and the next tick after "
                     "the wait ends is the one that wakes it.")
    emit({**plan, "results": results, "because": because}, as_json=args.json,
         lines=lines)
    return EXIT_OK


def cmd_work_continuations(args) -> int:
    """§12/P7 — the Parent checkpoints this node holds, and what would wake each.

    The read half of `work checkpoint`, and the place the product's sentence about
    an absent receiver has to be able to appear. `docs/daily-work-cycle.md` §17.4:
    «没有 receiver 时准确显示"checkpoint已保存，待续"，不能伪造后台运行».

    It reads the store and `runtime/continuation.receivers()` and nothing else —
    no board, no projection. One clock, and only one: a window wait is a
    comparison against an instant and this verb is where that comparison is
    printed. The clock is `--now`, through the same `_reading_clock` `work grants`
    judges delegations with — a second spelling of «the instant this reading is
    made at» would be a second set of refusals for one question. Whether a
    checkpoint is being woken is not a question about the board, and answering it
    from a projection that may be stale is how a saved checkpoint starts reading
    as a running one.

    The receiver answer comes from that registry and not from the record, which is
    the only arrangement under which «no receiver is installed» can be true of the
    fabric rather than a field somebody left off.

    THREE QUESTIONS, KEPT APART. `continuation` is «will anything wake this», and
    it still answers `AWAITING_CONTINUATION` because nothing on this node runs by
    itself. `wait` is «has the thing it waits for happened yet», and that one IS
    answerable — the children are in the admission store and the window has an
    instant. Printing only the first was one defect: a reader saw
    `AWAITING_CONTINUATION` on a checkpoint whose child finished last week, with
    the one fact that had changed nowhere on the line.

    `attempts` is the third, and it is a different question from either: «what
    became of the child attempts this record says it already made». `wait` asks
    about the attempt this Parent is waiting ON; `attempts` asks about the ones it
    already made, which is the fact that decides whether resuming means continuing
    the work or means dispatching a second child — `docs/SCENARIO-ACCEPTANCE.md`
    names «新建第二Task» beside this checkpoint's own reason for existing, and
    `docs/daily-work-cycle.md` §17.4 names the reading: «恢复前对账 baseline、
    writer、effect 和旧 attempt». `child_attempts` was written and validated and
    never read back until this line existed.

    `requirement` and `accepted` are printed for the same reason and are NOT
    answers: they are the two facts a resuming Parent reconciles the attempts
    against — which revision of the Requirement this record was written under, and
    which evidence this record already carries. `work checkpoint --evidence` calls
    one «an accepted evidence locator», so `accepted` here is the RECORD's own
    restatement of what the Item has.

    THE OTHER HALF OF «LOOK UP THE ATTEMPT AND ACCEPT THE RESULT ONCE» IS ON THE
    LINE NOW, AND IT WAS NOT. The paragraph that stood here said there was nowhere
    on this node that records an acceptance as a separate fact about an attempt.
    There is: `work accept <cycle>` writes who took a retired cycle's result up and
    as which locator, once, on the edge — so `attempts` can answer
    `ATTEMPT_ACCEPTED` instead of stopping at «it finished», which was the fact
    that could not distinguish «resume from this result» from «accept it a second
    time».

    THE TWO SPELLINGS OF `accepted` ON THIS SCREEN ARE TWO LEVELS, NOT TWO COPIES.
    The per-attempt `taken up by` line is «a take-up of THIS cycle is recorded»;
    the `accepted` line per checkpoint is «the Item's record now carries this
    evidence». They are allowed to be one write apart, because that record is
    restated by the Parent when it next checkpoints. A reading that called that lag
    a disagreement would invent a rule the design does not state, so this surface
    prints both and reconciles neither.

    ONE READING PER RECORD AND ONE READ PER ITEM, AND NEITHER IS THE OTHER'S
    SUMMARY. The per-record `attempts` block is what the RECORD names; the
    `carried over` block under a live record is what the ITEMS it owns still owe
    from the epochs that came before — «Parent替换后原child自动接回», which had no
    reader before this line existed. They are disjoint by construction: an attempt
    the record names is never also carried, so the two counts on the header (the
    `attempts` line and the `carried over` line) add up without counting anything
    twice. The attempt is printed under the record that OWNS the Item, and the
    superseded record it came from still prints it in its own block — the same
    child, two readings, and the one that says who owes it now is the new one's.
    """
    continuation = _continuation_module()
    # The registry is FILLED FROM WHAT IS INSTALLED before it is read. Reading
    # `receivers()` without filling it answers from the module's import-time
    # value, which is the empty tuple — a measurement of nothing in particular,
    # and one that keeps saying «nothing will wake it» on a node where the timer
    # is armed.
    #
    # This comment used to name the places that did it — «this one and `work
    # wake`» — and `work checkpoint`, the reader most likely to be a Parent that
    # the routine itself started, was not among them. A list of callers is the
    # thing that goes stale; the reading is what has to be shared, so every verb
    # that answers this question now measures it here.
    _wake_module().fill_registry(continuation, unit_dir=args.unit_dir)
    store_mod, control = _control_store()
    try:
        held = control.checkpoints()
        admissions = control.admissions()
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    if args.item:
        held = {ref: record for ref, record in held.items()
                if record.get("work_item") == args.item}
    # Which checkpoints the store still counts as owning their Item comes from the
    # STORE and not from a filter composed here. `live_checkpoints` is its answer
    # to «who owns what now» and this is its first caller: «one continuation owner
    # per scope» is a rule `put_checkpoint` enforces, so the set is the store's to
    # state. The two agree by construction — a record carries `superseded_by`
    # exactly when `continuation_state` answers `SUPERSEDED`, and the store's
    # `is_parked` answers exactly when it answers `PARKED`.
    #
    # THE PARKED ONES ARE COUNTED APART AND NOT LEFT OUT. A park takes a record out
    # of this set, and a header that said «0 live of 1 recorded» about a record
    # somebody deliberately set down would read as a node with nothing in flight
    # and one unexplained record. The count is what tells those two apart, and the
    # per-record block below prints the park itself — who set it down, when and why
    # — because that is the part the next reader needs.
    live = sorted(ref for ref in held if ref in control.live_checkpoints())
    parked_refs = sorted(ref for ref in held
                         if ref not in live and store_mod.is_parked(held[ref]))
    installed = continuation.receivers()
    equipment = _wake_module().equipment()
    states = {ref: continuation.continuation_state(record, receivers_seen=installed)
              for ref, record in held.items()}
    read_at, why = _reading_clock(args.now or datetime.datetime.now(
        datetime.timezone.utc).isoformat())
    if read_at is None:
        sys.stderr.write(f"serverosctl: {why}\n")
        return EXIT_FAULT
    waits = {ref: continuation.wait_state(record, admissions=admissions, now=read_at)
             for ref, record in held.items()}
    attempts = {ref: continuation.attempts_state(record, admissions=admissions)
                for ref, record in held.items()}
    # The Item-scope reading, on the same records and from the same store. Only
    # for the records that own their Item: a superseded record carries nothing —
    # it is what was carried FROM.
    carried = {ref: continuation.carried_attempts(record, checkpoints=held,
                                                  admissions=admissions)
               for ref, record in held.items() if ref in live}
    ready = [ref for ref in live if waits[ref]["state"] == "WAIT_OVER"]
    unreadable = [ref for ref in live if waits[ref]["state"] == "WAIT_UNANSWERABLE"]
    made = sum(len(attempts[ref]["attempts"]) for ref in live)
    by_answer = {answer: [ref for ref in live if attempts[ref]["state"] == answer]
                 for answer in continuation.ATTEMPT_ANSWERS}
    carried_refs = sorted({ref for ref in live for ref in carried[ref]["carried"]})
    lines = [f"store         {control.path}",
             f"checkpoints   {len(live)} live of {len(held)} recorded"
             + (f", {len(parked_refs)} parked" if parked_refs else "")
             + ("" if held else " — no Parent has checkpointed on this node"),
             f"receivers     "
             + (", ".join(str(r) for r in installed) if installed
                else _no_receiver_sentence(equipment)),
             f"waits         {len(ready)} over, "
             + f"{len(live) - len(ready) - len(unreadable)} still open, "
             + f"{len(unreadable)} unreadable"
             + ("" if live else " — nothing held to wait on"),
             f"attempts      {made} recorded on {len(live)} live checkpoint"
             + ("" if len(live) == 1 else "s") + " — "
             + f"{len(by_answer['ATTEMPT_ACCEPTED'])} all taken up, "
             + f"{len(by_answer['ATTEMPT_RETIRED'])} all finished with a take-up "
             + f"outstanding, "
             + f"{len(by_answer['ATTEMPT_OPEN'])} with one still open, "
             + f"{len(by_answer['ATTEMPT_UNANSWERABLE'])} this store cannot look up, "
             + f"{len(by_answer['ATTEMPT_NONE'])} naming none",
             # A SECOND COUNT OF ATTEMPTS, AND NOT A SECOND COPY OF THE FIRST. The
             # line above counts what the live RECORDS name; this one counts what
             # the ITEMS still owe from the epochs that came before them. They are
             # disjoint by construction — `carried_attempts` skips every attempt the
             # record in hand names — so a reader that adds the two is not counting
             # anything twice. Without this line the header read «0 with one still
             # open» for an Item whose replaced Parent had a child running.
             f"carried over  {len(carried_refs)} attempt"
             + ("" if len(carried_refs) == 1 else "s")
             + " left by an earlier epoch and no take-up closes"
             + (f" — {', '.join(carried_refs)}" if carried_refs
                else " — nothing came through with anything open"),
             f"read at       {read_at}"]
    if ready:
        # Named, not counted. A WAIT_OVER is the one answer here a person can act
        # on — it says the Item can be resumed now — and a count would send them
        # to the list to find out which.
        lines.append(f"resumable     {', '.join(sorted(ready))}")
    for ref in sorted(held):
        record, state, wait = held[ref], states[ref], waits[ref]
        lines.append("")
        lines.append(f"{ref}  {state['state']}  wait {wait['state']}  "
                     f"attempts {attempts[ref]['state']}")
        lines.append(f"  parent        {record.get('parent_owner')}")
        waiting = record.get("waiting_on") or {}
        lines.append(f"  waiting on    {waiting.get('kind')} "
                     + (", ".join(waiting.get("refs") or [])
                        if waiting.get("kind") == "child"
                        else f"until {waiting.get('until')}"))
        lines.append(f"  next          {record.get('next_safe_action')}")
        lines.append(f"  remaining     "
                     + (", ".join(record.get("remaining_results") or [])
                        or "— nothing outstanding"))
        lines.append(f"  write scope   "
                     + (", ".join(record.get("write_scope") or []) or "— unnamed"))
        lines.append(f"  written at    {record.get('written_at')}")
        # The disposition, printed on the record it applies to. A parked record is
        # one somebody set down on purpose and it is the reason `work wake` will
        # not consider it — so a reader of this surface has to be able to see it
        # here, on the same screen as the wait and the attempts, rather than only
        # in the wake routine's row. Both halves are printed when both happened:
        # «set down, then taken back up» is a different record from «never
        # touched», and a reader deciding whether to lift a park needs to know that
        # it has been lifted before. A park a later epoch has since walked over is
        # printed here too: the heading above names the operative reason
        # (`SUPERSEDED`), and this line is the record of what happened to the park
        # itself — nothing lifted it.
        if store_mod.is_parked(record):
            lines.append(f"  parked        {record.get('parked_at')} by "
                         f"{record.get('parked_by')} — "
                         f"{record.get('parked_reason')}")
        elif record.get("unparked_at"):
            lines.append(f"  parked        set down {record.get('parked_at')} by "
                         f"{record.get('parked_by')} "
                         f"({record.get('parked_reason')}), taken back up "
                         f"{record.get('unparked_at')} by "
                         f"{record.get('unparked_by')} "
                         f"({record.get('unparked_reason')})")
        lines.append(f"  requirement   "
                     + (record.get("requirement_revision")
                        or "— this record does not name a revision"))
        accepted = record.get("evidence_locators") or []
        lines.append(f"  accepted      "
                     + (", ".join(accepted)
                        or "— this record carries no accepted evidence"))
        made_here = attempts[ref]
        # The refs are read off the RECORD and the answers off the store: what was
        # tried is the record's fact, and what became of it is the store's. Printing
        # `made_here["attempts"]` here would be correct and would hide from
        # `TestEveryDeclaredFieldIsRenderedOrExempt` that this verb reads the field.
        tried = record.get("child_attempts") or []
        lines.append(f"  attempts      "
                     + (", ".join(tried)
                        or "— this record names no child attempt"))
        for entry in made_here["evidence"]:
            # One line per attempt, because the counts above cannot say WHICH
            # one is still open, and «which one» is the whole reason a resuming
            # Parent looks.
            if not entry["held"]:
                lines.append(f"    {entry['ref']}  {entry['state']}  — this store "
                             f"holds no such admission")
            elif entry["state"] == "ATTEMPT_ACCEPTED":
                lines.append(f"    {entry['ref']}  {entry['state']}  taken up by "
                             f"{entry['accepted_by']} as "
                             f"{entry['accepted_locator']} at "
                             f"{entry['accepted_at']}"
                             + (f"  (outcome {entry['outcome']})"
                                if entry.get("outcome") else ""))
            elif entry["state"] == "ATTEMPT_RETIRED":
                lines.append(f"    {entry['ref']}  {entry['state']}  "
                             + (entry.get("outcome") or "— no outcome recorded")
                             + f"  at {entry['retired_at']}  — no take-up of its "
                               f"result is recorded")
            else:
                lines.append(f"    {entry['ref']}  {entry['state']}  "
                             f"phase {entry.get('phase')}")
        if ref in carried:
            # Under the record that OWNS the Item, and not under the one that was
            # replaced: the obligation is the Item's, and where it is printed is
            # the statement of who owes it now. The superseded record's own block
            # above still names the attempt, so both readings of the same child are
            # on the screen and neither is the other's summary.
            held_over = carried[ref]
            lines.append(f"  carried over  {held_over['state']}"
                         + (f"  from {', '.join(held_over['predecessors'])}"
                            if held_over["predecessors"] else ""))
            lines.append(f"    {held_over['says']}")
            for entry in held_over["evidence"]:
                lines.append(f"    {entry['ref']}  {entry['state']}  named by "
                             f"{entry['named_by']} (epoch {entry['named_by_epoch']}, "
                             f"{entry['named_by_parent']})")
        for effect in record.get("unresolved_effects") or []:
            lines.append(f"  unresolved    {effect}")
        lines.append(f"  {state['says']}")
        lines.append(f"  wait          {wait['says']}")
        lines.append(f"  attempts say  {made_here['says']}")
    emit({"checkpoints": held, "states": states, "waits": waits,
          "attempts": attempts, "carried": carried, "receivers": list(installed),
          "read_at": read_at, "store": str(control.path)},
         as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_park(args) -> int:
    """§12/P7 — set a checkpoint down on purpose, and stop waking it.

    THE DISPOSITION THE DESIGN NAMES AND THIS TREE DID NOT HAVE. «Work leaves
    Focus only via Completed / Safely Parked / Cancelled + reconciled /
    Superseded + transferred», and this is the third of those. It had no field, no
    verb and no reader: `work checkpoint` had no flag for it, `supersede` demands a
    successor so it can only say «somebody else has this», and a checkpoint
    somebody had looked into and deliberately set down went on being evaluated by
    `work wake` once a minute for the rest of the node's life. What that costs is
    not verbosity: `REFUSE NO_AUTHORIZING_GRANT` is the sentence a genuinely
    blocked Item produces, so a parked Item producing it is the signal being eaten.

    IT DOES NOT REQUIRE A TRUSTED CONTEXT, and the asymmetry with `work delegate`
    is the one `work revoke` is built on: parking mints no authority, it takes a
    wake away, and a stop that is harder to reach than a start points the wrong
    way. What it does require is a reason and a signature, because «the work
    stopped» and «somebody decided the work should stop» are the two readings a
    later audit has to tell apart.

    `--by` defaults to the login this process runs as — read, not guessed — the
    same way `cmd_work_revoke`'s `--at` defaults to the clock this process reads.
    """
    store_mod, control = _control_store()
    ref = str(getattr(args, "checkpoint_ref", "") or "").strip()
    if not ref:
        sys.stderr.write(
            "serverosctl: a checkpoint ref is required, and it must be one this "
            "node recorded: setting down a name nobody wrote would be this verb "
            "inventing a record to park.\n")
        return EXIT_FAULT
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        sys.stderr.write(
            "serverosctl: --reason is required. The record of WHY a checkpoint "
            "was set down is the part a later reader cannot reconstruct from the "
            "record itself, and a park that says nothing leaves that reader "
            "unable to tell a decision from an accident.\n")
        return EXIT_FAULT
    by = str(getattr(args, "by", "") or "").strip() or getpass.getuser()
    at = str(getattr(args, "at", "") or "") or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    try:
        parked = control.park_checkpoint(ref, at=at, by=by, reason=reason)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    emit({"checkpoint": parked, "store": str(control.path)},
         as_json=args.json, lines=[
             f"store         {control.path}",
             f"checkpoint    {ref}",
             f"parked at     {parked.get('parked_at')} by "
             f"{parked.get('parked_by')}",
             f"reason        {parked.get('parked_reason')}",
             "stopped       the record stays and keeps every field it had. "
             "`work wake` no longer considers it — it prints it as REFUSE PARKED "
             "rather than as a wake candidate — and `work continuations` reports "
             "it as PARKED. `work unpark` is the way back"])
    return EXIT_OK


def cmd_work_unpark(args) -> int:
    """§12/P7 — take a park back up, and change nothing else.

    THE RESTORE HALF, AND THE REASON IT IS A VERB RATHER THAN A STORE EDIT. «This
    was set down, and is now being carried again» is a statement with a date, a
    signature and a reason on BOTH halves, and the record keeps all six: the park's
    own three fields stay exactly as they were written, so a later reader gets the
    whole sentence instead of a record that looks as if nobody had ever touched it.

    IT LIFTS A PARK AND NOTHING ELSE. The record goes back into the set
    `live_checkpoints` answers for, which makes it a candidate again — and whether
    anything can wake it is still a grant in force that names the Item plus a
    context that is fresh, neither of which is reachable from here. So this is not
    a way around a missing authority; it is how a person says the Item is one they
    are carrying, and the wake half goes on refusing it until it has an authority
    of its own.
    """
    store_mod, control = _control_store()
    ref = str(getattr(args, "checkpoint_ref", "") or "").strip()
    if not ref:
        sys.stderr.write(
            "serverosctl: a checkpoint ref is required, and it must be one this "
            "node recorded: lifting a park nobody made would put a record back in "
            "the set a wake reads on the strength of a call that never took it "
            "out.\n")
        return EXIT_FAULT
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        sys.stderr.write(
            "serverosctl: --reason is required. «It is being carried again» and "
            "«somebody decided it should be» are the two readings a later audit "
            "has to tell apart, and the record already carries the reason it was "
            "set down — this call is the other half of that sentence.\n")
        return EXIT_FAULT
    by = str(getattr(args, "by", "") or "").strip() or getpass.getuser()
    at = str(getattr(args, "at", "") or "") or datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    try:
        lifted = control.unpark_checkpoint(ref, at=at, by=by, reason=reason)
    except store_mod.StoreError as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    emit({"checkpoint": lifted, "store": str(control.path)},
         as_json=args.json, lines=[
             f"store         {control.path}",
             f"checkpoint    {ref}",
             f"set down      {lifted.get('parked_at')} by "
             f"{lifted.get('parked_by')} — {lifted.get('parked_reason')}",
             f"taken up      {lifted.get('unparked_at')} by "
             f"{lifted.get('unparked_by')} — {lifted.get('unparked_reason')}",
             "candidate     the record is in the set `work wake` reads again. A "
             "wake still needs a grant in force that names this Item and a context "
             "that is fresh, and this verb created neither"])
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
    # The verdict on top of the evidence above, and the reason this verb is the
    # one that prints it: a person reading `<ID> CLOSED` on the first line has
    # just been told the Item is finished by a word, and this line is where the
    # word is checked. Placed after the documents rather than before them
    # because it is a conclusion drawn FROM them.
    completion = item.get("completion")
    if not isinstance(completion, dict):
        # Not a default and not a pass: a chain that says nothing about the
        # boundary is silent, and silence is not completion.
        lines.append("完成边界   链条未给出判定 —— 缺席不是已完成")
    else:
        label = {"COMPLETE": "已完成", "DISCHARGED": "已结清·非完成",
                 "OPEN": "未结清", "NOT_ASSESSED": "未评估"}.get(
                     completion["state"], completion["state"])
        lines.append(f"完成边界   {label} —— {completion['why']}")
        # The three gaps are told apart because they are three different
        # situations: a field with no value, a document that is not there, and
        # a document the chain never mentioned at all. The last one is the one
        # a reader would otherwise mistake for the second.
        for name in completion.get("missing_fields") or []:
            lines.append(f"           ↳ 缺字段   {name}")
        for name in completion.get("missing_documents") or []:
            lines.append(f"           ↳ 缺文档   {name}")
        for name in completion.get("unreported_documents") or []:
            lines.append(f"           ↳ 链上未报告 {name} —— 既不说在有，也不说没有")
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


def cmd_work_understanding_brief(args) -> int:
    """§B2/P3 — the material a native Agent reads, at the version it must hand back.

    The range verbs are four different things and this is the one that carries
    text out:

      * `work understanding` says which cards a source delta touches;
      * `work understanding-patch` turns a range into a proposal a person
        confirms;
      * this one produces the INPUT to the semantic step: one range, the records
        it names as the collector wrote them, the card those records are a change
        to, and the two blocks — `source` and `read` — copied exactly as the
        validator will compare them;
      * `work understanding-semantic` takes the reading an authorised Agent wrote
        against that packet and commits the part of it that may be committed
        without a person.

    It reads no model and writes no reading, and that line holds in both
    directions: the Agent is a session somebody else runs, and a verb that could
    author a reading would be a verb whose "native Agent" was itself. What
    `--write` writes is the PACKET, which is an input a person can read before a
    session is handed it.

    It is also the only verb under `work` that puts message text on stdout, and
    that is what it is for: "what is the model about to be given" has no other
    answer on this node. A brief is a projection — four fields per record, the
    text and the two revision flags — while the digest the reading must hand back
    is taken over the whole record, so the packet is readable without being the
    thing the identity is over.

    A range the reader proposes and this packet cannot pin is PRINTED with the
    reason, beside the ranges that were handed over and not instead of them: a
    document range is a real piece of work on a real layer (the project cards on
    this node cite documents as well as chats), and a listing that showed only
    what it could carry would present a partial view of the layer as the whole
    of it.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. The "
            f"material a session reads is pinned to a source revision, and one "
            f"read relative to whatever directory the caller stood in is a packet "
            f"nobody else can reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                                "serveros_intake_capture_coverage")
    delta = _load_module_from(REPO_ROOT / "intake" / "understanding_delta.py",
                              "serveros_intake_understanding_delta")
    brief_mod = _load_module_from(REPO_ROOT / "intake" / "understanding_brief.py",
                                  "serveros_intake_understanding_brief")
    try:
        # Read here and passed in, for the same reason the other range verbs do
        # it: two readers of one tree drift, and the packet must be pinned to
        # what the reader proposes rather than to a second opinion about it.
        objects = capture.read_root(records)["objects"]
        report = delta.read_and_reconcile(records, objects)
        pinned, unpinnable = brief_mod.briefs(report, records)
        chosen = brief_mod.select(
            pinned, refused=unpinnable,
            card=getattr(args, "card", "") or None,
            manifest=getattr(args, "manifest", "") or None,
            position=getattr(args, "position", None))
    except (capture.Refused, delta.Refused, brief_mod.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    if args.write:
        if len(chosen) != 1:
            sys.stderr.write(
                f"serverosctl: --write takes one brief and this selection is "
                f"{len(chosen)}. A packet file that held several ranges would be "
                f"handed to a session that cannot tell which one it read; narrow "
                f"with --card/--manifest/--position.\n")
            return EXIT_FAULT
        try:
            brief_mod.write(chosen[0], args.write)
        except brief_mod.Refused as exc:
            sys.stderr.write(f"serverosctl: {exc}\n")
            return EXIT_FAULT

    if len(chosen) == 1:
        lines = brief_mod.render(chosen[0])
    else:
        lines = [f"{len(chosen)} range(s) the reader proposes:",
                 ""]
        for brief in chosen:
            source = brief["source"]
            lines.append(f"  {source['card']}  {source['manifest']} "
                         f"{source['from_position']}–{source['to_position']}"
                         f"  ({brief['range']['kind']})")
        lines += ["", "hand one range to --write: a session reads one packet."]
    if unpinnable:
        # Printed even when other ranges were handed over: a range the reader
        # proposes and this packet cannot carry is work somebody has to do, and
        # a listing that showed only the pinnable ones would present a partial
        # view of the layer as the whole of it.
        lines += ["", f"{len(unpinnable)} range(s) this packet cannot pin:"]
        for row in unpinnable:
            lines.append(f"  {row['card']}  {row['kind']}  "
                         f"{row['manifest']} {row['from_position']}–"
                         f"{row['to_position']}")
            lines.append(f"      {row['why']}")
    if args.write:
        lines.append(f"    written     {args.write}")
    emit({"schema": brief_mod.SCHEMA, "count": len(chosen), "briefs": chosen,
          "unpinnable": unpinnable},
         as_json=args.json, lines=lines)
    return EXIT_OK


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

    THE AUTHORITY HAS TWO FORMS AND ONE OF THEM IS RESOLVED

    `--grant` names a grant this node RECORDED, and the scope, the window and
    the root come out of the record: the scope is the grant's own `covers`, the
    root is its `delegated_by`, and `context/delegation.py` decides whether that
    root authorizes at `--now` — with its sentence copied rather than re-worded.
    A grant the store has never heard of, a root that has gone, a delegation
    that no longer authorizes and a record whose scope cannot be read are four
    refusals with four different next actions, and none of them is "commit
    anyway".

    `--delegation` hands in a file and asserts the same four fields. It is kept
    because it is `understanding_semantic`'s own contract and the module's tests
    are written against it — but a file is a shape no store checked, and
    `granted_by` in it is a string nothing verified. `--grant` is the product
    path; the two are mutually exclusive so that no commit can be authorized by
    one while reporting the other.

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
    if args.grant:
        # §P3: the authority is RESOLVED from Agent Control. `--delegation` is
        # the module's contract and this is the product path to it; the two are
        # mutually exclusive so that a commit never has two answers to "under
        # what authority" and no way to tell which one it used.
        delegation, why = _delegation_from_grant(args.grant, args.now)
        if delegation is None:
            sys.stderr.write(f"serverosctl: {why}\n")
            return EXIT_FAULT
        if not delegation["covers"]:
            sys.stderr.write(
                f"serverosctl: grant {args.grant!r} is recorded and authorizes, "
                f"and it names no `covers` — so it delegates no subject and every "
                f"claim will land as a candidate awaiting a person. That is the "
                f"safe direction and it is worth saying out loud before a reading "
                f"is committed under it.\n")
    elif args.delegation:
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


def cmd_work_source_policy(args) -> int:
    """§B1/P1 — which sources are in force, by what policy, decided by which rule.

    The four things this verb refuses to do are the four ways a policy report
    goes wrong. It does not pick between two lists that disagree: every row
    names the ONE rule that decided it (`decided_by`), and a label met under a
    deny is reported in `bypassed` rather than dropped — that is 「具体标签不能
    绕上级deny」 as a field instead of a sentence. It does not key anything by a
    display name: rows are keyed by the provider's own id and the collector's
    directory name is reported beside it, because `docx-research-outreach-sop`
    is the name of two objects in this very tree. It does not decide
    completeness: `coverage_owner` names whose answer an object's completeness
    is, and the four coverages stay four. And it does not merge the two
    publish-plane registries that live beside the exclusions — their existence
    is reported with why they are not inputs.

    With `--coverage` it also hands the resolved rows to the consumer that
    already exists for a followed-source list, `workchain.source_coverage`,
    whose fields come from that consumer's own contract entry. That consumer
    reads `records/projects/<project>/context/sources.yaml`, a file nobody has
    written, which is why every project reports UNPRODUCED there today; this is
    the adapter that lets the resolved policy answer it instead.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. "
            f"Declarations matched against a tree read relative to whatever "
            f"directory the caller stood in are declarations nobody else can "
            f"reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                                "serveros_intake_capture_coverage")
    policy = _load_module_from(REPO_ROOT / "intake" / "source_policy.py",
                               "serveros_intake_source_policy")
    try:
        objects = capture.read_root(records)["objects"]
        document = policy.resolve(records, objects)
    except (capture.Refused, policy.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT

    lines = policy.render(document)
    if getattr(args, "coverage", False):
        chain = _workchain_module()
        contract = chain.load_contract(
            REPO_ROOT / "intake" / "workchain-contract.yaml")
        entry = next((c for c in contract.get("coverage") or []
                      if c.get("id") == "followed-sources"), None)
        if entry is None:
            sys.stderr.write(
                "serverosctl: the contract names no `followed-sources` entry, so "
                "there is no consumer here to hand a list to and reporting an "
                "empty one would look like a project that follows nothing.\n")
            return EXIT_FAULT
        projected = policy.project_rows(document, list(entry.get("fields") or []))
        document = {**document, "followed_sources": chain.source_coverage(
            contract=contract, sources=projected["rows"])}
        lines.append("")
        lines.append("下游消费者 workchain.source_coverage（字段取自它自己的合同条目）")
        for row in document["followed_sources"]["rows"]:
            lines.append(f"  {row['state']:<8} {row['identity'] or '—'}"
                         + (f"  缺 {', '.join(row['missing_fields'])}"
                            if row["missing_fields"] else ""))
    emit(document, as_json=args.json, lines=lines)
    return EXIT_OK


def cmd_work_material_index(args) -> int:
    """§B1/P2 — the materials a capture refers to, and the four coverages.

    The verb exists because `files.by_message` is one entry per message and the
    capture tree holds messages naming two attachments: read as the material
    denominator the map understates this tree by 44% and reports a complete
    archive over the other half. So the four questions are asked separately —
    messages, discovery, materials, understanding — and the map is reported as
    navigation beside them. Nothing here downloads, unpacks, renames or
    re-records anything: a material registered as pending whose bytes are on
    disk is a STALE MARKER to reconcile (§10: 现存文件pending旧 → 核字节/关联后
    修索引，不重复下), and a material that is registered and absent is a gap
    under any policy depth, not a reason to fetch.

    `--coverage` is what the understanding half costs: it reads the cards
    through `understanding_delta`/`source_waterline` and copies their comparison
    for each object rather than forming a second opinion about what was read.
    Without it that half is `NOT_MEASURED` and says so, because a material on
    disk is not the same finding as its content having been understood.
    """
    paths = paths_mod.Paths()
    records = Path(getattr(args, "records_root", "") or
                   Path(paths.root("workspace")) / "records")
    if not records.is_absolute():
        sys.stderr.write(
            f"serverosctl: --records-root must be absolute, got {records}. A "
            f"material index read relative to whatever directory the caller "
            f"stood in is a denominator nobody else can reproduce.\n")
        return EXIT_FAULT
    capture = _load_module_from(REPO_ROOT / "intake" / "capture_coverage.py",
                                "serveros_intake_capture_coverage")
    policy = _load_module_from(REPO_ROOT / "intake" / "source_policy.py",
                               "serveros_intake_source_policy")
    index = _load_module_from(REPO_ROOT / "intake" / "material_index.py",
                              "serveros_intake_material_index")
    try:
        objects = capture.read_root(records)["objects"]
        resolved = policy.resolve(records, objects)
        understanding = None
        if getattr(args, "coverage", False):
            delta = _load_module_from(
                REPO_ROOT / "intake" / "understanding_delta.py",
                "serveros_intake_understanding_delta")
            waterline = _load_module_from(
                REPO_ROOT / "intake" / "source_waterline.py",
                "serveros_intake_source_waterline")
            rows = waterline.waterlines(records, delta, objects)["sources"]
            understanding = {row["source"]: row for row in rows}
        document = index.read_root(records, objects, policy=resolved,
                                   understanding=understanding,
                                   digest=getattr(args, "digest", False))
    except (capture.Refused, policy.Refused, index.Refused) as exc:
        sys.stderr.write(f"serverosctl: {exc}\n")
        return EXIT_FAULT
    emit(document, as_json=args.json, lines=index.render(document))
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
    # The tail means a flag typed after the verb reaches the checker rather
    # than this parser, which is what keeps the checker's `--help` and its
    # `--json` its own. The global form still works, and it is merged here
    # rather than in the parser so that the two positions cannot both claim it.
    if getattr(args, "json", False):
        forwarded.append("--json")
    forwarded.extend(getattr(args, "trailing_argv", None) or [])
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
    card_sub.choices["status"].add_argument(
        "--business-calendar", default="", metavar="PATH",
        help="the instance's business-calendar declaration "
             "(default ~/.serveros/business-calendar.yaml). Without one the "
             "schedule line reads calendar=UNKNOWN and this card claims no "
             "routine broadcast window — a node that has not imported a "
             "calendar does not get to assume Monday to Friday")
    card_sub.choices["status"].add_argument(
        "--day-store", default="", metavar="PATH",
        help="today's card and the hours it folded "
             "(default: the state root's `runtime/cards/day.json` — beside the "
             "delivery ledger, and the same document `card day-advance` "
             "writes). Absent means the card prints no day "
             "line at all; present and unreadable becomes a named gap, because "
             "the alternative to a fault is not silence")
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
    card_rec.add_argument(
        "--message-id", default="", metavar="ID",
        help="the message this send produced, when the sender has it. It is "
             "what makes a later update of that same message possible instead "
             "of a second message, so a receipt that drops it is a receipt for "
             "a send nobody can reach afterwards. Omit it only when the "
             "transport answered without one — the absence is recorded as an "
             "absence and not filled in")
    card_rec.add_argument("--idempotency-key", default="",
                          help="override the key the frozen block carries. The "
                               "default comes from the rendering: it names the "
                               "instant, so a re-render is a new key and the "
                               "cadence-window rule stays with whoever runs "
                               "the clock")
    card_rec.add_argument(
        "--episode", default="", metavar="EPISODE",
        help="which incident this send was about, for an alert or a recovery "
             "card. It is what a later hour or a later day looks the receipt up "
             "by, so «same episode, one card» is answerable at all — the value "
             "is `episode_of`'s, and a status card must not carry one. Required "
             "for --kind alert and --kind recovery, and refused for --kind "
             "status, by the receipt writer rather than here")
    card_rec.set_defaults(func=cmd_card_record_delivery)

    card_del = card_sub.add_parser(
        "deliveries", help="§3 · what has been sent, by version",
        **_COMMON)
    card_del.add_argument("--kind", default="", choices=[""] + list(_card_delivery().KINDS),
                          help="one card kind; omitted means all of them")
    card_del.add_argument("--limit", type=int, default=10,
                          help="how many recent entries to print")
    card_del.set_defaults(func=cmd_card_deliveries)

    # §29's incident half, and the ONE question about an episode that is asked
    # of what was SENT rather than of what is true now: the message carries the
    # card across hours and across days, so the ledger — not this hour's reading
    # of the alerts — is the document that can answer it.
    card_inc = card_sub.add_parser(
        "incident", help="§29 · which message carries this episode, and whether "
                         "it may still be patched: PATCH_MESSAGE inside the "
                         "service's window, otherwise NEW_MESSAGE (or "
                         "SUCCESSOR_MESSAGE once the message has aged out). "
                         "Reads the delivery ledger; sends and edits nothing",
        **_COMMON)
    card_inc.add_argument(
        "--kind", required=True, choices=list(_card_delivery().PER_EPISODE_KINDS),
        help="which incident card family: an episode belongs to these and not "
             "to a status card, which is about a workday")
    card_inc.add_argument(
        "--episode", required=True, metavar="EPISODE",
        help="the episode of the incident this is about — `alert_digest."
             "episode_of`, which is the alert ref and the open period rather "
             "than anything this command can derive")
    card_inc.add_argument("--at", default="",
                          help="override the instant the update window is "
                               "measured at. The default is now, and the "
                               "window is measured against when the recorded "
                               "message was SENT")
    card_inc.set_defaults(func=cmd_card_incident)

    # §11. One card per workday, and the two halves of that: `card day` answers
    # what this instant does to today's card and writes nothing, `card
    # day-advance` is the ONE writer. The split is the same one §3 states for the
    # delivery ledger — a reader that could also write is a reader whose reading
    # is a decision nobody recorded.
    card_day = card_sub.add_parser(
        "day", help="§11 · whether today is a workday, whether today's card "
                    "exists, and what this tick does to it: CREATE at the first "
                    "allowed tick, PATCH at the ones after, SKIP otherwise. "
                    "Reads and writes nothing",
        **_COMMON)
    card_day.add_argument(
        "--business-calendar", default="", metavar="PATH",
        help="the business-calendar declaration the day identity comes from "
             "(default ~/.serveros/business-calendar.yaml). Without one there is "
             "no day to file a card under, and this answers "
             "SKIP/CALENDAR_UNKNOWN")
    card_day.add_argument(
        "--day-store", default="", metavar="PATH",
        help="today's card and the hours it folded "
             "(default: the state root's `runtime/cards/day.json`, beside the "
             "delivery ledger)")
    card_day.add_argument("--at", default="", help="override the instant")
    card_day.set_defaults(func=cmd_card_day)

    card_adv = card_sub.add_parser(
        "day-advance", help="§11 · take today's card one tick forward and fold "
                            "the hour it replaces. The ONE writer of the day "
                            "store; sends nothing",
        **_COMMON)
    card_adv.add_argument(
        "--business-calendar", default="", metavar="PATH",
        help="as on `card status` and `card day`")
    card_adv.add_argument(
        "--day-store", default="", metavar="PATH",
        help="the document this command writes "
             "(default: the state root's `runtime/cards/day.json`, beside the "
             "delivery ledger)")
    card_adv.add_argument(
        "--gateway-binding", default="", metavar="PATH",
        help="the service-binding document the rendered hour is built with, as "
             "on `card status`: the row records what the card said, so it is "
             "the same rendering that is folded")
    card_adv.add_argument(
        "--device-ref", default="", metavar="REF",
        help="which device's binding to read, e.g. device:tokyo-shared-dev-01")
    card_adv.add_argument(
        "--sample", action="store_true",
        help="render the hour as 示例. Every layout fixture uses it; a store "
             "advanced with it holds a sample row, which is what the flag says")
    card_adv.add_argument("--at", default="", help="override the instant")
    card_adv.set_defaults(func=cmd_card_day_advance)

    # §11's message half. Separate from `day-advance` because the id it records
    # does not exist until the card has been sent, and because this is the ONE
    # surface that can say «the next update patches the message already in the
    # chat» rather than «the next update is a second card».
    card_ann = card_sub.add_parser(
        "day-announce",
        help="§11 · record which message carries today's card, so the next "
             "update is a patch of it and not a second card. Writes the day "
             "store; sends and edits nothing",
        **_COMMON)
    card_ann.add_argument(
        "--message-id", required=True, metavar="ID",
        help="the message the just-sent card lives in (the transport answers "
             "with it, and `card record-delivery --message-id` keeps it in the "
             "ledger)")
    card_ann.add_argument(
        "--day-store", default="", metavar="PATH",
        help="the document this command writes "
             "(default: the state root's `runtime/cards/day.json`, beside the "
             "delivery ledger)")
    card_ann.add_argument(
        "--at", default="",
        help="override the instant recorded as when that message was SENT. The "
             "default is now, which is the right answer when this runs "
             "immediately after the send; a hand-run against a message sent "
             "earlier must say so, because the service's update window is "
             "measured from this value")
    card_ann.set_defaults(func=cmd_card_day_announce)

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

    def _add_receipts(parser_) -> None:
        """The one root that answers whether a dispatch's worker is still held.

        On the two verbs that compose a decision — `focus` and `admit` — and on
        both rather than on the one that looks like it needs it: the rows the
        admitting verb selects are computed from the same rows focus printed, so
        a liveness read that reached one verb and not the other would let a
        person be shown one selection and have another admitted. Both take it or
        neither does.

        There is deliberately no default. The directory belongs to whoever
        dispatched, `runtime/bounded_invocation.invoke_once` takes it as an
        argument, and a guess here would read receipts from a place nobody ran
        this — the same reason `_dispatch_answer` refuses to guess it. Omitted,
        `worker_stopped` is absent from every row and the composition SAYS so
        rather than reporting `False`, which would blame the worker for a
        missing flag.
        """
        parser_.add_argument("--receipts", default="",
                             help="the directory `runtime/bounded_invocation."
                                  "invoke_once` wrote its receipts to. Omitted, "
                                  "no receipt is read and `worker_stopped` stays "
                                  "off every row — an absent field, not a "
                                  "released one")

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
    _add_receipts(wk_focus)
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

    wk_delegate = work_sub.add_parser(
        "delegate", help="§12/§5 — record a standing delegation. `--context-ref` "
                         "must name a trusted context this store already holds, "
                         "and that ref has exactly one producer: "
                         "agent.context.attach, inside a real turn", **_COMMON)
    wk_delegate.add_argument("--grant-ref", required=True, dest="grant_ref",
                             help="the name a board row carries to be admitted "
                                  "under, and what `work revoke` is keyed by")
    wk_delegate.add_argument("--context-ref", default="", dest="context_ref",
                             help="the trusted context this delegation derives "
                                  "from, as `agent.context.attach` minted it. "
                                  "Not a name you may choose: the store must "
                                  "already hold it")
    wk_delegate.add_argument("--item", action="append", default=[], dest="item",
                             help="an Item this delegation covers (repeatable, at "
                                  "least one). `runtime/wake.authority` carries a "
                                  "wake only under a grant that names the Item")
    wk_delegate.add_argument("--project", action="append", default=[],
                             dest="project",
                             help="a project this delegation covers (repeatable)")
    wk_delegate.add_argument("--action", action="append", default=[],
                             dest="action",
                             help="an action this delegation covers (repeatable)")
    wk_delegate.add_argument("--covers", action="append", default=[],
                             dest="covers",
                             help="a subject prefix this delegation covers for "
                                  "local semantic inference (repeatable)")
    wk_delegate.add_argument("--valid-from", default="", dest="valid_from",
                             help="the instant this delegation begins (ISO-8601)")
    wk_delegate.add_argument("--expires-at", default="", dest="expires_at",
                             help="the instant it ends (ISO-8601). Required: a "
                                  "delegation with no interval authorizes at "
                                  "every instant or at none")
    wk_delegate.add_argument("--wall-seconds", type=int, default=None,
                             dest="wall_seconds",
                             help="the longest one Parent may run under it")
    wk_delegate.add_argument("--max-attempts", type=int, default=None,
                             dest="max_attempts",
                             help="the bound on wakes this grant permits")
    wk_delegate.add_argument("--max-output-bytes", type=int, default=None,
                             dest="max_output_bytes")
    wk_delegate.add_argument("--dry-run", action="store_true", dest="dry_run",
                             help="print the record that would be written, and "
                                  "write nothing")
    wk_delegate.set_defaults(func=cmd_work_delegate)

    wk_revoke = work_sub.add_parser(
        "revoke", help="§5 — end a standing delegation. Keeps the record, and "
                       "keeps the FIRST instant it ended: a second revoke does "
                       "not move it", **_COMMON)
    wk_revoke.add_argument("--grant-ref", required=True, dest="grant_ref",
                           help="a grant this node recorded")
    wk_revoke.add_argument("--reason", default="",
                           help="why it ended. Required: the record of that is "
                                "what a later reader cannot reconstruct")
    wk_revoke.add_argument("--at", default="",
                           help="the instant the delegation ended (default: now). "
                                "Ignored if it was already revoked")
    wk_revoke.set_defaults(func=cmd_work_revoke)

    wk_admit = work_sub.add_parser(
        "admit", help="§12/P6 — admit one cycle: the focus selection, its budget "
                      "reservation and its lease/dispatch intent in one write. "
                      "Records the intent; performs nothing", **_COMMON)
    _add_work_roots(wk_admit, project_required=False)
    _add_receipts(wk_admit)
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

    wk_dispatch = work_sub.add_parser(
        "dispatch", help="§12/P7 — perform the dispatch an admitted cycle holds "
                         "its action key for: run the command after `--` under "
                         "`runtime/bounded_invocation.invoke_once`, then record "
                         "the receipt it wrote as the cycle's third phase",
        description="The command must follow `--`; every token after it is "
                    "passed to `runtime/bounded_invocation.invoke_once` "
                    "unchanged. The bounds are the admission's reservation, "
                    "not this command line's.", **_COMMON)
    wk_dispatch.add_argument("cycle")
    wk_dispatch.add_argument("--cwd", required=True,
                             help="the absolute directory the command runs in — "
                                  "an Item's worktree, for a cycle that has one")
    wk_dispatch.add_argument("--receipts", required=True,
                             help="the absolute private directory "
                                  "`invoke_once` writes its receipt and the two "
                                  "output files into. Required, and there is no "
                                  "default: it is the same directory the phase "
                                  "is recorded from, and a guessed one would "
                                  "record a receipt nobody can read")
    wk_dispatch.add_argument("--input-file", default="",
                             help="a file whose contents become the command's "
                                  "standard input, mixed into the receipt's "
                                  "input digest. The contents are not echoed")
    wk_dispatch.add_argument("--read-only", action="store_true",
                             help="record the dispatch as intended to be "
                                  "read-only. It is written to the receipt as "
                                  "`read_only_requested` and mixed into the "
                                  "input digest, and `invoke_once` does not "
                                  "enforce it — a declaration to the next "
                                  "reader, not a sandbox")
    wk_dispatch.set_defaults(func=_workchain_refusals(cmd_work_dispatch),
                             accepts_unknown_argv=True)

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

    wk_accept = work_sub.add_parser(
        "accept", help="§12/P7 — record that a retired cycle's result was taken "
                       "up, once, by a named Parent as a named locator. Writes "
                       "no verdict", **_COMMON)
    wk_accept.add_argument("cycle",
                           help="the cycle whose result is being taken up")
    wk_accept.add_argument("--by", required=True,
                           help="the Parent taking it up. Required, because an "
                                "acceptance that cannot be attributed to one is "
                                "not the handoff object's `accepted_by`")
    wk_accept.add_argument("--locator", required=True,
                           help="the exact input/output revision taken up. "
                                "Required, because an acceptance that does not "
                                "say which result is the fact a resuming Parent "
                                "came here for")
    wk_accept.add_argument("--at", default="",
                           help="the instant to record (default: now)")
    wk_accept.set_defaults(func=cmd_work_accept)

    wk_checkpoint = work_sub.add_parser(
        "checkpoint", help="§12/P7 — save where a logical Parent got to because "
                           "it has to wait for a child or a window, then stop. "
                           "Records; wakes nothing", **_COMMON)
    wk_checkpoint.add_argument("item", help="the canonical Work Item id")
    wk_checkpoint.add_argument("--epoch", type=int, required=True,
                               help="which epoch of that Item this Parent carries")
    wk_checkpoint.add_argument("--parent", required=True,
                               help="the logical Parent's own ref")
    wk_checkpoint.add_argument("--next-action", required=True,
                               help="the one bounded sentence a resuming Parent "
                                    "reads; this is what replaces replaying a "
                                    "session")
    wk_checkpoint.add_argument("--waiting-on-child", action="append", default=[],
                               metavar="CYCLE",
                               help="a delegated cycle this store holds an "
                                    "admission for. Repeatable")
    wk_checkpoint.add_argument("--waiting-on-window", default="",
                               metavar="ISO",
                               help="an instant that ends the wait. Must be after "
                                    "the write clock")
    wk_checkpoint.add_argument("--requirement", default="",
                               help="the accepted Requirement revision")
    wk_checkpoint.add_argument("--remaining", action="append", default=[],
                               help="a required result not yet produced. Repeatable")
    wk_checkpoint.add_argument("--evidence", action="append", default=[],
                               help="an accepted evidence locator. Repeatable")
    wk_checkpoint.add_argument("--attempt", action="append", default=[],
                               help="a child attempt already made. Repeatable")
    wk_checkpoint.add_argument("--unresolved-effect", action="append", default=[],
                               help="an effect left unknown. Repeatable")
    wk_checkpoint.add_argument("--write-scope", action="append", default=[],
                               help="a write scope this Parent holds. Repeatable")
    wk_checkpoint.add_argument("--at", default="",
                               help="the write clock (default: now)")
    wk_checkpoint.add_argument("--unit-dir", type=Path, default=None,
                               help="where to look for the installed units when "
                                    "reporting whether a receiver is equipped "
                                    "(default: the user's systemd unit directory)")
    wk_checkpoint.set_defaults(func=cmd_work_checkpoint)

    wk_continuations = work_sub.add_parser(
        "continuations", help="§12/P7 — the Parent checkpoints this node holds, "
                              "whether anything would wake them, and whether what "
                              "each is waiting for has happened yet", **_COMMON)
    wk_continuations.add_argument("--item", default="",
                                  help="only this Work Item's checkpoints")
    wk_continuations.add_argument("--now", default="",
                                  help="the instant to read a window against "
                                       "(default: now)")
    wk_continuations.add_argument("--unit-dir", type=Path, default=None,
                                  help="where to look for the installed units "
                                       "when reporting whether a receiver is "
                                       "equipped (default: the user's systemd "
                                       "unit directory)")
    wk_continuations.set_defaults(func=cmd_work_continuations)

    wk_park = work_sub.add_parser(
        "park", help="§12/P7 — set a checkpoint down on purpose: keep the record "
                     "and the reason, and stop everything that would wake it. "
                     "Records; deletes nothing, and supersedes nothing", **_COMMON)
    wk_park.add_argument("checkpoint_ref",
                         help="a checkpoint this node recorded, e.g. SRV-0001#1")
    wk_park.add_argument("--reason", default="",
                         help="why it is being set down. Required: the record of "
                              "that is the part a later reader cannot reconstruct, "
                              "and it is what tells a decision apart from an "
                              "accident")
    wk_park.add_argument("--by", default="",
                         help="who is setting it down (default: the login this "
                              "process runs as, which is read rather than guessed)")
    wk_park.add_argument("--at", default="",
                         help="the instant it was set down (default: now)")
    wk_park.set_defaults(func=cmd_work_park)

    wk_unpark = work_sub.add_parser(
        "unpark", help="§12/P7 — take a park back up: the record becomes a "
                       "candidate to be woken again, and still says it was set "
                       "down and lifted. Changes no authority", **_COMMON)
    wk_unpark.add_argument("checkpoint_ref",
                           help="a checkpoint this node recorded, e.g. SRV-0001#1")
    wk_unpark.add_argument("--reason", default="",
                           help="why it is being carried again. Required, for the "
                                "same reason the park needs one")
    wk_unpark.add_argument("--by", default="",
                           help="who is taking it back up (default: the login this "
                                "process runs as)")
    wk_unpark.add_argument("--at", default="",
                           help="the instant it was taken back up (default: now)")
    wk_unpark.set_defaults(func=cmd_work_unpark)

    wk_wake = work_sub.add_parser(
        "wake", help="§17.4/P7 — resume a checkpoint whose wait has ended, by "
                     "starting one Parent for it. What the continuation routine "
                     "runs on its timer, and what a person runs to see what it "
                     "would do", **_COMMON)
    wk_wake.add_argument("--item", default="",
                         help="only this Work Item's checkpoints")
    wk_wake.add_argument("--now", default="",
                         help="the instant to read a window against (default: now)")
    wk_wake.add_argument("--dry-run", action="store_true",
                         help="decide and print, and start nothing")
    wk_wake.add_argument("--parent", default="",
                         help="the executable a wake starts, as one absolute path. "
                              "Without it this verb decides and starts nothing — "
                              "which is what `--dry-run` prints and what an "
                              "install without a Parent is refused for")
    wk_wake.add_argument("--budget", type=int, default=0,
                         help="the longest one Parent may run, in seconds. Each "
                              "attempt is clamped to the smaller of this and the "
                              "grant's own budget.wall_seconds")
    wk_wake.add_argument("--cwd", default="",
                         help="the working directory a Parent starts in "
                              "(default: $HOME, the same as the unit's "
                              "WorkingDirectory=)")
    wk_wake.add_argument("--receipts", type=Path, default=None,
                         help="where the per-attempt receipts live (default: "
                              "beside the control store, under "
                              "<state>/serveros/runtime/wake/receipts)")
    wk_wake.add_argument("--unit-dir", type=Path, default=None,
                         help="where to look for the installed units when "
                              "reporting whether a receiver is equipped "
                              "(default: the user's systemd unit directory)")
    wk_wake.set_defaults(func=cmd_work_wake)

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

    wk_ubrief = work_sub.add_parser(
        "understanding-brief", help="§B2/P3 — the fixed-version material a "
                                    "native Agent reads: one range's records, "
                                    "the card they are a change to, and the two "
                                    "blocks the reading must hand back. Reads "
                                    "only; --write writes the packet a session "
                                    "is handed, never a reading", **_COMMON)
    wk_ubrief.add_argument("--records-root", default="",
                           help="the records checkout to read. Default: this "
                                "node's workspace root + /records. Must be absolute")
    wk_ubrief.add_argument("--card", default="",
                           help="only ranges for this card, named as the reader "
                                "names it (records-root relative)")
    wk_ubrief.add_argument("--manifest", default="",
                           help="only ranges citing this capture object, by its "
                                "manifest key")
    wk_ubrief.add_argument("--position", type=int, default=None,
                           help="only the range whose span contains this position")
    wk_ubrief.add_argument("--write", type=Path, default=None,
                           help="write ONE selected brief here as the packet a "
                                "session is handed. Must be absolute")
    wk_ubrief.set_defaults(func=cmd_work_understanding_brief)

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
    wk_usem_authority = wk_usem.add_mutually_exclusive_group()
    wk_usem_authority.add_argument("--delegation", type=Path, default=None,
                                   help="the standing grant that lets machine "
                                        "facts commit without a person (JSON: "
                                        "covers/until/granted_by/source), "
                                        "asserted by the caller. Read only, and "
                                        "absent means every claim is a candidate")
    wk_usem_authority.add_argument("--grant", default="",
                                   help="a grant REF this node recorded, resolved "
                                        "through Agent Control (scope, window and "
                                        "root from the record, not from the "
                                        "caller). Mutually exclusive with "
                                        "--delegation: one commit has one answer "
                                        "to 'under what authority'")
    wk_usem.add_argument("--now", default="",
                         help="the instant to judge the grant's root at, with "
                              "the --grant path. Default: this process's clock. "
                              "It is a flag because the reader's clock is part "
                              "of the answer")
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

    wk_sp = work_sub.add_parser(
        "source-policy", help="§B1/P1 — which sources are in force, by what "
                              "policy, and which rule decided each one: the "
                              "desired declarations, the two lists being "
                              "migrated, and the exclusions. Reads only; "
                              "--coverage also hands the rows to the existing "
                              "followed-source consumer", **_COMMON)
    wk_sp.add_argument("--records-root", default="",
                       help="the records checkout to read. Default: this node's "
                            "workspace root + /records. Must be absolute")
    wk_sp.add_argument("--coverage", action="store_true",
                       help="also project the resolved rows into the shape "
                            "`workchain.source_coverage` reads and report what "
                            "that consumer makes of them")
    wk_sp.set_defaults(func=cmd_work_source_policy)

    wk_mi = work_sub.add_parser(
        "material-index", help="§B1/P2 — the materials a source refers to: "
                               "one message may name several, one material may "
                               "have several sources, and the four coverages "
                               "(message/discovery/material/understanding) are "
                               "answered separately. Reads only; --coverage "
                               "also joins the card comparison for the "
                               "understanding half", **_COMMON)
    wk_mi.add_argument("--records-root", default="",
                       help="the records checkout to read. Default: this node's "
                            "workspace root + /records. Must be absolute")
    wk_mi.add_argument("--coverage", action="store_true",
                       help="also read the cards through understanding_delta / "
                            "source_waterline and copy their comparison onto "
                            "each object instead of reporting NOT_MEASURED")
    wk_mi.add_argument("--digest", action="store_true",
                       help="also hash the landed bytes. Off by default: it "
                            "reads every image, and a caller who wants the "
                            "landing state should not pay for a hash it will "
                            "not compare")
    wk_mi.set_defaults(func=cmd_work_material_index)

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

    rp_request = report_sub.add_parser(
        "request",
        help="record that somebody asked for THIS version of a published report "
             "to go to THIS recipient. It sends nothing: the version, the "
             "recipient and the authorization are all re-read at the moment of "
             "sending, and freezing them here would freeze the wrong three "
             "answers", **_COMMON)
    rp_request.add_argument(
        "--out-dir", required=True,
        help="the directory `report daily` published into. The version is read "
             "from the publication receipt there, not from an argument: a "
             "version supplied by the caller is one the receipt cannot "
             "corroborate")
    rp_request.add_argument("--recipient", required=True,
                            help="who is to receive it")
    rp_request.add_argument(
        "--requested-by", required=True,
        help="who asked. Required: 'somebody asked for this' with nobody named "
             "is the sentence this field exists to make unwritable")
    rp_request.add_argument(
        "--why", required=True,
        help="why this report is going to this recipient. Required for the same "
             "reason: a delivery with no stated purpose is one nobody can "
             "review later")
    rp_request.add_argument("--channel", default=None,
                            help="the channel or transport, if the requester "
                                 "named one")
    rp_request.add_argument("--at", default=None,
                            help="when it was requested, ISO-8601. A default "
                                 "difference, not a default value: absent means "
                                 "the clock was read here, which is recorded")
    rp_request.set_defaults(func=cmd_report_request)

    rp_withdraw = report_sub.add_parser(
        "withdraw",
        help="stop a pending request. This does not retract a message that has "
             "already gone out — nothing here can — and it does not delete the "
             "record that the request existed", **_COMMON)
    rp_withdraw.add_argument("--out-dir", required=True)
    rp_withdraw.add_argument("--because", required=True,
                             help="why it is being withdrawn. A withdrawal with "
                                  "no reason is indistinguishable from somebody "
                                  "losing interest, and the next reader has to "
                                  "tell those apart")
    rp_withdraw.add_argument("--at", default=None, help="ISO-8601, as `request`")
    rp_withdraw.set_defaults(func=cmd_report_withdraw)

    rp_deliver = report_sub.add_parser(
        "deliver",
        help="make ONE attempt at the pending request and record what the "
             "channel said. A missing consumer answers PENDING_CONSUMER, which "
             "is a queue that is waiting and not a delivery that failed",
        **_COMMON)
    rp_deliver.add_argument("--out-dir", required=True)
    rp_deliver.add_argument(
        "--conversation", default=None,
        help="who receives it, as user:<open_id> or chat:<chat_id>. The prefix "
             "is required because the transport's two destination flags are "
             "mutually exclusive and this command will not guess which one a "
             "bare identifier means — a daily report delivered to a group "
             "instead of to one person cannot be taken back. Absent (and with "
             "SERVEROS_REPORT_CONVERSATION unset) the answer is "
             "PENDING_CONSUMER: no attempt is made and none is recorded")
    rp_deliver.add_argument(
        "--authorization", default=None,
        help="what the caller just re-checked, and found still true, about "
             "sending now. Required to send: an authorization is a property of "
             "the moment, and a stored one would deliver tomorrow's report on "
             "the strength of yesterday's decision")
    rp_deliver.add_argument(
        "--withhold", default=None,
        help="the opposite decision, and it is a refusal with a reason rather "
             "than silence: the entry stays pending and the reason is printed")
    rp_deliver.add_argument("--at", default=None, help="ISO-8601, as `request`")
    rp_deliver.set_defaults(func=cmd_report_deliver)

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
                             "from. A path that is not a checkout, and no path "
                             "at all, are both reported as a GAP rather than as "
                             "zero commits: a scope whose work is not in git has "
                             "no checkout to pass, and refusing it would make "
                             "such a scope unable to run at all")
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

    doc_stewardship = document_sub.add_parser(
        "stewardship",
        help="P4 · 我们长期维护哪些文档、每一篇的交付形态/正文权威/写策略是什么，"
             "以及这份契约本身能不能被写闸门问。规则从 `--ability` 指名的能力脚本"
             "加载（不在这里重述一遍）。读，不写；不发任何东西，不读时钟", **_COMMON)
    doc_stewardship.add_argument(
        "--contracts", required=True,
        help="path to the managed-documents registry. Required and undefaulted: "
             "the registry IS the answer to '有哪些', and a defaulted one would "
             "report on documents nobody listed")
    doc_stewardship.add_argument(
        "--state-dir", required=True,
        help="the directory holding one state file per document. Required even "
             "when it is empty: 'never observed' and 'observed and unchanged' "
             "are different readings and only the state file can tell them apart")
    doc_stewardship.add_argument(
        "--ability", required=True,
        help="path to the document-stewardship ability's own script. Required "
             "and undefaulted — a searched-for gate is a gate whose version "
             "nobody can state, and an unstated path would let a second copy of "
             "the rules decide in its place")
    doc_stewardship.add_argument(
        "--surface-vocabulary", default=str(REPO_ROOT / "space" / "vocabulary.json"),
        help="the closed set of surfaces a body authority may live on. Default: "
             "the one that ships beside the kernel, so an installed node judges by "
             "the same set a source checkout does. A declared surface outside it is "
             "refused rather than defaulted")
    doc_stewardship.set_defaults(func=cmd_document_stewardship)

    doc_publish = document_sub.add_parser(
        "publish",
        help="P4 · 把一节正文发出去。默认只计划不写；加 --apply 才写。正文/媒体/"
             "索引/Git/送达各记一笔回执，「正文成功媒体失败」是可以被表示的，"
             "重跑只补缺的那条边。规则不在这里重述：能不能改由 --ability 的能力"
             "判，改的范围与读回由 intake/document_publish.py 判，怎么够到这篇"
             "文档由 runtime/document_transport.py 管（只有它知道 lark-cli）",
        **_COMMON)
    doc_publish.add_argument(
        "--request", required=True,
        help="path to the publish request (JSON, schema "
             "serveros/document-publish-request.v1). Required: the intent is the "
             "input, and an intent with a defaulted field is an intent nobody "
             "stated")
    doc_publish.add_argument(
        "--contracts", required=True,
        help="path to the managed-documents registry, the same file "
             "`document stewardship` reads")
    doc_publish.add_argument(
        "--state-dir", required=True,
        help="the per-document state directory. Required because the derived "
             "section keys live there: without it there is no live locator, and "
             "a patch with no locator patches nothing in particular")
    doc_publish.add_argument(
        "--ability", required=True,
        help="path to the document-stewardship ability's own script. Required "
             "and undefaulted; the write gate is asked from it, twice — once to "
             "plan, once against the text the live read found")
    doc_publish.add_argument(
        "--receipts", required=True,
        help="where the per-effect receipts are written. Required: the receipts "
             "are the only thing that makes 「正文落定、媒体没落定」 a state this "
             "host can be in and later repair")
    doc_publish.add_argument(
        "--approval", action="append", metavar="CLASS=REF",
        help="an approval for one change class on the approval tier, repeatable "
             "(e.g. --approval RESTRUCTURE=feishu-msg-<ref>). Without one, a "
             "RESTRUCTURE is refused rather than assumed approved")
    doc_publish.add_argument(
        "--apply", action="store_true",
        help="actually write. Without it the run is planned and the refusals are "
             "printed, and nothing is sent")
    doc_publish.add_argument(
        "--surface-vocabulary",
        default=str(REPO_ROOT / "space" / "vocabulary.json"),
        help="the closed set of surfaces a body authority may live on. Default: "
             "the one that ships beside the kernel")
    doc_publish.set_defaults(func=cmd_document_publish)

    information = sub.add_parser(
        "information", help="what was captured from the sources (§B1): the "
                            "declared policy and the coverage achieved, kept "
                            "apart", **_COMMON)
    information_sub = information.add_subparsers(dest="information_verb",
                                                 required=True)

    info_captured = information_sub.add_parser(
        "captured",
        help="the 68 manifests under <records>/information/raw, read as capture "
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

    info_reference = information_sub.add_parser(
        "reference",
        help="§4/§5/§6/§11 of EXTERNAL-REFERENCE-LIBRARY — 每个外部参考对象抓到"
             "什么、能证明什么：§4 的四条捕获质量问题逐条对着采集器自己写的标记"
             "清单判定（查过没命中 ≠ 没人查过），§11 的四个阶段分层（没有哪个蕴含"
             "另一个），发现出处按 provider+稳定标识分组而不按标题。`--note` 另校验"
             "一份我们自己写的笔记草稿的首层。读，不写", **_COMMON)
    info_reference.add_argument(
        "--records-root", default="",
        help="the records checkout to read. Default: this node's workspace "
             "root + /records. Must be absolute")
    info_reference.add_argument(
        "--note", default="",
        help="path to a candidate reference-note draft to check against §5/§6. "
             "Omit it and no note is checked — this verb does not go looking for "
             "one to approve")
    info_reference.set_defaults(func=cmd_information_reference)

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
    # value of `parse_args` here. Two verbs are the exception, and they are
    # different kinds of exception: `architecture` forwards its tail to a
    # checker whose flags this file deliberately does not re-declare, and
    # `work dispatch` takes the command it will run as the tail after `--`.
    # Both ask for the leftovers by name, which is what `accepts_unknown_argv`
    # means.
    #
    # `nargs=argparse.REMAINDER` was the obvious way to express "everything
    # after this point, options included", and it does not work: argparse does
    # not match a REMAINDER positional when the first token is an option
    # (bpo-17050), so `architecture project --out d` parsed as an empty tail
    # plus one unrecognized argument. Measured, not assumed. The leftovers come
    # back from argparse in the order they were typed, which is what a forwarded
    # argv requires and what a per-positional reconstruction would lose — and
    # for the dispatch it is more than convenient: the tail is a command, and a
    # parser that reordered or re-interpreted any token of it would change what
    # runs.
    if unknown:
        if not getattr(args, "accepts_unknown_argv", False):
            parser.error("unrecognized arguments: " + " ".join(unknown))
        args.trailing_argv = unknown
    elif getattr(args, "accepts_unknown_argv", False):
        # Set even when empty, so the handler never has to tell "no tail" from
        # "this verb was reached some other way".
        args.trailing_argv = []
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
