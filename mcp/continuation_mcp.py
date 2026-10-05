#!/usr/bin/env python3
"""serveros-continuation-mcp — the narrow control surface ONE wake carries.

    launcher -> (Parent) -> serveros-continuation-mcp -> serverosctl work …

WHY THIS EXISTS, AND WHY IT IS NOT A DAEMON
-------------------------------------------
A woken Parent has to be able to continue its own work — checkpoint where it got
to, accept a child's result, set its record down — and until now the only way it
could was a shell. That is not a boundary: `serverosctl work park <any ref>` needs
no context and no admission (measured: `runtime/cli.py#_control_store` opens the
control store directly and the four `work` verbs never call `check_trusted_context`),
so a Parent with Bash can set down another Item's epoch, accept a cycle its own
checkpoint never named, and checkpoint a sibling. The launcher's write scope was a
sentence in prose and nothing enforced it.

This adapter is the smallest thing that is a boundary. It is per-wake — it is
started by the launcher that starts the Parent and dies with it — and it carries
exactly ONE identity: the path in `$SERVEROS_CONTINUATION_ENVELOPE`, filed beside
the invocation receipt by `runtime/wake.py` before the Parent was spawned. There
is no daemon, no socket, no registry, and no long-lived authority: nothing to
outlive the wake, and nothing to keep alive between wakes.

Where `mcp/node_mcp.py` forwards every call to `serverosd` unchanged, this surface
does the opposite and says so: it is the one MCP surface in this product that
DECIDES, because the question «may this wake do this» has to be answered between
the Parent and the verb and there is nobody else standing there.

THE THREE THINGS IT WILL NOT DO
-------------------------------
1. **It does not re-mint authority.** It does not create a trusted context, does
   not widen a grant and does not take an actor from its caller. Every mutation is
   decided by `runtime/continuation_control.verify`, which re-reads the store —
   the checkpoint, the grant, the delegation root, the admissions — on every call
   and refuses on the first thing that changed since the envelope was filed.
2. **It does not implement a mutation.** Each one is executed by calling the verb
   that already owns it (`serverosctl work checkpoint|park|accept`) through
   `runtime/bounded_invocation.invoke_once`, so there is one implementation of
   each mutation, one durable receipt per action key, and — the part that matters
   for evidence — an artifact on disk for the exact argv, the exact stdin, the
   exit code and both output streams. A Parent's own account of what it did is
   narrative; the receipt is the record.
3. **It cannot be aimed at another Item.** `work.checkpoint` takes no Item,
   `work.park` defaults to the envelope's own checkpoint, and `work.accept` is
   confined to the child refs the checkpoint itself names. A caller passing a
   different Item does not get a slightly different effect — it gets a DENY naming
   the field that differs, because the Item is not a parameter of this surface.

WHAT A CALLER GETS BACK
-----------------------
Every answer carries `decision` — ALLOW, DENY or UNASKABLE — and `because`. DENY
is an answer about the request and UNASKABLE is an answer about this adapter
(it could not read something it needs), which is why they are not collapsed: a
revoked grant and a store that would not open are different repairs. The
distinction is `runtime/ownership_gate.py`'s, and this surface keeps it.

`isError` is false for both. A refusal is not a failed tool call.

Protocol: JSON-RPC 2.0 over stdio, one object per line, implemented directly —
the same decision, and the same reason, as `mcp/node_mcp.py`.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_RUNTIME = _ROOT / "runtime"
_SERVEROSCTL = _ROOT / "bin" / "serverosctl"

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "serveros-continuation"
SERVER_VERSION = "1.0.0"

#: How long one mutation may run, and how much of it is kept.
#:
#: Both are bounds rather than preferences: `invoke_once` refuses a non-positive
#: one, and a `work checkpoint` that hangs would otherwise hold this surface open
#: for as long as the Parent lives. The output bound is generous because the JSON
#: a work verb prints is a few kilobytes and truncation is reported as
#: `OUTPUT_LIMIT` rather than passed off as a complete answer.
MUTATION_TIMEOUT_SECONDS = 60
MUTATION_MAX_OUTPUT_BYTES = 400_000


def _load(name: str, path: Path):
    """Load a module by path under a chosen name, once.

    The fabric's own idiom, and it is load-bearing here rather than stylistic:
    `runtime/continuation_control.py` and `runtime/bounded_invocation.py` are
    also loaded by `runtime/wake.py`, each under one fixed name, so that
    `derivation` and `invoke_once` are one function with one behaviour. Loading
    either under a second name would give this process a second module object
    with a second module-level state, which is precisely how two answers start
    disagreeing about one record.
    """
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _cc():
    """`runtime/continuation_control.py` — the verdict engine."""
    return _load("serveros_runtime_continuation_control",
                 _RUNTIME / "continuation_control.py")


def _bounded():
    """`runtime/bounded_invocation.py` — the fabric's only bounded executor."""
    return _load("serveros_runtime_bounded_invocation",
                 _RUNTIME / "bounded_invocation.py")


def _cli():
    """`runtime/cli.py`, for the three things that already have one authority.

    Where this node's control store is (`_agent_control_store_path`, which
    honours `SERVEROS_STATE_DIR` and is pinned by
    `tests/test_control_store_path.py` to the same file the daemon writes), what
    a work verb's argv is, and how an Item's completion boundary is composed. A
    second composition of any of the three would be a second answer to a question
    this repository has already decided once.
    """
    return _load("cli", _RUNTIME / "cli.py")


def _completion_reader(cli):
    """Read one Item's completion boundary through the chain that composes it.

    `work item` prints this verdict and `intake/workchain.compose_item` composes
    it; this calls that function rather than reimplementing the rule, because
    completion in this fabric is not a record anybody writes — it is a reading of
    the Item's document chain, and a second reader would be a second completion
    authority.

    The one thing this decides is WHICH project. `work item` requires `--project`
    because a person knows which project they mean, and a capability envelope
    carries no project (a checkpoint records none — measured, and `runtime/
    cli.py` says so at the site where a grant is asked for one). So the projects
    the boards declare are tried in turn and the first projection holding the
    Item answers. An Item in none of them has no composable boundary here, and
    that is reported as unreadable rather than guessed at — `verify` turns it into
    UNASKABLE, which stops the mutation, because «this adapter could not look» is
    not «the Item is unfinished».
    """
    chain = cli._workchain_module()
    args = types.SimpleNamespace()

    def read(item_id: str) -> dict:
        try:
            boards, records, workspace = cli._workchain_roots(args)
            if boards is None:
                return {"unreadable": "the workchain roots did not resolve"}
            contract = chain.load_contract(
                cli.REPO_ROOT / "intake" / "workchain-contract.yaml")
            skeletons = cli._workchain_skeletons(chain)
            projects = chain.load_projects(boards_root=boards)
            for project in projects:
                item = chain.compose_item(
                    contract=contract, records_root=records,
                    workspace_root=workspace, skeletons=skeletons,
                    project=project["project"], item_id=item_id,
                    projects=projects)
                if item.get("found"):
                    completion = item.get("completion")
                    if isinstance(completion, dict):
                        return completion
                    return {"unreadable": (f"{item_id} composes under "
                                           f"{project['project']} and the chain "
                                           f"reports no completion boundary")}
            return {"unreadable": (f"{item_id} is in none of the {len(projects)} "
                                   f"board projections under {boards}, so its "
                                   f"completion boundary cannot be composed here")}
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            return {"unreadable": f"{type(exc).__name__}: {exc}"}

    return read


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------

TOOLS: tuple[dict, ...] = (
    {
        "name": "continuation.inspect",
        "description": (
            "This wake's own capability envelope, the live record it was issued "
            "for, and the completion boundary of the Item it carries. The Item, "
            "epoch and grant are this wake's and are not arguments: there is no "
            "parameter through which a caller could ask about another one. Read "
            "this first — a mutation refused for a reason you did not expect is "
            "a reason that is already in here."
        ),
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
    {
        "name": "child-result.inspect",
        "description": (
            "The admission this store holds for one child cycle. Confined to the "
            "child refs THIS checkpoint names — the same predicate `work.accept` "
            "is held to, on purpose: if a ref were readable but not receivable, a "
            "Parent would read another scope's result and only then be told the "
            "ref was never its own, and the reading is the leak."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"cycle": {"type": "string",
                                     "description": "a cycle ref named by this wake's own checkpoint"}},
            "required": ["cycle"],
            "additionalProperties": False,
        },
    },
    {
        "name": "work.checkpoint",
        "description": (
            "Save where this Parent got to, then stop. The Item is this wake's "
            "own and there is no argument for it, so a checkpoint cannot be "
            "written for another Item. It files this Item's NEXT epoch — the "
            "successor record that takes the scope over from the one this "
            "Parent was woken from, which the store supersedes in the same "
            "transaction. The epoch is derived for the same reason and is not "
            "an argument either, so it cannot be aimed at another Parent's "
            "record. Name exactly one way of waiting — `waiting_on_child` or "
            "`waiting_on_window` — because a checkpoint records where the wait "
            "ends and a record naming neither, or both, is refused by the "
            "verb. Executed by `serverosctl work checkpoint`, once, under a "
            "durable receipt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "next_action": {"type": "string",
                                "description": "the one bounded sentence a resuming Parent reads; required"},
                "parent": {"type": "string",
                           "description": "defaults to this wake's own parent_owner — the ref the envelope carries"},
                "requirement": {"type": "string", "description": "the accepted Requirement revision"},
                "remaining": {"type": "array", "items": {"type": "string"},
                              "description": "a required result not yet produced; repeatable"},
                "evidence": {"type": "array", "items": {"type": "string"},
                             "description": "an accepted evidence locator; repeatable"},
                "attempt": {"type": "array", "items": {"type": "string"},
                            "description": "a child attempt already made; repeatable"},
                "unresolved_effect": {"type": "array", "items": {"type": "string"},
                                      "description": "an effect left unknown; repeatable"},
                "waiting_on_child": {"type": "array", "items": {"type": "string"},
                                     "description": "a delegated cycle this store holds an admission for; repeatable. Exactly one of this and waiting_on_window, and one of the two is required"},
                "waiting_on_window": {"type": "string",
                                      "description": "an ISO instant that ends the wait. Exactly one of this and waiting_on_child, and one of the two is required"},
                "write_scope": {"type": "array", "items": {"type": "string"},
                                "description": "a write scope this Parent holds; each must stay inside the envelope's allowed_write_scope"},
            },
            "required": ["next_action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "work.park",
        "description": (
            "Set this wake's own checkpoint down on purpose: keep the record and "
            "the reason, stop everything that would wake it. Records; deletes "
            "nothing and supersedes nothing. The checkpoint is this wake's own — "
            "another Item's, or another epoch's, is a DENY rather than a "
            "different effect."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string",
                           "description": "why it is being set down; required — it is what tells a decision apart from an accident"},
                "checkpoint_ref": {"type": "string",
                                   "description": "defaults to this wake's own checkpoint_ref"},
            },
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "work.accept",
        "description": (
            "Record that a retired cycle's result was taken up, once, by this "
            "Parent as a named locator. Writes no verdict. The cycle must be one "
            "this wake's own checkpoint named AND one this store holds an "
            "admission for, belonging to this Item."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cycle": {"type": "string", "description": "the cycle whose result is being taken up"},
                "locator": {"type": "string",
                            "description": "the exact input/output revision taken up; required — an acceptance that does not say which result is not the fact a resuming Parent came for"},
                "by": {"type": "string",
                       "description": "defaults to this wake's own parent_owner"},
            },
            "required": ["cycle", "locator"],
            "additionalProperties": False,
        },
    },
    {
        "name": "work.complete",
        "description": (
            "NOT AVAILABLE, and declared so that a caller finds an answer rather "
            "than a gap. Completion in this fabric is not a record anybody "
            "writes: it is a verdict composed from the Item's document chain and "
            "read by `serverosctl work item`. There is no `work complete` verb "
            "and no completions collection, so implementing this would create a "
            "second completion authority. Every call returns DENY naming that; "
            "read the boundary with `continuation.inspect` instead."
        ),
        "inputSchema": {"type": "object", "properties": {},
                        "additionalProperties": False},
    },
)


def tool_names() -> tuple[str, ...]:
    return tuple(tool["name"] for tool in TOOLS)


def envelope_env_name() -> str:
    """The variable the receiver sets, read through the module that sets it.

    Not restated as a literal: `runtime/wake.py` is the producer and
    `runtime/continuation_control.py` already quotes it once for its own refusal
    message, and a third copy here is a third thing to drift. If the engine is
    unloadable then nothing on this surface works anyway, and the failure should
    be that, not a silently different variable name.
    """
    return _cc().ENVELOPE_ENV_NAME


class WiringFault(RuntimeError):
    """This adapter cannot serve at all — not a refusal of any request."""


class ContinuationServer:
    """One wake's control surface, bound to the envelope it was started with.

    Injected collaborators rather than module-level singletons, for
    `mcp/node_mcp.py`'s reason: a surface that can only be exercised against the
    live store is a surface that gets tested by hand. `cli` and `receipts` are
    the two a test replaces; the envelope path is the identity and is not one.
    """

    def __init__(self, envelope_path, *, cli=None, receipts=None, cwd=None,
                 python=None, serverosctl=None, completion=None) -> None:
        self.cc = _cc()
        self.envelope_path = Path(envelope_path)
        self.envelope = self.cc.load_envelope(self.envelope_path)
        self._cli_module = cli
        # The receipt root is the envelope's own directory, derived rather than
        # passed: `runtime/wake.py#envelope_path` files the envelope beside the
        # receipt it belongs to, so the two cannot disagree about where this
        # wake's evidence lives. A second environment variable for it would be a
        # second thing the launcher has to set, and the first thing to be set
        # inconsistently.
        self.receipts = Path(receipts or self.envelope_path.resolve().parent)
        self.cwd = Path(cwd or _ROOT)
        self.python = python or sys.executable
        self.serverosctl = Path(serverosctl or _SERVEROSCTL)
        # The third injection, and the same reason as the other two: the real
        # reader walks the whole workchain (`_completion_reader`), which is a
        # large amount of machinery to stand up to ask one question in a test.
        # Injected, the suite can exercise every refusal in this surface without
        # composing a board; left alone, the real chain answers, which is what
        # the deployed surface does.
        self._completion = completion

    # -- the two things this surface reads the world through ------------------

    def cli(self):
        if self._cli_module is None:
            self._cli_module = _cli()
        return self._cli_module

    def completion(self, item_id: str) -> dict:
        if self._completion is None:
            self._completion = _completion_reader(self.cli())
        return self._completion(item_id)

    def now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat()

    def verdict(self, operation: str, arguments: dict | None = None) -> dict:
        """The engine's answer for one operation, from the store as it stands.

        The store is opened here, per call, and `verify` reads it again through
        `checkpoints()` / `grants()` / `admissions()` — three `json.loads` of a
        small document, each of them the file on disk NOW (`AgentControlStore.
        _read` has no cache to be stale). That is what «re-checked on every
        mutation» costs, and it is the point of the whole surface.
        """
        _mod, store = self.cli()._control_store()
        return self.cc.verify(
            envelope=self.envelope, store=store, contexts=store.contexts(),
            operation=operation, arguments=dict(arguments or {}), now=self.now(),
            completion_of_item=self.completion)

    # -- operations -----------------------------------------------------------

    def _receipt(self, receipt: dict, key: str) -> dict:
        """The receipt, plus the two artifacts a verdict is allowed to rest on.

        `runtime/bounded_invocation` writes `<key>.stdin|stdout|stderr` beside
        the receipt and digests both streams. Quoting the digests and the exit
        code here is what makes this surface's answer checkable by somebody who
        was not in the conversation: a Parent saying «I checkpointed» is
        narrative, and these four values are the record.
        """
        out = self.receipts / (key + ".stdout")
        err = self.receipts / (key + ".stderr")
        parsed = None
        if out.is_file():
            try:
                parsed = json.loads(out.read_text())
            except (json.JSONDecodeError, OSError):
                parsed = None
        return {
            "action_key": receipt.get("action_key"),
            "status": receipt.get("status"),
            "effect": receipt.get("effect"),
            "process_exit_code": receipt.get("process_exit_code"),
            "replayed": receipt.get("replayed"),
            "executed_now": receipt.get("executed_now"),
            "output_sha256": receipt.get("output_sha256"),
            "stdout_artifact": str(out),
            "stderr_artifact": str(err),
            "stdout_json": parsed,
        }

    def _mutate(self, operation: str, tail: list, arguments: dict,
                input_text: dict) -> dict:
        verdict = self.verdict(operation, input_text)
        answer = {"operation": operation, "decision": verdict["decision"],
                  "because": verdict["because"], "verdict": verdict,
                  "executed": False, "receipt": None}
        if verdict["decision"] != "ALLOW":
            return answer
        key = "%s-%s" % (self.envelope["action_key"], operation.replace(".", "-"))
        try:
            receipt = _bounded().invoke_once(
                key=key,
                argv=[self.python, str(self.serverosctl), "work", *tail],
                cwd=self.cwd, receipts=self.receipts,
                input_text=json.dumps(input_text, sort_keys=True),
                timeout=MUTATION_TIMEOUT_SECONDS, readonly=False,
                max_output_bytes=MUTATION_MAX_OUTPUT_BYTES,
                # The envelope, so the verb runs in the same wake the adapter is
                # deciding for. It is inert to `serverosctl` and is here for the
                # same reason it is part of `invoke_once`'s digest: two calls
                # under one action key that differ in their environment are two
                # dispatches, and this one must replay rather than re-run.
                env={envelope_env_name(): str(self.envelope_path)})
        except ValueError as exc:
            # `invoke_once`'s refusals are all on the NEAR side of `Popen`:
            # nothing was started, nothing was written beyond a lock sidecar.
            # Reported as an answer rather than raised, because a refusal to
            # dispatch is exactly what this surface exists to produce.
            return {**answer, "decision": "DENY", "because": str(exc),
                    "dispatch_refused": True}
        answer["executed"] = True
        answer["receipt"] = self._receipt(receipt, key)
        return answer

    def inspect(self) -> dict:
        item_id = str(self.envelope.get("work_item_ref") or "")
        _mod, store = self.cli()._control_store()
        checkpoints = store.checkpoints()
        ref = str(self.envelope.get("checkpoint_ref") or "")
        grant_ref = str(self.envelope.get("grant_ref") or "")
        grants = store.grants()
        children = [str(r) for r in (self.envelope.get("allowed_child_refs") or [])]
        # The read operation's own verdict, so that a Parent can see the five
        # generic checks pass BEFORE it tries a mutation that writes — and
        # `decision`/`because` are lifted to the top for the reason
        # `invoke_once` always returns `replayed`: a caller that has to know
        # which tool answers in which shape will read the wrong one. Every
        # answer from this surface carries all three at the top.
        verdict = self.verdict("continuation.inspect", {})
        return {
            "operation": "continuation.inspect",
            "decision": verdict["decision"],
            "because": verdict["because"],
            # A read dispatches nothing, and says so the same way a refused
            # mutation does rather than by omitting the field.
            "executed": False,
            "receipt": None,
            "envelope": self.envelope,
            "envelope_path": str(self.envelope_path),
            "checkpoint": checkpoints.get(ref),
            "grant": grants.get(grant_ref),
            "children": [{"cycle": c, "admission": store.admissions().get(c)}
                         for c in children],
            "completion": self.completion(item_id),
            "verdict": verdict,
        }

    def dispatch(self, name: str, arguments: dict) -> dict:
        if name == "continuation.inspect":
            return self.inspect()
        if name == "child-result.inspect":
            cycle = str(arguments.get("cycle") or "")
            verdict = self.verdict(name, arguments)
            _mod, store = self.cli()._control_store()
            return {"operation": name, "decision": verdict["decision"],
                    "because": verdict["because"], "verdict": verdict,
                    "executed": False, "receipt": None,
                    "cycle": cycle,
                    "admission": (store.admissions().get(cycle)
                                  if verdict["decision"] == "ALLOW" else None)}
        if name == "work.complete":
            verdict = self.verdict(name, {})
            return {"operation": name, "decision": verdict["decision"],
                    "because": verdict["because"], "verdict": verdict,
                    "executed": False, "receipt": None}
        if name == "work.park":
            ref = str(arguments.get("checkpoint_ref")
                      or self.envelope.get("checkpoint_ref") or "")
            merged = {**arguments, "checkpoint_ref": ref}
            # `--by` is passed and not offered as a parameter. `work park`
            # defaults it to the login the process runs as, which for a woken
            # Parent is the OPERATOR account and not the Parent — a name that is
            # true of the machine and false of the work. The envelope's
            # `parent_owner` is the name this wake actually carries, so it is
            # what gets recorded, and no caller can put another name there.
            tail = ["park", ref, "--reason", str(arguments.get("reason") or ""),
                    "--by", str(self.envelope.get("parent_owner") or "")]
            return self._mutate(name, tail, merged, merged)
        if name == "work.accept":
            cycle = str(arguments.get("cycle") or "")
            by = str(arguments.get("by") or self.envelope.get("parent_owner") or "")
            merged = {**arguments, "cycle": cycle, "by": by}
            tail = ["accept", cycle, "--by", by,
                    "--locator", str(arguments.get("locator") or "")]
            return self._mutate(name, tail, merged, merged)
        if name == "work.checkpoint":
            item_id = str(self.envelope.get("work_item_ref") or "")
            epoch = self.envelope.get("epoch")
            # WHICH EPOCH THIS WRITES, AND WHY IT IS THE NEXT ONE. The
            # envelope's epoch is the record this Parent was woken FROM — the
            # one its predecessor wrote and then exited, which `runtime/wake.py`
            # hands it in the briefing as «a Parent that exited». A checkpoint
            # at THAT epoch is the one write the store refuses by design:
            # `AgentControlStore.put_checkpoint` will not replace a live record
            # under its own key, and the record a wake carries is live by
            # definition — the engine refuses the mutation otherwise. The key is
            # (Item, epoch), so overwriting it would make two Parents' states
            # one record (`runtime/continuation.py`, `checkpoint_key`).
            #
            # The store's refusal names the repair — «Carry the Item on under a
            # new epoch, which takes the scope over and keeps this record
            # readable» — and the design says the same in prose: a Parent that
            # replaces another writes the successor record, and a write at a
            # higher epoch supersedes the record it replaced in the same
            # transaction. So the woken Parent's checkpoint is its successor's
            # epoch. Derived here, and still NOT a parameter: there is no
            # argument for it, so it cannot be aimed at another Parent's record
            # either.
            #
            # MEASURED, because the argv is where the defect was: against one
            # store, `--epoch <the envelope's>` is refused with that sentence
            # and nothing written, and `--epoch <the envelope's + 1>` is
            # accepted and supersedes the record the wake carried. Every test of
            # this operation drives a stand-in verb and so never reaches
            # `put_checkpoint` at all; the acceptance run in a redirected state
            # root is what found it.
            if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
                raise WiringFault(
                    f"this wake's envelope carries epoch {epoch!r}, which is not "
                    f"the positive integer a checkpoint's key is derived from, so "
                    f"the successor epoch cannot be derived from it. Guessing one "
                    f"would write a record under a key nobody chose")
            successor = epoch + 1
            parent = str(arguments.get("parent")
                         or self.envelope.get("parent_owner") or "")
            # The Item and the parent are put into `arguments` here so that the
            # engine's own target rule sees exactly what will be dispatched and
            # can refuse a mismatch — rather than this function quietly
            # substituting and the rule then checking a value nobody asked for.
            merged = {**arguments, "item": item_id, "parent": parent}
            tail = ["checkpoint", item_id, "--epoch", str(successor),
                    "--parent", parent,
                    "--next-action", str(arguments.get("next_action") or "")]
            for flag, field in (("--requirement", "requirement"),
                                ("--waiting-on-window", "waiting_on_window")):
                if arguments.get(field):
                    tail += [flag, str(arguments[field])]
            for flag, field in (("--remaining", "remaining"),
                                ("--evidence", "evidence"),
                                ("--attempt", "attempt"),
                                ("--unresolved-effect", "unresolved_effect"),
                                ("--waiting-on-child", "waiting_on_child"),
                                ("--write-scope", "write_scope")):
                for value in arguments.get(field) or []:
                    tail += [flag, str(value)]
            return self._mutate(name, tail, merged, merged)
        raise WiringFault(f"no dispatch for {name!r}")

    # -- MCP lifecycle --------------------------------------------------------

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            return _result(request_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "This surface carries ONE wake's authority and no other. Its "
                    "Item, epoch, grant and child refs come from the capability "
                    "envelope the launcher filed, and are not arguments: there is "
                    "no parameter through which a caller could reach another "
                    "wake's work. Every mutation is re-decided against the live "
                    "store and executed under a durable receipt. `decision` is "
                    "ALLOW, DENY or UNASKABLE — DENY is a verdict about the "
                    "request, UNASKABLE means this adapter could not read "
                    "something it needs. Neither is a tool failure."
                ),
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return _result(request_id, {"tools": [dict(t) for t in TOOLS]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "ping":
            return _result(request_id, {})
        if str(method).startswith("notifications/"):
            return None
        # `resources/list` and `prompts/list` are deliberately not implemented,
        # for the reason `mcp/node_mcp.py` gives: an empty list from a surface
        # that never offered a capability is a claim that the set is empty.
        return _error(request_id, -32601, f"Method not found: {method!r}")

    def _call_tool(self, request_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in tool_names():
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"Unknown tool {name!r}. Available: "
                             f"{list(tool_names())}"}]})
        allowed = set(TOOLS[tool_names().index(name)]["inputSchema"]["properties"])
        unexpected = set(arguments) - allowed
        if unexpected:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} got unexpected argument(s) "
                             f"{sorted(unexpected)}. Accepted: "
                             f"{sorted(allowed)}"}]})
        try:
            payload = self.dispatch(name, arguments)
        except WiringFault as exc:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"{name}: {exc}"}]})
        except Exception as exc:  # noqa: BLE001 - the client must not hang
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} failed in this adapter: "
                             f"{type(exc).__name__}: {exc}"}]})
        return _result(request_id, {
            "content": [{"type": "text",
                         "text": json.dumps(payload, indent=2, sort_keys=True,
                                            default=str)}],
            "isError": False,
        })


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _result(request_id, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _write(outstream, payload: dict) -> None:
    outstream.write(json.dumps(payload, default=str) + "\n")
    outstream.flush()


def serve(instream=None, outstream=None, server=None) -> int:
    """One JSON-RPC object per line. Returns when stdin closes."""
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    server = server or ContinuationServer(os.environ.get(envelope_env_name(), ""))

    for line in instream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(outstream, _error(None, -32700, f"Parse error: {exc}"))
            continue
        if not isinstance(request, dict):
            _write(outstream, _error(None, -32600, "Request must be an object"))
            continue
        try:
            response = server.handle(request)
        except Exception as exc:  # noqa: BLE001 - the client must not hang
            response = _error(request.get("id"), -32603,
                              f"Internal error: {type(exc).__name__}: {exc}")
        if response is not None:
            _write(outstream, response)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="serveros-continuation-mcp",
        description="One wake's continuation control surface. Carries the "
                    "authority of the capability envelope it was started with, "
                    "and no other.")
    parser.add_argument("--envelope",
                        help="the capability envelope filed for this wake "
                             f"(default: ${envelope_env_name()})")
    parser.add_argument("--list-tools", action="store_true",
                        help="print the tool surface and exit")
    args = parser.parse_args(argv)

    if args.list_tools:
        for tool in TOOLS:
            print(f"{tool['name']:<24} {tool['description'].splitlines()[0]}")
        return 0

    path = args.envelope or os.environ.get(envelope_env_name(), "")
    try:
        server = ContinuationServer(path)
    except Exception as exc:  # noqa: BLE001 - refused loudly, on purpose
        # A surface started without a usable envelope would be a surface that
        # answers for any wake. It refuses to start instead, and says which file
        # it was pointed at, because a silent empty surface here is the failure
        # this whole adapter exists to prevent.
        sys.stderr.write(
            f"serveros-continuation-mcp: {type(exc).__name__}: {exc}\n"
            f"  envelope: {path!r}\n"
            f"  This surface carries one wake's authority and cannot be served "
            f"without the envelope that names it. It is started by the wake "
            f"launcher, which files that envelope and sets "
            f"${envelope_env_name()} to it.\n")
        return 2
    return serve(server=server)


if __name__ == "__main__":
    sys.exit(main())
