#!/usr/bin/env python3
"""serveros-work-mcp — the Workengine control surface.

Item and Run lifecycle truth lives in this package and nowhere else. This
module is the typed boundary an agent asks through; it is not a second copy of
that truth and it holds no state of its own.

Three things it does not do, each because the alternative is a bug that reads
as a feature:

  * It does not accept an `actor` argument. §15 says the actor is derived from a
    trusted context, not self-reported. A tool that took `actor: "hwz"` would
    let any caller write any name into an audit record, and the audit record is
    the only thing that makes a termination reviewable. The actor comes from
    resolving `context_ref`; if nothing can resolve it, the call is refused.

  * It does not stop anything. "Cancel Run" is a control-plane fact: it moves
    `runs.state` and appends one ledger record. It does not signal a process,
    delete an artifact, or touch a worktree. A caller that expected the worker
    to die will find it still running, which is exactly what
    `runtime_may_still_be_active` says.

  * It does not answer an empty history with silence. `run.events` on a Run
    that nothing was recorded about is a real and common answer, and it is not
    the same as "nothing happened" — the ledger does not record forward
    transitions. The tool returns that distinction rather than an empty list a
    caller has to interpret.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__ as SERVER_VERSION
from .engine import default_state_dir
from .model import RunState
from .store import ActiveWorkerAckRequired, ServerOSStore

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "serveros-work-mcp"

REFUSAL_CODES = (
    "UNTRUSTED_ACTOR",          # no context could be resolved to an actor
    "INVALID_CONTEXT",          # the item/run reference does not exist
    "ACTIVE_WORKER_ACK_REQUIRED",
)

# Where each surface tool lands in the store. §15 writes `abandon_run` as if it
# were a verb of its own; it is not, and this table says so rather than gaining
# a method that would be `cancel_run` with one argument fixed. `abandon` is a
# reason code — the Run ends as CANCELLED either way — and a second entry point
# would be a second place that has to remember the audit record, the active
# worker guard and the terminal restatement rule.
STORE_VERBS = {
    "work.item.get": "get_item",
    "work.run.get": "get_run",
    "work.run.list": "list_runs",
    "work.run.events": "run_events",
    "work.run.cancel": "cancel_run",
    "work.run.abandon": "cancel_run",  # operation="abandon"
}


def _schema(properties=None, required=None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


_REF = {"type": "string"}
_CONTEXT = {
    "type": "string",
    "description": (
        "A trusted context minted by ServerOS. The actor is resolved from this "
        "and is never taken from the caller."
    ),
}

TOOLS: tuple[dict, ...] = (
    {"name": "work.item.get",
     "description": "One Item: state, outcome, activities and its 0..N repo contexts.",
     "inputSchema": _schema({"item": _REF}, ["item"])},
    {"name": "work.run.get",
     "description": "One Run: state, worker, attempt, activity and artifact directory.",
     "inputSchema": _schema({"item": _REF, "run": _REF}, ["item", "run"])},
    {"name": "work.run.list",
     "description": "Runs, newest first. Unfiltered by state unless one is named.",
     "inputSchema": _schema({"item": _REF, "state": _REF}, [])},
    {"name": "work.run.events",
     "description": (
        "The ledger records about one Run, with an explicit statement of what "
        "the ledger does not cover. An empty list is not 'nothing happened'."
     ),
     "inputSchema": _schema({"item": _REF, "run": _REF}, ["item", "run"])},
    {"name": "work.run.cancel",
     "description": (
        "End a Run as CANCELLED, recording why and who. Control-plane fact "
        "only: it does not stop a worker process. dry_run=true plans without "
        "writing anything."
     ),
     "inputSchema": _schema({
         "item": _REF, "run": _REF, "reason": {"type": "string"},
         "context_ref": _CONTEXT,
         "reason_code": _REF, "target": _REF,
         "evidence": {"type": "array", "items": {"type": "string"}},
         "acknowledge_active_worker": {"type": "boolean"},
         "dry_run": {"type": "boolean"},
     }, ["item", "run", "reason", "context_ref"])},
    {"name": "work.run.abandon",
     "description": (
        "End a Run as CANCELLED with reason_code ABANDONED — the same "
        "operation as cancel_run, named for the case where the Run is being "
        "given up rather than called off. dry_run=true plans without writing."
     ),
     "inputSchema": _schema({
         "item": _REF, "run": _REF, "reason": {"type": "string"},
         "context_ref": _CONTEXT,
         "target": _REF,
         "evidence": {"type": "array", "items": {"type": "string"}},
         "acknowledge_active_worker": {"type": "boolean"},
         "dry_run": {"type": "boolean"},
     }, ["item", "run", "reason", "context_ref"])},
)


def tool_names() -> tuple[str, ...]:
    return tuple(tool["name"] for tool in TOOLS)


def _item_record(item) -> dict:
    if item is None:
        return {"state": "NOT_FOUND"}
    return {
        "item": item.id,
        "project": item.project,
        "title": item.title,
        "kind": item.kind,
        "state": item.state.value,
        "outcome": item.outcome.value if item.outcome else None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _run_record(run) -> dict:
    if run is None:
        return {"state": "NOT_FOUND"}
    return {
        "item": run.item_id,
        "run": run.id,
        "activity": run.activity_id,
        "worker": run.worker,
        "state": run.state.value,
        "attempt": run.attempt,
        "artifact_dir": run.artifact_dir,
        "started_at": run.started_at,
        "ended_at": run.ended_at or None,
    }


def _termination_plan(record: dict) -> dict:
    """The store's termination record, in the field names §15 asks for.

    The store speaks `from`/`to` because its event payload is a stored format
    that older Runs were already written in. This surface speaks
    `current_state`/`target_state` because that is what an agent branching on
    the answer reads. One translation, in one direction, at the boundary.
    """
    already = bool(record.get("already_terminal"))
    dry = bool(record.get("dry_run"))
    return {
        "item": record.get("item"),
        "run": record.get("run"),
        "operation": record.get("operation"),
        "current_state": record.get("from"),
        "target_state": record.get("to"),
        "reason_code": record.get("reason_code"),
        "reason": record.get("reason"),
        "actor": record.get("actor"),
        "evidence": record.get("evidence") or [],
        "active_worker_risk": bool(record.get("runtime_may_still_be_active")),
        "would_transition": not already,
        "would_write_event": not already and not dry,
        "already_terminal": already,
        "dry_run": dry,
        # Written into the answer rather than left to the caller's memory of
        # the design, because "cancel" reads as "stop" to everybody who has not
        # read this file.
        "does_not_stop_the_worker": True,
        "note": (
            "This is a control-plane fact. No worker process was signalled, no "
            "artifact or worktree was removed."
        ),
    }


class WorkControlClient:
    """The tool implementations, over a store and a context resolver.

    Both are injectable and both have an honest default. The store defaults to
    the deployment's own state directory. The resolver defaults to `None`, and
    with no resolver every mutation is refused — the same shape the node
    transport uses with `connector=None`, and for the same reason: a control
    surface that cannot verify who is asking must not act on the answer it
    could not get.
    """

    def __init__(self, *, store=None, resolve_context=None, state_dir=None) -> None:
        self._store = store
        self._resolve_context = resolve_context
        self._state_dir = state_dir

    @property
    def store(self) -> ServerOSStore:
        if self._store is None:
            self._store = ServerOSStore(self._state_dir or default_state_dir())
        return self._store

    # -- the trusted actor ---------------------------------------------------

    def actor_for(self, context_ref: str) -> dict:
        """Resolve a context into an actor, or say why it could not be.

        Never consults the caller for a name and never invents one. The three
        outcomes are distinct on purpose: no resolver bound, a ref that does not
        resolve, and a record that carries no actor are three different repair
        actions for whoever reads the refusal.
        """
        if not str(context_ref or "").strip():
            return {"state": "REFUSED", "reason_code": "UNTRUSTED_ACTOR",
                    "detail": "no context_ref was presented; there is nothing to "
                              "resolve an actor from and the caller's word is not one"}
        if self._resolve_context is None:
            return {"state": "REFUSED", "reason_code": "UNTRUSTED_ACTOR",
                    "detail": "no trusted-context resolver is bound to this surface, "
                              "so no context can be verified"}
        try:
            record = self._resolve_context(context_ref) or {}
        except Exception as exc:
            return {"state": "REFUSED", "reason_code": "UNTRUSTED_ACTOR",
                    "detail": f"the context resolver did not answer: {type(exc).__name__}"}
        if not record.get("found"):
            return {"state": "REFUSED", "reason_code": "UNTRUSTED_ACTOR",
                    "detail": record.get("because") or "the context_ref did not resolve"}
        actor = str(record.get("actor_ref") or "").strip()
        if not actor:
            return {"state": "REFUSED", "reason_code": "UNTRUSTED_ACTOR",
                    "detail": "the context resolved but carries no actor_ref"}
        return {"state": "RESOLVED", "actor_ref": actor,
                "context_ref": record.get("context_ref") or context_ref}

    # -- reads ---------------------------------------------------------------

    def item_get(self, item: str) -> dict:
        return _item_record(self.store.get_item(item))

    def run_get(self, item: str, run: str) -> dict:
        return _run_record(self.store.get_run(item, run))

    def run_list(self, item=None, state=None) -> dict:
        runs = self.store.list_runs(item, state=state)
        return {"count": len(runs), "runs": [_run_record(run) for run in runs]}

    def run_events(self, item: str, run: str) -> dict:
        return self.store.run_events_summary(item, run)

    # -- the one mutation ----------------------------------------------------

    def terminate(self, *, operation: str, arguments: dict) -> dict:
        item = arguments["item"]
        run = arguments["run"]
        reason = arguments["reason"]
        dry_run = bool(arguments.get("dry_run"))

        actor = self.actor_for(arguments.get("context_ref"))
        if actor["state"] != "RESOLVED":
            return {"state": "REFUSED", "dry_run": dry_run, **actor}

        # Read before writing so the plan is about the Run that exists. The
        # store would refuse an unknown Run too, but its refusal would arrive
        # after the actor check and would report as a store error rather than
        # as the missing reference it is.
        if self.store.get_run(item, run) is None:
            return {"state": "REFUSED", "reason_code": "INVALID_CONTEXT",
                    "detail": f"no Run {item}/{run}", "dry_run": dry_run}

        try:
            record = self.store.cancel_run(
                item, run,
                reason=reason,
                actor=actor["actor_ref"],
                reason_code=arguments.get("reason_code"),
                target=arguments.get("target") or RunState.CANCELLED,
                evidence=tuple(arguments.get("evidence") or ()),
                operation=operation,
                acknowledge_active_worker=bool(arguments.get("acknowledge_active_worker")),
                dry_run=dry_run,
            )
        except ActiveWorkerAckRequired as exc:
            # §16. A RUNNING Run is refused by default because this control
            # plane owns no worker handle; the caller gets the code and the
            # state rather than a sentence to parse.
            current = self.store.get_run(item, run)
            return {
                "state": "REFUSED",
                "reason_code": "ACTIVE_WORKER_ACK_REQUIRED",
                "detail": str(exc),
                "dry_run": dry_run,
                "current_state": current.state.value if current else None,
                "target_state": RunState(arguments.get("target") or RunState.CANCELLED).value,
                "active_worker_risk": True,
                "would_transition": False,
                "would_write_event": False,
                "resume_with": "acknowledge_active_worker=true",
            }
        except KeyError as exc:
            return {"state": "REFUSED", "reason_code": "INVALID_CONTEXT",
                    "detail": str(exc), "dry_run": dry_run}
        except ValueError as exc:
            return {"state": "REFUSED", "reason_code": "INVALID_CONTEXT",
                    "detail": str(exc), "dry_run": dry_run}

        return {"state": "PLANNED" if dry_run else "TERMINATED",
                **_termination_plan(record)}

    def call(self, name: str, arguments: dict | None = None) -> dict:
        args = dict(arguments or {})
        if name == "work.item.get":
            return self.item_get(args["item"])
        if name == "work.run.get":
            return self.run_get(args["item"], args["run"])
        if name == "work.run.list":
            return self.run_list(args.get("item"), args.get("state"))
        if name == "work.run.events":
            return self.run_events(args["item"], args["run"])
        if name == "work.run.cancel":
            return self.terminate(operation="cancel", arguments=args)
        if name == "work.run.abandon":
            return self.terminate(operation="abandon", arguments=args)
        raise RuntimeError(f"unimplemented Work MCP operation: {name}")


def _result(request_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


class WorkMcpServer:
    def __init__(self, client=None) -> None:
        self.client = client or WorkControlClient()

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
                    "Item and Run lifecycle truth lives in the Workengine and "
                    "nowhere else. This surface reads it and can end a Run; it "
                    "does not stop processes, remove artifacts or clean "
                    "worktrees, and ending a Run is a control-plane fact rather "
                    "than a kill. The actor on every mutation is resolved from "
                    "context_ref and is never taken from the caller."
                ),
            })
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return _result(request_id, {"tools": [dict(tool) for tool in TOOLS]})
        if method == "tools/call":
            return self._call_tool(request_id, params)
        if method == "ping":
            return _result(request_id, {})
        if str(method).startswith("notifications/"):
            return None
        return _error(request_id, -32601, f"Method not found: {method!r}")

    def _call_tool(self, request_id, params: dict) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in tool_names():
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"Unknown tool {name!r}. Available: {list(tool_names())}"}],
            })
        tool = TOOLS[tool_names().index(name)]
        allowed = set(tool["inputSchema"]["properties"])
        required = set(tool["inputSchema"].get("required") or [])
        unexpected = set(arguments) - allowed
        missing = required - set(arguments)
        if unexpected or missing:
            # An `actor` argument is refused here rather than ignored, because
            # silently dropping it would let a caller believe a name it supplied
            # was the one recorded.
            problem = []
            if unexpected:
                problem.append(f"unexpected={sorted(unexpected)}")
            if missing:
                problem.append(f"missing={sorted(missing)}")
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"{name}: {'; '.join(problem)}"}],
            })
        try:
            result = self.client.call(name, arguments)
        except KeyError as exc:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"{name}: INVALID_CONTEXT: {exc}"}],
            })
        except Exception as exc:
            return _result(request_id, {
                "isError": True,
                "content": [{"type": "text", "text":
                             f"{name} failed: {type(exc).__name__}: {exc}"}],
            })
        return _result(request_id, {
            "content": [{"type": "text", "text":
                         json.dumps(result, indent=2, sort_keys=True, default=str)}],
            "isError": False,
        })


def serve(instream=None, outstream=None, server=None) -> int:
    """Line-delimited JSON-RPC over stdio, the framing MCP uses."""
    instream = instream or sys.stdin
    outstream = outstream or sys.stdout
    server = server or WorkMcpServer()
    for line in instream:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            outstream.write(json.dumps(_error(None, -32700, "Parse error")) + "\n")
            outstream.flush()
            continue
        response = server.handle(request)
        if response is not None:
            outstream.write(json.dumps(response, default=str) + "\n")
            outstream.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serveros-work-mcp")
    parser.add_argument("--state-dir", help="absolute ServerOS state directory")
    parser.add_argument("--list-tools", action="store_true")
    args = parser.parse_args(argv)
    if args.list_tools:
        for tool in TOOLS:
            print(f"{tool['name']:<20} {tool['description']}")
        return 0
    state_dir = Path(args.state_dir) if args.state_dir else None
    return serve(server=WorkMcpServer(WorkControlClient(state_dir=state_dir)))


if __name__ == "__main__":
    sys.exit(main())
