"""Command-line interface for the Work Engine.

Commands::

    workengine create   --title <t> --requirement <r> [--repo ..] [--worktree ..]
                        [--plan step] [--plan step ...] [--id ..]
    workengine status   <id>
    workengine advance  <id> --to <STATE> [--note ..]
    workengine resume   <id> [--json]
    workengine list     [--status STATE]
    workengine evidence <id> --path <p> [--kind k] [--note n]

Global flags (may appear before the subcommand)::

    --state-dir PATH     Absolute path to the state directory.
    --json               Emit machine-readable JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .engine import WorkEngine, default_state_dir
from .model import State
from .state_machine import IllegalTransition, allowed_transitions

STATES = [s.value for s in State]


def _build_engine(args: argparse.Namespace) -> WorkEngine:
    state_dir = getattr(args, "state_dir", None)
    return WorkEngine(state_dir=state_dir or default_state_dir())


def _item_to_json(item) -> dict:
    return item.to_dict()


# -- output helpers ---------------------------------------------------------------

def _print_item(item, next_states=None) -> None:
    print(f"id            : {item.id}")
    print(f"title         : {item.title}")
    print(f"status        : {item.status.value}")
    print(f"created_at    : {item.created_at}")
    print(f"updated_at    : {item.updated_at}")
    print(f"requirement   : {item.requirement}")
    print(f"repo          : {item.repo or '-'}")
    print(f"worktree      : {item.worktree or '-'}")
    print("plan          :")
    for step in item.plan:
        print(f"  - {step}")
    print("evidence      :")
    for ev in item.evidence:
        print(f"  - [{ev.get('kind', 'file')}] {ev.get('path', '')}  {('(' + ev.get('note', '') + ')') if ev.get('note') else ''}")
    print("history       :")
    for h in item.history:
        seg = (h.get("from") + " -> " if h.get("from") else "") + h.get("to", "")
        print(f"  - {h.get('at', '')}  {h.get('action', '')}  {seg}" + (f"  ({h.get('note')})" if h.get("note") else ""))
    if next_states is not None:
        print("next transitions : " + (", ".join(next_states) if next_states else "<none>"))


# -- command handlers -------------------------------------------------------------

def _cmd_create(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    item = engine.create(
        title=args.title,
        requirement=args.requirement,
        repo=args.repo or "",
        worktree=args.worktree or "",
        plan=args.plan or [],
        item_id=args.id,
    )
    if getattr(args, "json", False):
        print(json.dumps(_item_to_json(item), ensure_ascii=False, indent=2))
    else:
        print(f"created {item.id}")
        _print_item(item, [s.value for s in allowed_transitions(item.status)])
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    item = engine.get(args.id)
    if item is None:
        print(f"error: unknown work item id: {args.id}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(_item_to_json(item), ensure_ascii=False, indent=2))
    else:
        _print_item(item)
    return 0


def _cmd_advance(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    try:
        item = engine.advance(args.id, args.to, note=args.note)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except IllegalTransition as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(_item_to_json(item), ensure_ascii=False, indent=2))
    else:
        print(f"advanced {item.id} -> {item.status.value}")
        _print_item(item)
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    try:
        info = engine.resume(args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    item = info["item"]
    next_states = [s.value for s in info["next"]]
    if getattr(args, "json", False):
        payload = item.to_dict()
        payload["resume"] = {"worktree": item.worktree, "next": next_states}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("# work-engine resume")
        print(f"id            : {item.id}")
        print(f"title         : {item.title}")
        print(f"status        : {item.status.value}")
        print(f"worktree      : {item.worktree or '<unset>'}")
        print("evidence pointers:")
        for ev in item.evidence:
            print(f"  - {ev.get('path', '')}")
        print("next transitions : " + (", ".join(next_states) if next_states else "<none>"))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    items = engine.list(status=args.status)
    if getattr(args, "json", False):
        print(json.dumps([_item_to_json(i) for i in items], ensure_ascii=False, indent=2))
    else:
        if not items:
            print("no work items")
            return 0
        for item in items:
            print(f"{item.id}\t{item.status.value}\t{item.title}")
    return 0


def _cmd_evidence(args: argparse.Namespace) -> int:
    engine = _build_engine(args)
    try:
        item = engine.attach_evidence(args.id, args.path, kind=args.kind, note=args.note)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(_item_to_json(item), ensure_ascii=False, indent=2))
    else:
        print(f"attached evidence to {item.id} -> {args.path}")
    return 0


def _common() -> argparse.ArgumentParser:
    """Global options, exposed on both the top-level parser and every subcommand.

    Duplicating them lets flags like ``--json`` / ``--state-dir`` appear either
    before or after the subcommand. ``default=argparse.SUPPRESS`` is essential:
    without it, a subparser would emit its default (``None``) for an option the
    user supplied *before* the subcommand, quietly overriding the value.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--state-dir",
        default=argparse.SUPPRESS,
        help="Absolute path to the state directory (default: repo state/).",
    )
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Emit machine-readable JSON.")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workengine",
        description="Autonomous Delivery Workflow v0 - C1 Work Engine",
        parents=[_common()],
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a new Work Item.", parents=[_common()])
    p_create.add_argument("--title", required=True)
    p_create.add_argument("--requirement", required=True)
    p_create.add_argument("--repo", default="")
    p_create.add_argument("--worktree", default="")
    p_create.add_argument("--plan", action="append", default=[], help="Plan step; repeatable.")
    p_create.add_argument("--id", default=None)

    p_status = sub.add_parser("status", help="Show a Work Item.", parents=[_common()])
    p_status.add_argument("id")

    p_advance = sub.add_parser("advance", help="Advance a Work Item to a legal state.", parents=[_common()])
    p_advance.add_argument("id")
    p_advance.add_argument("--to", required=True)
    p_advance.add_argument("--note", default=None)

    p_resume = sub.add_parser("resume", help="Resume a Work Item for a fresh session.", parents=[_common()])
    p_resume.add_argument("id")

    p_list = sub.add_parser("list", help="List Work Items.", parents=[_common()])
    p_list.add_argument("--status", default=None, choices=STATES)

    p_evidence = sub.add_parser("evidence", help="Attach an evidence pointer.", parents=[_common()])
    p_evidence.add_argument("id")
    p_evidence.add_argument("--path", required=True)
    p_evidence.add_argument("--kind", default="file")
    p_evidence.add_argument("--note", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = {
        "create": _cmd_create,
        "status": _cmd_status,
        "advance": _cmd_advance,
        "resume": _cmd_resume,
        "list": _cmd_list,
        "evidence": _cmd_evidence,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
