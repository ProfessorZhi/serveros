"""JSON-first command line interface for ServerOS v2."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .engine import ServerOS, default_state_dir
from .migration import MigrationManager
from .model import ItemState, RunState
from .store import ServerOSStore


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def emit(value, stream=sys.stdout) -> None:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_value), file=stream)


def _state_dir(args) -> Path:
    if getattr(args, "state_dir", None):
        path = Path(args.state_dir)
        if not path.is_absolute():
            raise ValueError("--state-dir must be absolute")
        return path.resolve()
    return default_state_dir()


def _server(args) -> ServerOS:
    artifacts = Path(args.artifacts_root).resolve() if getattr(args, "artifacts_root", None) else None
    return ServerOS(state_dir=_state_dir(args), artifacts_root=artifacts)


def _cmd_item(args):
    server = _server(args)
    if args.action == "create":
        server.register_project(args.project, args.prefix)
        return server.create_item(args.project, args.title, args.kind, args.card_locator)
    if args.action == "show":
        item = server.store.get_item(args.id)
        if item is None:
            raise KeyError(f"unknown Item: {args.id}")
        return {**item.to_dict(), "events": server.store.item_events(args.id)}
    if args.action == "list":
        return [item.to_dict() for item in server.store.list_items(args.project)]
    if args.action == "transition":
        side = {}
        if ItemState(args.to) in {ItemState.WAITING, ItemState.NEEDS_HUMAN, ItemState.DELEGATED}:
            side = {
                "reason": args.reason,
                "return_state": args.return_state,
                "unblock_condition": args.unblock_condition,
            }
        return server.store.transition_item(args.id, args.to, note=args.note, **side)
    if args.action == "reopen":
        return server.store.reopen_item(args.id, args.reason, args.source)
    raise ValueError("unsupported item action")


def _cmd_activity(args):
    server = _server(args)
    if args.action == "add":
        return server.add_activity(args.item, args.activity, args.kind, args.needs)
    raise ValueError("unsupported activity action")


def _run_payload(server: ServerOS, run) -> dict:
    item = server.store.get_item(run.item_id)
    return {
        "item": run.item_id,
        "activity": run.activity_id,
        "run": run.id,
        "project": item.project,
        "worker": run.worker,
        "state": run.state.value,
        "attempt": run.attempt,
        "artifact_dir": run.artifact_dir,
        "contexts": [asdict(context) for context in server.store.execution_contexts(run.item_id, run.activity_id)],
    }


def _cmd_run(args):
    server = _server(args)
    if args.action == "begin":
        return _run_payload(server, server.begin_run(args.item, args.activity, args.worker, args.idempotency_key))
    if args.action == "show":
        return _run_payload(server, server.resume_run(args.item, args.run))
    if args.action == "advance":
        return _run_payload(server, server.advance_run(args.item, args.run, args.to))
    raise ValueError("unsupported run action")


def _cmd_context(args):
    server = _server(args)
    return server.attach_context(
        args.item,
        args.activity,
        args.repo,
        args.mode,
        revision=args.revision,
        worktree=args.worktree,
        dirty=args.dirty,
    )


def _cmd_migrate(args):
    manager = MigrationManager(ServerOSStore(_state_dir(args)))
    if args.action == "plan":
        return manager.plan(json.loads(Path(args.manifest).read_text(encoding="utf-8")))
    if args.action == "inspect":
        return manager.inspect(args.project)
    if args.action == "apply":
        return manager.apply(args.project)
    if args.action == "verify":
        return manager.verify(args.project)
    if args.action == "compensate":
        return manager.compensate(args.project)
    raise ValueError("unsupported migrate action")


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--state-dir", default=argparse.SUPPRESS)
    common.add_argument("--artifacts-root", default=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="serverosctl", description="ServerOS v2 control plane", parents=[_common()])
    groups = parser.add_subparsers(dest="group", required=True)

    item = groups.add_parser("item", parents=[_common()])
    item_actions = item.add_subparsers(dest="action", required=True)
    create = item_actions.add_parser("create", parents=[_common()])
    create.add_argument("--project", required=True)
    create.add_argument("--prefix", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--kind", required=True)
    create.add_argument("--card-locator")
    show = item_actions.add_parser("show", parents=[_common()]); show.add_argument("id")
    listing = item_actions.add_parser("list", parents=[_common()]); listing.add_argument("--project")
    move = item_actions.add_parser("transition", parents=[_common()])
    move.add_argument("id"); move.add_argument("--to", required=True, choices=[state.value for state in ItemState])
    move.add_argument("--note", default=""); move.add_argument("--reason", default="")
    move.add_argument("--return-state", choices=[state.value for state in ItemState])
    move.add_argument("--unblock-condition", default="")
    reopen = item_actions.add_parser("reopen", parents=[_common()])
    reopen.add_argument("id"); reopen.add_argument("--reason", required=True); reopen.add_argument("--source", required=True)

    activity = groups.add_parser("activity", parents=[_common()])
    activity_actions = activity.add_subparsers(dest="action", required=True)
    add = activity_actions.add_parser("add", parents=[_common()])
    add.add_argument("item"); add.add_argument("activity"); add.add_argument("--kind", required=True)
    add.add_argument("--needs", action="append", default=[])

    run = groups.add_parser("run", parents=[_common()])
    run_actions = run.add_subparsers(dest="action", required=True)
    begin = run_actions.add_parser("begin", parents=[_common()])
    begin.add_argument("item"); begin.add_argument("activity"); begin.add_argument("--worker", required=True)
    begin.add_argument("--idempotency-key", required=True)
    show_run = run_actions.add_parser("show", parents=[_common()]); show_run.add_argument("item"); show_run.add_argument("run")
    advance = run_actions.add_parser("advance", parents=[_common()]); advance.add_argument("item"); advance.add_argument("run")
    advance.add_argument("--to", required=True, choices=[state.value for state in RunState])

    context = groups.add_parser("context", parents=[_common()])
    context_actions = context.add_subparsers(dest="action", required=True)
    attach = context_actions.add_parser("attach", parents=[_common()])
    attach.add_argument("item"); attach.add_argument("activity"); attach.add_argument("--repo", required=True)
    attach.add_argument("--mode", required=True, choices=["inspect", "test", "develop", "release"])
    attach.add_argument("--revision", required=True); attach.add_argument("--worktree", default="")
    attach.add_argument("--dirty", action="store_true")

    migrate = groups.add_parser("migrate", parents=[_common()])
    migrate_actions = migrate.add_subparsers(dest="action", required=True)
    plan = migrate_actions.add_parser("plan", parents=[_common()]); plan.add_argument("--manifest", required=True)
    for action in ("inspect", "apply", "verify", "compensate"):
        child = migrate_actions.add_parser(action, parents=[_common()]); child.add_argument("project")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        handler = {
            "item": _cmd_item,
            "activity": _cmd_activity,
            "run": _cmd_run,
            "context": _cmd_context,
            "migrate": _cmd_migrate,
        }[args.group]
        emit(handler(args))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        emit({"error": {"code": "INVALID_INPUT", "message": str(exc)}}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
