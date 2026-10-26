"""Measure a repository for an existing, explicitly bound continuation wake.

Location/configuration is supplied by the outer owner assembly. The original
wake envelope is read, never reminted: its wall expiry must not move on attach.
Stored Git facts are context metadata, not signed execution evidence.
"""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .model import ContextMode, ExecutionContext
from .owner_artifacts import real_path


def _runtime(name):
    key = "serveros_runtime_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).resolve().parents[1] / "runtime" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def execution_context(store, request, data, grant):
    import yaml
    workspace = getattr(store, "owner_workspace_root", None)
    receipts = getattr(store, "owner_continuation_receipts_root", None)
    if workspace is None or receipts is None:
        raise ValueError("OWNER_CONTEXT_BINDING_REQUIRED: explicit workspace and continuation receipt roots required")
    workspace, receipts = real_path(workspace), real_path(receipts)
    if not workspace.is_dir() or not receipts.is_dir():
        raise ValueError("OWNER_CONTEXT_BINDING_REQUIRED: configured roots must exist")
    if receipts.stat().st_uid != os.getuid() or receipts.stat().st_mode & 0o077:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: receipt root must be private to owner")
    catalog = real_path(workspace / "ops/workspace/serveros/projects.yaml")
    config = (yaml.safe_load(catalog.read_text(encoding="utf-8")) or {}).get("projects", {}).get(request["project"])
    if not isinstance(config, dict):
        raise ValueError("OWNER_REPOSITORY_MISMATCH: project missing from authored catalog")
    repo = request["repo"]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", repo):
        raise ValueError("OWNER_REPOSITORY_MISMATCH: exact repository identifier required")
    allowed = set(config.get("canonical_repos") or ())
    if config.get("worktrees_under"): allowed.add(config["worktrees_under"])
    if repo not in allowed:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: repository outside project catalog")
    worktree = real_path(workspace / "worktrees" / repo / request["item_ref"])
    checkpoint = data.get("checkpoints", {}).get(request["checkpoint_ref"])
    if not isinstance(checkpoint, dict) or checkpoint.get("work_item") != request["item_ref"]:
        raise ValueError("OWNER_CHECKPOINT_MISMATCH: exact current checkpoint required")
    if checkpoint.get("superseded_by") or (checkpoint.get("parked_at") and not checkpoint.get("unparked_at")):
        raise ValueError("OWNER_CHECKPOINT_MISMATCH: checkpoint is not current")
    now = datetime.datetime.fromtimestamp(store.clock(), datetime.timezone.utc)
    wake = _runtime("wake")
    spend = wake.spending(grant.get("budget"), item=request["item_ref"], grant_ref=request["grant_ref"])
    if spend.get("reason"):
        raise ValueError("OWNER_ENVELOPE_MISMATCH: current wake spending is not authorized")
    candidates = []
    files = list(receipts.glob("*.envelope.json"))
    if len(files) > 4096:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: bounded envelope catalog exceeded")
    for path in files:
        real_path(path)
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope must be private to owner")
        if path.stat().st_size > 65536:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: oversized envelope")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict):
            raise ValueError("OWNER_ENVELOPE_MISMATCH: malformed envelope")
        if envelope.get("checkpoint_ref") != request["checkpoint_ref"] or envelope.get("grant_ref") != request["grant_ref"]:
            continue
        expires = datetime.datetime.fromisoformat(str(envelope.get("expires_at")))
        issued = datetime.datetime.fromisoformat(str(envelope.get("issued_at")))
        if expires.tzinfo is None or issued.tzinfo is None or not issued <= now < expires:
            continue
        written = datetime.datetime.fromisoformat(str(checkpoint.get("written_at")))
        if written.tzinfo is None or issued < written:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope predates checkpoint")
        wait = _runtime("continuation").wait_state(checkpoint, admissions=data.get("admissions", {}), now=envelope["issued_at"])
        if wait.get("state") != "WAIT_OVER":
            raise ValueError("OWNER_ENVELOPE_MISMATCH: original wake prerequisite not established")
        attempt = envelope.get("attempt")
        if type(attempt) is not int or not 1 <= attempt <= spend["attempts_allowed"]:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: invalid wake attempt")
        key = wake.action_key(request["item_ref"], checkpoint.get("epoch"), attempt)
        if path != wake.envelope_path(receipts, key):
            raise ValueError("OWNER_ENVELOPE_MISMATCH: envelope filename differs from producer key")
        expected = wake.capability_envelope(checkpoint, grant, request["grant_ref"], attempt=attempt,
            key=key, spend=spend, now=envelope["issued_at"])
        if envelope != expected:
            raise ValueError("OWNER_ENVELOPE_MISMATCH: original producer derivation/current binding differs")
        candidates.append(envelope)
    if len(candidates) != 1:
        raise ValueError("OWNER_ENVELOPE_MISMATCH: exactly one current existing wake envelope required")
    resolved = _runtime("bounded_executor").binding(envelope=candidates[0], checkpoint=checkpoint,
        grant=grant, worktrees_root=workspace / "worktrees")
    if Path(resolved["worktree"]) != worktree:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: signed worktree differs from project layout")
    def git(*args):
        result = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError("OWNER_REPOSITORY_MISMATCH: cannot measure local Git repository")
        return result.stdout.strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != worktree:
        raise ValueError("OWNER_REPOSITORY_MISMATCH: Git top-level differs from worktree")
    revision, dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain"))
    for key, actual in (("expected_revision", revision), ("expected_worktree", str(worktree)), ("expected_dirty", dirty)):
        if key in request and request[key] != actual:
            raise ValueError("OWNER_REPOSITORY_MISMATCH: caller expectation differs from measured Git facts")
    return ExecutionContext(request["item_ref"], request["activity_ref"], repo, ContextMode(request["mode"]), revision, str(worktree), dirty)
