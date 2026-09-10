"""JSON-first command line interface for the ServerOS v3 control plane."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .board import render as render_board
from .context import resolve_capsule
from .engine import ServerOS, default_state_dir
from .migration import MigrationManager
from .model import ItemState, Outcome, RunState
from .store import ServerOSStore
from .federation import publish_inventory, check_inventory, proposal as federation_proposal


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
        return {**item.to_dict(), "deliverables": server.store.list_deliverables(args.id), "events": server.store.item_events(args.id)}
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
    if args.action == "outcome":
        return server.store.set_outcome(args.id, args.value)
    raise ValueError("unsupported item action")


def _cmd_activity(args):
    server = _server(args)
    if args.action == "add":
        return server.add_activity(args.item, args.activity, args.kind, args.needs)
    if args.action == "transition":
        return server.store.transition_activity(args.item, args.activity, args.to)
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
    if args.action == "evidence":
        return server.store.record_evidence(args.item,args.run,kind=args.kind,locator=args.locator,command=args.command,cases=args.case,exit_code=args.exit_code,revision=args.revision,dirty=args.dirty,verdict=args.verdict)
    if args.action == "finalize":
        return server.store.finalize_run(args.item,args.run,verdict=args.verdict,exit_code=args.exit_code,command=args.command,cases=args.case,revision=args.revision,dirty=args.dirty)
    raise ValueError("unsupported run action")


def _cmd_context(args):
    if args.action == "resolve":
        # The Capsule itself is a ServerOS product component; this only delegates.
        # Roots are resolved here and handed over, so that `run show` and
        # `context resolve` can never end up reading two different worlds.
        return resolve_capsule(
            getattr(args, "item", None), getattr(args, "cwd", "") or None, args
        )
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


def _cmd_deliverable(args):
    store = ServerOSStore(_state_dir(args))
    if args.action == "add":
        return store.add_deliverable(args.item, args.kind, args.locator)
    if args.action == "list":
        return store.list_deliverables(args.item)
    raise ValueError("unsupported deliverable action")

def _cmd_source(args):
    return ServerOSStore(_state_dir(args)).register_source(args.domain, args.stable_id, revision=args.revision, locator=args.locator)
def _cmd_observation(args):
    return ServerOSStore(_state_dir(args)).record_observation(args.source_id, args.id, semantic_type=args.semantic_type, content_hash=args.content_hash)
def _cmd_claim(args):
    return ServerOSStore(_state_dir(args)).record_claim(args.id, args.locator, args.observation)
def _cmd_feedback(args):
    if args.action == 'record':
        return ServerOSStore(_state_dir(args)).record_feedback(
            args.item, args.locator, args.kind, args.observation,
        )
    raise ValueError("unsupported feedback action")

def _cmd_candidate(args):
    s=ServerOSStore(_state_dir(args))
    if args.action == 'create': return s.create_candidate(args.id,args.project,args.title,args.kind,dedupe_key=args.dedupe_key,claim_ids=args.claim,domain=args.domain,external_id=args.external_id,external_revision=args.external_revision)
    if args.action == 'match': return s.match_candidate(args.id,args.item)
    if args.action == 'promote':
        if args.item: return s.promote_candidate(args.id,args.item)
        if args.create_new:
            if not all((args.project,args.prefix,args.title,args.kind)): raise ValueError('create-new promotion requires project, prefix, title, kind')
            return s.promote_candidate_to_new_item(
                args.id,
                project=args.project,
                prefix=args.prefix,
                title=args.title,
                kind=args.kind,
            )
        raise ValueError('promotion requires explicit --item or --create-new approval')
    state={'merge':'MERGED','reject':'REJECTED','defer':'DEFERRED'}[args.action]
    with s.transaction(immediate=True) as c:
        c.execute('UPDATE item_candidates SET state=? WHERE candidate_id=?',(state,args.id))
        return dict(c.execute('SELECT * FROM item_candidates WHERE candidate_id=?',(args.id,)).fetchone())

def _cmd_federation(args):
    store=ServerOSStore(_state_dir(args))
    store.resolve_evidence_run(args.evidence_run)
    if args.action=='inventory':
        return publish_inventory(
            args.domain,args.source,args.target,args.evidence_run,
            store.record_publication_receipt,store.get_publication_receipt,
        )
    observed=[json.loads(x) for x in Path(args.observed).read_text().splitlines() if x.strip()]
    desired=[json.loads(x) for x in Path(args.desired).read_text().splitlines() if x.strip()]
    d=check_inventory(observed,desired,evidence_run=args.evidence_run)
    return federation_proposal(d) if args.action=='proposal' else d


def _cmd_board(args):
    if args.action == "render":
        return render_board(ServerOSStore(_state_dir(args)), args.project, args.output_dir)
    raise ValueError("unsupported board action")


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
    parser = argparse.ArgumentParser(prog="serverosctl", description="ServerOS v3 control plane", parents=[_common()])
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
    outcome = item_actions.add_parser("outcome", parents=[_common()])
    outcome.add_argument("id"); outcome.add_argument("--set", dest="value", required=True, choices=[value.value for value in Outcome])

    activity = groups.add_parser("activity", parents=[_common()])
    activity_actions = activity.add_subparsers(dest="action", required=True)
    add = activity_actions.add_parser("add", parents=[_common()])
    add.add_argument("item"); add.add_argument("activity"); add.add_argument("--kind", required=True)
    add.add_argument("--needs", action="append", default=[])
    at = activity_actions.add_parser("transition", parents=[_common()])
    at.add_argument("item"); at.add_argument("activity"); at.add_argument("--to", required=True)

    source = groups.add_parser("source", parents=[_common()])
    source.add_argument("domain"); source.add_argument("stable_id"); source.add_argument("--revision", required=True); source.add_argument("--locator", required=True)
    obs = groups.add_parser("observation", parents=[_common()])
    obs.add_argument("source_id"); obs.add_argument("id"); obs.add_argument("--semantic-type", required=True); obs.add_argument("--content-hash", required=True)
    claim = groups.add_parser("claim", parents=[_common()])
    claim.add_argument("id"); claim.add_argument("--locator", required=True); claim.add_argument("--observation", action="append", required=True)
    feedback = groups.add_parser("feedback", parents=[_common()])
    feedback_actions = feedback.add_subparsers(dest="action", required=True)
    feedback_record = feedback_actions.add_parser("record", parents=[_common()])
    feedback_record.add_argument("item"); feedback_record.add_argument("--observation", required=True)
    feedback_record.add_argument("--locator", required=True); feedback_record.add_argument("--kind", required=True)

    candidate=groups.add_parser("candidate", parents=[_common()]); ca=candidate.add_subparsers(dest="action",required=True)
    cc=ca.add_parser("create"); cc.add_argument("id"); cc.add_argument("--project",required=True); cc.add_argument("--title",required=True); cc.add_argument("--kind",required=True); cc.add_argument("--dedupe-key",required=True); cc.add_argument("--claim",action="append",required=True); cc.add_argument("--domain",default=""); cc.add_argument("--external-id"); cc.add_argument("--external-revision")
    cm=ca.add_parser("match"); cm.add_argument("id"); cm.add_argument("--item",required=True)
    cp=ca.add_parser("promote"); cp.add_argument("id"); cp.add_argument("--item"); cp.add_argument("--create-new",action="store_true"); cp.add_argument("--project"); cp.add_argument("--prefix"); cp.add_argument("--title"); cp.add_argument("--kind")
    for action in ("merge","reject","defer"):
        x=ca.add_parser(action); x.add_argument("id")
    federation=groups.add_parser("federation",parents=[_common()]); federation.add_argument("domain"); fa=federation.add_subparsers(dest="action",required=True)
    fi=fa.add_parser("inventory"); fi.add_argument("--source",required=True); fi.add_argument("--target",required=True); fi.add_argument("--evidence-run",required=True)
    for action in ("check","proposal"):
        fx=fa.add_parser(action); fx.add_argument("--observed",required=True); fx.add_argument("--desired",required=True); fx.add_argument("--evidence-run",required=True)

    run = groups.add_parser("run", parents=[_common()])
    run_actions = run.add_subparsers(dest="action", required=True)
    begin = run_actions.add_parser("begin", parents=[_common()])
    begin.add_argument("item"); begin.add_argument("activity"); begin.add_argument("--worker", required=True)
    begin.add_argument("--idempotency-key", required=True)
    show_run = run_actions.add_parser("show", parents=[_common()]); show_run.add_argument("item"); show_run.add_argument("run")
    advance = run_actions.add_parser("advance", parents=[_common()]); advance.add_argument("item"); advance.add_argument("run")
    advance.add_argument("--to", required=True, choices=[state.value for state in RunState])
    evidence = run_actions.add_parser("evidence", parents=[_common()]); evidence.add_argument("item"); evidence.add_argument("run"); evidence.add_argument("--kind",required=True); evidence.add_argument("--locator",required=True); evidence.add_argument("--command",required=True); evidence.add_argument("--case",action="append",default=[]); evidence.add_argument("--exit-code",type=int,required=True); evidence.add_argument("--revision",required=True); evidence.add_argument("--verdict",required=True); evidence.add_argument("--dirty",action="store_true")
    finalize = run_actions.add_parser("finalize", parents=[_common()]); finalize.add_argument("item"); finalize.add_argument("run"); finalize.add_argument("--verdict",required=True); finalize.add_argument("--exit-code",type=int,required=True); finalize.add_argument("--command",required=True); finalize.add_argument("--case",action="append",default=[]); finalize.add_argument("--revision",required=True); finalize.add_argument("--dirty",action="store_true")

    context = groups.add_parser("context", parents=[_common()])
    context_actions = context.add_subparsers(dest="action", required=True)
    attach = context_actions.add_parser("attach", parents=[_common()])
    attach.add_argument("item"); attach.add_argument("activity"); attach.add_argument("--repo", required=True)
    attach.add_argument("--mode", required=True, choices=["inspect", "test", "develop", "release"])
    attach.add_argument("--revision", required=True); attach.add_argument("--worktree", default="")
    attach.add_argument("--dirty", action="store_true")
    resolve = context_actions.add_parser("resolve", parents=[_common()])
    resolve.add_argument("item", nargs="?", help="Item id; inferred from --cwd or the current directory when omitted")
    resolve.add_argument("--cwd", default="", help="directory to infer the Item from")

    deliverable = groups.add_parser("deliverable", parents=[_common()])
    deliverable_actions = deliverable.add_subparsers(dest="action", required=True)
    add_deliverable = deliverable_actions.add_parser("add", parents=[_common()])
    add_deliverable.add_argument("item"); add_deliverable.add_argument("--kind", required=True); add_deliverable.add_argument("--locator", required=True)
    list_deliverables = deliverable_actions.add_parser("list", parents=[_common()]); list_deliverables.add_argument("item")

    board = groups.add_parser("board", parents=[_common()])
    board_actions = board.add_subparsers(dest="action", required=True)
    render = board_actions.add_parser("render", parents=[_common()])
    render.add_argument("--project", required=True); render.add_argument("--output-dir", required=True)

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
            "deliverable": _cmd_deliverable,
            "source": _cmd_source, "observation": _cmd_observation, "claim": _cmd_claim, "feedback": _cmd_feedback,
            "candidate": _cmd_candidate, "federation": _cmd_federation,
            "board": _cmd_board,
            "migrate": _cmd_migrate,
        }[args.group]
        emit(handler(args))
        return 0
    except (ValueError, KeyError, OSError) as exc:
        emit({"error": {"code": "INVALID_INPUT", "message": str(exc)}}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
