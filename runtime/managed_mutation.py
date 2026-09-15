#!/usr/bin/env python3
"""C3 · A real mutation, through the pipeline, instead of beside it.

    intent -> transient plan -> compliance -> executor -> scoped audit

`network_mutation.py` already has this shape for one action, and one action is
not evidence of a pipeline — it is evidence that one thing works. This module is
the second action, on a different subject, and the question it answers is
whether the first was a pipeline or a coincidence.

The pipeline is not a framework
-------------------------------
There is no plan database, no plan daemon, no scheduler and no plan lifecycle
state machine. `plan()` returns a dict; the next function takes it; it is gone
when the call returns. `managed-mutation-contract.yaml` lists those four as
FORBIDDEN rather than absent, because "we did not build it yet" and "we decided
not to" are different statements and only the second one survives a later
sprint.

The action
----------
`projection.publish`: write a declared projection into
`state/serveros/projections/<name>/`, with a manifest that satisfies the
state-root contract's own gate.

That subject was chosen for a reason worth writing down, because the obvious
choices are wrong. A `docker network create` is C1's action and is structurally
unreachable here — coverage ABSENT on this node denies before an executor is
reached — so it cannot demonstrate a positive path. A container restart stops
something that is serving. A lock file under `locks/` looked ideal and is a
trap: that namespace is READ as lease intent, so a fixture lock left behind by
an interrupted run tells every later reader that a resource is held. A fixture
must not be able to lie to another subsystem, and the cheapest guarantee is not
to write into a namespace that means something.

What a projection has instead is a promise already made about it:
`LOSS_REPRODUCIBLE`. Its contract says losing it must be recoverable by
regenerating it, and enforces that on the way in with `reproducible: true`. So
the rollback here is a restore, not an apology — the reversibility is a
property of the subject rather than a claim about this code.

The executor refuses on its own
-------------------------------
`execute()` reads the verdict itself. It runs nothing unless the verdict is
ALLOW, and nothing unless `--execute` was passed. Those are two separate
refusals because they are repaired by different people: one means the request or
the policy has to change, the other means nobody asked for a mutation yet. The
writer is injected and has no default that a test can reach, so the one property
this module must be able to demonstrate — that on a refusal the actuator is
never entered — is demonstrated by the call list and not by a comment.

The audit is scoped, and the scope is a decision made before the write
---------------------------------------------------------------------
`Observation.take()` is given a list of paths and one directory. A machine-wide
`before != after` is not how this works and must not become how it works: on a
host with other actors, it reports their work as this mutation's side effects
and hides the real ones in the noise. Three different causes are kept apart:

    UNEXPECTED_SIDE_EFFECT      our executor wrote a path our plan did not name
    CONCURRENT_EXTERNAL_CHANGE  something changed that our executor did not write
    PROTECTED_RESOURCE_CHANGED  the declared protected set answers differently

The second is the ordinary case on this host — the daemon publishes its own
snapshots on a tick — and it is reported rather than attributed.

Exit codes, and they are not two
--------------------------------
    0   ALLOW — and `execution.performed` still says whether anything ran.
    10  DENY — a rule refused it, and `codes` names which.
    11  REQUIRE_APPROVAL — nothing refused it and nobody may take it alone.
    2   the question could not be put — no action, or a name that is not a name.
    12  it ran and the audit is not clean. Its own code, because "it did not
        crash" and "it stayed inside its scope" are different sentences and a
        caller that cannot tell them apart cannot hear about a side effect.
"""
from __future__ import annotations

import argparse
import hashlib
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


_paths = _load("serveros_runtime_paths", "paths.py")
_state_root = _load("serveros_runtime_state_root", "state_root.py")
_ownership = _load("serveros_runtime_ownership_gate", "ownership_gate.py")

SCHEMA = "serveros/managed-mutation.v1"
CONTRACT_RELATIVE = "runtime/managed-mutation-contract.yaml"

ACTION = "projection.publish"
TIER = "additive"
NAMESPACE = "projections"
MANIFEST_NAME = "projection.yaml"
CONTENT_NAME = "current.json"

# From the contract's `actions` block. A constant here rather than a second
# declaration to keep in sync: `tests/test_managed_mutation.py` asserts the two
# agree, which is the only direction that catches a contract edited alone.
RECOVERY = "REVERSIBLE"

EXIT_ALLOW = 0
EXIT_DENY = 10
EXIT_REQUIRE_APPROVAL = 11
EXIT_UNASKABLE = 2
EXIT_AUDIT_NOT_CLEAN = 12

# The preserve set: everything the audit compares across a run.
PROTECTED_KEYS = ("protection", "owner_ref", "owner_type", "lifecycle")


class Rejected(ValueError):
    """The request cannot be turned into a plan at all."""


def state_root() -> Path:
    """`paths.py` resolves it, and the contract names that resolver.

    Not `SERVEROS_STATE_DIR` by hand and not a path built from parts: the
    daemon uses this same resolver, and two answers to "where is the state
    root" is the disagreement this whole round is about.
    """
    return _paths.Paths().root("state")


def destination(name: str, *, root: Path | None = None) -> Path:
    root = Path(root) if root is not None else state_root()
    return root / NAMESPACE / name


def _safe_name(name) -> str:
    """A name, or nothing. Not sanitised into one.

    A name that needs sanitising is a request to write somewhere it did not
    name, and quietly writing `../../runtime/registry` as `registry` would be
    the single most expensive convenience in this file.
    """
    if not isinstance(name, str) or not name.strip():
        raise Rejected("the subject needs a `name`; it is a projection's identity")
    if name != name.strip() or "/" in name or name in (".", "..") or name.startswith("."):
        raise Rejected(
            f"{name!r} is not a name — a projection name has no path separators, "
            "no leading dot and no surrounding space. This is refused rather "
            "than cleaned, because cleaning it would write somewhere the "
            "request did not name."
        )
    return name


# ---------------------------------------------------------------------------
# Planning. Built, used, discarded.
# ---------------------------------------------------------------------------

def _digest(value) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _payload(request: dict, now: str) -> dict:
    """The manifest and the body, as they will be written. Pure."""
    manifest = {
        "authority": False,
        "generated": True,
        "generator": request.get("generator"),
        "generated_at": now,
        "source_refs": list(request.get("source_refs") or []),
        "reproducible": True,
    }
    body = request.get("content")
    return {"manifest": manifest, "body": body if body is not None else {}}


def plan(request: dict, *, root: Path | None = None, now: str) -> dict:
    """Intent as data. No side effect, no storage, no identifier to resume by."""
    if not isinstance(request, dict):
        raise Rejected("the request must be a mapping")
    action = request.get("action")
    if action != ACTION:
        raise Rejected(
            f"unknown action {action!r}; this pipeline declares {ACTION!r} and "
            "nothing else. An action it does not know is not a plan it should "
            "improvise."
        )
    subject = request.get("subject")
    if not isinstance(subject, dict):
        raise Rejected("the request must carry a `subject` mapping")
    name = _safe_name(subject.get("name"))

    dest = destination(name, root=root)
    rel = f"{NAMESPACE}/{name}"
    contract = _state_root.load_contract()
    where = _state_root.classify(rel, contract=contract)

    record = {
        "schema": SCHEMA,
        "transient": True,
        "action": ACTION,
        "tier": TIER,
        "subject_ref": f"projection:{name}",
        "subject_name": name,
        "subject_rel": rel,
        "destination": str(dest),
        "destination_class": where["class"],
        "destination_namespace": where["namespace"],
        "destination_admitted": where["authority"] is False,
        "owner_ref": request.get("owner_ref"),
        "reproducible": bool(subject.get("reproducible", False)),
        "source_refs": list(request.get("source_refs") or []),
        "proposed_changes": [
            {"path": str(dest / MANIFEST_NAME), "kind": "write"},
            {"path": str(dest / CONTENT_NAME), "kind": "write"},
        ],
        "policy_context": {
            "state_root_contract": _state_root.CONTRACT_RELATIVE,
            "namespace": where["namespace"],
            "class": where["class"],
        },
        "evidence_refs": [
            {"source": "request", "digest": _digest(
                {k: v for k, v in request.items() if k != "content"})},
            {"source": "destination-classification", "digest": _digest(where)},
        ],
        "recovery": RECOVERY,
        "created_at": now,
    }
    # The manifest and the body as they will be written. Carried on the record
    # rather than rebuilt by the executor: a plan that names the paths and lets
    # the executor decide the content has not described the change, and the
    # audit would then be comparing the world against a plan nobody read.
    record["_payload"] = _payload(request, now)
    return record


# ---------------------------------------------------------------------------
# Compliance. Three questions, in order, and the first refusal wins.
# ---------------------------------------------------------------------------

def compliance(plan_record: dict, *, context: dict | None, registry: dict,
               contract: dict | None = None) -> dict:
    """The verdict on this plan. It does not write anything and it cannot.

    The ownership question is delegated to C2's gate rather than asked again
    here. Two gates answering one question are two answers, and the drift
    between the first and the second is the failure this project keeps paying
    for.
    """
    subject_name = plan_record["subject_name"]
    index = registry if registry is not None else _ownership.build_registry_index()
    ownership = _ownership.evaluate(
        subject={"kind": "projection", "name": subject_name,
                 "ref": plan_record["subject_ref"]},
        action={"kind": TIER},
        context=context,
        registry=index,
        resolved=_ownership.resolve_owner_ref(
            owner_ref=plan_record.get("owner_ref"), registry=index,
            kind="projection"),
    )

    codes: list[str] = []
    if ownership["decision"] == "DENY":
        codes.append(ownership["code"] or "OWNER_UNKNOWN")

    contract = contract if contract is not None else _state_root.load_contract()
    where = _state_root.classify(plan_record["subject_rel"], contract=contract)
    if where["namespace"] is None:
        # The declared namespace this action writes into is gone from the
        # contract. Material under a root is not admitted by being there, and a
        # pipeline that admitted it because its own plan said so would be
        # checking its own homework.
        codes.append("TARGET_UNDECLARED_NAMESPACE")
    elif where["authority"]:
        codes.append("TARGET_IS_AUTHORITY")

    if not plan_record.get("reproducible", True):
        codes.append("NOT_REPRODUCIBLE")
    for ref in plan_record.get("source_refs") or []:
        if str(ref).startswith(f"{NAMESPACE}/"):
            codes.append("SOURCE_IS_A_PROJECTION")
            break

    if codes:
        decision, eligible = "DENY", False
    elif ownership["decision"] == "REQUIRE_APPROVAL":
        decision, eligible = "REQUIRE_APPROVAL", True
    else:
        decision, eligible = "ALLOW", True
    if ownership["decision"] == "REQUIRE_APPROVAL" and "PROTECTED_SUBJECT" not in codes:
        codes = ["PROTECTED_SUBJECT"] + codes

    return {
        "schema": SCHEMA,
        "decision": decision,
        "eligible": eligible,
        "codes": codes or [],
        "code": (codes or [None])[0],
        "ownership": ownership,
        "destination": {"class": where["class"], "namespace": where["namespace"],
                        "admitted": where["authority"] is False},
        "reason": (codes[0] if codes else
                   "every rule admitted it. The action's own policy has not "
                   "been consulted, so this is not a permission to act."),
    }


def ownership_verdict(plan_record: dict, *, context: dict | None) -> dict:
    """The gate's answer alone, for a caller that wants only that."""
    return compliance(plan_record, context=context,
                      registry=_ownership.build_registry_index())["ownership"]


def disclose(verdict: dict, level: str | None) -> dict:
    """The same envelope C1 uses, so a reader learns one shape and not two."""
    return {"axis": "authority", "level": level or "L1",
            "state": "DISCLOSED", "value": verdict}


# ---------------------------------------------------------------------------
# Observation: what is in the scope, now
# ---------------------------------------------------------------------------

#: Bytes are captured for the plan's own paths, up to this much. A restore that
#: has nothing to restore from is not a rollback, and the cap is what keeps
#: this from being a backup system: above it, the snapshot says so and the
#: rollback reports that it could not restore rather than pretending.
SNAPSHOT_BYTES_CAP = 1 << 20


def _describe(path: Path, *, capture_bytes: bool = True) -> dict:
    if path.is_symlink():
        return {"exists": True, "kind": "symlink", "sha256": None, "size": None,
                "data": None, "bytes_refused": False}
    if path.is_dir():
        return {"exists": True, "kind": "dir", "sha256": None, "size": None,
                "data": None, "bytes_refused": False}
    if path.is_file():
        data = path.read_bytes()
        held = data if (capture_bytes and len(data) <= SNAPSHOT_BYTES_CAP) else None
        return {"exists": True, "kind": "file",
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                "data": held, "bytes_refused": held is None}
    return {"exists": False, "kind": None, "sha256": None, "size": None,
            "data": None, "bytes_refused": False}


class Observation:
    """What was seen, in a scope somebody chose. Never the machine.

    Two things are read, because they catch different failures. The planned
    paths catch "the plan did not happen". The directory listing catches what
    the plan could not: something appearing in the subject directory that our
    executor did not write. A comparison over the planned paths alone can only
    ever confirm the plan, which is the check that agrees with itself.
    """

    def __init__(self, entries: dict[str, dict], listing: dict[str, list[str]]):
        self.entries = entries
        self.listing = listing

    @classmethod
    def take(cls, paths, *, directory: Path | None = None,
             capture_bytes: bool = True) -> "Observation":
        entries = {str(p): _describe(Path(p), capture_bytes=capture_bytes)
                   for p in paths}
        listing: dict[str, list[str]] = {}
        if directory is not None:
            directory = Path(directory)
            names = sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []
            listing[str(directory)] = names
        return cls(entries, listing)

    def diff(self, other: "Observation") -> dict:
        """Per path: SAME, CHANGED, APPEARED, DISAPPEARED.

        A path absent from the other observation is NOT_OBSERVED rather than
        SAME: the scope was fixed before the write, so a path that left it is a
        fact about the comparison and not a fact about the world.
        """
        out: dict[str, str] = {}
        for key, before in self.entries.items():
            after = other.entries.get(key)
            if after is None:
                out[key] = "NOT_OBSERVED"
            elif not before["exists"] and not after["exists"]:
                out[key] = "SAME"
            elif not before["exists"]:
                out[key] = "APPEARED"
            elif not after["exists"]:
                out[key] = "DISAPPEARED"
            elif (before["sha256"], before["kind"]) != (after["sha256"], after["kind"]):
                out[key] = "CHANGED"
            else:
                out[key] = "SAME"
        return out

    def changed(self, other: "Observation") -> list[str]:
        return sorted(k for k, v in self.diff(other).items()
                      if v in ("CHANGED", "APPEARED", "DISAPPEARED"))

    def listing_changes(self, other: "Observation",
                        explained: set[str] | None = None) -> dict[str, list[str]]:
        """Directory names that differ, minus the ones we wrote ourselves.

        `explained` is the set of file names the executor says it wrote. Passing
        it is the whole point: without it, a directory listing changes on every
        successful write and every write would read as someone else's doing.
        """
        explained = explained or set()
        out: dict[str, list[str]] = {}
        for key, names in self.listing.items():
            after = set(other.listing.get(key) or [])
            before_names = set(names)
            added = sorted(after - before_names - explained)
            removed = sorted(before_names - after)
            if added or removed:
                out[key] = [f"+{n}" for n in added] + [f"-{n}" for n in removed]
        return out


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------

class FilesystemWriter:
    """The only place this module changes anything.

    Atomic, in the same shape as the daemon's `_atomic_write`: a rename, so a
    reader never sees half of it. It is a class with a call log because a test
    has to be able to assert that a refused mutation did not reach it, and an
    assertion over a call log is the only kind that survives somebody later
    moving the check one line down.
    """

    def __init__(self):
        self.written: list[str] = []
        self.removed: list[str] = []

    def _replace(self, path: Path, raw: bytes) -> None:
        """The one primitive both callers go through: write beside, then rename.

        Kept as a separate name from `write` so that the declaration in
        `actuator-contract.yaml` has one site to name, and so that neither
        caller can quietly grow its own way of reaching the filesystem.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(raw)
        tmp.replace(path)
        self.written.append(str(path))

    def write(self, path: Path, payload: dict) -> None:
        self._replace(path, json.dumps(payload, indent=2, sort_keys=True,
                                       default=str).encode("utf-8"))

    def restore(self, path: Path, raw: bytes) -> None:
        """Put back exactly the bytes that were there.

        Deliberately not `write(path, json.loads(raw))`. That restores the
        VALUE and re-serialises it, so what comes back is a different sequence
        of bytes that happens to parse to the same object — and everything this
        pipeline measures is a digest. A rollback built that way reports a
        difference where the content is identical, and would report none where
        formatting was part of what was there. The captured bytes are what was
        observed, so the captured bytes are what goes back.
        """
        self._replace(path, raw)

    def remove(self, path: Path) -> None:
        Path(path).unlink(missing_ok=True)
        self.removed.append(str(path))

    def rmdir(self, path: Path) -> None:
        path = Path(path)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
            self.removed.append(str(path))


def execute(plan_record: dict, verdict: dict, *, writer, enabled: bool = False) -> dict:
    """Perform the mutation, or say exactly why nothing was performed."""
    if verdict.get("decision") != "ALLOW":
        return {"performed": False, "runner_called": False,
                "decision": verdict.get("decision"), "code": verdict.get("code"),
                "written": [],
                "reason": f"refused before the executor: {verdict.get('reason')}"}
    if not enabled:
        return {"performed": False, "runner_called": False, "decision": "ALLOW",
                "written": [],
                "paths": [c["path"] for c in plan_record["proposed_changes"]],
                "reason": ("compliance allowed it and nothing was performed: the "
                           "executor is off unless `--execute` is passed")}

    payload = plan_record["_payload"]
    dest = Path(plan_record["destination"])
    writer.write(dest / MANIFEST_NAME, payload["manifest"])
    writer.write(dest / CONTENT_NAME, payload["body"])
    return {"performed": True, "runner_called": True, "decision": "ALLOW",
            "written": list(writer.written),
            "destination": str(dest)}


# ---------------------------------------------------------------------------
# The audit. Scoped, and the scope was decided before the write.
# ---------------------------------------------------------------------------

def audit(plan_record: dict, execution: dict, *,
          before: Observation, after: Observation,
          protected_before: dict, protected_after: dict,
          manifest: dict) -> dict:
    """Did the plan happen, did anything protected move, did we reach too far."""
    codes: list[str] = []
    planned = [c["path"] for c in plan_record["proposed_changes"]]
    written = [str(p) for p in execution.get("written") or []]
    performed = bool(execution.get("performed"))

    # Only a run can be missing its target. A dry run that reported
    # PLAN_TARGET_NOT_WRITTEN would be telling a caller their mutation failed
    # when what happened is that they asked for a plan.
    if performed:
        for path in planned:
            entry = after.entries.get(path)
            if entry is None or not entry["exists"]:
                codes.append("PLAN_TARGET_NOT_WRITTEN")
                break
        if not codes and manifest.get("problems"):
            codes.append("PLAN_TARGET_MALFORMED")

    if protected_before != protected_after:
        codes.append("PROTECTED_RESOURCE_CHANGED")

    # Our own doing, and the one a scope check exists to catch: the executor
    # wrote a path the plan never named.
    if performed:
        stray = [p for p in written if p not in planned and not p.endswith(".tmp")]
        if stray:
            codes.append("UNEXPECTED_SIDE_EFFECT")

    # Somebody else's, and reported rather than attributed. The daemon
    # publishes its own snapshots on a tick, so on this host this is the
    # ordinary case and it must not be dressed up as a side effect of ours.
    explained = {Path(p).name for p in written}
    concurrent = [p for p in before.changed(after) if p not in written]
    listing = before.listing_changes(after, explained=explained)
    if (concurrent or listing) and not codes:
        codes.append("CONCURRENT_EXTERNAL_CHANGE")

    return {
        "schema": SCHEMA,
        "verdict": codes[0] if codes else "AUDIT_OK",
        "codes": codes,
        "performed": performed,
        "scope": {"paths": len(before.entries) + len(planned),
                  "directories": list(before.listing),
                  "machine_wide": False},
        "planned_changes": planned,
        "written": written,
        "diff": {k: v for k, v in before.diff(after).items() if v != "SAME"},
        "concurrent_external_change": concurrent,
        "concurrent_listing_change": listing,
        "protected_resources_compared": sorted(protected_before),
    }


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def rollback(plan_record: dict, *, before: Observation, writer,
             prune_created_parent: bool = True) -> dict:
    """Put the scope back, then LOOK at it. A restore nobody re-observed is a belief.

    Two cases, and they are different operations. A path that was not there is
    removed. A path that WAS there is written back from the bytes captured
    before the run — this action regenerates an existing projection as readily
    as it creates one, and a rollback that only knew how to delete would call
    itself REVERSIBLE while destroying the thing it overwrote.

    Scope discipline: it touches only the plan's own paths, and rmdirs only the
    destination directory and only when it is empty. A rollback that widened
    its reach when it failed is how a cleanup becomes the incident.
    """
    dest = Path(plan_record["destination"])
    restored: list[str] = []
    unrestorable: list[str] = []
    for change in plan_record["proposed_changes"]:
        path = Path(change["path"])
        entry = before.entries.get(str(path)) or {}
        if not entry.get("exists"):
            writer.remove(path)
            continue
        if entry.get("data") is None:
            unrestorable.append(str(path))
            continue
        writer.restore(path, entry["data"])
        restored.append(str(path))
    if prune_created_parent:
        writer.rmdir(dest)
    return {"recovery": plan_record["recovery"], "removed": list(writer.removed),
            "restored": restored, "unrestorable": unrestorable,
            "note": ("every path was either removed or written back from the "
                     "bytes captured before the write"
                     if not unrestorable else
                     "at least one path was too large to snapshot; its prior "
                     "contents were not restored")}


def rollback_verdict(before: Observation, after_restore: Observation) -> dict:
    differing = before.changed(after_restore)
    return {"verdict": "ROLLBACK_VERIFIED" if not differing else "ROLLBACK_INCOMPLETE",
            "differing": differing}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_request(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml
        return yaml.safe_load(text) or {}


def protected_snapshot(registry: dict | None = None) -> dict:
    """The declared protected set, as one comparable value.

    Bounded by construction: it is the registry's own entries — the refs the
    registry marks protected, plus any registration whose record says
    `protection: protected` — so the audit's "did anything protected move" is a
    question about a list somebody wrote, not a scan of the host.

    Read from `by_ref` and `protected_refs`, which are the fields
    `build_registry_index()` actually produces. It produces no `resources` key,
    and code that reached for one would have compared two empty dicts and
    called the comparison clean.
    """
    registry = registry if registry is not None else _ownership.build_registry_index()
    by_ref = registry.get("by_ref") or {}
    refs = set(registry.get("protected_refs") or {})
    refs.update(ref for ref, record in by_ref.items()
                if (record or {}).get("protection") == "protected")
    return {ref: {key: (by_ref.get(ref) or {}).get(key) for key in PROTECTED_KEYS}
            for ref in sorted(refs)}


def run(argv: list[str] | None = None, *, root: Path | None = None, writer=None,
        registry: dict | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=("plan", "run"))
    parser.add_argument("--request", required=True, help="JSON or YAML request file")
    parser.add_argument("--execute", action="store_true",
                        help="perform the mutation; without it nothing is written")
    parser.add_argument("--level", choices=["L0", "L1", "L2", "L3"])
    # Required in effect and not by argparse, for the same reason C1's is: its
    # absence is a refusal with a code and a repair, not a usage error that
    # teaches the caller to pass any string that parses.
    parser.add_argument(
        "--context",
        help="trusted context document ({context_ref, actor_ref}). Without it "
             "the ownership gate answers NO_TRUSTED_CONTEXT and nothing runs")
    args = parser.parse_args(argv)

    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    request = _read_request(args.request)
    try:
        plan_record = plan(request, root=root, now=now)
    except Rejected as exc:
        # A malformed REQUEST is not a refused CANDIDATE. Only the second has a
        # code and a repair, and answering the first with a code would tell the
        # caller their destination was refused when in fact nothing was looked
        # at. Exit 2, the same as C1 and C2.
        sys.stderr.write(f"REJECTED: {exc}\n")
        return EXIT_UNASKABLE

    context = _read_request(args.context) if args.context else None
    index = registry if registry is not None else _ownership.build_registry_index()
    verdict = compliance(plan_record, context=context, registry=index)

    writer = writer if writer is not None else FilesystemWriter()
    dest = Path(plan_record["destination"])
    scope_paths = [c["path"] for c in plan_record["proposed_changes"]]
    before = Observation.take(scope_paths, directory=dest)
    protected_before = protected_snapshot(index)

    execution = execute(plan_record, verdict, writer=writer, enabled=args.execute)

    after = Observation.take(scope_paths, directory=dest)
    manifest = _state_root.read_manifest(dest)
    audit_result = audit(plan_record, execution, before=before, after=after,
                         protected_before=protected_before,
                         protected_after=protected_snapshot(index), manifest=manifest)

    payload = {
        "plan": {k: v for k, v in plan_record.items() if not k.startswith("_")},
        "compliance": (disclose(verdict, args.level) if args.level else verdict),
        "execution": execution,
        "audit": audit_result,
    }
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")

    if verdict["decision"] == "DENY":
        sys.stderr.write(_render(verdict) + "\n")
        return EXIT_DENY
    if verdict["decision"] == "REQUIRE_APPROVAL":
        sys.stderr.write(_render(verdict) + "\n")
        return EXIT_REQUIRE_APPROVAL
    if execution["performed"] and audit_result["verdict"] != "AUDIT_OK":
        sys.stderr.write(f"AUDIT {audit_result['verdict']}: "
                         f"{', '.join(audit_result['codes'])}\n")
        return EXIT_AUDIT_NOT_CLEAN
    return EXIT_ALLOW


def _render(verdict: dict) -> str:
    return (f"{verdict['decision']}: {verdict['reason']} — "
            f"codes {', '.join(verdict['codes']) or 'none'}")


def exit_code(verdict: dict) -> int:
    """The verdict as a process exit code. A policy, and a pure function.

    Separate from `run()` so the mapping can be tested without a host: the
    verdict it maps is a record, and a test of the mapping should not need a
    real projection to produce one.
    """
    if not verdict.get("eligible"):
        return EXIT_DENY
    if verdict.get("decision") == "REQUIRE_APPROVAL":
        return EXIT_REQUIRE_APPROVAL
    return EXIT_ALLOW


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
